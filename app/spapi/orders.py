"""Ingesta diaria de Orders 2026-01-01 (SP-API 01 A.2, D1/D4/D6).

Lee `GET /orders/2026-01-01/orders` con `SpapiClient` (un solo refrescador
LWA) para MX o US y guarda resumenes append-only en
`spapi_order_observation` (migraciones 0030/0031). Clave bitemporal:
`(platform, amazon_order_id, last_updated_time, observed_at)` (E/0.1 mas
plataforma y re-observacion, patron 0026/0027): la ventana con solape
vuelve a observar los pedidos recientes y la vista `v_spapi_order_ultima`
muestra la ultima. Correcciones dentro de la misma corrida (mismo
`observed_at`) siguen absorbidas por `ON CONFLICT DO NOTHING` y cuentan
como skip, como manda `ingest_run`.

Ventana: `lastUpdatedAfter` = maximo `last_updated_time` observado menos
1 dia de solape; primera corrida: `createdAfter` = ahora menos 30 dias.
Paginacion con las guardas de TRASPASO-1 (token repetido y pagina vacia
con token paran y se marcan). Se pide `includedData=FULFILLMENT,PROCEEDS`
(A.2b, verificadas sin permiso especial en el acta A.2b/sonda.md) y jamas
BUYER ni RECIPIENT: cero PII, y la tabla no tiene columnas de comprador
ni direccion.

Dinero `(valor, moneda)` NUMERIC(14,4) + enum (regla 4); total incoherente
o ausente = NULLs (regla 3), la fila se escribe igual porque la identidad
es orden + tiempo. Tiempo fuente incoherente (purchase > updated) = fila
no escrita con motivo. Ninguna escritura a Amazon ni a `listing` (D2).
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

from app.ingest_run import formato_skip_reason
from app.redaction import install_scrub_filter
from app.spapi._ingesta import (
    abrir_run_spapi,
    ejecutar_cli_spapi,
    parsear_dinero_spapi,
    recorrer_paginas_spapi,
    sellar_con_aviso_paginacion,
    sellar_fallo_spapi,
    sellar_spapi,
    texto_limpio,
)
from app.spapi.client import (
    MERCADOS,
    CuboTasa,
    SpapiClient,
)
from app.spapi.salud import evaluar_alertas

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "spapi_orders"
API_VERSION = "2026-01-01"
RUTA_ORDERS = "/orders/2026-01-01/orders"
RESULTADOS_POR_PAGINA = "100"
MAX_PAGINAS_DEFAULT = 100
DIAS_INICIAL_DEFAULT = 30
SOLAPE_DIAS = 1

# Rate limit oficial de Orders (E/0.1: 0.0056 req/s, burst 20). El cubo
# vive en app.spapi.client (una copia, A.3).
ORDERS_TASA_SEG = 0.0056
ORDERS_BURST = 20

_SQL_MAX_OBSERVADO = """
SELECT max(last_updated_time) FROM spapi_order_observation WHERE platform = %s
"""
_SQL_INSERTAR = """
INSERT INTO spapi_order_observation
    (amazon_order_id, platform, marketplace_id, purchase_date,
     last_updated_time, order_status, fulfillment_status, fulfillment_channel,
     sales_channel, order_total_amount, order_total_currency, number_of_items,
     api_version, observed_at, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (platform, amazon_order_id, last_updated_time, observed_at) DO NOTHING
"""


class OrdenOmitida(Exception):
    """La orden no se escribe; lleva el motivo contable del skip."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class IngestaOrdersError(Exception):
    """La corrida no pudo completarse (el sello ok=false queda cuando se puede)."""


@dataclass(frozen=True)
class OrdenParseada:
    amazon_order_id: str
    purchase_date: datetime.datetime | None
    last_updated_time: datetime.datetime
    order_status: str | None
    fulfillment_status: str | None
    fulfillment_channel: str | None
    sales_channel: str | None
    total_amount: Decimal | None
    total_currency: str | None
    number_of_items: int | None


@dataclass(frozen=True)
class ResultadoIngesta:
    run_id: int
    ok: bool
    escritas: int
    omitidas: int
    skip_reason: str | None
    paginas: int
    ordenes_vistas: int
    aviso_paginacion: str | None


def _zulu(momento: datetime.datetime) -> str:
    # v0 exige Zulu (400 con +00:00, E/0.1); vale para ambas versiones.
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=datetime.UTC)
    return momento.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parsear_tiempo(valor: Any, *, campo: str) -> datetime.datetime | None:
    if valor is None:
        return None
    if not isinstance(valor, str) or not valor.strip():
        raise OrdenOmitida("tiempo_ilegible")
    texto = valor.strip()
    iso = texto[:-1] + "+00:00" if texto.endswith("Z") else texto
    try:
        momento = datetime.datetime.fromisoformat(iso)
    except ValueError:
        raise OrdenOmitida("tiempo_ilegible") from None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=datetime.UTC)
    return momento.astimezone(datetime.UTC)


def _parsear_dinero(obj: Any) -> tuple[Decimal | None, str | None]:
    return parsear_dinero_spapi(obj, aceptar_camel=True)


def _parsear_items(valor: Any) -> int | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if valor >= 0 else None
    if isinstance(valor, list):
        return len(valor)
    if isinstance(valor, dict):
        for clave in ("count", "Count", "numberOfItems", "NumberOfItems"):
            conteo = valor.get(clave)
            if isinstance(conteo, int) and not isinstance(conteo, bool) and conteo >= 0:
                return conteo
    return None


_texto = texto_limpio


def parsear_orden(obj: Any) -> OrdenParseada:
    """Normaliza un resumen searchOrders (tolerante a alias v0).

    Exige identidad (orderId + lastUpdatedTime) y tiempo coherente; lo
    demas ausente es NULL. Sin PII: ignora comprador, recipiente y
    direccion aunque vengan. Dos estados, dos columnas (revision PR #240):
    `order_status` SOLO del ciclo del pedido (orderStatus plano) y
    `fulfillment_status` SOLO del envio (fulfillment.fulfillmentStatus);
    nunca se mezclan aunque vengan los dos.
    """
    if not isinstance(obj, dict):
        raise OrdenOmitida("sin_identidad")
    amazon_order_id = _texto(obj.get("orderId", obj.get("AmazonOrderId")))
    if amazon_order_id is None:
        raise OrdenOmitida("sin_identidad")
    crudo_updated = obj.get("lastUpdatedTime", obj.get("LastUpdateDate"))
    if crudo_updated is None:
        raise OrdenOmitida("sin_identidad")
    last_updated = _parsear_tiempo(crudo_updated, campo="lastUpdatedTime")
    assert last_updated is not None
    purchase = None
    crudo_purchase = obj.get("createdTime", obj.get("PurchaseDate"))
    if crudo_purchase is not None:
        try:
            purchase = _parsear_tiempo(crudo_purchase, campo="createdTime")
        except OrdenOmitida:
            purchase = None
    if purchase is not None and purchase > last_updated:
        raise OrdenOmitida("tiempo_incoherente")
    # Secciones FULFILLMENT + PROCEEDS (A.2b, acta A.2b/sonda.md) con
    # respaldo a las claves planas del resumen pelado; ausente = NULL.
    ful = obj.get("fulfillment")
    ful = ful if isinstance(ful, dict) else {}
    pro = obj.get("proceeds")
    pro = pro if isinstance(pro, dict) else {}
    gran_total = pro.get("grandTotal")
    if not isinstance(gran_total, dict):
        gran_total = obj.get("orderTotal", obj.get("OrderTotal"))
    monto, moneda = _parsear_dinero(gran_total)
    return OrdenParseada(
        amazon_order_id=amazon_order_id,
        purchase_date=purchase,
        last_updated_time=last_updated,
        order_status=_texto(obj.get("orderStatus", obj.get("OrderStatus"))),
        fulfillment_status=_texto(ful.get("fulfillmentStatus")),
        fulfillment_channel=_texto(ful.get("fulfilledBy"))
        or _texto(obj.get("fulfillmentChannel", obj.get("FulfillmentChannel"))),
        sales_channel=_texto(obj.get("salesChannel", obj.get("SalesChannel"))),
        total_amount=monto,
        total_currency=moneda,
        number_of_items=_parsear_items(obj.get("orderItems")),
    )


def parametros_ventana(
    *,
    marketplace_id: str,
    ultimo_observado: datetime.datetime | None,
    ahora: datetime.datetime,
    dias_inicial: int = DIAS_INICIAL_DEFAULT,
    desde: datetime.date | None = None,
) -> dict:
    """Params del searchOrders: solape de 1 dia o ventana inicial de 30.

    `desde` (backfill A.2b, --desde YYYY-MM-DD) fuerza `lastUpdatedAfter`
    a esa fecha ignorando el maximo observado: completa observaciones de
    pedidos viejos sin tocar filas (la clave bitemporal inserta, no pisa).
    """
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=datetime.UTC)
    base = {
        "marketplaceIds": marketplace_id,
        "maxResultsPerPage": RESULTADOS_POR_PAGINA,
        # Secciones con estado y total (A.2b); jamas BUYER ni RECIPIENT.
        "includedData": "FULFILLMENT,PROCEEDS",
    }
    if desde is not None:
        base["lastUpdatedAfter"] = _zulu(
            datetime.datetime(desde.year, desde.month, desde.day, tzinfo=datetime.UTC)
        )
    elif ultimo_observado is None:
        if dias_inicial < 1:
            raise IngestaOrdersError("--dias-inicial debe ser >= 1")
        base["createdAfter"] = _zulu(ahora - datetime.timedelta(days=dias_inicial))
    else:
        if ultimo_observado.tzinfo is None:
            ultimo_observado = ultimo_observado.replace(tzinfo=datetime.UTC)
        base["lastUpdatedAfter"] = _zulu(ultimo_observado - datetime.timedelta(days=SOLAPE_DIAS))
    return base


def _extraer_ordenes(carga: Any) -> list[dict]:
    if not isinstance(carga, dict):
        raise IngestaOrdersError("contrato inesperado: sin lista orders")
    cont = carga.get("payload", carga)
    if not isinstance(cont, dict) or "orders" not in cont:
        raise IngestaOrdersError("contrato inesperado: sin lista orders")
    lote = cont["orders"]
    if not isinstance(lote, list):
        raise IngestaOrdersError("contrato inesperado: sin lista orders")
    return [o for o in lote if isinstance(o, dict)]


def recorrer_ordenes(
    client: SpapiClient,
    params_base: dict,
    *,
    max_paginas: int = MAX_PAGINAS_DEFAULT,
    cubo: CuboTasa | None = None,
) -> tuple[list[dict], dict]:
    """Sigue `paginationToken` con las guardas compartidas de `app.spapi._ingesta`."""
    ordenes, info = recorrer_paginas_spapi(
        client,
        ruta=RUTA_ORDERS,
        params_base=params_base,
        param_token="paginationToken",
        fuente="orders",
        error_cls=IngestaOrdersError,
        extraer_lote=_extraer_ordenes,
        capacidad=ORDERS_BURST,
        tasa=ORDERS_TASA_SEG,
        max_paginas=max_paginas,
        cubo=cubo,
    )
    return ordenes, {
        "paginas": info["paginas"],
        "conteo": info["conteo"],
        "aviso_paginacion": info["aviso_paginacion"],
    }


def _fila_insert(
    orden: OrdenParseada,
    *,
    platform: str,
    marketplace_id: str,
    observed_at: datetime.datetime,
    run_id: int,
) -> tuple:
    return (
        orden.amazon_order_id,
        platform,
        marketplace_id,
        orden.purchase_date,
        orden.last_updated_time,
        orden.order_status,
        orden.fulfillment_status,
        orden.fulfillment_channel,
        orden.sales_channel,
        orden.total_amount,
        orden.total_currency,
        orden.number_of_items,
        API_VERSION,
        observed_at,
        run_id,
    )


def _formato_skip_reason(skips: Counter) -> str | None:
    return formato_skip_reason(skips)


def ejecutar_ingesta(
    conn: psycopg.Connection,
    client: SpapiClient,
    *,
    platform: str,
    ahora: datetime.datetime | None = None,
    max_paginas: int = MAX_PAGINAS_DEFAULT,
    dias_inicial: int = DIAS_INICIAL_DEFAULT,
    desde: datetime.date | None = None,
) -> ResultadoIngesta:
    """Corre la ingesta diaria y sella su ingest_run (patron listings.sync).

    Nada se ejecuta antes del primer `with conn.transaction()` (leccion de
    listings.sync: un SELECT suelto abre transaccion implicita que el close
    revierte).
    """
    if platform not in MERCADOS:
        raise IngestaOrdersError(f"plataforma desconocida: {platform!r}")
    momento = ahora or datetime.datetime.now(datetime.UTC)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=datetime.UTC)
    marketplace_id = MERCADOS[platform]

    run_id = abrir_run_spapi(conn, SOURCE, platform)

    escritas = 0
    skips: Counter = Counter()
    paginas = 0
    vistas = 0
    aviso: str | None = None
    try:
        with conn.transaction():
            ultimo = conn.execute(_SQL_MAX_OBSERVADO, (platform,)).fetchone()[0]
            params = parametros_ventana(
                marketplace_id=marketplace_id,
                ultimo_observado=ultimo,
                ahora=momento,
                dias_inicial=dias_inicial,
                desde=desde,
            )
        crudas, info = recorrer_ordenes(client, params, max_paginas=max_paginas)
        paginas = info["paginas"]
        vistas = info["conteo"]
        aviso = info["aviso_paginacion"]
        with conn.transaction():
            for cruda in crudas:
                try:
                    orden = parsear_orden(cruda)
                except OrdenOmitida as exc:
                    skips[exc.motivo] += 1
                    continue
                cur = conn.execute(
                    _SQL_INSERTAR,
                    _fila_insert(
                        orden,
                        platform=platform,
                        marketplace_id=marketplace_id,
                        observed_at=momento,
                        run_id=run_id,
                    ),
                )
                if cur.rowcount == 1:
                    escritas += 1
                else:
                    skips["duplicada"] += 1
            # Paginacion incompleta no es exito: las filas parciales ya se
            # escriben y el siguiente watermark avanza sobre el hueco, asi que
            # ok=False deja rastro en ingest_run para reparar con --desde.
            sellar_con_aviso_paginacion(
                conn,
                run_id,
                aviso=aviso,
                escritas=escritas,
                skips=skips,
            )
        # A.5: alertas en flanco, FUERA de la transaccion del sello
        # (fail-silent: jamas rompe la ingesta).
        evaluar_alertas(conn, SOURCE, platform, run_id)
    except BaseException as exc:
        sellar_fallo_spapi(conn, run_id, exc, source=SOURCE, platform=platform)
        raise
    return ResultadoIngesta(
        run_id=run_id,
        ok=aviso is None,
        escritas=escritas,
        omitidas=sum(v for v in skips.values() if v > 0),
        skip_reason=formato_skip_reason(skips),
        paginas=paginas,
        ordenes_vistas=vistas,
        aviso_paginacion=aviso,
    )


_sellar = sellar_spapi


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest spapi_orders",
        description=(
            "Ingiere resumenes de Orders 2026-01-01 a spapi_order_observation "
            "(runbook: docs/DEPLOY.md). Solo lectura contra Amazon."
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
    parser.add_argument(
        "--dias-inicial",
        type=int,
        default=DIAS_INICIAL_DEFAULT,
        help="ventana createdAfter de la primera corrida",
    )
    parser.add_argument(
        "--desde",
        type=datetime.date.fromisoformat,
        default=None,
        help="backfill A.2b (YYYY-MM-DD): fuerza lastUpdatedAfter a esa fecha",
    )
    args = parser.parse_args(argv)
    codigo, resultado = ejecutar_cli_spapi(
        source=SOURCE,
        correr=lambda conn, client: ejecutar_ingesta(
            conn,
            client,
            platform=args.platform,
            max_paginas=args.max_paginas,
            dias_inicial=args.dias_inicial,
            desde=args.desde,
        ),
    )
    if codigo != 0 or resultado is None:
        return codigo
    print(
        f"spapi_orders {args.platform}: run={resultado.run_id} "
        f"escritas={resultado.escritas} omitidas={resultado.omitidas} "
        f"paginas={resultado.paginas} aviso={resultado.aviso_paginacion}"
    )
    if not resultado.ok:
        print(
            f"ingesta spapi_orders incompleta: {resultado.aviso_paginacion} (reparar con --desde)",
            file=sys.stderr,
        )
        return 1
    return 0
