"""REPRICING 02 R05-r3 B2: cobertura real de MeLi (DoD de D.4).

`tools/precio_cobertura.py --platform meli` decia `activas=unknown`
y salia 0 siempre (decorativa: ni con una base inexistente
fallaba). Ahora compara las activas del catalogo contra las
activas vistas por la corrida `meli_catalogo` terminada de hoy:
cuadra (0), no cuadra (1) o sin corrida (2 = no medido). Las
publicaciones activas sin miembros mapeados se cuentan aparte,
no fallan (DoD de M.1).
"""

from __future__ import annotations

import itertools
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

RAIZ = Path(__file__).resolve().parents[1]

ORDEN_COB_MELI = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db():
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"cobmeli_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_COB_MELI:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn, _dsn_de_db(dsn, db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _tool(*args, dsn):
    """Corre `tools/precio_cobertura.py` solo contra el DSN dado (lector)."""
    env = dict(os.environ)
    env["ORBIT_DSN_READ"] = dsn
    env["ORBIT_DSN_ADMIN"] = "postgresql://orbit:orbit@127.0.0.1:1/nula"
    env["ORBIT_DSN_DECIDE"] = "postgresql://orbit:orbit@127.0.0.1:1/nula"
    env.pop("ORBIT_PG_HOST", None)
    env["PYTHONPATH"] = RAIZ.as_posix()
    return subprocess.run(
        [sys.executable, "tools/precio_cobertura.py", *args],
        capture_output=True,
        text=True,
        cwd=RAIZ,
        env=env,
    )


def _producto(conn, sku: str) -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto cobertura meli') RETURNING id",
        (sku,),
    ).fetchone()[0]


def _listing(conn, producto: int, *, ext: str, sku: str) -> int:
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'meli', %s, %s) RETURNING id",
        (producto, ext, sku),
    ).fetchone()[0]


def _miembro(conn, lid: int, *, variante: str, sku: str, pid: int | None, activo=True) -> None:
    conn.execute(
        "INSERT INTO listing_miembro"
        " (listing_id, platform, variante_ext, seller_sku, product_id, activo, visto_at)"
        " VALUES (%s, 'meli', %s, %s, %s, %s, now())",
        (lid, variante, sku, pid, activo),
    )


def _run(conn, *, ok=True, cuando=None, terminado=True) -> int:
    cuando = cuando or datetime.now(UTC)
    fin = cuando if terminado else None
    return conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written, ok)"
        " VALUES ('meli_catalogo', %s, %s, 0, %s) RETURNING id",
        (cuando, fin, ok),
    ).fetchone()[0]


def _obs(conn, run: int | None, *, item: str, estado="active", cuando=None) -> None:
    cuando = cuando or datetime.now(UTC)
    conn.execute(
        "INSERT INTO meli_publicacion_observation"
        " (item_id, metric_date, observed_at, estado, ingest_run_id)"
        " VALUES (%s, %s::date, %s, %s, %s)",
        (item, cuando.date().isoformat(), cuando, estado, run),
    )


def _mapeada(conn, *, ext: str, sku: str) -> int:
    """Listing meli con un miembro raiz mapeado (fluye al catalogo)."""
    pid = _producto(conn, sku)
    lid = _listing(conn, pid, ext=ext, sku=sku)
    _miembro(conn, lid, variante="", sku=sku, pid=pid)
    return lid


@_skip_sin_pg
def test_meli_cuadra_con_sin_mapear_aparte():
    """B2: 2 mapeadas + 1 activa sin listing cuadran; la suelta se nombra."""
    with _db() as (conn, dsn):
        _mapeada(conn, ext="MLM1", sku="SKU-1")
        _mapeada(conn, ext="MLM2", sku="SKU-2")
        run = _run(conn)
        _obs(conn, run, item="MLM1")
        _obs(conn, run, item="MLM2")
        _obs(conn, run, item="MLM3")
        res = _tool("--platform", "meli", dsn=dsn)
    assert res.returncode == 0, res.stderr
    assert "api_activas=3" in res.stdout
    assert "catalogo=2" in res.stdout
    assert "sin_mapear=1" in res.stdout
    assert "[CUADRA]" in res.stdout
    assert "MLM3" in res.stdout


@_skip_sin_pg
def test_meli_sin_corrida_de_hoy_no_mide():
    """B2: sin corrida terminada de hoy, exit 2 (el checklist no falla)."""
    with _db() as (conn, dsn):
        _mapeada(conn, ext="MLM1", sku="SKU-1")
        res = _tool("--platform", "meli", dsn=dsn)
    assert res.returncode == 2
    assert "no medido" in res.stdout


@_skip_sin_pg
def test_meli_faltan_es_falla():
    """B2: activa mapeada en la corrida pero pausada despues = FALLA.

    La obs mas nueva (de una corrida sin terminar) dice `paused`
    mientras la ultima corrida terminada la vio `active`: el
    catalogo la pierde y la cobertura lo grita.
    """
    with _db() as (conn, dsn):
        _mapeada(conn, ext="MLM1", sku="SKU-1")
        ahora = datetime.now(UTC)
        run = _run(conn, cuando=ahora - timedelta(hours=2))
        _obs(conn, run, item="MLM1", cuando=ahora - timedelta(hours=2))
        incompleto = _run(conn, cuando=ahora - timedelta(hours=1), terminado=False)
        _obs(conn, incompleto, item="MLM1", estado="paused", cuando=ahora - timedelta(hours=1))
        res = _tool("--platform", "meli", dsn=dsn)
    assert res.returncode == 1, res.stdout
    assert "[NO-CUADRA]" in res.stdout
    assert "faltan_en_catalogo" in res.stdout and "MLM1" in res.stdout


@_skip_sin_pg
def test_meli_sobran_es_falla():
    """B2: activa en catalogo pero ausente de la corrida de hoy = FALLA.

    El item salio del scan (baja en la API): la obs mas nueva sigue
    `active` (ayer) y sus miembros se dieron de baja; ya no fluye
    pero el catalogo aun la cuenta.
    """
    with _db() as (conn, dsn):
        lid = _mapeada(conn, ext="MLM1", sku="SKU-1")
        ayer = datetime.now(UTC) - timedelta(days=1)
        run_viejo = _run(conn, cuando=ayer)
        _obs(conn, run_viejo, item="MLM1", cuando=ayer)
        conn.execute("UPDATE listing_miembro SET activo = false WHERE listing_id = %s", (lid,))
        _run(conn)
        res = _tool("--platform", "meli", dsn=dsn)
    assert res.returncode == 1, res.stdout
    assert "[NO-CUADRA]" in res.stdout
    assert "sobran_en_catalogo" in res.stdout and "MLM1" in res.stdout


@_skip_sin_pg
def test_meli_corrida_de_ayer_no_cuenta():
    """B2: solo corrida de ayer = sin corrida de hoy (exit 2)."""
    with _db() as (conn, dsn):
        _mapeada(conn, ext="MLM1", sku="SKU-1")
        ayer = datetime.now(UTC) - timedelta(days=1)
        run = _run(conn, cuando=ayer)
        _obs(conn, run, item="MLM1", cuando=ayer)
        res = _tool("--platform", "meli", dsn=dsn)
    assert res.returncode == 2
    assert "no medido" in res.stdout
