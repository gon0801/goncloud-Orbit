"""ORBIT 07 M2: economia compatible y fresca del producto.

Ciclos reales contra PostgreSQL desechable sobre el mundo de
test_productos_meta_ciclo (producto exclusivo con margen, meta live):

- la meta posterior al primer ciclo se aplica DIRECTA (sin la rampa de 0.5
  puntos): desde 25 % previo, una meta nueva de 4 % aplica 4 %;
- la cola revalida una pausa economica con la evaluacion del ciclo ejecutor:
  si la economia elegible no cambia conserva el 4 %; si el producto pierde
  elegibilidad durante el veto resuelve el respaldo completo y no aplica la
  meta individual congelada.

Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from decimal import Decimal

import pytest
from test_cycle import (
    DECIDED_AT,
    _config_version,
    _corre,
    _decisions_de,
    _entidad,
    _estado,
    _metrica,
    _run,
)
from test_cycle_target_ciclo import (
    _SYNCED,
    _a_live,
    _etiqueta,
    _familia,
    _ledger_ventas,
    _mundo_familia,
    _product_ad,
    _producto_con_listing,
    _targets_de,
)
from test_productos_io import _db as _db_productos
from test_productos_meta_ciclo import SQL76, _decision, _evaluacion, _settings, _target
from test_schema import _postgres_obligatorio_ausente

from app import apply_cola
from app.optimizer import productos as p

pytestmark = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextlib.contextmanager
def _db(prefijo: str):
    with _db_productos(prefijo) as conn:
        conn.execute(SQL76)
        yield conn


def _mundo(conn, *, margen: int = 8) -> dict:
    """Campana con un grupo exclusivo de SKU-FAM en amazon_us, meta live y
    fraccion 0.5. El margen del producto es `margen` (meta = margen / 2)."""
    ids = _mundo_familia(conn, margen_familia=margen)
    ids["ag"] = conn.execute(
        "SELECT parent_id FROM ad_entity WHERE id = %s", (ids["kw"],)
    ).fetchone()[0]
    _config_version(
        conn,
        {
            **_settings(conn),
            "ads_producto_meta_amazon_us": "live",
            "ads_producto_economia_amazon_us": "producto_directo_v1",
        },
    )
    return ids


def _config_con_pausa_economica(conn) -> None:
    _config_version(conn, {**_settings(conn), "ads_pause_economica": True})


def _config_con_meta(conn, modo: str) -> None:
    _config_version(conn, {**_settings(conn), "ads_producto_meta_amazon_us": modo})


def _config_con_economia(conn, modo: str) -> None:
    _config_version(conn, {**_settings(conn), "ads_producto_economia_amazon_us": modo})


def _producto(conn) -> int:
    return conn.execute("SELECT id FROM product WHERE odoo_sku = 'SKU-FAM'").fetchone()[0]


def _fees_por_orden(conn, producto: int, monto: Decimal) -> None:
    """Agrega un cargo por cada orden del producto: mueve el margen sin
    reescribir una vigencia de costo (ledger append-only)."""
    run = _run(conn)
    ordenes = conn.execute(
        "SELECT order_id, event_date FROM ledger_event"
        " WHERE product_id = %s AND kind = 'sale' ORDER BY event_date",
        (producto,),
    ).fetchall()
    for order_id, fecha in ordenes:
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, ingest_run_id)"
            " VALUES ('amazon_us', 'fee', %s, %s, %s, 'USD', 'referral', %s)",
            (fecha, order_id, -monto, run),
        )
    conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok)"
        " VALUES ('accounting_ledger_events', now(), true)"
    )


def _ventas_sin_costo(conn, producto: int, *, dias: int = 40) -> None:
    """Ventas ANTERIORES a la vigencia del costo: bajan la cobertura por monto
    por debajo de 0.95 y dejan el margen del producto en NULL (sin inventar
    un cero)."""
    run = _run(conn)
    inicio = dt.date(2026, 3, 1)
    for i in range(dias):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_us', 'sale', %s, %s, %s, 1, 100, 'USD', %s)",
            (inicio + dt.timedelta(days=i), f"sc-{producto}-{i}", producto, run),
        )
    conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok)"
        " VALUES ('accounting_ledger_events', now(), true)"
    )


def _metrica_economica(conn, run_id, kw) -> None:
    """Una fecha madura de la ventana de cortes con el caso de la sonda:
    5000 de gasto, 10000 de ventas, 120 clics y un pedido. Con meta 4 %
    califica la pausa economica; con 20 % o mas, no."""
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, 'USD', 5000, 10000, 1000, 120, 1, %s)",
        (kw, dt.date(2026, 8, 9), dt.datetime(2026, 8, 21, tzinfo=dt.UTC), run_id),
    )


def _aplicador(cycle_id: int):
    return type(
        "Aplicador",
        (),
        {"_cliente": lambda self: object(), "cycle_id_ejecutor": cycle_id},
    )()


def test_meta_posterior_se_aplica_directa_sin_rampa():
    """Desde 25 % previo, el cambio de margen a 8 % aplica 4 % en ese ciclo
    (no 24.5 %). El snapshot versiona la economia y conserva equilibrio y
    referencias."""
    with _db("orbit_m2_rampa") as conn:
        ids = _mundo(conn, margen=50)
        r1 = _corre(conn)
        assert r1.status == "done", r1.notes
        assert _target(conn, r1.cycle_id, ids["kw"]) == (Decimal("25"), "margen_producto")
        _a_live(conn, r1.cycle_id)
        _fees_por_orden(conn, _producto(conn), Decimal("42"))
        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        assert _target(conn, r2.cycle_id, ids["kw"]) == (Decimal("4"), "margen_producto")

        inputs = _decision(conn, r2.cycle_id, ids["kw"])[2]
        snap = inputs["target_snapshot"]
        assert snap["version"] == p.POLITICA_ECONOMIA == "producto_directo_v1"
        assert snap["procedencia"] == "margen_producto"
        assert Decimal(snap["meta_pct"]) == Decimal("4")
        assert Decimal(snap["equilibrio_pct"]) == Decimal("8")
        assert Decimal(snap["ancla"]) == Decimal("25")
        assert Decimal(snap["paso"]) == Decimal("-21")
        assert snap["moneda"] == "USD"
        assert snap["huella_alcance"].startswith("sha256:")
        assert snap["ventana_halo"]["desde"] < snap["ventana_halo"]["hasta_exclusivo"]


def test_cola_conserva_la_meta_individual_si_la_economia_no_cambia(monkeypatch):
    """La pausa economica decidida al 4 % se revalida con la evaluacion fresca
    del mismo producto y sigue calificando (no se sustituye por el 20 %)."""
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    with _db("orbit_m2_cola_conserva") as conn:
        ids = _mundo(conn)
        _config_con_pausa_economica(conn)
        _metrica_economica(conn, _run(conn), ids["kw"])
        r1 = _corre(conn)
        assert r1.status == "done", r1.notes
        pausa = _pausa_de(conn, r1.cycle_id, ids["kw"])
        assert pausa is not None, r1.notes
        assert pausa["target_procedencia"] == "margen_producto"
        assert Decimal(pausa["target_acos_pct_usado"]) == Decimal("4")

        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        evidencias: list[dict] = []
        resultado = apply_cola._revalida_pause(
            conn,
            _aplicador(r2.cycle_id),
            "amazon_us",
            apply_cola.FilaCola(1, "pause", ids["kw"], None, pausa["decision_id"], {}, "released"),
            dt.datetime.now(dt.UTC),
            evidencias,
        )
        assert resultado is None, "el 4 % fresco conserva la pausa economica"
        [evidencia] = evidencias
        assert Decimal(evidencia["target"]) == Decimal("4")
        assert evidencia["target_procedencia"] == "margen_producto"
        economia = evidencia["economia"]
        assert economia["producto"]["motivo"] is None
        assert Decimal(economia["producto"]["meta_pct"]) == Decimal("4")
        assert economia["producto"]["evaluacion_id"] > 0
        assert economia["producto"]["politica"] == p.POLITICA_PRODUCTO


def test_cola_resuelve_el_respaldo_cuando_el_producto_pierde_elegibilidad(monkeypatch):
    """Durante el veto el producto deja de tener margen: la cola NO aplica el
    4 % congelado; resuelve el respaldo completo y descarta la pausa."""
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    with _db("orbit_m2_cola_respaldo") as conn:
        ids = _mundo(conn)
        _config_con_pausa_economica(conn)
        _metrica_economica(conn, _run(conn), ids["kw"])
        r1 = _corre(conn)
        assert r1.status == "done", r1.notes
        pausa = _pausa_de(conn, r1.cycle_id, ids["kw"])
        assert pausa is not None and pausa["target_procedencia"] == "margen_producto"

        _ventas_sin_costo(conn, _producto(conn))
        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        margen = conn.execute(
            "SELECT margen_neto_pct FROM v_margen_producto WHERE product_id = %s",
            (_producto(conn),),
        ).fetchone()
        assert margen[0] is None, "sin cobertura el margen es NULL, no cero"

        evidencias: list[dict] = []
        resultado = apply_cola._revalida_pause(
            conn,
            _aplicador(r2.cycle_id),
            "amazon_us",
            apply_cola.FilaCola(1, "pause", ids["kw"], None, pausa["decision_id"], {}, "released"),
            dt.datetime.now(dt.UTC),
            evidencias,
        )
        assert resultado == apply_cola.MOTIVO_YA_NO_CALIFICA
        [evidencia] = evidencias
        assert evidencia["target_procedencia"] != "margen_producto"
        assert Decimal(evidencia["target"]) > Decimal("16.67")
        assert evidencia["economia"]["producto"]["motivo"] in (
            "sin_margen",
            "cobertura_baja",
            "ventana_corta",
        )


def test_la_seleccion_anterior_restaura_la_cascada_sin_borrar_evidencia(monkeypatch):
    """La seleccion auto03 conserva la meta live y devuelve la cola al camino
    anterior: usa el target congelado de la decision, no la economia fresca.
    Las evaluaciones no se borran."""
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    with _db("orbit_m2_cola_auto03") as conn:
        ids = _mundo(conn)
        _config_con_pausa_economica(conn)
        _metrica_economica(conn, _run(conn), ids["kw"])
        r1 = _corre(conn)
        assert r1.status == "done", r1.notes
        pausa = _pausa_de(conn, r1.cycle_id, ids["kw"])
        assert pausa is not None and pausa["target_procedencia"] == "margen_producto"
        evaluaciones_antes = conn.execute(
            "SELECT count(*) FROM evaluacion_producto_ciclo"
        ).fetchone()[0]

        _ventas_sin_costo(conn, _producto(conn))
        _config_con_economia(conn, "auto03")
        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        evidencias: list[dict] = []
        resultado = apply_cola._revalida_pause(
            conn,
            _aplicador(r2.cycle_id),
            "amazon_us",
            apply_cola.FilaCola(1, "pause", ids["kw"], None, pausa["decision_id"], {}, "released"),
            dt.datetime.now(dt.UTC),
            evidencias,
        )
        assert resultado is None, "auto03 revalida con el target congelado del 4 %"
        [evidencia] = evidencias
        assert evidencia["economia"]["version"] == "auto03"
        assert Decimal(evidencia["target"]) == Decimal("4")
        assert (
            conn.execute("SELECT count(*) FROM evaluacion_producto_ciclo").fetchone()[0]
            >= evaluaciones_antes
        )
        assert _settings(conn)["ads_producto_meta_amazon_us"] == "live"


def test_version_auto03_conserva_la_rampa_en_el_ciclo():
    """Con meta live y la version auto03, un cambio de margen posterior sigue
    la rampa de 0.5: 25 % previo y meta 4 % aplica 24.5 %, no 4 %."""
    with _db("orbit_m2_rampa_auto03") as conn:
        ids = _mundo(conn, margen=50)
        _config_con_economia(conn, "auto03")
        r1 = _corre(conn)
        assert r1.status == "done", r1.notes
        assert _target(conn, r1.cycle_id, ids["kw"]) == (Decimal("25"), "margen_producto")
        _a_live(conn, r1.cycle_id)
        _fees_por_orden(conn, _producto(conn), Decimal("42"))
        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        assert _target(conn, r2.cycle_id, ids["kw"]) == (Decimal("24.5"), "margen_producto")


def test_el_juicio_de_perdida_usa_el_equilibrio_del_producto():
    """h_ecff454c: con la meta individual ganando, el caso juzga con el
    equilibrio del PRODUCTO (8 %), no con el de la plataforma (o None). Una
    vendedora con ACoS 15 % sobre target 4 % recibe el recorte de perdida."""
    with _db("orbit_m2_equilibrio") as conn:
        ids = _mundo(conn)
        kw = ids["kw"]
        kw2 = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9603",
            parent=ids["ag"],
            match_type="EXACT",
            keyword_text="kw sana",
        )
        _estado(conn, kw2, synced_at=_SYNCED, current_bid=Decimal("1.00"), bid_currency="USD")
        run = _run(conn)
        fechas = [dt.date(2026, 7, 3) + dt.timedelta(days=i) for i in range(41)]
        obs_cero = DECIDED_AT - dt.timedelta(minutes=40)
        obs_dato = DECIDED_AT - dt.timedelta(minutes=20)
        for fecha in fechas:
            _metrica(
                conn,
                run,
                kw,
                fecha,
                obs_cero,
                cost="0",
                ad_revenue="0",
                clicks=0,
                orders=0,
                impressions=0,
            )
            _metrica(
                conn,
                run,
                kw2,
                fecha,
                obs_dato,
                cost="1",
                ad_revenue="100",
                clicks=10,
                orders=1,
                impressions=100,
            )
        for fecha in (
            dt.date(2026, 7, 20),
            dt.date(2026, 7, 27),
            dt.date(2026, 8, 3),
            dt.date(2026, 8, 5),
            dt.date(2026, 8, 9),
        ):
            _metrica(
                conn,
                run,
                kw,
                fecha,
                obs_dato,
                cost="30",
                ad_revenue="200",
                clicks=20,
                orders=1,
                impressions=100,
            )

        res = _corre(conn)

        assert res.status == "done", res.notes
        decisiones = [d for d in _decisions_de(conn, res.cycle_id) if d[0] == kw and d[1] == "bid"]
        assert decisiones, "ACoS 15 sobre target 4 y equilibrio 8 pierde dinero: recorta"
        inputs = decisiones[0][9]
        assert inputs["motivo"] in ("pierde_dinero", "pierde_dinero_fuerte")
        caso = inputs["caso"]
        assert Decimal(caso["economia"]["target_acos_pct"]) == Decimal("4")
        assert Decimal(caso["economia"]["plataforma"]["equilibrio_acos_pct"]) == Decimal("8")
        assert Decimal(inputs["target_snapshot"]["equilibrio_pct"]) == Decimal("8")


def test_cola_camina_el_respaldo_familiar_desde_el_previo_de_la_hoja():
    """h_32743606 (2): en la segunda vuelta del respaldo, la cola usa el
    target familiar previo de la hoja y coincide con el ciclo ejecutor."""
    with _db("orbit_m2_cola_rampa_familia") as conn:
        ids = _mundo_familia(conn, margen_familia=20)
        ids["ag"] = conn.execute(
            "SELECT parent_id FROM ad_entity WHERE id = %s", (ids["kw"],)
        ).fetchone()[0]
        _config_version(
            conn,
            {
                **_settings(conn),
                "ads_producto_meta_amazon_us": "shadow",
                "ads_producto_economia_amazon_us": "producto_directo_v1",
            },
        )
        r1 = _corre(conn)
        assert r1.status == "done", r1.notes
        assert _target(conn, r1.cycle_id, ids["kw"]) == (Decimal("29.5"), "margen_familia")
        _a_live(conn, r1.cycle_id)
        r2 = _corre(conn)
        assert r2.status == "done", r2.notes
        assert _target(conn, r2.cycle_id, ids["kw"]) == (Decimal("29.0"), "margen_familia")
        _a_live(conn, r2.cycle_id)
        r3 = _corre(conn)
        assert r3.status == "done", r3.notes
        ejecutor = _targets_de(conn, r3.cycle_id)[ids["kw"]]
        assert (ejecutor[2], ejecutor[3]) == (Decimal("28.5"), "margen_familia")

        vigente = apply_cola._target_pause_vigente(
            conn,
            "amazon_us",
            ids["kw"],
            r3.cycle_id,
            {"target_procedencia": "margen_producto", "target_snapshot": {}},
        )
        assert vigente == (Decimal("28.5"), "margen_familia")


def test_cola_reintenta_el_padre_de_la_subfamilia_sin_fila():
    """h_32743606 (1): una subfamilia sin fila en v_margen_familia reintenta
    al padre que si mide, igual que el ciclo ejecutor."""
    with _db("orbit_m2_cola_subfamilia") as conn:
        ids = _mundo_familia(conn, margen_familia=20)
        ids["ag"] = conn.execute(
            "SELECT parent_id FROM ad_entity WHERE id = %s", (ids["kw"],)
        ).fetchone()[0]
        pa1 = conn.execute(
            "SELECT id FROM ad_entity WHERE kind = 'product_ad' AND external_id = 'PA-1'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE ad_entity_state SET status = 'ARCHIVED' WHERE ad_entity_id = %s", (pa1,)
        )
        padre = _familia(conn, "Padre", "padre")
        sub = _familia(conn, "Sub", "sub", padre_id=padre)
        pid_sub, lid_sub = _producto_con_listing(conn, "SKU-SUB")
        _etiqueta(conn, pid_sub, sub)
        _product_ad(conn, ids["ag"], lid_sub, "PA-SUB")
        pid_padre, _lid = _producto_con_listing(conn, "SKU-PADRE")
        _etiqueta(conn, pid_padre, padre)
        hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
        _ledger_ventas(conn, pid_padre, hoy, dias=40, precio=100, costo=70)
        _config_version(
            conn,
            {
                **_settings(conn),
                "ads_producto_meta_amazon_us": "shadow",
                "ads_producto_economia_amazon_us": "producto_directo_v1",
            },
        )

        res = _corre(conn)

        assert res.status == "done", res.notes
        ejecutor = _targets_de(conn, res.cycle_id)[ids["kw"]]
        assert (ejecutor[2], ejecutor[3]) == (Decimal("29.5"), "margen_familia")
        vigente = apply_cola._target_pause_vigente(
            conn,
            "amazon_us",
            ids["kw"],
            res.cycle_id,
            {"target_procedencia": "margen_producto", "target_snapshot": {}},
        )
        assert vigente == (Decimal("29.5"), "margen_familia")


def _pausa_de(conn, cycle_id: int, entidad: int) -> dict | None:
    fila = conn.execute(
        "SELECT id, inputs FROM decision WHERE cycle_id = %s AND ad_entity_id = %s"
        " AND kind = 'pause'",
        (cycle_id, entidad),
    ).fetchone()
    if fila is None:
        return None
    inputs = fila[1]
    return {
        "decision_id": fila[0],
        "target_procedencia": inputs.get("target_procedencia"),
        "target_acos_pct_usado": inputs.get("target_acos_pct_usado"),
        "target_snapshot": inputs.get("target_snapshot"),
    }


def test_la_sombra_no_mezcla_el_equilibrio_producto_si_su_meta_no_gana():
    """F14: con la meta en sombra, si un goal de campana gana el target, el
    veredicto hipotetico usa el mismo equilibrio que el camino real (el de
    plataforma), no el del producto. El veredicto con y sin meta coincide
    porque el target resuelto es el mismo."""
    with _db("orbit_m2_sombra_equilibrio") as conn:
        ids = _mundo(conn)
        _config_con_meta(conn, "shadow")
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
            " bid_ceiling, bid_currency, enabled, mode)"
            " VALUES ('campaign', %s, 25, 0.40, 2.50, 'USD', true, 'live')",
            (ids["camp"],),
        )
        kw = ids["kw"]
        run = _run(conn)
        fechas = [dt.date(2026, 7, 3) + dt.timedelta(days=i) for i in range(41)]
        obs_cero = DECIDED_AT - dt.timedelta(minutes=40)
        obs_dato = DECIDED_AT - dt.timedelta(minutes=20)
        for fecha in fechas:
            _metrica(
                conn,
                run,
                kw,
                fecha,
                obs_cero,
                cost="0",
                ad_revenue="0",
                clicks=0,
                orders=0,
                impressions=0,
            )
        for fecha in (
            dt.date(2026, 7, 20),
            dt.date(2026, 7, 27),
            dt.date(2026, 8, 3),
            dt.date(2026, 8, 5),
            dt.date(2026, 8, 9),
        ):
            _metrica(
                conn,
                run,
                kw,
                fecha,
                obs_dato,
                cost="30",
                ad_revenue="200",
                clicks=20,
                orders=1,
                impressions=100,
            )

        res = _corre(conn)
        assert res.status == "done", res.notes
        _id, resultado = _evaluacion(conn, res.cycle_id)
        hoja = next(h for h in resultado["sombra_hojas"] if h["hoja_id"] == kw)
        assert hoja["procedencia_con_meta"] == "goal_campana"
        assert hoja["veredicto_con_meta"] == hoja["veredicto_actual"]
