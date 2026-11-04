"""Catalogo diario de Mercado Libre (REPRICING 02 M.1).

Por publicacion activa: una fila en `listing` con el producto ancla (el
del primer miembro mapeado por SKU ascendente), sus filas de
`listing_miembro` cruzando el atributo `SELLER_SKU` contra
`product.odoo_sku`, y una fila de `meli_publicacion_observation`. Sin red
real (cliente falso con la forma de la sonda M.0).
"""

from __future__ import annotations

import itertools
import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from test_schema import _postgres_obligatorio_ausente

from app.meli import catalogo
from app.reputacion_clientes import ReputacionError

RAIZ = Path(__file__).resolve().parents[1]

ORDEN_M1 = (
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

AHORA = datetime(2026, 10, 14, 11, 30, tzinfo=UTC)

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
    db = f"precio02m1_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_M1:
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
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (sku, f"Producto {sku}"),
    ).fetchone()[0]


def _variacion(
    var_id: int, sku: str | None, *, precio: float = 1989.0, stock: int | None = 5
) -> dict:
    return {
        "id": var_id,
        "price": precio,
        "available_quantity": stock,
        "attributes": ([{"id": "SELLER_SKU", "value_name": sku}] if sku is not None else []),
    }


def _item(
    item_id: str,
    *,
    estado: str = "active",
    precio: float | None = 1989.0,
    moneda: str | None = "MXN",
    stock_raiz: int | None = None,
    variaciones: list | None = None,
    sku_raiz: str | None = None,
    categoria: str = "MLM1440",
    tipo: str = "gold_pro",
) -> dict:
    return {
        "id": item_id,
        "status": estado,
        "site_id": "MLM",
        "category_id": categoria,
        "listing_type_id": tipo,
        "price": precio,
        "currency_id": moneda,
        "available_quantity": stock_raiz,
        "shipping": {"mode": "me2"},
        "attributes": (
            [{"id": "SELLER_SKU", "value_name": sku_raiz}] if sku_raiz is not None else []
        ),
        "variations": variaciones if variaciones is not None else [],
    }


class _ClienteFalso:
    """`ClienteMeli` de mentiras: `get` + `items_seller`, sin red."""

    def __init__(
        self,
        items: dict[str, dict],
        *,
        visitas: int | None = 7,
        falla_items: frozenset = frozenset(),
        truncado: bool = False,
        seller_id: int = 7,
    ) -> None:
        self._items = items
        self._visitas = visitas
        self._falla = falla_items
        self._truncado = truncado
        self._seller = seller_id
        self.llamadas: list[tuple[str, dict | None]] = []

    def get(self, path: str, params: dict | None = None):
        self.llamadas.append((path, params))
        if path == "/users/me":
            return {"id": self._seller, "site_id": "MLM"}, AHORA
        if path.startswith("/users/") and path.endswith("/items/search"):
            ids = sorted(self._items)
            return {"results": ids, "paging": {"total": len(ids)}}, AHORA
        if path.endswith("/visits/time_window"):
            if self._visitas is None:
                raise ValueError("visitas caidas")
            return {"total_visits": self._visitas, "results": []}, AHORA
        if path.startswith("/items/"):
            item_id = path.removeprefix("/items/")
            if item_id in self._falla:
                raise ReputacionError(f"boom {item_id}")
            return dict(self._items[item_id]), AHORA
        raise AssertionError(f"ruta inesperada: {path}")

    def items_seller(self, seller_id: int, max_paginas: int = 50):
        assert seller_id == self._seller
        datos, _ = self.get(f"/users/{seller_id}/items/search", {"search_type": "scan"})
        return [str(i) for i in datos["results"]], self._truncado

    def close(self) -> None:
        pass


def _corre(conn, cliente, *, ahora: datetime = AHORA):
    return catalogo.sincronizar(conn, cliente, hoy=ahora.date(), ahora=ahora)


@_skip_sin_pg
def test_publicacion_con_tres_variantes_mapeadas_da_una_fila_y_tres_miembros():
    with _db() as conn:
        pid_c = _producto(conn, "SKU-M1-C")
        pid_a = _producto(conn, "SKU-M1-A")
        pid_b = _producto(conn, "SKU-M1-B")
        cliente = _ClienteFalso(
            {
                "MLM1": _item(
                    "MLM1",
                    variaciones=[
                        _variacion(101, "SKU-M1-C", stock=2),
                        _variacion(102, "SKU-M1-A", stock=3),
                        _variacion(103, "SKU-M1-B", stock=4),
                    ],
                )
            }
        )
        resultado = _corre(conn, cliente)
        filas = conn.execute(
            "SELECT id, product_id, platform, external_id, seller_sku FROM listing"
        ).fetchall()
        assert len(filas) == 1
        lid, ancla, plat, externo, sku = filas[0]
        assert (ancla, plat, externo, sku) == (pid_a, "meli", "MLM1", "SKU-M1-A")
        miembros = conn.execute(
            "SELECT variante_ext, seller_sku, product_id, activo"
            " FROM listing_miembro WHERE listing_id = %s ORDER BY variante_ext",
            (lid,),
        ).fetchall()
        assert miembros == [
            ("101", "SKU-M1-C", pid_c, True),
            ("102", "SKU-M1-A", pid_a, True),
            ("103", "SKU-M1-B", pid_b, True),
        ]
        obs = conn.execute(
            "SELECT estado, precio, moneda, stock, tipo_publicacion, categoria_id,"
            " visitas, variantes FROM meli_publicacion_observation WHERE item_id = 'MLM1'"
        ).fetchone()
        assert obs is not None
        assert obs[:7] == ("active", Decimal(1989), "MXN", 9, "gold_pro", "MLM1440", 7)
        assert {"variante_ext": "101", "seller_sku": "SKU-M1-C", "stock": 2} in obs[7]
        assert resultado.con_todos == 1


@_skip_sin_pg
def test_pide_cada_item_con_include_attributes_all():
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        cliente = _ClienteFalso({"MLM1": _item("MLM1", sku_raiz="SKU-M1-A", stock_raiz=1)})
        _corre(conn, cliente)
        pedidos = [p for ruta, p in cliente.llamadas if ruta == "/items/MLM1"]
        assert pedidos != []
        assert all((p or {}).get("include_attributes") == "all" for p in pedidos)


@_skip_sin_pg
def test_variante_sin_producto_queda_con_product_id_nulo():
    with _db() as conn:
        pid = _producto(conn, "SKU-M1-A")
        cliente = _ClienteFalso(
            {
                "MLM1": _item(
                    "MLM1",
                    variaciones=[
                        _variacion(101, "SKU-M1-A", stock=3),
                        _variacion(102, "SKU-NADIE", stock=1),
                    ],
                )
            }
        )
        resultado = _corre(conn, cliente)
        lid = conn.execute("SELECT id FROM listing").fetchone()[0]
        miembros = conn.execute(
            "SELECT seller_sku, product_id FROM listing_miembro"
            " WHERE listing_id = %s ORDER BY variante_ext",
            (lid,),
        ).fetchall()
        assert miembros == [("SKU-M1-A", pid), ("SKU-NADIE", None)]
        assert resultado.con_alguno == 1


@_skip_sin_pg
def test_sku_no_mapeado_primero_no_rompe_el_ancla():
    with _db() as conn:
        pid = _producto(conn, "SKU-M1-A")
        cliente = _ClienteFalso(
            {
                "MLM1": _item(
                    "MLM1",
                    variaciones=[
                        _variacion(101, "SKU-000-NADIE", stock=1),
                        _variacion(102, "SKU-M1-A", stock=3),
                    ],
                )
            }
        )
        resultado = _corre(conn, cliente)
        lid, ancla = conn.execute("SELECT id, product_id FROM listing").fetchone()
        assert ancla == pid
        assert conn.execute(
            "SELECT seller_sku, product_id FROM listing_miembro"
            " WHERE listing_id = %s ORDER BY variante_ext",
            (lid,),
        ).fetchall() == [("SKU-000-NADIE", None), ("SKU-M1-A", pid)]
        assert resultado.con_alguno == 1


@_skip_sin_pg
def test_publicacion_sin_miembros_mapeados_no_entra_a_listing():
    with _db() as conn:
        cliente = _ClienteFalso(
            {
                "MLM1": _item(
                    "MLM1",
                    variaciones=[_variacion(101, "SKU-NADIE", stock=1), _variacion(102, None)],
                )
            }
        )
        resultado = _corre(conn, cliente)
        assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM listing_miembro").fetchone()[0] == 0
        variantes = conn.execute(
            "SELECT variantes FROM meli_publicacion_observation WHERE item_id = 'MLM1'"
        ).fetchone()[0]
        assert {"variante_ext": "101", "seller_sku": "SKU-NADIE", "stock": 1} in variantes
        assert {"variante_ext": "102", "seller_sku": None, "stock": 5} in variantes
        assert resultado.sin_ninguno == 1


@_skip_sin_pg
def test_publicacion_cerrada_no_crea_listing_pero_deja_observacion():
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        cliente = _ClienteFalso(
            {"MLM1": _item("MLM1", estado="closed", sku_raiz="SKU-M1-A", stock_raiz=0)}
        )
        _corre(conn, cliente)
        assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT estado FROM meli_publicacion_observation WHERE item_id = 'MLM1'"
            ).fetchone()[0]
            == "closed"
        )


@_skip_sin_pg
def test_publicacion_sin_variantes_usa_el_sku_de_la_raiz():
    with _db() as conn:
        pid = _producto(conn, "SKU-M1-A")
        cliente = _ClienteFalso(
            {"MLM1": _item("MLM1", precio=979.11, sku_raiz="SKU-M1-A", stock_raiz=4)}
        )
        _corre(conn, cliente)
        lid = conn.execute("SELECT id FROM listing").fetchone()[0]
        assert conn.execute(
            "SELECT variante_ext, seller_sku, product_id FROM listing_miembro"
            " WHERE listing_id = %s",
            (lid,),
        ).fetchall() == [("", "SKU-M1-A", pid)]
        assert conn.execute(
            "SELECT precio, stock FROM meli_publicacion_observation WHERE item_id = 'MLM1'"
        ).fetchone() == (Decimal("979.11"), 4)


@_skip_sin_pg
def test_segunda_corrida_actualiza_sin_duplicar():
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        items = {"MLM1": _item("MLM1", sku_raiz="SKU-M1-A", stock_raiz=4)}
        primero = _corre(conn, _ClienteFalso(items))
        segundo = _corre(conn, _ClienteFalso(items), ahora=AHORA + timedelta(hours=1))
        assert (primero.listings_nuevos, primero.listings_actualizados) == (1, 0)
        assert (segundo.listings_nuevos, segundo.listings_actualizados) == (0, 0)
        assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM listing_miembro").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM meli_publicacion_observation WHERE item_id = 'MLM1'"
            ).fetchone()[0]
            == 2
        )


@_skip_sin_pg
def test_variacion_desaparecida_queda_inactiva():
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        _producto(conn, "SKU-M1-B")
        _corre(
            conn,
            _ClienteFalso(
                {
                    "MLM1": _item(
                        "MLM1",
                        variaciones=[
                            _variacion(101, "SKU-M1-A"),
                            _variacion(102, "SKU-M1-B"),
                        ],
                    )
                }
            ),
        )
        _corre(
            conn,
            _ClienteFalso({"MLM1": _item("MLM1", variaciones=[_variacion(101, "SKU-M1-A")])}),
            ahora=AHORA + timedelta(hours=1),
        )
        lid = conn.execute("SELECT id FROM listing").fetchone()[0]
        assert conn.execute(
            "SELECT variante_ext, activo FROM listing_miembro"
            " WHERE listing_id = %s ORDER BY variante_ext",
            (lid,),
        ).fetchall() == [("101", True), ("102", False)]


@_skip_sin_pg
def test_visitas_caidas_dejan_null_y_cuentan_sin_tumbar_la_publicacion():
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        cliente = _ClienteFalso(
            {"MLM1": _item("MLM1", sku_raiz="SKU-M1-A", stock_raiz=4)}, visitas=None
        )
        resultado = _corre(conn, cliente)
        assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT visitas FROM meli_publicacion_observation WHERE item_id = 'MLM1'"
            ).fetchone()[0]
            is None
        )
        assert resultado.skips["meli: visitas sin dato"] == 1


def test_main_sin_dsn_sale_2(monkeypatch):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert catalogo.main([]) == 2


@_skip_sin_pg
def test_main_con_cliente_inyectado_corre_la_ingesta(monkeypatch):
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        db = conn.execute("SELECT current_database()").fetchone()[0]
        partes = urlsplit(_dsn_base())
        monkeypatch.setenv(
            "ORBIT_DSN_INGEST",
            urlunsplit((partes.scheme, partes.netloc, "/" + db, "", "")),
        )
        cliente = _ClienteFalso({"MLM1": _item("MLM1", sku_raiz="SKU-M1-A", stock_raiz=1)})
        assert catalogo.main([], cliente=cliente) == 0
        assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 1


def _siembra_listing(conn, externo: str, sku: str, *, activo: bool = True) -> int:
    pid = _producto(conn, sku)
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'meli', %s, %s) RETURNING id",
        (pid, externo, sku),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO listing_miembro"
        " (listing_id, platform, variante_ext, seller_sku, product_id, activo, visto_at)"
        " VALUES (%s, 'meli', '', %s, %s, %s, %s)",
        (lid, sku, pid, activo, AHORA - timedelta(days=1)),
    )
    return lid


@_skip_sin_pg
def test_item_que_falla_no_tira_a_los_demas_y_el_run_queda_sellado():
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        items = {f"MLM{i}": _item(f"MLM{i}", sku_raiz="SKU-M1-A", stock_raiz=1) for i in (1, 2, 3)}
        resultado = _corre(conn, _ClienteFalso(items, falla_items=frozenset({"MLM2"})))
        assert resultado.observadas == 2
        assert resultado.skips["meli: item fallo (se reintenta manana)"] == 1
        assert resultado.ok is True
        assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 2
        run = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason FROM ingest_run"
            " WHERE source = 'meli_catalogo'"
        ).fetchone()
        assert run[0] is True and run[1] == 2 and run[2] == 1
        assert "item fallo" in (run[3] or "")


@_skip_sin_pg
def test_cinco_fallos_seguidos_abortan_honesto_sin_escribir():
    with _db() as conn:
        items = {f"MLM{i}": _item(f"MLM{i}") for i in range(6)}
        with pytest.raises(ReputacionError, match="fallos seguidos"):
            _corre(conn, _ClienteFalso(items, falla_items=frozenset(items)))
        assert conn.execute("SELECT count(*) FROM meli_publicacion_observation").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 0


@_skip_sin_pg
def test_main_con_todo_fallado_sella_mal_y_sale_1(monkeypatch):
    with _db() as conn:
        db = conn.execute("SELECT current_database()").fetchone()[0]
        partes = urlsplit(_dsn_base())
        monkeypatch.setenv(
            "ORBIT_DSN_INGEST",
            urlunsplit((partes.scheme, partes.netloc, "/" + db, "", "")),
        )
        cliente = _ClienteFalso({"MLM1": _item("MLM1")}, falla_items=frozenset({"MLM1"}))
        assert catalogo.main([], cliente=cliente) == 1
        assert (
            conn.execute("SELECT ok FROM ingest_run WHERE source = 'meli_catalogo'").fetchone()[0]
            is False
        )


@_skip_sin_pg
def test_scan_truncado_cuenta_y_no_reconcilia():
    with _db() as conn:
        lid = _siembra_listing(conn, "MLM-VIEJO", "SKU-M1-A")
        _producto(conn, "SKU-M1-B")
        resultado = _corre(
            conn,
            _ClienteFalso(
                {"MLM1": _item("MLM1", sku_raiz="SKU-M1-B", stock_raiz=1)}, truncado=True
            ),
        )
        assert resultado.skips["meli: scan truncado"] == 1
        assert resultado.reconciliados == 0
        assert (
            conn.execute(
                "SELECT activo FROM listing_miembro WHERE listing_id = %s", (lid,)
            ).fetchone()[0]
            is True
        )


@_skip_sin_pg
def test_ausente_del_scan_da_de_baja_sin_borrar_la_fila():
    with _db() as conn:
        lid = _siembra_listing(conn, "MLM-VIEJO", "SKU-M1-A")
        _producto(conn, "SKU-M1-B")
        resultado = _corre(
            conn, _ClienteFalso({"MLM1": _item("MLM1", sku_raiz="SKU-M1-B", stock_raiz=1)})
        )
        assert resultado.reconciliados == 1
        assert conn.execute("SELECT count(*) FROM listing WHERE id = %s", (lid,)).fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT activo FROM listing_miembro WHERE listing_id = %s", (lid,)
            ).fetchone()[0]
            is False
        )


@_skip_sin_pg
def test_cierre_da_de_baja_miembros_y_deja_observacion():
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        _corre(conn, _ClienteFalso({"MLM1": _item("MLM1", sku_raiz="SKU-M1-A", stock_raiz=4)}))
        resultado = _corre(
            conn,
            _ClienteFalso({"MLM1": _item("MLM1", estado="closed", stock_raiz=0)}),
            ahora=AHORA + timedelta(hours=1),
        )
        lid = conn.execute("SELECT id FROM listing").fetchone()[0]
        assert conn.execute(
            "SELECT activo FROM listing_miembro WHERE listing_id = %s", (lid,)
        ).fetchall() == [(False,)]
        assert (
            conn.execute(
                "SELECT estado FROM meli_publicacion_observation"
                " WHERE item_id = 'MLM1' ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()[0]
            == "closed"
        )
        assert resultado.reconciliados == 0


@_skip_sin_pg
def test_error_de_escritura_no_tira_lo_demas():
    with _db() as conn:
        _producto(conn, "SKU-M1-A")
        conn.execute(
            "INSERT INTO meli_publicacion_observation"
            " (item_id, metric_date, observed_at, estado, variantes)"
            " VALUES ('MLM2', %s, %s, 'active', '[]')",
            (AHORA.date(), AHORA),
        )
        items = {
            "MLM1": _item("MLM1", sku_raiz="SKU-M1-A", stock_raiz=1),
            "MLM2": _item("MLM2", sku_raiz="SKU-M1-A", stock_raiz=1),
        }
        resultado = _corre(conn, _ClienteFalso(items))
        assert resultado.observadas == 1
        assert resultado.skips["meli: item fallo (se reintenta manana)"] == 1
        assert conn.execute("SELECT count(*) FROM listing").fetchone()[0] == 1


@_skip_sin_pg
def test_vendedor_sin_id_entero_aborta():
    with _db() as conn:
        with pytest.raises(ReputacionError, match="sin id entero"):
            _corre(conn, _ClienteFalso({"MLM1": _item("MLM1")}, seller_id="x"))
        assert conn.execute("SELECT count(*) FROM meli_publicacion_observation").fetchone()[0] == 0
