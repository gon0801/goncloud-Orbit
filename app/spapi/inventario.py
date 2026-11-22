"""Recorrido completo del Inventario FBA v1 (SP-API 01 A.4, D1/D4/D6).

GET /fba/inventory/v1/summaries con granularityType=Marketplace (E/0.4),
paginado con `nextToken` HERMANO de `payload` (correccion ronda 2 del acta
0.4; `siguiente_token` ya lo ve). Cada summary deja una fila append-only
en `spapi_inventario_observation` (migracion 0035), clave
`(seller_sku, platform, observed_at)`.

Guardas de paginacion hermanas de orders (token repetido, pagina vacia
con token, tope de paginas): un aviso sella el run ok=false conservando
lo escrito (patron A.2b `paginacion_incompleta:<aviso>`).

total_quantity es el dato de la tabla: ausente o ilegitimo = fila no
escrita (skip "sin_cantidad"). fulfillable_quantity solo viene con
details=true (no lo pedimos): NULL en la practica.

Un numero, una fuente (D2): el bridge sigue mandando en stock de
`listing`; esto es solo inventario observado. Ninguna escritura a Amazon.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

import psycopg

from app.redaction import install_scrub_filter
from app.spapi._ingesta import (
    abrir_run_spapi,
    dia_utc,
    ejecutar_cli_spapi,
    entero_no_negativo,
    formato_skip_reason,
    recorrer_paginas_spapi,
    sellar_con_aviso_paginacion,
    sellar_fallo_spapi,
    sellar_spapi,
    texto_limpio,
)
from app.spapi.client import (
    MERCADOS,
    RUTA_INVENTARIO,
    CuboTasa,
    SpapiClient,
)
from app.spapi.salud import evaluar_alertas

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "spapi_inventario"
API_VERSION = "v1"
MAX_PAGINAS_DEFAULT = 100

# Rate limit oficial de Inventario (E/0.4: x-amzn-ratelimit-limit 2.0/s).
INVENTARIO_TASA_SEG = 2.0
INVENTARIO_BURST = 2

_SQL_INSERTAR = """
INSERT INTO spapi_inventario_observation
    (seller_sku, asin, fn_sku, platform, metric_date, observed_at,
     total_quantity, fulfillable_quantity, api_version, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (seller_sku, platform, observed_at) DO NOTHING
"""


class InventarioOmitido(Exception):
    """El summary no deja fila; lleva el motivo contable del skip."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class IngestaInventarioError(Exception):
    """La corrida no pudo completarse (el sello ok=false queda cuando se puede)."""


@dataclass(frozen=True)
class InventarioParseado:
    seller_sku: str
    asin: str | None
    fn_sku: str | None
    total_quantity: int
    fulfillable_quantity: int | None


@dataclass(frozen=True)
class ResultadoIngesta:
    run_id: int
    ok: bool
    escritas: int
    omitidas: int
    skip_reason: str | None
    paginas: int
    resenas_vistas: int
    llamadas: int
    aviso_paginacion: str | None
    segundos: float


_texto = texto_limpio
_dia_utc = dia_utc
_cantidad = entero_no_negativo


def parsear_summary(obj: Any) -> InventarioParseado:
    """Normaliza un inventorySummary (claves del acta 0.4 + modelo oficial).

    Sin sellerSku = sin identidad: omit ("sin_sku"). Sin totalQuantity
    entero >= 0 = sin dato: omit ("sin_cantidad", regla 3).
    """
    if not isinstance(obj, dict):
        raise InventarioOmitido("sin_sku")
    sku = _texto(obj.get("sellerSku"))
    if sku is None:
        raise InventarioOmitido("sin_sku")
    total = _cantidad(obj.get("totalQuantity"))
    if total is None:
        raise InventarioOmitido("sin_cantidad")
    detalles = obj.get("inventoryDetails")
    fulfillable = None
    if isinstance(detalles, dict):
        fulfillable = _cantidad(detalles.get("fulfillableQuantity"))
    return InventarioParseado(
        seller_sku=sku,
        asin=_texto(obj.get("asin")),
        fn_sku=_texto(obj.get("fnSku")),
        total_quantity=total,
        fulfillable_quantity=fulfillable,
    )


def _resenas_de(carga: Any) -> list[dict]:
    # inventorySummaries bajo payload (acta 0.4); pagination es HERMANA
    # (la lee siguiente_token, no aqui).
    cont = carga.get("payload", carga) if isinstance(carga, dict) else None
    if not isinstance(cont, dict) or "inventorySummaries" not in cont:
        raise IngestaInventarioError("contrato inesperado: sin inventorySummaries")
    lote = cont["inventorySummaries"]
    if not isinstance(lote, list):
        raise IngestaInventarioError("contrato inesperado: sin inventorySummaries")
    return [r for r in lote if isinstance(r, dict)]


def recorrer_summaries(
    client: SpapiClient,
    params_base: dict,
    *,
    max_paginas: int = MAX_PAGINAS_DEFAULT,
    cubo: CuboTasa | None = None,
    medidor: Counter | None = None,
) -> tuple[list[dict], dict]:
    """Recorre todas las paginas con las guardas compartidas de `app.spapi._ingesta`."""
    return recorrer_paginas_spapi(
        client,
        ruta=RUTA_INVENTARIO,
        params_base=params_base,
        param_token="nextToken",
        fuente="inventario",
        error_cls=IngestaInventarioError,
        extraer_lote=_resenas_de,
        capacidad=INVENTARIO_BURST,
        tasa=INVENTARIO_TASA_SEG,
        max_paginas=max_paginas,
        cubo=cubo,
        medidor=medidor,
    )


def _fila_insert(
    inv: InventarioParseado,
    *,
    platform: str,
    metric_date: datetime.date,
    observed_at: datetime.datetime,
    run_id: int,
) -> tuple:
    return (
        inv.seller_sku,
        inv.asin,
        inv.fn_sku,
        platform,
        metric_date,
        observed_at,
        inv.total_quantity,
        inv.fulfillable_quantity,
        API_VERSION,
        run_id,
    )


_sellar = sellar_spapi
_formato_skip_reason = formato_skip_reason


def ejecutar_ingesta(
    conn: psycopg.Connection,
    client: SpapiClient,
    *,
    platform: str,
    ahora: datetime.datetime | None = None,
    max_paginas: int = MAX_PAGINAS_DEFAULT,
) -> ResultadoIngesta:
    """Recorrido completo de inventario y sello de su ingest_run.

    Nada se ejecuta antes del primer `with conn.transaction()`. Aviso de
    paginacion = ok=false conservando lo escrito (patron A.2b).
    """
    if platform not in MERCADOS:
        raise IngestaInventarioError(f"plataforma desconocida: {platform!r}")
    momento = ahora or datetime.datetime.now(datetime.UTC)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=datetime.UTC)
    marketplace_id = MERCADOS[platform]
    inicio = time.monotonic()

    run_id = abrir_run_spapi(conn, SOURCE, platform)

    escritas = 0
    skips: Counter = Counter()
    medidor: Counter = Counter()
    try:
        params = {
            "granularityType": "Marketplace",
            "granularityId": marketplace_id,
            "marketplaceIds": marketplace_id,
        }
        crudas, info = recorrer_summaries(client, params, max_paginas=max_paginas, medidor=medidor)
        paginas = info["paginas"]
        vistas = info["conteo"]
        aviso = info["aviso_paginacion"]
        llamadas = info["llamadas"]
        with conn.transaction():
            for cruda in crudas:
                try:
                    inv = parsear_summary(cruda)
                except InventarioOmitido as exc:
                    skips[exc.motivo] += 1
                    continue
                cur = conn.execute(
                    _SQL_INSERTAR,
                    _fila_insert(
                        inv,
                        platform=platform,
                        metric_date=_dia_utc(momento),
                        observed_at=momento,
                        run_id=run_id,
                    ),
                )
                if cur.rowcount == 1:
                    escritas += 1
                else:
                    skips["duplicada"] += 1
            sellar_con_aviso_paginacion(
                conn,
                run_id,
                aviso=aviso,
                escritas=escritas,
                skips=skips,
                llamadas=llamadas,
            )
        # A.5: alertas en flanco, FUERA de la transaccion del sello
        # (fail-silent: jamas rompe la ingesta).
        evaluar_alertas(conn, SOURCE, platform, run_id)
    except BaseException as exc:
        sellar_fallo_spapi(
            conn,
            run_id,
            exc,
            source=SOURCE,
            platform=platform,
            escritas=0,
            llamadas=medidor["llamadas"],
        )
        raise
    segundos = time.monotonic() - inicio
    return ResultadoIngesta(
        run_id=run_id,
        ok=aviso is None,
        escritas=escritas,
        omitidas=sum(v for v in skips.values() if v > 0),
        skip_reason=_formato_skip_reason(Counter({k: v for k, v in skips.items() if v > 0})),
        paginas=paginas,
        resenas_vistas=vistas,
        llamadas=llamadas,
        aviso_paginacion=aviso,
        segundos=segundos,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest spapi_inventario",
        description=(
            "Recorrido completo de Inventario FBA v1 a "
            "spapi_inventario_observation (runbook: docs/DEPLOY.md). "
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
        "--max-paginas", type=int, default=MAX_PAGINAS_DEFAULT, help="tope de paginas"
    )
    args = parser.parse_args(argv)
    codigo, resultado = ejecutar_cli_spapi(
        source=SOURCE,
        cliente_factory=SpapiClient,
        correr=lambda conn, client: ejecutar_ingesta(
            conn,
            client,
            platform=args.platform,
            max_paginas=args.max_paginas,
        ),
    )
    if codigo != 0 or resultado is None:
        return codigo
    print(
        f"spapi_inventario {args.platform}: run={resultado.run_id} "
        f"escritas={resultado.escritas} omitidas={resultado.omitidas} "
        f"paginas={resultado.paginas} aviso={resultado.aviso_paginacion} "
        f"llamadas={resultado.llamadas} segundos={resultado.segundos:.1f}"
    )
    if not resultado.ok:
        print(
            f"ingesta spapi_inventario incompleta: {resultado.aviso_paginacion}",
            file=sys.stderr,
        )
        return 1
    return 0
