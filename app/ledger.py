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
import logging
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from app.db import connect
from app.ingest_run import abrir_run, formato_skip_reason, sellar_fallo_seguro, sellar_run
from app.ledger_atribucion import Atribuidor, AtribuidorAmazon, atribuidor_amazon
from app.ledger_plan import (  # noqa: F401 - re-export estable de planificacion pura
    _BLOQUES_VENTA,
    _MAX_AMOUNT,
    _MAX_DECIMALES,
    _PLATAFORMA_ORIGEN,
    _RUIDO_FLOAT,
    _SIN_ORIGINAL,
    MAPA_KIND,
    MONEDAS,
    EventoLedger,
    FilaOrigenLedger,
    _amount_decimal,
    _desglose_negativo_en_payload,
    _dia,
    _importe_de_bloque,
    _lookup_kind,
    _money_from_payload,
    _motivo_skip_basico,
    _original_de,
    _parse_payload,
    _rows_skipped,
    mapear_destino,
    plan_eventos,
)
from app.meli.ledger import AtribuidorMeli, atribuidor_meli
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "accounting_ledger_events"


# REPRICING 02: despacho de la atribucion por plataforma de destino (F.1
# lleno el de Amazon, M.2 el de MeLi). La ingesta inserta lo que devuelvan
# y cuenta `venta_sin_atribuir` cuando no pueden.
_ATRIBUIDORES: dict[str, Atribuidor] = {
    "amazon_mx": atribuidor_amazon,
    "amazon_us": atribuidor_amazon,
    "meli": atribuidor_meli,
}


class LedgerError(Exception):
    """Error de la ingesta del ledger (snapshot inexistente o ilegible)."""


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
