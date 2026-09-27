"""C.2a: el target de cada hoja queda CONGELADO por ciclo en target_acos_ciclo.

La fila se captura donde HOY se calcula el target (cascada del motor), para
toda hoja que llega ahi — incluidas las que luego salen por no-op o por el
cooldown B.2 — y se escribe en TX3 aunque no deje decision. Es el insumo del
replay economico cuando el goal vigente ya no es el del ciclo.
"""

import datetime as dt
import json
from decimal import Decimal

import pytest
from psycopg.types.json import Json
from test_cycle import (
    DECIDED_AT,
    _config_version,
    _corre,
    _db_temporal,
    _decisions_de,
    _entidad,
    _estado,
    _goal_plataforma,
    _metrica,
    _obs,
    _rango,
    _run,
    _siembra_kw_bid,
    _siembra_kw_pause,
    _siembra_terminos,
)
from test_schema import _postgres_obligatorio_ausente

_SYNCED = DECIDED_AT - dt.timedelta(hours=4)


def _hoja_nueva(conn, run_id, *, camp_ext, ag_ext, kw_ext, texto) -> int:
    """Campania -> grupo -> keyword propias (evidencia de grupo aislada de la
    maestra: los umbrales del grupo 9101 del golden quedan intactos)."""
    camp = _entidad(conn, "amazon_us", "campaign", camp_ext)
    ag = _entidad(conn, "amazon_us", "ad_group", ag_ext, parent=camp)
    kw = _entidad(
        conn, "amazon_us", "keyword", kw_ext, parent=ag, match_type="EXACT", keyword_text=texto
    )
    _estado(conn, kw, synced_at=_SYNCED, current_bid=Decimal("1.00"), bid_currency="USD")
    _estado(conn, ag, synced_at=_SYNCED)
    _estado(conn, camp, synced_at=_SYNCED)
    return kw


def _siembra_hoja_sin_banda(conn, run_id) -> int:
    """Ventana de bids con ACoS 24% (cost 23.40 / revenue 97.50, entre 0.85x y
    1.15x del target 25) -> sin banda -> NO-OP sin decision; la de cortes
    lleva 9 ordenes -> sin pause."""
    kw = _hoja_nueva(conn, run_id, camp_ext="9301", ag_ext="9311", kw_ext="9321", texto="kw noop")
    for fecha in _rango(dt.date(2026, 7, 14), dt.date(2026, 7, 17)):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="0.25",
            ad_revenue="1.00",
            clicks=0,
            orders=1,
            impressions=10,
        )
    for i, fecha in enumerate(_rango(dt.date(2026, 7, 18), dt.date(2026, 8, 12))):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="0.80",
            ad_revenue="2.50",
            clicks=1,
            orders=1 if i < 5 else 0,
            impressions=10,
        )
    for fecha in _rango(dt.date(2026, 8, 13), dt.date(2026, 8, 16)):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="0.85",
            ad_revenue="8.75",
            clicks=6,
            orders=0,
            impressions=60,
        )
    for fecha in _rango(dt.date(2026, 8, 17), dt.date(2026, 8, 19)):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="0.10",
            ad_revenue="0.10",
            clicks=1,
            orders=0,
            impressions=10,
        )
    return kw


def _siembra_hoja_en_cooldown(conn, run_id, config_id: int) -> int:
    """kw de pausa (cortes: orders 0, clicks 105, cost 45) con un apply de
    pause VERIFICADO confirmado hace 2d por un ciclo live: con B.2a encendido
    decide pause y sale por cooldown_7d DESPUES del punto del freeze."""
    kw = _hoja_nueva(
        conn, run_id, camp_ext="9401", ag_ext="9411", kw_ext="9421", texto="kw cooldown"
    )
    _siembra_kw_pause(conn, run_id, kw)
    confirmado = DECIDED_AT - dt.timedelta(days=2)
    ciclo_ejecutor = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform, status, started_at, finished_at)"
        " VALUES ('live', 'amazon_us', 'done', %s, %s) RETURNING id",
        (confirmado - dt.timedelta(hours=1), confirmado),
    ).fetchone()[0]
    decision_id = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, inputs)"
        " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            ciclo_ejecutor,
            kw,
            confirmado,
            config_id,
            confirmado - dt.timedelta(hours=1),
            dt.date(2026, 7, 11),
            dt.date(2026, 8, 10),  # <= confirmado - 10d (trigger de madurez)
            Json({"seed": "cooldown c2a"}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO decision_application (decision_id, attempted_at, confirmed_at,"
        " verify_ok, platform_ack, applied_cycle_id) VALUES (%s, %s, %s, TRUE, %s, %s)",
        (
            decision_id,
            confirmado - dt.timedelta(minutes=5),
            confirmado,
            Json({"estado": "ok"}),
            ciclo_ejecutor,
        ),
    )
    return kw


def _siembra_c2a(conn) -> dict:
    """Maestra del golden con el flag B.2a encendido + las dos hojas extra
    (no-op y cooldown) en campanias propias."""
    run_id = _run(conn)
    config_id = _config_version(
        conn, {"ads_optimizer_mode": "shadow", "ads_pause_sin_cooldown_bid": True}
    )
    _goal_plataforma(conn)
    camp = _entidad(conn, "amazon_us", "campaign", "9001")
    ag = _entidad(conn, "amazon_us", "ad_group", "9101", parent=camp)
    kw_bid = _entidad(
        conn, "amazon_us", "keyword", "9201", parent=ag, match_type="EXACT", keyword_text="kw bid"
    )
    kw_pause = _entidad(
        conn, "amazon_us", "keyword", "9202", parent=ag, match_type="EXACT", keyword_text="kw pause"
    )
    _estado(conn, kw_bid, synced_at=_SYNCED, current_bid=Decimal("1.00"), bid_currency="USD")
    _estado(conn, kw_pause, synced_at=_SYNCED, current_bid=Decimal("1.00"), bid_currency="USD")
    _estado(conn, ag, synced_at=_SYNCED)
    _estado(conn, camp, synced_at=_SYNCED)
    _siembra_kw_bid(conn, run_id, kw_bid)
    _siembra_kw_pause(conn, run_id, kw_pause)
    _siembra_terminos(conn, run_id, ag)
    kw_noop = _siembra_hoja_sin_banda(conn, run_id)
    kw_cooldown = _siembra_hoja_en_cooldown(conn, run_id, config_id)
    return {
        "config_id": config_id,
        "kw_bid": kw_bid,
        "kw_pause": kw_pause,
        "kw_noop": kw_noop,
        "kw_cooldown": kw_cooldown,
    }


def _targets_de(conn, cycle_id: int) -> dict:
    return {
        fila[0]: fila
        for fila in conn.execute(
            "SELECT ad_entity_id, decided_at, target_acos_pct, procedencia"
            " FROM target_acos_ciclo WHERE cycle_id = %s",
            (cycle_id,),
        ).fetchall()
    }


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_noop_y_cooldown_dejan_su_fila_de_target():
    """Congelar SOLO las hojas con decision deja el freeze incompleto: una
    hoja en no-op (sin banda) y una que sale por cooldown B.2 llegaron al
    calculo del target y DEBEN dejar su fila, aunque no dejen decision."""
    with _db_temporal("orbit_c2a_freeze") as (conn, _c):
        ids = _siembra_c2a(conn)
        res = _corre(conn)

        assert res.status == "done"
        notas = json.loads(res.notes)
        assert notas["skips"]["entidad"]["sin_banda"] == 1
        assert notas["skips"]["entidad"]["cooldown_7d"] == 1

        decisiones = {(fila[0], fila[1]) for fila in _decisions_de(conn, res.cycle_id)}
        assert (ids["kw_bid"], "bid") in decisiones
        assert (ids["kw_pause"], "pause") in decisiones
        assert (ids["kw_noop"], "bid") not in decisiones
        assert (ids["kw_cooldown"], "pause") not in decisiones

        por = _targets_de(conn, res.cycle_id)
        assert set(por) == {ids["kw_bid"], ids["kw_pause"], ids["kw_noop"], ids["kw_cooldown"]}
        for _ent, decided_at, target, procedencia in por.values():
            assert decided_at == DECIDED_AT
            assert target == Decimal("25.00")
            assert procedencia == "goal_plataforma"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_inputs_target_usado_coincide_con_la_tabla():
    """Una sola fuente: el target que la decision congela en sus inputs es EL
    MISMO valor y procedencia que quedo en target_acos_ciclo (dos fuentes que
    divergen rompen la auditoria del replay)."""
    with _db_temporal("orbit_c2a_inputs") as (conn, _c):
        ids = _siembra_c2a(conn)
        res = _corre(conn)

        assert res.status == "done"
        por = _targets_de(conn, res.cycle_id)
        decisiones = _decisions_de(conn, res.cycle_id)
        hojas = [fila for fila in decisiones if fila[1] in ("bid", "pause")]
        assert {(fila[0], fila[1]) for fila in hojas} == {
            (ids["kw_bid"], "bid"),
            (ids["kw_pause"], "pause"),
        }
        for entidad_id, kind, *_rest, inputs in hojas:
            _ent, _decided, target, procedencia = por[entidad_id]
            assert Decimal(inputs["target_acos_pct_usado"]) == target, kind
            assert inputs["target_procedencia"] == procedencia, kind
