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
    assert OTRA not in " ".join(cambios)
