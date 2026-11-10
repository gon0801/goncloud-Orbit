"""Sonda H2: una corrida SP-API que falla UNA vez por 5xx/red/contrato no
avisa (salud espera la segunda fallida seguida) y el vigilante la cuenta
como "presente" (su docstring dice que ya aviso por su camino).

Pura: conexion falsa; se intercepta notifica_spapi_fallo.
"""

import contextlib
from datetime import UTC, datetime

import app.spapi.salud as salud
from app.spapi.vigilante import faltantes

enviados = []
salud.notifica_spapi_fallo = lambda f, p, m: enviados.append(("fallo", f, p, m))
salud.notifica_spapi_recuperada = lambda f, p: enviados.append(("recuperada", f, p))


class ConnFalsa:
    def __init__(self, filas):
        self.filas = filas

    @contextlib.contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params):
        filas = self.filas

        class R:
            def fetchall(self_inner):
                return filas

        return R()


inicio = datetime(2026, 11, 9, 5, 40, tzinfo=UTC)
fin = datetime(2026, 11, 9, 5, 52, tzinfo=UTC)
# (id, started_at, finished_at, ok, rows_written, rows_skipped, skip_reason, llamadas)
# historial: la de hoy fallo por 5xx; la de ayer fue ok.
historial = [
    (900, inicio, fin, False, 0, 0, "http_5xx: 503 Service Unavailable", 40),
    (880, inicio, fin, True, 342, 0, None, 684),
]
salud.evaluar_alertas(ConnFalsa(historial), "spapi_pricing", "amazon_mx", 900)
print("avisos tras primera fallida 5xx (repeticion saltada por pasar de 06:15):", enviados)

filas_ventana = [
    (f, p, inicio, fin, (f, p) != ("spapi_pricing", "amazon_mx"))
    for f in salud.FUENTES_SPAPI
    for p in ("amazon_mx", "amazon_us")
]
print(
    "vigilante 07:30, faltantes:",
    faltantes(
        filas_ventana,
        desde=datetime(2026, 11, 9, 4, 30, tzinfo=UTC),
        hasta=datetime(2026, 11, 9, 7, 30, tzinfo=UTC),
    ),
)
