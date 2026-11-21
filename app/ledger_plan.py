"""Mapeo y planificacion pura de eventos de ledger (extraido de app/ledger.py, Fase 3.5).

Convierte filas crudas de `ledger_events` (contabilidad) en `EventoLedger`
validados, conteos de skips y huecos de cobertura sin tocar SQLite ni Postgres.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# (event_type, fee_category|None) -> (kind, fee_type_policy).
# fee_type_policy None -> fee_type NULL; "category" -> fee_category (o NULL).
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

MONEDAS = ("MXN", "USD")
_MAX_DECIMALES = Decimal("0.0001")
_RUIDO_FLOAT = Decimal("0.00001")
_MAX_AMOUNT = Decimal(10) ** 10


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
    de MeLi). `amount_original`, `amount_original_currency` y `fx_fecha`
    (ARREGLOS 03 D-I): importe antes de conversion, moneda original y dia
    de tasa exacta.
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
    """ItemPrice/ItemTax/... -> Decimal si CurrencyCode coincide; si no, NULL."""
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
    """(amount_original, amount_original_currency, fx_fecha) del evento."""
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
    """Fila cruda -> EventoLedger. None = skip (motivo via plan_eventos)."""
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
    assert dia is not None
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
    """Filas crudas -> eventos listos + skips (no escritos) + huecos producto."""
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
