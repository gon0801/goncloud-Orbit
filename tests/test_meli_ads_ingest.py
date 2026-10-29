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
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.meli.ads_ingest import (
    _MAX_BIGINT,
    _MAX_INTEGER,
    _SQL_LOCK_MELI,
    MONEDA_PLATAFORMA,
    MeliAdsError,
    _dinero,
    _entero,
    _marca_corridas,
    _nombres_vigentes,
    ingest_meli_ads,
    leer_origen,
    main,
    version_canonica,
)

HOY = dt.datetime.now(dt.UTC).date()
D1 = HOY - dt.timedelta(days=1)
D2 = HOY - dt.timedelta(days=2)
# As-of del origen (`updated_at`). Fechas fijas a proposito: el orden entre
# snapshots no depende del reloj de la maquina que corre el test.
AS_OF_INICIAL = "2026-10-29 08:00:00"
AS_OF_CORRECCION = "2026-10-29 12:00:00"
AS_OF_ATRASADO = "2026-10-29 07:00:00"
AS_OF_INTERMEDIO = "2026-10-29 11:00:00"
AS_OF_MEDIO = "2026-10-29 10:00:00"

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
    as_of: str | None = None,
) -> Path:
    """Crea una SQLite con el esquema real. `as_of` fija `updated_at`."""
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_ORIGEN)
    if con_moneda:
        con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN currency TEXT")
    columnas = "date, campaign_id, campaign_name, clicks, impressions, spend, ad_sales"
    if con_moneda:
        columnas += ", currency"
    if as_of is not None:
        columnas += ", updated_at"
    valores = []
    for fila in filas:
        valor = list(fila)
        if con_moneda:
            valor.append(moneda)
        if as_of is not None:
            valor.append(as_of)
        valores.append(tuple(valor))
    con.executemany(
        f"INSERT INTO meli_ads_campaigns_daily ({columnas})"
        f" VALUES ({', '.join(['?'] * len(columnas.split(',')))})",
        valores,
    )
    con.commit()
    con.close()
    return ruta


def _snapshot_sin_pk(ruta: Path, filas: list[tuple], *, as_of: str) -> Path:
    """SQLite sin PK: el adaptador debe detectar claves canonicas duplicadas."""
    con = sqlite3.connect(ruta)
    con.execute(
        "CREATE TABLE meli_ads_campaigns_daily"
        " (date TEXT, campaign_id TEXT, campaign_name TEXT, clicks INTEGER,"
        " impressions INTEGER, spend REAL, ad_sales REAL, updated_at TEXT)"
    )
    con.executemany(
        "INSERT INTO meli_ads_campaigns_daily VALUES (?,?,?,?,?,?,?,?)",
        [(*fila, as_of) for fila in filas],
    )
    con.commit()
    con.close()
    return ruta


# ---------------------------------------------------------------------------
# (a) Adaptador SQLite y forma canonica
# ---------------------------------------------------------------------------


def test_version_canonica_ignora_nombre_y_cambia_por_cada_campo(tmp_path):
    """La version cubre campana, fecha, moneda y TODAS las metricas."""
    base_args = {
        "campaign_external_id": "C1",
        "metric_date": D1,
        "metric_currency": "MXN",
        "cost": Decimal("12.3400"),
        "ad_revenue": Decimal("0.0000"),
        "impressions": 100,
        "clicks": 10,
        "orders": None,
    }
    base = version_canonica(**base_args)
    # Mismos hechos con otra representacion decimal: misma version.
    assert (
        version_canonica(**{**base_args, "cost": Decimal("12.34"), "ad_revenue": Decimal("0")})
        == base
    )
    # Cada campo cambia la version de forma independiente.
    cambios = (
        {"campaign_external_id": "C2"},
        {"metric_date": D2},
        {"metric_currency": "USD"},
        {"cost": Decimal("12.35")},
        {"ad_revenue": Decimal("0.0001")},
        {"impressions": 101},
        {"clicks": 11},
        {"orders": 1},
    )
    for cambio in cambios:
        assert version_canonica(**{**base_args, **cambio}) != base, cambio
    # Ausente != cero: el NULL no colisiona con el cero real.
    assert version_canonica(**{**base_args, "cost": None}) != version_canonica(
        **{**base_args, "cost": Decimal("0")}
    )
    # El nombre de campana no entra en la version (renombrar no crea hechos).
    filas = [(D1.isoformat(), "C1", "OTRO NOMBRE", 10, 100, 12.34, 0.0)]
    origen = leer_origen(_snapshot(tmp_path / "n.db", filas))
    assert not origen.rechazos
    assert origen.filas[0].source_version == base


def test_dinero_no_redondea_en_silencio_ni_colapsa_versiones():
    with pytest.raises(MeliAdsError, match="4 decimales"):
        _dinero("1.23456")
    with pytest.raises(MeliAdsError, match="NUMERIC"):
        _dinero("10000000000")
    with pytest.raises(MeliAdsError, match="cuantiza a cero"):
        _dinero("0.000001")
    # Ruido binario acotado del REAL de SQLite: se acepta y se cuantiza.
    assert _dinero("1.000001") == Decimal("1.0000")
    assert _dinero("0") == Decimal("0.0000")
    # Dos importes genuinamente distintos no colapsan en una version.
    assert _dinero("1.0001") != _dinero("1.0002")


def test_enteros_respetan_limites_de_columna():
    assert _entero(_MAX_BIGINT, _MAX_BIGINT) == _MAX_BIGINT
    with pytest.raises(MeliAdsError, match="fuera de rango"):
        _entero(_MAX_BIGINT + 1, _MAX_BIGINT)
    assert _entero(_MAX_INTEGER, _MAX_INTEGER) == _MAX_INTEGER
    with pytest.raises(MeliAdsError, match="fuera de rango"):
        _entero(_MAX_INTEGER + 1, _MAX_INTEGER)


def test_leer_filas_usa_revision_del_origen(tmp_path):
    """Si el snapshot trae `revision`, esa es la version (no el hash)."""
    snap = _snapshot(tmp_path / "rev.db", _FILAS)
    con = sqlite3.connect(snap)
    con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
    con.execute("UPDATE meli_ads_campaigns_daily SET revision = 'r1'")
    con.commit()
    con.close()

    origen = leer_origen(snap)
    assert not origen.rechazos
    assert {fila.source_version for fila in origen.filas} == {"rev:r1"}

    con = sqlite3.connect(snap)
    con.execute("UPDATE meli_ads_campaigns_daily SET revision = 'r2'")
    con.commit()
    con.close()
    assert {fila.source_version for fila in leer_origen(snap).filas} == {"rev:r2"}


def test_leer_filas_conserva_cero_real_y_dato_faltante(tmp_path):
    origen = leer_origen(_snapshot(tmp_path / "snap.db", _FILAS))
    assert not origen.rechazos
    assert len(origen.filas) == 4
    por_clave = {(f.campaign_external_id, f.metric_date): f for f in origen.filas}

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
    snap = _snapshot(
        tmp_path / "usd.db", _FILAS, con_moneda=True, moneda="USD", as_of=AS_OF_INICIAL
    )
    origen = leer_origen(snap)
    assert origen.filas == ()
    assert sum(origen.rechazos.values()) == 4
    assert all("moneda fuera de dominio" in motivo for motivo in origen.rechazos)


def test_leer_filas_rechaza_identidad_ausente(tmp_path):
    filas_origen = [(D1.isoformat(), " ", "SIN ID", 1, 2, 3.0, 4.0)]
    origen = leer_origen(_snapshot(tmp_path / "sin-id.db", filas_origen))
    assert origen.filas == ()
    assert sum(origen.rechazos.values()) == 1
    assert any("identidad de campana ausente" in motivo for motivo in origen.rechazos)


def test_leer_filas_rechaza_fecha_futura(tmp_path):
    futura = (HOY + dt.timedelta(days=1)).isoformat()
    origen = leer_origen(
        _snapshot(tmp_path / "futura.db", [(futura, "C1", "ALFA", 1, 2, 3.0, 4.0)])
    )
    assert origen.filas == ()
    assert any("fecha futura" in motivo for motivo in origen.rechazos)


def test_updated_at_invalido_rechaza_el_snapshot(tmp_path):
    snap = _snapshot(tmp_path / "mixto.db", _FILAS, as_of=AS_OF_INICIAL)
    con = sqlite3.connect(snap)
    con.execute(
        "UPDATE meli_ads_campaigns_daily SET updated_at = 'not-a-date'"
        " WHERE campaign_id = 'C1' AND date = ?",
        (D1.isoformat(),),
    )
    con.commit()
    con.close()
    origen = leer_origen(snap)
    assert origen.rechazos["updated_at ausente o ilegible"] == 1
    assert origen.total == len(_FILAS)


def test_as_of_futuro_es_rechazado(tmp_path):
    snap = _snapshot(
        tmp_path / "futuro.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 3.0, 4.0)],
        as_of="2099-01-01 00:00:00",
    )
    origen = leer_origen(snap)
    assert origen.rechazos["as_of futuro fuera de la tolerancia de reloj"] == 1


def test_revision_o_identidad_con_separador_se_rechazan(tmp_path):
    snap = _snapshot(
        tmp_path / "revision.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 3.0, 4.0)],
        as_of=AS_OF_INICIAL,
    )
    con = sqlite3.connect(snap)
    con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
    con.execute("UPDATE meli_ads_campaigns_daily SET revision = 'r|1'")
    con.commit()
    con.close()
    assert any("revision con separador" in motivo for motivo in leer_origen(snap).rechazos)

    separador = _snapshot(
        tmp_path / "separador.db",
        [(D1.isoformat(), "C|1", "ALFA", 1, 2, 3.0, 4.0)],
        as_of=AS_OF_INICIAL,
    )
    assert any("campaign_id con separador" in motivo for motivo in leer_origen(separador).rechazos)


def test_snapshot_sin_tabla_de_origen(tmp_path):
    ruta = tmp_path / "vacia.db"
    sqlite3.connect(ruta).close()
    with pytest.raises(MeliAdsError, match="meli_ads_campaigns_daily"):
        leer_origen(ruta)


def test_nombres_vigentes_usa_el_nombre_mas_reciente(tmp_path):
    filas = leer_origen(
        _snapshot(
            tmp_path / "renombre.db",
            [
                (D2.isoformat(), "C1", "NOMBRE VIEJO", 1, 2, 3.0, 4.0),
                (D1.isoformat(), "C1", "NOMBRE NUEVO", 1, 2, 3.0, 4.0),
            ],
        )
    ).filas
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
    as_of: str | None = None,
) -> None:
    """Edita el origen como lo hace el sync: valor nuevo y `updated_at` nuevo."""
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
    if as_of is not None:
        con.execute(
            "UPDATE meli_ads_campaigns_daily SET updated_at = ? WHERE campaign_id = ? AND date = ?",
            (as_of, campaign_id, metric_date),
        )
    con.commit()
    con.close()


def _espera_lock(conn, timeout: float = 10.0) -> None:
    """Espera a que otra conexion quede bloqueada en el advisory lock de MeLi."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        pendientes = conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
        ).fetchone()[0]
        if pendientes:
            return
        time.sleep(0.02)
    raise AssertionError("la ingesta no quedo esperando el candado de MeLi")


class _RelojFijo:
    """Envuelve una conexion y fija `clock_timestamp()` para forzar colisiones."""

    def __init__(self, inner, instante: dt.datetime):
        self.inner = inner
        self.instante = instante

    def execute(self, query, params=None):
        if query == "SELECT clock_timestamp()":

            class _Resultado:
                def __init__(self, valor):
                    self.valor = valor

                def fetchone(self):
                    return (self.valor,)

            return _Resultado(self.instante)
        if params is None:
            return self.inner.execute(query)
        return self.inner.execute(query, params)

    def __getattr__(self, nombre):
        return getattr(self.inner, nombre)


class _PausaAntesLock:
    """Pausa una ingesta justo antes del advisory lock (para invertir ids)."""

    def __init__(self, inner, entered: threading.Event, release: threading.Event):
        self.inner = inner
        self.entered = entered
        self.release = release

    def execute(self, query, params=None):
        if query == _SQL_LOCK_MELI:
            self.entered.set()
            if not self.release.wait(10):
                raise RuntimeError("timeout esperando release del candado")
        if params is None:
            return self.inner.execute(query)
        return self.inner.execute(query, params)

    def __getattr__(self, nombre):
        return getattr(self.inner, nombre)


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
    snap = _snapshot(tmp_path / "snap.db", _FILAS, as_of=AS_OF_INICIAL)
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
    assert run[4] is not None and run[4].startswith("as_of=")
    assert run[5] is not None


def test_reintento_del_mismo_snapshot_no_duplica(pg, tmp_path):
    snap = _snapshot(tmp_path / "snap.db", _FILAS, as_of=AS_OF_INICIAL)
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
    assert "misma version canonica (recopia o reintento)" in run[3]
    # La clave de dedupe no es la corrida: hay dos ingest_run y una sola fila.
    assert segunda["run_id"] != primera["run_id"]


def test_snapshot_atrasado_no_reemplaza_la_correccion_vigente(pg, tmp_path):
    """Bloqueante Limi: un snapshot viejo ingerido despues no puede pisar la
    correccion vigente, aunque su observed_at (hora de ingesta) sea posterior.
    """
    nuevo = _snapshot(
        tmp_path / "nuevo.db",
        [(D1.isoformat(), "C2", "BETA", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_CORRECCION,
    )
    viejo = _snapshot(
        tmp_path / "viejo.db",
        [(D1.isoformat(), "C2", "BETA", 3, 50, 5.0, 20.0)],
        as_of=AS_OF_ATRASADO,
    )
    ingest_meli_ads(pg, nuevo)
    entidades, observaciones = _conteos(pg)
    assert _ultima(pg, "C2", D1)[0] == Decimal("6.5000")

    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(pg, viejo)

    assert _conteos(pg) == (entidades, observaciones)
    assert _ultima(pg, "C2", D1)[0] == Decimal("6.5000")
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False and run[1] == 0 and run[2] == 1
    assert "snapshot atrasado" in run[3]


def test_snapshot_con_el_mismo_as_of_no_reemplaza(pg, tmp_path):
    """Mismo as-of y contenido distinto es ambiguo: se rechaza (fail-closed)."""
    snap = _snapshot(
        tmp_path / "snap.db",
        [(D1.isoformat(), "C2", "BETA", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_INICIAL,
    )
    ingest_meli_ads(pg, snap)
    _, observaciones = _conteos(pg)

    # Edicion in-place SIN avanzar updated_at: el origen no prueba que sea
    # posterior; no puede volverse vigente.
    _corrige(snap, campaign_id="C2", metric_date=D1.isoformat(), cost=5.0)
    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(pg, snap)

    assert _conteos(pg)[1] == observaciones
    assert _ultima(pg, "C2", D1)[0] == Decimal("6.5000")


def test_recopia_con_as_of_nuevo_y_mismo_contenido_no_agrega(pg, tmp_path):
    """Re-copiar el mismo hecho con un `updated_at` mas nuevo no es correccion:
    la version (hash) manda y el dedupe no agrega observaciones."""
    snap = _snapshot(tmp_path / "snap.db", _FILAS, as_of=AS_OF_INICIAL)
    ingest_meli_ads(pg, snap)
    entidades, observaciones = _conteos(pg)

    con = sqlite3.connect(snap)
    con.execute("UPDATE meli_ads_campaigns_daily SET updated_at = ?", (AS_OF_CORRECCION,))
    con.commit()
    con.close()

    resultado = ingest_meli_ads(pg, snap)
    assert resultado["rows_written"] == 0
    assert resultado["duplicados"] == 4
    assert _conteos(pg) == (entidades, observaciones)


def test_snapshot_atrasado_no_restaura_nombre(pg, tmp_path):
    """Aunque las metricas sean identicas, un snapshot atrasado no puede
    tocar el catalogo: el nombre vigente se queda con el del snapshot nuevo."""
    nuevo = _snapshot(
        tmp_path / "nuevo.db",
        [(D1.isoformat(), "C2", "NOMBRE NUEVO", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_CORRECCION,
    )
    viejo = _snapshot(
        tmp_path / "viejo.db",
        [(D1.isoformat(), "C2", "NOMBRE VIEJO", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_ATRASADO,
    )
    ingest_meli_ads(pg, nuevo)
    _, observaciones = _conteos(pg)
    assert (
        pg.execute(
            "SELECT name FROM ad_entity WHERE platform = 'meli' AND external_id = 'C2'"
        ).fetchone()[0]
        == "NOMBRE NUEVO"
    )

    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(pg, viejo)

    assert (
        pg.execute(
            "SELECT name FROM ad_entity WHERE platform = 'meli' AND external_id = 'C2'"
        ).fetchone()[0]
        == "NOMBRE NUEVO"
    )
    assert _conteos(pg)[1] == observaciones


def test_mismo_as_of_no_restaura_nombre(pg, tmp_path):
    """Con el mismo as-of no se aplica el nombre: una copia editada no puede
    restaurar el nombre viejo aunque las metricas sean identicas."""
    nuevo = _snapshot(
        tmp_path / "nuevo.db",
        [(D1.isoformat(), "C2", "NOMBRE NUEVO", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_INICIAL,
    )
    viejo = _snapshot(
        tmp_path / "viejo.db",
        [(D1.isoformat(), "C2", "NOMBRE VIEJO", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_INICIAL,
    )
    ingest_meli_ads(pg, nuevo)
    _, observaciones = _conteos(pg)

    resultado = ingest_meli_ads(pg, viejo)
    assert resultado["rows_written"] == 0
    assert resultado["duplicados"] == 1
    assert (
        pg.execute(
            "SELECT name FROM ad_entity WHERE platform = 'meli' AND external_id = 'C2'"
        ).fetchone()[0]
        == "NOMBRE NUEVO"
    )
    assert _conteos(pg)[1] == observaciones


def test_concurrencia_observed_at_se_toma_bajo_el_candado(pg, tmp_path):
    """Dos conexiones: la nueva espera el candado y la vieja termina primero.

    En el codigo viejo el observed_at de la nueva se calculaba ANTES de
    esperar, quedaba anterior al de la vieja y v_metric_latest mostraba 5.0.
    Bajo el candado, la nueva calcula su reloj despues y muestra 6.5.
    """
    psycopg = pytest.importorskip("psycopg")
    nuevo = _snapshot(
        tmp_path / "nuevo.db",
        [(D1.isoformat(), "C2", "BETA", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_CORRECCION,
    )
    pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('meli', 'campaign', 'C2', 'BETA') ON CONFLICT DO NOTHING"
    )
    run_id = pg.execute(
        "INSERT INTO ingest_run (source, ok, finished_at)"
        " VALUES ('test_concurrencia', true, now()) RETURNING id"
    ).fetchone()[0]
    version_vieja = version_canonica(
        "C2", D1, "MXN", Decimal("5.0000"), Decimal("20.0000"), 50, 3, None
    )
    epoch_viejo = int(dt.datetime.fromisoformat(AS_OF_ATRASADO).replace(tzinfo=dt.UTC).timestamp())

    resultado: dict[str, object] = {}

    def corre_ingesta() -> None:
        try:
            with psycopg.connect(_test_dsn(), dbname=pg.info.dbname, autocommit=True) as conexion:
                resultado["valor"] = ingest_meli_ads(conexion, nuevo)
        except BaseException as exc:  # noqa: BLE001 - se re-lanza abajo
            resultado["error"] = exc

    # La conexion vieja toma el candado y espera; la nueva queda bloqueada.
    with psycopg.connect(_test_dsn(), dbname=pg.info.dbname, autocommit=False) as vieja:
        vieja.execute("SELECT pg_advisory_xact_lock(%s, %s)", (7071, 1))
        hilo = threading.Thread(target=corre_ingesta)
        hilo.start()
        try:
            _espera_lock(pg)
            # La vieja inserta DESPUES de que la nueva empezo a esperar: su
            # observed_at es posterior al que la nueva habia calculado antes.
            vieja.execute(
                "INSERT INTO ads_metric_observation"
                " (ad_entity_id, metric_date, observed_at, metric_currency, cost,"
                "  ad_revenue, impressions, clicks, orders, source_report_id, ingest_run_id)"
                " SELECT id, %s, clock_timestamp(), 'MXN', 5.0, 20.0, 50, 3, NULL, %s, %s"
                " FROM ad_entity WHERE platform = 'meli' AND external_id = 'C2'",
                (D1, f"meli-ads|C2|{D1.isoformat()}|{version_vieja}|{epoch_viejo}", run_id),
            )
            vieja.commit()
        finally:
            hilo.join(20)
    assert not hilo.is_alive()
    if "error" in resultado:
        raise resultado["error"]

    assert _ultima(pg, "C2", D1)[0] == Decimal("6.5000")
    observaciones = pg.execute(
        "SELECT o.observed_at FROM ads_metric_observation o"
        " JOIN ad_entity e ON e.id = o.ad_entity_id"
        " WHERE e.external_id = 'C2' AND o.metric_date = %s ORDER BY o.observed_at",
        (D1,),
    ).fetchall()
    assert len(observaciones) == 2
    assert observaciones[0][0] < observaciones[1][0]


def test_recopia_deduplicada_avanza_la_marca_y_bloquea_el_snapshot_anterior(pg, tmp_path):
    """Secuencia de tres snapshots: la recopia sin métricas nuevas avanza el
    as-of aceptado; un snapshot posterior con as-of intermedio no puede
    restaurar el nombre ni cambiar las métricas."""
    original = _snapshot(
        tmp_path / "original.db",
        [(D1.isoformat(), "C2", "NOMBRE NUEVO", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_INICIAL,
    )
    recopia = _snapshot(
        tmp_path / "recopia.db",
        [(D1.isoformat(), "C2", "NOMBRE NUEVO", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_CORRECCION,
    )
    atrasado_nombre = _snapshot(
        tmp_path / "atrasado-nombre.db",
        [(D1.isoformat(), "C2", "NOMBRE VIEJO", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_INTERMEDIO,
    )
    atrasado_metricas = _snapshot(
        tmp_path / "atrasado-metricas.db",
        [(D1.isoformat(), "C2", "NOMBRE VIEJO", 3, 50, 5.0, 20.0)],
        as_of=AS_OF_INTERMEDIO,
    )

    ingest_meli_ads(pg, original)
    _, observaciones = _conteos(pg)

    # La recopia no agrega observaciones, pero su as-of 12:00 queda aceptado.
    resultado = ingest_meli_ads(pg, recopia)
    assert resultado["rows_written"] == 0
    marca = pg.execute(
        "SELECT skip_reason FROM ingest_run WHERE id = %s", (resultado["run_id"],)
    ).fetchone()[0]
    epoch_12 = int(dt.datetime.fromisoformat(AS_OF_CORRECCION).replace(tzinfo=dt.UTC).timestamp())
    assert marca.startswith(f"as_of={epoch_12}")

    # 11:00 es anterior a la marca 12:00: no restaura el nombre.
    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(pg, atrasado_nombre)
    assert (
        pg.execute(
            "SELECT name FROM ad_entity WHERE platform = 'meli' AND external_id = 'C2'"
        ).fetchone()[0]
        == "NOMBRE NUEVO"
    )
    assert _conteos(pg)[1] == observaciones

    # El mismo hueco con contenido distinto tampoco cambia las métricas.
    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(pg, atrasado_metricas)
    assert _ultima(pg, "C2", D1)[0] == Decimal("6.5000")
    assert _conteos(pg)[1] == observaciones


def test_correccion_agrega_observacion_y_v_metric_latest_la_expone(pg, tmp_path):
    snap = _snapshot(tmp_path / "snap.db", _FILAS, as_of=AS_OF_INICIAL)
    ingest_meli_ads(pg, snap)
    _, antes = _conteos(pg)

    _corrige(
        snap,
        campaign_id="C2",
        metric_date=D1.isoformat(),
        cost=6.5,
        as_of=AS_OF_CORRECCION,
    )
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
    snap = _snapshot(tmp_path / "snap.db", _FILAS, as_of=AS_OF_INICIAL)
    ingest_meli_ads(pg, snap)
    _, observaciones = _conteos(pg)

    # Renombre a mitad de la ventana: el nombre viejo sigue en la fecha vieja y
    # el catalogo debe quedarse con el de la fecha mas reciente.
    _corrige(
        snap,
        campaign_id="C1",
        metric_date=D1.isoformat(),
        campaign_name="ALFA RENOMBRADA",
        as_of=AS_OF_CORRECCION,
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
    snap = _snapshot(
        tmp_path / "usd.db", _FILAS, con_moneda=True, moneda="USD", as_of=AS_OF_INICIAL
    )
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
    snap = _snapshot(tmp_path / "sin-id.db", filas, as_of=AS_OF_INICIAL)

    with pytest.raises(MeliAdsError, match="identidad de campana ausente"):
        ingest_meli_ads(pg, snap)

    # Ni la entidad de la fila valida ni su observacion sobreviven.
    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False and run[1] == 0 and run[2] == 2
    assert "identidad de campana ausente" in run[3]


def test_updated_at_invalido_sella_run_fallida(pg, tmp_path):
    snap = _snapshot(tmp_path / "mixto.db", _FILAS, as_of=AS_OF_INICIAL)
    con = sqlite3.connect(snap)
    con.execute(
        "UPDATE meli_ads_campaigns_daily SET updated_at = 'not-a-date'"
        " WHERE campaign_id = 'C1' AND date = ?",
        (D1.isoformat(),),
    )
    con.commit()
    con.close()

    with pytest.raises(MeliAdsError, match="updated_at"):
        ingest_meli_ads(pg, snap)
    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run == (False, 0, len(_FILAS))


def test_as_of_futuro_no_envenena_la_marca(pg, tmp_path):
    futuro = _snapshot(
        tmp_path / "futuro.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)],
        as_of="2099-01-01 00:00:00",
    )
    with pytest.raises(MeliAdsError, match="as_of futuro"):
        ingest_meli_ads(pg, futuro)
    assert _marca_corridas(pg) == -1
    assert _conteos(pg) == (0, 0)

    normal = _snapshot(
        tmp_path / "normal.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)],
        as_of=AS_OF_INICIAL,
    )
    ingest_meli_ads(pg, normal)
    assert _ultima(pg, "C1", D1)[0] == Decimal("6.0000")


def test_claves_canonicas_duplicadas_rechazan_snapshot(pg, tmp_path):
    snap = _snapshot_sin_pk(
        tmp_path / "sin-pk.db",
        [
            (D1.isoformat(), "C1", "ALFA", 1, 2, 1.0, 4.0),
            (D1.isoformat(), "C1", "ALFA", 1, 2, 2.0, 4.0),
        ],
        as_of=AS_OF_INICIAL,
    )
    with pytest.raises(MeliAdsError, match="clave canonica duplicada"):
        ingest_meli_ads(pg, snap)
    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run == (False, 0, 2)


def test_error_de_lectura_sella_run_ok_false(pg, tmp_path):
    vacia = tmp_path / "vacia.db"
    sqlite3.connect(vacia).close()
    with pytest.raises(MeliAdsError, match="no trae la tabla"):
        ingest_meli_ads(pg, vacia)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False and run[1] == 0 and run[2] == 0
    assert "no trae la tabla" in run[3]


def test_revision_con_separador_sella_run_fallida(pg, tmp_path):
    snap = _snapshot(
        tmp_path / "revision.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 3.0, 4.0)],
        as_of=AS_OF_INICIAL,
    )
    con = sqlite3.connect(snap)
    con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
    con.execute("UPDATE meli_ads_campaigns_daily SET revision = 'r|1'")
    con.commit()
    con.close()

    with pytest.raises(MeliAdsError, match="revision con separador"):
        ingest_meli_ads(pg, snap)
    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run == (False, 0, 1)


def test_marca_es_el_maximo_entre_corridas_y_no_el_id(pg, tmp_path):
    """Dos conexiones invierten los ids: la recopia de 12:00 abre primero y
    termina última; la marca debe ser 12:00, no la de id más alto (10:00)."""
    psycopg = pytest.importorskip("psycopg")
    base = _snapshot(tmp_path / "base.db", _FILAS, as_of=AS_OF_INICIAL)
    recopy = _snapshot(
        tmp_path / "recopy.db",
        [(D1.isoformat(), "C1", "ALFA", 10, 100, 12.34, 0.0)],
        as_of=AS_OF_CORRECCION,
    )
    middle = _snapshot(
        tmp_path / "middle.db",
        [(D1.isoformat(), "C2", "BETA", 3, 50, 6.5, 20.0)],
        as_of=AS_OF_MEDIO,
    )
    stale = _snapshot(
        tmp_path / "stale.db",
        [(D1.isoformat(), "C1", "VIEJO", 10, 100, 99.0, 0.0)],
        as_of=AS_OF_INTERMEDIO,
    )
    ingest_meli_ads(pg, base)

    entered = threading.Event()
    release = threading.Event()
    errores: list[BaseException] = []

    def recopia_baja_id():
        try:
            with psycopg.connect(_test_dsn(), dbname=pg.info.dbname, autocommit=True) as otra:
                otra.execute("SET TIME ZONE 'UTC'")
                ingest_meli_ads(_PausaAntesLock(otra, entered, release), recopy)
        except BaseException as exc:  # noqa: BLE001 - se re-lanza abajo
            errores.append(exc)

    hilo = threading.Thread(target=recopia_baja_id)
    hilo.start()
    try:
        if not entered.wait(3):
            release.set()
            hilo.join(5)
            raise AssertionError(f"la recopia no llego al candado: {errores}")
        # Id mayor y termina primero, con as-of 10:00.
        resultado_medio = ingest_meli_ads(pg, middle)
    finally:
        release.set()
        hilo.join(20)
    assert not hilo.is_alive() and not errores, errores

    # La recopia (id menor) terminó después con as-of 12:00.
    runs = pg.execute("SELECT id, skip_reason FROM ingest_run ORDER BY id").fetchall()
    assert len(runs) == 3
    assert resultado_medio["run_id"] > runs[1][0]
    epoch_12 = int(dt.datetime.fromisoformat(AS_OF_CORRECCION).replace(tzinfo=dt.UTC).timestamp())
    assert runs[1][1].startswith(f"as_of={epoch_12}")

    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(pg, stale)
    assert _ultima(pg, "C1", D1)[0] == Decimal("12.3400")
    assert _ultima(pg, "C2", D1)[0] == Decimal("6.5000")
    assert _marca_corridas(pg) == epoch_12


def test_colision_de_pk_aborta_y_revierte_escrituras(pg, tmp_path):
    """La primera fila escribe, la segunda choca contra la PK: rollback total."""
    psycopg = pytest.importorskip("psycopg")
    fijo = dt.datetime(2026, 10, 29, 13, 0, tzinfo=dt.UTC)
    pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('meli', 'campaign', 'C2', 'BETA')"
    )
    run0 = pg.execute(
        "INSERT INTO ingest_run (source, ok, finished_at) VALUES ('seed', true, now()) RETURNING id"
    ).fetchone()[0]
    version_5 = version_canonica(
        "C2", D1, "MXN", Decimal("5.0000"), Decimal("20.0000"), 50, 3, None
    )
    epoch_7 = int(dt.datetime.fromisoformat(AS_OF_ATRASADO).replace(tzinfo=dt.UTC).timestamp())
    pg.execute(
        "INSERT INTO ads_metric_observation"
        " (ad_entity_id, metric_date, observed_at, metric_currency, cost, ad_revenue,"
        "  impressions, clicks, orders, source_report_id, ingest_run_id)"
        " SELECT id, %s, %s, 'MXN', 5.0, 20.0, 50, 3, NULL, %s, %s"
        " FROM ad_entity WHERE platform = 'meli' AND external_id = 'C2'",
        (D1, fijo, f"meli-ads|C2|{D1.isoformat()}|{version_5}|{epoch_7}", run0),
    )
    snap = _snapshot(
        tmp_path / "colision.db",
        [
            (D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0),
            (D1.isoformat(), "C2", "BETA", 3, 50, 6.5, 20.0),
        ],
        as_of=AS_OF_CORRECCION,
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        ingest_meli_ads(_RelojFijo(pg, fijo), snap)

    assert _conteos(pg) == (1, 1)
    assert pg.execute("SELECT count(*) FROM ad_entity WHERE external_id = 'C1'").fetchone()[0] == 0
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run == (False, 0, 0)
