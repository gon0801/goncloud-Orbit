"""Corrida diaria del motor de precios (REPRICING 01 A.5 + 02 S.1).

Camino por plataforma: claim en `ads_optimizer_lock` (`precio:<platform>`,
patron copiado de `app/cycle.py`, NO importado: `cycle` trae `app.apply`)
+ advisory lock de sesion; fila `precio_corrida` (las `abierta` viejas
pasan a `abortada`); huerfanas a `error`; cierre por observacion; decidir
(persiste una decision por goal vigente sin decision hoy, con su modo
efectivo; la segunda corrida del dia salta los ya decididos por el
UNIQUE; con el global en `off` no decide); compuerta (fusible por
universo e insumo sistemico desde lo persistido hoy, con fila
`precio_compuerta_medicion` por universo); aplicar (retiene lo del
universo `Retenida` no liberado; relee config y goal antes de cada
escritura y retiene `apagador`/`goal_cambiado`; corte por errores seguidos
de escritura `live`; `live` con `cambios.aplicar`, orden INSERT+COMMIT ->
PATCH -> sello; en `shadow` inserta el cambio virtual
`confirmado`/`virtual`, sin PATCH);
cierra la fila; libera el claim al salir. Sin fase "en memoria": lo que
un paso deja, el siguiente lo lee de la base. Se puede lanzar N veces al
dia: converge (REPRICING 02 S.1 mato el cupo y la prioridad; S.3 sumo la
compuerta: fusible por universo, insumo sistemico medido desde lo
persistido, corte por errores y liberacion del dueno).

La corrida recibe UN `Mercado` duck-typed y no conoce Amazon. Los
insumos del dia salen de `mercado.insumos` (UNA llamada por lote tras
los frenos por unidad), las cotizaciones de `mercado.cotizar` (ya
`puerto.Cotizacion`, fees partidos) y la escritura de `cambios.aplicar`.
`platform` sale de `mercado.clave`.

El reloj es el de la base (`SELECT now()` una vez): `hoy` y `as_of`
salen de ahi, nunca del reloj local (este modulo no llama `datetime.now`
ni importa `time`). `L` es el importe del escenario (el canal es de E.3/E.4:
no se toca). Sin escenario no hay decision: `no_evaluado(precio_ausente)`
es una fila, nunca un objeto inventado.

Los frenos de S6 salen `frenado(api_error)` (dias de `error` segun
config), `frenado(no_confirmado)` (el ultimo cambio real desde el goal
vigente no lo confirmo la observacion; se reanuda con un goal nuevo) y
el detalle va al log: `MOTIVOS_FRENADO` vive en `app/precio/tipos.py`.
`decisiones` cuenta filas `precio_decision` persistidas por esta corrida
(B.2: lo que no dejo fila no cuenta ni sale en lineas); `escritas` cuenta
cambios reales creados por esta corrida (= PATCH intentados). Toda
decision guarda `goal_id` y `l_origen='politica`; `subir`/`bajar` guardan
`verificacion='cotizada'` (la maquina siempre cotiza en este paso).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import psycopg.errors
from psycopg.types.json import Json

import app.precio.cambios as cambios_mod
from app.estimacion_venta import DetalleFee
from app.precio.compuerta import (
    Anterior,
    Cortacircuito,
    Intencion,
    Retenida,
    armar_deriva,
    es_nueva,
    evaluar,
    modo_efectivo,
)
from app.precio.config import ConfigPrecio, leer_config, leer_config_motor
from app.precio.config import _entero as _entero_config
from app.precio.config import _modo as _modo_config
from app.precio.config import _modo_universo as _modo_universo_config
from app.precio.puerto import EscrituraNoDisponible, Miembro, UnidadPrecio
from app.precio.reglas import decidir
from app.precio.tipos import (
    Decision,
    EntradaDecision,
    Importe,
    PideCotizacion,
    motivo_permitido,
)
from app.redaction import scrub

logger = logging.getLogger(__name__)

__all__ = [
    "LockOcupado",
    "Resumen",
    "motor_lock",
    "freno_por_error",
    "reporte",
    "correr",
]

_TTL_LOCK_SEGUNDOS = 3600


class LockOcupado(Exception):
    """El claim `precio:<platform>` lo tiene otro dueno: no se decide."""


class ConfigInvalida(ValueError):
    """La config no sirve para correr (clave ausente o fuera de cota):
    error de uso/config, no un fallo de la corrida (K8: el CLI sale 2)."""


@dataclass
class Resumen:
    """Lo que imprime el CLI: lineas por goal + `decisiones=N escritas=M`."""

    decisiones: int = 0
    escritas: int = 0
    cerrados: dict = field(default_factory=dict)
    huerfanas: int = 0
    lineas: tuple = ()
    errores: tuple = ()
    retenidas: int = 0


def motor_lock(platform: str) -> str:
    """`job_key` del claim en `ads_optimizer_lock`."""
    return f"precio:{platform}"


# Claim ATOMICO en UNA sentencia (patron copiado de `app/cycle.py`, sin
# importar: `cycle` trae `app.apply`). Sin fila devuelta = lock vigente
# ajeno. TTL en el WHERE, jamas SELECT-luego-INSERT.
_SQL_CLAIM = """
INSERT INTO ads_optimizer_lock (job_key, owner, claimed_at, heartbeat_at, ttl_seconds)
VALUES (%s, %s, now(), now(), %s)
ON CONFLICT (job_key) DO UPDATE
   SET owner = EXCLUDED.owner, claimed_at = now(), heartbeat_at = now(),
       ttl_seconds = EXCLUDED.ttl_seconds
 WHERE ads_optimizer_lock.heartbeat_at
       + make_interval(secs => ads_optimizer_lock.ttl_seconds) <= now()
RETURNING claimed_at
"""

_SQL_LIBERAR = "DELETE FROM ads_optimizer_lock WHERE job_key = %s AND owner = %s"

# Advisory de la corrida: el de sesion, no el transaccional (S5 exige
# autocommit para INSERT+COMMIT -> PATCH: un xact-lock moriria en el
# primer COMMIT; desviacion declarada del `pg_advisory_xact_lock` de S5).
# Con tope cero: `pg_try_advisory_lock` (K2, r4) en vez de esperar sin
# tope; si no se obtiene, se suelta el claim propio y sale `LockOcupado`
# de inmediato (el `finally` ya libera lo propio).
_SQL_ADVISORY_TOMAR = "SELECT pg_try_advisory_lock(hashtext('precio:' || %s))"
_SQL_ADVISORY_SOLTAR = "SELECT pg_advisory_unlock(hashtext('precio:' || %s))"

# Latido del claim (K2, r4): patron `_SQL_HEARTBEAT` de `app/cycle.py`
# l.374, copiado, no importado (`cycle` trae `app.apply`).
_SQL_LATIDO = "UPDATE ads_optimizer_lock SET heartbeat_at = now() WHERE job_key = %s AND owner = %s"


def _tomar_lock(conn: psycopg.Connection, platform: str, owner: str) -> None:
    fila = conn.execute(_SQL_CLAIM, (motor_lock(platform), owner, _TTL_LOCK_SEGUNDOS)).fetchone()
    if fila is None:
        raise LockOcupado(f"{motor_lock(platform)} en curso (owner distinto de {owner})")
    if not conn.execute(_SQL_ADVISORY_TOMAR, (platform,)).fetchone()[0]:
        conn.execute(_SQL_LIBERAR, (motor_lock(platform), owner))
        raise LockOcupado(f"{motor_lock(platform)} en curso (advisory en otra sesion)")


def _soltar_lock(conn: psycopg.Connection, platform: str, owner: str) -> None:
    try:
        conn.execute(_SQL_ADVISORY_SOLTAR, (platform,)).fetchone()
    finally:
        conn.execute(_SQL_LIBERAR, (motor_lock(platform), owner))


def _latir_claim(conn: psycopg.Connection, platform: str, owner: str) -> None:
    """Latido del claim por goal (K2, r4): `heartbeat_at = now()`.

    Fail-open como `cycle` (el TTL es el backstop): un latido fallido se
    registra con `scrub` y no tumba la corrida.
    """
    try:
        conn.execute(_SQL_LATIDO, (motor_lock(platform), owner))
    except psycopg.Error as exc:
        logger.warning("corrida latido fallo (TTL es el backstop): %s", scrub(str(exc)))


def freno_por_error(
    conn: psycopg.Connection,
    listing_id: int,
    platform: str,
    hoy: date,
    *,
    dias: int,
    desde: date,
) -> bool:
    """S6: `dias` dias seguidos con `error` -> `frenado(api_error)` hasta un goal nuevo.

    La corrida SIGUIENTE al ultimo dia de error ya frena (no al tercer dia:
    con 3 errores los dias 1-3, el dia 4 frena). El dia frenado no deja
    `error`, asi que la ventana con hueco no bastaba (3 errores, 1 frenado,
    3 errores...): una vez junta la racha en `[desde, hoy)` queda frenado
    aunque los dias siguientes vengan limpios. Se reanuda con un goal nuevo
    (el `desde` posterior excluye la racha vieja): el patron de #6, #10 y
    `no_confirmado`. Solo cambios reales (la reversa es del dueno, el
    virtual no falla). Declarado: con el cooldown sellado no se alcanza en
    produccion.
    """
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
    `no_confirmado` -> frenar hoy, sin cotizar ni PATCH.

    Se reanuda con un goal nuevo (el `desde` posterior lo excluye): el mismo
    patron de #6 y #10. Solo cambios reales (`aplicado`): el virtual nace
    cerrado y la reversa es del dueno.
    """
    fila = conn.execute(
        "SELECT estado FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s AND NOT es_reversa AND aplicado"
        " AND (enviado_at AT TIME ZONE 'UTC')::date >= %s"
        " ORDER BY id DESC LIMIT 1",
        (listing_id, platform, desde),
    ).fetchone()
    return fila is not None and fila[0] == "no_confirmado"


def _numero_entero(settings, clave: str, *, minimo: int, maximo: int) -> int:
    """El ayudante unico de `app/precio/config.py` (REPRICING 02 0.a: la
    copia privada se borro)."""
    return _entero_config(settings, clave, minimo=minimo, maximo=maximo)


def validar_freno_dias_error(settings) -> int:
    """`precio_freno_dias_error` entero 1-14 (punto (f): vive en `corrida.py`)."""
    return _numero_entero(settings, "precio_freno_dias_error", minimo=1, maximo=14)


def _no_evaluado(goal: Decimal, mode: str, motivo: str, diagnostico: str = "") -> Decision:
    if diagnostico:
        # K6 (r4): el diagnostico puede traer texto de la excepcion.
        logger.info("corrida no_evaluado %s: %s", motivo, scrub(diagnostico))
    return Decision(
        resultado="no_evaluado",
        motivo=motivo,
        m_actual=None,
        goal=goal,
        p_actual=None,
        p_objetivo=None,
        p_aplicado=None,
        componentes=None,
        u15=None,
        u60=None,
        n15=None,
        n60=None,
        racha=None,
        perdiendo=None,
        aplicado=False,
        mode=mode,
    )


def _frenado(goal: Decimal, mode: str, motivo: str, diagnostico: str) -> Decision:
    # K6 (r4): el diagnostico puede traer texto de la excepcion.
    logger.info("corrida frenado %s: %s", motivo, scrub(diagnostico))
    return Decision(
        resultado="frenado",
        motivo=motivo,
        m_actual=None,
        goal=goal,
        p_actual=None,
        p_objetivo=None,
        p_aplicado=None,
        componentes=None,
        u15=None,
        u60=None,
        n15=None,
        n60=None,
        racha=None,
        perdiendo=None,
        aplicado=False,
        mode=mode,
    )


def _evento_cotizacion(listing_id: int, platform: str, hoy: date, intento: int) -> str:
    """Identidad de la cotizacion: par + dia + intento (UNIQUE en 0039)."""
    return f"precio-cotiz-{listing_id}-{platform}-{hoy.isoformat()}-{intento}"


def _detalle_a_dict(detalle: DetalleFee) -> dict:
    """Arbol `DetalleFee` -> dicts de `precio_cotizacion.fee_details`."""
    return {
        "fee_type": detalle.fee_type,
        "final_fee": str(detalle.final_fee),
        "tax_amount": None if detalle.tax_amount is None else str(detalle.tax_amount),
        "included_fee_details": [
            _detalle_a_dict(incluido) for incluido in detalle.included_fee_details
        ],
    }


def _persistir_cotizacion(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    platform: str,
    hoy: date,
    oferta_id: int,
    pedido: PideCotizacion,
    cot,
) -> int:
    """La cotizacion nace ANTES que la decision (S5 r3-1): es su insumo."""
    evento = _evento_cotizacion(listing_id, platform, hoy, pedido.intento)
    if cot.fees is not None:
        total, total_moneda = cot.fees.total.valor, pedido.precio.moneda
        detalles = [_detalle_a_dict(det) for det in cot.fees.detalle]
        estimada_en, codigo, estado = cot.estimada_en, None, "success"
    else:
        total, total_moneda, detalles = None, None, []
        estimada_en, codigo, estado = None, cot.error_code or "desconocido", "error"
    return conn.execute(
        "INSERT INTO precio_cotizacion (listing_id, platform, intento, oferta_observation_id,"
        " quoted_price, quoted_price_currency, total_fees, total_fees_currency, fee_details,"
        " fees_estimated_at, estado, error_code, source_event_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            listing_id,
            platform,
            pedido.intento,
            oferta_id,
            pedido.precio.valor,
            pedido.precio.moneda,
            total,
            total_moneda,
            Json(detalles),
            estimada_en,
            estado,
            codigo,
            evento,
        ),
    ).fetchone()[0]


def cotizar_y_decidir(
    conn: psycopg.Connection,
    entrada: EntradaDecision,
    *,
    mercado,
    oferta,
    oferta_id: int,
    hoy: date,
    config: ConfigPrecio,
    fees_escenario,
) -> tuple[Decision, int | None]:
    """Maquina S4 #3: como maximo dos cotizaciones reales, persistidas.

    Devuelve `(decision_final, cotizacion_id_usada)`. Cotiza con
    `mercado.cotizar` (ya `puerto.Cotizacion`, fees partidos) y decide con
    `decidir(..., cotizaciones, fees_escenario)`.

    K1: `observed_at` se captura DESPUES de la respuesta (el servidor
    estampa `TimeOfFeesEstimation` al recibir; con el `ahora` congelado toda
    cotizacion real moria con `fee_contrato_incompatible`). El reloj lo
    consulta el adaptador contra la base en ese momento, nunca un valor
    congelado. Un pedido que no se puede cotizar es `fee_error` local, no
    excepcion.
    """
    hechas: list = []
    ultimo_id: int | None = None
    for _ in range(3):
        salida = decidir(
            entrada,
            hoy=hoy,
            config=config,
            cotizaciones=tuple(hechas),
            fees_escenario=fees_escenario,
        )
        if not isinstance(salida, PideCotizacion):
            return salida, ultimo_id
        evento = _evento_cotizacion(entrada.listing_id, entrada.platform, hoy, salida.intento)
        reuso = mercado.cotizacion_guardada(
            conn,
            evento=evento,
            listing_id=entrada.listing_id,
            hoy=hoy,
            oferta_id=oferta_id,
            pedido=salida,
        )
        if reuso is not None:
            ultimo_id, cot = reuso
        else:
            cot = mercado.cotizar(conn, oferta, salida.precio)
            try:
                ultimo_id = _persistir_cotizacion(
                    conn,
                    listing_id=entrada.listing_id,
                    platform=entrada.platform,
                    hoy=hoy,
                    oferta_id=oferta_id,
                    pedido=salida,
                    cot=cot,
                )
            except psycopg.errors.UniqueViolation:
                # Carrera: otro proceso cito el mismo intento hoy; releer.
                reuso = mercado.cotizacion_guardada(
                    conn,
                    evento=evento,
                    listing_id=entrada.listing_id,
                    hoy=hoy,
                    oferta_id=oferta_id,
                    pedido=salida,
                )
                if reuso is None:
                    raise
                ultimo_id, cot = reuso
        hechas.append(cot)
    raise AssertionError("la maquina pidio una tercera cotizacion (S4 #3: maximo dos)")


def moneda_contexto(conn: psycopg.Connection, listing_id: int, platform: str) -> str:
    """Moneda para `p_actual_currency` cuando la decision no trae P.

    La columna es NOT NULL: sin escenario no hay P pero la fila existe
    igual (S5). Cadena con dato real: ultima oferta del par y, si nunca
    hubo, ultima moneda observada en la plataforma (cualquier listing:
    la moneda es por plataforma, no por listing). Sin ninguna de las dos
    revienta visible (`ValueError`): no se inventa, no hay mapa estatico
    en codigo (una fuente: las observaciones).
    """
    fila = conn.execute(
        "SELECT price_currency FROM estimacion_oferta_observation"
        " WHERE listing_id = %s AND platform = %s"
        " ORDER BY observed_at DESC LIMIT 1",
        (listing_id, platform),
    ).fetchone()
    if fila is not None:
        return fila[0]
    fila = conn.execute(
        "SELECT price_currency FROM estimacion_oferta_observation"
        " WHERE platform = %s"
        " ORDER BY observed_at DESC LIMIT 1",
        (platform,),
    ).fetchone()
    if fila is not None:
        return fila[0]
    raise ValueError(f"sin moneda observable para {platform}: sin ofertas")


def persistir_decision(
    conn: psycopg.Connection,
    decision: Decision,
    *,
    listing_id: int,
    platform: str,
    product_id: int,
    canal: str | None,
    escenario_id: int | None,
    fee_observation_id: int | None,
    cotizacion_id: int | None,
    config_version_id: int | None,
    goal_id: int | None = None,
    l_origen: str | None = None,
    envio_muestra_id: int | None = None,
    senal_evidencia: str | None = None,
    verificacion: str | None = None,
) -> int:
    """Una fila por (listing, platform, dia): la segunda del dia revienta
    por UNIQUE (la corrida salta esos goals antes). El trigger fija
    `decision_date` y `goal` desde la fila vigente del mismo mode (con
    `goal_id`, valida ESE goal)."""
    comp = decision.componentes
    moneda = comp.p_actual.moneda if comp else moneda_contexto(conn, listing_id, platform)
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, product_id,"
        " canal, m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, escenario_id,"
        " fee_observation_id, cotizacion_id, u15, u60, n15, n60, racha_senal, perdiendo,"
        " buy_box_is_own, mode, config_version_id, goal_id, l_origen, envio_muestra_id,"
        " senal_evidencia, verificacion)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s)"
        " RETURNING id",
        (
            listing_id,
            platform,
            decision.resultado,
            decision.motivo,
            product_id,
            canal,
            decision.m_actual,
            decision.goal,
            comp.p_actual.valor if comp else None,
            moneda,
            decision.p_objetivo.valor if decision.p_objetivo else None,
            decision.p_objetivo.moneda if decision.p_objetivo else moneda,
            decision.p_aplicado.valor if decision.p_aplicado else None,
            decision.p_aplicado.moneda if decision.p_aplicado else moneda,
            comp.ingreso.valor if comp else None,
            comp.ingreso.moneda if comp else moneda,
            comp.costo.valor if comp else None,
            comp.costo.moneda if comp else moneda,
            comp.fees.valor if comp else None,
            comp.fees.moneda if comp else moneda,
            comp.envio.valor if comp else None,
            comp.envio.moneda if comp else moneda,
            comp.isr.valor if comp else None,
            comp.isr.moneda if comp else moneda,
            escenario_id,
            fee_observation_id,
            cotizacion_id,
            decision.u15,
            decision.u60,
            decision.n15,
            decision.n60,
            decision.racha,
            decision.perdiendo,
            decision.buy_box_is_own,
            decision.mode,
            config_version_id,
            goal_id,
            l_origen,
            envio_muestra_id,
            senal_evidencia,
            verificacion,
        ),
    ).fetchone()[0]


def _linea(lid: int, platform: str, mode: str, decision: Decision) -> str:
    return f"listing={lid} {platform} {mode} {decision.resultado} {decision.motivo or '-'}"


def _persistir_uno(
    conn: psycopg.Connection,
    decision: Decision,
    *,
    lid: int,
    mode: str,
    prod_id: int,
    canal,
    meta,
    platform: str,
    goal_id: int,
    cfg_id,
    lineas: list,
    errores: list,
    senal_evidencia: str | None = None,
) -> tuple[int, int]:
    """Persiste UNA decision ya decidida; devuelve `(persistida, saltada)` (0/1).

    B.2: lo que no dejo fila no cuenta ni sale en lineas (la linea la agrega
    aqui mismo). `goal_reemplazado` (el dueno reedito el goal a media
    corrida): sin fila, error `goal_cambiado`, `saltada=True`. Cualquier otro
    fallo que no es de base (p. ej. `moneda_contexto` sin moneda observable,
    K8): sin fila, error. Un error de base (`psycopg.Error`) aborta: no es
    una publicacion, es la infra.
    """
    verificacion = (
        (decision.verificacion or "cotizada") if decision.resultado in ("subir", "bajar") else None
    )
    try:
        persistir_decision(
            conn,
            decision,
            listing_id=lid,
            platform=platform,
            # MeLi: la cuenta (costo y muestra) es del miembro que manda, no
            # del ancla; el trigger de la 0054 exige la muestra de ESE producto.
            product_id=(meta.get("product_id") if meta else None) or prod_id,
            canal=canal,
            escenario_id=meta["escenario_id"] if meta else None,
            fee_observation_id=meta["fee_observation_id"] if meta else None,
            cotizacion_id=meta["cotizacion_id"] if meta else None,
            config_version_id=cfg_id,
            goal_id=goal_id,
            l_origen=meta["logistica_origen"] if meta else None,
            envio_muestra_id=meta["envio_muestra_id"] if meta else None,
            senal_evidencia=senal_evidencia,
            verificacion=verificacion,
        )
    except psycopg.errors.CheckViolation as exc:
        # El goal se reedito a media corrida (`goal_reemplazado` de la
        # 0054): la publicacion no deja fila (sin motivo en vocabulario
        # para reedicion) y el error scrubbado va al resumen (B2).
        if "goal_reemplazado" not in str(exc):
            raise
        msg = scrub(f"listing={lid} {platform} goal_cambiado: {exc}")
        logger.error("corrida %s", msg)
        errores.append(msg)
        return 0, 1
    except psycopg.Error:
        raise
    except Exception as exc:  # noqa: BLE001 - una publicacion no tumba a las demas
        msg = scrub(f"listing={lid} {platform} {exc.__class__.__name__}: {exc}")
        logger.error("corrida %s", msg)
        errores.append(msg)
        return 0, 0
    lineas.append(_linea(lid, platform, mode, decision))
    return 1, 0


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
    lineas: list,
    errores: list,
    modo_global: str,
    modo_universo: dict,
) -> tuple[int, int]:
    """Una unidad de decidir sobre su Lectura del lote: maquina y persistencia.

    S.2: la fila guarda el modo EFECTIVO (`min` de global, universo y goal);
    con efectivo `off` no hay fila (se vuelve antes de cotizar). Sin canal
    conocido el universo no restringe (neutro `live`). Devuelve `(persistida,
    saltada)` (0/1). `psycopg.Error` sale (es infra); un `ValueError` del
    mercado (cotizacion guardada que no coincide, oferta ausente) sale
    `no_evaluado(escenario_incoherente)`; cualquier otra excepcion aisla la
    publicacion (B1).
    """
    if lectura.entrada is None:
        modo_fila = modo_efectivo(modo_global, "live", mode)
        if modo_fila == "off":
            return 0, 0
    else:
        clave = f"{platform}/{lectura.entrada.canal}"
        universo = modo_universo.get(clave, "off")
        modo_fila = modo_efectivo(modo_global, universo, mode)
        if modo_fila == "off":
            # A-W1: sin entrada el universo vale off (S.2, sin fila y sin
            # cotizar), pero si la entrada ausente es lo que apaga (global
            # y goal vivos) deja error visible en vez de 0,0 en silencio.
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
    except psycopg.Error:
        raise
    except Exception as exc:  # noqa: BLE001 - decidir aislado por publicacion (B1)
        msg = scrub(f"listing={lid} {platform} fase1: {exc.__class__.__name__}: {exc}")
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
    modo_global: str,
    modo_universo: dict,
) -> tuple[int, list[str], list[str], int]:
    """Una decision persistida por goal vigente sin decision hoy (J).

    S.2: cada fila guarda su modo efectivo (los frenos, sin canal
    conocido, usan `min` de global y goal). Frenos por unidad (antes de
    pedir insumos), UNA llamada `mercado.insumos` por lote para las no
    frenadas y, por unidad, la maquina de cotizaciones con su
    `persistir_decision`. Devuelve `(decisiones, lineas, errores,
    saltadas)`: `saltadas` cuenta las publicaciones sin fila por reedicion
    del goal a media corrida. Lo que este paso deja, `_aplicar_pendientes`
    lo lee de la base.
    """
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
    decisiones = 0
    saltadas = 0
    lineas: list[str] = []
    errores: list[str] = []
    goal_de: dict[int, int] = {}
    lote: list = []
    for gid, lid, plat, mode, goal_pct, vigente_desde, prod_id, ext, sku in goals:
        if lid in decididas:
            continue
        goal_de[lid] = gid
        _latir_claim(conn, platform, owner)
        modo_freno = modo_efectivo(modo_global, "live", mode)
        if modo_freno == "off":
            continue
        if freno_no_confirmado(conn, lid, plat, desde=vigente_desde):
            persistida, saltada = _persistir_uno(
                conn,
                _frenado(
                    Decimal(goal_pct),
                    modo_freno,
                    "no_confirmado",
                    "ultimo cambio real sin confirmar desde el goal vigente",
                ),
                lid=lid,
                mode=modo_freno,
                prod_id=prod_id,
                canal=None,
                meta=None,
                platform=platform,
                goal_id=gid,
                cfg_id=cfg_id,
                lineas=lineas,
                errores=errores,
            )
            decisiones += persistida
            saltadas += saltada
            continue
        if freno_por_error(conn, lid, plat, hoy, dias=freno_dias, desde=vigente_desde):
            persistida, saltada = _persistir_uno(
                conn,
                _frenado(
                    Decimal(goal_pct),
                    modo_freno,
                    "api_error",
                    f"error en los {freno_dias} dias previos",
                ),
                lid=lid,
                mode=modo_freno,
                prod_id=prod_id,
                canal=None,
                meta=None,
                platform=platform,
                goal_id=gid,
                cfg_id=cfg_id,
                lineas=lineas,
                errores=errores,
            )
            decisiones += persistida
            saltadas += saltada
            continue
        lote.append(
            (
                UnidadPrecio(
                    listing_id=lid,
                    plataforma=platform,
                    externo=ext,
                    canal=None,
                    producto_ancla=prod_id,
                    miembros=(Miembro(prod_id, sku, None),),
                ),
                mode,
                goal_pct,
                vigente_desde,
                prod_id,
            )
        )

    if lote:
        metas = {
            unidad.listing_id: _MetaGoal(
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
                lineas=lineas,
                errores=errores,
                modo_global=modo_global,
                modo_universo=modo_universo,
            )
            decisiones += persistida
            saltadas += saltada
    return decisiones, lineas, errores, saltadas


# La medicion de la compuerta, UNA por corrida y universo (S.3): fusible
# por `platform/canal`, insumo sistemico desde lo persistido, corte por
# errores al aplicar. Corre entre decidir y aplicar, sobre filas guardadas:
# punto de sabotaje de las pruebas (matarla aqui deja lo decidido sin
# aplicar).
_SQL_MEDIR_HOY = (
    "SELECT d.id, d.listing_id, d.canal::text, d.resultado, d.motivo, d.goal_id,"
    " d.c_valor, d.c_currency::text, d.l_valor, d.l_currency::text,"
    " d.r_valor, d.r_currency::text, d.escenario_id"
    " FROM precio_decision d"
    " WHERE d.platform = %s AND d.decision_date = %s AND d.m_actual IS NOT NULL"
    " AND d.canal IS NOT NULL"
    " ORDER BY d.listing_id"
)

_SQL_MEDIR_PREVIA = (
    "SELECT DISTINCT ON (d.listing_id) NULL, d.listing_id, NULL, d.resultado, d.motivo,"
    " d.goal_id, d.c_valor, d.c_currency::text, d.l_valor, d.l_currency::text,"
    " d.r_valor, d.r_currency::text, d.escenario_id"
    " FROM precio_decision d"
    " WHERE d.platform = %s AND d.decision_date < %s AND d.listing_id = ANY(%s)"
    " ORDER BY d.listing_id, d.decision_date DESC, d.id DESC"
)

_SQL_MEDIR_ESCENARIOS = (
    "SELECT e.id, e.fx_rate, e.fx_base::text, e.fx_quote::text,"
    " e.fee_variable, e.fee_fijo, e.moneda::text"
    " FROM estimacion_escenario e WHERE e.id = ANY(%s)"
)

_SQL_MEDIR_LIBERADAS = (
    "SELECT decision_id FROM v_precio_decision_liberada WHERE decision_id = ANY(%s)"
)

_SQL_MEDIR_INSERT = (
    "INSERT INTO precio_compuerta_medicion (corrida_id, canal, medidas, nuevas, causa,"
    " insumo, detalle) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

# Insumo vigilado -> posiciones de columna (OJO 2, S.3): `_SQL_MEDIR_HOY`
# para la decision, `_SQL_MEDIR_ESCENARIOS` para fx y fees.
# costo/envio/retencion son columnas de la decision (`c_valor`, `l_valor`,
# `r_valor`); fx y el partido de fees viven en el escenario (`fx_rate`,
# `fee_variable`, `fee_fijo`) y se alcanzan por `escenario_id`, sin releer
# fuentes. Cada entrada: `(valor, moneda)` o `(valor, par)`, donde `par`
# son las posiciones que deben coincidir (moneda, o base/quote de fx) para
# comparar. Sin columna propia en la decision no se inventa: sin dato no
# compara.
_INSUMO_DECISION = {"costo": (6, 7), "envio": (8, 9), "retencion": (10, 11)}
_INSUMO_ESCENARIO = {"fx": (1, (2, 3)), "fee_fijo": (5, (6,)), "fee_variable": (4, (6,))}
_INSUMOS_MEDIR = ("costo", "envio", "retencion", "fx", "fee_fijo", "fee_variable")


def _par_decision(fila_hoy, fila_previa, valor: int, moneda: int):
    """Par `(antes, ahora)` de un insumo de la decision, o `(None, None)`
    si no compara (ausente o distinta moneda)."""
    ahora, ahora_mon = fila_hoy[valor], fila_hoy[moneda]
    if fila_previa is None:
        return (None, ahora)
    antes, antes_mon = fila_previa[valor], fila_previa[moneda]
    if antes is None or ahora is None or antes_mon != ahora_mon:
        return (None, None)
    return (antes, ahora)


def _par_escenario(escenarios, id_hoy, id_previo, valor: int, par: tuple):
    """Par `(antes, ahora)` de un insumo del escenario, o `(None, None)`
    si no compara (sin escenario, ausente o distinto par moneda)."""
    esc_hoy = escenarios.get(id_hoy) if id_hoy is not None else None
    esc_previa = escenarios.get(id_previo) if id_previo is not None else None
    if esc_hoy is None or esc_previa is None:
        return (None, None)
    if any(esc_hoy[i] is None or esc_previa[i] is None for i in par):
        return (None, None)
    if any(esc_hoy[i] != esc_previa[i] for i in par):
        return (None, None)
    antes, ahora = esc_previa[valor], esc_hoy[valor]
    if antes is None or ahora is None:
        return (None, None)
    return (antes, ahora)


def _medir_compuerta(
    conn: psycopg.Connection, plataforma: str, hoy: date, motor, corrida_id: int
) -> dict:
    """S.3: arma `Intencion` y `Deriva` desde lo PERSISTIDO hoy, evalua UN
    universo por canal y escribe una fila de `precio_compuerta_medicion`
    por universo con medidas > 0. Devuelve `{canal: Veredicto}`.

    Mide el dia completo, no solo lo decidido en esta corrida: una segunda
    corrida con goals sembrados a media tarde ve el mismo numerador. Miden
    las decisiones de hoy CON cuenta (`m_actual`), shadow y live juntas,
    con canal conocido (sin canal no hay universo que medir).
    """
    filas = conn.execute(_SQL_MEDIR_HOY, (plataforma, hoy)).fetchall()
    if not filas:
        return {}
    por_listing = {fila[1]: fila for fila in filas}
    # La previa trae las columnas en las MISMAS posiciones que la de hoy
    # (los NULL alinean): `_par_decision` usa un solo par de indices.
    previas = {
        fila[1]: fila
        for fila in conn.execute(_SQL_MEDIR_PREVIA, (plataforma, hoy, list(por_listing))).fetchall()
    }
    ids_escenario = {fila[12] for fila in list(por_listing.values()) + list(previas.values())} - {
        None
    }
    escenarios = (
        {
            fila[0]: fila
            for fila in conn.execute(_SQL_MEDIR_ESCENARIOS, (list(ids_escenario),)).fetchall()
        }
        if ids_escenario
        else {}
    )
    liberadas = {
        fila[0]
        for fila in conn.execute(_SQL_MEDIR_LIBERADAS, ([fila[0] for fila in filas],)).fetchall()
    }
    por_canal: dict[str, list] = {}
    for fila in filas:
        por_canal.setdefault(fila[2], []).append(fila)
    veredictos = {}
    for canal, medidas_filas in por_canal.items():
        intenciones = []
        pares: dict[str, list] = {insumo: [] for insumo in _INSUMOS_MEDIR}
        for fila in medidas_filas:
            previa = previas.get(fila[1])
            liberada = fila[0] in liberadas
            if fila[3] in ("subir", "bajar"):
                anterior = (
                    Anterior(resultado=previa[3], motivo=previa[4], goal_id=previa[5])
                    if previa is not None
                    else None
                )
                intenciones.append(
                    Intencion(
                        decision_id=fila[0],
                        listing_id=fila[1],
                        canal=canal,
                        nueva=es_nueva(anterior, goal_id=fila[5]),
                        liberada=liberada,
                    )
                )
            for insumo, (valor, moneda) in _INSUMO_DECISION.items():
                antes, ahora = _par_decision(fila, previa, valor, moneda)
                pares[insumo].append((antes, ahora, liberada))
            for insumo, (valor, par) in _INSUMO_ESCENARIO.items():
                antes, ahora = _par_escenario(
                    escenarios,
                    fila[12],
                    previa[12] if previa is not None else None,
                    valor,
                    par,
                )
                pares[insumo].append((antes, ahora, liberada))
        derivas = [
            armar_deriva(canal, insumo, pares[insumo], pct=motor.insumo_salto_pct)
            for insumo in _INSUMOS_MEDIR
        ]
        veredicto = evaluar(intenciones, medidas=len(medidas_filas), derivas=derivas, config=motor)
        veredictos[canal] = veredicto
        if isinstance(veredicto, Retenida):
            causa, insumo, detalle = veredicto.causa, veredicto.insumo, veredicto.detalle
        else:
            causa, insumo, detalle = None, None, None
        conn.execute(
            _SQL_MEDIR_INSERT,
            (corrida_id, canal, veredicto.medidas, veredicto.nuevas, causa, insumo, detalle),
        )
    return veredictos


_SQL_SETTINGS_VIGENTE = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_GOAL_POR_ID = "SELECT mode::text, valid_from, valid_to FROM precio_goal WHERE id = %s"

_SQL_RETENER = "INSERT INTO precio_retencion (decision_id, corrida_id, causa) VALUES (%s, %s, %s)"

_SQL_PENDIENTE_LIBERADA = (
    "SELECT decision_id FROM v_precio_decision_liberada WHERE decision_id = ANY(%s)"
)


def _retener_por_compuerta(conn, corrida_id, veredictos, liberadas, canal, did) -> bool:
    """Retencion del universo `Retenida` no liberado, con su causa (S.3).

    Devuelve si retuvo (el llamador cuenta y sigue). Sin veredicto para el
    canal (pendiente sin universo medido) deja pasar: el trigger exige la
    medicion para estas causas y no hay cual pedir.
    """
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

    Pendiente = decision de hoy `subir`/`bajar` sin cambio con efecto (ni
    real ni virtual). Nunca una decision de ayer: su escenario ya no vale.
    En orden de listing_id. Por decision: su universo `Retenida` y no
    liberada deja retencion con la causa del veredicto; la config Y el goal
    se releen antes de CADA escritura (S.2: `apagador`, config ilegible =
    fail closed; `goal_cambiado` si ya no vigente, ausente o ya no `live`
    para una decision `live`); con el corte abierto, retencion
    `corte_errores`; en `shadow`, virtual; en `live`, `cambios.aplicar` y
    el resultado alimenta el `Cortacircuito` (`limite` = `corte`). Devuelve
    `(escritas, virtuales, retenidas, errores)`: `escritas` cuenta cambios
    reales creados (= PATCH intentados); una publicacion no tumba a las
    demas (B.4); `EscrituraNoDisponible` deja de aplicar `live` el resto de
    la corrida (fallaria igual en cada una y lo pendiente se reintenta en
    la siguiente); un error de base aborta.
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
    liberadas = (
        {
            fila[0]
            for fila in conn.execute(
                _SQL_PENDIENTE_LIBERADA, ([fila[0] for fila in pendientes],)
            ).fetchall()
        }
        if pendientes
        else set()
    )
    cortacircuito = Cortacircuito(seguidos=0, limite=corte)
    escritas = 0
    virtuales = 0
    retenidas = 0
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
        if _retener_por_compuerta(conn, corrida_id, veredictos, liberadas, canal, did):
            retenidas += 1
            continue
        try:
            fila_cfg = conn.execute(_SQL_SETTINGS_VIGENTE).fetchone()
            settings = fila_cfg[0] if fila_cfg is not None else None
            if settings is None:
                raise ValueError("config sin precio_modo_global: sin config_version")
            efectivo = modo_efectivo(
                _modo_config(settings, "precio_modo_global"),
                _modo_universo_config(settings, "precio_modo_universo").get(
                    f"{platform}/{canal}", "off"
                ),
                mode,
            )
        except psycopg.Error:
            raise
        except Exception:  # noqa: BLE001 - config ilegible a media corrida: fail closed
            efectivo = "off"
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
                    SimpleNamespace(
                        p_actual=Importe(Decimal(p_actual), p_actual_mon),
                        p_aplicado=Importe(Decimal(p_aplicado), p_aplicado_mon),
                    ),
                    ahora,
                )
            except psycopg.Error:
                raise
            except Exception as exc:  # noqa: BLE001 - una publicacion no tumba a las demas
                msg = scrub(f"listing={lid} {platform} {exc.__class__.__name__}: {exc}")
                logger.error("corrida %s", msg)
                errores.append(msg)
                continue
            virtuales += 1
            continue
        if parche_sin_sellar:
            continue
        try:
            res = cambios_mod.aplicar(conn, mercado, did, ahora=ahora)
        except psycopg.Error:
            raise
        except EscrituraNoDisponible as exc:
            msg = scrub(f"listing={lid} {platform} parche_sin_sellar: {exc}")
            logger.error("corrida %s", msg)
            errores.append(msg)
            parche_sin_sellar = True
            continue
        except Exception as exc:  # noqa: BLE001 - una publicacion no tumba a las demas
            msg = scrub(f"listing={lid} {platform} {exc.__class__.__name__}: {exc}")
            logger.error("corrida %s", msg)
            errores.append(msg)
            # A-W2: la excepcion tambien alimenta el corte (igual que un
            # Acuse con error): cinco seguidas abren el cortacircuito.
            cortacircuito = cortacircuito.tras("error")
            continue
        cortacircuito = cortacircuito.tras(res.estado)
        if res.id_cambio is not None:
            escritas += 1
    return escritas, virtuales, retenidas, errores


def _abrir_corrida(
    conn: psycopg.Connection, platform: str, owner: str, cfg_id, modo_global: str, hoy: date
) -> int:
    """Fila `abierta` de esta ejecucion; las `abierta` viejas de la
    plataforma pasan a `abortada` (una corrida que muere queda `abierta`)."""
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
    """Sella la fila `cerrada` con sus conteos, en un solo UPDATE (el trigger
    solo deja sellar una vez)."""
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
    """La corrida de una plataforma (conn en autocommit, rol decides).

    `platform` sale de `mercado.clave`. Valida config (`leer_config` +
    `leer_config_motor` + modos del apagador + freno; el `ValueError` al
    arrancar sale `ConfigInvalida`) -> toma el lock -> abre su fila
    `precio_corrida` (las `abierta` viejas de la plataforma pasan a
    `abortada`) -> huerfanas -> cierre por observacion -> decidir, SALVO
    con el global en `off` (la limpieza corre siempre; decidir y sus filas,
    no) -> compuerta (fusible e insumo por universo, con medicion) ->
    aplicar (retiene lo retenido no liberado, relee config y goal por
    escritura, corte por errores; `live` con `cambios.aplicar`, `shadow`
    con virtual) ->
    cierra su fila con los conteos -> avisa -> suelta el lock. Con el claim
    perdido levanta `LockOcupado` sin decidir nada. `avisar` (gancho del
    carril B) se llama una vez al terminar con `(conn, platform, hoy,
    resumen)`; si levanta, se registra (log scrubbado + entrada en
    `errores`) y la corrida devuelve su resumen normal.
    """
    platform = mercado.clave
    # D3 (r3): como `aplicar`: sin autocommit no hay corrida (el INSERT
    # tiene que confirmar antes de la escritura).
    if not conn.autocommit:
        raise ValueError("correr exige conexión en autocommit: cada bloque confirma al salir (S5)")
    # D1 (r3): settings e `id` de la `config_version` vigente en UNA consulta.
    # K8 (r4): lo de config sale como `ConfigInvalida` (subclase de
    # `ValueError`: los tests que esperan `ValueError` siguen pasando).
    try:
        fila_cfg = conn.execute(
            "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if fila_cfg is None:
            raise ValueError("config sin precio_catalogo_max_dias_sin_reportar: sin config_version")
        cfg_id, settings = fila_cfg
        config = leer_config(settings)
        motor = leer_config_motor(settings)
        modo_global = _modo_config(settings, "precio_modo_global")
        modo_universo = _modo_universo_config(settings, "precio_modo_universo")
        freno_dias = validar_freno_dias_error(settings)
    except ValueError as exc:
        raise ConfigInvalida(str(exc)) from exc
    ahora_raw = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
    ahora = ahora_raw.replace(tzinfo=UTC)
    hoy = ahora.date()

    try:
        # D2 (r3): el lock se toma DENTRO del `try` (si el advisory falla,
        # el `finally` suelta: el claim no queda tomado una hora).
        _tomar_lock(conn, platform, owner)
        corrida_id = _abrir_corrida(conn, platform, owner, cfg_id, modo_global, hoy)
        huerfanas = cambios_mod.cerrar_huerfanas(conn, mercado)
        cerrados = cambios_mod.cerrar_por_observacion(conn, mercado, hoy)
        if modo_global == "off":
            decisiones, lineas, errores, saltadas = 0, [], [], 0
        else:
            decisiones, lineas, errores, saltadas = _decidir_pendientes(
                conn,
                mercado,
                config,
                hoy,
                ahora,
                owner=owner,
                freno_dias=freno_dias,
                cfg_id=cfg_id,
                modo_global=modo_global,
                modo_universo=modo_universo,
            )
        veredictos = _medir_compuerta(conn, platform, hoy, motor, corrida_id)
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
        )
        if avisar is not None:
            try:
                avisar(conn, platform, hoy, resumen)
            except Exception as exc:  # noqa: BLE001 - un fallo del sender no tumba `correr`
                msg = scrub(f"avisar: {exc.__class__.__name__}: {exc}")
                logger.error("corrida %s", msg)
                resumen = replace(resumen, errores=resumen.errores + (msg,))
        return resumen
    finally:
        try:
            _soltar_lock(conn, platform, owner)
        except Exception as exc:  # noqa: BLE001 - K4: registra, no enmascara la original
            logger.error(
                "corrida %s", scrub(f"soltar lock {platform}: {exc.__class__.__name__}: {exc}")
            )


class _MetaGoal:
    """La meta del dia que la corrida le pasa al mercado (duck-typed:
    `.goal`, `.mode`, `.goal_vigente_desde`)."""

    __slots__ = ("goal", "mode", "goal_vigente_desde")

    def __init__(self, *, goal: Decimal, mode: str, goal_vigente_desde: date) -> None:
        self.goal = goal
        self.mode = mode
        self.goal_vigente_desde = goal_vigente_desde


def reporte(
    conn: psycopg.Connection, *, desde: date, hasta: date, platform: str | None = None
) -> list[str]:
    """Resumen de solo lectura (SELECT): decisiones y cambios por dia."""
    extra = "AND platform = %s" if platform else ""
    params_dec = [desde, hasta] + ([platform] if platform else [])
    decisiones = conn.execute(
        "SELECT decision_date, platform, resultado, count(*)"
        " FROM precio_decision WHERE decision_date BETWEEN %s AND %s "
        + extra
        + " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        params_dec,
    ).fetchall()
    cambios = conn.execute(
        "SELECT (enviado_at AT TIME ZONE 'UTC')::date, platform, estado, count(*)"
        " FROM precio_cambio WHERE (enviado_at AT TIME ZONE 'UTC')::date"
        " BETWEEN %s AND %s " + extra + " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        params_dec,
    ).fetchall()
    por_dia: dict = {}
    for fecha, plat, resultado, cuenta in decisiones:
        dia = por_dia.setdefault((str(fecha), plat), {"decisiones": 0, "cambios": {}})
        dia["decisiones"] += cuenta
        dia[resultado] = dia.get(resultado, 0) + cuenta
    for fecha, plat, estado, cuenta in cambios:
        if fecha is None:
            continue
        dia = por_dia.setdefault((str(fecha), plat), {"decisiones": 0, "cambios": {}})
        dia["cambios"][estado] = dia["cambios"].get(estado, 0) + cuenta
    lineas = []
    for fecha, plat in sorted(por_dia):
        dia = por_dia[(fecha, plat)]
        partes = [f"{fecha} {plat} decisiones={dia['decisiones']}"]
        for resultado in (
            "subir",
            "bajar",
            "mantener",
            "no_evaluado",
            "goal_inalcanzable",
            "frenado",
        ):
            if resultado in dia:
                partes.append(f"{resultado}={dia[resultado]}")
        if dia["cambios"]:
            partes.append(
                "cambios:" + ",".join(f"{e}={c}" for e, c in sorted(dia["cambios"].items()))
            )
        lineas.append(" ".join(partes))
    return lineas
