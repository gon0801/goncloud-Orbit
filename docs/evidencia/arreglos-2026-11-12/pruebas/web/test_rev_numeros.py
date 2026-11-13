"""Revision web (numeros de las pantallas). Cada prueba FALLA por la asercion
que describe la falla; no corrige nada.

Correr: python -m pytest -q -p no:cacheprovider tests/_revision_web/test_rev_numeros.py
"""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dashboard_resumen as dr  # noqa: E402
from app import pantalla_busquedas, pantalla_ruido  # noqa: E402
from app.optimizer.windows import DIAS_MADUREZ_CORTES  # noqa: E402
from app.ui_metricas import _tarjeta_canal, vista_resumen  # noqa: E402


class _Cursor:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return list(self._filas)

    def fetchone(self):
        return self._filas[0] if self._filas else None


class _ConnFalsa:
    """Responde por fragmento de SQL; lo demas devuelve vacio."""

    def __init__(self, respuestas):
        self._respuestas = respuestas

    def execute(self, sql, params=None):
        for fragmento, filas in self._respuestas:
            if fragmento in sql:
                return _Cursor(filas)
        return _Cursor([])


# ---------------------------------------------------------------------------
# F1: «Sin ventas atribuidas» cuando SI hubo ventas (ACoS None por huecos)
# ---------------------------------------------------------------------------


def test_tarjeta_no_afirma_sin_ventas_cuando_el_acos_falta_por_huecos():
    """Con meta configurada, una venta conocida de 5,000 y un dia sin gasto
    (dinero no alineado -> ACoS None), la tarjeta dice «Sin ventas
    atribuidas frente a la meta». Regla 3: dato faltante no es cero."""
    bloque = {
        "moneda": "MXN",
        "dias": 7,
        "totals": {
            "cost": "800.0000",
            "ad_revenue": "5000.0000",
            "acos": None,  # dinero_alineado False: un dia sin gasto
            "sin_ventas": False,
            "dinero_alineado": False,
        },
        "comparison": {"totals": {}, "variacion": {}},
        "coverage": {"dias_con_dato": 7, "completo": True},
        "series": [],
    }
    tarjeta = _tarjeta_canal("amazon_mx", bloque, "20")
    assert "Sin ventas" not in tarjeta["frase"], tarjeta["frase"]


# ---------------------------------------------------------------------------
# F2: comparar un periodo con dias inmaduros contra uno maduro
# ---------------------------------------------------------------------------


def test_resumen_7d_no_concluye_caida_por_dias_inmaduros():
    """Mismo negocio en ambos periodos (100 de venta y 20 de gasto por dia en
    la foto final). El periodo actual de 7 dias cae entero en [D-8, D-1] y
    trae la foto temprana que mide produccion (venta x0.76, gasto x1.03;
    sonda oct-2026). La tarjeta concluye «Atencion» y «las ventas bajaron»
    aunque no bajo nada: compara inmaduro contra maduro."""
    hoy = dt.date(2026, 11, 12)
    ventana = dr.ventana_resumen(periodo=7, desde=None, hasta=None, hoy=hoy)
    filas = []
    fecha = ventana.anterior_desde - dt.timedelta(days=6)
    while fecha <= ventana.hasta:
        inmaduro = fecha >= hoy - dt.timedelta(days=dr.DIAS_INMADUROS)
        costo = Decimal("20.6000") if inmaduro else Decimal("20.0000")
        venta = Decimal("76.0000") if inmaduro else Decimal("100.0000")
        observado = dt.datetime.combine(fecha + dt.timedelta(days=2), dt.time(8), tzinfo=dt.UTC)
        filas.append((fecha, costo, venta, 10, 2, observado))
        fecha += dt.timedelta(days=1)
    conn = _ConnFalsa(
        [
            ("GROUP BY v.metric_date", filas),
            ("SELECT max(o.metric_date)", [(ventana.hasta,)]),
            ("max(r.finished_at)", [(None,)]),
        ]
    )
    instante = dt.datetime(2026, 11, 12, 18, 0, tzinfo=dt.UTC)
    contrato = dr.resumen(
        conn, ventana=ventana, canales=("amazon_mx",), hoy=hoy, instante=instante
    )
    tarjeta = vista_resumen(contrato)["tarjetas"]["amazon_mx"]
    assert "bajaron" not in tarjeta["frase"], tarjeta["frase"]
    assert tarjeta["estado_texto"] != "Atención", (tarjeta["estado_texto"], tarjeta["frase"])


# ---------------------------------------------------------------------------
# F3: Campanas «30d» y Resumen «30 dias» no cuadran despues de las 18:00 CDMX
# ---------------------------------------------------------------------------

try:  # bancos de Postgres de la suite existente
    import test_api_dashboard as tad
    from test_schema import _postgres_obligatorio_ausente

    _SIN_PG = _postgres_obligatorio_ausente()
except Exception:  # pragma: no cover
    tad = None
    _SIN_PG = True


@pytest.mark.skipif(_SIN_PG, reason="sin Postgres local")
def test_campanas_30d_y_resumen_30d_dan_el_mismo_gasto_a_las_21h_cdmx(monkeypatch):
    """2026-11-13 03:00Z = 12-nov 21:00 CDMX (hora real de la sonda). Hay
    gasto de 10 MXN diario hasta el 11-nov (D-1, lo que la ingesta trae). El
    Resumen (zona del canal) suma [13-oct, 11-nov] = 300; Campanas (fecha UTC)
    suma [14-oct, 12-nov] = 290, con el ultimo dia vacio. Mismo rotulo, dos
    numeros (regla 2). En prod hoy: 23,113.11 vs 23,713.53 MXN."""
    from app import api_dashboard as dash

    instante = dt.datetime(2026, 11, 13, 3, 0, tzinfo=dt.UTC)
    with tad._db_temporal("orbit_rev_numeros_30d") as (conn, dsn):
        run = tad._run(conn)
        camp = tad._campana(conn, "amazon_mx", "7001", "Campana MX")
        fecha = dt.date(2026, 10, 1)
        while fecha <= dt.date(2026, 11, 11):
            tad._metrica(
                conn, run, camp, fecha, cost="10.0000", ad_revenue="50.0000", clicks=1, moneda="MXN"
            )
            fecha += dt.timedelta(days=1)
        monkeypatch.setattr(dash, "_ahora_utc", lambda: instante)
        monkeypatch.setattr(dash, "_hoy_utc", lambda: instante.date())
        cliente = tad._cliente(dsn, monkeypatch)
        resumen = cliente.get(
            "/api/dashboard/resumen", params={"periodo": 30, "canal": "amazon_mx"}
        ).json()
        campanas = cliente.get("/api/dashboard/campanas", params={"platform": "amazon_mx"}).json()
    gasto_resumen = Decimal(resumen["canales"]["amazon_mx"]["totals"]["cost"])
    gasto_campanas = sum(Decimal(i["metricas_30d"]["cost"]) for i in campanas["items"])
    assert gasto_campanas == gasto_resumen, (gasto_campanas, gasto_resumen)


# ---------------------------------------------------------------------------
# F4: la pantalla que invita a negar a mano usa datos de 3 dias, no de 10
# ---------------------------------------------------------------------------


def test_busquedas_sin_venta_respeta_la_madurez_de_cortes():
    """«Busquedas que gastan sin vender» invita a agregar negatives a mano
    (un corte). Regla 6: cortar exige >= 10 dias de maduracion
    (windows.DIAS_MADUREZ_CORTES = 10); la pantalla corta en hoy - 3. En prod
    los pedidos por busqueda crecen 54 -> 63 (MX) despues de la primera foto."""
    hoy = dt.date(2026, 11, 12)
    conn = _ConnFalsa([("FROM config_version", [({},)])])
    pantalla = pantalla_busquedas.lee_busquedas(conn, plataforma="amazon_mx", hoy=hoy)
    assert pantalla.hasta <= hoy - dt.timedelta(days=DIAS_MADUREZ_CORTES), pantalla.hasta


# ---------------------------------------------------------------------------
# F5: «Despues del encendido» mezcla dias inmaduros sin decirlo
# ---------------------------------------------------------------------------


def test_ruido_antes_y_despues_no_mete_dias_inmaduros_sin_marca():
    """El tablero de ruido compara ACoS antes/despues del encendido del motor.
    «Despues» llega hasta D-1 (8 dias inmaduros, venta x0.76) y la pantalla
    no lo marca: el ACoS «despues» sale inflado. Aqui todos los dias maduros
    tienen ACoS 20 %; solo los inmaduros lo empeoran."""
    hoy = dt.datetime.now(dt.UTC).date()
    encendido = hoy - dt.timedelta(days=20)
    filas = []
    fecha = encendido - dt.timedelta(days=30)
    while fecha <= hoy - dt.timedelta(days=1):
        inmaduro = fecha >= hoy - dt.timedelta(days=8)
        venta = Decimal("76") if inmaduro else Decimal("100")
        filas.append((fecha, Decimal("20"), 1, venta))
        fecha += dt.timedelta(days=1)
    conn = _ConnFalsa(
        [
            ("FROM config_version c", [(dt.datetime.combine(encendido, dt.time(9), dt.UTC),)]),
            ("FROM v_metric_latest m", filas),
        ]
    )
    res = pantalla_ruido._comparacion(conn, "amazon_mx", 30)
    marcado = any("inmadur" in clave for clave in res)
    assert marcado or res["despues"]["acos_pct"] == "20.00", res["despues"]
