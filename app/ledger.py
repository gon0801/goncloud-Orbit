"""Ingesta del ledger desde la SQLite de contabilidad (ORBIT 06 0.6).

Fuente: snapshot READ-ONLY de `ledger_events` (mismo runbook `.backup()`
que costos). Destino: `ledger_event` de Orbit (rol ORBIT_DSN_INGEST). Este
modulo NUNCA escribe en contabilidad y no toca decision / apply_queue /
goals.

Decisiones selladas (plans/orbit-06.md, "Decisiones de la 0.6"):

1. Alcance Amazon y MeLi (M.2 abrio `meli`; antes excluida contada). `amazon`→`amazon_mx`.
2. Kind via MAPA_KIND (tabla): sale_gross→sale; refund→refund;
   fee+tax/isr_withheld→withholding; resto fee→fee (ads conserva etiqueta).
   La devolucion de MeLi sin categoria lleva fee_type 'refund' (ARREGLOS 03
   D-H: MeLi no manda la categoria y sin etiqueta el margen salia vacio).
3. Acceso `--sqlite` mode=ro.
4. Signo TAL CUAL; filas que violan ledger_convencion_signos → NO escritas
   (jamas voltear un fee+).
5. product_id: ASIN→listing (Amazon), SKU→product.odoo_sku (MeLi);
   cogs_at_sale NO se ingiere.
6. source_event_id=dedupe_key; order_id ''→NULL; ISR sin orden ENTRA;
   ON CONFLICT DO NOTHING en los tres indices de dedupe.
7. event_date timestamp→DATE; no fusionar dedupe_keys distintos.
8. Desglose fiscal del raw_payload si CurrencyCode coincide; moneda = la
   del amount (amazon_us puede ser MXN). El desglose fiscal de US sigue
   vacio: sus bloques vienen en USD y esas columnas estan en la moneda del
   monto (guardar dolares ahi mezclaria monedas). ARREGLOS 03 D-I: el
   importe en dolares va aparte (amount_original, amount_original_currency,
   fx_fecha, ver `_original_de`); un cargo de US solo trae la moneda y el
   monto original queda NULL (no se inventa).

`python -m app.cli ingest ledger --sqlite RUTA`.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg

from app.db import connect
from app.ingest_run import abrir_run, formato_skip_reason, sellar_fallo_seguro, sellar_run
from app.ledger_atribucion import Atribuidor, AtribuidorAmazon, atribuidor_amazon
from app.meli.ledger import AtribuidorMeli, atribuidor_meli
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "accounting_ledger_events"

# (event_type, fee_category|None) → (kind, fee_type_policy).
# fee_type_policy None → fee_type NULL; "category" → fee_category (o NULL).
# sale/refund ignoran fee_category en el lookup (clave con None).
# fee+withholding se clavea por categoria exacta; el resto de fee cae en
# ("fee", None).
# Fuera de la tabla (ARREGLOS 03 D-H): mapear_destino completa fee_type
# 'refund' para una devolucion de MeLi sin categoria (MeLi no la manda).
MAPA_KIND: dict[tuple[str, str | None], tuple[str, str | None]] = {
    ("sale_gross", None): ("sale", None),
    ("refund", None): ("refund", "category"),
    ("fee", "tax_withheld"): ("withholding", "category"),
    ("fee", "isr_withheld"): ("withholding", "category"),
    ("fee", None): ("fee", "category"),
}

_PLATAFORMA_ORIGEN = {
    "amazon": "amazon_mx",
    "amazon_us": "amazon_us",
    "meli": "meli",
}

# REPRICING 02: despacho de la atribucion por plataforma de destino (F.1
# lleno el de Amazon, M.2 el de MeLi). La ingesta inserta lo que devuelvan
# y cuenta `venta_sin_atribuir` cuando no pueden.
_ATRIBUIDORES: dict[str, Atribuidor] = {
    "amazon_mx": atribuidor_amazon,
    "amazon_us": atribuidor_amazon,
    "meli": atribuidor_meli,
}

MONEDAS = ("MXN", "USD")
_MAX_DECIMALES = Decimal("0.0001")
_RUIDO_FLOAT = Decimal("0.00001")
_MAX_AMOUNT = Decimal(10) ** 10


class LedgerError(Exception):
    """Error de la ingesta del ledger (snapshot inexistente o ilegible)."""


@dataclass(frozen=True)
class FilaOrigenLedger:
    """Fila cruda de ledger_events tal cual viene de contabilidad."""

    platform: str
    order_id: str | None
    event_type: str
    fee_category: str | None
    sku: str | None
    quantity: int | None
    amount: float | None
    currency: str | None
    event_date: str | None
    cogs_at_sale: float | None
    raw_payload: str | None
    dedupe_key: str | None


@dataclass(frozen=True)
class EventoLedger:
    """Fila lista para ledger_event (sin ingest_run_id).

    `payload` no se persiste: viaja para el atribuidor (M.2: `item_id`
    de MeLi). Se llama asi y no `raw` porque ya viene parseado.

    `amount_original`, `amount_original_currency` y `fx_fecha` (ARREGLOS 03
    D-I): el importe antes de que contabilidad lo convirtiera, su moneda y el
    dia de la tasa (solo si fue la del dia). Ver `_original_de`.
    """

    platform: str
    kind: str
    event_date: date
    order_id: str | None
    product_id: int | None
    quantity: int | None
    amount: Decimal
    amount_currency: str
    item_price: Decimal | None
    item_tax: Decimal | None
    shipping_price: Decimal | None
    shipping_tax: Decimal | None
    fee_type: str | None
    source_event_id: str | None
    amount_original: Decimal | None
    amount_original_currency: str | None
    fx_fecha: date | None
    payload: dict | None = None


@dataclass(frozen=True)
class ResultadoSync:
    """Outcome contable de la corrida (espejo de la ingest_run sellada)."""

    run_id: int
    ok: bool
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    filas_origen: int
    eventos_finales: int
    por_kind: dict[str, int]
    ventas_sin_asin: int = 0
    sin_listing_para_asin: int = 0
    venta_con_asin_sin_cantidad: int = 0
    desglose_fiscal_negativo_descartado: int = 0
    ventas_meli_sin_sku: int = 0
    ventas_meli_sin_producto: int = 0
    ventas_meli_sin_cantidad: int = 0
    reatribuidas: int = 0
    rango_min: date | None = None
    rango_max: date | None = None


def leer_origen(ruta: Path | str) -> tuple[FilaOrigenLedger, ...]:
    """Lee ledger_events del snapshot. Abre con mode=ro."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise LedgerError(f"snapshot inexistente: {ruta}")
    con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    try:
        return tuple(
            FilaOrigenLedger(
                platform=fila[0] or "",
                order_id=fila[1],
                event_type=fila[2] or "",
                fee_category=fila[3],
                sku=fila[4],
                quantity=fila[5],
                amount=fila[6],
                currency=fila[7],
                event_date=fila[8],
                cogs_at_sale=fila[9],
                raw_payload=fila[10],
                dedupe_key=fila[11],
            )
            for fila in con.execute(
                "SELECT platform, order_id, event_type, fee_category, sku,"
                " quantity, amount, currency, event_date, cogs_at_sale,"
                " raw_payload, dedupe_key"
                " FROM ledger_events"
                " ORDER BY id"
            )
        )
    finally:
        con.close()


def _dia(texto: str | None) -> date | None:
    if not isinstance(texto, str) or not texto:
        return None
    try:
        return datetime.fromisoformat(texto).date()
    except ValueError:
        return None


def _lookup_kind(event_type: str, fee_category: str | None) -> tuple[str, str | None] | None:
    """Resuelve (kind, fee_type) via MAPA_KIND. None = event_type desconocido."""
    event_type = (event_type or "").strip()
    if event_type == "sale_gross":
        kind, policy = MAPA_KIND[("sale_gross", None)]
        return kind, None if policy is None else fee_category
    if event_type == "refund":
        kind, policy = MAPA_KIND[("refund", None)]
        fee_type = (fee_category or None) if policy == "category" else None
        return kind, fee_type
    if event_type == "fee":
        exact = MAPA_KIND.get(("fee", fee_category))
        if exact is not None:
            kind, policy = exact
        else:
            kind, policy = MAPA_KIND[("fee", None)]
        fee_type = (fee_category or None) if policy == "category" else None
        return kind, fee_type
    return None


def _parse_payload(texto: str | None) -> dict[str, Any]:
    if not texto:
        return {}
    try:
        data = json.loads(texto)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _money_from_payload(payload: dict[str, Any], clave: str, moneda_amount: str) -> Decimal | None:
    """ItemPrice/ItemTax/... → Decimal si CurrencyCode coincide; si no, NULL."""
    bloque = payload.get(clave)
    if not isinstance(bloque, dict):
        return None
    code = (bloque.get("CurrencyCode") or "").strip().upper()
    if code != moneda_amount:
        return None
    raw = bloque.get("Amount")
    if raw is None:
        return None
    try:
        valor = Decimal(str(raw))
    except Exception:
        return None
    if not valor.is_finite():
        return None
    if abs(valor) >= _MAX_AMOUNT:
        return None
    try:
        cuantizado = valor.quantize(_MAX_DECIMALES)
    except InvalidOperation:
        return None
    if abs(valor - cuantizado) >= _RUIDO_FLOAT:
        return None
    if cuantizado < 0:
        return None
    return cuantizado


def _desglose_negativo_en_payload(payload: dict[str, Any], moneda_amount: str) -> int:
    """Cuenta bloques fiscales con CurrencyCode coincidente y Amount < 0."""
    n = 0
    for clave in ("ItemPrice", "ItemTax", "ShippingPrice", "ShippingTax"):
        bloque = payload.get(clave)
        if not isinstance(bloque, dict):
            continue
        code = (bloque.get("CurrencyCode") or "").strip().upper()
        if code != moneda_amount:
            continue
        raw = bloque.get("Amount")
        if raw is None:
            continue
        try:
            valor = Decimal(str(raw))
        except Exception:
            continue
        if valor.is_finite() and valor < 0:
            n += 1
    return n


# ARREGLOS 03 D-I: los ocho bloques del item de pedido de SP-API con los que
# contabilidad arma el precio bruto de la venta antes de pasarlo a pesos
# (goncloud-accounting scripts/sync_amazon.py, gross_price): +1 suma, -1 resta.
_BLOQUES_VENTA: tuple[tuple[str, int], ...] = (
    ("ItemPrice", 1),
    ("ItemTax", 1),
    ("ShippingPrice", 1),
    ("ShippingTax", 1),
    ("ShippingDiscount", -1),
    ("ShippingDiscountTax", -1),
    ("PromotionDiscount", -1),
    ("PromotionDiscountTax", -1),
)

_SIN_ORIGINAL: tuple[None, None, None] = (None, None, None)


def _importe_de_bloque(bloque: dict[str, Any]) -> Decimal | None:
    """Amount de un bloque de SP-API; None si falta o no es un numero usable."""
    raw = bloque.get("Amount")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        valor = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if not valor.is_finite() or abs(valor) >= _MAX_AMOUNT:
        return None
    return valor


def _original_de(
    payload: dict[str, Any], kind: str, moneda: str, dia: date
) -> tuple[Decimal | None, str | None, date | None]:
    """(amount_original, amount_original_currency, fx_fecha) del evento.

    Ventas: suma de los ocho bloques de SP-API si todos los presentes traen
    la misma CurrencyCode y es distinta de `moneda`; un bloque ausente vale 0
    (igual que contabilidad); un bloque con otra moneda o ilegible -> las
    tres NULL. Un total que no es > 0 deja el monto NULL con la moneda puesta.
    Cargos, devoluciones y retenciones: contabilidad solo manda
    `orig_currency`, asi que el monto queda NULL con la moneda puesta
    (dividir pesos entre una tasa seria inventarlo).
    `fx_fecha` = `dia` solo con fx_source 'exact' (la tasa del dia del
    evento); con 'nearest_prior' u otro, NULL.
    """
    fx = dia if payload.get("fx_source") == "exact" else None
    if kind != "sale":
        origen = payload.get("orig_currency")
        if not isinstance(origen, str):
            return _SIN_ORIGINAL
        origen = origen.strip().upper()
        if origen not in MONEDAS or origen == moneda:
            return _SIN_ORIGINAL
        return None, origen, fx

    codigo: str | None = None
    total = Decimal(0)
    for clave, signo in _BLOQUES_VENTA:
        bloque = payload.get(clave)
        if bloque is None:
            continue
        if not isinstance(bloque, dict):
            return _SIN_ORIGINAL
        code = bloque.get("CurrencyCode")
        if not isinstance(code, str) or code.strip().upper() not in MONEDAS:
            return _SIN_ORIGINAL
        code = code.strip().upper()
        if codigo is not None and code != codigo:
            return _SIN_ORIGINAL
        codigo = code
        valor = _importe_de_bloque(bloque)
        if valor is None:
            return _SIN_ORIGINAL
        total += signo * valor
    if codigo is None or codigo == moneda:
        return _SIN_ORIGINAL
    total = total.quantize(_MAX_DECIMALES)
    if total <= 0 or total >= _MAX_AMOUNT:
        return None, codigo, fx
    return total, codigo, fx


def _amount_decimal(amount: float) -> Decimal | None:
    valor = Decimal(str(amount))
    if not valor.is_finite():
        return None
    if abs(valor) >= _MAX_AMOUNT:
        return None
    try:
        cuantizado = valor.quantize(_MAX_DECIMALES)
    except InvalidOperation:
        return None
    if abs(valor - cuantizado) >= _RUIDO_FLOAT:
        return None
    return cuantizado


def _motivo_skip_basico(fila: FilaOrigenLedger) -> str | None:
    plataforma = (fila.platform or "").strip()
    if plataforma not in _PLATAFORMA_ORIGEN:
        return f"plataforma fuera de dominio: {fila.platform}"
    if _lookup_kind(fila.event_type, fila.fee_category) is None:
        return "event_type desconocido"
    if (
        fila.amount is None
        or isinstance(fila.amount, bool)
        or not isinstance(fila.amount, (int, float))
    ):
        return "amount nulo o no finito"
    if not math.isfinite(fila.amount):
        return "amount nulo o no finito"
    if not isinstance(fila.currency, str) or fila.currency.strip().upper() not in MONEDAS:
        return f"moneda fuera de dominio (MXN/USD): {fila.currency}"
    if not isinstance(fila.event_date, str) or _dia(fila.event_date) is None:
        return "fecha ilegible (event_date)"
    if _amount_decimal(fila.amount) is None:
        valor = Decimal(str(fila.amount))
        if abs(valor) >= _MAX_AMOUNT:
            return "amount fuera de rango NUMERIC(14,4)"
        return "amount con mas de 4 decimales"
    return None


def mapear_destino(
    fila: FilaOrigenLedger,
    listings: dict[tuple[str, str], int] | None = None,
    productos: dict[str, int] | None = None,
) -> EventoLedger | None:
    """Fila cruda → EventoLedger. None = skip (motivo via plan_eventos)."""
    if _motivo_skip_basico(fila) is not None:
        return None
    assert isinstance(fila.amount, (int, float)) and not isinstance(fila.amount, bool)
    assert isinstance(fila.currency, str)
    kind_fee = _lookup_kind(fila.event_type, fila.fee_category)
    assert kind_fee is not None
    kind, fee_type = kind_fee
    amount = _amount_decimal(fila.amount)
    assert amount is not None
    # D4: signo TAL CUAL; lo que viola el CHECK no se escribe ni se voltea.
    if kind == "sale" and amount <= 0:
        return None
    if kind in ("fee", "refund", "withholding") and amount > 0:
        return None

    plataforma = _PLATAFORMA_ORIGEN[fila.platform.strip()]
    # ARREGLOS 03 D-H: MeLi no manda fee_category en sus devoluciones; una
    # devolucion sin categoria es una devolucion (la publicidad de MeLi nunca
    # llega como refund: entra como fee con categoria ads). Amazon conserva
    # NULL: su contrato es la categoria o nada.
    if plataforma == "meli" and kind == "refund" and fee_type is None:
        fee_type = "refund"
    moneda = fila.currency.strip().upper()
    payload = _parse_payload(fila.raw_payload)
    order_raw = fila.order_id
    order_id = None if order_raw is None or str(order_raw).strip() == "" else str(order_raw).strip()

    if fila.dedupe_key is None or str(fila.dedupe_key).strip() == "":
        source_event_id = None
    else:
        source_event_id = str(fila.dedupe_key).strip()

    quantity = fila.quantity
    if quantity is None and kind == "sale":
        qs = payload.get("QuantityShipped")
        if isinstance(qs, int):
            quantity = qs
        elif isinstance(qs, str) and qs.isdigit():
            quantity = int(qs)

    product_id = None
    if kind == "sale" and listings is not None:
        asin = payload.get("ASIN")
        if isinstance(asin, str) and asin.strip():
            product_id = listings.get((plataforma, asin.strip()))
    if kind == "sale" and product_id is None and plataforma == "meli" and productos is not None:
        sku = fila.sku
        if isinstance(sku, str) and sku.strip():
            product_id = productos.get(sku.strip())

    # CHECK ledger_venta_con_cantidad: sale + product_id exige quantity > 0.
    if kind == "sale" and product_id is not None and (quantity is None or quantity <= 0):
        product_id = None

    dia = _dia(fila.event_date)
    assert dia is not None  # _motivo_skip_basico ya rechazo la fecha ilegible
    amount_original, moneda_original, fx_fecha = _original_de(payload, kind, moneda, dia)

    return EventoLedger(
        platform=plataforma,
        kind=kind,
        event_date=dia,
        order_id=order_id,
        product_id=product_id,
        quantity=quantity,
        amount=amount,
        amount_currency=moneda,
        item_price=_money_from_payload(payload, "ItemPrice", moneda),
        item_tax=_money_from_payload(payload, "ItemTax", moneda),
        shipping_price=_money_from_payload(payload, "ShippingPrice", moneda),
        shipping_tax=_money_from_payload(payload, "ShippingTax", moneda),
        fee_type=fee_type,
        source_event_id=source_event_id,
        amount_original=amount_original,
        amount_original_currency=moneda_original,
        fx_fecha=fx_fecha,
        payload=payload,
    )


def plan_eventos(
    filas: list[FilaOrigenLedger] | tuple[FilaOrigenLedger, ...],
    *,
    listings: dict[tuple[str, str], int],
    productos: dict[str, int],
) -> tuple[list[EventoLedger], Counter, Counter]:
    """Filas crudas → eventos listos + skips (no escritos) + huecos producto.

    Los huecos ASIN/listing se CUENTAN (D5) pero la fila SI se escribe: viven
    en el contador de cobertura, no en rows_skipped (que es solo no-escrito).
    """
    eventos: list[EventoLedger] = []
    skips: Counter = Counter()
    huecos: Counter = Counter()
    for fila in filas:
        motivo = _motivo_skip_basico(fila)
        if motivo is not None:
            skips[motivo] += 1
            continue
        assert isinstance(fila.amount, (int, float)) and not isinstance(fila.amount, bool)
        kind_fee = _lookup_kind(fila.event_type, fila.fee_category)
        assert kind_fee is not None
        kind, _ = kind_fee
        amount = _amount_decimal(fila.amount)
        assert amount is not None
        if kind == "sale" and amount <= 0:
            skips["viola ledger_convencion_signos"] += 1
            continue
        if kind in ("fee", "refund", "withholding") and amount > 0:
            skips["viola ledger_convencion_signos"] += 1
            continue
        ev = mapear_destino(fila, listings=listings, productos=productos)
        assert ev is not None
        payload = ev.payload or {}
        huecos["desglose_fiscal_negativo_descartado"] += _desglose_negativo_en_payload(
            payload, ev.amount_currency
        )
        if ev.kind == "sale" and ev.platform != "meli":
            asin = payload.get("ASIN")
            if not (isinstance(asin, str) and asin.strip()):
                huecos["venta sin ASIN"] += 1
            else:
                asin_s = asin.strip()
                listing_pid = listings.get((ev.platform, asin_s))
                qty_ok = ev.quantity is not None and ev.quantity > 0
                if listing_pid is None:
                    huecos["sin listing para ASIN"] += 1
                elif not qty_ok:
                    huecos["venta con ASIN sin cantidad"] += 1
        if ev.kind == "sale" and ev.platform == "meli":
            sku = fila.sku
            if not (isinstance(sku, str) and sku.strip()):
                huecos["venta meli sin sku"] += 1
            elif productos.get(sku.strip()) is None:
                huecos["venta meli sin producto"] += 1
            elif ev.quantity is None or ev.quantity <= 0:
                huecos["venta meli sin cantidad"] += 1
        eventos.append(ev)
    return eventos, skips, huecos


def _rows_skipped(skips: Counter) -> int:
    """Solo lo no escrito: `venta_sin_atribuir` va al skip_reason y a un
    contador aparte, no a rows_skipped (REPRICING 02 0.a)."""
    return sum(n for motivo, n in skips.items() if motivo != "venta_sin_atribuir")


_formato_skip_reason = formato_skip_reason

_COLS = (
    "platform, kind, event_date, order_id, product_id, quantity,"
    " amount, amount_currency, item_price, item_tax, shipping_price,"
    " shipping_tax, fee_type, source_event_id, amount_original,"
    " amount_original_currency, fx_fecha, ingest_run_id"
)
_VALS = ", ".join(["%s"] * len(_COLS.split(",")))

# Tres indices parciales: el ON CONFLICT debe coincidir con el predicado.
_SQL_INSERT_SOURCE = f"""
INSERT INTO ledger_event ({_COLS})
VALUES ({_VALS})
ON CONFLICT (platform, kind, source_event_id)
 WHERE source_event_id IS NOT NULL
 DO NOTHING
"""

_SQL_INSERT_SIN_ORDEN = f"""
INSERT INTO ledger_event ({_COLS})
VALUES ({_VALS})
ON CONFLICT (platform, kind, fee_type, event_date, amount, amount_currency)
 WHERE source_event_id IS NULL AND order_id IS NULL
 DO NOTHING
"""

_SQL_INSERT_CON_ORDEN = f"""
INSERT INTO ledger_event ({_COLS})
VALUES ({_VALS})
ON CONFLICT (platform, kind, order_id, fee_type, event_date, amount, amount_currency)
 WHERE source_event_id IS NULL AND order_id IS NOT NULL
 DO NOTHING
"""


def _sql_insert_para(ev: EventoLedger) -> str:
    if ev.source_event_id is not None:
        return _SQL_INSERT_SOURCE
    if ev.order_id is None:
        return _SQL_INSERT_SIN_ORDEN
    return _SQL_INSERT_CON_ORDEN


def _params(ev: EventoLedger, run_id: int) -> tuple:
    return (
        ev.platform,
        ev.kind,
        ev.event_date,
        ev.order_id,
        ev.product_id,
        ev.quantity,
        ev.amount,
        ev.amount_currency,
        ev.item_price,
        ev.item_tax,
        ev.shipping_price,
        ev.shipping_tax,
        ev.fee_type,
        ev.source_event_id,
        ev.amount_original,
        ev.amount_original_currency,
        ev.fx_fecha,
        run_id,
    )


_SQL_ATRIBUIR = """
INSERT INTO ledger_event_atribucion
    (ledger_event_id, platform, listing_id, variante_ext, resuelto_por, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (ledger_event_id) DO NOTHING
"""

# Solo ventas de Amazon: la atribucion de MeLi sale del SKU y no del mapa
# ASIN -> listing (ver el diseno de H-H4).
_PLATAFORMAS_REATRIBUIBLES = frozenset({"amazon_mx", "amazon_us"})

_SQL_REATRIBUIR = """
UPDATE ledger_event
   SET product_id = %s
 WHERE id = %s
   AND product_id IS NOT NULL
   AND product_id <> %s
"""


def _id_de_evento(conn: psycopg.Connection, ev: EventoLedger) -> int | None:
    """Id del evento en ledger_event por su llave de dedupe, insertado ahora
    o en una corrida anterior (None si el indice parcial todavia no lo vio)."""
    if ev.source_event_id is not None:
        fila = conn.execute(
            "SELECT id FROM ledger_event"
            " WHERE platform = %s AND kind = %s AND source_event_id = %s",
            (ev.platform, ev.kind, ev.source_event_id),
        ).fetchone()
    elif ev.order_id is None:
        fila = conn.execute(
            "SELECT id FROM ledger_event"
            " WHERE platform = %s AND kind = %s AND fee_type IS NOT DISTINCT FROM %s"
            " AND event_date = %s AND amount = %s AND amount_currency = %s"
            " AND source_event_id IS NULL AND order_id IS NULL",
            (ev.platform, ev.kind, ev.fee_type, ev.event_date, ev.amount, ev.amount_currency),
        ).fetchone()
    else:
        fila = conn.execute(
            "SELECT id FROM ledger_event"
            " WHERE platform = %s AND kind = %s AND order_id = %s"
            " AND fee_type IS NOT DISTINCT FROM %s AND event_date = %s"
            " AND amount = %s AND amount_currency = %s"
            " AND source_event_id IS NULL",
            (
                ev.platform,
                ev.kind,
                ev.order_id,
                ev.fee_type,
                ev.event_date,
                ev.amount,
                ev.amount_currency,
            ),
        ).fetchone()
    return None if fila is None else fila[0]


def _atribuir_ventas(
    conn: psycopg.Connection,
    ventas: list[EventoLedger],
    *,
    run_id: int,
    atribuidores: Mapping[str, Atribuidor],
) -> Counter:
    """Atribuye toda venta del plan, tambien la ya ingerida: la primera
    corrida con atribuidor llena la historia. Devuelve el contador de las
    que quedaron sin atribuir (van al skip_reason, no a rows_skipped)."""
    sin_atribuir: Counter = Counter()
    for ev in ventas:
        atribuidor = atribuidores.get(ev.platform)
        if atribuidor is None:
            sin_atribuir["venta_sin_atribuir"] += 1
            continue
        try:
            atribucion = atribuidor.atribuir(ev)
        except Exception as exc:
            logger.warning("atribucion de platform=%s reviento: %s", ev.platform, scrub(str(exc)))
            sin_atribuir["venta_sin_atribuir"] += 1
            continue
        if atribucion is None:
            sin_atribuir["venta_sin_atribuir"] += 1
            continue
        eid = _id_de_evento(conn, ev)
        if eid is None:
            sin_atribuir["venta_sin_atribuir"] += 1
            continue
        conn.execute(
            _SQL_ATRIBUIR,
            (
                eid,
                ev.platform,
                atribucion.listing_id,
                atribucion.variante,
                atribucion.resuelto_por,
                run_id,
            ),
        )
    return sin_atribuir


def _reatribuir_ventas(
    conn: psycopg.Connection,
    ventas: list[EventoLedger],
    *,
    run_id: int,
) -> int:
    """Corrige el product_id de ventas de Amazon ya ingeridas cuando el mapa
    ASIN -> listing cambio desde la primera ingesta. El trigger
    ledger_solo_reatribuye escribe la auditoria en ledger_reatribucion.

    No toca ventas sin producto (Q3: no se reatribuye NULL -> producto) ni
    ventas cuyo producto ahora es NULL (el mapa no decide, se conserva el
    viejo). Devuelve cuantas filas cambio.
    """
    cambiadas = 0
    # El trigger lee esta marca local para sellar ingest_run_id en la auditoria.
    conn.execute("SELECT set_config('orbit.ingest_run_id', %s, true)", (str(run_id),))
    for ev in ventas:
        if (
            ev.kind != "sale"
            or ev.platform not in _PLATAFORMAS_REATRIBUIBLES
            or ev.product_id is None
        ):
            continue
        eid = _id_de_evento(conn, ev)
        if eid is None:
            continue
        cur = conn.execute(_SQL_REATRIBUIR, (ev.product_id, eid, ev.product_id))
        cambiadas += cur.rowcount or 0
    return cambiadas


def sync_ledger(
    conn: psycopg.Connection,
    ruta_sqlite: Path | str,
    *,
    atribuidores: Mapping[str, Atribuidor] | None = None,
) -> ResultadoSync:
    """Corre la ingesta completa y sella su ingest_run.

    Patron costs/listings: abrir run en txn propia, trabajo en otra, sello
    al final. Sin SELECT antes de la primera transaccion (bug corridas 35/36).
    """
    origen = leer_origen(ruta_sqlite)

    with conn.transaction():
        run_id = abrir_run(conn, SOURCE)

    insertadas = 0
    por_kind: Counter = Counter()
    eventos: list[EventoLedger] = []
    skips: Counter = Counter()
    huecos: Counter = Counter()
    if atribuidores is None:
        atr_amz = AtribuidorAmazon(conn)
        despacho: Mapping[str, Atribuidor] = {
            "amazon_mx": atr_amz,
            "amazon_us": atr_amz,
            "meli": AtribuidorMeli(conn),
        }
    else:
        despacho = atribuidores
    try:
        with conn.transaction():
            listings = {
                (plataforma, external_id): product_id
                for plataforma, external_id, product_id in conn.execute(
                    "SELECT platform, external_id, product_id FROM listing"
                )
            }
            productos = {
                sku.strip(): pid
                for sku, pid in conn.execute(
                    "SELECT odoo_sku, id FROM product WHERE odoo_sku IS NOT NULL"
                )
                if isinstance(sku, str) and sku.strip()
            }
            eventos, skips, huecos = plan_eventos(origen, listings=listings, productos=productos)
            for ev in eventos:
                cur = conn.execute(_sql_insert_para(ev), _params(ev, run_id))
                if cur.rowcount and cur.rowcount > 0:
                    insertadas += 1
                    por_kind[ev.kind] += 1
                else:
                    skips["conflicto dedupe"] += 1
            ventas = [ev for ev in eventos if ev.kind == "sale"]
            tiene_tabla_atrib = atribuidores is not None or (
                conn.execute("SELECT to_regclass('public.ledger_event_atribucion')").fetchone()[0]
                is not None
            )
            if tiene_tabla_atrib:
                skips.update(
                    _atribuir_ventas(
                        conn,
                        ventas,
                        run_id=run_id,
                        atribuidores=despacho,
                    )
                )
            reatribuidas = _reatribuir_ventas(conn, ventas, run_id=run_id)

            skip_reason = _formato_skip_reason(skips)
            sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=insertadas,
                rows_skipped=_rows_skipped(skips),
                skip_reason=skip_reason,
            )
    except BaseException as exc:
        sellar_fallo_seguro(conn, run_id, exc)
        raise

    fechas = [ev.event_date for ev in eventos]
    return ResultadoSync(
        run_id=run_id,
        ok=True,
        rows_written=insertadas,
        rows_skipped=_rows_skipped(skips),
        skip_reason=_formato_skip_reason(skips),
        filas_origen=len(origen),
        eventos_finales=len(eventos),
        por_kind=dict(por_kind),
        ventas_sin_asin=huecos["venta sin ASIN"],
        sin_listing_para_asin=huecos["sin listing para ASIN"],
        venta_con_asin_sin_cantidad=huecos["venta con ASIN sin cantidad"],
        desglose_fiscal_negativo_descartado=huecos["desglose_fiscal_negativo_descartado"],
        ventas_meli_sin_sku=huecos["venta meli sin sku"],
        ventas_meli_sin_producto=huecos["venta meli sin producto"],
        ventas_meli_sin_cantidad=huecos["venta meli sin cantidad"],
        reatribuidas=reatribuidas,
        rango_min=min(fechas) if fechas else None,
        rango_max=max(fechas) if fechas else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest ledger",
        description=(
            "Ingresa el ledger (ventas + fees + refunds + withholdings) desde"
            " un snapshot read-only de la SQLite de contabilidad"
            " (runbook: docs/DEPLOY.md, 'Ingesta del ledger')."
        ),
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        help="ruta del snapshot (producido con la API .backup(); ver runbook)",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir ledger (fail-closed)",
            file=sys.stderr,
        )
        return 2
    ruta = Path(args.sqlite)
    if not ruta.is_file():
        print(
            f"el snapshot no existe: {ruta} (runbook: docs/DEPLOY.md, ingesta del ledger)",
            file=sys.stderr,
        )
        return 2
    try:
        conn = connect(dsn)
        try:
            resultado = sync_ledger(conn, ruta)
        finally:
            conn.close()
    except Exception as exc:
        print(
            "ingesta del ledger fallo (la ingest_run quedo sellada ok=false cuando"
            f" fue posible): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1

    print(f"== Ingesta del ledger desde contabilidad ({SOURCE}) ==")
    print(
        f"run_id={resultado.run_id} ok={resultado.ok}"
        f" rows_written={resultado.rows_written} rows_skipped={resultado.rows_skipped}"
        f" reatribuidas={resultado.reatribuidas}"
    )
    print(
        f"origen={resultado.filas_origen} planificados={resultado.eventos_finales}"
        f" por_kind={resultado.por_kind}"
    )
    print(f"ventana: rango_min={resultado.rango_min} rango_max={resultado.rango_max}")
    print(
        f"cobertura_producto: venta_sin_ASIN={resultado.ventas_sin_asin}"
        f" sin_listing={resultado.sin_listing_para_asin}"
        f" asin_sin_cantidad={resultado.venta_con_asin_sin_cantidad}"
        f" meli_sin_sku={resultado.ventas_meli_sin_sku}"
        f" meli_sin_producto={resultado.ventas_meli_sin_producto}"
        f" meli_sin_cantidad={resultado.ventas_meli_sin_cantidad}"
    )
    if resultado.desglose_fiscal_negativo_descartado:
        print(
            f"desglose_fiscal_negativo_descartado={resultado.desglose_fiscal_negativo_descartado}"
        )
    if resultado.skip_reason:
        print(f"skips: {resultado.skip_reason}")
    return 0
