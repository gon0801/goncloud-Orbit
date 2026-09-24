"""Limites y sellos del replay economico, sin tocar DB ni Ads."""

import datetime as dt
from decimal import Decimal

from tools.replay_ads_economico import agregado, riesgo, ventana_vintage


def test_riesgo_economico_fronteras_y_dato_ausente():
    objetivo = Decimal("20")
    assert riesgo(Decimal("80"), Decimal("0"), "USD", objetivo)[0] is True
    assert riesgo(Decimal("79.99"), Decimal("0"), "USD", objetivo)[0] is False
    assert riesgo(Decimal("100"), Decimal("100"), "USD", objetivo) == (
        True,
        Decimal("80"),
    )
    assert riesgo(Decimal("80"), Decimal("500"), "USD", objetivo)[0] is False
    assert riesgo(Decimal("1000"), Decimal("0"), "MXN", objetivo)[0] is True
    assert riesgo(Decimal("999.99"), Decimal("0"), "MXN", objetivo)[0] is False
    assert riesgo(Decimal("80"), None, "USD", objetivo) == (None, None)
    assert riesgo(Decimal("80"), Decimal("0"), "EUR", objetivo) == (None, None)


def test_vintage_descarta_atribucion_futura_y_ventana_inmadura():
    as_of = dt.datetime(2026, 9, 11, 8, 40, tzinfo=dt.UTC)
    filas = []
    for dia in range(7):
        fecha = dt.date(2026, 8, 26) + dt.timedelta(days=dia)
        filas.append((1, fecha, as_of - dt.timedelta(hours=1), "USD", Decimal("10"), Decimal("0")))
    filas.append(
        (1, dt.date(2026, 9, 4), as_of - dt.timedelta(hours=1), "USD", Decimal("0"), Decimal("0"))
    )
    filas.append(
        (
            1,
            dt.date(2026, 8, 26),
            as_of + dt.timedelta(days=1),
            "USD",
            Decimal("10"),
            Decimal("118"),
        )
    )
    vintage = ventana_vintage(filas, as_of)
    resumen = agregado(vintage[1], as_of)
    assert resumen is not None
    assert resumen["hasta"] == "2026-09-01"
    assert resumen["fechas"] == 7
    assert resumen["cost"] == Decimal("70")
    assert resumen["revenue"] == Decimal("0")
