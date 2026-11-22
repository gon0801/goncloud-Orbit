"""Corrida diaria del motor de precios (REPRICING 01 A.5 + 02 S.1).

Camino por plataforma: claim en `ads_optimizer_lock` (`precio:<platform>`)
+ advisory lock de sesion via `app/lock.py`; fila `precio_corrida` (las
`abierta` viejas pasan a `abortada`); huerfanas a `error`; cierre por
observacion; decidir (persiste una decision por goal vigente sin decision hoy,
con su modo efectivo); compuerta (`app/precio/compuerta_io.py`); aplicar
(retiene lo del universo `Retenida` no liberado; relee config y goal antes de
cada escritura; corte por errores; `live` con `cambios.aplicar`; `shadow` con
cambio virtual); cierra la fila; avisa; libera el claim al salir.

La cotizacion y persistencia de decisiones viven en `app/precio/decision_io.py`,
la medicion SQL de la compuerta en `app/precio/compuerta_io.py` y el reporte de
solo lectura en `app/precio/fuentes.py`. Este modulo escribe UNICAMENTE en
`precio_corrida` y `precio_retencion`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg

import app.precio.cambios as cambios_mod
from app.lock import (
    latir_lock,
    soltar_lock_con_advisory,
    tomar_lock_con_advisory,
)
from app.precio.compuerta import (
    Cortacircuito,
    Retenida,
    insumo_atrasado,
    modo_efectivo,
)
from app.precio.compuerta_io import a_la_venta as _a_la_venta
from app.precio.compuerta_io import medir_compuerta as _medir_compuerta
from app.precio.config import ConfigPrecio, leer_config, leer_config_motor
from app.precio.config import leer_entero as _entero_config
from app.precio.config import leer_modo as _modo_config
from app.precio.config import leer_modo_universo as _modo_universo_config
from app.precio.decision_io import (
    ERRORES_DE_FILA,
    _frenado,
    _no_evaluado,
    _rechazo_de_base,
    cotizar_y_decidir,
    moneda_contexto,
    persistir_decision,
)
from app.precio.decision_io import _persistir_uno as _persistir_uno_base
from app.precio.fuentes import reporte
from app.precio.puerto import EscrituraNoDisponible, MetaGoal
from app.precio.tipos import Importe, motivo_permitido
from app.redaction import scrub

logger = logging.getLogger(__name__)

__all__ = [
    "LockOcupado",
    "ConfigInvalida",
    "Resumen",
    "ERRORES_DE_FILA",
    "motor_lock",
    "freno_por_error",
    "freno_no_confirmado",
    "validar_freno_dias_error",
    "cotizar_y_decidir",
    "moneda_contexto",
    "persistir_decision",
    "reporte",
    "correr",
]

_TTL_LOCK_SEGUNDOS = 3600


@dataclass(frozen=True)
class _PreciosVirtual:
    """Contenedor tipado para `cambios.insertar_virtual` (sin `SimpleNamespace`)."""

    p_actual: Importe
    p_aplicado: Importe


class LockOcupado(Exception):
    """El claim `precio:<platform>` lo tiene otro dueno: no se decide."""


class ConfigInvalida(ValueError):
    """La config no sirve para correr (clave ausente o fuera de cota):
    error de uso/config, no un fallo de la corrida (K8: el CLI sale 2)."""


@dataclass
class Resumen:
    """Lo que imprime el CLI: lineas por goal + `decisiones=N escritas=M`.

    `espera_insumos`: esta corrida no decidio porque el insumo del dia no
    habia llegado (J-H3). El gancho `avisar` no manda ni sella el resumen
    diario: lo manda la corrida que si decide.
    """

    decisiones: int = 0
    escritas: int = 0
    cerrados: dict = field(default_factory=dict)
    huerfanas: int = 0
    lineas: tuple = ()
    errores: tuple = ()
    retenidas: int = 0
    espera_insumos: bool = False


def motor_lock(platform: str) -> str:
    """`job_key` del claim en `ads_optimizer_lock`."""
    return f"precio:{platform}"


def _tomar_lock(conn: psycopg.Connection, platform: str, owner: str) -> None:
    job = motor_lock(platform)
    tomar_lock_con_advisory(
        conn,
        job,
        owner,
        ttl_seconds=_TTL_LOCK_SEGUNDOS,
        error_cls=LockOcupado,
        mensaje_ocupado=f"{job} en curso (owner distinto de {owner})",
        mensaje_advisory=f"{job} en curso (advisory en otra sesion)",
    )


def _soltar_lock(conn: psycopg.Connection, platform: str, owner: str) -> None:
    soltar_lock_con_advisory(conn, motor_lock(platform), owner)


def _latir_claim(conn: psycopg.Connection, platform: str, owner: str) -> None:
    """Latido del claim por goal (K2, r4): `heartbeat_at = now()`."""
    latir_lock(conn, motor_lock(platform), owner, etiqueta="corrida")


def freno_por_error(
    conn: psycopg.Connection,
    listing_id: int,
    platform: str,
    hoy: date,
    *,
    dias: int,
    desde: date,
) -> bool:
    """S6: `dias` dias seguidos con `error` -> `frenado(api_error)` hasta un goal nuevo."""
    filas = conn.execute(
        "SELECT DISTINCT (enviado_at AT TIME ZONE 'UTC')::date"
        " FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s AND NOT es_reversa"
        " AND estado = 'error'"
        " AND (enviado_at AT TIME ZONE 'UTC')::date >= %s"
        " AND (enviado_at AT TIME ZONE 'UTC')::date < %s",
        (listing_id, platform, desde, hoy),
    ).fetchall()
    racha = 0
    previo: date | None = None
    for dia in sorted(f[0] for f in filas):
        racha = racha + 1 if previo is not None and dia == previo + timedelta(days=1) else 1
        if racha >= dias:
            return True
        previo = dia
    return False


def freno_no_confirmado(
    conn: psycopg.Connection, listing_id: int, platform: str, *, desde: date
) -> bool:
    """S6/AC5: el ultimo cambio real no-reversa desde el goal vigente quedo
    `no_confirmado` -> frenar hoy, sin cotizar ni PATCH."""
    fila = conn.execute(
        "SELECT estado FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s AND NOT es_reversa AND aplicado"
        " AND (enviado_at AT TIME ZONE 'UTC')::date >= %s"
        " ORDER BY id DESC LIMIT 1",
        (listing_id, platform, desde),
    ).fetchone()
    return fila is not None and fila[0] == "no_confirmado"


def validar_freno_dias_error(settings) -> int:
    """`precio_freno_dias_error` entero 1-14."""
    return _entero_config(settings, "precio_freno_dias_error", minimo=1, maximo=14)


def _persistir_uno(conn: psycopg.Connection, decision, **kwargs) -> tuple[int, int]:
    """Delegado compatible con monkeypatches sobre `corrida.persistir_decision`."""
    return _persistir_uno_base(conn, decision, persistir_fn=persistir_decision, **kwargs)


def _decidir_lectura(
    conn: psycopg.Connection,
    *,
    lectura,
    lid: int,
    mode: str,
    goal_pct,
    prod_id: int,
    platform: str,
    hoy: date,
    config: ConfigPrecio,
    mercado,
    goal_id: int,
    cfg_id,
    corrida_id: int,
    lineas: list,
    errores: list,
    modo_global: str,
    modo_universo: dict,
) -> tuple[int, int]:
    """Una unidad de decidir sobre su Lectura del lote: maquina y persistencia."""
    if lectura.entrada is None:
        modo_fila = modo_efectivo(modo_global, "live", mode)
        if modo_fila == "off":
            return 0, 0
    else:
        clave = f"{platform}/{lectura.entrada.canal}"
        universo = modo_universo.get(clave, "off")
        modo_fila = modo_efectivo(modo_global, universo, mode)
        if modo_fila == "off":
            if clave not in modo_universo and modo_global != "off" and mode != "off":
                msg = scrub(f"listing={lid} {platform} universo_sin_entrada: {clave}")
                logger.error("corrida %s", msg)
                errores.append(msg)
            return 0, 0
    comun = {
        "lid": lid,
        "mode": modo_fila,
        "prod_id": prod_id,
        "platform": platform,
        "goal_id": goal_id,
        "cfg_id": cfg_id,
        "corrida_id": corrida_id,
        "lineas": lineas,
        "errores": errores,
    }
    if lectura.entrada is None:
        motivo = lectura.motivo or "estimacion_motivo_desconocido"
        diagnostico = lectura.diagnostico or f"listing {lid} sin entrada"
        if motivo == "escenario_incoherente" and lectura.diagnostico:
            msg = scrub(f"listing={lid} {platform} fase1: {diagnostico}")
            logger.error("corrida %s", msg)
            errores.append(msg)
        if not motivo_permitido("no_evaluado", motivo):
            crudo = f"motivo_estimacion={motivo!r} {diagnostico}"
            decision = _no_evaluado(
                Decimal(goal_pct), modo_fila, "estimacion_motivo_desconocido", crudo
            )
        else:
            decision = _no_evaluado(Decimal(goal_pct), modo_fila, motivo, diagnostico)
        return _persistir_uno(conn, decision, canal=None, meta=None, **comun)
    entrada = replace(lectura.entrada, mode=modo_fila)
    aislado: Exception | None = None
    try:
        decision, cotiz_id = cotizar_y_decidir(
            conn,
            entrada,
            mercado=mercado,
            oferta=lectura.cotizable,
            oferta_id=lectura.ids["oferta_observation_id"],
            hoy=hoy,
            config=config,
            fees_escenario=lectura.fees_escenario,
        )
    except ValueError as exc:
        return _persistir_uno(
            conn,
            _no_evaluado(Decimal(goal_pct), modo_fila, "escenario_incoherente", str(exc)),
            canal=None,
            meta=None,
            **comun,
        )
    except ERRORES_DE_FILA as exc:
        aislado = exc
    except psycopg.Error:
        raise
    except Exception as exc:
        aislado = exc
    if aislado is not None:
        msg = scrub(f"listing={lid} {platform} fase1: {aislado.__class__.__name__}: {aislado}")
        logger.error("corrida %s", msg)
        errores.append(msg)
        return _persistir_uno(
            conn,
            _no_evaluado(Decimal(goal_pct), modo_fila, "escenario_incoherente", msg),
            canal=None,
            meta=None,
            **comun,
        )
    meta = {**lectura.ids, "cotizacion_id": cotiz_id, "canal": entrada.canal}
    return _persistir_uno(
        conn,
        decision,
        canal=entrada.canal,
        meta=meta,
        senal_evidencia=entrada.senal.evidencia,
        **comun,
    )


def _decidir_pendientes(
    conn: psycopg.Connection,
    mercado,
    config: ConfigPrecio,
    hoy: date,
    ahora: datetime,
    *,
    owner: str,
    freno_dias: int,
    cfg_id,
    corrida_id: int,
    modo_global: str,
    modo_universo: dict,
) -> tuple[int, list[str], list[str], int, bool]:
    """Una decision persistida por goal vigente sin decision hoy (J)."""
    platform = mercado.clave
    goals = conn.execute(
        "SELECT g.id, g.listing_id, g.platform, g.mode::text, g.margen_goal_pct,"
        " g.valid_from, l.product_id, l.external_id, l.seller_sku"
        " FROM precio_goal g JOIN listing l"
        " ON l.id = g.listing_id AND l.platform = g.platform"
        " WHERE g.platform = %s AND g.valid_from <= %s"
        " AND (g.valid_to IS NULL OR g.valid_to > %s)"
        " ORDER BY g.listing_id",
        (platform, hoy, hoy),
    ).fetchall()
    decididas = {
        f[0]
        for f in conn.execute(
            "SELECT listing_id FROM precio_decision WHERE platform = %s AND decision_date = %s",
            (platform, hoy),
        ).fetchall()
    }
    goals = [goal for goal in goals if goal[1] not in decididas]
    activas = _a_la_venta(conn, mercado, hoy) if goals else None
    unidades = mercado.unidades(conn, [goal[1] for goal in goals]) if goals else {}
    decisiones = 0
    saltadas = 0
    lineas: list[str] = []
    errores: list[str] = []
    goal_de: dict[int, int] = {}
    lote: list = []
    for gid, lid, plat, mode, goal_pct, vigente_desde, prod_id, _ext, _sku in goals:
        goal_de[lid] = gid
        _latir_claim(conn, platform, owner)
        modo_freno = modo_efectivo(modo_global, "live", mode)
        if modo_freno == "off":
            continue
        decision_corte = None
        if activas is not None and lid not in activas:
            decision_corte = _no_evaluado(Decimal(goal_pct), modo_freno, "no_a_la_venta")
        elif freno_no_confirmado(conn, lid, plat, desde=vigente_desde):
            decision_corte = _frenado(
                Decimal(goal_pct),
                modo_freno,
                "no_confirmado",
                "ultimo cambio real sin confirmar desde el goal vigente",
            )
        elif freno_por_error(conn, lid, plat, hoy, dias=freno_dias, desde=vigente_desde):
            decision_corte = _frenado(
                Decimal(goal_pct),
                modo_freno,
                "api_error",
                f"error en los {freno_dias} dias previos",
            )
        if decision_corte is not None:
            persistida, saltada = _persistir_uno(
                conn,
                decision_corte,
                lid=lid,
                mode=modo_freno,
                prod_id=prod_id,
                canal=None,
                meta=None,
                platform=platform,
                goal_id=gid,
                cfg_id=cfg_id,
                corrida_id=corrida_id,
                lineas=lineas,
                errores=errores,
            )
            decisiones += persistida
            saltadas += saltada
            continue
        lote.append((unidades[lid], mode, goal_pct, vigente_desde, prod_id))

    if lote:
        metas = {
            unidad.listing_id: MetaGoal(
                goal=Decimal(goal_pct), mode=mode, goal_vigente_desde=vigente
            )
            for (unidad, mode, goal_pct, vigente, _prod) in lote
        }
        lecturas = mercado.insumos(
            conn,
            tuple(unidad for (unidad, *_resto) in lote),
            hoy=hoy,
            ahora=ahora,
            config=config,
            metas=metas,
        )
        espera = insumo_atrasado([lectura.motivo for lectura in lecturas.values()], ahora_utc=ahora)
        if espera is not None:
            lineas.append(f"{platform}: {espera}; decide el siguiente repaso")
            return decisiones, lineas, errores, saltadas, True
        for unidad, mode, goal_pct, _vigente, prod_id in lote:
            persistida, saltada = _decidir_lectura(
                conn,
                lectura=lecturas[unidad.listing_id],
                lid=unidad.listing_id,
                mode=mode,
                goal_pct=goal_pct,
                prod_id=prod_id,
                platform=platform,
                hoy=hoy,
                config=config,
                mercado=mercado,
                goal_id=goal_de[unidad.listing_id],
                cfg_id=cfg_id,
                corrida_id=corrida_id,
                lineas=lineas,
                errores=errores,
                modo_global=modo_global,
                modo_universo=modo_universo,
            )
            decisiones += persistida
            saltadas += saltada
    return decisiones, lineas, errores, saltadas, False


_SQL_SETTINGS_VIGENTE = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
_SQL_GOAL_POR_ID = "SELECT mode::text, valid_from, valid_to FROM precio_goal WHERE id = %s"
_SQL_RETENER = "INSERT INTO precio_retencion (decision_id, corrida_id, causa) VALUES (%s, %s, %s)"
_SQL_PENDIENTE_LIBERADA = (
    "SELECT decision_id FROM v_precio_decision_liberada WHERE decision_id = ANY(%s)"
)


def _modo_releido(conn, platform: str, canal, mode: str) -> str:
    """Modo efectivo con la config vigente releida; ilegible = `off` (fail closed)."""
    try:
        fila_cfg = conn.execute(_SQL_SETTINGS_VIGENTE).fetchone()
        settings = fila_cfg[0] if fila_cfg is not None else None
        if settings is None:
            raise ValueError("config sin precio_modo_global: sin config_version")
        return modo_efectivo(
            _modo_config(settings, "precio_modo_global"),
            _modo_universo_config(settings, "precio_modo_universo").get(
                f"{platform}/{canal}", "off"
            ),
            mode,
        )
    except psycopg.Error:
        raise
    except Exception:
        return "off"


def _retener_por_compuerta(conn, corrida_id, veredictos, liberadas, canal, did) -> bool:
    veredicto = veredictos.get(canal)
    if not isinstance(veredicto, Retenida) or did in liberadas:
        return False
    conn.execute(_SQL_RETENER, (did, corrida_id, veredicto.causa))
    return True


def _aplicar_pendientes(
    conn: psycopg.Connection,
    mercado,
    veredictos: dict,
    hoy: date,
    *,
    owner: str,
    ahora: datetime,
    corrida_id: int,
    corte: int,
) -> tuple[int, int, int, list[str]]:
    """Aplica lo pendiente de HOY que la compuerta deja pasar (J).

    Relee el catalogo activo (`_a_la_venta`), la config y el goal antes de cada
    fila (E.2) para que una publicacion que salga del catalogo a mitad de una
    corrida larga nunca reciba un PATCH.
    """
    platform = mercado.clave
    pendientes = conn.execute(
        "SELECT d.id, d.listing_id, d.mode, d.p_actual, d.p_actual_currency,"
        " d.p_aplicado, d.p_aplicado_currency, d.goal_id, d.canal::text"
        " FROM precio_decision d"
        " WHERE d.platform = %s AND d.decision_date = %s"
        " AND d.resultado IN ('subir', 'bajar')"
        " AND NOT EXISTS (SELECT 1 FROM precio_cambio c WHERE c.decision_id = d.id)"
        " ORDER BY d.listing_id",
        (platform, hoy),
    ).fetchall()
    if not pendientes:
        return 0, 0, 0, []
    liberadas = {
        fila[0]
        for fila in conn.execute(
            _SQL_PENDIENTE_LIBERADA, ([fila[0] for fila in pendientes],)
        ).fetchall()
    }
    cortacircuito = Cortacircuito(seguidos=0, limite=corte)
    escritas = virtuales = retenidas = 0
    errores: list[str] = []
    parche_sin_sellar = False
    for (
        did,
        lid,
        mode,
        p_actual,
        p_actual_mon,
        p_aplicado,
        p_aplicado_mon,
        gid,
        canal,
    ) in pendientes:
        _latir_claim(conn, platform, owner)
        if mode not in ("shadow", "live"):
            msg = scrub(f"listing={lid} {platform} modo_desconocido: {mode!r}")
            logger.error("corrida %s", msg)
            errores.append(msg)
            continue
        activas = _a_la_venta(conn, mercado, hoy)
        if activas is not None and lid not in activas:
            logger.info(
                "corrida listing=%s %s no_a_la_venta: decision %s sin aplicar", lid, platform, did
            )
            continue
        if _retener_por_compuerta(conn, corrida_id, veredictos, liberadas, canal, did):
            retenidas += 1
            continue
        efectivo = _modo_releido(conn, platform, canal, mode)
        alcanza = efectivo == "live" if mode == "live" else efectivo != "off"
        if not alcanza:
            conn.execute(_SQL_RETENER, (did, corrida_id, "apagador"))
            retenidas += 1
            continue
        goal = conn.execute(_SQL_GOAL_POR_ID, (gid,)).fetchone() if gid is not None else None
        vigente = goal is not None and goal[1] <= hoy and (goal[2] is None or goal[2] > hoy)
        if not vigente or (mode == "live" and goal[0] != "live"):
            conn.execute(_SQL_RETENER, (did, corrida_id, "goal_cambiado"))
            retenidas += 1
            continue
        if cortacircuito.abierto:
            conn.execute(_SQL_RETENER, (did, corrida_id, "corte_errores"))
            retenidas += 1
            continue
        if mode == "shadow":
            try:
                cambios_mod.insertar_virtual(
                    conn,
                    did,
                    lid,
                    platform,
                    _PreciosVirtual(
                        p_actual=Importe(Decimal(p_actual), p_actual_mon),
                        p_aplicado=Importe(Decimal(p_aplicado), p_aplicado_mon),
                    ),
                    corrida_id=corrida_id,
                )
            except ERRORES_DE_FILA as exc:
                _rechazo_de_base(errores, lid, platform, exc)
                continue
            except psycopg.Error:
                raise
            except Exception as exc:
                msg = scrub(f"listing={lid} {platform} {exc.__class__.__name__}: {exc}")
                logger.error("corrida %s", msg)
                errores.append(msg)
                continue
            virtuales += 1
            continue
        if parche_sin_sellar:
            continue
        try:
            res = cambios_mod.aplicar(conn, mercado, did, ahora=ahora, corrida_id=corrida_id)
        except ERRORES_DE_FILA as exc:
            _rechazo_de_base(errores, lid, platform, exc)
            cortacircuito = cortacircuito.tras("error")
            continue
        except psycopg.Error:
            raise
        except EscrituraNoDisponible as exc:
            msg = scrub(f"listing={lid} {platform} parche_sin_sellar: {exc}")
            logger.error("corrida %s", msg)
            errores.append(msg)
            parche_sin_sellar = True
            continue
        except Exception as exc:
            msg = scrub(f"listing={lid} {platform} {exc.__class__.__name__}: {exc}")
            logger.error("corrida %s", msg)
            errores.append(msg)
            cortacircuito = cortacircuito.tras("error")
            continue
        cortacircuito = cortacircuito.tras(res.estado)
        if res.id_cambio is not None:
            escritas += 1
    return escritas, virtuales, retenidas, errores


def _abrir_corrida(
    conn: psycopg.Connection, platform: str, owner: str, cfg_id, modo_global: str, hoy: date
) -> int:
    conn.execute(
        "UPDATE precio_corrida SET estado = 'abortada' WHERE platform = %s AND estado = 'abierta'",
        (platform,),
    )
    return conn.execute(
        "INSERT INTO precio_corrida (platform, fecha, owner, config_version_id, modo_global)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (platform, hoy, owner, cfg_id, modo_global),
    ).fetchone()[0]


def _cerrar_corrida(
    conn: psycopg.Connection,
    corrida_id: int,
    *,
    decisiones: int,
    movidas: int,
    virtuales: int,
    retenidas: int,
    errores: int,
    saltadas: int,
) -> None:
    conn.execute(
        "UPDATE precio_corrida SET estado = 'cerrada', cerrada_at = now(),"
        " decisiones = %s, movidas = %s, virtuales = %s, retenidas = %s,"
        " errores = %s, saltadas_por_edicion = %s WHERE id = %s",
        (decisiones, movidas, virtuales, retenidas, errores, saltadas, corrida_id),
    )


def correr(
    conn: psycopg.Connection,
    mercado,
    *,
    owner: str,
    avisar=None,
) -> Resumen:
    """La corrida de una plataforma (conn en autocommit, rol decides)."""
    platform = mercado.clave
    if not conn.autocommit:
        raise ValueError("correr exige conexión en autocommit: cada bloque confirma al salir (S5)")
    try:
        fila_cfg = conn.execute(
            "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if fila_cfg is None:
            raise ValueError("config sin precio_catalogo_max_dias_sin_reportar: sin config_version")
        cfg_id, settings = fila_cfg
        config = leer_config(settings)
        motor = leer_config_motor(settings)
        modo_global = motor.modo_global
        modo_universo = dict(motor.modo_universo)
        freno_dias = motor.freno_dias_error
    except ValueError as exc:
        raise ConfigInvalida(str(exc)) from exc
    ahora_raw = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
    ahora = ahora_raw.replace(tzinfo=UTC)
    hoy = ahora.date()

    _tomar_lock(conn, platform, owner)
    try:
        corrida_id = _abrir_corrida(conn, platform, owner, cfg_id, modo_global, hoy)
        huerfanas = cambios_mod.cerrar_huerfanas(conn, mercado)
        cerrados = cambios_mod.cerrar_por_observacion(conn, mercado, hoy)
        if modo_global == "off":
            decisiones, lineas, errores, saltadas, espera_insumos = 0, [], [], 0, False
        else:
            decisiones, lineas, errores, saltadas, espera_insumos = _decidir_pendientes(
                conn,
                mercado,
                config,
                hoy,
                ahora,
                owner=owner,
                freno_dias=freno_dias,
                cfg_id=cfg_id,
                corrida_id=corrida_id,
                modo_global=modo_global,
                modo_universo=modo_universo,
            )
        veredictos = _medir_compuerta(conn, mercado, hoy, motor, corrida_id)
        escritas, virtuales, retenidas, errores_apl = _aplicar_pendientes(
            conn,
            mercado,
            veredictos,
            hoy,
            owner=owner,
            ahora=ahora,
            corrida_id=corrida_id,
            corte=motor.corte_errores_consecutivos,
        )
        errores = [*errores, *errores_apl]
        _cerrar_corrida(
            conn,
            corrida_id,
            decisiones=decisiones,
            movidas=escritas,
            virtuales=virtuales,
            retenidas=retenidas,
            errores=len(errores),
            saltadas=saltadas,
        )
        resumen = Resumen(
            decisiones=decisiones,
            escritas=escritas,
            cerrados=cerrados,
            huerfanas=huerfanas,
            lineas=tuple(lineas),
            errores=tuple(errores),
            retenidas=retenidas,
            espera_insumos=espera_insumos,
        )
        if avisar is not None:
            try:
                avisar(conn, platform, hoy, resumen)
            except Exception as exc:
                msg = scrub(f"avisar: {exc.__class__.__name__}: {exc}")
                logger.error("corrida %s", msg)
                resumen = replace(resumen, errores=resumen.errores + (msg,))
        return resumen
    finally:
        try:
            _soltar_lock(conn, platform, owner)
        except Exception as exc:
            logger.error(
                "corrida %s", scrub(f"soltar lock {platform}: {exc.__class__.__name__}: {exc}")
            )
