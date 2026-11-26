"""Ejecucion y reconciliacion de los ajustes automaticos por ubicacion (AUTO-04).

Corre solo en modo `live`, sobre las evaluaciones del ciclo con veredicto
`mover` que aun no tienen desenlace. La intencion se escribe ANTES del HTTP en
`campana_ajuste` (con `evaluacion_id`) sobre una conexion DEDICADA con
`autocommit=False`: asi el candado advisory de transaccion que toma
`_aplica_y_confirma` se retiene durante todo el `PUT` y no se suelta con el
commit del ciclo (hallazgo 1 de la revision).

Este modulo no importa `app.ads.write`: construye el cliente en `apply.py` y
lo recibe ya armado.
"""

from __future__ import annotations

import datetime as dt
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
from app.ads.client import AdsApiError, AdsAuthError, AdsClientError
from app.apply_ajustes import (
    AjusteEnCurso,
    AjusteNoConfirmado,
    CambioExternoDetectado,
    HuellaDesactualizada,
    _config_leida,
    aplica_ajuste_motor,
)
from app.apply_cuota import consume_quota
from app.apply_estados import ErrorTrasPut
from app.campana_ajustes import CambiarAjusteUbicacion
from app.db import OrbitDbError, settings_vigentes, tx_durable
from app.db import connect as db_connect
from app.optimizer import goals as g
from app.optimizer.ubicacion import (
    max_en_espera_desde_settings,
    modo_ubicacion_desde_settings,
)
from app.ubicacion_io import evaluaciones_a_aplicar

MOTIVO_CUOTA = "cuota_agotada"
MOTIVO_TOPE = "tope_campanas_en_espera"
MOTIVO_CAMBIO_EXTERNO = "cambio_externo"
MOTIVO_CONFIG_CAMBIO = "config_cambio"
MOTIVO_MODO_NO_LIVE = "modo_no_live"
MOTIVO_ERROR_PUT = "error_put"
MOTIVO_AJUSTE_EN_CURSO = "ajuste_en_curso"

MOTIVOS_SIN_APLICAR = (
    MOTIVO_CUOTA,
    MOTIVO_TOPE,
    MOTIVO_CAMBIO_EXTERNO,
    MOTIVO_CONFIG_CAMBIO,
    MOTIVO_MODO_NO_LIVE,
    MOTIVO_ERROR_PUT,
    MOTIVO_AJUSTE_EN_CURSO,
)

_CAMPOS_POR_UBICACION = (
    ("arriba_de_busqueda", "ajuste_top_pct"),
    ("resto_de_busqueda", "ajuste_resto_pct"),
    ("paginas_de_producto", "ajuste_producto_pct"),
)

_SQL_PENDIENTES = """
SELECT a.id, a.campana_id, c.external_id, a.antes_config_id, a.despues
  FROM campana_ajuste a
  JOIN ad_entity c ON c.id = a.campana_id
 WHERE a.platform = %s AND a.clase = 'ajuste_ubicacion'
   AND a.evaluacion_id IS NOT NULL AND a.confirmado_el IS NULL
   AND NOT EXISTS (SELECT 1 FROM ubicacion_sin_aplicar s
                    WHERE s.evaluacion_id = a.evaluacion_id)
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


@dataclass(frozen=True)
class ResumenAplica:
    candidatas: int
    aplicadas: int
    sin_aplicar: tuple[tuple[int, str], ...]


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
    for _ubicacion, campo in _CAMPOS_POR_UBICACION:
        if getattr(antes, campo) != getattr(despues, campo):
            return campo
    return None


def _coincide(leida: ConfigCampana, destino: ConfigCampana, campo: str) -> bool:
    return leida.estrategia_puja == destino.estrategia_puja and (
        (getattr(leida, campo) or 0) == (getattr(destino, campo) or 0)
    )


def reconcilia_automaticos(
    conn: psycopg.Connection,
    cliente,
    platform: str,
    *,
    tick: Callable[[], None] | None = None,
) -> ResumenReconcilia:
    """Sella las intenciones automaticas que Amazon ya refleja, sin mandar PUT.

    Si la lectura viva no coincide con el destino, la fila queda pendiente y la
    campana se abstiene (`ajuste_en_vuelo`) hasta que la reconciliacion la
    resuelva.
    """
    filas = conn.execute(_SQL_PENDIENTES, (platform,)).fetchall()
    selladas = 0
    for ajuste_id, _campana, externa, antes_id, despues in filas:
        if tick is not None:
            tick()
        antes = config_por_id(conn, antes_id)
        destino = ConfigCampana.desde_json(despues)
        campo = _campo_movido(antes, destino)
        if campo is None:
            continue
        leida = _config_leida(cliente, platform, externa)
        if leida is None or not _coincide(leida, destino, campo):
            continue
        ahora = dt.datetime.now(dt.UTC)
        with conn.transaction():
            conn.execute(_SQL_CONFIRMA, (ahora, ajuste_id))
            guarda_config(conn, platform, [mezcla_observacion(antes, leida)], ahora)
        conn.commit()
        selladas += 1
    return ResumenReconcilia(revisadas=len(filas), selladas=selladas)


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


def _motivo_error_put(exc: Exception) -> str | None:
    """Motivo a registrar, o None si el PUT pudo llegar (ambiguo)."""
    if isinstance(exc, AdsAuthError):
        return MOTIVO_ERROR_PUT
    status = getattr(exc, "status", None)
    if status in (401, 403):
        return MOTIVO_ERROR_PUT
    if not getattr(exc, "put_iniciado", False):
        return MOTIVO_ERROR_PUT
    if status is not None and status < 500 and status != 429:
        return MOTIVO_ERROR_PUT
    return None


def _cuenta_en_espera(conn: psycopg.Connection, platform: str) -> int:
    return conn.execute(_SQL_CUENTA_EN_ESPERA, (platform,)).fetchone()[0]


def aplica_automaticos(
    conn: psycopg.Connection,
    *,
    dsn: str,
    cliente,
    platform: str,
    cycle_id: int,
    settings,
    tick: Callable[[], None] | None = None,
    abrir_conexion: Callable[[], psycopg.Connection] | None = None,
) -> ResumenAplica:
    """Ejecuta los movimientos `live` del ciclo en el orden determinista.

    Cuota, tope de campanas en espera y relectura de configuracion se cobran o
    verifican ANTES del HTTP; lo que no se envia deja su motivo en
    `ubicacion_sin_aplicar`. La conexion dedicada no usa autocommit para
    retener el candado de transaccion durante el `PUT`.
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
        if modo != "live":
            descarta(eval_id, MOTIVO_MODO_NO_LIVE)
            continue
        # El dueno pudo apagar la campana o el motor durante la fase de pujas.
        if not _sigue_vivo(conn, platform, caso.campana_id):
            descarta(eval_id, MOTIVO_MODO_NO_LIVE)
            continue
        vigente_id, _vigente = config_vigente_con_id(conn, caso.campana_id)
        if vigente_id is None or vigente_id != caso.config_id:
            descarta(eval_id, MOTIVO_CONFIG_CAMBIO)
            continue
        if _cuenta_en_espera(conn, platform) >= maximo:
            descarta(eval_id, MOTIVO_TOPE)
            continue
        with tx_durable(conn):
            usada = consume_quota(conn, platform, "ubicacion")
        if not usada:
            descarta(eval_id, MOTIVO_CUOTA)
            continue
        conexion = None
        try:
            conexion = abrir()
            aplica_ajuste_motor(
                conexion,
                cliente,
                campana_id=caso.campana_id,
                plataforma=platform,
                ajuste=CambiarAjusteUbicacion(
                    ubicacion=veredicto.ubicacion, porcentaje=veredicto.a_pct
                ),
                evaluacion_id=eval_id,
            )
            aplicadas += 1
        except CambioExternoDetectado:
            descarta(eval_id, MOTIVO_CAMBIO_EXTERNO)
        except AjusteEnCurso:
            descarta(eval_id, MOTIVO_AJUSTE_EN_CURSO)
        except (AjusteNoConfirmado, ErrorTrasPut):
            # El PUT pudo llegar (o ya salio): la intencion queda pendiente
            # para que la reconciliacion la selle. Nunca se marca no aplicada.
            pass
        except HuellaDesactualizada:
            descarta(eval_id, MOTIVO_CONFIG_CAMBIO)
        except AdsAuthError:
            descarta(eval_id, MOTIVO_ERROR_PUT)
            raise
        except AdsApiError as exc:
            if exc.status in (401, 403):
                descarta(eval_id, MOTIVO_ERROR_PUT)
                raise
            if not getattr(exc, "put_iniciado", False):
                # Fallo ANTES del PUT: no salio nada.
                descarta(eval_id, MOTIVO_ERROR_PUT)
            elif (
                exc.status is not None
                and (exc.status < 500 or exc.status == 207)
                and exc.status != 429
            ):
                # Rechazo determinista del PUT (207/4xx): Amazon no aplico.
                descarta(eval_id, MOTIVO_ERROR_PUT)
            # 5xx, 429 o red durante el PUT: ambiguo, queda pendiente.
        except AdsClientError:
            descarta(eval_id, MOTIVO_ERROR_PUT)
        except (OrbitDbError, psycopg.Error):
            # La base fallo sin poder distinguir si el PUT salio: no se marca
            # no aplicado y se aborta la fase para que el ciclo sea recuperable.
            raise
        finally:
            if conexion is not None:
                conexion.close()

    return ResumenAplica(
        candidatas=len(pendientes), aplicadas=aplicadas, sin_aplicar=tuple(sin_aplicar)
    )
