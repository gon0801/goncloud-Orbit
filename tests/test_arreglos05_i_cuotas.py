"""Regresiones del bloque I de ARREGLOS 05: Ajustes rechaza una cuota que no
puede guardar.

Contrato: el formulario no envia `1,000`; la API distingue campo omitido de
`null` invalido; `null` explicito, booleanos y fracciones ilegibles en una cuota
se rechazan SIN crear version nueva; omitir una cuota conserva su valor; un
`null` valido de otro campo se guarda; un entero valido con version actual
guarda y apila UNA fila de snapshot; con version vieja responde 409 sin tocar
nada.

El defecto del padre: `settings.js` hacia `Number("1,000") -> NaN -> null`, la
API aceptaba `null` en una cuota (y pydantic convertia `true` en `1`) y
`proxima_config` saltaba la llave con `if cap is None: continue`; la pantalla
decia `Guardado` habiendo ignorado la cuota.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json
from test_api_write import TOKEN, _db_con_rol_admin, _secrets_token
from test_schema import _postgres_obligatorio_ausente

import app.config_write as config_write
from app.main import app

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

PLAT = "amazon_us"
CLAVE_TARGET = f"ads_target_acos_pct_{PLAT}"
CLAVE_CAP_BID = f"ads_apply_cap_{PLAT}_bid"
CLAVE_CAP_PAUSE = f"ads_apply_cap_{PLAT}_pause"
URL = f"/api/ads-optimizer/settings/{PLAT}"


def _base_settings(**extra) -> dict:
    settings = {
        "ads_optimizer_mode": "shadow",
        CLAVE_TARGET: "20",
        CLAVE_CAP_BID: "10",
        CLAVE_CAP_PAUSE: "2",
    }
    settings.update(extra)
    return settings


def _siembra(conn, **extra) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("i-base", Json(_base_settings(**extra))),
    ).fetchone()[0]


def _filas(conn) -> int:
    return conn.execute("SELECT count(*) FROM config_version").fetchone()[0]


def _vigente(conn) -> dict:
    return conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[
        0
    ]


def _cliente(tmp_path, monkeypatch, dsn_admin) -> TestClient:
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Frontera: la cuota cruda llega al validador canonico (no se coacciona antes)
# ---------------------------------------------------------------------------


def test_arreglos05_i_cuerpo_settings_conserva_la_cuota_cruda():
    """Un `true` en una cuota NO se convierte en 1 en el modelo HTTP: la
    decision (y el mensaje) es del validador canonico, no de pydantic."""
    from app.api_write import CuerpoSettings

    cuerpo = CuerpoSettings(base_config_version_id=1, caps={"bid": True, "pause": None})
    assert cuerpo.caps == {"bid": True, "pause": None}


# ---------------------------------------------------------------------------
# Validacion pura: un solo camino, el de cap_desde_settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mala", [None, True, False, 1.5, "1.5", "1,000", "-1"])
def test_arreglos05_i_proxima_config_rechaza_cuota_que_no_puede_guardar(mala):
    """`null`, booleanos, fracciones y texto ilegible se rechazan; ademas con
    una edicion valida al lado, para que el rechazo no sea una edicion vacia."""
    base = _base_settings()
    with pytest.raises(config_write.SettingsInvalido):
        config_write.proxima_config(base, PLAT, caps={"bid": mala}, target_manual_pct=Decimal("25"))


def test_arreglos05_i_proxima_config_normaliza_el_entero_valido():
    """Un entero valido se guarda con la forma canonica de la config ("7"), no
    con el texto de entrada ("007")."""
    nuevo, cambios = config_write.proxima_config(_base_settings(), PLAT, caps={"bid": "007"})
    assert nuevo[CLAVE_CAP_BID] == "7"
    assert cambios == ["cap bid 10 -> 7"]


def test_arreglos05_i_proxima_config_cuota_omitida_conserva_y_guarda_lo_demas():
    """Omitir una cuota no la toca; las demas ediciones validas si se guardan."""
    nuevo, cambios = config_write.proxima_config(
        _base_settings(), PLAT, caps={"pause": 4}, target_manual_pct=Decimal("25")
    )
    assert nuevo[CLAVE_CAP_BID] == "10"
    assert nuevo[CLAVE_CAP_PAUSE] == "4"
    assert nuevo[CLAVE_TARGET] == "25"
    assert cambios == ["target manual 20 -> 25", "cap pause 2 -> 4"]


# ---------------------------------------------------------------------------
# HTTP + PG: rechazos sin version nueva
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_i_api_null_explicito_en_cuota_rechaza_sin_version(tmp_path, monkeypatch):
    with _db_con_rol_admin("orbit_i_null") as (conn, dsn_admin, _dsn_l):
        base = _siembra(conn)
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin)
        resp = cliente.post(
            URL,
            json={
                "base_config_version_id": base,
                "target_manual_pct": "25",
                "caps": {"bid": None},
            },
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422, resp.text
        assert "cap bid" in resp.json()["detail"]
        assert "null" in resp.json()["detail"]
        assert _filas(conn) == 1
        assert _vigente(conn)[CLAVE_TARGET] == "20"


@_skip_db
def test_arreglos05_i_api_booleano_en_cuota_rechaza_sin_version(tmp_path, monkeypatch):
    with _db_con_rol_admin("orbit_i_bool") as (conn, dsn_admin, _dsn_l):
        base = _siembra(conn)
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin)
        resp = cliente.post(
            URL,
            json={
                "base_config_version_id": base,
                "target_manual_pct": "25",
                "caps": {"bid": True},
            },
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422, resp.text
        assert "booleano" in resp.json()["detail"]
        assert _filas(conn) == 1
        assert _vigente(conn)[CLAVE_CAP_BID] == "10"


@pytest.mark.parametrize("mala", [1.5, "1.5", "1,000"])
@_skip_db
def test_arreglos05_i_api_fraccion_o_texto_ilegible_rechaza_sin_version(
    tmp_path, monkeypatch, mala
):
    with _db_con_rol_admin("orbit_i_frac") as (conn, dsn_admin, _dsn_l):
        base = _siembra(conn)
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin)
        resp = cliente.post(
            URL,
            json={
                "base_config_version_id": base,
                "target_manual_pct": "25",
                "caps": {"bid": mala},
            },
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422, resp.text
        assert _filas(conn) == 1
        assert _vigente(conn)[CLAVE_CAP_BID] == "10"


# ---------------------------------------------------------------------------
# HTTP + PG: caminos validos y version optimista
# ---------------------------------------------------------------------------


@_skip_db
def test_arreglos05_i_api_cuota_omitida_conserva_y_apila_una_fila(tmp_path, monkeypatch):
    with _db_con_rol_admin("orbit_i_omit") as (conn, dsn_admin, _dsn_l):
        base = _siembra(conn)
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin)
        resp = cliente.post(
            URL,
            json={"base_config_version_id": base, "caps": {"pause": 4}},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 200, resp.text
        assert _filas(conn) == 2
        guardada = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s",
            (resp.json()["config_version_id"],),
        ).fetchone()[0]
        assert guardada[CLAVE_CAP_BID] == "10"
        assert guardada[CLAVE_CAP_PAUSE] == "4"
        assert guardada[CLAVE_TARGET] == "20"


@_skip_db
def test_arreglos05_i_api_null_valido_en_otro_campo_se_guarda(tmp_path, monkeypatch):
    """El rechazo de null es SOLO para cuotas: `resumen_acos_objetivo` conserva
    su null valido (quitar la meta) y se guarda."""
    with _db_con_rol_admin("orbit_i_nullok") as (conn, dsn_admin, _dsn_l):
        base = _siembra(conn, resumen_acos_objetivo={"amazon_us": "15"})
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin)
        resp = cliente.post(
            URL,
            json={
                "base_config_version_id": base,
                "resumen_acos_objetivo": {"amazon_us": None},
            },
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 200, resp.text
        assert _filas(conn) == 2
        guardada = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s",
            (resp.json()["config_version_id"],),
        ).fetchone()[0]
        assert "resumen_acos_objetivo" not in guardada
        assert guardada[CLAVE_CAP_BID] == "10"


@_skip_db
def test_arreglos05_i_api_entero_valido_guarda_una_version_y_snapshot(tmp_path, monkeypatch):
    with _db_con_rol_admin("orbit_i_ok") as (conn, dsn_admin, _dsn_l):
        base = _siembra(conn)
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin)
        resp = cliente.post(
            URL,
            json={"base_config_version_id": base, "caps": {"bid": 25}},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["created"] is True
        assert _filas(conn) == 2
        guardada = conn.execute(
            "SELECT settings FROM config_version WHERE id = %s",
            (resp.json()["config_version_id"],),
        ).fetchone()[0]
        assert guardada[CLAVE_CAP_BID] == "25"


@_skip_db
def test_arreglos05_i_api_version_vieja_409_y_cero_cambios(tmp_path, monkeypatch):
    with _db_con_rol_admin("orbit_i_409") as (conn, dsn_admin, _dsn_l):
        base = _siembra(conn)
        cliente = _cliente(tmp_path, monkeypatch, dsn_admin)
        primera = cliente.post(
            URL,
            json={"base_config_version_id": base, "caps": {"bid": 25}},
            headers={"x-orbit-token": TOKEN},
        )
        assert primera.status_code == 200, primera.text
        segunda = cliente.post(
            URL,
            json={"base_config_version_id": base, "caps": {"bid": 30}},
            headers={"x-orbit-token": TOKEN},
        )
        assert segunda.status_code == 409, segunda.text
        assert _filas(conn) == 2
        assert _vigente(conn)[CLAVE_CAP_BID] == "25"
