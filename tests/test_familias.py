"""Familias en dos niveles (A2): migracion, escritor/lector, API y pagina."""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo
from test_fabrica_migracion import _ledger_producto, _producto, db_fabrica
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import fabrica_plan as fp
from app import familias
from app.api_write import HEADER_TOKEN
from app.main import app

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

TOKEN = "token-familias-test-271828"
HOY = dt.datetime.now(dt.UTC).date()

MIGRACIONES = Path(__file__).resolve().parents[1] / "migrations"
ORDEN_FAMILIAS = ("0001_initial.sql", "0047_familias.sql")


@contextmanager
def db_familias():
    """DB temporal con 0001 + 0047; yields conn autocommit."""
    import os
    import socket

    dsn = _test_dsn()
    db = f"orbit_familias_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_FAMILIAS:
            conn.execute((MIGRACIONES / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _secrets_token(tmp_path, monkeypatch):
    d = tmp_path / "secrets"
    d.mkdir(exist_ok=True)
    (d / "api_write_token").write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))


def _dsn_temp(monkeypatch, conn):
    dsn = make_conninfo(_test_dsn(), dbname=conn.info.dbname)
    monkeypatch.setenv("ORBIT_DSN_READ", dsn)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
    return dsn


def _ventas(conn, pid, n, platform="amazon_mx"):
    """N ventas de 1 unidad en [hoy-n, hoy): dentro de la ventana 90d."""
    run = conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok)"
        " VALUES ('t-ventas', now(), true) RETURNING id"
    ).fetchone()[0]
    for i in range(n):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id,"
            " product_id, quantity, amount, amount_currency, ingest_run_id)"
            " VALUES (%s, 'sale', %s, %s, %s, 1, 100, 'MXN', %s)",
            (platform, HOY - dt.timedelta(days=n - i), f"v-{pid}-{i}", pid, run),
        )


# ---------------------------------------------------------------------------
# Pureza: slug y aviso (sin DB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("Arras", "arras"),
        ("Arras premium", "arras_premium"),
        ("Café Barato 20%", "cafe_barato_20"),
        ("  Aseo_Hogar-2 ", "aseo_hogar_2"),
    ],
)
def test_slug_determinista(nombre, esperado):
    assert familias.slug(nombre) == esperado


@pytest.mark.parametrize("nombre", ["", "   ", "!!!", "---"])
def test_slug_vacio_o_irreconocible_rechaza(nombre):
    with pytest.raises(familias.FamiliaInvalida):
        familias.slug(nombre)


def test_aviso_mezcla_necesita_dos():
    assert fp.aviso_mezcla_familias([]) is None
    assert fp.aviso_mezcla_familias(["Arras"]) is None
    assert fp.aviso_mezcla_familias(["Arras", "Arras"]) is None
    aviso = fp.aviso_mezcla_familias(["Collares", "Arras"])
    assert aviso is not None
    assert "Arras" in aviso and "Collares" in aviso


# ---------------------------------------------------------------------------
# Migracion 0047 + escritor/lector
# ---------------------------------------------------------------------------


@_skip_db
def test_tercer_nivel_rechazado_por_trigger():
    """Plan A2: un tercer nivel lo rechaza el Python con error cerrado y el
    candado final es el trigger 0047 (un CHECK no ve otras filas); la DB
    queda sin cambios."""
    with db_familias() as conn:
        raiz = familias.crea(conn, "amazon_mx", "Arras")
        hija = familias.crea(conn, "amazon_mx", "Arras premium", raiz["id"])
        assert hija["padre_id"] == raiz["id"]
        with pytest.raises(familias.FamiliaInvalida, match="tercer nivel"):
            familias.crea(conn, "amazon_mx", "Arras premium oro", hija["id"])
        # El candado final es el trigger, no solo el Python: SQL crudo tambien
        # revienta (regla 9: la regresion demuestra el candado real).
        with pytest.raises(Exception, match="0047: tercer nivel"):
            conn.execute(
                "INSERT INTO familia(platform, nombre, slug, padre_id)"
                " VALUES ('amazon_mx', 'Cruda', 'cruda', %s)",
                (hija["id"],),
            )
        filas = conn.execute("SELECT nombre FROM familia ORDER BY id").fetchall()
        assert [f[0] for f in filas] == ["Arras", "Arras premium"]


@_skip_db
def test_trigger_etiqueta_otra_plataforma_y_reparentado_con_hijas():
    """F2 AI-review PR #382: los dos invariantes sin camino en la app
    (etiqueta cruzada y reparentar con hijas) tienen regresion SQL cruda."""
    with db_familias() as conn:
        pid, _ = _producto(conn, sku="SKU-F2", asin="B0FAMILIAF2", seller_sku="SF2")
        us = familias.crea(conn, "amazon_us", "Arras")
        with pytest.raises(Exception, match="0047: la familia .* no es de"):
            conn.execute(
                "INSERT INTO producto_familia(product_id, platform, familia_id)"
                " VALUES (%s, 'amazon_mx', %s)",
                (pid, us["id"]),
            )
        raiz = familias.crea(conn, "amazon_mx", "Raiz")
        familias.crea(conn, "amazon_mx", "Hija", raiz["id"])
        otra = familias.crea(conn, "amazon_mx", "Otra")
        with pytest.raises(Exception, match="0047: .* ya tiene hijas"):
            conn.execute(
                "UPDATE familia SET padre_id = %s WHERE id = %s",
                (otra["id"], raiz["id"]),
            )


@_skip_db
def test_trigger_serializa_reparentado_concurrente():
    """F1 AI-review PR #382: el FOR UPDATE del trigger serializa el
    reparentado contra la hija concurrente sin confirmar (sin el lock,
    el UPDATE pasaria y formaria un tercer nivel)."""
    with db_familias() as conn:
        b = familias.crea(conn, "amazon_mx", "B")
        a = familias.crea(conn, "amazon_mx", "A")
        dsn = make_conninfo(_test_dsn(), dbname=conn.info.dbname)
        t1 = psycopg.connect(dsn)
        t1.execute(
            "INSERT INTO familia(platform, nombre, slug, padre_id)"
            " VALUES ('amazon_mx', 'HijaB', 'hijab', %s)",
            (b["id"],),
        )
        t2 = psycopg.connect(dsn, autocommit=True)
        t2.execute("SET lock_timeout = '1s'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            t2.execute("UPDATE familia SET padre_id = %s WHERE id = %s", (a["id"], b["id"]))
        t1.commit()
        t1.close()
        t2.execute("SET lock_timeout = '10s'")
        with pytest.raises(psycopg.errors.RaiseException, match="ya tiene hijas"):
            t2.execute("UPDATE familia SET padre_id = %s WHERE id = %s", (a["id"], b["id"]))
        t2.close()


@_skip_db
def test_fks_con_indice_de_apoyo():
    """F5 AI-review PR #382: toda FK de 0047 tiene indice de apoyo
    (invariante transversal; test_schema solo parsea 0001/0002/0039)."""
    with db_familias() as conn:
        indices = {
            (fila[0], fila[1])
            for fila in conn.execute(
                "SELECT tablename, indexdef FROM pg_indexes"
                " WHERE schemaname = 'public'"
                " AND tablename IN ('familia', 'producto_familia')"
            ).fetchall()
        }
        por_tabla = {}
        for tabla, definicion in indices:
            por_tabla.setdefault(tabla, []).append(definicion)
        assert any("padre_id" in d for d in por_tabla["familia"])
        assert any("familia_id" in d for d in por_tabla["producto_familia"])


@_skip_db
def test_trigger_raiz_no_cambia_plataforma_con_hijas():
    """F3 AI-review PR #382: un UPDATE de platform en una raiz con hijas
    no las deja en otra plataforma."""
    with db_familias() as conn:
        raiz = familias.crea(conn, "amazon_mx", "Raiz")
        familias.crea(conn, "amazon_mx", "Hija", raiz["id"])
        with pytest.raises(Exception, match="0047: .* otra plataforma"):
            conn.execute(
                "UPDATE familia SET platform = 'amazon_us' WHERE id = %s",
                (raiz["id"],),
            )


@_skip_db
def test_trigger_no_mueve_plataforma_con_etiquetas():
    """F6 AI-review PR #382: mover la plataforma de una familia con
    etiquetas (aunque no tenga hijas) no deja filas cruzadas."""
    with db_familias() as conn:
        pid, _ = _producto(conn, sku="SKU-F6", asin="B0FAMILIAF6", seller_sku="SF6")
        fam = familias.crea(conn, "amazon_mx", "Sola")
        familias.asigna(conn, [pid], fam["id"])
        with pytest.raises(Exception, match="0047: .* etiquetas en otra plataforma"):
            conn.execute(
                "UPDATE familia SET platform = 'amazon_us' WHERE id = %s",
                (fam["id"],),
            )


@_skip_db
def test_mover_deja_una_fila():
    """Plan A2: reasignar mueve la etiqueta, no duplica (una fila por
    producto y plataforma)."""
    with db_familias() as conn:
        pid, _ = _producto(conn, sku="SKU-MV", asin="B0FAMILIA01", seller_sku="SMV")
        a = familias.crea(conn, "amazon_mx", "Arras")
        b = familias.crea(conn, "amazon_mx", "Arras baratas")
        familias.asigna(conn, [pid], a["id"])
        familias.asigna(conn, [pid], b["id"])
        filas = conn.execute(
            "SELECT familia_id FROM producto_familia WHERE product_id = %s", (pid,)
        ).fetchall()
        assert [f[0] for f in filas] == [b["id"]]


@_skip_db
def test_asigna_masiva_idempotente():
    """Plan A2: la asignacion masiva corrida dos veces deja lo mismo."""
    with db_familias() as conn:
        pids = [
            _producto(conn, sku=f"SKU-B{i}", asin=f"B0FAMILI{i:02d}", seller_sku=f"SB{i}")[0]
            for i in range(3)
        ]
        fam = familias.crea(conn, "amazon_mx", "Arras")
        uno = familias.asigna(conn, pids, fam["id"])
        dos = familias.asigna(conn, pids, fam["id"])
        assert uno == dos == {"familia_id": fam["id"], "asignados": 3}
        filas = conn.execute(
            "SELECT product_id, familia_id FROM producto_familia ORDER BY product_id"
        ).fetchall()
        assert filas == [(pid, fam["id"]) for pid in sorted(pids)]


@_skip_db
def test_crea_duplicada_409_y_padre_ajeno_422():
    with db_familias() as conn:
        mx = familias.crea(conn, "amazon_mx", "Arras")
        with pytest.raises(familias.FamiliaDuplicada):
            familias.crea(conn, "amazon_mx", "Arras")
        with pytest.raises(familias.FamiliaDuplicada):
            familias.crea(conn, "amazon_mx", "arras")
        with pytest.raises(familias.FamiliaNoExiste):
            familias.crea(conn, "amazon_mx", "Huerfana", 99999)
        us = familias.crea(conn, "amazon_us", "Arras")
        with pytest.raises(familias.FamiliaInvalida):
            familias.crea(conn, "amazon_mx", "Mezcla", us["id"])
        assert mx["slug"] == "arras"


@_skip_db
def test_asigna_producto_inexistente_404():
    with db_familias() as conn:
        fam = familias.crea(conn, "amazon_mx", "Arras")
        with pytest.raises(familias.FamiliaNoExiste):
            familias.asigna(conn, [424242], fam["id"])
        with pytest.raises(familias.FamiliaNoExiste):
            familias.asigna(conn, [1], 424242)


@_skip_db
def test_arbol_conteo_ventas_y_meta_pais():
    with db_familias() as conn:
        pid, _ = _producto(conn, sku="SKU-AR", asin="B0FAMILIAR", seller_sku="SAR")
        _ventas(conn, pid, 10)
        raiz = familias.crea(conn, "amazon_mx", "Arras")
        hija = familias.crea(conn, "amazon_mx", "Arras premium", raiz["id"])
        familias.asigna(conn, [pid], hija["id"])
        (nodo,) = familias.arbol(conn, "amazon_mx")
        assert nodo["nombre"] == "Arras"
        assert nodo["productos"] == 0
        assert nodo["origen_meta"] == "usa la meta del país"
        (sub,) = nodo["hijas"]
        assert sub["nombre"] == "Arras premium"
        assert sub["productos"] == 1
        assert sub["ventas_90d"] == 10
        assert sub["origen_meta"] == "usa la meta del país"


@_skip_db
def test_productos_busca_filtra_y_mide():
    with db_familias() as conn:
        pid, _ = _producto(conn, sku="SKU-ARRAS-1", asin="B0FAMILIAQ", seller_sku="SAQ")
        conn.execute("UPDATE product SET name = 'Arras doradas' WHERE id = %s", (pid,))
        _ventas(conn, pid, 5)
        otro, _ = _producto(conn, sku="SKU-COL-1", asin="B0FAMILIAC", seller_sku="SAC")
        conn.execute("UPDATE product SET name = 'Collar rojo' WHERE id = %s", (otro,))
        fam = familias.crea(conn, "amazon_mx", "Arras")
        familias.asigna(conn, [pid], fam["id"])
        todos = familias.productos(conn, "amazon_mx")
        assert {i["id"] for i in todos} == {pid, otro}
        solo = familias.productos(conn, "amazon_mx", q="arras")
        assert [i["id"] for i in solo] == [pid]
        assert solo[0]["familia"]["nombre"] == "Arras"
        assert solo[0]["ventas_90d"] == 5
        huerfanos = familias.productos(conn, "amazon_mx", sin_familia=True)
        assert [i["id"] for i in huerfanos] == [otro]
        assert huerfanos[0]["familia"] is None


@_skip_db
def test_origen_meta_exige_familia():
    with db_familias() as conn:
        fam = familias.crea(conn, "amazon_mx", "Arras")
        assert familias.origen_meta_familia(conn, "amazon_mx", fam["id"]) == (
            "usa la meta del país"
        )
        with pytest.raises(familias.FamiliaNoExiste):
            familias.origen_meta_familia(conn, "amazon_mx", 424242)


# ---------------------------------------------------------------------------
# Fabrica: slug y aviso
# ---------------------------------------------------------------------------


def _args_fabrica(productos=None, listings=None, tipo="collar_perro"):
    import tools.fabrica_campanas as fc

    return fc._parser().parse_args(
        [
            "--plataforma",
            "amazon_mx",
            *(["--tipo-producto", tipo] if tipo else []),
            "--nombre-base",
            "Prueba",
            "--modo",
            "shadow",
            *(["--productos", productos] if productos else ["--listing-ids", listings]),
            "--budget-auto",
            "150",
            "--budget-phrase",
            "120",
            "--budget-product",
            "120",
            "--budget-broad",
            "120",
            "--budget-exact",
            "150",
            "--bid-auto",
            "4.50",
            "--bid-phrase",
            "5.00",
            "--bid-product",
            "5.00",
            "--bid-broad",
            "4.00",
            "--bid-exact",
            "6.00",
        ]
    )


def _base_fabrica(conn):
    conn.execute(
        "INSERT INTO config_version(label,settings) VALUES ('fabrica',"
        ' \'{"ads_target_fraccion_margen_amazon_mx": "0.5"}\'::jsonb)'
    )
    conn.execute(
        "INSERT INTO keyword_biblioteca(tipo_producto,platform,texto,orders,origen)"
        " VALUES ('arras','amazon_mx','arras',2,'manual')"
    )


@_skip_db
def test_fabrica_toma_slug_de_familia_unica():
    """Plan A2 lane 8 (unit): con familia unica el tipo_producto es el slug,
    aunque se de otro tipo (la familia manda)."""
    import tools.fabrica_campanas as fc

    with db_fabrica("orbit_familias_fab") as conn:
        _base_fabrica(conn)
        pid, _ = _producto(conn, sku="SKU-FAM", asin="B0FAMILIAF", seller_sku="SAF")
        _ledger_producto(conn, pid, hoy=HOY)
        fam = familias.crea(conn, "amazon_mx", "Arras")
        familias.asigna(conn, [pid], fam["id"])
        plan = fc._arma_plan(_args_fabrica(productos=str(pid), tipo="otro_tipo"), conn)
        assert plan.tipo_producto == "arras"
        assert plan.familia == "arras"
        assert plan.advertencia_mezcla is None


@_skip_db
def test_fabrica_avisa_mezcla_y_exige_tipo():
    """Plan A2 (unit): dos familias = aviso antes del plan y tipo dado
    obligatorio (no hay slug unico que tomar)."""
    import tools.fabrica_campanas as fc

    with db_fabrica("orbit_familias_mez") as conn:
        _base_fabrica(conn)
        p1, _ = _producto(conn, sku="SKU-M1", asin="B0FAMILIAM1", seller_sku="SM1")
        p2, _ = _producto(conn, sku="SKU-M2", asin="B0FAMILIAM2", seller_sku="SM2")
        _ledger_producto(conn, p1, hoy=HOY)
        _ledger_producto(conn, p2, hoy=HOY, fee_desfase=7)
        familias.asigna(conn, [p1], familias.crea(conn, "amazon_mx", "Arras")["id"])
        familias.asigna(conn, [p2], familias.crea(conn, "amazon_mx", "Collares")["id"])
        plan = fc._arma_plan(_args_fabrica(productos=f"{p1},{p2}"), conn)
        assert plan.tipo_producto == "collar_perro"
        assert plan.familia is None
        assert plan.advertencia_mezcla is not None
        assert "Arras" in plan.advertencia_mezcla
        assert "Collares" in plan.advertencia_mezcla
        with pytest.raises(fc.Abortar, match="--tipo-producto es obligatorio"):
            fc._arma_plan(_args_fabrica(productos=f"{p1},{p2}", tipo=None), conn)


@_skip_db
def test_fabrica_parcial_no_toma_slug():
    """CodeRabbit PR #382 (Major): un grupo con un producto etiquetado y
    otro sin etiqueta NO toma el slug: usa el tipo dado y avisa."""
    import tools.fabrica_campanas as fc

    with db_fabrica("orbit_familias_par") as conn:
        _base_fabrica(conn)
        p1, _ = _producto(conn, sku="SKU-P1", asin="B0FAMILIAP1", seller_sku="SP1")
        p2, _ = _producto(conn, sku="SKU-P2", asin="B0FAMILIAP2", seller_sku="SP2")
        _ledger_producto(conn, p1, hoy=HOY)
        _ledger_producto(conn, p2, hoy=HOY, fee_desfase=7)
        familias.asigna(conn, [p1], familias.crea(conn, "amazon_mx", "Arras")["id"])
        plan = fc._arma_plan(_args_fabrica(productos=f"{p1},{p2}"), conn)
        assert plan.tipo_producto == "collar_perro"
        assert plan.familia is None
        assert plan.advertencia_mezcla is not None
        assert "sin familia" in plan.advertencia_mezcla
        assert str(p2) in plan.advertencia_mezcla


@_skip_db
def test_preview_web_sin_tipo_con_familia_unica():
    """CodeRabbit PR #382: el schema web acepta tipo ausente y el preview
    lo resuelve del slug (misma resolucion que el CLI)."""
    from app import fabrica_web as fw

    with db_fabrica("orbit_familias_web") as conn:
        _base_fabrica(conn)
        pid, lid = _producto(conn, sku="SKU-WEB", asin="B0FAMWEB01", seller_sku="SWB")
        _ledger_producto(conn, pid, hoy=HOY)
        familias.asigna(conn, [pid], familias.crea(conn, "amazon_mx", "Arras")["id"])
        roles = fp.ROLES_ORDEN_CREACION
        salida = fw.previsualizar(
            conn,
            {
                "plataforma": "amazon_mx",
                "nombre_base": "Web",
                "listing_ids": [lid],
                "objetivo": {"origen": "manual_lanzamiento", "acos_pct": "25.00"},
                "modo": "shadow",
                "parametros": {rol: {"budget": "120.00", "bid": "4.00"} for rol in roles},
            },
        )
        assert salida["plan"]["tipo_producto"] == "arras"
        assert salida["familia"] == "arras"
        assert salida["advertencia_mezcla"] is None


@_skip_db
def test_fabrica_sin_etiqueta_camino_viejo():
    """Sin etiqueta no hay familia ni aviso y el tipo dado se usa igual."""
    import tools.fabrica_campanas as fc

    with db_fabrica("orbit_familias_vie") as conn:
        _base_fabrica(conn)
        pid, _ = _producto(conn, sku="SKU-VIE", asin="B0FAMILIAV1", seller_sku="SAV")
        _ledger_producto(conn, pid, hoy=HOY)
        plan = fc._arma_plan(_args_fabrica(productos=str(pid)), conn)
        assert plan.tipo_producto == "collar_perro"
        assert plan.familia is None
        assert plan.advertencia_mezcla is None
        with pytest.raises(fc.Abortar, match="--tipo-producto es obligatorio"):
            fc._arma_plan(_args_fabrica(productos=str(pid), tipo=None), conn)


# ---------------------------------------------------------------------------
# API /api/familias*
# ---------------------------------------------------------------------------


def test_api_sin_token_401(tmp_path, monkeypatch):
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    cliente = TestClient(app)
    resp = cliente.post("/api/familias", json={"platform": "amazon_mx", "nombre": "Arras"})
    assert resp.status_code == 401
    resp = cliente.post("/api/familias/asignar", json={"familia_id": 1, "product_ids": [1]})
    assert resp.status_code == 401


@_skip_db
def test_api_crea_asigna_y_errores(tmp_path, monkeypatch):
    _secrets_token(tmp_path, monkeypatch)
    with db_familias() as conn:
        _dsn_temp(monkeypatch, conn)
        pid, _ = _producto(conn, sku="SKU-API", asin="B0FAMILIAP1", seller_sku="SAP")
        cliente = TestClient(app)
        headers = {HEADER_TOKEN: TOKEN}
        raiz = cliente.post(
            "/api/familias",
            json={"platform": "amazon_mx", "nombre": "Arras"},
            headers=headers,
        )
        assert raiz.status_code == 200, raiz.text
        assert raiz.json()["slug"] == "arras"
        hija = cliente.post(
            "/api/familias",
            json={
                "platform": "amazon_mx",
                "nombre": "Arras premium",
                "padre_id": raiz.json()["id"],
            },
            headers=headers,
        )
        assert hija.status_code == 200, hija.text
        tercero = cliente.post(
            "/api/familias",
            json={
                "platform": "amazon_mx",
                "nombre": "Arras oro",
                "padre_id": hija.json()["id"],
            },
            headers=headers,
        )
        assert tercero.status_code == 422
        dupe = cliente.post(
            "/api/familias",
            json={"platform": "amazon_mx", "nombre": "Arras"},
            headers=headers,
        )
        assert dupe.status_code == 409
        ok = cliente.post(
            "/api/familias/asignar",
            json={"familia_id": hija.json()["id"], "product_ids": [pid]},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"familia_id": hija.json()["id"], "asignados": 1}
        fantasma = cliente.post(
            "/api/familias/asignar",
            json={"familia_id": 424242, "product_ids": [pid]},
            headers=headers,
        )
        assert fantasma.status_code == 404


# ---------------------------------------------------------------------------
# Pagina /familias
# ---------------------------------------------------------------------------


@_skip_db
def test_pagina_familias_muestra_insignia_y_arbol(tmp_path, monkeypatch):
    _secrets_token(tmp_path, monkeypatch)
    with db_familias() as conn:
        _dsn_temp(monkeypatch, conn)
        pid, _ = _producto(conn, sku="SKU-PAG", asin="B0FAMILIAG1", seller_sku="SPG")
        conn.execute("UPDATE product SET name = 'Arras pagina' WHERE id = %s", (pid,))
        fam = familias.crea(conn, "amazon_mx", "Arras")
        familias.asigna(conn, [pid], fam["id"])
        otro, _ = _producto(conn, sku="SKU-PAG2", asin="B0FAMILIAG2", seller_sku="SPG2")
        conn.execute("UPDATE product SET name = 'Collar pagina' WHERE id = %s", (otro,))
        cliente = TestClient(app)
        html = cliente.get("/familias").text
        assert "Arras pagina" in html
        assert "sin familia" in html
        assert "usa la meta del país" in html
        assert "familias.js" in html
        filtrado = cliente.get("/familias", params={"sin_familia": "1"}).text
        assert "Collar pagina" in filtrado
        assert "Arras pagina" not in filtrado
        buscado = cliente.get("/familias", params={"q": "collar"}).text
        assert "Collar pagina" in buscado
        assert "Arras pagina" not in buscado
