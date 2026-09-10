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
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

import psycopg

from app.redaction import install_scrub_filter, scrub
from app.spapi.client import (
    MERCADOS,
    RUTA_INVENTARIO,
    CuboTasa,
    SpapiClient,
    prefijo_motivo,
    sanear,
    siguiente_token,
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

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source, platform) VALUES (%s, %s) RETURNING id"
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


def _texto(valor: Any) -> str | None:
    if isinstance(valor, str) and valor.strip():
        return valor.strip()
    return None


def _dia_utc(momento: datetime.datetime) -> datetime.date:
    # Hallazgo 3 grok (baja): metric_date es el dia UTC de observed_at
    # (lo que exige el trigger), no el dia de pared del tzinfo que traiga
    # `ahora`. En produccion `ahora` es UTC y calza; esto blinda el resto.
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=datetime.UTC)
    return momento.astimezone(datetime.UTC).date()


def _cantidad(valor: Any) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return None
    return valor


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
    """Recorre todas las paginas con las guardas hermanas de orders.

    Devuelve (resenas crudas acumuladas, info con paginas/conteo/aviso).
    Un status != 200 es fatal; pagina vacia o token repetido paran y se
    marcan, nunca loop. Con `medidor`, cada intento HTTP suma
    `medidor["llamadas"]` (hallazgo 2 grok: si el recorrido muere, el
    sello igual declara las llamadas que si ocurrieron).
    """
    if max_paginas < 1:
        raise IngestaInventarioError("--max-paginas debe ser >= 1")
    # Reloj y espera salen del cliente (inyectables en tests).
    if cubo is None:
        cubo = CuboTasa(
            sleep=client._sleep,
            clock=client._clock,
            capacidad=INVENTARIO_BURST,
            tasa=INVENTARIO_TASA_SEG,
        )
    resenas: list[dict] = []
    vistos: set[str] = set()
    token: str | None = None
    aviso: str | None = None
    siguiente: str | None = None
    paginas = 0
    llamadas = 0
    for pagina in range(1, max_paginas + 1):
        params = dict(params_base)
        if token is not None:
            params["nextToken"] = token
        # El cubo viaja DENTRO del get (patron A.3 revision #5).
        resp = client.get(RUTA_INVENTARIO, params=params, limitador=cubo)
        llamadas += 1
        if medidor is not None:
            medidor["llamadas"] += 1
        if resp.status_code != 200:
            raise IngestaInventarioError(f"inventario status={resp.status_code}")
        try:
            carga = resp.json()
        except ValueError:
            raise IngestaInventarioError("inventario respuesta no JSON") from None
        carga = sanear(carga, "inventario")
        lote = _resenas_de(carga)
        paginas = pagina
        resenas.extend(lote)
        siguiente = siguiente_token(carga)
        if siguiente is None:
            break
        if siguiente in vistos:
            aviso = "next_token_repetido"
            break
        if not lote:
            aviso = "pagina_vacia_con_token"
            break
        vistos.add(siguiente)
        token = siguiente
    else:
        if siguiente is not None:
            aviso = "limite_max_paginas"
    return resenas, {
        "paginas": paginas,
        "conteo": len(resenas),
        "aviso_paginacion": aviso,
        "llamadas": llamadas,
    }


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


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


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

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE, platform)).fetchone()[0]

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
            motivo = _formato_skip_reason(Counter({k: v for k, v in skips.items() if v > 0}))
            if aviso is not None:
                # Hallazgo 1 grok: como orders, el aviso no descarta el
                # detalle de skips ya calculado.
                motivo_aviso = f"paginacion_incompleta:{aviso}"
                if motivo:
                    motivo_aviso = f"{motivo_aviso}; {motivo}"
                _sellar(
                    conn,
                    run_id,
                    ok=False,
                    escritas=escritas,
                    skips=skips,
                    motivo=motivo_aviso,
                    llamadas=llamadas,
                )
            else:
                _sellar(
                    conn,
                    run_id,
                    ok=True,
                    escritas=escritas,
                    skips=skips,
                    motivo=motivo,
                    llamadas=llamadas,
                )
        # A.5: alertas en flanco, FUERA de la transaccion del sello
        # (fail-silent: jamas rompe la ingesta).
        evaluar_alertas(conn, SOURCE, platform)
    except BaseException as exc:
        # Hallazgo 2 grok: INSERTs y sello van en UNA transaccion; si
        # revienta, Postgres deshace las filas y el contador Python
        # mentiria — se sella 0 (como orders). Las llamadas si ocurrieron:
        # las declara el medidor, no un 0 fijo.
        try:
            with conn.transaction():
                _sellar(
                    conn,
                    run_id,
                    ok=False,
                    escritas=0,
                    skips=Counter(),
                    motivo=f"{prefijo_motivo(exc)}: {scrub(str(exc)) or type(exc).__name__}",
                    llamadas=medidor["llamadas"],
                )
            evaluar_alertas(conn, SOURCE, platform)
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
                max_paginas=args.max_paginas,
            )
        finally:
            conn.close()
    except IngestaInventarioError as exc:
        print(f"ingesta spapi_inventario fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ingesta spapi_inventario fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
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
