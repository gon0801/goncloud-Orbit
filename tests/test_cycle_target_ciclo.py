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


# ---------------------------------------------------------------------------
# A5: peldaño margen_familia en el ciclo (vista + mapeo + freeze).
# ---------------------------------------------------------------------------


def _producto_con_listing(conn, sku, platform="amazon_us", moneda="USD"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pid, platform, f"ASIN-{sku}", f"SS-{sku}"),
    ).fetchone()[0]
    return pid, lid


def _product_ad(conn, ag, lid, external):
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
        " VALUES ('amazon_us', 'product_ad', %s, %s, %s) RETURNING id",
        (external, ag, lid),
    ).fetchone()[0]


def _familia(conn, nombre, slug, padre_id=None, platform="amazon_us"):
    return conn.execute(
        "INSERT INTO familia (platform, nombre, slug, padre_id)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (platform, nombre, slug, padre_id),
    ).fetchone()[0]


def _etiqueta(conn, pid, fid, platform="amazon_us"):
    conn.execute(
        "INSERT INTO producto_familia (product_id, platform, familia_id) VALUES (%s, %s, %s)",
        (pid, platform, fid),
    )


def _ledger_ventas(conn, pid, hoy, *, dias, precio, costo, platform="amazon_us", moneda="USD"):
    """`dias` ventas diarias de `precio` con costo `costo` + fee 10 % con
    orden: cobertura 1, margen = (precio - 0.1*precio - costo) / precio.
    `costo=None` = sin fila de costo (cobertura 0; el trigger prohibe
    DELETE de vigencias, asi que la ausencia se siembra, no se borra)."""
    run = _run(conn)
    if costo is not None:
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency,"
            " includes_tax, valid_from)"
            " VALUES (%s, %s, %s, true, %s)",
            (pid, costo, moneda, hoy - dt.timedelta(days=dias + 40)),
        )
    for i in range(dias):
        fecha = hoy - dt.timedelta(days=20 + dias - i)
        orden = f"o-{pid}-{i}"
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES (%s, 'sale', %s, %s, %s, 1, %s, %s, %s)",
            (platform, fecha, orden, pid, precio, moneda, run),
        )
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, ingest_run_id)"
            " VALUES (%s, 'fee', %s, %s, %s, %s, 'referral', %s)",
            (platform, fecha, orden, -precio // 10, moneda, run),
        )
    conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok)"
        " VALUES ('accounting_ledger_events', now(), true)"
    )


def _mundo_familia(
    conn, *, margen_familia=20, dias=40, mezcla=False, sin_costos=False, suelto_dias=0
):
    """Campaña con keyword (métricas de bid) + product_ad -> listing ->
    producto etiquetado en familia; settings fracción 0.5 + manual 30, goal
    de plataforma SIN target (el peldaño familiar puede ganar).
    `suelto_dias` siembra un producto SIN etiqueta (solo lo ve plataforma):
    con 70, la plataforma mide (60 dias) aunque la familia tenga 40."""
    run_id = _run(conn)
    _config_version(
        conn,
        {
            "ads_optimizer_mode": "shadow",
            "ads_target_acos_pct_amazon_us": 30,
            "ads_target_fraccion_margen_amazon_us": "0.5",
        },
    )
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, bid_floor, bid_ceiling,"
        " bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode)"
        " VALUES ('platform', 'amazon_us', 0.40, 2.50, 'USD', '9002', '9102',"
        " 0.75, true, 'live')"
    )
    camp = _entidad(conn, "amazon_us", "campaign", "9401")
    ag = _entidad(conn, "amazon_us", "ad_group", "9402", parent=camp)
    kw = _entidad(
        conn, "amazon_us", "keyword", "9403", parent=ag, match_type="EXACT", keyword_text="kw fam"
    )
    _estado(conn, kw, synced_at=_SYNCED, current_bid=Decimal("1.00"), bid_currency="USD")
    _estado(conn, ag, synced_at=_SYNCED)
    _estado(conn, camp, synced_at=_SYNCED)
    _siembra_kw_bid(conn, run_id, kw)
    hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
    costo = None if sin_costos else 100 - margen_familia - 10
    pid, lid = _producto_con_listing(conn, "SKU-FAM")
    _ledger_ventas(conn, pid, hoy, dias=dias, precio=100, costo=costo)
    fid = _familia(conn, "Barata", "barata")
    _etiqueta(conn, pid, fid)
    _product_ad(conn, ag, lid, "PA-1")
    if mezcla:
        pid2, lid2 = _producto_con_listing(conn, "SKU-OTRA")
        _ledger_ventas(conn, pid2, hoy, dias=dias, precio=100, costo=costo)
        fid2 = _familia(conn, "Otra", "otra")
        _etiqueta(conn, pid2, fid2)
        _product_ad(conn, ag, lid2, "PA-2")
    if suelto_dias:
        for n in range(3):
            pid3, _l3 = _producto_con_listing(conn, f"SKU-SUELTO-{n}")
            _ledger_ventas(conn, pid3, hoy, dias=suelto_dias, precio=100, costo=50)
    return {"kw": kw, "camp": camp, "familia": fid}


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_familia_20_target_camina_a_10_y_congela():
    """Familia 20 % (fracción 0.5 → derivado 10): la plataforma abstiene
    (40 días < 60), el destino es el setting 30 y el aplicado camina a 29.5
    con procedencia margen_familia; el snapshot lleva familia_etiqueta/usada
    y notes declara el derivado 10."""
    with _db_temporal("orbit_c5_fam") as (conn, _c):
        ids = _mundo_familia(conn)
        res = _corre(conn)
        assert res.status == "done", res.notes
        por = _targets_de(conn, res.cycle_id)
        target, procedencia = por[ids["kw"]][2], por[ids["kw"]][3]
        assert procedencia == "margen_familia"
        assert target == Decimal("29.5")
        bids = [d for d in _decisions_de(conn, res.cycle_id) if d[1] == "bid"]
        assert len(bids) == 1
        inputs = bids[0][9]
        assert inputs["target_procedencia"] == "margen_familia"
        snap = inputs["target_snapshot"]
        assert snap["familia_etiqueta"] == ids["familia"]
        assert snap["familia_usada"] == ids["familia"]
        assert snap["target_aplicado"] == "29.5"
        assert Decimal(snap["margen_neto_pct"]) == 20
        notas = json.loads(res.notes)["target"]["familias"][str(ids["familia"])]
        assert notas["motivo"] is None
        assert Decimal(notas["derivado"]) == 10


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_mezcla_dos_familias_cae_a_plataforma():
    """Campaña que anuncia dos familias: familia efectiva None → el peldaño
    no aplica y la hoja cae a margen_plataforma."""
    with _db_temporal("orbit_c5_mezcla") as (conn, _c):
        ids = _mundo_familia(conn, mezcla=True, suelto_dias=70)
        res = _corre(conn)
        assert res.status == "done", res.notes
        por = _targets_de(conn, res.cycle_id)
        assert por[ids["kw"]][3] == "margen_plataforma"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_costos_faltantes_caen_a_plataforma_con_motivo():
    """Familia con 10 % de ventas sin costo (cobertura < 0.95) + plataforma
    sana (el suelto diluye): la familia no mide → cae a plataforma y
    notes.target.familias declara cobertura_baja (L5)."""
    with _db_temporal("orbit_c5_cob") as (conn, _c):
        run_id = _run(conn)
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "shadow",
                "ads_target_acos_pct_amazon_us": 30,
                "ads_target_fraccion_margen_amazon_us": "0.5",
            },
        )
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, platform, bid_floor, bid_ceiling,"
            " bid_currency, harvest_campaign_id, harvest_ad_group_id,"
            " harvest_default_bid, enabled, mode)"
            " VALUES ('platform', 'amazon_us', 0.40, 2.50, 'USD', '9002', '9102',"
            " 0.75, true, 'live')"
        )
        camp = _entidad(conn, "amazon_us", "campaign", "9421")
        ag = _entidad(conn, "amazon_us", "ad_group", "9422", parent=camp)
        kw = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9423",
            parent=ag,
            match_type="EXACT",
            keyword_text="kw cob",
        )
        _estado(conn, kw, synced_at=_SYNCED, current_bid=Decimal("1.00"), bid_currency="USD")
        _estado(conn, ag, synced_at=_SYNCED)
        _estado(conn, camp, synced_at=_SYNCED)
        _siembra_kw_bid(conn, run_id, kw)
        hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
        pid_ok, lid_ok = _producto_con_listing(conn, "SKU-COB-OK")
        _ledger_ventas(conn, pid_ok, hoy, dias=40, precio=100, costo=70)
        pid_sin, lid_sin = _producto_con_listing(conn, "SKU-COB-SIN")
        _ledger_ventas(conn, pid_sin, hoy, dias=5, precio=100, costo=None)
        fid = _familia(conn, "Cob", "cob")
        _etiqueta(conn, pid_ok, fid)
        _etiqueta(conn, pid_sin, fid)
        _product_ad(conn, ag, lid_ok, "PA-COB-1")
        _product_ad(conn, ag, lid_sin, "PA-COB-2")
        for n in range(3):
            pid3, _l3 = _producto_con_listing(conn, f"SKU-SU-{n}")
            _ledger_ventas(conn, pid3, hoy, dias=70, precio=100, costo=50)
        res = _corre(conn)
        assert res.status == "done", res.notes
        por = _targets_de(conn, res.cycle_id)
        assert por[kw][3] == "margen_plataforma"
        notas = json.loads(res.notes)["target"]["familias"][str(fid)]
        assert notas["motivo"] == "cobertura_baja"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_subfamilia_10_dias_gobierna_padre():
    """Sub con 10 días (ventana_corta) + padre con 40: gana el padre y el
    snapshot lo declara (familia_usada = padre, motivo None)."""
    with _db_temporal("orbit_c5_sub") as (conn, _c):
        run_id = _run(conn)
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "shadow",
                "ads_target_acos_pct_amazon_us": 30,
                "ads_target_fraccion_margen_amazon_us": "0.5",
            },
        )
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, platform, bid_floor, bid_ceiling,"
            " bid_currency, harvest_campaign_id, harvest_ad_group_id,"
            " harvest_default_bid, enabled, mode)"
            " VALUES ('platform', 'amazon_us', 0.40, 2.50, 'USD', '9002', '9102',"
            " 0.75, true, 'live')"
        )
        camp = _entidad(conn, "amazon_us", "campaign", "9411")
        ag = _entidad(conn, "amazon_us", "ad_group", "9412", parent=camp)
        kw = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9413",
            parent=ag,
            match_type="EXACT",
            keyword_text="kw sub",
        )
        _estado(conn, kw, synced_at=_SYNCED, current_bid=Decimal("1.00"), bid_currency="USD")
        _estado(conn, ag, synced_at=_SYNCED)
        _estado(conn, camp, synced_at=_SYNCED)
        _siembra_kw_bid(conn, run_id, kw)
        hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
        # Padre con 40 días al 40 % + sub con 10 días (no mide).
        pid_padre, _l0 = _producto_con_listing(conn, "SKU-PADRE")
        _ledger_ventas(conn, pid_padre, hoy, dias=40, precio=100, costo=50)
        pid_sub, lid_sub = _producto_con_listing(conn, "SKU-SUB")
        _ledger_ventas(conn, pid_sub, hoy, dias=10, precio=100, costo=50)
        fid_padre = _familia(conn, "Arras", "arras")
        fid_sub = _familia(conn, "Mini", "mini", padre_id=fid_padre)
        _etiqueta(conn, pid_padre, fid_padre)
        _etiqueta(conn, pid_sub, fid_sub)
        _product_ad(conn, ag, lid_sub, "PA-SUB")
        res = _corre(conn)
        assert res.status == "done", res.notes
        por = _targets_de(conn, res.cycle_id)
        assert por[kw][3] == "margen_familia"
        bids = [d for d in _decisions_de(conn, res.cycle_id) if d[1] == "bid"]
        snap = bids[0][9]["target_snapshot"]
        assert snap["familia_etiqueta"] == fid_sub
        assert snap["familia_usada"] == fid_padre


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_conciliacion_familia_de_un_producto_y_padre_sin_doble_conteo():
    """Regla 10: familia de 1 producto == v_margen_producto (todas las
    columnas de dinero); raíz con directa + sub = suma exacta (B-F13)."""
    with _db_temporal("orbit_c5_conc") as (conn, _c):
        hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
        pid, _l = _producto_con_listing(conn, "SKU-SOLO")
        _ledger_ventas(conn, pid, hoy, dias=40, precio=100, costo=50)
        solo = _familia(conn, "Solo", "solo")
        _etiqueta(conn, pid, solo)
        fila_fam = conn.execute(
            "SELECT venta_total, venta_cubierta, cargos_con_orden, cargos_sin_orden,"
            " cogs, cobertura, margen_neto_pct FROM v_margen_familia WHERE familia_id = %s",
            (solo,),
        ).fetchone()
        fila_prod = conn.execute(
            "SELECT venta_total, venta_cubierta, cargos_con_orden, cargos_sin_orden,"
            " cogs, cobertura, margen_neto_pct FROM v_margen_producto WHERE product_id = %s",
            (pid,),
        ).fetchone()
        assert tuple(fila_fam) == tuple(fila_prod)
        # Raíz: directa (40 días) + sub (40 días) = suma exacta de componentes.
        pid_d, _l2 = _producto_con_listing(conn, "SKU-DIR")
        _ledger_ventas(conn, pid_d, hoy, dias=40, precio=100, costo=50)
        pid_s, _l3 = _producto_con_listing(conn, "SKU-SUB2")
        _ledger_ventas(conn, pid_s, hoy, dias=40, precio=100, costo=50)
        raiz = _familia(conn, "Raiz", "raiz")
        sub = _familia(conn, "Sub", "sub", padre_id=raiz)
        _etiqueta(conn, pid_d, raiz)
        _etiqueta(conn, pid_s, sub)
        raiz_f = conn.execute(
            "SELECT venta_total, venta_cubierta, cogs FROM v_margen_familia WHERE familia_id = %s",
            (raiz,),
        ).fetchone()
        suma = conn.execute(
            "SELECT SUM(venta_total), SUM(venta_cubierta), SUM(cogs)"
            " FROM v_margen_producto WHERE product_id = ANY(%s)",
            ([pid_d, pid_s],),
        ).fetchone()
        assert tuple(raiz_f) == tuple(suma)
