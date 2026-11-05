"""Lote PEZ: nueve identidades, deriva atomica y reversa sin pisar cambios."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_precio02_meli_catalogo import _db, _skip_sin_pg

from tools.corrige_meli_skus import ErrorLote, aplicar, revertir

pytestmark = _skip_sin_pg


@pytest.fixture
def escenario():
    with _db() as conn:
        filas = []
        for i in range(10):
            pid = conn.execute(
                "INSERT INTO product(odoo_sku,name) VALUES (%s,%s) RETURNING id",
                (f"SET-{i}", f"Producto {i}"),
            ).fetchone()[0]
            lid = conn.execute(
                "INSERT INTO listing(product_id,platform,external_id,seller_sku) "
                "VALUES (%s,'meli',%s,%s) RETURNING id",
                (pid, f"MLM{i}", f"ANCLA-{i}"),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO listing_miembro(listing_id,platform,variante_ext,seller_sku,"
                "product_id,activo,visto_at) VALUES (%s,'meli',%s,%s,NULL,%s,%s)",
                (lid, str(i), f"NH-{i}", i not in (7, 8), datetime(2026, 11, 5, tzinfo=UTC)),
            )
            cid = conn.execute(
                "INSERT INTO sku_cost(product_id,cost_amount,cost_currency,includes_tax,"
                "valid_from) VALUES (%s,77.47,'MXN',false,'2020-01-01') RETURNING id",
                (pid,),
            ).fetchone()[0]
            if i < 9:
                filas.append(
                    {
                        "listing_id": lid,
                        "item_id": f"MLM{i}",
                        "variante_ext": str(i),
                        "seller_sku": f"NH-{i}",
                        "product_id_antes": None,
                        "activo_antes": i not in (7, 8),
                        "product_id_destino": pid,
                        "odoo_sku": f"SET-{i}",
                        "costo": {
                            "id": cid,
                            "importe": "77.4700",
                            "moneda": "MXN",
                            "includes_tax": False,
                            "valid_from": "2020-01-01",
                            "valid_to": None,
                        },
                        "evidencia": f"identidad/SET-{i}.json",
                    }
                )
        conn.execute("SET ROLE app_admin")
        try:
            yield conn, {"version": 1, "filas": filas}
        finally:
            conn.execute("RESET ROLE")


def foto(conn):
    return {
        t: conn.execute(f"SELECT to_jsonb(t) FROM {t} t ORDER BY to_jsonb(t)::text").fetchall()
        for t in ("listing", "listing_miembro", "product", "sku_cost", "meli_sku_alias")
    }


def test_aplicar_ensayo_reversa_y_reintento(escenario, tmp_path):
    conn, lote = escenario
    assert not conn.execute(
        "SELECT has_table_privilege(current_user, 'sku_cost', 'UPDATE')"
    ).fetchone()[0]
    assert not conn.execute(
        "SELECT has_table_privilege(current_user, 'product', 'UPDATE')"
    ).fetchone()[0]
    assert conn.execute("SELECT count(*) FROM listing_miembro WHERE NOT activo").fetchone()[0] == 2
    antes = foto(conn)
    respaldo = tmp_path / "respaldo.json"
    assert aplicar(conn, lote) == {"estado": "dry-run", "miembros": 9, "aliases": 9}
    assert foto(conn) == antes
    assert not respaldo.exists()
    assert aplicar(conn, lote, respaldo=respaldo, apply=True)["estado"] == "aplicado"
    assert respaldo.stat().st_mode & 0o777 == 0o600
    assert conn.execute("SELECT count(*) FROM meli_sku_alias").fetchone()[0] == 9
    assert (
        conn.execute(
            "SELECT count(*) FROM listing_miembro WHERE product_id IS NOT NULL"
        ).fetchone()[0]
        == 9
    )
    assert conn.execute(
        "SELECT seller_sku, product_id FROM listing_miembro WHERE seller_sku='NH-9'"
    ).fetchone() == ("NH-9", None)
    for tabla in ("listing", "product", "sku_cost"):
        assert foto(conn)[tabla] == antes[tabla]
    for (_, m0), (_, m1) in zip(
        sorted((x[0]["listing_id"], x[0]) for x in antes["listing_miembro"]),
        sorted((x[0]["listing_id"], x[0]) for x in foto(conn)["listing_miembro"]),
        strict=True,
    ):
        assert {k: v for k, v in m0.items() if k != "product_id"} == {
            k: v for k, v in m1.items() if k != "product_id"
        }
    with pytest.raises(ErrorLote):
        aplicar(conn, lote, respaldo=tmp_path / "otro.json", apply=True)
    assert not (tmp_path / "otro.json").exists()
    assert revertir(conn, respaldo)["estado"] == "revertido"
    assert foto(conn) == antes
    with pytest.raises(ErrorLote):
        revertir(conn, respaldo)


@pytest.mark.parametrize("deriva", ["sku", "costo", "duplicado", "canonico", "respaldo"])
def test_deriva_aborta_sin_escritura_parcial(escenario, tmp_path, deriva):
    conn, lote = escenario
    respaldo = tmp_path / "antes.json"
    ultima = lote["filas"][-1]
    conn.execute("RESET ROLE")
    if deriva == "sku":
        conn.execute(
            "UPDATE listing_miembro SET seller_sku='AJENO' WHERE listing_id=%s",
            (ultima["listing_id"],),
        )
    elif deriva == "costo":
        ultima["costo"]["importe"] = "78.4700"
    elif deriva == "duplicado":
        conn.execute(
            "INSERT INTO listing_miembro SELECT listing_id,platform,'otra',seller_sku,"
            "product_id,activo,visto_at FROM listing_miembro WHERE listing_id=%s",
            (ultima["listing_id"],),
        )
    elif deriva == "canonico":
        conn.execute(
            "INSERT INTO product(odoo_sku,name) VALUES (%s,'Conflicto')", (ultima["seller_sku"],)
        )
    else:
        respaldo.write_text("no sobrescribir")
    conn.execute("SET ROLE app_admin")
    antes = foto(conn)
    with pytest.raises((ErrorLote, FileExistsError)):
        aplicar(conn, lote, respaldo=respaldo, apply=True)
    assert foto(conn) == antes
    if deriva == "respaldo":
        assert respaldo.read_text() == "no sobrescribir"
    else:
        assert not respaldo.exists()


def test_reversa_rechaza_deriva_del_estado_aplicado(escenario, tmp_path):
    conn, lote = escenario
    respaldo = tmp_path / "antes.json"
    aplicar(conn, lote, respaldo=respaldo, apply=True)
    conn.execute("RESET ROLE")
    conn.execute(
        "UPDATE listing_miembro SET visto_at=visto_at+interval '1 second' WHERE listing_id=%s",
        (lote["filas"][-1]["listing_id"],),
    )
    conn.execute("SET ROLE app_admin")
    antes = foto(conn)
    with pytest.raises(ErrorLote):
        revertir(conn, respaldo)
    assert foto(conn) == antes
