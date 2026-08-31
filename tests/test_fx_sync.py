"""Tests de la ingesta de tipos de cambio desde contabilidad (ORBIT 06 0.5).

Evidencia TDD (regla 9) — rojo contra codigo anterior (2026-08-31):
  .venv/Scripts/python.exe -m pytest tests/test_fx_sync.py -q --tb=line
  -> ModuleNotFoundError: No module named 'app.fx'
  (exit 2, collection error; 1 error in ~13s)

Fuente: snapshot de `currency_rates` (rate_date, base_currency,
quote_currency, rate). Decisiones: plans/orbit-06.md §Decisiones de la 0.5
(D1 mapeo invertido, D2 --sqlite, D3 ON CONFLICT DO NOTHING).

(a) UNITARIOS: mapear_destino / plan_tasas / leer_origen / main fail-closed.
(b) INTEGRACION: sync_fx + fx_resolve (exact / nearest_prior / cero filas)
    y doble corrida no-op. Skip sin Postgres utilizable.
"""

from __future__ import annotations

import os
import socket
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.fx import (
    SOURCE,
    FilaOrigenFx,
    TasaFx,
    leer_origen,
    main,
    mapear_destino,
    plan_tasas,
    sync_fx,
)

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))

# ---------------------------------------------------------------------------
# Helpers: snapshot de accounting con el esquema REAL de currency_rates
# ---------------------------------------------------------------------------

_DDL_FX = """
CREATE TABLE currency_rates (
    rate_date TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    rate REAL,
    PRIMARY KEY (rate_date, base_currency, quote_currency)
);
"""


def _snapshot(ruta: Path, filas: list[tuple]) -> Path:
    """filas: (rate_date, base_currency, quote_currency, rate)."""
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_FX)
    con.executemany(
        "INSERT INTO currency_rates (rate_date, base_currency, quote_currency, rate)"
        " VALUES (?, ?, ?, ?)",
        filas,
    )
    con.commit()
    con.close()
    return ruta


def _fila(
    rate_date: str = "2026-08-01",
    base: str = "MXN",
    quote: str = "USD",
    rate: float | None = 16.95,
) -> FilaOrigenFx:
    return FilaOrigenFx(rate_date=rate_date, base=base, quote=quote, rate=rate)


def _tasa_absurda_si_base_mxn(tasa: TasaFx) -> bool:
    """Una tasa ~17 con base MXN diria '1 peso = 17 dolares': imposible."""
    return tasa.base == "MXN" and Decimal("10") < tasa.rate < Decimal("25")


# ---------------------------------------------------------------------------
# (a) mapear_destino: puro — D1 (etiquetas invertidas, numero se conserva)
# ---------------------------------------------------------------------------


def test_tasa_cerca_de_17_no_puede_tener_base_mxn():
    """Trampa semantica (D1): fuente (MXN, USD, ~17) → destino (USD, MXN, 17).

    Nadie paga 17 dolares por un peso. Tras el mapeo la fila NO puede tener
    base=MXN con rate en (10, 25).
    """
    tasa = mapear_destino(_fila(rate=16.95, base="MXN", quote="USD"))
    assert tasa is not None
    assert tasa.base == "USD"
    assert tasa.quote == "MXN"
    assert tasa.rate == Decimal("16.95")
    assert tasa.rate_date == date(2026, 8, 1)
    assert not _tasa_absurda_si_base_mxn(tasa)


def test_mapear_invierte_mxn_usd_a_usd_mxn():
    tasa = mapear_destino(_fila(rate=17.5))
    assert tasa == TasaFx(
        rate_date=date(2026, 8, 1),
        base="USD",
        quote="MXN",
        rate=Decimal("17.5"),
    )


def test_mapear_rechaza_rate_no_positivo():
    assert mapear_destino(_fila(rate=0.0)) is None
    assert mapear_destino(_fila(rate=-1.0)) is None
    assert mapear_destino(_fila(rate=None)) is None


def test_mapear_rechaza_par_desconocido():
    assert mapear_destino(_fila(base="USD", quote="MXN", rate=17.0)) is None
    assert mapear_destino(_fila(base="EUR", quote="USD", rate=1.1)) is None


def test_plan_tasas_cuenta_skips_y_conserva_validas():
    filas = [
        _fila("2026-08-01", "MXN", "USD", 16.95),
        _fila("2026-08-02", "MXN", "USD", 0.0),
        _fila("2026-08-03", "EUR", "USD", 1.1),
        _fila("2026-08-04", "MXN", "USD", None),
    ]
    tasas, skips = plan_tasas(filas)
    assert len(tasas) == 1
    assert tasas[0].rate_date == date(2026, 8, 1)
    assert tasas[0].base == "USD" and tasas[0].quote == "MXN"
    assert skips["rate no positivo o nulo (dato faltante)"] == 2
    assert skips["par de monedas no soportado"] == 1


def test_leer_origen_lee_currency_rates(tmp_path):
    snap = _snapshot(
        tmp_path / "fx.db",
        [
            ("2026-08-01", "MXN", "USD", 16.95),
            ("2026-08-02", "MXN", "USD", 17.10),
        ],
    )
    filas = leer_origen(snap)
    assert filas == (
        FilaOrigenFx("2026-08-01", "MXN", "USD", 16.95),
        FilaOrigenFx("2026-08-02", "MXN", "USD", 17.10),
    )


def test_main_sin_dsn_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert main(["--sqlite", str(tmp_path / "x.db")]) == 2
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_main_sin_snapshot_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ORBIT_DSN_INGEST", "postgresql://x/y")
    assert main(["--sqlite", str(tmp_path / "no-existe.db")]) == 2
    err = capsys.readouterr().err.lower()
    assert "snapshot" in err or "existe" in err


# ---------------------------------------------------------------------------
# (b) integracion: sync_fx + fx_resolve contra la migracion real
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sync_fx_y_fx_resolve_ciclo_completo(tmp_path):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_fx_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)

        dia = date(2026, 8, 1)
        snap = _snapshot(
            tmp_path / "fx.db",
            [
                (dia.isoformat(), "MXN", "USD", 16.95),
                # par desconocido: skip contado
                (dia.isoformat(), "EUR", "USD", 1.1),
                # rate nulo: skip
                ("2026-08-02", "MXN", "USD", None),
            ],
        )
        r1 = sync_fx(conn, snap)
        assert r1.ok is True
        assert r1.rows_written == 1
        assert r1.rows_skipped == 2
        assert SOURCE == "accounting_currency_rates"
        run = conn.execute(
            "SELECT source, ok, rows_written, rows_skipped, finished_at"
            " FROM ingest_run WHERE id = %s",
            (r1.run_id,),
        ).fetchone()
        assert run[0] == SOURCE
        assert run[1] is True and run[2] == 1 and run[3] == 2 and run[4] is not None

        # exact el dia que existe
        exact = conn.execute(
            "SELECT rate, rate_date, source FROM fx_resolve(%s, 'USD', 'MXN')",
            (dia,),
        ).fetchall()
        assert len(exact) == 1
        assert exact[0][0] == Decimal("16.95")
        assert exact[0][1] == dia
        assert exact[0][2] == "exact"

        # nearest_prior dentro de 7 dias
        prior = conn.execute(
            "SELECT rate, rate_date, source FROM fx_resolve(%s, 'USD', 'MXN')",
            (dia + timedelta(days=3),),
        ).fetchall()
        assert len(prior) == 1
        assert prior[0][1] == dia and prior[0][2] == "nearest_prior"

        # mas de 7 dias sin tasa -> cero filas (sellado: nunca constante)
        lejos = conn.execute(
            "SELECT rate FROM fx_resolve(%s, 'USD', 'MXN')",
            (dia + timedelta(days=10),),
        ).fetchall()
        assert lejos == []

        # etiquetas destino correctas (nunca base MXN con rate ~17)
        fila_pub = conn.execute(
            "SELECT base_currency, quote_currency, rate FROM fx_rate"
        ).fetchone()
        assert fila_pub == ("USD", "MXN", Decimal("16.95"))
        assert not _tasa_absurda_si_base_mxn(
            TasaFx(rate_date=dia, base=fila_pub[0], quote=fila_pub[1], rate=fila_pub[2])
        )

        # doble corrida: ON CONFLICT DO NOTHING → rows_written=0, misma PK
        conteo_antes = conn.execute("SELECT count(*) FROM fx_rate").fetchone()[0]
        r2 = sync_fx(conn, snap)
        assert r2.ok is True
        assert r2.rows_written == 0
        assert conn.execute("SELECT count(*) FROM fx_rate").fetchone()[0] == conteo_antes
        assert (
            conn.execute("SELECT count(*) FROM ingest_run WHERE source = %s", (SOURCE,)).fetchone()[
                0
            ]
            == 2
        )
    finally:
        if conn is not None:
            conn.close()
        admin.execute(pgsql.SQL("DROP DATABASE {} WITH (FORCE)").format(pgsql.Identifier(db)))
        admin.close()
