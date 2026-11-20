"""Prueba pura del bloque W-D para en_prod.sh (sin base ni red).

(a) F10: con venta conocida y ACoS vacio por un hueco, la tarjeta dice
«Faltan datos», no «Sin ventas».
(b) F3: el periodo anterior se lee a la misma edad; con la foto final la
tarjeta concluiria una caida que no existe.
"""

import datetime as dt
from decimal import Decimal

from app import dashboard_resumen as dr
from app.ui_metricas import _tarjeta_canal, vista_resumen


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


# (a) F10: «Sin ventas atribuidas» cuando SI hubo ventas (ACoS None por huecos)
bloque_hueco = {
    "moneda": "MXN",
    "dias": 7,
    "totals": {
        "cost": "800.0000",
        "ad_revenue": "5000.0000",
        "acos": None,
        "sin_ventas": False,
        "dinero_alineado": False,
    },
    "comparison": {"totals": {}, "variacion": {}},
    "coverage": {"dias_con_dato": 7, "completo": True},
    "series": [],
}
frase_hueco = _tarjeta_canal("amazon_mx", bloque_hueco, "20")["frase"]
assert "Faltan datos" in frase_hueco, frase_hueco
assert "Sin ventas" not in frase_hueco, frase_hueco

# (b) F3: mismo negocio en ambos periodos; el periodo actual trae la foto
# temprana (venta x0.76, gasto x1.03) y el anterior ya maduro. Sin el corte a
# la misma edad, el Resumen compara inmaduro contra maduro.
hoy = dt.date(2026, 11, 12)
ventana = dr.ventana_resumen(periodo=7, desde=None, hasta=None, hoy=hoy)
filas_finales = []
fecha = ventana.anterior_desde - dt.timedelta(days=6)
while fecha <= ventana.hasta:
    inmaduro = fecha >= hoy - dt.timedelta(days=dr.DIAS_INMADUROS)
    costo = Decimal("20.6000") if inmaduro else Decimal("20.0000")
    venta = Decimal("76.0000") if inmaduro else Decimal("100.0000")
    observado = dt.datetime.combine(fecha + dt.timedelta(days=2), dt.time(8), tzinfo=dt.UTC)
    filas_finales.append((fecha, costo, venta, 10, 2, observado))
    fecha += dt.timedelta(days=1)

filas_a_la_edad = []
fecha = ventana.anterior_desde
while fecha <= ventana.anterior_hasta:
    observado = dt.datetime.combine(fecha + dt.timedelta(days=2), dt.time(8), tzinfo=dt.UTC)
    filas_a_la_edad.append((fecha, Decimal("20.6000"), Decimal("76.0000"), 10, 2, observado))
    fecha += dt.timedelta(days=1)

conn = _ConnFalsa(
    [
        # La consulta con corte se responde antes: tambien trae GROUP BY.
        ("o.observed_at <=", filas_a_la_edad),
        ("GROUP BY v.metric_date", filas_finales),
        ("SELECT max(o.metric_date)", [(ventana.hasta,)]),
        ("max(r.finished_at)", [(None,)]),
    ]
)
instante = dt.datetime(2026, 11, 12, 18, 0, tzinfo=dt.UTC)
contrato = dr.resumen(conn, ventana=ventana, canales=("amazon_mx",), hoy=hoy, instante=instante)
bloque = contrato["canales"]["amazon_mx"]
comparison = bloque["comparison"]

assert Decimal(comparison["totals"]["ad_revenue"]) == Decimal("532"), (
    f"periodo anterior no es la lectura a la misma edad: {comparison['totals']['ad_revenue']}"
)
assert Decimal(comparison["totals"]["ad_revenue"]) != Decimal("676"), (
    "periodo anterior lee la foto final"
)
assert comparison["observado_hasta"] is not None, "falta observado_hasta"
assert comparison["observado_hasta"] == (instante - dt.timedelta(days=7)).isoformat(), comparison[
    "observado_hasta"
]
frase = vista_resumen(contrato)["tarjetas"]["amazon_mx"]["frase"]
assert "bajaron" not in frase, frase

print("W-D OK")
