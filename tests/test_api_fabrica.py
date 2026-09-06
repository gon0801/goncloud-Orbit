"""API de fabrica con Postgres real y frontera externa simulada."""

from __future__ import annotations

import datetime as dt
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
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


def test_catalogo_no_inventa_margenes_y_excluye_multilisting(escenario):
    cliente, _, _, _, ids = escenario
    respuesta = cliente.get("/api/fabrica/catalogo?plataforma=amazon_mx")
    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["moneda"] == "MXN" and data["tipos_producto"] == ["collar_perro"]
    filas = data["productos"]
    assert [p["id"] for p in filas] == list(ids)
    assert filas[0]["elegible"] and filas[0]["margen_neto_pct"] == "40.00000000000000000"
    assert filas[1]["margen_neto_pct"] is None and not filas[1]["elegible"]
    assert "margen" in filas[1]["motivo"].lower()
    assert not filas[2]["elegible"] and "listing" in filas[2]["motivo"].lower()


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
