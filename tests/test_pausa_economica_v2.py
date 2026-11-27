"""ADS PROTECCION P03: regla pura `economic_pause_v2` (rojo -> verde).

La regla vive en `app.optimizer.pausa_economica`; aqui se fija su contrato con
`_decide_pause` y con la era `v1` congelada. Ninguna prueba toca red ni base.
"""

import datetime as dt
from decimal import Decimal

import pytest

from app.optimizer import bid
from app.optimizer import pausa_economica as regla
from app.optimizer.caso import VERSION_PROTECCION_VENTA, ResultadoProteccion

HOY = dt.date(2026, 11, 27)
FIN = HOY - dt.timedelta(days=10)  # window_end de la ventana de cortes
DESDE = FIN + dt.timedelta(days=1)
HASTA = HOY - dt.timedelta(days=1)


def _agregado(cost, revenue, *, orders=0, clicks=300, moneda="USD", fechas=22):
    return bid.AgregadoMetricas(
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
        observed_at_max=dt.datetime(2026, 11, 17, tzinfo=dt.UTC),
    )


def _reciente(
    cost="0",
    revenue="0",
    *,
    orders=0,
    moneda="USD",
    completa=True,
    desde=DESDE,
    hasta=HASTA,
    evaluado_el=HOY,
):
    return bid.LecturaRecienteEconomica(
        desde=desde,
        hasta=hasta,
        evaluado_el=evaluado_el,
        moneda=moneda,
        completa=completa,
        cost=None if cost is None else Decimal(str(cost)),
        ad_revenue=None if revenue is None else Decimal(str(revenue)),
        orders=orders,
    )


_SIN_RECIENTE = object()


def _entrada(cortes, reciente=_SIN_RECIENTE, *, target="20", exceso="80", moneda="USD"):
    return regla.EntradaPausaEconomica(
        cortes=cortes,
        reciente=_reciente() if reciente is _SIN_RECIENTE else reciente,
        moneda=moneda,
        target_acos_pct=Decimal(target),
        exceso_minimo=Decimal(exceso),
    )


def _evalua(cortes, reciente=_SIN_RECIENTE, **kwargs):
    return regla.evalua_pausa_economica(_entrada(cortes, reciente, **kwargs))


def _pausar(cortes, reciente=_SIN_RECIENTE, **kwargs):
    return _evalua(cortes, reciente, **kwargs).veredicto == "pausar"


def test_v2_no_pausa_si_la_ventana_reciente_salva():
    """v2a: la venta reciente baja el combinado de 3x la meta."""
    cortes = _agregado("145", "100")  # cruza madura: 145 > 60, exceso 125
    reciente = _reciente("10", "200")  # combinado 155/300: no cruza
    assert not _pausar(cortes, reciente)
    assert _evalua(cortes, reciente).motivo == regla.MOTIVO_SALVADA


def test_v2_no_pausa_con_recuperacion_rentable_reciente():
    """v2b: el combinado sigue sobre el umbral, pero la ventana reciente es
    rentable (cost <= target * revenue / 100) con al menos un pedido."""
    cortes = _agregado("145", "100")
    reciente = _reciente("20", "100", orders=1)  # 165/200 cruza; 20 <= 20
    assert not _pausar(cortes, reciente)
    assert _evalua(cortes, reciente).motivo == regla.MOTIVO_RECUPERANDO


def test_v2_pausa_cuando_combinada_sigue_sobre_el_umbral():
    """Caso US 4924: sangra en maduro y la ventana reciente no la salva."""
    cortes = _agregado("358.64", "443.67")
    veredicto = _evalua(cortes, _reciente("30", "0"))
    assert veredicto.veredicto == "pausar"
    assert veredicto.motivo == regla.MOTIVO_PAUSE_ECONOMICA
    assert veredicto.exceso_combinado is not None


@pytest.mark.parametrize(
    "reciente",
    [
        _reciente(completa=False),
        _reciente(moneda="MXN"),
        _reciente("10", "0", desde=DESDE + dt.timedelta(days=1)),
        _reciente("10", "0", hasta=HASTA - dt.timedelta(days=1)),
        _reciente("-1", "0"),
        _reciente(None, "0"),
        None,
    ],
    ids=[
        "incompleta",
        "otra_moneda",
        "desde_desalineado",
        "hasta_no_es_d_menos_1",
        "negativo",
        "cost_none",
        "ausente",
    ],
)
def test_v2_invariantes_de_ventana_reciente_fail_closed(reciente):
    """Regla 3: cualquier ventana reciente no medible devuelve incompleto."""
    veredicto = _evalua(_agregado("145", "100"), reciente)
    assert veredicto.veredicto == "mantener"
    assert veredicto.motivo == regla.MOTIVO_INCOMPLETO


def test_v2_madura_bajo_umbral_no_espera_por_la_reciente():
    """El umbral maduro se mide ANTES de la ventana reciente: si ya no cruza,
    el motivo es `no_cruza_madura` aunque la reciente este incompleta (la fila
    en cola se descarta en vez de esperar)."""
    veredicto = _evalua(_agregado("50", "100"), _reciente(completa=False))
    assert veredicto.veredicto == "mantener"
    assert veredicto.motivo == regla.MOTIVO_NO_CRUZA_MADURA


def test_v2_es_subconjunto_de_v1():
    """Propiedad: donde v2 pausa, v1 (la era congelada) tambien pausa."""
    casos = [
        (_agregado("145", "100"), _reciente()),
        (_agregado("358.64", "443.67"), _reciente("30", "0")),
        (_agregado("1200", "900", moneda="MXN"), _reciente("0", "0", moneda="MXN")),
        (_agregado("90", "0"), _reciente("10", "0")),
        (_agregado("145", "1009"), _reciente()),
    ]
    for cortes, reciente in casos:
        v2 = _evalua(cortes, reciente)
        v1 = bid.decide_pause(
            platform="amazon_us" if cortes.metric_currency == "USD" else "amazon_mx",
            bids=None,
            cortes=cortes,
            target_acos_pct=Decimal("20"),
            umbral_pause=300,
            policy_version="economic_pause_v1",
        )
        if v2.veredicto == "pausar":
            assert v1.kind == "pause", (cortes.cost, cortes.ad_revenue)


def _proteccion(motivo="venta_reciente"):
    return ResultadoProteccion(
        version=VERSION_PROTECCION_VENTA,
        accion="esperar",
        motivo=motivo,
        ancla=HOY - dt.timedelta(days=2),
        vence_el=HOY + dt.timedelta(days=8),
        abre_episodio=False,
    )


def test_v2_conserva_precedencia_de_proteccion_cuando_no_pausa():
    """Un `mantener` de v2 cede el motivo a la proteccion por venta reciente
    (politica.py lo consume para frenar subidas)."""
    pausa, motivo = bid._decide_pause(
        _agregado("145", "100", orders=0, clicks=300),
        "USD",
        300,
        Decimal("40"),
        Decimal("20"),
        regla.POLITICA_PAUSE_ECONOMICA_V2,
        proteccion=_proteccion(),
        reciente_economica=_reciente("10", "200"),
    )
    assert pausa is None
    assert motivo == "venta_reciente"


def test_v2_pausar_gana_a_la_proteccion():
    """Brief P03: cuando v2 devuelve `pausar`, el resultado es el pause
    economico (misma semantica que bid.py:252-253)."""
    pausa, motivo = bid._decide_pause(
        _agregado("145", "100", orders=0, clicks=300),
        "USD",
        300,
        Decimal("40"),
        Decimal("20"),
        regla.POLITICA_PAUSE_ECONOMICA_V2,
        proteccion=_proteccion(),
        reciente_economica=_reciente("30", "0"),
    )
    assert motivo is None
    assert pausa is not None and pausa.kind == "pause"
    assert pausa.motivo == regla.MOTIVO_PAUSE_ECONOMICA


def test_v1_congelada_sigue_reproduciendo_decisiones_historicas():
    """La era v1 no cambia: su exceso y su motivo son los de siempre y no
    mira la ventana reciente."""
    pausa = bid.decide_pause(
        platform="amazon_us",
        bids=None,
        cortes=_agregado("145", "100", clicks=5),
        target_acos_pct=Decimal("20"),
        umbral_pause=300,
        policy_version="economic_pause_v1",
    )
    assert pausa.kind == "pause"
    assert pausa.motivo == "pause_economica"
    sin_era = bid.decide_pause(
        platform="amazon_us",
        bids=None,
        cortes=_agregado("145", "100", clicks=5),
        target_acos_pct=Decimal("20"),
        umbral_pause=300,
    )
    assert sin_era.kind is None


@pytest.mark.parametrize(
    ("cortes", "motivo"),
    [
        (_agregado("50", "100"), regla.MOTIVO_NO_CRUZA_MADURA),
        (_agregado("145", "100", fechas=6), regla.MOTIVO_CORTES_INCOMPLETOS),
        (_agregado("145", "100", moneda="MXN"), regla.MOTIVO_CORTES_INCOMPLETOS),
        (_agregado("145", None), regla.MOTIVO_CORTES_INCOMPLETOS),
        (_agregado("145", "100", clicks=None), regla.MOTIVO_CORTES_INCOMPLETOS),
        (_agregado("-1", "100"), regla.MOTIVO_CORTES_INCOMPLETOS),
    ],
)
def test_v2_paso_1_distingue_no_cruza_madura_de_cortes_incompletos(cortes, motivo):
    veredicto = _evalua(cortes)
    assert veredicto.veredicto == "mantener"
    assert veredicto.motivo == motivo


def test_v2_toda_hoja_juzgada_tiene_un_motivo_del_vocabulario_cerrado():
    motivos = set(regla.MOTIVOS_MANTENER) | {regla.MOTIVO_PAUSE_ECONOMICA}
    casos = [
        (_agregado("145", "100"), _reciente()),
        (_agregado("145", "100"), _reciente("10", "200")),
        (_agregado("145", "100"), _reciente("20", "100", orders=1)),
        (_agregado("145", "100"), _reciente(completa=False)),
        (_agregado("50", "100"), _reciente()),
        (_agregado("145", "100", fechas=6), _reciente()),
        (None, _reciente()),
    ]
    for cortes, reciente in casos:
        veredicto = _evalua(cortes, reciente)
        assert veredicto.veredicto in regla.VEREDICTOS_PAUSA_ECONOMICA
        assert veredicto.motivo in motivos
        assert veredicto.motivo != ""


def test_codec_de_entrada_es_inversa_exacta():
    entrada = _entrada(_agregado("145", "100"), _reciente("30", "0"))
    vuelta = regla.entrada_desde_json(regla.entrada_a_json(entrada))
    assert vuelta == entrada
    assert regla.evalua_pausa_economica(vuelta).veredicto == "pausar"
