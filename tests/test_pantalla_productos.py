"""Contrato de la pantalla de productos sin clics (BIDS 02, P.4). Con base."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from test_fabrica_migracion import _entidad, _producto, db_fabrica
from test_impulso_io import _con_margen, _impulso_vivo, _observa
from test_migracion_0064 import SQL64
from test_migracion_0065 import SQL65
from test_migracion_0066 import SQL66
from test_schema import _postgres_obligatorio_ausente

from app import pantalla_productos as pp

HOY = dt.date(2026, 10, 9)
ROOT = Path(__file__).resolve().parents[1]
SQL20 = (ROOT / "migrations" / "0020_ads_producto_metrica.sql").read_text(encoding="utf-8")
SQL62 = (ROOT / "migrations" / "0062_bids02_placement.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _db_p4(prefijo):
    class _Db:
        def __enter__(self):
            self._ctx = db_fabrica(prefijo)
            conn = self._ctx.__enter__()
            conn.execute(SQL20)
            conn.execute(SQL62)
            conn.execute(SQL64)
            conn.execute(SQL65)
            conn.execute(SQL66)
            conn.execute(
                "INSERT INTO config_version(label,settings) VALUES ('fabrica','{}'::jsonb)"
            )
            return conn

        def __exit__(self, *exc):
            return self._ctx.__exit__(*exc)

    return _Db()


def _activa(conn, lid, external, *, camp=None, ag=None):
    """Bolsa con anuncio, ad group y campana en ENABLED."""
    camp_id = _entidad(conn, "amazon_mx", "campaign", camp or f"c-{external}")
    ag_id = _entidad(conn, "amazon_mx", "ad_group", ag or f"ag-{external}", parent=camp_id)
    pa_id = _entidad(conn, "amazon_mx", "product_ad", external, parent=ag_id, listing_id=lid)
    for ent in (camp_id, ag_id, pa_id):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (ent,),
        )
    return pa_id


def _ventas(conn, pid, unidades, *, hace_dias=10):
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity, amount,"
        " amount_currency, ingest_run_id) VALUES ('amazon_mx', 'sale', %s, %s, %s, %s, 'MXN',"
        " %s)",
        (HOY - dt.timedelta(days=hace_dias), pid, unidades, 100 + pid, run),
    )


def _clics_ads(conn, asin, sku, clics, *, hace_dias=10):
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO ads_product_metric_observation (platform, advertised_asin, advertised_sku,"
        " metric_date, observed_at, metric_currency, clicks, ingest_run_id)"
        " VALUES ('amazon_mx', %s, %s, %s, now(), 'MXN', %s, %s)",
        (asin, sku, HOY - dt.timedelta(days=hace_dias), clics, run),
    )


@_skip_db
def test_candidato_pide_ventas_pocos_clics_y_anuncio():
    """P.4: 2 pedidos + 19 clics + anuncio activo = candidato. Con 1
    pedido, 20 clics o sin anuncio activo, no."""
    with _db_p4("orbit_p4_cand") as conn:
        pid, lid = _producto(conn, sku="S-CAND", asin="B0CAND0001", seller_sku="SC-1")
        _activa(conn, lid, "ad-cand")
        _ventas(conn, pid, 2)
        _clics_ads(conn, "B0CAND0001", "SC-1", 19)
        pid1, lid1 = _producto(conn, sku="S-UNO", asin="B0CAND0002", seller_sku="SC-2")
        _activa(conn, lid1, "ad-uno")
        _ventas(conn, pid1, 1)
        _clics_ads(conn, "B0CAND0002", "SC-2", 5)
        pid20, lid20 = _producto(conn, sku="S-VEINTE", asin="B0CAND0003", seller_sku="SC-3")
        _activa(conn, lid20, "ad-veinte")
        _ventas(conn, pid20, 3)
        _clics_ads(conn, "B0CAND0003", "SC-3", 20)
        pid0, _lid0 = _producto(conn, sku="S-NAD", asin="B0CAND0004", seller_sku="SC-4")
        _ventas(conn, pid0, 5)
        _clics_ads(conn, "B0CAND0004", "SC-4", 3)
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert [c.product_id for c in pantalla.candidatos] == [pid]
        assert pantalla.candidatos[0].unidades_90d == 2
        assert pantalla.candidatos[0].clics_ads_60d == 19


@_skip_db
def test_candidato_dos_publicaciones_cuenta_una_y_sin_ads_none():
    """P.4: dos listings activos del mismo producto dan una fila; sin
    filas de ads, los clics son None."""
    with _db_p4("orbit_p4_dos") as conn:
        pid, lid = _producto(conn, sku="S-DOS", asin="B0CAND0005", seller_sku="SC-5")
        lid2 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0CAND0006', 'SC-6') RETURNING id",
            (pid,),
        ).fetchone()[0]
        _activa(conn, lid, "ad-dos-1")
        _activa(conn, lid2, "ad-dos-2")
        _ventas(conn, pid, 2)
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert [c.product_id for c in pantalla.candidatos] == [pid]
        assert pantalla.candidatos[0].clics_ads_60d is None
        assert pantalla.candidatos[0].listing_ids == (lid, lid2)


@_skip_db
def test_impulso_en_curso_trae_dia_y_veredicto():
    """P.4: el impulso en curso trae dia, metricas y frase; sin lectura
    el veredicto es None."""
    with _db_p4("orbit_p4_imp") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lanzado=HOY - dt.timedelta(days=5))
        _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=1))
        _observa(conn, ids["camps"][1], HOY - dt.timedelta(days=1))
        from app import impulso_io as io

        class _Pausa:
            def pausa(self, lote):
                pass

            def reanuda(self, lote):
                pass

        io.vigila(conn, _Pausa(), hoy=HOY)
        pid2, lid2 = _con_margen(conn, sku="SS-2", asin="B0AAAAAAAB", seller_sku="SS-2", desfase=20)
        sin = _impulso_vivo(conn, pid=pid2, lid=lid2, lote="impulso-sin-lectura")
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        por_id = {i.impulso_id: i for i in pantalla.impulsos}
        visto = por_id[ids["impulso"]]
        assert visto.dia == 6
        assert (visto.impresiones, visto.clics, visto.pedidos) == (200, 10, 0)
        assert visto.veredicto == "en_curso"
        assert visto.frase.startswith("Día 6 de 14")
        assert "sigue aprendiendo" in visto.frase
        assert por_id[sin["impulso"]].veredicto is None
        assert por_id[sin["impulso"]].frase == "Aún sin lectura."


@_skip_db
def test_impulso_clics_fuera_de_amazon():
    """P.4: suma los clics fuera_de_amazon de sus campanas desde el
    lanzamiento; sin filas trae None."""
    with _db_p4("orbit_p4_fuera") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid)
        for camp, clics in ((ids["camps"][0], 1), (ids["camps"][1], 2)):
            conn.execute(
                "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
                " metric_date, observed_at, metric_currency, clicks, source_report_id)"
                " VALUES ('amazon_mx', %s, 'fuera_de_amazon', %s, now(), 'MXN', %s, 'r1')",
                (camp, HOY - dt.timedelta(days=1), clics),
            )
        conn.execute(
            "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
            " metric_date, observed_at, metric_currency, clicks, source_report_id)"
            " VALUES ('amazon_mx', %s, 'arriba_de_busqueda', %s, now(), 'MXN', 50, 'r1')",
            (ids["camps"][0], HOY - dt.timedelta(days=1)),
        )
        pid2, lid2 = _con_margen(conn, sku="SS-2", asin="B0AAAAAAAB", seller_sku="SS-2", desfase=20)
        sin = _impulso_vivo(conn, pid=pid2, lid=lid2, lote="impulso-sin-placement")
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        por_id = {i.impulso_id: i for i in pantalla.impulsos}
        assert por_id[ids["impulso"]].clics_fuera_de_amazon == 3
        assert por_id[sin["impulso"]].clics_fuera_de_amazon is None


@_skip_db
def test_sin_pedido_y_sin_producto():
    """P.4: sin pedido en 330 dias lista; con pedido reciente no; los
    anuncios activos sin listing se cuentan."""
    with _db_p4("orbit_p4_11m") as conn:
        pid, lid = _producto(conn, sku="S-VIEJO", asin="B0OLD00001", seller_sku="SO-1")
        _activa(conn, lid, "ad-viejo")
        _ventas(conn, pid, 1, hace_dias=400)
        pid2, lid2 = _producto(conn, sku="S-NUEVO", asin="B0OLD00002", seller_sku="SO-2")
        _activa(conn, lid2, "ad-nuevo")
        _ventas(conn, pid2, 1, hace_dias=100)
        camp = _entidad(conn, "amazon_mx", "campaign", "c-huerf")
        ag = _entidad(conn, "amazon_mx", "ad_group", "ag-huerf", parent=camp)
        pa = _entidad(conn, "amazon_mx", "product_ad", "ad-huerf", parent=ag, listing_id=None)
        for ent in (camp, ag, pa):
            conn.execute(
                "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
                " VALUES (%s, 'ENABLED', now())",
                (ent,),
            )
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert [s.product_id for s in pantalla.sin_pedido] == [pid]
        assert pantalla.anuncios_sin_producto == 1


def test_frases_puras():
    """P.4: las frases del diseno, sin base."""
    from decimal import Decimal

    frase = pp.frase_en_curso(
        dia=6,
        plazo=14,
        impresiones=1840,
        clics=14,
        gasto=Decimal("48"),
        tope=Decimal("350"),
        moneda="MXN",
        pedidos=0,
    )
    assert frase == (
        "Día 6 de 14 · 1,840 impresiones · 14 clics · 48 de 350 MXN"
        " · sin pedidos · sigue aprendiendo"
    )
    assert pp.frase_final("vende") == "Vende. Sigue encendido y ya lo cuida el motor."
    assert pp.frase_final("clics_sin_ventas") == "Recibió clics y no vendió. Orbit lo pausó."
    assert pp.frase_fuera_de_amazon(3, 14) == "3 de sus 14 clics vinieron de fuera de Amazon."
    assert pp.frase_fuera_de_amazon(None, 14) is None
