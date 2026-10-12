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
from test_migracion_0068 import SQL68
from test_migracion_0069 import SQL69
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
            conn.execute(SQL68)
            conn.execute(SQL69)
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
def test_candidatos_usan_las_constantes_del_modulo(monkeypatch):
    """R09-r2 (regla 2): los umbrales salen de las constantes, no de
    literales en el SQL: con ventana de 5 dias, ventas de hace 10 no
    cuentan."""
    with _db_p4("orbit_p4_cte") as conn:
        pid, lid = _producto(conn, sku="S-CTE", asin="B0CTE00001", seller_sku="SC-CTE")
        _activa(conn, lid, "ad-cte")
        _ventas(conn, pid, 2)
        _clics_ads(conn, "B0CTE00001", "SC-CTE", 3)
        assert [c.product_id for c in pp.lee_productos(conn, "amazon_mx", HOY).candidatos] == [pid]
        monkeypatch.setattr(pp, "DIAS_PEDIDOS_CANDIDATO", 5)
        assert pp.lee_productos(conn, "amazon_mx", HOY).candidatos == ()


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
        for k in range(5, 0, -1):  # 5 dias con dato (B3: el dia sigue al dato)
            _observa(conn, ids["camps"][0], HOY - dt.timedelta(days=k))
            _observa(conn, ids["camps"][1], HOY - dt.timedelta(days=k))
        from app import impulso_io as io

        class _Pausa:
            def pausa(self, lote):
                pass

            def pausa_huerfanas(self, lote):
                return False

            def reanuda(self, lote):
                pass

        io.vigila(conn, _Pausa(), hoy=HOY)
        pid2, lid2 = _con_margen(conn, sku="SS-2", asin="B0AAAAAAAB", seller_sku="SS-2", desfase=20)
        sin = _impulso_vivo(conn, pid=pid2, lid=lid2, lote="impulso-sin-lectura")
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        por_id = {i.impulso_id: i for i in pantalla.impulsos}
        visto = por_id[ids["impulso"]]
        assert visto.dia == 6
        assert (visto.impresiones, visto.clics, visto.pedidos) == (1000, 50, 0)
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


def _retiro_abierto(conn, pa, ad, motivo):
    return conn.execute(
        "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, retirado_el, actor) VALUES ('amazon_mx', %s, 'pausa',"
        " 'lote-r8', NULL, %s, %s, 'ag-8', 'c-8', 'SS-8', 'B0XXXXXXXX', 'adGroupId=1',"
        " now(), 'dueno')"
        " RETURNING id",
        (motivo, pa, ad),
    ).fetchone()[0]


@_skip_db
def test_retiros_abiertos_sin_impulso_aparecen():
    """R09 B8: retiros abiertos sin impulso (sin_pedidos/subfamilia) salen
    en retiros_abiertos; al reponer desaparecen."""
    with _db_p4("orbit_p4_r8") as conn:
        _pid, lid = _producto(conn, sku="SS-8", asin="B0XXXXXXXX", seller_sku="SS-8")
        pa = _activa(conn, lid, "ad-8")
        rid = _retiro_abierto(conn, pa, "ad-8", "sin_pedidos")
        conn.commit()
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert [(r.retiro_id, r.motivo, r.ad_id_externo) for r in pantalla.retiros_abiertos] == [
            (rid, "sin_pedidos", "ad-8")
        ]
        assert pantalla.como_dict()["retiros_abiertos"][0]["retiro_id"] == rid
        conn.execute(
            "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo,"
            " repuesto_el, actor) VALUES (%s, 'ad-8', 'ENABLED', now(), 'dueno')",
            (rid,),
        )
        conn.commit()
        assert pp.lee_productos(conn, "amazon_mx", HOY).retiros_abiertos == ()


def _lectura_p4(
    conn,
    imp,
    corte,
    *,
    dias,
    impr,
    clics,
    gasto="10",
    pedidos=0,
    veredicto="en_curso",
    final=False,
    hecho="nada",
):
    conn.execute(
        "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
        " impresiones, clics, gasto, pedidos, veredicto, es_final, hecho)"
        " VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s)",
        (imp, corte, dias, impr, clics, gasto, pedidos, veredicto, final, hecho),
    )


@_skip_db
def test_r09r4_pp05_retiros_mx_y_us_no_se_mezclan():
    """R09-r4 B6 (PP05): retiros abiertos de MX y US: cada pantalla
    muestra solo los suyos."""
    with _db_p4("orbit_p4_r4pp05") as conn:
        _pid, lid = _producto(conn, sku="SS-5", asin="B0PP050001", seller_sku="SS-5")
        _pidu, lidu = _producto(
            conn, sku="SS-5U", asin="B0PP050002", platform="amazon_us", seller_sku="SS-5U"
        )
        pa = _activa(conn, lid, "ad-pp05mx")
        _retiro_abierto(conn, pa, "ad-pp05mx", "sin_pedidos")
        pa_u = _entidad(conn, "amazon_us", "product_ad", "ad-pp05us", listing_id=lidu)
        conn.execute(
            "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
            " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
            " reversa, retirado_el, actor) VALUES ('amazon_us', 'sin_pedidos',"
            " 'pausa', 'lote-pp05', NULL, %s, 'ad-pp05us', 'ag-u', 'c-u', 'SS-5U',"
            " 'B0PP050002', 'x', now(), 'dueno')",
            (pa_u,),
        )
        conn.commit()
        mx = pp.lee_productos(conn, "amazon_mx", HOY)
        us = pp.lee_productos(conn, "amazon_us", HOY)
        assert [r.ad_id_externo for r in mx.retiros_abiertos] == ["ad-pp05mx"]
        assert [r.ad_id_externo for r in us.retiros_abiertos] == ["ad-pp05us"]


@_skip_db
def test_r09r4_pp07_retiro_subfamilia_sale_en_pantalla():
    """R09-r4 B6 (PP07): retiros abiertos de motivo 'subfamilia'
    tambien salen (no solo 'sin_pedidos')."""
    with _db_p4("orbit_p4_r4pp07") as conn:
        _pid, lid = _producto(conn, sku="SS-7", asin="B0PP070001", seller_sku="SS-7")
        pa = _activa(conn, lid, "ad-pp07")
        rid = _retiro_abierto(conn, pa, "ad-pp07", "subfamilia")
        conn.commit()
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert [(r.retiro_id, r.motivo, r.ad_id_externo) for r in pantalla.retiros_abiertos] == [
            (rid, "subfamilia", "ad-pp07")
        ]


@_skip_db
def test_p4_clics_fuera_no_superan_clics():
    """R09 B10 (n2): placement sin tope vs acumulado de la ultima lectura:
    los de fuera se capan a los del impulso."""
    from test_impulso_io import _impulso_vivo

    with _db_p4("orbit_p4_r10a") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lanzado=HOY - dt.timedelta(days=30))
        _lectura_p4(
            conn,
            ids["impulso"],
            HOY - dt.timedelta(days=20),
            dias=10,
            impr=900,
            clics=14,
            pedidos=1,
            veredicto="vende",
            final=True,
            hecho="graduado",
        )
        for d in range(1, 26):
            conn.execute(
                "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
                " metric_date, observed_at, metric_currency, impressions, clicks,"
                " source_report_id) VALUES ('amazon_mx', %s, 'fuera_de_amazon', %s, now(),"
                " 'MXN', 50, 2, 'r10')",
                (ids["camps"][1], HOY - dt.timedelta(days=d)),
            )
        conn.commit()
        visto = pp.lee_productos(conn, "amazon_mx", HOY).impulsos[0]
        assert visto.clics_fuera_de_amazon <= visto.clics


@_skip_db
def test_r09r4_4a_fuera_solo_ventana_y_4b_null_es_none():
    """R09-r4 B5 (4a): clics de fuera anteriores al lanzamiento o
    posteriores al corte no cuentan; solo lanzado..corte. (4b): un dia
    sin dato (clicks NULL) en la ventana deja el total en None."""
    with _db_p4("orbit_p4_r44") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lanzado=HOY - dt.timedelta(days=30))
        _lectura_p4(
            conn,
            ids["impulso"],
            HOY - dt.timedelta(days=20),
            dias=10,
            impr=900,
            clics=14,
            pedidos=1,
            veredicto="vende",
            final=True,
            hecho="graduado",
        )

        def _fuera(camp, dia, clics, tarde=False):
            conn.execute(
                "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
                " metric_date, observed_at, metric_currency, impressions, clicks,"
                " source_report_id) VALUES ('amazon_mx', %s, 'fuera_de_amazon', %s,"
                " now() + make_interval(hours => %s), 'MXN', 50, %s, %s)",
                (camp, dia, 1 if tarde else 0, clics, f"r4-{camp}-{dia}-{clics}"),
            )

        _fuera(ids["camps"][1], HOY - dt.timedelta(days=40), 100)  # pre-lanzamiento: no cuenta
        for d in range(20, 26):  # ventana dias 25..20: 2 por dia = 12
            _fuera(ids["camps"][1], HOY - dt.timedelta(days=d), 2)
        _fuera(ids["camps"][1], HOY - dt.timedelta(days=1), 50)  # post-corte: no cuenta
        conn.commit()
        visto = pp.lee_productos(conn, "amazon_mx", HOY).impulsos[0]
        assert visto.clics_fuera_de_amazon == 12
        # 4b: el dia 22 llega sin dato (NULL mas reciente): total None.
        _fuera(ids["camps"][1], HOY - dt.timedelta(days=22), None, tarde=True)
        conn.commit()
        visto = pp.lee_productos(conn, "amazon_mx", HOY).impulsos[0]
        assert visto.clics_fuera_de_amazon is None


@_skip_db
def test_p4_pausado_a_mano_no_dice_sigue_aprendiendo():
    """R09 B10 (n3): lote pausado sin lectura final: la frase no dice
    'sigue aprendiendo'."""
    from test_impulso_io import _impulso_vivo

    with _db_p4("orbit_p4_r10b") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lanzado=HOY - dt.timedelta(days=5))
        _lectura_p4(conn, ids["impulso"], HOY - dt.timedelta(days=1), dias=5, impr=200, clics=3)
        conn.execute("UPDATE fabrica_lote SET estado = 'pausado' WHERE lote = %s", (ids["lote"],))
        conn.commit()
        visto = pp.lee_productos(conn, "amazon_mx", HOY).impulsos[0]
        assert "sigue aprendiendo" not in visto.frase


@_skip_db
def test_p4_dia_no_pasa_del_plazo():
    """R09 B10 (n4): en_curso con dias mas alla del plazo: el dia se capa."""
    from test_impulso_io import _impulso_vivo

    with _db_p4("orbit_p4_r10c") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lanzado=HOY - dt.timedelta(days=19))
        _lectura_p4(
            conn,
            ids["impulso"],
            HOY - dt.timedelta(days=1),
            dias=19,
            impr=5000,
            clics=40,
            gasto="120",
        )
        conn.commit()
        visto = pp.lee_productos(conn, "amazon_mx", HOY).impulsos[0]
        assert visto.dia <= visto.plazo_dias, visto.frase


def _ventas_us(conn, pid, unidades, *, hace_dias=10):
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity, amount,"
        " amount_currency, ingest_run_id) VALUES ('amazon_us', 'sale', %s, %s, %s, %s, 'USD',"
        " %s)",
        (HOY - dt.timedelta(days=hace_dias), pid, unidades, 10 + pid, run),
    )


@_skip_db
def test_r09r3_s03_pedidos_no_mezclan_mx_us():
    """R09 B12 (S3): los pedidos del candidato de MX no suman los de US."""
    with _db_p4("orbit_p4_r3s03") as conn:
        pid, lid = _producto(conn, sku="S-S03", asin="B0S0300001", seller_sku="SC-S03")
        _activa(conn, lid, "ad-s03")
        _ventas(conn, pid, 2)
        _ventas_us(conn, pid, 5)
        _clics_ads(conn, "B0S0300001", "SC-S03", 3)
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert [c.product_id for c in pantalla.candidatos] == [pid]
        assert pantalla.candidatos[0].unidades_90d == 2


@_skip_db
def test_r09r3_s09_candidato_solo_sus_publicaciones():
    """R09 B12 (S9): el candidato de MX no trae sus listings de US."""
    with _db_p4("orbit_p4_r3s09") as conn:
        pid, lid = _producto(conn, sku="S-S09", asin="B0S0900001", seller_sku="SC-S09")
        lid_us = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'B0S0900002', 'SC-S09-US') RETURNING id",
            (pid,),
        ).fetchone()[0]
        _activa(conn, lid, "ad-s09")
        _ventas(conn, pid, 2)
        _clics_ads(conn, "B0S0900001", "SC-S09", 3)
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert [c.product_id for c in pantalla.candidatos] == [pid]
        assert pantalla.candidatos[0].listing_ids == (lid,)
        assert lid_us not in pantalla.candidatos[0].listing_ids


@_skip_db
def test_r09r3_s11_impulsos_no_mezclan_mx_us():
    """R09 B12 (S11): la pantalla de MX no trae los impulsos de US."""
    with _db_p4("orbit_p4_r3s11") as conn:
        pid, lid = _con_margen(conn)
        mx = _impulso_vivo(conn, pid=pid, lid=lid, lote="impulso-s11-mx")
        pid_us, lid_us = _producto(
            conn,
            sku="SS-S11-US",
            asin="B0S11US001",
            platform="amazon_us",
            seller_sku="SS-S11-US",
        )
        us = _impulso_vivo(
            conn,
            pid=pid_us,
            lid=lid_us,
            lote="impulso-s11-us",
            platform="amazon_us",
            moneda="USD",
        )
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        assert {i.impulso_id for i in pantalla.impulsos} == {mx["impulso"]}
        assert us["impulso"] not in {i.impulso_id for i in pantalla.impulsos}


@_skip_db
def test_r09r4_intencion_no_se_ofrece_regresar():
    """R09-r4 B4 (tabla propia): una intencion pendiente no sale en
    retiros abiertos; el retiro sellado si."""
    with _db_p4("orbit_p4_r4sello") as conn:
        pid, lid = _producto(conn, sku="S-R4", asin="B0R4000001", seller_sku="SC-R4")
        camp = _entidad(conn, "amazon_mx", "campaign", "c-r4")
        ag = _entidad(conn, "amazon_mx", "ad_group", "ag-r4", parent=camp)
        pa = _entidad(conn, "amazon_mx", "product_ad", "ad-r4", parent=ag, listing_id=lid)
        conn.execute(
            "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro,"
            " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
            " reversa, actor, creada_el) VALUES ('amazon_mx', 'sin_pedidos', 'pausa',"
            " 'lote-r4', %s, 'ad-r4', 'ag-r4', 'c-r4', 'SC-R4', 'B0R4000001',"
            " 'state=ENABLED', 'dueno', now())",
            (pa,),
        )
        assert pp.lee_productos(conn, "amazon_mx", HOY).retiros_abiertos == ()
        rid = conn.execute(
            "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
            " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
            " reversa, retirado_el, actor) VALUES ('amazon_mx', 'sin_pedidos', 'pausa',"
            " 'lote-r4', NULL, %s, 'ad-r4', 'ag-r4', 'c-r4', 'SC-R4', 'B0R4000001',"
            " 'state=ENABLED', now(), 'dueno') RETURNING id",
            (pa,),
        ).fetchone()[0]
        assert [r.retiro_id for r in pp.lee_productos(conn, "amazon_mx", HOY).retiros_abiertos] == [
            rid
        ]


@_skip_db
def test_r09r4_pp12_fuera_no_supera_clics_aun_en_ventana():
    """R09-r4 B6 (PP12): si Amazon trae mas clics de fuera que clics
    totales en la ventana (dato inconsistente), el tope capa: fuera
    nunca supera clics."""
    with _db_p4("orbit_p4_r4pp12") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lanzado=HOY - dt.timedelta(days=30))
        _lectura_p4(
            conn,
            ids["impulso"],
            HOY - dt.timedelta(days=20),
            dias=10,
            impr=900,
            clics=12,
            pedidos=1,
            veredicto="vende",
            final=True,
            hecho="graduado",
        )

        def _fuera(camp, dia, clics):
            conn.execute(
                "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
                " metric_date, observed_at, metric_currency, impressions, clicks,"
                " source_report_id) VALUES ('amazon_mx', %s, 'fuera_de_amazon', %s,"
                " now(), 'MXN', 50, %s, %s)",
                (camp, dia, clics, f"pp12-{camp}-{dia}-{clics}"),
            )

        for d in range(20, 27):  # ventana dias 26..20: 2 por dia = 14 > 12 clics
            _fuera(ids["camps"][1], HOY - dt.timedelta(days=d), 2)
        conn.commit()
        visto = pp.lee_productos(conn, "amazon_mx", HOY).impulsos[0]
        assert visto.clics_fuera_de_amazon == 12


@_skip_db
def test_r09r5_p05_ventana_termina_en_ultima_lectura():
    """R09-r5 B6 (P05): con dos lecturas, los clics de fuera cuentan
    hasta la ULTIMA lectura (no la primera)."""
    with _db_p4("orbit_p4_p05") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lanzado=HOY - dt.timedelta(days=10))
        for corte in (HOY - dt.timedelta(days=4), HOY - dt.timedelta(days=2)):
            conn.execute(
                "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
                " impresiones, clics, gasto, pedidos, veredicto, es_final, hecho)"
                " VALUES (%s, %s, now(), 1, 100, 10, 1, 0, 'en_curso', FALSE, 'nada')",
                (ids["impulso"], corte),
            )
        conn.execute(
            "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
            " metric_date, observed_at, metric_currency, clicks, source_report_id)"
            " VALUES ('amazon_mx', %s, 'fuera_de_amazon', %s, now(), 'MXN', 2, 'r1')",
            (ids["camps"][0], HOY - dt.timedelta(days=3)),
        )
        conn.commit()
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        por_id = {i.impulso_id: i for i in pantalla.impulsos}
        assert por_id[ids["impulso"]].clics_fuera_de_amazon == 2


@_skip_db
def test_r09r5_p07_sin_lectura_cota_hoy():
    """R09-r5 B6 (P07): sin lectura, los clics de fuera cuentan hasta
    hoy (no ayer)."""
    with _db_p4("orbit_p4_p07") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid)
        conn.execute(
            "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
            " metric_date, observed_at, metric_currency, clicks, source_report_id)"
            " VALUES ('amazon_mx', %s, 'fuera_de_amazon', %s, now(), 'MXN', 4, 'r1')",
            (ids["camps"][0], HOY),
        )
        conn.commit()
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        por_id = {i.impulso_id: i for i in pantalla.impulsos}
        assert por_id[ids["impulso"]].clics_fuera_de_amazon == 4


@_skip_db
def test_r09r5_p08_dia_lanzamiento_cuenta():
    """R09-r5 B6 (P08): los clics de fuera del dia del lanzamiento
    cuentan (el dia no queda fuera)."""
    with _db_p4("orbit_p4_p08") as conn:
        pid, lid = _con_margen(conn)
        ids = _impulso_vivo(conn, pid=pid, lid=lid, lanzado=HOY - dt.timedelta(days=5))
        conn.execute(
            "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
            " metric_date, observed_at, metric_currency, clicks, source_report_id)"
            " VALUES ('amazon_mx', %s, 'fuera_de_amazon', %s, now(), 'MXN', 6, 'r1')",
            (ids["camps"][0], HOY - dt.timedelta(days=5)),
        )
        conn.commit()
        pantalla = pp.lee_productos(conn, "amazon_mx", HOY)
        por_id = {i.impulso_id: i for i in pantalla.impulsos}
        assert por_id[ids["impulso"]].clics_fuera_de_amazon == 6
