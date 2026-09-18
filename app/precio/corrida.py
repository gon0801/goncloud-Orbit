"""Corrida diaria del motor de precios (REPRICING 01 A.5, fila A.5 del plan).

Camino por plataforma: claim en `ads_optimizer_lock` (`precio:<platform>`,
patron copiado de `app/cycle.py`, NO importado: `cycle` trae `app.apply`)
+ advisory lock de sesion; huerfanas a `error`; cierre por observacion;
decide todos los goals vigentes (uno por listing: la segunda corrida del
dia salta los ya decididos por el UNIQUE); ordena por prioridad; reparte
el cupo (`repartir_cupo`, una llamada por plataforma); aplica `live` con
`cambiar_precio` (orden INSERT+COMMIT -> PATCH -> sello) bajo
`app/precio/cuota.py`; en `shadow` inserta el cambio virtual
(`confirmado`/`virtual`, sin PATCH); libera el claim al salir.

El reloj es el de la base (`SELECT now()` una vez): `hoy` y `as_of`
salen de ahi, nunca del reloj local (este modulo no llama `datetime.now`
ni importa `time`: el cubo y sus relojes los inyecta quien llama).
`L` es el importe del escenario (el canal es de E.3/E.4: no se toca).
Sin escenario no hay decision: `no_evaluado(precio_ausente)` es una fila,
nunca un objeto inventado.

Los frenos de S6 salen `frenado(api_error)` (dias de `error` segun
config), `frenado(no_confirmado)` (el ultimo cambio real desde el goal
vigente no lo confirmo la observacion; se reanuda con un goal nuevo) y
el detalle va al log: `MOTIVOS_FRENADO` vive en `app/precio/tipos.py`.
`buy_box_is_own` sale de la `spapi_price_observation` del dia
(`metric_date = hoy`; NULL = no observable, se declara). `ingreso_60d` es la suma de `sale`
del producto en [hoy-75, hoy-16] en la moneda del escenario (NULL si no
hay). `escritas` cuenta cambios reales creados (= PATCH intentados).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

import psycopg
import psycopg.errors
from psycopg.types.json import Json

import app.precio.cuota as cuota_mod
from app.estimacion_fees import ProductFeesClient, cotizar_a_precio
from app.estimacion_insumos import OfertaResuelta
from app.estimacion_reader import leer_escenarios
from app.estimacion_venta import DetalleFee
from app.precio.config import ConfigPrecio, leer_config
from app.precio.fuentes import config_vigente_settings
from app.precio.reglas import decidir, repartir_cupo
from app.precio.tipos import (
    CambioPrevio,
    Componentes,
    CotizacionVerificada,
    Decision,
    EntradaDecision,
    Escenario,
    HistorialMargen,
    Importe,
    ObservacionPricing,
    PideCotizacion,
    VentasInsumos,
)
from app.precio.ventas import evaluar_senal
from app.spapi.precio_write import (
    FormaParcheSinSellar,
    cambiar_precio,
    cerrar_por_observacion,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LockOcupado",
    "Resumen",
    "motor_lock",
    "freno_por_error",
    "cambios_previos",
    "armar_entrada",
    "reporte",
    "correr",
]

_TTL_LOCK_SEGUNDOS = 3600


class LockOcupado(Exception):
    """El claim `precio:<platform>` lo tiene otro dueno: no se decide."""


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
_SQL_ADVISORY_TOMAR = "SELECT pg_advisory_lock(hashtext('precio:' || %s))"
_SQL_ADVISORY_SOLTAR = "SELECT pg_advisory_unlock(hashtext('precio:' || %s))"


def _tomar_lock(conn: psycopg.Connection, platform: str, owner: str) -> None:
    fila = conn.execute(_SQL_CLAIM, (motor_lock(platform), owner, _TTL_LOCK_SEGUNDOS)).fetchone()
    if fila is None:
        raise LockOcupado(f"{motor_lock(platform)} en curso (owner distinto de {owner})")
    conn.execute(_SQL_ADVISORY_TOMAR, (platform,)).fetchone()


def _soltar_lock(conn: psycopg.Connection, platform: str, owner: str) -> None:
    try:
        conn.execute(_SQL_ADVISORY_SOLTAR, (platform,)).fetchone()
    finally:
        conn.execute(_SQL_LIBERAR, (motor_lock(platform), owner))


# La huerfana (d): COMMIT sin PATCH detectada al arrancar, con el lock ya
# tomado. Se cierra a `error` con `error_code = 'huerfana_sin_patch'`
# (la 0039 exige codigo en `error`; no hay columna «motivo»): un `error`
# no afirma que el precio no se movio (`revertir` cubre ese caso). Solo
# no-reversas (la `pendiente` reversa es del dueno) y SIN GET previo.
_SQL_HUERFANAS = """
UPDATE precio_cambio SET estado = 'error', error_code = 'huerfana_sin_patch'
 WHERE platform = %s AND estado = 'pendiente' AND NOT es_reversa
RETURNING id
"""


def cerrar_huerfanas(conn: psycopg.Connection, platform: str) -> int:
    """Cierra huerfanas; devuelve cuantas (van al resumen y al log)."""
    filas = conn.execute(_SQL_HUERFANAS, (platform,)).fetchall()
    if filas:
        logger.info("corrida %s huerfanas=%s", platform, len(filas))
    return len(filas)


def freno_por_error(
    conn: psycopg.Connection, listing_id: int, platform: str, hoy: date, *, dias: int
) -> bool:
    """S6: `error` en cada uno de los `dias` previos -> frenar hoy.

    Un reintento por dia: al tercer dia de `error` (clave
    `precio_freno_dias_error`, cota 1-14) la decision sale `frenado`.
    Solo cambios reales (la reversa es del dueno, el virtual no falla).
    """
    filas = conn.execute(
        "SELECT DISTINCT (enviado_at AT TIME ZONE 'UTC')::date"
        " FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s AND NOT es_reversa"
        " AND estado = 'error'"
        " AND (enviado_at AT TIME ZONE 'UTC')::date BETWEEN %s - %s AND %s - 1",
        (listing_id, platform, hoy, dias, hoy),
    ).fetchall()
    cubiertos = {f[0] for f in filas}
    return all((hoy - timedelta(days=n)) in cubiertos for n in range(1, dias + 1))


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


def _numero_entero(settings: Mapping, clave: str, *, minimo: int, maximo: int) -> int:
    """Replica de `app/precio/config.py::_entero` (punto (f): `config.py`
    no se toca y `_entero` es privado; `ValueError` nombra la clave)."""
    if clave not in settings or settings[clave] is None:
        raise ValueError(f"config sin {clave}")
    valor = settings[clave]
    if isinstance(valor, bool):
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}")
    try:
        numero = Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}") from exc
    if not numero.is_finite() or numero != numero.to_integral_value():
        raise ValueError(f"setting {clave}: debe ser entero: {valor!r}")
    entero = int(numero)
    if not minimo <= entero <= maximo:
        raise ValueError(f"setting {clave}: fuera de cota [{minimo}, {maximo}]: {entero}")
    return entero


def validar_freno_dias_error(settings: Mapping) -> int:
    """`precio_freno_dias_error` entero 1-14 (punto (f): vive en `corrida.py`)."""
    return _numero_entero(settings, "precio_freno_dias_error", minimo=1, maximo=14)


def _direccion(antes: Decimal, despues: Decimal) -> str | None:
    if despues > antes:
        return "subir"
    if despues < antes:
        return "bajar"
    return None


def cambios_previos(
    conn: psycopg.Connection, listing_id: int, platform: str
) -> tuple[CambioPrevio, ...]:
    """Cambios no-reversa del par para #6, #9 y #10 (reales y virtuales).

    La direccion sale de comparar importes (la fila no la guarda). Un
    cambio con `antes == despues` no tiene direccion y se salta (no deberia
    existir: el motor solo mueve precio).
    """
    filas = conn.execute(
        "SELECT (enviado_at AT TIME ZONE 'UTC')::date, precio_antes, precio_despues,"
        " estado, es_reversa, aplicado"
        " FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s AND NOT es_reversa"
        " ORDER BY id",
        (listing_id, platform),
    ).fetchall()
    cambios: list[CambioPrevio] = []
    for enviado_en, antes, despues, estado, _reversa, aplicado in filas:
        direccion = _direccion(antes, despues)
        if direccion is None or enviado_en is None:
            continue
        cambios.append(
            CambioPrevio(
                enviado_en=enviado_en,
                direccion=direccion,
                estado=estado,
                es_reversa=False,
                aplicado=bool(aplicado),
            )
        )
    return tuple(cambios)


def historial_margenes(
    conn: psycopg.Connection, listing_id: int, platform: str
) -> tuple[HistorialMargen, ...]:
    """Distancias `|m - goal|` al decidir cada cambio (#10).

    Salen del `precio_decision` que autorizo cada cambio (su `m_actual` y
    su `goal` con su fecha): la fila es inmutable y el numero se reproduce
    sin releer insumos.
    """
    filas = conn.execute(
        "SELECT d.m_actual, d.goal, d.decision_date, c.precio_antes, c.precio_despues,"
        " c.aplicado"
        " FROM precio_cambio c JOIN precio_decision d ON d.id = c.decision_id"
        " WHERE c.listing_id = %s AND c.platform = %s AND NOT c.es_reversa"
        " AND d.m_actual IS NOT NULL AND d.goal IS NOT NULL"
        " ORDER BY c.id",
        (listing_id, platform),
    ).fetchall()
    puntos: list[HistorialMargen] = []
    for m_actual, goal, fecha, antes, despues, aplicado in filas:
        direccion = _direccion(antes, despues)
        if direccion is None or fecha is None:
            continue
        puntos.append(
            HistorialMargen(
                direccion=direccion,
                distancia=abs(Decimal(m_actual) - Decimal(goal)),
                fecha=fecha,
                aplicado=bool(aplicado),
            )
        )
    return tuple(puntos)


def _detalle_fee(nodo: dict) -> DetalleFee:
    """`fee_details` normalizado (forma de `parsear_respuesta_fees`) a arbol."""
    if not isinstance(nodo, dict):
        raise ValueError(f"detalle de fee no es objeto: {nodo!r}")
    try:
        tasa_raw = nodo.get("tax_amount", nodo.get("TaxAmount"))
        hijos_raw = nodo.get("included_fee_details", nodo.get("IncludedFeeDetailList")) or ()
        return DetalleFee(
            fee_type=nodo["fee_type"],
            final_fee=Decimal(str(nodo["final_fee"])),
            tax_amount=None if tasa_raw is None else Decimal(str(tasa_raw)),
            included_fee_details=tuple(_detalle_fee(hijo) for hijo in hijos_raw),
        )
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"detalle de fee invalido: {nodo!r}") from exc


class InsumoIncoherente(Exception):
    """El escenario existe pero no arma `EntradaDecision` (falta una pieza
    o no cuadra): sale `no_evaluado(escenario_incoherente)`, nunca invento."""


def _importe(valor, moneda: str, *, campo: str) -> Importe:
    if valor is None or moneda is None:
        raise InsumoIncoherente(f"{campo} sin valor o sin moneda")
    return Importe(Decimal(valor), moneda)


def _componentes_por_nombre(componentes: list) -> dict:
    try:
        return {c["nombre"]: c for c in componentes}
    except (KeyError, TypeError) as exc:
        raise InsumoIncoherente(f"componentes no son lista de dicts: {componentes!r}") from exc


def armar_entrada(
    conn: psycopg.Connection,
    listing_id: int,
    platform: str,
    *,
    hoy: date,
    ahora: datetime,
    config: ConfigPrecio,
    mode: str,
    goal: Decimal,
    goal_vigente_desde: date,
    product_id: int,
) -> tuple[EntradaDecision | None, dict | None]:
    """`(entrada, ids)` desde las tablas; `(None, None)` = sin escenario.

    `ids` trae `escenario_id`, `fee_observation_id` y `oferta_observation_id`
    para persistir decision y cotizaciones. `motivo_estimacion` pasa tal
    cual cuando el escenario no esta `disponible` (S2: `decidir` lo vuelve
    `no_evaluado` sin gastar cotizacion). Cualquier pieza faltante o que no
    cuadre levanta `InsumoIncoherente` (el llamador lo vuelve
    `escenario_incoherente`).
    """
    escenarios = leer_escenarios(conn, [listing_id], as_of=ahora)
    if not escenarios:
        return None, None
    esc = escenarios[0]
    motivo_estimacion = esc.motivos[0] if esc.estado != "disponible" and esc.motivos else None

    fila_ids = conn.execute(
        "SELECT oferta_observation_id, fee_observation_id FROM estimacion_escenario WHERE id = %s",
        (esc.id,),
    ).fetchone()
    if fila_ids is None:
        raise InsumoIncoherente(f"escenario {esc.id} desaparecio")
    oferta_id, fee_id = fila_ids
    oferta = conn.execute(
        "SELECT seller_sku, asin, price_amount, price_currency, observed_at"
        " FROM estimacion_oferta_observation WHERE id = %s",
        (oferta_id,),
    ).fetchone()
    if oferta is None:
        raise InsumoIncoherente(f"oferta {oferta_id} del escenario {esc.id} ausente")
    (sku, asin, p_valor, p_moneda, oferta_obs_en) = oferta
    fee = conn.execute(
        "SELECT total_fees, quoted_price_currency, fee_details FROM estimacion_fee_observation"
        " WHERE id = %s",
        (fee_id,),
    ).fetchone()
    if fee is None:
        raise InsumoIncoherente(f"fee {fee_id} del escenario {esc.id} ausente")
    fee_total, fee_moneda, fee_detalles_raw = fee

    partes = _componentes_por_nombre(esc.componentes)
    # B.4 (K3): el JSONB de `componentes` puede traer basura (importes no
    # numericos, nodos que no son dicts). `Decimal(...)` levanta
    # `InvalidOperation`/`TypeError` y `.get` sobre un no-dict,
    # `AttributeError`: nada de eso aborta la corrida; la publicacion sale
    # `no_evaluado(escenario_incoherente)` y el resto sigue.
    try:
        bruto = partes["precio_bruto"]
        ingreso_c = partes["ingreso_normalizado"]
        costo_c = partes["costo_normalizado"]
        envio_c = partes["logistica"]
        isr_c = partes["isr"]
        monedas = {
            p_moneda,
            ingreso_c.get("moneda_normalizada"),
            costo_c.get("moneda_normalizada"),
            fee_moneda,
            envio_c.get("moneda_normalizada"),
            isr_c.get("moneda_normalizada"),
        }
        if len(monedas) != 1:
            raise InsumoIncoherente(
                f"escenario {esc.id} con monedas divergentes: {sorted(monedas)}"
            )
        moneda = p_moneda
        comp = Componentes(
            p_actual=_importe(p_valor, moneda, campo="p_actual"),
            ingreso=_importe(ingreso_c.get("importe_normalizado"), moneda, campo="ingreso"),
            costo=_importe(costo_c.get("importe_normalizado"), moneda, campo="costo"),
            fees=_importe(fee_total, moneda, campo="fees"),
            envio=_importe(envio_c.get("importe_normalizado"), moneda, campo="envio"),
            isr=_importe(isr_c.get("importe_normalizado"), moneda, campo="isr"),
        )
        ingreso_valor = comp.ingreso.valor
        if ingreso_valor <= 0:
            raise InsumoIncoherente(f"escenario {esc.id} con ingreso no positivo")
        divisor_iva = Decimal(bruto.get("importe_normalizado")) / ingreso_valor
        isr_tasa = comp.isr.valor / ingreso_valor
        try:
            detalles = tuple(_detalle_fee(nodo) for nodo in (fee_detalles_raw or []))
        except ValueError as exc:
            raise InsumoIncoherente(f"fee_details del escenario {esc.id}: {exc}") from exc
        escenario = Escenario(
            componentes=comp,
            fee_detalles=detalles,
            precio_cotizado=_importe(p_valor, moneda, campo="precio_cotizado"),
            iva_divisor=divisor_iva,
            isr_tasa=isr_tasa,
            precio_incluye_iva=divisor_iva != 1,
            oferta_observada_en=oferta_obs_en,
        )
    except KeyError as exc:
        raise InsumoIncoherente(f"escenario {esc.id} sin componente {exc}") from exc
    except (InvalidOperation, TypeError, AttributeError) as exc:
        raise InsumoIncoherente(f"escenario {esc.id} con componentes podridos: {exc}") from exc

    # S2 «fila del dia» + S4 #1 «nunca el valor de ayer»: `metric_date`
    # es el dia UTC de captura (0032). Sin fila de hoy, `pricing = None` y
    # `decidir` sale `no_evaluado(precio_sin_observar)` sin gastar cotizacion.
    pricing = conn.execute(
        "SELECT own_listing_price, own_listing_currency, observed_at, buy_box_is_own"
        " FROM spapi_price_observation"
        " WHERE asin = %s AND platform = %s AND metric_date = %s"
        " AND own_listing_price IS NOT NULL"
        " ORDER BY observed_at DESC LIMIT 1",
        (asin, platform, hoy),
    ).fetchone()
    observacion = None
    buy_box = None
    if pricing is not None:
        observacion = ObservacionPricing(
            precio=_importe(pricing[0], pricing[1], campo="pricing"),
            observada_en=pricing[2],
        )
        buy_box = pricing[3]

    insumos = _insumos_ventas(conn, product_id=product_id, platform=platform, sku=sku)
    racha_previa = conn.execute(
        "SELECT racha_senal FROM precio_decision"
        " WHERE listing_id = %s AND platform = %s AND decision_date < %s"
        " ORDER BY decision_date DESC LIMIT 1",
        (listing_id, platform, hoy),
    ).fetchone()
    senal = evaluar_senal(
        insumos,
        hoy=hoy,
        config=config,
        racha_previa=int(racha_previa[0]) if racha_previa and racha_previa[0] is not None else 0,
    )
    ingreso_60d = _ingreso_60d(
        conn, product_id=product_id, platform=platform, hoy=hoy, moneda=moneda
    )
    ids = {
        "escenario_id": esc.id,
        "fee_observation_id": fee_id,
        "oferta_observation_id": oferta_id,
    }
    return (
        EntradaDecision(
            listing_id=listing_id,
            platform=platform,
            product_id=product_id,
            canal=esc.canal,
            mode=mode,
            goal=goal,
            escenario=escenario,
            pricing=observacion,
            senal=senal,
            ingreso_60d=ingreso_60d,
            cambios=cambios_previos(conn, listing_id, platform),
            historial=historial_margenes(conn, listing_id, platform),
            goal_vigente_desde=goal_vigente_desde,
            motivo_estimacion=motivo_estimacion,
            buy_box_is_own=buy_box,
        ),
        ids,
    )


def _insumos_ventas(
    conn: psycopg.Connection, *, product_id: int, platform: str, sku: str
) -> VentasInsumos:
    """Filas ya leidas para S4 #5 (ventas del ledger, inventario y estado).

    Dia cubierto (S2 «Unidades vendidas»): `event_date` maxima cargada por
    un `ingest_run` `ok` del ledger DE LA PLATAFORMA —nunca la ultima venta
    del producto (un producto quieto no significa ledger rezagado). El
    `source` es el literal de `app/ledger.py::SOURCE` (`app.ledger` no se
    importa: el candado lo veta y el literal queda declarado aqui).
    """
    ventas = [
        (dia, qty)
        for dia, qty in conn.execute(
            "SELECT event_date, quantity FROM ledger_event"
            " WHERE product_id = %s AND platform = %s AND kind = 'sale'"
            " AND quantity IS NOT NULL",
            (product_id, platform),
        ).fetchall()
    ]
    primera = conn.execute(
        "SELECT min(event_date) FROM ledger_event"
        " WHERE product_id = %s AND platform = %s AND kind = 'sale'",
        (product_id, platform),
    ).fetchone()[0]
    cubierto = conn.execute(
        "SELECT max(e.event_date) FROM ledger_event e"
        " JOIN ingest_run r ON r.id = e.ingest_run_id"
        " WHERE e.platform = %s AND e.kind = 'sale' AND r.ok"
        " AND r.source = 'accounting_ledger_events'",
        (platform,),
    ).fetchone()[0]
    skus = [
        f[0]
        for f in conn.execute(
            "SELECT DISTINCT seller_sku FROM listing WHERE product_id = %s AND platform = %s",
            (product_id, platform),
        ).fetchall()
    ]
    inventario: list = []
    activos: list = []
    for codigo in skus or [sku]:
        inventario.extend(
            conn.execute(
                "SELECT metric_date, total_quantity FROM spapi_inventario_observation"
                " WHERE seller_sku = %s AND platform = %s",
                (codigo, platform),
            ).fetchall()
        )
        for estado, obs in conn.execute(
            "SELECT status, observed_at FROM spapi_listing_estado_observation"
            " WHERE seller_sku = %s AND platform = %s",
            (codigo, platform),
        ).fetchall():
            dia = obs.date() if isinstance(obs, datetime) else obs
            activos.append((dia, "BUYABLE" in (estado or "")))
    return VentasInsumos(
        ventas=tuple(ventas),
        inventario=tuple(inventario),
        listing_activo=tuple(activos),
        primera_venta=primera,
        dia_cubierto_hasta=cubierto,
    )


def _ingreso_60d(
    conn: psycopg.Connection, *, product_id: int, platform: str, hoy: date, moneda: str
) -> Importe | None:
    """Suma de `sale` en [hoy-75, hoy-16] en la moneda del escenario.

    La prioridad ordena por `|m - goal| * ingreso_60d`: sin ventas en la
    ventana no hay peso (regla 3: ausente no es cero, va al fondo).
    """
    total = conn.execute(
        "SELECT sum(amount) FROM ledger_event"
        " WHERE product_id = %s AND platform = %s AND kind = 'sale'"
        " AND amount_currency = %s AND event_date BETWEEN %s - 75 AND %s - 16",
        (product_id, platform, moneda, hoy, hoy),
    ).fetchone()[0]
    if total is None:
        return None
    return Importe(Decimal(total), moneda)


def _no_evaluado(goal: Decimal, mode: str, motivo: str, diagnostico: str = "") -> Decision:
    if diagnostico:
        logger.info("corrida no_evaluado %s: %s", motivo, diagnostico)
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
    logger.info("corrida frenado %s: %s", motivo, diagnostico)
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


def _oferta_para_cotizar(conn: psycopg.Connection, oferta_id: int):
    """Fila de oferta como `OfertaResuelta` (insumo de `cotizar_a_precio`)."""
    fila = conn.execute(
        "SELECT listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        " fetched_at, source_event_id, canonical_input, context_fingerprint"
        " FROM estimacion_oferta_observation WHERE id = %s",
        (oferta_id,),
    ).fetchone()
    if fila is None:
        raise InsumoIncoherente(f"oferta {oferta_id} ausente para cotizar")
    return OfertaResuelta(
        listing_id=fila[0],
        platform=fila[1],
        seller_sku=fila[2],
        asin=fila[3],
        canal=fila[4],
        price_amount=Decimal(fila[5]),
        price_currency=fila[6],
        fetched_at=fila[7],
        canonical_input=dict(fila[9] or {}),
        context_fingerprint=fila[10],
        source_event_id=fila[8],
    )


def _evento_cotizacion(listing_id: int, platform: str, hoy: date, intento: int) -> str:
    """Identidad de la cotizacion: par + dia + intento (UNIQUE en 0039)."""
    return f"precio-cotiz-{listing_id}-{platform}-{hoy.isoformat()}-{intento}"


def _cotizacion_guardada(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    platform: str,
    hoy: date,
    oferta_id: int,
    pedido: PideCotizacion,
):
    """La cotizacion de hoy para este intento, si el relanzamiento la encuentra.

    El relanzamiento del mismo dia (B.3: el proceso murio entre cotizar y
    persistir la decision) REUSA la fila en vez de re-cotizar: la tabla es
    append-only y el `source_event_id` es UNIQUE. Solo reusa si es la misma
    oferta al mismo precio (si difiere, revienta visible: no se mezcla).
    """
    fila = conn.execute(
        "SELECT id, quoted_price, quoted_price_currency, total_fees, fee_details,"
        " estado, error_code, oferta_observation_id FROM precio_cotizacion"
        " WHERE source_event_id = %s",
        (_evento_cotizacion(listing_id, platform, hoy, pedido.intento),),
    ).fetchone()
    if fila is None:
        return None
    (cid, precio, moneda, total, detalles, estado, codigo, oferta_guardada) = fila
    if (
        oferta_guardada != oferta_id
        or precio != pedido.precio.valor
        or moneda != pedido.precio.moneda
    ):
        raise InsumoIncoherente(
            f"cotizacion {cid} de hoy no coincide con el pedido"
            f" (oferta {oferta_guardada}/{oferta_id}, {precio} {moneda}"
            f" vs {pedido.precio.valor} {pedido.precio.moneda})"
        )
    return (
        cid,
        SimpleNamespace(
            estado=estado,
            fee_details=list(detalles or []),
            total_fees=total,
            error_code=codigo,
        ),
    )


def _persistir_cotizacion(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    platform: str,
    hoy: date,
    oferta_id: int,
    pedido: PideCotizacion,
    resultado,
) -> int:
    """La cotizacion nace ANTES que la decision (S5 r3-1): es su insumo."""
    evento = _evento_cotizacion(listing_id, platform, hoy, pedido.intento)
    if resultado.estado == "success":
        total, total_moneda = resultado.total_fees, pedido.precio.moneda
        detalles = resultado.fee_details
        estimada_en, codigo = resultado.fees_estimated_at, None
    else:
        total, total_moneda = None, None
        detalles = []
        estimada_en, codigo = None, resultado.error_code or "desconocido"
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
            resultado.estado,
            codigo,
            evento,
        ),
    ).fetchone()[0]


def _verificada(pedido: PideCotizacion, resultado) -> CotizacionVerificada:
    if resultado.estado == "success":
        detalles = tuple(_detalle_fee(nodo) for nodo in (resultado.fee_details or []))
        return CotizacionVerificada(
            precio=pedido.precio,
            detalles=detalles,
            fee_total=Decimal(resultado.total_fees),
            estado="success",
            error_code=None,
        )
    return CotizacionVerificada(
        precio=pedido.precio,
        detalles=(),
        fee_total=Decimal("0"),
        estado="error",
        error_code=resultado.error_code or "desconocido",
    )


def cotizar_y_decidir(
    conn: psycopg.Connection,
    entrada: EntradaDecision,
    *,
    fees: ProductFeesClient,
    oferta_id: int,
    hoy: date,
    config: ConfigPrecio,
) -> tuple[Decision, int | None]:
    """Maquina S4 #3: como maximo dos cotizaciones reales, persistidas.

    Devuelve `(decision_final, cotizacion_id_usada)`.

    K1: `observed_at` se captura DESPUES de la respuesta (el servidor
    estampa `TimeOfFeesEstimation` al recibir, despues de que la corrida
    arranco: con el `ahora` congelado toda cotizacion real moria con
    `fee_contrato_incompatible`). `now_utc` consulta la base en ese
    momento, nunca un valor congelado; la ventana `[fetched_at,
    observed_at]` sigue siendo la del contrato. Un pedido que no se puede
    cotizar (oferta ausente) es `fee_error` local, no excepcion.
    """
    oferta = _oferta_para_cotizar(conn, oferta_id)

    def _reloj_base():
        fila = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
        return fila.replace(tzinfo=UTC)

    hechas: list[CotizacionVerificada] = []
    ultimo_id: int | None = None
    for _ in range(3):
        salida = decidir(entrada, hoy=hoy, config=config, cotizaciones=tuple(hechas))
        if not isinstance(salida, PideCotizacion):
            return salida, ultimo_id
        reuso = _cotizacion_guardada(
            conn,
            listing_id=entrada.listing_id,
            platform=entrada.platform,
            hoy=hoy,
            oferta_id=oferta_id,
            pedido=salida,
        )
        if reuso is not None:
            ultimo_id, resultado = reuso
        else:
            resultado = cotizar_a_precio(
                fees, oferta, salida.precio.valor, observed_at=None, now_utc=_reloj_base
            )
            try:
                ultimo_id = _persistir_cotizacion(
                    conn,
                    listing_id=entrada.listing_id,
                    platform=entrada.platform,
                    hoy=hoy,
                    oferta_id=oferta_id,
                    pedido=salida,
                    resultado=resultado,
                )
            except psycopg.errors.UniqueViolation:
                # Carrera: otro proceso cito el mismo intento hoy; releer.
                reuso = _cotizacion_guardada(
                    conn,
                    listing_id=entrada.listing_id,
                    platform=entrada.platform,
                    hoy=hoy,
                    oferta_id=oferta_id,
                    pedido=salida,
                )
                if reuso is None:
                    raise
                ultimo_id, resultado = reuso
        try:
            hechas.append(_verificada(salida, resultado))
        except (ValueError, TypeError) as exc:
            raise InsumoIncoherente(f"cotizacion {ultimo_id} no arma verificada: {exc}") from exc
    raise AssertionError("la maquina pidio una tercera cotizacion (S4 #3: maximo dos)")


def moneda_contexto(conn: psycopg.Connection, listing_id: int, platform: str) -> str:
    """Moneda para `p_actual_currency` cuando la decision no trae P.

    La columna es NOT NULL: sin escenario no hay P pero la fila existe
    igual (S5). Cadena con dato real: ultima oferta del par y, si nunca
    hubo, ultima moneda observada en la plataforma (cualquier listing:
    la moneda es por plataforma, no por listing). Sin ninguna de las dos
    revienta visible (`InsumoIncoherente`): no se inventa, no hay mapa
    estatico en codigo (una fuente: las observaciones).
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
    raise InsumoIncoherente(f"sin moneda observable para {platform}: sin ofertas")


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
) -> int:
    """Una fila por (listing, platform, dia): la segunda del dia revienta
    por UNIQUE (la corrida salta esos goals antes). El trigger fija
    `decision_date` y `goal` desde la fila vigente del mismo mode."""
    comp = decision.componentes
    moneda = comp.p_actual.moneda if comp else moneda_contexto(conn, listing_id, platform)
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, motivo, product_id,"
        " canal, m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, escenario_id,"
        " fee_observation_id, cotizacion_id, u15, u60, n15, n60, racha_senal, perdiendo,"
        " buy_box_is_own, mode, prioridad, config_version_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
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
        ),
    ).fetchone()[0]


def _insertar_virtual(
    conn: psycopg.Connection,
    decision_id: int,
    listing_id: int,
    platform: str,
    decision: Decision,
    ahora: datetime,
) -> int:
    """Sombra fiel (S4 #13): el cambio virtual nace `confirmado`/`virtual`
    con `precio_antes = p_actual` (sin GET de Pricing en sombra), consume
    cooldown y freno sin ocupar el indice de abierto. Cero PATCH."""
    assert decision.p_actual is not None and decision.p_aplicado is not None
    return conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at, confirmado_por)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, false,"
        " 'confirmado', %s, 'virtual') RETURNING id",
        (
            decision_id,
            listing_id,
            platform,
            decision.p_actual.valor,
            decision.p_actual.moneda,
            decision.p_aplicado.valor,
            decision.p_aplicado.moneda,
            ahora,
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
    hoy: date,
    cap: int,
    agotado: bool,
    parche_sin_sellar: bool,
    lector,
    escritor,
    construir_cuerpo,
    limitador,
    cfg_id,
    ahora: datetime,
):
    """Un goal de fase 3: reserva, persiste y aplica.

    Devuelve `(decision_salida, escritas, agotado, parche_sin_sellar,
    error)`. B.4: una publicacion no tumba a las demas —`FormaParcheSinSellar`
    ademas deja de aplicar `live` el resto de la corrida (fallaria igual en
    cada una y quemaria cupo); un `InsumoIncoherente` en `persistir` (p. ej.
    `moneda_contexto` sin moneda observable, K8) no puede persistir; un error
    de base (`psycopg.Error`) aborta: no es una publicacion, es la infra.
    """
    compite = decision.resultado in ("subir", "bajar") and decision.aplicado
    if compite and parche_sin_sellar:
        return decision, 0, agotado, True, "parche_sin_sellar: sin PATCH el resto de la corrida"
    try:
        # K4: las reversas se cuentan al reservar, no solo al arrancar (el
        # dueno puede revertir a media corrida).
        reservado = (
            compite
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
            )
            res = cambiar_precio(
                conn,
                did,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=construir_cuerpo,
                limitador=limitador,
            )
            escritas_1 = 1 if res.id_cambio is not None else 0
            return decision, escritas_1, agotado, parche_sin_sellar, None
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
        )
        if decision.resultado in ("subir", "bajar"):
            _insertar_virtual(conn, did, lid, platform, decision, ahora)
        return decision, 0, agotado, parche_sin_sellar, None
    except psycopg.Error:
        raise
    except FormaParcheSinSellar as exc:
        return decision, 0, agotado, True, f"parche_sin_sellar: {exc}"
    except Exception as exc:  # noqa: BLE001 - una publicacion no tumba a las demas
        return decision, 0, agotado, parche_sin_sellar, f"{exc.__class__.__name__}: {exc}"


def correr(
    conn: psycopg.Connection,
    platform: str,
    *,
    lector,
    escritor,
    fees: ProductFeesClient,
    construir_cuerpo=None,
    limitador,
    owner: str,
) -> Resumen:
    """La corrida diaria de una plataforma (conn en autocommit, rol decide).

    Valida umbrales (ValueError al arrancar) -> toma el lock -> huerfanas
    -> cierre por observacion -> decide (freno de errores, escenario,
    maquina de cotizaciones) -> ordena por prioridad -> reparte cupo ->
    persiste y aplica (`live`) o virtualiza (`shadow`) -> suelta el lock.
    Con el claim perdido levanta `LockOcupado` sin decidir nada.
    """
    settings = config_vigente_settings(conn)
    config = leer_config(settings)
    cap = cuota_mod.validar_cap(settings, platform)
    freno_dias = validar_freno_dias_error(settings)
    cfg_id = conn.execute("SELECT id FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[0]
    ahora_raw = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
    ahora = ahora_raw.replace(tzinfo=UTC)
    hoy = ahora.date()

    _tomar_lock(conn, platform, owner)
    try:
        huerfanas = cerrar_huerfanas(conn, platform)
        cerrados = cerrar_por_observacion(conn, hoy)
        goals = conn.execute(
            "SELECT g.listing_id, g.platform, g.mode::text, g.margen_goal_pct,"
            " g.valid_from, l.product_id"
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

        # Fase 1: decidir todo (freno, escenario, maquina). Sin persistir.
        plan: list = []
        for lid, plat, mode, goal_pct, vigente_desde, prod_id in goals:
            if lid in decididas:
                continue
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
            if freno_por_error(conn, lid, plat, hoy, dias=freno_dias):
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
            try:
                entrada, ids = armar_entrada(
                    conn,
                    lid,
                    plat,
                    hoy=hoy,
                    ahora=ahora,
                    config=config,
                    mode=mode,
                    goal=Decimal(goal_pct),
                    goal_vigente_desde=vigente_desde,
                    product_id=prod_id,
                )
            except InsumoIncoherente as exc:
                plan.append(
                    (
                        lid,
                        _no_evaluado(Decimal(goal_pct), mode, "escenario_incoherente", str(exc)),
                        None,
                        prod_id,
                    )
                )
                continue
            if entrada is None:
                plan.append(
                    (
                        lid,
                        _no_evaluado(
                            Decimal(goal_pct),
                            mode,
                            "precio_ausente",
                            f"sin escenario disponible para listing {lid}",
                        ),
                        None,
                        prod_id,
                    )
                )
                continue
            try:
                decision, cotiz_id = cotizar_y_decidir(
                    conn,
                    entrada,
                    fees=fees,
                    oferta_id=ids["oferta_observation_id"],
                    hoy=hoy,
                    config=config,
                )
            except InsumoIncoherente as exc:
                plan.append(
                    (
                        lid,
                        _no_evaluado(Decimal(goal_pct), mode, "escenario_incoherente", str(exc)),
                        None,
                        prod_id,
                    )
                )
                continue
            plan.append(
                (lid, decision, {**ids, "cotizacion_id": cotiz_id, "canal": entrada.canal}, prod_id)
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
        errores: list[str] = []
        agotado = False
        parche_sin_sellar = False
        por_lid = {lid_p: (meta_p, prod_p) for (lid_p, _d, meta_p, prod_p) in plan}
        modos = {lid_g: mode_g for (lid_g, _p, mode_g, _g, _v, _r) in goals}
        for lid, decision in repartidos.items():
            meta, prod_id = por_lid[lid]
            (
                decision,
                escritas_1,
                agotado,
                parche_sin_sellar,
                error,
            ) = _fase3_uno(
                conn,
                lid=lid,
                decision=decision,
                meta=meta,
                prod_id=prod_id,
                canal=meta["canal"] if meta else None,
                platform=platform,
                hoy=hoy,
                cap=cap,
                agotado=agotado,
                parche_sin_sellar=parche_sin_sellar,
                lector=lector,
                escritor=escritor,
                construir_cuerpo=construir_cuerpo,
                limitador=limitador,
                cfg_id=cfg_id,
                ahora=ahora,
            )
            escritas += escritas_1
            if error is not None:
                msg = f"listing={lid} {platform} {error}"
                logger.error("corrida %s", msg)
                errores.append(msg)
            decisiones += 1
            lineas.append(_linea(lid, platform, modos[lid], decision))
        return Resumen(
            decisiones=decisiones,
            escritas=escritas,
            cerrados=cerrados,
            huerfanas=huerfanas,
            lineas=tuple(lineas),
            errores=tuple(errores),
        )
    finally:
        _soltar_lock(conn, platform, owner)


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
