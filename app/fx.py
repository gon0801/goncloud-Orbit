"""Ingesta de tipos de cambio desde la SQLite de contabilidad (ORBIT 06 0.5).

Fuente: snapshot READ-ONLY de `currency_rates` (mismo runbook `.backup()`
que costos). Destino: `fx_rate` de Orbit (rol ORBIT_DSN_INGEST). Este
modulo NUNCA escribe en contabilidad y NO toca `fx_resolve` (sellado 3).

Decisiones selladas (plans/orbit-06.md, "Decisiones de la 0.5"):

1. LA TRAMPA: la fuente trae `(base=MXN, quote=USD, rate≈16.9–18.6)` — el
   numero es pesos por dolar, pero las etiquetas estan invertidas. El
   consumidor llama `fx_resolve(fecha, 'USD', 'MXN')` y MULTIPLICA
   (`monto_USD * rate = monto_MXN`). Mapeo: conservar el numero, invertir
   etiquetas → `(USD, MXN, rate)`. Una tasa ~17 con base MXN es
   semanticamente imposible (nadie paga 17 dolares por un peso).
2. Par distinto de (MXN, USD) → skip contado. Rate ≤ 0 / NULL / no finito
   → skip (sellado 1 + CHECK fx_rate_positiva).
3. Re-corrida: INSERT … ON CONFLICT DO NOTHING sobre la PK. Jamas UPDATE
   (`prohibir_mutacion`).

`python -m app.cli ingest fx --sqlite RUTA`: sin DSN o sin snapshot →
exit 2 fail-closed; fallo de corrida → exit 1; exito → 0.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg

from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "accounting_currency_rates"

# NUMERIC(18,8): el ruido binario del REAL de SQLite (residuo << 1e-9) se
# cuantiza; precision genuina mas alla de 8 decimales se rechaza.
_MAX_DECIMALES = Decimal("0.00000001")
_RUIDO_FLOAT = Decimal("0.000000001")
_MAX_RATE = Decimal(10) ** 10


class FxError(Exception):
    """Error de la ingesta FX (snapshot inexistente o ilegible)."""


@dataclass(frozen=True)
class FilaOrigenFx:
    """Fila cruda de currency_rates (etiquetas tal cual vienen)."""

    rate_date: str  # 'YYYY-MM-DD'
    base: str
    quote: str
    rate: float | None


@dataclass(frozen=True)
class TasaFx:
    """Fila lista para fx_rate: etiquetas ya corregidas (USD→MXN)."""

    rate_date: date
    base: str
    quote: str
    rate: Decimal


@dataclass(frozen=True)
class ResultadoSync:
    """Outcome contable de la corrida (espejo de la ingest_run sellada)."""

    run_id: int
    ok: bool
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    filas_origen: int
    tasas_finales: int
    rango_min: date | None = None
    rango_max: date | None = None
    huecos_gt_3d: tuple[tuple[date, date, int], ...] = ()


def detectar_huecos(
    tasas: list[TasaFx] | tuple[TasaFx, ...], umbral: int = 3
) -> tuple[tuple[date, date, int], ...]:
    """Huecos de calendario > umbral dias (obstaculo 2 / evidencia de corrida)."""
    if len(tasas) < 2:
        return ()
    ordenadas = sorted(tasas, key=lambda t: t.rate_date)
    huecos: list[tuple[date, date, int]] = []
    for prev, nxt in zip(ordenadas, ordenadas[1:], strict=False):
        dias = (nxt.rate_date - prev.rate_date).days
        if dias > umbral:
            huecos.append((prev.rate_date, nxt.rate_date, dias))
    return tuple(huecos)


# ---------------------------------------------------------------------------
# Lectura del snapshot (read-only por construccion: mode=ro + solo SELECT)
# ---------------------------------------------------------------------------


def leer_origen(ruta: Path | str) -> tuple[FilaOrigenFx, ...]:
    """Lee currency_rates del snapshot. Abre con mode=ro."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise FxError(f"snapshot inexistente: {ruta}")
    con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    try:
        return tuple(
            FilaOrigenFx(
                rate_date=fila[0] or "",
                base=fila[1] or "",
                quote=fila[2] or "",
                rate=fila[3],
            )
            for fila in con.execute(
                "SELECT rate_date, base_currency, quote_currency, rate"
                " FROM currency_rates"
                " ORDER BY rate_date, base_currency, quote_currency"
            )
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Mapeo puro: etiquetas invertidas, numero se conserva (D1)
# ---------------------------------------------------------------------------


def _dia(texto: str) -> date | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto).date()
    except ValueError:
        return None


def _motivo_skip(fila: FilaOrigenFx) -> str | None:
    """Sellado 1 + D1: que hace que una fila NO se escriba."""
    if fila.rate is None or not math.isfinite(fila.rate) or fila.rate <= 0:
        return "rate no positivo o nulo (dato faltante)"
    base = (fila.base or "").strip().upper()
    quote = (fila.quote or "").strip().upper()
    if (base, quote) != ("MXN", "USD"):
        return "par de monedas no soportado"
    if _dia(fila.rate_date) is None:
        return "fecha ilegible (rate_date)"
    valor = Decimal(str(fila.rate))
    if abs(valor - valor.quantize(_MAX_DECIMALES)) >= _RUIDO_FLOAT:
        return "rate con mas de 8 decimales"
    if valor >= _MAX_RATE:
        return "rate fuera de rango NUMERIC(18,8)"
    if valor.quantize(_MAX_DECIMALES) <= 0:
        return "rate no positivo o nulo (dato faltante)"
    return None


def mapear_destino(fila: FilaOrigenFx) -> TasaFx | None:
    """Fuente (MXN, USD, rate) → destino (USD, MXN, rate). None = skip."""
    if _motivo_skip(fila) is not None:
        return None
    dia = _dia(fila.rate_date)
    assert dia is not None
    return TasaFx(
        rate_date=dia,
        base="USD",
        quote="MXN",
        rate=Decimal(str(fila.rate)).quantize(_MAX_DECIMALES),
    )


def plan_tasas(
    filas: list[FilaOrigenFx] | tuple[FilaOrigenFx, ...],
) -> tuple[list[TasaFx], Counter]:
    """Filas crudas → tasas listas + skips contados por motivo."""
    tasas: list[TasaFx] = []
    skips: Counter = Counter()
    for fila in filas:
        motivo = _motivo_skip(fila)
        if motivo is not None:
            skips[motivo] += 1
            continue
        tasa = mapear_destino(fila)
        assert tasa is not None
        tasas.append(tasa)
    return tasas, skips


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


# ---------------------------------------------------------------------------
# Escritura en Orbit (fx_rate, rol de ingesta)
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

# PK (rate_date, base, quote): conflicto = no-op REAL. Nunca UPDATE.
_SQL_INSERTAR = """
INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, ingest_run_id)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (rate_date, base_currency, quote_currency) DO NOTHING
"""


def sync_fx(conn: psycopg.Connection, ruta_sqlite: Path | str) -> ResultadoSync:
    """Corre la ingesta completa y sella su ingest_run.

    Patron structure/costs: abrir run en txn propia, trabajo en otra, sello
    al final. Sin SELECT antes de la primera transaccion (bug corridas 35/36).
    """
    origen = leer_origen(ruta_sqlite)
    tasas, skips = plan_tasas(origen)
    huecos = detectar_huecos(tasas)

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    insertadas = 0
    try:
        with conn.transaction():
            for tasa in tasas:
                cur = conn.execute(
                    _SQL_INSERTAR,
                    (tasa.rate_date, tasa.base, tasa.quote, tasa.rate, run_id),
                )
                # DO NOTHING deja rowcount=0; solo contamos filas nuevas.
                if cur.rowcount and cur.rowcount > 0:
                    insertadas += 1

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

    return ResultadoSync(
        run_id=run_id,
        ok=True,
        rows_written=insertadas,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        filas_origen=len(origen),
        tasas_finales=len(tasas),
        rango_min=min((t.rate_date for t in tasas), default=None),
        rango_max=max((t.rate_date for t in tasas), default=None),
        huecos_gt_3d=huecos,
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
        prog="python -m app.cli ingest fx",
        description=(
            "Ingresa tipos de cambio desde un snapshot read-only de la SQLite"
            " de contabilidad (runbook: docs/DEPLOY.md, 'Ingesta de tipos de"
            " cambio')."
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
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir FX (fail-closed)",
            file=sys.stderr,
        )
        return 2
    ruta = Path(args.sqlite)
    if not ruta.is_file():
        print(
            f"el snapshot no existe: {ruta} (runbook: docs/DEPLOY.md, ingesta de FX)",
            file=sys.stderr,
        )
        return 2
    try:
        conn = connect(dsn)
        try:
            resultado = sync_fx(conn, ruta)
        finally:
            conn.close()
    except Exception as exc:
        print(
            "ingesta de FX fallo (la ingest_run quedo sellada ok=false cuando"
            f" fue posible): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1

    print(f"== Ingesta de tipos de cambio ({SOURCE}) ==")
    print(
        f"run_id={resultado.run_id} ok={resultado.ok}"
        f" rows_written={resultado.rows_written} rows_skipped={resultado.rows_skipped}"
    )
    print(
        f"tasas: finales={resultado.tasas_finales}"
        f" (filas origen: {resultado.filas_origen})"
        f" rango={resultado.rango_min}..{resultado.rango_max}"
    )
    if resultado.huecos_gt_3d:
        lista = "; ".join(f"{a}->{b} ({d}d)" for a, b, d in resultado.huecos_gt_3d)
        print(f"huecos_>3d: {lista}")
    else:
        print("huecos_>3d: (ninguno)")
    if resultado.skip_reason:
        print(f"skips: {resultado.skip_reason}")
    return 0
