"""Corrida diaria del motor de precios (REPRICING 01 A.5 + 02 0.b).

Camino por plataforma: claim en `ads_optimizer_lock` (`precio:<platform>`,
patron copiado de `app/cycle.py`, NO importado: `cycle` trae `app.apply`)
+ advisory lock de sesion; huerfanas a `error`; cierre por observacion;
decide todos los goals vigentes (uno por listing: la segunda corrida del
dia salta los ya decididos por el UNIQUE); ordena por prioridad; reparte
el cupo (`repartir_cupo`, una llamada por plataforma); aplica `live` con
`cambios.aplicar` (orden INSERT+COMMIT -> PATCH -> sello) bajo
`app/precio/cuota.py`; en `shadow` inserta el cambio virtual
(`confirmado`/`virtual`, sin PATCH); libera el claim al salir.

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
`ingreso_60d` es la suma de `sale` del producto en [hoy-75, hoy-16] en la
moneda del escenario (NULL si no hay). `decisiones` cuenta filas
`precio_decision` del dia persistidas (B.2: lo que no dejo fila no cuenta
ni sale en lineas); `escritas` cuenta cambios reales creados (= PATCH
intentados). Toda decision guarda `goal_id` y `l_origen='politica`;
`subir`/`bajar` guardan `verificacion='cotizada'` (la maquina siempre
cotiza en este paso).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import psycopg.errors
from psycopg.types.json import Json

import app.precio.cambios as cambios_mod
import app.precio.cuota as cuota_mod
from app.estimacion_venta import DetalleFee
from app.precio.config import ConfigPrecio, leer_config
from app.precio.config import _entero as _entero_config
from app.precio.puerto import EscrituraNoDisponible, Miembro, UnidadPrecio
from app.precio.reglas import decidir, repartir_cupo
from app.precio.tipos import (
    Decision,
    EntradaDecision,
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
    """La config no sirve para correr (cap fuera de cota, clave ausente):
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
        prioridad=None,
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
        prioridad=None,
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
        " buy_box_is_own, mode, prioridad, config_version_id, goal_id, l_origen, verificacion)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s)"
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
            decision.prioridad,
            config_version_id,
            goal_id,
            l_origen,
            verificacion,
        ),
    ).fetchone()[0]


def _linea(lid: int, platform: str, mode: str, decision: Decision) -> str:
    return f"listing={lid} {platform} {mode} {decision.resultado} {decision.motivo or '-'}"


def _fase3_uno(
    conn: psycopg.Connection,
    *,
    lid: int,
    decision: Decision,
    meta,
    prod_id: int,
    canal,
    platform: str,
    goal_id: int,
    hoy: date,
    cap: int,
    agotado: bool,
    parche_sin_sellar: bool,
    mercado,
    cfg_id,
    ahora: datetime,
):
    """Un goal de fase 3: reserva, persiste y aplica.

    Devuelve `(decision_salida, escritas, agotado, parche_sin_sellar, error,
    persistida)`. B.4: una publicacion no tumba a las demas
    —`EscrituraNoDisponible` ademas deja de aplicar `live` el resto de la
    corrida (fallaria igual en cada una y quemaria cupo); un `ValueError`
    en `persistir` (p. ej. `moneda_contexto` sin moneda observable, K8) no
    puede persistir; un error de base (`psycopg.Error`) aborta: no es una
    publicacion, es la infra. Rama de parche sin sellar (B.1): la decision
    SE persiste (`compite` trae `meta` con cotizacion) pero SIN reservar
    (no gasta cupo), sin escritura y sin virtual —un virtual afirmaria una
    aplicacion que no ocurrio; la pantalla lee "decidido sin cambio" mas el
    error del resumen. `persistida` dice si quedo la fila (B.2: el que
    llama solo cuenta y alinea lo persistido).
    """
    compite = decision.resultado in ("subir", "bajar") and decision.aplicado
    persistida = False
    try:
        if compite and parche_sin_sellar:
            persistir_decision(
                conn,
                decision,
                listing_id=lid,
                platform=platform,
                product_id=prod_id,
                canal=canal,
                escenario_id=meta["escenario_id"],
                fee_observation_id=meta["fee_observation_id"],
                cotizacion_id=meta["cotizacion_id"],
                config_version_id=cfg_id,
                goal_id=goal_id,
                l_origen="politica",
            )
            return (
                decision,
                0,
                agotado,
                True,
                "parche_sin_sellar: sin escritura el resto de la corrida",
                True,
            )
        # K4: las reversas se cuentan al reservar, no solo al arrancar (el
        # dueno puede revertir a media corrida). B7: defensa en profundidad
        # (el invariante lo sostienen `reglas` y el chequeo de
        # `cambios.aplicar`): la escritura solo en `live`. K1: la reserva
        # cobra aunque `aplicar` salte sin fila (no se devuelve: `used` es
        # monotono por la 0002).
        reservado = (
            compite
            and decision.mode == "live"
            and not agotado
            and cuota_mod.reservar(
                conn,
                platform=platform,
                cap=cap,
                extra=cuota_mod.reversas_hoy(conn, platform, hoy),
            )
        )
        if compite and not reservado:
            agotado = True
            decision = _cuota_reescrita(decision)
        if compite and reservado:
            did = persistir_decision(
                conn,
                decision,
                listing_id=lid,
                platform=platform,
                product_id=prod_id,
                canal=canal,
                escenario_id=meta["escenario_id"],
                fee_observation_id=meta["fee_observation_id"],
                cotizacion_id=meta["cotizacion_id"],
                config_version_id=cfg_id,
                goal_id=goal_id,
                l_origen="politica",
                verificacion="cotizada",
            )
            persistida = True
            res = cambios_mod.aplicar(conn, mercado, did, ahora=ahora)
            escritas_1 = 1 if res.id_cambio is not None else 0
            return decision, escritas_1, agotado, parche_sin_sellar, None, True
        verificacion = "cotizada" if decision.resultado in ("subir", "bajar") else None
        did = persistir_decision(
            conn,
            decision,
            listing_id=lid,
            platform=platform,
            product_id=prod_id,
            canal=canal,
            escenario_id=meta["escenario_id"] if meta else None,
            fee_observation_id=meta["fee_observation_id"] if meta else None,
            cotizacion_id=meta["cotizacion_id"] if meta else None,
            config_version_id=cfg_id,
            goal_id=goal_id,
            l_origen="politica",
            verificacion=verificacion,
        )
        persistida = True
        # B7: el virtual solo en `shadow` (un `live` sin aplicar no afirma
        # aplicacion; el trigger lo frenaria, pero la guarda va en codigo).
        if decision.resultado in ("subir", "bajar") and decision.mode == "shadow":
            cambios_mod.insertar_virtual(conn, did, lid, platform, decision, ahora)
        return decision, 0, agotado, parche_sin_sellar, None, True
    except psycopg.errors.CheckViolation as exc:
        # El goal se reedito a media corrida (`goal_reemplazado` de la
        # 0054): la publicacion no deja fila (sin motivo en vocabulario
        # para reedicion) y el error scrubbado va al resumen (B2).
        if "goal_reemplazado" not in str(exc):
            raise
        return decision, 0, agotado, parche_sin_sellar, f"goal_cambiado: {exc}", False
    except psycopg.Error:
        raise
    except EscrituraNoDisponible as exc:
        return decision, 0, agotado, True, f"parche_sin_sellar: {exc}", persistida
    except Exception as exc:  # noqa: BLE001 - una publicacion no tumba a las demas
        return (
            decision,
            0,
            agotado,
            parche_sin_sellar,
            f"{exc.__class__.__name__}: {exc}",
            persistida,
        )


def _aislar_falla_fase1(
    *,
    goal_pct,
    mode: str,
    lid: int,
    platform: str,
    prod_id: int,
    plan: list,
    errores: list,
    exc: Exception,
) -> None:
    """B1 (r3): fase 1 aislada como la fase 3.

    La publicacion sale `no_evaluado(escenario_incoherente)` y el error
    scrubbado va al log y a `errores` (B2: tambien el `diagnostico`, que
    `_no_evaluado` loguea en INFO).
    """
    msg = scrub(f"listing={lid} {platform} fase1: {exc.__class__.__name__}: {exc}")
    logger.error("corrida %s", msg)
    errores.append(msg)
    plan.append(
        (
            lid,
            _no_evaluado(Decimal(goal_pct), mode, "escenario_incoherente", msg),
            None,
            prod_id,
        )
    )


def _fase1_lectura(
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
    plan: list,
    errores: list,
) -> None:
    """Un goal de fase 1 sobre su Lectura del lote: maquina, sin persistir.

    Agrega `(lid, decision, meta, prod_id)` a `plan` (`meta` es None cuando
    no hay ids que persistir). `psycopg.Error` sale (es infra); un
    `ValueError` del mercado (cotizacion guardada que no coincide, oferta
    ausente) sale `no_evaluado(escenario_incoherente)`; cualquier otra
    excepcion aisla la publicacion (B1).
    """
    if lectura.entrada is None:
        motivo = lectura.motivo or "estimacion_motivo_desconocido"
        diagnostico = lectura.diagnostico or f"listing {lid} sin entrada"
        if motivo == "escenario_incoherente" and lectura.diagnostico:
            msg = scrub(f"listing={lid} {platform} fase1: {diagnostico}")
            logger.error("corrida %s", msg)
            errores.append(msg)
        if not motivo_permitido("no_evaluado", motivo):
            crudo = f"motivo_estimacion={motivo!r} {diagnostico}"
            plan.append(
                (
                    lid,
                    _no_evaluado(Decimal(goal_pct), mode, "estimacion_motivo_desconocido", crudo),
                    None,
                    prod_id,
                )
            )
            return
        plan.append(
            (lid, _no_evaluado(Decimal(goal_pct), mode, motivo, diagnostico), None, prod_id)
        )
        return
    entrada = lectura.entrada
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
        plan.append(
            (
                lid,
                _no_evaluado(Decimal(goal_pct), mode, "escenario_incoherente", str(exc)),
                None,
                prod_id,
            )
        )
        return
    except psycopg.Error:
        raise
    except Exception as exc:  # noqa: BLE001 - fase 1 aislada como la fase 3 (B1)
        _aislar_falla_fase1(
            goal_pct=goal_pct,
            mode=mode,
            lid=lid,
            platform=platform,
            prod_id=prod_id,
            plan=plan,
            errores=errores,
            exc=exc,
        )
        return
    plan.append(
        (
            lid,
            decision,
            {**lectura.ids, "cotizacion_id": cotiz_id, "canal": entrada.canal},
            prod_id,
        )
    )


def correr(
    conn: psycopg.Connection,
    mercado,
    *,
    owner: str,
    avisar=None,
) -> Resumen:
    """La corrida diaria de una plataforma (conn en autocommit, rol decides).

    `platform` sale de `mercado.clave`. Valida umbrales (ValueError al
    arrancar) -> toma el lock -> huerfanas -> cierre por observacion ->
    decide (frenos por unidad; UNA llamada `mercado.insumos` por lote para
    las no frenadas; por unidad: maquina de cotizaciones) -> ordena por
    prioridad -> reparte cupo -> persiste y aplica (`live`) o virtualiza
    (`shadow`) -> avisa -> suelta el lock. Con el claim perdido levanta
    `LockOcupado` sin decidir nada. `avisar` (gancho del carril B) se llama
    una vez al terminar la fase 3 con `(conn, platform, hoy, resumen)`; si
    levanta, se registra (log scrubbado + entrada en `errores`) y la corrida
    devuelve su resumen normal.
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
        cap = cuota_mod.validar_cap(settings, platform)
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
        huerfanas = cambios_mod.cerrar_huerfanas(conn, mercado)
        cerrados = cambios_mod.cerrar_por_observacion(conn, mercado, hoy)
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
        reversas = cuota_mod.reversas_hoy(conn, platform, hoy)
        cupo = max(0, cap - reversas)
        # `errores` nace antes de la fase 1 (B1: la fase 1 tambien registra).
        errores: list[str] = []

        # Fase 1a: frenos por unidad, ANTES de pedir insumos (igual que
        # hoy frenaba antes de armar la entrada). Los no frenados juntan
        # su unidad para el lote.
        plan: list = []
        goal_de: dict = {}
        lote: list = []
        for gid, lid, plat, mode, goal_pct, vigente_desde, prod_id, ext, sku in goals:
            if lid in decididas:
                continue
            goal_de[lid] = gid
            _latir_claim(conn, platform, owner)
            if freno_no_confirmado(conn, lid, plat, desde=vigente_desde):
                plan.append(
                    (
                        lid,
                        _frenado(
                            Decimal(goal_pct),
                            mode,
                            "no_confirmado",
                            "ultimo cambio real sin confirmar desde el goal vigente",
                        ),
                        None,
                        prod_id,
                    )
                )
                continue
            if freno_por_error(conn, lid, plat, hoy, dias=freno_dias, desde=vigente_desde):
                plan.append(
                    (
                        lid,
                        _frenado(
                            Decimal(goal_pct),
                            mode,
                            "api_error",
                            f"error en los {freno_dias} dias previos",
                        ),
                        None,
                        prod_id,
                    )
                )
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

        # Fase 1b: UNA llamada de insumos por lote y, por unidad, la
        # maquina de cotizaciones (sin persistir).
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
                _fase1_lectura(
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
                    plan=plan,
                    errores=errores,
                )

        # Fase 2: prioridad (ausente al fondo, desempate listing) y cupo.
        orden = sorted(
            range(len(plan)),
            key=lambda i: (
                plan[i][1].prioridad is None,
                -(plan[i][1].prioridad or Decimal(0)),
                plan[i][0],
            ),
        )
        candidatos = tuple((plan[i][0], plan[i][1]) for i in orden)
        repartidos = dict(repartir_cupo(candidatos, cupo=cupo))

        # Fase 3: persistir y aplicar por orden de prioridad (ver `_fase3_uno`).
        decisiones = 0
        escritas = 0
        lineas: list[str] = []
        agotado = False
        parche_sin_sellar = False
        por_lid = {lid_p: (meta_p, prod_p) for (lid_p, _d, meta_p, prod_p) in plan}
        modos = {lid_g: mode_g for (_gid, lid_g, _p, mode_g, *_r) in goals}
        for lid, decision in repartidos.items():
            _latir_claim(conn, platform, owner)
            meta, prod_id = por_lid[lid]
            (
                decision,
                escritas_1,
                agotado,
                parche_sin_sellar,
                error,
                persistida,
            ) = _fase3_uno(
                conn,
                lid=lid,
                decision=decision,
                meta=meta,
                prod_id=prod_id,
                canal=meta["canal"] if meta else None,
                platform=platform,
                goal_id=goal_de[lid],
                hoy=hoy,
                cap=cap,
                agotado=agotado,
                parche_sin_sellar=parche_sin_sellar,
                mercado=mercado,
                cfg_id=cfg_id,
                ahora=ahora,
            )
            escritas += escritas_1
            if error is not None:
                # B2 (r3): en `errores` va el texto scrubbado (el gancho es
                # de Telegram en el carril B).
                msg = scrub(f"listing={lid} {platform} {error}")
                logger.error("corrida %s", msg)
                errores.append(msg)
            if persistida:
                decisiones += 1
                lineas.append(_linea(lid, platform, modos[lid], decision))
        resumen = Resumen(
            decisiones=decisiones,
            escritas=escritas,
            cerrados=cerrados,
            huerfanas=huerfanas,
            lineas=tuple(lineas),
            errores=tuple(errores),
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


def _cuota_reescrita(decision: Decision) -> Decision:
    return replace(decision, resultado="mantener", motivo="cuota", aplicado=False, p_aplicado=None)


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
