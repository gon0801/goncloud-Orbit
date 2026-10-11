"""Ingesta del reporte por placement (BIDS 02, V.2).

spCampaigns agrupado por campaignPlacement, DAILY, en corrida propia
(`SOURCE_PLACEMENTS`) para que un fallo aqui no tire los cuatro reportes
principales. La prueba 2 de PRUEBAS.md lo acepto en vivo; rechazo
`topOfSearchImpressionShare` con 400.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import socket
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

import app.ads.reports
from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.placements import (
    PLACEMENTS_CFG,
    SOURCE_PLACEMENTS,
    _planea_filas_placements,
    sync_placements,
)
from app.ads.reports import AdsReportsError, solicitar_reporte
from app.ads.structure import PerfilAds

ROOT = Path(__file__).resolve().parents[1]
SQL40 = (ROOT / "migrations" / "0040_ads_report_result.sql").read_text(encoding="utf-8")


def _sql62() -> str:
    return (ROOT / "migrations" / "0062_bids02_placement.sql").read_text(encoding="utf-8")


_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

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
            client_id="fake-id", client_secret="fake-secret", refresh_token="fake-token"
        ),
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _token(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-access", "expires_in": 3600})


def _fila(**cambios):
    base = {
        "date": "2026-10-05",
        "campaignId": 9001,
        "placementClassification": "Top of Search on-Amazon",
        "impressions": 100,
        "clicks": 10,
        "cost": 50.25,
        "purchases30d": 2,
        "sales30d": 400.0,
    }
    base.update(cambios)
    return base


# ---------------------------------------------------------------------------
# (a) UNITARIOS - cuerpo del reporte
# ---------------------------------------------------------------------------


def test_body_reporte_placements():
    """spCampaigns con groupBy [campaign, campaignPlacement], DAILY, las 8
    columnas pedidas y SIN topOfSearchImpressionShare (400 vivo)."""
    registro: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.method == "POST" and request.url.path == "/reporting/reports":
            registro.append(json.loads(request.content))
            return httpx.Response(200, json={"reportId": "rep-place", "status": "PENDING"})
        raise AssertionError(f"llamada inesperada: {request.method} {request.url}")

    perfil = PerfilAds(
        profile_id=202,
        country="MX",
        currency_code="MXN",
        account_type="seller",
        valid_payment_method=True,
        account_name="Cuenta Test",
        aceptado=True,
        platform="amazon_mx",
        moneda="MXN",
    )
    client = _cliente(handler)
    assert (
        solicitar_reporte(
            client, perfil, PLACEMENTS_CFG, dt.date(2026, 10, 5), dt.date(2026, 10, 5)
        )
        == "rep-place"
    )
    assert len(registro) == 1
    cfg = registro[0]["configuration"]
    assert cfg["reportTypeId"] == "spCampaigns"
    assert cfg["groupBy"] == ["campaign", "campaignPlacement"]
    assert cfg["timeUnit"] == "DAILY"
    assert cfg["columns"] == [
        "date",
        "campaignId",
        "placementClassification",
        "impressions",
        "clicks",
        "cost",
        "purchases30d",
        "sales30d",
    ]
    assert "topOfSearchImpressionShare" not in cfg["columns"]


# ---------------------------------------------------------------------------
# (b) UNITARIOS - planificador puro
# ---------------------------------------------------------------------------


def test_clasificaciones_mapean_a_ubicacion():
    filas = [
        _fila(placementClassification="Top of Search on-Amazon"),
        _fila(placementClassification="Other on-Amazon"),
        _fila(placementClassification="Detail Page on-Amazon"),
        _fila(placementClassification="Off Amazon"),
    ]
    plan, skips = _planea_filas_placements(filas, entidades={"9001": 7})
    assert [f.placement for f in plan] == [
        "arriba_de_busqueda",
        "resto_de_busqueda",
        "paginas_de_producto",
        "fuera_de_amazon",
    ]
    assert skips == Counter()
    assert plan[0].ad_entity_id == 7
    assert plan[0].cost == Decimal("50.25")


def test_filas_malas_saltan_con_motivo():
    filas = [
        _fila(placementClassification="Luna on-Amazon"),
        _fila(placementClassification={"lugar": "x"}),
        _fila(placementClassification=["Top of Search on-Amazon"]),
        _fila(campaignId=4242),
        _fila(cost=-1),
        _fila(clicks=-2),
        _fila(date="no-es-fecha"),
        _fila(impressions="muchas"),
    ]
    plan, skips = _planea_filas_placements(filas, entidades={"9001": 7})
    assert plan == []
    assert skips == Counter(
        {
            "fila de placements con placement desconocido": 3,
            "fila de placements de campana desconocida": 1,
            "fila de placements con metrica negativa": 2,
            "fila de placements con date invalida": 1,
            "fila de placements con metrica no numerica": 1,
        }
    )


def test_metrica_ausente_queda_null_y_campana_numerica_casa():
    plan, skips = _planea_filas_placements(
        [_fila(clicks=None, campaignId=9001)], entidades={"9001": 7}
    )
    assert skips == Counter()
    assert plan[0].clicks is None
    assert plan[0].ad_entity_id == 7


def test_fila_sin_ninguna_metrica_salta_y_cero_si_pasa():
    vacia = _fila(impressions=None, clicks=None, cost=None, purchases30d=None, sales30d=None)
    plan, skips = _planea_filas_placements([vacia], entidades={"9001": 7})
    assert plan == []
    assert skips == Counter({"fila de placements sin metricas": 1})
    plan, skips = _planea_filas_placements(
        [_fila(impressions=0, clicks=0, cost=0, purchases30d=0, sales30d=0)],
        entidades={"9001": 7},
    )
    assert skips == Counter()
    assert len(plan) == 1


# ---------------------------------------------------------------------------
# (c) INTEGRACION
# ---------------------------------------------------------------------------


@contextmanager
def _db_placements(prefijo: str):
    import psycopg
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL40)
        conn.execute(_sql62())
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _siembra_campana(conn, platform: str, external: str) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
        " RETURNING id",
        (platform, external),
    ).fetchone()[0]


def _observadas(conn) -> int:
    return conn.execute("SELECT count(*) FROM ads_placement_observation").fetchone()[0]


def _transporte_ok(filas_por_scope: dict, prefijo: str = "R-PLACE"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.url.path == "/v2/profiles":
            return httpx.Response(200, json=PERFILES_API)
        if request.method == "POST" and request.url.path == "/reporting/reports":
            scope = request.headers.get("Amazon-Advertising-API-Scope")
            reporte = f"{prefijo}-{scope}"
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
        scope = reporte.rsplit("-", 1)[1]
        filas = filas_por_scope.get(scope, [])
        return httpx.Response(200, content=gzip.compress(json.dumps(filas).encode("utf-8")))

    return _cliente(handler)


@_skip_db
def test_mismo_reporte_dos_veces_no_agrega_filas():
    ayer = dt.date(2026, 10, 5)
    filas = {"202": [_fila()], "101": [_fila(campaignId=8001)]}
    with _db_placements("orbit_pl_dedupe") as conn:
        _siembra_campana(conn, "amazon_mx", "9001")
        _siembra_campana(conn, "amazon_us", "8001")
        escritos1 = sync_placements(
            conn, _transporte_ok(filas), desde=ayer, hasta=ayer, sleep=lambda s: None
        )
        escritos2 = sync_placements(
            conn, _transporte_ok(filas), desde=ayer, hasta=ayer, sleep=lambda s: None
        )
        assert (escritos1, escritos2) == (2, 0)
        assert _observadas(conn) == 2


@_skip_db
def test_dos_reportes_mismo_dia_dejan_dos_observaciones_y_distinct_on_una():
    ayer = dt.date(2026, 10, 5)
    filas = {"202": [_fila(cost=50.25)], "101": []}
    with _db_placements("orbit_pl_bitemp") as conn:
        camp = _siembra_campana(conn, "amazon_mx", "9001")
        assert sync_placements(conn, _transporte_ok(filas, "R-UNO"), desde=ayer, hasta=ayer) == 1
        assert sync_placements(conn, _transporte_ok(filas, "R-DOS"), desde=ayer, hasta=ayer) == 1
        assert _observadas(conn) == 2
        vigentes = conn.execute(
            "SELECT DISTINCT ON (ad_entity_id, placement, metric_date) cost, source_report_id"
            " FROM ads_placement_observation WHERE ad_entity_id = %s"
            " ORDER BY ad_entity_id, placement, metric_date, observed_at DESC",
            (camp,),
        ).fetchall()
        assert len(vigentes) == 1


@_skip_db
def test_fallo_de_transporte_sella_run_propia_y_no_toca_principal():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.url.path == "/v2/profiles":
            return httpx.Response(200, json=PERFILES_API)
        raise AssertionError("red caida")

    with _db_placements("orbit_pl_fallo") as conn:
        with pytest.raises(AssertionError):
            sync_placements(
                conn, _cliente(handler), desde=dt.date(2026, 10, 5), hasta=dt.date(2026, 10, 5)
            )
        runs = conn.execute("SELECT source, ok FROM ingest_run").fetchall()
        assert runs == [(SOURCE_PLACEMENTS, False)]
        assert "amazon_ads_reports_v3" not in [r[0] for r in runs]


@_skip_db
def test_cero_perfiles_sella_una_sola_vez_y_avisa_fallando(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.url.path == "/v2/profiles":
            return httpx.Response(200, json=[])
        raise AssertionError("llamada inesperada")

    with _db_placements("orbit_pl_cero") as conn:
        with pytest.raises(AdsReportsError, match="ningun perfil aceptado"):
            sync_placements(
                conn, _cliente(handler), desde=dt.date(2026, 10, 5), hasta=dt.date(2026, 10, 5)
            )
        runs = conn.execute("SELECT source, ok FROM ingest_run").fetchall()
        assert runs == [(SOURCE_PLACEMENTS, False)]
        fallos = conn.execute(
            "SELECT count(*) FROM ads_report_result WHERE status = 'global_failed'"
        ).fetchone()[0]
        assert fallos == 1
        # Con el raise dentro del try, el except de fase API reintentaba el
        # sello y moria con este warning (doble procesamiento de la run).
        assert "sello tambien fallo" not in caplog.text


@_skip_db
def test_moneda_de_cada_fila_es_la_de_su_perfil():
    ayer = dt.date(2026, 10, 5)
    filas = {"202": [_fila()], "101": [_fila(campaignId=8001)]}
    with _db_placements("orbit_pl_moneda") as conn:
        _siembra_campana(conn, "amazon_mx", "9001")
        _siembra_campana(conn, "amazon_us", "8001")
        assert (
            sync_placements(
                conn, _transporte_ok(filas), desde=ayer, hasta=ayer, sleep=lambda s: None
            )
            == 2
        )
        monedas = dict(
            conn.execute(
                "SELECT platform, metric_currency FROM ads_placement_observation"
            ).fetchall()
        )
        assert monedas == {"amazon_mx": "MXN", "amazon_us": "USD"}


# ---------------------------------------------------------------------------
# (d) MAIN
# ---------------------------------------------------------------------------


def test_main_placements_excluyente_con_productos(monkeypatch, capsys):
    from app.ads.reports import main

    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert main(["--placements", "--productos"]) == 2
    err = capsys.readouterr().err
    assert "--placements" in err and "--productos" in err


def test_main_placements_sin_dsn_sale_2(monkeypatch, capsys):
    from app.ads.reports import main

    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert main(["--placements"]) == 2
    assert "ORBIT_DSN_INGEST no esta definido" in capsys.readouterr().err


@_skip_db
def test_main_placements_fallo_devuelve_1_y_no_llama_sync_metrics(monkeypatch):
    from app.ads.reports import main

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _token(request)
        if request.url.path == "/v2/profiles":
            return httpx.Response(200, json=PERFILES_API)
        raise AssertionError("red caida")

    with _db_placements("orbit_pl_main") as conn:
        from urllib.parse import urlsplit, urlunsplit

        partes = urlsplit(_test_dsn())
        dsn_tmp = urlunsplit((partes.scheme, partes.netloc, f"/{conn.info.dbname}", "", ""))
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_tmp)
        monkeypatch.setattr("app.ads.reports.AdsClient", lambda creds: _cliente(handler))
        monkeypatch.setattr(
            app.ads.reports.AdsCredentials,
            "from_secrets_dir",
            classmethod(lambda cls: None),
        )
        llamadas = []
        monkeypatch.setattr("app.ads.reports.sync_metrics", lambda *a, **k: llamadas.append(1))
        assert main(["--placements", "--fecha", "2026-10-05", "--fecha-fin", "2026-10-05"]) == 1
        assert llamadas == []


@_skip_db
def test_main_placements_preflight_roto_sella_run_propia(monkeypatch):
    from urllib.parse import urlsplit, urlunsplit

    from app.ads.reports import main

    def _rotas(cls):
        raise RuntimeError("credenciales rotas")

    with _db_placements("orbit_pl_preflight") as conn:
        partes = urlsplit(_test_dsn())
        dsn_tmp = urlunsplit((partes.scheme, partes.netloc, f"/{conn.info.dbname}", "", ""))
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_tmp)
        monkeypatch.setattr(app.ads.reports.AdsCredentials, "from_secrets_dir", classmethod(_rotas))
        assert main(["--placements"]) == 1
        runs = conn.execute("SELECT source, ok FROM ingest_run").fetchall()
        assert runs == [(SOURCE_PLACEMENTS, False)]


@_skip_db
def test_main_placements_exito_devuelve_0(monkeypatch):
    """v2-05: con --placements y todo bien, tampoco corren los 4 reportes."""
    from urllib.parse import urlsplit, urlunsplit

    from app.ads.reports import main

    filas = {"202": [_fila()], "101": [_fila(campaignId=8001)]}
    with _db_placements("orbit_pl_exito") as conn:
        _siembra_campana(conn, "amazon_mx", "9001")
        _siembra_campana(conn, "amazon_us", "8001")
        partes = urlsplit(_test_dsn())
        dsn_tmp = urlunsplit((partes.scheme, partes.netloc, f"/{conn.info.dbname}", "", ""))
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_tmp)
        monkeypatch.setattr("app.ads.reports.AdsClient", lambda creds: _transporte_ok(filas))
        monkeypatch.setattr(
            app.ads.reports.AdsCredentials,
            "from_secrets_dir",
            classmethod(lambda cls: None),
        )

        def _espia(*a, **k):
            raise AssertionError("sync_metrics no debe correr con --placements")

        monkeypatch.setattr("app.ads.reports.sync_metrics", _espia)
        assert main(["--placements", "--fecha", "2026-10-05", "--fecha-fin", "2026-10-05"]) == 0
        assert _observadas(conn) == 2


@_skip_db
def test_metrica_ausente_se_guarda_null_en_la_base():
    """v2-09/v2-10: la metrica ausente llega NULL hasta la BASE (no 0)."""
    ayer = dt.date(2026, 10, 5)
    with _db_placements("orbit_pl_null") as conn:
        _siembra_campana(conn, "amazon_mx", "9001")
        filas = {"202": [_fila(clicks=None, cost=None, purchases30d=None, sales30d=None)]}
        assert sync_placements(conn, _transporte_ok(filas), desde=ayer, hasta=ayer) == 1
        assert conn.execute(
            "SELECT impressions, clicks, cost, orders, ad_revenue FROM ads_placement_observation"
        ).fetchall() == [(100, None, None, None, None)]


@_skip_db
def test_fallo_en_fase_de_base_sella_ok_false(monkeypatch):
    """v2-20: un fallo en la fase de base sella la run ok=false y no deja filas."""
    ayer = dt.date(2026, 10, 5)

    def _revienta(*a, **k):
        raise RuntimeError("base caida a media ingesta")

    with _db_placements("orbit_pl_dbfail") as conn:
        _siembra_campana(conn, "amazon_mx", "9001")
        monkeypatch.setattr("app.ads.placements.ingest_placements", _revienta)
        with pytest.raises(RuntimeError, match="base caida"):
            sync_placements(conn, _transporte_ok({"202": [_fila()]}), desde=ayer, hasta=ayer)
        assert conn.execute("SELECT source, ok FROM ingest_run").fetchall() == [
            (SOURCE_PLACEMENTS, False)
        ]
        assert _observadas(conn) == 0


def test_fila_futura_y_fuera_de_rango_saltan():
    """NB5: con rango, lo futuro y lo fuera de [desde, hasta] salta."""
    dia = dt.date(2026, 10, 5)
    plan, skips = _planea_filas_placements(
        [
            _fila(date="2026-10-05"),
            _fila(date="2026-12-25"),
            _fila(date="2026-01-01"),
        ],
        entidades={"9001": 7},
        desde=dia,
        hasta=dia,
        hoy=dt.date(2026, 10, 6),
    )
    assert [f.metric_date for f in plan] == [dia]
    assert skips == Counter(
        {
            "fila de placements con metric_date futura": 1,
            "fila de placements con metric_date fuera del rango": 1,
        }
    )


@_skip_db
def test_sync_salta_fechas_que_no_pidio():
    """NB5: el sync escribe solo lo del rango pedido."""
    hoy = dt.datetime.now(dt.UTC).date()
    ayer = hoy - dt.timedelta(days=1)
    filas = {
        "202": [
            _fila(date=ayer.isoformat()),
            _fila(date=(hoy + dt.timedelta(days=5)).isoformat()),
            _fila(date=(hoy - dt.timedelta(days=60)).isoformat()),
        ]
    }
    with _db_placements("orbit_pl_rango") as conn:
        _siembra_campana(conn, "amazon_mx", "9001")
        assert sync_placements(conn, _transporte_ok(filas), desde=ayer, hasta=ayer) == 1
        fechas = conn.execute(
            "SELECT DISTINCT metric_date FROM ads_placement_observation"
        ).fetchall()
        assert fechas == [(ayer,)]
        motivo = conn.execute("SELECT skip_reason FROM ingest_run").fetchone()[0]
        assert "metric_date futura" in motivo
        assert "fuera del rango" in motivo
