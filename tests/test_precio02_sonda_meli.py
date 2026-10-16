"""REPRICING 02 R05-r3 B1: la sonda de MeLi arma la unidad con miembros.

`tools/precio_sonda.py:_unidad` leia solo `listing` (un miembro con
variante None): sobre una publicacion con variantes, `escribir`
(CR-2) abortaba sin PUT, y sobre una sin variantes el PUT
`{"price": X}` nunca probaba la forma candidata. Ahora arma los
miembros desde `listing_miembro` activos, igual que
`MercadoMeli.catalogo`, y `_corre` con el mercado real manda PUT con
"variations". Amazon sigue con miembro unico.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

from app.meli.precio_mercado import MercadoMeli
from app.meli.write_client import MeliWriteClient
from app.precio.puerto import Miembro
from tools.precio_sonda import Abortar, _corre, _unidad

RAIZ = Path(__file__).resolve().parents[1]

ORDEN_SONDA_MELI = (
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
AHORA = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db():
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"precio02sondam_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_SONDA_MELI:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
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
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto sonda meli') RETURNING id",
        (sku,),
    ).fetchone()[0]


def _listing_meli(conn, producto: int, *, ext="MLM1", sku="SKU-A") -> int:
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'meli', %s, %s) RETURNING id",
        (producto, ext, sku),
    ).fetchone()[0]


def _miembro(conn, lid: int, *, variante: str, sku: str, pid: int | None, activo=True) -> None:
    conn.execute(
        "INSERT INTO listing_miembro"
        " (listing_id, platform, variante_ext, seller_sku, product_id, activo, visto_at)"
        " VALUES (%s, 'meli', %s, %s, %s, %s, %s)",
        (lid, variante, sku, pid, activo, AHORA),
    )


class _Lector:
    """GET falso con la forma de M.0: precio raiz + variaciones vivas."""

    def __init__(self, variaciones):
        self.variaciones = variaciones

    def get(self, path, params=None):
        return {
            "price": 979.11,
            "currency_id": "MXN",
            "variations": self.variaciones,
        }, AHORA


class _Creds:
    access_token = "x"


def _mercado_real(puts: list, variaciones):
    def _transporte(request: httpx.Request) -> httpx.Response:
        puts.append(request.content.decode())
        return httpx.Response(200, json={})

    escritor = MeliWriteClient(
        modo_confirmado="live", creds=_Creds(), transport=httpx.MockTransport(_transporte)
    )
    return MercadoMeli("meli", lector=_Lector(variaciones), escritor=escritor, fees=object())


def _acepto():
    return argparse.Namespace(acepto_mutacion_real=True, go="plan repricing-02 X.4")


@_skip_sin_pg
def test_unidad_meli_arma_miembros_desde_listing_miembro():
    """B1: dos variantes activas (+ una dada de baja) -> dos miembros."""
    with _db() as conn:
        pid_a = _producto(conn, "SKU-A")
        pid_b = _producto(conn, "SKU-B")
        lid = _listing_meli(conn, pid_a)
        _miembro(conn, lid, variante="101", sku="SKU-A", pid=pid_a)
        _miembro(conn, lid, variante="102", sku="SKU-B", pid=pid_b)
        _miembro(conn, lid, variante="103", sku="SKU-VIEJA", pid=None, activo=False)
        unidad = _unidad(conn, listing_id=lid, platform="meli")
    assert unidad.miembros == (
        Miembro(pid_a, "SKU-A", "101"),
        Miembro(pid_b, "SKU-B", "102"),
    )


@_skip_sin_pg
def test_corre_meli_real_manda_put_con_variations(capsys):
    """B1: `_corre` con MercadoMeli real: ida y vuelta llevan variations.

    Muere si `_unidad` vuelve al miembro unico con variante None (el
    CR-2 aborta sin PUT) o si el cuerpo pierde las variaciones.
    """
    with _db() as conn:
        pid_a = _producto(conn, "SKU-A")
        pid_b = _producto(conn, "SKU-B")
        lid = _listing_meli(conn, pid_a)
        _miembro(conn, lid, variante="101", sku="SKU-A", pid=pid_a)
        _miembro(conn, lid, variante="102", sku="SKU-B", pid=pid_b)
        unidad = _unidad(conn, listing_id=lid, platform="meli")
        puts: list = []
        mercado = _mercado_real(puts, [{"id": 101}, {"id": 102}])
        rc = _corre(conn, mercado, unidad, _acepto())
    assert rc == 0
    assert len(puts) == 2
    ida = json.loads(puts[0])
    vuelta = json.loads(puts[1])
    assert ida["price"] == 979.12
    assert vuelta["price"] == 979.11
    for cuerpo in (ida, vuelta):
        assert [v["id"] for v in cuerpo["variations"]] == ["101", "102"]
        assert [v["price"] for v in cuerpo["variations"]] == [cuerpo["price"]] * 2
    assert "sonda" in capsys.readouterr().out


@_skip_sin_pg
def test_unidad_meli_sin_miembros_aborta():
    """B1: listing meli sin miembros activos no tiene ruta que escribir."""
    with _db() as conn:
        pid = _producto(conn, "SKU-A")
        lid = _listing_meli(conn, pid)
        with pytest.raises(Abortar, match="sin miembros"):
            _unidad(conn, listing_id=lid, platform="meli")


@_skip_sin_pg
def test_unidad_amazon_sigue_miembro_unico():
    """B1: el arreglo es solo meli; Amazon arma un miembro sin variante."""
    with _db() as conn:
        pid = _producto(conn, "ODOO-AMZ")
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0AMZ0001', 'SKU-AMZ') RETURNING id",
            (pid,),
        ).fetchone()[0]
        unidad = _unidad(conn, listing_id=lid, platform="amazon_mx")
    assert unidad.miembros == (Miembro(pid, "SKU-AMZ", None),)
