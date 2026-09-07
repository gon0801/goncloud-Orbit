"""Tests de evaluacion del catalogo (ORBIT 19 B.4, app/evaluacion_catalogo.py).

TODOS los fixtures del spec 0.4 seccion 8 (tabla "resultado esperado") contra
el modulo PURO: sin DB, sin red. El adaptador de API vive en fabrica_web.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.disponibilidad import ESTADO_DESCONOCIDO
from app.economia_observada import EconomiaProducto
from app.evaluacion_catalogo import (
    ETIQUETA_DENTRO_DEL_OBJETIVO,
    ETIQUETA_GASTO_SIN_VENTAS,
    ETIQUETA_POR_ENCIMA_DEL_OBJETIVO,
    ETIQUETA_SIN_DATOS,
    ObservacionAds,
    evaluar_ads,
    evaluar_listing,
    motivos_margen,
    objetivo_comparacion,
    ordenar,
)

UTC = dt.UTC
VENTANA = (dt.date(2026, 8, 1), dt.date(2026, 8, 31))


def _obs(
    metric_date: dt.date,
    cost: Decimal | None = None,
    sales30d: Decimal | None = None,
    clicks: int | None = None,
    purchases30d: Decimal | None = None,
    promoted30d: Decimal | None = None,
    observed_at: dt.datetime | None = None,
) -> ObservacionAds:
    """Fila con observed_at MADURO por defecto (D+35 >= D+30, §2)."""
    return ObservacionAds(
        metric_date=metric_date,
        observed_at=observed_at
        or dt.datetime.combine(metric_date + dt.timedelta(days=35), dt.time.min, UTC),
        clicks=clicks,
        cost=cost,
        purchases30d=purchases30d,
        sales30d=sales30d,
        attributed_sales_same_sku30d=promoted30d,
    )


def _economia(
    margen: Decimal | None = None,
    venta: Decimal | None = None,
    muestra_limitada: bool | None = None,
) -> EconomiaProducto:
    return EconomiaProducto(
        platform="amazon_mx",
        product_id=1,
        moneda="MXN",
        margen_neto_pct=margen,
        venta_total=venta,
        muestra_limitada=muestra_limitada,
    )


def _listing(
    listing_id: int = 1,
    economia: EconomiaProducto | None = None,
    filas: list[ObservacionAds] | None = None,
    objetivos: list[Decimal | None] | None = None,
    disponibilidad: dict | None = None,
) -> object:
    return evaluar_listing(
        listing_id=listing_id,
        platform="amazon_mx",
        product_id=1,
        asin="B000000001",
        seller_sku="SS-1",
        filas_ads=filas or [],
        ventana=VENTANA,
        economia=economia or _economia(),
        disponibilidad=disponibilidad
        if disponibilidad is not None
        else {"estado": ESTADO_DESCONOCIDO},
        objetivos_grupos=objetivos or (),
    )


# ---------------------------------------------------------------------------
# §8: ratios desde sumas (nunca promedio de porcentajes)
# ---------------------------------------------------------------------------


def test_ratios_desde_sumas_acos_25_no_20():
    """Gasto 10/ventas 100 + gasto 90/ventas 300 => ACoS 25% (100/400)."""
    res = evaluar_ads(
        [
            _obs(dt.date(2026, 8, 10), cost=Decimal("10"), sales30d=Decimal("100")),
            _obs(dt.date(2026, 8, 11), cost=Decimal("90"), sales30d=Decimal("300")),
        ],
        VENTANA,
    )
    assert res.acos_pct == Decimal("25")
    assert res.cost == Decimal("100")
    assert res.sales30d == Decimal("400")
    assert res.muestra == 2  # muestras visibles (§2)


def test_ratios_desde_sumas_cpc_y_cvr():
    res = evaluar_ads(
        [
            _obs(dt.date(2026, 8, 10), cost=Decimal("10"), clicks=100, purchases30d=1),
            _obs(dt.date(2026, 8, 11), cost=Decimal("90"), clicks=300, purchases30d=3),
        ],
        VENTANA,
    )
    assert res.cpc == Decimal("0.25")  # 100/400, no promedio de cpc por fila
    assert res.cvr_pct == Decimal("1")  # 100*4/400


# ---------------------------------------------------------------------------
# §8: igualdad ACoS = objetivo -> Dentro
# ---------------------------------------------------------------------------


def test_igualdad_acos_objetivo_cuenta_dentro():
    res = evaluar_ads(
        [_obs(dt.date(2026, 8, 10), cost=Decimal("25"), sales30d=Decimal("100"))],
        VENTANA,
        objetivo=Decimal("25"),
    )
    assert res.acos_pct == Decimal("25")
    assert res.etiqueta == ETIQUETA_DENTRO_DEL_OBJETIVO
    encima = evaluar_ads(
        [_obs(dt.date(2026, 8, 10), cost=Decimal("26"), sales30d=Decimal("100"))],
        VENTANA,
        objetivo=Decimal("25"),
    )
    assert encima.etiqueta == ETIQUETA_POR_ENCIMA_DEL_OBJETIVO


# ---------------------------------------------------------------------------
# §8: distintos objetivos de campana -> sin etiqueta dentro/fuera, sin promedio
# ---------------------------------------------------------------------------


def test_distintos_objetivos_sin_etiqueta_y_sin_target_promedio():
    # Dos campanas con targets distintos sobre la misma publicacion.
    assert objetivo_comparacion([Decimal("20"), Decimal("30")]) is None
    assert objetivo_comparacion([]) is None
    assert objetivo_comparacion([None]) is None
    # Targets iguales en varios grupos: ese valor SI es el objetivo.
    assert objetivo_comparacion([Decimal("25"), Decimal("25")]) == Decimal("25")

    # Las filas del ASIN en varias campanas se SUMAN hacia la clave (§2:
    # no se reparten agregados): 10/100 + 15/150 => ACoS 10 (25/250) sin etiqueta.
    res = evaluar_ads(
        [
            _obs(dt.date(2026, 8, 10), cost=Decimal("10"), sales30d=Decimal("100")),
            _obs(dt.date(2026, 8, 10), cost=Decimal("15"), sales30d=Decimal("150")),
        ],
        VENTANA,
        objetivo=None,
    )
    assert res.acos_pct == Decimal("10")
    assert res.etiqueta is None  # D2: sin objetivo capturado, solo el numero


# ---------------------------------------------------------------------------
# §8: cero vs ausencia vs reporte faltante (los 3 casos de la tabla)
# ---------------------------------------------------------------------------


def test_cero_observado_es_gasto_sin_ventas():
    res = evaluar_ads(
        [_obs(dt.date(2026, 8, 10), cost=Decimal("10"), sales30d=Decimal("0"))],
        VENTANA,
        objetivo=Decimal("25"),
    )
    assert res.etiqueta == ETIQUETA_GASTO_SIN_VENTAS
    assert res.acos_pct is None  # ACoS = null (§4.3)
    assert res.sales30d == Decimal("0")  # cero observado, no ausencia


def test_asin_ausente_del_gzip_completed_es_sin_datos():
    """El gzip SP solo trae filas con actividad: ASIN ausente => Sin datos.
    COMPLETED no lo convierte en Por probar (§2/§8)."""
    res = evaluar_ads([], VENTANA, objetivo=Decimal("25"))
    assert res.etiqueta == ETIQUETA_SIN_DATOS
    assert res.muestra == 0
    assert res.acos_pct is None


def test_reporte_faltante_es_sin_datos():
    res = evaluar_ads([], VENTANA)
    assert res.etiqueta == ETIQUETA_SIN_DATOS
    assert res.acos_pct is None


def test_por_probar_no_existe_como_etiqueta():
    """Regla cerrada 0.4: sin cobertura demostrada, Por probar JAMAS se
    asigna. El modulo ni siquiera define la constante."""
    import app.evaluacion_catalogo as ev

    assert not any("por_probar" in c for c in vars(ev) if isinstance(c, str))
    res = evaluar_ads([], VENTANA)
    assert res.etiqueta == ETIQUETA_SIN_DATOS


def test_ventas_ausentes_con_gasto_no_inventan_cero():
    """sales30d NULL en todas las filas = ausencia: regla 5, sin etiqueta
    (no se convierte en Gasto sin ventas)."""
    res = evaluar_ads(
        [_obs(dt.date(2026, 8, 10), cost=Decimal("10"), sales30d=None)],
        VENTANA,
        objetivo=Decimal("25"),
    )
    assert res.sales30d is None
    assert res.etiqueta is None


# ---------------------------------------------------------------------------
# §8: margen 0/negativo/8% vs objetivo 25% -> seleccionable con motivo (AC3)
# ---------------------------------------------------------------------------


def test_margen_cero_negativo_e_inferior_son_seleccionables_con_motivo():
    for margen, esperado in (
        (Decimal("0"), "Margen cero."),
        (Decimal("-5"), "Margen negativo."),
        (Decimal("8"), "Margen inferior al objetivo."),
        (None, "Margen sin medir."),
    ):
        evaluacion = _listing(economia=_economia(margen=margen), objetivos=[Decimal("25")])
        assert evaluacion.seleccionable is True  # AC3: no bloquea
        assert esperado in evaluacion.motivos
    # Margen sano al nivel del objetivo: sin motivos de margen.
    assert motivos_margen(Decimal("30"), Decimal("25")) == ()


# ---------------------------------------------------------------------------
# §8: muestra 1 vs 100 compras -> mismo resultado, conteos distintos (AC6)
# ---------------------------------------------------------------------------


def test_muestra_1_vs_100_compras_mismo_resultado_conteos_distintos():
    def con_compras(n: Decimal) -> object:
        return evaluar_ads(
            [
                _obs(
                    dt.date(2026, 8, 10),
                    cost=Decimal("25"),
                    sales30d=Decimal("100"),
                    purchases30d=n,
                )
            ],
            VENTANA,
            objetivo=Decimal("25"),
        )

    una, cien = con_compras(Decimal("1")), con_compras(Decimal("100"))
    assert una.etiqueta == cien.etiqueta == ETIQUETA_DENTRO_DEL_OBJETIVO
    assert una.acos_pct == cien.acos_pct == Decimal("25")
    # La muestra va visible, no como mala nota (§2): conteos distintos.
    assert una.purchases30d == Decimal("1")
    assert cien.purchases30d == Decimal("100")


# ---------------------------------------------------------------------------
# §8: dos listings del mismo producto -> total financiero compartido (§5)
# ---------------------------------------------------------------------------


def test_dos_listings_mismo_producto_comparten_total_financiero():
    compartida = _economia(margen=Decimal("20"), venta=Decimal("100"))
    a = _listing(listing_id=1, economia=compartida)
    b = _listing(listing_id=2, economia=compartida)
    assert a.economia.venta_total == b.economia.venta_total == Decimal("100")
    assert a.economia.margen_neto_pct == b.economia.margen_neto_pct == Decimal("20")
    # El orden por ventas totales NO duplica ni suma 200: empatan y desempata
    # listing_id (§5/§7).
    assert [e.listing_id for e in ordenar([b, a], "ventas_totales")] == [1, 2]


# ---------------------------------------------------------------------------
# §8: historia fuera de ventana -> Sin datos (no Por probar, no "nuevo")
# ---------------------------------------------------------------------------


def test_historia_fuera_de_ventana_es_sin_datos():
    filas = [
        _obs(dt.date(2026, 5, 10), cost=Decimal("50"), sales30d=Decimal("200")),
        _obs(dt.date(2026, 9, 5), cost=Decimal("1"), sales30d=Decimal("1")),
    ]
    res = evaluar_ads(filas, VENTANA)  # ambas fechas fuera de la ventana
    assert res.etiqueta == ETIQUETA_SIN_DATOS
    assert res.muestra == 0


# ---------------------------------------------------------------------------
# §8: madurez sin/con observacion posterior (D+1 vs >= D+30)
# ---------------------------------------------------------------------------


def test_madurez_sin_observacion_posterior_es_provisional():
    d = dt.date(2026, 8, 10)
    res = evaluar_ads(
        [
            _obs(
                d,
                cost=Decimal("25"),
                sales30d=Decimal("100"),
                observed_at=dt.datetime(2026, 8, 11, tzinfo=UTC),  # D+1
            )
        ],
        VENTANA,
        objetivo=Decimal("25"),
    )
    assert res.etiqueta == ETIQUETA_DENTRO_DEL_OBJETIVO
    assert not res.maduro
    assert res.provisional  # consulta en D+40: el calendario no madura la fila


def test_madurez_con_observacion_posterior_es_maduro():
    d = dt.date(2026, 8, 10)
    filas = [
        ObservacionAds(
            metric_date=d,
            observed_at=dt.datetime(2026, 8, 11, tzinfo=UTC),
            cost=Decimal("25"),
            sales30d=Decimal("100"),
        ),
        ObservacionAds(  # re-lectura append-only con observed_at >= D+30
            metric_date=d,
            observed_at=dt.datetime.combine(d + dt.timedelta(days=30), dt.time.min, UTC),
            cost=Decimal("25"),
            sales30d=Decimal("100"),
        ),
    ]
    res = evaluar_ads(filas, VENTANA, objetivo=Decimal("25"))
    assert res.maduro
    assert not res.provisional
    assert res.muestra == 1  # re-lectura: misma fecha, no duplica muestra


def test_madurez_exactamente_d_mas_30_cuenta():
    """Limite inclusive (§2: observed_at >= D + 30 dias)."""
    d = dt.date(2026, 8, 1)
    res = evaluar_ads(
        [
            ObservacionAds(
                metric_date=d,
                observed_at=dt.datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
                cost=Decimal("25"),
                sales30d=Decimal("100"),
            )
        ],
        VENTANA,
    )
    assert res.maduro


# ---------------------------------------------------------------------------
# §8: orden estable con nulls y desempate listing_id (§7 D1)
# ---------------------------------------------------------------------------


def _con_acos(listing_id: int, acos: Decimal | None) -> object:
    filas = [] if acos is None else [_obs(dt.date(2026, 8, 10), cost=acos, sales30d=Decimal("100"))]
    return _listing(listing_id=listing_id, filas=filas)


def test_orden_asc_nulls_al_final_desempate_listing_id():
    elems = [
        _con_acos(3, None),
        _con_acos(2, Decimal("40")),
        _con_acos(1, Decimal("40")),
        _con_acos(4, Decimal("10")),
    ]
    assert [e.listing_id for e in ordenar(elems, "acos")] == [4, 1, 2, 3]


def test_orden_desc_nulls_siguen_al_final():
    elems = [
        _con_acos(3, None),
        _con_acos(2, Decimal("40")),
        _con_acos(1, Decimal("40")),
        _con_acos(4, Decimal("10")),
    ]
    # Desc: 40,40 (desempate listing_id asc), 10, y el NULL al FINAL tambien.
    assert [e.listing_id for e in ordenar(elems, "acos", descendente=True)] == [1, 2, 4, 3]


def test_orden_muestra_limitada_no_entra_al_sort_d4():
    """Economia con muestra 1-29 fechas: margen maduro None => NULL al final
    (D4: la muestra limitada jamas es input del sort)."""
    limitada = _economia(muestra_limitada=True)  # margen_neto_pct None
    madura = _economia(margen=Decimal("15"))
    a = _listing(listing_id=1, economia=limitada)
    b = _listing(listing_id=2, economia=madura)
    assert [e.listing_id for e in ordenar([a, b], "margen_observado")] == [2, 1]
    assert [e.listing_id for e in ordenar([a, b], "margen_observado", descendente=True)] == [
        2,
        1,
    ]


def test_orden_mx_us_no_se_mezclan():
    mx = _listing(listing_id=1)
    us = evaluar_listing(
        listing_id=2,
        platform="amazon_us",
        product_id=2,
        asin="B000000002",
        seller_sku="SS-2",
        filas_ads=[],
        ventana=VENTANA,
        economia=EconomiaProducto(platform="amazon_us", product_id=2, moneda="USD"),
        disponibilidad={"estado": ESTADO_DESCONOCIDO},
    )
    with pytest.raises(ValueError, match="no se mezclan"):
        ordenar([mx, us], "acos")


def test_orden_todas_las_metricas_soportadas():
    import app.evaluacion_catalogo as ev

    for metrica in ev.METRICAS_ORDEN:
        ordenar([_listing(listing_id=1)], metrica)
    with pytest.raises(ValueError, match="no soportada"):
        ordenar([_listing(listing_id=1)], "muestra_limitada")


# ---------------------------------------------------------------------------
# §8: columna promovida ausente / halo solo si ambas presentes (§1)
# ---------------------------------------------------------------------------


def test_columna_promovida_ausente_no_se_resta():
    res = evaluar_ads(
        [
            _obs(
                dt.date(2026, 8, 10),
                cost=Decimal("10"),
                sales30d=Decimal("100"),
                promoted30d=None,  # attributedSalesSameSku30d ausente
            )
        ],
        VENTANA,
    )
    assert res.sales30d == Decimal("100")  # el total SI es visible
    assert res.promoted30d is None  # promovido desconocido
    assert res.halo30d is None  # no se resta con una falta


def test_halo30d_solo_si_ambas_presentes():
    res = evaluar_ads(
        [
            _obs(
                dt.date(2026, 8, 10),
                sales30d=Decimal("100"),
                promoted30d=Decimal("60"),
            )
        ],
        VENTANA,
    )
    assert res.halo30d == Decimal("40")

    solo_promovido = evaluar_ads(
        [_obs(dt.date(2026, 8, 10), sales30d=None, promoted30d=Decimal("60"))],
        VENTANA,
    )
    assert solo_promovido.halo30d is None  # falta el total: par desconocido


# ---------------------------------------------------------------------------
# §8/AC10: disponibilidad desconocida no bloquea; ninguna etiqueta bloquea
# ---------------------------------------------------------------------------


def test_disponibilidad_desconocida_no_bloquea():
    evaluacion = _listing(disponibilidad={"estado": ESTADO_DESCONOCIDO})
    assert evaluacion.disponibilidad["estado"] == ESTADO_DESCONOCIDO
    assert evaluacion.seleccionable is True


def test_ninguna_etiqueta_bloquea_la_seleccion():
    casos = {
        ETIQUETA_SIN_DATOS: [],
        ETIQUETA_GASTO_SIN_VENTAS: [
            _obs(dt.date(2026, 8, 10), cost=Decimal("10"), sales30d=Decimal("0"))
        ],
        ETIQUETA_DENTRO_DEL_OBJETIVO: [
            _obs(dt.date(2026, 8, 10), cost=Decimal("25"), sales30d=Decimal("100"))
        ],
        ETIQUETA_POR_ENCIMA_DEL_OBJETIVO: [
            _obs(dt.date(2026, 8, 10), cost=Decimal("26"), sales30d=Decimal("100"))
        ],
        None: [_obs(dt.date(2026, 8, 10), cost=Decimal("0"), sales30d=Decimal("0"))],
    }
    for etiqueta, filas in casos.items():
        evaluacion = _listing(filas=filas, objetivos=[Decimal("25")])
        assert evaluacion.ads.etiqueta == etiqueta
        assert evaluacion.seleccionable is True  # AC3/AC10: jamas bloquea


def test_evaluacion_listing_sin_grupo_muestra_acos_sin_etiqueta():
    """D2: antes de capturar objetivo, ACoS sin etiqueta dentro/fuera."""
    evaluacion = _listing(
        filas=[_obs(dt.date(2026, 8, 10), cost=Decimal("40"), sales30d=Decimal("100"))],
        objetivos=[],
    )
    assert evaluacion.objetivo_acos_pct is None
    assert evaluacion.ads.acos_pct == Decimal("40")
    assert evaluacion.ads.etiqueta is None


# ---------------------------------------------------------------------------
# (b) INTEGRACION del endpoint: adaptador fabrica_web.evaluacion contra
# Postgres real con 0018/0019 (grupos) + 0020 (Ads) + 0021/0022. La logica
# pura ya esta cubierta arriba; aqui se prueba el cableado.
# ---------------------------------------------------------------------------

import os  # noqa: E402
import socket  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

import psycopg  # noqa: E402
from psycopg import sql as pgsql  # noqa: E402
from test_schema import _postgres_obligatorio_ausente, _test_dsn  # noqa: E402

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

ROOT = Path(__file__).resolve().parents[1]
_ORDEN_DB = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0003_goal_bounds_explicit.sql",
    "0004_ad_entity_kind_product_ad.sql",
    "0013_entidad_inerte.sql",
    "0014_keyword_archivo_manual.sql",
    "0015_target_margen_plataforma.sql",
    "0016_target_margen_correcciones.sql",
    "0017_first_seen_at.sql",
    "0018_fabrica_campanas.sql",
    "0019_fabrica_grupo_publicacion_v2.sql",
    "0020_ads_producto_metrica.sql",
    "0021_economia_observada.sql",
    "0022_disponibilidad_snapshot.sql",
)


@contextmanager
def db_evaluacion():
    dsn = _test_dsn()
    db = f"orbit_b4_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in _ORDEN_DB:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sembrar_grupo_planeado(conn, listing, seller_sku, target):
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES ('b4-test', 'amazon_mx', 'collar_perro',"
        " 'B4', 'GO', %s, %s, 'shadow', 'planeado')",
        ("0" * 64, "{}"),
    )
    grupo = conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
        " target_acos_pct, target_procedencia, go_literal, target_origen)"
        " VALUES ('amazon_mx', 'collar_perro', 'B4', 'b4-test', %s, 'manual', 'GO',"
        " 'manual_lanzamiento') RETURNING id",
        (target,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO campana_grupo_producto (grupo_id, product_id, listing_id, seller_sku)"
        " VALUES (%s, %s, %s, %s)",
        (grupo, listing[0], listing[1], seller_sku),
    )


@_skip_db
def test_endpoint_evaluacion_integra_ads_economia_disponibilidad_y_objetivo():
    from app import fabrica_web as fw

    with db_evaluacion() as conn:
        hoy = dt.datetime.now(dt.UTC).date()
        hasta = hoy - dt.timedelta(days=1)
        desde = hasta - dt.timedelta(days=30)
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P-B4', 'P') RETURNING id"
        ).fetchone()[0]
        con_ads = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0TEST00001', 'SS-B4') RETURNING id",
            (pid,),
        ).fetchone()[0]
        sin_ads = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0TEST00002', 'SS-B4X') RETURNING id",
            (pid,),
        ).fetchone()[0]
        # Fila Ads MADURA: metric_date = desde del cron y observed_at hoy
        # (hoy >= desde + 30 dias exactos por la ventana de 31 dias, §2).
        run = conn.execute(
            "INSERT INTO ingest_run (source) VALUES ('test-b4') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ads_product_metric_observation (platform, advertised_asin,"
            " advertised_sku, metric_date, observed_at, metric_currency, clicks, cost,"
            " purchases30d, sales30d, ingest_run_id)"
            " VALUES ('amazon_mx', 'B0TEST00001', 'SS-B4', %s, %s, 'MXN', 400, 25, 4, 100, %s)",
            (desde, dt.datetime.combine(hoy, dt.time.min, UTC), run),
        )
        _sembrar_grupo_planeado(conn, (pid, con_ads), "SS-B4", Decimal("25"))

        res = fw.evaluacion(conn, "amazon_mx")
        por_id = {p["listing_id"]: p for p in res["publicaciones"]}
        assert res["ventana_ads"] == {"desde": desde.isoformat(), "hasta": hasta.isoformat()}

        anunciado = por_id[con_ads]
        assert anunciado["objetivo_acos_pct"] == "25.00"
        assert anunciado["ads"]["acos_pct"] == "25"
        assert anunciado["ads"]["etiqueta"] == ETIQUETA_DENTRO_DEL_OBJETIVO  # igualdad = Dentro
        assert anunciado["ads"]["maduro"] is True
        assert anunciado["ads"]["muestra"] == 1
        assert anunciado["seleccionable"] is True
        assert anunciado["disponibilidad"]["estado"] == ESTADO_DESCONOCIDO

        # ASIN sin filas en la ventana: Sin datos, jamas Por probar.
        assert por_id[sin_ads]["ads"]["etiqueta"] == ETIQUETA_SIN_DATOS
        assert por_id[sin_ads]["seleccionable"] is True

        with pytest.raises(Exception, match="criterio de orden"):
            fw.evaluacion(conn, "amazon_mx", orden="muestra_limitada")


def test_modulo_es_puro_en_runtime():
    """El modulo de evaluacion no importa IO (psycopg/httpx) en runtime: solo
    el adaptador de API consulta la base (B.4, criterio de aceptacion). Se
    corre en subprocess limpio: este archivo importa app.disponibilidad
    (adaptador) y contaminaria sys.modules."""
    import subprocess
    import sys

    codigo = (
        "import sys; import app.evaluacion_catalogo;"
        " assert 'psycopg' not in sys.modules and 'httpx' not in sys.modules"
    )
    res = subprocess.run(
        [sys.executable, "-c", codigo], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stderr
