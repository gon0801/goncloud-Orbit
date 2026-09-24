"""Limites y sellos del replay economico, sin tocar DB ni Ads."""

import datetime as dt
from decimal import Decimal

from tools.replay_ads_economico import agregado, medir, riesgo, ventana_vintage


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


def test_cache_de_hoja_no_da_target_a_hermanas_ni_campana():
    instante = dt.datetime(2026, 9, 11, 8, 40, tzinfo=dt.UTC)

    class Conexion:
        def execute(self, sql, _params=None):
            if "FROM optimizer_cycle " in sql:
                return [(1, "amazon_us", instante, instante, instante)]
            if "FROM ad_entity " in sql:
                return [
                    (1, "campaign", "amazon_us", None, "campana"),
                    (2, "ad_group", "amazon_us", 1, "grupo"),
                    (3, "keyword", "amazon_us", 2, "hoja 3"),
                    (4, "keyword", "amazon_us", 2, "hoja 4"),
                ]
            if "FROM ads_metric_observation" in sql:
                return [
                    (
                        entidad,
                        dt.date(2026, 8, 23) + dt.timedelta(days=i),
                        instante - dt.timedelta(days=1),
                        "USD",
                        Decimal("15"),
                        Decimal("15"),
                    )
                    for entidad in (1, 3, 4)
                    for i in range(10)
                ]
            if "FROM decision d" in sql:
                return [(1, 3, "20", "cache_estado")]
            if "FROM ads_optimizer_goal" in sql:
                return []
            raise AssertionError(sql)

    resultados = {
        fila["entity"]: fila
        for fila in medir(Conexion(), dt.date(2026, 9, 11), dt.date(2026, 9, 11))["rows"]
    }
    assert resultados[3]["target"] == Decimal("20")
    assert resultados[1]["target"] is None
    assert resultados[4]["target"] is None


def test_replay_usa_decided_at_si_started_at_es_posterior():
    decidido = dt.datetime(2026, 9, 11, 8, 40, 2, 203767, tzinfo=dt.UTC)
    iniciado = decidido + dt.timedelta(microseconds=18545)

    class Conexion:
        def execute(self, sql, _params=None):
            if "FROM optimizer_cycle " in sql:
                return [(1, "amazon_us", iniciado, decidido, decidido)]
            if "FROM ad_entity " in sql:
                return [
                    (1, "campaign", "amazon_us", None, "campana"),
                    (2, "ad_group", "amazon_us", 1, "grupo"),
                    (3, "keyword", "amazon_us", 2, "hoja"),
                ]
            if "FROM ads_metric_observation" in sql:
                filas = [
                    (
                        3,
                        dt.date(2026, 8, 23) + dt.timedelta(days=i),
                        decidido - dt.timedelta(days=1),
                        "USD",
                        Decimal("15"),
                        Decimal("15"),
                    )
                    for i in range(10)
                ]
                filas.append(
                    (
                        3,
                        dt.date(2026, 8, 23),
                        decidido + dt.timedelta(microseconds=10000),
                        "USD",
                        Decimal("15"),
                        Decimal("133"),
                    )
                )
                return filas
            if "FROM decision d" in sql:
                return [(1, 3, "20", "cache_estado")]
            if "FROM ads_optimizer_goal" in sql:
                return []
            raise AssertionError(sql)

    filas = medir(Conexion(), dt.date(2026, 9, 11), dt.date(2026, 9, 11))["rows"]
    hoja = next(fila for fila in filas if fila["entity"] == 3)
    assert hoja["as_of"] == decidido
    assert hoja["revenue"] == Decimal("105")
    assert hoja["candidate"] is True


def test_ciclo_sin_decisiones_no_inventa_reloj():
    iniciado = dt.datetime(2026, 9, 17, 8, 40, tzinfo=dt.UTC)

    class Conexion:
        def execute(self, sql, _params=None):
            if "FROM optimizer_cycle " in sql:
                return [(66, "amazon_us", iniciado, None, None)]
            if "FROM ad_entity " in sql:
                return []
            if "FROM ads_metric_observation" in sql:
                return []
            if "FROM decision d" in sql:
                return []
            if "FROM ads_optimizer_goal" in sql:
                return []
            raise AssertionError(sql)

    resultado = medir(Conexion(), dt.date(2026, 9, 17), dt.date(2026, 9, 17))
    assert resultado["rows"] == []
    assert resultado["cycles_without_decision_clock"] == [
        {"cycle": 66, "platform": "amazon_us", "reason": "sin_reloj_unico"}
    ]


def test_seleccion_de_ciclos_usa_limites_utc_sin_timezone_de_sesion():
    consultas = []

    class Conexion:
        def execute(self, sql, params=None):
            consultas.append((sql, params))
            return []

    medir(Conexion(), dt.date(2026, 9, 11), dt.date(2026, 9, 24))
    inicio = dt.datetime(2026, 9, 11, tzinfo=dt.UTC)
    fin = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    for sql, params in consultas:
        if "FROM optimizer_cycle " in sql or "FROM decision d" in sql:
            assert "started_at::date" not in sql
            assert params == (inicio, fin)
