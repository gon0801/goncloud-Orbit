"""Tests de settings de escritura (DASHBOARD 01 Phase 3 / task 3.1 + enmienda
2026-09-04 E1-E6).

DoD (regla 9: cada candado se demuestra fallando contra el codigo anterior):

1. Sin credencial -> rechazado (401) en POST de settings.
2. Edicion de goal = UPDATE con updated_at nuevo (camino goals_write).
3. Cambio de config = fila NUEVA de config_version (append-only).
4. Con margen encendido, editar el manual exige advertencia ANTES de guardar
   (HTML + ack_respaldo en el wire).
5. Apagar la fraccion = config nueva SIN la clave y el resolver cae al manual
   (fraccion ausente -> sin_fraccion).
6. La pantalla /settings muestra la procedencia del target vigente por
   plataforma (consume cascada_target_acos_con_procedencia).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json
from test_api_write import TOKEN, _db_con_rol_admin, _secrets_token
from test_goals_write import T_EDITADO, T_SEMBRADO, _siembra_goal_plataforma
from test_schema import _postgres_obligatorio_ausente

from app.main import app
from app.optimizer import goals as g

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

PLAT = "amazon_us"
CLAVE_TARGET = f"ads_target_acos_pct_{PLAT}"
CLAVE_FRACCION = f"ads_target_fraccion_margen_{PLAT}"
ADVERTENCIA_RESPALDO = (
    "este valor NO gobierna mientras el target por margen este encendido; queda de respaldo"
)


# ---------------------------------------------------------------------------
# 1. Auth: sin credencial -> rechazado
# ---------------------------------------------------------------------------


def test_settings_post_sin_token_401(tmp_path, monkeypatch):
    """DoD E5/auth: POST /api/ads-optimizer/settings/{plat} sin header
    x-orbit-token -> 401 ANTES de abrir el DSN."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post(
        f"/api/ads-optimizer/settings/{PLAT}",
        json={"target_manual_pct": "20", "base_config_version_id": 1},
    )
    assert resp.status_code == 401
    assert "x-orbit-token" in resp.json()["detail"]


def test_settings_post_token_en_query_no_autentica(tmp_path, monkeypatch):
    """Sellado 18: la query string JAMAS autentica."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.delenv("ORBIT_DSN_ADMIN", raising=False)
    resp = TestClient(app).post(
        f"/api/ads-optimizer/settings/{PLAT}",
        params={"x-orbit-token": TOKEN},
        json={"target_manual_pct": "20", "base_config_version_id": 1},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 5. proxima_config puro: omitir fraccion + resolver sin_fraccion
# ---------------------------------------------------------------------------


def test_proxima_config_apaga_margen_omite_clave_nunca_null():
    """E3: apagar margen = la config nueva OMITE la clave (nunca NULL)."""
    import app.config_write as config_write

    actual = {
        "ads_optimizer_mode": "live",
        CLAVE_TARGET: "20",
        CLAVE_FRACCION: "0.5",
        f"ads_apply_cap_{PLAT}_bid": "10",
    }
    nuevo, cambios = config_write.proxima_config(
        actual,
        PLAT,
        margen_habilitado=False,
    )
    assert CLAVE_FRACCION not in nuevo
    assert nuevo.get(CLAVE_FRACCION) is not None or CLAVE_FRACCION not in nuevo
    assert "ads_optimizer_mode" in nuevo and nuevo["ads_optimizer_mode"] == "live"
    assert CLAVE_TARGET in nuevo
    assert any("fraccion" in c or "margen" in c for c in cambios)


def test_apagar_fraccion_resolver_cae_a_sin_fraccion():
    """DoD: tras omitir la clave, fraccion_desde_settings -> None y
    resuelve_target_margen -> motivo sin_fraccion (cae al manual)."""
    import app.config_write as config_write

    actual = {CLAVE_TARGET: "20", CLAVE_FRACCION: "0.5"}
    nuevo, _ = config_write.proxima_config(actual, PLAT, margen_habilitado=False)
    assert CLAVE_FRACCION not in nuevo
    assert g.fraccion_desde_settings(nuevo, PLAT) is None
    medicion = g.MedicionMargen(
        margen_neto_pct=Decimal("40"),
        cobertura=Decimal("0.98"),
        dias_con_venta=80,
        venta_cubierta=Decimal("1000"),
        ledger_fresco_at=dt.datetime(2026, 9, 4, tzinfo=dt.UTC),
        moneda="USD",
        ventana_desde=dt.date(2026, 5, 1),
        ventana_hasta=dt.date(2026, 8, 20),
    )
    res = g.resuelve_target_margen(
        medicion,
        g.fraccion_desde_settings(nuevo, PLAT),
        hoy=dt.date(2026, 9, 4),
        ultimo=Decimal("20"),
        setting=Decimal("20"),
    )
    assert res.motivo == "sin_fraccion"
    assert res.aplicado is None


def test_proxima_config_enciende_margen_escribe_fraccion():
    import app.config_write as config_write

    actual = {CLAVE_TARGET: "20", "ads_optimizer_mode": "live"}
    nuevo, _ = config_write.proxima_config(
        actual,
        PLAT,
        margen_habilitado=True,
        fraccion=Decimal("0.5"),
    )
    assert nuevo[CLAVE_FRACCION] == "0.5"
    assert g.fraccion_desde_settings(nuevo, PLAT) == Decimal("0.5")


def test_proxima_config_no_toca_mode():
    """E6: ads_optimizer_mode no se edita desde settings."""
    import app.config_write as config_write

    actual = {"ads_optimizer_mode": "shadow", CLAVE_TARGET: "20"}
    nuevo, _ = config_write.proxima_config(actual, PLAT, target_manual_pct=Decimal("22"))
    assert nuevo["ads_optimizer_mode"] == "shadow"


def test_proxima_config_exige_ack_si_manual_con_margen_on():
    """E2: editar target manual con margen encendido sin ack -> error."""
    import app.config_write as config_write

    actual = {CLAVE_TARGET: "20", CLAVE_FRACCION: "0.5"}
    with pytest.raises(config_write.SettingsInvalido) as exc:
        config_write.proxima_config(
            actual,
            PLAT,
            target_manual_pct=Decimal("25"),
            ack_respaldo=False,
        )
    assert "respaldo" in str(exc.value).lower() or "gobierna" in str(exc.value).lower()


def test_proxima_config_manual_igual_con_margen_on_no_exige_ack():
    """E2 es sobre EDITAR el manual: reenviar el mismo valor (el form manda
    el campo prellenado) con el margen encendido no es una edicion y no pide
    ack; el cambio real (un cap) pasa solo."""
    import app.config_write as config_write

    actual = {CLAVE_TARGET: "20", CLAVE_FRACCION: "0.5"}
    nuevo, cambios = config_write.proxima_config(
        actual, PLAT, target_manual_pct=Decimal("20"), caps={"bid": 3}, ack_respaldo=False
    )
    assert nuevo[CLAVE_TARGET] == "20" and nuevo[f"ads_apply_cap_{PLAT}_bid"] == "3"
    assert cambios == ["cap bid ausente -> 3"]


def test_proxima_config_fraccion_null_cuenta_como_apagada():
    """Una clave con JSON null es margen APAGADO para el resolver
    (fraccion_desde_settings -> None): editar el manual no pide ack y apagar
    limpia la clave a medias."""
    import app.config_write as config_write

    actual = {CLAVE_TARGET: "20", CLAVE_FRACCION: None}
    assert g.fraccion_desde_settings(actual, PLAT) is None
    nuevo, _ = config_write.proxima_config(actual, PLAT, target_manual_pct=Decimal("25"))
    assert nuevo[CLAVE_TARGET] == "25"
    nuevo, cambios = config_write.proxima_config(actual, PLAT, margen_habilitado=False)
    assert CLAVE_FRACCION not in nuevo
    assert cambios == ["margen apagado (fraccion None -> ausente)"]


# ---------------------------------------------------------------------------
# 3. Config = fila NUEVA (append-only)
# ---------------------------------------------------------------------------


@_skip_db
def test_guarda_config_inserta_fila_nueva_con_label_settings_ui():
    """DoD: cambio de config = fila NUEVA; label con actor settings-ui."""
    import app.config_write as config_write

    with _db_con_rol_admin("orbit_cfg_new") as (conn, _dsn_a, _dsn_l):
        id_viejo = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            (
                "previa",
                Json(
                    {
                        "ads_optimizer_mode": "live",
                        CLAVE_TARGET: "20",
                        CLAVE_FRACCION: "0.5",
                        f"ads_apply_cap_{PLAT}_bid": "5",
                    }
                ),
            ),
        ).fetchone()[0]
        resultado = config_write.guarda_config(
            conn,
            platform=PLAT,
            base_config_version_id=id_viejo,
            target_manual_pct=Decimal("21"),
            ack_respaldo=True,
            ahora=dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.UTC),
        )
        assert resultado["config_version_id"] > id_viejo
        fila = conn.execute(
            "SELECT id, label, settings FROM config_version WHERE id = %s",
            (resultado["config_version_id"],),
        ).fetchone()
        assert fila[0] == resultado["config_version_id"]
        assert "settings-ui" in fila[1]
        assert "settings UI" in fila[1]
        assert fila[2][CLAVE_TARGET] == "21"
        assert fila[2][CLAVE_FRACCION] == "0.5"
        assert fila[2]["ads_optimizer_mode"] == "live"
        # La vieja sigue intacta (append-only)
        vieja = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s", (id_viejo,)
        ).fetchone()[0]
        assert vieja[CLAVE_TARGET] == "20"


@_skip_db
def test_guarda_config_apaga_margen_sin_clave_en_fila_nueva():
    import app.config_write as config_write

    with _db_con_rol_admin("orbit_cfg_off") as (conn, _dsn_a, _dsn_l):
        id_viejo = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            (
                "con-margen",
                Json({CLAVE_TARGET: "20", CLAVE_FRACCION: "0.5", "ads_optimizer_mode": "live"}),
            ),
        ).fetchone()[0]
        resultado = config_write.guarda_config(
            conn,
            platform=PLAT,
            base_config_version_id=id_viejo,
            margen_habilitado=False,
            ahora=dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.UTC),
        )
        settings = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s",
            (resultado["config_version_id"],),
        ).fetchone()[0]
        assert CLAVE_FRACCION not in settings
        assert g.fraccion_desde_settings(settings, PLAT) is None


# ---------------------------------------------------------------------------
# 2. Goal UPDATE + updated_at (reuso goals_write; UI lo despacha)
# ---------------------------------------------------------------------------


@_skip_db
def test_edicion_goal_desde_settings_actualiza_updated_at():
    """DoD: edicion de goal = UPDATE con updated_at nuevo (goals_write)."""
    from app import goals_write

    with _db_con_rol_admin("orbit_set_goal") as (conn, _dsn_a, _dsn_l):
        gid = _siembra_goal_plataforma(conn, target="25")
        antes = conn.execute(
            "SELECT updated_at FROM ads_optimizer_goal WHERE id = %s", (gid,)
        ).fetchone()[0]
        assert antes == T_SEMBRADO
        fila = goals_write.edita_goal(
            conn, gid, target_acos_pct=Decimal("18"), updated_at=T_EDITADO
        )
        assert fila["updated_at"] == T_EDITADO
        # edita_goal deja dict_row en la conexion: leer por nombre.
        despues = conn.execute(
            "SELECT target_acos_pct, updated_at FROM ads_optimizer_goal WHERE id = %s",
            (gid,),
        ).fetchone()
        assert despues["target_acos_pct"] == Decimal("18")
        assert despues["updated_at"] == T_EDITADO


# ---------------------------------------------------------------------------
# 4 + 6. UI: advertencia + procedencia
# ---------------------------------------------------------------------------


@_skip_db
def test_pagina_settings_muestra_procedencia_y_advertencia_con_margen(monkeypatch):
    """E1 + E2: /settings muestra peldano vigente y la advertencia de respaldo
    cuando el margen esta encendido."""
    from test_api_dashboard import _campana, _config_version, _db_temporal, _goal_db

    with _db_temporal("orbit_set_ui") as (conn, dsn_read):
        settings = {
            "ads_optimizer_mode": "live",
            CLAVE_TARGET: "20",
            CLAVE_FRACCION: "0.5",
            f"ads_apply_cap_{PLAT}_bid": "10",
            f"ads_apply_cap_{PLAT}_pause": "2",
            f"ads_apply_cap_{PLAT}_negative": "5",
            f"ads_apply_cap_{PLAT}_harvest": "2",
            "ads_target_acos_pct_amazon_mx": "20",
            "ads_apply_cap_amazon_mx_bid": "10",
            "ads_apply_cap_amazon_mx_pause": "2",
            "ads_apply_cap_amazon_mx_negative": "5",
            "ads_apply_cap_amazon_mx_harvest": "2",
        }
        _config_version(conn, settings)
        _campana(conn, PLAT, "c-set")
        _goal_db(conn, scope="platform", platform=PLAT, target=None)
        # Ciclo con margen gobernando (notes.target) para que la cascada
        # reporte margen_plataforma en la pantalla.
        conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform, status, notes) VALUES"
            " ('live', %s::platform, 'done', %s)",
            (
                PLAT,
                Json(
                    {
                        "target": {
                            "procedencia": "margen_plataforma",
                            "target_aplicado": "19.5",
                        }
                    }
                ),
            ),
        )
        # La UI abre ConexionLectura -> ORBIT_DSN_READ (mismo patron que test_ui)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_read)
        resp = TestClient(app).get("/settings")
        assert resp.status_code == 200, resp.text
        html = resp.text
        assert 'data-pantalla="settings"' in html
        # El ciclo sembrado resolvio margen_plataforma (notes TEXT con JSON):
        # la pantalla debe decir ESE peldano y su aplicado, no el setting.
        assert "margen_plataforma" in html and "19.5" in html
        assert ADVERTENCIA_RESPALDO.split(";")[0] in html.lower() or (
            "NO gobierna" in html or "no gobierna" in html.lower()
        )
        assert "ads_optimizer_mode" in html or "live" in html
        # harvest no editable: no inputs harvest_*
        assert 'name="harvest_campaign_id"' not in html


@_skip_db
def test_endpoint_get_settings_expone_procedencia_por_plataforma(monkeypatch):
    """GET /api/dashboard/settings trae peldano por plataforma (E1)."""
    from test_api_dashboard import _config_version, _db_temporal, _goal_db

    with _db_temporal("orbit_set_api") as (conn, dsn_read):
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "live",
                CLAVE_TARGET: "20",
                "ads_target_acos_pct_amazon_mx": "22",
            },
        )
        _goal_db(conn, scope="platform", platform=PLAT, target=None)
        _goal_db(conn, scope="platform", platform="amazon_mx", target=None)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_read)
        resp = TestClient(app).get("/api/dashboard/settings")
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert "plataformas" in cuerpo
        plats = {p["plataforma"]: p for p in cuerpo["plataformas"]}
        assert PLAT in plats
        assert "peldano" in plats[PLAT]["target_vigente"]
        assert plats[PLAT]["target_vigente"]["peldano"] in g.PELDANOS_CASCADA
        assert cuerpo.get("modo_global") == "live"
        # Fraccion ausente => margen apagado
        assert plats[PLAT]["margen_habilitado"] is False


def test_target_margen_del_ciclo_acepta_notes_como_string_json():
    """Regresion: notes jsonb a veces llega como str (driver sin codec).
    Antes se descartaba y la UI mentia setting_plataforma (regla 9)."""
    from app import api_dashboard as dash

    class _Conn:
        def execute(self, sql, params=None):
            class _R:
                def fetchone(self_inner):
                    return (
                        '{"target": {"procedencia": "margen_plataforma",'
                        ' "target_aplicado": "19.5"}}',
                    )

            return _R()

    assert dash._target_margen_del_ciclo(_Conn(), "amazon_us") == Decimal("19.5")


@_skip_db
def test_endpoint_post_settings_sin_ack_con_margen_422(tmp_path, monkeypatch):
    """E2 en el wire: target_manual con margen ON y sin ack_respaldo -> 422."""
    with _db_con_rol_admin("orbit_cfg_ack") as (conn, dsn_admin, _dsn_l):
        id_viejo = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("m", Json({CLAVE_TARGET: "20", CLAVE_FRACCION: "0.5", "ads_optimizer_mode": "live"})),
        ).fetchone()[0]
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        resp = TestClient(app).post(
            f"/api/ads-optimizer/settings/{PLAT}",
            json={
                "base_config_version_id": id_viejo,
                "target_manual_pct": "25",
                "ack_respaldo": False,
            },
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422
        # No se inserto fila nueva
        n = conn.execute("SELECT count(*) FROM config_version").fetchone()[0]
        assert n == 1
