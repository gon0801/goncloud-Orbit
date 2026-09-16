# Drive e2e de superficie de usuario: la app como la ve el usuario, NO sus
# internos. Se corre con el COMANDO_DRIVE del Drive.md (pytest verify/). No es
# la suite unitaria del repo: pytest la corre y verify/ es lo que se ejecuta.
#
# Superficie: POSTGRES (el proceso completo, bajo pytest con monopatch):
#   - /health (la app arranca y responde: entrar)
#   - GET /api/ads-optimizer/status (ver el estado: lista el ciclo por plataforma)
#   - GET /api/ads-optimizer/goals (ver la lista de goals)
#   - GET /api/ads-optimizer/audit (ver la lista de decisiones paginadas)
# Las tres rutas leen la base desechable con ORBIT_TEST_DSN (Postgres 16
# desechable, esquema migrado) y devuelven JSON. Dinero como string (regla 4).
# Ningun test toca app_write ni ORBIT_DSN_ADMIN (superficie de escritura).
# ORBIT_SECRETS_DIR apunta a un dir VACIO: el canal notifica queda
# deshabilitado, sin token, sin carga fuera del repo ni secretos leidos.

from __future__ import annotations

import os

import pytest

fastapi_testclients = pytest.importorskip("fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402  (skip condicional arriba)

from app.main import app  # noqa: E402  (idem)


def _client() -> TestClient:
    if not os.environ.get("ORBIT_TEST_DSN"):
        pytest.skip("sin Postgres: ORBIT_TEST_DSN no esta definido")
    return TestClient(app)


def test_entrar_la_app_responde_salud():
    # La app arranca y el healthcheck responde OK.
    with _client() as c:
        r = c.get("/health")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"


def test_ver_el_estado_del_optimizador_por_plataforma():
    with _client() as c:
        r = c.get("/api/ads-optimizer/status")
        assert r.status_code == 200, r.text
        data = r.json()
        # Un ciclo por plataforma o vacio si no hubo: nunca un DSN filtrado.
        dump = str(data)
        assert "postgresql://" not in dump and "10.13.13." + "1" not in dump
        assert isinstance(data.get("ciclos", data), (list, dict))


def test_ver_la_lista_de_goals():
    with _client() as c:
        r = c.get("/api/ads-optimizer/goals")
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, (list, dict))


def test_ver_la_lista_de_decisiones_audit_con_pagina():
    with _client() as c:
        r = c.get("/api/ads-optimizer/audit", params={"limit": 10, "offset": 0})
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, (list, dict))
