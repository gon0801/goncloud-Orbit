"""Tests de la migracion 0024 (REPUTACION 01 1.1).

(a) ESTATICOS: la migracion parsea y trae invariantes, triggers
    append-only y GRANTs.
(b) INTEGRACION: 0001 + 0024 en Postgres real; cada invariante muerde.
    Skip fail-closed sin Postgres (misma condicion que test_schema).
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from pathlib import Path

import pglast
import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
SQL24 = (ROOT / "migrations" / "0024_reputacion.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

OBS = "2026-09-07 22:00:00+00"  # observed_at FIJO (evita flakiness)
OBS2 = "2026-09-07 23:00:00+00"
FETCH = "2026-09-07 21:30:00+00"

# ---------------------------------------------------------------------------
# (a) ESTATICOS
# ---------------------------------------------------------------------------


def test_migracion_parsea_y_trae_invariantes():
    assert len(tuple(pglast.parse_sql(SQL24))) > 0
    assert "UNIQUE NULLS NOT DISTINCT\n        (platform, external_id" in SQL24
    assert "UNIQUE\n        (platform, review_external_id)" in SQL24
    assert "CHECK (\n        rating IS NULL OR (rating >= 1 AND rating <= 5)" in SQL24
    assert "review_count IS NULL OR review_count >= 0" in SQL24
    assert "resolved = (resolved_at IS NOT NULL)" in SQL24
    assert "resolved_at IS NULL OR resolved_at >= created_at" in SQL24
    # Append-only por motor desde el dia 1 (leccion de 0023), en las dos
    # tablas de hechos; reputation_alert queda fuera a proposito.
    assert SQL24.count("EXECUTE FUNCTION prohibir_mutacion()") == 4
    triggers = SQL24.split("CREATE TRIGGER")[1:]
    assert len(triggers) == 4
    assert all("ON reputation_alert" not in t.split(";")[0] for t in triggers)
    # GRANTs: ingesta inserta hechos, decide inserta alertas y sella resolved.
    assert "GRANT INSERT ON reputation_snapshot, review_event TO app_ingest" in SQL24
    assert "GRANT INSERT ON reputation_alert TO app_decide" in SQL24
    assert "GRANT UPDATE (resolved, resolved_at) ON reputation_alert" in SQL24
    # Sin UPDATE ni DELETE de datos en el codigo: fuera comentarios, el
    # BEFORE UPDATE OR DELETE de los triggers y el GRANT UPDATE por columna.
    codigo = (
        "\n".join(linea for linea in SQL24.splitlines() if not linea.strip().startswith("--"))
        .replace("GRANT UPDATE", "")
        .replace("BEFORE UPDATE OR DELETE", "")
    )
    assert "UPDATE" not in codigo
    assert "DELETE" not in codigo


# ---------------------------------------------------------------------------
# (b) INTEGRACION: Postgres real con 0001 + 0024
# ---------------------------------------------------------------------------

ORDEN = ("0001_initial.sql", "0024_reputacion.sql")


@contextmanager
def db_reputacion(prefijo: str = "orbit_rep01"):
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sembrar_listing(conn, odoo_sku="P-1", plataforma="amazon_mx", asin="B0X"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (odoo_sku, odoo_sku),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO listing (product_id, platform, external_id) VALUES (%s, %s, %s)",
        (pid, plataforma, asin),
    )


@_skip_db
def test_snapshot_listing_y_cuenta_ok_y_append():
    with db_reputacion() as conn:
        _sembrar_listing(conn)
        conn.execute(
            "INSERT INTO reputation_snapshot (platform, external_id, alcance,"
            " metric_date, rating, review_count, fetched_at, observed_at)"
            " VALUES ('amazon_mx', 'B0X', 'listing', '2026-09-07', 4.30, 120,"
            f" '{FETCH}', '{OBS}')"
        )
        # Metrica de cuenta: external_id NULL, sin FK que la frene.
        conn.execute(
            "INSERT INTO reputation_snapshot (platform, alcance, metric_date,"
            " rating, review_count, fetched_at, observed_at, extra)"
            " VALUES ('meli', 'cuenta', '2026-09-07', NULL, NULL,"
            f" '{FETCH}', '{OBS}', '{{\"level\": \"5_green\"}}')"
        )
        # Re-observacion posterior ANADE (append-only), no pisa.
        conn.execute(
            "INSERT INTO reputation_snapshot (platform, external_id, alcance,"
            " metric_date, rating, review_count, fetched_at, observed_at)"
            " VALUES ('amazon_mx', 'B0X', 'listing', '2026-09-07', 4.30, 121,"
            f" '{FETCH}', '{OBS2}')"
        )
        assert conn.execute("SELECT count(*) FROM reputation_snapshot").fetchone()[0] == 3


@_skip_db
def test_snapshot_invariantes_muerden():
    with db_reputacion() as conn:
        _sembrar_listing(conn)
        base = (
            "INSERT INTO reputation_snapshot (platform, external_id, alcance,"
            " metric_date, rating, review_count, fetched_at, observed_at)"
            " VALUES ("
        )
        # Rating fuera de [1, 5] en ambos extremos.
        for rating in ("0.99", "5.01"):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    base + f"'amazon_mx', 'B0X', 'listing', '2026-09-07', {rating},"
                    f" 1, '{FETCH}', '{OBS}')"
                )
        # Conteo negativo.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                base + f"'amazon_mx', 'B0X', 'listing', '2026-09-07', 4.5, -1, '{FETCH}', '{OBS}')"
            )
        # Alcance incoherente: listing sin external_id, cuenta con external_id.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                base + f"'amazon_mx', NULL, 'listing', '2026-09-07', 4.5, 1, '{FETCH}', '{OBS}')"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                base + f"'amazon_mx', 'B0X', 'cuenta', '2026-09-07', 4.5, 1, '{FETCH}', '{OBS}')"
            )
        # FK: listing fuera del catalogo no se escribe.
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                base + "'amazon_mx', 'B0FANTASMA', 'listing', '2026-09-07', 4.5,"
                f" 1, '{FETCH}', '{OBS}')"
            )
        # Duplicado exacto (mismo observed_at) -> UNIQUE muerde.
        conn.execute(
            base + f"'amazon_mx', 'B0X', 'listing', '2026-09-07', 4.5, 1, '{FETCH}', '{OBS}')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                base + f"'amazon_mx', 'B0X', 'listing', '2026-09-07', 4.5, 1, '{FETCH}', '{OBS}')"
            )
        # Append-only por motor: UPDATE y DELETE revientan con el ERRCODE
        # del trigger prohibir_mutacion (restrict_violation, no P0001).
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE reputation_snapshot SET rating = 1.0")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM reputation_snapshot")


@_skip_db
def test_review_event_dedup_por_id_externo_y_rating():
    with db_reputacion() as conn:
        _sembrar_listing(conn)
        base = (
            "INSERT INTO review_event (platform, external_id, review_external_id,"
            " rating, texto, published_at, fetched_at, observed_at) VALUES ("
        )
        conn.execute(
            base + "'amazon_mx', 'B0X', 'R1', 1, 'malo',"
            f" '2026-09-06 10:00:00+00', '{FETCH}', '{OBS}')"
        )
        # Mismo review con DISTINTO observed_at: sigue siendo duplicado.
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                base + "'amazon_mx', 'B0X', 'R1', 1, 'malo',"
                f" '2026-09-06 10:00:00+00', '{FETCH}', '{OBS2}')"
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(base + f"'amazon_mx', 'B0X', 'R2', 6, NULL, NULL, '{FETCH}', '{OBS}')")
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE review_event SET rating = 5")


@_skip_db
def test_alert_mutable_solo_para_sellar():
    with db_reputacion() as conn:
        _sembrar_listing(conn)
        # Resuelta sin fecha y fecha sin marca: incoherente.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO reputation_alert (platform, external_id, tipo,"
                " severidad, mensaje, resolved) VALUES ('amazon_mx', 'B0X',"
                " 'rating_bajo', 'aviso', 'cayo', TRUE)"
            )
        # resolved_at anterior a created_at.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO reputation_alert (platform, external_id, tipo,"
                " severidad, mensaje, resolved, created_at, resolved_at)"
                " VALUES ('amazon_mx', 'B0X', 'rating_bajo', 'aviso', 'cayo',"
                " TRUE, '2026-09-07 22:00:00+00', '2026-09-07 21:00:00+00')"
            )
        # Listing sin platform.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO reputation_alert (external_id, tipo, severidad,"
                " mensaje) VALUES ('B0X', 'rating_bajo', 'aviso', 'cayo')"
            )
        # Alerta de cuenta (NULLs) + alerta de listing: ok.
        conn.execute(
            "INSERT INTO reputation_alert (tipo, severidad, mensaje)"
            " VALUES ('salud_cuenta', 'critica', 'cuenta en riesgo')"
        )
        aid = conn.execute(
            "INSERT INTO reputation_alert (platform, external_id, tipo,"
            " severidad, mensaje, created_at) VALUES ('amazon_mx', 'B0X',"
            " 'resena_1_2', 'critica', 'review 1 estrella',"
            " '2026-09-07 22:00:00+00') RETURNING id"
        ).fetchone()[0]
        # Sellar resolved funciona (sin trigger que lo frene).
        conn.execute(
            "UPDATE reputation_alert SET resolved = TRUE,"
            " resolved_at = '2026-09-07 23:00:00+00' WHERE id = %s",
            (aid,),
        )
        assert conn.execute(
            "SELECT resolved FROM reputation_alert WHERE id = %s", (aid,)
        ).fetchone()[0]
        assert (
            conn.execute("SELECT count(*) FROM reputation_alert WHERE NOT resolved").fetchone()[0]
            == 1
        )
