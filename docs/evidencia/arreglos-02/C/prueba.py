"""Prueba real del bloque C (ARREGLOS 02) para en_prod.sh: funciones puras,
sin base ni red. Corre sobre el codigo DESPLEGADO.

- Un CambioBid con bid_antes == bid_despues (ajuste de campana) da
  direccion == 0, no 1: el ajuste ya no cuenta como subida de bid.
- Con el como_json() del caso de Estados Unidos de C.1 embebido (generado en
  local con fabrica_caso), decide da Mover con factor -0.25 y bid_nuevo 7.50
  (motivo gasto_sin_venta_doble): el ajuste en la trayectoria ya no hace
  esperar el recorte como precio por medir.
"""

import datetime as dt
import json
from decimal import Decimal

from app.optimizer.caso import CambioBid, CasoHoja
from app.optimizer.politica import Mover, decide

CASO = json.loads(
    """
{
    "plataforma": "amazon_us",
    "hoja_id": 1001,
    "ad_group_id": 2001,
    "bid": {
        "valor": "10.00",
        "moneda": "USD",
        "piso": "0.02",
        "techo": "20.00"
    },
    "economia": {
        "plataforma": {
            "moneda": "USD",
            "equilibrio_acos_pct": "31.49",
            "gasto_para_concluir": "36",
            "confianza_recorte": "0.80",
            "confianza_subida": "0.70"
        },
        "target_acos_pct": "28.34"
    },
    "propia": {
        "reciente": {
            "clics": 100,
            "pedidos": 0,
            "venta": "0",
            "gasto": "80",
            "impresiones": 3000
        },
        "antiguo": {
            "clics": 0,
            "pedidos": 0,
            "venta": "0",
            "gasto": "0",
            "impresiones": 0
        },
        "esquema": "dos_tramos_v1"
    },
    "pedidos_inmaduros": 0,
    "grupo": null,
    "cuenta": null,
    "precio": {
        "gasto": "0",
        "clics": 0
    },
    "trayectoria": {
        "cambios": [
            {
                "fecha": "2026-09-29",
                "bid_antes": "10.00",
                "bid_despues": "10.00",
                "origen": "ajuste_de_campana"
            }
        ],
        "efecto": {
            "dias_post": 10,
            "impresiones_pre7": 5000,
            "clics_pre7": 50,
            "impresiones_post": 100,
            "dias_post_trafico": 7,
            "clics_post": 5,
            "gasto_post": "100",
            "clics_pre": 40,
            "gasto_pre": "120",
            "vendia": false
        },
        "episodio": null
    },
    "pausa": {
        "cortes": null,
        "umbral_clics": 100,
        "gasto_minimo": "350",
        "expected_clicks": null,
        "politica_economica": null,
        "proteccion": null
    },
    "ventana_desde": "2026-07-11",
    "ventana_hasta": "2026-09-29",
    "observado_al": null
}
"""
)

ajuste = CambioBid(
    fecha=dt.date(2026, 9, 29),
    bid_antes=Decimal("10.00"),
    bid_despues=Decimal("10.00"),
    origen="ajuste_de_campana",
)
assert ajuste.direccion == 0, ajuste.direccion

veredicto = decide(CasoHoja.desde_json(CASO))
assert isinstance(veredicto, Mover), veredicto
assert veredicto.factor == Decimal("-0.25"), veredicto.factor
assert veredicto.bid_nuevo == Decimal("7.50"), veredicto.bid_nuevo
assert veredicto.motivo == "gasto_sin_venta_doble", veredicto.motivo

print("arreglos02 C ok")
