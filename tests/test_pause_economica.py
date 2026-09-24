"""Proteccion economica madura en hojas y replay de su version."""

import datetime as dt
from decimal import Decimal

import pytest

from app.optimizer import bid, windows

HOY = dt.datetime(2026, 9, 24, 10, 0, tzinfo=dt.UTC)
FIN = dt.date(2026, 9, 14)


def _corte(cost, revenue, *, moneda="USD", fechas=7, clicks=231, orders=1):
    return windows.AgregadoMetricas(
        window_start=FIN - dt.timedelta(days=29),
        window_end=FIN,
        fechas=tuple(FIN - dt.timedelta(days=n) for n in range(fechas)),
        metric_currency=moneda,
        cost=None if cost is None else Decimal(str(cost)),
        ad_revenue=None if revenue is None else Decimal(str(revenue)),
        revenue_same_sku=Decimal("0"),
        impressions=1000,
        clicks=clicks,
        orders=orders,
        observed_at_max=HOY,
    )


def _decide(corte, *, platform="amazon_us", target="20", politica="economic_pause_v1"):
    return bid.decide_bid(
        platform=platform,
        bids=None,
        cortes=corte,
        target_acos_pct=Decimal(target),
        bid_actual=Decimal("0.40"),
        bid_moneda="USD" if platform == "amazon_us" else "MXN",
        floor=Decimal("0.40"),
        ceiling=Decimal("2.50"),
        umbral_pause=300,
        policy_version=politica,
    )


@pytest.mark.parametrize(
    ("cost", "revenue", "expected"),
    [
        ("100", "100", "pause_economica"),  # 100 > 60, exceso 80
        ("80", "0", "pause_economica"),  # cero MEDIDO, 80 inclusivo
        ("79.99", "0", None),
        ("80", "500", None),  # exceso negativo y ACoS 16%
        ("30", "5", None),  # ACoS >60%, exceso insuficiente
        ("100", "100.01", None),  # exceso 79.998
        ("60", "100", None),  # cociente estrictamente >3x
    ],
)
def test_regla_economica_us(cost, revenue, expected):
    assert _decide(_corte(cost, revenue)).motivo == (expected or "bids_sin_observaciones")


def test_regla_mxn_exige_exceso_1000_en_moneda_original():
    assert (
        _decide(_corte("1200", "1000", moneda="MXN"), platform="amazon_mx").motivo
        == "pause_economica"
    )
    assert _decide(_corte("1199.99", "1000", moneda="MXN"), platform="amazon_mx").kind is None


@pytest.mark.parametrize(
    "corte",
    [
        _corte("100", None),
        _corte(None, "0"),
        _corte("100", "0", moneda="MXN"),
        _corte("100", "0", fechas=6),
    ],
)
def test_faltante_moneda_erronea_o_inmadurez_no_pausan(corte):
    assert _decide(corte).kind is None


def test_revenue_ausente_deja_motivo_auditable():
    assert _decide(_corte("100", None)).motivo == "pause_economica_dato_faltante"


def test_venta_con_clicks_100_a_231_y_bid_en_floor_sigue_en_pause():
    assert _decide(_corte("100", "100", clicks=100)).kind == "pause"
    assert _decide(_corte("100", "100", clicks=231)).kind == "pause"


def test_politica_anterior_no_reinterpreta_decision_historica():
    assert _decide(_corte("100", "100"), politica=None).kind is None


@pytest.mark.parametrize(
    ("cost", "revenue", "target", "esperado"),
    [
        ("100", "100", "20", None),
        ("100", "150", "20", "ya_no_califica"),
        ("100", "100", "40", "ya_no_califica"),
        ("80", "0", "20", None),
    ],
)
def test_revalida_pause_economica_con_venta_tardia(monkeypatch, cost, revenue, target, esperado):
    from app import apply_cola

    class Conn:
        def execute(self, sql, params):
            class Cursor:
                def fetchone(self):
                    if "SELECT inputs" in sql:
                        return (
                            {
                                "motivo": "pause_economica",
                                "economic_policy": {"version": "economic_pause_v1"},
                            },
                        )
                    return (71,)

            return Cursor()

    monkeypatch.setattr(apply_cola.apply, "_identidad", lambda *_: ("keyword", "2423"))
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    monkeypatch.setattr(apply_cola, "_gracia_activa", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_pause_propio_verificado", lambda *_: False)
    monkeypatch.setattr(
        apply_cola, "_target_pause_vigente", lambda *_: (Decimal(target), "goal_campana")
    )
    monkeypatch.setattr(apply_cola.windows, "ventanas_evidencia_ad_group", lambda *_: {})
    monkeypatch.setattr(apply_cola.windows, "ventana_cortes", lambda *_: _corte(cost, revenue))
    monkeypatch.setattr(
        apply_cola.cortes, "umbral_corte", lambda *_: type("U", (), {"umbral": 300})()
    )
    fila = apply_cola.FilaCola(1, "pause", 2423, None, 1, {}, "released")
    aplicador = type(
        "Aplicador", (), {"_cliente": lambda self: object(), "cycle_id_ejecutor": 42}
    )()
    assert apply_cola._revalida_pause(Conn(), aplicador, "amazon_us", fila, HOY) == esperado


def test_target_revalidacion_usa_goal_vigente_y_margen_del_ciclo():
    from app import apply_cola

    class Cursor:
        def __init__(self, one=None, many=()):
            self.one = one
            self.many = many

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.many

    class Conn:
        def execute(self, sql, params=None):
            if sql == apply_cola._SQL_PAUSE_TARGET_STATE:
                return Cursor((3926, Decimal("15")))
            if sql == apply_cola._SQL_NOTAS_CICLO_APLICADOR:
                return Cursor(("{" + '"target":{"target_aplicado":"30"}}',))
            if sql == apply_cola._SQL_CONFIG_TARGET:
                return Cursor(({"ads_target_acos_pct_amazon_us": "10"},))
            if sql == apply_cola.apply._SQL_GOALS_ENTIDAD:
                return Cursor(
                    many=(
                        (
                            "campaign",
                            3926,
                            "amazon_us",
                            Decimal("20"),
                            Decimal("0.1"),
                            Decimal("2.5"),
                            "USD",
                            None,
                            None,
                            None,
                            True,
                            "live",
                        ),
                    )
                )
            raise AssertionError(sql)

    assert apply_cola._target_pause_vigente(Conn(), "amazon_us", 2423, 42) == (
        Decimal("20"),
        "goal_campana",
    )
