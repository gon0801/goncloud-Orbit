"""Tests de la ingesta Ads por producto anunciado (ORBIT 19 B.1,
`spAdvertisedProduct` -> ads_product_metric_observation, migracion 0020).

(a) UNITARIOS: body del reporte (patron de tests/test_reports_pipeline.py,
    formas verificadas en vivo 2026-09-06, docs/evidencia/orbit-19/0.3) y la
    planificacion pura `_planea_filas_productos`: grano (asin, sku, fecha)
    agregado por SUMA, sin reparto de agregados de campana, columna faltante
    -> NULL (regla 3) y gates con vocabulario CERRADO (regla 9).
(b) ESTATICO (pglast): la migracion 0020 parsea y sella apend-only + dedupe.
(c) INTEGRACION: aplican 0001+0020 en un Postgres temporal y corren el
    orquestador completo contra el mock: corrida 1, dedupe por
    source_report_id, bitemporalidad (reporte NUEVO del mismo dia = fila
    nueva), moneda por perfil (trigger), US no verificado (400) sin filas
    inventadas y cero escritura a ads_metric_observation. Skip automatico sin
    Postgres utilizable (misma condicion que test_schema).

Regla 9: los skips se AFIRMAN con su motivo, no solo "que no reviente".
"""

from __future__ import annotations

import datetime as dt
import gzip
import inspect
import json
import os
import socket
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

import httpx
import pglast
import pytest
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.reports import (
    PRODUCTOS_CFG,
    AdsReportsError,
    _FilaProducto,
    _planea_filas_productos,
    solicitar_reporte,
    sync_metrics,
)
from app.ads.structure import PerfilAds

ROOT = Path(__file__).resolve().parents[1]
SQL20 = (ROOT / "migrations" / "0020_ads_producto_metrica.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

FAKE_CLIENT_ID = "fake-client-id-123"
FAKE_CLIENT_SECRET = "fake-client-secret-XYZ"
FAKE_REFRESH_TOKEN = "fake-refresh-token-ABC"

PERFILES_API = [
    {
        "profileId": 101,
        "countryCode": "US",
        "currencyCode": "USD",
        "accountInfo": {"id": "1", "type": "seller", "name": "Goncloud US"},
    },
    {
        "profileId": 202,
        "countryCode": "MX",
        "currencyCode": "MXN",
        "accountInfo": {"id": "2", "type": "seller", "name": "Goncloud MX"},
    },
]


def _cliente(handler) -> AdsClient:
    return AdsClient(
        AdsCredentials(
            client_id=FAKE_CLIENT_ID,
            client_secret=FAKE_CLIENT_SECRET,
            refresh_token=FAKE_REFRESH_TOKEN,
        ),
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _perfil(profile_id: int, country: str) -> PerfilAds:
    platform, moneda = ("amazon_us", "USD") if country == "US" else ("amazon_mx", "MXN")
    return PerfilAds(
        profile_id=profile_id,
        country=country,
        currency_code=moneda,
        account_type="seller",
        valid_payment_method=True,
        account_name="Cuenta Test",
        aceptado=True,
        platform=platform,
        moneda=moneda,
    )


def _token(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-access", "expires_in": 3600})


# ---------------------------------------------------------------------------
# (a) UNITARIOS - body del reporte
# ---------------------------------------------------------------------------


def test_body_reporte_productos():
    """ORBIT 19 B.1: reportTypeId spAdvertisedProduct, groupBy ["advertiser"]
    (forma verificada en vivo 2026-09-06), columns STRINGS con el contrato
    minimo 30d de la politica 0.4 §1, SIN filters y SIN salesSameSku30d (400
    vivo documentado en docs/evidencia/orbit-19/0.3/columnas-api-400.txt)."""
    registro: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.method == "POST" and request.url.path == "/reporting/reports":
            registro.append(json.loads(request.content))
            return httpx.Response(200, json={"reportId": "rep-prod", "status": "PENDING"})
        raise AssertionError(f"llamada inesperada: {request.method} {request.url}")

    client = _cliente(handler)
    assert (
        solicitar_reporte(
            client, _perfil(202, "MX"), PRODUCTOS_CFG, dt.date(2026, 9, 3), dt.date(2026, 9, 3)
        )
        == "rep-prod"
    )

    assert len(registro) == 1
    cuerpo = registro[0]
    assert cuerpo["startDate"] == "2026-09-03"
    assert cuerpo["endDate"] == "2026-09-03"
    cfg = cuerpo["configuration"]
    assert cfg["adProduct"] == "SPONSORED_PRODUCTS"
    assert cfg["reportTypeId"] == "spAdvertisedProduct"
    assert cfg["groupBy"] == ["advertiser"]  # ARRAY, no string
    assert cfg["timeUnit"] == "DAILY"
    assert cfg["format"] == "GZIP_JSON"
    assert cfg["columns"] == [
        "date",
        "advertisedAsin",
        "advertisedSku",
        "campaignId",
        "adGroupId",
        "adId",
        "impressions",
        "clicks",
        "cost",
        "purchases30d",
        "sales30d",
        "purchasesSameSku30d",
        "attributedSalesSameSku30d",
    ]
    assert all(isinstance(columna, str) for columna in cfg["columns"])  # STRINGS
    assert "filters" not in cfg
    # PROHIBIDO pedir salesSameSku30d: 400 vivo
    assert "salesSameSku30d" not in cfg["columns"]


# ---------------------------------------------------------------------------
# (a) UNITARIOS - planificacion pura (grano, no-reparto, gates; regla 9)
# ---------------------------------------------------------------------------


def test_plan_productos_campana_multiproducto_no_reparte_agregados():
    """DoD B.1: dos ASIN en la MISMA campana conservan CADA UNO sus metricas
    observadas; la ingesta NUNCA reparte un agregado de campana entre
    productos (politica 0.4 §2). El planificador ni siquiera conoce el total
    de campana: A queda con SU cost 3 y B con SU cost 7, no 5 y 5."""
    filas = [
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0AAAAAAA1",
            "advertisedSku": "SKU-A",
            "campaignId": 111,
            "adGroupId": 1111,
            "adId": 11,
            "impressions": 100,
            "clicks": 5,
            "cost": 3.0,
            "purchases30d": 1,
            "sales30d": 50.0,
            "purchasesSameSku30d": 1,
            "attributedSalesSameSku30d": 40.0,
        },
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0BBBBBBB2",
            "advertisedSku": "SKU-B",
            "campaignId": 111,  # MISMA campana: multiproducto
            "adGroupId": 1112,
            "adId": 12,
            "impressions": 50,
            "clicks": 2,
            "cost": 7.0,
            "purchases30d": 0,
            "sales30d": 0,
            "purchasesSameSku30d": 0,
            "attributedSalesSameSku30d": 0,
        },
    ]

    plan, skips = _planea_filas_productos(
        filas, hoy=dt.date(2026, 9, 4), fecha_ini=dt.date(2026, 9, 3), fecha_fin=dt.date(2026, 9, 3)
    )

    assert skips == Counter()
    assert len(plan) == 2  # una fila por (asin, sku): el grano sellado
    a = next(f for f in plan if f.asin == "B0AAAAAAA1")
    b = next(f for f in plan if f.asin == "B0BBBBBBB2")
    assert isinstance(a, _FilaProducto)
    assert (a.cost, a.clicks, a.sales30d) == (Decimal("3.0"), 5, Decimal("50.0"))
    assert (b.cost, b.clicks, b.sales30d) == (Decimal("7.0"), 2, Decimal("0"))
    # cada producto cita SOLO la campana que la API le atribuyo
    assert a.campaign_ids == ["111"] and b.campaign_ids == ["111"]
    assert a.ad_ids == ["11"] and b.ad_ids == ["12"]


def test_plan_productos_clave_envenenada_no_resucita():
    """Hallazgo cross-review codex 2026-09-07: una clave cuya fusion queda
    100% vacia se descarta; una fila POSTERIOR del mismo (asin, sku, fecha)
    no puede recrearla con solo sus valores (publicaria un subtotal parcial
    como completo, dependiendo del orden del gzip). El veneno es adhesivo."""
    base = {"date": "2026-09-03", "advertisedAsin": "B0CCCCCCC1", "advertisedSku": "SKU-C"}
    filas = [
        {
            **base,
            "campaignId": 111,
            "adGroupId": 1111,
            "adId": 11,
            "impressions": None,
            "clicks": None,
            "cost": 10,
            "purchases30d": None,
            "sales30d": None,
            "purchasesSameSku30d": None,
            "attributedSalesSameSku30d": None,
        },
        {
            **base,
            "campaignId": 222,
            "adGroupId": 2222,
            "adId": 22,
            "impressions": 3,
            "clicks": None,
            "cost": None,
            "purchases30d": None,
            "sales30d": None,
            "purchasesSameSku30d": None,
            "attributedSalesSameSku30d": None,
        },
        # tercera fila con datos: NO resucita la clave envenenada
        {
            **base,
            "campaignId": 333,
            "adGroupId": 3333,
            "adId": 33,
            "impressions": 7,
            "clicks": 1,
            "cost": 4,
            "purchases30d": 0,
            "sales30d": 9,
            "purchasesSameSku30d": 0,
            "attributedSalesSameSku30d": 9,
        },
    ]
    plan, skips = _planea_filas_productos(
        filas, hoy=dt.date(2026, 9, 4), fecha_ini=dt.date(2026, 9, 3), fecha_fin=dt.date(2026, 9, 3)
    )
    assert plan == []
    assert skips["fila sin ninguna metrica"] == 1
    assert skips["fila de clave envenenada (subtotal parcial)"] == 1


def test_plan_productos_fila_invalida_envenena_la_clave():
    """Hallazgo cross-review codex 2026-09-07: una fila con metrica no
    numerica (o sin NINGUNA metrica) ENVENENA la clave (asin, sku, fecha):
    si otra campana del mismo producto trae datos validos, el subtotal
    parcial NO se publica como completo. Ambos ordenes del gzip dan igual."""
    hoy = dt.date(2026, 9, 4)
    rango = dict(hoy=hoy, fecha_ini=dt.date(2026, 9, 3), fecha_fin=dt.date(2026, 9, 3))
    valida = {
        "date": "2026-09-03",
        "advertisedAsin": "B0EEEEEEE1",
        "advertisedSku": "SKU-E",
        "campaignId": 111,
        "adGroupId": 1111,
        "adId": 11,
        "cost": 5,
    }
    # (1) metrica no numerica en otra campana del MISMO (asin, sku, fecha)
    no_numerica = {**valida, "campaignId": 222, "adGroupId": 2222, "adId": 22, "cost": "abc"}
    for filas in ([valida, no_numerica], [no_numerica, valida]):
        plan, skips = _planea_filas_productos(filas, **rango)
        assert plan == [], f"subtotal parcial publicado: {plan}"
        assert skips["fila de productos con metrica no numerica o fraccionaria"] == 1
    # invalida primero: la fila valida posterior topa contra la clave envenenada
    _, skips = _planea_filas_productos([no_numerica, valida], **rango)
    assert skips["fila de clave envenenada (subtotal parcial)"] == 1
    # (2) fila sin NINGUNA metrica en otra campana del MISMO (asin, sku, fecha)
    sin_metricas = {k: v for k, v in valida.items() if k not in ("cost",)}
    sin_metricas.update({"campaignId": 222, "adGroupId": 2222, "adId": 22})
    for filas in ([valida, sin_metricas], [sin_metricas, valida]):
        plan, skips = _planea_filas_productos(filas, **rango)
        assert plan == [], f"subtotal parcial publicado: {plan}"
        assert skips["fila sin ninguna metrica"] == 1
    _, skips = _planea_filas_productos([sin_metricas, valida], **rango)
    assert skips["fila de clave envenenada (subtotal parcial)"] == 1


def test_plan_productos_same_sku_mayor_que_total_envenena_la_clave():
    """Hallazgo cross-review 2026-09-07 (2a ronda): una fila con ventas
    promovidas MAYORES que las totales se descarta por el pre-check del
    CHECK apm_same_sku_cabe; sin envenenar la clave, otra campana valida
    del MISMO (asin, sku, fecha) publicaria su subtotal como completo y
    podria etiquetar 'Gasto sin ventas' con ventas reales desconocidas."""
    hoy = dt.date(2026, 9, 4)
    rango = dict(hoy=hoy, fecha_ini=dt.date(2026, 9, 3), fecha_fin=dt.date(2026, 9, 3))
    valida = {
        "date": "2026-09-03",
        "advertisedAsin": "B0EEEEEEE1",
        "advertisedSku": "SKU-E",
        "campaignId": 111,
        "adGroupId": 1111,
        "adId": 11,
        "cost": 5,
        "sales30d": 0.0,
    }
    corrupta = {
        **valida,
        "campaignId": 222,
        "adGroupId": 2222,
        "adId": 22,
        "sales30d": 100.0,
        "attributedSalesSameSku30d": 150.0,  # promovidas > totales
    }
    for filas in ([valida, corrupta], [corrupta, valida]):
        plan, skips = _planea_filas_productos(filas, **rango)
        assert plan == [], f"subtotal parcial publicado: {plan}"
        assert skips["fila con metrica same_sku mayor que el total"] == 1
    # corrupta primero: la fila valida posterior topa contra la clave envenenada
    _, skips = _planea_filas_productos([corrupta, valida], **rango)
    assert skips["fila de clave envenenada (subtotal parcial)"] == 1


@pytest.mark.parametrize(
    "metricas_invalidas",
    [
        {},
        {"cost": "abc"},
        {"sales30d": 10, "attributedSalesSameSku30d": 11},
        {"purchases30d": 1, "purchasesSameSku30d": 2},
    ],
    ids=["sin_metricas", "no_numerica", "ventas_inconsistentes", "compras_inconsistentes"],
)
@pytest.mark.parametrize("orden", ["valida_primero", "invalida_primero", "fusion_y_reintentos"])
def test_descartes_de_clave_invalida_cuentan_todas_las_filas(metricas_invalidas, orden):
    """Cada fila cruda se cuenta una vez, incluso al retirar una fusion previa."""
    identidad = {
        "date": "2026-09-03",
        "advertisedAsin": "B0EEEEEEE1",
        "advertisedSku": "SKU-E",
    }
    valida = {**identidad, "cost": 5, "sales30d": 0}
    invalida = {**identidad, **metricas_invalidas}
    secuencias = {
        "valida_primero": [valida, invalida],
        "invalida_primero": [invalida, valida],
        "fusion_y_reintentos": [valida, valida, invalida, valida, invalida],
    }
    independiente = {**valida, "advertisedSku": "SKU-INDEPENDIENTE"}
    filas = [*secuencias[orden], independiente]
    plan, skips = _planea_filas_productos(
        filas, hoy=dt.date(2026, 9, 4), fecha_ini=dt.date(2026, 9, 3), fecha_fin=dt.date(2026, 9, 3)
    )
    assert [fila.sku for fila in plan] == ["SKU-INDEPENDIENTE"]
    assert len(plan) + sum(skips.values()) == len(filas)


def test_plan_productos_dos_campanas_del_mismo_asin_suman():
    """DoD B.1: filas del MISMO asin/sku en campanas DISTINTAS se SUMAN hacia
    el grano (platform, asin, sku, fecha); la metrica que un aporte trajo
    ausente queda envenenada a None (regla 3: la suma con sumando desconocido
    es desconocida, jamas 0)."""
    filas = [
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0AAAAAAA1",
            "advertisedSku": "SKU-A",
            "campaignId": 111,
            "adGroupId": 1111,
            "adId": 11,
            "impressions": 100,
            "clicks": 5,
            "cost": 3.0,
            "purchases30d": 1,
            "sales30d": 50.0,
            "purchasesSameSku30d": 1,
            "attributedSalesSameSku30d": 40.0,
        },
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0AAAAAAA1",
            "advertisedSku": "SKU-A",
            "campaignId": 222,  # otra campana anuncia el MISMO asin/sku
            "adGroupId": 2222,
            "adId": 22,
            "impressions": 10,
            "clicks": 1,
            "cost": 2.0,
            # attributedSalesSameSku30d AUSENTE -> la fusionada queda None
        },
    ]

    plan, skips = _planea_filas_productos(
        filas, hoy=dt.date(2026, 9, 4), fecha_ini=dt.date(2026, 9, 3), fecha_fin=dt.date(2026, 9, 3)
    )

    assert len(plan) == 1
    fusionada = plan[0]
    assert (fusionada.cost, fusionada.clicks, fusionada.impressions) == (
        Decimal("5.0"),
        6,
        110,
    )
    # envenenamiento: TODO lo que el aporte de la campana 222 no trajo es None
    # (sales30d/purchases30d ausentes ahi), incluido attributed (no 40+0)
    assert fusionada.sales30d is None
    assert fusionada.purchases30d is None
    assert fusionada.attributed_sales_same_sku_30d is None
    # trazabilidad de ambas campanas, sin repeticion
    assert fusionada.campaign_ids == ["111", "222"]
    assert fusionada.ad_ids == ["11", "22"]
    assert skips == Counter({"fila agregada por clave duplicada en el reporte": 1})


def test_plan_productos_columna_faltante_es_none():
    """DoD B.1 / regla 3: una columna ausente del gzip es NULL, jamas 0 (la
    API solo trae filas con actividad y columnas pueden faltar; un 0 observado
    es OTRO dato)."""
    filas = [
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0CCCCCCC3",
            "advertisedSku": "SKU-C",
            "campaignId": 333,
            "clicks": 4,
            "cost": 1.5,
        }
    ]

    plan, skips = _planea_filas_productos(
        filas, hoy=dt.date(2026, 9, 4), fecha_ini=dt.date(2026, 9, 3), fecha_fin=dt.date(2026, 9, 3)
    )

    assert skips == Counter()
    assert len(plan) == 1
    fila = plan[0]
    assert (fila.impressions, fila.purchases30d, fila.sales30d) == (None, None, None)
    assert (fila.purchases_same_sku30d, fila.attributed_sales_same_sku_30d) == (None, None)
    assert (fila.cost, fila.clicks) == (Decimal("1.5"), 4)


def test_plan_productos_gates_con_motivo():
    """Regla 9: cada skip se AFIRMA con su clave (vocabulario CERRADO):
    asin/sku ausentes, date invalida, futura, fuera de rango, metrica no
    numerica/fraccionaria, sin ninguna metrica, same_sku > total y el par
    complementario envenenado post-fusion."""
    filas = [
        # validas
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0AAAAAAA1",
            "advertisedSku": "SKU-A",
            "campaignId": 111,
            "clicks": 1,
        },
        # gates de identidad
        {"date": "2026-09-03", "advertisedSku": "SKU-X", "campaignId": 1, "clicks": 1},
        {"date": "2026-09-03", "advertisedAsin": "B0YYYYYYYY9", "campaignId": 1, "clicks": 1},
        # gates de fecha
        {"date": "viernes", "advertisedAsin": "B0A", "advertisedSku": "S", "clicks": 1},
        {
            "date": "2026-09-05",
            "advertisedAsin": "B0A",
            "advertisedSku": "S",
            "campaignId": 1,
            "clicks": 1,
        },
        {
            "date": "2026-09-02",
            "advertisedAsin": "B0A",
            "advertisedSku": "S",
            "campaignId": 1,
            "clicks": 1,
        },
        # metrica fraccionaria en contador
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0A",
            "advertisedSku": "S",
            "campaignId": 1,
            "clicks": 2.5,
        },
        # sin NINGUNA metrica
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0A",
            "advertisedSku": "S",
            "campaignId": 1,
        },
        # same_sku > total (pre-check: el CHECK apm_same_sku_cabe abortaria el
        # lote entero si llegara al INSERT)
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0A",
            "advertisedSku": "S",
            "campaignId": 1,
            "sales30d": 10.0,
            "attributedSalesSameSku30d": 12.0,
        },
        # par complementario envenenado: la clave queda sin ninguna metrica
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0ZZZZZZZ9",
            "advertisedSku": "SKU-Z",
            "campaignId": 1,
            "clicks": 2,
        },
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0ZZZZZZZ9",
            "advertisedSku": "SKU-Z",
            "campaignId": 1,
            "cost": 0.5,
        },
    ]

    plan, skips = _planea_filas_productos(
        filas, hoy=dt.date(2026, 9, 4), fecha_ini=dt.date(2026, 9, 3), fecha_fin=dt.date(2026, 9, 3)
    )

    # solo la fila valida de arriba llega al plan (SKU-Z envenenado se descarta)
    assert [fila.sku for fila in plan] == ["SKU-A"]
    assert skips == Counter(
        {
            "fila de productos sin advertisedAsin": 1,
            "fila de productos sin advertisedSku": 1,
            "fila con date invalida": 1,
            "fila con metric_date futura": 1,
            "fila con metric_date fuera del rango solicitado": 1,
            "fila de productos con metrica no numerica o fraccionaria": 1,
            "fila sin ninguna metrica": 2,  # la cruda + la clave envenenada
            "fila con metrica same_sku mayor que el total": 1,
            "fila agregada por clave duplicada en el reporte": 1,
        }
    )


def test_plan_productos_metrica_negativa_aborta_fail_closed():
    """Mismo sellado que terminos (cross-review 1.5): la FUSION podria
    compensar un negativo con un positivo hermano y colarlo bajo el CHECK
    apm_no_negativos; ese nivel de corrupcion se quiere VER, no tragar."""
    filas = [
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0AAAAAAA1",
            "advertisedSku": "SKU-A",
            "campaignId": 111,
            "cost": -1.0,
            "clicks": 1,
        },
        {
            "date": "2026-09-03",
            "advertisedAsin": "B0AAAAAAA1",
            "advertisedSku": "SKU-A",
            "campaignId": 222,
            "cost": 4.0,
            "clicks": 2,
        },
    ]

    with pytest.raises(AdsReportsError) as excinfo:
        _planea_filas_productos(
            filas,
            hoy=dt.date(2026, 9, 4),
            fecha_ini=dt.date(2026, 9, 3),
            fecha_fin=dt.date(2026, 9, 3),
        )
    assert "negativa" in str(excinfo.value)
    assert "fail-closed" in str(excinfo.value)


def test_sql_del_modulo_parsea_como_postgres():
    """Guarda barata que SIEMPRE corre (patron test_reports_pipeline): la
    sintaxis SQL del modulo es Postgres valida segun el parser real,
    incluyendo el ON CONFLICT contra el indice parcial apm_dedupe_reporte."""
    import app.ads.reports as reports_modulo

    sql = reports_modulo._SQL_INSERT_PRODUCTO.replace("%s", "NULL")
    assert pglast.parse_sql(sql), "_SQL_INSERT_PRODUCTO no parseo"


# ---------------------------------------------------------------------------
# (b) ESTATICO - migracion 0020
# ---------------------------------------------------------------------------


def test_0020_parsea_y_sella_append_only():
    """El DDL de 0020 es Postgres valido, crea la tabla con la PK bitemporal
    del grano (platform, asin, sku, metric_date, observed_at), el indice
    parcial de dedupe por reporte, la sella con COMMENT y NO habilita
    UPDATE/DELETE ni toca tablas previas."""
    from pglast import ast

    stmts = [s.stmt for s in pglast.parse_sql(SQL20)]
    creadas = {s.relation.relname for s in stmts if isinstance(s, ast.CreateStmt)}
    assert "ads_product_metric_observation" in creadas
    # append-only: la migracion no borra ni altera tablas previas
    assert not [s for s in stmts if isinstance(s, ast.DropStmt)]
    alteraciones = [s for s in stmts if isinstance(s, ast.AlterTableStmt)]
    assert all(s.relname == "ads_product_metric_observation" for s in alteraciones), [
        s.relname for s in alteraciones
    ]
    # sin UPDATE/DELETE en GRANTs: solo SELECT/INSERT
    for g in stmts:
        if isinstance(g, ast.GrantStmt) and g.is_grant:
            nombres = {p.priv_name for p in (g.privileges or [])}
            assert not nombres & {"update", "delete", "truncate"}, nombres
    assert (
        "PRIMARY KEY (platform, advertised_asin, advertised_sku, metric_date, observed_at)" in SQL20
    )
    assert "apm_dedupe_reporte" in SQL20
    assert "salesSameSku30d" not in PRODUCTOS_CFG["columns"]


@_skip_db
def test_0023_triggers_append_only_bloquean_mutacion():
    """Hallazgo cross-review codex: 0020/0022 declaraban append-only solo con
    GRANTs. 0023 anade prohibir_mutacion (0001 §16): UPDATE, DELETE y TRUNCATE
    reventan aunque el rol los tengan."""
    import psycopg
    from psycopg import sql as pgsql

    SQL22 = (ROOT / "migrations" / "0022_disponibilidad_snapshot.sql").read_text(encoding="utf-8")
    SQL23 = (ROOT / "migrations" / "0023_append_only_bloque_b.sql").read_text(encoding="utf-8")

    SQL22 = (ROOT / "migrations" / "0022_disponibilidad_snapshot.sql").read_text(encoding="utf-8")
    SQL23 = (ROOT / "migrations" / "0023_append_only_bloque_b.sql").read_text(encoding="utf-8")
    dsn = _test_dsn()
    db = f"orbit_apm23_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL20)
        conn.execute(SQL22)
        conn.execute(SQL23)
        run = conn.execute(
            "INSERT INTO ingest_run (source) VALUES ('t23') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_product_metric_observation (platform, advertised_asin,"
            " advertised_sku, metric_date, observed_at, metric_currency, clicks, ingest_run_id)"
            " VALUES ('amazon_mx', 'B0T23AAAA1', 'S', '2026-09-01', now(), 'MXN', 1, %s)",
            (run,),
        )
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE ads_product_metric_observation SET clicks = 99")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM ads_product_metric_observation")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE ads_product_metric_observation")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE disponibilidad_observation")
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def test_poll_de_productos_usa_presupuesto_mayor():
    """Regresion del hallazgo productivo 2026-09-07: el primer reporte
    spAdvertisedProduct real tardo ~25 min en salir de PENDING y la corrida
    aborto fail-closed con el tope estandar de 120 intentos (10 min). El sync
    debe pedir INTENTOS_POLL_PRODUCTOS (>= 300) SOLO para spAdvertisedProduct,
    sin tocar el presupuesto de los 4 reportes estandar."""
    import re

    import app.ads.reports as reports

    assert reports.INTENTOS_POLL_PRODUCTOS >= 300
    # La rama del sync elige por reportTypeId, no globalmente.
    fuente = inspect.getsource(reports.sync_metrics)
    assert 'cfg.get("reportTypeId") == "spAdvertisedProduct"' in fuente
    assert "INTENTOS_POLL_PRODUCTOS" in fuente
    assert re.search(r"else\s+INTENTOS_POLL\b", fuente)
    # esperar_reporte sigue honrando el default estandar.
    assert (
        inspect.signature(reports.esperar_reporte).parameters["intentos"].default
        == reports.INTENTOS_POLL
    )


# ---------------------------------------------------------------------------
# (c) INTEGRACION - patron de test_reports_pipeline con fail-closed del DSN
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_pipeline_productos_en_vivo():
    """Corrida completa del orquestador de productos (reportes=(PRODUCTOS_CFG,))
    contra 0001+0020 en DB temporal:

    - CORRIDA 1: grano por (platform, asin, sku) con suma de campanas,
      multiproducto SIN reparto, columna faltante NULL, moneda por perfil,
      trazabilidad JSONB y cero escritura a ads_metric_observation.
    - RE-CORRIDA mismo report id: dedupe del indice parcial, tabla intacta.
    - CORRIDA con report id NUEVO: observacion NUEVA (append-only, regla 5).
    - Moneda cruzada: el trigger metric_moneda_de_plataforma rechaza.
    """
    import psycopg  # import real: sin driver el guard fail-closed ya decidio
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_apm_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # 0001 completa
        conn.execute(SQL20)  # la migracion NUEVA aplica sobre esquema fresh

        ahora = dt.datetime.now(dt.UTC)
        ayer = ahora.date() - dt.timedelta(days=1)
        medianoche_ayer = dt.datetime(ayer.year, ayer.month, ayer.day, tzinfo=dt.UTC)

        corrida = {"n": 1}

        def _filas(scope: str) -> list[dict]:
            if scope == "202":  # amazon_mx
                return [
                    {
                        "date": str(ayer),
                        "advertisedAsin": "B0AAAAAAA1",
                        "advertisedSku": "SKU-A",
                        "campaignId": 111,
                        "adGroupId": 1111,
                        "adId": 11,
                        "impressions": 100,
                        "clicks": 5,
                        "cost": 3.0,
                        "purchases30d": 1,
                        "sales30d": 50.0,
                        "purchasesSameSku30d": 1,
                        "attributedSalesSameSku30d": 40.0,
                    },
                    {
                        "date": str(ayer),
                        "advertisedAsin": "B0BBBBBBB2",
                        "advertisedSku": "SKU-B",
                        "campaignId": 111,  # misma campana: multiproducto
                        "adGroupId": 1112,
                        "adId": 12,
                        "impressions": 50,
                        "clicks": 2,
                        "cost": 7.0,
                        "purchases30d": 0,
                        "sales30d": 0,
                        "purchasesSameSku30d": 0,
                        "attributedSalesSameSku30d": 0,
                    },
                    {
                        "date": str(ayer),
                        "advertisedAsin": "B0AAAAAAA1",
                        "advertisedSku": "SKU-A",
                        "campaignId": 222,  # otra campana, MISMO asin/sku
                        "adGroupId": 2222,
                        "adId": 22,
                        "impressions": 10,
                        "clicks": 1,
                        "cost": 2.0,
                    },
                ]
            # amazon_us: una fila simple (cost/clicks)
            return [
                {
                    "date": str(ayer),
                    "advertisedAsin": "B0CCCCCCC3",
                    "advertisedSku": "SKU-C",
                    "campaignId": 333,
                    "adGroupId": 3333,
                    "adId": 33,
                    "clicks": 4,
                    "cost": 1.5,
                }
            ]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token(request)
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=PERFILES_API)
            if request.method == "POST" and request.url.path == "/reporting/reports":
                scope = request.headers.get("Amazon-Advertising-API-Scope")
                reporte = f"R-PROD-{scope}"
                if corrida["n"] >= 3:
                    reporte += "-B"  # reporte NUEVO del mismo dia (regla 5)
                return httpx.Response(200, json={"reportId": reporte, "status": "PENDING"})
            if request.url.path.startswith("/reporting/reports/"):
                reporte = request.url.path.rsplit("/", 1)[1]
                return httpx.Response(
                    200,
                    json={
                        "reportId": reporte,
                        "status": "COMPLETED",
                        "url": f"https://bucket.example.com/{reporte}.json",
                    },
                )
            reporte = request.url.path.removesuffix(".json").removeprefix("/")
            scope = reporte.removesuffix("-B").rsplit("-", 1)[1]
            return httpx.Response(
                200, content=gzip.compress(json.dumps(_filas(scope)).encode("utf-8"))
            )

        client = _cliente(handler)

        # ------------------------------------------------------------------
        # CORRIDA 1: 3 claves escritas (A fusionada, B, C-US) + 1 fila
        # absorbida por la fusion de las dos campanas del asin A
        # ------------------------------------------------------------------
        res1 = sync_metrics(
            conn,
            client,
            fecha_ini=ayer,
            fecha_fin=ayer,
            sleep=lambda s: None,
            reportes=(PRODUCTOS_CFG,),
        )

        assert res1.ok is True
        assert res1.rows_written == 3
        assert res1.rows_skipped == 1
        assert "1x fila agregada por clave duplicada en el reporte" in res1.skip_reason
        assert [resumen.report_id for resumen in res1.reportes] == [
            "R-PROD-101",
            "R-PROD-202",
        ]

        _SQL_FILAS = (
            "SELECT platform, advertised_asin, advertised_sku, metric_date::text,"
            " observed_at, metric_currency, impressions, clicks, cost, purchases30d,"
            " sales30d, purchases_same_sku30d, attributed_sales_same_sku_30d,"
            " campaign_ids, ad_ids, source_report_id, ingest_run_id"
            " FROM ads_product_metric_observation"
        )
        filas_db = conn.execute(_SQL_FILAS).fetchall()
        assert len(filas_db) == 3

        a = next(f for f in filas_db if f[2] == "SKU-A")
        assert (a[0], a[1]) == ("amazon_mx", "B0AAAAAAA1")
        assert a[3] == str(ayer)
        assert a[4] >= medianoche_ayer  # observado >= hecho (invariante)
        assert a[5] == "MXN"  # moneda del perfil MX
        assert a[6:9] == (110, 6, Decimal("5.0"))  # 111 + 222 SUMAN
        # columnas ausentes en el aporte de la campana 222 -> NULL (regla 3)
        assert (a[9], a[10], a[11], a[12]) == (None, None, None, None)
        assert a[13] == ["111", "222"]  # trazabilidad JSONB
        assert a[14] == ["11", "22"]
        assert a[15] == "R-PROD-202"
        assert a[16] == res1.run_id

        # multiproducto SIN reparto: B conserva SU cost 7 (no 12/2 ni promedio)
        b = next(f for f in filas_db if f[2] == "SKU-B")
        assert (b[8], b[7]) == (Decimal("7.0"), 2)
        assert b[13] == ["111"]

        # moneda del perfil US
        c = next(f for f in filas_db if f[2] == "SKU-C")
        assert (c[0], c[5]) == ("amazon_us", "USD")
        assert (c[8], c[7], c[9], c[10], c[11], c[12]) == (
            Decimal("1.5"),
            4,
            None,
            None,
            None,
            None,
        )

        # cero escritura a la tabla de campana: esta ingesta no reparte NI
        # toca agregados de entidad
        assert conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0] == 0

        # ------------------------------------------------------------------
        # RE-CORRIDA (mismos report ids): dedupe del indice parcial
        # ------------------------------------------------------------------
        time.sleep(0.01)  # observed_at distinto: la PK bitemporal no colisiona
        corrida["n"] = 2
        res2 = sync_metrics(
            conn,
            client,
            fecha_ini=ayer,
            fecha_fin=ayer,
            sleep=lambda s: None,
            reportes=(PRODUCTOS_CFG,),
        )
        assert res2.ok is True
        assert res2.rows_written == 0
        assert res2.rows_skipped == 4  # 3 duplicadas de DB + 1 fusionada del plan
        assert "1x fila duplicada del reporte R-PROD-101" in res2.skip_reason
        assert "2x fila duplicada del reporte R-PROD-202" in res2.skip_reason
        assert (
            conn.execute("SELECT count(*) FROM ads_product_metric_observation").fetchone()[0] == 3
        )

        # ------------------------------------------------------------------
        # APPEND-ONLY (regla 5): reporte NUEVO del mismo dia inserta fila
        # NUEVA, jamas UPDATE; la observacion vieja conserva su valor
        # ------------------------------------------------------------------
        time.sleep(0.01)
        corrida["n"] = 3
        res3 = sync_metrics(
            conn,
            client,
            fecha_ini=ayer,
            fecha_fin=ayer,
            sleep=lambda s: None,
            reportes=(PRODUCTOS_CFG,),
        )
        assert res3.ok is True
        assert res3.rows_written == 3
        assert (
            conn.execute("SELECT count(*) FROM ads_product_metric_observation").fetchone()[0] == 6
        )
        observaciones = conn.execute(
            "SELECT source_report_id, observed_at FROM ads_product_metric_observation"
            " WHERE platform = 'amazon_mx' AND advertised_sku = 'SKU-A' AND metric_date = %s"
            " ORDER BY observed_at",
            (ayer,),
        ).fetchall()
        assert len(observaciones) == 2
        assert {o[0] for o in observaciones} == {"R-PROD-202", "R-PROD-202-B"}
        assert observaciones[0][1] < observaciones[1][1]  # observed_at DISTINTO

        # ------------------------------------------------------------------
        # MONEDA CRUZADA: el trigger metric_moneda_de_plataforma (con la rama
        # nueva de 0020) rechaza con ERRCODE check_violation
        # ------------------------------------------------------------------
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO ads_product_metric_observation"
                " (platform, advertised_asin, advertised_sku, metric_date, observed_at,"
                " metric_currency, source_report_id, ingest_run_id)"
                " VALUES ('amazon_us', 'B0X', 'S', %s, now(), 'MXN', 'R-CRUCE', %s)",
                (ayer, res1.run_id),
            )
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_pipeline_productos_us_no_verificado_no_inventa_filas():
    """Politica 0.4 §1: perfil amazon_us NO verificado (400 en el primer
    POST). La corrida es fail-closed (aborta, run sellada ok=false) y NO
    inventa filas de US ni de nadie: un perfil sin reporte no genera dato."""
    import psycopg  # import real: sin driver el guard fail-closed ya decidio
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_apmus_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL20)

        ayer = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.amazon.com":
                return _token(request)
            if request.url.path == "/v2/profiles":
                return httpx.Response(200, json=PERFILES_API)
            if request.method == "POST" and request.url.path == "/reporting/reports":
                if request.headers.get("Amazon-Advertising-API-Scope") == "101":
                    # US no verificado: la API rechaza el report type
                    return httpx.Response(400, json={"code": 400})
                return httpx.Response(200, json={"reportId": "R-PROD-202", "status": "PENDING"})
            if request.url.path.startswith("/reporting/reports/"):
                reporte = request.url.path.rsplit("/", 1)[1]
                return httpx.Response(
                    200,
                    json={
                        "reportId": reporte,
                        "status": "COMPLETED",
                        "url": f"https://bucket.example.com/{reporte}.json",
                    },
                )
            return httpx.Response(200, content=gzip.compress(json.dumps([]).encode("utf-8")))

        client = _cliente(handler)
        with pytest.raises(AdsApiError):
            sync_metrics(
                conn,
                client,
                fecha_ini=ayer,
                fecha_fin=ayer,
                sleep=lambda s: None,
                reportes=(PRODUCTOS_CFG,),
            )

        # cero filas inventadas y run sellada ok=false con el rastro del 400
        assert (
            conn.execute("SELECT count(*) FROM ads_product_metric_observation").fetchone()[0] == 0
        )
        run = conn.execute(
            "SELECT ok, rows_skipped, skip_reason, finished_at"
            " FROM ingest_run WHERE ok IS FALSE ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run[0] is False
        assert run[1] == 0
        assert run[2] is not None and "status=400" in run[2]
        assert run[3] is not None  # sellada, no abierta
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
