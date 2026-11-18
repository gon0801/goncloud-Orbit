# ARREGLOS 03 D-L, prueba real (estimacion.md F6 caida de nivel y F9 reclamos inventados).
# Uso: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-03/D-L/prueba.py
# Solo funciones puras y un cliente MeLi falso: no lee la base ni sale a la red.
# Con el codigo anterior sale 1: ImportError: cannot import name 'mejor_nivel_previo'.
# F8 (preguntas que ya no estan en MeLi) la comprueba el otro comando de la prueba-real:
# bash docs/evidencia/arreglos-01/desplegado.sh "max(observed_at) FROM meli_question"
import datetime as dt

from app.reputacion import fetch_seller
from app.reputacion_alertas import evalua_salud_cuenta, mejor_nivel_previo
from app.reputacion_plan import plan_seller

hoy = dt.date(2026, 11, 12)
serie = [(hoy, "3_yellow", 24), (hoy - dt.timedelta(days=1), "3_yellow", 24), (hoy - dt.timedelta(days=2), "5_green", 24)]
previo = mejor_nivel_previo(serie, hoy)
assert previo == "5_green", f"nivel previo {previo}"
assert evalua_salud_cuenta("3_yellow", previo) is not None, "verde -> amarillo sin alerta"
plan = plan_seller({"level_id": "5_green"}, None, dt.datetime(2026, 11, 12, 9, 30, tzinfo=dt.UTC))
assert plan.disputas_total is None and plan.disputas_abiertas is None, "reclamos inventados"


class ClienteFalso:
    """Imita ClienteMeli.get: devuelve (dict, fetched_at) por ruta, sin red."""

    def __init__(self, respuestas):
        self._r = respuestas

    def get(self, path, params=None):
        return self._r[path], dt.datetime(2026, 11, 12, 9, 30, tzinfo=dt.UTC)


sin_lista = ClienteFalso({
    "/users/me": {"id": 1},
    "/users/1": {"seller_reputation": {"level_id": "5_green", "transactions": {}}},
    "/post-purchase/v1/claims/search": {"paging": {"total": 24}},
})
leido, _skips = fetch_seller(sin_lista)
assert leido.disputas_total is None, f"reclamos sin lista dan {leido.disputas_total}"
print("D-L desplegado")
