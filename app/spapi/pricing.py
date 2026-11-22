"""Pase diario de Pricing v0 con Buy Box (SP-API 01 A.3, D1/D4/D6).

Por cada ASIN propio en `listing` (por plataforma, MX + US): dos llamadas
con `SpapiClient` — ofertas (`IsBuyBoxWinner`, `ListingPrice`, `SellerId`,
`IsFulfilledByAmazon`, E/0.2) y precio competitivo como complemento — y una
fila append-only en `spapi_price_observation` (migracion 0032), clave
`(asin, platform, observed_at)`.

El seller propio sale de Ads `/v2/profiles` (`accountInfo.id`, ID publico):
un solo merchant en todos los marketplaces (sonda 2026-09-09 + acta 0.5).
0 ofertas = fila con `offers_count=0` y precios NULL (ausencia, no error,
E/0.2). Precio sin moneda utilizable y con ofertas = fila no escrita.

Transacciones (F1): una por ASIN (solo el INSERT) mas abrir/sellar el run
en las suyas; las llamadas HTTP corren sin transaccion abierta. Un fallo
fatal conserva las filas ya escritas y sella `ok=false` con su conteo.

Fallos acotados (F2): 403/404 en un ASIN = omitido (`skip_reason`
`http_403`/`http_404`) y el pase sigue; 401 persistente y 429 agotado =
fatales; cualquier otro fallo (5xx, red, contrato) cuenta y aborta al
superar el 20 % de los ASIN vistos o 25 seguidos.

Conteos (F3): `offers_count` sale de `Summary.TotalOfferCount`,
`fba_offers_count` de `Summary.NumberOfOffers` (canal `Amazon`,
modelo oficial productPricingV0.json) y `lowest_price` de
`Summary.LowestPrices` (condicion `New`); la pagina es respaldo declarado
cuando el `Summary` falta o no trae el dato.

Un numero, una fuente (D2): el bridge sigue mandando en precio/stock de
`listing`; esto son observaciones propias. Ninguna escritura a Amazon.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

from app.redaction import install_scrub_filter
from app.spapi._ingesta import (
    AvanceEntidad as _Avance,
)
from app.spapi._ingesta import (
    abrir_run_spapi,
    dia_utc,
    ejecutar_cli_spapi,
    ejecutar_con_politica_f2,
    entero_no_negativo,
    formato_skip_reason,
    llamar_json_spapi,
    parsear_dinero_spapi,
    sellar_fallo_spapi,
    sellar_spapi,
    vigilar_umbral,
)
from app.spapi.client import (
    MERCADOS,
    VENDEDORES_PROPIOS,
    CuboTasa,
    SpapiClient,
    SpapiNoPermitida,
    construir_ruta_ofertas,
)
from app.spapi.salud import evaluar_alertas

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "spapi_pricing"
API_VERSION = "v0"
RUTA_OFERTAS_TPL = "/products/pricing/v0/items/{asin}/offers"
RUTA_COMPETITIVO = "/products/pricing/v0/competitivePrice"

# Rate limit oficial de Pricing (E/0.2: 0.5/s; burst no publicado: sin
# burst supuesto, 1 llamada cada 2 s como minimo).
PRICING_TASA_SEG = 0.5
PRICING_BURST = 1

_SQL_UNIVERSO = "SELECT DISTINCT external_id FROM listing WHERE platform = %s ORDER BY 1"
_SQL_INSERTAR = """
INSERT INTO spapi_price_observation
    (asin, platform, metric_date, observed_at,
     own_listing_price, own_listing_currency,
     buy_box_price, buy_box_currency, buy_box_seller_id, buy_box_is_own,
     offers_count, fba_offers_count,
     lowest_price, lowest_currency, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (asin, platform, observed_at) DO NOTHING
"""


class PrecioOmitido(Exception):
    """El ASIN no deja fila; lleva el motivo contable del skip."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class IngestaPricingError(Exception):
    """La corrida no pudo completarse (el sello ok=false queda cuando se puede)."""


@dataclass(frozen=True)
class PrecioParseado:
    asin: str
    own_price: Decimal | None
    own_currency: str | None
    buy_box_price: Decimal | None
    buy_box_currency: str | None
    buy_box_seller_id: str | None
    buy_box_is_own: bool | None
    offers_count: int | None
    fba_offers_count: int | None
    lowest_price: Decimal | None
    lowest_currency: str | None


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


def _dinero(obj: Any) -> tuple[Decimal | None, str | None]:
    return parsear_dinero_spapi(obj, aceptar_camel=False)


def _ofertas_de(carga: Any) -> list[dict]:
    cont = carga.get("payload", carga) if isinstance(carga, dict) else None
    if not isinstance(cont, dict):
        raise IngestaPricingError("contrato inesperado: sin lista Offers")
    if "Offers" not in cont:
        # F4: el modelo oficial no marca Offers como requerida y la sonda
        # 0.2 vio US con 0 ofertas; status Success sin la clave = cero
        # ofertas (fila con offers_count=0), no error. Solo el envoltorio
        # irreconocible (sin status Success) sigue fatal.
        if str(cont.get("status", "")).lower() == "success":
            return []
        raise IngestaPricingError("contrato inesperado: sin lista Offers")
    lote = cont["Offers"]
    if not isinstance(lote, list):
        raise IngestaPricingError("contrato inesperado: sin lista Offers")
    return [o for o in lote if isinstance(o, dict)]


def _resumen_de(cuerpo_ofertas: Any) -> dict:
    cont = (
        cuerpo_ofertas.get("payload", cuerpo_ofertas) if isinstance(cuerpo_ofertas, dict) else None
    )
    if not isinstance(cont, dict):
        return {}
    resumen = cont.get("Summary")
    return resumen if isinstance(resumen, dict) else {}


_entero_no_negativo = entero_no_negativo


def _conteo_total_resumen(resumen: dict) -> int | None:
    # F3: Summary.TotalOfferCount (requerido en el modelo oficial) cubre
    # todas las ofertas; la pagina trae un subconjunto.
    return _entero_no_negativo(resumen.get("TotalOfferCount"))


def _conteo_fba_resumen(resumen: dict) -> int | None:
    # F3: Summary.NumberOfOffers [{condition, fulfillmentChannel
    # ("Amazon"|"Merchant", enum oficial), OfferCount}]; suma el canal
    # Amazon EN CONDICION New (la ingesta pide ItemCondition=New y el
    # minimo filtra igual: las condiciones usadas/coleccionable no
    # cuentan). Lista presente = autoritativa (aunque sume 0); ausente o
    # no lista = respaldo en la pagina.
    lote = resumen.get("NumberOfOffers")
    if not isinstance(lote, list):
        return None
    total = 0
    for entrada in lote:
        if not isinstance(entrada, dict):
            continue
        if entrada.get("condition") != "New":
            continue
        if entrada.get("fulfillmentChannel") != "Amazon":
            continue
        conteo = _entero_no_negativo(entrada.get("OfferCount"))
        if conteo is not None:
            total += conteo
    return total


def _minimo_resumen(resumen: dict) -> tuple[Decimal | None, str | None]:
    # F3: Summary.LowestPrices [{condition, ListingPrice:{CurrencyCode,
    # Amount}, ...}], condicion New; sin dato utilizable = respaldo en la
    # pagina.
    lote = resumen.get("LowestPrices")
    if not isinstance(lote, list):
        return None, None
    candidatos = []
    for entrada in lote:
        if not isinstance(entrada, dict) or entrada.get("condition") != "New":
            continue
        monto, moneda = _dinero(entrada.get("ListingPrice"))
        if monto is not None:
            candidatos.append((monto, moneda))
    if not candidatos:
        return None, None
    # Sin mezcla de monedas en el min: todos los candidatos pasaron por
    # _dinero (solo MXN/USD) y un marketplace cotiza en una sola moneda.
    return min(candidatos, key=lambda par: par[0])


def _competitivas_de(carga: Any) -> list[dict]:
    # En productPricingV0, payload es una lista de Price [{status, ASIN, Product}]
    # con ruta Product.CompetitivePricing.CompetitivePrices (nunca un dict).
    cont = carga.get("payload", carga) if isinstance(carga, dict) else None
    if isinstance(cont, dict):
        raise IngestaPricingError("contrato inesperado: payload competitivo no es lista")
    if not isinstance(cont, list):
        raise IngestaPricingError("contrato inesperado: sin precios competitivos")
    entradas: list[dict] = []
    for item in cont:
        if not isinstance(item, dict):
            continue
        prod = item.get("Product")
        if not isinstance(prod, dict):
            continue
        comp = prod.get("CompetitivePricing")
        if not isinstance(comp, dict):
            continue
        lote = comp.get("CompetitivePrices")
        if isinstance(lote, list):
            entradas.extend(e for e in lote if isinstance(e, dict))
    return entradas


def _precio_propio_competitivo(entradas: list[dict]) -> tuple[Decimal | None, str | None]:
    for entrada in entradas:
        # belongsToRequester NO esta en el modelo oficial: se lee tolerante
        # (si Amazon lo manda se usa; si no, el propio solo sale de offers).
        # Pendiente: pinar las claves internas reales en sonda (E/0.2 solo
        # registro claves_top ASIN/Product/status).
        if entrada.get("belongsToRequester") is not True:
            continue
        precio = entrada.get("Price")
        if not isinstance(precio, dict):
            continue
        for clave in ("ListingPrice", "LandedPrice"):
            monto, moneda = _dinero(precio.get(clave))
            if monto is not None:
                return monto, moneda
    return None, None


def parsear_precios(
    asin: str,
    cuerpo_ofertas: Any,
    cuerpo_competitivo: Any,
    *,
    vendedor_propio: str | None,
) -> PrecioParseado:
    """Normaliza ofertas + competitivo a una observacion (E/0.2).

    0 ofertas = conteos en 0 y precios NULL (ausencia, no error). Con
    ofertas pero sin ningun precio utilizable = PrecioOmitido
    ("precio_sin_moneda"): la fila no se escribe. Conteos y minimo desde
    `Summary` (F3); la pagina es respaldo cuando el `Summary` falta.
    Sin clave `Offers` y status Success = cero ofertas (F4).
    """
    ofertas = _ofertas_de(cuerpo_ofertas)
    competitivas = _competitivas_de(cuerpo_competitivo)
    resumen = _resumen_de(cuerpo_ofertas)
    if len(ofertas) == 0:
        return PrecioParseado(
            asin=asin,
            own_price=None,
            own_currency=None,
            buy_box_price=None,
            buy_box_currency=None,
            buy_box_seller_id=None,
            buy_box_is_own=None,
            offers_count=0,
            fba_offers_count=0,
            lowest_price=None,
            lowest_currency=None,
        )
    # F3: conteos desde Summary con la pagina como respaldo declarado.
    conteo_total = _conteo_total_resumen(resumen)
    conteo = conteo_total if conteo_total is not None else len(ofertas)
    conteo_fba = _conteo_fba_resumen(resumen)
    fba = (
        conteo_fba
        if conteo_fba is not None
        else sum(1 for o in ofertas if o.get("IsFulfilledByAmazon") is True)
    )
    ganadora: dict | None = next((o for o in ofertas if o.get("IsBuyBoxWinner") is True), None)
    vendedor_ganador = (
        ganadora.get("SellerId")
        if isinstance(ganadora, dict) and isinstance(ganadora.get("SellerId"), str)
        else None
    )
    precio_ganador, moneda_ganadora = (
        _dinero(ganadora.get("ListingPrice")) if ganadora is not None else (None, None)
    )
    propia = next(
        (
            o
            for o in ofertas
            if vendedor_propio is not None and o.get("SellerId") == vendedor_propio
        ),
        None,
    )
    if propia is not None:
        precio_propio, moneda_propia = _dinero(propia.get("ListingPrice"))
    else:
        precio_propio, moneda_propia = _precio_propio_competitivo(competitivas)
    utilizables = []
    for o in ofertas:
        monto, moneda = _dinero(o.get("ListingPrice"))
        if monto is not None:
            utilizables.append((monto, moneda))
    if not utilizables:
        raise PrecioOmitido("precio_sin_moneda")
    minimo, moneda_minima = _minimo_resumen(resumen)
    if minimo is None:
        # Misma premisa que _minimo_resumen: candidatos ya filtrados por
        # _dinero (MXN/USD) y un marketplace cotiza en una sola moneda.
        minimo, moneda_minima = min(utilizables, key=lambda par: par[0])
    return PrecioParseado(
        asin=asin,
        own_price=precio_propio,
        own_currency=moneda_propia,
        buy_box_price=precio_ganador,
        buy_box_currency=moneda_ganadora,
        buy_box_seller_id=vendedor_ganador,
        buy_box_is_own=(vendedor_ganador == vendedor_propio)
        if vendedor_ganador is not None and vendedor_propio is not None
        else None,
        offers_count=conteo,
        fba_offers_count=fba,
        lowest_price=minimo,
        lowest_currency=moneda_minima,
    )


def _universo(conn: psycopg.Connection, platform: str) -> list[str]:
    filas = conn.execute(_SQL_UNIVERSO, (platform,)).fetchall()
    return [r[0] for r in filas if isinstance(r[0], str) and r[0].strip()]


def _llamar(client: SpapiClient, cubo: CuboTasa, *, path: str, params: dict, asin: str) -> Any:
    return llamar_json_spapi(
        client,
        cubo,
        path=path,
        params=params,
        fuente="pricing",
        clave=asin,
        error_cls=IngestaPricingError,
    )


def _vigilar_umbral(
    asin: str,
    fallos: int,
    racha: int,
    vistos: int,
    ultimo_status: int | None,
    *,
    total: int | None = None,
) -> None:
    vigilar_umbral(
        asin,
        fallos,
        racha,
        vistos,
        ultimo_status,
        fuente="pricing",
        error_cls=IngestaPricingError,
        total=total,
    )


def _procesar_asin(
    conn: psycopg.Connection,
    client: SpapiClient,
    cubo: CuboTasa,
    *,
    asin: str,
    marketplace_id: str,
    propio: str | None,
    platform: str,
    momento: datetime.datetime,
    run_id: int,
    av: _Avance,
) -> None:
    """Un ASIN: dos llamadas, parseo y su propia transaccion de INSERT (F1)."""
    try:
        ruta = construir_ruta_ofertas(asin)
    except SpapiNoPermitida:
        av.skips["asin_invalido"] += 1
        return
    av.vistas += 1

    def _leer() -> PrecioParseado:
        av.llamadas += 1
        cuerpo_ofertas = _llamar(
            client,
            cubo,
            path=ruta,
            params={"MarketplaceId": marketplace_id, "ItemCondition": "New"},
            asin=asin,
        )
        av.llamadas += 1
        cuerpo_competitivo = _llamar(
            client,
            cubo,
            path=RUTA_COMPETITIVO,
            params={
                "MarketplaceId": marketplace_id,
                "ItemType": "Asin",
                "Asins": asin,
            },
            asin=asin,
        )
        return parsear_precios(
            asin,
            cuerpo_ofertas,
            cuerpo_competitivo,
            vendedor_propio=propio,
        )

    ok, precio = ejecutar_con_politica_f2(
        clave=asin,
        fuente="pricing",
        error_cls=IngestaPricingError,
        omitido_cls=PrecioOmitido,
        av=av,
        obtener_y_parsear=_leer,
        vigilar=_vigilar_umbral,
    )
    if not ok:
        return
    with conn.transaction():
        cur = conn.execute(
            _SQL_INSERTAR,
            _fila_insert(
                precio,
                platform=platform,
                metric_date=dia_utc(momento),
                observed_at=momento,
                run_id=run_id,
            ),
        )
    if cur.rowcount == 1:
        av.escritas += 1
    else:
        av.skips["duplicada"] += 1
    av.racha = 0


def _fila_insert(
    precio: PrecioParseado,
    *,
    platform: str,
    metric_date: datetime.date,
    observed_at: datetime.datetime,
    run_id: int,
) -> tuple:
    return (
        precio.asin,
        platform,
        metric_date,
        observed_at,
        precio.own_price,
        precio.own_currency,
        precio.buy_box_price,
        precio.buy_box_currency,
        precio.buy_box_seller_id,
        precio.buy_box_is_own,
        precio.offers_count,
        precio.fba_offers_count,
        precio.lowest_price,
        precio.lowest_currency,
        run_id,
    )


_formato_skip_reason = formato_skip_reason
_sellar = sellar_spapi


def ejecutar_ingesta(
    conn: psycopg.Connection,
    client: SpapiClient,
    *,
    platform: str,
    ahora: datetime.datetime | None = None,
    max_asins: int | None = None,
    vendedor_propio: str | None = None,
) -> ResultadoIngesta:
    """Un pase diario por el universo y sello de su ingest_run.

    Nada se ejecuta antes del primer `with conn.transaction()` (leccion de
    listings.sync). Dos llamadas por ASIN (~2 s entre llamadas a 0.5/s).
    F1: las llamadas HTTP corren sin transaccion abierta; cada INSERT va en
    su propia transaccion y el sello en la suya. F2: politica de fallos por
    ASIN en el docstring del modulo.
    """
    if platform not in MERCADOS:
        raise IngestaPricingError(f"plataforma desconocida: {platform!r}")
    momento = ahora or datetime.datetime.now(datetime.UTC)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=datetime.UTC)
    marketplace_id = MERCADOS[platform]
    propio = vendedor_propio or VENDEDORES_PROPIOS.get(marketplace_id)
    inicio = time.monotonic()

    run_id = abrir_run_spapi(conn, SOURCE, platform)

    av = _Avance()
    try:
        with conn.transaction():
            universo = _universo(conn, platform)
        if max_asins is not None:
            if max_asins < 1:
                raise IngestaPricingError("--max-asins debe ser >= 1")
            universo = universo[:max_asins]
        av.total = len(universo)
        # Reloj y espera salen del cliente (inyectables en tests).
        cubo = CuboTasa(
            sleep=client._sleep,
            clock=client._clock,
            capacidad=PRICING_BURST,
            tasa=PRICING_TASA_SEG,
        )
        for asin in universo:
            _procesar_asin(
                conn,
                client,
                cubo,
                asin=asin,
                marketplace_id=marketplace_id,
                propio=propio,
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
            escritas=av.escritas,
            skips=av.skips,
            llamadas=av.llamadas,
        )
        raise
    segundos = time.monotonic() - inicio
    return ResultadoIngesta(
        run_id=run_id,
        ok=True,
        escritas=av.escritas,
        omitidas=sum(v for v in av.skips.values() if v > 0),
        skip_reason=_formato_skip_reason(av.skips),
        asins_vistos=av.vistas,
        llamadas=av.llamadas,
        segundos=segundos,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest spapi_pricing",
        description=(
            "Pase diario de Pricing v0 a spapi_price_observation "
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
        "--max-asins", type=int, default=None, help="tope de ASINs (por defecto: todos)"
    )
    parser.add_argument(
        "--seller-id",
        default=None,
        help="seller propio del marketplace (por defecto: el verificado en A.3)",
    )
    args = parser.parse_args(argv)
    codigo, resultado = ejecutar_cli_spapi(
        source=SOURCE,
        cliente_factory=SpapiClient,
        correr=lambda conn, client: ejecutar_ingesta(
            conn,
            client,
            platform=args.platform,
            max_asins=args.max_asins,
            vendedor_propio=args.seller_id,
        ),
    )
    if codigo != 0 or resultado is None:
        return codigo
    print(
        f"spapi_pricing {args.platform}: run={resultado.run_id} "
        f"escritas={resultado.escritas} omitidas={resultado.omitidas} "
        f"asins={resultado.asins_vistos} llamadas={resultado.llamadas} "
        f"segundos={resultado.segundos:.1f}"
    )
    return 0
