"""Revision web (solo reproduccion; no corrige nada).

Cada prueba FALLA mientras la falla exista: la asercion dice lo que deberia pasar.
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from psycopg.types.json import Json  # noqa: E402
from test_api_write import TOKEN, _db_con_rol_admin, _secrets_token  # noqa: E402
from test_goals_write import T_EDITADO, _ConnFila, _fila_mxn  # noqa: E402
from test_schema import _postgres_obligatorio_ausente  # noqa: E402

import app.config_write as config_write  # noqa: E402
from app import goals_write  # noqa: E402
from app.main import app  # noqa: E402

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(), reason="sin Postgres local de pruebas"
)

PLAT = "amazon_mx"


def _base():
    return {"ads_optimizer_mode": "live", f"ads_target_acos_pct_{PLAT}": "20"}


# --- F1: meta de ACoS sin rango ------------------------------------------------


@pytest.mark.parametrize("tecleado", ["0.2", "2000"])
def test_target_manual_fuera_de_banda_se_rechaza(tecleado):
    """El target derivado del margen vive en la banda [10, 45]; el manual no
    tiene banda: 0.2 (el dueno confunde con la fraccion 0.5 del mismo form) o
    2000 (un cero de mas) se guardan y el motor recorta/sube con eso."""
    with pytest.raises(config_write.SettingsInvalido):
        config_write.proxima_config(_base(), PLAT, target_manual_pct=Decimal(tecleado))


@pytest.mark.parametrize("tecleado", ["0.2", "2000"])
def test_target_de_goal_fuera_de_banda_se_rechaza(tecleado):
    conn = _ConnFila(_fila_mxn(bid_floor=Decimal("1"), bid_ceiling=Decimal("45")))
    with pytest.raises(goals_write.GoalInvalido):
        goals_write.edita_goal(conn, 7, target_acos_pct=Decimal(tecleado), updated_at=T_EDITADO)


def test_techo_de_puja_de_goal_sin_tope_se_rechaza():
    """Techo por defecto MXN = 45.00. Un techo de 4500 MXN (sin el punto) pasa."""
    conn = _ConnFila(_fila_mxn(bid_floor=Decimal("1"), bid_ceiling=Decimal("45")))
    with pytest.raises(goals_write.GoalInvalido):
        goals_write.edita_goal(conn, 7, bid_ceiling=Decimal("4500"), updated_at=T_EDITADO)


@_skip_db
def test_endpoint_settings_guarda_target_2000(tmp_path, monkeypatch):
    """Por el cable, igual que la pantalla: 200 y fila nueva con "2000"."""
    with _db_con_rol_admin("orbit_rev_tgt") as (conn, dsn_admin, _dsn_l):
        vieja = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json(_base())),
        ).fetchone()[0]
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        resp = TestClient(app).post(
            f"/api/ads-optimizer/settings/{PLAT}",
            json={"base_config_version_id": vieja, "target_manual_pct": "2000"},
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code == 422, f"se guardo un target de 2000%: {resp.text}"


# --- F2: errores de validacion que la pantalla pinta como [object Object] ------


def test_error_422_de_settings_es_texto_legible(tmp_path, monkeypatch):
    """settings.js pinta `"Error: " + r.data.detail`. Si el dueno teclea
    "20%" o un cap con decimales, FastAPI responde detail = LISTA de objetos
    y la pantalla muestra "Error: [object Object]"."""
    _secrets_token(tmp_path, monkeypatch)
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://nadie@127.0.0.1:1/x")

    class _Nada:
        def close(self):
            pass

    import app.api_write as api_write

    monkeypatch.setattr(api_write, "connect", lambda _dsn: _Nada())
    resp = TestClient(app).post(
        f"/api/ads-optimizer/settings/{PLAT}",
        json={"base_config_version_id": 1, "target_manual_pct": "20%"},
        headers={"x-orbit-token": TOKEN},
    )
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str), (
        f"detail no es texto; la pantalla mostraria [object Object]: {resp.json()['detail']!r}"
    )


@_skip_db
def test_cap_ilegible_se_descarta_en_silencio_y_dice_guardado(tmp_path, monkeypatch):
    """settings.js manda caps con Number(valor): "1,000" -> NaN -> JSON null.
    La API toma null como "no tocar": si va junto a otro cambio, responde
    200 ("Guardado") y el cap tecleado nunca se guardo."""
    with _db_con_rol_admin("orbit_rev_cap") as (conn, dsn_admin, _dsn_l):
        vieja = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
            ("previa", Json(_base())),
        ).fetchone()[0]
        _secrets_token(tmp_path, monkeypatch)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", dsn_admin)
        # cuerpo identico al que arma settings.js con cap_bid="1,000" y target 21
        resp = TestClient(app).post(
            f"/api/ads-optimizer/settings/{PLAT}",
            json={
                "base_config_version_id": vieja,
                "ack_respaldo": False,
                "target_manual_pct": "21",
                "caps": {"bid": None},
            },
            headers={"x-orbit-token": TOKEN},
        )
        assert resp.status_code != 200, (
            f"el cap ilegible se tiro y la pantalla dice Guardado: {resp.json()}"
        )


def test_settings_js_number_de_cap_con_coma_viaja_como_null():
    """La mitad JS de la falla anterior: JSON.stringify({bid: Number('1,000')})."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("sin node")
    salida = subprocess.run(
        [node, "-e", "console.log(JSON.stringify({bid: Number('1,000')}))"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(salida)["bid"] is not None, f"settings.js manda {salida.strip()}"


_ = dt  # fecha disponible para variantes
