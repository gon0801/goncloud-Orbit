"""El harness debe detectar fallos y conducir el dashboard vigente."""

import argparse
import importlib.machinery
import importlib.util
import json
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.main import app
from tests.test_schema import _hay_postgres_local, _test_dsn


@pytest.fixture
def harness(monkeypatch, tmp_path):
    ruta = Path(__file__).resolve().parents[1] / ".cursor/skills/verify-orbit/helpers/orbit-verify"
    loader = importlib.machinery.SourceFileLoader("orbit_verify_prueba", str(ruta))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    modulo = importlib.util.module_from_spec(spec)
    loader.exec_module(modulo)
    monkeypatch.setattr(modulo, "SCRATCH_ROOT", tmp_path / "scratch")
    monkeypatch.setattr(modulo, "EVIDENCE_ROOT", tmp_path / "evidence")
    return modulo


@pytest.fixture
def dashboard(harness, monkeypatch):
    if not _hay_postgres_local():
        pytest.skip("requiere Postgres de pruebas")
    nombre = "orbit_verify_test_" + uuid.uuid4().hex
    with psycopg.connect(_test_dsn(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nombre)))
        try:
            with psycopg.connect(_test_dsn(), dbname=nombre, autocommit=True) as conn:
                conn.execute("SET TIME ZONE 'UTC'")
                harness._aplicar_migraciones(conn)
                semilla = harness._sembrar(conn)
                dsn = make_conninfo(_test_dsn(), dbname=nombre)
            monkeypatch.setenv("ORBIT_DSN_READ", dsn)
            harness._save_state(
                {
                    "run_id": "prueba",
                    "base_url": "http://127.0.0.1:18010",
                    "port": 18010,
                    "seed": semilla,
                }
            )
            with TestClient(app) as client:

                def http(url, timeout=5):
                    respuesta = client.get(url)
                    return respuesta.status_code, dict(respuesta.headers), respuesta.content

                monkeypatch.setattr(harness, "_http", http)
                yield harness
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(nombre)))


def test_drive_cortes_conduce_la_pantalla_vigente(dashboard):
    dashboard.cmd_drive_cortes(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((dashboard.EVIDENCE_ROOT / "prueba/cortes/PROOF.json").read_text())
    assert prueba["ok"] is True


@pytest.mark.parametrize(
    "pantalla", ["decisiones", "contribucion", "inertes", "campanas", "resumen", "salud"]
)
def test_drives_conducen_el_dashboard_vigente(dashboard, monkeypatch, pantalla):
    monkeypatch.setattr(dashboard, "_chrome_screenshot", lambda *args, **kwargs: True)
    # Esta prueba ya no abre el navegador para la captura. El foco y el ancho
    # 390 tambien lo mide el navegador; el drive real, con Edge o Chrome, no.
    if pantalla == "resumen":
        monkeypatch.setattr(
            dashboard,
            "_foco_y_ancho",
            lambda *args, **kwargs: {
                "foco": {
                    "ok": True,
                    "focoVisible": True,
                    "outlineStyle": "solid",
                    "outlineWidth": "2px",
                },
                "ancho390": {"ancho": 390, "bodyScroll": False, "tablaScroll": True},
            },
        )
    getattr(dashboard, "cmd_drive_" + pantalla)(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((dashboard.EVIDENCE_ROOT / f"prueba/{pantalla}/PROOF.json").read_text())
    assert prueba["ok"] is True


def test_drive_cortes_exige_ancho_calculado_de_senal(harness, monkeypatch):
    harness._save_state({"run_id": "prueba", "base_url": "http://127.0.0.1:18010", "port": 18010})
    html = """
    <body data-pantalla="cortes"><h2>Propuestas pendientes de decisión</h2>
    Crecen Recortan Por que Confirmar rechazo zapato blanco Campana A
    <button data-vetar="1"></button><form data-veto="1">
    <input name="dias" value="30"><input name="actor"><input name="token"></form>
    <div class="senal-bloque"><div class="action-row"><div class="action-body">Señal</div>
    </div></div></body>
    """
    api = {
        "items": [
            {
                "kind": "negative",
                "search_term": "zapato blanco",
                "estado": "pending_veto",
                "nombre": "Campana A",
            }
        ]
    }

    def http(url, timeout=5):
        if url.endswith("/api/dashboard/cortes"):
            return 200, {}, json.dumps(api).encode()
        if url.endswith("/cortes"):
            return 200, {}, html.encode()
        return 200, {}, b'<body data-pantalla="resumen"></body>'

    medicion = {"senal": [{"contenido": 28, "disponible": 300, "ok": False}], "png": True}
    monkeypatch.setattr(harness, "_http", http)
    monkeypatch.setattr(harness, "_foco_y_ancho", lambda *args, **kwargs: medicion)
    with pytest.raises(SystemExit):
        harness.cmd_drive_cortes(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((harness.EVIDENCE_ROOT / "prueba/cortes/PROOF.json").read_text())
    assert prueba["checks"]["senal_ancho_calculado"] is False

    medicion["senal"] = [{"contenido": 300, "disponible": 300, "ok": True}]
    harness.cmd_drive_cortes(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((harness.EVIDENCE_ROOT / "prueba/cortes/PROOF.json").read_text())
    assert prueba["checks"]["senal_ancho_calculado"] is True
    assert prueba["ok"] is True


def test_drive_secundarias_recorre_rutas_sin_drive_especifico(dashboard):
    dashboard.cmd_drive_secundarias(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((dashboard.EVIDENCE_ROOT / "prueba/secundarias/PROOF.json").read_text())
    assert prueba["ok"] is True
    assert set(prueba["pantallas"]) == {
        "gasto-sin-venta",
        "busquedas-sin-venta",
        "keywords-danadas",
        "ruido",
        "donde-poner-el-dinero",
        "reputacion",
        "envios",
        "familias",
        "precios",
    }
    for pantalla in prueba["pantallas"].values():
        assert pantalla["status"] == 200
        assert pantalla["nav_activa"] is True


def test_drive_resumen_valida_navegacion_a2_y_css_propio(dashboard, monkeypatch):
    monkeypatch.setattr(dashboard, "_chrome_screenshot", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        dashboard,
        "_foco_y_ancho",
        lambda *args, **kwargs: {
            "foco": {
                "ok": True,
                "focoVisible": True,
                "outlineStyle": "solid",
                "outlineWidth": "2px",
            },
            "ancho390": {"ancho": 390, "bodyScroll": False, "tablaScroll": True},
        },
    )

    dashboard.cmd_drive_resumen(argparse.Namespace(run_id="prueba"))

    prueba = json.loads((dashboard.EVIDENCE_ROOT / "prueba/resumen/PROOF.json").read_text())
    assert prueba["ok"] is True
    for check in (
        "nav_oportunidades",
        "nav_decisiones",
        "nav_rendimiento",
        "nav_catalogo",
        "menu_movil_mas",
        "accion_crear_campanas",
        "pendientes_revision",
        "monedas_principales",
        "sin_footer_antiguo",
        "css_390",
        "css_foco",
    ):
        assert prueba["checks"][check] is True
    assert "nav_inertes" not in prueba["checks"]
    assert "nav_fabrica" not in prueba["checks"]


@pytest.mark.parametrize("pantalla", ["campanas", "resumen", "salud"])
def test_drive_falla_si_no_hay_captura(dashboard, monkeypatch, pantalla):
    monkeypatch.setenv("ORBIT_VERIFY_CHROME", "/no-existe/chrome")
    with pytest.raises(SystemExit):
        getattr(dashboard, "cmd_drive_" + pantalla)(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((dashboard.EVIDENCE_ROOT / f"prueba/{pantalla}/PROOF.json").read_text())
    assert prueba["checks"]["screenshot"] is False
    assert prueba["ok"] is False


def test_captura_anterior_no_oculta_fallo_de_chrome(harness, monkeypatch, tmp_path):
    png = tmp_path / "captura.png"
    png.write_bytes(b"captura anterior")
    monkeypatch.setenv("ORBIT_VERIFY_CHROME", "/no-existe/chrome")
    assert (
        harness._chrome_screenshot("http://127.0.0.1:18010", png, tmp_path / "error.txt") is False
    )


def test_captura_390_exige_viewport_real_y_cinco_controles_visibles(harness, monkeypatch, tmp_path):
    datos = {
        "ancho390": {"ancho": 1280},
        "menuMovil": {"cantidad": 5, "todosDentro": False},
        "png": True,
    }

    def cdp_falso(url, png, detalle, **kwargs):
        png.write_bytes(b"png")
        return datos

    def chrome_cli_falso(*args, **kwargs):
        comando = args[0]
        ruta = next(arg.split("=", 1)[1] for arg in comando if arg.startswith("--screenshot="))
        Path(ruta).write_bytes(b"png")
        return harness.subprocess.CompletedProcess(comando, 0, "", "")

    monkeypatch.setattr(harness, "_foco_y_ancho", cdp_falso)
    monkeypatch.setattr(harness.subprocess, "run", chrome_cli_falso)
    png = tmp_path / "captura-390.png"
    detalle = tmp_path / "captura-390.json"
    assert harness._chrome_screenshot("http://127.0.0.1:18010", png, detalle, ancho=390) is False

    datos = {
        "ancho390": {"ancho": 390},
        "menuMovil": {"cantidad": 5, "todosDentro": True},
        "png": True,
    }
    assert harness._chrome_screenshot("http://127.0.0.1:18010", png, detalle, ancho=390) is True


def test_run_duplicado_no_limpia_la_instancia_existente(harness, monkeypatch):
    harness._save_state({"run_id": "existente"})
    ruta = harness._state_path("existente")
    previo = ruta.read_bytes()
    monkeypatch.setattr(harness, "cmd_cleanup", lambda args: ruta.unlink())
    with pytest.raises(SystemExit):
        harness.cmd_drive_maintain_verification_skill(
            argparse.Namespace(run_id="existente", port=18010, force=False)
        )
    assert ruta.exists(), "el rechazo no debe borrar el run existente"
    assert ruta.read_bytes() == previo


def test_corte_no_combina_campos_de_filas_distintas(dashboard, monkeypatch):
    http_real = dashboard._http

    def http(url, timeout=5):
        status, headers, raw = http_real(url, timeout)
        if url.endswith("/api/dashboard/cortes"):
            datos = json.loads(raw)
            fila = datos["items"][0]
            datos["items"] = [dict(fila, nombre="Otra campana"), dict(fila, search_term="otro")]
            raw = json.dumps(datos).encode()
        return status, headers, raw

    monkeypatch.setattr(dashboard, "_http", http)
    with pytest.raises(SystemExit):
        dashboard.cmd_drive_cortes(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((dashboard.EVIDENCE_ROOT / "prueba/cortes/PROOF.json").read_text())
    assert prueba["checks"]["api_has_negative_pending_veto"] is False


@pytest.mark.parametrize("ausente", [None, "selector", "comparador", "titulo"])
def test_drive_fabrica_valida_redisenio_sin_exigir_titulo_duplicado(harness, monkeypatch, ausente):
    """Conduce el HTML real y rechaza la ausencia de piezas del flujo nuevo."""
    harness._save_state({"run_id": "prueba", "base_url": "http://127.0.0.1:18010", "port": 18010})
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    html = respuesta.text
    faltantes = {
        "selector": 'id="fabrica-productos"',
        "comparador": 'id="fabrica-comparador"',
        "titulo": "<h1>Crear campañas</h1>",
    }
    if ausente:
        html = html.replace(faltantes[ausente], "")

    def http(url, timeout=5):
        if "/api/fabrica/catalogo" in url:
            return 200, {}, b'{"productos": [], "tipos_producto": []}'
        if "/api/fabrica/lotes" in url:
            return 200, {}, b'{"items": []}'
        if url.endswith("/campanas/nuevas"):
            return 200, {}, html.encode()
        return 200, {}, b'<body data-pantalla="resumen"></body>'

    monkeypatch.setattr(harness, "_http", http)
    if ausente:
        with pytest.raises(SystemExit):
            harness.cmd_drive_fabrica(argparse.Namespace(run_id="prueba"))
    else:
        harness.cmd_drive_fabrica(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((harness.EVIDENCE_ROOT / "prueba/fabrica/PROOF.json").read_text())
    assert prueba["ok"] is (ausente is None)
