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
