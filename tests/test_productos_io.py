"""AUTO-03 D.6 y D.7: lectura por lote de productos, evaluacion en sombra y
rango pedido de la ingesta, contra PostgreSQL (escritura en la base).

Las ventanas se anclan en el reloj real: ingest_run.started_at y el
observed_at de la ingesta son now() de la base, y S6 exige que la corrida
empiece con la fecha ya madura.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from test_cycle import _config_version, _corre, _db_temporal, _decisions_de, _entidad, _estado
from test_cycle import _metrica as _metrica_hoja
from test_cycle_target_ciclo import _mundo_familia, _producto_con_listing, _targets_de
from test_schema import _postgres_obligatorio_ausente

from app import productos_io
from app.ads.reports import PRODUCTOS_CFG, escribir_grupo
from app.ads.structure import PerfilAds
from app.cycle import lee_goals
from app.optimizer import productos as p

pytestmark = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

SQL20 = (
    Path(__file__).resolve().parents[1] / "migrations" / "0020_ads_producto_metrica.sql"
).read_text(encoding="utf-8")
SQL17 = (Path(__file__).resolve().parents[1] / "migrations" / "0017_first_seen_at.sql").read_text(
    encoding="utf-8"
)

PERFIL_US = PerfilAds(
    profile_id=301,
    country="US",
    currency_code="USD",
    account_type="seller",
    valid_payment_method=True,
    account_name="Cuenta Test",
    aceptado=True,
    platform="amazon_us",
    moneda="USD",
)


@contextlib.contextmanager
def _db(prefijo: str):
    with _db_temporal(prefijo) as (conn, _extra):
        conn.execute(SQL17)
        conn.execute(SQL20)
        yield conn


def _hoy() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def _ventana() -> list[dt.date]:
    v = p.ventana_pausa(dt.datetime.now(dt.UTC))
    return [v.desde + dt.timedelta(days=n) for n in range((v.hasta_exclusivo - v.desde).days)]


def _run(conn, source: str = "test") -> int:
    return conn.execute(
        "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id", (source,)
    ).fetchone()[0]


def _mundo(conn) -> dict:
    """Campana -> ad group con una keyword (da el watermark) y un product ad
    ENABLED del producto SKU-A (ASIN-SKU-A, seller SS-SKU-A)."""
    run = _run(conn)
    ahora = dt.datetime.now(dt.UTC)
    camp = _entidad(conn, "amazon_us", "campaign", "c1")
    ag = _entidad(conn, "amazon_us", "ad_group", "g1", parent=camp)
    kw = _entidad(
        conn, "amazon_us", "keyword", "k1", parent=ag, match_type="EXACT", keyword_text="kw a"
    )
    for entidad in (camp, ag, kw):
        _estado(conn, entidad, synced_at=ahora)
    _metrica_hoja(conn, run, kw, _hoy() - dt.timedelta(days=1), ahora, cost="1")
    pid, lid = _producto_con_listing(conn, "SKU-A")
    pa = _anuncio(conn, ag, lid, "pa1", "ENABLED")
    return {"run": run, "camp": camp, "ag": ag, "kw": kw, "pid": pid, "lid": lid, "pa": pa}


def _otro_producto(conn, m) -> int:
    """SKU-B (ASIN-SKU-B, seller SS-SKU-B) anunciado en su propio ad group."""
    ag = _entidad(conn, "amazon_us", "ad_group", "g2", parent=m["camp"])
    _estado(conn, ag, synced_at=dt.datetime.now(dt.UTC))
    pid, lid = _producto_con_listing(conn, "SKU-B")
    _anuncio(conn, ag, lid, "pa-b", "ENABLED")
    return pid


def _filas_salvo(conn, run, fechas, faltantes, sku: str) -> None:
    for f in fechas:
        if f not in faltantes:
            _fila(conn, run, f, sku=f"SS-{sku}", asin=f"ASIN-{sku}")


def _anuncio(conn, ag, lid, externo, estado) -> int:
    pa = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
        " VALUES ('amazon_us', 'product_ad', %s, %s, %s) RETURNING id",
        (externo, ag, lid),
    ).fetchone()[0]
    _estado(conn, pa, synced_at=dt.datetime.now(dt.UTC), status=estado)
    return pa


def _fila(conn, run, fecha, *, sku="SS-SKU-A", asin="ASIN-SKU-A", cost="2.5", clicks=3):
    conn.execute(
        "INSERT INTO ads_product_metric_observation (platform, advertised_asin,"
        " advertised_sku, metric_date, observed_at, metric_currency, clicks, cost,"
        " purchases30d, sales30d, purchases_same_sku30d, attributed_sales_same_sku_30d,"
        " ingest_run_id) VALUES ('amazon_us', %s, %s, %s, now() - interval '1 hour', 'USD',"
        " %s, %s, 0, 0, 0, 0, %s)",
        (asin, sku, fecha, clicks, Decimal(cost), run),
    )


def _fila_reporte(
    fecha, *, asin="ASIN-OTRO", sku="SS-OTRO", clicks=1, cost=1.0, ventas=0, ad_id=3
) -> dict:
    return {
        "date": fecha.isoformat(),
        "advertisedAsin": asin,
        "advertisedSku": sku,
        "campaignId": 1,
        "adGroupId": 2,
        "adId": ad_id,
        "impressions": 10,
        "clicks": clicks,
        "cost": cost,
        "purchases30d": 1 if ventas else 0,
        "sales30d": ventas,
        "purchasesSameSku30d": 1 if ventas else 0,
        "attributedSalesSameSku30d": ventas,
    }


def _ingesta(conn, desde, hasta, filas=()) -> int:
    """La escritura real de la ingesta de productos (reports.escribir_grupo):
    sella el written con el rango pedido y la run ok."""
    run = _run(conn, "amazon_ads_products_v3")
    item = SimpleNamespace(
        perfil=PERFIL_US, cfg=PRODUCTOS_CFG, report_id=f"r{run}", filas=list(filas)
    )
    escribir_grupo(conn, run, None, [item], fecha_ini=desde, fecha_fin=hasta)
    return run


def _entradas(conn) -> tuple[p.EntradaProducto, ...]:
    campanas = [
        f[0]
        for f in conn.execute(
            "SELECT id FROM ad_entity WHERE platform = 'amazon_us' AND kind = 'campaign'"
        )
    ]
    return productos_io.lee_entradas_productos(
        conn,
        "amazon_us",
        decidido_el=dt.datetime.now(dt.UTC),
        settings={},
        goals=lee_goals(conn, "amazon_us", campanas),
    )


def _entrada(conn, pid) -> p.EntradaProducto:
    return next(e for e in _entradas(conn) if e.producto.product_id == pid)


def test_s6_fecha_sin_fila_cubierta_es_cero_y_sin_cobertura_desconocida():
    with _db("orbit_auto03_s6") as conn:
        m = _mundo(conn)
        fechas = _ventana()
        _fila(conn, m["run"], fechas[10], cost="7.25", clicks=4)
        assert _entrada(conn, m["pid"]).actividad_pausa == p.SinDato(("fecha_desconocida",))
        _ingesta(conn, fechas[0], fechas[-1])
        actividad = _entrada(conn, m["pid"]).actividad_pausa
        assert isinstance(actividad, p.AdsCompletos)
        assert actividad.gasto == p.Dinero(Decimal("7.25"), "USD")
        assert actividad.clics == 4
        assert actividad.ventas_total == p.Dinero(Decimal("0"), "USD")
        assert actividad.referencias_por_fecha[10] == f"{fechas[10].isoformat()}:fila"
        assert sum(":cero:" in r for r in actividad.referencias_por_fecha) == 29


def test_sku_distinto_en_el_asin_deja_impedimento():
    with _db("orbit_auto03_sku") as conn:
        m = _mundo(conn)
        _fila(conn, m["run"], _hoy() - dt.timedelta(days=20), sku="OTRO-SKU", cost="0.40")
        entrada = _entrada(conn, m["pid"])
        assert entrada.impedimentos == ("sku_distinto_en_asin",)
        assert "sku_distinto_en_asin" in p.resolver_producto(entrada).meta.motivos


def test_product_ad_sin_listing_vuelve_no_exclusivo_al_grupo():
    with _db("orbit_auto03_sinlisting") as conn:
        m = _mundo(conn)
        grupos = _entrada(conn, m["pid"]).grupos_aptos_para_meta
        assert [(g.grupo_id, g.hojas) for g in grupos] == [(m["ag"], (m["kw"],))]
        _anuncio(conn, m["ag"], None, "pa-sin-listing", "ENABLED")
        assert _entrada(conn, m["pid"]).grupos_aptos_para_meta == ()


def test_paused_cuenta_para_la_exclusividad_y_archived_no():
    with _db("orbit_auto03_paused") as conn:
        m = _mundo(conn)
        _pid2, lid2 = _producto_con_listing(conn, "SKU-B")
        pa2 = _anuncio(conn, m["ag"], lid2, "pa2", "PAUSED")
        assert _entrada(conn, m["pid"]).grupos_aptos_para_meta == ()
        conn.execute(
            "UPDATE ad_entity_state SET status = 'ARCHIVED' WHERE ad_entity_id = %s", (pa2,)
        )
        grupos = _entrada(conn, m["pid"]).grupos_aptos_para_meta
        assert [g.grupo_id for g in grupos] == [m["ag"]]
        assert grupos[0].huella_alcance == productos_io.huella_grupo(conn, m["ag"])


def test_rango_corrida_manual_de_un_dia_cubre_solo_ese_dia():
    with _db("orbit_auto03_rango1") as conn:
        m = _mundo(conn)
        otro = _otro_producto(conn, m)
        fechas = _ventana()
        dia = fechas[12]
        _ingesta(conn, dia, dia)
        rango = conn.execute(
            "SELECT fecha_desde, fecha_hasta, cobertura_integra FROM ads_report_result"
            " WHERE status = 'written'"
        ).fetchall()
        assert rango == [(dia, dia, True)]
        _filas_salvo(conn, m["run"], fechas, {dia}, "SKU-A")
        _filas_salvo(conn, m["run"], fechas, {dia, fechas[13]}, "SKU-B")
        assert isinstance(_entrada(conn, m["pid"]).actividad_pausa, p.AdsCompletos)
        assert _entrada(conn, otro).actividad_pausa == p.SinDato(("fecha_desconocida",))


def test_rango_fila_sin_rango_no_cubre_ninguna_fecha():
    with _db("orbit_auto03_rango_null") as conn:
        m = _mundo(conn)
        fechas = _ventana()
        run = _run(conn, "amazon_ads_products_v3")
        conn.execute("UPDATE ingest_run SET ok = true, finished_at = now() WHERE id = %s", (run,))
        conn.execute(
            "INSERT INTO ads_report_result (ingest_run_id, profile_id, platform, report_name,"
            " source_report_id, status) VALUES (%s, 301, 'amazon_us', 'productos', 'viejo',"
            " 'written')",
            (run,),
        )
        for f in fechas[1:]:
            _fila(conn, m["run"], f)
        assert _entrada(conn, m["pid"]).actividad_pausa == p.SinDato(("fecha_desconocida",))


def test_rango_fecha_fuera_del_rango_sin_fila_queda_desconocida():
    with _db("orbit_auto03_rango_fuera") as conn:
        m = _mundo(conn)
        otro = _otro_producto(conn, m)
        fechas = _ventana()
        _ingesta(conn, fechas[0], fechas[19])
        _filas_salvo(conn, m["run"], fechas, {fechas[5]}, "SKU-A")
        _filas_salvo(conn, m["run"], fechas, {fechas[25]}, "SKU-B")
        assert isinstance(_entrada(conn, m["pid"]).actividad_pausa, p.AdsCompletos)
        assert _entrada(conn, otro).actividad_pausa == p.SinDato(("fecha_desconocida",))


def test_rango_clics_invalidos_dejan_la_cobertura_en_falso():
    with _db("orbit_auto03_rango_invalido") as conn:
        m = _mundo(conn)
        fechas = _ventana()
        _ingesta(conn, fechas[0], fechas[-1], [_fila_reporte(fechas[3], clicks="abc")])
        assert conn.execute(
            "SELECT fecha_desde, fecha_hasta, cobertura_integra FROM ads_report_result"
            " WHERE status = 'written'"
        ).fetchall() == [(fechas[0], fechas[-1], False)]
        for f in fechas[1:]:
            _fila(conn, m["run"], f)
        assert _entrada(conn, m["pid"]).actividad_pausa == p.SinDato(("fecha_desconocida",))


def _ciclo(prefijo: str, *, meta: str | None, rompe_margen: bool = False, sin_target: bool = False):
    """Corre el ciclo de _mundo_familia (producto con margen 20 % en un
    grupo exclusivo con una keyword) con la meta en `meta`."""
    with _db(prefijo) as conn:
        ids = _mundo_familia(conn)
        if meta is not None:
            vigente = conn.execute(
                "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
            _config_version(conn, {**vigente, "ads_producto_meta_amazon_us": meta})
        if sin_target:
            # Sin setting ni familia, y la plataforma no mide con 40 dias:
            # la cascada actual no da target y la hoja se salta.
            vigente = conn.execute(
                "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
            vigente.pop("ads_target_acos_pct_amazon_us")
            _config_version(conn, vigente)
            conn.execute("DELETE FROM producto_familia")
        if rompe_margen:
            conn.execute("DROP VIEW v_margen_producto CASCADE")
        res = _corre(conn)
        decisiones = [
            (d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7], d[8], d[9])
            for d in _decisions_de(conn, res.cycle_id)
        ]
        targets = {k: (v[2], v[3]) for k, v in _targets_de(conn, res.cycle_id).items()}
        evaluaciones = conn.execute(
            "SELECT product_id, meta_pct, resultado FROM evaluacion_producto_ciclo"
            " WHERE cycle_id = %s",
            (res.cycle_id,),
        ).fetchall()
        return ids, res, decisiones, targets, evaluaciones


def test_sombra_no_cambia_decisiones_y_un_error_sql_queda_en_notes():
    _ids, base, dec_off, tgt_off, ev_off = _ciclo("orbit_auto03_off", meta=None)
    ids, sombra, dec_sombra, tgt_sombra, ev_sombra = _ciclo("orbit_auto03_sh", meta="shadow")
    _ids, roto, dec_roto, tgt_roto, ev_roto = _ciclo(
        "orbit_auto03_roto", meta="shadow", rompe_margen=True
    )
    assert base.status == sombra.status == roto.status == "done"
    assert dec_off and dec_sombra == dec_off and dec_roto == dec_off
    assert tgt_sombra == tgt_off == tgt_roto
    assert ev_off == [] and ev_roto == []
    assert "productos" not in json.loads(base.notes)

    notas = json.loads(sombra.notes)["productos"]
    assert notas["modo_meta"] == "shadow" and "error" not in notas
    assert notas["productos_evaluados"] == notas["con_meta"] == 1
    assert notas["evaluaciones_guardadas"] == 1
    assert notas["grupos_aptos"] == 1 and notas["hojas_bajo_grupo_apto"] == 1
    [(_pid, meta_pct, resultado)] = ev_sombra
    assert meta_pct == Decimal("10")
    [hoja] = resultado["sombra_hojas"]
    assert hoja["hoja_id"] == ids["kw"]
    assert (hoja["target_actual"], hoja["procedencia_actual"]) == ("29.5", "margen_familia")
    assert Decimal(hoja["target_con_meta"]) == 10
    assert hoja["procedencia_con_meta"] == "margen_producto"
    assert resultado["pausas"] == []

    notas_roto = json.loads(roto.notes)["productos"]
    assert notas_roto["error"].startswith("lectura: ")
    assert "v_margen_producto" in notas_roto["error"]


def test_evaluacion_unica_por_ciclo_producto_e_inmutable():
    with _db("orbit_auto03_inmutable") as conn:
        _mundo_familia(conn)
        vigente = conn.execute(
            "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        _config_version(conn, {**vigente, "ads_producto_meta_amazon_us": "shadow"})
        res = _corre(conn)
        fila = conn.execute(
            "SELECT id, cycle_id, platform, product_id, decided_at, politica, moneda, entrada,"
            " resultado, huella_alcance, ventana_desde, ventana_hasta"
            " FROM evaluacion_producto_ciclo WHERE cycle_id = %s",
            (res.cycle_id,),
        ).fetchone()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO evaluacion_producto_ciclo (cycle_id, platform, product_id,"
                " decided_at, politica, moneda, entrada, resultado, huella_alcance,"
                " ventana_desde, ventana_hasta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s)",
                (*fila[1:7], json.dumps(fila[7]), json.dumps(fila[8]), *fila[9:]),
            )
        with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
            conn.execute(
                "UPDATE evaluacion_producto_ciclo SET politica = 'v2' WHERE id = %s", (fila[0],)
            )
        with pytest.raises(psycopg.errors.RestrictViolation, match="APPEND-ONLY"):
            conn.execute("DELETE FROM evaluacion_producto_ciclo WHERE id = %s", (fila[0],))


def test_hoja_sin_target_actual_congela_el_veredicto_con_la_meta():
    """h_cbb7cad0: sin target en la cascada actual la hoja se sigue saltando,
    pero la sombra congela el veredicto que la meta de 10 habria dado."""
    (
        _ids,
        base,
        dec_off,
        tgt_off,
        _ev,
    ) = _ciclo("orbit_auto03_st_off", meta=None, sin_target=True)
    ids, sombra, dec_sombra, tgt_sombra, ev_sombra = _ciclo(
        "orbit_auto03_st_sh", meta="shadow", sin_target=True
    )
    assert base.status == sombra.status == "done"
    assert dec_sombra == dec_off and tgt_sombra == tgt_off
    assert ids["kw"] not in tgt_sombra
    assert json.loads(sombra.notes)["skips"]["entidad"]["sin_target"] == 1
    assert "error" not in json.loads(sombra.notes)["productos"]
    [(_pid, meta_pct, resultado)] = ev_sombra
    assert meta_pct == Decimal("10")
    [hoja] = resultado["sombra_hojas"]
    assert hoja["hoja_id"] == ids["kw"]
    assert (hoja["target_actual"], hoja["procedencia_actual"]) == (None, None)
    assert hoja["veredicto_actual"] == {"tipo": "Mantener", "motivo": "sin_target"}
    assert Decimal(hoja["target_con_meta"]) == 10
    assert hoja["procedencia_con_meta"] == "margen_producto"
    assert hoja["veredicto_con_meta"]["tipo"] in {"Mantener", "Mover", "Regresar", "Pausar"}


def test_candidatos_a_pausa_y_anuncios_afectables():
    """h_023cdc6c: un producto que gasto 60 USD con 120 clics y cero ventas
    en [D-60, D-30) es candidato con los limites por omision (100 clics,
    40 USD); su anuncio cuyo adId esta en ad_ids es afectable, uno fuera de
    ad_ids no. El que vendio no es candidato; con el goal en shadow ningun
    anuncio es elegible."""
    with _db("orbit_auto03_candidatos") as conn:
        m = _mundo(conn)
        otro = _otro_producto(conn, m)
        _anuncio(conn, m["ag"], m["lid"], "pa-fuera", "ENABLED")
        conn.execute("UPDATE ad_entity SET first_seen_at = now() - interval '200 days'")
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, platform, bid_floor, bid_ceiling,"
            " bid_currency, harvest_campaign_id, harvest_ad_group_id, harvest_default_bid,"
            " enabled, mode) VALUES ('platform', 'amazon_us', 0.40, 2.50, 'USD', '9002',"
            " '9102', 0.75, true, 'live')"
        )
        fechas = _ventana()
        filas = [
            _fila_reporte(f, asin="ASIN-SKU-A", sku="SS-SKU-A", clicks=4, cost=2.0, ad_id="pa1")
            for f in fechas
        ] + [
            _fila_reporte(
                f,
                asin="ASIN-SKU-B",
                sku="SS-SKU-B",
                clicks=4,
                cost=2.0,
                ad_id="pa-b",
                ventas=25.0 if f == fechas[3] else 0,
            )
            for f in fechas
        ]
        _ingesta(conn, fechas[0], fechas[-1], filas)
        entradas = _entradas(conn)
        a = next(e for e in entradas if e.producto.product_id == m["pid"])
        b = next(e for e in entradas if e.producto.product_id == otro)
        assert a.actividad_pausa.gasto == p.Dinero(Decimal("60"), "USD")
        assert a.actividad_pausa.clics == 120
        assert p.cumple_limites_pausa(a) and not p.cumple_limites_pausa(b)
        assert [x.ad_entity_id for x in a.anuncios] == [m["pa"]]
        assert len(b.anuncios) == 1
        resumen = productos_io.resumen_cobertura(
            "amazon_us", [p.resolver_producto(e) for e in entradas], (None, {})
        )
        assert (
            resumen["candidatos_pausa"],
            resumen["anuncios_afectables"],
            resumen["anuncios_elegibles"],
        ) == (1, 1, 2)
        conn.execute("UPDATE ads_optimizer_goal SET mode = 'shadow'")
        assert all(e.anuncios == () for e in _entradas(conn))
