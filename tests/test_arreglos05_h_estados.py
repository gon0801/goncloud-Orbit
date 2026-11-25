"""ARREGLOS 05 H: el tope del ciclo en la tabla unica de estados.

El cupo agotado es un hecho del ledger, hermano de `intentos_agotados`: se
modela como evento y estado terminal, y el llamador ramifica solo por
`transicion.accion`.
"""

from __future__ import annotations

from app.apply_estados import (
    TABLA_TRANSICIONES,
    AccionCambio,
    EstadoCambio,
    EventoCambio,
    Transicion,
    decidir,
    transicion_de_reserva_cupo,
)

E = EstadoCambio
V = EventoCambio
A = AccionCambio


def test_arreglos05_h_cupo_agotado_es_estado_terminal_y_evento_del_ledger():
    """Estados y eventos son vocabulario cerrado; el nuevo es terminal y la
    tabla cubre las 12 x 17 casillas."""
    assert E.CUPO_CICLO_AGOTADO.value == "cupo_ciclo_agotado"
    assert V.CUPO_CICLO_AGOTADO.value == "cupo_ciclo_agotado"
    assert E.CUPO_CICLO_AGOTADO.es_terminal
    assert len(EstadoCambio) == 12
    assert len(EventoCambio) == 17
    assert len(TABLA_TRANSICIONES) == len(EstadoCambio) * len(EventoCambio) == 204


def test_arreglos05_h_ninguna_casilla_del_cupo_agotado_envia():
    """Con el cupo agotado jamas sale un envio: pendiente queda terminado,
    incierto se lee y el estado nuevo es absorbente."""
    for estado in EstadoCambio:
        assert decidir(estado, V.CUPO_CICLO_AGOTADO).accion != A.ENVIAR, estado
    assert decidir(E.PENDIENTE, V.CUPO_CICLO_AGOTADO) == Transicion(
        A.NO_TOCAR, E.CUPO_CICLO_AGOTADO
    )
    assert decidir(E.INCIERTO, V.CUPO_CICLO_AGOTADO) == Transicion(
        A.LEER_RESULTADO, E.INCIERTO, origen_incompleto=True
    )
    for evento in EventoCambio:
        transicion = decidir(E.CUPO_CICLO_AGOTADO, evento)
        assert transicion.accion != A.ENVIAR
        if evento in (V.RECHAZO_IDENTIDAD, V.DUENO_DESPLAZADO):
            assert transicion.accion == A.ABORTAR_FASE
            assert transicion.propaga_aborto is True
        else:
            assert transicion.estado_siguiente == E.CUPO_CICLO_AGOTADO


def test_arreglos05_h_clasificador_de_reserva_consulta_la_tabla():
    """El llamador pregunta por la reserva y solo `enviar` deja seguir."""
    con_lugar = transicion_de_reserva_cupo(agotado=False)
    assert (con_lugar.accion, con_lugar.estado_siguiente) == (A.ENVIAR, E.INCIERTO)
    agotado = transicion_de_reserva_cupo(agotado=True)
    assert (agotado.accion, agotado.estado_siguiente) == (
        A.NO_TOCAR,
        E.CUPO_CICLO_AGOTADO,
    )
