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
    ventana, jamas reutilizando la de bids), ANCLA de terminos en SUS
    observaciones (ad group SIN metricas, forma real de produccion; hallazgo
    codex+grok), acople motor<->trigger decision_madurez_corte (decision
    pause con ventana de bids RECHAZADA; con la de cortes inserta), guardas
    watermark/synced_at/sin_datos, completitud 6 vs 7 fechas, veneno con
    fixture DISCRIMINANTE (dos filas in-window, una NULL; hallazgo grok),
    is_asin_like expuesto desde la fuente (bool_or fail-closed) y
    observed_at_max (insumo de decision.data_observed_at; hallazgo codex).
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
from test_schema import SQL, _postgres_obligatorio_ausente, _test_dsn

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
        observed_at_max=None,
    )


def test_completitud_unidad_es_la_entidad():
    """>=7 fechas distintas de la ENTIDAD en SU ventana: 6 fechas incompleta,
    7 completa. El termino NO tiene umbral de fechas propio (2.3 lo exige con
    sus umbrales de clicks/cost): esa asimetria esta sellada en el plan."""
    assert _agregado(6).completa is False
    assert _agregado(6).fechas_distintas == 6
    assert _agregado(7).completa is True
    assert _agregado(0).completa is False  # ventana vacia: incompleta


def _terminos_cortes(n_fechas: int) -> w.TerminosCortes:
    """Fixture puro de TerminosCortes con termino simbolico."""
    fechas = tuple(INICIO_CORTES + _DIA * i for i in range(n_fechas))
    return w.TerminosCortes(
        ad_entity_id=1,
        window_start=INICIO_CORTES if fechas else None,
        window_end=FIN_CORTES if fechas else None,
        fechas_entidad=fechas,
        terminos=(),
    )


def test_completitud_de_terminos_mismo_borde_6_vs_7():
    """Mismo borde sellado de la entidad, medido sobre fechas de TERMINOS
    (el camino de produccion de higiene: ad groups sin metricas). 6 fechas
    incompleta, 7 completa, sin observaciones incompleta (hallazgo grok r2:
    el live solo cubria 0 y 14)."""
    assert _terminos_cortes(6).completa is False
    assert _terminos_cortes(7).completa is True
    assert _terminos_cortes(0).completa is False
    assert _terminos_cortes(0).window_end is None  # sin observaciones: sin ventana


# ---------------------------------------------------------------------------
# (c) GUARDA DE SINTAXIS - las SQL del modulo son Postgres valido
# ---------------------------------------------------------------------------


def test_sql_del_modulo_parsea_como_postgres():
    """Patron test_reports_pipeline: pglast es dev-dep declarada y su
    desaparicion debe FALLAR ruidosamente, no saltar en silencio."""
    for nombre in (
        "_SQL_MAX_FECHA_ENTIDAD",
        "_SQL_AGREGA_METRICAS",
        "_SQL_MAX_FECHA_TERMINOS",
        "_SQL_TERMINOS_CORTES",
        "_SQL_FECHAS_ENTIDAD_TERMINOS",
        "_SQL_WATERMARK_PLATAFORMA",
        "_SQL_SYNC_PLATAFORMA",
        "_SQL_EVIDENCIA_AD_GROUP",
        "_SQL_COLAPSO_TERMINOS",
    ):
        sql = getattr(w, nombre)
        if "{where}" in sql:
            sql = sql.format(where="WHERE ad_entity_id = NULL")
        assert pglast.parse_sql(sql.replace("%s", "NULL")), f"{nombre} no parseo"


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
    asin=False,
) -> None:
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term,"
        " metric_date, observed_at, metric_currency, cost, clicks, orders,"
        " ad_revenue, is_asin_like, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
            asin,
            report_id,
            run_id,
        ),
    )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_terminos_y_cortes_en_vivo():
    """Terminos + colapso sin doble conteo + completitud, contra la
    migracion real (regla 5: el mismo (entidad, fecha) tiene N observaciones;
    SIEMPRE colapsa a la ultima). La ventana de bids se borro con bandas_v1
    (BIDS 02 M.3); sus secciones se fueron con ella."""
    with _db_temporal("orbit_win_test") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9001")

        # ------------------------------------------------------------------
        # TERMINOS: ad group SIN metricas (forma real de produccion: los
        # terminos viven en ad groups y NO hay reporte de metricas de ad
        # groups). REGRESION del ancla (hallazgo codex+grok): la ventana de
        # terminos se ancla en SUS observaciones; anclada en v_metric_latest
        # del ad group devolvia () SIEMPRE.
        # ------------------------------------------------------------------
        ag = _entidad(conn, "amazon_us", "ad_group", "9101", parent=camp)

        # "arras de boda": UNA fecha madura (08-10) con doble observacion + UNA
        # fresca (08-18) que sube el ancla. Con max=08-18: el recorte de
        # frescura daria 08-15 y la madurez 08-12 -> manda la MADUREZ; si la
        # ventana terminara en max-3d (08-15) el termino tendria 2 fechas y
        # cost 0.80 (DISCRIMINANTE bids-vs-cortes; hallazgo grok r2)
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
            dt.date(2026, 8, 18),
            _obs(dt.date(2026, 8, 18), 23),
            moneda="USD",
            report_id="R-ST-3",
            cost=Decimal("0.50"),
            clicks=5,
            orders=0,
        )
        # "arras plata": 8 fechas maduras (07-28..08-04) -> completa la entidad
        for fecha in _rango(dt.date(2026, 7, 28), dt.date(2026, 8, 4)):
            _termino(
                conn,
                run_id,
                "amazon_us",
                ag,
                "arras plata",
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-ST-P",
                cost=Decimal("0.05"),
                clicks=1,
                orders=0,
            )
        # ASIN-like (patron sellado: 10 alfanumericos empezando en b0): 2.3 lo
        # salta SIEMPRE; el agregado tiene que exponerlo desde la fuente
        # (regla 2), no re-clasificarlo
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "b0abc12xyz",
            dt.date(2026, 8, 5),
            _obs(dt.date(2026, 8, 5)),
            moneda="USD",
            report_id="R-ST-A",
            cost=Decimal("0.60"),
            clicks=1,
            orders=0,
            asin=True,
        )
        # DIVERGENCIA IMPOSIBLE a proposito (el clasificador es determinista):
        # una fila ASIN-like y otra no, fechas distintas. bool_or = fail-closed
        # -- cualquier fila ASIN-like salta el termino completo. Es el sello
        # del SEMANTICO del agregado, no un fixture de ingesta.
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "mixto imposible",
            dt.date(2026, 8, 6),
            _obs(dt.date(2026, 8, 6)),
            moneda="USD",
            report_id="R-ST-M1",
            cost=Decimal("0.01"),
            clicks=1,
            orders=0,
            asin=True,
        )
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "mixto imposible",
            dt.date(2026, 8, 7),
            _obs(dt.date(2026, 8, 7)),
            moneda="USD",
            report_id="R-ST-M2",
            cost=Decimal("0.01"),
            clicks=1,
            orders=0,
        )
        # VENENO en terminos (mismo bool_and que metricas): cost NULL en una
        # fecha y valor en otra -> la suma de cost es None, los clicks suman
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "cost nulo",
            dt.date(2026, 8, 8),
            _obs(dt.date(2026, 8, 8)),
            moneda="USD",
            report_id="R-ST-N1",
            clicks=1,
            orders=0,
        )
        _termino(
            conn,
            run_id,
            "amazon_us",
            ag,
            "cost nulo",
            dt.date(2026, 8, 9),
            _obs(dt.date(2026, 8, 9)),
            moneda="USD",
            report_id="R-ST-N2",
            cost=Decimal("0.40"),
            clicks=2,
            orders=0,
        )

        tc = w.terminos_cortes(conn, ag, DECIDED_AT)
        assert tc.window_end == FIN_CORTES  # min(max(08-18) - 3d=08-15, 08-12)
        assert tc.window_start == INICIO_CORTES
        assert dt.date(2026, 8, 18) not in tc.fechas_entidad  # fresca: fuera
        # 14 fechas de entidad en ventana (08-10, 07-28..08-04, 08-05..08-09)
        assert tc.fechas_distintas == 14
        assert tc.completa is True

        terminos = {t.search_term: t for t in tc.terminos}
        term = terminos["arras de boda"]
        # SOLO 08-10: si la ventana terminara en el recorte de frescura
        # (max-3d = 08-15) el termino tendria 2 fechas y cost 0.80
        assert term.fechas_distintas == 1
        assert term.cost == Decimal("0.30")  # solo la observacion mas nueva
        assert term.clicks == 2
        assert isinstance(term.clicks, int)  # mismo sello de tipo que metricas
        assert term.metric_currency == "USD"
        assert term.is_asin_like is False
        assert term.observed_at_max == _obs(dt.date(2026, 8, 11), 7)
        # termino long-tail de 8 fechas propias (la completitud es de la
        # ENTIDAD; el termino no exige fechas propias)
        plata = terminos["arras plata"]
        assert plata.fechas_distintas == 8
        assert plata.cost == Decimal("0.40")
        # ASIN-like expuesto desde la fuente: True directo y True por bool_or
        # ante divergencia (fail-closed)
        assert terminos["b0abc12xyz"].is_asin_like is True
        assert terminos["mixto imposible"].is_asin_like is True
        nulo = terminos["cost nulo"]
        assert nulo.cost is None  # sin bool_and, SUM ignoraria el NULL: 0.40
        assert nulo.clicks == 3

        # un ad group sin terminos devuelve la estructura VACIA, no error
        ag_vacio = _entidad(conn, "amazon_us", "ad_group", "9102", parent=camp)
        res_vacio = w.terminos_cortes(conn, ag_vacio, DECIDED_AT)
        assert res_vacio.terminos == ()
        assert res_vacio.completa is False


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
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
        cortes = w.ventana_cortes(conn, kw, DECIDED_AT)
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
                    INICIO_BIDS,
                    FIN_BIDS,
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


# ---------------------------------------------------------------------------
# (b) INTEGRACION - ventana de EVIDENCIA por ad group (CORTES 01, task 1.1)
#
# D = fecha UTC de decided_at (08-22) SIN -3d de frescura NI clamp por
# watermark (asimetria DELIBERADA del spec); ventana LITERAL
# BETWEEN D-90 AND D-10 (L=90 es lookback, NO longitud: son 81 fechas).
# ---------------------------------------------------------------------------

DESDE_EVIDENCIA = dt.date(2026, 5, 24)  # D(08-22) - 90d, borde inferior INCLUSIVE
HASTA_EVIDENCIA = dt.date(2026, 8, 12)  # D - 10d, borde superior INCLUSIVE


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_evidencia_ad_group_en_vivo():
    """Suma colapsada de las hojas keyword+product_target por ad group sobre
    la ventana LITERAL D-90..D-10: un query por plataforma, dict por
    ad_group_id, grupo sin filas en ventana AUSENTE (regla 3: jamas ceros)."""
    with _db_temporal("orbit_evid_test") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9001")

        # GRUPO A: dos hojas (keyword + product_target) con overlap de fechas
        ag_a = _entidad(conn, "amazon_us", "ad_group", "9101", parent=camp)
        kw_a = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9201",
            parent=ag_a,
            match_type="EXACT",
            keyword_text="arras",
        )
        pt_a = _entidad(conn, "amazon_us", "product_target", "9301", parent=ag_a)

        # borde inferior EXACTO D-90 (05-24): dentro
        _metrica(
            conn,
            run_id,
            kw_a,
            dt.date(2026, 5, 24),
            _obs(dt.date(2026, 5, 24)),
            moneda="USD",
            report_id="R-A",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("1.00"),
            clicks=1,
            orders=0,
        )
        # DOBLE observacion (kw_a, 06-01): gana la MAS NUEVA (cost 2.00 /
        # clicks 2 / orders 1); la vieja JAMAS se suma (regla 5). La
        # correccion llega al dia siguiente, como un backfill real.
        _metrica(
            conn,
            run_id,
            kw_a,
            dt.date(2026, 6, 1),
            _obs(dt.date(2026, 6, 1), 1),
            moneda="USD",
            report_id="R-A-1",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("1.00"),
            clicks=1,
            orders=0,
        )
        _metrica(
            conn,
            run_id,
            kw_a,
            dt.date(2026, 6, 1),
            _obs(dt.date(2026, 6, 2), 9),
            moneda="USD",
            report_id="R-A-2",
            cost=Decimal("2.00"),
            ad_revenue=Decimal("2.00"),
            clicks=2,
            orders=1,
        )
        # borde superior EXACTO D-10 (08-12): dentro
        _metrica(
            conn,
            run_id,
            kw_a,
            dt.date(2026, 8, 12),
            _obs(dt.date(2026, 8, 12), 5),
            moneda="USD",
            report_id="R-A",
            cost=Decimal("4.00"),
            ad_revenue=Decimal("4.00"),
            clicks=4,
            orders=1,
        )
        # FUERA de bordes (mismo kw_a): D-91 (05-23) y D-9 (08-13) con valores
        # que revolverian cualquier suma si entraran
        for fecha in (dt.date(2026, 5, 23), dt.date(2026, 8, 13)):
            _metrica(
                conn,
                run_id,
                kw_a,
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-A",
                cost=Decimal("99.00"),
                ad_revenue=Decimal("99.00"),
                clicks=99,
                orders=99,
            )

        # pt_a: 06-01 (MISMO dia que kw_a -> UNION de fechas: cuenta 1) y
        # 06-07 con observed_at POSTERIOR a decided_at: observed_at_max viene
        # CRUDO (sin clamp aqui; el clamp es de 1.2)
        _metrica(
            conn,
            run_id,
            pt_a,
            dt.date(2026, 6, 1),
            _obs(dt.date(2026, 6, 1)),
            moneda="USD",
            report_id="R-PT",
            cost=Decimal("10.00"),
            ad_revenue=Decimal("10.00"),
            clicks=10,
            orders=2,
        )
        _metrica(
            conn,
            run_id,
            pt_a,
            dt.date(2026, 6, 7),
            dt.datetime(2026, 8, 23, 8, 0, tzinfo=dt.UTC),
            moneda="USD",
            report_id="R-PT",
            cost=Decimal("20.00"),
            ad_revenue=Decimal("20.00"),
            clicks=20,
            orders=3,
        )

        # placement bajo el MISMO grupo: NO es hoja de evidencia (solo
        # keyword+product_target); sus valores gigantes no deben entrar
        pl_a = _entidad(conn, "amazon_us", "placement", "9401", parent=ag_a)
        _metrica(
            conn,
            run_id,
            pl_a,
            dt.date(2026, 6, 8),
            _obs(dt.date(2026, 6, 8)),
            moneda="USD",
            report_id="R-PL",
            cost=Decimal("500.00"),
            ad_revenue=Decimal("500.00"),
            clicks=500,
            orders=500,
        )

        # GRUPO B: veneno por None (regla 3, POR METRICA): clicks NULL en una
        # fecha y valor en otra -> clicks del grupo None; cost sigue sumando
        ag_b = _entidad(conn, "amazon_us", "ad_group", "9102", parent=camp)
        kw_b = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9202",
            parent=ag_b,
            match_type="EXACT",
            keyword_text="parcial",
        )
        _metrica(
            conn,
            run_id,
            kw_b,
            dt.date(2026, 6, 10),
            _obs(dt.date(2026, 6, 10)),
            moneda="USD",
            report_id="R-B",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("1.00"),
            orders=0,
        )
        _metrica(
            conn,
            run_id,
            kw_b,
            dt.date(2026, 6, 11),
            _obs(dt.date(2026, 6, 11)),
            moneda="USD",
            report_id="R-B",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("1.00"),
            clicks=5,
            orders=0,
        )

        # GRUPO C: solo filas FUERA de la ventana (frescas) -> AUSENTE del dict
        ag_c = _entidad(conn, "amazon_us", "ad_group", "9103", parent=camp)
        kw_c = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9203",
            parent=ag_c,
            match_type="EXACT",
            keyword_text="fresco",
        )
        for fecha in (dt.date(2026, 8, 13), dt.date(2026, 8, 20)):
            _metrica(
                conn,
                run_id,
                kw_c,
                fecha,
                _obs(fecha),
                moneda="USD",
                report_id="R-C",
                cost=Decimal("1.00"),
                ad_revenue=Decimal("1.00"),
                clicks=1,
                orders=0,
            )

        # GRUPO E: ad group SIN hojas -> AUSENTE del dict
        _entidad(conn, "amazon_us", "ad_group", "9104", parent=camp)

        # GRUPO MX: misma forma en OTRA plataforma -> solo aparece al pedir MX
        camp_mx = _entidad(conn, "amazon_mx", "campaign", "7001")
        ag_mx = _entidad(conn, "amazon_mx", "ad_group", "7101", parent=camp_mx)
        kw_mx = _entidad(
            conn,
            "amazon_mx",
            "keyword",
            "7201",
            parent=ag_mx,
            match_type="EXACT",
            keyword_text="plata",
        )
        _metrica(
            conn,
            run_id,
            kw_mx,
            dt.date(2026, 7, 1),
            _obs(dt.date(2026, 7, 1)),
            moneda="MXN",
            report_id="R-MX",
            cost=Decimal("1.00"),
            ad_revenue=Decimal("1.00"),
            clicks=1,
            orders=0,
        )

        evid = w.ventanas_evidencia_ad_group(conn, "amazon_us", DECIDED_AT)

        # sin filas en ventana (C), sin hojas (E) y otra plataforma (MX):
        # AUSENTES, jamas ceros inventados (regla 3)
        assert set(evid) == {ag_a, ag_b}

        a = evid[ag_a]
        assert a.ventana_desde == DESDE_EVIDENCIA
        assert a.ventana_hasta == HASTA_EVIDENCIA
        # UNION de fechas del GRUPO: 05-24, 06-01 (dos hojas el mismo dia = 1),
        # 06-07, 08-12. Sumar conteos por hoja daria 5 (regla 9: infla Z).
        assert a.fechas_distintas == 4
        # colapso bitemporal: SOLO la obs mas nueva de 06-01 suma (2.00, no
        # 1.00+2.00); bordes: D-91/D-9 fuera (99.00/99 clicks no entraron)
        assert a.cost == Decimal("37.00")  # 1+2+4 (kw) + 10+20 (pt)
        assert a.clicks == 37
        assert a.orders == 7  # 0+1+1 (kw) + 2+3 (pt)
        assert a.ad_revenue == Decimal("37.00")
        # tipos sellados del modulo: contadores int, dinero Decimal
        assert isinstance(a.clicks, int)
        assert isinstance(a.cost, Decimal)
        assert a.metric_currency == "USD"
        # CRUDO, sin clamp: la obs de pt_a (06-07) se observo el 08-23 08:00,
        # POSTERIOR a decided_at (08-22 12:00). El clamp a decided_at es de
        # 1.2; aqui llega crudo (hallazgo qwen ronda 2).
        assert a.observed_at_max == dt.datetime(2026, 8, 23, 8, 0, tzinfo=dt.UTC)

        # veneno POR METRICA: sin el bool_and, SUM ignoraria el NULL y daria
        # clicks=5 (suma parcial disfrazada de completa; regla 9)
        b = evid[ag_b]
        assert b.clicks is None
        assert b.cost == Decimal("2.00")
        assert b.orders == 0
        assert b.fechas_distintas == 2
        assert b.observed_at_max == _obs(dt.date(2026, 6, 11))

        # otra plataforma: mismo query, sus grupos y SU moneda
        evid_mx = w.ventanas_evidencia_ad_group(conn, "amazon_mx", DECIDED_AT)
        assert set(evid_mx) == {ag_mx}
        assert evid_mx[ag_mx].clicks == 1
        assert evid_mx[ag_mx].metric_currency == "MXN"

        # decided_at naive: rechazo ruidoso ANTES de tocar la base (patron
        # _fecha_utc del modulo)
        with pytest.raises(ValueError) as excinfo:
            w.ventanas_evidencia_ad_group(conn, "amazon_us", dt.datetime(2026, 8, 22, 12))
        assert "tz-aware" in str(excinfo.value)


# ---------------------------------------------------------------------------
# A4: conversion jerarquica + CPC vigente (integracion)
# ---------------------------------------------------------------------------


@contextmanager
def _db_temporal_a4(prefijo: str):
    """DB temporal con 0001 + 0018 (fabrica) + 0047 (familias)
    (verificado: aplican limpio)."""
    import pathlib

    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    migraciones = pathlib.Path(__file__).resolve().parents[1] / "migrations"
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ("0001_initial.sql", "0018_fabrica_campanas.sql", "0047_familias.sql"):
            conn.execute((migraciones / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _familia(conn, platform, nombre, slug, padre_id=None):
    return conn.execute(
        "INSERT INTO familia (platform, nombre, slug, padre_id)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (platform, nombre, slug, padre_id),
    ).fetchone()[0]


_SQL_TERMINOS_CORTES_ANTES = """
SELECT ad_entity_id,
       search_term,
       min(metric_currency),
       CASE WHEN bool_and(cost IS NOT NULL) THEN sum(cost) END,
       CASE WHEN bool_and(ad_revenue IS NOT NULL) THEN sum(ad_revenue) END,
       CASE WHEN bool_and(clicks IS NOT NULL) THEN sum(clicks)::bigint END,
       CASE WHEN bool_and(orders IS NOT NULL) THEN sum(orders)::bigint END,
       count(DISTINCT metric_date),
       bool_or(is_asin_like),
       max(observed_at)
  FROM (
      SELECT DISTINCT ON (ad_entity_id, search_term, metric_date)
             ad_entity_id, search_term, metric_date, metric_currency,
             cost, ad_revenue, clicks, orders, is_asin_like, observed_at,
             source_report_id
        FROM search_term_observation
       WHERE ad_entity_id = %s
         AND metric_date BETWEEN %s AND %s
       ORDER BY ad_entity_id, search_term, metric_date, observed_at DESC,
                source_report_id DESC NULLS LAST
  ) colapsado
 GROUP BY ad_entity_id, search_term
 ORDER BY search_term
"""


def test_subquery_compartido_deja_terminos_cortes_igual():
    """S.4 Cambio 1: el texto final es el de antes salvo espacios."""
    assert " ".join(w._SQL_TERMINOS_CORTES.split()) == " ".join(_SQL_TERMINOS_CORTES_ANTES.split())


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_subquery_compartido_da_las_mismas_filas():
    """S.4 Cambio 1: la constante vieja y la nueva devuelven lo mismo."""
    with _db_temporal("orbit_win_s4") as conn:
        run_id = _run(conn)
        grupo = _entidad(conn, "amazon_mx", "ad_group", "g-1")
        for i, fecha in enumerate(_rango(dt.date(2026, 9, 20), dt.date(2026, 9, 26))):
            _termino(
                conn,
                run_id,
                "amazon_mx",
                grupo,
                "collar oro",
                fecha,
                _obs(fecha),
                moneda="MXN",
                report_id="R",
                cost=Decimal("10.00"),
                clicks=5,
                orders=0 if i < 6 else None,
            )
        params = (grupo, dt.date(2026, 9, 20), dt.date(2026, 9, 26))
        antes = conn.execute(_SQL_TERMINOS_CORTES_ANTES, params).fetchall()
        ahora = conn.execute(w._SQL_TERMINOS_CORTES, params).fetchall()
        assert antes == ahora
        assert len(ahora) == 1
