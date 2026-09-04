"""ORBIT 06 2.3: vista v_target_margen_plataforma + resolver del peldano.

La vista SOLO MIDE (spec §3, formula literal); el resolver puro
(goals.resuelve_target_margen) aplica guardas, banda y paso (spec §5/§7).
Los rojos (a), (c) y (g) del DoD viven aqui; (b)/(f) en
test_optimizer_goals.py, (d) en test_cycle.py, (e) en test_api_dashboard.py
y test_notifica.py.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

RAIZ = Path(__file__).resolve().parents[1]
SQL_BASE = (RAIZ / "migrations" / "0001_initial.sql").read_text(encoding="utf-8")


def _sql15() -> str:
    """La migracion 0015 (lectura perezosa: sin ella, cada test de vista
    falla en su fixture con FileNotFoundError en vez de romper la
    coleccion del archivo)."""
    return (RAIZ / "migrations" / "0015_target_margen_plataforma.sql").read_text(encoding="utf-8")


def _postgres_obligatorio_ausente() -> bool:
    import os
    import socket

    if os.environ.get("ORBIT_TEST_DSN"):
        return False
    try:
        socket.create_connection(("127.0.0.1", 5432), timeout=1).close()
    except OSError:
        return True
    return False


def _test_dsn() -> str:
    import os

    return os.environ.get("ORBIT_TEST_DSN") or "postgresql://orbit:orbit@localhost:5432/postgres"


@pytest.fixture()
def conn15():
    """DB temporal con el esquema + la migracion 0015 (patron _db_temporal)."""
    import contextlib

    nombre = "orbit_target_margen"
    admin = psycopg.connect(_test_dsn(), autocommit=True)
    admin.execute(f'DROP DATABASE IF EXISTS "{nombre}"')
    admin.execute(f'CREATE DATABASE "{nombre}"')
    admin.close()
    conn = psycopg.connect(_test_dsn().rsplit("/", 1)[0] + f"/{nombre}")
    try:
        conn.execute(SQL_BASE)
        conn.execute(_sql15())
        conn.commit()
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()
        admin = psycopg.connect(_test_dsn(), autocommit=True)
        admin.execute(f'DROP DATABASE IF EXISTS "{nombre}"')
        admin.close()


def _hoy(conn) -> dt.date:
    """CURRENT_DATE de la base (el `hoy` de la vista; misma maquina)."""
    return conn.execute("SELECT CURRENT_DATE").fetchone()[0]


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[
        0
    ]


def _producto(conn, sku: str, costo: str, moneda: str, desde: dt.date) -> int:
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from) VALUES (%s, %s, %s, true, %s)",
        (pid, Decimal(costo), moneda, desde),
    )
    return pid


def _venta(
    conn,
    run: int,
    pid: int | None,
    fecha: dt.date,
    monto: str,
    moneda: str,
    order_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, product_id, quantity,"
        " amount, amount_currency, order_id, ingest_run_id)"
        " VALUES ('amazon_us', 'sale', %s, %s, 1, %s, %s, %s, %s)",
        (fecha, pid, Decimal(monto), moneda, order_id, run),
    )


def _cargo(
    conn,
    run: int,
    fecha: dt.date,
    monto: str,
    fee_type: str | None,
    moneda: str = "MXN",
    kind: str = "fee",
    order_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
        " fee_type, order_id, ingest_run_id) VALUES ('amazon_us', %s, %s, %s, %s, %s, %s, %s)",
        (kind, fecha, Decimal(monto), moneda, fee_type, order_id, run),
    )


def _siembra_feliz(conn, hoy: dt.date) -> None:
    """70 ventas x100 con costo 50 + 7 fees x-100 + 1 fee ads x-9999:
    venta_total = cubierta = 7000, con_orden 0, sin_orden -700, cogs 3500,
    margen 40, cobertura 1, dias 70, fees_sin_tipo 0."""
    run = _run(conn)
    pid = _producto(conn, "P1", "50", "MXN", hoy - dt.timedelta(days=200))
    for i in range(70):
        _venta(conn, run, pid, hoy - dt.timedelta(days=100 - i), "100", "MXN")
    for i in range(7):
        _cargo(conn, run, hoy - dt.timedelta(days=90 - i), "-100", "closing")
    _cargo(conn, run, hoy - dt.timedelta(days=50), "-9999", "ads")
    conn.commit()


def _fila_vista(conn, platform: str = "amazon_us") -> dict | None:
    conn.row_factory = dict_row
    try:
        return conn.execute(
            "SELECT * FROM v_target_margen_plataforma WHERE platform = %s", (platform,)
        ).fetchone()
    finally:
        conn.row_factory = tuple


# ---------------------------------------------------------------------------
# Rojos (a): la vista mide (una fila por plataforma, ventana, NULLs fail-loud)
# ---------------------------------------------------------------------------


def test_migracion_0015_parsea_con_grants_y_comment():
    """Estatico (corre en local): la migracion existe, parsea y trae GRANT a
    lectura + COMMENT con formula y lag (patron 0013/0014)."""
    import pglast

    texto = _sql15()
    pglast.parse_sql(texto)
    assert "CREATE VIEW v_target_margen_plataforma" in texto
    assert "COMMENT ON VIEW v_target_margen_plataforma" in texto
    for rol in ("app_read", "app_ingest", "app_decide", "app_admin"):
        assert rol in texto
    assert "GRANT SELECT ON v_target_margen_plataforma" in texto


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_vista_una_fila_por_plataforma_con_numeros_exactos(conn15):
    """Rojo (a/g): ventana [H-105, H-15) como DATEs, venta_total 7000 =
    cubierta (cobertura 1), cargos_con_orden 0, cargos_sin_orden -700 (el
    fee ads NO entra), cogs 3500, margen 40 exacto, dias 70, MXN,
    fees_sin_tipo 0."""
    hoy = _hoy(conn15)
    _siembra_feliz(conn15, hoy)
    fila = _fila_vista(conn15)
    assert fila is not None
    assert fila["ventana_desde"] == hoy - dt.timedelta(days=105)
    assert fila["ventana_hasta"] == hoy - dt.timedelta(days=15)
    assert fila["venta_total"] == Decimal("7000")
    assert fila["venta_cubierta"] == Decimal("7000")
    assert fila["cargos_con_orden"] == Decimal("0")
    assert fila["cargos_sin_orden"] == Decimal("-700")
    assert fila["cogs"] == Decimal("3500")
    assert fila["margen_neto_pct"] == Decimal("40")
    assert fila["cobertura"] == Decimal("1")
    assert fila["dias_con_venta"] == 70
    assert fila["fees_sin_tipo"] == 0
    assert fila["moneda"] == "MXN"
    assert fila["ledger_fresco_at"] is not None
    # MX sin filas no sale (GROUP BY): una fila por plataforma CON dato
    assert _fila_vista(conn15, "amazon_mx") is None


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_vista_bordes_de_ventana_incluye_h105_excluye_h15(conn15):
    """Rojo (a): event_date >= H-105 entra, event_date >= H-15 no."""
    hoy = _hoy(conn15)
    run = _run(conn15)
    pid = _producto(conn15, "PB", "10", "MXN", hoy - dt.timedelta(days=200))
    _venta(conn15, run, pid, hoy - dt.timedelta(days=105), "100", "MXN")
    _venta(conn15, run, pid, hoy - dt.timedelta(days=15), "100", "MXN")
    conn15.commit()
    fila = _fila_vista(conn15)
    assert fila["venta_total"] == Decimal("100")
    assert fila["venta_cubierta"] == Decimal("100")
    assert fila["dias_con_venta"] == 1


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_vista_cobertura_por_monto_baja_nulifica_margen(conn15):
    """Rojo (g): cobertura POR MONTO 5400/6000 = 0.9 -> margen NULL
    (fail-loud) con dias sanos (60 fechas: el gate que nulifica es la
    cobertura, no los dias). El conteo (60/60 lineas) daria 1: la trampa A3."""
    hoy = _hoy(conn15)
    run = _run(conn15)
    pid = _producto(conn15, "PC", "10", "MXN", hoy - dt.timedelta(days=200))
    for i in range(54):
        _venta(conn15, run, pid, hoy - dt.timedelta(days=100 - i), "100", "MXN")
    for i in range(6):
        _venta(conn15, run, None, hoy - dt.timedelta(days=46 - i), "100", "MXN")
    conn15.commit()
    fila = _fila_vista(conn15)
    assert fila["dias_con_venta"] == 60
    assert fila["venta_total"] == Decimal("6000")
    assert fila["venta_cubierta"] == Decimal("5400")
    assert fila["cobertura"] == Decimal("0.9")
    assert fila["margen_neto_pct"] is None
    assert fila["moneda"] == "MXN"


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_vista_cargos_plataforma_se_prorratean(conn15):
    """Rojo (g): 57 ventas x100 cubiertas + 3 x100 sin costo (cobertura
    0.95, en el borde: el margen APLICA; 60 fechas, dias OK) + fee
    plataforma -300: el margen usa -300 x 0.95 = -285 ->
    100 x (5700 - 285 - 2850)/5700 = 45."""
    hoy = _hoy(conn15)
    run = _run(conn15)
    pid = _producto(conn15, "PP", "50", "MXN", hoy - dt.timedelta(days=200))
    for i in range(57):
        _venta(conn15, run, pid, hoy - dt.timedelta(days=100 - i), "100", "MXN")
    for i in range(3):
        _venta(conn15, run, None, hoy - dt.timedelta(days=43 - i), "100", "MXN")
    _cargo(conn15, run, hoy - dt.timedelta(days=90), "-300", "closing")
    conn15.commit()
    fila = _fila_vista(conn15)
    assert fila["dias_con_venta"] == 60
    assert fila["cobertura"] == Decimal("0.95")
    assert fila["cargos_sin_orden"] == Decimal("-300")
    assert fila["margen_neto_pct"] == Decimal("45")


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_vista_cargos_con_orden_atados_a_su_venta(conn15):
    """Rojo (g, A5): el cargo con order_id pertenece a SU venta aunque caiga
    fuera de la ventana; con order de venta NO cubierta o inexistente no
    cuenta."""
    hoy = _hoy(conn15)
    run = _run(conn15)
    pid = _producto(conn15, "PO", "10", "MXN", hoy - dt.timedelta(days=200))
    _venta(conn15, run, pid, hoy - dt.timedelta(days=100), "100", "MXN", order_id="O1")
    _venta(conn15, run, None, hoy - dt.timedelta(days=99), "100", "MXN", order_id="O2")
    # fuera de la ventana (H-10) pero de venta cubierta -> CUENTA
    _cargo(conn15, run, hoy - dt.timedelta(days=10), "-50", "closing", order_id="O1")
    # de venta no cubierta -> NO cuenta
    _cargo(conn15, run, hoy - dt.timedelta(days=98), "-30", "closing", order_id="O2")
    # de orden inexistente -> NO cuenta
    _cargo(conn15, run, hoy - dt.timedelta(days=98), "-10", "closing", order_id="OX")
    # refund con orden cubierta tambien es cargo con orden
    _cargo(conn15, run, hoy - dt.timedelta(days=97), "-5", "closing", kind="refund", order_id="O1")
    conn15.commit()
    fila = _fila_vista(conn15)
    assert fila["cargos_con_orden"] == Decimal("-55")
    assert fila["cargos_sin_orden"] == Decimal("0")
    assert fila["venta_cubierta"] == Decimal("100")
    assert fila["venta_total"] == Decimal("200")


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_vista_fee_sin_tipo_nulifica_margen(conn15):
    """Rojo (g, A6): un fee con fee_type NULL cuenta fees_sin_tipo y deja
    el margen NULL (doble castigo potencial: fail-loud)."""
    hoy = _hoy(conn15)
    _siembra_feliz(conn15, hoy)
    run = _run(conn15)
    _cargo(conn15, run, hoy - dt.timedelta(days=40), "-1", None)
    conn15.commit()
    fila = _fila_vista(conn15)
    assert fila["fees_sin_tipo"] == 1
    assert fila["margen_neto_pct"] is None
    assert fila["venta_cubierta"] == Decimal("7000")


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_vista_dias_cortos_nulifican_margen(conn15):
    """Rojo (g): 59 fechas con venta (< 60) -> margen NULL aunque todo lo
    demas este sano."""
    hoy = _hoy(conn15)
    run = _run(conn15)
    pid = _producto(conn15, "PD", "10", "MXN", hoy - dt.timedelta(days=200))
    for i in range(59):
        _venta(conn15, run, pid, hoy - dt.timedelta(days=100 - i), "100", "MXN")
    conn15.commit()
    fila = _fila_vista(conn15)
    assert fila["dias_con_venta"] == 59
    assert fila["cobertura"] == Decimal("1")
    assert fila["margen_neto_pct"] is None


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_vista_mezcla_de_moneda_nulifica_margen(conn15):
    """Rojo (g): dos monedas en la ventana -> moneda NULL (canario) Y
    margen NULL (fail-loud, spec adjudicado: antes se calculaba igual)."""
    hoy = _hoy(conn15)
    run = _run(conn15)
    pid = _producto(conn15, "PM", "10", "MXN", hoy - dt.timedelta(days=200))
    for i in range(60):
        _venta(conn15, run, pid, hoy - dt.timedelta(days=100 - i), "100", "MXN")
    conn15.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, amount,"
        " amount_currency, ingest_run_id)"
        " VALUES ('amazon_us', 'sale', %s, 100, 'USD', %s)",
        (hoy - dt.timedelta(days=40), run),
    )
    conn15.commit()
    fila = _fila_vista(conn15)
    assert fila["dias_con_venta"] == 61
    assert fila["moneda"] is None
    assert fila["margen_neto_pct"] is None


# ---------------------------------------------------------------------------
# Rojos (c): el resolver puro (guardas, banda, paso, fraccion)
# ---------------------------------------------------------------------------


def _medicion(
    *,
    margen=Decimal("40"),
    cobertura=Decimal("1"),
    dias=70,
    venta_cubierta=Decimal("7000"),
    fresco_hoy_menos=1,
) -> object:
    """Medicion feliz y parametrizable (sin DB)."""
    from app.optimizer import goals as g

    return g.MedicionMargen(
        margen_neto_pct=margen,
        cobertura=cobertura,
        dias_con_venta=dias,
        venta_cubierta=venta_cubierta,
        ledger_fresco_at=(
            dt.datetime(2026, 9, 4, 12, 0) - dt.timedelta(days=fresco_hoy_menos)
            if fresco_hoy_menos is not None
            else None
        ),
        moneda="MXN",
        ventana_desde=dt.date(2026, 5, 22),
        ventana_hasta=dt.date(2026, 8, 20),
    )


def test_resolver_feliz_aplica_derivado_con_ultimo_cercano():
    """Rojo (c): margen 40 x fraccion 0.5 = 20, ultimo 20.2 -> clamp no
    recorta (|20-20.2| <= 0.5): aplicado 20, motivo None."""
    from app.optimizer import goals as g

    res = g.resuelve_target_margen(
        _medicion(), Decimal("0.5"), dt.date(2026, 9, 4), Decimal("20.2")
    )
    assert res.motivo is None
    assert res.derivado == Decimal("20")
    assert res.aplicado == Decimal("20")


def test_resolver_cinco_abstenciones_aisladas():
    """Rojo (c/h): cada motivo con TODO lo demas sano (un caso por motivo).
    fuera_de_banda YA NO EXISTE (A1: la banda clampe, D-2.3.10)."""
    from app.optimizer import goals as g

    hoy = dt.date(2026, 9, 4)
    casos = [
        ("sin_margen", _medicion(margen=None, venta_cubierta=None, cobertura=None, dias=None)),
        ("cobertura_baja", _medicion(cobertura=Decimal("0.9"))),
        ("ventana_corta", _medicion(dias=59)),
        ("sin_fraccion", _medicion()),
        ("ledger_rancio", _medicion(fresco_hoy_menos=4)),
    ]
    for motivo, med in casos:
        fraccion = None if motivo == "sin_fraccion" else Decimal("0.5")
        res = g.resuelve_target_margen(med, fraccion, hoy, Decimal("20"))
        assert res.motivo == motivo, motivo
        assert res.aplicado is None, motivo
        assert res.derivado is None, motivo


def test_resolver_dias_antes_que_margen_nulo():
    """Rojo (h, D-2.3.11): con la vista nulificando el margen ante dias
    cortos, el resolver debe etiquetar ventana_corta (no sin_margen)."""
    from app.optimizer import goals as g

    res = g.resuelve_target_margen(
        _medicion(margen=None, cobertura=Decimal("1"), dias=59, venta_cubierta=Decimal("100")),
        Decimal("0.5"),
        dt.date(2026, 9, 4),
        Decimal("20"),
    )
    assert res.motivo == "ventana_corta"
    assert res.aplicado is None


def test_resolver_banda_clampea_no_abstiene():
    """Rojo (h, A1): derivado 45.2 -> aplicado 45 y 9 -> 10 con ultimo
    None (sin ancla); bordes 10/45 exactos aplican sin motivo."""
    from app.optimizer import goals as g

    hoy = dt.date(2026, 9, 4)
    res = g.resuelve_target_margen(_medicion(margen=Decimal("90.4")), Decimal("0.5"), hoy, None)
    assert res.motivo is None
    assert res.derivado == Decimal("45.2")
    assert res.aplicado == Decimal("45")
    res = g.resuelve_target_margen(_medicion(margen=Decimal("18")), Decimal("0.5"), hoy, None)
    assert res.motivo is None
    assert res.derivado == Decimal("9")
    assert res.aplicado == Decimal("10")
    res = g.resuelve_target_margen(_medicion(margen=Decimal("20")), Decimal("0.5"), hoy, None)
    assert (res.derivado, res.aplicado, res.motivo) == (Decimal("10"), Decimal("10"), None)
    res = g.resuelve_target_margen(_medicion(margen=Decimal("90")), Decimal("0.5"), hoy, None)
    assert (res.derivado, res.aplicado, res.motivo) == (Decimal("45"), Decimal("45"), None)


def test_resolver_banda_antes_que_paso():
    """Rojo (h, A1): con ultimo 30 y derivado 45.2, primero clamp a 45 y
    luego paso a 30.5 (el paso no congela el crudo fuera de banda)."""
    from app.optimizer import goals as g

    res = g.resuelve_target_margen(
        _medicion(margen=Decimal("90.4")), Decimal("0.5"), dt.date(2026, 9, 4), Decimal("30")
    )
    assert res.motivo is None
    assert res.derivado == Decimal("45.2")
    assert res.aplicado == Decimal("30.5")


def test_resolver_paso_maximo_medio_punto():
    """Rojo (c): derivado 20 con ultimo 19 -> 19.5; con ultimo 21 -> 20.5;
    sin ultimo -> derivado sin recorte."""
    from app.optimizer import goals as g

    hoy = dt.date(2026, 9, 4)
    med = _medicion()
    assert g.resuelve_target_margen(med, Decimal("0.5"), hoy, Decimal("19")).aplicado == Decimal(
        "19.5"
    )
    assert g.resuelve_target_margen(med, Decimal("0.5"), hoy, Decimal("21")).aplicado == Decimal(
        "20.5"
    )
    sin = g.resuelve_target_margen(med, Decimal("0.5"), hoy, None)
    assert sin.aplicado == Decimal("20") and sin.motivo is None


def test_resolver_fraccion_ausente_abstiene():
    """Rojo (c/h): clave ausente (o JSON null) -> None -> sin_fraccion;
    '0.5' (string o numero) -> 0.5."""
    from app.optimizer import goals as g

    for settings in ({}, {"k": "x"}, {"ads_target_fraccion_margen_amazon_us": None}):
        assert g.fraccion_desde_settings(settings, "amazon_us") is None
    assert g.fraccion_desde_settings(
        {"ads_target_fraccion_margen_amazon_us": "0.5"}, "amazon_us"
    ) == Decimal("0.5")
    assert g.fraccion_desde_settings(
        {"ads_target_fraccion_margen_amazon_us": 0.5}, "amazon_us"
    ) == Decimal("0.5")
    res = g.resuelve_target_margen(_medicion(), None, dt.date(2026, 9, 4), Decimal("20"))
    assert res.motivo == "sin_fraccion" and res.aplicado is None


def test_resolver_fraccion_invalida_revienta():
    """Rojo (h, A4): clave PRESENTE pero invalida (basura, coma decimal,
    cero, negativa, > 1, NaN/Inf) -> ValueError ruidoso, NO abstencion."""
    from app.optimizer import goals as g

    for mala in ("abc", "", "0,5", "0", "-0.2", "1.5", "NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="fraccion"):
            g.fraccion_desde_settings({"ads_target_fraccion_margen_amazon_us": mala}, "amazon_us")


def test_resuelve_target_margen_valida_fraccion():
    """Rojo (h, D-2.3.12): el nucleo tambien valida (defensa sin settings):
    fraccion non-None invalida -> ValueError."""
    from app.optimizer import goals as g

    for mala in (Decimal("0"), Decimal("-0.5"), Decimal("1.5"), Decimal("NaN")):
        with pytest.raises(ValueError, match="fraccion"):
            g.resuelve_target_margen(_medicion(), mala, dt.date(2026, 9, 4), Decimal("20"))


def test_resolver_fresco_al_borde_no_es_rancio():
    """Rojo (c): fresco == hoy-3 aplica (rancio es <, estricto)."""
    from app.optimizer import goals as g

    res = g.resuelve_target_margen(
        _medicion(fresco_hoy_menos=3), Decimal("0.5"), dt.date(2026, 9, 4), Decimal("20")
    )
    assert res.motivo is None and res.aplicado == Decimal("20")


# ---------------------------------------------------------------------------
# Rojo (g): tools/compara_target_margen.py sobre la base de tests
# ---------------------------------------------------------------------------


def _goal_sin_target(conn) -> None:
    """Goal de plataforma habilitado SIN target (el peldano puede ganar)."""
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode)"
        " VALUES ('platform', 'amazon_us', NULL, 0.40, 2.50, 'USD', '9002', '9102',"
        " 0.75, true, 'live')"
    )


def _metrica_acos_33(conn, run: int, kw: int) -> None:
    """10 dias x cost 3.40 / revenue 10 (ACoS 34 %): con manual 30 no hay
    banda (34 < 1.15x30=34.5); con derivado 29.5 SI (-12 %, 34 > 33.925).
    Correccion 2026-09-04 al rojo (g): la version con 3.33 asumia
    multiplicador 1.12 y el motor sellado usa 1.15 (con 3.33 ninguna de las
    dos ramas dispara y el test jamas verdea; con 3.40 discrimina).
    Cero ordenes con revenue != 0: ni pause (10 clicks) ni cero-ventas."""
    from test_cycle import _metrica, _obs

    for i in range(10):
        fecha = dt.date(2026, 8, 7) + dt.timedelta(days=i)
        _metrica(
            conn,
            run,
            kw,
            fecha,
            _obs(fecha),
            cost="3.40",
            ad_revenue="10.00",
            clicks=1,
            orders=0,
            impressions=10,
        )


@pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
def test_compara_produce_tabla_sobre_base_de_tests():
    """Rojo (g): un ciclo real con el peldano ganado (29.5) y una decision
    bid en zona de estrado: la tabla trae 1 fila que cambia de banda
    (manual 30 sin banda vs derivado 29.5 con -12 %)."""
    from test_cycle import (
        DECIDED_AT,
        _config_version,
        _corre,
        _db_temporal,
        _entidad,
        _estado,
        _run,
        _siembra_ledger_feliz,
    )

    from tools.compara_target_margen import compara

    with _db_temporal("orbit_c_compara") as (conn, _c):
        run = _run(conn)
        _config_version(
            conn,
            {
                "ads_optimizer_mode": "shadow",
                "ads_target_acos_pct_amazon_us": 30,
                "ads_target_fraccion_margen_amazon_us": "0.5",
            },
        )
        _goal_sin_target(conn)
        camp = _entidad(conn, "amazon_us", "campaign", "9401")
        ag = _entidad(conn, "amazon_us", "ad_group", "9402", parent=camp)
        kw = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9403",
            parent=ag,
            match_type="EXACT",
            keyword_text="kw compara",
        )
        sinc = DECIDED_AT - dt.timedelta(hours=4)
        _estado(conn, kw, synced_at=sinc, current_bid=Decimal("1.00"), bid_currency="USD")
        _estado(conn, ag, synced_at=sinc)
        _estado(conn, camp, synced_at=sinc)
        _metrica_acos_33(conn, run, kw)
        hoy = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
        _siembra_ledger_feliz(conn, hoy)
        res = _corre(conn)
        assert res.status == "done", res.notes
        filas, resumen = compara(conn, res.cycle_id)
        assert resumen["decisiones"] == 1
        assert resumen["cambian_banda"] == 1
        assert len(filas) == 1
        fila = filas[0]
        assert fila["cambia_banda"] is True
        assert fila["factor_manual"] is None
        # Correccion 2026-09-04: el factor es el delta de banda sellado
        # (-0.12), no el multiplicador (0.88) que el rojo original asumia.
        assert fila["factor_derivado"] == Decimal("-0.12")


# ---------------------------------------------------------------------------
# ORBIT 06 2.3 - superficie y tool puros (verdes nuevos de la implementacion)
# ---------------------------------------------------------------------------


def _inputs_bid_acos_34() -> dict:
    """Inputs congelados minimos con ACoS 34 % (10 dias, 5 ordenes): con
    manual 30 no hay banda (34 < 1.15x30); con derivado 29.5 dispara -12 %.
    Sin corte -> umbrales de replay de su era; sin pause (hay ordenes)."""
    return {
        "platform": "amazon_us",
        "goal": {"bid_floor": "0.40", "bid_ceiling": "2.50"},
        "ventanas": {
            "bids": {
                "window_start": "2026-08-07",
                "window_end": "2026-08-16",
                "fechas": 10,
                "moneda": "USD",
                "cost": "34.00",
                "ad_revenue": "100.00",
                "revenue_same_sku": "100.00",
                "clicks": 50,
                "orders": 5,
                "observed_at_max": "2026-08-16T12:00:00+00:00",
            },
            "cortes": None,
        },
        "target_acos_pct_usado": "29.5",
        "bid_actual": "1.00",
        "bid_moneda": "USD",
    }


def test_replay_bid_con_target_inyecta_y_devuelve_factor():
    """replay_bid_con_target reejecuta decide_bid con el target DADO (no el
    congelado) y devuelve el ResultadoBid completo: manual 30 -> no-op sin
    banda; derivado 29.5 -> bid -12 % con new 0.88."""
    from app.optimizer.replay import replay_bid_con_target

    inputs = _inputs_bid_acos_34()
    res_m = replay_bid_con_target(inputs, Decimal("30"))
    assert res_m.kind is None
    assert res_m.factor is None
    res_d = replay_bid_con_target(inputs, Decimal("29.5"))
    assert res_d.kind == "bid"
    assert res_d.factor == Decimal("-0.12")
    assert res_d.new_value == Decimal("0.88")


def test_resuelve_targets_defaults_y_fallo_ruidoso():
    """Defaults desde notes.target (derivado = aplicado, manual = setting);
    args explicitos ganan; faltante = ValueError en voz alta (regla 3)."""
    from tools.compara_target_margen import resuelve_targets

    notas = {"target_aplicado": "29.5", "setting": "30"}
    assert resuelve_targets(notas, None, None) == (Decimal("30"), Decimal("29.5"))
    assert resuelve_targets(notas, "25", "18") == (Decimal("25"), Decimal("18"))
    with pytest.raises(ValueError):
        resuelve_targets({"setting": "30"}, None, None)
    with pytest.raises(ValueError):
        resuelve_targets({"target_aplicado": "29.5"}, None, None)
    with pytest.raises(ValueError):
        resuelve_targets(None, None, None)
    with pytest.raises(ValueError):
        resuelve_targets(notas, "basura", None)


def test_renglones_tolera_inputs_corruptos():
    """Una entrada corrupta no tumba la tabla: queda con error visible,
    cambia_banda False y cuenta en errores; la sana compara normal."""
    from tools.compara_target_margen import renglones

    entradas = [
        {
            "id": 1,
            "ad_entity_id": 11,
            "entidad_kind": "keyword",
            "keyword_text": "kw sana",
            "name": None,
            "external_id": None,
            "inputs": _inputs_bid_acos_34(),
        },
        {
            "id": 2,
            "ad_entity_id": 12,
            "entidad_kind": "keyword",
            "keyword_text": None,
            "name": None,
            "external_id": "x9",
            "inputs": None,
        },
    ]
    filas, cambian, errores = renglones(entradas, Decimal("30"), Decimal("29.5"))
    assert (cambian, errores) == (1, 1)
    assert filas[0]["cambia_banda"] is True
    assert filas[0]["entidad"] == "keyword:kw sana"
    assert filas[1]["cambia_banda"] is False
    assert filas[1]["error"]
    assert filas[1]["entidad"] == "keyword:x9"


def test_salud_bloque_target_abstencion_con_setting_vigente_null():
    """Abstencion CON setting en el snapshot: el vigente sigue null (el
    bloque describe EL PELDANO, y la cascada por entidad no tiene usado
    unico de ciclo; D-2.3.6). El setting solo alimenta al tool."""
    from app import api_common

    bloque = api_common.bloque_target_margen(
        {
            "notes": {
                "target": {
                    "procedencia": None,
                    "motivo_abstencion": "sin_fraccion",
                    "margen_neto_pct": None,
                    "fraccion": None,
                    "ventana_desde": None,
                    "ventana_hasta": None,
                    "target_derivado": None,
                    "target_aplicado": None,
                    "setting": "30",
                    "ledger_fresco_at": "2026-09-03T12:00:00+00:00",
                    "moneda": "MXN",
                }
            }
        },
        hoy=dt.date(2026, 9, 4),
    )
    assert bloque["target_vigente"] is None
    assert bloque["motivo_abstencion"] == "sin_fraccion"
