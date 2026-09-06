"""Totales de presentacion: no mezclar monedas ni rellenar huecos."""

from app import ui_metricas


def test_resumen_suma_decimales_y_calcula_acos_del_total():
    datos = {
        "moneda": "MXN",
        "series": [
            {"cost": "0.10", "ad_revenue": "0.20", "clicks": 0},
            {"cost": "0.20", "ad_revenue": "0.80", "clicks": 3},
            {"cost": None, "ad_revenue": None, "clicks": None},
        ],
    }
    resultado = ui_metricas.kpis_serie(datos)
    assert resultado == {
        "cost": "0.30",
        "ad_revenue": "1.00",
        "clicks": 3,
        "acos": "30.00",
        "cobertura": {"cost": 2, "ad_revenue": 2, "clicks": 2, "acos": 2},
        "dias": 3,
    }


def test_resumen_vacio_y_sin_ventas_no_inventa_acos():
    assert ui_metricas.kpis_serie({"series": []})["cost"] is None
    assert ui_metricas.kpis_serie({"series": []})["clicks"] is None
    datos = {"series": [{"cost": "0", "ad_revenue": "0", "clicks": 0}]}
    resultado = ui_metricas.kpis_serie(datos)
    assert resultado["acos"] is None
    assert resultado["cost"] == "0.00"
    assert resultado["clicks"] == 0


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


def test_cobertura_por_campo_y_acos_no_mezcla_periodos():
    datos = {
        "series": [
            {"cost": "10", "ad_revenue": "100", "clicks": 1},
            {"cost": "20", "ad_revenue": None, "clicks": None},
        ]
    }
    resultado = ui_metricas.kpis_serie(datos)
    assert resultado["acos"] is None
    assert resultado["cobertura"] == {"cost": 2, "ad_revenue": 1, "clicks": 1, "acos": 1}


def test_acos_rechaza_coberturas_iguales_con_dias_distintos():
    datos = {
        "series": [
            {"cost": "10", "ad_revenue": None, "clicks": 1},
            {"cost": None, "ad_revenue": "100", "clicks": 1},
        ]
    }
    assert ui_metricas.kpis_serie(datos)["acos"] is None


def test_acos_redondea_como_la_serie_del_dashboard():
    datos = {"series": [{"cost": "11.125", "ad_revenue": "100", "clicks": 1}]}
    assert ui_metricas.kpis_serie(datos)["acos"] == "11.13"
