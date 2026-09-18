"""Corrida diaria del motor de precios (REPRICING 01, A.5).

`app/precio/corrida.py` (claim `precio:<platform>` + advisory lock, cierre
por observacion, huerfanas, decide+persiste para los goals vigentes, cuota
por prioridad, aplica `live` con un solo cubo, virtuales en `shadow`) +
`app/precio/cuota.py` + `precio` en `app/cli.py` + cron en `docs/DEPLOY.md`.

Base real (`ORBIT_TEST_DSN`, cero `skipped` con el DSN de VERIFY): DB
temporal con 0001 + 0002 + 0028 + 0032 + 0035 + 0039. Todo el HTTP por
`httpx.MockTransport`: cero red, cero Amazon, cero reintentos en rafaga
(sleeps falsos).
"""

from __future__ import annotations

import itertools
import os
import socket
import threading
from contextlib import contextmanager
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
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0032_spapi_pricing.sql",
    "0035_spapi_listings_inventario.sql",
    "0039_precio.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()

PROPIO = VENDEDORES_PROPIOS[MERCADOS["amazon_mx"]]
CRED = {"lwa_app_id": "id-a5", "lwa_client_secret": "sec-a5", "refresh_token": "r-a5"}

# Todas las claves que lee la corrida (`leer_config` + cap + freno).
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
        " componentes, exclusiones, canonical_input, context_fingerprint, source_event_id)"
        " VALUES (%s, %s, %s, %s, 'fba', %s, %s, %s, 'vtest', %s, %s, %s, %s,"
        " %s, " + moneda_sql + ", %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s) RETURNING id",
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


def _corre(conn, red, platform="amazon_mx", **kw):
    """`correr` con red falsa y cubo sin dormir de verdad."""
    clock, sleep, _estado = _reloj_falso()
    fijar_fee = kw.pop("fijar_fee", True)
    if red.fee_time is None and fijar_fee:
        red.fee_time = _fee_time(conn)
    lector, escritor, fees = _clientes(red, platform)
    kw.setdefault("construir_cuerpo", _cuerpo_falso)
    kw.setdefault("owner", "tdd")
    kw.setdefault("limitador", CuboTasa(sleep=sleep, clock=clock, capacidad=1, tasa=0.5))
    return correr(
        conn,
        platform,
        lector=lector,
        escritor=escritor,
        fees=fees,
        **kw,
    )


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


@_skip_sin_pg
def test_cuota_saturada_reparte_por_prioridad_sin_tocar_ads():
    """cap=1 con dos subidas: la primera aplica, la segunda `mantener(cuota)`."""
    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-Q-A", ext="B0A50000QA", mode="live")
        b = _cadena(conn, sku="SKU-Q-B", ext="B0A50000QB", mode="live")
        _config(conn, {**SETTINGS, "precio_cap_amazon_mx": 1})
        _price_obs(conn, asin="B0A50000QA")
        _price_obs(conn, asin="B0A50000QB")
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 1)
        filas = conn.execute(
            "SELECT listing_id, resultado, motivo FROM precio_decision ORDER BY listing_id"
        ).fetchall()
        assert filas[0][1:] == ("subir", None)
        assert filas[1][1:] == ("mantener", "cuota")
        assert red.n_patch == 1
        assert conn.execute("SELECT used, cap FROM apply_quota_state").fetchone() == (1, 1)
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_quota_state WHERE motor NOT LIKE 'precio:%'"
            ).fetchone()[0]
            == 0
        )


def _ventas_parejas(conn, prod, *, qty, run_id, amount="200", platform="amazon_mx"):
    """Ventas planas en la ventana de `ingreso_60d` ([hoy-75, hoy-16])."""
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
def test_fase2_aplica_en_orden_de_prioridad():
    """Fase 2: con cupo para dos, el PATCH de mayor prioridad sale primero.

    Misma |m-goal|, distinto `ingreso_60d` (ventas planas 5:1, sin racha):
    B se crea primero (id menor) pero A abre: un mutante que ordene por
    `listing_id` deja pasar las lineas e invierte los PATCH.
    """
    import json as _json

    with _db() as (conn, _dsn):
        b = _cadena(conn, sku="SKU-O-B", ext="B0A50000OB", mode="live")
        a = _cadena(conn, sku="SKU-O-A", ext="B0A50000OA", mode="live")
        _price_obs(conn, asin="B0A50000OA")
        _price_obs(conn, asin="B0A50000OB")
        run_id, _fin = _run_ledger(conn)
        _ventas_parejas(conn, a["producto"], qty=5, run_id=run_id)
        _ventas_parejas(conn, b["producto"], qty=1, amount="100", run_id=run_id)
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 2)
        assert [ln.split(" ")[0] for ln in res.lineas] == [
            f"listing={a['listing']}",
            f"listing={b['listing']}",
        ]
        assert b["listing"] < a["listing"]
        cuerpos = [_json.loads(pedido.content) for pedido in red.pedidos_patch]
        assert [cuerpo["sku"] for cuerpo in cuerpos] == [a["sku"], b["sku"]]


@_skip_sin_pg
def test_fase3_respeta_reserva_denegada(monkeypatch):
    """Fase 3: si `reservar` niega, la candidata sale `mantener(cuota)`."""
    import app.precio.cuota as cuota_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        monkeypatch.setattr(cuota_mod, "reservar", lambda *a, **k: False)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        assert _decision_unica(conn)[:2] == ("mantener", "cuota")
        assert red.n_patch == 0


@_skip_sin_pg
def test_reversa_del_dia_consume_cupo():
    """Una reversa de hoy + cap=1: el candidato queda `mantener(cuota)`.

    El cambio original es de AYER: hoy solo la reversa consume cupo.
    """
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _config(conn, {**SETTINGS, "precio_cap_amazon_mx": 1})
        _price_obs(conn)
        # Andamio en OTRO listing (con goal live + decision de hoy: la
        # corrida lo salta por `decididas`): cambio confirmado de ayer +
        # reversa de hoy.
        prod_b = _producto(conn, sku="SKU-QR-B")
        lid = _listing(conn, prod_b, ext="B0A50000RB", sku="SKU-QR-B")
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid)
        ahora = datetime.now(UTC)
        orig = _cambio_con_estado(conn, dec, lid, estado="enviado", dia=ahora - timedelta(days=1))
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (orig,),
        )
        rev = conn.execute(
            "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
            " estado, enviado_at, es_reversa, reversa_de)"
            " VALUES (%s, 'amazon_mx', 110, 'MXN', 100, 'MXN', true,"
            " 'pendiente', %s, true, %s) RETURNING id",
            (lid, ahora, orig),
        ).fetchone()[0]
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado',"
            " ack = '{\"http_status\": 202}'::jsonb WHERE id = %s",
            (rev,),
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        # B ya tiene decision de hoy (andamio): solo A se decide.
        assert (res.decisiones, res.escritas) == (1, 0)
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision"
            " WHERE listing_id = (SELECT id FROM listing WHERE seller_sku = 'SKU-A5')"
        ).fetchone()
        assert tuple(fila) == ("mantener", "cuota")
        assert red.n_patch == 0


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
    from app.precio.corrida import cambios_previos

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


def _decision_subir(conn, lid, *, mode="live", m="0.25", goal="0.30", platform="amazon_mx"):
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado, mode,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency)"
        " VALUES (%s, %s, 'subir', %s, %s, %s,"
        " 100, 'MXN', 110, 'MXN', 110, 'MXN',"
        " 10, 'MXN', 20, 'MXN', 12, 'MXN', 5, 'MXN', 8, 'MXN')"
        " RETURNING id",
        (lid, platform, mode, Decimal(goal), Decimal(m)),
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
    """La corrida evalua el freno de errores por cada goal (cableado)."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        llamadas = []
        real = corrida_mod.freno_por_error

        def _espia(c, lid, platform, hoy, *, dias, desde):
            llamadas.append((lid, platform, dias))
            return real(c, lid, platform, hoy, dias=dias, desde=desde)

        monkeypatch.setattr(corrida_mod, "freno_por_error", _espia)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        assert llamadas == [(datos["listing"], "amazon_mx", 3)]


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
    from app.spapi.client import CuboTasa
    from app.spapi.precio_write import cambiar_precio

    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid)
        clock, sleep, _estado = _reloj_falso()
        red = _RedFalsa(precio_vivo="100.00")
        lector, escritor, _fees = _clientes(red)
        consumos = []
        cubo = CuboTasa(sleep=sleep, clock=clock, capacidad=100, tasa=1000.0)
        real = cubo.consumir

        def _contando():
            consumos.append(1)
            return real()

        cubo.consumir = _contando
        res = cambiar_precio(
            conn,
            dec,
            lector=lector,
            escritor=escritor,
            construir_cuerpo=_cuerpo_falso,
            limitador=cubo,
        )
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
def test_repartir_cupo_recibe_una_plataforma(monkeypatch):
    """(b) `repartir_cupo` se llama una vez por plataforma, sin mezclas.

    C3-7: con un goal vigente de OTRA plataforma, ese listing no entra
    a los candidatos.
    """
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        prod_us = _producto(conn, "SKU-RP-US")
        lid_us = _listing(conn, prod_us, platform="amazon_us", ext="B0A50000US", sku="SKU-RP-US")
        _goal(conn, lid_us, platform="amazon_us", mode="live")
        llamadas = []
        real = corrida_mod.repartir_cupo

        def _espia(candidatos, *, cupo):
            plataformas = set()
            lids = []
            for lid, _dec in candidatos:
                lids.append(lid)
                plataformas.add(
                    conn.execute("SELECT platform FROM listing WHERE id = %s", (lid,)).fetchone()[0]
                )
            llamadas.append((lids, plataformas, cupo))
            return real(candidatos, cupo=cupo)

        monkeypatch.setattr(corrida_mod, "repartir_cupo", _espia)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        _corre(conn, red)
        assert len(llamadas) == 1
        assert llamadas[0][1] == {"amazon_mx"}
        assert lid_us not in llamadas[0][0]


@_skip_sin_pg
@pytest.mark.parametrize(
    "ajuste,clave",
    [
        ({"precio_cap_amazon_mx": 99}, "precio_cap_amazon_mx"),
        ({"precio_freno_dias_error": 0}, "precio_freno_dias_error"),
    ],
)
def test_umbral_fuera_de_cota_aborta_al_arrancar(ajuste, clave):
    """(f) cap o freno fuera de cota: `ValueError` que nombra la clave."""
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
@pytest.mark.parametrize("clave", ["precio_cap_amazon_mx", "precio_freno_dias_error"])
def test_umbral_ausente_aborta_al_arrancar(clave):
    """(f) cap o freno ausente: `ValueError` que nombra la clave, sin default."""
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


def _ventas_perdiendo(conn, prod, *, sku, run_id, platform="amazon_mx", hoy=None):
    """75 dias de ledger + inventario + estado: u15=15, u60=120 (pierde).

    Esperado = 120/60*15*(1-0.40) = 18 > 15: con `senal_dias=1` la racha
    cierra en esta corrida (`perdiendo`, racha 1).
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
    from app.precio.config import leer_config
    from app.precio.corrida import armar_entrada
    from app.precio.reglas import decidir

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
        _ventas_perdiendo(conn, prod, sku="SKU-F6", run_id=run_ledger)
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
        entrada, _ids = armar_entrada(
            conn,
            lid,
            "amazon_mx",
            hoy=hoy,
            ahora=ahora,
            config=config,
            mode="shadow",
            goal=Decimal("0.30"),
            goal_vigente_desde=hoy - timedelta(days=30),
            product_id=prod,
        )
        assert entrada is not None
        d = decidir(entrada, hoy=hoy, config=config)
        assert (d.resultado, d.motivo) == ("frenado", "perdiendo_tras_subida")


# ---------------------------------------------------------------------------
# CLI `precio` y cron
# ---------------------------------------------------------------------------

# Fuente unica de la linea (docs/DEPLOY.md la copia de aqui).
LINEA_CRONTAB_PRECIO = (
    "10 13 * * * /usr/bin/flock -n /tmp/precio-corrida.lock"
    " docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx"
    " >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1"
)


def test_linea_crontab_en_deploy():
    """`docs/DEPLOY.md` trae la linea EXACTA y los previos a instalarla.

    D15: se afirma contra el doc, no contra la constante (y B6: los
    renglones previos a instalar la linea).
    """
    deploy = (RAIZ / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    assert LINEA_CRONTAB_PRECIO in deploy
    assert "10 13 * * *" in deploy
    assert "flock" in deploy
    assert "precio-corrida.log" in deploy
    assert "claves `precio_*" in deploy
    assert "no pasa el entorno del host" in deploy
    assert "13:10 UTC" in deploy
    assert "no toca Amazon" in deploy


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

    def _falso_correr(conn, platform, **kw):
        llamadas["platform"] = platform
        llamadas["kw"] = kw
        print("listing=1 amazon_mx shadow mantener en_tolerancia")
        return Resumen(decisiones=1, escritas=0, cerrados={}, huerfanas=0)

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo == 0
    assert llamadas["platform"] == "amazon_mx"
    # D14: `correr` recibe los clientes de `_clientes_precio` y el limitador.
    assert llamadas["kw"]["lector"] == "l"
    assert llamadas["kw"]["escritor"] == "e"
    assert llamadas["kw"]["fees"] == "f"
    assert llamadas["kw"]["limitador"] == "cubo"
    assert "decisiones=1 escritas=0" in capsys.readouterr().out


def test_cli_precio_lock_ocupado_sale_cero(monkeypatch, capsys):
    """Claim perdido: mensaje y exit 0 (el trabajo ya esta en curso)."""
    from app import cli as cli_mod

    def _ocioso(*a, **k):
        raise LockOcupado("precio:amazon_mx en curso (owner otro)")

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _ocioso)
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo == 0
    assert "en curso" in capsys.readouterr().err


@_skip_sin_pg
def test_huerfana_se_cierra_sin_get_y_sale_en_resumen():
    """La `pendiente` no-reversa amanece `error/huerfana_sin_patch`, cero red."""
    with _db() as (conn, _dsn):
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _config(conn)
        _goal(conn, lid, mode="live")
        dec = _decision_subir(conn, lid)
        ayer = datetime.now(UTC) - timedelta(days=1)
        cid = _cambio_con_estado(conn, dec, lid, estado="pendiente", dia=ayer)
        red = _RedFalsa()
        res = _corre(conn, red)
        assert res.huerfanas == 1
        fila = conn.execute(
            "SELECT estado, error_code FROM precio_cambio WHERE id = %s", (cid,)
        ).fetchone()
        assert tuple(fila) == ("error", "huerfana_sin_patch")
        assert red.n_get == 0 and red.n_fees == 0 and red.n_patch == 0


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
def test_relanzar_despues_de_morir_antes_de_persistir(monkeypatch):
    """B.3: muere tras cotizar y antes de persistir: el relanzamiento reusa y decide."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        real = corrida_mod.repartir_cupo

        def _muere(*a, **k):
            raise RuntimeError("muere a media corrida")

        monkeypatch.setattr(corrida_mod, "repartir_cupo", _muere)
        with pytest.raises(RuntimeError, match="muere a media corrida"):
            _corre(conn, red)
        monkeypatch.setattr(corrida_mod, "repartir_cupo", real)
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
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


@_skip_sin_pg
def test_reversa_a_media_corrida_reduce_cupo(monkeypatch):
    """K4: una reversa del dueno a media corrida entra en la guarda de `reservar`."""
    import app.precio.cuota as cuota_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        real = cuota_mod.reversas_hoy
        llamadas = []

        def _creciente(c, platform, hoy):
            n = real(c, platform, hoy)
            llamadas.append(n)
            return 99 if len(llamadas) > 1 else n

        monkeypatch.setattr(cuota_mod, "reversas_hoy", _creciente)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 0)
        assert _decision_unica(conn)[:2] == ("mantener", "cuota")
        assert red.n_patch == 0


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

    def _falso_correr(conn, platform, **kw):
        return Resumen(decisiones=3, escritas=2, cerrados={}, huerfanas=0)

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
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
def test_reservar_cupo_exacta_N():
    """LA-cuota-le: con `cap = N`, pasan exactamente N reservas y la N+1 no."""
    import app.precio.cuota as cuota_mod

    with _db() as (conn, _dsn):
        _config(conn, {**SETTINGS, "precio_cap_amazon_mx": 2})
        assert cuota_mod.reservar(conn, platform="amazon_mx", cap=2) is True
        assert cuota_mod.reservar(conn, platform="amazon_mx", cap=2) is True
        assert cuota_mod.reservar(conn, platform="amazon_mx", cap=2) is False
        usadas = conn.execute(
            "SELECT used FROM apply_quota_state WHERE motor = 'precio:amazon_mx'"
            " AND quota_date = (now() AT TIME ZONE 'UTC')::date"
        ).fetchone()[0]
        assert usadas == 2


@_skip_sin_pg
def test_reservar_cap_cero_no_inserta():
    """LA-cuota-cap0: `cap = 0` (o `extra = cap`) -> `False` y sin fila."""
    import app.precio.cuota as cuota_mod

    with _db() as (conn, _dsn):
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        assert cuota_mod.reservar(conn, platform="amazon_mx", cap=0) is False
        assert cuota_mod.reservar(conn, platform="amazon_us", cap=2, extra=2) is False
        assert (
            conn.execute(
                "SELECT count(*) FROM apply_quota_state WHERE quota_date = %s", (hoy,)
            ).fetchone()[0]
            == 0
        )


@_skip_sin_pg
def test_cupo_descuenta_reversas_al_repartir(monkeypatch):
    """El `cupo` que entra a `repartir` ya descuenta las reversas (planear, no solo cobrar)."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _config(conn, {**SETTINGS, "precio_cap_amazon_mx": 1})
        _price_obs(conn)
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-CD-B", ext="B0A50000CD")
        orig = _cambio_con_estado(conn, dec_b, _otro, estado="confirmado", dia=datetime.now(UTC))
        conn.execute("ALTER TABLE precio_cambio DISABLE TRIGGER precio_cambio_coherente")
        try:
            rev = conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
                " estado, enviado_at, es_reversa, reversa_de)"
                " VALUES (NULL, %s, 'amazon_mx', 110, 'MXN', 100, 'MXN', true,"
                " 'pendiente', %s, true, %s) RETURNING id",
                (_otro, datetime.now(UTC), orig),
            ).fetchone()[0]
        finally:
            conn.execute("ALTER TABLE precio_cambio ENABLE TRIGGER precio_cambio_coherente")
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', ack = '{\"http_status\": 202}'::jsonb"
            " WHERE id = %s",
            (rev,),
        )
        real = corrida_mod.repartir_cupo
        vistos = []

        def _espia(candidatos, *, cupo):
            vistos.append(cupo)
            return real(candidatos, cupo=cupo)

        monkeypatch.setattr(corrida_mod, "repartir_cupo", _espia)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert vistos == [0]
        assert (res.decisiones, res.escritas) == (1, 0)


@_skip_sin_pg
def test_cambio_real_de_hoy_no_es_reversa():
    """LA-reversas-todas: un cambio real de hoy no cuenta como reversa (cupo intacto)."""
    with _db() as (conn, _dsn):
        datos = _cadena(conn, mode="live")
        _price_obs(conn)
        _config(conn, {**SETTINGS, "precio_cap_amazon_mx": 1})
        _otro, dec_b = _andamio_hoy(conn, sku="SKU-RT-B", ext="B0A50000RT")
        _cambio_con_estado(conn, dec_b, _otro, estado="enviado", dia=datetime.now(UTC))
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)
        assert _decision_unica(conn)[0] == "subir"


@_skip_sin_pg
def test_ingreso_60d_ventana_declarada():
    """LA-ingreso60-ventana: `ingreso_60d` suma [hoy-75, hoy-16] (la de `u60`)."""
    from app.precio.corrida import _ingreso_60d

    with _db() as (conn, _dsn):
        prod = _producto(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        run_ledger, _fin = _run_ledger(conn)
        for atraso, importe in ((1, "100"), (20, "50")):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
                " product_id, quantity, amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, %s, 'MXN', %s)",
                (hoy - timedelta(days=atraso), datetime.now(UTC), prod, importe, run_ledger),
            )
        total = _ingreso_60d(conn, product_id=prod, platform="amazon_mx", hoy=hoy, moneda="MXN")
        assert total is not None and total.valor == Decimal("50")


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
def test_sin_prioridad_va_al_fondo():
    """LA-prioridad-none: candidata sin prioridad aplica despues de una con prioridad."""
    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-PN-A", ext="B0A50000PA", mode="live")
        b = _cadena(conn, sku="SKU-PN-B", ext="B0A50000PB", mode="live")
        _price_obs(conn, asin="B0A50000PA")
        _price_obs(conn, asin="B0A50000PB")
        run_ledger, _fin = _run_ledger(conn)
        _ventas_parejas(conn, a["producto"], qty=5, run_id=run_ledger)
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (2, 2)
        assert [ln.split(" ")[0] for ln in res.lineas] == [
            f"listing={a['listing']}",
            f"listing={b['listing']}",
        ]


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
                " r_valor, r_currency, decision_date, racha_senal)"
                " VALUES (%s, 'amazon_mx', 'subir', 'live', 0.30, 0.25,"
                " 100, 'MXN', 110, 'MXN', 110, 'MXN', 10, 'MXN', 20, 'MXN',"
                " 12, 'MXN', 5, 'MXN', 8, 'MXN', %s, 2)",
                (datos["listing"], ayer),
            )
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        run_ledger, _fin = _run_ledger(conn)
        _ventas_perdiendo(conn, datos["producto"], sku=datos["sku"], run_id=run_ledger)
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
    import app.precio.corrida as corrida_mod
    from app.spapi.precio_write import FormaParcheSinSellar
    from app.spapi.precio_write import cambiar_precio as _cambiar_real

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-R2-A", ext="B0A50000RA", mode="live")
        b = _cadena(conn, sku="SKU-R2-B", ext="B0A50000RB", mode="live")
        _price_obs(conn, asin="B0A50000RA")
        _price_obs(conn, asin="B0A50000RB")
        llamadas = []

        def _una_vez(*args, **kwargs):
            llamadas.append(1)
            if len(llamadas) == 1:
                raise FormaParcheSinSellar("forma sin sello")
            return _cambiar_real(*args, **kwargs)

        monkeypatch.setattr(corrida_mod, "cambiar_precio", _una_vez)
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
    """B.3: el gancho se llama una vez al terminar la fase 3 con los cuatro args."""
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
        real = corrida_mod.repartir_cupo

        def _muere(*a, **k):
            raise RuntimeError("muere a media corrida")

        monkeypatch.setattr(corrida_mod, "repartir_cupo", _muere)
        with pytest.raises(RuntimeError, match="muere a media corrida"):
            _corre(conn, red)
        monkeypatch.setattr(corrida_mod, "repartir_cupo", real)
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
def test_error_de_base_en_fase3_aborta_y_libera_lock(monkeypatch):
    """R1-fase3-traga-db: un `psycopg.Error` sale de `correr` y libera el lock."""
    from psycopg.errors import OperationalError

    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-R2-C", ext="B0A50000RC", mode="live")
        b = _cadena(conn, sku="SKU-R2-D", ext="B0A50000RD", mode="live")
        _price_obs(conn, asin="B0A50000RC")
        _price_obs(conn, asin="B0A50000RD")
        red = _RedFalsa(skus={a["evento"]: a["sku"], b["evento"]: b["sku"]})

        def _base_caida(*args, **kwargs):
            raise OperationalError("conexion perdida")

        monkeypatch.setattr(corrida_mod, "cambiar_precio", _base_caida)
        with pytest.raises(OperationalError):
            _corre(conn, red)
        monkeypatch.undo()
        res = _corre(conn, red)
        assert (res.decisiones, res.escritas) == (1, 1)


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
    from app.precio.corrida import _insumos_ventas

    with _db() as (conn, _dsn):
        prod = _producto(conn)
        run_costos, _fin = _run(conn)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, observed_at,"
            " product_id, quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, %s, %s, 1, 200, 'MXN', %s)",
            (hoy - timedelta(days=1), datetime.now(UTC), prod, run_costos),
        )
        insumos = _insumos_ventas(conn, product_id=prod, platform="amazon_mx", sku="SKU-R2-X")
        assert insumos.dia_cubierto_hasta is None


def test_cli_precio_imprime_errores_en_stderr(monkeypatch, capsys):
    """R1-cli-errores: cada error del resumen sale en stderr, con `scrub`."""
    from app import cli as cli_mod
    from app.redaction import register_secret

    register_secret("s3cr3t-r2")

    def _falso_correr(conn, platform, **kw):
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
    cli_mod.main(["precio", "--platform", "amazon_mx"])
    err = capsys.readouterr().err
    assert "listing=1 amazon_mx" in err and "s3cr3t-r2" not in err


def test_cli_precio_con_errores_sale_distinto_de_cero(monkeypatch, capsys):
    """K1: con `errores` en el resumen el CLI sale distinto de cero."""
    from app import cli as cli_mod

    def _falso_correr(conn, platform, **kw):
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
    codigo = cli_mod.main(["precio", "--platform", "amazon_mx"])
    assert codigo != 0
    assert "decisiones=1 escritas=0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# r3 (BRIEF-r3 sobre 650e51d)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_fase1_aisla_error_de_publicacion(monkeypatch):
    """B1: un `ValueError` en fase 1 deja `no_evaluado` y la corrida sigue."""
    import app.precio.corrida as corrida_mod

    with _db() as (conn, _dsn):
        a = _cadena(conn, sku="SKU-R3-A", ext="B0A50000RA", mode="live")
        b = _cadena(conn, sku="SKU-R3-B", ext="B0A50000RB", mode="live")
        _price_obs(conn, asin="B0A50000RA")
        _price_obs(conn, asin="B0A50000RB")
        real = corrida_mod.evaluar_senal
        llamadas = []

        def _revienta(insumos, **kw):
            llamadas.append(1)
            if len(llamadas) == 1:
                raise ValueError("venta invalida en 2026-09-01: -1")
            return real(insumos, **kw)

        monkeypatch.setattr(corrida_mod, "evaluar_senal", _revienta)
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
def test_fase3_guardas_explicitas_de_modo():
    """B7: la incoherente (`live` sin aplicar, `shadow` aplicada) ni PATCH ni virtual."""
    from app.precio.corrida import _fase3_uno
    from app.precio.tipos import Componentes, Decision, Importe

    with _db() as (conn, _dsn):
        live = _cadena(conn, sku="SKU-R3-F", ext="B0A50000RF", mode="live")
        sombra = _cadena(conn, sku="SKU-R3-G", ext="B0A50000RG", mode="shadow")
        _price_obs(conn, asin="B0A50000RF")
        _price_obs(conn, asin="B0A50000RG")
        cfg = conn.execute("SELECT id FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[0]
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        ahora = datetime.now(UTC)
        base = {
            "resultado": "subir",
            "motivo": None,
            "m_actual": Decimal("0.25"),
            "goal": Decimal("0.30"),
            "p_actual": Importe(Decimal("116"), "MXN"),
            "p_objetivo": Importe(Decimal("127.60"), "MXN"),
            "p_aplicado": Importe(Decimal("127.60"), "MXN"),
            "componentes": Componentes(
                p_actual=Importe(Decimal("116"), "MXN"),
                ingreso=Importe(Decimal("100"), "MXN"),
                costo=Importe(Decimal("60"), "MXN"),
                fees=Importe(Decimal("12"), "MXN"),
                envio=Importe(Decimal("5"), "MXN"),
                isr=Importe(Decimal("8"), "MXN"),
            ),
            "u15": None,
            "u60": None,
            "n15": None,
            "n60": None,
            "racha": None,
            "perdiendo": None,
            "prioridad": None,
        }
        comun = {
            "meta": None,
            "canal": None,
            "platform": "amazon_mx",
            "hoy": hoy,
            "cap": 20,
            "agotado": False,
            "parche_sin_sellar": False,
            "lector": None,
            "escritor": None,
            "construir_cuerpo": _cuerpo_falso,
            "limitador": None,
            "cfg_id": cfg,
            "ahora": ahora,
        }
        fria = Decision(**base, aplicado=False, mode="live")
        _dec, escritas, _ag, _parche, error, persistida = _fase3_uno(
            conn, decision=fria, lid=live["listing"], prod_id=live["producto"], **comun
        )
        assert (escritas, error, persistida) == (0, None, True)
        fria_shadow = Decision(**base, aplicado=True, mode="shadow")
        _dec2, escritas2, _ag2, _parche2, error2, persistida2 = _fase3_uno(
            conn, decision=fria_shadow, lid=sombra["listing"], prod_id=sombra["producto"], **comun
        )
        assert (escritas2, error2, persistida2) == (0, None, True)
        assert _dec2.motivo == "cuota"
        assert _cuenta_cambios(conn) == 0
