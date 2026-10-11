"""Comando `avisos-campana` (BIDS 02, V.4). Postgres real.

Lee con ORBIT_DSN_READ, marca lo enviado con ORBIT_DSN_INGEST
(`ingest_run` source `avisos_campana:<clase>`, sellada el mismo dia
UTC). Dos corridas el mismo dia envian una vez; lo fallido sale en la
siguiente corrida.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from test_notifica import _canal
from test_pantalla_dinero import (
    SQL1,
    SQL2,
    SQL60,
    SQL61,
    SQL62,
    _siembra_campana,
    _siembra_config,
    _siembra_gasto_campana,
    _siembra_placement,
)
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import cli_bids

RAIZ = Path(__file__).resolve().parents[1]
SQL36 = (RAIZ / "migrations" / "0036_ingest_run_platform.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def _db_avisos(prefijo: str):
    import os
    import socket

    import psycopg
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL1)
        conn.execute(SQL2)
        conn.execute(SQL36)
        conn.execute(SQL60)
        conn.execute(SQL61)
        conn.execute(SQL62)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _hoy():
    return dt.datetime.now(dt.UTC).date()


def _siembra_aviso(conn):
    """Campana MX que hoy merece `campana_sin_presupuesto` (5 dias al tope)."""
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    hoy = _hoy()
    hasta = hoy - dt.timedelta(days=1)
    obs = dt.datetime.combine(hoy, dt.time(), tzinfo=dt.UTC)
    c1 = _siembra_campana(conn, "amazon_mx", "c1", "Tope")
    _siembra_config(conn, c1, obs - dt.timedelta(days=10), Decimal("100"), "MANUAL")
    for i in range(7):
        dia = hasta - dt.timedelta(days=6 - i)
        gasto = Decimal("90") if i < 5 else Decimal("0")
        _siembra_gasto_campana(conn, c1, dia, gasto, obs, run)
    return c1


def _dsns(monkeypatch, conn):
    partes = urlsplit(_test_dsn())
    dsn = urlunsplit((partes.scheme, partes.netloc, f"/{conn.info.dbname}", "", ""))
    monkeypatch.setenv("ORBIT_DSN_READ", dsn)
    monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)


@_skip_db
def test_comando_envia_y_marca(monkeypatch, tmp_path):
    with _db_avisos("orbit_v4_cmd") as conn:
        _siembra_aviso(conn)
        _dsns(monkeypatch, conn)
        with _canal(tmp_path, monkeypatch) as mensajes:
            assert cli_bids.main_avisos_campana([]) == 0
            assert [m["text"] for m in mensajes] == [
                "Tope: se queda sin presupuesto. Usó 90 % o más en 5 de los últimos 7 días."
            ]
        marcas = conn.execute(
            "SELECT source, platform, ok FROM ingest_run WHERE source LIKE 'avisos_campana:%'"
        ).fetchall()
        assert marcas == [("avisos_campana:campana_sin_presupuesto", "amazon_mx", True)]


@_skip_db
def test_comando_dos_veces_mismo_dia_envia_una(monkeypatch, tmp_path):
    with _db_avisos("orbit_v4_doble") as conn:
        _siembra_aviso(conn)
        _dsns(monkeypatch, conn)
        with _canal(tmp_path, monkeypatch) as mensajes:
            assert cli_bids.main_avisos_campana([]) == 0
            assert cli_bids.main_avisos_campana([]) == 0
            assert len(mensajes) == 1


@_skip_db
def test_comando_error_sql_en_us_no_calla_mx(monkeypatch, tmp_path):
    import app.pantalla_dinero as pd

    with _db_avisos("orbit_v4_tx") as conn:
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        hoy = _hoy()
        hasta = hoy - dt.timedelta(days=1)
        obs = dt.datetime.combine(hoy, dt.time(), tzinfo=dt.UTC)
        c1 = _siembra_campana(conn, "amazon_mx", "c1", "Ubic")
        _siembra_placement(
            conn,
            "amazon_mx",
            c1,
            "fuera_de_amazon",
            hasta,
            obs,
            Decimal("1735"),
            1127,
            0,
            Decimal("0"),
        )
        c2 = _siembra_campana(conn, "amazon_mx", "c2", "Tope")
        _siembra_config(conn, c2, obs - dt.timedelta(days=10), Decimal("100"), "MANUAL")
        for i in range(7):
            _siembra_gasto_campana(
                conn,
                c2,
                hasta - dt.timedelta(days=6 - i),
                Decimal("90") if i < 5 else Decimal("0"),
                obs,
                run,
            )
        c3 = _siembra_campana(conn, "amazon_mx", "c3", "Holgada")
        _siembra_config(conn, c3, obs - dt.timedelta(days=1), Decimal("500"), "MANUAL")
        _siembra_gasto_campana(conn, c3, hasta, Decimal("1"), obs, run)
        _dsns(monkeypatch, conn)
        real = pd.lee_ubicaciones

        def _lee(c, *, plataforma, desde, hasta):
            if plataforma == "amazon_us":
                c.execute("SELECT 1/0")
            return real(c, plataforma=plataforma, desde=desde, hasta=hasta)

        monkeypatch.setattr(pd, "lee_ubicaciones", _lee)
        with _canal(tmp_path, monkeypatch) as mensajes:
            assert cli_bids.main_avisos_campana([]) == 0
            assert len(mensajes) == 3


@_skip_db
def test_comando_fallo_no_marca_y_reintenta(monkeypatch, tmp_path):
    with _db_avisos("orbit_v4_reintento") as conn:
        _siembra_aviso(conn)
        _dsns(monkeypatch, conn)
        with _canal(tmp_path, monkeypatch, status=500):
            assert cli_bids.main_avisos_campana([]) == 0
        marcas = conn.execute(
            "SELECT count(*) FROM ingest_run WHERE source LIKE 'avisos_campana:%'"
        ).fetchone()[0]
        assert marcas == 0
        with _canal(tmp_path, monkeypatch) as mensajes:
            assert cli_bids.main_avisos_campana([]) == 0
            assert len(mensajes) == 1


def test_comando_sin_dsn_sale_2(monkeypatch, capsys):
    monkeypatch.delenv("ORBIT_DSN_READ", raising=False)
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert cli_bids.main_avisos_campana([]) == 2
    assert "ORBIT_DSN_READ" in capsys.readouterr().err


def test_comando_args_extra_sale_2():
    assert cli_bids.main_avisos_campana(["--foo"]) == 2


# B9 (R05 r3): la llave es (plataforma, clase, dia) y el umbral es por mercado.
# ---------------------------------------------------------------------------


def _corrida(conn):
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]


def _obs_avisos():
    return dt.datetime.combine(_hoy(), dt.time(), tzinfo=dt.UTC)


def _siembra_tres_clases(conn, plataforma, moneda, sufijo, gasto_ubi):
    """Una plataforma con un caso de cada clase (modelo test_r05c_v4)."""
    run = _corrida(conn)
    hasta = _hoy() - dt.timedelta(days=1)
    obs = _obs_avisos()
    tope = _siembra_campana(conn, plataforma, f"t{sufijo}", f"Tope {sufijo}")
    _siembra_config(
        conn, tope, obs - dt.timedelta(days=10), Decimal("100"), "MANUAL", moneda=moneda
    )
    for i in range(7):
        dia = hasta - dt.timedelta(days=6 - i)
        _siembra_gasto_campana(
            conn, tope, dia, Decimal("95") if i < 5 else Decimal("0"), obs, run, moneda=moneda
        )
    for i in range(7, 30):
        _siembra_gasto_campana(
            conn, tope, hasta - dt.timedelta(days=i), Decimal("95"), obs, run, moneda=moneda
        )
    holg = _siembra_campana(conn, plataforma, f"h{sufijo}", f"Holgada {sufijo}")
    _siembra_config(
        conn, holg, obs - dt.timedelta(days=40), Decimal("5354"), "MANUAL", moneda=moneda
    )
    for i in range(30):
        _siembra_gasto_campana(
            conn, holg, hasta - dt.timedelta(days=i), Decimal("43"), obs, run, moneda=moneda
        )
    _siembra_placement(
        conn, plataforma, holg, "fuera_de_amazon", hasta, obs, gasto_ubi, 1127, 0, Decimal("0")
    )
    return tope, holg


@_skip_db
def test_comando_marca_de_ayer_no_calla_hoy(monkeypatch, tmp_path):
    """M4: la llave del dia tiene cota inferior; lo de ayer no calla hoy."""
    with _db_avisos("orbit_v4_ayer") as conn:
        _siembra_tres_clases(conn, "amazon_mx", "MXN", "MX", Decimal("1735"))
        for clase in (
            "ubicacion_gasta_sin_vender",
            "campana_sin_presupuesto",
            "presupuesto_expuesto",
        ):
            conn.execute(
                "INSERT INTO ingest_run (source, platform, ok, started_at, finished_at)"
                " VALUES (%s, 'amazon_mx', true, now() - interval '1 day',"
                " now() - interval '1 day')",
                (f"avisos_campana:{clase}",),
            )
        _dsns(monkeypatch, conn)
        with _canal(tmp_path, monkeypatch) as mensajes:
            assert cli_bids.main_avisos_campana([]) == 0
            assert len(mensajes) == 3, [m["text"] for m in mensajes]


@_skip_db
def test_comando_marca_de_mx_no_calla_us_ni_otra_clase(monkeypatch, tmp_path):
    """M6/M7: la llave incluye plataforma y clase."""
    with _db_avisos("orbit_v4_llave") as conn:
        _siembra_tres_clases(conn, "amazon_mx", "MXN", "MX", Decimal("1735"))
        _siembra_tres_clases(conn, "amazon_us", "USD", "US", Decimal("36"))
        conn.execute(
            "INSERT INTO ingest_run (source, platform, ok, started_at, finished_at)"
            " VALUES ('avisos_campana:campana_sin_presupuesto', 'amazon_mx', true, now(), now())"
        )
        _dsns(monkeypatch, conn)
        with _canal(tmp_path, monkeypatch) as mensajes:
            assert cli_bids.main_avisos_campana([]) == 0
            textos = [m["text"] for m in mensajes]
        assert len(textos) == 5, textos
        assert not any("Tope MX" in t for t in textos)
        assert any("Tope US" in t for t in textos)


@_skip_db
def test_comando_umbral_por_plataforma(monkeypatch, tmp_path):
    """N5: 36 USD avisa en US, 349 MXN no avisa en MX."""
    with _db_avisos("orbit_v4_umbral") as conn:
        hasta = _hoy() - dt.timedelta(days=1)
        mx = _siembra_campana(conn, "amazon_mx", "m1", "Camp MX")
        us = _siembra_campana(conn, "amazon_us", "u1", "Camp US")
        _siembra_placement(
            conn,
            "amazon_mx",
            mx,
            "fuera_de_amazon",
            hasta,
            _obs_avisos(),
            Decimal("349"),
            10,
            0,
            Decimal("0"),
        )
        _siembra_placement(
            conn,
            "amazon_us",
            us,
            "fuera_de_amazon",
            hasta,
            _obs_avisos(),
            Decimal("36"),
            10,
            0,
            Decimal("0"),
        )
        _dsns(monkeypatch, conn)
        with _canal(tmp_path, monkeypatch) as mensajes:
            assert cli_bids.main_avisos_campana([]) == 0
            textos = [m["text"] for m in mensajes]
        assert textos == ["Fuera de Amazon: 36 USD en 30 días, 10 clics, ningún pedido."], textos
