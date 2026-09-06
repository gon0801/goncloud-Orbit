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
                dsn = conn.info.dsn
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
    getattr(dashboard, "cmd_drive_" + pantalla)(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((dashboard.EVIDENCE_ROOT / f"prueba/{pantalla}/PROOF.json").read_text())
    assert prueba["ok"] is True


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
