"""API de fabrica con Postgres real y frontera externa simulada."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import tuple_row
from psycopg.types.json import Json
from test_fabrica_migracion import _ledger_producto, _producto, db_fabrica
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import fabrica_plan as fp
from app.api_write import HEADER_TOKEN

pytestmark = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
TOKEN = "token-prueba-fabrica-local"
HEADERS = {HEADER_TOKEN: TOKEN}


def _modulos():
    assert importlib.util.find_spec("app.api_fabrica"), "falta el router de fabrica"
    from app import api_fabrica, fabrica_web

    return api_fabrica, fabrica_web


@pytest.fixture
def escenario(monkeypatch):
    api_fabrica, fw = _modulos()
    with db_fabrica("orbit_api_fabrica") as conn:
        migraciones = Path(__file__).resolve().parents[1] / "migrations"
        for nombre in (
            "0020_ads_producto_metrica.sql",
            "0021_economia_observada.sql",
            "0022_disponibilidad_snapshot.sql",
            "0028_estimacion_venta.sql",
            "0049_jev_ads.sql",
            "0050_jev_revision_created_at.sql",
        ):
            conn.execute((migraciones / nombre).read_text(encoding="utf-8"))
        conn.row_factory = tuple_row
        dsn = make_conninfo(_test_dsn(), dbname=conn.info.dbname)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn)
        import os

        Path(os.environ["ORBIT_SECRETS_DIR"], "api_write_token").write_text(TOKEN)
        pid, _ = _producto(conn, sku="A", asin="B0AAAAAAAA", seller_sku="SA")
        _ledger_producto(conn, pid, hoy=dt.datetime.now(dt.UTC).date())
        sin, _ = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        multiple, _ = _producto(conn, sku="C", asin="B0CCCCCCCC", seller_sku="SC")
        conn.execute(
            "INSERT INTO listing(product_id, platform, external_id, seller_sku) "
            "VALUES (%s,'amazon_mx','B0DDDDDDDD','SC2')",
            (multiple,),
        )
        conn.execute(
            "INSERT INTO config_version(label,settings) VALUES ('fabrica',%s)",
            (Json({"ads_target_fraccion_margen_amazon_mx": "0.5"}),),
        )
        conn.execute(
            "INSERT INTO keyword_biblioteca(tipo_producto,platform,texto,orders,origen) "
            "VALUES ('collar_perro','amazon_mx','collar',2,'manual')"
        )
        solicitud = {
            "plataforma": "amazon_mx",
            "tipo_producto": "collar_perro",
            "nombre_base": "Collar",
            "productos": [pid],
            "modo": "shadow",
            "parametros": {
                rol: {"budget": "120.00", "bid": "4.00"} for rol in fp.ROLES_ORDEN_CREACION
            },
        }
        app = FastAPI()
        app.include_router(api_fabrica.router)

        def prohibido(*args, **kwargs):
            raise AssertionError("ninguna llamada externa en estos tests")

        monkeypatch.setattr(fw.fc.AdsCredentials, "from_secrets_dir", prohibido)
        with TestClient(app) as cliente:
            yield cliente, conn, solicitud, fw, (pid, sin, multiple)


def _preview(cliente, solicitud):
    respuesta = cliente.post("/api/fabrica/plan", json=solicitud)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _crear(cliente, solicitud, huella):
    return cliente.post(
        "/api/fabrica/crear",
        headers=HEADERS,
        json={"solicitud": solicitud, "huella": huella, "confirmacion": "CREAR 5 CAMPAÑAS"},
    )


def _solicitud_v2(solicitud, listing_ids, *, objetivo=None):
    datos = {clave: valor for clave, valor in solicitud.items() if clave != "productos"}
    datos["listing_ids"] = listing_ids
    datos["objetivo"] = objetivo or {"origen": "manual_lanzamiento", "acos_pct": "25.00"}
    return datos


def _motor_simulado(monkeypatch, fw, conn, *, error=False):
    llamadas = []

    def mutar(args, plan, huella, *, lote):
        llamadas.append(lote)
        fw.fc._inserta_lote(conn, lote, plan, huella, args.go)
        conn.execute(
            "INSERT INTO fabrica_lote_paso(lote,orden,rol,recurso,request_payload,estado,"
            "external_id,ack,readback_estado) VALUES (%s,1,'category_exact','campaign',"
            "'{}','applied','externo-1',%s,'ENABLED')",
            (lote, Json({"access_token": TOKEN, "cuerpo": "ACK NO PUBLICO"})),
        )
        if error:
            fw.fc._sella_lote(conn, lote, "failed", f"ACK NO PUBLICO {TOKEN}")
            raise fw.fc.Abortar(f"ACK NO PUBLICO {TOKEN}")
        fw.fc._sella_lote(conn, lote, "applied", None)
        return 0

    monkeypatch.setattr(fw.fc, "_mutar", mutar)
    return llamadas


def test_catalogo_no_inventa_margenes_y_abre_multilisting_por_publicacion(escenario):
    cliente, _, _, _, ids = escenario
    respuesta = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx")
    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["moneda"] == "MXN" and data["tipos_producto"] == ["collar_perro"]
    filas = data["productos"]
    assert [p["id"] for p in filas] == list(ids)
    assert "elegible" not in filas[0] and "margen_neto_pct" not in filas[0]
    assert filas[0]["publicaciones"][0]["elegible"]
    assert filas[0]["publicaciones"][0]["margen_neto_pct"] == "40.00000000000000000"
    assert filas[0]["publicaciones"][0]["dias_con_venta"] == 70
    assert filas[0]["publicaciones"][0]["ventana_desde"]
    assert filas[0]["publicaciones"][0]["ventana_hasta"]
    assert filas[1]["publicaciones"][0]["margen_neto_pct"] is None
    assert filas[1]["publicaciones"][0]["elegible"]
    assert "margen" in filas[1]["publicaciones"][0]["motivos"][0].lower()
    assert all(publicacion["elegible"] for publicacion in filas[2]["publicaciones"])


def test_catalogo_identifica_todas_las_publicaciones_por_plataforma(escenario):
    cliente, conn, _, _, ids = escenario
    conn.execute("UPDATE product SET name = 'Nombre interno' WHERE id = %s", (ids[2],))
    conn.execute(
        "INSERT INTO listing(product_id,platform,external_id,seller_sku) "
        "VALUES (%s,'amazon_us','B0EEEEEEEE','SKU-US')",
        (ids[2],),
    )
    mx = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx").json()["productos"][2]
    assert mx["nombre"] == "Nombre interno"
    assert [(p["asin"], p["seller_sku"], p["url"]) for p in mx["publicaciones"]] == [
        ("B0CCCCCCCC", "SC", "https://www.amazon.com.mx/dp/B0CCCCCCCC"),
        ("B0DDDDDDDD", "SC2", "https://www.amazon.com.mx/dp/B0DDDDDDDD"),
    ]
    assert all(publicacion["elegible"] for publicacion in mx["publicaciones"])
    us = cliente.get("/api/fabrica/catalogo?plataforma=amazon_us").json()["productos"][0]
    assert [(p["asin"], p["seller_sku"], p["url"]) for p in us["publicaciones"]] == [
        ("B0EEEEEEEE", "SKU-US", "https://www.amazon.com/dp/B0EEEEEEEE"),
    ]
    conn.execute(
        "UPDATE listing SET external_id = 'javascript:alert(1)' WHERE platform='amazon_us'"
    )
    invalido = cliente.get("/api/fabrica/catalogo?plataforma=amazon_us").json()["productos"][0]
    assert invalido["publicaciones"][0]["url"] is None
    assert not invalido["publicaciones"][0]["elegible"]
    assert "ASIN invalido." in invalido["publicaciones"][0]["motivos"]
    conn.execute("UPDATE listing SET external_id = 'A0AAAAAAAA' WHERE platform='amazon_us'")
    catalogo_us = cliente.get("/api/fabrica/catalogo?plataforma=amazon_us").json()
    fuera_de_patron = catalogo_us["productos"][0]
    assert not fuera_de_patron["publicaciones"][0]["elegible"]
    assert "ASIN invalido." in fuera_de_patron["publicaciones"][0]["motivos"]


def test_preview_solo_lectura_sin_amazon_y_con_dinero_string(escenario):
    cliente, conn, solicitud, _, _ = escenario
    vista = _preview(cliente, solicitud)
    assert vista["lote"] == "web-" + vista["huella"]
    assert len(vista["campanas"]) == 5
    assert vista["presupuesto_diario_total"] == "600.00"
    assert vista["plan"]["target_acos_pct"] == "20.00"
    assert vista["plan"]["semillas"]["keywords"] == ["collar"]
    assert all(p["budget"] == "120.00" and p["bid"] == "4.00" for p in vista["campanas"])
    assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone()[0] == 0


def test_preview_v2_acepta_sin_margen_y_varios_listings_sin_escribir(escenario):
    cliente, conn, solicitud, _, ids = escenario
    listing_sin_margen = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[1],)
    ).fetchone()[0]
    listing_multiple = [
        fila[0]
        for fila in conn.execute(
            "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx' ORDER BY id",
            (ids[2],),
        ).fetchall()
    ]
    vista = _preview(cliente, _solicitud_v2(solicitud, [listing_sin_margen, *listing_multiple]))
    assert vista["plan"]["schema_version"] == 2
    assert vista["plan"]["objetivo"] == {
        "origen": "manual_lanzamiento",
        "acos_pct": "25.00",
        "procedencia": "manual_lanzamiento confirmado",
        "fraccion": None,
        "derivado": None,
    }
    assert [p["margen_neto_pct"] for p in vista["plan"]["publicaciones"]] == [None, None, None]
    assert len({p["product_id"] for p in vista["plan"]["publicaciones"]}) == 2
    assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone()[0] == 0


def test_preview_v2_rechaza_mezcla_y_sku_duplicado_sin_escribir(escenario):
    cliente, conn, solicitud, _, ids = escenario
    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
    ).fetchone()[0]
    mezclada = _solicitud_v2(solicitud, [listing])
    mezclada["productos"] = [ids[0]]
    assert cliente.post("/api/fabrica/plan", json=mezclada).status_code == 422
    conn.execute(
        "UPDATE listing SET seller_sku = 'DUPLICADO' "
        "WHERE product_id = %s AND platform = 'amazon_mx'",
        (ids[2],),
    )
    multiples = [
        fila[0]
        for fila in conn.execute(
            "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx' ORDER BY id",
            (ids[2],),
        ).fetchall()
    ]
    respuesta = cliente.post("/api/fabrica/plan", json=_solicitud_v2(solicitud, multiples))
    assert respuesta.status_code == 422
    assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone()[0] == 0


def test_crear_v2_exige_interruptor_y_target_valido_sin_mutar(escenario, monkeypatch):
    cliente, conn, solicitud, fw, ids = escenario
    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
    ).fetchone()[0]
    v2 = _solicitud_v2(solicitud, [listing])
    vista = _preview(cliente, v2)
    llamadas = []
    monkeypatch.setattr(fw.fc, "_mutar", lambda *args, **kwargs: llamadas.append(args))
    bloqueada = _crear(cliente, v2, vista["huella"])
    assert bloqueada.status_code == 409
    assert llamadas == []
    invalida = _solicitud_v2(
        solicitud,
        [listing],
        objetivo={"origen": "manual_lanzamiento", "acos_pct": "25.123"},
    )
    assert cliente.post("/api/fabrica/plan", json=invalida).status_code == 422
    assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone()[0] == 0


@pytest.mark.parametrize("valor", ["0", "70"])
def test_preview_v2_rechaza_manual_fuera_de_banda(escenario, valor):
    """A1: manual 0 y 70 por el formulario/API → 422 con el mensaje de la
    banda (el MISMO de CLI y nucleo), sin escribir lote."""
    cliente, conn, solicitud, fw, ids = escenario
    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
    ).fetchone()[0]
    v2 = _solicitud_v2(
        solicitud, [listing], objetivo={"origen": "manual_lanzamiento", "acos_pct": valor}
    )
    respuesta = cliente.post("/api/fabrica/plan", json=v2)
    assert respuesta.status_code == 422
    assert "banda" in respuesta.json()["detail"]["mensaje"]
    assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone()[0] == 0


@pytest.mark.parametrize("valor", ["0", "70"])
def test_bids_sugeridos_rechaza_manual_fuera_de_banda(escenario, valor):
    """F2 AI-review PR #381: sugerir_bids valida la banda igual que el alta
    (antes 422 en el API; sin el fix pasaba y alteraba las semillas exactas).
    Falla antes de credenciales/Amazon: sin mocks de red."""
    cliente, conn, solicitud, fw, ids = escenario
    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
    ).fetchone()[0]
    with pytest.raises(HTTPException) as exc:
        fw.sugerir_bids(
            conn,
            {
                "plataforma": "amazon_mx",
                "tipo_producto": "collar_perro",
                "listing_ids": [listing],
                "objetivo": {"origen": "manual_lanzamiento", "acos_pct": valor},
            },
        )
    assert exc.value.status_code == 422
    assert "banda" in exc.value.detail["mensaje"]


def test_bids_sugeridos_rechaza_manual_fuera_de_forma(escenario):
    """F2 AI-review PR #381 (resto): 25.123 en banda pero con 3 decimales →
    422 NUMERIC (igual que /plan), antes de tocar Amazon."""
    cliente, conn, solicitud, fw, ids = escenario
    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
    ).fetchone()[0]
    with pytest.raises(HTTPException) as exc:
        fw.sugerir_bids(
            conn,
            {
                "plataforma": "amazon_mx",
                "tipo_producto": "collar_perro",
                "listing_ids": [listing],
                "objetivo": {"origen": "manual_lanzamiento", "acos_pct": "25.123"},
            },
        )
    assert exc.value.status_code == 422
    assert "NUMERIC" in exc.value.detail["mensaje"]


def test_reenvio_v2_reordena_listings_y_no_duplica_mutacion(escenario, monkeypatch):
    cliente, conn, solicitud, fw, ids = escenario
    listing_ids = [
        fila[0]
        for fila in conn.execute(
            "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx' ORDER BY id",
            (ids[2],),
        ).fetchall()
    ]
    conn.execute(
        "INSERT INTO config_version(label, settings) VALUES ('v2', %s)",
        (Json({"ads_target_fraccion_margen_amazon_mx": "0.5", "fabrica.creacion": "v2"}),),
    )
    v2 = _solicitud_v2(solicitud, list(reversed(listing_ids)))
    vista = _preview(cliente, v2)
    llamadas = _motor_simulado(monkeypatch, fw, conn)
    primera = _crear(cliente, v2, vista["huella"])
    v2["listing_ids"] = listing_ids
    segunda = _crear(cliente, v2, vista["huella"])
    assert primera.status_code == segunda.status_code == 200
    assert primera.json() == segunda.json()
    assert llamadas == [vista["lote"]]


@pytest.mark.parametrize(
    "valor", [1.5, True, "NaN", "Infinity", "1e99999", "0", "-1", "1.001", "9999999999999"]
)
def test_montos_invalidos_se_rechazan_sin_escribir(escenario, valor):
    cliente, conn, solicitud, _, _ = escenario
    solicitud["parametros"]["category_exact"]["budget"] = valor
    r = cliente.post("/api/fabrica/plan", json=solicitud)
    assert r.status_code == 422
    assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone()[0] == 0


@pytest.mark.parametrize(
    "cambio",
    [
        {"productos": [1, 1]},
        {"productos": [True]},
        {"productos": [0]},
        {"productos": ["1"]},
        {"productos": []},
        {"modo": "off"},
        {"nombre_base": " "},
        {"tipo_producto": "BAD"},
        {"token": TOKEN},
    ],
)
def test_solicitud_invalida_no_filtra_input(escenario, cambio):
    cliente, _, solicitud, _, _ = escenario
    solicitud.update(cambio)
    r = cliente.post("/api/fabrica/plan", json=solicitud)
    assert r.status_code == 422
    assert TOKEN not in r.text


def test_auth_antes_de_conectar_admin(escenario, monkeypatch):
    cliente, _, solicitud, fw, _ = escenario

    def prohibido(*args, **kwargs):
        raise AssertionError("conexion antes de auth")

    monkeypatch.setattr(fw, "connect", prohibido)
    for path in (
        "crear",
        "lotes/web-falso/pausar",
        "lotes/web-falso/reconciliar",
        "lotes/web-falso/registrar",
    ):
        r = cliente.post(f"/api/fabrica/{path}?x-orbit-token={TOKEN}", json={})
        assert r.status_code == 401
    r = cliente.post(
        "/api/fabrica/crear",
        json={"solicitud": solicitud, "huella": "a" * 64, "confirmacion": "CREAR 5 CAMPAÑAS"},
    )
    assert r.status_code == 401


def test_huella_obsoleta_y_confirmacion_invalida_no_crean(escenario):
    cliente, conn, solicitud, _, _ = escenario
    vista = _preview(cliente, solicitud)
    solicitud["nombre_base"] = "Otro nombre"
    r = _crear(cliente, solicitud, vista["huella"])
    assert r.status_code == 409 and r.json()["detail"]["lote"] == vista["lote"]
    r = cliente.post(
        "/api/fabrica/crear",
        headers=HEADERS,
        json={"solicitud": solicitud, "huella": vista["huella"], "confirmacion": "crear"},
    )
    assert r.status_code == 422
    assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone()[0] == 0


def test_reenvio_consulta_lote_durable_aun_si_cambian_datos(escenario, monkeypatch):
    cliente, conn, solicitud, fw, _ = escenario
    vista = _preview(cliente, solicitud)
    llamadas = _motor_simulado(monkeypatch, fw, conn)
    primero = _crear(cliente, solicitud, vista["huella"])
    assert primero.status_code == 200, primero.text
    conn.execute("INSERT INTO config_version(label, settings) VALUES ('sin config', '{}')")
    segundo = _crear(cliente, solicitud, vista["huella"])
    assert segundo.status_code == 200 and segundo.json() == primero.json()
    assert llamadas == [vista["lote"]]
    assert primero.json()["pasos"][0]["external_id"] == "externo-1"
    assert "ACK NO PUBLICO" not in primero.text and TOKEN not in primero.text
    items = cliente.get("/api/fabrica/lotes?plataforma=amazon_mx").json()["items"]
    assert len(items) == 1 and "pasos" not in items[0]


def test_error_conserva_lote_y_reenvio_no_recrea(escenario, monkeypatch):
    cliente, conn, solicitud, fw, _ = escenario
    vista = _preview(cliente, solicitud)
    llamadas = _motor_simulado(monkeypatch, fw, conn, error=True)
    r = _crear(cliente, solicitud, vista["huella"])
    assert r.status_code == 409
    assert r.json()["detail"]["lote"] == vista["lote"]
    assert TOKEN not in r.text and "ACK NO PUBLICO" not in r.text
    detalle = cliente.get("/api/fabrica/lotes/" + vista["lote"])
    assert detalle.status_code == 200 and detalle.json()["estado"] == "failed"
    assert TOKEN not in detalle.text and "ACK NO PUBLICO" not in detalle.text
    assert _crear(cliente, solicitud, vista["huella"]).status_code == 200
    assert llamadas == [vista["lote"]]


def test_fallo_previo_a_insert_no_pierde_intento(escenario, monkeypatch):
    cliente, conn, solicitud, fw, _ = escenario
    vista = _preview(cliente, solicitud)

    def falla(*args, **kwargs):
        raise OSError("credencial privada")

    monkeypatch.setattr(fw.fc, "_mutar", falla)
    r = _crear(cliente, solicitud, vista["huella"])
    assert r.status_code == 503
    assert conn.execute("SELECT estado FROM fabrica_lote").fetchone()[0] == "failed"
    assert cliente.get("/api/fabrica/lotes/" + vista["lote"]).status_code == 200


def test_lock_sesion_serializa_creacion_y_todas_las_acciones(escenario, monkeypatch):
    cliente, conn, solicitud, fw, _ = escenario
    vista = _preview(cliente, solicitud)
    dentro = threading.Event()
    liberar = threading.Event()

    def bloqueado(args, plan, huella, *, lote):
        fw.fc._inserta_lote(conn, lote, plan, huella, args.go)
        dentro.set()
        assert liberar.wait(8)
        fw.fc._sella_lote(conn, lote, "applied", None)
        return 0

    monkeypatch.setattr(fw.fc, "_mutar", bloqueado)
    with ThreadPoolExecutor(max_workers=1) as pool:
        futuro = pool.submit(_crear, cliente, solicitud, vista["huella"])
        try:
            assert dentro.wait(8)
            assert _crear(cliente, solicitud, vista["huella"]).status_code == 409
            for accion in ("pausar", "reconciliar", "registrar"):
                r = cliente.post(
                    f"/api/fabrica/lotes/{vista['lote']}/{accion}",
                    headers=HEADERS,
                    json={"confirmacion": accion.upper() + " GRUPO"},
                )
                assert r.status_code == 409
        finally:
            liberar.set()
        assert futuro.result(timeout=8).status_code == 200
    assert _crear(cliente, solicitud, vista["huella"]).status_code == 200


def test_acciones_reutilizan_motor_y_desarmado_no_resucita(escenario, monkeypatch):
    cliente, conn, solicitud, fw, _ = escenario
    vista = _preview(cliente, solicitud)
    llamadas = _motor_simulado(monkeypatch, fw, conn, error=True)
    _crear(cliente, solicitud, vista["huella"])
    ejecutadas = []

    def reconciliar(args):
        ejecutadas.append(("reconciliar", args.lote, args.plataforma))
        return 0

    def registrar(args):
        ejecutadas.append(("registrar", args.registrar))
        fw.fc._sella_lote(conn, args.registrar, "applied", None)
        return 0

    def pausar(args):
        ejecutadas.append(("pausar", args.desarmar, args.acepto_mutacion_real))
        fw.fc._sella_lote(conn, args.desarmar, "desarmado", None)
        return 0

    monkeypatch.setattr(fw.fc, "_reconciliar_cmd", reconciliar)
    monkeypatch.setattr(fw.fc, "_registrar_cmd", registrar)
    monkeypatch.setattr(fw.fc, "_desarmar", pausar)
    for accion in ("reconciliar", "registrar", "pausar"):
        r = cliente.post(
            f"/api/fabrica/lotes/{vista['lote']}/{accion}",
            headers=HEADERS,
            json={"confirmacion": accion.upper() + " GRUPO"},
        )
        assert r.status_code == 200, r.text
    assert len(ejecutadas) == 3 and llamadas == [vista["lote"]]
    for accion in ("reconciliar", "registrar"):
        r = cliente.post(
            f"/api/fabrica/lotes/{vista['lote']}/{accion}",
            headers=HEADERS,
            json={"confirmacion": accion.upper() + " GRUPO"},
        )
        assert r.status_code == 409
    assert len(ejecutadas) == 3


def test_lote_inexistente_404_y_plataforma_invalida_422(escenario):
    cliente, _, _, _, _ = escenario
    assert cliente.get("/api/fabrica/lotes/no-existe").status_code == 404
    for ruta in ("catalogo", "lotes"):
        assert cliente.get(f"/api/fabrica/{ruta}?plataforma=mercadolibre").status_code == 422


def test_router_fabrica_publicado():
    _modulos()


def test_endpoint_bids_sugeridos_valida_entrada_y_devuelve_motor(escenario, monkeypatch):
    cliente, _, _, fw, ids = escenario
    visto = []

    def sugerir(conn, solicitud):
        visto.append(solicitud)
        return {"fuente": "amazon_v4", "roles": {"category_exact": {"bid": "9.80"}}}

    monkeypatch.setattr(fw, "sugerir_bids", sugerir)
    respuesta = cliente.post(
        "/api/fabrica/bids-sugeridos",
        json={
            "plataforma": "amazon_mx",
            "tipo_producto": "collar_perro",
            "listing_ids": [ids[0]],
            "objetivo": {"origen": "manual_lanzamiento", "acos_pct": "25.00"},
        },
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["roles"]["category_exact"]["bid"] == "9.80"
    assert visto[0]["listing_ids"] == [ids[0]]


def test_preview_firma_procedencia_y_terna_amazon(escenario):
    cliente, _, solicitud, _, _ = escenario
    solicitud["parametros"]["category_phrase"].update(
        {
            "fuente_bid": "amazon_v4",
            "recomendaciones": [
                {
                    "tipo": "KEYWORD_PHRASE_MATCH",
                    "valor": "collar",
                    "minimo": "3.00",
                    "sugerido": "4.00",
                    "maximo": "5.00",
                }
            ],
        }
    )
    vista = _preview(cliente, solicitud)
    parametro = vista["plan"]["parametros"]["category_phrase"]
    assert parametro["fuente_bid"] == "amazon_v4"
    assert parametro["recomendaciones"][0]["sugerido"] == "4.00"
    frase = next(c for c in vista["campanas"] if c["rol"] == "category_phrase")
    assert frase["fuente_bid"] == "amazon_v4"
    assert vista["bids"]["category_phrase"] == [
        {
            "tipo": "KEYWORD_PHRASE_MATCH",
            "valor": "collar",
            "minimo": "3.00",
            "sugerido": "4.00",
            "maximo": "5.00",
            "bid_efectivo": "4.00",
            "fuente": "amazon_v4",
        }
    ]


def test_preview_v1_rechaza_cobertura_obsoleta_como_422(escenario):
    cliente, _, solicitud, _, _ = escenario
    solicitud["parametros"]["category_phrase"].update(
        {
            "fuente_bid": "amazon_v4",
            "recomendaciones": [
                {
                    "tipo": "KEYWORD_PHRASE_MATCH",
                    "valor": "semilla anterior",
                    "minimo": "3.00",
                    "sugerido": "4.00",
                    "maximo": "5.00",
                }
            ],
        }
    )

    respuesta = cliente.post("/api/fabrica/plan", json=solicitud)

    assert respuesta.status_code == 422
    assert "semillas vigentes" in respuesta.json()["detail"]["mensaje"]


@pytest.mark.parametrize("plan_ilegible", [{"schema_version": 2}, ["no es objeto"], {}, []])
def test_detalle_lote_con_plan_ilegible_conserva_estado_y_pasos(escenario, plan_ilegible):
    cliente, conn, _, _, _ = escenario
    conn.execute(
        "INSERT INTO fabrica_lote"
        " (lote,platform,tipo_producto,nombre_base,go_literal,huella,plan,modo_goal,estado)"
        " VALUES ('plan-ilegible','amazon_mx','collar_perro','Collar','go','sha',%s,"
        " 'shadow','failed')",
        (Json(plan_ilegible),),
    )
    conn.execute(
        "INSERT INTO fabrica_lote_paso(lote,orden,rol,recurso,request_payload,estado)"
        " VALUES ('plan-ilegible',1,'category_exact','campaign','{}','failed')"
    )
    conn.commit()

    respuesta = cliente.get("/api/fabrica/lotes/plan-ilegible")

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "failed"
    assert respuesta.json()["plan"] is None
    assert respuesta.json()["bids"] is None
    assert len(respuesta.json()["pasos"]) == 1


def test_bids_sugeridos_reales_dejan_manual_un_rol_sin_objetivos(escenario, monkeypatch):
    cliente, conn, _, fw, ids = escenario
    listing_id = conn.execute("SELECT id FROM listing WHERE product_id = %s", (ids[0],)).fetchone()[
        0
    ]

    class Respuesta:
        def __init__(self, cuerpo):
            self.cuerpo = cuerpo

        def json(self):
            filas = []
            for expresion in self.cuerpo["targetingExpressions"]:
                sugerido = {
                    "CLOSE_MATCH": "2.00",
                    "LOOSE_MATCH": "4.00",
                    "SUBSTITUTES": "6.00",
                    "COMPLEMENTS": "8.00",
                }.get(expresion["type"], "4.00")
                filas.append(
                    {
                        "targetingExpression": expresion,
                        "bidValues": [
                            {"suggestedBid": sugerido},
                            {"suggestedBid": sugerido},
                            {"suggestedBid": sugerido},
                        ],
                    }
                )
            return {"bidRecommendations": [{"bidRecommendationsForTargetingExpressions": filas}]}

    class ClienteAds:
        @staticmethod
        def recommend_bids(cuerpo, *, profile_id):
            assert profile_id == 101
            return Respuesta(cuerpo)

    monkeypatch.setattr(fw.AdsCredentials, "from_secrets_dir", lambda: object())
    monkeypatch.setattr(fw, "AdsClient", lambda credenciales: ClienteAds())
    monkeypatch.setattr(fw.fc, "_perfiles", lambda cliente_ads: {"amazon_mx": 101})
    respuesta = cliente.post(
        "/api/fabrica/bids-sugeridos",
        json={
            "plataforma": "amazon_mx",
            "tipo_producto": "collar_perro",
            "listing_ids": [listing_id],
            "objetivo": {"origen": "manual_lanzamiento", "acos_pct": "25.00"},
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    roles = respuesta.json()["roles"]
    assert roles["category_phrase"]["bid"] == "4.00"
    assert roles["category_phrase"]["recomendaciones"][0]["bid_efectivo"] == "4.00"
    assert roles["auto_discovery"]["bid"] == "5.00"
    assert {r["bid_efectivo"] for r in roles["auto_discovery"]["recomendaciones"]} == {"5.00"}
    assert roles["category_exact"]["disponible"] is False
    assert roles["product_targeting"]["disponible"] is False


def test_detalle_incierto_advierte_no_recrear_y_no_expone_respuesta(escenario, monkeypatch):
    cliente, conn, solicitud, fw, _ = escenario
    vista = _preview(cliente, solicitud)

    def incierto(args, plan, huella, *, lote):
        fw.fc._inserta_lote(conn, lote, plan, huella, args.go)
        conn.execute(
            "INSERT INTO fabrica_lote_paso(lote,orden,rol,recurso,request_payload,estado) "
            "VALUES (%s,1,'category_exact','campaign','{}','planeado')",
            (lote,),
        )
        raise fw.fc.Abortar("respuesta externa privada")

    monkeypatch.setattr(fw.fc, "_mutar", incierto)
    assert _crear(cliente, solicitud, vista["huella"]).status_code == 409
    detalle = cliente.get("/api/fabrica/lotes/" + vista["lote"]).json()
    assert "incierto" in detalle["detalle"].lower()
    assert "consola" in detalle["detalle"].lower()
    assert "repetir" in detalle["detalle"].lower()
    listado = cliente.get("/api/fabrica/lotes?plataforma=amazon_mx").json()
    assert listado["items"][0]["detalle"] == detalle["detalle"]


def test_fallo_sin_pasos_informa_revision_configuracion(escenario, monkeypatch):
    cliente, _, solicitud, fw, _ = escenario
    vista = _preview(cliente, solicitud)

    def falla(*args, **kwargs):
        raise OSError("configuracion privada")

    monkeypatch.setattr(fw.fc, "_mutar", falla)
    _crear(cliente, solicitud, vista["huella"])
    r = cliente.get("/api/fabrica/lotes/" + vista["lote"])
    assert "configuración" in r.json()["detalle"].lower()
    assert "no se registraron pasos" in r.json()["detalle"].lower()


def test_setting_ausente_es_503_y_tipo_nuevo_es_valido(escenario):
    cliente, conn, solicitud, _, _ = escenario
    solicitud["tipo_producto"] = "tipo_nuevo"
    assert _preview(cliente, solicitud)["plan"]["tipo_producto"] == "tipo_nuevo"
    conn.execute("INSERT INTO config_version(label,settings) VALUES ('sin setting','{}')")
    r = cliente.post("/api/fabrica/plan", json=solicitud)
    assert r.status_code == 503


def test_configuracion_motor_ausente_es_503_con_lote_recuperable(escenario, monkeypatch):
    cliente, _, solicitud, fw, _ = escenario
    vista = _preview(cliente, solicitud)

    def sin_configuracion(*args, **kwargs):
        raise fw.fc.Abortar("ORBIT_DSN_INGEST no esta en el entorno")

    monkeypatch.setattr(fw.fc, "_mutar", sin_configuracion)
    r = _crear(cliente, solicitud, vista["huella"])
    assert r.status_code == 503
    assert r.json()["detail"]["lote"] == vista["lote"]
    assert cliente.get("/api/fabrica/lotes/" + vista["lote"]).status_code == 200


def _listing_mx(conn, product_id):
    return conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx' ORDER BY id",
        (product_id,),
    ).fetchone()[0]


def _escenario_leido(listing_id, **overrides):
    from decimal import Decimal

    from app.estimacion_repository import EscenarioLeido

    datos = dict(
        id=99,
        listing_id=listing_id,
        canal="fba",
        valoracion_date=dt.date(2026, 9, 8),
        observed_at=dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.UTC),
        estado="disponible",
        motivos=(),
        contribucion=Decimal("42.5000"),
        contribucion_pct=Decimal("36.6379"),
        moneda="MXN",
        componentes=[],
        exclusiones=(),
        canonical_input={
            "resultado": {
                "estado": "disponible",
                "contribucion": "42.5000",
                "contribucion_pct": "36.6379",
            }
        },
        context_fingerprint="fp-api",
        politica_version_id=3,
        formula_version="S3",
    )
    datos.update(overrides)
    return EscenarioLeido(**datos)


def test_catalogo_y_evaluacion_estimacion_mismo_snapshot(escenario, monkeypatch):
    cliente, conn, _, fw, ids = escenario
    lid = _listing_mx(conn, ids[0])
    as_of = dt.datetime(2026, 9, 8, 18, 0, tzinfo=dt.UTC)
    llamadas = []

    def fake_leer(_conn, listing_ids, *, as_of):
        llamadas.append((tuple(listing_ids), as_of))
        return [_escenario_leido(lid)] if lid in listing_ids else []

    monkeypatch.setattr("app.estimacion_proyeccion.leer_escenarios", fake_leer)

    class _Reloj(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return as_of

    monkeypatch.setattr(fw.dt, "datetime", _Reloj)
    cat = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx")
    eva = cliente.get("/api/fabrica/evaluacion?plataforma=amazon_mx")
    assert cat.status_code == eva.status_code == 200, (cat.text, eva.text)
    assert cat.json()["as_of"] == as_of.isoformat()
    assert eva.json()["as_of"] == as_of.isoformat()
    pub_cat = next(
        p for prod in cat.json()["productos"] for p in prod["publicaciones"] if p["id"] == lid
    )
    pub_eva = next(p for p in eva.json()["publicaciones"] if p["listing_id"] == lid)
    for pub in (pub_cat, pub_eva):
        est = pub["estimacion"]
        assert est["snapshot_id"] == 99
        assert est["estado"] == "disponible"
        assert est["contribucion"] == "42.5000"
        assert est["contribucion_pct"] == "36.6379"
    assert pub_cat["estimacion"]["snapshot_id"] == pub_eva["estimacion"]["snapshot_id"]
    assert pub_cat["estimacion"]["estado"] == pub_eva["estimacion"]["estado"]
    assert pub_cat["estimacion"]["contribucion"] == pub_eva["estimacion"]["contribucion"]
    assert pub_cat["margen_neto_pct"] == "40.00000000000000000"
    assert "economia" in pub_eva
    assert [as_of for _ids, as_of in llamadas] == [as_of, as_of]


def test_evaluacion_reutiliza_as_of_de_catalogo(escenario, monkeypatch):
    """El cliente puede fijar el mismo corte sin depender de datetime.now.

    La ventana Ads sigue el reloj del segundo GET, no el as_of historico.
    """
    cliente, conn, _, fw, ids = escenario
    lid = _listing_mx(conn, ids[0])
    corte_catalogo = dt.datetime(2026, 9, 8, 18, 0, tzinfo=dt.UTC)
    reloj_eva = dt.datetime(2026, 9, 10, 19, 0, tzinfo=dt.UTC)
    llamadas = []

    def fake_leer(_conn, listing_ids, *, as_of):
        llamadas.append(as_of)
        return [_escenario_leido(lid)] if lid in listing_ids else []

    monkeypatch.setattr("app.estimacion_proyeccion.leer_escenarios", fake_leer)

    class _Reloj(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return corte_catalogo

    monkeypatch.setattr(fw.dt, "datetime", _Reloj)
    cat = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx")
    assert cat.status_code == 200
    as_of_iso = cat.json()["as_of"]
    monkeypatch.setattr(
        fw.dt,
        "datetime",
        type(
            "_Reloj2",
            (dt.datetime,),
            {"now": classmethod(lambda cls, tz=None: reloj_eva)},
        ),
    )
    eva = cliente.get(
        "/api/fabrica/evaluacion",
        params={"plataforma": "amazon_mx", "as_of": as_of_iso},
    )
    assert eva.status_code == 200, eva.text
    assert eva.json()["as_of"] == as_of_iso
    hoy = reloj_eva.date()
    hasta = hoy - dt.timedelta(days=1)
    desde = hasta - dt.timedelta(days=30)
    assert eva.json()["ventana_ads"] == {
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
    }
    assert llamadas == [corte_catalogo, corte_catalogo]


def test_get_estimacion_decimal_como_cadena_y_null_se_queda_null(escenario, monkeypatch):
    cliente, conn, _, _, ids = escenario
    lid = _listing_mx(conn, ids[0])
    lid_sin = _listing_mx(conn, ids[1])

    def fake_leer(_conn, listing_ids, *, as_of):
        return [_escenario_leido(lid)] if lid in listing_ids else []

    monkeypatch.setattr("app.estimacion_proyeccion.leer_escenarios", fake_leer)
    data = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx").json()
    por_id = {p["id"]: p["estimacion"] for prod in data["productos"] for p in prod["publicaciones"]}
    assert por_id[lid]["contribucion"] == "42.5000"
    assert isinstance(por_id[lid]["contribucion"], str)
    assert por_id[lid_sin]["contribucion"] is None
    assert por_id[lid_sin]["estado"] == "incompleta"
    assert por_id[lid_sin]["motivos"] == ["escenario_ausente"]


def test_get_estimacion_no_llama_http_oauth_ni_write(escenario, monkeypatch):
    cliente, _, _, fw, _ = escenario

    def prohibido(*args, **kwargs):
        raise AssertionError("GET no debe llamar red ni escritura")

    monkeypatch.setattr(fw.fc, "_mutar", prohibido)
    monkeypatch.setattr("app.estimacion_repository.persistir_escenario", prohibido)
    monkeypatch.setattr("app.estimacion_fees.ProductFeesClient", prohibido)
    cat = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx")
    eva = cliente.get("/api/fabrica/evaluacion?plataforma=amazon_mx")
    assert cat.status_code == eva.status_code == 200
    assert "estimacion" in cat.json()["productos"][0]["publicaciones"][0]
    assert "estimacion" in eva.json()["publicaciones"][0]


def test_get_estimacion_leer_escenarios_una_vez_por_request(escenario, monkeypatch):
    cliente, _, _, _, _ = escenario
    llamadas = []

    def fake_leer(_conn, listing_ids, *, as_of):
        llamadas.append(list(listing_ids))
        return []

    monkeypatch.setattr("app.estimacion_proyeccion.leer_escenarios", fake_leer)
    cat = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx")
    eva = cliente.get("/api/fabrica/evaluacion?plataforma=amazon_mx")
    assert cat.status_code == eva.status_code == 200
    assert len(llamadas) == 2
    assert len(llamadas[0]) >= 1
    assert len(llamadas[1]) >= 1


def test_catalogo_estimacion_stub_sin_fila(escenario):
    cliente, _, _, _, _ = escenario
    data = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx").json()
    for prod in data["productos"]:
        for pub in prod["publicaciones"]:
            est = pub["estimacion"]
            assert est["estado"] == "incompleta"
            assert est["motivos"] == ["escenario_ausente"]
            assert est["snapshot_id"] is None
            assert est["contribucion"] is None
            assert est["base_porcentaje"] == "ingreso_normalizado"


# ---------------------------------------------------------------------------
# A7 B8: manual no-finito con mensaje que dice "no es finito"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    ["NaN", "nan", "Infinity", "-Infinity", float("nan"), float("inf"), float("-inf")],
)
def test_objetivo_manual_no_finito_dice_no_es_finito(valor):
    """A7 B8: str y float no-finitos caen con el MISMO mensaje (los floats
    son el delta: en trunk daban el generico de Pydantic)."""
    from pydantic import ValidationError

    api_fabrica, _ = _modulos()
    with pytest.raises(ValidationError) as exc:
        api_fabrica.ObjetivoPlan(origen="manual_lanzamiento", acos_pct=valor)
    assert "no es finito" in str(exc.value)


@pytest.mark.parametrize("valor", ["20.5", 20.5, 20])
def test_objetivo_manual_finito_pasa_en_str_y_float(valor):
    """A7 B8: el finito pasa en ambas formas (el float finito aguas abajo
    se valida via str en _decimal, sin expansion binaria)."""
    api_fabrica, _ = _modulos()
    objetivo = api_fabrica.ObjetivoPlan(origen="manual_lanzamiento", acos_pct=valor)
    assert objetivo.acos_pct == valor


def test_objetivo_manual_exige_acos_pct_y_decimal():
    """A7 B8: None exige acos_pct; texto no numerico no es decimal."""
    from pydantic import ValidationError

    api_fabrica, _ = _modulos()
    with pytest.raises(ValidationError) as exc:
        api_fabrica.ObjetivoPlan(origen="manual_lanzamiento", acos_pct=None)
    assert "exige acos_pct" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        api_fabrica.ObjetivoPlan(origen="manual_lanzamiento", acos_pct="abc")
    assert "no es decimal" in str(exc.value)
    medido = api_fabrica.ObjetivoPlan(origen="margen_medido", acos_pct=None)
    assert medido.acos_pct is None


@pytest.mark.parametrize("valor", [float("nan"), float("inf")])
def test_crear_manual_no_finito_422_sin_filas(escenario, valor):
    """A7 B8: POST crear con NaN/Infinity JSON numerico -> 422 y 0 filas
    (el status ya pasaba en trunk; el MENSAJE del modelo discrimina)."""
    cliente, conn, solicitud, _, ids = escenario
    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
    ).fetchone()[0]
    v2 = _solicitud_v2(
        solicitud, [listing], objetivo={"origen": "manual_lanzamiento", "acos_pct": valor}
    )
    # TestClient serializa con allow_nan=False: el NaN/Infinity del form roto
    # viaja como cuerpo crudo (el servidor lo parsea con json.loads).
    cuerpo = json.dumps({"solicitud": v2, "huella": "0" * 64, "confirmacion": "CREAR 5 CAMPAÑAS"})
    respuesta = cliente.post(
        "/api/fabrica/crear",
        headers={**HEADERS, "Content-Type": "application/json"},
        content=cuerpo,
    )
    assert respuesta.status_code == 422
    assert "no es finito" in respuesta.text  # discrimina: en trunk era "valid string"
    assert conn.execute("SELECT count(*) FROM fabrica_lote").fetchone()[0] == 0


def test_crear_error_ajeno_sigue_generico_sin_input(escenario):
    """A7 B8: el passthrough del "no es finito" no abre la puerta: otros
    errores de validacion siguen con el generico y sin input crudo."""
    cliente, conn, solicitud, _, _ids = escenario
    v2 = _solicitud_v2(solicitud, [], objetivo={"origen": "margen_medido"})
    respuesta = cliente.post(
        "/api/fabrica/crear",
        headers=HEADERS,
        json={"solicitud": v2, "huella": "0" * 64, "confirmacion": "CREAR 5 CAMPAÑAS"},
    )
    assert respuesta.status_code == 422
    assert "Los datos no son válidos" in respuesta.text
    assert "margen_medido" not in respuesta.text


# ---------------------------------------------------------------------------
# A7 B5: procedencia del target a 2 decimales (post-huella, fabrica.js intacto)
# ---------------------------------------------------------------------------


def test_procedencia_dos_dec_fija_los_3_formatos():
    """A7 B5: pura sobre crudos REALES de target_del_grupo: medida larga,
    clamp (el "[10, 45]" sin punto queda intacto, igual que enteros) y
    manual (sin numeros, intacta)."""
    from decimal import Decimal

    _, fw = _modulos()
    crudo = fp.target_del_grupo([Decimal("38.21")], Decimal("0.5")).procedencia
    assert crudo == "margen_minimo_grupo: 0.5 x 38.21 = 19.105 -> redondeo NUMERIC(6,2) = 19.10"
    assert fw._procedencia_dos_dec(crudo) == (
        "margen_minimo_grupo: 0.50 x 38.21 = 19.10 -> redondeo NUMERIC(6,2) = 19.10"
    )
    clamp = fp.target_del_grupo([Decimal("5")], Decimal("0.5")).procedencia
    assert clamp == "margen_minimo_grupo: 0.5 x 5 = 2.5 -> clamp [10, 45] = 10"
    assert (
        fw._procedencia_dos_dec(clamp)
        == "margen_minimo_grupo: 0.50 x 5 = 2.50 -> clamp [10, 45] = 10"
    )
    assert (
        fw._procedencia_dos_dec("manual_lanzamiento confirmado") == "manual_lanzamiento confirmado"
    )


def test_preview_formatea_procedencia_y_huella_sigue_valida(escenario, monkeypatch):
    """A7 B5: el preview publica la procedencia a 2 decimales (v1 y v2
    medida) y la huella sigue valida para crear (formato post-huella
    sobre la copia serializada: huella-neutral por construccion)."""
    import re

    cliente, conn, solicitud, fw, ids = escenario
    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO config_version(label, settings) VALUES ('v2', %s)",
        (Json({"ads_target_fraccion_margen_amazon_mx": "0.5", "fabrica.creacion": "v2"}),),
    )
    v2 = _solicitud_v2(solicitud, [listing], objetivo={"origen": "margen_medido"})
    vista = _preview(cliente, v2)
    proc = vista["plan"]["objetivo"]["procedencia"]
    assert proc == "margen_minimo_grupo: 0.50 x 40.00 = 20.00"
    for token in re.findall(r"\d+\.\d+", proc):
        assert re.fullmatch(r"\d+\.\d{2}", token), token
    v1 = _preview(cliente, solicitud)
    assert v1["plan"]["target_procedencia"] == "margen_minimo_grupo: 0.50 x 40.00 = 20.00"
    llamadas = _motor_simulado(monkeypatch, fw, conn)
    respuesta = _crear(cliente, v2, vista["huella"])
    assert respuesta.status_code == 200, respuesta.text
    assert llamadas == [vista["lote"]]


# ---------------------------------------------------------------------------
# JEV 2.2: export del preview (plan + huella + fuentes de semillas) y
# asesoria visible por huella (lectura pura; la biblioteca queda intacta)
# ---------------------------------------------------------------------------


def _ficha_jev_fabrica(conn, producto: int, listing: int) -> object:
    import uuid as _uuid

    ficha_id = _uuid.uuid4()
    conn.execute(
        "INSERT INTO jev_ficha_version (id, producto_id, plataforma, listings,"
        " hechos, desconocidos, sha256, aprobador, observado_at, revisar_antes_de)"
        " VALUES (%s, %s, 'amazon_mx', ARRAY[%s], %s::jsonb, '{}', %s, 'aprobador',"
        " now(), now() + interval '120 days')",
        (
            ficha_id,
            producto,
            listing,
            json.dumps([{"texto": "hecho", "fuente": "fuente"}]),
            "f" * 64,
        ),
    )
    return ficha_id


def _censo_fabrica(conn, producto: int):
    from datetime import UTC, datetime

    from app.jev_ads import CensoCongelado, EstadoAnuncio, MiembroCenso

    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'",
        (producto,),
    ).fetchone()[0]
    ficha_id = _ficha_jev_fabrica(conn, producto, listing)
    miembro = MiembroCenso(
        anuncio_ids=(listing,),
        producto_id=producto,
        listing_ids=frozenset({listing}),
        estados=(EstadoAnuncio("ENABLED", datetime.now(UTC)),),
        ficha_version_id=ficha_id,
    )
    return CensoCongelado(miembros=(miembro,), exhaustivo=True), ficha_id


def test_export_semillas_trae_plan_huella_y_fuentes_sin_tocar_biblioteca(escenario):
    """DoD 2.2: el export contiene plan, huella y filas fuente de semillas
    (biblioteca heredada incluida); exportar no crea ni elimina keywords ni
    modifica la biblioteca."""
    cliente, conn, solicitud, fw, ids = escenario
    conn.execute(
        "INSERT INTO negative_biblioteca(tipo_producto, platform, texto, origen)"
        " VALUES ('collar_perro', 'amazon_mx', 'antipulgas', 'manual')"
    )
    conteos_antes = {
        tabla: conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
        for tabla in ("keyword_biblioteca", "negative_biblioteca")
    }
    export = cliente.post("/api/fabrica/export-semillas", json=solicitud)
    assert export.status_code == 200, export.text
    datos = export.json()
    preview = _preview(cliente, solicitud)
    assert datos["huella"] == preview["huella"]
    assert datos["plan_sha256"] == datos["huella"]
    assert datos["plan_canonico"] == preview["plan"]
    assert datos["fuentes_semillas"]["keywords_biblioteca"] == ["collar"]
    # G4-4: todas las fuentes, no solo la biblioteca de keywords.
    assert datos["fuentes_semillas"]["negativos_biblioteca"] == ["antipulgas"]
    assert isinstance(datos["fuentes_semillas"]["terminos_vendedores"], list)
    assert isinstance(datos["fuentes_semillas"]["terminos_exactos"], list)
    assert set(datos["terminos_a_cotejar"]) == {"collar", "antipulgas"}
    # G4-6 (R2): el rol de cada termino viaja con el; y los listings del plan.
    assert datos["roles_terminos"] == {"antipulgas": ["negativo"], "collar": ["keyword"]}
    assert datos["listings"] == sorted(
        fila[0]
        for fila in conn.execute(
            "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
        ).fetchall()
    )
    assert datos["plataforma"] == "amazon_mx"
    assert datos["productos"] == [ids[0]]
    assert (
        conn.execute(
            "SELECT count(*) FROM jev_revision WHERE plan_sha256 = %s",
            (datos["huella"],),
        ).fetchone()[0]
        == 0
    )
    assert {
        tabla: conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
        for tabla in ("keyword_biblioteca", "negative_biblioteca")
    } == conteos_antes


def test_asesoria_por_huella_cambia_con_el_plan_y_conserva_fuentes(escenario):
    """DoD 2.2: el negativo heredado se coteja con los productos NUEVOS; la
    misma huella conserva las fuentes congeladas; cambiar parametros cambia
    la huella y la revision visible; el GET no escribe."""
    import uuid as _uuid

    from app.jev_ads import SemillasARevisar
    from app.jev_asesor import AsesorAds

    cliente, conn, solicitud, fw, ids = escenario
    conn.execute(
        "INSERT INTO negative_biblioteca(tipo_producto, platform, texto, origen)"
        " VALUES ('collar_perro', 'amazon_mx', 'antipulgas', 'manual')"
    )
    export = cliente.post("/api/fabrica/export-semillas", json=solicitud).json()
    censo, ficha_id = _censo_fabrica(conn, ids[0])

    def pedir_satisface(termino, ficha):
        import uuid as _uuid2
        from decimal import Decimal

        from app.jev_ads import ClavePar, Juicio
        from app.jev_juicios import ResultadoPar

        return ResultadoPar(
            juicio=Juicio(
                intento_id=_uuid2.uuid4(),
                clave=ClavePar("a" * 64, ficha.id, "b" * 64),
                relacion="satisface",
                probabilidades={
                    "satisface": Decimal("0.70"),
                    "no_satisface": Decimal("0.20"),
                    "informacion_insuficiente": Decimal("0.10"),
                },
                confidence=Decimal("0.80"),
                observado_at=NOW_FABRICA,
            ),
            usage=None,
            duracion_ms=5,
        )

    asesor = AsesorAds(
        conn, pedir=pedir_satisface, api_key="k", presupuesto=5, ahora=lambda: NOW_FABRICA
    )
    revision = asesor.evaluar(
        SemillasARevisar(
            plan_sha256=export["plan_sha256"],
            plan_canonico=export["plan_canonico"],
            fuentes_semillas=export["fuentes_semillas"],
            terminos=tuple(export["terminos_a_cotejar"]),
            censo=censo,
            plataforma="amazon_mx",
        ),
        solicitud_id=_uuid.uuid4(),
    )
    assert revision.presupuesto_agotado is False

    respuesta = cliente.get(f"/api/fabrica/asesoria/{export['huella']}")
    assert respuesta.status_code == 200, respuesta.text
    asesoria = respuesta.json()["asesoria"]
    assert asesoria["sujeto"] == "semillas"
    assert asesoria["plan_sha256"] == export["huella"]
    por_termino = {r["termino"]: r["resultado"] for r in asesoria["resultados"]}
    assert set(por_termino) == {"collar", "antipulgas"}
    assert por_termino["collar"]["tipo"] == "hay_compatible"
    assert asesoria["fuentes_semillas"] == export["fuentes_semillas"]

    # Repetir la misma huella conserva las fuentes congeladas (append-only:
    # una revision nueva con las MISMAS fuentes, la vieja intacta).
    conteo_revisiones = conn.execute(
        "SELECT count(*) FROM jev_revision WHERE plan_sha256 = %s",
        (export["huella"],),
    ).fetchone()[0]
    asesor.evaluar(
        SemillasARevisar(
            plan_sha256=export["plan_sha256"],
            plan_canonico=export["plan_canonico"],
            fuentes_semillas=export["fuentes_semillas"],
            terminos=tuple(export["terminos_a_cotejar"]),
            censo=censo,
            plataforma="amazon_mx",
        ),
        solicitud_id=_uuid.uuid4(),
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM jev_revision WHERE plan_sha256 = %s",
            (export["huella"],),
        ).fetchone()[0]
        == conteo_revisiones + 1
    )
    fuentes_guardadas = conn.execute(
        "SELECT fuentes_semillas FROM jev_revision WHERE plan_sha256 = %s"
        " ORDER BY created_at DESC LIMIT 1",
        (export["huella"],),
    ).fetchone()[0]
    assert fuentes_guardadas == export["fuentes_semillas"]

    # Cambiar un parametro cambia la huella: esa revision visible es OTRA
    # (404 hasta que alguien la revise).
    otra_solicitud = dict(solicitud)
    otra_solicitud["parametros"] = {
        rol: {"budget": "200.00", "bid": "4.00"} for rol in fp.ROLES_ORDEN_CREACION
    }
    export_2 = cliente.post("/api/fabrica/export-semillas", json=otra_solicitud).json()
    assert export_2["huella"] != export["huella"]
    assert cliente.get(f"/api/fabrica/asesoria/{export_2['huella']}").status_code == 404
    asesor.evaluar(
        SemillasARevisar(
            plan_sha256=export_2["plan_sha256"],
            plan_canonico=export_2["plan_canonico"],
            fuentes_semillas=export_2["fuentes_semillas"],
            terminos=tuple(export_2["terminos_a_cotejar"]),
            censo=censo,
            plataforma="amazon_mx",
        ),
        solicitud_id=_uuid.uuid4(),
    )
    respuesta_2 = cliente.get(f"/api/fabrica/asesoria/{export_2['huella']}")
    assert respuesta_2.status_code == 200
    assert respuesta_2.json()["asesoria"]["plan_sha256"] == export_2["huella"]

    # El GET no escribe: los conteos de revisiones/eventos no cambian.
    conteos = {
        tabla: conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
        for tabla in ("jev_revision", "jev_par_evento")
    }
    cliente.get(f"/api/fabrica/asesoria/{export['huella']}")
    assert {
        tabla: conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
        for tabla in ("jev_revision", "jev_par_evento")
    } == conteos


def _juicio_simple(termino, ficha, relacion="satisface"):
    import uuid as _uuid
    from decimal import Decimal

    from app.jev_ads import ClavePar, Juicio

    return Juicio(
        intento_id=_uuid.uuid4(),
        clave=ClavePar("a" * 64, ficha.id, "b" * 64),
        relacion=relacion,
        probabilidades={
            "satisface": Decimal("0.70"),
            "no_satisface": Decimal("0.20"),
            "informacion_insuficiente": Decimal("0.10"),
        },
        confidence=Decimal("0.80"),
        observado_at=NOW_FABRICA,
    )


NOW_FABRICA = dt.datetime(2026, 10, 4, tzinfo=dt.UTC)


def test_cli_evaluar_plan_calcula_el_export_y_la_asesoria_queda_por_huella(escenario, tmp_path):
    """R2 y bloqueante de codex en B10: el CLI no confia en un export editado.
    Recibe la MISMA solicitud del preview, calcula el export por el camino del
    endpoint y la asesoria queda en la huella del plan; una solicitud que no
    valida sale 2 sin escribir."""
    import uuid as _uuid
    from decimal import Decimal

    from app.jev_ads import ClavePar, Juicio
    from app.jev_juicios import ResultadoPar
    from tools.jev_ads import main as cli_jev

    cliente, conn, solicitud, fw, ids = escenario
    listing = conn.execute(
        "SELECT id FROM listing WHERE product_id = %s AND platform = 'amazon_mx'", (ids[0],)
    ).fetchone()[0]
    _ficha_jev_fabrica(conn, ids[0], listing)
    export = cliente.post("/api/fabrica/export-semillas", json=solicitud).json()
    ruta = tmp_path / "solicitud.json"
    ruta.write_text(json.dumps(solicitud), encoding="utf-8")

    def pedir(termino, ficha):
        return ResultadoPar(
            juicio=Juicio(
                intento_id=_uuid.uuid4(),
                clave=ClavePar("a" * 64, ficha.id, "b" * 64),
                relacion="satisface",
                probabilidades={
                    "satisface": Decimal("0.80"),
                    "no_satisface": Decimal("0.10"),
                    "informacion_insuficiente": Decimal("0.10"),
                },
                confidence=Decimal("0.80"),
                observado_at=NOW_FABRICA,
            ),
            usage=None,
            duracion_ms=5,
        )

    argv = ["evaluar-plan", "--plan", str(ruta), "--solicitud", str(_uuid.uuid4())]
    salida: list[str] = []
    assert cli_jev(argv, pedir=pedir, imprimir=salida.append) == 0
    assert any("pares nuevos 1, reutilizables 0; pagaria 1" in linea for linea in salida), salida
    assert cli_jev([*argv, "--aplicar"], pedir=pedir, imprimir=salida.append) == 0
    esperado = f"resultado grupo collar (keyword) HayCompatible productos=[{ids[0]}] cobertura=1/1"
    assert esperado in salida
    asesoria = cliente.get(f"/api/fabrica/asesoria/{export['huella']}").json()["asesoria"]
    assert asesoria["plan_sha256"] == export["huella"]
    assert asesoria["fuentes_semillas"] == export["fuentes_semillas"]
    ruta.write_text(json.dumps({**solicitud, "modo": "otro"}), encoding="utf-8")
    revisiones = conn.execute("SELECT count(*) FROM jev_revision").fetchone()[0]
    assert cli_jev([*argv[:3], "--solicitud", str(_uuid.uuid4()), "--aplicar"], pedir=pedir) == 2
    assert conn.execute("SELECT count(*) FROM jev_revision").fetchone()[0] == revisiones
