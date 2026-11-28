"""Totales de presentacion: no mezclar monedas ni rellenar huecos."""

from app import ui_metricas


def test_inertes_separa_monedas_y_no_confunde_espera_desconocida():
    items = [
        {
            "moneda": "MXN",
            "gasto_90d": "0.10",
            "en_espera": False,
            "clasificacion": "zombie",
            "archivable_desde": "2026-01-01",
        },
        {
            "moneda": "USD",
            "gasto_90d": "0.20",
            "en_espera": True,
            "clasificacion": "zombie",
            "archivable_desde": "2027-01-01",
        },
        {
            "moneda": "MXN",
            "gasto_90d": None,
            "en_espera": None,
            "clasificacion": "zombie",
            "archivable_desde": None,
        },
    ]
    resultado = ui_metricas.kpis_inertes(items)
    assert resultado["gasto"] == {"MXN": "0.10", "USD": "0.20"}
    assert resultado["hojas"] == 3
    assert resultado["en_espera"] == 1
    assert resultado["sin_antiguedad"] == 1


def test_pendientes_leen_las_fechas_en_corto_como_el_resto_de_la_pantalla():
    contrato = {
        "canales": {
            "amazon_mx": {
                "moneda": "MXN",
                "dias": 8,
                "totals": {
                    "cost": "10.0000",
                    "ad_revenue": "0.0000",
                    "clicks": 5,
                    "orders": 0,
                    "acos": None,
                    "roas": None,
                    "sin_ventas": True,
                },
                "comparison": {},
                "series": [
                    {
                        "fecha": "2026-11-19",
                        "cost": "10.0000",
                        "ad_revenue": "0.0000",
                        "clicks": 5,
                        "orders": 0,
                        "inmaduro": True,
                        "ma7": {
                            "cost": "10.0000",
                            "ad_revenue": "0.0000",
                            "clicks": 5,
                            "orders": 0,
                        },
                    }
                ],
                "coverage": {
                    "dias": 8,
                    "dias_con_dato": 1,
                    "completo": False,
                    "por_metrica": {"orders": True},
                },
                "maduracion": {
                    "aplica": True,
                    "ventana_inmaduros": {"desde": "2026-11-19", "hasta": "2026-11-26"},
                    "motivo": None,
                },
            }
        },
        "metas_acos": {},
    }
    pendientes = ui_metricas.vista_resumen(contrato)["que_revisar"]
    assert len(pendientes) == 1
    assert pendientes[0]["caveat"] == (
        "Ojo: 1 de esos 1 días caen en la ventana inmadura (19 nov a 26 nov); "
        "Amazon puede atribuir ventas hasta 7 días después del clic."
    )
    assert "2026-11-19" not in pendientes[0]["caveat"]
