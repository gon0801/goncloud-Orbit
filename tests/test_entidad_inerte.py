"""Tests de la vista v_entidad_inerte (BIDS 01, tarea 1.2, D2).

La vista es la UNICA fuente de "inerte" para el ciclo, la pagina, el digest
y la herramienta: hoja ENABLED (con ad group y campana ENABLED) sin
impresiones en los ultimos N = 14 dias contados desde el WATERMARK de su
plataforma (max(metric_date) en v_metric_latest, jamas desde now()).
Clasificacion sobre 90 dias desde el watermark: con_ventas_previas /
gasto_sin_ventas / peso_muerto.

Son INTEGRACION (Postgres temporal con 0001+0002+0003+0013): skip
automatico sin servidor, como el resto de la suite.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import SQL, SQL2, SQL3, _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
SQL13 = (ROOT / "migrations" / "0013_entidad_inerte.sql").read_text(encoding="utf-8")

_ANCLA = dt.datetime(2026, 8, 22, 8, 0, tzinfo=dt.UTC)


@contextmanager
def _db_inerte(prefijo: str):
    """DB temporal con el esquema + la migracion 0013."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL2)
        conn.execute(SQL3)
        conn.execute(SQL13)
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


def _estado(conn, ad_entity_id: int, status: str = "ENABLED") -> None:
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, %s)",
        (ad_entity_id, status, _ANCLA),
    )


def _metrica(
    conn,
    run_id: int,
    ad_entity_id: int,
    fecha: dt.date,
    *,
    moneda: str = "USD",
    cost=None,
    ad_revenue=None,
    clicks=None,
    orders=None,
    impressions=None,
) -> None:
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            ad_entity_id,
            fecha,
            dt.datetime(fecha.year, fecha.month, fecha.day, 1, tzinfo=dt.UTC),
            moneda,
            Decimal(cost) if cost is not None else None,
            Decimal(ad_revenue) if ad_revenue is not None else None,
            impressions,
            clicks,
            orders,
            run_id,
        ),
    )


def _filas_por_external(conn) -> dict:
    filas = conn.execute(
        "SELECT external_id, clasificacion, dias_sin_impresiones, watermark,"
        " gasto_90d, ordenes_90d, moneda FROM v_entidad_inerte"
    ).fetchall()
    return {f[0]: f[1:] for f in filas}


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_vista_clasifica_tres_casos_y_excluye_reciente():
    """DoD 1.2: sin impresiones 20d con gasto 90d -> gasto_sin_ventas; sin
    impresiones y sin nada -> peso_muerto; con impresiones hace 3d -> NO
    aparece. N contado desde el watermark, no desde now()."""
    with _db_inerte("orbit_inerte_3casos") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9401")
        ag = _entidad(conn, "amazon_us", "ad_group", "9402", parent=camp)
        gasto = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9403",
            parent=ag,
            match_type="EXACT",
            keyword_text="gasto sin ventas",
        )
        muerto = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9404",
            parent=ag,
            match_type="EXACT",
            keyword_text="peso muerto",
        )
        reciente = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9405",
            parent=ag,
            match_type="EXACT",
            keyword_text="con trafico",
        )
        for eid in (camp, ag, gasto, muerto, reciente):
            _estado(conn, eid)
        # Watermark US = 08-16 (la hoja reciente). Ventana 14d: > 08-02.
        _metrica(
            conn,
            run_id,
            gasto,
            dt.date(2026, 7, 30),
            cost="5.00",
            ad_revenue="0.00",
            clicks=5,
            orders=0,
            # Cross-review grok-3: impresion REAL fuera de 14d (forma de
            # reporte) para pinear dias_sin_impresiones == 17.
            impressions=50,
        )
        _metrica(
            conn,
            run_id,
            reciente,
            dt.date(2026, 8, 16),
            cost="5.00",
            ad_revenue="0.00",
            clicks=5,
            orders=0,
            impressions=50,
        )

        por = _filas_por_external(conn)
        assert set(por) == {"9403", "9404"}
        clas, dias, wm, gasto90, ord90, moneda = por["9403"]
        assert clas == "gasto_sin_ventas"
        assert dias == 17  # ultima impresion real 07-30, watermark 08-16
        assert wm == dt.date(2026, 8, 16)
        assert gasto90 == Decimal("5")
        assert ord90 == 0
        assert moneda == "USD"  # BIDS 01 revision: gasto con su moneda
        clas, dias, wm, gasto90, ord90, moneda = por["9404"]
        assert clas == "peso_muerto"
        assert dias is None
        assert gasto90 == 0
        assert moneda is None  # sin filas: no hay moneda que exponer


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_vista_cuenta_desde_el_watermark_no_desde_now():
    """DoD 1.2 (watermark viejo): max(metric_date) MX = 08-09 con
    impresiones 08-07 -> 12 - 10 = 2 dias <= 14 -> NO inerte, aunque desde
    el hoy real parezca muerta de hace semanas. Con N desde now() esta
    hoja SI saldria: el test la distingue."""
    with _db_inerte("orbit_inerte_wm") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_mx", "campaign", "9501")
        ag = _entidad(conn, "amazon_mx", "ad_group", "9502", parent=camp)
        vieja = _entidad(
            conn,
            "amazon_mx",
            "keyword",
            "9503",
            parent=ag,
            match_type="EXACT",
            keyword_text="trafico viejo",
        )
        ancla = _entidad(
            conn,
            "amazon_mx",
            "keyword",
            "9504",
            parent=ag,
            match_type="EXACT",
            keyword_text="ancla",
        )
        inerte = _entidad(
            conn,
            "amazon_mx",
            "keyword",
            "9505",
            parent=ag,
            match_type="EXACT",
            keyword_text="inerte mx",
        )
        muerta = _entidad(
            conn,
            "amazon_mx",
            "keyword",
            "9506",
            parent=ag,
            match_type="EXACT",
            keyword_text="muerta mx",
        )
        # Cross-review grok-4: borde exacto de N con wm 08-09 (ventana >
        # 07-26): impresion en wm-14 == 07-26 -> INERTE; en wm-13 == 07-27
        # -> viva. Un `>=` en vez de `>` voltearia ambas.
        borde_in = _entidad(
            conn,
            "amazon_mx",
            "keyword",
            "9507",
            parent=ag,
            match_type="EXACT",
            keyword_text="borde dentro",
        )
        borde_out = _entidad(
            conn,
            "amazon_mx",
            "keyword",
            "9508",
            parent=ag,
            match_type="EXACT",
            keyword_text="borde fuera",
        )
        for eid in (camp, ag, vieja, ancla, inerte, muerta, borde_in, borde_out):
            _estado(conn, eid)
        _metrica(
            conn,
            run_id,
            vieja,
            dt.date(2026, 8, 7),
            moneda="MXN",
            cost="5.00",
            ad_revenue="0.00",
            clicks=5,
            orders=0,
            impressions=20,
        )
        _metrica(
            conn,
            run_id,
            ancla,
            dt.date(2026, 8, 9),
            moneda="MXN",
            cost="1.00",
            ad_revenue="0.00",
            clicks=1,
            orders=0,
            impressions=1,
        )
        _metrica(
            conn,
            run_id,
            inerte,
            dt.date(2026, 7, 1),
            moneda="MXN",
            cost="3.00",
            ad_revenue="0.00",
            clicks=2,
            orders=0,
        )
        _metrica(
            conn,
            run_id,
            borde_in,
            dt.date(2026, 7, 26),
            moneda="MXN",
            cost="1.00",
            ad_revenue="0.00",
            clicks=1,
            orders=0,
            impressions=5,
        )
        _metrica(
            conn,
            run_id,
            borde_out,
            dt.date(2026, 7, 27),
            moneda="MXN",
            cost="1.00",
            ad_revenue="0.00",
            clicks=1,
            orders=0,
            impressions=5,
        )

        por = _filas_por_external(conn)
        assert "9503" not in por  # 2 dias desde SU watermark: con trafico
        assert "9504" not in por  # el ancla tambien tiene trafico reciente
        assert "9508" not in por  # wm-13 == 07-27: dentro de 14d
        assert por["9505"][0] == "gasto_sin_ventas"
        assert por["9505"][2] == dt.date(2026, 8, 9)  # watermark POR plataforma
        assert por["9505"][5] == "MXN"
        assert por["9506"][0] == "peso_muerto"
        assert "9507" in por  # wm-14 == 07-26: fuera de 14d, inerte


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_vista_exige_enabled_en_hoja_grupo_y_campana():
    """Solo hojas con status ENABLED propio, del ad group y de la campana:
    una hoja con metricas viejas en campana PAUSED no es inerte (ya la
    cubre CAMPANA ACTIVA 01 con campana_no_enabled)."""
    with _db_inerte("orbit_inerte_enabled") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9601")
        ag = _entidad(conn, "amazon_us", "ad_group", "9602", parent=camp)
        kw = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9603",
            parent=ag,
            match_type="EXACT",
            keyword_text="campana pausada",
        )
        _estado(conn, camp, status="PAUSED")
        _estado(conn, ag)
        _estado(conn, kw)
        _metrica(
            conn,
            run_id,
            kw,
            dt.date(2026, 7, 1),
            cost="9.00",
            ad_revenue="0.00",
            clicks=9,
            orders=0,
        )

        por = _filas_por_external(conn)
        assert por == {}


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_vista_impressions_desconocido_no_es_cero():
    """Revision PR #133 (regla 3): observacion reciente con impressions NULL
    (y clicks > 0) es dato DESCONOCIDO, no cero -> la hoja NO aparece (sigue
    optimizandose). Control: hoja con impressions = 0 explicito SI aparece."""
    with _db_inerte("orbit_inerte_null") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9701")
        ag = _entidad(conn, "amazon_us", "ad_group", "9702", parent=camp)
        nula = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9703",
            parent=ag,
            match_type="EXACT",
            keyword_text="trafico desconocido",
        )
        cero = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9704",
            parent=ag,
            match_type="EXACT",
            keyword_text="cero explicito",
        )
        ancla = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9705",
            parent=ag,
            match_type="EXACT",
            keyword_text="ancla",
        )
        for eid in (camp, ag, nula, cero, ancla):
            _estado(conn, eid)
        # Watermark = 08-16; las tres hojas tienen fila reciente (> 08-02).
        _metrica(
            conn,
            run_id,
            nula,
            dt.date(2026, 8, 15),
            cost="5.00",
            ad_revenue="0.00",
            clicks=5,
            orders=0,
        )
        _metrica(
            conn,
            run_id,
            cero,
            dt.date(2026, 8, 15),
            cost="5.00",
            ad_revenue="0.00",
            clicks=0,
            orders=0,
            impressions=0,
        )
        _metrica(
            conn,
            run_id,
            ancla,
            dt.date(2026, 8, 16),
            cost="1.00",
            ad_revenue="0.00",
            clicks=1,
            orders=0,
            impressions=50,
        )

        por = _filas_por_external(conn)
        assert "9703" not in por  # NULL = desconocido, jamas inerte
        assert "9705" not in por  # con trafico real
        assert por["9704"][0] == "gasto_sin_ventas"  # cero EXPLICITO si es inerte


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_vista_mezcla_de_monedas_anula_gasto_y_moneda():
    """Revision PR #133 (regla 4, fail-loud): una hoja con dos monedas en
    90d expone moneda NULL y gasto_90d NULL (jamas una suma mezclada); la
    clasificacion no depende de la moneda (gasto crudo > 0). La mezcla es
    imposible por inserts normales (trigger metric_moneda_sellada): se
    deshabilita en la DB temporal (somos duenos) y se rehabilita."""
    with _db_inerte("orbit_inerte_moneda") as conn:
        run_id = _run(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9801")
        ag = _entidad(conn, "amazon_us", "ad_group", "9802", parent=camp)
        mezcla = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9803",
            parent=ag,
            match_type="EXACT",
            keyword_text="mezcla",
        )
        for eid in (camp, ag, mezcla):
            _estado(conn, eid)
        conn.execute("ALTER TABLE ads_metric_observation DISABLE TRIGGER metric_moneda_sellada")
        try:
            _metrica(
                conn,
                run_id,
                mezcla,
                dt.date(2026, 7, 1),
                cost="3.00",
                ad_revenue="0.00",
                clicks=2,
                orders=0,
                impressions=0,
            )
            _metrica(
                conn,
                run_id,
                mezcla,
                dt.date(2026, 7, 2),
                moneda="MXN",
                cost="400.00",
                ad_revenue="0.00",
                clicks=2,
                orders=0,
                impressions=0,
            )
        finally:
            conn.execute("ALTER TABLE ads_metric_observation ENABLE TRIGGER metric_moneda_sellada")

        por = _filas_por_external(conn)
        clas, dias, wm, gasto90, ord90, moneda = por["9803"]
        assert clas == "gasto_sin_ventas"  # el crudo suma > 0: clasifica igual
        assert gasto90 is None  # mezcla: jamas un numero mezclado
        assert moneda is None
