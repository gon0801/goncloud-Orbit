"""ORBIT 07 M1: subpolitica pura de proteccion por venta reciente.

El caso central reproduce la sonda del plan: 500 MXN de gasto, 120 clics, cero
pedidos maduros y un pedido reciente. Bajo la version nueva la pausa se
suprime y el juicio de la hoja sigue; bajo la version historica (sin marcador)
PAUSA. Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from test_optimizer_caso import cortes, economia_mx, fabrica_caso, nivel, tramo

from app.optimizer import proteccion as subproteccion
from app.optimizer.caso import (
    DIAS_PROTECCION_VENTA,
    VERSION_PROTECCION_VENTA,
    CasoHoja,
    EpisodioProteccion,
    InsumosPausa,
    InsumosProteccion,
    LecturaReciente,
    PrecioVentana,
)
from app.optimizer.politica import Mantener, Mover, Pausar, decide

HOY = dt.date(2026, 10, 9)
ANCLA = dt.date(2026, 10, 5)
VENCE = ANCLA + dt.timedelta(days=DIAS_PROTECCION_VENTA)


def _reciente(pedidos=1, ancla=ANCLA, cubierta=True):
    return LecturaReciente(
        pedidos=pedidos,
        ancla=ancla,
        cubierta=cubierta,
        observado_al=dt.datetime(2026, 10, 9, 8, 40, tzinfo=dt.UTC),
    )


def _proteccion(
    *,
    reciente=None,
    episodio=None,
    hoy=HOY,
    ventana_desde=dt.date(2026, 9, 20),
    ventana_hasta=dt.date(2026, 9, 29),
    frena_subidas=False,
):
    return InsumosProteccion(
        version=VERSION_PROTECCION_VENTA,
        evaluado_el=hoy,
        reciente=reciente if reciente is not None else _reciente(),
        ventana_desde=ventana_desde,
        ventana_hasta=ventana_hasta,
        episodio=episodio,
        frena_subidas=frena_subidas,
    )


def _caso(*, proteccion=None, clicks=120, cost="500", politica=None, inmaduros=1):
    pausa = InsumosPausa(
        cortes(clicks=clicks, cost=cost),
        100,
        Decimal("500"),
        None,
        politica,
        proteccion=proteccion,
    )
    return fabrica_caso(
        propia=nivel(tramo(clics=clicks, pedidos=0, venta="0", gasto=cost)),
        pedidos_inmaduros=inmaduros,
        pausa=pausa,
        observado_al=dt.datetime(2026, 10, 9, 8, 40, tzinfo=dt.UTC),
    )


def test_regresion_venta_reciente_historica_pausa():
    """Sin marcador de proteccion (decision historica) la pausa por umbral
    sigue igual: es la semantica que M1 corrige, conservada para el replay."""
    assert decide(_caso()) == Pausar("pause_umbral")


def test_venta_reciente_suprime_la_pausa_y_el_juicio_sigue():
    insumos = _proteccion()
    veredicto = decide(_caso(proteccion=insumos))
    assert veredicto == Mantener("venta_reciente")
    resultado = subproteccion.evalua(insumos)
    assert resultado.accion == "esperar"
    assert resultado.ancla == ANCLA
    assert resultado.vence_el == VENCE
    assert resultado.abre_episodio is True


def test_episodio_vigente_no_se_mueve_con_otra_lectura_ni_venta_posterior():
    """Otra corrida con un ancla mas reciente conserva el ancla original: el
    episodio no se renueva."""
    episodio = EpisodioProteccion(ancla=ANCLA, vence_el=VENCE)
    resultado = subproteccion.evalua(_proteccion(reciente=_reciente(ancla=HOY), episodio=episodio))
    assert resultado.accion == "esperar"
    assert resultado.ancla == ANCLA
    assert resultado.vence_el == VENCE
    assert resultado.abre_episodio is False


def test_al_vencer_reevalua_y_la_pausa_procede():
    """Vencido el episodio con el ancla ya en la ventana madura y todavia cero
    pedidos, el ancla quedo corregida: la proteccion se consume."""
    episodio = EpisodioProteccion(ancla=ANCLA, vence_el=VENCE)
    insumos = _proteccion(
        hoy=VENCE,
        episodio=episodio,
        ventana_desde=ANCLA,
        ventana_hasta=VENCE - dt.timedelta(days=1),
    )
    assert subproteccion.evalua(insumos).accion == "aplicar"
    assert decide(_caso(proteccion=insumos)) == Pausar("pause_umbral")


def test_al_vencer_sin_madurez_del_ancla_no_libera():
    """Un ancla que aun no entra a la ventana madura (rezago de ingesta) es
    desconocida: no se libera la pausa ni se abre otra gracia."""
    episodio = EpisodioProteccion(ancla=ANCLA, vence_el=VENCE)
    insumos = _proteccion(
        hoy=VENCE,
        episodio=episodio,
        ventana_hasta=ANCLA - dt.timedelta(days=1),
    )
    resultado = subproteccion.evalua(insumos)
    assert resultado.accion == "indeterminada"
    assert decide(_caso(proteccion=insumos)) == Mantener("venta_reciente")


def test_otro_pedido_inmaduro_no_renueva_el_episodio_consumido():
    episodio = EpisodioProteccion(ancla=ANCLA, vence_el=VENCE)
    insumos = _proteccion(
        hoy=VENCE,
        episodio=episodio,
        reciente=_reciente(ancla=VENCE),
        ventana_desde=ANCLA,
        ventana_hasta=VENCE - dt.timedelta(days=1),
    )
    assert subproteccion.evalua(insumos).accion == "aplicar"
    assert decide(_caso(proteccion=insumos)) == Pausar("pause_umbral")


def test_fuente_incompleta_sin_episodio_no_abre_gracia_y_la_pausa_sigue():
    """Sin episodio previo ni pedido conocido, una fuente incompleta no
    acredita venta reciente: no se inventa un cero ni se abre una gracia
    nueva. La pausa historica sigue y la lectura queda como indeterminada en
    el resultado."""
    insumos = _proteccion(reciente=_reciente(pedidos=None, ancla=None, cubierta=False))
    resultado = subproteccion.evalua(insumos)
    assert resultado.accion == "aplicar"
    assert resultado.motivo == subproteccion.MOTIVO_INDETERMINADA
    assert decide(_caso(proteccion=insumos)) == Pausar("pause_umbral")


def test_arreglos01_o_pedido_conocido_sin_dato_completo_espera_y_abre():
    """A-H1: el ancla de un pedido visto manda aunque `pedidos` sea
    desconocido por una fuente incompleta o por un dia venenoso."""
    for cubierta in (True, False):
        resultado = subproteccion.evalua(
            _proteccion(reciente=_reciente(pedidos=None, ancla=ANCLA, cubierta=cubierta))
        )
        assert resultado.accion == "esperar", cubierta
        assert resultado.motivo == subproteccion.MOTIVO_PROTEGIDA, cubierta
        assert resultado.ancla == ANCLA, cubierta
        assert resultado.vence_el == VENCE, cubierta
        assert resultado.abre_episodio is True, cubierta


def test_fuente_incompleta_con_episodio_activo_conserva_la_espera():
    episodio = EpisodioProteccion(ancla=ANCLA, vence_el=VENCE)
    insumos = _proteccion(reciente=_reciente(cubierta=False), episodio=episodio)
    resultado = subproteccion.evalua(insumos)
    assert resultado.accion == "esperar"
    assert resultado.ancla == ANCLA
    assert resultado.vence_el == VENCE


def test_pausa_economica_conserva_precedencia_sobre_la_proteccion():
    """Gasto 5000, venta 10000, cero pedidos y 120 clics: la regla economica
    califica con datos completos y la venta reciente no la oculta."""
    pausa = InsumosPausa(
        cortes(clicks=120, cost="5000", revenue="10000", orders=0),
        100,
        Decimal("500"),
        None,
        "economic_pause_v1",
        proteccion=_proteccion(),
    )
    caso = fabrica_caso(
        economia=economia_mx(target="4"),
        pausa=pausa,
        propia=nivel(tramo(clics=120, pedidos=0, venta="0", gasto="5000")),
        pedidos_inmaduros=1,
    )
    assert decide(caso) == Pausar("pause_economica")


def test_sin_pedido_reciente_la_pausa_sigue():
    veredicto = decide(_caso(proteccion=_proteccion(reciente=_reciente(pedidos=0, ancla=None))))
    assert veredicto == Pausar("pause_umbral")


def test_puja_autorizada_sigue_su_camino_durante_la_espera():
    """Episodio activo, lectura reciente ya sin pedidos y cero pedidos
    maduros: la pausa se suprime pero R9 (gasto sin venta) mueve la puja."""
    episodio = EpisodioProteccion(ancla=ANCLA, vence_el=VENCE)
    veredicto = decide(
        _caso(
            proteccion=_proteccion(reciente=_reciente(pedidos=0, ancla=None), episodio=episodio),
            inmaduros=0,
        )
    )
    assert isinstance(veredicto, Mover)
    assert veredicto.factor == Decimal("-0.12")
    assert veredicto.motivo == "gasto_sin_venta"


def test_freeze_roundtrip_con_proteccion():
    caso = _caso(proteccion=_proteccion(episodio=EpisodioProteccion(ANCLA, VENCE)))
    assert CasoHoja.desde_json(caso.como_json()) == caso
    assert caso.como_json()["pausa"]["proteccion"]["episodio"]["ancla"] == ANCLA.isoformat()
    assert decide(CasoHoja.desde_json(caso.como_json())) == decide(caso)


def test_arreglos01_p_no_sube_el_bid_durante_la_espera():
    """C-H4: la hoja cuya pausa espera por una venta reciente no recibe +15 %.
    El marcador congelado deja el juicio de su era intacto."""
    propia = nivel(tramo(clics=50, pedidos=5, venta="5000", gasto="200", impresiones=2000))
    cuenta = nivel(
        tramo(clics=10000, pedidos=100, venta="100000", gasto="9000", impresiones=400000)
    )

    def caso(proteccion):
        pausa = InsumosPausa(
            cortes(clicks=120, cost="500", orders=0),
            100,
            Decimal("500"),
            None,
            None,
            proteccion=proteccion,
        )
        return fabrica_caso(
            propia=propia,
            grupo=propia,
            cuenta=cuenta,
            precio=PrecioVentana(Decimal("100"), 50),
            pausa=pausa,
        )

    episodio = EpisodioProteccion(ANCLA, VENCE)
    espera = _proteccion(episodio=episodio, frena_subidas=True)
    veredicto = decide(caso(espera))
    assert veredicto == Mantener("subida_en_espera_de_venta")
    congelado = caso(espera).como_json()["pausa"]["proteccion"]
    assert congelado["frena_subidas"] is True
    assert decide(CasoHoja.desde_json(caso(espera).como_json())) == veredicto

    historica = _proteccion(episodio=episodio)
    assert decide(caso(historica)) == Mover(
        Decimal("0.15"), Decimal("11.5000"), "bajo_target", "hoja"
    )
