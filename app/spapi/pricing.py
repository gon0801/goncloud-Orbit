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
fatales; cualquier otro fallo (5xx, contrato) cuenta y aborta al superar
el 20 % de los ASIN vistos o 25 seguidos.

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
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from app.redaction import install_scrub_filter, scrub
from app.spapi.client import (
    MERCADOS,
    CuboTasa,
    SpapiClient,
    SpapiNoPermitida,
    construir_ruta_ofertas,
    sanear,
)

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "spapi_pricing"
API_VERSION = "v0"
RUTA_OFERTAS_TPL = "/products/pricing/v0/items/{asin}/offers"
RUTA_COMPETITIVO = "/products/pricing/v0/competitivePrice"

# Seller propio por marketplace (IDs publicos, no secretos): un solo
# merchant en MX/US/CA verificado el 2026-09-09 con Ads GET /v2/profiles
# (accountInfo.id, type seller; acta 0.5 cito el de MX).
VENDEDORES_PROPIOS = {
    "A1AM78C64UM0Y8": "A29XRL07YRN0L",
    "ATVPDKIKX0DER": "A29XRL07YRN0L",
}

# Rate limit oficial de Pricing (E/0.2: 0.5/s; burst no publicado: sin
# burst supuesto, 1 llamada cada 2 s como minimo).
PRICING_TASA_SEG = 0.5
PRICING_BURST = 1

MONEDAS = frozenset({"MXN", "USD"})
_MAX_DINERO = Decimal(10) ** 10
_MAX_DECIMALES = Decimal("0.0001")

# F2: un ASIN malo no mata el pase, pero un muro si. 401 persistente y 429
# agotado (el cliente ya reintento una vez) son fatales de inmediato; 5xx,
# red y contrato cuentan y abortan al superar el 20 % de los ASIN vistos
# (falla rapido contra un muro total) o 25 seguidos (muro tardio diluido
# entre exitos). 403/404 son ausencia esperada: omiten sin contar.
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
       ok = %s
 WHERE id = %s
"""
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


class _FalloHttp(Exception):
    """Una llamada volvio status != 200; el pase decide por codigo (F2)."""

    def __init__(self, asin: str, status: int) -> None:
        super().__init__(f"pricing {asin} status={status}")
        self.asin = asin
        self.status = status


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
    # Money de Amazon con CurrencyCode siempre presente; sin moneda
    # utilizable (ausente, fuera de MXN/USD, monto ilegitimo) = (None, None).
    if not isinstance(obj, dict):
        return None, None
    moneda = obj.get("CurrencyCode")
    if not isinstance(moneda, str) or moneda.strip() not in MONEDAS:
        return None, None
    try:
        monto = Decimal(str(obj.get("Amount")))
    except (InvalidOperation, ValueError, TypeError):
        return None, None
    if not monto.is_finite() or abs(monto) >= _MAX_DINERO:
        return None, None
    try:
        monto = monto.quantize(_MAX_DECIMALES)
    except InvalidOperation:
        return None, None
    return monto, moneda.strip()


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


def _entero_no_negativo(valor: Any) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return None
    return valor


def _conteo_total_resumen(resumen: dict) -> int | None:
    # F3: Summary.TotalOfferCount (requerido en el modelo oficial) cubre
    # todas las ofertas; la pagina trae un subconjunto.
    return _entero_no_negativo(resumen.get("TotalOfferCount"))


def _conteo_fba_resumen(resumen: dict) -> int | None:
    # F3: Summary.NumberOfOffers [{condition, fulfillmentChannel
    # ("Amazon"|"Merchant", enum oficial), OfferCount}]; suma el canal
    # Amazon. Lista presente = autoritativa (aunque sume 0); ausente o no
    # lista = respaldo en la pagina.
    lote = resumen.get("NumberOfOffers")
    if not isinstance(lote, list):
        return None
    total = 0
    for entrada in lote:
        if not isinstance(entrada, dict):
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
    return min(candidatos, key=lambda par: par[0])


def _competitivas_de(carga: Any) -> list[dict]:
    cont = carga.get("payload", carga) if isinstance(carga, dict) else None
    if not isinstance(cont, dict):
        raise IngestaPricingError("contrato inesperado: sin precios competitivos")
    prod = cont.get("Product", cont)
    if not isinstance(prod, dict):
        raise IngestaPricingError("contrato inesperado: sin precios competitivos")
    for clave in ("CompetitivePrices", "competitivePrices"):
        lote = prod.get(clave)
        if isinstance(lote, list):
            return [e for e in lote if isinstance(e, dict)]
    return []


def _precio_propio_competitivo(entradas: list[dict]) -> tuple[Decimal | None, str | None]:
    for entrada in entradas:
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
    cubo.consumir()
    resp = client.get(path, params=params)
    if resp.status_code != 200:
        raise _FalloHttp(asin, resp.status_code)
    try:
        return sanear(resp.json(), "pricing")
    except ValueError:
        raise IngestaPricingError(f"pricing {asin} respuesta no JSON") from None


def _vigilar_umbral(asin: str, fallos: int, racha: int, vistos: int) -> None:
    if fallos > UMBRAL_FALLOS_PORC * vistos or racha >= UMBRAL_FALLOS_RACHA:
        raise IngestaPricingError(
            f"pricing {asin}: umbral de fallos superado ({fallos}/{vistos}, racha {racha})"
        )


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


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


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

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    escritas = 0
    skips: Counter = Counter()
    vistas = 0
    fallos = 0
    racha = 0
    llamadas = 0
    try:
        with conn.transaction():
            universo = _universo(conn, platform)
        if max_asins is not None:
            if max_asins < 1:
                raise IngestaPricingError("--max-asins debe ser >= 1")
            universo = universo[:max_asins]
        # Reloj y espera salen del cliente (inyectables en tests).
        cubo = CuboTasa(
            sleep=client._sleep,
            clock=client._clock,
            capacidad=PRICING_BURST,
            tasa=PRICING_TASA_SEG,
        )
        for asin in universo:
            try:
                ruta = construir_ruta_ofertas(asin)
            except SpapiNoPermitida:
                skips["asin_invalido"] += 1
                continue
            vistas += 1
            try:
                # Llamadas cuenta intentos (la tasa se gasto aunque falle).
                llamadas += 1
                cuerpo_ofertas = _llamar(
                    client,
                    cubo,
                    path=ruta,
                    params={"MarketplaceId": marketplace_id, "ItemCondition": "New"},
                    asin=asin,
                )
                llamadas += 1
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
                precio = parsear_precios(
                    asin,
                    cuerpo_ofertas,
                    cuerpo_competitivo,
                    vendedor_propio=propio,
                )
            except PrecioOmitido as exc:
                skips[exc.motivo] += 1
                racha = 0
                continue
            except _FalloHttp as exc:
                if exc.status in _HTTP_OMITIDO:
                    skips[f"http_{exc.status}"] += 1
                    racha = 0
                    continue
                if exc.status in _HTTP_FATAL:
                    raise IngestaPricingError(
                        f"pricing {asin} status={exc.status} persistente"
                    ) from exc
                fallos += 1
                racha += 1
                skips[f"http_{exc.status}"] += 1
                _vigilar_umbral(asin, fallos, racha, vistas)
                continue
            except IngestaPricingError:
                # Contrato o respuesta no JSON: cuenta contra el umbral
                # (un cambio de forma tumba el pase en vez de
                # escribir 342 filas vacias) sin matar el pase al
                # primer ASIN raro.
                fallos += 1
                racha += 1
                skips["contrato"] += 1
                _vigilar_umbral(asin, fallos, racha, vistas)
                continue
            with conn.transaction():
                cur = conn.execute(
                    _SQL_INSERTAR,
                    _fila_insert(
                        precio,
                        platform=platform,
                        metric_date=momento.date(),
                        observed_at=momento,
                        run_id=run_id,
                    ),
                )
            if cur.rowcount == 1:
                escritas += 1
            else:
                skips["duplicada"] += 1
            racha = 0
        motivo = _formato_skip_reason(Counter({k: v for k, v in skips.items() if v > 0}))
        with conn.transaction():
            _sellar(conn, run_id, ok=True, escritas=escritas, skips=skips, motivo=motivo)
    except BaseException as exc:
        try:
            with conn.transaction():
                _sellar(
                    conn,
                    run_id,
                    ok=False,
                    escritas=escritas,
                    skips=skips,
                    motivo=scrub(str(exc)) or type(exc).__name__,
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
        escritas=escritas,
        omitidas=sum(v for v in skips.values() if v > 0),
        skip_reason=_formato_skip_reason(Counter({k: v for k, v in skips.items() if v > 0})),
        asins_vistos=vistas,
        llamadas=llamadas,
        segundos=segundos,
    )


def _sellar(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    escritas: int,
    skips: Counter,
    motivo: str | None,
) -> None:
    conn.execute(
        _SQL_SELLAR_RUN,
        (escritas, sum(skips.values()), motivo, ok, run_id),
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
                max_asins=args.max_asins,
                vendedor_propio=args.seller_id,
            )
        finally:
            conn.close()
    except IngestaPricingError as exc:
        print(f"ingesta spapi_pricing fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ingesta spapi_pricing fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    print(
        f"spapi_pricing {args.platform}: run={resultado.run_id} "
        f"escritas={resultado.escritas} omitidas={resultado.omitidas} "
        f"asins={resultado.asins_vistos} llamadas={resultado.llamadas} "
        f"segundos={resultado.segundos:.1f}"
    )
    return 0
