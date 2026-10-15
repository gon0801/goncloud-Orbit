"""REPRICING 02 F.4: sonda generica de escritura (`tools/precio_sonda.py`).

Sin `--acepto-mutacion-real` imprime el plan y no escribe (DoD). Con el
flag y `--go` mueve la publicacion un centavo y la revierte al vivo.
El mercado se inyecta (falso aqui); en produccion sale de
`precio_mercados.abrir`.
"""

from __future__ import annotations

import itertools
import os
import socket
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from test_schema import _postgres_obligatorio_ausente  # noqa: E402

from tools.precio_sonda import Abortar, main  # noqa: E402

ORDEN_SONDA = ("0001_initial.sql",)

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
def _db_sonda():
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"precio02sonda_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_SONDA:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn, _dsn_de_db(dsn, db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


class _MercadoSondaFalso:
    """Precio vivo fijo; `escribir` solo anota (la red la pone `abrir`)."""

    def __init__(self, *, vivo="116.00", moneda="MXN"):
        from app.precio.tipos import Importe

        self.vivo = Importe(Decimal(vivo), moneda)
        self.escrituras: list = []

    def precio_vivo(self, unidad):
        return self.vivo

    def escribir(self, unidad, precio, *, sonda=False):
        from app.precio.puerto import Acuse

        self.escrituras.append((precio.valor, precio.moneda, sonda))
        return Acuse(aceptada=True, codigo=None, cuerpo={"falso": True})


def _listing(conn, *, sku="SS-SONDA-1", ext="B0SONDA0001"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, 'Producto sonda') RETURNING id",
        (f"ODOO-{sku}",),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_mx', %s, %s) RETURNING id",
        (pid, ext, sku),
    ).fetchone()[0]


@_skip_sin_pg
def test_sonda_sin_flag_imprime_plan_y_no_escribe(monkeypatch, capsys):
    mercado = _MercadoSondaFalso()
    with _db_sonda() as (conn, dsn):
        lid = _listing(conn)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", dsn)
        codigo = main(["--platform", "amazon_mx", "--listing-id", str(lid)], mercado=mercado)
    assert codigo == 0
    assert mercado.escrituras == []
    salida = capsys.readouterr().out
    assert "116.00 MXN -> 116.01 MXN -> 116.00 MXN" in salida


@_skip_sin_pg
def test_sonda_con_go_mueve_un_centavo_y_revierte(monkeypatch, capsys):
    mercado = _MercadoSondaFalso()
    with _db_sonda() as (conn, dsn):
        lid = _listing(conn)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", dsn)
        codigo = main(
            [
                "--platform",
                "amazon_mx",
                "--listing-id",
                str(lid),
                "--acepto-mutacion-real",
                "--go",
                "sonda-f4",
            ],
            mercado=mercado,
        )
    assert codigo == 0
    assert mercado.escrituras == [
        (Decimal("116.01"), "MXN", True),
        (Decimal("116.00"), "MXN", True),
    ]
    assert "revierte" in capsys.readouterr().out


@_skip_sin_pg
def test_sonda_listing_inexistente_o_de_otra_plataforma_aborta(monkeypatch):
    mercado = _MercadoSondaFalso()
    with _db_sonda() as (conn, dsn):
        lid = _listing(conn)
        monkeypatch.setenv("ORBIT_DSN_DECIDE", dsn)
        with pytest.raises(Abortar, match="inexistente"):
            main(["--platform", "amazon_mx", "--listing-id", "999999"], mercado=mercado)
        with pytest.raises(Abortar, match="otra plataforma"):
            main(["--platform", "amazon_us", "--listing-id", str(lid)], mercado=mercado)
        assert mercado.escrituras == []
