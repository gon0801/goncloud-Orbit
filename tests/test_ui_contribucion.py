"""UI de contribucion por campana (ORBIT 06 · 1.4).

DoD: el rango se ve como rango; ausencia NO se renderiza como cero; una
plataforma sin contribucion no rompe la pantalla. Patron de tests/test_ui.py
(quota 1.5): render HTML por el camino real con el endpoint fakeado o DB.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_contribucion_entidad import _aplicar_esquema, _entidad, _semilla_mx_completa
from test_schema import _postgres_obligatorio_ausente

from app import ui
from app.api import _conexion_lectura
from app.main import app

ETIQUETA = "contribucion pre-cargos · no decisoria"


def _item_con_rango(
    *,
    nombre: str = "Camp MX",
    sin_halo: str = "26.0000",
    con_halo: str = "50.0000",
    moneda: str = "MXN",
    fx_source: str | None = None,
) -> dict:
    return {
        "ad_entity_id": 1,
        "nombre": nombre,
        "contrib_sin_halo": sin_halo,
        "contrib_con_halo": con_halo,
        "moneda": moneda,
        "metric_date_from": "2026-05-28",
        "metric_date_to": "2026-08-16",
        "fx_source": fx_source,
        "motivo_ausencia": None,
        "etiqueta": ETIQUETA,
    }


def _item_ausente(*, motivo: str = "serie_incompleta", nombre: str = "Camp US") -> dict:
    return {
        "ad_entity_id": 2,
        "nombre": nombre,
        "contrib_sin_halo": None,
        "contrib_con_halo": None,
        "moneda": "USD",
        "metric_date_from": "2026-05-28",
        "metric_date_to": "2026-08-16",
        "fx_source": None,
        "motivo_ausencia": motivo,
        "etiqueta": ETIQUETA,
    }


def _payload(
    mx_items: list[dict] | None = None,
    us_items: list[dict] | None = None,
) -> dict:
    return {
        "plataformas": {
            "amazon_mx": {
                "ventana": {"desde": "2026-05-28", "hasta": "2026-08-16"},
                "filas": [] if mx_items is None else mx_items,
            },
            "amazon_us": {
                "ventana": {"desde": "2026-05-28", "hasta": "2026-08-16"},
                "filas": [] if us_items is None else us_items,
            },
        }
    }


def _contribucion_html_fakeado(monkeypatch, payload: dict) -> str:
    monkeypatch.setattr(ui.dash, "contribucion_campanas", lambda conn: payload)
    app.dependency_overrides[_conexion_lectura] = lambda: None
    try:
        return TestClient(app).get("/contribucion").text
    finally:
        app.dependency_overrides.pop(_conexion_lectura, None)


def _fila_de(html: str, nombre: str) -> str:
    filas = [f for f in html.split("<tr>") if f">{nombre}</td>" in f]
    assert len(filas) == 1, f"la campana {nombre} debe aparecer en una sola fila"
    return filas[0]


def test_ui_contribucion_rango_se_ve_como_rango(monkeypatch):
    """DoD: contrib_sin..contrib_con visible como rango, con moneda y etiqueta."""
    html = _contribucion_html_fakeado(
        monkeypatch,
        _payload(mx_items=[_item_con_rango()]),
    )
    assert 'data-pantalla="contribucion"' in html
    assert ETIQUETA in html
    fila = _fila_de(html, "Camp MX")
    assert "26.0000" in fila and "50.0000" in fila
    assert "26.0000 .. 50.0000" in fila or (
        "26.0000" in fila and ".." in fila and "50.0000" in fila
    )
    assert "MXN" in fila
    assert "2026-05-28" in html and "2026-08-16" in html


def test_ui_contribucion_ausencia_no_se_pinta_como_cero(monkeypatch):
    """Regla 3 en presentacion (mismo criterio que quota 1.5): sin contribucion
    -> guion CON etiqueta del motivo dominante, jamas 0."""
    html = _contribucion_html_fakeado(
        monkeypatch,
        _payload(us_items=[_item_ausente(motivo="catalogo_parcial")]),
    )
    fila = _fila_de(html, "Camp US")
    assert "—" in fila
    assert "catalogo parcial" in fila or "catalogo_parcial" in fila
    assert ">0<" not in fila.replace("2026", ""), "ausencia jamas se pinta como cero"
    assert "0.0000" not in fila


def test_ui_contribucion_plataforma_vacia_no_rompe(monkeypatch):
    """DoD: una plataforma sin filas de contribucion sigue renderizando."""
    html = _contribucion_html_fakeado(
        monkeypatch,
        _payload(mx_items=[_item_con_rango()], us_items=[]),
    )
    assert "<h2>amazon_mx" in html
    assert "<h2>amazon_us" in html
    assert "sin campanas con actividad" in html.lower() or "—" in html


def test_ui_contribucion_fx_source_visible_cuando_aplica(monkeypatch):
    html = _contribucion_html_fakeado(
        monkeypatch,
        _payload(
            us_items=[
                _item_con_rango(
                    nombre="Camp US",
                    sin_halo="10.0000",
                    con_halo="20.0000",
                    moneda="USD",
                    fx_source="nearest_prior",
                )
            ]
        ),
    )
    fila = _fila_de(html, "Camp US")
    assert "nearest_prior" in fila or "FX aproximado" in fila


def test_ui_contribucion_edad_de_dato_en_la_fila(monkeypatch):
    """DoD: la edad de dato es POR campana (metric_date_from/to en SU fila),
    no solo la ventana a nivel plataforma. Fechas del item DISTINTAS de la
    ventana para que el rojo/verde sea discriminant (la ventana se pinta en
    el encabezado de plataforma)."""
    item = _item_con_rango()
    item["metric_date_from"] = "2026-06-01"
    item["metric_date_to"] = "2026-08-10"
    html = _contribucion_html_fakeado(
        monkeypatch,
        _payload(mx_items=[item]),
    )
    fila = _fila_de(html, "Camp MX")
    assert "2026-06-01" in fila and "2026-08-10" in fila


def test_ui_contribucion_rango_parcial_declara_motivo(monkeypatch):
    """Cobertura parcial (D4: no SUM parcial disfrazado de completo): con
    rango presente Y hijas ausentes, el motivo se ve JUNTO al rango."""
    item = _item_con_rango()
    item["motivo_ausencia"] = "catalogo_parcial"
    html = _contribucion_html_fakeado(
        monkeypatch,
        _payload(mx_items=[item]),
    )
    fila = _fila_de(html, "Camp MX")
    assert "26.0000 .. 50.0000" in fila
    assert "catalogo parcial" in fila or "catalogo_parcial" in fila


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ui_contribucion_integracion_rollup_campana(monkeypatch):
    """Rollup D2: la campana padre SUMA la contribucion de sus hijas."""
    psycopg = pytest.importorskip("psycopg")
    import os
    import socket

    from psycopg import sql as pgsql
    from psycopg.conninfo import make_conninfo
    from test_schema import _test_dsn

    dsn = _test_dsn()
    name = f"orbit_ui_contrib_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(name)))
        conn = psycopg.connect(dsn, dbname=name, autocommit=True)
        _aplicar_esquema(conn)
        s = _semilla_mx_completa(conn)
        kw2 = _entidad(conn, "amazon_mx", "keyword", "KW-MX-2", s["ag"])
        rid = s["rid"]
        conn.execute(
            "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
            " metric_currency, cost, ad_revenue, revenue_same_sku, ingest_run_id)"
            " VALUES (%s, %s, now(), 'MXN', 5, 40, 20, %s)",
            (kw2, s["dia"], rid),
        )
        fila = conn.execute(
            "SELECT SUM(contrib_sin_halo), SUM(contrib_con_halo)"
            " FROM v_contribucion_entidad v"
            " JOIN ad_entity kw ON kw.id = v.ad_entity_id"
            " JOIN ad_entity ag ON ag.id = kw.parent_id"
            " JOIN ad_entity cam ON cam.id = ag.parent_id"
            " WHERE cam.id = %s",
            (s["cam"],),
        ).fetchone()
        assert fila[0] is not None
        esperado_sin = Decimal(fila[0])
        esperado_con = Decimal(fila[1])

        monkeypatch.setenv("ORBIT_DSN_READ", make_conninfo(dsn, dbname=name))
        html = TestClient(app).get("/contribucion").text
        assert 'data-pantalla="contribucion"' in html
        assert str(esperado_sin).split(".")[0] in html or f"{esperado_sin:.4f}" in html
        assert str(esperado_con).split(".")[0] in html or f"{esperado_con:.4f}" in html
        assert ETIQUETA in html
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(name))
        )
        admin.close()
