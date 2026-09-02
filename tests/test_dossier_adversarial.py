"""Tests del expediente adversarial (ORBIT 05 tarea 2.1a)."""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import stat
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json
from test_architecture import _imports_runtime, _violaciones
from test_schema import SQL, SQL2, SQL3, _postgres_obligatorio_ausente, _test_dsn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import dossier_adversarial as da  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
DECIDED_AT = dt.datetime(2026, 9, 2, 16, 12, 50, tzinfo=dt.UTC)
KEYWORD = "calzas ninja"
SECRETO = "Atza|xxx"


@contextmanager
def _db_temporal(prefijo: str):
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)
        conn.execute(SQL2)
        conn.execute(SQL3)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _dsn_lectura(conn) -> str:
    return make_conninfo(_test_dsn(), dbname=conn.info.dbname)


def _inputs_bid() -> dict:
    return {
        "motor": "bid",
        "platform": "amazon_us",
        "ventanas": {
            "bids": {
                "window_start": "2026-07-18",
                "window_end": "2026-08-16",
                "fechas": 30,
                "cost": "36.0000",
                "ad_revenue": "100.0000",
                "revenue_same_sku": None,
                "clicks": 50,
                "orders": 5,
                "moneda": "USD",
                "observed_at_max": "2026-08-20T06:00:00+00:00",
            },
            "cortes": {
                "window_start": "2026-07-19",
                "window_end": "2026-08-17",
                "fechas": 30,
                "cost": "25.2100",
                "ad_revenue": "0.0000",
                "revenue_same_sku": None,
                "clicks": 120,
                "orders": 0,
                "moneda": "USD",
                "observed_at_max": "2026-08-25T08:06:11.871936+00:00",
            },
        },
        "goal": {
            "scope": "platform",
            "target_acos_pct": "25.00",
            "bid_floor": "0.4000",
            "bid_ceiling": "2.5000",
            "harvest": None,
        },
        "target_acos_pct_usado": "25.00",
        "bid_actual": "1.0000",
        "bid_moneda": "USD",
        "factor": "-0.25",
        "motivo": "banda_menos_25",
        "modo": "live",
        "corte": {"umbral_clicks_usado": 100, "cost_min_usado": "40", "elegible": False},
    }


def _siembra(conn, *, secreto: str | None = None) -> dict:
    inputs = _inputs_bid()
    if secreto is not None:
        inputs["motivo"] = f"{inputs['motivo']} {secreto}"
    config_id = conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t-dossier', '{}'::jsonb)"
        " RETURNING id"
    ).fetchone()[0]
    ciclo_id = conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform, started_at, status)"
        " VALUES ('ads_optimizer', 'live', 'amazon_us', %s, 'done') RETURNING id",
        (DECIDED_AT,),
    ).fetchone()[0]
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES ('amazon_us', 'campaign', '3918', 'USPerNog Exact') RETURNING id"
    ).fetchone()[0]
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, name)"
        " VALUES ('amazon_us', 'ad_group', '39180', %s, 'AG Exact') RETURNING id",
        (camp,),
    ).fetchone()[0]
    kw = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES ('amazon_us', 'keyword', '99901', %s, 'EXACT', %s)"
        " RETURNING id",
        (ag, KEYWORD),
    ).fetchone()[0]
    synced = DECIDED_AT + dt.timedelta(minutes=2)
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " synced_at) VALUES (%s, NULL, NULL, 'ENABLED', %s)",
        (camp, synced),
    )
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " synced_at) VALUES (%s, NULL, NULL, 'ENABLED', %s)",
        (ag, synced),
    )
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " synced_at) VALUES (%s, 0.75, 'USD', 'ENABLED', %s)",
        (kw, synced),
    )
    dec_id = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, old_value, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, 1.00, 0.75, 'USD', %s)"
        " RETURNING id",
        (
            ciclo_id,
            kw,
            DECIDED_AT,
            config_id,
            DECIDED_AT - dt.timedelta(hours=1),
            dt.date(2026, 7, 18),
            dt.date(2026, 8, 16),
            Json(inputs),
        ),
    ).fetchone()[0]
    payload = {"keywordId": "99901", "bid": "0.75"}
    ack = {"keywords": {"success": [{"keywordId": "99901", "index": 0}], "error": []}}
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada,"
        " started_at, finished_at, resultado, ack)"
        " VALUES (%s, 1, 'normal', %s, true, %s, %s, 'ok', %s)",
        (
            dec_id,
            Json(payload),
            DECIDED_AT + dt.timedelta(seconds=5),
            DECIDED_AT + dt.timedelta(seconds=8),
            Json(ack),
        ),
    )
    conn.execute(
        "INSERT INTO decision_application (decision_id, attempted_at, confirmed_at,"
        " verify_ok, platform_ack, applied_cycle_id, error)"
        " VALUES (%s, %s, %s, true, %s, %s, NULL)",
        (
            dec_id,
            DECIDED_AT + dt.timedelta(seconds=5),
            DECIDED_AT + dt.timedelta(seconds=8),
            Json(ack),
            ciclo_id,
        ),
    )
    return {"ciclo": ciclo_id, "decision": dec_id, "keyword": kw}


def _claves(d: dict) -> tuple[str, ...]:
    return tuple(d.keys())


def _archivos_dossier(out_dir: Path) -> list[Path]:
    if not out_dir.exists():
        return []
    return [
        p
        for p in out_dir.iterdir()
        if p.name.startswith("dossier_") or p.name == "prompt_revisor.md"
    ]


def test_escanear_secretos_detecta_patrones():
    assert da.escanear_secretos("keyword calzas ninja bid 0.75") == []
    hits = da.escanear_secretos(f"token {SECRETO} y Bearer x")
    assert hits, "Atza| y Bearer deben disparar el escaner"


def test_modulo_no_importa_ads():
    path = RAIZ / "tools" / "dossier_adversarial.py"
    viol = _violaciones(_imports_runtime(path), ("app.ads",))
    assert not viol, f"el dossier no puede importar app.ads.*: {viol}"
    fuente = path.read_text(encoding="utf-8")
    for patron in ("__import__(", "import_module("):
        assert patron not in fuente


def test_main_sin_dsn_fail_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("ORBIT_DSN_READ", raising=False)
    rc = da.main(["--ciclos", "33", "--out", str(tmp_path / "out")])
    assert rc != 0


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_dossier_aplicada_allowlist_replay_md_permisos(monkeypatch, tmp_path):
    with _db_temporal("orbit_dossier") as conn:
        ids = _siembra(conn)
        regs = da.construir_registros(conn, [ids["ciclo"]])
        assert len(regs) == 1
        reg = regs[0]
        assert _claves(reg) == da.CLAVES_REGISTRO
        assert _claves(reg["decision"]) == da.CLAVES_DECISION
        assert _claves(reg["entidad"]) == da.CLAVES_ENTIDAD
        assert _claves(reg["entidad"]["ad_group"]) == da.CLAVES_AD_GROUP
        assert _claves(reg["entidad"]["campana"]) == da.CLAVES_CAMPANA
        assert _claves(reg["decision_application"]) == da.CLAVES_APPLICATION
        assert _claves(reg["readback"]) == da.CLAVES_READBACK
        assert _claves(reg["ciclo"]) == da.CLAVES_CICLO
        assert _claves(reg["replay"]) == da.CLAVES_REPLAY
        assert reg["apply_attempts"]
        assert _claves(reg["apply_attempts"][0]) == da.CLAVES_ATTEMPT
        assert reg["replay"]["replay_coincide"] is True
        assert Decimal(str(reg["replay"]["new_value"])) == Decimal("0.75")

        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_lectura(conn))
        out = tmp_path / "out"
        rc = da.main(["--ciclos", str(ids["ciclo"]), "--out", str(out)])
        assert rc == 0
        assert stat.S_IMODE(out.stat().st_mode) == 0o700
        fecha = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
        md = out / f"dossier_{fecha}.md"
        js = out / f"dossier_{fecha}.json"
        prompt = out / "prompt_revisor.md"
        for archivo in (md, js, prompt):
            assert archivo.is_file(), archivo.name
            assert stat.S_IMODE(archivo.stat().st_mode) == 0o600
        texto_md = md.read_text(encoding="utf-8")
        assert str(ids["decision"]) in texto_md
        assert KEYWORD in texto_md
        blob = json.loads(js.read_text(encoding="utf-8"))
        assert blob["registros"][0]["decision"]["id"] == ids["decision"]
        assert "dossier_" in prompt.read_text(encoding="utf-8")


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_dossier_secreto_en_inputs_no_escribe(monkeypatch, tmp_path):
    with _db_temporal("orbit_dossier_sec") as conn:
        ids = _siembra(conn, secreto=SECRETO)
        monkeypatch.setenv("ORBIT_DSN_READ", _dsn_lectura(conn))
        out = tmp_path / "out_sec"
        rc = da.main(["--ciclos", str(ids["ciclo"]), "--out", str(out)])
        assert rc != 0
        assert _archivos_dossier(out) == []
