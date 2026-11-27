"""Ejecucion y reconciliacion de los ajustes automaticos por ubicacion (AUTO-04).

Corre solo en modo `live`, sobre las evaluaciones del ciclo con veredicto
`mover` que aun no tienen desenlace. La intencion se escribe ANTES del HTTP en
`campana_ajuste` (con `evaluacion_id`) sobre una conexion DEDICADA con
`autocommit=False`: asi el candado advisory de transaccion que toma
`_aplica_y_confirma` se retiene durante todo el `PUT`.

El desenlace se decide por observacion, no por el tipo de error: si el PUT no
llego a intentarse, la evaluacion se cierra con su motivo (nada salio a
Amazon); si empezo y no confirmo, la intencion queda pendiente y
`reconcilia_automaticos` la sella o la cierra segun lo que Amazon muestre.

Este modulo no importa `app.ads.write`: construye el cliente en `apply.py` y
lo recibe ya armado.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from app.ads.campana_config import (
    ConfigCampana,
    config_por_id,
    config_vigente_con_id,
    guarda_config,
    mezcla_observacion,
)
from app.ads.client import AdsAuthError, AdsClientError
from app.apply_ajustes import (
    _SQL_AJUSTE_POR_HUELLA,
    _SQL_PLATAFORMA_CAMPANA,
    AjusteEnCurso,
    AjusteHecho,
    AjusteNoConfirmado,
    CambioExternoDetectado,
    HuellaDesactualizada,
    _aplica_y_confirma,
    _config_leida,
)
from app.apply_cuota import consume_quota
from app.apply_estados import STATUS_IDENTIDAD_RECHAZADA
from app.campana_ajustes import AjusteSinCambio, CambiarAjusteUbicacion, planea_ajuste
from app.db import connect as db_connect
from app.db import settings_vigentes, tx_durable
from app.optimizer import goals as g
from app.optimizer.ubicacion import (
    max_en_espera_desde_settings,
    modo_ubicacion_desde_settings,
)
from app.ubicacion_io import evaluaciones_a_aplicar, sql_orden_viva

MOTIVO_CUOTA = "cuota_agotada"
MOTIVO_TOPE = "tope_campanas_en_espera"
MOTIVO_CAMBIO_EXTERNO = "cambio_externo"
MOTIVO_CONFIG_CAMBIO = "config_cambio"
MOTIVO_MODO_NO_LIVE = "modo_no_live"
MOTIVO_ERROR_PUT = "error_put"
MOTIVO_AJUSTE_EN_CURSO = "ajuste_en_curso"
MOTIVO_PUJAS_PENDIENTES = "pujas_pendientes"

MOTIVOS_SIN_APLICAR = (
    MOTIVO_CUOTA,
    MOTIVO_TOPE,
    MOTIVO_CAMBIO_EXTERNO,
    MOTIVO_CONFIG_CAMBIO,
    MOTIVO_MODO_NO_LIVE,
    MOTIVO_ERROR_PUT,
    MOTIVO_AJUSTE_EN_CURSO,
    MOTIVO_PUJAS_PENDIENTES,
)

# Errores con los que el envio de UNA evaluacion termina sin abortar la fase.
# El orden importa: CambioExternoDetectado hereda de HuellaDesactualizada.
_MOTIVO_SIN_PUT: tuple[tuple[type[Exception], str], ...] = (
    (CambioExternoDetectado, MOTIVO_CAMBIO_EXTERNO),
    (HuellaDesactualizada, MOTIVO_CONFIG_CAMBIO),
    (AjusteSinCambio, MOTIVO_CONFIG_CAMBIO),
    (AjusteEnCurso, MOTIVO_AJUSTE_EN_CURSO),
    (AjusteNoConfirmado, MOTIVO_ERROR_PUT),
    (AdsClientError, MOTIVO_ERROR_PUT),
)
_ERRORES_DE_ENVIO = tuple(clase for clase, _motivo in _MOTIVO_SIN_PUT)

_CAMPOS_POR_UBICACION = {
    "arriba_de_busqueda": "ajuste_top_pct",
    "resto_de_busqueda": "ajuste_resto_pct",
    "paginas_de_producto": "ajuste_producto_pct",
}

_SQL_PENDIENTES = f"""
SELECT a.id, c.external_id, a.antes_config_id, a.despues, a.evaluacion_id
  FROM campana_ajuste a
  JOIN ad_entity c ON c.id = a.campana_id
 WHERE a.platform = %s AND a.clase = 'ajuste_ubicacion'
   AND a.evaluacion_id IS NOT NULL AND a.confirmado_el IS NULL
   AND {sql_orden_viva("a")}
 ORDER BY a.id
"""

_SQL_CONFIRMA = "UPDATE campana_ajuste SET confirmado_el = %s WHERE id = %s"

_SQL_CUENTA_EN_ESPERA = """
SELECT count(DISTINCT campana_id)
  FROM campana_ajuste
 WHERE platform = %s AND clase = 'ajuste_ubicacion'
   AND confirmado_el IS NOT NULL
   AND confirmado_el >= now() - interval '7 days'
"""

_SQL_SIN_APLICAR = """
INSERT INTO ubicacion_sin_aplicar (evaluacion_id, motivo, registrado_el)
VALUES (%s, %s, %s)
ON CONFLICT (evaluacion_id) DO NOTHING
"""


@dataclass(frozen=True)
class ResumenReconcilia:
    revisadas: int
    selladas: int
    cerradas: int


@dataclass(frozen=True)
class ResumenAplica:
    candidatas: int
    aplicadas: int
    sin_aplicar: tuple[tuple[int, str], ...]


class _ClienteVigilado:
    """Registra si el PUT llego a intentarse: lo que falla antes no salio."""

    def __init__(self, cliente) -> None:
        self._cliente = cliente
        self.put_iniciado = False

    def list_sellado(self, path, payload):
        return self._cliente.list_sellado(path, payload)

    def ajustar_campana(self, externa, cuerpo):
        self.put_iniciado = True
        return self._cliente.ajustar_campana(externa, cuerpo)


def esquema_ubicaciones_disponible(conn: psycopg.Connection) -> bool:
    """False en una base anterior a la migracion 0104: el motor no existe y
    ninguna capa intenta leer sus tablas."""
    return bool(
        conn.execute("SELECT to_regclass('public.ubicacion_evaluacion') IS NOT NULL").fetchone()[0]
    )


def hay_pendientes_automaticos(conn: psycopg.Connection, platform: str) -> bool:
    if not esquema_ubicaciones_disponible(conn):
        return False
    return bool(conn.execute(_SQL_PENDIENTES, (platform,)).fetchone())


def hay_evaluaciones_a_aplicar(conn: psycopg.Connection, cycle_id: int, platform: str) -> bool:
    if not esquema_ubicaciones_disponible(conn):
        return False
    fila = conn.execute(
        "SELECT 1 FROM ubicacion_evaluacion u WHERE u.cycle_id = %s AND u.platform = %s"
        " AND u.modo = 'live' AND u.veredicto = 'mover'"
        " AND NOT EXISTS (SELECT 1 FROM campana_ajuste a WHERE a.evaluacion_id = u.id)"
        " AND NOT EXISTS (SELECT 1 FROM ubicacion_sin_aplicar s WHERE s.evaluacion_id = u.id)"
        " LIMIT 1",
        (cycle_id, platform),
    ).fetchone()
    return fila is not None


def registra_sin_aplicar(conn: psycopg.Connection, evaluacion_id: int, motivo: str) -> None:
    if motivo not in MOTIVOS_SIN_APLICAR:
        raise ValueError(f"motivo fuera del vocabulario de ubicaciones: {motivo}")
    with tx_durable(conn):
        conn.execute(_SQL_SIN_APLICAR, (evaluacion_id, motivo, dt.datetime.now(dt.UTC)))


def _campo_movido(antes: ConfigCampana, despues: ConfigCampana) -> str | None:
    for campo in _CAMPOS_POR_UBICACION.values():
        if getattr(antes, campo) != getattr(despues, campo):
            return campo
    return None


def _coincide(leida: ConfigCampana, esperada: ConfigCampana, campo: str) -> bool:
    return leida.estrategia_puja == esperada.estrategia_puja and (
        (getattr(leida, campo) or 0) == (getattr(esperada, campo) or 0)
    )


def reconcilia_automaticos(
    conn: psycopg.Connection,
    cliente,
    platform: str,
    *,
    tick: Callable[[], None] | None = None,
) -> ResumenReconcilia:
    """Decide las intenciones automaticas pendientes leyendo Amazon, sin PUT.

    Amazon muestra el porcentaje destino: se sella (como el readback, compara
    solo el campo). Muestra otro valor legible: la intencion no llego (o el
    dueno la piso) y se cierra con `error_put`, para que la campana no quede
    en `ajuste_en_vuelo` para siempre. Lectura ilegible: queda pendiente hasta
    la siguiente reconciliacion.
    """
    filas = conn.execute(_SQL_PENDIENTES, (platform,)).fetchall()
    selladas = cerradas = 0
    for ajuste_id, externa, antes_id, despues, evaluacion_id in filas:
        if tick is not None:
            tick()
        antes = config_por_id(conn, antes_id)
        destino = ConfigCampana.desde_json(despues)
        campo = _campo_movido(antes, destino)
        if campo is None:
            continue
        leida = _config_leida(cliente, platform, externa)
        if leida is None or leida.estrategia_puja is None:
            continue
        if (getattr(leida, campo) or 0) != (getattr(destino, campo) or 0):
            registra_sin_aplicar(conn, evaluacion_id, MOTIVO_ERROR_PUT)
            cerradas += 1
            continue
        ahora = dt.datetime.now(dt.UTC)
        with conn.transaction():
            conn.execute(_SQL_CONFIRMA, (ahora, ajuste_id))
            guarda_config(conn, platform, [mezcla_observacion(antes, leida)], ahora)
        conn.commit()
        selladas += 1
    return ResumenReconcilia(revisadas=len(filas), selladas=selladas, cerradas=cerradas)


def _sigue_vivo(conn: psycopg.Connection, platform: str, campana: int) -> bool:
    """Relee settings y goal frescos: el dueno pudo apagar la campana o el
    motor durante la fase de pujas."""
    frescos = settings_vigentes(conn)
    if frescos is None or modo_ubicacion_desde_settings(frescos, platform) != "live":
        return False
    if g.modo_desde_settings(frescos) != "live":
        return False
    from app.cycle_grupos import lee_goals

    goal_plataforma, goals_campana = lee_goals(conn, platform, [campana])
    objetivo = g.resuelve_goal(goals_campana.get(campana), goal_plataforma)
    return objetivo is not None and objetivo.enabled and objetivo.mode == "live"


def _motivo_consola(cliente, platform: str, vigente: ConfigCampana, ubicacion: str) -> str | None:
    """Revalidacion viva ANTES de cobrar cuota. Sin lectura completa no se
    manda nada a ciegas; un valor distinto del juzgado es mano del dueno en la
    consola despues del sync."""
    leida = _config_leida(cliente, platform, vigente.campana_externa)
    if leida is None or leida.estrategia_puja is None:
        return MOTIVO_ERROR_PUT
    if not _coincide(leida, vigente, _CAMPOS_POR_UBICACION[ubicacion]):
        return MOTIVO_CAMBIO_EXTERNO
    return None


def _motivo_sin_put(exc: Exception) -> str:
    return next(motivo for clase, motivo in _MOTIVO_SIN_PUT if isinstance(exc, clase))


def _identidad_rechazada(exc: Exception) -> bool:
    return isinstance(exc, AdsAuthError) or (
        getattr(exc, "status", None) in STATUS_IDENTIDAD_RECHAZADA
    )


def _cuenta_en_espera(conn: psycopg.Connection, platform: str) -> int:
    return conn.execute(_SQL_CUENTA_EN_ESPERA, (platform,)).fetchone()[0]


_SQL_INSERTA_AUTOMATICO = """
INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id, despues,
    huella, actor, go_literal, evaluacion_id)
VALUES (%s, %s, %s, %s, %s, %s, 'orbit_ubicacion', 'AUTO_UBICACION', %s)
ON CONFLICT (huella) DO NOTHING
RETURNING id
"""


def aplica_ajuste_motor(
    conn,
    cliente,
    *,
    campana_id: int,
    plataforma: str,
    ajuste,
    evaluacion_id: int,
) -> AjusteHecho:
    """Aplica un ajuste nacido de una evaluacion `live` de ubicaciones.

    Camino compartido con el manual: escribe la intencion ANTES del HTTP y
    confirma con readback, pero sin exigir el literal humano y con la huella
    `auto:<evaluacion_id>:<huella del plan>` (permite re-aplicar tras un
    regreso del dueno sin chocar en UNIQUE(huella)). El rol `app_decide` y la
    frontera de la migracion 0104 garantizan que solo se ejecute lo que nacio
    de una evaluacion `live` con veredicto `mover` de la misma campana.
    """
    fila = conn.execute(_SQL_PLATAFORMA_CAMPANA, (campana_id,)).fetchone()
    if fila is None or fila[0] != plataforma:
        raise ValueError(f"campana {campana_id} no es de {plataforma}")
    antes_id, vigente = config_vigente_con_id(conn, campana_id)
    if vigente is None:
        raise HuellaDesactualizada(
            f"campana {campana_id} sin configuracion vigente: no hay ajuste", None
        )
    plan = planea_ajuste(vigente, ajuste, campana_id=campana_id, plataforma=plataforma)
    huella = f"auto:{evaluacion_id}:{plan.huella()}"
    existente = conn.execute(_SQL_AJUSTE_POR_HUELLA, (huella,)).fetchone()
    if existente is not None:
        _eid, confirmada, ecamp, _eclase = existente
        if ecamp != campana_id:
            raise ValueError(f"la huella {huella} es de otra campana")
        if confirmada:
            return AjusteHecho(_eid, campana_id, plan.clase, True)
        ajuste_id = _eid
    else:
        metida = conn.execute(
            _SQL_INSERTA_AUTOMATICO,
            (
                campana_id,
                plataforma,
                plan.clase,
                antes_id,
                json.dumps(plan.despues.como_json()),
                huella,
                evaluacion_id,
            ),
        ).fetchone()
        if metida is None:
            ganadora = conn.execute(_SQL_AJUSTE_POR_HUELLA, (huella,)).fetchone()
            if ganadora is not None and ganadora[1]:
                return AjusteHecho(ganadora[0], campana_id, plan.clase, True)
            raise AjusteEnCurso(f"huella {huella} en curso en otro intento: reintenta")
        ajuste_id = metida[0]
    conn.commit()
    _aplica_y_confirma(
        conn,
        cliente,
        plataforma,
        plan.clase,
        plan.despues,
        plan.ajuste,
        campana_id=campana_id,
        ajuste_id=ajuste_id,
        previa=plan.antes,
    )
    return AjusteHecho(ajuste_id, campana_id, plan.clase, True)


def aplica_automaticos(
    conn: psycopg.Connection,
    *,
    dsn: str,
    cliente,
    platform: str,
    cycle_id: int,
    settings,
    pujas_completas: bool = True,
    tick: Callable[[], None] | None = None,
    abrir_conexion: Callable[[], psycopg.Connection] | None = None,
) -> ResumenAplica:
    """Ejecuta los movimientos `live` del ciclo en el orden determinista.

    Modo, configuracion, tope de campanas en espera y lectura viva se
    verifican ANTES de cobrar cuota; lo que no se envia deja su motivo en
    `ubicacion_sin_aplicar`. Un rechazo de identidad o un fallo de base aborta
    la fase: la reconciliacion converge desde cualquier punto de caida.
    """
    pendientes = evaluaciones_a_aplicar(conn, cycle_id, platform)
    if not pendientes:
        return ResumenAplica(candidatas=0, aplicadas=0, sin_aplicar=())
    modo = modo_ubicacion_desde_settings(settings, platform)
    maximo = max_en_espera_desde_settings(settings, platform)
    abrir = abrir_conexion or (lambda: db_connect(dsn, autocommit=False))
    aplicadas = 0
    sin_aplicar: list[tuple[int, str]] = []

    def descarta(evaluacion_id: int, motivo: str) -> None:
        registra_sin_aplicar(conn, evaluacion_id, motivo)
        sin_aplicar.append((evaluacion_id, motivo))

    for eval_id, caso, veredicto in pendientes:
        if tick is not None:
            tick()
        if not pujas_completas:
            descarta(eval_id, MOTIVO_PUJAS_PENDIENTES)
            continue
        if modo != "live" or not _sigue_vivo(conn, platform, caso.campana_id):
            descarta(eval_id, MOTIVO_MODO_NO_LIVE)
            continue
        vigente_id, vigente = config_vigente_con_id(conn, caso.campana_id)
        if vigente_id is None or vigente_id != caso.config_id:
            descarta(eval_id, MOTIVO_CONFIG_CAMBIO)
            continue
        if _cuenta_en_espera(conn, platform) >= maximo:
            descarta(eval_id, MOTIVO_TOPE)
            continue
        conexion = abrir()
        vigilado = _ClienteVigilado(cliente)
        try:
            motivo = _motivo_consola(vigilado, platform, vigente, veredicto.ubicacion)
            if motivo is not None:
                descarta(eval_id, motivo)
                continue
            with tx_durable(conn):
                usada = consume_quota(conn, platform, "ubicacion")
            if not usada:
                descarta(eval_id, MOTIVO_CUOTA)
                continue
            aplica_ajuste_motor(
                conexion,
                vigilado,
                campana_id=caso.campana_id,
                plataforma=platform,
                ajuste=CambiarAjusteUbicacion(
                    ubicacion=veredicto.ubicacion, porcentaje=veredicto.a_pct
                ),
                evaluacion_id=eval_id,
            )
            aplicadas += 1
        except _ERRORES_DE_ENVIO as exc:
            if not vigilado.put_iniciado:
                descarta(eval_id, _motivo_sin_put(exc))
            if _identidad_rechazada(exc):
                raise
        finally:
            conexion.close()

    return ResumenAplica(
        candidatas=len(pendientes), aplicadas=aplicadas, sin_aplicar=tuple(sin_aplicar)
    )
