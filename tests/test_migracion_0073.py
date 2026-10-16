"""Migracion 0073 (REPRICING 02 M.3): costo del miembro que manda.

En `meli`, `estimacion_escenario.sku_cost_id` debe ser del producto de
`miembro_product_id` (la cuenta de la fila), y el miembro debe ser de la
unidad. Amazon intacto: costo siempre del producto del listing.
"""

from __future__ import annotations

import itertools
import os
import socket
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
SQL73 = (MIGRACIONES / "0073_precio02_meli_miembro_costo.sql").read_text(encoding="utf-8")

ORDEN_73 = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
    "0055_precio02_escenario_exige.sql",
    "0073_precio02_meli_miembro_costo.sql",
)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()


@contextmanager
def _db73():
    from psycopg import sql as pgsql

    dsn = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
    partes = urlsplit(dsn)
    db = f"precio02m73_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(
            urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment)),
            autocommit=True,
        )
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_73:
            conn.execute((MIGRACIONES / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _producto(conn, sku: str) -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (sku, sku),
    ).fetchone()[0]


def _minimo(conn, pids: tuple[int, ...], *, plataforma: str, miembro: int | None, costo: int):
    """Fila minima que llega al chequeo de costo del trigger."""
    ahora = "2026-09-08 12:00:00+00"
    ancla = pids[0]
    lids = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (ancla, plataforma, "EXT-73", "SKU-73"),
    ).fetchone()[0]
    if plataforma == "meli":
        for i, pid in enumerate(pids[:2]):
            conn.execute(
                "INSERT INTO listing_miembro"
                " (listing_id, platform, variante_ext, seller_sku, product_id,"
                " activo, visto_at)"
                " VALUES (%s, 'meli', %s, %s, %s, true, %s)",
                (lids, f"v-{i}", f"SKU-73-{i}", pid, ahora),
            )
    run = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written,"
        " rows_skipped, ok) VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE)"
        " RETURNING id",
        (ahora, ahora),
    ).fetchone()[0]
    costo_id = conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id) VALUES (%s, 10, 'MXN', FALSE, '2026-01-01', %s)"
        " RETURNING id",
        (costo, run),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO estimacion_escenario (listing_id, platform, seller_sku, asin, canal,"
        " valoracion_date, observed_at, formula_version, estado, motivos, componentes,"
        " exclusiones, canonical_input, context_fingerprint, source_event_id,"
        " miembro_product_id, sku_cost_id)"
        " VALUES (%s, %s, 'SKU-73', 'EXT-73', %s, '2026-09-08', %s, 'S3',"
        " 'incompleta', '[\"costo_ausente\"]', '[]', '[]', '{}', 'fp73', 'ev73', %s, %s)",
        (
            lids,
            plataforma,
            "meli" if plataforma == "meli" else "fbm",
            ahora,
            miembro,
            costo_id,
        ),
    )


@_skip_db
def test_0073_meli_acepta_costo_del_miembro():
    with _db73() as conn:
        pa = _producto(conn, "SKU-73-A")
        pb = _producto(conn, "SKU-73-B")
        _minimo(conn, (pa, pb), plataforma="meli", miembro=pb, costo=pb)


@_skip_db
def test_0073_meli_rechaza_costo_fuera_de_la_unidad():
    with _db73() as conn:
        pa = _producto(conn, "SKU-73-A")
        pb = _producto(conn, "SKU-73-B")
        pc = _producto(conn, "SKU-73-C")
        with pytest.raises(psycopg.errors.CheckViolation, match="no es del miembro"):
            _minimo(conn, (pa, pb), plataforma="meli", miembro=pb, costo=pc)


@_skip_db
def test_0073_meli_rechaza_miembro_fuera_de_la_unidad():
    with _db73() as conn:
        pa = _producto(conn, "SKU-73-A")
        pb = _producto(conn, "SKU-73-B")
        pc = _producto(conn, "SKU-73-C")
        with pytest.raises(psycopg.errors.CheckViolation, match="no es de la unidad"):
            _minimo(conn, (pa, pb), plataforma="meli", miembro=pc, costo=pc)


@_skip_db
def test_0073_amazon_intacto():
    with _db73() as conn:
        pa = _producto(conn, "SKU-73-A")
        pb = _producto(conn, "SKU-73-B")
        with pytest.raises(psycopg.errors.CheckViolation, match="no es del producto del listing"):
            _minimo(conn, (pa, pb), plataforma="amazon_mx", miembro=None, costo=pb)


@_skip_db
def test_0073_amazon_feliz_pasa():
    with _db73() as conn:
        pa = _producto(conn, "SKU-73-A")
        _minimo(conn, (pa,), plataforma="amazon_mx", miembro=None, costo=pa)


@_skip_db
def test_0073_reversa_restaura_0028():
    reversa = (MIGRACIONES / "0073_reversa_precio02_meli_miembro_costo.sql").read_text(
        encoding="utf-8"
    )
    sql_cuerpo = (
        "SELECT pg_get_functiondef('estimacion_escenario_referencias_coherentes()'::regprocedure)"
    )
    with _db73() as conn:
        cuerpo = conn.execute(sql_cuerpo).fetchone()[0]
        assert "miembro_product_id" in cuerpo
        conn.execute(reversa)
        cuerpo = conn.execute(sql_cuerpo).fetchone()[0]
        assert "miembro_product_id" not in cuerpo
        pa = _producto(conn, "SKU-73-A")
        pb = _producto(conn, "SKU-73-B")
        with pytest.raises(psycopg.errors.CheckViolation, match="no es del producto del listing"):
            _minimo(conn, (pa, pb), plataforma="meli", miembro=pb, costo=pb)
        _minimo(conn, (pa,), plataforma="amazon_mx", miembro=None, costo=pa)
