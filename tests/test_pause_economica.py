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
        ("120", "200", None),  # cociente == 3x exacto con exceso 80: estricto lo excluye
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
    # B1: la revalidacion economica exige el flag prendido.
    monkeypatch.setattr(apply_cola, "_flag_pause_economica", lambda *_: True)
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


def test_pause_antigua_con_venta_tardia_conserva_corte_si_exceso_sigue_alto(monkeypatch):
    from app import apply_cola

    class Conn:
        def execute(self, sql, params):
            class Cursor:
                def fetchone(self):
                    if sql == apply_cola._SQL_DECISION_INPUTS:
                        return ({"motivo": "pause_umbral"},)
                    return (71,)

            return Cursor()

    monkeypatch.setattr(apply_cola.apply, "_identidad", lambda *_: ("keyword", "2423"))
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    monkeypatch.setattr(apply_cola, "_gracia_activa", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_pause_propio_verificado", lambda *_: False)
    # B1: la revalidacion economica exige el flag prendido.
    monkeypatch.setattr(apply_cola, "_flag_pause_economica", lambda *_: True)
    monkeypatch.setattr(
        apply_cola, "_target_pause_vigente", lambda *_: (Decimal("20"), "goal_campana")
    )
    monkeypatch.setattr(apply_cola.windows, "ventanas_evidencia_ad_group", lambda *_: {})
    monkeypatch.setattr(apply_cola.windows, "ventana_cortes", lambda *_: _corte("100", "100"))
    monkeypatch.setattr(
        apply_cola.cortes, "umbral_corte", lambda *_: type("U", (), {"umbral": 300})()
    )
    fila = apply_cola.FilaCola(1, "pause", 2423, None, 1, {}, "released")
    aplicador = type(
        "Aplicador", (), {"_cliente": lambda self: object(), "cycle_id_ejecutor": 42}
    )()
    assert apply_cola._revalida_pause(Conn(), aplicador, "amazon_us", fila, HOY) is None


@pytest.mark.parametrize(
    ("hay_goal", "esperado"), [(True, "espera_target"), (False, "ya_no_califica")]
)
def test_revalida_pause_sin_target_espera_solo_con_goal(monkeypatch, hay_goal, esperado):
    """Obs4: economica sin target confiable ESPERA (released, reintenta) si
    hay goal que puede volver; sin goal se descarta terminal."""
    from app import apply_cola

    class Conn:
        def execute(self, sql, params):
            class Cursor:
                def fetchone(self):
                    if "SELECT inputs" in sql:
                        return ({"motivo": "pause_economica"},)
                    return (None,)

            return Cursor()

    monkeypatch.setattr(apply_cola.apply, "_identidad", lambda *_: ("keyword", "2423"))
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    monkeypatch.setattr(apply_cola, "_gracia_activa", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_pause_propio_verificado", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_target_pause_vigente", lambda *_: (None, None))
    monkeypatch.setattr(apply_cola, "_hay_goal", lambda *_: hay_goal)
    fila = apply_cola.FilaCola(1, "pause", 2423, None, 1, {}, "released")
    aplicador = type(
        "Aplicador", (), {"_cliente": lambda self: object(), "cycle_id_ejecutor": 42}
    )()
    assert apply_cola._revalida_pause(Conn(), aplicador, "amazon_us", fila, HOY) == esperado


def test_negativos_no_pausan_ni_abstencion_ruidosa():
    """Obs2/M11: cost o revenue negativos no califican ninguna regla, con
    motivo auditable (mata la guarda cost<0: sin ella el motivo cambia)."""
    assert _decide(_corte("-5", "100")).kind is None
    assert _decide(_corte("-5", "100")).motivo == "pause_economica_dato_faltante"
    assert _decide(_corte("100", "-1")).kind is None
    assert _decide(_corte("100", "-1")).motivo == "pause_economica_dato_faltante"


@pytest.mark.parametrize(
    ("enabled", "mode"),
    [(False, "live"), (True, "off")],
    ids=["disabled", "off"],
)
def test_target_revalidacion_sin_goal_habilitado_da_none(enabled, mode):
    """Obs2/M8: goal deshabilitado o en off -> sin target (None, None)."""
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
                return Cursor((3926, None))
            if sql == apply_cola._SQL_NOTAS_CICLO_APLICADOR:
                return Cursor(('{"target":{"target_aplicado":"30"}}',))
            if sql == apply_cola._SQL_CONFIG_TARGET:
                return Cursor(({},))
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
                            enabled,
                            mode,
                        ),
                    )
                )
            raise AssertionError(sql)

    assert apply_cola._target_pause_vigente(Conn(), "amazon_us", 2423, 42) == (None, None)


def test_target_revalidacion_peldano_margen_con_goal_sin_target():
    """Obs2/M12: goal de campana vivo pero sin target -> margen del ciclo."""
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
                return Cursor((3926, None))
            if sql == apply_cola._SQL_NOTAS_CICLO_APLICADOR:
                return Cursor(('{"target":{"target_aplicado":"30"}}',))
            if sql == apply_cola._SQL_CONFIG_TARGET:
                return Cursor(({},))
            if sql == apply_cola.apply._SQL_GOALS_ENTIDAD:
                return Cursor(
                    many=(
                        (
                            "campaign",
                            3926,
                            "amazon_us",
                            None,
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
        Decimal("30"),
        "margen_plataforma",
    )


def test_registra_evidencia_conserva_nota_previa_no_json():
    """Obs3: notes con texto libre (rastro) no se pisa: se anida."""
    import json as json_std

    from app import apply_cola

    guardado = {}

    class Cursor:
        def __init__(self, one=None):
            self.one = one

        def fetchone(self):
            return self.one

    class Conn:
        def execute(self, sql, params=None):
            if sql == apply_cola._SQL_LEE_NOTAS_CICLO:
                return Cursor(('{"target":{"target_aplicado":null}}\nrastro: ciclo muerto',))
            guardado["sql"] = sql
            guardado["params"] = params
            return Cursor()

    apply_cola._registra_evidencia(Conn(), 42, [], {"decision_id": 1})
    assert guardado["sql"] == apply_cola._SQL_ESCRIBE_NOTAS_CICLO
    notas = json_std.loads(guardado["params"][0])
    assert notas["revalidaciones_economicas"] == [{"decision_id": 1}]
    assert notas["nota_previa_no_json"].endswith("rastro: ciclo muerto")


def test_flag_apagado_revalidacion_legada_no_adopta_regla_economica(monkeypatch):
    """B1'r2: con el flag APAGADO, una fila legada pause_umbral cuya hoja
    vendio (orders>0) pero aun cruza el limite economico se descarta
    vendio_en_ventana (camino pre-C.3), JAMAS se aplica."""
    from app import apply_cola

    class Conn:
        def execute(self, sql, params):
            class Cursor:
                def fetchone(self):
                    if sql == apply_cola._SQL_DECISION_INPUTS:
                        return ({"motivo": "pause_umbral"},)
                    return (71,)

            return Cursor()

    monkeypatch.setattr(apply_cola.apply, "_identidad", lambda *_: ("keyword", "2423"))
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    monkeypatch.setattr(apply_cola, "_gracia_activa", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_pause_propio_verificado", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_flag_pause_economica", lambda *_: False)
    monkeypatch.setattr(
        apply_cola, "_target_pause_vigente", lambda *_: (Decimal("20"), "goal_campana")
    )
    monkeypatch.setattr(apply_cola.windows, "ventanas_evidencia_ad_group", lambda *_: {})
    monkeypatch.setattr(apply_cola.windows, "ventana_cortes", lambda *_: _corte("100", "100"))
    monkeypatch.setattr(
        apply_cola.cortes, "umbral_corte", lambda *_: type("U", (), {"umbral": 300})()
    )
    fila = apply_cola.FilaCola(1, "pause", 2423, None, 1, {}, "released")
    aplicador = type(
        "Aplicador", (), {"_cliente": lambda self: object(), "cycle_id_ejecutor": 42}
    )()
    assert (
        apply_cola._revalida_pause(Conn(), aplicador, "amazon_us", fila, HOY)
        == apply_cola.MOTIVO_VENDIO_EN_VENTANA
    )


def test_fila_economica_que_solo_califica_umbral_sigue_aplicando(monkeypatch):
    """Obs1r2: fila economica cuya hoja fresca solo califica por la regla
    antigua (0 pedidos, clicks>=umbral, exceso<minimo) APLICA (None), no
    se descarta (mata `califica and motivo==economica`)."""
    from app import apply_cola

    class Conn:
        def execute(self, sql, params):
            class Cursor:
                def fetchone(self):
                    if sql == apply_cola._SQL_DECISION_INPUTS:
                        return ({"motivo": "pause_economica"},)
                    if sql == apply_cola._SQL_LEE_NOTAS_CICLO:
                        return ("{}",)

                    return (71,)

            return Cursor()

    monkeypatch.setattr(apply_cola.apply, "_identidad", lambda *_: ("keyword", "2423"))
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    monkeypatch.setattr(apply_cola, "_gracia_activa", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_pause_propio_verificado", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_flag_pause_economica", lambda *_: True)
    monkeypatch.setattr(
        apply_cola, "_target_pause_vigente", lambda *_: (Decimal("20"), "goal_campana")
    )
    monkeypatch.setattr(apply_cola.windows, "ventanas_evidencia_ad_group", lambda *_: {})
    monkeypatch.setattr(
        apply_cola.windows, "ventana_cortes", lambda *_: _corte("50", "0", clicks=300, orders=0)
    )
    evidencias_b: list = []
    monkeypatch.setattr(
        apply_cola.cortes, "umbral_corte", lambda *_: type("U", (), {"umbral": 300})()
    )
    fila = apply_cola.FilaCola(1, "pause", 2423, None, 1, {}, "released")
    aplicador = type(
        "Aplicador", (), {"_cliente": lambda self: object(), "cycle_id_ejecutor": 42}
    )()
    assert (
        apply_cola._revalida_pause(Conn(), aplicador, "amazon_us", fila, HOY, evidencias_b) is None
    )
    assert evidencias_b[0]["version"] == "economic_pause_v1"


def test_evidencia_registra_version_efectiva_no_nominal(monkeypatch):
    """Obs3r2: fila economica revalidada con flag APAGADO registra
    version None (la politica aplicada fue la anterior, no v1)."""
    from app import apply_cola

    class Conn:
        def execute(self, sql, params):
            class Cursor:
                def fetchone(self):
                    if sql == apply_cola._SQL_DECISION_INPUTS:
                        return ({"motivo": "pause_economica"},)
                    if sql == apply_cola._SQL_LEE_NOTAS_CICLO:
                        return ("{}",)

                    return (71,)

            return Cursor()

    monkeypatch.setattr(apply_cola.apply, "_identidad", lambda *_: ("keyword", "2423"))
    monkeypatch.setattr(apply_cola.apply, "_estado_de_readback", lambda *_: "ENABLED")
    monkeypatch.setattr(apply_cola, "_gracia_activa", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_pause_propio_verificado", lambda *_: False)
    monkeypatch.setattr(apply_cola, "_flag_pause_economica", lambda *_: False)
    monkeypatch.setattr(
        apply_cola, "_target_pause_vigente", lambda *_: (Decimal("20"), "goal_campana")
    )
    monkeypatch.setattr(apply_cola.windows, "ventanas_evidencia_ad_group", lambda *_: {})
    monkeypatch.setattr(apply_cola.windows, "ventana_cortes", lambda *_: _corte("100", "500"))
    monkeypatch.setattr(
        apply_cola.cortes, "umbral_corte", lambda *_: type("U", (), {"umbral": 300})()
    )
    fila = apply_cola.FilaCola(1, "pause", 2423, None, 1, {}, "released")
    aplicador = type(
        "Aplicador", (), {"_cliente": lambda self: object(), "cycle_id_ejecutor": 42}
    )()
    evidencias: list = []
    assert (
        apply_cola._revalida_pause(Conn(), aplicador, "amazon_us", fila, HOY, evidencias)
        == "ya_no_califica"
    )
    assert evidencias[0]["version"] is None


def test_economica_abstiene_con_orders_o_clicks_desconocidos():
    """Obs4r2: orders/clicks None no pausan aunque el exceso cruce (misma
    abstencion que la regla umbral; el motivo lo dice)."""
    assert _decide(_corte("127.94", "0", orders=None)).kind is None
    assert _decide(_corte("127.94", "0", orders=None)).motivo == "pause_orders_desconocido"
    assert _decide(_corte("127.94", "0", clicks=None)).kind is None
    assert _decide(_corte("127.94", "0", clicks=None)).motivo == "pause_clicks_o_cost_desconocidos"
