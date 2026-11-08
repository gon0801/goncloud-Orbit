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
from test_cycle import _config_version, _corre, _run
from test_cycle_target_ciclo import _a_live, _mundo_familia
from test_productos_io import _db as _db_productos
from test_productos_meta_ciclo import SQL76, _decision, _settings, _target
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
    _config_version(conn, {**_settings(conn), "ads_producto_meta_amazon_us": "live"})
    return ids


def _config_con_pausa_economica(conn) -> None:
    _config_version(conn, {**_settings(conn), "ads_pause_economica": True})


def _config_con_meta(conn, modo: str) -> None:
    _config_version(conn, {**_settings(conn), "ads_producto_meta_amazon_us": modo})


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
        assert economia["producto"]["politica"] == "v2"


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
    """Apagar la regla (meta en shadow) restaura la cascada previa en la cola
    sin borrar evaluaciones: la pausa individual congelada no se aplica."""
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    with _db("orbit_m2_cola_apagada") as conn:
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

        _config_con_meta(conn, "shadow")
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
        assert resultado == apply_cola.MOTIVO_YA_NO_CALIFICA
        [evidencia] = evidencias
        assert evidencia["target_procedencia"] != "margen_producto"
        assert evidencia["economia"]["producto"]["motivo"] == "meta_no_live"
        assert Decimal(evidencia["target"]) > Decimal("16.67")
        assert (
            conn.execute("SELECT count(*) FROM evaluacion_producto_ciclo").fetchone()[0]
            >= evaluaciones_antes
        )


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
