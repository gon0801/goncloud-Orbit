"""Pase diario de Listings Items 2021-08-01 (SP-API 01 A.4, D1/D4/D6).

Por cada seller_sku propio en `listing` (por plataforma, MX + US): una
llamada con `SpapiClient` al item (`sku` + `summaries`, E/0.3) y una fila
append-only en `spapi_listing_estado_observation` (migracion 0035), clave
`(seller_sku, platform, observed_at)`.

El estado sale de summaries[0] del marketplace pedido (si hay varios, el
que calza con el marketplaceId; sin summary = fila no escrita). `status`
se guarda TEXT tal cual (lista oficial OPEN/CLOSED/etc., sin enum
inventado).

Transacciones y fallos (patron pricing F1/F2): una transaccion por SKU,
error por SKU (404/403/5xx) = skip contado y la corrida sigue; racha de
fallos aborta con el mismo criterio de _vigilar_umbral.

Un numero, una fuente (D2): el bridge sigue mandando en precio/stock de
`listing`; esto es solo estado del listing. Ninguna escritura a Amazon.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import psycopg

from app.redaction import install_scrub_filter, scrub
from app.spapi.client import (
    MERCADOS,
    VENDEDORES_PROPIOS,
    CuboTasa,
    SpapiClient,
    SpapiNoPermitida,
    construir_ruta_listings,
    sanear,
)

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "spapi_listings"
API_VERSION = "2021-08-01"

# Rate limit oficial de Listings (E/0.3: x-amzn-ratelimit-limit 5.0).
LISTINGS_TASA_SEG = 5.0
LISTINGS_BURST = 5

# Politica de fallos por SKU (mismo criterio que pricing F2).
UMBRAL_FALLOS_PORC = 0.20
UMBRAL_FALLOS_RACHA = 25
_HTTP_OMITIDO = frozenset({403, 404})
_HTTP_FATAL = frozenset({401, 429})

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id"
_SQL_SELLAR_RUN = """
UPDATE ingest_run
   SET finished_at = now(),
       rows_written = %s,
       rows_skipped = %s,
       skip_reason = %s,
       ok = %s,
       llamadas = %s
 WHERE id = %s
"""
_SQL_UNIVERSO = (
    "SELECT DISTINCT seller_sku FROM listing"
    " WHERE platform = %s AND seller_sku IS NOT NULL AND btrim(seller_sku) <> ''"
    " ORDER BY 1"
)
_SQL_INSERTAR = """
INSERT INTO spapi_listing_estado_observation
    (seller_sku, asin, platform, status, product_type,
     last_updated_date, api_version, observed_at, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (seller_sku, platform, observed_at) DO NOTHING
"""


class EstadoOmitido(Exception):
    """El SKU no deja fila; lleva el motivo contable del skip."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class IngestaListingsError(Exception):
    """La corrida no pudo completarse (el sello ok=false queda cuando se puede)."""


class _FalloHttp(Exception):
    """Una llamada volvio status != 200; el pase decide por codigo."""

    def __init__(self, sku: str, status: int) -> None:
        super().__init__(f"listings {sku} status={status}")
        self.sku = sku
        self.status = status


@dataclass(frozen=True)
class EstadoParseado:
    seller_sku: str
    asin: str | None
    status: str | None
    product_type: str | None
    last_updated_date: datetime.datetime | None


@dataclass(frozen=True)
class ResultadoIngesta:
    run_id: int
    ok: bool
    escritas: int
    omitidas: int
    skip_reason: str | None
    asins_vistos: int
    llamadas: int
    segundos: float


@dataclass
class _Avance:
    """Contadores mutables del pase (el helper por SKU los actualiza)."""

    escritas: int = 0
    vistas: int = 0
    fallos: int = 0
    racha: int = 0
    llamadas: int = 0
    skips: Counter = field(default_factory=Counter)


def _texto(valor: Any) -> str | None:
    if isinstance(valor, str) and valor.strip():
        return valor.strip()
    return None


def _parsear_tiempo(valor: Any) -> datetime.datetime | None:
    # lastUpdatedDate ISO (E/0.3 no pino el formato exacto; se acepta lo que
    # fromisoformat entiende, con Zulu). Ilegible = NULL (regla 3), no omit.
    if not isinstance(valor, str) or not valor.strip():
        return None
    texto = valor.strip()
    iso = texto[:-1] + "+00:00" if texto.endswith("Z") else texto
    try:
        momento = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=datetime.UTC)
    return momento.astimezone(datetime.UTC)


def parsear_estado(sku: str, cuerpo: Any, *, marketplace_id: str) -> EstadoParseado:
    """Normaliza el item de Listings al estado del marketplace pedido (E/0.3).

    Sin summaries, o sin summary de ese marketplace = EstadoOmitido
    ("sin_summary"): la fila no se escribe.
    """
    cont = cuerpo if isinstance(cuerpo, dict) else None
    if cont is None:
        raise IngestaListingsError("contrato inesperado: sin summaries")
    lote = cont.get("summaries")
    if lote is None:
        raise EstadoOmitido("sin_summary")
    if not isinstance(lote, list):
        raise IngestaListingsError("contrato inesperado: sin summaries")
    resumen = next(
        (s for s in lote if isinstance(s, dict) and s.get("marketplaceId") == marketplace_id),
        None,
    )
    if resumen is None:
        raise EstadoOmitido("sin_summary")
    return EstadoParseado(
        seller_sku=sku,
        asin=_texto(resumen.get("asin")),
        status=_texto(resumen.get("status")),
        product_type=_texto(resumen.get("productType")),
        last_updated_date=_parsear_tiempo(resumen.get("lastUpdatedDate")),
    )


def _universo(conn: psycopg.Connection, platform: str) -> list[str]:
    filas = conn.execute(_SQL_UNIVERSO, (platform,)).fetchall()
    return [r[0] for r in filas if isinstance(r[0], str) and r[0].strip()]


def _llamar(client: SpapiClient, cubo: CuboTasa, *, path: str, params: dict, sku: str) -> Any:
    # El cubo viaja DENTRO del get: cada intento consume cuota y honra
    # x-amzn-RateLimit-Limit (patron A.3 revision #5).
    resp = client.get(path, params=params, limitador=cubo)
    if resp.status_code != 200:
        raise _FalloHttp(sku, resp.status_code)
    try:
        return sanear(resp.json(), "listings")
    except ValueError:
        raise IngestaListingsError(f"listings {sku} respuesta no JSON") from None


def _vigilar_umbral(sku: str, fallos: int, racha: int, vistos: int) -> None:
    if fallos > UMBRAL_FALLOS_PORC * vistos or racha >= UMBRAL_FALLOS_RACHA:
        raise IngestaListingsError(
            f"listings {sku}: umbral de fallos superado ({fallos}/{vistos}, racha {racha})"
        )


def _fila_insert(
    estado: EstadoParseado,
    *,
    platform: str,
    observed_at: datetime.datetime,
    run_id: int,
) -> tuple:
    return (
        estado.seller_sku,
        estado.asin,
        platform,
        estado.status,
        estado.product_type,
        estado.last_updated_date,
        API_VERSION,
        observed_at,
        run_id,
    )


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


def _procesar_sku(
    conn: psycopg.Connection,
    client: SpapiClient,
    cubo: CuboTasa,
    *,
    sku: str,
    seller_id: str,
    marketplace_id: str,
    platform: str,
    momento: datetime.datetime,
    run_id: int,
    av: _Avance,
) -> None:
    """Un SKU: una llamada, parseo y su propia transaccion de INSERT."""
    try:
        ruta = construir_ruta_listings(seller_id, sku)
    except SpapiNoPermitida:
        av.skips["sku_invalido"] += 1
        return
    av.vistas += 1
    try:
        av.llamadas += 1
        cuerpo = _llamar(
            client,
            cubo,
            path=ruta,
            params={"marketplaceIds": marketplace_id},
            sku=sku,
        )
        estado = parsear_estado(sku, cuerpo, marketplace_id=marketplace_id)
    except EstadoOmitido as exc:
        av.skips[exc.motivo] += 1
        av.racha = 0
        return
    except _FalloHttp as exc:
        if exc.status in _HTTP_OMITIDO:
            av.skips[f"http_{exc.status}"] += 1
            av.racha = 0
            return
        if exc.status in _HTTP_FATAL:
            raise IngestaListingsError(f"listings {sku} status={exc.status} persistente") from exc
        av.fallos += 1
        av.racha += 1
        av.skips[f"http_{exc.status}"] += 1
        _vigilar_umbral(sku, av.fallos, av.racha, av.vistas)
        return
    except IngestaListingsError:
        av.fallos += 1
        av.racha += 1
        av.skips["contrato"] += 1
        _vigilar_umbral(sku, av.fallos, av.racha, av.vistas)
        return
    with conn.transaction():
        cur = conn.execute(
            _SQL_INSERTAR,
            _fila_insert(estado, platform=platform, observed_at=momento, run_id=run_id),
        )
    if cur.rowcount == 1:
        av.escritas += 1
    else:
        av.skips["duplicada"] += 1
    av.racha = 0


def _sellar(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    escritas: int,
    skips: Counter,
    motivo: str | None,
    llamadas: int,
) -> None:
    conn.execute(
        _SQL_SELLAR_RUN,
        (escritas, sum(skips.values()), motivo, ok, llamadas, run_id),
    )


def ejecutar_ingesta(
    conn: psycopg.Connection,
    client: SpapiClient,
    *,
    platform: str,
    ahora: datetime.datetime | None = None,
    max_skus: int | None = None,
    seller_id: str | None = None,
) -> ResultadoIngesta:
    """Un pase diario por el universo y sello de su ingest_run.

    Nada se ejecuta antes del primer `with conn.transaction()`. Una
    llamada por SKU (~5/s); HTTP sin transaccion abierta, INSERT por SKU
    en la suya, sello en la suya.
    """
    if platform not in MERCADOS:
        raise IngestaListingsError(f"plataforma desconocida: {platform!r}")
    momento = ahora or datetime.datetime.now(datetime.UTC)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=datetime.UTC)
    marketplace_id = MERCADOS[platform]
    propio = seller_id or VENDEDORES_PROPIOS.get(marketplace_id)
    if propio is None:
        raise IngestaListingsError(f"sin sellerId para {platform}")
    inicio = time.monotonic()

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    av = _Avance()
    try:
        with conn.transaction():
            universo = _universo(conn, platform)
        if max_skus is not None:
            if max_skus < 1:
                raise IngestaListingsError("--max-skus debe ser >= 1")
            universo = universo[:max_skus]
        # Reloj y espera salen del cliente (inyectables en tests).
        cubo = CuboTasa(
            sleep=client._sleep,
            clock=client._clock,
            capacidad=LISTINGS_BURST,
            tasa=LISTINGS_TASA_SEG,
        )
        for sku in universo:
            _procesar_sku(
                conn,
                client,
                cubo,
                sku=sku,
                seller_id=propio,
                marketplace_id=marketplace_id,
                platform=platform,
                momento=momento,
                run_id=run_id,
                av=av,
            )
        motivo = _formato_skip_reason(Counter({k: v for k, v in av.skips.items() if v > 0}))
        with conn.transaction():
            _sellar(
                conn,
                run_id,
                ok=True,
                escritas=av.escritas,
                skips=av.skips,
                motivo=motivo,
                llamadas=av.llamadas,
            )
    except BaseException as exc:
        try:
            with conn.transaction():
                _sellar(
                    conn,
                    run_id,
                    ok=False,
                    escritas=av.escritas,
                    skips=av.skips,
                    motivo=scrub(str(exc)) or type(exc).__name__,
                    llamadas=av.llamadas,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello; error: %s",
                run_id,
                scrub(str(exc)),
            )
        raise
    segundos = time.monotonic() - inicio
    return ResultadoIngesta(
        run_id=run_id,
        ok=True,
        escritas=av.escritas,
        omitidas=sum(v for v in av.skips.values() if v > 0),
        skip_reason=_formato_skip_reason(Counter({k: v for k, v in av.skips.items() if v > 0})),
        asins_vistos=av.vistas,
        llamadas=av.llamadas,
        segundos=segundos,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest spapi_listings",
        description=(
            "Pase diario de Listings Items 2021-08-01 a "
            "spapi_listing_estado_observation (runbook: docs/DEPLOY.md). "
            "Solo lectura contra Amazon."
        ),
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=sorted(MERCADOS),
        help="mercado a ingerir (MX o US)",
    )
    parser.add_argument(
        "--max-skus", type=int, default=None, help="tope de SKUs (por defecto: todos)"
    )
    parser.add_argument(
        "--seller-id",
        default=None,
        help="seller propio del marketplace (por defecto: el verificado en A.3)",
    )
    args = parser.parse_args(argv)
    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir (fail-closed)",
            file=sys.stderr,
        )
        return 2
    try:
        from app.db import connect

        conn = connect(dsn)
        try:
            resultado = ejecutar_ingesta(
                conn,
                SpapiClient(),
                platform=args.platform,
                max_skus=args.max_skus,
                seller_id=args.seller_id,
            )
        finally:
            conn.close()
    except IngestaListingsError as exc:
        print(f"ingesta spapi_listings fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ingesta spapi_listings fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    print(
        f"spapi_listings {args.platform}: run={resultado.run_id} "
        f"escritas={resultado.escritas} omitidas={resultado.omitidas} "
        f"skus={resultado.asins_vistos} llamadas={resultado.llamadas} "
        f"segundos={resultado.segundos:.1f}"
    )
    return 0
