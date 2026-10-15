"""Rutas de goals y compuerta de `/api/precios` (REPRICING 02, G.2).

`app/api_precios_write.py`: `POST /api/precios/goals/plan` (vista previa,
no escribe), `POST /api/precios/goals/aplicar` (todo o nada con huella) y
`POST /api/precios/compuerta/{corrida_id}/liberar` (idempotente). Las tres
con `exige_token` ANTES de abrir la conexion admin (mismo patron que
`app/api_write.py`).

DoD de la fila G.2 (5 pruebas `test_g2_*`): sin token 401 sin conectar;
plan no escribe; huella vieja 409 con el plan nuevo; live sin go 422;
liberar dos veces la misma fila. Extras con razon: aplicar-ok (sin ella
un aplicar que nunca escribe pasa el DoD), seleccion `sin_goal` (la usa
G.3), tope de 500 unidades (lo pina el bosquejo T) y liberar sin
retencion 409 (bosquejo T: `CorridaSinRetencion -> 409`).

Base real (`ORBIT_TEST_DSN`, cero `skipped` con el DSN de VERIFY): cada
test levanta una DB temporal con ORDEN54 (reuso `_db`/`_cadena`/`_config`
de `test_precio_goals`) y apunta `ORBIT_DSN_ADMIN` ahi; el token sale de
`test_api_write` (`_secrets_token` + `TOKEN`).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_api_write import TOKEN, _secrets_token
from test_precio_goals import _cadena, _config, _db
from test_schema import _postgres_obligatorio_ausente

from app.main import app

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CABECERA = {"x-orbit-token": TOKEN}


def _corrida(conn, cfg: int) -> int:
    return conn.execute(
        "INSERT INTO precio_corrida (platform, owner, config_version_id, modo_global)"
        " VALUES ('amazon_mx', 'test-g2', %s, 'shadow') RETURNING id",
        (cfg,),
    ).fetchone()[0]


def _medicion(conn, corrida_id: int, causa: str = "movimiento_masivo") -> None:
    conn.execute(
        "INSERT INTO precio_compuerta_medicion (corrida_id, canal, medidas, nuevas, causa)"
        " VALUES (%s, 'fba', 80, 80, %s)",
        (corrida_id, causa),
    )


def _cuenta(conn, tabla: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]


# ---------------------------------------------------------------------------
# DoD 1: sin token, 401 ANTES de abrir la conexion admin
# ---------------------------------------------------------------------------


def test_g2_sin_token_401_sin_abrir_admin(tmp_path, monkeypatch):
    """Las tres rutas sin header -> 401 y `connect` jamas se llama (espiado)."""
    import app.api_write as api_write

    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    llamadas: list = []

    def _espia(dsn, **kw):
        llamadas.append(dsn)
        raise AssertionError("no debe conectar sin token")

    monkeypatch.setattr(api_write, "connect", _espia)
    cliente = TestClient(app)
    plan = {
        "platform": "amazon_mx",
        "pedido": {"tipo": "valor", "mode": "shadow", "goal_pct": "30.00"},
        "listing_ids": [1],
    }
    rutas = [
        ("/api/precios/goals/plan", plan),
        ("/api/precios/goals/aplicar", {**plan, "huella": "0" * 16, "actor": "dueno"}),
        ("/api/precios/compuerta/1/liberar", {"actor": "dueno", "nota": "suelta"}),
    ]
    for ruta, cuerpo in rutas:
        resp = cliente.post(ruta, json=cuerpo)
        assert resp.status_code == 401, f"{ruta} sin token deberia ser 401"
        assert "x-orbit-token" in resp.json()["detail"]
    assert llamadas == []


# ---------------------------------------------------------------------------
# DoD 2: plan no escribe
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_g2_plan_no_escribe(tmp_path, monkeypatch):
    """El plan devuelve filas + huella y deja `precio_goal` intacto."""
    _secrets_token(tmp_path, monkeypatch)
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        resp = TestClient(app).post(
            "/api/precios/goals/plan",
            json={
                "platform": "amazon_mx",
                "pedido": {"tipo": "margen_de_hoy", "mode": "shadow"},
                "listing_ids": [datos["listing"]],
            },
            headers=_CABECERA,
        )
        assert resp.status_code == 200
        cuerpo = resp.json()
        assert len(cuerpo["huella"]) == 16
        (fila,) = cuerpo["filas"]
        assert fila["listing_id"] == datos["listing"]
        assert fila["accion"] == "sembrar"
        assert fila["goal_propuesto"] == "0.2400"
        assert fila["confirmable"] is True
        assert _cuenta(conn, "precio_goal") == 0
        assert _cuenta(conn, "precio_compuerta_liberacion") == 0


# ---------------------------------------------------------------------------
# DoD 3: aplicar con huella vieja -> 409 con el plan nuevo
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_g2_aplicar_huella_vieja_409_con_plan_nuevo(tmp_path, monkeypatch):
    """Huella que no coincide -> 409, el cuerpo trae el plan fresco y nada
    se escribe (el pedido 30.00/shadow no tiene bloqueos: el 409 sale de
    la huella, no de un bloqueo)."""
    _secrets_token(tmp_path, monkeypatch)
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        cliente = TestClient(app)
        base = {
            "platform": "amazon_mx",
            "pedido": {"tipo": "valor", "mode": "shadow", "goal_pct": "30.00"},
            "listing_ids": [datos["listing"]],
        }
        fresca = cliente.post("/api/precios/goals/plan", json=base, headers=_CABECERA).json()[
            "huella"
        ]
        resp = cliente.post(
            "/api/precios/goals/aplicar",
            json={**base, "huella": "f" * 16, "actor": "dueno"},
            headers=_CABECERA,
        )
        assert resp.status_code == 409
        detalle = resp.json()["detail"]
        assert "huella" in detalle["message"]
        assert detalle["plan_nuevo"]["huella"] == fresca
        assert len(detalle["plan_nuevo"]["filas"]) == 1
        assert _cuenta(conn, "precio_goal") == 0


# ---------------------------------------------------------------------------
# DoD 4: live sin go literal -> 422
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_g2_live_sin_go_422(tmp_path, monkeypatch):
    """Subir a live sin `go_literal` -> 422 y cero escrituras."""
    _secrets_token(tmp_path, monkeypatch)
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        cliente = TestClient(app)
        base = {
            "platform": "amazon_mx",
            "pedido": {"tipo": "valor", "mode": "live", "goal_pct": "30.00"},
            "listing_ids": [datos["listing"]],
        }
        fresca = cliente.post("/api/precios/goals/plan", json=base, headers=_CABECERA).json()[
            "huella"
        ]
        resp = cliente.post(
            "/api/precios/goals/aplicar",
            json={**base, "huella": fresca, "actor": "dueno"},
            headers=_CABECERA,
        )
        assert resp.status_code == 422
        assert "go" in resp.json()["detail"]
        assert _cuenta(conn, "precio_goal") == 0


# ---------------------------------------------------------------------------
# DoD 5: liberar dos veces devuelve la misma fila
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_g2_liberar_dos_veces_misma_fila(tmp_path, monkeypatch):
    """Soltar dos veces -> 200 las dos, el mismo cuerpo (mismo
    `liberada_en`) y una sola fila."""
    _secrets_token(tmp_path, monkeypatch)
    with _db() as (conn, dsn):
        corr = _corrida(conn, _config(conn))
        _medicion(conn, corr)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        cliente = TestClient(app)
        cuerpo = {"actor": "dueno", "nota": "ya revise el fusible"}
        primero = cliente.post(
            f"/api/precios/compuerta/{corr}/liberar", json=cuerpo, headers=_CABECERA
        )
        assert primero.status_code == 200
        segundo = cliente.post(
            f"/api/precios/compuerta/{corr}/liberar", json=cuerpo, headers=_CABECERA
        )
        assert segundo.status_code == 200
        assert segundo.json() == primero.json()
        assert primero.json()["corrida_id"] == corr
        assert _cuenta(conn, "precio_compuerta_liberacion") == 1


# ---------------------------------------------------------------------------
# Extras: aplicar-ok, sin_goal, tope 500, liberar sin retencion
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_g2_aplicar_ok_escribe_el_lote(tmp_path, monkeypatch):
    """Con la huella fresca, aplicar siembra y deja el rastro (lote =
    huella, origen manual); sin esta prueba un aplicar que nunca escribe
    pasaria el DoD."""
    _secrets_token(tmp_path, monkeypatch)
    with _db() as (conn, dsn):
        datos = _cadena(conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        cliente = TestClient(app)
        base = {
            "platform": "amazon_mx",
            "pedido": {"tipo": "valor", "mode": "shadow", "goal_pct": "30.00"},
            "listing_ids": [datos["listing"]],
        }
        fresca = cliente.post("/api/precios/goals/plan", json=base, headers=_CABECERA).json()[
            "huella"
        ]
        resp = cliente.post(
            "/api/precios/goals/aplicar",
            json={**base, "huella": fresca, "actor": "dueno"},
            headers=_CABECERA,
        )
        assert resp.status_code == 200
        cuerpo = resp.json()
        assert cuerpo["sembrados"] == 1
        assert cuerpo["cerrados"] == 0
        assert cuerpo["lote"] == fresca
        (hecho,) = cuerpo["hechos"]
        assert hecho["accion"] == "sembrar"
        assert hecho["listing_id"] == datos["listing"]
        fila = conn.execute(
            "SELECT margen_goal_pct, mode, creado_por, origen, lote"
            " FROM precio_goal WHERE listing_id = %s",
            (datos["listing"],),
        ).fetchone()
        assert fila[0] == "0.3000" or str(fila[0]) == "0.3000"
        assert fila[1] == "shadow"
        assert fila[2] == "dueno"
        assert fila[3] == "manual"
        assert fila[4] == fresca


@_skip_sin_pg
def test_g2_plan_sin_goal_lista_solo_los_sin_goal(tmp_path, monkeypatch):
    """`sin_goal` planea solo las unidades sin goal vigente de la
    plataforma (la seleccion que G.3 usa para "margen de hoy")."""
    from app.precio.goals_write import sembrar_goal

    _secrets_token(tmp_path, monkeypatch)
    with _db() as (conn, dsn):
        libre = _cadena(conn, sku="SKU-G2-LIBRE", ext="ASIN-G2-LIBRE")
        ocupado = _cadena(conn, sku="SKU-G2-OCUP", ext="ASIN-G2-OCUP")
        sembrar_goal(
            conn,
            listing_id=ocupado["listing"],
            platform="amazon_mx",
            goal_pct="30.00",
            mode="shadow",
            go_literal=None,
        )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        resp = TestClient(app).post(
            "/api/precios/goals/plan",
            json={
                "platform": "amazon_mx",
                "pedido": {"tipo": "margen_de_hoy", "mode": "shadow"},
                "sin_goal": True,
            },
            headers=_CABECERA,
        )
        assert resp.status_code == 200
        filas = resp.json()["filas"]
        assert [f["listing_id"] for f in filas] == [libre["listing"]]
        assert _cuenta(conn, "precio_goal") == 1


@_skip_sin_pg
def test_g2_plan_mas_de_500_unidades_422(tmp_path, monkeypatch):
    """El tope de 500 unidades por llamada (bosquejo T, tope de
    `/api/familias/asignar`) tambien frena a `sin_goal`, con 422."""
    _secrets_token(tmp_path, monkeypatch)
    with _db() as (conn, dsn):
        conn.execute(
            "INSERT INTO product (odoo_sku, name)"
            " SELECT 'SKU-G2-TOPE-' || g, 'G2 tope' FROM generate_series(1, 501) g"
        )
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " SELECT id, 'amazon_mx', 'ASIN-' || odoo_sku, odoo_sku FROM product"
            " WHERE odoo_sku LIKE 'SKU-G2-TOPE-%'"
        )
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        resp = TestClient(app).post(
            "/api/precios/goals/plan",
            json={
                "platform": "amazon_mx",
                "pedido": {"tipo": "margen_de_hoy", "mode": "shadow"},
                "sin_goal": True,
            },
            headers=_CABECERA,
        )
        assert resp.status_code == 422
        assert "500" in resp.json()["detail"]


@_skip_sin_pg
def test_g2_liberar_sin_retencion_409(tmp_path, monkeypatch):
    """Soltar una corrida que no retuvo nada -> 409 (bosquejo T) y cero
    filas."""
    _secrets_token(tmp_path, monkeypatch)
    with _db() as (conn, dsn):
        corr = _corrida(conn, _config(conn))
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn)
        resp = TestClient(app).post(
            f"/api/precios/compuerta/{corr}/liberar",
            json={"actor": "dueno", "nota": "no hay que soltar"},
            headers=_CABECERA,
        )
        assert resp.status_code == 409
        assert "no retuvo" in resp.json()["detail"]
        assert _cuenta(conn, "precio_compuerta_liberacion") == 0
