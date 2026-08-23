"""Tests de ventanas y guardas del optimizador (`app.optimizer.windows`, task 2.1).

(a) UNITARIOS (corren siempre, sin DB): aritmetica exacta de las ventanas 30d
    (bids = max(metric_date) - 3d; cortes = min(ese valor, decided_at(UTC) -
    10d)), bordes EXACTOS de las guardas de plataforma (watermark 7d y
    synced_at 48h, comparadores ESTRICTOS: exactamente 7d/48h NO salta) y la
    completitud como propiedad pura (la unidad es la ENTIDAD, no el termino).
(b) INTEGRACION (patron tests/test_reports_pipeline.py: DB temporal +
    migracion entera via test_schema.SQL + seed propio + DROP final; auto-skip
    solo si ORBIT_TEST_DSN no esta explicito y no hay Postgres local):
    colapso a la ULTIMA observacion por fecha SIN doble conteo (metricas y
    terminos), doble ventana (el agregado de cortes se calcula sobre SU
    ventana, jamas reutilizando la de bids), acople motor<->trigger
    decision_madurez_corte (decision pause con ventana de bids RECHAZADA; con
    la de cortes inserta), guardas watermark/synced_at/sin_datos y
    completitud 6 vs 7 fechas.
(c) Guarda de sintaxis: las SQL del modulo parsean como Postgres real (pglast).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal

import pglast
import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.optimizer import windows as w

# ---------------------------------------------------------------------------
# Reloj FIJO del fixture (determinismo: el modulo jamas esconde un now())
# ---------------------------------------------------------------------------

DECIDED_AT = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
HOY = DECIDED_AT.date()
AHORA = DECIDED_AT
MAX_FECHA = dt.date(2026, 8, 19)  # max(metric_date) de las entidades completas
FIN_BIDS = dt.date(2026, 8, 16)  # MAX_FECHA - 3d
INICIO_BIDS = dt.date(2026, 7, 18)  # FIN_BIDS - 29d (30 dias inclusive)
FIN_CORTES = dt.date(2026, 8, 12)  # min(FIN_BIDS, DECIDED_AT - 10d)
INICIO_CORTES = dt.date(2026, 7, 14)  # FIN_CORTES - 29d

_DIA = dt.timedelta(days=1)


def _obs(fecha: dt.date, hora: int = 1) -> dt.datetime:
    """observed_at de una observacion: medianoche + hora UTC (>= metric_date)."""
    return dt.datetime(fecha.year, fecha.month, fecha.day, hora, tzinfo=dt.UTC)


def _rango(inicio: dt.date, fin: dt.date) -> list[dt.date]:
    """Fechas diarias inclusive (helper de seed)."""
    dias: list[dt.date] = []
    fecha = inicio
    while fecha <= fin:
        dias.append(fecha)
        fecha += _DIA
    return dias


# ---------------------------------------------------------------------------
# (a) UNITARIOS - aritmetica de ventanas (siempre corren)
# ---------------------------------------------------------------------------


def test_fin_ventana_bids_max_menos_3d_exacto():
    """Bids: la ventana termina en max(metric_date) - 3d EXACTO; 30 dias
    calendario INCLUSIVE (inicio = fin - 29d, o sea 30 fechas posibles)."""
    fin = w.fin_ventana_bids(MAX_FECHA)
    assert fin == FIN_BIDS
    inicio = w.inicio_ventana(fin)
    assert inicio == INICIO_BIDS
    assert (fin - inicio).days + 1 == 30


def test_fin_ventana_cortes_toma_el_minimo():
    """Cortes: window_end = min(max - 3d, decided_at(UTC) - 10d). Manda la
    madurez cuando decided_at - 10d es mas viejo; manda la frescura cuando no;
    en empate es el mismo dia."""
    # decided_at - 10d (08-12) < max - 3d (08-16): manda la madurez
    assert w.fin_ventana_cortes(MAX_FECHA, DECIDED_AT) == FIN_CORTES
    # decided_at - 10d (09-05) > max - 3d (08-16): manda la frescura
    decided_lejano = dt.datetime(2026, 9, 15, 0, tzinfo=dt.UTC)
    assert w.fin_ventana_cortes(MAX_FECHA, decided_lejano) == FIN_BIDS
    # empate: decided_at 08-26 -> 08-16 por ambos caminos
    decided_empate = dt.datetime(2026, 8, 26, 0, tzinfo=dt.UTC)
    assert w.fin_ventana_cortes(MAX_FECHA, decided_empate) == FIN_BIDS


def test_fin_ventana_cortes_convierte_a_fecha_utc():
    """La fecha de decided_at se toma en UTC: 23:30 UTC del 08-26 es dia
    08-26 (fin 08-16), aunque en otra zona ya sea 08-27."""
    decided = dt.datetime(2026, 8, 26, 23, 30, tzinfo=dt.UTC)
    assert w.fin_ventana_cortes(MAX_FECHA, decided) == FIN_BIDS
    # DISCRIMINANTE del offset: 20:30 en UTC-6 son 02:30 UTC del 08-23; la
    # fecha que manda es la UTC (fin 08-13). Leer la hora como fecha local
    # (sin convertir) daria 08-22 y fin 08-12.
    decided_offset = dt.datetime(2026, 8, 22, 20, 30, tzinfo=dt.timezone(-dt.timedelta(hours=6)))
    assert w.fin_ventana_cortes(MAX_FECHA, decided_offset) == dt.date(2026, 8, 13)


def test_fin_ventana_cortes_exige_tz_aware():
    """Un decided_at naive evaluaria segun la TZ local del proceso: se rechaza
    ruidosamente (mismo principio que el trigger con UTC fijado)."""
    with pytest.raises(ValueError) as excinfo:
        w.fin_ventana_cortes(MAX_FECHA, dt.datetime(2026, 8, 22, 12))
    assert "tz-aware" in str(excinfo.value)


def test_guarda_plataforma_rechaza_ahora_naive():
    """Misma asimetria cerrada: un `ahora` naive se rechaza con mensaje claro
    ANTES de tocar la base (la resta contra synced_at aware seria un TypeError
    criptico a mitad de guarda)."""
    with pytest.raises(ValueError) as excinfo:
        w.guarda_plataforma(None, "amazon_us", ahora=dt.datetime(2026, 8, 22, 12))
    assert "tz-aware" in str(excinfo.value)


def test_salta_por_watermark_borde_exacto_7d():
    """Comparador ESTRICTO: exactamente 7 dias de watermark NO salta; 8 si."""
    assert not w.salta_por_watermark(HOY, HOY - dt.timedelta(days=7))
    assert w.salta_por_watermark(HOY, HOY - dt.timedelta(days=8))


def test_salta_por_sync_borde_exacto_48h():
    """Comparador ESTRICTO: exactamente 48h de synced_at NO salta; 48h+1s si."""
    assert not w.salta_por_sync(AHORA, AHORA - dt.timedelta(hours=48))
    assert not w.salta_por_sync(AHORA, AHORA - dt.timedelta(hours=47))
    assert w.salta_por_sync(AHORA, AHORA - dt.timedelta(hours=48, seconds=1))


def _agregado(n_fechas: int) -> w.AgregadoMetricas:
    fechas = tuple(INICIO_BIDS + _DIA * i for i in range(n_fechas))
    return w.AgregadoMetricas(
        window_start=INICIO_BIDS,
        window_end=FIN_BIDS,
        fechas=fechas,
        metric_currency="USD",
        cost=Decimal("0"),
        ad_revenue=Decimal("0"),
        revenue_same_sku=Decimal("0"),
        impressions=0,
        clicks=0,
        orders=0,
    )


def test_completitud_unidad_es_la_entidad():
    """>=7 fechas distintas de la ENTIDAD en SU ventana: 6 fechas incompleta,
    7 completa. El termino NO tiene umbral de fechas propio (2.3 lo exige con
    sus umbrales de clicks/cost): esa asimetria esta sellada en el plan."""
    assert _agregado(6).completa is False
    assert _agregado(6).fechas_distintas == 6
    assert _agregado(7).completa is True
    assert _agregado(0).completa is False  # ventana vacia: incompleta


# ---------------------------------------------------------------------------
# (c) GUARDA DE SINTAXIS - las SQL del modulo son Postgres valido
# ---------------------------------------------------------------------------


def test_sql_del_modulo_parsea_como_postgres():
    """Patron test_reports_pipeline: pglast es dev-dep declarada y su
    desaparicion debe FALLAR ruidosamente, no saltar en silencio."""
    for nombre in (
        "_SQL_MAX_FECHA_ENTIDAD",
        "_SQL_AGREGA_METRICAS",
        "_SQL_TERMINOS_CORTES",
        "_SQL_WATERMARK_PLATAFORMA",
        "_SQL_SYNC_PLATAFORMA",
    ):
        sql = getattr(w, nombre).replace("%s", "NULL")
        assert pglast.parse_sql(sql), f"{nombre} no parseo"


# ---------------------------------------------------------------------------
# (b) INTEGRACION - patron test_reports_pipeline, fail-closed por DSN explicito
# ---------------------------------------------------------------------------

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


@contextmanager
def _db_temporal(prefijo: str):
    """DB temporal con la migracion entera (el esquema sellado, sin toques)."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # la migracion entera
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[
        0
    ]


def _entidad(conn, platform: str, kind: str, external: str, parent=None, **extra) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, extra.get("match_type"), extra.get("keyword_text")),
    ).fetchone()[0]


def _estado(conn, ad_entity_id: int, synced_at: dt.datetime) -> None:
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, synced_at) VALUES (%s, %s)",
        (ad_entity_id, synced_at),
    )


def _metrica(
    conn,
    run_id,
    ad_entity_id,
    fecha,
    observed_at,
    *,
    moneda,
    report_id,
    cost=None,
    ad_revenue=None,
    same_sku=None,
    impressions=None,
    clicks=None,
    orders=None,
) -> None:
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, revenue_same_sku, impressions, clicks,"
        " orders, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            ad_entity_id,
            fecha,
            observed_at,
            moneda,
            cost,
            ad_revenue,
            same_sku,
            impressions,
            clicks,
            orders,
            report_id,
            run_id,
        ),
    )


def _termino(
    conn,
    run_id,
    platform,
    ad_entity_id,
    term,
    fecha,
    observed_at,
    *,
    moneda,
    report_id,
    cost=None,
    ad_revenue=None,
    clicks=None,
    orders=None,
) -> None:
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term,"
        " metric_date, observed_at, metric_currency, cost, clicks, orders,"
        " ad_revenue, is_asin_like, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s, %s)",
        (
            platform,
            ad_entity_id,
            term,
            fecha,
            observed_at,
            moneda,
            cost,
            clicks,
            orders,
            ad_revenue,
            report_id,
            run_id,
        ),
    )


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ventanas_y_agregados_en_vivo():
    """Doble ventana + colapso sin doble conteo + completitud, contra la
    migracion real (regla 5: el mismo (entidad, fecha) tiene N observaciones;
    SIEMPRE colapsa a la ultima)."""
    with _db_temporal("orbit_win_test") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9001")

        # kw_a: 33 fechas diarias 07-18..08-19 (max = MAX_FECHA)
        kw_a = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9201",
            parent=camp,
            match_type="EXACT",
            keyword_text="arras",
        )
        for fecha in _rango(INICIO_BIDS, MAX_FECHA):
            _metrica(
                conn,
                run_id,
                kw_a,
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-A",
                cost=Decimal("0.00"),
                ad_revenue=Decimal("0.00"),
                same_sku=Decimal("0.00"),
                impressions=0,
                clicks=0,
                orders=0,
            )
        # DOBLE observacion (kw_a, 08-10) con source_report_id DISTINTO y
        # observed_at distinto: la correccion MAS NUEVA (2.50) debe ganar SOLA
        _metrica(
            conn,
            run_id,
            kw_a,
            dt.date(2026, 8, 10),
            _obs(dt.date(2026, 8, 10), 2),
            moneda="USD",
            report_id="R-A-1",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("10.00"),
            impressions=1,
            clicks=1,
            orders=0,
        )
        _metrica(
            conn,
            run_id,
            kw_a,
            dt.date(2026, 8, 10),
            _obs(dt.date(2026, 8, 11), 9),
            moneda="USD",
            report_id="R-A-2",
            cost=Decimal("2.50"),
            ad_revenue=Decimal("25.00"),
            impressions=2,
            clicks=2,
            orders=0,
        )

        # ------------------------------------------------------------------
        # VENTANA DE BIDS: max(metric_date) - 3d exacto, sin doble conteo
        # ------------------------------------------------------------------
        bids = w.ventana_bids(conn, kw_a)
        assert bids.window_end == FIN_BIDS
        assert bids.window_start == INICIO_BIDS
        # ningun metric_date de los insumos > window_end (por construccion)
        assert max(bids.fechas) <= bids.window_end
        assert min(bids.fechas) >= bids.window_start
        assert bids.fechas_distintas == 30  # 07-18..08-16
        assert bids.completa is True
        assert bids.metric_currency == "USD"
        # SOLO la observacion mas nueva de 08-10: 2.50, no 0+1.00+2.50=3.50
        assert bids.cost == Decimal("2.50")
        assert bids.ad_revenue == Decimal("25.00")
        assert bids.clicks == 2
        assert bids.impressions == 2
        # TIPOS sellados: contadores int (cast ::bigint), dinero Decimal; un
        # Decimal en los contadores reventaria el json.dumps de inputs en 3.1
        # (Decimal('2') == 2 es True, por eso el isinstance y no el ==).
        assert isinstance(bids.clicks, int) and isinstance(bids.impressions, int)
        assert isinstance(bids.cost, Decimal)

        # ------------------------------------------------------------------
        # VENTANA DE CORTES: SU PROPIA ventana (decided_at - 10d manda)
        # ------------------------------------------------------------------
        cortes = w.ventana_cortes(conn, kw_a, DECIDED_AT)
        assert cortes.window_end == FIN_CORTES
        assert cortes.window_start == INICIO_CORTES
        # ningun metric_date > window_end; las fechas frescas quedaron fuera
        assert max(cortes.fechas) <= cortes.window_end
        assert dt.date(2026, 8, 13) not in cortes.fechas
        assert cortes.fechas_distintas == 26  # 07-18..08-12
        assert cortes.completa is True
        # la doble observacion sigue dentro de la ventana de cortes: colapsada
        assert cortes.cost == Decimal("2.50")

        # ------------------------------------------------------------------
        # COMPLETITUD: 6 fechas incompleta, 7 completa (la unidad es la
        # entidad); fechas FUERA de la ventana (08-18/08-19) no cuentan ni
        # entran a los agregados, pero SI suben el max (definen la ventana)
        # ------------------------------------------------------------------
        kw_b = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9202",
            parent=camp,
            match_type="EXACT",
            keyword_text="seis",
        )
        for fecha in _rango(INICIO_BIDS, INICIO_BIDS + _DIA * 5):  # 6 fechas
            _metrica(
                conn,
                run_id,
                kw_b,
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-B",
                cost=Decimal("1.00"),
                ad_revenue=Decimal("2.00"),
                clicks=1,
                orders=0,
            )
        for fecha in (dt.date(2026, 8, 18), dt.date(2026, 8, 19)):
            _metrica(
                conn,
                run_id,
                kw_b,
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-B",
                cost=Decimal("99.00"),
                ad_revenue=Decimal("99.00"),
                clicks=99,
                orders=0,
            )
        bids_b = w.ventana_bids(conn, kw_b)
        assert bids_b.window_end == FIN_BIDS  # mismas ventanas: mismo max
        assert bids_b.fechas_distintas == 6
        assert bids_b.completa is False
        # el agregado excluye lo fresco: cost 6.00, no 6+198
        assert bids_b.cost == Decimal("6.00")
        assert bids_b.clicks == 6

        kw_c = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9203",
            parent=camp,
            match_type="EXACT",
            keyword_text="siete",
        )
        for fecha in _rango(INICIO_BIDS, INICIO_BIDS + _DIA * 6):  # 7 fechas
            _metrica(
                conn,
                run_id,
                kw_c,
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-C",
                cost=Decimal("1.00"),
                ad_revenue=Decimal("2.00"),
                clicks=1,
                orders=0,
            )
        _metrica(
            conn,
            run_id,
            kw_c,
            MAX_FECHA,
            _obs(MAX_FECHA),
            moneda="USD",
            report_id="R-C",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("2.00"),
            clicks=1,
            orders=0,
        )
        bids_c = w.ventana_bids(conn, kw_c)
        assert bids_c.fechas_distintas == 7
        assert bids_c.completa is True
        assert bids_c.cost == Decimal("7.00")

        # ------------------------------------------------------------------
        # VENTANA VACIA y entidad SIN observaciones (regla 3: None explicito)
        # ------------------------------------------------------------------
        kw_e = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9204",
            parent=camp,
            match_type="EXACT",
            keyword_text="fresco",
        )
        _metrica(
            conn,
            run_id,
            kw_e,
            MAX_FECHA,
            _obs(MAX_FECHA),
            moneda="USD",
            report_id="R-E",
            cost=Decimal("5.00"),
            ad_revenue=Decimal("5.00"),
            clicks=5,
            orders=0,
        )
        bids_e = w.ventana_bids(conn, kw_e)
        # hay observaciones (max existe) pero NINGUNA cae en la ventana: el
        # agregado existe, vacio y incompleto; no es None ni un inventado
        assert bids_e is not None
        assert bids_e.fechas == ()
        assert bids_e.cost is None
        assert bids_e.completa is False

        kw_z = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9299",
            parent=camp,
            match_type="EXACT",
            keyword_text="vacio",
        )
        assert w.ventana_bids(conn, kw_z) is None
        assert w.ventana_cortes(conn, kw_z, DECIDED_AT) is None
        assert w.terminos_cortes(conn, kw_z, DECIDED_AT) == ()

        # ------------------------------------------------------------------
        # AGREGADO PARCIAL VENENADO (regla 3): una observacion con cost NULL
        # en la ventana envenena el SUM de cost (None), no lo disfraza
        # ------------------------------------------------------------------
        kw_p = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9205",
            parent=camp,
            match_type="EXACT",
            keyword_text="parcial",
        )
        _metrica(
            conn,
            run_id,
            kw_p,
            dt.date(2026, 8, 1),
            _obs(dt.date(2026, 8, 1)),
            moneda="USD",
            report_id="R-P",
            ad_revenue=Decimal("1.00"),
            clicks=1,
            orders=0,
        )
        _metrica(
            conn,
            run_id,
            kw_p,
            MAX_FECHA,
            _obs(MAX_FECHA),
            moneda="USD",
            report_id="R-P",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("1.00"),
            clicks=1,
            orders=0,
        )
        bids_p = w.ventana_bids(conn, kw_p)
        assert bids_p.cost is None  # 08-01 trajo cost NULL: no hay suma completa
        assert bids_p.clicks == 1  # clicks si estaba en todas: suma normal
        assert bids_p.ad_revenue == Decimal("1.00")

        # ------------------------------------------------------------------
        # TERMINOS: colapsados sobre la ventana de CORTES (unidad entidad)
        # ------------------------------------------------------------------
        ag = _entidad(conn, "amazon_us", "ad_group", "9101", parent=camp)
        for fecha in _rango(dt.date(2026, 8, 5), dt.date(2026, 8, 12)):
            _metrica(
                conn,
                run_id,
                ag,
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-AG",
                cost=Decimal("0.10"),
                ad_revenue=Decimal("0.20"),
                clicks=1,
                orders=0,
            )
        _metrica(
            conn,
            run_id,
            ag,
            MAX_FECHA,
            _obs(MAX_FECHA),
            moneda="USD",
            report_id="R-AG",
            cost=Decimal("0.10"),
            ad_revenue=Decimal("0.20"),
            clicks=1,
            orders=0,
        )

        # el termino tiene UNA fecha (08-10) con doble observacion + UNA fecha
        # fresca (08-15) que esta DENTRO de la ventana de bids pero FUERA de
        # la de cortes: el agregado de terminos tiene que usar la de CORTES
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "arras de boda",
            dt.date(2026, 8, 10),
            _obs(dt.date(2026, 8, 10), 5),
            moneda="USD",
            report_id="R-ST-1",
            cost=Decimal("0.10"),
            clicks=1,
            orders=0,
        )
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "arras de boda",
            dt.date(2026, 8, 10),
            _obs(dt.date(2026, 8, 11), 7),
            moneda="USD",
            report_id="R-ST-2",
            cost=Decimal("0.30"),
            clicks=2,
            orders=0,
        )
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "arras de boda",
            dt.date(2026, 8, 15),
            _obs(dt.date(2026, 8, 15), 23),
            moneda="USD",
            report_id="R-ST-3",
            cost=Decimal("0.50"),
            clicks=5,
            orders=0,
        )

        res = w.ventanas_entidad(conn, ag, DECIDED_AT)
        assert res.bids is not None and res.bids.window_end == FIN_BIDS
        assert res.bids.completa is True  # 8 fechas en ventana de bids
        assert res.cortes is not None and res.cortes.window_end == FIN_CORTES
        assert res.cortes.completa is True  # 8 fechas en ventana de cortes
        assert res.cortes.cost == Decimal("0.80")  # 8 x 0.10 (08-19 fuera)

        # termino de UNA fecha dentro de entidad completa: EXISTE (la unidad
        # de completitud es la entidad; el termino no exige fechas propias)
        terminos = {t.search_term: t for t in res.terminos}
        assert "arras de boda" in terminos
        term = terminos["arras de boda"]
        # SOLO 08-10 (la 08-15 quedo fuera de la ventana de cortes): si el
        # agregado de terminos reutilizara la ventana de BIDS serian 2 fechas
        # y cost 0.80
        assert term.fechas_distintas == 1
        assert term.cost == Decimal("0.30")  # solo la observacion mas nueva
        assert term.clicks == 2
        assert isinstance(term.clicks, int)  # mismo sello de tipo que metricas
        assert term.metric_currency == "USD"

        # una keyword sin terminos devuelve vacio, no error
        res_kw = w.ventanas_entidad(conn, kw_a, DECIDED_AT)
        assert res_kw.terminos == ()
        assert res_kw.cortes.cost == Decimal("2.50")


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_guardas_y_trigger_en_vivo():
    """Guardas de plataforma con sus bordes + acople motor<->trigger
    decision_madurez_corte (una decision de corte con la ventana de bids NO
    puede entrar a la base)."""
    psycopg = pytest.importorskip("psycopg")

    with _db_temporal("orbit_guard_test") as conn:
        run_id = _run(conn)

        # SIN DATOS: meli no tiene metricas NI estado -> motivo explicito
        motivo = w.guarda_plataforma(conn, "meli", ahora=AHORA)
        assert motivo is not None
        assert motivo.guarda == "sin_datos"
        assert "meli" in motivo.detalle

        # amazon_us: watermark EXACTO 7d + sync 47h -> NO salta (bordes)
        camp_us = _entidad(conn, "amazon_us", "campaign", "9001")
        _metrica(
            conn,
            run_id,
            camp_us,
            HOY - dt.timedelta(days=7),
            _obs(HOY),
            moneda="USD",
            report_id="R-US",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("2.00"),
            clicks=1,
            orders=0,
        )
        _estado(conn, camp_us, AHORA - dt.timedelta(hours=47))
        assert w.guarda_plataforma(conn, "amazon_us", ahora=AHORA) is None

        # amazon_mx: watermark de 8d -> salta por watermark
        camp_mx = _entidad(conn, "amazon_mx", "campaign", "7001")
        watermark_viejo = HOY - dt.timedelta(days=8)
        _metrica(
            conn,
            run_id,
            camp_mx,
            watermark_viejo,
            _obs(watermark_viejo),
            moneda="MXN",
            report_id="R-MX",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("2.00"),
            clicks=1,
            orders=0,
        )
        _estado(conn, camp_mx, AHORA - dt.timedelta(hours=47))
        motivo = w.guarda_plataforma(conn, "amazon_mx", ahora=AHORA)
        assert motivo is not None
        assert motivo.guarda == "watermark"
        assert watermark_viejo.isoformat() in motivo.detalle

        # meli: watermark 7d (ok) pero sync de 49h -> salta por synced_at
        camp_meli = _entidad(conn, "meli", "campaign", "5001")
        _metrica(
            conn,
            run_id,
            camp_meli,
            HOY - dt.timedelta(days=7),
            _obs(HOY),
            moneda="MXN",
            report_id="R-ML",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("2.00"),
            clicks=1,
            orders=0,
        )
        _estado(conn, camp_meli, AHORA - dt.timedelta(hours=49))
        motivo = w.guarda_plataforma(conn, "meli", ahora=AHORA)
        assert motivo is not None
        assert motivo.guarda == "synced_at"
        assert "48" in motivo.detalle

        # ------------------------------------------------------------------
        # ACOPLE MOTOR<->TRIGGER: decision pause con la ventana de BIDS
        # (reciente) la RECHAZA decision_madurez_corte; con la de CORTES entra
        # ------------------------------------------------------------------
        kw = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9201",
            parent=camp_us,
            match_type="EXACT",
            keyword_text="arras",
        )
        for fecha in _rango(INICIO_BIDS, MAX_FECHA):
            _metrica(
                conn,
                run_id,
                kw,
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-TRG",
                cost=Decimal("1.00"),
                ad_revenue=Decimal("2.00"),
                clicks=1,
                orders=0,
            )
        bids = w.ventana_bids(conn, kw)
        cortes = w.ventana_cortes(conn, kw, DECIDED_AT)
        assert bids.window_end == FIN_BIDS
        assert cortes.window_end == FIN_CORTES
        # ningun insumo del corte es posterior a SU window_end (DoD)
        assert max(cortes.fechas) <= cortes.window_end

        config_id = conn.execute(
            "INSERT INTO config_version (settings) VALUES ('{}') RETURNING id"
        ).fetchone()[0]
        ciclo_id = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('shadow', 'amazon_us')"
            " RETURNING id"
        ).fetchone()[0]
        data_observed_at = dt.datetime(2026, 8, 20, 0, 0, tzinfo=dt.UTC)

        # ventana de BIDS (08-16 > decided_at - 10d = 08-12): el trigger salta
        with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
            conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
                " config_version_id, data_observed_at, window_start, window_end, inputs)"
                " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, '{}')",
                (
                    ciclo_id,
                    kw,
                    DECIDED_AT,
                    config_id,
                    data_observed_at,
                    bids.window_start,
                    bids.window_end,
                ),
            )
        assert "maduracion" in str(excinfo.value)

        # ventana de CORTES (== decided_at - 10d EXACTO): inserta
        conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
            " config_version_id, data_observed_at, window_start, window_end, inputs)"
            " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, '{}')",
            (
                ciclo_id,
                kw,
                DECIDED_AT,
                config_id,
                data_observed_at,
                cortes.window_start,
                cortes.window_end,
            ),
        )
        fila = conn.execute(
            "SELECT kind, window_start, window_end FROM decision WHERE ad_entity_id = %s",
            (kw,),
        ).fetchone()
        assert fila == ("pause", INICIO_CORTES, FIN_CORTES)
