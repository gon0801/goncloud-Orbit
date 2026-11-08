"""AUTO-03 F.4 (dinero): la meta por producto en vivo decide las pujas.

Ciclos reales contra PostgreSQL sobre el mundo de test_cycle_target_ciclo:
una campaña con un grupo exclusivo del producto SKU-FAM (margen 8 %, fracción
0.5: meta 4 %) y, en otra campaña, una keyword sin product ads que sigue la
cascada sin producto.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from test_cycle import _config_version, _corre, _decisions_de, _entidad, _estado
from test_cycle_target_ciclo import (
    _SYNCED,
    _a_live,
    _ledger_ventas,
    _metrica_r9,
    _mundo_familia,
    _product_ad,
    _producto_con_listing,
    _run,
    _targets_de,
)
from test_productos_io import _db as _db_productos
from test_schema import _postgres_obligatorio_ausente

from app import apply_cola

pytestmark = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


SQL76 = (
    Path(__file__).resolve().parents[1] / "migrations" / "0076_auto03_peldano_producto.sql"
).read_text(encoding="utf-8")


@contextlib.contextmanager
def _db(prefijo: str):
    """La cadena de test_productos_io mas la 0076 (el CHECK de procedencia con
    margen_producto, aplicada en produccion)."""
    with _db_productos(prefijo) as conn:
        conn.execute(SQL76)
        yield conn


def _settings(conn) -> dict:
    return conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[
        0
    ]


def _mundo(conn, *, meta: str | None = "live") -> dict:
    ids = _mundo_familia(conn, margen_familia=8)
    ids["ag"] = conn.execute(
        "SELECT parent_id FROM ad_entity WHERE id = %s", (ids["kw"],)
    ).fetchone()[0]
    camp2 = _entidad(conn, "amazon_us", "campaign", "9501")
    ag2 = _entidad(conn, "amazon_us", "ad_group", "9502", parent=camp2)
    kw2 = _entidad(
        conn, "amazon_us", "keyword", "9503", parent=ag2, match_type="EXACT", keyword_text="kw 2"
    )
    _estado(conn, kw2, synced_at=_SYNCED, current_bid=Decimal("1.00"), bid_currency="USD")
    for entidad in (ag2, camp2):
        _estado(conn, entidad, synced_at=_SYNCED)
    _metrica_r9(conn, _run(conn), kw2)
    ids["kw2"] = kw2
    if meta is not None:
        _config_version(conn, {**_settings(conn), "ads_producto_meta_amazon_us": meta})
    return ids


def _comparte_grupo(conn, ids) -> None:
    """Otro producto entra al grupo del producto: deja de ser exclusivo."""
    hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
    pid, lid = _producto_con_listing(conn, "SKU-OTRO")
    _ledger_ventas(conn, pid, hoy, dias=40, precio=100, costo=50)
    _product_ad(conn, ids["ag"], lid, "PA-OTRO")


def _decision(conn, cycle_id: int, hoja: int):
    return conn.execute(
        "SELECT id, kind, inputs, evaluacion_producto_id FROM decision"
        " WHERE cycle_id = %s AND ad_entity_id = %s",
        (cycle_id, hoja),
    ).fetchone()


def _target(conn, cycle_id: int, hoja: int):
    fila = _targets_de(conn, cycle_id).get(hoja)
    return (fila[2], fila[3]) if fila is not None else None


def _evaluacion(conn, cycle_id: int):
    return conn.execute(
        "SELECT id, resultado FROM evaluacion_producto_ciclo WHERE cycle_id = %s", (cycle_id,)
    ).fetchone()


@contextlib.contextmanager
def _ciclo_live(prefijo: str):
    with _db(prefijo) as conn:
        ids = _mundo(conn)
        res = _corre(conn)
        assert res.status == "done", res.notes
        yield conn, ids, res


def test_hoja_de_grupo_apto_puja_con_la_meta_4_y_no_sube_a_10():
    with _ciclo_live("orbit_f4_meta") as (conn, ids, res):
        assert _target(conn, res.cycle_id, ids["kw"]) == (Decimal("4"), "margen_producto")
        assert _target(conn, res.cycle_id, ids["kw2"])[1] != "margen_producto"
        ev_id, resultado = _evaluacion(conn, res.cycle_id)
        [hoja] = resultado["sombra_hojas"]
        assert hoja["hoja_id"] == ids["kw"] and Decimal(hoja["meta"]["aplicado"]) == 4
        assert hoja["veredicto_actual"] == hoja["veredicto_con_meta"]

        _id, kind, inputs, evaluacion_id = _decision(conn, res.cycle_id, ids["kw"])
        assert (kind, evaluacion_id) == ("bid", ev_id)
        assert inputs["target_procedencia"] == "margen_producto"
        assert Decimal(inputs["caso"]["economia"]["target_acos_pct"]) == 4
        snap = inputs["target_snapshot"]
        assert Decimal(snap["target_aplicado"]) == Decimal(snap["meta_pct"]) == 4
        assert snap["ancla"] is None
        assert snap["grupo_id"] == ids["ag"] and snap["huella_alcance"].startswith("sha256:")
        assert _decision(conn, res.cycle_id, ids["kw2"])[3] is None
        notas = json.loads(res.notes)["productos"]
        assert notas["modo_meta"] == "live" and "error" not in notas


def test_goal_de_campana_con_target_gana_a_la_meta():
    with _db("orbit_f4_goal") as conn:
        ids = _mundo(conn)
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
            " bid_ceiling, bid_currency, enabled, mode)"
            " VALUES ('campaign', %s, 25, 0.40, 2.50, 'USD', true, 'live')",
            (ids["camp"],),
        )
        res = _corre(conn)
        assert res.status == "done", res.notes
        assert _target(conn, res.cycle_id, ids["kw"]) == (Decimal("25"), "goal_campana")
        assert _decision(conn, res.cycle_id, ids["kw"])[3] is None
        assert _evaluacion(conn, res.cycle_id)[1]["sombra_hojas"] == []


def test_grupo_compartido_no_usa_el_producto_aunque_venga_de_la_meta():
    with _ciclo_live("orbit_f4_compartido") as (conn, ids, r1):
        assert _target(conn, r1.cycle_id, ids["kw"]) == (Decimal("4"), "margen_producto")
        _a_live(conn, r1.cycle_id)
        _comparte_grupo(conn, ids)
        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        target, procedencia = _target(conn, r2.cycle_id, ids["kw"])
        assert procedencia != "margen_producto" and target > 10
        assert _decision(conn, r2.cycle_id, ids["kw"])[3] is None


def test_grupo_compartido_revalida_con_el_respaldo_y_no_aplica_la_meta(monkeypatch):
    """ORBIT 07 M2: si la hoja pasa a un grupo compartido durante el veto, la
    pausa no se revalida con la meta individual congelada ni se descarta por
    alcance: resuelve la economia completa de respaldo de ese ciclo."""
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    with _ciclo_live("orbit_f4_alcance") as (conn, ids, res):
        decision_id = _decision(conn, res.cycle_id, ids["kw"])[0]
        fila = apply_cola.FilaCola(1, "pause", ids["kw"], None, decision_id, {}, "released")
        aplicador = type(
            "Aplicador",
            (),
            {"_cliente": lambda self: object(), "cycle_id_ejecutor": res.cycle_id},
        )()
        ahora = dt.datetime.now(dt.UTC)
        antes = apply_cola._revalida_pause(conn, aplicador, "amazon_us", fila, ahora)
        assert antes != "alcance_cambio"
        _comparte_grupo(conn, ids)
        evidencias: list[dict] = []
        despues = apply_cola._revalida_pause(conn, aplicador, "amazon_us", fila, ahora, evidencias)
        assert despues != "alcance_cambio"
        assert despues == "ya_no_califica"
        [evidencia] = evidencias
        assert evidencia["economia"]["producto"]["motivo"] == "grupo_no_exclusivo"
        assert evidencia["target_procedencia"] != "margen_producto"
        assert Decimal(evidencia["target"]) != 4


def test_pausa_con_margen_familia_se_revalida_con_la_familia_congelada():
    with _db("orbit_f4_familia") as conn:
        ids = _mundo(conn, meta=None)
        res = _corre(conn)
        assert res.status == "done", res.notes
        assert _target(conn, res.cycle_id, ids["kw"]) == (Decimal("29.5"), "margen_familia")
        inputs = _decision(conn, res.cycle_id, ids["kw"])[2]
        vigente = apply_cola._target_pause_vigente(
            conn, "amazon_us", ids["kw"], res.cycle_id, inputs
        )
        assert vigente == (Decimal("29.5"), "margen_familia")


def test_guardado_fallido_deja_sin_decision_solo_las_hojas_de_grupo_apto():
    with _db("orbit_f4_guardado") as conn:
        ids = _mundo(conn)
        conn.execute(
            "CREATE FUNCTION f4_rechaza() RETURNS trigger LANGUAGE plpgsql AS"
            " $$BEGIN RAISE EXCEPTION 'f4: insercion rechazada'; END$$"
        )
        conn.execute(
            "CREATE TRIGGER f4_rechaza BEFORE INSERT ON evaluacion_producto_ciclo"
            " FOR EACH ROW EXECUTE FUNCTION f4_rechaza()"
        )
        res = _corre(conn)
        assert res.status == "done", res.notes
        assert _decision(conn, res.cycle_id, ids["kw"]) is None
        assert _target(conn, res.cycle_id, ids["kw"]) is None
        assert _decision(conn, res.cycle_id, ids["kw2"]) is not None
        assert _target(conn, res.cycle_id, ids["kw2"]) is not None
        notas = json.loads(res.notes)
        assert notas["skips"]["entidad"]["producto_sin_evidencia"] == 1
        assert notas["productos"]["error"].startswith("guardado: ")
        assert res.decisions_count == len(_decisions_de(conn, res.cycle_id))


def test_lectura_fallida_deja_sin_decision_las_hojas_con_previo_margen_producto():
    with _ciclo_live("orbit_f4_lectura") as (conn, ids, r1):
        assert _target(conn, r1.cycle_id, ids["kw"]) == (Decimal("4"), "margen_producto")
        _a_live(conn, r1.cycle_id)
        conn.execute("DROP VIEW v_margen_producto CASCADE")
        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        assert _decision(conn, r2.cycle_id, ids["kw"]) is None
        assert _target(conn, r2.cycle_id, ids["kw"]) is None
        assert _target(conn, r2.cycle_id, ids["kw2"]) is not None
        notas = json.loads(r2.notes)
        assert notas["skips"]["entidad"]["producto_sin_evidencia"] == 1
        assert "v_margen_producto" in notas["productos"]["error"]
