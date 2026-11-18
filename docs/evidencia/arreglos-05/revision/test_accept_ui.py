"""Cierre externo: comportamiento CORRECTO; no asserts de SHA ni de fuente."""
import os
import pytest
import ui


@pytest.fixture(scope="module")
def escenario():
    print(ui.metadata())
    with ui.scenario() as s:
        yield s


def test_04_retiro_no_envia_plan_borrado_tras_fallar_nueva_previa(escenario):
    observado = ui.browser(escenario, "retiro")
    assert not observado["escrituras_capturadas"], observado


def test_04_vecino_impulso_no_envia_plan_borrado_tras_fallar_nueva_previa(escenario):
    observado = ui.browser(escenario, "impulso")
    assert not observado["escrituras_capturadas"], observado


def test_15_ruido_no_publica_acos_con_gasto_desconocido(escenario):
    observado = ui.metricas(escenario)
    assert observado["ruido"]["despues"]["acos_pct"] is None, observado


def test_16_resumen_no_afirma_ausencia_de_ventas_que_api_reporta(escenario):
    observado = ui.metricas(escenario)
    assert observado["resumen"]["sin_ventas"] is False
    assert not observado["html_con_afirmacion_sin_ventas"], observado


def test_control_contrato_resumen_conserva_desconocido_y_csp(escenario):
    observado = ui.metricas(escenario)
    assert observado["resumen"]["cost"] is None
    assert observado["resumen"]["acos"] is None
    assert observado["resumen"]["ad_revenue"] == "200.0000"
    assert observado["headers"] == {"content-security-policy": "default-src 'self'", "cache-control": "no-store"}


def test_control_auth_query_y_form_no_sustituyen_header(escenario, monkeypatch):
    from app import api_write
    # Solo se provee el secreto esperado ficticio, no se reemplaza la auth.
    monkeypatch.setattr(api_write, "_lee_token_escritura", lambda: "control-ficticio")
    client = escenario["client"]
    for ruta in ("/api/fabrica/retiro/ejecutar", "/api/fabrica/impulso/lanzar"):
        r = client.post(ruta + "?token=control-ficticio", data={"token": "control-ficticio"},
                        headers={"Origin": "https://origen-invalido.example"})
        assert r.status_code == 401, r.text
