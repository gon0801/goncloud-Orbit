"""Corrida diaria del motor de precios (REPRICING 01, A.5; REPRICING 02, S.1).

`app/precio/corrida.py` (claim `precio:<platform>` + advisory lock, fila
`precio_corrida`, cierre por observacion, huerfanas, decidir, compuerta y
aplicar sobre filas guardadas: `live` con un solo cubo, virtuales en
`shadow`) + `precio` en `app/cli.py` + cron en `docs/DEPLOY.md`.

Base real (`ORBIT_TEST_DSN`, cero `skipped` con el DSN de VERIFY): DB
temporal con ORDEN59. Todo el HTTP por `httpx.MockTransport`: cero red,
cero Amazon, cero reintentos en rafaga (sleeps falsos).
"""

from __future__ import annotations

import itertools
import os
import socket
import threading
from contextlib import contextmanager
from contextlib import nullcontext as _nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente

from app.precio.corrida import LockOcupado, Resumen, correr
from app.spapi.client import MERCADOS, VENDEDORES_PROPIOS, CuboTasa, SpapiClient
from app.spapi.precio_write import construir_escritor

RAIZ = Path(__file__).resolve().parents[1]

ORDEN59 = (
    "0001_initial.sql",
    "0020_ads_producto_metrica.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0032_spapi_pricing.sql",
    "0035_spapi_listings_inventario.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
    "0058_precio02_decision_exige.sql",
    "0059_precio02_repara_retencion_coherente.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()

PROPIO = VENDEDORES_PROPIOS[MERCADOS["amazon_mx"]]
CRED = {"lwa_app_id": "id-a5", "lwa_client_secret": "sec-a5", "refresh_token": "r-a5"}

# Todas las claves que lee la corrida (`leer_config` + `leer_config_motor`).
SETTINGS = {
    "precio_caida_ventas_pct": "0.40",
    "precio_senal_dias": 3,
    "precio_u60_min": 20,
    "precio_fechas_excluidas": [],
    "precio_escalon_max_pct": "0.10",
    "precio_movimiento_min_pct": "0.01",
    "precio_movimiento_min_abs_mxn": "1.00",
    "precio_movimiento_min_abs_usd": "0.10",
    "precio_tolerancia": "0.005",
    "precio_dias_entre_cambios": 7,
    "precio_freno_cambios": 3,
    "precio_divergencia_max_pct": "0.01",
    "precio_cap_amazon_mx": 5,
    "precio_freno_dias_error": 3,
    "precio_modo_global": "live",
    "precio_modo_universo": {"amazon_mx/fba": "live"},
    # S.3: la compuerta exige el motor completo (valores de la 0054).
    "precio_goal_min_pct": "0.10",
    "precio_goal_max_pct": "0.60",
    "precio_catalogo_max_dias_sin_reportar": 3,
    "precio_aviso_dias_sin_evaluar": 3,
    "precio_fusible_frac": "0.50",
    "precio_fusible_min_movimientos": 10,
    "precio_insumo_salto_pct": "0.05",
    "precio_insumo_salto_frac": "0.30",
    "precio_corte_errores_consecutivos": 5,
    "precio_senal_trafico_min": 200,
    "precio_cohorte_caida_pct": "0.25",
    "precio_envio_ventana_dias": 180,
}


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db():
    """DB temporal con ORDEN59; entrega `(conn, dsn)` con conn en autocommit."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"corrida_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN59:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn, _dsn_de_db(dsn, db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


def _config(conn, settings=None) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("test-corrida", Json(dict(settings if settings is not None else SETTINGS))),
    ).fetchone()[0]


def _producto(conn, sku="SKU-A5-1") -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto corrida') RETURNING id",
        (sku,),
    ).fetchone()[0]


def _listing(conn, producto: int, *, platform="amazon_mx", ext="B0A5000001", sku="SKU-A5") -> int:
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (producto, platform, ext, sku),
    ).fetchone()[0]


def _goal(
    conn,
    listing: int,
    *,
    platform="amazon_mx",
    mode="shadow",
    pct="0.30",
    desde=None,
    hasta=None,
) -> int:
    base = desde or conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    if desde is None:
        base = base - timedelta(days=30)
    return conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
        " valid_from, valid_to, creado_por, go_literal)"
        " VALUES (%s, %s, %s, %s, %s, %s, 't',"
        " CASE WHEN %s = 'live' THEN 'go-tdd' END) RETURNING id",
        (listing, platform, Decimal(pct), mode, base, hasta, mode),
    ).fetchone()[0]


def _oferta_obs(
    conn,
    listing: int,
    *,
    platform="amazon_mx",
    ext="B0A5000001",
    sku="SKU-A5",
    precio="116",
    evento=None,
    huella="ctx-a5",
    fetched=None,
    observed=None,
) -> int:
    base = datetime.now(UTC)
    evento = evento or f"oferta-{sku}"
    return conn.execute(
        "INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku, asin,"
        " canal, price_amount, price_currency, fetched_at, observed_at, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, 'fba', %s, 'MXN', %s, %s, %s, %s, %s) RETURNING id",
        (
            listing,
            platform,
            sku,
            ext,
            Decimal(precio),
            fetched or base,
            observed or base,
            evento,
            Json({}),
            huella,
        ),
    ).fetchone()[0]


_FEE_DETALLES = [
    {
        "fee_type": "ReferralFee",
        "final_fee": "12",
        "tax_amount": None,
        "included_fee_details": [],
    },
    {
        "fee_type": "FbaFee",
        "final_fee": "3",
        "tax_amount": None,
        "included_fee_details": [],
    },
]


def _fee_obs(
    conn,
    oferta_id: int,
    listing: int,
    *,
    platform="amazon_mx",
    ext="B0A5000001",
    sku="SKU-A5",
    precio="116",
    total="15",
    evento=None,
    huella="ctx-a5",
    fetched=None,
    observed=None,
) -> int:
    base = datetime.now(UTC)
    evento = evento or f"fee-{sku}"
    return conn.execute(
        "INSERT INTO estimacion_fee_observation (oferta_observation_id, listing_id, platform,"
        " seller_sku, asin, canal, quoted_price_amount, quoted_price_currency, total_fees,"
        " fee_details, fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, %s, 'fba', %s, 'MXN', %s, %s, %s, %s, %s, 'success',"
        " %s, %s, %s) RETURNING id",
        (
            oferta_id,
            listing,
            platform,
            sku,
            ext,
            Decimal(precio),
            Decimal(total),
            Json(_FEE_DETALLES),
            fetched or base,
            fetched or base,
            observed or base,
            evento,
            Json({}),
            huella,
        ),
    ).fetchone()[0]


def _componentes(*, costo="60") -> list:
    # m = (100 - C - 15 - 0 - 2.50) / 100; con C=60 -> 0.225 (sube a goal 0.30).
    return [
        {
            "nombre": "precio_bruto",
            "importe_normalizado": "116",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
        {
            "nombre": "ingreso_normalizado",
            "importe_normalizado": "100",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
        {
            "nombre": "costo_normalizado",
            "importe_normalizado": costo,
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
        {
            "nombre": "logistica",
            "importe_normalizado": "0",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
        {
            "nombre": "isr",
            "importe_normalizado": "2.50",
            "moneda_normalizada": "MXN",
            "pertenece_a_total": True,
        },
    ]


def _politica(conn) -> int:
    return conn.execute(
        "INSERT INTO estimacion_politica_version (label, universo, formula_version,"
        " settings, valid_from)"
        " VALUES ('test-a5', 'amazon_mx/fba', 'vtest', %s, '2026-09-01') RETURNING id",
        (Json({}),),
    ).fetchone()[0]


def _costo(conn, producto: int) -> int:
    return conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from)"
        " VALUES (%s, 60, 'MXN', false, '2026-01-01') RETURNING id",
        (producto,),
    ).fetchone()[0]


def _run(conn):
    return conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok, rows_skipped)"
        " VALUES ('accounting_sku_costs', now(), true, 0)"
        " RETURNING id, finished_at"
    ).fetchone()


def _run_ledger(conn, *, ok=True):
    """Carga del ledger (`accounting_ledger_events`, `app/ledger.py::SOURCE`)."""
    return conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok, rows_skipped)"
        " VALUES ('accounting_ledger_events', now(), %s, 0)"
        " RETURNING id, finished_at",
        (ok,),
    ).fetchone()


def _escenario(
    conn,
    listing: int,
    oferta_id: int,
    fee_id: int,
    politica_id: int,
    costo_id: int,
    run_id: int,
    validada_en,
    *,
    platform="amazon_mx",
    ext="B0A5000001",
    sku="SKU-A5",
    m="22.50",
    evento=None,
    huella="ctx-a5",
    estado="disponible",
    costo="60",
    motivos=None,
    fee_variable=None,
    fee_fijo=None,
) -> int:
    evento = evento or f"esc-{sku}"
    observed = datetime.now(UTC)
    valoracion = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    # `estimacion_escenario_no_disponible_sin_total`: fuera de `disponible`
    # van NULL moneda, contribucion y contribucion_pct.
    sin_total = estado != "disponible"
    moneda_sql = "'MXN'" if not sin_total else "%s"
    return conn.execute(
        "INSERT INTO estimacion_escenario (listing_id, platform, seller_sku, asin, canal,"
        " valoracion_date, observed_at, politica_version_id, formula_version,"
        " oferta_observation_id, fee_observation_id, sku_cost_id, costo_validation_run_id,"
        " costo_validated_at, moneda, contribucion, contribucion_pct, estado, motivos,"
        " componentes, exclusiones, canonical_input, context_fingerprint, source_event_id,"
        " fee_variable, fee_fijo)"
        " VALUES (%s, %s, %s, %s, 'fba', %s, %s, %s, 'vtest', %s, %s, %s, %s,"
        " %s, " + moneda_sql + ", %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s, %s) RETURNING id",
        (
            listing,
            platform,
            sku,
            ext,
            valoracion,
            observed,
            politica_id,
            oferta_id,
            fee_id,
            costo_id,
            run_id,
            validada_en,
            *([None] if sin_total else []),
            None if sin_total else Decimal(m).quantize(Decimal("0.01")),
            None if sin_total else Decimal(m),
            estado,
            Json(motivos if motivos is not None else []),
            Json(_componentes(costo=costo)),
            Json([]),
            Json({}),
            huella,
            evento,
            None if fee_variable is None else Decimal(fee_variable),
            None if fee_fijo is None else Decimal(fee_fijo),
        ),
    ).fetchone()[0]


def _cadena(conn, *, sku="SKU-A5", ext="B0A5000001", mode="shadow", costo="60"):
    """Listing + goal + oferta + fee + escenario coherente (m=0.225, P=116)."""
    prod = _producto(conn, sku=f"ODOO-{sku}")
    listing = _listing(conn, prod, ext=ext, sku=sku)
    _config(conn)
    _goal(conn, listing, mode=mode)
    marca = datetime.now(UTC)
    huella = f"ctx-{sku}"
    oferta = _oferta_obs(
        conn, listing, ext=ext, sku=sku, huella=huella, fetched=marca, observed=marca
    )
    fee = _fee_obs(
        conn, oferta, listing, ext=ext, sku=sku, huella=huella, fetched=marca, observed=marca
    )
    pol = _politica(conn)
    costo_id = _costo(conn, prod)
    run_id, validada_en = _run(conn)
    _escenario(
        conn,
        listing,
        oferta,
        fee,
        pol,
        costo_id,
        run_id,
        validada_en,
        ext=ext,
        sku=sku,
        huella=huella,
        costo=costo,
    )
    return {"producto": prod, "listing": listing, "sku": sku, "ext": ext, "evento": f"oferta-{sku}"}


def _price_obs(
    conn,
    *,
    asin="B0A5000001",
    platform="amazon_mx",
    precio="116",
    dia=None,
    observada=None,
    buybox=True,
):
    """Fila en `spapi_price_observation` (pricing de control S2)."""
    dia = dia or conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    if observada is None:
        observada = datetime(dia.year, dia.month, dia.day, 12, tzinfo=UTC)
    conn.execute(
        "INSERT INTO spapi_price_observation (asin, platform, metric_date, observed_at,"
        " own_listing_price, own_listing_currency, buy_box_is_own)"
        " VALUES (%s, %s, %s, %s, %s, 'MXN', %s)",
        (asin, platform, dia, observada, Decimal(precio), buybox),
    )


def _cuenta_decisiones(conn) -> int:
    return conn.execute("SELECT count(*) FROM precio_decision").fetchone()[0]


def _cuenta_cambios(conn) -> int:
    return conn.execute("SELECT count(*) FROM precio_cambio").fetchone()[0]


def _fee_time(conn):
    """`fetched_at` maximo de ofertas: `fee_time` valido para todas."""
    return conn.execute("SELECT max(fetched_at) FROM estimacion_oferta_observation").fetchone()[0]


# ---------------------------------------------------------------------------
# Red falsa: Pricing (GET ofertas), PATCH y ProductFees (POST)
# ---------------------------------------------------------------------------


def _ofertas_body(precio, moneda="MXN", seller=PROPIO, asin="B0A5000001"):
    return {
        "payload": {
            "ASIN": asin,
            "status": "Success",
            "Offers": [
                {
                    "SellerId": seller,
                    "IsBuyBoxWinner": True,
                    "IsFulfilledByAmazon": True,
                    "ListingPrice": {"Amount": precio, "CurrencyCode": moneda},
                }
            ],
        }
    }


def _respuesta_fees(request: httpx.Request, *, skus: dict, fee_time=None) -> httpx.Response:
    """Eco del pedido con referral 12 % + fba 3.00 (forma que verifica).

    El bloque identificador exige el `seller_sku` de la oferta, que el
    pedido no trae: `skus` mapea `source_event_id -> seller_sku` (explicito,
    sin magia).
    """
    import json as _json

    cuerpo = _json.loads(request.content.decode("utf-8"))
    pedido = cuerpo["FeesEstimateRequest"]
    ident = pedido["Identifier"]
    precio = Decimal(str(pedido["PriceToEstimateFees"]["ListingPrice"]["Amount"]))
    moneda = pedido["PriceToEstimateFees"]["ListingPrice"]["CurrencyCode"]
    referral = (Decimal("0.12") * precio).quantize(Decimal("0.01"))
    fba = Decimal("3.00")
    total = referral + fba
    t = (fee_time or datetime.now(UTC)).isoformat()
    return httpx.Response(
        200,
        json={
            "payload": {
                "FeesEstimateResult": {
                    "Status": "Success",
                    "FeesEstimateIdentifier": {
                        "MarketplaceId": "A1AM78C64UM0Y8",
                        "IdType": "SellerSKU",
                        "IdValue": skus[ident],
                        "SellerInputIdentifier": ident,
                        "IsAmazonFulfilled": True,
                        "PriceToEstimateFees": {
                            "ListingPrice": {"CurrencyCode": moneda, "Amount": float(precio)}
                        },
                    },
                    "FeesEstimate": {
                        "TimeOfFeesEstimation": t,
                        "TotalFeesEstimate": {"CurrencyCode": moneda, "Amount": float(total)},
                        "FeeDetailList": [
                            {
                                "FeeType": "ReferralFee",
                                "FeeAmount": {"CurrencyCode": moneda, "Amount": float(referral)},
                                "FinalFee": {"CurrencyCode": moneda, "Amount": float(referral)},
                            },
                            {
                                "FeeType": "FbaFee",
                                "FeeAmount": {"CurrencyCode": moneda, "Amount": float(fba)},
                                "FinalFee": {"CurrencyCode": moneda, "Amount": float(fba)},
                            },
                        ],
                    },
                }
            }
        },
    )


class _RedFalsa:
    """Pricing + PATCH + Fees por `MockTransport`; cuenta cada verbo."""

    def __init__(
        self, *, precio_vivo="116.00", ofertas=None, patch=(202, None), fee_time=None, skus=None
    ):
        self.precio_vivo = precio_vivo
        self.ofertas = ofertas
        self.patch = patch
        # `fee_time` dentro de [fetched_at, observed_at]: el `fetched` de la
        # oferta (determinista; `now` real corre riesgo de microcarrera).
        self.fee_time = fee_time
        self.skus = dict(skus or {})
        self.n_get = 0
        self.n_patch = 0
        self.n_fees = 0
        self.pedidos_patch: list = []
        self.aviso_patch = None
        self.retener_patch = None

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/o2/token":
            return httpx.Response(200, json={"access_token": "tok-a5", "expires_in": 3600})
        if request.method == "GET" and "offers" in path:
            self.n_get += 1
            # El ASIN se extrae de la ruta (el parser lo exige igual al pedido).
            asin = path.rstrip("/").split("/")[-2] if path.endswith("/offers") else "B0A5000001"
            cuerpo = (
                self.ofertas
                if self.ofertas is not None
                else _ofertas_body(float(self.precio_vivo), asin=asin)
            )
            if isinstance(cuerpo, tuple):
                return httpx.Response(cuerpo[0], json=cuerpo[1])
            return httpx.Response(200, json=cuerpo)
        if request.method == "GET" and "competitivePrice" in path:
            self.n_get += 1
            return httpx.Response(200, json={"payload": []})
        if request.method == "POST" and "feesEstimate" in path:
            self.n_fees += 1
            return _respuesta_fees(request, skus=self.skus, fee_time=self.fee_time)
        if request.method == "PATCH":
            self.n_patch += 1
            self.pedidos_patch.append(request)
            if self.n_patch == 1:
                # C3-1: barrera anti-intermitencia (ver el test de dos hilos).
                if self.aviso_patch is not None:
                    self.aviso_patch.set()
                if self.retener_patch is not None:
                    assert self.retener_patch.wait(timeout=120)
            status, body = self.patch
            if body is None:
                body = {"status": "ACCEPTED", "submissionId": "sub-a5"}
            return httpx.Response(status, json=body)
        raise AssertionError(f"llamada inesperada: {request.method} {path}")

    @property
    def transport(self):
        return httpx.MockTransport(self._handler)


def _reloj_falso():
    estado = {"v": 1000.0, "esperas": []}

    def clock():
        return estado["v"]

    def sleep(segundos):
        estado["esperas"].append(segundos)
        estado["v"] += segundos

    return clock, sleep, estado


def _clientes(red: _RedFalsa, platform="amazon_mx"):
    from app.estimacion_fees import ProductFeesClient

    clock, sleep, _estado = _reloj_falso()
    lector = SpapiClient(credentials=dict(CRED), transport=red.transport, sleep=lambda s: None)
    escritor = construir_escritor(lector, platform, transport=red.transport, sleep=lambda s: None)
    fees = ProductFeesClient(
        credentials=dict(CRED), transport=red.transport, sleep=lambda s: None, clock=clock
    )
    return lector, escritor, fees


def _cuerpo_falso(*, platform, sku, precio, moneda):
    return {"falso": True, "sku": sku, "precio": str(precio), "moneda": moneda}


def _mercado(red, platform="amazon_mx", *, construir_cuerpo=_cuerpo_falso, cubo=None):
    """MercadoAmazon con la red falsa (los tests no tocan `correr` sin mercado)."""
    from app.spapi.precio_mercado import MercadoAmazon

    lector, escritor, fees = _clientes(red, platform)
    return MercadoAmazon(
        platform,
        lector=lector,
        escritor=escritor,
        fees=fees,
        limitador=cubo,
        construir_cuerpo=construir_cuerpo,
    )


def _corre(conn, red, platform="amazon_mx", **kw):
    """`correr` con red falsa y cubo sin dormir de verdad."""
    clock, sleep, _estado = _reloj_falso()
    fijar_fee = kw.pop("fijar_fee", True)
    if red.fee_time is None and fijar_fee:
        red.fee_time = _fee_time(conn)
    kw.pop("construir_cuerpo", None)
    cubo = kw.pop("limitador", None)
    if cubo is None:
        cubo = CuboTasa(sleep=sleep, clock=clock, capacidad=1, tasa=0.5)
    kw.setdefault("owner", "tdd")
    return correr(conn, _mercado(red, platform, cubo=cubo), **kw)


def _decision_unica(conn):
    return conn.execute("SELECT resultado, motivo, p_aplicado FROM precio_decision").fetchone()


# ---------------------------------------------------------------------------
# Aceptacion A.5
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_dia_sin_insumos_da_no_evaluado_y_cero_patch():
    """Sin escenario ni pricing: N `no_evaluado`, cero PATCH, cero cambios."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        # La plataforma si tiene moneda observable (oferta de OTRO listing):
        # el listing bajo prueba sigue sin insumos propios.
        otro = _listing(conn, _producto(conn, "SKU-A5-2"), ext="B0A5000002", sku="SKU-A5-B")
        _oferta_obs(conn, otro, ext="B0A5000002", sku="SKU-A5-B")
        _config(conn)
        _goal(conn, lid)
        red = _RedFalsa()
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        assert _decision_unica(conn)[:2] == ("no_evaluado", "precio_ausente")
        assert _cuenta_cambios(conn) == 0
        assert red.n_patch == 0 and red.n_get == 0 and red.n_fees == 0


@_skip_sin_pg
def test_live_subir_aplica_un_patch():
    """Cadena live coherente: subir aplicado, un PATCH, resumen 1/1."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        fila = _decision_unica(conn)
        assert fila[0] == "subir" and fila[2] == Decimal("127.60")
        assert red.n_patch == 1
        estado = conn.execute("SELECT estado FROM precio_cambio").fetchone()[0]
        assert estado == "enviado"


def _ventas_parejas(conn, prod, *, qty, run_id, amount="200", platform="amazon_mx"):
    """Ventas planas en [hoy-75, hoy-16] (la ventana de `u60`)."""
    hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    for atraso in range(16, 76):
        dia = hoy - timedelta(days=atraso)
        mediodia = datetime(dia.year, dia.month, dia.day, 12, tzinfo=UTC)
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
            " product_id, quantity, amount, amount_currency, ingest_run_id)"
            " VALUES (%s, 'sale', %s, %s, %s, %s, %s, 'MXN', %s)",
            (platform, dia, mediodia, prod, qty, Decimal(amount), run_id),
        )


@_skip_sin_pg
def test_segunda_corrida_del_dia_no_decide():
    """Con decision de hoy, la segunda corrida sale 0/0 sin tocar nada."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        primera = _corre(conn, red)
        assert (primera.decisiones, primera.escritas) == (1, 1)
        segunda = _corre(conn, red)
        assert (segunda.decisiones, segunda.escritas) == (0, 0)
        assert _cuenta_decisiones(conn) == 1 and _cuenta_cambios(conn) == 1
        assert len(red.pedidos_patch) == 1


@_skip_sin_pg
def test_dos_hilos_exactamente_un_patch():
    """Dos corridas simultaneas: una gana el claim, un solo PATCH.

    Barrera: el primer PATCH avisa y se retiene hasta que el otro hilo
    recibe `LockOcupado`. Sin ella, el hilo 0 podia terminar y soltar el
    claim antes de que el 1 lo intentara: intermitente.
    """
    import time as _time

    import psycopg as _psycopg

    with _db() as (conn, dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        red.aviso_patch = threading.Event()
        red.retener_patch = threading.Event()
        resultados = []
        errores = []

        def _hilo(numero):
            try:
                otro = _psycopg.connect(dsn, autocommit=True)
                try:
                    resultados.append(_corre(otro, red, owner=f"tdd-hilo-{numero}"))
                except LockOcupado:
                    resultados.append("ocupado")
                finally:
                    otro.close()
            except Exception as exc:  # noqa: BLE001
                errores.append(exc)

        hilos = [threading.Thread(target=_hilo, args=(i,)) for i in range(2)]
        for h in hilos:
            h.start()
        assert red.aviso_patch.wait(timeout=120)
        plazo = _time.monotonic() + 120
        while "ocupado" not in resultados and _time.monotonic() < plazo:
            _time.sleep(0.05)
        assert "ocupado" in resultados
        red.retener_patch.set()
        for h in hilos:
            h.join(timeout=120)
            assert not h.is_alive()
        assert not errores
        hechos = sorted(
            ((r.decisiones, r.escritas) if r != "ocupado" else "ocupado" for r in resultados),
            key=str,
        )
        assert hechos == [(1, 1), "ocupado"]
        assert len(red.pedidos_patch) == 1


@_skip_sin_pg
def test_shadow_subir_crea_virtual_sin_patch():
    """En sombra el subir nace `confirmado/virtual`, cero PATCH."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="shadow")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT estado, confirmado_por, aplicado, precio_despues FROM precio_cambio"
        ).fetchone()
        assert tuple(fila[:3]) == ("confirmado", "virtual", False)
        assert fila[3] == Decimal("127.60")
        assert red.n_patch == 0


@_skip_sin_pg
def test_cambios_previos_incluyen_virtual_para_cooldown_en_sombra():
    """El feed de cambios lleva el virtual (en sombra cuenta para cooldown).

    La secuencia de dos dias no se siembra (la base fija `decision_date` a
    hoy y `precio_decision` es append-only): el freno con virtual lo cubren
    las reglas (A.2) y aqui el feed que la corrida les entrega.
    """
    from app.precio.fuentes import cambios_previos

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="shadow")
        lid = datos["listing"]
        dec = _decision_subir(conn, lid, mode="shadow")
        hace_3 = datetime.now(UTC) - timedelta(days=3)
        conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at, confirmado_por)"
            " VALUES (%s, %s, 'amazon_mx', 100, 'MXN', 110, 'MXN', false,"
            " 'confirmado', %s, 'virtual')",
            (dec, lid, hace_3),
        )
        cambios = cambios_previos(conn, lid, "amazon_mx")
        assert len(cambios) == 1
        unico = cambios[0]
        assert (unico.direccion, unico.estado, unico.aplicado) == ("subir", "confirmado", False)
        assert unico.enviado_en == hace_3.date()


def _cambio_con_estado(conn, dec, lid, *, estado, dia, error=None, reversa=False):
    """Cambio nacido `pendiente` llevado a `estado` (transicion legal)."""
    cid = conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at)"
        " VALUES (%s, %s, 'amazon_mx', 100, 'MXN', 110, 'MXN', true,"
        " 'pendiente', %s) RETURNING id",
        (dec, lid, dia),
    ).fetchone()[0]
    if estado == "enviado":
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado',"
            " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
            (cid,),
        )
    elif estado == "error":
        conn.execute(
            "UPDATE precio_cambio SET estado = 'error', error_code = %s WHERE id = %s",
            (error or "PATCH /x 500", cid),
        )
    elif estado in ("confirmado", "no_confirmado"):
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado',"
            " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
            (cid,),
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = %s, confirmado_por = 'observacion' WHERE id = %s",
            (estado, cid),
        )
    return cid


def _decision_subir(
    conn,
    lid,
    *,
    mode="live",
    m="0.25",
    goal="0.30",
    platform="amazon_mx",
    goal_id=None,
    canal=None,
):
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, mode,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency,"
        " l_origen, verificacion, goal_id, canal)"
        " VALUES (%s, %s, 'subir', %s, %s, %s,"
        " 100, 'MXN', 110, 'MXN', 110, 'MXN',"
        " 10, 'MXN', 20, 'MXN', 12, 'MXN', 5, 'MXN', 8, 'MXN',"
        " 'politica', 'cotizada', %s, %s)"
        " RETURNING id",
        (lid, platform, mode, Decimal(goal), Decimal(m), goal_id, canal),
    ).fetchone()[0]


@_skip_sin_pg
def test_enviado_de_ayer_se_cierra_antes_de_decidir():
    """El `enviado` de ayer del MISMO listing amanece `no_confirmado` y frena hoy.

    El cierre corre antes de decidir: un mutante que cierre despues deja
    pasar el `subir` y muere.
    """
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-EC-B", ext="B0A50000EC")
        ayer = datetime.now(UTC) - timedelta(days=1)
        cid = _cambio_historia(conn, dec_b, datos["listing"], estado="enviado", dia=ayer)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (
            conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid,)).fetchone()[0]
            == "no_confirmado"
        )
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("frenado", "no_confirmado")
        assert red.n_patch == 0


@_skip_sin_pg
def test_freno_tres_dias_de_error_unidad():
    """Tres `error` seguidos frenan; con dos o huecos, no.

    El end-to-end de dos dias no se siembra (la base fija `decision_date`
    a hoy): la rama `True` en la corrida la prueba el mutante M-freno, que
    quita la llamada y deja pasar el `subir`.
    """
    from app.precio.corrida import freno_por_error

    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        desde = hoy - timedelta(days=30)
        assert freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=desde) is False
        for atraso in (1, 2):
            dia = datetime.now(UTC) - timedelta(days=atraso)
            _cambio_con_estado(conn, dec, lid, estado="error", dia=dia)
        assert freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=desde) is False
        _cambio_con_estado(
            conn, dec, lid, estado="error", dia=datetime.now(UTC) - timedelta(days=3)
        )
        assert freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=desde) is True
        # B3: con goal nuevo posterior a la racha se evalua normal.
        assert (
            freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=hoy - timedelta(days=1))
            is False
        )
        # D11: con hueco (-1, -2, -4) no hay racha de 3: no frena.
        prod_h = _producto(conn, sku="SKU-FH-H")
        lid_h = _listing(conn, prod_h, ext="B0A50000FH", sku="SKU-FH-H")
        _goal(conn, lid_h, mode="live")
        dec_h = _decision_subir(conn, lid_h)
        for atraso in (1, 2, 4):
            _cambio_con_estado(
                conn, dec_h, lid_h, estado="error", dia=datetime.now(UTC) - timedelta(days=atraso)
            )
        assert freno_por_error(conn, lid_h, "amazon_mx", hoy, dias=3, desde=desde) is False


@_skip_sin_pg
def test_freno_forzado_persiste_sin_patch(monkeypatch):
    """Con freno activo la corrida persiste `frenado` y no aplica."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        monkeypatch.setattr(corrida_mod, "freno_por_error", lambda *a, **k: True)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = _decision_unica(conn)
        assert fila[:2] == ("frenado", "api_error")
        assert _cuenta_cambios(conn) == 0
        assert red.n_patch == 0


@_skip_sin_pg
def test_freno_se_evalua_para_cada_goal(monkeypatch):
    """La corrida evalua el freno de errores por cada goal (cableado).

    K9: con DOS goals, el espia ve los dos.
    """
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        prod_b = _producto(conn, sku="SKU-FG-B")
        lid_b = _listing(conn, prod_b, ext="B0A50000FB", sku="SKU-FG-B")
        _goal(conn, lid_b, mode="live")
        llamadas = []
        real = corrida_mod.freno_por_error

        def _espia(c, lid, platform, hoy, *, dias, desde):
            llamadas.append((lid, platform, dias))
            return real(c, lid, platform, hoy, dias=dias, desde=desde)

        monkeypatch.setattr(corrida_mod, "freno_por_error", _espia)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 1)
        assert sorted(llamadas) == sorted(
            [(datos["listing"], "amazon_mx", 3), (lid_b, "amazon_mx", 3)]
        )


@_skip_sin_pg
def test_huerfana_no_toca_reversas():
    """La `pendiente` reversa es del dueno: la corrida no la cierra.

    Sembrada ayer como el caso positivo: solo difiere en `es_reversa`.
    """
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid)
        ahora = datetime.now(UTC)
        orig = _cambio_con_estado(conn, dec, lid, estado="enviado", dia=ahora)
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (orig,),
        )
        conn.execute(
            "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at, es_reversa, reversa_de)"
            " VALUES (%s, 'amazon_mx', 110, 'MXN', 100, 'MXN', true,"
            " 'pendiente', %s, true, %s) RETURNING id",
            (lid, ahora - timedelta(days=1), orig),
        ).fetchone()[0]
        red = _RedFalsa()
        res = _corre(conn, red)
        assert res.huerfanas == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_cambio WHERE es_reversa AND estado = 'pendiente'"
            ).fetchone()[0]
            == 1
        )


@_skip_sin_pg
def test_cambiar_precio_pasa_limitador_a_sus_lecturas():
    """(a) con `limitador`, el GET previo y el readback consumen cubo."""
    from app.precio.cambios import aplicar
    from app.spapi.client import CuboTasa

    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid)
        clock, sleep, _estado = _reloj_falso()
        red = _RedFalsa(precio_vivo="100.00")
        consumos = []
        cubo = CuboTasa(sleep=sleep, clock=clock, capacidad=100, tasa=1000.0)
        real = cubo.consumir

        def _contando():
            consumos.append(1)
            return real()

        cubo.consumir = _contando
        res = aplicar(conn, _mercado(red, cubo=cubo), dec)
        assert res.estado == "enviado"
        # C3-6: conteo exacto (GET previo + readback; el PATCH no consume).
        assert len(consumos) == 2


@_skip_sin_pg
def test_corrida_usa_un_solo_cubo_por_plataforma():
    """(a) la corrida aplica todo con el limitador inyectado (uno por plataforma).

    C3-6: conteos exactos (2 consumos por cambio); un cubo nuevo por
    listing deja cero consumos y muere.
    """
    with _db() as (conn, _dsn):
        ua = _cadena(conn, sku="SKU-U-A", ext="B0A50000UA", mode="live")
        ub = _cadena(conn, sku="SKU-U-B", ext="B0A50000UB", mode="live")
        _price_obs(conn, asin="B0A50000UA")
        _price_obs(conn, asin="B0A50000UB")
        clock, sleep, _estado = _reloj_falso()
        cubo = CuboTasa(sleep=sleep, clock=clock, capacidad=100, tasa=1000.0)
        real = cubo.consumir
        consumos = []

        def _contando():
            consumos.append(1)
            return real()

        cubo.consumir = _contando
        red = _RedFalsa(skus={ua["evento"]: ua["sku"], ub["evento"]: ub["sku"]})
        res = _corre(conn, red, limitador=cubo)
        assert (res.decisiones, res.escritas) == (2, 2)
        assert len(consumos) == 4
        assert len(red.pedidos_patch) == 2


@_skip_sin_pg
def test_compuerta_corre_una_vez_por_plataforma(monkeypatch):
    """(b) la compuerta corre una vez por plataforma, sin mezclas.

    C3-7: con un goal vigente de OTRA plataforma, ese listing no se decide
    ni se aplica en esta corrida.
    """
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        prod_us = _producto(conn, "SKU-RP-US")
        lid_us = _listing(conn, prod_us, platform="amazon_us", ext="B0A50000US", sku="SKU-RP-US")
        _goal(conn, lid_us, platform="amazon_us", mode="live")
        llamadas = []
        real = corrida_mod._medir_compuerta

        def _espia(conn_e, plataforma, hoy, config, corrida_id):
            llamadas.append(plataforma)
            return real(conn_e, plataforma, hoy, config, corrida_id)

        monkeypatch.setattr(corrida_mod, "_medir_compuerta", _espia)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert llamadas == ["amazon_mx"]
        assert (res.decisiones, res.escritas) == (1, 1)
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_decision WHERE listing_id = %s", (lid_us,)
            ).fetchone()[0]
            == 0
        )


@_skip_sin_pg
@pytest.mark.parametrize(
    "ajuste,clave",
    [
        ({"precio_freno_dias_error": 0}, "precio_freno_dias_error"),
        ({"precio_freno_dias_error": 15}, "precio_freno_dias_error"),
        ({"precio_freno_dias_error": True}, "precio_freno_dias_error"),
    ],
    ids=["freno-0", "r1b2_m32-freno-15", "r1b2_m33-freno-True"],
)
def test_umbral_fuera_de_cota_aborta_al_arrancar(ajuste, clave):
    """(f) freno fuera de cota: `ValueError` que nombra la clave."""
    with _db() as (conn, _dsn):
        _config(conn, {**SETTINGS, **ajuste})
        red = _RedFalsa()
        with pytest.raises(ValueError, match=clave):
            _corre(conn, red)
        # D12: el aborto es al arrancar (antes del lock): con config sana
        # la corrida valida corre (el claim no quedo tomado).
        _config(conn)
        assert _corre(conn, red).decisiones == 0


@_skip_sin_pg
@pytest.mark.parametrize(
    "clave", ["precio_freno_dias_error", "precio_modo_global", "precio_modo_universo"]
)
def test_umbral_ausente_aborta_al_arrancar(clave):
    """(f) freno o modo global ausente: `ValueError` que nombra la clave, sin default."""
    with _db() as (conn, _dsn):
        ajustes = {k: v for k, v in SETTINGS.items() if k != clave}
        _config(conn, ajustes)
        red = _RedFalsa()
        with pytest.raises(ValueError, match=clave):
            _corre(conn, red)
        # D12: el aborto es al arrancar (antes del lock): con config sana
        # la corrida valida corre (el claim no quedo tomado).
        _config(conn)
        assert _corre(conn, red).decisiones == 0


@_skip_sin_pg
def test_reporte_es_solo_lectura():
    """`precio --reporte` resume decisiones y cambios sin escribir nada.

    D13: la conexion va en `default_transaction_read_only = on` (cualquier
    escritura del reporte revienta); la corrida vive en su propio test.
    """
    from app.precio.corrida import reporte

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        dec = _decision_subir(conn, datos["listing"])
        _cambio_con_estado(conn, dec, datos["listing"], estado="enviado", dia=datetime.now(UTC))
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        conn.execute("SET default_transaction_read_only = on")
        lineas = reporte(conn, desde=hoy, hasta=hoy, platform="amazon_mx")
        assert any("subir=1" in ln for ln in lineas)
        assert any("decisiones=1" in ln for ln in lineas)
        assert _cuenta_decisiones(conn) == 1
        assert _cuenta_cambios(conn) == 1


def _ventas_perdiendo(conn, prod, *, sku, run_id, platform="amazon_mx", hoy=None, lid=None):
    """75 dias de ledger + inventario + estado: u15=15, u60=120 (pierde).

    Esperado = 120/60*15*(1-0.40) = 18 > 15: con `senal_dias=1` la racha
    cierra en esta corrida (`perdiendo`, racha 1). S.7: con `lid` las
    ventas se atribuyen a la unidad (sin atribuir, la senal sale
    `venta_sin_publicacion`).
    """
    hoy = hoy or conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    for atraso in range(1, 76):
        dia = hoy - timedelta(days=atraso)
        qty = 1 if atraso <= 15 else 2
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
            " product_id, quantity, amount, amount_currency, ingest_run_id)"
            " VALUES (%s, 'sale', %s, %s, %s, %s, %s, 'MXN', %s)",
            (platform, dia, datetime.now(UTC), prod, qty, Decimal("200"), run_id),
        )
        if lid is not None:
            conn.execute(
                "INSERT INTO ledger_event_atribucion (ledger_event_id, platform,"
                " listing_id, variante_ext, resuelto_por, ingest_run_id)"
                " VALUES (currval('ledger_event_id_seq'), %s, %s, NULL, 'sku', %s)",
                (platform, lid, run_id),
            )
        if atraso <= 15:
            mediodia = datetime(dia.year, dia.month, dia.day, 12, tzinfo=UTC)
            conn.execute(
                "INSERT INTO spapi_inventario_observation (seller_sku, platform,"
                " metric_date, observed_at, total_quantity, api_version)"
                " VALUES (%s, %s, %s, %s, 5, 'v1')",
                (sku, platform, dia, mediodia),
            )
            conn.execute(
                "INSERT INTO spapi_listing_estado_observation (seller_sku, platform,"
                " status, api_version, observed_at)"
                " VALUES (%s, %s, 'BUYABLE', 'v1', %s)",
                (sku, platform, mediodia),
            )


@_skip_sin_pg
def test_virtual_de_sombra_dispara_freno_6():
    """(c) el virtual cuenta en sombra: perdiendo + subida = `frenado`.

    A nivel del ensamblado de la corrida + `decidir` (la corrida completa
    saltaria este listing: el andamio trae decision de hoy y la base fija
    `decision_date`; el feed de cambios es el mismo que usa el run).
    """
    from app.estimacion_reader import leer_cuenta
    from app.precio.config import leer_config
    from app.precio.fuentes import cambios_previos, historial_margenes, ultima_subida
    from app.precio.objetivo import ErrorObjetivo, derivar_ref_fijo
    from app.precio.puerto import Miembro, UnidadPrecio
    from app.precio.reglas import decidir
    from app.precio.tipos import Escenario
    from app.spapi.precio_mercado import historia_unidad

    with _db() as (conn, _dsn):
        ajustes = {**SETTINGS, "precio_senal_dias": 1}
        prod = _producto(conn, sku="ODOO-F6")
        lid = _listing(conn, prod, ext="B0A50000F6", sku="SKU-F6")
        _config(conn, ajustes)
        _goal(conn, lid, mode="shadow")
        marca = datetime.now(UTC)
        oferta = _oferta_obs(
            conn,
            lid,
            ext="B0A50000F6",
            sku="SKU-F6",
            huella="ctx-F6",
            fetched=marca,
            observed=marca,
        )
        fee = _fee_obs(
            conn,
            oferta,
            lid,
            ext="B0A50000F6",
            sku="SKU-F6",
            huella="ctx-F6",
            fetched=marca,
            observed=marca,
        )
        pol = _politica(conn)
        costo_id = _costo(conn, prod)
        run_id, validada_en = _run(conn)
        _escenario(
            conn,
            lid,
            oferta,
            fee,
            pol,
            costo_id,
            run_id,
            validada_en,
            ext="B0A50000F6",
            sku="SKU-F6",
            huella="ctx-F6",
        )
        run_ledger, _fin_ledger = _run_ledger(conn)
        _ventas_perdiendo(conn, prod, sku="SKU-F6", run_id=run_ledger, lid=lid)
        dec = _decision_subir(conn, lid, mode="shadow")
        conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at, confirmado_por)"
            " VALUES (%s, %s, 'amazon_mx', 100, 'MXN', 110, 'MXN', false,"
            " 'confirmado', %s, 'virtual')",
            (dec, lid, datetime.now(UTC)),
        )
        _price_obs(conn, asin="B0A50000F6")
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        ahora = datetime.now(UTC)
        config = leer_config(ajustes)
        cuenta = leer_cuenta(conn, lid, as_of=ahora)
        assert cuenta is not None and not isinstance(cuenta, str)
        from app.precio.tipos import EntradaDecision, Fees, Importe, ObservacionPricing
        from app.precio.ventas import evaluar_senal

        pricing = conn.execute(
            "SELECT own_listing_price, own_listing_currency, observed_at, buy_box_is_own"
            " FROM spapi_price_observation"
            " WHERE asin = %s AND platform = %s AND metric_date = %s"
            " AND own_listing_price IS NOT NULL"
            " ORDER BY observed_at DESC LIMIT 1",
            ("B0A50000F6", "amazon_mx", hoy),
        ).fetchone()
        unidad = UnidadPrecio(
            listing_id=lid,
            plataforma="amazon_mx",
            externo="B0A50000F6",
            canal=None,
            producto_ancla=prod,
            miembros=(Miembro(prod, "SKU-F6", None),),
        )
        historia = historia_unidad(conn, unidad=unidad, canal=cuenta.canal, hoy=hoy)
        previos = cambios_previos(conn, lid, "amazon_mx")
        entrada = EntradaDecision(
            listing_id=lid,
            platform="amazon_mx",
            product_id=prod,
            canal=cuenta.canal,
            mode="shadow",
            goal=Decimal("0.30"),
            escenario=Escenario(
                componentes=cuenta.componentes,
                fee_detalles=cuenta.fee_detalles,
                precio_cotizado=cuenta.precio_cotizado,
                iva_divisor=cuenta.iva_divisor,
                isr_tasa=cuenta.isr_tasa,
                precio_incluye_iva=cuenta.precio_incluye_iva,
                oferta_observada_en=cuenta.oferta_observada_en,
            ),
            pricing=(
                ObservacionPricing(precio=Importe(pricing[0], pricing[1]), observada_en=pricing[2])
                if pricing is not None
                else None
            ),
            senal=evaluar_senal(
                historia,
                hoy=hoy,
                config=config,
                racha_previa=0,
                ultima_subida=ultima_subida(previos),
                trafico_min=ajustes["precio_senal_trafico_min"],
            ),
            cambios=previos,
            historial=historial_margenes(conn, lid, "amazon_mx"),
            goal_vigente_desde=hoy - timedelta(days=30),
            motivo_estimacion=None,
            buy_box_is_own=pricing[3] if pricing is not None else None,
        )
        try:
            _ref, fijo = derivar_ref_fijo(
                cuenta.fee_detalles,
                cuenta.componentes.fees.valor,
                cuenta.precio_cotizado.valor,
            )
            fees_escenario = Fees(
                total=cuenta.componentes.fees,
                variable=Importe(cuenta.componentes.fees.valor - fijo, "MXN"),
                fijo=Importe(fijo, "MXN"),
                impuesto_pendiente=False,
            )
        except ErrorObjetivo as exc:
            fees_escenario = exc.motivo
        d = decidir(entrada, hoy=hoy, config=config, fees_escenario=fees_escenario)
        assert (d.resultado, d.motivo) == ("frenado", "perdiendo_tras_subida")


# ---------------------------------------------------------------------------
# CLI `precio` y cron
# ---------------------------------------------------------------------------

# Fuente unica de las lineas (docs/DEPLOY.md las copia de aqui). D.3: las
# dos de precios (MX+US en serie) mas el job de muestras (12:15 MX, 12:20
# US) antes de la estimacion.
LINEA_CRONTAB_PRECIO = (
    "10 13 * * * /usr/bin/flock -n /tmp/precio-spapi.lock"
    " docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx"
    " --platform amazon_us"
    " >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1"
)
LINEA_CRONTAB_PRECIO_REPASOS = (
    "10 15-23/2 * * * /usr/bin/flock -n /tmp/precio-spapi.lock"
    " docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx"
    " --platform amazon_us"
    " >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1"
)
LINEA_CRONTAB_MUESTRAS_MX = (
    "15 12 * * * docker exec orbit-app-1 python -m app.envio_muestras"
    " --platform amazon_mx --canal fbm"
    " >> /mnt/data/appdata/orbit/logs/precio-muestras.log 2>&1"
)
LINEA_CRONTAB_MUESTRAS_US = (
    "20 12 * * * docker exec orbit-app-1 python -m app.envio_muestras"
    " --platform amazon_us --canal fbm"
    " >> /mnt/data/appdata/orbit/logs/precio-muestras.log 2>&1"
)
# D.4: catalogo diario + muestras meli + 2 de precios MeLi (lock propio).
# Palabra por palabra del plan (guia "Despliegues", D.4).
LINEA_CRONTAB_MELI_CATALOGO = (
    "30 11 * * * docker exec orbit-app-1 python -m app.meli.catalogo"
    " >> /mnt/data/appdata/orbit/logs/meli-catalogo.log 2>&1"
)
LINEA_CRONTAB_MELI_MUESTRAS = (
    "25 12 * * * docker exec orbit-app-1 python -m app.envio_muestras"
    " --platform meli --canal meli"
    " >> /mnt/data/appdata/orbit/logs/precio-muestras.log 2>&1"
)
LINEA_CRONTAB_MELI_PRECIO = (
    "10 13 * * * /usr/bin/flock -n /tmp/precio-meli.lock"
    " docker exec orbit-app-1 python -m app.cli precio --platform meli"
    " >> /mnt/data/appdata/orbit/logs/precio-meli.log 2>&1"
)
LINEA_CRONTAB_MELI_PRECIO_REPASOS = (
    "10 15-23/2 * * * /usr/bin/flock -n /tmp/precio-meli.lock"
    " docker exec orbit-app-1 python -m app.cli precio --platform meli"
    " >> /mnt/data/appdata/orbit/logs/precio-meli.log 2>&1"
)


def test_linea_crontab_en_deploy():
    """`docs/DEPLOY.md` trae las cuatro lineas EXACTAS y los previos a instalarlas.

    D15: se afirma contra el doc, no contra las constantes (y B6: los
    renglones previos a instalar las lineas).
    """
    deploy = (RAIZ / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    assert LINEA_CRONTAB_PRECIO in deploy
    assert LINEA_CRONTAB_PRECIO_REPASOS in deploy
    assert LINEA_CRONTAB_MUESTRAS_MX in deploy
    assert LINEA_CRONTAB_MUESTRAS_US in deploy
    assert "10 13 * * *" in deploy
    assert "10 15-23/2 * * *" in deploy
    assert "15 12 * * *" in deploy
    assert "20 12 * * *" in deploy
    assert "--platform amazon_mx --platform amazon_us" in deploy
    assert "precio-spapi.lock" in deploy
    assert "flock" in deploy
    assert "precio-corrida.log" in deploy
    assert "precio-muestras.log" in deploy
    assert "claves `precio_*" in deploy
    assert "no pasa el entorno del host" in deploy
    assert "13:10 UTC" in deploy
    assert "no toca Amazon" in deploy
    assert "forma del parche sellada (A.4)" in deploy


def test_linea_crontab_meli_en_deploy():
    """`docs/DEPLOY.md` trae las CUATRO lineas MeLi EXACTAS (D.4).

    D15 espejo de `test_linea_crontab_en_deploy`: se afirma contra el
    doc; las constantes viven arriba, palabra por palabra del plan.
    """
    deploy = (RAIZ / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    assert LINEA_CRONTAB_MELI_CATALOGO in deploy
    assert LINEA_CRONTAB_MELI_MUESTRAS in deploy
    assert LINEA_CRONTAB_MELI_PRECIO in deploy
    assert LINEA_CRONTAB_MELI_PRECIO_REPASOS in deploy
    assert "30 11 * * *" in deploy
    assert "25 12 * * *" in deploy
    assert "--platform meli" in deploy
    assert "precio-meli.lock" in deploy
    assert "meli-catalogo.log" in deploy
    assert "precio-meli.log" in deploy
    assert "precio:meli" in deploy
    assert "11:30" in deploy
    assert "12:25" in deploy


def test_cli_precio_sin_dsn_fail_closed(monkeypatch, capsys):
    """Sin `ORBIT_DSN_DECIDE`, `precio` sale 2 sin tocar nada."""
    from app import cli as cli_mod

    monkeypatch.delenv("ORBIT_DSN_DECIDE", raising=False)
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo == 2
    assert "ORBIT_DSN_DECIDE" in capsys.readouterr().err


def test_cli_precio_despacha_corrida_e_imprime_resumen(monkeypatch, capsys):
    """`precio --platform` corre por el mismo camino e imprime el final."""
    from app import cli as cli_mod

    llamadas = {}

    def _falso_correr(conn, mercado, **kw):
        llamadas["mercado"] = mercado
        llamadas["kw"] = kw
        print("listing=1 amazon_mx shadow mantener en_tolerancia")
        return Resumen(decisiones=1, escritas=0, cerrados={}, huerfanas=0)

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
    monkeypatch.setattr(
        "app.precio_mercados.abrir",
        lambda clave, **kw: _nullcontext(
            (llamadas.setdefault("abrir", (clave, kw)) or "mercado-falso") and "mercado-falso"
        ),
    )
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo == 0
    assert llamadas["mercado"] == "mercado-falso"
    # D14: el mercado se abre con los clientes de `_clientes_precio` y el limitador.
    assert llamadas["abrir"][0] == "amazon_mx"
    assert llamadas["abrir"][1]["lector"] == "l"
    assert llamadas["abrir"][1]["escritor"] == "e"
    assert llamadas["abrir"][1]["fees"] == "f"
    assert llamadas["abrir"][1]["limitador"] == "cubo"
    assert "decisiones=1 escritas=0" in capsys.readouterr().out


def test_cli_precio_dos_platforms_corren_en_serie(monkeypatch, capsys):
    """F.4: `--platform mx --platform us` corre las dos, en orden."""
    from app import cli as cli_mod

    orden = []

    def _falso_correr(conn, mercado, **kw):
        orden.append(mercado)
        return Resumen(decisiones=1, escritas=0, cerrados={}, huerfanas=0)

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
    monkeypatch.setattr(
        "app.precio_mercados.abrir",
        lambda clave, **kw: _nullcontext(f"mercado-{clave}"),
    )
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx", "--platform", "amazon_us"])
    assert codigo == 0
    assert orden == ["mercado-amazon_mx", "mercado-amazon_us"]
    assert capsys.readouterr().out.count("decisiones=1 escritas=0") == 2


def test_cli_precio_lock_ocupado_sale_cero(monkeypatch, capsys):
    """Claim perdido: mensaje y exit 0 (el trabajo ya esta en curso)."""
    from app import cli as cli_mod

    def _ocioso(*a, **k):
        raise LockOcupado("precio:amazon_mx en curso (owner otro)")

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _ocioso)
    monkeypatch.setattr(
        "app.precio_mercados.abrir", lambda clave, **kw: _nullcontext("mercado-falso")
    )
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo == 0
    assert "en curso" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# r1 (BRIEF-r1): correcciones B, mutantes C y bajas K. Rojo primero en tdd.md.
# ---------------------------------------------------------------------------


def _cierra_goal(conn, lid, hoy):
    """Cierra la vigencia abierta (lo unico que el trigger permite tocar)."""
    conn.execute(
        "UPDATE precio_goal SET valid_to = %s WHERE listing_id = %s AND valid_to IS NULL",
        (hoy, lid),
    )


def _cambio_historia(conn, dec, lid, **kw):
    """Cambio con fecha pasada que referencia decision de otro listing."""
    conn.execute("ALTER TABLE precio_cambio DISABLE TRIGGER precio_cambio_coherente")
    try:
        return _cambio_con_estado(conn, dec, lid, **kw)
    finally:
        conn.execute("ALTER TABLE precio_cambio ENABLE TRIGGER precio_cambio_coherente")


def _andamio_hoy(conn, *, sku="SKU-AND", ext="B0A50000AN"):
    """Listing con goal live + decision de hoy: andamio para sembrar cambios con fecha.

    La decision de hoy lo excluye de la corrida (`decididas`).
    """
    lid = _listing(conn, _producto(conn, f"ODOO-{sku}"), ext=ext, sku=sku)
    _goal(conn, lid, mode="live")
    return lid, _decision_subir(conn, lid)


@_skip_sin_pg
def test_pricing_solo_fila_del_dia():
    """B.1: solo hay observacion de ayer: `no_evaluado(precio_sin_observar)`, sin cotizar."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _price_obs(conn, dia=hoy - timedelta(days=1))
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        assert _decision_unica(conn)[:2] == ("no_evaluado", "precio_sin_observar")
        assert red.n_fees == 0 and red.n_patch == 0


@_skip_sin_pg
def test_senal_sin_ventas_con_ledger_al_dia():
    """B.2: sin ventas y ledger al dia: no `ledger_hueco`, `n15 = 15`."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        otro = _producto(conn, "SKU-B2-OTRO")
        run_ledger, _fin = _run_ledger(conn)
        _ventas_parejas(conn, otro, qty=1, run_id=run_ledger)
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
            " product_id, quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, 200, 'MXN', %s)",
            (hoy - timedelta(days=1), datetime.now(UTC), otro, run_ledger),
        )
        for atraso in range(1, 16):
            dia = hoy - timedelta(days=atraso)
            mediodia = datetime(dia.year, dia.month, dia.day, 12, tzinfo=UTC)
            conn.execute(
                "INSERT INTO spapi_inventario_observation (seller_sku, platform,"
                " metric_date, observed_at, total_quantity, api_version)"
                " VALUES (%s, 'amazon_mx', %s, %s, 5, 'v1')",
                (datos["sku"], dia, mediodia),
            )
            conn.execute(
                "INSERT INTO spapi_listing_estado_observation (seller_sku, platform,"
                " status, api_version, observed_at)"
                " VALUES (%s, 'amazon_mx', 'BUYABLE', 'v1', %s)",
                (datos["sku"], mediodia),
            )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        assert _decision_unica(conn)[0] == "subir"
        n15 = conn.execute("SELECT n15 FROM precio_decision").fetchone()[0]
        assert n15 == 15


@_skip_sin_pg
def test_ledger_rezagado_da_ledger_hueco():
    """B.2: ledger de la plataforma hasta hace 5 dias (carga de ayer fallida) -> `ledger_hueco`."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _cierra_goal(conn, datos["listing"], hoy)
        _goal(conn, datos["listing"], mode="live", pct="0.10", desde=hoy)
        run_ok, _fin = _run_ledger(conn)
        for atraso, importe in ((6, "200"), (5, "201")):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
                " product_id, quantity, amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, %s, 'MXN', %s)",
                (
                    hoy - timedelta(days=atraso),
                    datetime.now(UTC),
                    datos["producto"],
                    importe,
                    run_ok,
                ),
            )
        run_malo, _fin2 = _run_ledger(conn, ok=False)
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
            " product_id, quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, 202, 'MXN', %s)",
            (hoy - timedelta(days=1), datetime.now(UTC), datos["producto"], run_malo),
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        assert _decision_unica(conn)[:2] == ("mantener", "ventas_sin_dato:ledger_hueco")
        assert red.n_fees == 0


@_skip_sin_pg
def test_relanzar_despues_de_morir_en_compuerta(monkeypatch):
    """B.3: muere en la compuerta (decidido, sin aplicar): el relanzamiento
    no decide y aplica lo pendiente, sin cotizar de nuevo."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})

        def _muere(*a, **k):
            raise RuntimeError("muere a media corrida")

        monkeypatch.setattr(corrida_mod, "_medir_compuerta", _muere)
        with pytest.raises(RuntimeError, match="muere a media corrida"):
            _corre(conn, red)
        assert _cuenta_decisiones(conn) == 1 and _cuenta_cambios(conn) == 0
        monkeypatch.undo()
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 1)
        assert _decision_unica(conn)[0] == "subir"
        assert conn.execute("SELECT count(*) FROM precio_cotizacion").fetchone()[0] == 2


@_skip_sin_pg
def test_falla_de_una_publicacion_no_tumba_a_otra():
    """B.4: sin `seller_sku` en una: la otra aplica y el error va al resumen."""
    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-F-A", ext="B0A50000FA", mode="live")
        b = _cadena(conn, sku="SKU-F-B", ext="B0A50000FB", mode="live")
        _price_obs(conn, asin="B0A50000FA")
        _price_obs(conn, asin="B0A50000FB")
        conn.execute("UPDATE listing SET seller_sku = NULL WHERE id = %s", (a["listing"],))
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 1)
        fila_a = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s", (a["listing"],)
        ).fetchone()
        assert fila_a[0] == "subir"
        fila_b = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s", (b["listing"],)
        ).fetchone()
        assert fila_b[0] == "subir"
        assert len(res.errores) == 1 and f"listing={a['listing']}" in res.errores[0]
        assert red.n_patch == 1


@_skip_sin_pg
def test_escenario_con_importe_no_numerico():
    """B.4: importe no numerico -> `no_evaluado(escenario_incoherente)`; el otro goal sigue."""
    with _db() as (conn, _dsn):
        malo = _cadena(conn, sku="SKU-E-A", ext="B0A50000EA", mode="live", costo="doce")
        bueno = _cadena(conn, sku="SKU-E-B", ext="B0A50000EB", mode="live")
        _price_obs(conn, asin="B0A50000EA")
        _price_obs(conn, asin="B0A50000EB")
        red = _RedFalsa(skus={malo["evento"]: malo["sku"], bueno["evento"]: bueno["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 1)
        fila_mala = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (malo["listing"],),
        ).fetchone()
        assert fila_mala == ("no_evaluado", "escenario_incoherente")
        fila_buena = conn.execute(
            "SELECT resultado FROM precio_decision WHERE listing_id = %s", (bueno["listing"],)
        ).fetchone()
        assert fila_buena[0] == "subir"


@_skip_sin_pg
def test_no_confirmado_frena():
    """B.5: ultimo cambio quedo `no_confirmado` -> `frenado(no_confirmado)`, sin PATCH."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-B5-B", ext="B0A50000B5")
        hace8 = datetime.now(UTC) - timedelta(days=8)
        _cambio_historia(conn, dec_b, datos["listing"], estado="no_confirmado", dia=hace8)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("frenado", "no_confirmado")
        assert red.n_patch == 0 and red.n_fees == 0


@_skip_sin_pg
def test_goal_nuevo_reanuda_tras_no_confirmado():
    """B.5: goal nuevo posterior al `no_confirmado` -> se evalua normal (guarda del reanude)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _cierra_goal(conn, datos["listing"], hoy - timedelta(days=5))
        _goal(conn, datos["listing"], mode="live", desde=hoy - timedelta(days=5))
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-B5-C", ext="B0A50000BC")
        hace8 = datetime.now(UTC) - timedelta(days=8)
        _cambio_historia(conn, dec_b, datos["listing"], estado="no_confirmado", dia=hace8)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        fila = conn.execute(
            "SELECT resultado FROM precio_decision WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()
        assert fila[0] == "subir"


@_skip_sin_pg
def test_freno_dos_dias_con_config_2():
    """B.6: `precio_freno_dias_error = 2` con dos dias de `error` -> `frenado(api_error)`."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _config(conn, {**SETTINGS, "precio_freno_dias_error": 2})
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-B6-B", ext="B0A50000B6")
        for atraso in (1, 2):
            _cambio_historia(
                conn,
                dec_b,
                datos["listing"],
                estado="error",
                dia=datetime.now(UTC) - timedelta(days=atraso),
                error="PATCH /x 500",
            )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("frenado", "api_error")
        assert red.n_patch == 0


@_skip_sin_pg
def test_cotizacion_con_reloj_de_amazon():
    """K1: el servidor estampa al recibir: `observed_at` se captura tras la respuesta."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red, fijar_fee=False)
        assert (res.decisiones, res.escritas) == (1, 1)
        assert _decision_unica(conn)[0] == "subir"


def test_cli_precio_conexion_caida_sin_traceback(monkeypatch, capsys):
    """K6: `connect` que falla en la corrida sale scrubbado y distinto de cero (como `_cycle`)."""
    from app import cli as cli_mod
    from app.redaction import register_secret

    register_secret("s3cr3t")

    def _cae(*a, **k):
        raise RuntimeError("boom secreto-s3cr3t")

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", _cae)
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo != 0
    err = capsys.readouterr().err
    assert "s3cr3t" not in err and "Traceback" not in err


def test_cli_precio_reporte_conexion_caida_sin_traceback(monkeypatch, capsys):
    """K6: `connect` que falla en `--reporte` sale scrubbado y distinto de cero."""
    from app import cli as cli_mod
    from app.redaction import register_secret

    register_secret("s3cr3t")

    def _cae(*a, **k):
        raise RuntimeError("boom secreto-s3cr3t")

    monkeypatch.setenv("ORBIT_DSN_READ", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "connect", _cae)
    codigo = cli_mod.main(["precio", "--reporte", "--desde", "2026-09-01", "--hasta", "2026-09-17"])
    assert codigo != 0
    err = capsys.readouterr().err
    assert "s3cr3t" not in err and "Traceback" not in err


def test_cli_precio_reporte_ventana_invertida(monkeypatch, capsys):
    """K8b: `--reporte` con `--desde` posterior a `--hasta` -> exit 2 con mensaje."""
    from app import cli as cli_mod

    codigo = cli_mod.main(["precio", "--reporte", "--desde", "2026-09-17", "--hasta", "2026-09-01"])
    assert codigo == 2
    assert "invertida" in capsys.readouterr().err


def test_cli_precio_imprime_escritas_reales(monkeypatch, capsys):
    """LA-cli-escritas: el CLI imprime el `escritas` real de `correr` (no un fijo)."""
    from app import cli as cli_mod

    def _falso_correr(conn, mercado, **kw):
        return Resumen(decisiones=3, escritas=2, cerrados={}, huerfanas=0)

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
    monkeypatch.setattr(
        "app.precio_mercados.abrir", lambda clave, **kw: _nullcontext("mercado-falso")
    )
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo == 0
    assert "decisiones=3 escritas=2" in capsys.readouterr().out


@_skip_sin_pg
def test_huerfana_otras_plataformas_intactas():
    """LA-huerf-plataforma: la corrida de `amazon_mx` no toca una `pendiente` de `amazon_us`."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        prod_u = _producto(conn, "SKU-HU-U")
        lid_u = _listing(conn, prod_u, platform="amazon_us", ext="B0A50000HU", sku="SKU-HU-U")
        _goal(conn, lid_u, platform="amazon_us", mode="live")
        dec_u = _decision_subir(conn, lid_u, platform="amazon_us")
        cid_u = conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at)"
            " VALUES (%s, %s, 'amazon_us', 100, 'MXN', 110, 'MXN', true,"
            " 'pendiente', now()) RETURNING id",
            (dec_u, lid_u),
        ).fetchone()[0]
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        assert res.huerfanas == 0
        estado = conn.execute(
            "SELECT estado FROM precio_cambio WHERE id = %s", (cid_u,)
        ).fetchone()[0]
        assert estado == "pendiente"


@_skip_sin_pg
def test_freno_solo_cuenta_error():
    """LA-freno-estado: otro estado no frena (3 `confirmado` -> `mantener(cooldown)`)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        andamios = (
            ("SKU-FE-1", "B0A50000F1", "0.10", 3),
            ("SKU-FE-2", "B0A50000F2", "0.20", 2),
            ("SKU-FE-3", "B0A50000F3", "0.25", 1),
        )
        for sku, ext, m, atraso in andamios:
            lid_i = _listing(conn, _producto(conn, f"ODOO-{sku}"), ext=ext, sku=sku)
            _goal(conn, lid_i, mode="live")
            dec = _decision_subir(conn, lid_i, m=m)
            _cambio_historia(
                conn,
                dec,
                datos["listing"],
                estado="confirmado",
                dia=datetime.now(UTC) - timedelta(days=atraso),
            )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("mantener", "cooldown")


@_skip_sin_pg
def test_goal_cerrado_hoy_no_se_decide():
    """LA-goal-cerrado-hoy: `valid_to = hoy` no se decide."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _goal(conn, lid, mode="live", hasta=hoy)
        red = _RedFalsa()
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert _cuenta_decisiones(conn) == 0


@_skip_sin_pg
def test_goal_futuro_no_se_decide():
    """LA-goal-futuro: `valid_from` manana no se decide."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _goal(conn, lid, mode="live", desde=hoy + timedelta(days=1))
        red = _RedFalsa()
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert _cuenta_decisiones(conn) == 0
        # Regresion de r3: si el goal futuro entrara, la decision no se
        # podria persistir (sin moneda observable) y quedaria el error.
        assert res.errores == ()
        assert res.lineas == ()


@_skip_sin_pg
def test_reversa_confirmada_no_enfria():
    """LA-previos-reversas: reversa confirmada ayer no pone al par en cooldown (A.2 R14)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-PR-B", ext="B0A50000PR")
        hace10 = datetime.now(UTC) - timedelta(days=10)
        orig = _cambio_historia(conn, dec_b, datos["listing"], estado="confirmado", dia=hace10)
        conn.execute("ALTER TABLE precio_cambio DISABLE TRIGGER precio_cambio_coherente")
        try:
            rev = conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
                " estado, enviado_at, es_reversa, reversa_de)"
                " VALUES (NULL, %s, 'amazon_mx', 110, 'MXN', 100, 'MXN', true,"
                " 'pendiente', %s, true, %s) RETURNING id",
                (datos["listing"], datetime.now(UTC) - timedelta(days=1), orig),
            ).fetchone()[0]
        finally:
            conn.execute("ALTER TABLE precio_cambio ENABLE TRIGGER precio_cambio_coherente")
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', ack = '{\"http_status\": 202}'::jsonb"
            " WHERE id = %s",
            (rev,),
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (rev,),
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        fila = conn.execute(
            "SELECT resultado FROM precio_decision WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()
        assert fila[0] == "subir"


@_skip_sin_pg
def test_racha_previa_entra_a_la_senal():
    """LA-racha-previa: racha 2 ayer + perdiendo hoy = 3 en la decision."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        ayer = hoy - timedelta(days=1)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        try:
            conn.execute(
                "INSERT INTO precio_decision (listing_id, platform, resultado, mode,"
                " goal, m_actual, p_actual, p_actual_currency, p_objetivo,"
                " p_objetivo_currency, p_aplicado, p_aplicado_currency, i_valor, i_currency,"
                " c_valor, c_currency, f_valor, f_currency, l_valor, l_currency,"
                " r_valor, r_currency, decision_date, racha_senal,"
                " l_origen, verificacion)"
                " VALUES (%s, 'amazon_mx', 'subir', 'live', 0.30, 0.25,"
                " 100, 'MXN', 110, 'MXN', 110, 'MXN', 10, 'MXN', 20, 'MXN',"
                " 12, 'MXN', 5, 'MXN', 8, 'MXN', %s, 2,"
                " 'politica', 'cotizada')",
                (datos["listing"], ayer),
            )
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        run_ledger, _fin = _run_ledger(conn)
        _ventas_perdiendo(
            conn,
            datos["producto"],
            sku=datos["sku"],
            run_id=run_ledger,
            lid=datos["listing"],
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        racha = conn.execute(
            "SELECT racha_senal FROM precio_decision WHERE listing_id = %s AND decision_date = %s",
            (datos["listing"], hoy),
        ).fetchone()[0]
        assert racha == 3


@_skip_sin_pg
def test_buybox_de_la_observacion_va_a_la_decision():
    """LA-buybox: `buy_box_is_own` de la observacion del dia queda en la decision."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn, buybox=False)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        buybox = conn.execute(
            "SELECT buy_box_is_own FROM precio_decision WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()[0]
        assert buybox is False


@_skip_sin_pg
def test_live_saltado_no_cuenta_escrita():
    """LA-escritas-cuenta: un `live` saltado (vivo distinto) no cuenta como escrita."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]}, precio_vivo="999.00")
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        assert _decision_unica(conn)[0] == "subir"
        assert _cuenta_cambios(conn) == 0


# ---------------------------------------------------------------------------
# r2 (BRIEF-r2 sobre 052aa42)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_parche_sin_sellar_persiste_filas_sin_reintentar(monkeypatch):
    """B.1: el goal saltado por parche sin sellar deja su fila del dia.

    Un solo intento de PATCH en toda la corrida (el segundo goal se decide
    pero no toca la red) y dos `precio_decision` del dia.
    """
    import app.precio.cambios as cambios_mod
    from app.precio.cambios import aplicar as _aplicar_real
    from app.precio.puerto import EscrituraNoDisponible

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-R2-A", ext="B0A50000RA", mode="live")
        b = _cadena(conn, sku="SKU-R2-B", ext="B0A50000RB", mode="live")
        _price_obs(conn, asin="B0A50000RA")
        _price_obs(conn, asin="B0A50000RB")
        llamadas = []

        def _una_vez(*args, **kwargs):
            llamadas.append(1)
            if len(llamadas) == 1:
                raise EscrituraNoDisponible("forma sin sello")
            return _aplicar_real(*args, **kwargs)

        monkeypatch.setattr(cambios_mod, "aplicar", _una_vez)
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})
        res = _corre(conn, red)
        assert len(llamadas) == 1
        assert _cuenta_decisiones(conn) == 2
        assert (res.decisiones, res.escritas) == (2, 0)
        assert any("parche_sin_sellar" in e for e in res.errores)


@_skip_sin_pg
def test_decision_no_persistida_no_cuenta_ni_linea():
    """B.2: lo que no dejo fila no suma en `decisiones` ni sale en lineas."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        _goal(conn, lid, mode="live")
        # Sin ofertas en la plataforma: `moneda_contexto` no halla moneda.
        red = _RedFalsa()
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert res.lineas == ()
        assert len(res.errores) == 1
        assert _cuenta_decisiones(conn) == 0


@_skip_sin_pg
def test_avisar_recibe_conexion_plataforma_hoy_y_resumen():
    """B.3: el gancho se llama una vez al terminar de aplicar, con los cuatro args."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        llamadas = []

        def _avisar(c, p, h, r):
            llamadas.append((c, p, h, r))

        res = _corre(conn, red, avisar=_avisar)
        assert len(llamadas) == 1
        c, p, h, r = llamadas[0]
        assert c is conn and p == "amazon_mx" and r == res
        hoy_fila = conn.execute("SELECT decision_date FROM precio_decision").fetchone()[0]
        assert h == hoy_fila


@_skip_sin_pg
def test_avisar_que_levanta_no_tumba_corrida():
    """B.3: un gancho que falla se registra y la corrida devuelve su resumen."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})

        def _avisar_malo(*a):
            raise RuntimeError("sender caido")

        res = _corre(conn, red, avisar=_avisar_malo)
        assert (res.decisiones, res.escritas) == (1, 1)
        assert _cuenta_decisiones(conn) == 1
        assert any("avisar" in e for e in res.errores)


@_skip_sin_pg
def test_relanzar_con_otro_pedido_no_reusa_cotizacion(monkeypatch):
    """R1-reuso-sin-guarda: con otro P* no se reusa: `no_evaluado`, sin mezcla."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})

        def _muere(*a, **k):
            # S.1: esta prueba exige morir ANTES de persistir (morir en la
            # compuerta dejaria fila y el UNIQUE impediria re-decidir con
            # el goal nuevo), asi que el sabotaje va en persistir.
            raise RuntimeError("muere a media corrida")

        monkeypatch.setattr(corrida_mod, "_persistir_uno", _muere)
        with pytest.raises(RuntimeError, match="muere a media corrida"):
            _corre(conn, red)
        assert _cuenta_decisiones(conn) == 0
        monkeypatch.undo()
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _cierra_goal(conn, datos["listing"], hoy)
        _goal(conn, datos["listing"], mode="live", pct="0.35", desde=hoy)
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        assert res.lineas == (
            f"listing={datos['listing']} amazon_mx live no_evaluado escenario_incoherente",
        )
        assert res.errores == ()


@_skip_sin_pg
def test_error_de_base_en_aplicar_aborta_y_libera_lock(monkeypatch):
    """R1-aplicar-traga-db: un `psycopg.Error` sale de `correr` y libera el lock."""
    from psycopg.errors import OperationalError

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-R2-C", ext="B0A50000RC", mode="live")
        b = _cadena(conn, sku="SKU-R2-D", ext="B0A50000RD", mode="live")
        _price_obs(conn, asin="B0A50000RC")
        _price_obs(conn, asin="B0A50000RD")
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})

        def _base_caida(*args, **kwargs):
            raise OperationalError("conexion perdida")

        import app.precio.cambios as cambios_mod

        monkeypatch.setattr(cambios_mod, "aplicar", _base_caida)
        with pytest.raises(OperationalError):
            _corre(conn, red)
        monkeypatch.undo()
        res = _corre(conn, red)
        # S.1: decidir ya persistio las 2; el relanzamiento solo aplica.
        assert (res.decisiones, res.escritas) == (0, 2)


@_skip_sin_pg
def test_goal_reeditado_a_media_corrida_sale_goal_cambiado():
    """R02-r1 (CodeRabbit): el goal se cierra justo antes de persistir: la
    publicacion no deja fila, el error `goal_cambiado` va al resumen y la
    corrida sigue (no aborta)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        import app.precio.corrida as corrida_mod

        real = corrida_mod.persistir_decision

        def _reedita(*args, **kwargs):
            conn.execute(
                "UPDATE precio_goal SET valid_to = %s WHERE listing_id = %s AND valid_to IS NULL",
                (hoy, datos["listing"]),
            )
            return real(*args, **kwargs)

        import unittest.mock as _mock

        with _mock.patch.object(corrida_mod, "persistir_decision", _reedita):
            res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert _cuenta_decisiones(conn) == 0
        assert len(res.errores) == 1 and "goal_cambiado" in res.errores[0]


@_skip_sin_pg
def test_decidir_aisla_typeerror_de_publicacion(monkeypatch):
    """R02-r1 (CodeRabbit): un `TypeError` en insumos tambien aisla la
    publicacion (B1 cubre toda `Exception` no-psycopg)."""
    import app.spapi.precio_mercado as mercado_mod

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-R3-T", ext="B0A50000RA", mode="live")
        b = _cadena(conn, sku="SKU-R3-U", ext="B0A50000RB", mode="live")
        _price_obs(conn, asin="B0A50000RA")
        _price_obs(conn, asin="B0A50000RB")
        real = mercado_mod.evaluar_senal
        llamadas = []

        def _revienta(insumos, **kw):
            llamadas.append(1)
            if len(llamadas) == 1:
                raise TypeError("venta rota")
            return real(insumos, **kw)

        monkeypatch.setattr(mercado_mod, "evaluar_senal", _revienta)
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 1)
        fila_a = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s", (a["listing"],)
        ).fetchone()
        assert fila_a == ("no_evaluado", "escenario_incoherente")
        assert len(res.errores) == 1


@_skip_sin_pg
def test_no_confirmado_viejo_mas_confirmado_nuevo_no_frena():
    """R1-noconf-orden: manda el ultimo: viejo sin confirmar + nuevo confirmado."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-R2-E", ext="B0A50000RE")
        ahora = datetime.now(UTC)
        # Viejos (>22 dias: fuera de `perdiendo_tras_subida` y de cooldown).
        _cambio_historia(
            conn, dec_b, datos["listing"], estado="no_confirmado", dia=ahora - timedelta(days=25)
        )
        _cambio_historia(
            conn, dec_b, datos["listing"], estado="confirmado", dia=ahora - timedelta(days=23)
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        fila = conn.execute(
            "SELECT resultado FROM precio_decision WHERE listing_id = %s", (datos["listing"],)
        ).fetchone()
        assert fila[0] == "subir"


@_skip_sin_pg
def test_cobertura_ignora_ledger_de_otra_fuente():
    """R1-cubierto-sin-source: venta de un run `ok` de otra fuente no cubre."""
    from app.precio.puerto import Miembro, UnidadPrecio
    from app.spapi.precio_mercado import historia_unidad

    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod, ext="B0A50000R2", sku="SKU-R2-X")
        run_costos, _fin = _run(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
            " product_id, quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, 200, 'MXN', %s)",
            (hoy - timedelta(days=1), datetime.now(UTC), prod, run_costos),
        )
        unidad = UnidadPrecio(
            listing_id=lid,
            plataforma="amazon_mx",
            externo="B0A50000R2",
            canal=None,
            producto_ancla=prod,
            miembros=(Miembro(prod, "SKU-R2-X", None),),
        )
        historia = historia_unidad(conn, unidad=unidad, canal="fba", hoy=hoy)
        assert historia.ledger_cubierto_hasta is None


def test_cli_precio_imprime_errores_en_stderr(monkeypatch, capsys):
    """R1-cli-errores: cada error del resumen sale en stderr, con `scrub`."""
    from app import cli as cli_mod
    from app.redaction import register_secret

    register_secret("s3cr3t-r2")

    def _falso_correr(conn, mercado, **kw):
        return Resumen(
            decisiones=1,
            escritas=0,
            cerrados={},
            huerfanas=0,
            errores=("listing=1 amazon_mx parche_sin_sellar: s3cr3t-r2",),
        )

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
    monkeypatch.setattr(
        "app.precio_mercados.abrir", lambda clave, **kw: _nullcontext("mercado-falso")
    )
    cli_mod.main(["precio", "--platform", "amazon_mx"])
    err = capsys.readouterr().err
    assert "listing=1 amazon_mx" in err and "s3cr3t-r2" not in err


def test_cli_precio_con_errores_sale_distinto_de_cero(monkeypatch, capsys):
    """K1: con `errores` en el resumen el CLI sale distinto de cero."""
    from app import cli as cli_mod

    def _falso_correr(conn, mercado, **kw):
        return Resumen(
            decisiones=1,
            escritas=0,
            cerrados={},
            huerfanas=0,
            errores=("listing=1 amazon_mx boom",),
        )

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
    monkeypatch.setattr(
        "app.precio_mercados.abrir", lambda clave, **kw: _nullcontext("mercado-falso")
    )
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo != 0
    assert "decisiones=1 escritas=0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# r3 (BRIEF-r3 sobre 650e51d)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_decidir_aisla_error_de_publicacion(monkeypatch):
    """B1: un `ValueError` al decidir deja `no_evaluado` y la corrida sigue."""
    import app.spapi.precio_mercado as mercado_mod

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-R3-A", ext="B0A50000RA", mode="live")
        b = _cadena(conn, sku="SKU-R3-B", ext="B0A50000RB", mode="live")
        _price_obs(conn, asin="B0A50000RA")
        _price_obs(conn, asin="B0A50000RB")
        real = mercado_mod.evaluar_senal
        llamadas = []

        def _revienta(insumos, **kw):
            llamadas.append(1)
            if len(llamadas) == 1:
                raise ValueError("venta invalida en 2026-09-01: -1")
            return real(insumos, **kw)

        monkeypatch.setattr(mercado_mod, "evaluar_senal", _revienta)
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 1)
        fila_a = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s", (a["listing"],)
        ).fetchone()
        assert fila_a == ("no_evaluado", "escenario_incoherente")
        fila_b = conn.execute(
            "SELECT resultado FROM precio_decision WHERE listing_id = %s", (b["listing"],)
        ).fetchone()
        assert fila_b[0] == "subir"
        assert len(res.errores) == 1


@_skip_sin_pg
def test_errores_guardan_scrub(monkeypatch, caplog):
    """B2: el secreto no sale ni en el log, ni en `errores`, ni en el gancho."""
    import logging

    import app.precio.corrida as corrida_mod
    from app.redaction import register_secret

    register_secret("s3cr3t-r3")
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)

        def _revienta(*a, **k):
            raise RuntimeError("boom s3cr3t-r3")

        monkeypatch.setattr(corrida_mod, "cotizar_y_decidir", _revienta)
        avisos = []
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        with caplog.at_level(logging.ERROR, logger="app.precio.corrida"):
            res = _corre(conn, red, avisar=lambda c, p, h, r: avisos.append(r))
        assert (res.decisiones, res.escritas) == (1, 0)
        assert len(res.errores) == 1
        texto_log = "\n".join(r.getMessage() for r in caplog.records)
        assert "s3cr3t-r3" not in texto_log
        assert "s3cr3t-r3" not in "\n".join(res.errores)
        assert len(avisos) == 1
        assert "s3cr3t-r3" not in "\n".join(avisos[0].errores)


@_skip_sin_pg
def test_freno_persiste_tras_dia_frenado():
    """B3: racha vieja sin errores nuevos igual frena (dia 5)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-R3-C", ext="B0A50000RC")
        ahora = datetime.now(UTC)
        for atraso in (2, 3, 4):
            _cambio_historia(
                conn,
                dec_b,
                datos["listing"],
                estado="error",
                dia=ahora - timedelta(days=atraso),
                error="PATCH /x 500",
            )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("frenado", "api_error")
        assert red.n_patch == 0


@_skip_sin_pg
def test_freno_dia4_frena():
    """B3: racha -1,-2,-3: el dia 4 frena (pin; el dia 5 lo cubre el test rojo)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-R3-H", ext="B0A50000RH")
        ahora = datetime.now(UTC)
        for atraso in (1, 2, 3):
            _cambio_historia(
                conn,
                dec_b,
                datos["listing"],
                estado="error",
                dia=ahora - timedelta(days=atraso),
                error="PATCH /x 500",
            )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("frenado", "api_error")
        assert red.n_patch == 0


@_skip_sin_pg
def test_escenario_no_disponible_da_motivo():
    """B4: escenario `incompleta` con fee NULL: sale su motivo, sin mas lecturas."""
    with _db() as (conn, _dsn):
        prod = _producto(conn, sku="ODOO-SKU-R3-D")
        lid = _listing(conn, prod, ext="B0A50000RD", sku="SKU-R3-D")
        _config(conn)
        _goal(conn, lid, mode="live")
        marca = datetime.now(UTC)
        oferta = _oferta_obs(
            conn,
            lid,
            ext="B0A50000RD",
            sku="SKU-R3-D",
            huella="ctx-R3-D",
            fetched=marca,
            observed=marca,
        )
        pol = _politica(conn)
        costo_id = _costo(conn, prod)
        run_id, validada_en = _run(conn)
        _escenario(
            conn,
            lid,
            oferta,
            None,
            pol,
            costo_id,
            run_id,
            validada_en,
            ext="B0A50000RD",
            sku="SKU-R3-D",
            huella="ctx-R3-D",
            estado="incompleta",
            motivos=["fee_ausente"],
        )
        _price_obs(conn, asin="B0A50000RD")
        red = _RedFalsa(skus={"oferta-SKU-R3-D": "SKU-R3-D"})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s", (lid,)
        ).fetchone()
        assert fila == ("no_evaluado", "fee_ausente")
        assert red.n_fees == 0 and red.n_patch == 0


@_skip_sin_pg
def test_escenario_sin_motivos_da_desconocido():
    """B4: `incompleta` sin motivos: `estimacion_motivo_desconocido`."""
    with _db() as (conn, _dsn):
        prod = _producto(conn, sku="ODOO-SKU-R3-E")
        lid = _listing(conn, prod, ext="B0A50000RE", sku="SKU-R3-E")
        _config(conn)
        _goal(conn, lid, mode="live")
        marca = datetime.now(UTC)
        oferta = _oferta_obs(
            conn,
            lid,
            ext="B0A50000RE",
            sku="SKU-R3-E",
            huella="ctx-R3-E",
            fetched=marca,
            observed=marca,
        )
        pol = _politica(conn)
        costo_id = _costo(conn, prod)
        run_id, validada_en = _run(conn)
        _escenario(
            conn,
            lid,
            oferta,
            None,
            pol,
            costo_id,
            run_id,
            validada_en,
            ext="B0A50000RE",
            sku="SKU-R3-E",
            huella="ctx-R3-E",
            estado="incompleta",
        )
        _price_obs(conn, asin="B0A50000RE")
        red = _RedFalsa(skus={"oferta-SKU-R3-E": "SKU-R3-E"})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s", (lid,)
        ).fetchone()
        assert fila == ("no_evaluado", "estimacion_motivo_desconocido")


def test_cli_precio_fechas_sin_reporte_es_uso(monkeypatch, capsys):
    """B5: `--desde/--hasta` sin `--reporte` = exit 2 antes de conectar."""
    from app import cli as cli_mod

    llamadas = []

    def _no_conecta():
        llamadas.append(1)
        raise AssertionError("no debio conectar")

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", _no_conecta)
    codigo = cli_mod.main(
        ["precio", "--platform", "amazon_mx", "--desde", "2026-09-01", "--hasta", "2026-09-02"]
    )
    assert codigo == 2
    assert llamadas == []
    assert "reporte" in capsys.readouterr().err


@_skip_sin_pg
def test_motivo_fuera_de_vocabulario_da_desconocido():
    """R3-nodisp-vocab: motivo raro sale `estimacion_motivo_desconocido`."""
    with _db() as (conn, _dsn):
        prod = _producto(conn, sku="ODOO-SKU-R4-A")
        lid = _listing(conn, prod, ext="B0A50000RA", sku="SKU-R4-A")
        _config(conn)
        _goal(conn, lid, mode="live")
        marca = datetime.now(UTC)
        oferta = _oferta_obs(
            conn,
            lid,
            ext="B0A50000RA",
            sku="SKU-R4-A",
            huella="ctx-R4-A",
            fetched=marca,
            observed=marca,
        )
        pol = _politica(conn)
        costo_id = _costo(conn, prod)
        run_id, validada_en = _run(conn)
        _escenario(
            conn,
            lid,
            oferta,
            None,
            pol,
            costo_id,
            run_id,
            validada_en,
            ext="B0A50000RA",
            sku="SKU-R4-A",
            huella="ctx-R4-A",
            estado="incompleta",
            motivos=["motivo_raro_zzz"],
        )
        _price_obs(conn, asin="B0A50000RA")
        red = _RedFalsa(skus={"oferta-SKU-R4-A": "SKU-R4-A"})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s", (lid,)
        ).fetchone()
        assert fila == ("no_evaluado", "estimacion_motivo_desconocido")


@_skip_sin_pg
def test_decidir_error_de_base_aborta_y_libera_lock(monkeypatch):
    """R3-decidir-traga-db: `psycopg.Error` al decidir sale y libera el lock."""
    from psycopg.errors import OperationalError

    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})

        def _base_caida(*args, **kwargs):
            raise OperationalError("conexion perdida")

        for punto in ("cotizar_y_decidir",):
            monkeypatch.setattr(corrida_mod, punto, _base_caida)
            with pytest.raises(OperationalError):
                _corre(conn, red)
            monkeypatch.undo()
        import app.spapi.precio_mercado as mercado_mod

        monkeypatch.setattr(
            mercado_mod.MercadoAmazon,
            "insumos",
            lambda *a, **k: (_ for _ in ()).throw(OperationalError("conexion perdida")),
        )
        with pytest.raises(OperationalError):
            _corre(conn, red)
        monkeypatch.undo()
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)


@_skip_sin_pg
def test_sin_autocommit_aborta_antes_del_lock():
    """R3-autocommit: sin autocommit, `ValueError` antes de tomar el lock."""
    import psycopg as _psycopg

    from app.precio.corrida import correr

    with _db() as (conn, dsn):
        _config(conn)
        cruda = _psycopg.connect(dsn)
        try:
            # El mensaje nombra a `correr` (el de `aplicar` llega
            # tarde, al aplicar: con el chequeo quitado el test muere).
            with pytest.raises(ValueError, match="correr exige"):
                correr(cruda, _mercado(_RedFalsa()), owner="tdd")
        finally:
            cruda.close()
        red = _RedFalsa()
        assert _corre(conn, red).decisiones == 0


@_skip_sin_pg
def test_reporte_con_fallo_sale_1_scrubbeado(monkeypatch, capsys):
    """R3-reporte-scrub: fallo en `reporte` = exit 1, scrubbeado, sin traceback."""
    import app.precio.corrida as corrida_mod
    from app import cli as cli_mod
    from app.redaction import register_secret

    register_secret("s3cr3t-r4")

    def _reporte_roto(*a, **k):
        raise RuntimeError("boom s3cr3t-r4")

    with _db() as (conn, _dsn):
        monkeypatch.setattr(corrida_mod, "reporte", _reporte_roto)
        monkeypatch.setenv("ORBIT_DSN_READ", "postgresql://falso/db")
        monkeypatch.setattr(cli_mod, "connect", lambda *a, **k: conn)
        codigo = cli_mod.main(
            [
                "precio",
                "--reporte",
                "--platform",
                "amazon_mx",
                "--desde",
                "2026-09-01",
                "--hasta",
                "2026-09-02",
            ]
        )
        assert codigo == 1
        err = capsys.readouterr().err
        assert "s3cr3t-r4" not in err
        assert "Traceback" not in err


@_skip_sin_pg
def test_dia_de_estado_es_utc():
    """R3-utc-dia: con la sesion en otra zona, el dia de estado es UTC."""
    from app.precio.puerto import Miembro, UnidadPrecio
    from app.spapi.precio_mercado import historia_unidad

    with _db() as (conn, _dsn):
        prod = _producto(conn, sku="ODOO-SKU-R4-U")
        lid = _listing(conn, prod, ext="B0A50000RU", sku="SKU-R4-U")
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        madrugada = datetime(hoy.year, hoy.month, hoy.day, 1, 0, tzinfo=UTC)
        conn.execute("SET TIME ZONE 'America/Mexico_City'")
        conn.execute(
            "INSERT INTO spapi_listing_estado_observation (seller_sku, platform,"
            " status, api_version, observed_at)"
            " VALUES (%s, 'amazon_mx', 'BUYABLE', 'v1', %s)",
            ("SKU-R4-U", madrugada),
        )
        unidad = UnidadPrecio(
            listing_id=lid,
            plataforma="amazon_mx",
            externo="B0A50000RU",
            canal=None,
            producto_ancla=prod,
            miembros=(Miembro(prod, "SKU-R4-U", None),),
        )
        historia = historia_unidad(conn, unidad=unidad, canal="fbm", hoy=hoy)
        assert [(d.dia, d.disponibilidad) for d in historia.dias] == [(hoy, "disponible")]


@_skip_sin_pg
def test_latido_por_goal():
    """K2: con dos goals, `heartbeat_at` queda posterior al `claimed_at`."""
    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-R4-LA", ext="B0A50000LA", mode="live")
        b = _cadena(conn, sku="SKU-R4-LB", ext="B0A50000LB", mode="live")
        _price_obs(conn, asin="B0A50000LA")
        _price_obs(conn, asin="B0A50000LB")
        vistos = []

        def _avisar(c, p, h, r):
            fila = c.execute(
                "SELECT claimed_at, heartbeat_at FROM ads_optimizer_lock"
                " WHERE job_key = 'precio:amazon_mx'"
            ).fetchone()
            vistos.append(fila)

        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})
        res = _corre(conn, red, avisar=_avisar)
        assert (res.decisiones, res.escritas) == (2, 2)
        assert len(vistos) == 1
        assert vistos[0][1] is not None and vistos[0][1] > vistos[0][0]


@_skip_sin_pg
def test_advisory_ajeno_no_espera():
    """K2: con el advisory en otra sesion, `LockOcupado` sin esperar."""
    import time as _time

    import psycopg as _psycopg

    with _db() as (conn, dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        otra = _psycopg.connect(dsn, autocommit=True)
        try:
            otra.execute("SELECT pg_advisory_lock(hashtext('precio:amazon_mx'))")
            red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
            resultados = []

            def _hilo():
                try:
                    resultados.append(_corre(conn, red, owner="tdd-adv"))
                except LockOcupado:
                    resultados.append("ocupado")

            hilo = threading.Thread(target=_hilo)
            inicio = _time.monotonic()
            hilo.start()
            hilo.join(timeout=30)
            assert not hilo.is_alive()
            assert resultados == ["ocupado"]
            assert _time.monotonic() - inicio < 30
        finally:
            otra.close()


@_skip_sin_pg
def test_soltar_que_falla_no_enmascara(monkeypatch, caplog):
    """K4: si `_soltar_lock` revienta en el `finally`, sale la original."""
    import logging

    import app.precio.corrida as corrida_mod
    from app.redaction import register_secret

    register_secret("s3cr3t-r4b")
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)

        def _soltar_roto(*a):
            raise RuntimeError("soltar caido s3cr3t-r4b")

        def _muere(*a, **k):
            raise RuntimeError("falla madre")

        monkeypatch.setattr(corrida_mod, "_soltar_lock", _soltar_roto)
        monkeypatch.setattr(corrida_mod, "_medir_compuerta", _muere)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        with (
            caplog.at_level(logging.ERROR, logger="app.precio.corrida"),
            pytest.raises(RuntimeError, match="falla madre"),
        ):
            _corre(conn, red)
        texto = "\n".join(r.getMessage() for r in caplog.records)
        assert "s3cr3t-r4b" not in texto
        assert "soltar lock" in texto


def test_diagnostico_con_secreto_no_sale_en_log(caplog):
    """K6: el diagnostico de `_no_evaluado`/`_frenado` va con `scrub`."""
    import logging
    from decimal import Decimal

    from app.precio.corrida import _frenado, _no_evaluado
    from app.redaction import register_secret

    register_secret("s3cr3t-r4c")
    with caplog.at_level(logging.INFO, logger="app.precio.corrida"):
        _no_evaluado(Decimal("0.30"), "live", "precio_ausente", "diag s3cr3t-r4c")
        _frenado(Decimal("0.30"), "live", "api_error", "diag s3cr3t-r4c")
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "s3cr3t-r4c" not in texto


@_skip_sin_pg
def test_config_invalida_sale_2(monkeypatch, capsys):
    """K8: freno fuera de cota al arrancar = exit 2 que nombra la clave."""
    from app import cli as cli_mod

    with _db() as (conn, _dsn):
        _config(conn, {**SETTINGS, "precio_freno_dias_error": 99})
        monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
        monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: (conn, lambda: None))
        monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
        monkeypatch.setattr(
            "app.precio_mercados.abrir",
            lambda clave, **kw: _nullcontext(_mercado(_RedFalsa())),
        )
        codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
        assert codigo == 2
        assert "precio_freno_dias_error" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# r5: latidos del claim (K2) — un latido por goal al decidir y uno por
# pendiente al aplicar, fail-open con warning scrubbeado.
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_latido_decidir_late_un_goal(monkeypatch):
    """R4-latido-decidir: el claim late una vez por goal al decidir.

    La compuerta saboteada corta la corrida tras decidir: con dos goals
    vivos los latidos de decidir son exactamente 2.
    """
    import app.precio.corrida as corrida_mod

    class _CorteFase1(Exception):
        pass

    with _db() as (conn, _dsn):
        ua = _cadena(conn, sku="SKU-LT-A", ext="B0A50000LA", mode="live")
        ub = _cadena(conn, sku="SKU-LT-B", ext="B0A50000LB", mode="live")
        _price_obs(conn, asin="B0A50000LA")
        _price_obs(conn, asin="B0A50000LB")
        latidos = []
        real = corrida_mod._latir_claim

        def _espia(conn_lat, platform, owner):
            latidos.append(platform)
            return real(conn_lat, platform, owner)

        def _corte(*a, **k):
            raise _CorteFase1("corte tras decidir")

        monkeypatch.setattr(corrida_mod, "_latir_claim", _espia)
        monkeypatch.setattr(corrida_mod, "_medir_compuerta", _corte)
        red = _RedFalsa(skus={ua["evento"]: ua["sku"], ub["evento"]: ub["sku"]})
        with pytest.raises(_CorteFase1):
            _corre(conn, red)
        assert latidos == ["amazon_mx", "amazon_mx"]


@_skip_sin_pg
def test_latido_aplicar_late_un_goal(monkeypatch):
    """R4-latido-aplicar: el claim late una vez por pendiente al aplicar.

    Corrida completa de dos goals: 2 latidos de decidir + 2 de aplicar.
    """
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        ua = _cadena(conn, sku="SKU-LT-C", ext="B0A50000LC", mode="live")
        ub = _cadena(conn, sku="SKU-LT-D", ext="B0A50000LD", mode="live")
        _price_obs(conn, asin="B0A50000LC")
        _price_obs(conn, asin="B0A50000LD")
        latidos = []
        real = corrida_mod._latir_claim

        def _espia(conn_lat, platform, owner):
            latidos.append(platform)
            return real(conn_lat, platform, owner)

        monkeypatch.setattr(corrida_mod, "_latir_claim", _espia)
        red = _RedFalsa(skus={ua["evento"]: ua["sku"], ub["evento"]: ub["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 2)
        assert latidos == ["amazon_mx"] * 4


@_skip_sin_pg
def test_latido_roto_no_tumba_corrida(monkeypatch, caplog):
    """R4-latido-fail-closed: un latido que levanta `psycopg.Error` no tumba.

    `_SQL_LATIDO` saboteado apunta a una tabla inexistente cuyo nombre trae
    el secreto: el `UndefinedTable` se traga (fail-open, el TTL es el
    backstop), la corrida decide y aplica normal, y el warning sale
    scrubbeado.
    """
    import logging

    import app.precio.corrida as corrida_mod
    from app.redaction import register_secret

    register_secret("s3cr3t-lat5")
    with _db() as (conn, _dsn):
        ua = _cadena(conn, sku="SKU-LT-E", ext="B0A50000LE", mode="live")
        ub = _cadena(conn, sku="SKU-LT-F", ext="B0A50000LF", mode="live")
        _price_obs(conn, asin="B0A50000LE")
        _price_obs(conn, asin="B0A50000LF")
        monkeypatch.setattr(
            corrida_mod,
            "_SQL_LATIDO",
            "UPDATE ads_optimizer_lock_s3cr3t_lat5 SET heartbeat_at = now()"
            " WHERE job_key = %s AND owner = %s",
        )
        red = _RedFalsa(skus={ua["evento"]: ua["sku"], ub["evento"]: ub["sku"]})
        with caplog.at_level(logging.WARNING, logger="app.precio.corrida"):
            res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 2)
        assert _cuenta_decisiones(conn) == 2
        texto = "\n".join(r.getMessage() for r in caplog.records)
        assert "latido fallo" in texto
        assert "s3cr3t-lat5" not in texto


# ---------------------------------------------------------------------------
# Bloque R1-b2 (REPRICING 01, Fase 11 r1-cierre-r2): mata los 17 mutantes del
# lead (M1, M9, M9b, M10b, M17, M18, M20, M32, M33, M46-M50, M54, M58, M60).
# Solo tests (app/ intacto); cada prueba afirma lo persistido. Evidencia:
# docs/evidencia/repricing-01/R.1/bis-r2.md. Otros carriles anexan debajo.
# ---------------------------------------------------------------------------


def _r1b2_fila_senal(conn):
    """La fila de `precio_decision` con sus numeros de senal."""
    return conn.execute(
        "SELECT resultado, motivo, u15, u60, n15, n60, racha_senal, perdiendo FROM precio_decision"
    ).fetchone()


def _r1b2_corrida_perdiendo(conn, *, extras=False):
    """Escenario `_ventas_perdiendo` corrido en live; con `extras` agrega
    filas `refund`/`fee` con cantidad en [hoy-15, hoy-1]. Devuelve la fila."""
    datos = _cadena(conn, mode="live")
    _price_obs(conn)
    run_id, _fin = _run_ledger(conn)
    _ventas_perdiendo(
        conn,
        datos["producto"],
        sku=datos["sku"],
        run_id=run_id,
        lid=datos["listing"],
    )
    if extras:
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        for kind, qty, importe, atraso, evento in (
            ("refund", 5, "-50", 5, "refund-m9-1"),
            ("fee", 7, "-70", 10, "fee-m9-1"),
        ):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
                " product_id, quantity, amount, amount_currency, ingest_run_id,"
                " source_event_id)"
                " VALUES ('amazon_mx', %s, %s, %s, %s, %s, %s, 'MXN', %s, %s)",
                (
                    kind,
                    hoy - timedelta(days=atraso),
                    datetime.now(UTC),
                    datos["producto"],
                    qty,
                    Decimal(importe),
                    run_id,
                    evento,
                ),
            )
    red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
    res = _corre(conn, red)
    assert (res.decisiones, res.escritas) == (1, 1)
    return _r1b2_fila_senal(conn)


@_skip_sin_pg
def test_r1b2_m9_refund_y_fee_no_inflan_u15():
    """M9: filas `refund`/`fee` con cantidad en la ventana no entran a la
    senal: u15=15 y la fila igual que sin esas filas."""
    with _db() as (conn, _dsn):
        control = _r1b2_corrida_perdiendo(conn)
    with _db() as (conn, _dsn):
        con_extras = _r1b2_corrida_perdiendo(conn, extras=True)
    assert con_extras[2] == 15
    assert tuple(con_extras) == tuple(control)


@_skip_sin_pg
def test_r1b2_m9b_primera_venta_sale_solo_de_kind_sale():
    """M9b: ventas de los ultimos 60 dias + `refund` de hace 100: la senal
    sigue `historia_corta` (la primera venta ignora el `refund`)."""
    from app.precio.puerto import Miembro, UnidadPrecio
    from app.spapi.precio_mercado import historia_unidad

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _cierra_goal(conn, datos["listing"], hoy)
        _goal(conn, datos["listing"], mode="live", pct="0.10", desde=hoy)
        run_id, _fin = _run_ledger(conn)
        for atraso in range(1, 61):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
                " product_id, quantity, amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, 200, 'MXN', %s)",
                (hoy - timedelta(days=atraso), datetime.now(UTC), datos["producto"], run_id),
            )
            conn.execute(
                "INSERT INTO ledger_event_atribucion (ledger_event_id, platform,"
                " listing_id, variante_ext, resuelto_por, ingest_run_id)"
                " VALUES (currval('ledger_event_id_seq'), 'amazon_mx', %s, NULL, 'sku', %s)",
                (datos["listing"], run_id),
            )
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
            " product_id, quantity, amount, amount_currency, ingest_run_id,"
            " source_event_id)"
            " VALUES ('amazon_mx', 'refund', %s, %s, %s, 3, -30, 'MXN', %s, 'refund-m9b-1')",
            (hoy - timedelta(days=100), datetime.now(UTC), datos["producto"], run_id),
        )
        unidad = UnidadPrecio(
            listing_id=datos["listing"],
            plataforma="amazon_mx",
            externo=datos["ext"],
            canal=None,
            producto_ancla=datos["producto"],
            miembros=(Miembro(datos["producto"], datos["sku"], None),),
        )
        historia = historia_unidad(conn, unidad=unidad, canal="fba", hoy=hoy)
        assert historia.primera_venta == hoy - timedelta(days=60)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert tuple(fila) == ("mantener", "ventas_sin_dato:historia_corta")
        assert red.n_fees == 0 and red.n_patch == 0


@_skip_sin_pg
def test_r1b2_m54_dia_inactivo_no_deja_perdiendo():
    """M54: con racha previa 2 y un dia no BUYABLE en la ventana, la
    decision sale `listing_inactivo` (perdiendo false, racha 0)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        ayer = hoy - timedelta(days=1)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        try:
            conn.execute(
                "INSERT INTO precio_decision (listing_id, platform, resultado, mode,"
                " goal, m_actual, p_actual, p_actual_currency, p_objetivo,"
                " p_objetivo_currency, p_aplicado, p_aplicado_currency, i_valor, i_currency,"
                " c_valor, c_currency, f_valor, f_currency, l_valor, l_currency,"
                " r_valor, r_currency, decision_date, racha_senal,"
                " l_origen, verificacion)"
                " VALUES (%s, 'amazon_mx', 'subir', 'live', 0.30, 0.25,"
                " 100, 'MXN', 110, 'MXN', 110, 'MXN', 10, 'MXN', 20, 'MXN',"
                " 12, 'MXN', 5, 'MXN', 8, 'MXN', %s, 2,"
                " 'politica', 'cotizada')",
                (datos["listing"], ayer),
            )
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        run_ledger, _fin = _run_ledger(conn)
        _ventas_perdiendo(
            conn,
            datos["producto"],
            sku=datos["sku"],
            run_id=run_ledger,
            lid=datos["listing"],
        )
        # La tabla es append-only: el dia se apaga INSERTANDO (activo solo
        # si TODAS las filas del dia dicen activo; a las 18:00 para no chocar
        # con la clave unica de la de mediodia).
        dia_apagado = hoy - timedelta(days=7)
        conn.execute(
            "INSERT INTO spapi_listing_estado_observation (seller_sku, platform,"
            " status, api_version, observed_at) VALUES (%s, 'amazon_mx', 'INACTIVE',"
            " 'v1', %s)",
            (
                datos["sku"],
                datetime(dia_apagado.year, dia_apagado.month, dia_apagado.day, 18, tzinfo=UTC),
            ),
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM spapi_listing_estado_observation"
                " WHERE seller_sku = %s AND platform = 'amazon_mx'"
                " AND (observed_at AT TIME ZONE 'UTC')::date = %s",
                (datos["sku"], dia_apagado),
            ).fetchone()[0]
            == 2
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        fila = conn.execute(
            "SELECT resultado, motivo, racha_senal, perdiendo FROM precio_decision"
            " WHERE listing_id = %s AND decision_date = %s",
            (datos["listing"], hoy),
        ).fetchone()
        assert tuple(fila) == ("subir", None, 0, False)


@_skip_sin_pg
def test_r1b2_m46_m50_fila_persiste_canal_e_ids_sin_prioridad():
    """M46-M50 (S.1): la fila del subir live trae canal y los tres ids
    exactos (config, cotizacion usada, fee del escenario); `prioridad`
    queda NULL (S.1 la borro del motor)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        fila = conn.execute(
            "SELECT resultado, m_actual, goal, prioridad, canal, config_version_id,"
            " cotizacion_id, fee_observation_id FROM precio_decision"
        ).fetchone()
        assert fila[0] == "subir"
        assert fila[3] is None
        assert fila[4] == "fba"
        assert fila[4] == conn.execute("SELECT canal FROM estimacion_escenario").fetchone()[0]
        assert fila[5] == conn.execute("SELECT max(id) FROM config_version").fetchone()[0]
        cotizaciones = [
            r[0] for r in conn.execute("SELECT id FROM precio_cotizacion ORDER BY id").fetchall()
        ]
        assert cotizaciones != []
        # La maquina puede cotizar dos veces; la usada es la ultima.
        assert fila[6] == cotizaciones[-1]
        assert (
            fila[7]
            == conn.execute("SELECT fee_observation_id FROM estimacion_escenario").fetchone()[0]
        )


@_skip_sin_pg
def test_r1b2_m20_reversas_en_error_no_frenan():
    """M20: tres dias seguidos con `error` que son reversas (y ningun error
    real): `freno_por_error` es False."""
    from app.precio.corrida import freno_por_error

    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid)
        orig = _cambio_con_estado(
            conn, dec, lid, estado="enviado", dia=datetime.now(UTC) - timedelta(days=10)
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (orig,),
        )
        for atraso in (1, 2, 3):
            rev = conn.execute(
                "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at, es_reversa, reversa_de)"
                " VALUES (%s, 'amazon_mx', 110, 'MXN', 100, 'MXN', true,"
                " 'pendiente', %s, true, %s) RETURNING id",
                (lid, datetime.now(UTC) - timedelta(days=atraso), orig),
            ).fetchone()[0]
            conn.execute(
                "UPDATE precio_cambio SET estado = 'error', error_code = 'PATCH /x 500'"
                " WHERE id = %s",
                (rev,),
            )
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_cambio"
                " WHERE listing_id = %s AND estado = 'error' AND es_reversa",
                (lid,),
            ).fetchone()[0]
            == 3
        )
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        assert (
            freno_por_error(conn, lid, "amazon_mx", hoy, dias=3, desde=hoy - timedelta(days=30))
            is False
        )


@_skip_sin_pg
def test_r1b2_m18_pricing_del_dia_usa_la_mas_reciente():
    """M18: dos observaciones del dia (temprana divergente, tardia igual al
    escenario): manda la tardia y la decision es subir."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _price_obs(
            conn,
            precio="200",
            observada=datetime(hoy.year, hoy.month, hoy.day, 8, tzinfo=UTC),
        )
        _price_obs(
            conn,
            precio="116",
            observada=datetime(hoy.year, hoy.month, hoy.day, 18, tzinfo=UTC),
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        assert tuple(_decision_unica(conn)) == ("subir", None, Decimal("127.60"))


# ---------------------------------------------------------------------------
# REPRICING 02 S.1: sin cupo, decidir -> compuerta -> aplicar sobre filas.
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_s1_ocho_live_mueven_precio():
    """S.1: 8 decisiones live que mueven precio, las 8 se escriben (sin cupo)."""
    with _db() as (conn, _dsn):
        skus = {}
        for i in range(8):
            datos = _cadena(conn, sku=f"SKU-S1-{i:02d}", ext=f"B0S10000{i:02d}", mode="live")
            _price_obs(conn, asin=f"B0S10000{i:02d}")
            skus[datos["evento"]] = datos["sku"]
        red = _RedFalsa(skus=skus)
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (8, 8)
        assert red.n_patch == 8
        assert _cuenta_decisiones(conn) == 8 and _cuenta_cambios(conn) == 8


@_skip_sin_pg
def test_s1_segunda_corrida_aplica_lo_pendiente():
    """S.1: lo decidido se guarda antes de aplicarse; la segunda corrida no
    decide y aplica solo lo pendiente (el vivo distinto deja sin fila)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        skus = {datos["evento"]: datos["sku"]}
        primera = _corre(conn, _RedFalsa(skus=skus, precio_vivo="999.00"))
        assert (primera.decisiones, primera.escritas) == (1, 0)
        assert _decision_unica(conn)[0] == "subir"
        assert _cuenta_cambios(conn) == 0
        segunda = _corre(conn, _RedFalsa(skus=skus))
        assert (segunda.decisiones, segunda.escritas) == (0, 1)
        assert _cuenta_decisiones(conn) == 1 and _cuenta_cambios(conn) == 1


@_skip_sin_pg
def test_s1_corrida_abre_cierra_y_aborta_la_anterior(monkeypatch):
    """S.1: cada ejecucion abre y cierra su fila de `precio_corrida`; una
    fila `abierta` de una corrida anterior pasa a `abortada`."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        cfg = conn.execute("SELECT max(id) FROM config_version").fetchone()[0]
        skus = {datos["evento"]: datos["sku"]}
        res = _corre(conn, _RedFalsa(skus=skus))
        assert (res.decisiones, res.escritas) == (1, 1)
        filas = conn.execute(
            "SELECT platform, estado, owner, config_version_id, modo_global,"
            " decisiones, movidas, virtuales, retenidas, errores,"
            " saltadas_por_edicion, cerrada_at IS NOT NULL"
            " FROM precio_corrida ORDER BY id"
        ).fetchall()
        assert [tuple(f) for f in filas] == [
            (
                "amazon_mx",
                "cerrada",
                "tdd",
                cfg,
                "live",
                1,
                1,
                0,
                0,
                0,
                0,
                True,
            )
        ]

        def _muere(*a, **k):
            raise RuntimeError("corte en compuerta")

        monkeypatch.setattr(corrida_mod, "_medir_compuerta", _muere)
        with pytest.raises(RuntimeError, match="corte en compuerta"):
            _corre(conn, _RedFalsa(skus=skus))
        assert [
            f[0] for f in conn.execute("SELECT estado FROM precio_corrida ORDER BY id").fetchall()
        ] == ["cerrada", "abierta"]
        monkeypatch.undo()
        tercera = _corre(conn, _RedFalsa(skus=skus))
        assert (tercera.decisiones, tercera.escritas) == (0, 0)
        assert [
            f[0] for f in conn.execute("SELECT estado FROM precio_corrida ORDER BY id").fetchall()
        ] == ["cerrada", "abortada", "cerrada"]


@_skip_sin_pg
def test_s1_config_sin_caps_no_detiene():
    """S.1: una config sin `precio_cap_*` no detiene la corrida."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        sin_caps = {k: v for k, v in SETTINGS.items() if not k.startswith("precio_cap_")}
        assert len(sin_caps) < len(SETTINGS) and "precio_modo_global" in sin_caps
        _config(conn, sin_caps)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)


@_skip_sin_pg
def test_s1_corrida_no_toca_apply_quota_state():
    """S.1: las cuotas de Ads en `apply_quota_state` no cambian en la corrida."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _config(conn, {**SETTINGS, "ads_apply_cap_amazon_mx_bid": 20})
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        conn.execute(
            "INSERT INTO apply_quota_state (motor, quota_date, cap, used)"
            " VALUES ('ads_optimizer:amazon_mx:bid', %s, 20, 3)",
            (hoy,),
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        assert conn.execute("SELECT motor, cap, used FROM apply_quota_state").fetchall() == [
            ("ads_optimizer:amazon_mx:bid", 20, 3)
        ]


# ---------------------------------------------------------------------------
# S.2: el apagador (modo global, modo por universo, modo efectivo)
# ---------------------------------------------------------------------------


def _config_modos(conn, *, global_="live", universos=None):
    """Config vigente con los modos del apagador (la ultima manda)."""
    if universos is None:
        universos = {"amazon_mx/fba": "live"}
    return _config(
        conn,
        {**SETTINGS, "precio_modo_global": global_, "precio_modo_universo": dict(universos)},
    )


@_skip_sin_pg
def test_s2_global_shadow_goal_live_decide_shadow_y_virtual():
    """S.2: global shadow + goal live = decision shadow con cambio virtual, cero PATCH."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _config_modos(conn, global_="shadow")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute("SELECT resultado, mode FROM precio_decision").fetchone()
        assert tuple(fila) == ("subir", "shadow")
        cambio = conn.execute(
            "SELECT estado, confirmado_por, aplicado, precio_despues FROM precio_cambio"
        ).fetchone()
        assert tuple(cambio[:3]) == ("confirmado", "virtual", False)
        assert cambio[3] == Decimal("127.60")
        assert red.n_patch == 0


@_skip_sin_pg
def test_s2_global_off_limpia_y_no_decide(monkeypatch):
    """S.2: global off = cierra huerfanas y enviados, cero decisiones y cero filas."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _config_modos(conn, global_="off")
        _price_obs(conn)
        llamadas = []
        real = corrida_mod._decidir_pendientes

        def _espia(*a, **k):
            llamadas.append(1)
            return real(*a, **k)

        monkeypatch.setattr(corrida_mod, "_decidir_pendientes", _espia)
        ayer = datetime.now(UTC) - timedelta(days=1)
        # Huerfana: pendiente de ayer sin PATCH.
        prod_h = _producto(conn, sku="SKU-S2-H")
        lid_h = _listing(conn, prod_h, ext="B0A50000S2", sku="SKU-S2-H")
        _goal(conn, lid_h, mode="live")
        dec_h = _decision_subir(conn, lid_h)
        hid = _cambio_con_estado(conn, dec_h, lid_h, estado="pendiente", dia=ayer)
        # Enviado de ayer: se cierra por observacion (precio distinto: 116 vs 110).
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-S2-B", ext="B0A50000SB")
        eid = _cambio_historia(conn, dec_b, datos["listing"], estado="enviado", dia=ayer)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert res.huerfanas == 1
        assert _cuenta_decisiones(conn) == 2
        assert (
            conn.execute(
                "SELECT estado, error_code FROM precio_cambio WHERE id = %s", (hid,)
            ).fetchone()
        ) == ("error", "huerfana_sin_patch")
        assert (
            conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (eid,)).fetchone()[0]
            == "no_confirmado"
        )
        assert (
            conn.execute("SELECT decisiones, movidas, modo_global FROM precio_corrida").fetchone()
        ) == (0, 0, "off")
        assert red.n_patch == 0 and red.n_fees == 0
        assert llamadas == []


@_skip_sin_pg
def test_s2_apagado_a_media_corrida_retiene_apagador(monkeypatch):
    """S.2: config a off tras la primera escritura = la segunda no sale y
    deja retencion `apagador` (la corrida relee antes de CADA escritura)."""
    import app.precio.cambios as cambios_mod

    with _db() as (conn, _dsn):
        ua = _cadena(conn, sku="SKU-S2-A", ext="B0A50000SA", mode="live")
        ub = _cadena(conn, sku="SKU-S2-B2", ext="B0A50000SB", mode="live")
        _config_modos(conn)
        _price_obs(conn, asin="B0A50000SA")
        _price_obs(conn, asin="B0A50000SB")
        real_aplicar = cambios_mod.aplicar
        llamadas = []

        def _apaga_tras_primera(conn_w, mercado_w, did, **kw):
            salida = real_aplicar(conn_w, mercado_w, did, **kw)
            llamadas.append(did)
            if len(llamadas) == 1:
                vigente = conn.execute(
                    "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
                vigente["precio_modo_global"] = "off"
                conn.execute(
                    "INSERT INTO config_version (label, settings) VALUES ('apagon-test', %s)",
                    (Json(vigente),),
                )
            return salida

        monkeypatch.setattr(cambios_mod, "aplicar", _apaga_tras_primera)
        red = _RedFalsa(skus={ua["evento"]: ua["sku"], ub["evento"]: ub["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 1)
        assert res.retenidas == 1
        assert red.n_patch == 1
        primera, segunda = ua["listing"], ub["listing"]
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_cambio WHERE listing_id = %s", (primera,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_cambio WHERE listing_id = %s", (segunda,)
            ).fetchone()[0]
            == 0
        )
        corrida = conn.execute("SELECT id FROM precio_corrida").fetchone()[0]
        dec_b = conn.execute(
            "SELECT id FROM precio_decision WHERE listing_id = %s", (segunda,)
        ).fetchone()[0]
        assert (
            conn.execute("SELECT decision_id, corrida_id, causa FROM precio_retencion").fetchone()
        ) == (dec_b, corrida, "apagador")
        retenidas = conn.execute(
            "SELECT retenidas FROM precio_corrida WHERE id = %s", (corrida,)
        ).fetchone()[0]
        assert retenidas == 1


def _pendiente_manana(conn, lid, gid):
    """Decision live de hoy con goal y canal (la manana que el repaso relee)."""
    return _decision_subir(conn, lid, mode="live", goal_id=gid, canal="fba")


def _retencion_de(conn, dec):
    return conn.execute(
        "SELECT causa FROM precio_retencion WHERE decision_id = %s", (dec,)
    ).fetchone()


@_skip_sin_pg
def test_s2_repaso_no_aplica_live_con_goal_pasado_a_shadow():
    """S.2: live de la manana cuyo goal se reeditó a shadow = el repaso no
    la aplica y deja retencion `goal_cambiado`."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config_modos(conn)
        gid_viejo = _goal(conn, lid, mode="live")
        dec = _pendiente_manana(conn, lid, gid_viejo)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _cierra_goal(conn, lid, hoy)
        _goal(conn, lid, mode="shadow", desde=hoy)
        red = _RedFalsa(precio_vivo="100.00")  # vivo = p_actual sembrado (si aplicara, el CAS pasa)
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert res.retenidas == 1
        assert red.n_patch == 0
        assert _retencion_de(conn, dec) == ("goal_cambiado",)
        assert _cuenta_cambios(conn) == 0


@_skip_sin_pg
def test_s2_repaso_no_aplica_live_con_goal_cerrado():
    """S.2: live de la manana con el goal cerrado = el repaso no la aplica
    y deja retencion `goal_cambiado`."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config_modos(conn)
        gid = _goal(conn, lid, mode="live")
        dec = _pendiente_manana(conn, lid, gid)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        _cierra_goal(conn, lid, hoy)
        red = _RedFalsa(precio_vivo="100.00")  # vivo = p_actual sembrado (si aplicara, el CAS pasa)
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert res.retenidas == 1
        assert red.n_patch == 0
        assert _retencion_de(conn, dec) == ("goal_cambiado",)
        assert _cuenta_cambios(conn) == 0


@_skip_sin_pg
def test_s2_repaso_no_aplica_live_con_goal_ya_no_live():
    """S.2: live de la manana cuyo goal sigue vigente pero ya no es live =
    el repaso no la aplica (rama `ya no live` del repaso)."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config_modos(conn)
        gid = _goal(conn, lid, mode="live")
        dec = _pendiente_manana(conn, lid, gid)
        conn.execute("ALTER TABLE precio_goal DISABLE TRIGGER precio_goal_solo_cierra_vigencia")
        try:
            # El CHECK live_exige_go es bidireccional: shadow exige go NULL.
            conn.execute(
                "UPDATE precio_goal SET mode = 'shadow', go_literal = NULL WHERE id = %s",
                (gid,),
            )
        finally:
            conn.execute("ALTER TABLE precio_goal ENABLE TRIGGER precio_goal_solo_cierra_vigencia")
        red = _RedFalsa(precio_vivo="100.00")  # vivo = p_actual sembrado (si aplicara, el CAS pasa)
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert res.retenidas == 1
        assert red.n_patch == 0
        assert _retencion_de(conn, dec) == ("goal_cambiado",)
        assert _cuenta_cambios(conn) == 0


@_skip_sin_pg
def test_s2_repaso_no_aplica_live_sin_goal_id():
    """S.2: live pendiente sin goal verificado = fail closed con
    `goal_cambiado`, jamas un PATCH sin goal."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config_modos(conn)
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid, mode="live", canal="fba")
        red = _RedFalsa(precio_vivo="100.00")  # vivo = p_actual sembrado (si aplicara, el CAS pasa)
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert res.retenidas == 1
        assert red.n_patch == 0
        assert _retencion_de(conn, dec) == ("goal_cambiado",)
        assert _cuenta_cambios(conn) == 0


@_skip_sin_pg
def test_s2_universo_sin_entrada_vale_off():
    """S.2: universo sin entrada en `precio_modo_universo` = off: sin fila
    (ni siquiera cotiza)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _config_modos(conn, universos={})
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert _cuenta_decisiones(conn) == 0
        assert conn.execute("SELECT count(*) FROM precio_cotizacion").fetchone()[0] == 0
        assert red.n_patch == 0 and red.n_fees == 0


@_skip_sin_pg
def test_s2_universo_sin_entrada_deja_error_visible():
    """A-W1: el universo sin entrada sigue valiendo off (0 filas, sin
    cotizar) pero deja un error visible en el resumen con la clave
    ausente (antes: 0,0 en silencio, sin fila ni linea)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _config_modos(conn, universos={})
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (0, 0)
        assert _cuenta_decisiones(conn) == 0
        assert conn.execute("SELECT count(*) FROM precio_cotizacion").fetchone()[0] == 0
        assert any(
            "universo_sin_entrada" in err
            and str(datos["listing"]) in err
            and "amazon_mx/fba" in err
            for err in res.errores
        ), res.errores


# ---------------------------------------------------------------------------
# REPRICING 02 S.3: la compuerta (fusible, insumo sistemico, corte, liberacion)
# ---------------------------------------------------------------------------


def _hoy(conn):
    return conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]


def _motor_de(settings):
    from app.precio.config import leer_config_motor

    return leer_config_motor(settings)


def _decision_s3(
    conn,
    lid,
    *,
    resultado="subir",
    motivo=None,
    mode="shadow",
    canal="fba",
    m="0.25",
    i="10",
    c="20",
    f="12",
    logi="5",
    r="8",
    p_actual="100",
    p_objetivo="110",
    p_aplicado="110",
    goal="0.30",
    goal_id=None,
    escenario_id=None,
    dia=None,
    con_cuenta=True,
):
    """Decision con cuenta completa (mide) o pelada (`no_evaluado`, no mide).

    Con `dia`, la fila nace en ese dia (triggers de usuario fuera: el de
    fecha y el de coherencia; FKs y CHECKs siguen activos). Sin `dia`, el
    trigger fija hoy y valida el goal.
    """
    mueve = resultado in ("subir", "bajar")
    columnas = (
        "listing_id, platform, resultado, motivo, mode, goal, m_actual,"
        " p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor,"
        " c_currency, f_valor, f_currency, l_valor, l_currency, r_valor,"
        " r_currency, l_origen, verificacion, goal_id, canal, escenario_id"
    )
    valores = (
        lid,
        "amazon_mx",
        resultado,
        motivo,
        mode,
        Decimal(goal),
        Decimal(m) if con_cuenta else None,
        Decimal(p_actual) if con_cuenta else None,
        "MXN",
        Decimal(p_objetivo) if con_cuenta and mueve else None,
        "MXN",
        Decimal(p_aplicado) if con_cuenta and mueve else None,
        "MXN",
        Decimal(i) if con_cuenta else None,
        "MXN",
        Decimal(c) if con_cuenta else None,
        "MXN",
        Decimal(f) if con_cuenta else None,
        "MXN",
        Decimal(logi) if con_cuenta else None,
        "MXN",
        Decimal(r) if con_cuenta else None,
        "MXN",
        "politica" if con_cuenta else None,
        "cotizada" if mueve and con_cuenta else None,
        goal_id,
        canal,
        escenario_id,
    )
    if dia is not None:
        columnas += ", decision_date"
        valores += (dia,)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER USER")
        try:
            return conn.execute(
                f"INSERT INTO precio_decision ({columnas})"
                f" VALUES ({', '.join(['%s'] * len(valores))}) RETURNING id",
                valores,
            ).fetchone()[0]
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER USER")
    return conn.execute(
        f"INSERT INTO precio_decision ({columnas})"
        f" VALUES ({', '.join(['%s'] * len(valores))}) RETURNING id",
        valores,
    ).fetchone()[0]


def _unidad_s3(conn, tag, *, mode="shadow"):
    """Producto + listing + goal vigente hace 30 dias. Devuelve `(lid, gid)`."""
    prod = _producto(conn, f"ODOO-S3-{tag}")
    lid = _listing(conn, prod, ext=f"B0S3{tag}", sku=f"SKU-S3-{tag}")
    return lid, _goal(conn, lid, mode=mode)


def _corrida_s3(conn, cfg, *, platform="amazon_mx", modo="live"):
    return conn.execute(
        "INSERT INTO precio_corrida (platform, owner, config_version_id, modo_global)"
        " VALUES (%s, 'tdd-s3', %s, %s) RETURNING id",
        (platform, cfg, modo),
    ).fetchone()[0]


def _medicion_de(conn, corrida):
    return conn.execute(
        "SELECT canal, medidas, nuevas, causa, insumo FROM precio_compuerta_medicion"
        " WHERE corrida_id = %s ORDER BY canal",
        (corrida,),
    ).fetchall()


class _MercadoEstricto:
    """Cualquier llamada es un fallo: el repaso ocioso no toca al Mercado."""

    clave = "amazon_mx"

    def __getattr__(self, nombre):
        raise AssertionError(f"el repaso llamo al Mercado: {nombre}")


class _MercadoErrorSiempre:
    """Vivo legible, escritura siempre rechazada (Acuse, no excepcion)."""

    clave = "amazon_mx"

    def __init__(self):
        self.escrituras = 0

    def validar_destino(self, unidad) -> None:
        return None

    def observado(self, conn, unidad, *, despues_de, hasta):
        return None

    def precio_vivo(self, unidad):
        from app.precio.tipos import Importe

        # Vivo = p_actual sembrado (100): el compare-and-set pasa y escribe.
        return Importe(Decimal("100.00"), "MXN")

    def escribir(self, unidad, destino):
        from app.precio.puerto import Acuse

        self.escrituras += 1
        return Acuse(aceptada=False, codigo="tdd-falla", cuerpo={})


class _MercadoExcepcionSiempre(_MercadoErrorSiempre):
    """Vivo legible, escritura que EXCEDE (no Acuse): el fallo llega al
    `except` generico de `_aplicar_pendientes` (A-W2)."""

    def escribir(self, unidad, destino):
        self.escrituras += 1
        raise RuntimeError("saboteo-tdd: escribir explota")


@_skip_sin_pg
def test_s3_80_intenciones_nuevas_retienen_y_no_escriben_nada():
    """S.3: 80 subir nuevas sobre 158 medidas retienen con
    `movimiento_masivo`: 80 retenciones, cero cambios, cero PATCH."""
    from types import SimpleNamespace

    from app.precio.corrida import _aplicar_pendientes, _medir_compuerta

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        hoy = _hoy(conn)
        for n in range(158):
            lid, gid = _unidad_s3(conn, f"80-{n:03d}")
            if n < 80:
                _decision_s3(conn, lid, goal_id=gid)
            else:
                _decision_s3(conn, lid, resultado="mantener", motivo="en_tolerancia", goal_id=gid)
        corrida = _corrida_s3(conn, cfg)
        motor = _motor_de(SETTINGS)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, motor, corrida)
        assert set(veredictos) == {"fba"}
        assert veredictos["fba"].causa == "movimiento_masivo"
        assert _medicion_de(conn, corrida) == [("fba", 158, 80, "movimiento_masivo", None)]
        mercado = SimpleNamespace(clave="amazon_mx")
        resultado = _aplicar_pendientes(
            conn,
            mercado,
            veredictos,
            hoy,
            owner="tdd",
            ahora=datetime.now(UTC),
            corrida_id=corrida,
            corte=motor.corte_errores_consecutivos,
        )
        assert resultado == (0, 0, 80, [])
        assert _cuenta_cambios(conn) == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_retencion WHERE causa = 'movimiento_masivo'"
            ).fetchone()[0]
            == 80
        )


@_skip_sin_pg
def test_s3_79_intenciones_pasan_y_aplican():
    """S.3: 79 nuevas sobre 158 medidas no retienen (mayor estricto): la
    corrida aplica las 79 en sombra."""
    from types import SimpleNamespace

    from app.precio.compuerta import Abierta
    from app.precio.corrida import _aplicar_pendientes, _medir_compuerta

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        hoy = _hoy(conn)
        for n in range(158):
            lid, gid = _unidad_s3(conn, f"79-{n:03d}")
            if n < 79:
                _decision_s3(conn, lid, goal_id=gid)
            else:
                _decision_s3(conn, lid, resultado="mantener", motivo="en_tolerancia", goal_id=gid)
        corrida = _corrida_s3(conn, cfg)
        motor = _motor_de(SETTINGS)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, motor, corrida)
        assert veredictos["fba"] == Abierta(medidas=158, nuevas=79)
        assert _medicion_de(conn, corrida) == [("fba", 158, 79, None, None)]
        mercado = SimpleNamespace(clave="amazon_mx")
        resultado = _aplicar_pendientes(
            conn,
            mercado,
            veredictos,
            hoy,
            owner="tdd",
            ahora=datetime.now(UTC),
            corrida_id=corrida,
            corte=motor.corte_errores_consecutivos,
        )
        assert resultado == (0, 79, 0, [])
        assert _cuenta_cambios(conn) == 79


@_skip_sin_pg
def test_s3_no_evaluado_no_cuenta_en_las_medidas():
    """S.3: 2 subir con cuenta + 3 `no_evaluado` sin cuenta = 2 medidas
    (uno con canal, como la ruta `escenario_incoherente`: ni asi mide)."""
    from app.precio.corrida import _medir_compuerta

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        hoy = _hoy(conn)
        for n in range(2):
            lid, gid = _unidad_s3(conn, f"ne-s-{n}")
            _decision_s3(conn, lid, goal_id=gid)
        for n, motivo, canal in (
            (0, "precio_ausente", None),
            (1, "escenario_incoherente", "fba"),
            (2, "precio_sin_observar", None),
        ):
            lid, gid = _unidad_s3(conn, f"ne-n-{n}")
            _decision_s3(
                conn,
                lid,
                resultado="no_evaluado",
                motivo=motivo,
                canal=canal,
                goal_id=gid,
                con_cuenta=False,
            )
        corrida = _corrida_s3(conn, cfg)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, _motor_de(SETTINGS), corrida)
        assert veredictos["fba"].medidas == 2
        assert _medicion_de(conn, corrida) == [("fba", 2, 2, None, None)]


@_skip_sin_pg
def test_s3_segunda_ola_al_mismo_goal_no_cuenta_como_nueva():
    """S.3: subir de hoy con subir ayer hacia el mismo goal no es nueva
    (nuevas = 1 de 2 subir); sin ayer, si."""
    from app.precio.corrida import _medir_compuerta

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        hoy = _hoy(conn)
        ayer = hoy - timedelta(days=1)
        lid_a, gid_a = _unidad_s3(conn, "ola-a")
        _decision_s3(conn, lid_a, goal_id=gid_a, dia=ayer)
        _decision_s3(conn, lid_a, goal_id=gid_a)
        lid_b, gid_b = _unidad_s3(conn, "ola-b")
        _decision_s3(conn, lid_b, goal_id=gid_b)
        corrida = _corrida_s3(conn, cfg)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, _motor_de(SETTINGS), corrida)
        assert (veredictos["fba"].medidas, veredictos["fba"].nuevas) == (2, 1)
        assert _medicion_de(conn, corrida) == [("fba", 2, 1, None, None)]


@_skip_sin_pg
def test_s3_costo_20_en_60_retiene_nombrando_costo():
    """S.3: costo +20 % en 60 de 158 comparables retiene con causa
    `insumo_sistemico` e insumo `costo` (sin movimiento masivo)."""
    from app.precio.corrida import _medir_compuerta

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        hoy = _hoy(conn)
        ayer = hoy - timedelta(days=1)
        for n in range(158):
            lid, gid = _unidad_s3(conn, f"c-{n:03d}")
            _decision_s3(
                conn,
                lid,
                resultado="mantener",
                motivo="en_tolerancia",
                goal_id=gid,
                c="100",
                dia=ayer,
            )
            _decision_s3(
                conn,
                lid,
                resultado="mantener",
                motivo="en_tolerancia",
                goal_id=gid,
                c="120" if n < 60 else "100",
            )
        corrida = _corrida_s3(conn, cfg)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, _motor_de(SETTINGS), corrida)
        retenida = veredictos["fba"]
        assert (retenida.causa, retenida.insumo) == ("insumo_sistemico", "costo")
        assert (retenida.medidas, retenida.nuevas) == (158, 0)
        assert _medicion_de(conn, corrida) == [("fba", 158, 0, "insumo_sistemico", "costo")]
        detalle = conn.execute(
            "SELECT detalle FROM precio_compuerta_medicion WHERE corrida_id = %s",
            (corrida,),
        ).fetchone()[0]
        assert detalle and "costo" in detalle


@_skip_sin_pg
def test_s3_salto_solo_fbm_retiene_fbm_y_deja_pasar_fba():
    """S.3: con FBA y FBM en la plataforma, un salto que solo afecta a FBM
    retiene FBM (`insumo_sistemico`) y aplica FBA."""
    from types import SimpleNamespace

    from app.precio.compuerta import Abierta
    from app.precio.corrida import _aplicar_pendientes, _medir_compuerta

    settings = {
        **SETTINGS,
        "precio_modo_universo": {"amazon_mx/fba": "live", "amazon_mx/fbm": "live"},
    }
    with _db() as (conn, _dsn):
        cfg = _config(conn, settings)
        hoy = _hoy(conn)
        ayer = hoy - timedelta(days=1)
        for n in range(5):
            lid, gid = _unidad_s3(conn, f"a-{n}")
            _decision_s3(
                conn,
                lid,
                resultado="mantener",
                motivo="en_tolerancia",
                goal_id=gid,
                c="100",
                dia=ayer,
            )
            _decision_s3(
                conn, lid, resultado="mantener", motivo="en_tolerancia", goal_id=gid, c="100"
            )
        for n in range(2):
            lid, gid = _unidad_s3(conn, f"a-s-{n}")
            _decision_s3(conn, lid, goal_id=gid)
        for n in range(12):
            lid, gid = _unidad_s3(conn, f"b-{n}")
            _decision_s3(
                conn,
                lid,
                resultado="mantener",
                motivo="en_tolerancia",
                goal_id=gid,
                c="100",
                canal="fbm",
                dia=ayer,
            )
            _decision_s3(
                conn,
                lid,
                resultado="mantener",
                motivo="en_tolerancia",
                goal_id=gid,
                c="120",
                canal="fbm",
            )
        for n in range(2):
            lid, gid = _unidad_s3(conn, f"b-s-{n}")
            _decision_s3(conn, lid, goal_id=gid, canal="fbm")
        corrida = _corrida_s3(conn, cfg)
        motor = _motor_de(settings)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, motor, corrida)
        assert set(veredictos) == {"fba", "fbm"}
        assert veredictos["fba"] == Abierta(medidas=7, nuevas=2)
        assert (veredictos["fbm"].causa, veredictos["fbm"].insumo) == (
            "insumo_sistemico",
            "costo",
        )
        assert _medicion_de(conn, corrida) == [
            ("fba", 7, 2, None, None),
            ("fbm", 14, 2, "insumo_sistemico", "costo"),
        ]
        mercado = SimpleNamespace(clave="amazon_mx")
        resultado = _aplicar_pendientes(
            conn,
            mercado,
            veredictos,
            hoy,
            owner="tdd",
            ahora=datetime.now(UTC),
            corrida_id=corrida,
            corte=motor.corte_errores_consecutivos,
        )
        assert resultado == (0, 2, 2, [])
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_retencion WHERE causa = 'insumo_sistemico'"
            ).fetchone()[0]
            == 2
        )


@_skip_sin_pg
def test_s3_liberar_aplica_lo_retenido_de_hoy_y_no_lo_de_ayer():
    """S.3: tras `liberar`, la corrida siguiente aplica lo retenido de hoy;
    lo de ayer nunca es pendiente."""
    from types import SimpleNamespace

    from app.precio.compuerta import Abierta
    from app.precio.corrida import _aplicar_pendientes, _medir_compuerta
    from app.precio.liberaciones import liberar

    settings = {**SETTINGS, "precio_fusible_min_movimientos": 2}
    with _db() as (conn, _dsn):
        cfg = _config(conn, settings)
        hoy = _hoy(conn)
        ayer = hoy - timedelta(days=1)
        for n in range(3):
            lid, gid = _unidad_s3(conn, f"lib-{n}")
            _decision_s3(conn, lid, goal_id=gid)
        lid_viejo, gid_viejo = _unidad_s3(conn, "lib-viejo")
        dec_ayer = _decision_s3(conn, lid_viejo, goal_id=gid_viejo, dia=ayer)
        primera = _corrida_s3(conn, cfg)
        motor = _motor_de(settings)
        retenidos = _medir_compuerta(conn, "amazon_mx", hoy, motor, primera)
        assert retenidos["fba"].causa == "movimiento_masivo"
        mercado = SimpleNamespace(clave="amazon_mx")
        momento = datetime.now(UTC)
        assert _aplicar_pendientes(
            conn,
            mercado,
            retenidos,
            hoy,
            owner="tdd",
            ahora=momento,
            corrida_id=primera,
            corte=motor.corte_errores_consecutivos,
        ) == (0, 0, 3, [])
        assert _cuenta_cambios(conn) == 0
        soltada = liberar(conn, corrida_id=primera, actor="dueno", nota="ola real, soltar")
        assert (soltada.corrida_id, soltada.actor) == (primera, "dueno")
        assert soltada.liberada_en is not None
        segunda = _corrida_s3(conn, cfg)
        abiertos = _medir_compuerta(conn, "amazon_mx", hoy, motor, segunda)
        assert abiertos["fba"] == Abierta(medidas=3, nuevas=0)
        assert _aplicar_pendientes(
            conn,
            mercado,
            abiertos,
            hoy,
            owner="tdd",
            ahora=momento,
            corrida_id=segunda,
            corte=motor.corte_errores_consecutivos,
        ) == (0, 3, 0, [])
        assert _cuenta_cambios(conn) == 3
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_cambio WHERE decision_id = %s", (dec_ayer,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_retencion WHERE decision_id = %s", (dec_ayer,)
            ).fetchone()[0]
            == 0
        )


@_skip_sin_pg
def test_s3_liberar_dos_veces_devuelve_la_fila_y_sin_retencion_levanta():
    """S.3: `liberar` es idempotente por corrida; sin medicion con causa
    (o sin medicion, o corrida inexistente) levanta `CorridaSinRetencion`."""
    from app.precio.liberaciones import CorridaSinRetencion, liberar

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        retenida = _corrida_s3(conn, cfg)
        conn.execute(
            "INSERT INTO precio_compuerta_medicion (corrida_id, canal, medidas, nuevas, causa)"
            " VALUES (%s, 'fba', 10, 8, 'movimiento_masivo')",
            (retenida,),
        )
        primera = liberar(conn, corrida_id=retenida, actor="dueno", nota="soltar")
        segunda = liberar(conn, corrida_id=retenida, actor="otro", nota="otra nota")
        assert segunda == primera
        assert conn.execute("SELECT count(*) FROM precio_compuerta_liberacion").fetchone()[0] == 1
        limpia = _corrida_s3(conn, cfg)
        conn.execute(
            "INSERT INTO precio_compuerta_medicion (corrida_id, canal, medidas, nuevas)"
            " VALUES (%s, 'fba', 10, 1)",
            (limpia,),
        )
        with pytest.raises(CorridaSinRetencion):
            liberar(conn, corrida_id=limpia, actor="dueno", nota="nada que soltar")
        vacia = _corrida_s3(conn, cfg)
        with pytest.raises(CorridaSinRetencion):
            liberar(conn, corrida_id=vacia, actor="dueno", nota="nada que soltar")
        with pytest.raises(CorridaSinRetencion):
            liberar(conn, corrida_id=999999, actor="dueno", nota="nada que soltar")


@_skip_sin_pg
def test_s3_repaso_sin_nada_pendiente_no_llama_al_mercado():
    """S.3: segunda corrida sin nada pendiente: 0/0 sin tocar al Mercado
    (cualquier llamada revienta en `_MercadoEstricto`)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        primera = _corre(conn, red)
        assert (primera.decisiones, primera.escritas) == (1, 1)
        segunda = correr(conn, _MercadoEstricto(), owner="tdd")
        assert (segunda.decisiones, segunda.escritas) == (0, 0)
        assert segunda.retenidas == 0


@_skip_sin_pg
def test_s3_cinco_errores_seguidos_cortan_con_corte_errores():
    """S.3: 7 live con escritura siempre rechazada: 5 cambios en error y las
    2 restantes retenidas `corte_errores` (el Mercado se toca 5 veces)."""
    from app.precio.compuerta import Abierta
    from app.precio.corrida import _aplicar_pendientes, _medir_compuerta

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        hoy = _hoy(conn)
        for n in range(7):
            lid, gid = _unidad_s3(conn, f"cor-{n}", mode="live")
            _decision_s3(conn, lid, mode="live", goal_id=gid)
        corrida = _corrida_s3(conn, cfg)
        motor = _motor_de(SETTINGS)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, motor, corrida)
        assert veredictos["fba"] == Abierta(medidas=7, nuevas=7)  # 7 < minimo 10: pasa
        mercado = _MercadoErrorSiempre()
        resultado = _aplicar_pendientes(
            conn,
            mercado,
            veredictos,
            hoy,
            owner="tdd",
            ahora=datetime.now(UTC),
            corrida_id=corrida,
            corte=motor.corte_errores_consecutivos,
        )
        assert resultado == (5, 0, 2, [])
        assert mercado.escrituras == 5
        assert (
            conn.execute("SELECT count(*) FROM precio_cambio WHERE estado = 'error'").fetchone()[0]
            == 5
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_retencion WHERE causa = 'corte_errores'"
            ).fetchone()[0]
            == 2
        )


@_skip_sin_pg
def test_s3_cinco_excepciones_seguidas_cortan_la_corrida():
    """A-W2: 7 live con escritura que EXCEDE (no Acuse de error): las 5
    excepciones alimentan el cortacircuito igual que un Acuse y las 2
    restantes quedan retenidas `corte_errores` (el Mercado se toca 5
    veces, no 7)."""
    from app.precio.compuerta import Abierta
    from app.precio.corrida import _aplicar_pendientes, _medir_compuerta

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        hoy = _hoy(conn)
        for n in range(7):
            lid, gid = _unidad_s3(conn, f"w2-{n}", mode="live")
            _decision_s3(conn, lid, mode="live", goal_id=gid)
        corrida = _corrida_s3(conn, cfg)
        motor = _motor_de(SETTINGS)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, motor, corrida)
        assert veredictos["fba"] == Abierta(medidas=7, nuevas=7)  # 7 < minimo 10: pasa
        mercado = _MercadoExcepcionSiempre()
        resultado = _aplicar_pendientes(
            conn,
            mercado,
            veredictos,
            hoy,
            owner="tdd",
            ahora=datetime.now(UTC),
            corrida_id=corrida,
            corte=motor.corte_errores_consecutivos,
        )
        escritas, virtuales, retenidas, errores = resultado
        assert (escritas, virtuales, retenidas) == (0, 0, 2)
        assert len(errores) == 5
        assert mercado.escrituras == 5
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_retencion WHERE causa = 'corte_errores'"
            ).fetchone()[0]
            == 2
        )


@_skip_sin_pg
def test_s3_fee_variable_del_escenario_retiene_nombrando_insumo():
    """S.3 (OJO 2): el partido de fees vive en el escenario
    (`estimacion_escenario.fee_variable`, via `escenario_id`): +30 % en 12
    de 12 retiene nombrando `fee_variable`."""
    from app.precio.corrida import _medir_compuerta

    with _db() as (conn, _dsn):
        cfg = _config(conn)
        hoy = _hoy(conn)
        ayer = hoy - timedelta(days=1)
        pol = _politica(conn)
        run_id, validada_en = _run(conn)
        for n in range(12):
            tag = f"fee-{n:02d}"
            prod = _producto(conn, f"ODOO-S3-{tag}")
            lid = _listing(conn, prod, ext=f"B0S3{tag}", sku=f"SKU-S3-{tag}")
            gid = _goal(conn, lid, mode="shadow")
            costo_id = _costo(conn, prod)
            for dia, variable in ((ayer, "10.00"), (hoy, "13.00")):
                sello = dia.isoformat()
                oferta = _oferta_obs(
                    conn,
                    lid,
                    ext=f"B0S3{tag}",
                    sku=f"SKU-S3-{tag}",
                    evento=f"oferta-{tag}-{sello}",
                    huella=f"ctx-{tag}-{sello}",
                )
                fee = _fee_obs(
                    conn,
                    oferta,
                    lid,
                    ext=f"B0S3{tag}",
                    sku=f"SKU-S3-{tag}",
                    evento=f"fee-{tag}-{sello}",
                    huella=f"ctx-{tag}-{sello}",
                )
                esc = _escenario(
                    conn,
                    lid,
                    oferta,
                    fee,
                    pol,
                    costo_id,
                    run_id,
                    validada_en,
                    ext=f"B0S3{tag}",
                    sku=f"SKU-S3-{tag}",
                    evento=f"esc-{tag}-{sello}",
                    huella=f"ctx-{tag}-{sello}",
                    fee_variable=variable,
                    fee_fijo="3.00",
                )
                _decision_s3(
                    conn,
                    lid,
                    resultado="mantener",
                    motivo="en_tolerancia",
                    goal_id=gid,
                    escenario_id=esc,
                    **({"dia": dia} if dia == ayer else {}),
                )
        corrida = _corrida_s3(conn, cfg)
        veredictos = _medir_compuerta(conn, "amazon_mx", hoy, _motor_de(SETTINGS), corrida)
        retenida = veredictos["fba"]
        assert (retenida.causa, retenida.insumo) == ("insumo_sistemico", "fee_variable")
        assert _medicion_de(conn, corrida) == [("fba", 12, 0, "insumo_sistemico", "fee_variable")]


# ---------------------------------------------------------------------------
# REPRICING 02 S.4: errores sin efecto, huerfanas por lectura viva, cierre
# por plataforma. Cada prueba trae su fixture de `readback_precio`.
# ---------------------------------------------------------------------------


def _error_con_readback(conn, dec, lid, *, dia, readback, error="PATCH /x 500"):
    """`error` sembrado con su readback: `readback = (precio, moneda)`."""
    cid = _cambio_historia(conn, dec, lid, estado="error", dia=dia, error=error)
    conn.execute(
        "UPDATE precio_cambio SET readback_precio = %s, readback_precio_currency = %s,"
        " readback_estado = 'ok', readback_at = %s WHERE id = %s",
        (readback[0], readback[1], dia, cid),
    )
    return cid


class _VivoFijo:
    """Mercado minimo para `cerrar_huerfanas`: vivo fijo o ilegible."""

    clave = "amazon_mx"

    def __init__(self, vivo):
        self.vivo = vivo
        self.lecturas = 0

    def precio_vivo(self, unidad):
        from app.precio.puerto import PrecioVivoAusente
        from app.precio.tipos import Importe

        self.lecturas += 1
        if self.vivo is None:
            raise PrecioVivoAusente("sin oferta propia")
        return Importe(Decimal(self.vivo[0]), self.vivo[1])


def _pendiente_huerfana(conn):
    """Listing + `pendiente` no-reversa de ayer (antes=100, despues=110)."""
    prod = _producto(conn)
    lid = _listing(conn, prod)
    _config(conn)
    _goal(conn, lid, mode="live")
    dec = _decision_subir(conn, lid)
    cid = _cambio_con_estado(
        conn, dec, lid, estado="pendiente", dia=datetime.now(UTC) - timedelta(days=1)
    )
    return lid, cid


@_skip_sin_pg
def test_s4_error_sin_efecto_no_consume_cooldown():
    """S.4 DoD 1: `error` con readback == `precio_antes` no consume
    cooldown: la corrida reintenta (subir + PATCH) en vez de mantener."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _config(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-S4-1", ext="B0A50000S1")
        _error_con_readback(
            conn,
            dec_b,
            datos["listing"],
            dia=datetime.now(UTC) - timedelta(days=1),
            readback=(100, "MXN"),
        )
        red = _RedFalsa(
            skus={datos["evento"]: datos["sku"]},
            patch=(200, {"submissionId": "s4-1", "status": "ERROR"}),
        )
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("subir", None)
        assert red.n_patch == 1
        hoy = _hoy(conn)
        nuevo = conn.execute(
            "SELECT estado, readback_precio, readback_precio_currency FROM precio_cambio"
            " WHERE listing_id = %s AND (enviado_at AT TIME ZONE 'UTC')::date = %s",
            (datos["listing"], hoy),
        ).fetchone()
        assert tuple(nuevo) == ("error", Decimal("116.00"), "MXN")


@_skip_sin_pg
def test_s4_error_con_efecto_si_consume_cooldown():
    """S.4 DoD 2 (candado): `error` con readback == `precio_despues` SI
    consume cooldown: `mantener(cooldown)` sin PATCH."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _config(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-S4-2", ext="B0A50000S2")
        _error_con_readback(
            conn,
            dec_b,
            datos["listing"],
            dia=datetime.now(UTC) - timedelta(days=1),
            readback=(110, "MXN"),
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("mantener", "cooldown")
        assert red.n_patch == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM precio_cambio WHERE listing_id = %s", (datos["listing"],)
            ).fetchone()[0]
            == 1
        )


@_skip_sin_pg
def test_s4_huerfana_movida_en_corrida_confirma_y_cuenta():
    """S.4 DoD 3: la corrida resuelve la huerfana movida
    (`confirmado/lectura_viva`) y la cuenta en el resumen.

    Reemplaza a `test_huerfana_se_cierra_sin_get_y_sale_en_resumen`: S.4
    SI lee el precio vivo.
    """
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid)
        ayer = datetime.now(UTC) - timedelta(days=1)
        cid = _cambio_con_estado(conn, dec, lid, estado="pendiente", dia=ayer)
        red = _RedFalsa(precio_vivo="110.00")
        res = _corre(conn, red)
        assert res.huerfanas == 1
        fila = conn.execute(
            "SELECT estado, confirmado_por, ack FROM precio_cambio WHERE id = %s", (cid,)
        ).fetchone()
        assert tuple(fila) == ("confirmado", "lectura_viva", {"origen": "huerfana_recuperada"})


@_skip_sin_pg
def test_s4_huerfana_quieta_es_error_sin_efecto():
    """S.4: huerfana con vivo == `precio_antes` sella `error` con
    `huerfana_sin_efecto`, y `cambios_previos` la lee `sin_efecto`."""
    from app.precio.cambios import cerrar_huerfanas
    from app.precio.fuentes import cambios_previos

    with _db() as (conn, _dsn):
        lid, cid = _pendiente_huerfana(conn)
        assert cerrar_huerfanas(conn, _VivoFijo(("100", "MXN"))) == 1
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s", (cid,)
        ).fetchone()
        assert tuple(fila) == ("error", "huerfana_sin_efecto")
        (previo,) = cambios_previos(conn, lid, "amazon_mx")
        assert previo.sin_efecto is True


@_skip_sin_pg
def test_s4_huerfana_otro_precio_sella_sin_patch():
    """S.4 (candado): huerfana con vivo distinto de antes y despues sella
    `error/huerfana_sin_patch` como hoy."""
    from app.precio.cambios import cerrar_huerfanas

    with _db() as (conn, _dsn):
        _lid, cid = _pendiente_huerfana(conn)
        assert cerrar_huerfanas(conn, _VivoFijo(("105.50", "MXN"))) == 1
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s", (cid,)
        ).fetchone()
        assert tuple(fila) == ("error", "huerfana_sin_patch")


@_skip_sin_pg
def test_s4_huerfana_ilegible_sella_sin_patch():
    """S.4 (candado): huerfana con vivo ilegible sella
    `error/huerfana_sin_patch`, tras leer el vivo una vez."""
    from app.precio.cambios import cerrar_huerfanas

    with _db() as (conn, _dsn):
        _lid, cid = _pendiente_huerfana(conn)
        mercado = _VivoFijo(None)
        assert cerrar_huerfanas(conn, mercado) == 1
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s", (cid,)
        ).fetchone()
        assert tuple(fila) == ("error", "huerfana_sin_patch")
        assert mercado.lecturas == 1


@_skip_sin_pg
def test_s4_corrida_mx_no_cierra_cambios_us():
    """S.4 DoD 4 (candado): la corrida de `amazon_mx` cierra su `enviado`
    por observacion y deja intacto el de `amazon_us`."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _config(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-S4-4", ext="B0A50000S4")
        ayer = datetime.now(UTC) - timedelta(days=1)
        cid_mx = _cambio_historia(conn, dec_b, datos["listing"], estado="enviado", dia=ayer)
        prod_u = _producto(conn, "SKU-S4-4-U")
        lid_u = _listing(conn, prod_u, platform="amazon_us", ext="B0A50000S4", sku="SKU-S4-4-U")
        _goal(conn, lid_u, platform="amazon_us", mode="live")
        dec_u = _decision_subir(conn, lid_u, platform="amazon_us")
        cid_u = conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at)"
            " VALUES (%s, %s, 'amazon_us', 100, 'MXN', 110, 'MXN', true,"
            " 'pendiente', %s) RETURNING id",
            (dec_u, lid_u, ayer),
        ).fetchone()[0]
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado',"
            " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
            (cid_u,),
        )
        _price_obs(conn, precio="110")
        # Obs MX del ASIN americano que tambien cerraria: si el cierre no
        # filtrara por plataforma, el mutante cierra el cambio de `amazon_us`.
        _price_obs(conn, asin="B0A50000S4", precio="110")
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        _corre(conn, red)
        fila_mx = conn.execute(
            "SELECT estado, confirmado_por FROM precio_cambio WHERE id = %s", (cid_mx,)
        ).fetchone()
        assert tuple(fila_mx) == ("confirmado", "observacion")
        assert (
            conn.execute("SELECT estado FROM precio_cambio WHERE id = %s", (cid_u,)).fetchone()[0]
            == "enviado"
        )


@_skip_sin_pg
def test_s4_dos_dias_sin_efecto_reintentan_y_arman_racha():
    """S.4 DoD 5: dos `error` sin efecto seguidos no congelan: hoy se
    reintenta (subir + PATCH fallido) y manana el freno ve 3 dias."""
    from app.precio.corrida import freno_por_error

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _config(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-S4-5", ext="B0A50000S5")
        for atraso in (2, 1):
            _error_con_readback(
                conn,
                dec_b,
                datos["listing"],
                dia=datetime.now(UTC) - timedelta(days=atraso),
                readback=(100, "MXN"),
            )
        red = _RedFalsa(
            skus={datos["evento"]: datos["sku"]},
            patch=(200, {"submissionId": "s4-5", "status": "ERROR"}),
        )
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("subir", None)
        assert red.n_patch == 1
        hoy = _hoy(conn)
        nuevo = conn.execute(
            "SELECT estado, readback_precio, readback_precio_currency FROM precio_cambio"
            " WHERE listing_id = %s AND (enviado_at AT TIME ZONE 'UTC')::date = %s",
            (datos["listing"], hoy),
        ).fetchone()
        assert tuple(nuevo) == ("error", Decimal("116.00"), "MXN")
        assert (
            freno_por_error(
                conn,
                datos["listing"],
                "amazon_mx",
                hoy + timedelta(days=1),
                dias=3,
                desde=hoy - timedelta(days=30),
            )
            is True
        )


@_skip_sin_pg
def test_s4_racha_sin_efecto_frena_cuarta_corrida():
    """S.4 DoD 5 (candado): tres `error` sin efecto seguidos frenan la
    cuarta corrida con `frenado(api_error)` sin PATCH. El freno SI cuenta
    los sin efecto (solo el cooldown los ignora)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _config(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-S4-5B", ext="B0A50000SB")
        for atraso in (3, 2, 1):
            _error_con_readback(
                conn,
                dec_b,
                datos["listing"],
                dia=datetime.now(UTC) - timedelta(days=atraso),
                readback=(100, "MXN"),
            )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila == ("frenado", "api_error")
        assert red.n_patch == 0
