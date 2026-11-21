"""Pruebas unitarias y en PG16 para los cimientos hoja de Ola B1.

Cubre:
- `app/secrets.py`: resolucion canonica de `ORBIT_SECRETS_DIR` (vacia/ausente).
- `app/ads/sp_wire.py`: parser unico fail-closed de HTTP 207 y extraccion de ids.
- `app/ceremonia.py`: validacion fail-closed de `--acepto-mutacion-real`,
  `--huella`, `--esperado` y `--go`.
- `app/lock.py`: claim atomico, heartbeat, release y `tomar_lock_con_advisory`
  en PostgreSQL 16 real (sin fugar el claim si falla o bloquea el advisory).
- `app/db.py`: `config_vigente` y `settings_vigentes` (con tuple_row y dict_row).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import psycopg
import pytest
from helpers.pg import db_desde_template
from psycopg.rows import dict_row
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import ceremonia, db, lock, secrets
from app.ads import sp_wire

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def test_secrets_directorio_resuelve_vacio_y_definido(monkeypatch):
    monkeypatch.delenv("ORBIT_SECRETS_DIR", raising=False)
    assert secrets.directorio_secretos() == Path(secrets.DEFAULT_SECRETS_DIR)
    monkeypatch.setenv("ORBIT_SECRETS_DIR", "")
    assert secrets.directorio_secretos() == Path(secrets.DEFAULT_SECRETS_DIR)
    monkeypatch.setenv("ORBIT_SECRETS_DIR", "/tmp/secretos-prueba")
    assert secrets.directorio_secretos() == Path("/tmp/secretos-prueba")


def test_sp_wire_errores_put_e_ids():
    r_vacio = httpx.Response(207, json={})
    assert sp_wire.errores_put(r_vacio, "campaigns")
    r_sin_json = httpx.Response(207, content=b"no-json")
    assert sp_wire.errores_put(r_sin_json, "campaigns") == ["respuesta sin JSON"]
    r_ok = httpx.Response(207, json={"campaigns": {"success": [{"campaignId": "1"}], "error": []}})
    assert sp_wire.errores_put(r_ok, "campaigns") == []
    assert sp_wire.errores_de_ack({"keywords": {"error": [{"code": "BAD"}]}}) == [{"code": "BAD"}]
    assert sp_wire.id_de_ack({"keywords": {"success": [{"keywordId": "99"}]}}, "keywordId") == "99"
    assert (
        sp_wire.id_product_ad_de_ack({"productAds": {"success": [{"productAdId": "77"}]}}) == "77"
    )
    assert sp_wire.id_product_ad_de_ack({"productAds": {"success": [{"adId": True}]}}) is None
    # Regresion Devin #706: un id presente pero invalido (blanco/booleano)
    # debe distinguirse de un id ausente y rechazar el delete en reversa.
    assert sp_wire.estado_id_de_ack({"keywords": {"success": [{}]}}, "keywordId") == (False, None)
    assert sp_wire.estado_id_de_ack(
        {"keywords": {"success": [{"keywordIdList": [" "]}]}}, "keywordId"
    ) == (True, None)
    assert sp_wire.estado_id_de_ack(
        {"keywords": {"success": [{"keywordIdList": [True]}]}}, "keywordId"
    ) == (True, None)
    assert sp_wire.estado_id_de_ack(
        {"keywords": {"success": [{"keywordIdList": ["123"]}]}}, "keywordId"
    ) == (True, "123")


def test_reversa_rechazada_falla_cerrado_con_id_invalido():
    from app.apply_harvest import _reversa_rechazada

    assert _reversa_rechazada({"keywords": {"success": [{}]}}, "keywordId", "123") is False
    assert (
        _reversa_rechazada(
            {"keywords": {"success": [{"keywordIdList": ["123"]}]}}, "keywordId", "123"
        )
        is False
    )
    assert (
        _reversa_rechazada(
            {"keywords": {"success": [{"keywordIdList": [" "]}]}}, "keywordId", "123"
        )
        is True
    )
    assert (
        _reversa_rechazada(
            {"keywords": {"success": [{"keywordIdList": [True]}]}}, "keywordId", "123"
        )
        is True
    )
    assert (
        _reversa_rechazada({"keywords": {"success": [{"keywordId": ""}]}}, "keywordId", "123")
        is True
    )


def test_ceremonia_dry_run_y_validaciones():
    assert ceremonia.valida_ceremonia(acepto=False) is False
    with pytest.raises(ceremonia.ErrorCeremonia, match="falto --acepto-mutacion-real"):
        ceremonia.valida_ceremonia(acepto=False, go="go")
    with pytest.raises(ceremonia.ErrorCeremonia, match="esperado"):
        ceremonia.valida_ceremonia(
            acepto=True,
            go="go",
            huella="h",
            huella_plan="h",
            esperado=2,
            real=3,
            exige_esperado=True,
        )
    with pytest.raises(ceremonia.ErrorCeremonia, match="falta --go"):
        ceremonia.valida_ceremonia(acepto=True, go="   ", huella="h", huella_plan="h")
    with pytest.raises(ceremonia.ErrorCeremonia, match="huella"):
        ceremonia.valida_ceremonia(acepto=True, go="go", huella="otra", huella_plan="h")
    with pytest.raises(ceremonia.ErrorCeremonia, match="sin go literal"):
        ceremonia.valida_ceremonia(
            acepto=True,
            go="go",
            huella="h",
            huella_plan="h",
            exige_go=False,
            rechaza_go_si_no_exige=True,
        )
    assert (
        ceremonia.valida_ceremonia(
            acepto=True,
            go="go dueno",
            huella="h1",
            huella_plan="h1",
            esperado=4,
            real=4,
            exige_esperado=True,
        )
        is True
    )


@_skip_db
def test_lock_y_config_vigente_en_pg16():
    class _Vacia:
        def execute(self, _sql):
            return self

        def fetchone(self):
            return None

    assert db.config_vigente(_Vacia()) is None
    assert db.settings_vigentes(_Vacia()) is None
    with db_desde_template(prefijo="orbit_ola_b1", base=_test_dsn(), autocommit=True) as (
        conn,
        dsn,
    ):
        cid = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('b1', %s) RETURNING id",
            (Json({"clave": "valor"}),),
        ).fetchone()[0]
        assert db.config_vigente(conn) == (cid, {"clave": "valor"})
        assert db.settings_vigentes(conn) == {"clave": "valor"}
        with conn.cursor(row_factory=dict_row) as cur:
            assert db.config_vigente(cur) == (cid, {"clave": "valor"})

        class _Ocupado(RuntimeError):
            pass

        lock.tomar_lock_con_advisory(
            conn,
            "precio:amazon_mx",
            "owner-1",
            ttl_seconds=600,
            error_cls=_Ocupado,
            mensaje_ocupado="ocupado",
        )
        with (
            psycopg.connect(dsn, autocommit=True) as conn2,
            pytest.raises(_Ocupado, match="ocupado"),
        ):
            lock.tomar_lock_con_advisory(
                conn2,
                "precio:amazon_mx",
                "owner-2",
                ttl_seconds=600,
                error_cls=_Ocupado,
                mensaje_ocupado="ocupado",
            )
        lock.latir_lock(conn, "precio:amazon_mx", "owner-1", etiqueta="test")
        lock.soltar_lock_con_advisory(conn, "precio:amazon_mx", "owner-1")
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_optimizer_lock WHERE job_key = 'precio:amazon_mx'"
            ).fetchone()[0]
            == 0
        )
