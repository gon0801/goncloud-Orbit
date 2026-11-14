"""Prueba real del bloque B (ARREGLOS 02) para en_prod.sh: funciones puras,
sin base ni red. Corre sobre el codigo DESPLEGADO.

Embebe el inputs congelado de la decision 2865 (hoja 4934 de Estados Unidos,
falla F1 de la revision del 2026-11-12):
- tal cual (sin revision): reproduce da el bid guardado, con su semantica de 4 decimales;
- con "revision": 1 en el caso: decide da Mover con bid_nuevo 0.30, un centavo
  arriba del piso aprendido 0.29 donde la hoja ya habia perdido su trafico.
"""

import json
from decimal import Decimal

from app.optimizer.caso import CasoHoja
from app.optimizer.politica import Mover, decide
from app.optimizer.replay import reproduce

INPUTS = json.loads(
    """
{
    "caso": {
        "bid": {
            "piso": "0.1000",
            "techo": "2.5000",
            "valor": "0.3300",
            "moneda": "USD"
        },
        "grupo": {
            "antiguo": {
                "clics": 296,
                "gasto": "103.6200",
                "venta": "693.3800",
                "pedidos": 7,
                "impresiones": 21327
            },
            "esquema": "dos_tramos_v1",
            "reciente": {
                "clics": 701,
                "gasto": "292.3800",
                "venta": "646.1800",
                "pedidos": 6,
                "impresiones": 59996
            }
        },
        "pausa": {
            "cortes": {
                "cost": "131.6300",
                "clicks": 347,
                "fechas": [
                    "2026-09-29",
                    "2026-09-30",
                    "2026-10-01",
                    "2026-10-02",
                    "2026-10-03",
                    "2026-10-04",
                    "2026-10-05",
                    "2026-10-06",
                    "2026-10-07",
                    "2026-10-08",
                    "2026-10-09",
                    "2026-10-10",
                    "2026-10-11",
                    "2026-10-12",
                    "2026-10-13",
                    "2026-10-14",
                    "2026-10-15",
                    "2026-10-16",
                    "2026-10-17",
                    "2026-10-18",
                    "2026-10-19",
                    "2026-10-20",
                    "2026-10-21",
                    "2026-10-22",
                    "2026-10-23",
                    "2026-10-24",
                    "2026-10-25",
                    "2026-10-26",
                    "2026-10-27",
                    "2026-10-28"
                ],
                "orders": 3,
                "ad_revenue": "367.7800",
                "window_end": "2026-10-28",
                "impressions": 33606,
                "window_start": "2026-09-29",
                "metric_currency": "USD",
                "observed_at_max": "2026-11-07T07:14:52.256273+00:00",
                "revenue_same_sku": "249.7800"
            },
            "gasto_minimo": "40",
            "umbral_clics": 116,
            "expected_clicks": "76.69230769230769230769230769",
            "politica_economica": null
        },
        "cuenta": {
            "antiguo": {
                "clics": 3799,
                "gasto": "1692.8100",
                "venta": "5641.5500",
                "pedidos": 51,
                "impresiones": 296972
            },
            "esquema": "dos_tramos_v1",
            "reciente": {
                "clics": 4480,
                "gasto": "1869.9000",
                "venta": "3929.9900",
                "pedidos": 36,
                "impresiones": 423199
            }
        },
        "precio": {
            "clics": 322,
            "gasto": "121.8600"
        },
        "propia": {
            "antiguo": {
                "clics": 265,
                "gasto": "91.3800",
                "venta": "693.3800",
                "pedidos": 7,
                "impresiones": 18105
            },
            "esquema": "dos_tramos_v1",
            "reciente": {
                "clics": 699,
                "gasto": "291.7400",
                "venta": "646.1800",
                "pedidos": 6,
                "impresiones": 59679
            }
        },
        "hoja_id": 4934,
        "economia": {
            "plataforma": {
                "moneda": "USD",
                "confianza_subida": "0.70",
                "confianza_recorte": "0.80",
                "equilibrio_acos_pct": "24.4561535309317847485502039",
                "gasto_para_concluir": "36"
            },
            "target_acos_pct": "19.56492282474542779884016312"
        },
        "plataforma": "amazon_us",
        "ad_group_id": 3993,
        "trayectoria": {
            "efecto": {
                "vendia": true,
                "clics_pre": 2,
                "dias_post": 7,
                "gasto_pre": "0.6300",
                "clics_post": 83,
                "clics_pre7": 17,
                "gasto_post": "32.6900",
                "impresiones_post": 11582,
                "impresiones_pre7": 2182,
                "dias_post_trafico": 7
            },
            "cambios": [
                {
                    "fecha": "2026-09-10",
                    "origen": "motor",
                    "bid_antes": "0.3300",
                    "bid_despues": "0.379500"
                },
                {
                    "fecha": "2026-09-18",
                    "origen": "motor",
                    "bid_antes": "0.3800",
                    "bid_despues": "0.437000"
                },
                {
                    "fecha": "2026-10-02",
                    "origen": "motor",
                    "bid_antes": "0.4400",
                    "bid_despues": "0.330000"
                },
                {
                    "fecha": "2026-10-10",
                    "origen": "motor",
                    "bid_antes": "0.3300",
                    "bid_despues": "0.247500"
                },
                {
                    "fecha": "2026-10-17",
                    "origen": "regreso_por_desplome",
                    "bid_antes": "0.2500",
                    "bid_despues": "0.3300"
                },
                {
                    "fecha": "2026-10-25",
                    "origen": "motor",
                    "bid_antes": "0.3300",
                    "bid_despues": "0.290400"
                },
                {
                    "fecha": "2026-10-30",
                    "origen": "regreso_por_desplome",
                    "bid_antes": "0.2900",
                    "bid_despues": "0.3300"
                }
            ]
        },
        "observado_al": "2026-11-07T07:14:52.256273+00:00",
        "ventana_desde": "2026-08-09",
        "ventana_hasta": "2026-10-28",
        "pedidos_inmaduros": 1
    },
    "goal": {
        "scope": "platform",
        "harvest": {
            "moneda": "USD",
            "ad_group_id": "522582072501798",
            "campaign_id": "251723662158466",
            "default_bid": "0.6800"
        },
        "bid_floor": "0.1000",
        "bid_ceiling": "2.5000",
        "target_acos_pct": null
    },
    "modo": "live",
    "corte": {
        "elegible": true,
        "evidencia": {
            "clicks": 997,
            "fechas": 69,
            "orders": 13,
            "ventana_desde": "2026-08-09",
            "ventana_hasta": "2026-10-28",
            "observed_at_max": "2026-11-07T07:14:52.256273+00:00"
        },
        "cost_min_usado": "40",
        "expected_clicks": "76.69230769230769230769230769",
        "umbral_clicks_usado": 116
    },
    "motor": "bid",
    "nivel": "ad_group",
    "factor": "-0.12",
    "motivo": "grupo_sangra_vendedora",
    "platform": "amazon_us",
    "politica": "niveles_v3",
    "bid_actual": "0.3300",
    "bid_moneda": "USD",
    "veredicto_kind": "Mover",
    "target_snapshot": {
        "moneda": "MXN",
        "setting": "20",
        "fraccion": "0.8",
        "cobertura": "0.98863639196376174812",
        "procedencia": "margen_plataforma",
        "venta_total": "465338.1200",
        "paso_politica": "asimetrico_v1",
        "ventana_desde": "2026-07-25",
        "ventana_hasta": "2026-10-23",
        "ultimo_avisado": "20.59660018969924734472251872",
        "margen_neto_pct": "24.4561535309317847485502039",
        "target_aplicado": "19.56492282474542779884016312",
        "target_derivado": "19.56492282474542779884016312",
        "ledger_fresco_at": "2026-11-07T08:15:05.461069+00:00",
        "motivo_abstencion": null,
        "ad_revenue_ventana": null,
        "convergiendo_a_setting": false
    },
    "target_procedencia": "margen_plataforma",
    "target_acos_pct_usado": "19.56492282474542779884016312"
}
"""
)

kind, nuevo, moneda = reproduce(INPUTS)
assert (kind, nuevo, moneda) == ("bid", Decimal("0.2904"), "USD"), (kind, nuevo, moneda)

con_revision = json.loads(json.dumps(INPUTS))
con_revision["caso"]["revision"] = 1
veredicto = decide(CasoHoja.desde_json(con_revision["caso"]))
assert isinstance(veredicto, Mover), veredicto
assert veredicto.bid_nuevo == Decimal("0.30"), veredicto.bid_nuevo
assert veredicto.bid_nuevo > CasoHoja.desde_json(INPUTS["caso"]).trayectoria.piso_aprendido

print("arreglos02 B ok")
