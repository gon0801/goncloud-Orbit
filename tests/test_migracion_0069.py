"""Migracion 0069 (BIDS 02 R09-r4): tabla de intenciones de retiro.

anuncio_retiro_intencion: intencion escrita ANTES del PUT (cerrada_el
NULL = pendiente). Solo-cierre (NULL -> valor, sin tocar lo demas),
sin DELETE, una pendiente por anuncio."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from test_fabrica_migracion import _entidad, _producto, db_fabrica
from test_migracion_0065 import SQL64, SQL65
from test_schema import _postgres_obligatorio_ausente

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
SQL66 = (MIGRACIONES / "0066_bids02_retiro_anuncios.sql").read_text(encoding="utf-8")
SQL68 = (MIGRACIONES / "0068_bids02_sellos.sql").read_text(encoding="utf-8")
SQL69 = (MIGRACIONES / "0069_bids02_retiro_intencion.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


def _anuncio(conn, lid, external="ad-69"):
    camp = _entidad(conn, "amazon_mx", "campaign", f"c-{external}")
    ag = _entidad(conn, "amazon_mx", "ad_group", f"ag-{external}", parent=camp)
    return _entidad(conn, "amazon_mx", "product_ad", external, parent=ag, listing_id=lid)


def _intencion(conn, ent, external="ad-69"):
    return conn.execute(
        "INSERT INTO anuncio_retiro_intencion (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, actor, creada_el) VALUES ('amazon_mx', 'sin_pedidos', 'pausa',"
        " 'retiro-69', NULL, %s, %s, 'ag-69', 'c-69', 'SS-69', 'B069000001',"
        " 'state=ENABLED', 'dueno', now()) RETURNING id",
        (ent, external),
    ).fetchone()[0]


@_skip_db
def test_0069_una_pendiente_por_anuncio():
    """0069: dos intenciones pendientes del mismo anuncio se rechazan;
    pendiente + cerrada conviven."""
    with db_fabrica("orbit_m69") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        iid = _intencion(conn, ent)
        with pytest.raises(psycopg.errors.UniqueViolation, match="una_pendiente"):
            _intencion(conn, ent)
        conn.execute(
            "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = 'x' WHERE id = %s",
            (iid,),
        )
        _intencion(conn, ent)


@_skip_db
def test_0069_cierra_y_lo_demas_es_historia():
    """0069: cerrar una pendiente (NULL -> now()) pasa; tocar otro
    campo, re-cerrar o DELETE se rechaza."""
    with db_fabrica("orbit_m69b") as conn:
        conn.execute(SQL64)
        conn.execute(SQL65)
        conn.execute(SQL66)
        conn.execute(SQL68)
        conn.execute(SQL69)
        _pid, lid = _producto(conn)
        ent = _anuncio(conn, lid)
        iid = _intencion(conn, ent)
        assert (
            conn.execute(
                "SELECT cerrada_el FROM anuncio_retiro_intencion WHERE id = %s", (iid,)
            ).fetchone()[0]
            is None
        )
        conn.execute(
            "UPDATE anuncio_retiro_intencion SET cerrada_el = now(), detalle = 'sello'"
            " WHERE id = %s",
            (iid,),
        )
        assert (
            conn.execute(
                "SELECT cerrada_el IS NOT NULL FROM anuncio_retiro_intencion WHERE id = %s",
                (iid,),
            ).fetchone()[0]
            is True
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="SOLO-CIERRE"):
            conn.execute(
                "UPDATE anuncio_retiro_intencion SET modo = 'archivo' WHERE id = %s", (iid,)
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="SOLO-CIERRE"):
            conn.execute(
                "UPDATE anuncio_retiro_intencion SET cerrada_el = now() WHERE id = %s", (iid,)
            )
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM anuncio_retiro_intencion WHERE id = %s", (iid,))
