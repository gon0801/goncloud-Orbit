"""Ingesta MARGEN ESTIMADO 01 A.3 — ofertas frescas + cotizacion Product Fees.

Fuente: snapshot SQLite READ-ONLY del bridge (`amazon_listing_prices`).
Destino: `estimacion_oferta_observation` + `estimacion_fee_observation`.
Reutiliza resolucion A.2 (`app.estimacion_insumos`) y cotizacion A.3
(`app.estimacion_fees`). Las llamadas HTTP ocurren SIN transaccion abierta.

Universo: listings Amazon con oferta fresca del registro. Cada item falla aislado;
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
from datetime import UTC, datetime, timedelta
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
    construir_huella_snapshot_listing,
    leer_ofertas_bridge,
    persistir_oferta_observation,
    resolver_oferta_para_listing,
)
from app.estimacion_repository import (
    persistir_escenario,
    sembrar_escenario_desde_refs,
    sembrar_escenario_negativo_transicion,
)
from app.estimacion_universo import plataformas_con_cotizador
from app.meli.estimacion import (
    CotizadorMeli,
    construir_fee_canonical_input_meli,
    construir_fee_outcome_canonical_meli,
    construir_fee_source_event_id_meli,
    construir_huella_meli,
    resolver_oferta_meli,
)
from app.redaction import install_scrub_filter, scrub
from app.reputacion_clientes import ClienteMeli, MeliCredentials, ReputacionError

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "bridge_estimacion_fees"

# Vocabulario cerrado para skip_reason (evita cardinalidad alta / datos externos).
SKIP_OFERTA_EXCEPCION = "oferta_excepcion"
SKIP_OFERTA_PERSISTENCIA = "oferta_persistencia"
SKIP_FEE_EXCEPCION = "fee_excepcion"
SKIP_FEE_PERSISTENCIA = "fee_persistencia"
SKIP_ESCENARIO_EXCEPCION = "escenario_excepcion"
SKIP_ESCENARIO_PERSISTENCIA = "escenario_persistencia"
SKIP_MELI_SIN_CLIENTE = "meli_sin_cliente"

MOTIVOS_TRANSICION_NEGATIVA = frozenset(
    {
        "oferta_ausente",
        "identidad_ambigua",
        "oferta_futura",
        "oferta_desactualizada",
        "logistica_fbm_pendiente",
    }
)

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
    escenarios_nuevos: int
    escenarios_reutilizados: int


@dataclass
class _Tally:
    """Conteo de la corrida: nuevo suma fila escrita, reutilizado no."""

    ofertas_nuevas: int = 0
    ofertas_reutilizadas: int = 0
    fees_nuevos: int = 0
    fees_reutilizados: int = 0
    fees_error: int = 0
    escenarios_nuevos: int = 0
    escenarios_reutilizados: int = 0
    rows_written: int = 0

    def oferta(self, reutilizada: bool) -> None:
        if reutilizada:
            self.ofertas_reutilizadas += 1
        else:
            self.ofertas_nuevas += 1
            self.rows_written += 1

    def fee(self, reutilizada: bool) -> None:
        if reutilizada:
            self.fees_reutilizados += 1
        else:
            self.fees_nuevos += 1
            self.rows_written += 1

    def escenario(self, reutilizado: bool) -> None:
        if reutilizado:
            self.escenarios_reutilizados += 1
        else:
            self.escenarios_nuevos += 1
            self.rows_written += 1

    def negativos(self, nuevos: int, reutilizados: int, escritos: int) -> None:
        self.escenarios_nuevos += nuevos
        self.escenarios_reutilizados += reutilizados
        self.rows_written += escritos


def _formato_skip_reason(skips: Counter[str]) -> str | None:
    if not skips:
        return None
    partes = [f"{n}x {motivo}" for motivo, n in sorted(skips.items())]
    return "; ".join(partes)


def _log_excepcion_item(motivo: str, exc: BaseException) -> None:
    logger.warning("estimacion ingest item skip=%s detalle=%s", motivo, scrub(str(exc)))


def _persistir_oferta_item(
    conn: psycopg.Connection,
    *,
    oferta,
    ahora: datetime,
    run_id: int,
) -> tuple[int, bool] | None:
    """Persiste oferta; retorna (id, reutilizada) o None si falla."""
    try:
        with conn.transaction():
            persistido = persistir_oferta_observation(
                conn, oferta, observed_at=ahora, ingest_run_id=run_id
            )
        return persistido.id, persistido.reutilizada
    except Exception as exc:
        _log_excepcion_item(SKIP_OFERTA_PERSISTENCIA, exc)
        return None


def _persistir_fee_item(
    conn: psycopg.Connection,
    *,
    oferta,
    cotizacion,
    oferta_id: int,
    fee_observed_at: datetime,
    run_id: int,
) -> tuple[int, bool] | None:
    """Persiste fee; retorna (id, reutilizada) o None si falla."""
    try:
        if oferta.platform == "meli":
            canon_outcome = construir_fee_outcome_canonical_meli(
                oferta,
                cotizacion,
                attempted_at=fee_observed_at,
                canonica_entrada=construir_fee_canonical_input_meli(oferta),
            )
            evento = construir_fee_source_event_id_meli(canon_outcome)
        else:
            canon_outcome = construir_fee_outcome_canonical(
                oferta, cotizacion, attempted_at=fee_observed_at
            )
            evento = construir_fee_source_event_id(canon_outcome)
        with conn.transaction():
            fee_row = persistir_fee_observation(
                conn,
                oferta_observation_id=oferta_id,
                oferta=oferta,
                resultado=cotizacion,
                observed_at=fee_observed_at,
                source_event_id=evento,
                canonical_input=canon_outcome,
                ingest_run_id=run_id,
            )
        return fee_row.id, fee_row.reutilizada
    except Exception as exc:
        _log_excepcion_item(SKIP_FEE_PERSISTENCIA, exc)
        return None


def _persistir_escenario_item(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    oferta_id: int,
    fee_id: int,
    fee_observed_at: datetime,
) -> tuple[bool, bool] | None:
    """Sembra y persiste escenario; retorna (nuevo, reutilizado) o None si falla."""
    escenario_observed_at = fee_observed_at + timedelta(microseconds=1)
    valoracion = fee_observed_at.astimezone(UTC).date()
    try:
        with conn.transaction():
            esc = sembrar_escenario_desde_refs(
                conn,
                listing_id=listing_id,
                oferta_observation_id=oferta_id,
                fee_observation_id=fee_id,
                valoracion_date=valoracion,
                observed_at=escenario_observed_at,
            )
            esc_row = persistir_escenario(conn, esc)
        return not esc_row.reutilizada, esc_row.reutilizada
    except Exception as exc:
        _log_excepcion_item(SKIP_ESCENARIO_PERSISTENCIA, exc)
        return None


def _persistir_escenario_negativo_item(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    motivo: str,
    snapshot_huella: str,
    observed_at: datetime,
) -> tuple[bool, bool] | None:
    """Persiste escenario negativo de transicion; retorna (nuevo, reutilizado) o None."""
    valoracion = observed_at.astimezone(UTC).date()
    try:
        with conn.transaction():
            esc = sembrar_escenario_negativo_transicion(
                conn,
                listing_id=listing_id,
                motivo=motivo,
                snapshot_huella=snapshot_huella,
                valoracion_date=valoracion,
                observed_at=observed_at,
            )
            if esc is None:
                return None
            esc_row = persistir_escenario(conn, esc)
        return not esc_row.reutilizada, esc_row.reutilizada
    except Exception as exc:
        _log_excepcion_item(SKIP_ESCENARIO_PERSISTENCIA, exc)
        return None


def _contar_transicion_negativa_huella(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    motivo: str,
    huella: str,
    observed_at: datetime,
) -> tuple[int, int, int]:
    """Incrementos (nuevos, reutilizados, escritos) para un outcome negativo."""
    if motivo not in MOTIVOS_TRANSICION_NEGATIVA:
        return 0, 0, 0
    fila = _persistir_escenario_negativo_item(
        conn,
        listing_id=listing_id,
        motivo=motivo,
        snapshot_huella=huella,
        observed_at=observed_at,
    )
    if fila is None:
        return 0, 0, 0
    nuevo, reutilizado = fila
    return int(nuevo), int(reutilizado), int(nuevo)


def _huella_meli_actual(conn: psycopg.Connection, item_id: str, motivo: str) -> str:
    """Huella de la transicion negativa: ultima observacion de la publicacion."""
    fila = conn.execute(
        "SELECT id FROM meli_publicacion_observation WHERE item_id = %s"
        " ORDER BY observed_at DESC, id DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    return construir_huella_meli(item_id, fila[0] if fila else None, motivo)


def _resolver_oferta_item(conn, filas_bridge, listing_id, platform, seller_sku, asin, ahora):
    """Oferta por plataforma: `meli` desde PG, Amazon desde el bridge."""
    if platform == "meli":
        return resolver_oferta_meli(conn, listing_id, ahora=ahora)
    return resolver_oferta_para_listing(
        filas_bridge,
        listing_id=listing_id,
        platform=platform,
        seller_sku=seller_sku or "",
        asin=asin,
        now_utc=ahora,
    )


def _huella_item(conn, filas_bridge, platform, asin, motivo):
    """Huella de la transicion negativa por plataforma."""
    if platform == "meli":
        return _huella_meli_actual(conn, asin, motivo)
    return construir_huella_snapshot_listing(filas_bridge, platform=platform, asin=asin)


def _cotizar_item(oferta, *, es_meli, meli_cotizador, fees_client, reloj_fee):
    """Cotiza por plataforma (nunca levanta por error de plataforma)."""
    if es_meli:
        assert meli_cotizador is not None
        return meli_cotizador.cotizar(oferta, oferta.price_amount, ahora=reloj_fee)
    return cotizar_oferta(fees_client, oferta, now_utc=reloj_fee)


def ejecutar_ingesta(
    conn: psycopg.Connection,
    *,
    ruta_sqlite: Path | str,
    now_utc: datetime | None = None,
    client: ProductFeesClient | None = None,
    fee_now_utc: Callable[[], datetime] | None = None,
    meli_client: ClienteMeli | None = None,
) -> ResultadoIngestaEstimacion:
    """Pipeline delgado: resuelve ofertas, persiste y cotiza fees.

    Amazon resuelve del snapshot del bridge; `meli` de
    `meli_publicacion_observation` (M.1) y cotiza con `CotizadorMeli`.
    Con listings `meli` y sin `meli_client`, falla cerrado.
    """
    ahora = now_utc or datetime.now(UTC)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=UTC)
    ahora = ahora.astimezone(UTC)
    reloj_fee = fee_now_utc or (
        (lambda: ahora) if now_utc is not None else (lambda: datetime.now(UTC))
    )

    filas_bridge = leer_ofertas_bridge(ruta_sqlite)
    skips: Counter[str] = Counter()
    tally = _Tally()

    with conn.transaction():
        listings = conn.execute(
            "SELECT id, platform, external_id, seller_sku"
            " FROM listing WHERE platform = ANY(%s) ORDER BY id",
            (list(plataformas_con_cotizador()),),
        ).fetchall()

    # CR-1 (CodeRabbit): sin cliente MeLi los listings meli van a skip
    # visible y Amazon sigue (un token roto no tumba la estimacion
    # Amazon; el aviso "solo Amazon" de main() lo promete). El run se
    # abre igual: con su skip_reason se explica solo, no es huerfano.
    if meli_client is None:
        n_sin_cliente = sum(1 for _, plataforma, _, _ in listings if plataforma == "meli")
        if n_sin_cliente:
            skips[SKIP_MELI_SIN_CLIENTE] += n_sin_cliente
        listings = [fila for fila in listings if fila[1] != "meli"]

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE, ahora)).fetchone()[0]

    meli_cotizador = CotizadorMeli(meli_client) if meli_client is not None else None
    # Perezoso: sin items Amazon no se toca `ProductFeesClient` (sus
    # secretos); con `client` inyectado el comportamiento no cambia.
    fees_client: ProductFeesClient | None = client

    for listing_id, platform, asin, seller_sku in listings:
        es_meli = platform == "meli"
        if not es_meli and fees_client is None:
            try:
                fees_client = ProductFeesClient()
            except Exception:
                with conn.transaction():
                    _sellar_run(
                        conn,
                        run_id,
                        ok=False,
                        rows_written=tally.rows_written,
                        rows_skipped=sum(skips.values()),
                        skip_reason=_formato_skip_reason(skips),
                    )
                raise
        # Falla real 2026-10-16 (D.4): el carril meli lee con SELECTs
        # pelados (`resolver_oferta_meli`, `_huella_meli_actual`) que dejan
        # la conexion INTRANS; sin este bloque, cada `with
        # conn.transaction()` de persistir seria SAVEPOINT y todo el carril
        # (mas el sello del run) se revertia al cerrar. Amazon no lo sufre:
        # resuelve del snapshot del bridge sin tocar `conn`.
        try:
            with conn.transaction():
                resultado_oferta = _resolver_oferta_item(
                    conn, filas_bridge, listing_id, platform, seller_sku, asin, ahora
                )
        except Exception as exc:
            _log_excepcion_item(SKIP_OFERTA_EXCEPCION, exc)
            skips[SKIP_OFERTA_EXCEPCION] += 1
            continue

        if resultado_oferta.motivo is not None:
            try:
                with conn.transaction():
                    huella = _huella_item(
                        conn, filas_bridge, platform, asin, resultado_oferta.motivo
                    )
                    tally.negativos(
                        *_contar_transicion_negativa_huella(
                            conn,
                            listing_id=listing_id,
                            motivo=resultado_oferta.motivo,
                            huella=huella,
                            observed_at=ahora,
                        )
                    )
            except Exception as exc:
                _log_excepcion_item(SKIP_OFERTA_EXCEPCION, exc)
                skips[SKIP_OFERTA_EXCEPCION] += 1
                continue
            skips[resultado_oferta.motivo] += 1
            continue

        oferta = resultado_oferta.oferta
        assert oferta is not None

        oferta_row = _persistir_oferta_item(conn, oferta=oferta, ahora=ahora, run_id=run_id)
        if oferta_row is None:
            skips[SKIP_OFERTA_PERSISTENCIA] += 1
            continue
        oferta_id, oferta_reutilizada = oferta_row
        tally.oferta(oferta_reutilizada)

        try:
            cotizacion = _cotizar_item(
                oferta,
                es_meli=es_meli,
                meli_cotizador=meli_cotizador,
                fees_client=fees_client,
                reloj_fee=reloj_fee,
            )
        except Exception as exc:
            _log_excepcion_item(SKIP_FEE_EXCEPCION, exc)
            skips[SKIP_FEE_EXCEPCION] += 1
            continue

        fee_observed_at = cotizacion.fetched_at
        fee_row = _persistir_fee_item(
            conn,
            oferta=oferta,
            cotizacion=cotizacion,
            oferta_id=oferta_id,
            fee_observed_at=fee_observed_at,
            run_id=run_id,
        )
        if fee_row is None:
            skips[SKIP_FEE_PERSISTENCIA] += 1
            continue
        fee_id, fee_reutilizada = fee_row
        tally.fee(fee_reutilizada)
        if cotizacion.estado == "error":
            tally.fees_error += 1
            skips[cotizacion.error_code or "fee_error"] += 1

        esc_row = _persistir_escenario_item(
            conn,
            listing_id=listing_id,
            oferta_id=oferta_id,
            fee_id=fee_id,
            fee_observed_at=fee_observed_at,
        )
        if esc_row is None:
            skips[SKIP_ESCENARIO_PERSISTENCIA] += 1
            continue
        esc_nuevo, esc_reutilizado = esc_row
        tally.escenario(esc_reutilizado)

    skip_reason = _formato_skip_reason(skips)
    ok = True
    with conn.transaction():
        _sellar_run(
            conn,
            run_id,
            ok=ok,
            rows_written=tally.rows_written,
            rows_skipped=sum(skips.values()),
            skip_reason=skip_reason,
        )

    return ResultadoIngestaEstimacion(
        run_id=run_id,
        ok=ok,
        rows_written=tally.rows_written,
        rows_skipped=sum(skips.values()),
        skip_reason=skip_reason,
        ofertas_nuevas=tally.ofertas_nuevas,
        ofertas_reutilizadas=tally.ofertas_reutilizadas,
        fees_nuevos=tally.fees_nuevos,
        fees_reutilizados=tally.fees_reutilizados,
        fees_error=tally.fees_error,
        escenarios_nuevos=tally.escenarios_nuevos,
        escenarios_reutilizados=tally.escenarios_reutilizados,
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
        meli_client: ClienteMeli | None = None
        try:
            meli_client = ClienteMeli(MeliCredentials.from_secrets_dir())
        except (ReputacionError, OSError) as exc:
            print(f"aviso: sin cliente MeLi ({scrub(str(exc))}); solo Amazon", file=sys.stderr)
    except Exception as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 1

    try:
        conn = connect(dsn)
        try:
            resultado = ejecutar_ingesta(conn, ruta_sqlite=ruta, meli_client=meli_client)
        finally:
            conn.close()
            if meli_client is not None:
                meli_client.close()
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
    print(
        f"escenarios nuevos={resultado.escenarios_nuevos}"
        f" reutilizados={resultado.escenarios_reutilizados}"
    )
    if resultado.skip_reason:
        print(f"skip_reason: {resultado.skip_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
