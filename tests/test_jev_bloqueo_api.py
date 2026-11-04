"""B.4: rutas /api/jev-bloqueo y la degradacion de /cortes.

La prueba 1 pega al HTTP. El transporte de Amazon no recibe llamadas con el
interruptor apagado. Quitar sigue. La consulta rota deja la pantalla en 200.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_bloqueo_dueno import _db, _skip_db
from test_bloqueo_dueno_efecto import _dsn_de, _parche, _prepara, _transporte

import app.bloqueo_dueno_efecto as efecto
from app.apply import _cliente_ajuste
from app.main import app

TOKEN = "token-de-prueba"


def _secrets(tmp_path, monkeypatch) -> None:
    carpeta = tmp_path / "secrets"
    carpeta.mkdir()
    (carpeta / "api_write_token").write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(carpeta))


def _cliente(conn, monkeypatch, tmp_path) -> TestClient:
    dsn = _dsn_de(conn)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
    monkeypatch.setenv("ORBIT_DSN_READ", dsn)
    _secrets(tmp_path, monkeypatch)
    return TestClient(app)


def _apaga(conn, valor=None) -> None:
    settings = {} if valor is None else {"jev.bloqueo": valor}
    conn.execute(
        "INSERT INTO config_version (settings) VALUES (%s::jsonb)",
        (json.dumps(settings),),
    )


def _nombres(conn, grupo: int, campana_ext: str) -> None:
    conn.execute(
        "UPDATE ad_entity SET name = 'Campana MX' WHERE external_id = %s",
        (campana_ext,),
    )
    conn.execute("UPDATE ad_entity SET name = 'Grupo MX' WHERE id = %s", (grupo,))


def _terminos(cuerpo) -> list[str]:
    return [
        propuesta["termino"]
        for plataforma in cuerpo["bloqueos_jev"]["plataformas"]
        for propuesta in plataforma["propuestas"]
    ]


def _post(cliente, ruta, cuerpo):
    return cliente.post(
        "/api/jev-bloqueo/" + ruta,
        json=cuerpo,
        headers={"x-orbit-token": TOKEN},
    )


@_skip_db
@pytest.mark.parametrize("valor", [None, "true"])
def test_interruptor_apagado_niega_sin_http_y_oculta_propuestas(monkeypatch, tmp_path, valor):
    _parche(monkeypatch)
    vistos: list[httpx.Request] = []
    mock, vistos, _store = _transporte("ag", "baño")

    def cliente_grabado(plataforma, transport=None):
        return _cliente_ajuste(plataforma, transport=mock)

    monkeypatch.setattr(efecto, "_cliente", cliente_grabado)
    with _db() as conn:
        senal, grupo, _ext, campana, termino, _roster = _prepara(conn)
        _nombres(conn, grupo, campana)
        cliente = _cliente(conn, monkeypatch, tmp_path)
        encendido = cliente.get("/api/dashboard/cortes")
        assert encendido.status_code == 200
        assert termino in _terminos(encendido.json())
        _apaga(conn, valor)
        apagado = cliente.get("/api/dashboard/cortes")
        assert apagado.status_code == 200
        assert apagado.json()["bloqueos_disponibles"] is True
        assert termino not in _terminos(apagado.json())
        for ruta, cuerpo in (
            ("aprobar", {"senal_id": str(senal)}),
            ("rechazar", {"senal_id": str(senal)}),
            ("reanudar", {"plataforma": "amazon_mx"}),
        ):
            resp = _post(cliente, ruta, cuerpo)
            assert resp.status_code == 409
            assert resp.json()["detail"] == "interruptor"
        assert vistos == []


@_skip_db
def test_quitar_sigue_con_interruptor_ausente(monkeypatch, tmp_path):
    _parche(monkeypatch)
    with _db() as conn:
        senal, grupo, grupo_ext, campana, termino, _roster = _prepara(conn)
        _nombres(conn, grupo, campana)
        mock, vistos, _store = _transporte(grupo_ext, termino)
        hecho = efecto.aprobar(conn, senal, transport=mock)
        assert hecho.paso == "aplicado"
        _apaga(conn)
        vistos.clear()

        def cliente_grabado(plataforma, transport=None):
            return _cliente_ajuste(plataforma, transport=mock)

        monkeypatch.setattr(efecto, "_cliente", cliente_grabado)
        cliente = _cliente(conn, monkeypatch, tmp_path)
        resp = _post(cliente, "quitar", {"veredicto_id": str(hecho.veredicto_id)})
        assert resp.status_code == 200
        assert resp.json()["paso"] == "revertido"
        assert any(r.url.path == "/sp/negativeKeywords/delete" for r in vistos)


@_skip_db
def test_reanudar_escribe_fila_y_no_llama_amazon(monkeypatch, tmp_path):
    _parche(monkeypatch)
    mock, vistos, _store = _transporte("ag", "baño")

    def cliente_grabado(plataforma, transport=None):
        return _cliente_ajuste(plataforma, transport=mock)

    monkeypatch.setattr(efecto, "_cliente", cliente_grabado)
    with _db() as conn:
        _prepara(conn)
        cliente = _cliente(conn, monkeypatch, tmp_path)
        resp = _post(cliente, "reanudar", {"plataforma": "amazon_us"})
        assert resp.status_code == 200
        assert resp.json()["paso"] == "reanudacion"
        assert resp.json()["motivo"] is None
        fila = conn.execute(
            """
            SELECT tipo, plataforma::text, ad_group_id, termino, senal_id, origen_id
              FROM bloqueo_veredicto
             WHERE tipo = 'reanudacion'
            """
        ).fetchone()
        assert fila == ("reanudacion", "amazon_us", None, None, None, None)
        assert vistos == []
        invalida = _post(cliente, "reanudar", {"plataforma": "meli"})
        assert invalida.status_code == 422


@_skip_db
def test_cortes_consulta_rota_sigue_200(monkeypatch, tmp_path):
    with _db() as conn:
        cliente = _cliente(conn, monkeypatch, tmp_path)

        def rota(conn_lectura):
            raise RuntimeError("consulta rota")

        monkeypatch.setattr("app.bloqueo_dueno.lee_pantalla", rota)
        json_resp = cliente.get("/api/dashboard/cortes")
        assert json_resp.status_code == 200
        assert json_resp.json()["bloqueos_disponibles"] is False
        assert json_resp.json()["bloqueos_jev"]["bloqueos"] == []
        html = cliente.get("/cortes")
        assert html.status_code == 200
        assert 'data-bloqueos-disponibles="false"' in html.text
