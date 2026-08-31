"""Ingesta del ledger desde la SQLite de contabilidad (ORBIT 06 0.6).

Fuente: snapshot READ-ONLY de `ledger_events` (mismo runbook `.backup()`
que costos). Destino: `ledger_event` de Orbit (rol ORBIT_DSN_INGEST). Este
modulo NUNCA escribe en contabilidad y no toca decision / apply_queue /
goals.

Decisiones selladas (plans/orbit-06.md, "Decisiones de la 0.6"):

1. Alcance Amazon; MeLi excluida contada. `amazon`→`amazon_mx`.
2. Kind via MAPA_KIND (tabla): sale_gross→sale; refund→refund;
   fee+tax/isr_withheld→withholding; resto fee→fee (ads conserva etiqueta).
3. Acceso `--sqlite` mode=ro.
4. Signo TAL CUAL; filas que violan ledger_convencion_signos → NO escritas
   (jamas voltear un fee+).
5. product_id solo ASIN→listing; cogs_at_sale NO se ingiere.
6. source_event_id=dedupe_key; order_id ''→NULL; ISR sin orden ENTRA;
   ON CONFLICT DO NOTHING en los tres indices de dedupe.
7. event_date timestamp→DATE; no fusionar dedupe_keys distintos.
8. Desglose fiscal del raw_payload si CurrencyCode coincide; moneda = la
   del amount (amazon_us puede ser MXN).

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
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg

from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "accounting_ledger_events"

# (event_type, fee_category|None) → (kind, fee_type_policy).
# fee_type_policy None → fee_type NULL; "category" → fee_category (o NULL).
# sale/refund ignoran fee_category en el lookup (clave con None).
# fee+withholding se clavea por categoria exacta; el resto de fee cae en
# ("fee", None).
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
    """Fila lista para ledger_event (sin ingest_run_id)."""

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
    rango_min: date | None = None
    rango_max: date | None = None


# ---------------------------------------------------------------------------
# Lectura del snapshot (read-only por construccion: mode=ro + solo SELECT)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Mapeo puro (D1-D8): frontera parsea; dominio confia en EventoLedger
# ---------------------------------------------------------------------------


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
    if plataforma == "meli":
        return "plataforma meli excluida"
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
) -> EventoLedger | None:
    """Fila cruda → EventoLedger. None = skip (motivo via plan_eventos)."""
    if _motivo_skip_basico(fila) is not None:
        return None
    kind_fee = _lookup_kind(fila.event_type, fila.fee_category)
    assert kind_fee is not None
    kind, fee_type = kind_fee
    amount = _amount_decimal(fila.amount)  # type: ignore[arg-type]
    assert amount is not None
    # D4: signo TAL CUAL; lo que viola el CHECK no se escribe ni se voltea.
    if kind == "sale" and amount <= 0:
        return None
    if kind in ("fee", "refund", "withholding") and amount > 0:
        return None

    plataforma = _PLATAFORMA_ORIGEN[fila.platform.strip()]
    moneda = fila.currency.strip().upper()  # type: ignore[union-attr]
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

    # CHECK ledger_venta_con_cantidad: sale + product_id exige quantity > 0.
    if kind == "sale" and product_id is not None and (quantity is None or quantity <= 0):
        product_id = None

    return EventoLedger(
        platform=plataforma,
        kind=kind,
        event_date=_dia(fila.event_date),  # type: ignore[arg-type]
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
    )


def plan_eventos(
    filas: list[FilaOrigenLedger] | tuple[FilaOrigenLedger, ...],
    *,
    listings: dict[tuple[str, str], int],
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
        kind_fee = _lookup_kind(fila.event_type, fila.fee_category)
        assert kind_fee is not None
        kind, _ = kind_fee
        amount = _amount_decimal(fila.amount)  # type: ignore[arg-type]
        assert amount is not None
        if kind == "sale" and amount <= 0:
            skips["viola ledger_convencion_signos"] += 1
            continue
        if kind in ("fee", "refund", "withholding") and amount > 0:
            skips["viola ledger_convencion_signos"] += 1
            continue
        ev = mapear_destino(fila, listings=listings)
        assert ev is not None
        payload = _parse_payload(fila.raw_payload)
        huecos["desglose_fiscal_negativo_descartado"] += _desglose_negativo_en_payload(
            payload, ev.amount_currency
        )
        if ev.kind == "sale":
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
        eventos.append(ev)
    return eventos, skips, huecos


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


# ---------------------------------------------------------------------------
# Escritura en Orbit (ledger_event, rol de ingesta)
# ---------------------------------------------------------------------------

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id"

_SQL_SELLAR_RUN = """
UPDATE ingest_run
   SET finished_at = now(),
       rows_written = %s,
       rows_skipped = %s,
       skip_reason = %s,
       ok = %s
 WHERE id = %s
"""

_COLS = (
    "platform, kind, event_date, order_id, product_id, quantity,"
    " amount, amount_currency, item_price, item_tax, shipping_price,"
    " shipping_tax, fee_type, source_event_id, ingest_run_id"
)
_VALS = "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"

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
        run_id,
    )


def sync_ledger(conn: psycopg.Connection, ruta_sqlite: Path | str) -> ResultadoSync:
    """Corre la ingesta completa y sella su ingest_run.

    Patron costs/listings: abrir run en txn propia, trabajo en otra, sello
    al final. Sin SELECT antes de la primera transaccion (bug corridas 35/36).
    """
    origen = leer_origen(ruta_sqlite)

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    insertadas = 0
    por_kind: Counter = Counter()
    eventos: list[EventoLedger] = []
    skips: Counter = Counter()
    huecos: Counter = Counter()
    try:
        with conn.transaction():
            listings = {
                (plataforma, external_id): product_id
                for plataforma, external_id, product_id in conn.execute(
                    "SELECT platform, external_id, product_id FROM listing"
                )
            }
            eventos, skips, huecos = plan_eventos(origen, listings=listings)
            for ev in eventos:
                cur = conn.execute(_sql_insert_para(ev), _params(ev, run_id))
                if cur.rowcount and cur.rowcount > 0:
                    insertadas += 1
                    por_kind[ev.kind] += 1
                else:
                    skips["conflicto dedupe"] += 1

            skip_reason = _formato_skip_reason(skips)
            _sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=insertadas,
                rows_skipped=sum(skips.values()),
                skip_reason=skip_reason,
            )
    except BaseException as exc:
        try:
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    rows_written=0,
                    rows_skipped=0,
                    skip_reason=scrub(str(exc)) or type(exc).__name__,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo; "
                "el error original de la corrida era: %s",
                run_id,
                scrub(str(exc)),
            )
        raise

    fechas = [ev.event_date for ev in eventos]
    return ResultadoSync(
        run_id=run_id,
        ok=True,
        rows_written=insertadas,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        filas_origen=len(origen),
        eventos_finales=len(eventos),
        por_kind=dict(por_kind),
        ventas_sin_asin=huecos["venta sin ASIN"],
        sin_listing_para_asin=huecos["sin listing para ASIN"],
        venta_con_asin_sin_cantidad=huecos["venta con ASIN sin cantidad"],
        desglose_fiscal_negativo_descartado=huecos["desglose_fiscal_negativo_descartado"],
        rango_min=min(fechas) if fechas else None,
        rango_max=max(fechas) if fechas else None,
    )


def _sellar_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    rows_written: int,
    rows_skipped: int,
    skip_reason: str | None,
) -> None:
    conn.execute(_SQL_SELLAR_RUN, (rows_written, rows_skipped, skip_reason, ok, run_id))


# ---------------------------------------------------------------------------
# main del pipeline
# ---------------------------------------------------------------------------


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
    )
    if resultado.desglose_fiscal_negativo_descartado:
        print(
            f"desglose_fiscal_negativo_descartado={resultado.desglose_fiscal_negativo_descartado}"
        )
    if resultado.skip_reason:
        print(f"skips: {resultado.skip_reason}")
    return 0
