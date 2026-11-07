"""Encender la pausa por producto desde la pantalla (AUTO-03 I.8, S8).

POST /api/ads-optimizer/productos/pausa/{platform} despacha a
`productos_modo.cambia_pausa` (I.7) con token y DSN admin; GET
/api/dashboard/productos/pausa (lectura) dice el modo vigente, los limites y
si las dos claves estan guardadas. Archivo nuevo: el lider mueve estas
pruebas a tests/test_apply_producto.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json
from test_api_write import TOKEN, _db_con_rol_admin, _secrets_token
from test_schema import _postgres_obligatorio_ausente

from app.main import app

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

MX = "amazon_mx"
CLAVE_PAUSA = f"ads_producto_pausa_{MX}"
CLAVE_CLICS = f"ads_producto_pausa_clics_{MX}"
CLAVE_GASTO = f"ads_producto_pausa_gasto_{MX}"
RUTA = f"/api/ads-optimizer/productos/pausa/{MX}"

_SQL_PRACTICAS_ABIERTAS = """
SELECT count(*)
  FROM apply_queue q JOIN ad_entity e ON e.id = q.ad_entity_id AND e.kind = 'product_ad'
 WHERE q.platform = 'amazon_mx' AND q.kind = 'pause' AND q.modo = 'shadow'
   AND q.estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
"""


def _config(conn, settings: dict) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('previa', %s) RETURNING id",
        (Json(settings),),
    ).fetchone()[0]


def _configs(conn) -> list:
    return conn.execute("SELECT id, settings FROM config_version ORDER BY id").fetchall()


def _practica_abierta(conn, config_id: int) -> int:
    """Una propuesta de practica (shadow, pending_veto) de pause sobre un
    product ad de amazon_mx, como la deja el ciclo con la pausa en shadow."""
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES ('amazon_mx', 'campaign', 'c-1') RETURNING id"
    ).fetchone()[0]
    grupo = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES ('amazon_mx', 'ad_group', 'g-1', %s) RETURNING id",
        (camp,),
    ).fetchone()[0]
    anuncio = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES ('amazon_mx', 'product_ad', 'pa-1', %s) RETURNING id",
        (grupo,),
    ).fetchone()[0]
    ciclo = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('shadow', 'amazon_mx') RETURNING id"
    ).fetchone()[0]
    decision = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " data_observed_at, window_start, window_end, inputs)"
        " VALUES (%s, %s, 'pause', %s, now() - interval '31 days', CURRENT_DATE - 60,"
        " CURRENT_DATE - 30, '{}'::jsonb) RETURNING id",
        (ciclo, anuncio, config_id),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO apply_queue (platform, ad_entity_id, kind, decision_id, modo, estado,"
        " vence_el, request_payload)"
        " VALUES ('amazon_mx', %s, 'pause', %s, 'shadow', 'pending_veto',"
        " now() + interval '48 hours', '{}'::jsonb) RETURNING id",
        (anuncio, decision),
    ).fetchone()[0]


def _cliente(tmp_path, monkeypatch, dsn_admin: str, dsn_lectura: str) -> TestClient:
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
    monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
    return TestClient(app)


def _post(cliente: TestClient, modo: str, base: int, token: str | None = TOKEN):
    headers = {"x-orbit-token": token} if token is not None else {}
    return cliente.post(RUTA, json={"modo": modo, "base_config_version_id": base}, headers=headers)


def test_encender_sin_token_401(tmp_path, monkeypatch):
    """Sin el header x-orbit-token: 401 antes de abrir el DSN admin."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = _post(TestClient(app), "live", 1, token=None)
    assert resp.status_code == 401
    assert "x-orbit-token" in resp.json()["detail"]
    resp = _post(TestClient(app), "live", 1, token="otro")
    assert resp.status_code == 401


@_skip_db
def test_encender_sin_limites_guardados_422_y_no_escribe(tmp_path, monkeypatch):
    """S8: sin las dos claves de limites, `live` es 422 con el motivo del
    servidor y no escribe config ni toca las practicas; la pantalla lo ve
    como limites_guardados falso. `off` y una plataforma ajena son 422."""
    with _db_con_rol_admin("orbit_enc_sinlim") as (conn, dsn_admin, dsn_lectura):
        base = _config(conn, {CLAVE_PAUSA: "shadow", CLAVE_CLICS: "100"})
        q = _practica_abierta(conn, base)
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin, dsn_lectura)

        estado = cliente.get(f"/api/dashboard/productos/pausa?platform={MX}")
        assert estado.status_code == 200, estado.text
        assert estado.json()["limites_guardados"] is False
        assert estado.json()["modo"] == "shadow"

        resp = _post(cliente, "live", base)
        assert resp.status_code == 422, resp.text
        assert "limites" in resp.json()["detail"]
        # `off` sigue siendo del CLI y la plataforma es vocabulario cerrado.
        hdr = {"x-orbit-token": TOKEN}
        cuerpo = {"modo": "off", "base_config_version_id": base}
        assert cliente.post(RUTA, json=cuerpo, headers=hdr).status_code == 422
        cuerpo = {"modo": "live", "base_config_version_id": base}
        ajena = cliente.post("/api/ads-optimizer/productos/pausa/meli", json=cuerpo, headers=hdr)
        assert ajena.status_code == 422
        assert [f[0] for f in _configs(conn)] == [base]
        assert conn.execute("SELECT estado FROM apply_queue WHERE id = %s", (q,)).fetchone()[0] == (
            "pending_veto"
        )


@_skip_db
def test_encender_con_limites_live_descarta_practicas_y_volver_a_practica(tmp_path, monkeypatch):
    """Con limites guardados, `live` escribe una config nueva con la pausa en
    vivo y deja cero practicas abiertas de product_ad; `shadow` la regresa a
    practica con otra config; GET /productos/pausa refleja cada paso. Una base
    ya superada es 409 sin escribir."""
    with _db_con_rol_admin("orbit_enc_vivo") as (conn, dsn_admin, dsn_lectura):
        base = _config(
            conn,
            {
                "ads_optimizer_mode": "live",
                CLAVE_PAUSA: "shadow",
                CLAVE_CLICS: "60",
                CLAVE_GASTO: "300",
            },
        )
        q = _practica_abierta(conn, base)
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin, dsn_lectura)

        estado = cliente.get(f"/api/dashboard/productos/pausa?platform={MX}").json()
        assert estado == {
            "platform": MX,
            "modo": "shadow",
            "modo_texto": "Práctica",
            "config_version_id": base,
            "limites": {"clics": 60, "gasto": {"valor": "300", "moneda": "MXN"}},
            "limites_guardados": True,
            "error": None,
        }
        assert conn.execute(_SQL_PRACTICAS_ABIERTAS).fetchone()[0] == 1

        resp = _post(cliente, "live", base)
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert cuerpo["modo"] == "live" and cuerpo["modo_anterior"] == "shadow"
        assert cuerpo["config_escrita"] is True
        assert cuerpo["practicas_antes"] == 1 and cuerpo["practicas_despues"] == 0
        assert cuerpo["practicas_descartadas"] == 1
        configs = _configs(conn)
        assert len(configs) == 2 and configs[-1][0] == cuerpo["config_version_id"]
        assert configs[-1][1][CLAVE_PAUSA] == "live"
        assert configs[-1][1]["ads_optimizer_mode"] == "live"
        assert configs[-1][1][CLAVE_CLICS] == "60"
        assert conn.execute(_SQL_PRACTICAS_ABIERTAS).fetchone()[0] == 0
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", "encendido_pausa_producto")
        vivo = cliente.get(f"/api/dashboard/productos/pausa?platform={MX}").json()
        assert vivo["modo"] == "live" and vivo["modo_texto"] == "En vivo"
        assert vivo["config_version_id"] == cuerpo["config_version_id"]

        # La pantalla vieja (base superada) no enciende ni regresa: 409.
        obsoleta = _post(cliente, "shadow", base)
        assert obsoleta.status_code == 409, obsoleta.text
        assert str(cuerpo["config_version_id"]) in obsoleta.json()["detail"]
        assert len(_configs(conn)) == 2

        resp = _post(cliente, "shadow", cuerpo["config_version_id"])
        assert resp.status_code == 200, resp.text
        regreso = resp.json()
        assert regreso["modo"] == "shadow" and regreso["modo_anterior"] == "live"
        configs = _configs(conn)
        assert len(configs) == 3 and configs[-1][1][CLAVE_PAUSA] == "shadow"
        practica = cliente.get(f"/api/dashboard/productos/pausa?platform={MX}").json()
        assert practica["modo"] == "shadow" and practica["modo_texto"] == "Práctica"
        assert practica["config_version_id"] == regreso["config_version_id"]
        assert practica["limites_guardados"] is True


@_skip_db
def test_encender_get_pausa_sin_config_y_plataforma_ajena(tmp_path, monkeypatch):
    """Sin config_version la pausa esta apagada, con los limites por omision
    y sin base para encender; una plataforma ajena es 422."""
    with _db_con_rol_admin("orbit_enc_get") as (_conn, dsn_admin, dsn_lectura):
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin, dsn_lectura)
        estado = cliente.get(f"/api/dashboard/productos/pausa?platform={MX}").json()
        assert estado["modo"] == "off" and estado["modo_texto"] == "Apagada"
        assert estado["config_version_id"] is None
        assert estado["limites_guardados"] is False
        assert estado["limites"] == {"clics": 100, "gasto": {"valor": "500", "moneda": "MXN"}}
        resp = cliente.get("/api/dashboard/productos/pausa?platform=meli")
        assert resp.status_code == 422
