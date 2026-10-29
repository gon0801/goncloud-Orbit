"""Tests de la ingesta de Mercado Libre Ads campana/dia (RESUMEN-ADS-01).

Fuente: `meli_ads_campaigns_daily` del snapshot de la SQLite de accounting,
con el esquema REAL medido en vivo el 2026-10-29 (PK `(date, campaign_id)`,
sin columna de moneda ni de revision). El plan nombraba
`meli_ads_metrics_daily`, que es total por dia y no tiene campana; la puerta
de fuente esta en docs/evidencia/resumen-ads-multicanal/01-ingesta/.

(a) UNITARIOS: adaptador SQLite real en `tmp_path` (esquema del origen).
    Version canonica, dato faltante, cero real, moneda e identidad invalidas,
    snapshot ausente o sin la tabla.
(b) INTEGRACION: `ingest_meli_ads` contra un Postgres temporal con la
    migracion aplicada. Reintento idempotente, correccion append-only visible
    en `v_metric_latest`, renombre de campana sin observacion nueva, moneda
    invalida e identidad invalida con la corrida sellada `ok=false` y cero
    escrituras parciales. Skip automatico sin Postgres utilizable.

Regla 9: cada comportamiento nuevo se afirma contra el valor real en la base,
no contra un contador interno.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.meli.ads_ingest import (
    MONEDA_PLATAFORMA,
    MeliAdsError,
    _nombres_vigentes,
    ingest_meli_ads,
    leer_filas,
    main,
    version_canonica,
)

HOY = dt.datetime.now(dt.UTC).date()
D1 = HOY - dt.timedelta(days=1)
D2 = HOY - dt.timedelta(days=2)

# Esquema REAL de accounting (PRAGMA table_info del 2026-10-29), con el
# PRIMARY KEY del origen. `currency` se agrega solo en los tests que prueban
# el rechazo: la tabla viva no la tiene.
_DDL_ORIGEN = """
CREATE TABLE meli_ads_campaigns_daily (
    date TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    campaign_name TEXT DEFAULT '',
    clicks INTEGER DEFAULT 0,
    impressions INTEGER DEFAULT 0,
    spend REAL DEFAULT 0,
    ad_sales REAL DEFAULT 0,
    acos REAL DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (date, campaign_id)
);
"""

# (date, campaign_id, campaign_name, clicks, impressions, spend, ad_sales)
_FILAS = [
    (D2.isoformat(), "C1", "ALFA", 0, 0, 0.0, 0.0),  # ceros reales
    (D1.isoformat(), "C1", "ALFA", 10, 100, 12.34, 0.0),  # revenue cero real
    (D1.isoformat(), "C2", "BETA", 3, 50, 5.0, 20.0),
    (D2.isoformat(), "C2", "BETA", None, None, None, None),  # faltante -> NULL
]


def _snapshot(
    ruta: Path,
    filas: list[tuple],
    *,
    con_moneda: bool = False,
    moneda: str = "MXN",
) -> Path:
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_ORIGEN)
    if con_moneda:
        con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN currency TEXT")
        filas = [(*fila, moneda) for fila in filas]
    con.executemany(
        "INSERT INTO meli_ads_campaigns_daily"
        " (date, campaign_id, campaign_name, clicks, impressions, spend, ad_sales"
        f"{', currency' if con_moneda else ''})"
        f" VALUES ({', '.join(['?'] * (8 if con_moneda else 7))})",
        filas,
    )
    con.commit()
    con.close()
    return ruta


# ---------------------------------------------------------------------------
# (a) Adaptador SQLite y forma canonica
# ---------------------------------------------------------------------------


def test_version_canonica_ignora_nombre_y_detecta_metricas(tmp_path):
    """La version cubre campana, fecha, moneda y TODAS las metricas."""
    base = version_canonica("C1", D1, "MXN", Decimal("12.3400"), Decimal("0.0000"), 100, 10, None)
    # Mismos hechos con otra representacion decimal: misma version.
    assert version_canonica("C1", D1, "MXN", Decimal("12.34"), Decimal("0"), 100, 10, None) == base
    # Un cambio de metrica es otra version.
    assert version_canonica("C1", D1, "MXN", Decimal("12.35"), Decimal("0"), 100, 10, None) != base
    # Ausente != cero: el NULL no colisiona con el cero real.
    assert version_canonica("C1", D1, "MXN", None, Decimal("0"), 100, 10, None) != version_canonica(
        "C1", D1, "MXN", Decimal("0"), Decimal("0"), 100, 10, None
    )
    # El nombre de campana no entra en la version (renombrar no crea hechos).
    filas = [(D1.isoformat(), "C1", "OTRO NOMBRE", 10, 100, 12.34, 0.0)]
    otra, rechazos = leer_filas(_snapshot(tmp_path / "n.db", filas))
    assert not rechazos
    assert otra[0].source_version == base


def test_leer_filas_usa_revision_del_origen(tmp_path):
    """Si el snapshot trae `revision`, esa es la version (no el hash)."""
    snap = _snapshot(tmp_path / "rev.db", _FILAS)
    con = sqlite3.connect(snap)
    con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
    con.execute("UPDATE meli_ads_campaigns_daily SET revision = 'r1'")
    con.commit()
    con.close()

    filas, rechazos = leer_filas(snap)
    assert not rechazos
    assert {fila.source_version for fila in filas} == {"rev:r1"}

    con = sqlite3.connect(snap)
    con.execute("UPDATE meli_ads_campaigns_daily SET revision = 'r2'")
    con.commit()
    con.close()
    filas, _ = leer_filas(snap)
    assert {fila.source_version for fila in filas} == {"rev:r2"}


def test_leer_filas_conserva_cero_real_y_dato_faltante(tmp_path):
    filas, rechazos = leer_filas(_snapshot(tmp_path / "snap.db", _FILAS))
    assert not rechazos
    assert len(filas) == 4
    por_clave = {(f.campaign_external_id, f.metric_date): f for f in filas}

    cero = por_clave[("C1", D2)]
    assert cero.cost == Decimal("0.0000")
    assert cero.ad_revenue == Decimal("0.0000")
    assert cero.clicks == 0 and cero.impressions == 0
    assert cero.orders is None  # el origen no publica orders

    faltante = por_clave[("C2", D2)]
    assert faltante.cost is None
    assert faltante.ad_revenue is None
    assert faltante.clicks is None and faltante.impressions is None

    assert por_clave[("C1", D1)].metric_currency == MONEDA_PLATAFORMA


def test_leer_filas_rechaza_moneda_fuera_de_dominio(tmp_path):
    snap = _snapshot(tmp_path / "usd.db", _FILAS, con_moneda=True, moneda="USD")
    filas, rechazos = leer_filas(snap)
    assert filas == []
    assert sum(rechazos.values()) == 4
    assert all("moneda fuera de dominio" in motivo for motivo in rechazos)


def test_leer_filas_rechaza_identidad_ausente(tmp_path):
    filas_origen = [(D1.isoformat(), " ", "SIN ID", 1, 2, 3.0, 4.0)]
    filas, rechazos = leer_filas(_snapshot(tmp_path / "sin-id.db", filas_origen))
    assert filas == []
    assert sum(rechazos.values()) == 1
    assert any("identidad de campana ausente" in motivo for motivo in rechazos)


def test_leer_filas_rechaza_fecha_futura(tmp_path):
    futura = (HOY + dt.timedelta(days=1)).isoformat()
    filas, rechazos = leer_filas(
        _snapshot(tmp_path / "futura.db", [(futura, "C1", "ALFA", 1, 2, 3.0, 4.0)])
    )
    assert filas == []
    assert any("fecha futura" in motivo for motivo in rechazos)


def test_snapshot_sin_tabla_de_origen(tmp_path):
    ruta = tmp_path / "vacia.db"
    sqlite3.connect(ruta).close()
    with pytest.raises(MeliAdsError, match="meli_ads_campaigns_daily"):
        leer_filas(ruta)


def test_nombres_vigentes_usa_el_nombre_mas_reciente(tmp_path):
    filas, _ = leer_filas(
        _snapshot(
            tmp_path / "renombre.db",
            [
                (D2.isoformat(), "C1", "NOMBRE VIEJO", 1, 2, 3.0, 4.0),
                (D1.isoformat(), "C1", "NOMBRE NUEVO", 1, 2, 3.0, 4.0),
            ],
        )
    )
    assert _nombres_vigentes(filas) == {"C1": "NOMBRE NUEVO"}


# ---------------------------------------------------------------------------
# (a) main: fail-closed de configuracion (sin Postgres)
# ---------------------------------------------------------------------------


def test_main_sin_dsn_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    snap = _snapshot(tmp_path / "snap.db", _FILAS)
    assert main(["--sqlite", str(snap)]) == 2
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_main_sin_snapshot_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ORBIT_DSN_INGEST", "postgresql://x/y")
    inexistente = tmp_path / "no-existe.db"
    assert main(["--sqlite", str(inexistente)]) == 2
    assert "no-existe.db" in capsys.readouterr().err


def test_main_exige_flag_sqlite():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# (b) Integracion contra la migracion real
# ---------------------------------------------------------------------------

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


@pytest.fixture
def pg():
    if not _DSN_EXPLICITO and not _hay_postgres_local():
        pytest.skip("sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_meli_ads_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # migracion 0001 completa
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _corrige(
    ruta: Path,
    *,
    campaign_id: str,
    metric_date: str,
    cost: float | None = None,
    campaign_name: str | None = None,
) -> None:
    con = sqlite3.connect(ruta)
    if cost is not None:
        con.execute(
            "UPDATE meli_ads_campaigns_daily SET spend = ? WHERE campaign_id = ? AND date = ?",
            (cost, campaign_id, metric_date),
        )
    if campaign_name is not None:
        con.execute(
            "UPDATE meli_ads_campaigns_daily SET campaign_name = ?"
            " WHERE campaign_id = ? AND date = ?",
            (campaign_name, campaign_id, metric_date),
        )
    con.commit()
    con.close()


def _conteos(conn) -> tuple[int, int]:
    return (
        conn.execute("SELECT count(*) FROM ad_entity WHERE platform = 'meli'").fetchone()[0],
        conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0],
    )


def _ultima(conn, campaign_id: str, fecha: dt.date):
    return conn.execute(
        "SELECT l.cost, l.ad_revenue, l.impressions, l.clicks, l.orders, l.metric_currency"
        " FROM v_metric_latest l JOIN ad_entity e ON e.id = l.ad_entity_id"
        " WHERE e.platform = 'meli' AND e.external_id = %s AND l.metric_date = %s",
        (campaign_id, fecha),
    ).fetchone()


def test_ingesta_escribe_entidades_y_observaciones(pg, tmp_path):
    snap = _snapshot(tmp_path / "snap.db", _FILAS)
    resultado = ingest_meli_ads(pg, snap)

    assert resultado["rows_written"] == 4
    assert resultado["duplicados"] == 0
    assert resultado["entidades_nuevas"] == 2

    entidades = pg.execute(
        "SELECT external_id, kind, name FROM ad_entity WHERE platform = 'meli' ORDER BY external_id"
    ).fetchall()
    assert entidades == [("C1", "campaign", "ALFA"), ("C2", "campaign", "BETA")]

    # Ceros reales y NULL conviven: el cero no se convierte en ausencia.
    assert _ultima(pg, "C1", D2) == (
        Decimal("0.0000"),
        Decimal("0.0000"),
        0,
        0,
        None,
        "MXN",
    )
    assert _ultima(pg, "C2", D2) == (None, None, None, None, None, "MXN")
    assert _ultima(pg, "C2", D1) == (
        Decimal("5.0000"),
        Decimal("20.0000"),
        50,
        3,
        None,
        "MXN",
    )

    run = pg.execute(
        "SELECT source, ok, rows_written, rows_skipped, skip_reason, finished_at"
        " FROM ingest_run WHERE id = %s",
        (resultado["run_id"],),
    ).fetchone()
    assert run[0] == "accounting_meli_ads_campaigns"
    assert run[1] is True and run[2] == 4 and run[3] == 0
    assert run[4] is None and run[5] is not None


def test_reintento_del_mismo_snapshot_no_duplica(pg, tmp_path):
    snap = _snapshot(tmp_path / "snap.db", _FILAS)
    primera = ingest_meli_ads(pg, snap)
    entidades, observaciones = _conteos(pg)

    segunda = ingest_meli_ads(pg, snap)
    assert segunda["rows_written"] == 0
    assert segunda["duplicados"] == 4
    assert segunda["rows_skipped"] == 4
    assert _conteos(pg) == (entidades, observaciones)

    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason FROM ingest_run WHERE id = %s",
        (segunda["run_id"],),
    ).fetchone()
    assert run[0] is True and run[1] == 0 and run[2] == 4
    assert "snapshot ya ingerido (dedupe source_report_id)" in run[3]
    # La clave de dedupe no es la corrida: hay dos ingest_run y una sola fila.
    assert segunda["run_id"] != primera["run_id"]


def test_correccion_agrega_observacion_y_v_metric_latest_la_expone(pg, tmp_path):
    snap = _snapshot(tmp_path / "snap.db", _FILAS)
    ingest_meli_ads(pg, snap)
    _, antes = _conteos(pg)

    _corrige(snap, campaign_id="C2", metric_date=D1.isoformat(), cost=6.5)
    resultado = ingest_meli_ads(pg, snap)

    assert resultado["rows_written"] == 1
    assert resultado["duplicados"] == 3
    assert _conteos(pg)[1] == antes + 1
    assert _ultima(pg, "C2", D1)[0] == Decimal("6.5000")

    historia = pg.execute(
        "SELECT o.cost FROM ads_metric_observation o"
        " JOIN ad_entity e ON e.id = o.ad_entity_id"
        " WHERE e.platform = 'meli' AND e.external_id = 'C2' AND o.metric_date = %s"
        " ORDER BY o.observed_at",
        (D1,),
    ).fetchall()
    assert [fila[0] for fila in historia] == [Decimal("5.0000"), Decimal("6.5000")]


def test_renombre_actualiza_catalogo_sin_observacion(pg, tmp_path):
    snap = _snapshot(tmp_path / "snap.db", _FILAS)
    ingest_meli_ads(pg, snap)
    _, observaciones = _conteos(pg)

    # Renombre a mitad de la ventana: el nombre viejo sigue en la fecha vieja y
    # el catalogo debe quedarse con el de la fecha mas reciente.
    _corrige(
        snap,
        campaign_id="C1",
        metric_date=D1.isoformat(),
        campaign_name="ALFA RENOMBRADA",
    )
    resultado = ingest_meli_ads(pg, snap)

    nombre = pg.execute(
        "SELECT name FROM ad_entity WHERE platform = 'meli' AND external_id = 'C1'"
    ).fetchone()[0]
    assert nombre == "ALFA RENOMBRADA"
    assert resultado["rows_written"] == 0
    assert resultado["entidades_actualizadas"] == 1
    assert _conteos(pg)[1] == observaciones


def test_moneda_invalida_aborta_y_sella_ok_false(pg, tmp_path):
    snap = _snapshot(tmp_path / "usd.db", _FILAS, con_moneda=True, moneda="USD")
    with pytest.raises(MeliAdsError, match="moneda fuera de dominio"):
        ingest_meli_ads(pg, snap)

    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason, finished_at"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False
    assert run[1] == 0 and run[2] == 4
    assert "moneda fuera de dominio" in run[3]
    assert run[4] is not None


def test_identidad_invalida_no_deja_escrituras_parciales(pg, tmp_path):
    filas = [
        (D2.isoformat(), "C1", "ALFA", 1, 2, 3.0, 4.0),  # valida y anterior
        (D1.isoformat(), "", "SIN ID", 1, 2, 3.0, 4.0),  # invalida y posterior
    ]
    snap = _snapshot(tmp_path / "sin-id.db", filas)

    with pytest.raises(MeliAdsError, match="identidad de campana ausente"):
        ingest_meli_ads(pg, snap)

    # Ni la entidad de la fila valida ni su observacion sobreviven.
    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False and run[1] == 0 and run[2] == 1
    assert "identidad de campana ausente" in run[3]
