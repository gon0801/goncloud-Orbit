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
    _SQL_LOCK_MELI,
    _SQL_MAX_OBSERVADO,
    SOURCE,
    _marcas_corridas,
    _nombres_vigentes,
    _texto_persistible,
    ingest_meli_ads,
    main,
)
from app.meli.ads_source import (
    _MAX_BIGINT,
    _MAX_INTEGER,
    MONEDA_PLATAFORMA,
    MeliAdsError,
    _dinero,
    _entero,
    leer_origen,
    version_canonica,
)

HOY = dt.datetime.now(dt.UTC).date()
D1 = HOY - dt.timedelta(days=1)
D2 = HOY - dt.timedelta(days=2)
# As-of del origen (`updated_at`) RELATIVO al reloj del test. Fechas fijas
# hacian que el mismo fixture cambiara de rama (real/futuro) segun la hora.
AHORA = dt.datetime.now(dt.UTC).replace(microsecond=0)
AS_OF_ATRASADO = (AHORA - dt.timedelta(hours=4)).isoformat()
AS_OF_INICIAL = (AHORA - dt.timedelta(hours=3)).isoformat()
AS_OF_MEDIO = (AHORA - dt.timedelta(hours=2)).isoformat()
AS_OF_INTERMEDIO = (AHORA - dt.timedelta(hours=1)).isoformat()
AS_OF_CORRECCION = (AHORA - dt.timedelta(minutes=30)).isoformat()

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


class _ColisionTrasMax:
    """Inserta una fila en el observed_at que la ingesta va a elegir.

    Simula un escritor concurrente que no toma el advisory lock: el insert de
    la ingesta choca contra la PK y debe abortar la corrida.
    """

    def __init__(self, inner, entidad_id: int, fecha, run_id: int, report_id: str):
        self.inner = inner
        self.entidad_id = entidad_id
        self.fecha = fecha
        self.run_id = run_id
        self.report_id = report_id

    def execute(self, query, params=None):
        if query == _SQL_MAX_OBSERVADO:
            resultado = self.inner.execute(query)
            maximo = resultado.fetchone()[0]
            self.inner.execute(
                "INSERT INTO ads_metric_observation"
                " (ad_entity_id, metric_date, observed_at, metric_currency, cost,"
                "  ad_revenue, impressions, clicks, orders, source_report_id, ingest_run_id)"
                " VALUES (%s, %s, %s, 'MXN', 5.5, 20.0, 50, 3, NULL, %s, %s)",
                (
                    self.entidad_id,
                    self.fecha,
                    maximo + dt.timedelta(microseconds=1),
                    self.report_id,
                    self.run_id,
                ),
            )

            class _Resultado:
                def __init__(self, valor):
                    self.valor = valor

                def fetchone(self):
                    return (self.valor,)

            return _Resultado(maximo)
        if params is None:
            return self.inner.execute(query)
        return self.inner.execute(query, params)

    def __getattr__(self, nombre):
        return getattr(self.inner, nombre)


class _DuplicadoId:
    """Inserta una fila con el source_report_id entrante y otro observed_at.

    Fuerza el camino de conflicto real del indice de dedupe, sin colision de
    PK, para probar el contador `duplicados_id`.
    """

    def __init__(self, inner, entidad_id, fecha, run_id, report_id):
        self.inner = inner
        self.entidad_id = entidad_id
        self.fecha = fecha
        self.run_id = run_id
        self.report_id = report_id

    def execute(self, query, params=None):
        if query == _SQL_MAX_OBSERVADO:
            resultado = self.inner.execute(query)
            maximo = resultado.fetchone()[0]
            self.inner.execute(
                "INSERT INTO ads_metric_observation"
                " (ad_entity_id, metric_date, observed_at, metric_currency, cost,"
                "  ad_revenue, impressions, clicks, orders, source_report_id, ingest_run_id)"
                " VALUES (%s, %s, %s, 'MXN', 6.0, 8.0, 2, 1, NULL, %s, %s)",
                (self.entidad_id, self.fecha, maximo, self.report_id, self.run_id),
            )

            class _Resultado:
                def __init__(self, valor):
                    self.valor = valor

                def fetchone(self):
                    return (self.valor,)

            return _Resultado(maximo)
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


def test_futuro_dentro_de_tolerancia_no_envenena_la_marca(pg, tmp_path):
    """Un as-of hasta 24 h adelante se acepta, pero no puede dejar atras una
    correccion real posterior."""
    ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)
    futuro = _snapshot(
        tmp_path / "futuro.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)],
        as_of=(ahora + dt.timedelta(hours=1)).isoformat(),
    )
    actual = _snapshot(
        tmp_path / "actual.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)],
        as_of=ahora.isoformat(),
    )
    ingest_meli_ads(pg, futuro)
    ingest_meli_ads(pg, actual)
    assert _ultima(pg, "C1", D1)[0] == Decimal("6.0000")


def test_reloj_retrocede_no_deja_vigente_la_anterior(pg, tmp_path):
    """Dos correcciones con as-of creciente y observed_at que retrocede: la
    segunda debe quedar vigente."""
    as_of = dt.datetime.now(dt.UTC).replace(microsecond=0) - dt.timedelta(hours=2)
    reloj = dt.datetime.now(dt.UTC).replace(microsecond=0)
    uno = _snapshot(
        tmp_path / "uno.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)],
        as_of=as_of.isoformat(),
    )
    dos = _snapshot(
        tmp_path / "dos.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.5, 8.0)],
        as_of=(as_of + dt.timedelta(hours=1)).isoformat(),
    )
    ingest_meli_ads(_RelojFijo(pg, reloj), uno)
    ingest_meli_ads(_RelojFijo(pg, reloj - dt.timedelta(seconds=1)), dos)
    assert _ultima(pg, "C1", D1)[0] == Decimal("6.5000")


def test_updated_at_con_fraccion_o_anio_desbordado_se_rechaza(pg, tmp_path):
    for valor in (
        "2026-10-29 08:00:00.500",
        "2026-10-29 08:00:00.0000001",
        "2026-10-29 08:00:00.0000002",
        "2026-10-29 08:00:00+999999999999",
        "9999-12-31 23:59:59",
        # Offsets validos de ISO-8601 que desbordan datetime al normalizar a
        # UTC: antes de 28eea61b el OverflowError escapaba de leer_origen.
        "0001-01-01T00:00:00+00:01",
        "9999-12-31T23:59:59-00:01",
    ):
        snap = _snapshot(
            tmp_path / f"{abs(hash(valor))}.db",
            [(D1.isoformat(), "C1", "ALFA", 1, 2, 3.0, 4.0)],
            as_of=AS_OF_INICIAL,
        )
        con = sqlite3.connect(snap)
        con.execute("UPDATE meli_ads_campaigns_daily SET updated_at = ?", (valor,))
        con.commit()
        con.close()
        origen = leer_origen(snap)
        assert origen.rechazos["updated_at ausente o ilegible"] == 1
        with pytest.raises(MeliAdsError, match="updated_at"):
            ingest_meli_ads(pg, snap)


def test_entero_con_doble_signo_y_cero_negativo(tmp_path):
    with pytest.raises(MeliAdsError, match="ilegible"):
        _entero("--1", _MAX_INTEGER)
    with pytest.raises(MeliAdsError, match="fuera de rango"):
        _entero("1" * 5000, _MAX_BIGINT)
    assert _dinero("-0.0") == Decimal("0.0000")
    assert _dinero("-0.0") == _dinero("0.0")


def test_entero_desbordado_se_cuenta_como_rechazo(tmp_path):
    snap = _snapshot(
        tmp_path / "entero-largo.db",
        [(D1.isoformat(), "C1", "ALFA", "1" * 5000, 2, 3.0, 4.0)],
        as_of=AS_OF_INICIAL,
    )
    origen = leer_origen(snap)
    assert sum(origen.rechazos.values()) == 1
    assert any("entero" in motivo for motivo in origen.rechazos)


def test_dinero_limite_numeric_exacto_del_revisor():
    with pytest.raises(MeliAdsError):
        _dinero("9999999999.99996")


def test_recopia_real_conserva_su_as_of_frente_a_una_marca_futura(pg, tmp_path):
    """Una recopia real deduplicada avanza el reloj real aunque exista una
    marca futura; el snapshot intermedio se rechaza."""
    real_5 = _snapshot(
        tmp_path / "real5.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)],
        as_of=(AHORA - dt.timedelta(hours=4)).isoformat(),
    )
    futuro_6 = _snapshot(
        tmp_path / "futuro6.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)],
        as_of=(AHORA + dt.timedelta(hours=1)).isoformat(),
    )
    recopia_6 = _snapshot(
        tmp_path / "recopia6.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)],
        as_of=(AHORA - dt.timedelta(minutes=30)).isoformat(),
    )
    intermedio_7 = _snapshot(
        tmp_path / "intermedio7.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 7.0, 8.0)],
        as_of=(AHORA - dt.timedelta(hours=1)).isoformat(),
    )
    ingest_meli_ads(pg, real_5)
    ingest_meli_ads(pg, futuro_6)
    recopia = ingest_meli_ads(pg, recopia_6)
    assert recopia["rows_written"] == 0
    assert recopia["duplicados_version"] == 1
    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(pg, intermedio_7)
    assert _ultima(pg, "C1", D1)[0] == Decimal("6.0000")


def test_futuro_en_cuarentena_no_bloquea_una_correccion_cercana(pg, tmp_path):
    """Cuatro minutos antes del timestamp futuro, una correccion real sigue
    siendo aceptada: la marca futura no se reclasifica como real."""
    futuro = _snapshot(
        tmp_path / "futuro.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)],
        as_of=(AHORA + dt.timedelta(minutes=10)).isoformat(),
    )
    correccion = _snapshot(
        tmp_path / "correccion.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 7.0, 8.0)],
        as_of=(AHORA + dt.timedelta(minutes=6)).isoformat(),
    )
    ingest_meli_ads(_RelojFijo(pg, AHORA), futuro)
    ingest_meli_ads(_RelojFijo(pg, AHORA + dt.timedelta(minutes=6)), correccion)
    assert _ultima(pg, "C1", D1)[0] == Decimal("7.0000")


def test_reintento_futuro_no_se_reclasifica_y_no_bloquea_la_correccion(pg, tmp_path):
    """Caso A: un as-of aceptado como futuro sigue en cuarentena al avanzar el
    reloj. Su reintento exacto es no-op y una correccion real posterior entra."""
    inicio = dt.datetime.now(dt.UTC).replace(microsecond=0)
    as_of_futuro = inicio + dt.timedelta(minutes=10)
    as_of_correccion = inicio + dt.timedelta(minutes=6)

    def snap(nombre: str, costo: float, as_of: dt.datetime) -> Path:
        return _snapshot(
            tmp_path / nombre,
            [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)],
            as_of=as_of.isoformat(),
        )

    futuro = snap("futuro.db", 5.0, as_of_futuro)
    correccion = snap("correccion.db", 6.0, as_of_correccion)
    ingest_meli_ads(_RelojFijo(pg, inicio), futuro)

    reintento = ingest_meli_ads(_RelojFijo(pg, inicio + dt.timedelta(minutes=11)), futuro)
    assert reintento["rows_written"] == 0
    assert reintento["duplicados"] == 1
    marca = pg.execute(
        "SELECT skip_reason FROM ingest_run WHERE id = %s", (reintento["run_id"],)
    ).fetchone()[0]
    assert marca.startswith("reintento=")
    assert "as_of=" not in marca and "futuro=" not in marca
    assert _marcas_corridas(pg) == (-1, int(as_of_futuro.timestamp()))

    ingest_meli_ads(_RelojFijo(pg, inicio + dt.timedelta(minutes=11)), correccion)
    assert _ultima(pg, "C1", D1)[0] == Decimal("6.0000")
    assert _marcas_corridas(pg) == (
        int(as_of_correccion.timestamp()),
        int(as_of_futuro.timestamp()),
    )


def test_reintento_futuro_tras_correccion_real_es_noop(pg, tmp_path):
    """Caso B: el reintento del futuro original no compara contra la
    observacion vigente (ya corregida); reconoce la aceptacion anterior."""
    ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(nombre: str, costo: float, as_of: dt.datetime) -> Path:
        return _snapshot(
            tmp_path / nombre,
            [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)],
            as_of=as_of.isoformat(),
        )

    futuro = snap("futuro.db", 5.0, ahora + dt.timedelta(hours=1))
    real = snap("real.db", 6.0, ahora)
    ingest_meli_ads(_RelojFijo(pg, ahora), futuro)
    ingest_meli_ads(_RelojFijo(pg, ahora), real)
    _, observaciones = _conteos(pg)

    reintento = ingest_meli_ads(_RelojFijo(pg, ahora), futuro)
    assert reintento["rows_written"] == 0
    assert reintento["duplicados"] == 1
    assert _conteos(pg)[1] == observaciones
    assert _ultima(pg, "C1", D1)[0] == Decimal("6.0000")
    marca = pg.execute(
        "SELECT skip_reason FROM ingest_run WHERE id = %s", (reintento["run_id"],)
    ).fetchone()[0]
    assert marca.startswith("reintento=")
    assert _marcas_corridas(pg)[1] == int((ahora + dt.timedelta(hours=1)).timestamp())


def test_mismo_as_of_con_contenido_distinto_tras_futuro_se_rechaza(pg, tmp_path):
    """Mismo as-of que una aceptacion futura y otra huella: no es reintento."""
    inicio = dt.datetime.now(dt.UTC).replace(microsecond=0)
    as_of = inicio + dt.timedelta(minutes=10)

    def snap(nombre: str, costo: float) -> Path:
        return _snapshot(
            tmp_path / nombre,
            [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)],
            as_of=as_of.isoformat(),
        )

    ingest_meli_ads(_RelojFijo(pg, inicio), snap("futuro.db", 5.0))
    with pytest.raises(MeliAdsError, match="mismo as_of"):
        ingest_meli_ads(_RelojFijo(pg, inicio), snap("editado.db", 7.0))
    assert _ultima(pg, "C1", D1)[0] == Decimal("5.0000")
    assert _conteos(pg)[1] == 1


def test_snapshot_futuro_atrasado_nunca_aceptado_se_rechaza(pg, tmp_path):
    """Dentro del reloj futuro, un as-of anterior a la marca tampoco entra si
    nunca fue aceptado: no es un reintento, es un snapshot atrasado."""
    inicio = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(nombre: str, costo: float, minutos: int) -> Path:
        return _snapshot(
            tmp_path / nombre,
            [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)],
            as_of=(inicio + dt.timedelta(minutes=minutos)).isoformat(),
        )

    ingest_meli_ads(_RelojFijo(pg, inicio), snap("futuro20.db", 5.0, 20))
    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(_RelojFijo(pg, inicio), snap("futuro10.db", 6.0, 10))
    assert _ultima(pg, "C1", D1)[0] == Decimal("5.0000")
    assert _conteos(pg)[1] == 1


def test_futuro_conserva_el_piso_de_una_observacion_legada(pg, tmp_path):
    """Un futuro no borra el piso real de una base legada: el snapshot mas
    atrasado que ese piso se rechaza aunque no exista marca real."""
    ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)
    legacy_asof = ahora - dt.timedelta(hours=1)
    stale_asof = ahora - dt.timedelta(hours=2)
    future_asof = ahora + dt.timedelta(hours=1)
    run_id = pg.execute(
        "INSERT INTO ingest_run (source, ok, finished_at)"
        " VALUES ('legacy', true, now()) RETURNING id"
    ).fetchone()[0]
    entidad = pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('meli', 'campaign', 'C1', 'LEGACY') RETURNING id"
    ).fetchone()[0]
    version = version_canonica("C1", D1, "MXN", Decimal("10"), Decimal("8"), 2, 1, None)
    pg.execute(
        "INSERT INTO ads_metric_observation"
        " (ad_entity_id, metric_date, observed_at, metric_currency, cost,"
        "  ad_revenue, impressions, clicks, orders, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, 'MXN', 10, 8, 2, 1, NULL, %s, %s)",
        (
            entidad,
            D1,
            ahora,
            f"meli-ads|C1|{D1.isoformat()}|{version}|{int(legacy_asof.timestamp())}",
            run_id,
        ),
    )
    futuro = _snapshot(
        tmp_path / "futuro.db",
        [(D1.isoformat(), "C1", "FUTURE", 1, 2, 20.0, 8.0)],
        as_of=future_asof.isoformat(),
    )
    atrasado = _snapshot(
        tmp_path / "atrasado.db",
        [(D1.isoformat(), "C1", "STALE", 1, 2, 5.0, 8.0)],
        as_of=stale_asof.isoformat(),
    )

    ingest_meli_ads(_RelojFijo(pg, ahora), futuro)
    marca = pg.execute(
        "SELECT ok, skip_reason FROM ingest_run WHERE source = %s ORDER BY id",
        (SOURCE,),
    ).fetchall()
    assert marca[0][0] is True
    assert f"piso={int(legacy_asof.timestamp())}" in marca[0][1]

    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(_RelojFijo(pg, ahora), atrasado)

    assert _ultima(pg, "C1", D1)[0] == Decimal("20.0000")
    assert _conteos(pg)[1] == 2


def test_mismo_as_of_legado_con_contenido_distinto_se_rechaza(pg, tmp_path):
    """Una observacion legada con el mismo as-of y otra version impide que un
    snapshot distinto la pise, aunque no exista una marca real."""
    run_id = pg.execute(
        "INSERT INTO ingest_run (source, ok, finished_at)"
        " VALUES ('legacy', true, now()) RETURNING id"
    ).fetchone()[0]
    entidad = pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('meli', 'campaign', 'C1', 'LEGACY') RETURNING id"
    ).fetchone()[0]
    version = version_canonica("C1", D1, "MXN", Decimal("10"), Decimal("8"), 2, 1, None)
    as_of = dt.datetime.fromisoformat(AS_OF_INICIAL).replace(tzinfo=dt.UTC)
    pg.execute(
        "INSERT INTO ads_metric_observation"
        " (ad_entity_id, metric_date, observed_at, metric_currency, cost,"
        "  ad_revenue, impressions, clicks, orders, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, now(), 'MXN', 10, 8, 2, 1, NULL, %s, %s)",
        (
            entidad,
            D1,
            f"meli-ads|C1|{D1.isoformat()}|{version}|{int(as_of.timestamp())}",
            run_id,
        ),
    )
    snap = _snapshot(
        tmp_path / "mismo-asof-legado.db",
        [(D1.isoformat(), "C1", "OTRO", 1, 2, 99.0, 8.0)],
        as_of=AS_OF_INICIAL,
    )

    with pytest.raises(MeliAdsError, match="mismo as_of"):
        ingest_meli_ads(pg, snap)

    assert _ultima(pg, "C1", D1)[0] == Decimal("10.0000")
    assert _conteos(pg)[1] == 1


def test_duplicado_futuro_sin_aceptacion_no_borra_el_piso_legado(pg, tmp_path):
    """Repetir un snapshot futuro que solo coincide con observaciones legadas
    no debe sellar `futuro=` sin piso: el as-of legado sigue siendo el piso."""
    ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)
    futuro_asof = ahora + dt.timedelta(hours=1)
    run_id = pg.execute(
        "INSERT INTO ingest_run (source, ok, finished_at)"
        " VALUES ('legacy', true, now()) RETURNING id"
    ).fetchone()[0]
    entidad = pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('meli', 'campaign', 'C1', 'LEGACY') RETURNING id"
    ).fetchone()[0]
    version = version_canonica("C1", D1, "MXN", Decimal("10"), Decimal("8"), 2, 1, None)
    pg.execute(
        "INSERT INTO ads_metric_observation"
        " (ad_entity_id, metric_date, observed_at, metric_currency, cost,"
        "  ad_revenue, impressions, clicks, orders, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, 'MXN', 10, 8, 2, 1, NULL, %s, %s)",
        (
            entidad,
            D1,
            ahora,
            f"meli-ads|C1|{D1.isoformat()}|{version}|{int(futuro_asof.timestamp())}",
            run_id,
        ),
    )
    duplicado = _snapshot(
        tmp_path / "duplicado-futuro.db",
        [(D1.isoformat(), "C1", "LEGACY", 1, 2, 10.0, 8.0)],
        as_of=futuro_asof.isoformat(),
    )
    atrasado = _snapshot(
        tmp_path / "atrasado-real.db",
        [(D1.isoformat(), "C1", "STALE", 1, 2, 99.0, 8.0)],
        as_of=(ahora - dt.timedelta(hours=2)).isoformat(),
    )

    resultado = ingest_meli_ads(_RelojFijo(pg, ahora), duplicado)
    assert resultado["rows_written"] == 0
    assert _conteos(pg)[1] == 1
    assert _marcas_corridas(pg) == (-1, -1)
    marca = pg.execute(
        "SELECT skip_reason FROM ingest_run WHERE id = %s", (resultado["run_id"],)
    ).fetchone()[0]
    assert marca.startswith("reintento=")

    with pytest.raises(MeliAdsError, match="snapshot atrasado"):
        ingest_meli_ads(_RelojFijo(pg, ahora), atrasado)
    assert _ultima(pg, "C1", D1)[0] == Decimal("10.0000")
    assert _conteos(pg)[1] == 1


def test_huella_incluye_metricas_aunque_exista_revision(pg, tmp_path):
    """La revision no reemplaza las metricas en la huella: mismo as_of con
    costo distinto es contenido distinto, no un reintento."""
    ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(nombre: str, costo: float) -> Path:
        ruta = _snapshot(
            tmp_path / nombre,
            [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)],
            as_of=ahora.isoformat(),
        )
        con = sqlite3.connect(ruta)
        con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
        con.execute("UPDATE meli_ads_campaigns_daily SET revision = 'r1'")
        con.commit()
        con.close()
        return ruta

    ingest_meli_ads(_RelojFijo(pg, ahora), snap("revision-5.db", 5.0))
    with pytest.raises(MeliAdsError, match="mismo as_of"):
        ingest_meli_ads(_RelojFijo(pg, ahora), snap("revision-9.db", 9.0))
    assert _ultima(pg, "C1", D1)[0] == Decimal("5.0000")
    assert _conteos(pg)[1] == 1


def test_huella_no_colisiona_con_separadores_de_campo(pg, tmp_path):
    """La huella JSON escapa separadores: dos snapshots estructuralmente
    distintos no pueden producir los mismos bytes."""
    ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(nombre: str, campaign: str, revision: str) -> Path:
        ruta = _snapshot(
            tmp_path / nombre,
            [(D1.isoformat(), campaign, "ALFA", 1, 2, 5.0, 8.0)],
            as_of=ahora.isoformat(),
        )
        con = sqlite3.connect(ruta)
        con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
        con.execute("UPDATE meli_ads_campaigns_daily SET revision = ?", (revision,))
        con.commit()
        con.close()
        return ruta

    ingest_meli_ads(_RelojFijo(pg, ahora), snap("delimiter-1.db", "A", "X\x1frev:Y"))
    with pytest.raises(MeliAdsError, match="mismo as_of"):
        ingest_meli_ads(_RelojFijo(pg, ahora), snap("delimiter-2.db", "A\x1frev:X", "Y"))
    entidades = pg.execute(
        "SELECT external_id FROM ad_entity WHERE platform = 'meli' ORDER BY external_id"
    ).fetchall()
    assert entidades == [("A",)]
    assert _conteos(pg)[1] == 1


def test_snapshot_vacio_es_noop_exitoso_sin_aceptacion(pg, tmp_path):
    """Un snapshot vacio es no-op exitoso; no inventa as-of ni aceptacion."""
    snap = _snapshot(tmp_path / "vacio.db", [])
    resultado = ingest_meli_ads(pg, snap)
    assert resultado["rows_written"] == 0
    assert resultado["rows_skipped"] == 0
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run == (True, 0, 0, "snapshot vacio (no-op)")
    assert _marcas_corridas(pg) == (-1, -1)
    assert _conteos(pg) == (0, 0)


def test_reintento_de_aceptacion_legada_sin_huella_es_noop(pg, tmp_path):
    """Compatibilidad: una aceptacion anterior sin snap= reconoce el primer
    reintento exacto contra toda la observacion vigente."""
    snap = _snapshot(tmp_path / "legacy.db", _FILAS, as_of=AS_OF_INICIAL)
    primera = ingest_meli_ads(pg, snap)
    pg.execute(
        "UPDATE ingest_run SET skip_reason = regexp_replace("
        " skip_reason, '; snap=[0-9a-f]{64}', '') WHERE id = %s",
        (primera["run_id"],),
    )
    _, observaciones = _conteos(pg)

    segunda = ingest_meli_ads(pg, snap)
    assert segunda["rows_written"] == 0
    assert segunda["duplicados"] == len(_FILAS)
    assert _conteos(pg)[1] == observaciones
    marca = pg.execute(
        "SELECT skip_reason FROM ingest_run WHERE id = %s", (segunda["run_id"],)
    ).fetchone()[0]
    assert marca.startswith("reintento=")


def test_reintento_no_agrega_otra_aceptacion(pg, tmp_path):
    """Cada reintento sella su corrida pero no duplica la aceptacion temporal."""
    snap = _snapshot(tmp_path / "retry.db", _FILAS, as_of=AS_OF_INICIAL)
    ingest_meli_ads(pg, snap)
    antes = pg.execute(
        "SELECT count(*) FROM ingest_run WHERE ok = true"
        " AND (skip_reason LIKE 'as_of=%%' OR skip_reason LIKE 'futuro=%%')"
    ).fetchone()[0]
    ingest_meli_ads(pg, snap)
    despues = pg.execute(
        "SELECT count(*) FROM ingest_run WHERE ok = true"
        " AND (skip_reason LIKE 'as_of=%%' OR skip_reason LIKE 'futuro=%%')"
    ).fetchone()[0]
    assert antes == 1 and despues == 1


def test_texto_persistible_escapa_controles_c0_y_c1():
    assert _texto_persistible("antes\x00despues") == "antes\\x00despues"
    assert _texto_persistible("antes\x85despues") == "antes\\x85despues"
    assert _texto_persistible("a\nb") == "a\\x0ab"


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


def test_moneda_con_nul_sella_la_corrida_y_no_deja_datos(pg, tmp_path):
    """PostgreSQL rechaza NUL en texto. El motivo se escapa ANTES de escribir
    el sello: la corrida no queda abierta y no hay escrituras parciales."""
    snap = _snapshot(
        tmp_path / "nul.db",
        _FILAS,
        con_moneda=True,
        moneda="MXN\x00USD",
        as_of=AS_OF_INICIAL,
    )
    with pytest.raises(MeliAdsError, match="moneda fuera de dominio"):
        ingest_meli_ads(pg, snap)

    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason, finished_at"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False and run[1] == 0 and run[2] == len(_FILAS)
    assert run[4] is not None
    assert "\\x00" in run[3] and "\x00" not in run[3]


def test_campaign_id_con_nul_y_fecha_invalida_sella_la_corrida(pg, tmp_path):
    """El NUL del campaign_id se rechaza en la frontera del origen. El mensaje
    se escapa antes de persistir: no hay datos parciales ni corrida abierta."""
    filas = [
        (D2.isoformat(), "C1", "ALFA", 1, 2, 3.0, 4.0),
        ("no-es-fecha", "C\x00NUL", "MALA", 1, 2, 3.0, 4.0),
    ]
    snap = _snapshot(tmp_path / "nul-id.db", filas, as_of=AS_OF_INICIAL)
    with pytest.raises(MeliAdsError, match="NUL"):
        ingest_meli_ads(pg, snap)

    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason, finished_at"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False and run[1] == 0 and run[2] == len(filas)
    assert run[4] is not None
    assert "campaign_id con NUL" in run[3] and "\x00" not in run[3]


def test_campaign_name_con_nul_sella_la_corrida(pg, tmp_path):
    filas = [(D1.isoformat(), "C1", "ALFA\x00NUL", 1, 2, 5.0, 8.0)]
    snap = _snapshot(tmp_path / "nul-nombre.db", filas, as_of=AS_OF_INICIAL)
    with pytest.raises(MeliAdsError, match="campaign_name con NUL"):
        ingest_meli_ads(pg, snap)

    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason, finished_at"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False and run[1] == 0 and run[2] == 1
    assert run[4] is not None
    assert "campaign_name con NUL" in run[3] and "\x00" not in run[3]


def test_revision_con_nul_sella_la_corrida(pg, tmp_path):
    snap = _snapshot(
        tmp_path / "nul-revision.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 5.0, 8.0)],
        as_of=AS_OF_INICIAL,
    )
    con = sqlite3.connect(snap)
    con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
    con.execute("UPDATE meli_ads_campaigns_daily SET revision = ?", ("r\x00NUL",))
    con.commit()
    con.close()

    with pytest.raises(MeliAdsError, match="revision con NUL"):
        ingest_meli_ads(pg, snap)

    assert _conteos(pg) == (0, 0)
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason, finished_at"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is False and run[1] == 0 and run[2] == 1
    assert run[4] is not None
    assert "revision con NUL" in run[3] and "\x00" not in run[3]


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
    assert _marcas_corridas(pg)[0] == -1
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
    assert _marcas_corridas(pg)[0] == epoch_12


def test_duplicados_id_cuenta_conflicto_real_del_indice(pg, tmp_path):
    """Un conflicto de source_report_id (sin colision de PK) no es una recopia:
    se cuenta aparte como `duplicados_id`."""
    fijo = dt.datetime(2026, 10, 29, 13, 0, tzinfo=dt.UTC)
    as_of_real = fijo - dt.timedelta(minutes=30)
    entidad_c2 = pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('meli', 'campaign', 'C2', 'BETA') RETURNING id"
    ).fetchone()[0]
    entidad_c1 = pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('meli', 'campaign', 'C1', 'ALFA') RETURNING id"
    ).fetchone()[0]
    run0 = pg.execute(
        "INSERT INTO ingest_run (source, ok, finished_at) VALUES ('seed', true, now()) RETURNING id"
    ).fetchone()[0]
    pg.execute(
        "INSERT INTO ads_metric_observation"
        " (ad_entity_id, metric_date, observed_at, metric_currency, cost, ad_revenue,"
        "  impressions, clicks, orders, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, 'MXN', 1.0, 2.0, 10, 1, NULL, %s, %s)",
        (
            entidad_c2,
            D2,
            fijo,
            f"meli-ads|C2|{D2.isoformat()}|seed|{int((fijo - dt.timedelta(hours=4)).timestamp())}",
            run0,
        ),
    )
    snap = _snapshot(
        tmp_path / "duplicado-id.db",
        [(D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0)],
        as_of=as_of_real.isoformat(),
    )
    version_6 = version_canonica("C1", D1, "MXN", Decimal("6.0000"), Decimal("8.0000"), 2, 1, None)
    epoch_12 = int(as_of_real.timestamp())
    duplicado = _DuplicadoId(
        _RelojFijo(pg, fijo),
        entidad_id=entidad_c1,
        fecha=D1,
        run_id=run0,
        report_id=f"meli-ads|C1|{D1.isoformat()}|{version_6}|{epoch_12}",
    )

    resultado = ingest_meli_ads(duplicado, snap)

    assert resultado["rows_written"] == 0
    assert resultado["duplicados_version"] == 0
    assert resultado["duplicados_id"] == 1
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason"
        " FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run[0] is True and run[1] == 0 and run[2] == 1
    assert "source_report_id ya ingerido" in run[3]


def test_cli_main_exitoso(pg, tmp_path, monkeypatch, capsys):
    snap = _snapshot(tmp_path / "cli.db", _FILAS, as_of=AS_OF_INICIAL)
    dsn = _test_dsn().rsplit("/", 1)[0] + "/" + pg.info.dbname
    monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)
    assert main(["--sqlite", str(snap)]) == 0
    salida = capsys.readouterr().out
    assert "ok=True" in salida
    assert "rows_written=4" in salida


def test_colision_de_pk_aborta_y_revierte_escrituras(pg, tmp_path):
    """Un escritor concurrente sin candado choca en la PK. La corrida aborta y
    revierte la fila que ya habia escrito."""
    psycopg = pytest.importorskip("psycopg")
    fijo = dt.datetime(2026, 10, 29, 13, 0, tzinfo=dt.UTC)
    entidad = pg.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('meli', 'campaign', 'C2', 'BETA') RETURNING id"
    ).fetchone()[0]
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
        " VALUES (%s, %s, %s, 'MXN', 5.0, 20.0, 50, 3, NULL, %s, %s)",
        (entidad, D1, fijo, f"meli-ads|C2|{D1.isoformat()}|{version_5}|{epoch_7}", run0),
    )
    snap = _snapshot(
        tmp_path / "colision.db",
        [
            (D1.isoformat(), "C1", "ALFA", 1, 2, 6.0, 8.0),
            (D1.isoformat(), "C2", "BETA", 3, 50, 6.5, 20.0),
        ],
        as_of=AS_OF_CORRECCION,
    )
    colision = _ColisionTrasMax(
        _RelojFijo(pg, fijo),
        entidad_id=entidad,
        fecha=D1,
        run_id=run0,
        report_id=f"meli-ads|C2|{D1.isoformat()}|colision|{epoch_7}",
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        ingest_meli_ads(colision, snap)

    assert _conteos(pg) == (1, 1)
    assert pg.execute("SELECT count(*) FROM ad_entity WHERE external_id = 'C1'").fetchone()[0] == 0
    run = pg.execute(
        "SELECT ok, rows_written, rows_skipped FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run == (False, 0, 0)
