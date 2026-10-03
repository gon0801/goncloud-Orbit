"""Confianzas del motor (A3): proxima_config/guarda_config con
confianza_recorte/confianza_subida por plataforma.

Casos del plan: el label nombra ambos valores; fraccion sin cambio es
no-op; segundo guardado identico es no-op; 0.40 y 1.0 se rechazan sin
fila nueva; cada plataforma conserva sus valores.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json
from test_api_write import TOKEN, _db_con_rol_admin, _secrets_token
from test_schema import _postgres_obligatorio_ausente

import app.config_write as config_write
from app.main import app
from app.optimizer import goals as g

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

PLAT = "amazon_us"
OTRA = "amazon_mx"
AHORA = dt.datetime(2026, 10, 2, 12, 0, tzinfo=dt.UTC)


def _base(fraccion="0.5"):
    return {
        "ads_optimizer_mode": "live",
        f"ads_target_acos_pct_{PLAT}": "20",
        f"ads_target_fraccion_margen_{PLAT}": fraccion,
    }


@_skip_db
def test_guardar_confianzas_label_nombra_ambos_valores():
    """Plan A3: guardar un cambio escribe fila nueva con label que nombra
    ambos valores; lo demas viaja intacto."""
    with _db_con_rol_admin("orbit_cfg_conf") as (conn, _a, _b):
        vieja = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json(_base())),
        ).fetchone()[0]
        salida = config_write.guarda_config(
            conn,
            platform=PLAT,
            base_config_version_id=vieja,
            ahora=AHORA,
            confianza_recorte=Decimal("0.90"),
            confianza_subida=Decimal("0.60"),
        )
        fila = conn.execute(
            "SELECT label, settings FROM config_version WHERE id = %s",
            (salida["config_version_id"],),
        ).fetchone()
        assert "confianza recorte ausente -> 0.90" in fila[0]
        assert "confianza subida ausente -> 0.60" in fila[0]
        assert fila[1][f"ads_confianza_recorte_{PLAT}"] == "0.90"
        assert fila[1][f"ads_confianza_subida_{PLAT}"] == "0.60"
        assert fila[1][f"ads_target_fraccion_margen_{PLAT}"] == "0.5"
        assert g.confianza_recorte_desde_settings(fila[1], PLAT) == Decimal("0.90")
        assert g.confianza_subida_desde_settings(fila[1], PLAT) == Decimal("0.60")


def test_fraccion_sin_cambio_es_noop():
    """Plan A3: guardar la fraccion vigente sin tocar nada es edicion
    vacia (no escribe fila)."""
    with pytest.raises(config_write.SettingsInvalido, match="edicion vacia"):
        config_write.proxima_config(
            _base(),
            PLAT,
            margen_habilitado=True,
            fraccion=Decimal("0.5"),
        )


@_skip_db
def test_segundo_guardado_identico_es_noop():
    """Plan A3: repetir la misma edicion no escribe otra fila."""
    with _db_con_rol_admin("orbit_cfg_conf2") as (conn, _a, _b):
        vieja = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json(_base())),
        ).fetchone()[0]
        primera = config_write.guarda_config(
            conn,
            platform=PLAT,
            base_config_version_id=vieja,
            ahora=AHORA,
            confianza_recorte=Decimal("0.90"),
        )
        with pytest.raises(config_write.SettingsInvalido, match="edicion vacia"):
            config_write.guarda_config(
                conn,
                platform=PLAT,
                base_config_version_id=primera["config_version_id"],
                ahora=AHORA,
                confianza_recorte=Decimal("0.90"),
            )
        total = conn.execute("SELECT count(*) FROM config_version").fetchone()[0]
        assert total == 2


@_skip_db
@pytest.mark.parametrize("mala", ["0.40", "1.0", "1.5", "0.49"])
def test_confianza_fuera_de_rango_rechazada_sin_fila(mala):
    """Plan A3: 0.40 y 1.0 (y vecinos) se rechazan sin fila nueva."""
    with _db_con_rol_admin("orbit_cfg_conf3") as (conn, _a, _b):
        vieja = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json(_base())),
        ).fetchone()[0]
        with pytest.raises(config_write.SettingsInvalido, match="confianza"):
            config_write.guarda_config(
                conn,
                platform=PLAT,
                base_config_version_id=vieja,
                ahora=AHORA,
                confianza_subida=Decimal(mala),
            )
        total = conn.execute("SELECT count(*) FROM config_version").fetchone()[0]
        assert total == 1


@_skip_db
def test_endpoint_post_confianzas_wire_200_y_422(tmp_path, monkeypatch):
    """F2 AI-review PR #383: el contrato por el wire que usa el dueno
    (0.99 -> 200 con "0.99" exacto en la fila; 1.0 y 0.40 -> 422)."""
    with _db_con_rol_admin("orbit_cfg_wire") as (conn, dsn_admin, _dsn_l):
        vieja = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json(_base())),
        ).fetchone()[0]
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        cliente = TestClient(app)
        headers = {"x-orbit-token": TOKEN}
        url = f"/api/ads-optimizer/settings/{PLAT}"
        ok = cliente.post(
            url,
            json={
                "base_config_version_id": vieja,
                "confianza_recorte": "0.99",
                "confianza_subida": "0.50",
            },
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        settings = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s",
            (ok.json()["config_version_id"],),
        ).fetchone()[0]
        assert settings[f"ads_confianza_recorte_{PLAT}"] == "0.99"
        assert settings[f"ads_confianza_subida_{PLAT}"] == "0.50"
        for mala in ("1.0", "0.40"):
            resp = cliente.post(
                url,
                json={
                    "base_config_version_id": ok.json()["config_version_id"],
                    "confianza_recorte": mala,
                },
                headers=headers,
            )
            assert resp.status_code == 422, (mala, resp.text)
        assert conn.execute("SELECT count(*) FROM config_version").fetchone()[0] == 2


@_skip_db
def test_get_settings_expone_confianzas_resueltas(monkeypatch):
    """F2 AI-review PR #383: GET /api/dashboard/settings trae ambas claves
    resueltas (guardada + default relleno)."""
    from test_api_dashboard import _config_version, _db_temporal, _goal_db

    with _db_temporal("orbit_cfg_get") as (conn, dsn_read):
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "live",
                f"ads_target_acos_pct_{PLAT}": "20",
                "ads_target_acos_pct_amazon_mx": "22",
                f"ads_confianza_recorte_{PLAT}": "0.90",
            },
        )
        _goal_db(conn, scope="platform", platform=PLAT, target=None)
        _goal_db(conn, scope="platform", platform="amazon_mx", target=None)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_read)
        resp = TestClient(app).get("/api/dashboard/settings")
        assert resp.status_code == 200, resp.text
        plats = {p["plataforma"]: p for p in resp.json()["plataformas"]}
        assert plats[PLAT]["confianza_recorte"] == "0.90"
        assert plats[PLAT]["confianza_subida"] == "0.70"
        assert plats["amazon_mx"]["confianza_recorte"] == "0.80"


@_skip_db
def test_get_settings_fraccion_corrupta_no_se_muestra(monkeypatch):
    """Lane 5 A3: fraccion corrupta en la vigente = el GET NO muestra
    (500, igual que target y confianzas corruptas)."""
    from test_api_dashboard import _config_version, _db_temporal, _goal_db

    with _db_temporal("orbit_cfg_corrupt") as (conn, dsn_read):
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "live",
                f"ads_target_acos_pct_{PLAT}": "20",
                f"ads_target_fraccion_margen_{PLAT}": "5",
            },
        )
        _goal_db(conn, scope="platform", platform=PLAT, target=None)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_read)
        resp = TestClient(app, raise_server_exceptions=False).get("/api/dashboard/settings")
        assert resp.status_code == 500


def test_cada_plataforma_conserva_sus_valores():
    """Plan A3: editar una plataforma no toca las claves de la otra."""
    base = dict(_base())
    base[f"ads_confianza_recorte_{OTRA}"] = "0.95"
    nuevo, cambios = config_write.proxima_config(base, PLAT, confianza_recorte=Decimal("0.90"))
    assert nuevo[f"ads_confianza_recorte_{OTRA}"] == "0.95"
    assert nuevo[f"ads_confianza_recorte_{PLAT}"] == "0.90"
    # F4 AI-review PR #383: el rastro nombra SOLO el cambio us (nada de mx).
    assert cambios == ["confianza recorte ausente -> 0.90"]


# ---------------------------------------------------------------------------
# Interruptor del motor de bids (A6): ads_bid_politica_<platform>
# ---------------------------------------------------------------------------


def test_motor_bid_proxima_setea_revierte_y_no_toca():
    """A6-M9 (vacio-se-escribe): "evidencia_v2" ESCRIBE la clave,
    "bandas_v1" explicito REVIERTE (carril 10, A6-r2 B1), None no toca,
    ajeno (incluido "" y el ID viejo "evidencia") = SettingsInvalido,
    repetir valor = vacia."""
    k = f"ads_bid_politica_{PLAT}"
    nuevo, cambios = config_write.proxima_config(_base(), PLAT, motor_bid="evidencia_v2")
    assert nuevo[k] == "evidencia_v2"
    assert cambios == ["motor bid ausente -> evidencia_v2"]
    assert g.motor_evidencia_desde_settings(nuevo, PLAT) is True
    assert g.motor_evidencia_desde_settings(nuevo, OTRA) is False

    base_ev = dict(_base())
    base_ev[k] = "evidencia_v2"
    nuevo2, cambios2 = config_write.proxima_config(base_ev, PLAT, motor_bid="bandas_v1")
    assert nuevo2[k] == "bandas_v1"
    assert cambios2 == ["motor bid evidencia_v2 -> bandas_v1"]
    assert g.motor_evidencia_desde_settings(nuevo2, PLAT) is False

    for malo in ("", "evidencia", "v2"):
        with pytest.raises(config_write.SettingsInvalido, match="motor bid"):
            config_write.proxima_config(_base(), PLAT, motor_bid=malo)
    with pytest.raises(config_write.SettingsInvalido, match="edicion vacia"):
        config_write.proxima_config(base_ev, PLAT, motor_bid="evidencia_v2")
    with pytest.raises(config_write.SettingsInvalido, match="edicion vacia"):
        config_write.proxima_config(_base(), PLAT)


@_skip_db
def test_guardar_motor_bid_label_y_lector():
    """A6: guardar el interruptor escribe fila nueva con label y la
    config resultante pasa el lector del motor (regla 2)."""
    with _db_con_rol_admin("orbit_cfg_motor") as (conn, _a, _b):
        vieja = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json(_base())),
        ).fetchone()[0]
        salida = config_write.guarda_config(
            conn, platform=PLAT, base_config_version_id=vieja, ahora=AHORA, motor_bid="evidencia_v2"
        )
        fila = conn.execute(
            "SELECT label, settings FROM config_version WHERE id = %s",
            (salida["config_version_id"],),
        ).fetchone()
        assert "motor bid ausente -> evidencia_v2" in fila[0]
        assert fila[1][f"ads_bid_politica_{PLAT}"] == "evidencia_v2"
        assert g.motor_evidencia_desde_settings(fila[1], PLAT) is True
        # Volver a v1 escribe bandas_v1 explicito (carril 10, A6-r2 B1).
        salida2 = config_write.guarda_config(
            conn,
            platform=PLAT,
            base_config_version_id=salida["config_version_id"],
            ahora=AHORA,
            motor_bid="bandas_v1",
        )
        fila2 = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s",
            (salida2["config_version_id"],),
        ).fetchone()[0]
        assert fila2[f"ads_bid_politica_{PLAT}"] == "bandas_v1"


@_skip_db
def test_endpoint_post_motor_bid_wire_200_y_422(tmp_path, monkeypatch):
    """A6: el contrato por el wire (evidencia_v2 -> 200 con la clave en la
    fila; bandas_v1 -> 200 y revierte; ajeno -> 422 del Literal)."""
    with _db_con_rol_admin("orbit_cfg_motorw") as (conn, dsn_admin, _dsn_l):
        vieja = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json(_base())),
        ).fetchone()[0]
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        cliente = TestClient(app)
        headers = {"x-orbit-token": TOKEN}
        url = f"/api/ads-optimizer/settings/{PLAT}"
        ok = cliente.post(
            url,
            json={"base_config_version_id": vieja, "motor_bid": "evidencia_v2"},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        settings = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s",
            (ok.json()["config_version_id"],),
        ).fetchone()[0]
        assert settings[f"ads_bid_politica_{PLAT}"] == "evidencia_v2"
        malo = cliente.post(
            url,
            json={"base_config_version_id": ok.json()["config_version_id"], "motor_bid": "v2"},
            headers=headers,
        )
        assert malo.status_code == 422, malo.text
        vuelve = cliente.post(
            url,
            json={
                "base_config_version_id": ok.json()["config_version_id"],
                "motor_bid": "bandas_v1",
            },
            headers=headers,
        )
        assert vuelve.status_code == 200, vuelve.text
        settings2 = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s",
            (vuelve.json()["config_version_id"],),
        ).fetchone()[0]
        assert settings2[f"ads_bid_politica_{PLAT}"] == "bandas_v1"


def test_literal_motor_bid_api_pineado_contra_valor():
    """A6: el Literal del endpoint admite EXACTAMENTE los dos valores
    sellados; si goals cambia el vocabulario, este test truena."""
    from typing import get_args

    from app.api_write import CuerpoSettings

    anotacion = CuerpoSettings.model_fields["motor_bid"].annotation
    literales = [a for a in get_args(anotacion) if a is not type(None)]
    assert len(literales) == 1
    assert set(get_args(literales[0])) == {g.POLITICA_BANDAS_V1, g.POLITICA_BANDAS_EVIDENCIA}


@_skip_db
def test_get_settings_expone_motor_bid_crudo_y_vive(monkeypatch):
    """A6: GET /api/dashboard/settings trae motor_bid CRUDO (None =
    ausente) + motor_bid_vive RESUELTO por plataforma."""
    from test_api_dashboard import _config_version, _db_temporal, _goal_db

    with _db_temporal("orbit_cfg_getmotor") as (conn, dsn_read):
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "live",
                f"ads_target_acos_pct_{PLAT}": "20",
                "ads_target_acos_pct_amazon_mx": "22",
                f"ads_bid_politica_{PLAT}": "evidencia_v2",
            },
        )
        _goal_db(conn, scope="platform", platform=PLAT, target=None)
        _goal_db(conn, scope="platform", platform="amazon_mx", target=None)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_read)
        resp = TestClient(app).get("/api/dashboard/settings")
        assert resp.status_code == 200, resp.text
        plats = {p["plataforma"]: p for p in resp.json()["plataformas"]}
        assert plats[PLAT]["motor_bid"] == "evidencia_v2"
        assert plats[PLAT]["motor_bid_vive"] is True
        assert plats["amazon_mx"]["motor_bid"] is None
        assert plats["amazon_mx"]["motor_bid_vive"] is False


@_skip_db
def test_get_settings_motor_bid_corrupto_no_se_muestra(monkeypatch):
    """A6 lane 5: clave presente pero corrupta = el GET NO muestra (500,
    igual que target/fraccion/confianzas corruptas)."""
    from test_api_dashboard import _config_version, _db_temporal, _goal_db

    with _db_temporal("orbit_cfg_motorc") as (conn, dsn_read):
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "live",
                f"ads_target_acos_pct_{PLAT}": "20",
                f"ads_bid_politica_{PLAT}": "EVIDENCIA_V2",
            },
        )
        _goal_db(conn, scope="platform", platform=PLAT, target=None)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_read)
        resp = TestClient(app, raise_server_exceptions=False).get("/api/dashboard/settings")
        assert resp.status_code == 500
