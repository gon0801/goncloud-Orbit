"""Ingesta MARGEN ESTIMADO 01 A.3 — ofertas frescas + cotizacion Product Fees.

Fuente: snapshot SQLite READ-ONLY del bridge (`amazon_listing_prices`).
Destino: `estimacion_oferta_observation` + `estimacion_fee_observation`.
Reutiliza resolucion A.2 (`app.estimacion_insumos`) y cotizacion A.3
(`app.estimacion_fees`). Las llamadas HTTP ocurren SIN transaccion abierta.

Universo: listings `amazon_mx` con oferta FBA fresca. Cada item falla aislado;
la corrida sella `ingest_run` con filas/errores contados.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from app.db import connect
from app.estimacion_fees import (
    ProductFeesClient,
    construir_fee_outcome_canonical,
    construir_fee_source_event_id,
    cotizar_oferta,
    persistir_fee_observation,
)
from app.estimacion_insumos import (
    InsumosError,
    leer_ofertas_bridge,
    persistir_oferta_observation,
    resolver_oferta_para_listing,
)
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "bridge_estimacion_fees"

# Vocabulario cerrado para skip_reason (evita cardinalidad alta / datos externos).
SKIP_OFERTA_EXCEPCION = "oferta_excepcion"
SKIP_OFERTA_PERSISTENCIA = "oferta_persistencia"
SKIP_FEE_EXCEPCION = "fee_excepcion"
SKIP_FEE_PERSISTENCIA = "fee_persistencia"

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source, started_at) VALUES (%s, %s) RETURNING id"
_SQL_SELLAR_RUN = """
UPDATE ingest_run
   SET finished_at = now(),
       rows_written = %s,
       rows_skipped = %s,
       skip_reason = %s,
       ok = %s
 WHERE id = %s
"""


@dataclass(frozen=True)
class ResultadoIngestaEstimacion:
    run_id: int
    ok: bool
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    ofertas_nuevas: int
    ofertas_reutilizadas: int
    fees_nuevos: int
    fees_reutilizados: int
    fees_error: int


def _formato_skip_reason(skips: Counter[str]) -> str | None:
    if not skips:
        return None
    partes = [f"{n}x {motivo}" for motivo, n in sorted(skips.items())]
    return "; ".join(partes)


def _log_excepcion_item(motivo: str, exc: BaseException) -> None:
    logger.warning("estimacion ingest item skip=%s detalle=%s", motivo, scrub(str(exc)))


def ejecutar_ingesta(
    conn: psycopg.Connection,
    *,
    ruta_sqlite: Path | str,
    now_utc: datetime | None = None,
    client: ProductFeesClient | None = None,
    fee_now_utc: Callable[[], datetime] | None = None,
) -> ResultadoIngestaEstimacion:
    """Pipeline delgado: resuelve ofertas MX, persiste y cotiza fees."""
    ahora = now_utc or datetime.now(UTC)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=UTC)
    ahora = ahora.astimezone(UTC)
    reloj_fee = fee_now_utc or (
        (lambda: ahora) if now_utc is not None else (lambda: datetime.now(UTC))
    )

    filas_bridge = leer_ofertas_bridge(ruta_sqlite)
    skips: Counter[str] = Counter()
    ofertas_nuevas = 0
    ofertas_reutilizadas = 0
    fees_nuevos = 0
    fees_reutilizados = 0
    fees_error = 0
    rows_written = 0

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE, ahora)).fetchone()[0]

    with conn.transaction():
        listings = conn.execute(
            "SELECT id, platform, external_id, seller_sku"
            " FROM listing WHERE platform = 'amazon_mx' ORDER BY id"
        ).fetchall()

    fees_client = client or ProductFeesClient()

    for listing_id, platform, asin, seller_sku in listings:
        try:
            resultado_oferta = resolver_oferta_para_listing(
                filas_bridge,
                listing_id=listing_id,
                platform=platform,
                seller_sku=seller_sku or "",
                asin=asin,
                now_utc=ahora,
            )
        except Exception as exc:
            _log_excepcion_item(SKIP_OFERTA_EXCEPCION, exc)
            skips[SKIP_OFERTA_EXCEPCION] += 1
            continue

        if resultado_oferta.motivo is not None:
            skips[resultado_oferta.motivo] += 1
            continue

        oferta = resultado_oferta.oferta
        assert oferta is not None

        try:
            with conn.transaction():
                persistido = persistir_oferta_observation(
                    conn, oferta, observed_at=ahora, ingest_run_id=run_id
                )
            if persistido.reutilizada:
                ofertas_reutilizadas += 1
            else:
                ofertas_nuevas += 1
                rows_written += 1
        except Exception as exc:
            _log_excepcion_item(SKIP_OFERTA_PERSISTENCIA, exc)
            skips[SKIP_OFERTA_PERSISTENCIA] += 1
            continue

        try:
            cotizacion = cotizar_oferta(fees_client, oferta, now_utc=reloj_fee)
        except Exception as exc:
            _log_excepcion_item(SKIP_FEE_EXCEPCION, exc)
            skips[SKIP_FEE_EXCEPCION] += 1
            continue

        fee_observed_at = cotizacion.fetched_at
        canon_outcome = construir_fee_outcome_canonical(
            oferta, cotizacion, attempted_at=fee_observed_at
        )
        evento = construir_fee_source_event_id(canon_outcome)

        try:
            with conn.transaction():
                fee_row = persistir_fee_observation(
                    conn,
                    oferta_observation_id=persistido.id,
                    oferta=oferta,
                    resultado=cotizacion,
                    observed_at=fee_observed_at,
                    source_event_id=evento,
                    canonical_input=canon_outcome,
                    ingest_run_id=run_id,
                )
            if fee_row.reutilizada:
                fees_reutilizados += 1
            else:
                fees_nuevos += 1
                rows_written += 1
            if cotizacion.estado == "error":
                fees_error += 1
                skips[cotizacion.error_code or "fee_error"] += 1
        except Exception as exc:
            _log_excepcion_item(SKIP_FEE_PERSISTENCIA, exc)
            skips[SKIP_FEE_PERSISTENCIA] += 1

    skip_reason = _formato_skip_reason(skips)
    ok = True
    with conn.transaction():
        _sellar_run(
            conn,
            run_id,
            ok=ok,
            rows_written=rows_written,
            rows_skipped=sum(skips.values()),
            skip_reason=skip_reason,
        )

    return ResultadoIngestaEstimacion(
        run_id=run_id,
        ok=ok,
        rows_written=rows_written,
        rows_skipped=sum(skips.values()),
        skip_reason=skip_reason,
        ofertas_nuevas=ofertas_nuevas,
        ofertas_reutilizadas=ofertas_reutilizadas,
        fees_nuevos=fees_nuevos,
        fees_reutilizados=fees_reutilizados,
        fees_error=fees_error,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest estimacion",
        description=(
            "Ingesta ofertas frescas y cotizacion Product Fees desde snapshot"
            " read-only del bridge (runbook: docs/DEPLOY.md)."
        ),
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        help="ruta del snapshot del bridge (API .backup(); ver runbook)",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir estimacion (fail-closed)",
            file=sys.stderr,
        )
        return 2
    ruta = Path(args.sqlite)
    if not ruta.is_file():
        print(f"snapshot inexistente: {ruta}", file=sys.stderr)
        return 2

    try:
        conn = connect(dsn)
        try:
            resultado = ejecutar_ingesta(conn, ruta_sqlite=ruta)
        finally:
            conn.close()
    except InsumosError as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 1
    except Exception as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 1

    print(f"ingest_run={resultado.run_id} ok={resultado.ok}")
    print(f"rows_written={resultado.rows_written} rows_skipped={resultado.rows_skipped}")
    print(
        f"ofertas nuevas={resultado.ofertas_nuevas} reutilizadas={resultado.ofertas_reutilizadas}"
    )
    print(
        f"fees nuevos={resultado.fees_nuevos} reutilizados={resultado.fees_reutilizados}"
        f" errores={resultado.fees_error}"
    )
    if resultado.skip_reason:
        print(f"skip_reason: {resultado.skip_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
