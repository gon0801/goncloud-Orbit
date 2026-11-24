"""ARREGLOS 05 A: pruebas exhaustivas de la tabla `estado x evento` y chequeos `/correct`.

Recorre TODAS las casillas de `app.apply_estados.TABLA_TRANSICIONES` (11 estados
x 16 eventos = 176 casillas; ARREGLOS 05 B agrego 2 estados y 3 eventos, y C un evento),
comprueba los invariantes de los bloques A y B sobre toda la tabla, valida los
clasificadores puros y ejecuta los chequeos estructurales
de `/correct` que impiden reintroducir la premisa rota (constantes, consultas
`HAVING` ad hoc o comprobaciones 401/403 duplicadas fuera del dueno unico).
"""

from __future__ import annotations

import ast
import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from app.ads.client import AdsApiError
from app.ads.write import AdsApiErrorMutacion
from app.apply_estados import (
    RESULTADO_IDENTIDAD_SUSTITUIDA,
    RESULTADO_NO_APLICADO,
    RESULTADO_OK,
    RESULTADO_PAUSADA_POR_DUENO,
    RESULTADOS_TERMINALES_REVERSA,
    STATUS_IDENTIDAD_RECHAZADA,
    TABLA_TRANSICIONES,
    TOPE_INTENTOS_CAMBIO,
    AccionCambio,
    EstadoCambio,
    EventoCambio,
    Transicion,
    decidir,
    envia_tras_lectura,
    estado_inicial_desde_hechos,
    evento_desde_error,
    evento_desde_lectura_bid,
    evento_desde_lectura_estado,
    evento_desde_lectura_objeto,
    evento_desde_status,
    transicion_desde_error,
    transicion_desde_status,
)

RAIZ = Path(__file__).resolve().parents[1]
E = EstadoCambio
V = EventoCambio
A = AccionCambio

# Expectativa literal de cada casilla `(estado, evento) -> (accion, estado_siguiente)`
# para la prueba parametrizada de toda la tabla: sin derivar de la implementacion.
_ESPERADO_TERMINAL = {
    ev: (A.ABORTAR_FASE if ev in (V.RECHAZO_IDENTIDAD, V.DUENO_DESPLAZADO) else A.NO_TOCAR)
    for ev in V
}

CASILLAS_ESPERADAS: dict[tuple[EstadoCambio, EventoCambio], tuple[AccionCambio, EstadoCambio]] = {
    # PENDIENTE
    (E.PENDIENTE, V.NUEVO_HORARIO): (A.ENVIAR, E.INCIERTO),
    (E.PENDIENTE, V.OK_CONFIRMADO): (A.SELLAR_APLICADO, E.APLICADO),
    (E.PENDIENTE, V.NO_APLICADO_EN_AMAZON): (A.NO_TOCAR, E.PENDIENTE),
    (E.PENDIENTE, V.ERROR_4XX_DETERMINISTA): (A.SELLAR_RECHAZADO, E.RECHAZADO),
    (E.PENDIENTE, V.ERROR_5XX_O_RED): (A.MANTENER_INCIERTO, E.INCIERTO),
    (E.PENDIENTE, V.RECHAZO_IDENTIDAD): (A.ABORTAR_FASE, E.RECHAZADO),
    (E.PENDIENTE, V.RECHAZO_IDENTIDAD_DEFINITIVO): (A.ABORTAR_FASE, E.PENDIENTE),
    (E.PENDIENTE, V.PAUSA_MANUAL_DUENO): (A.SELLAR_PAUSADO_DUENO, E.PAUSADO_POR_DUENO),
    (E.PENDIENTE, V.ENTIDAD_ARCHIVADA): (A.SELLAR_TERMINAL, E.ARCHIVADO),
    (E.PENDIENTE, V.ENTIDAD_SIN_IDENTIDAD): (A.SELLAR_TERMINAL, E.SIN_IDENTIDAD),
    (E.PENDIENTE, V.DUENO_DESPLAZADO): (A.ABORTAR_FASE, E.PENDIENTE),
    (E.PENDIENTE, V.INTENTOS_AGOTADOS): (A.NO_TOCAR, E.TOPE_INTENTOS),
    (E.PENDIENTE, V.OPERACION_EN_CURSO): (A.NO_TOCAR, E.EN_VUELO),
    # ARREGLOS 05 B: lo leido antes de enviar
    (E.PENDIENTE, V.IDENTIDAD_SUSTITUIDA): (A.NO_TOCAR, E.AJENO),
    (E.PENDIENTE, V.IDENTIDAD_DUDOSA): (A.NO_TOCAR, E.PENDIENTE),
    (E.PENDIENTE, V.CAMBIO_MANUAL_DUENO): (A.NO_TOCAR, E.MOVIDO_POR_DUENO),
    # INCIERTO
    (E.INCIERTO, V.NUEVO_HORARIO): (A.LEER_RESULTADO, E.INCIERTO),
    (E.INCIERTO, V.OK_CONFIRMADO): (A.SELLAR_APLICADO, E.APLICADO),
    (E.INCIERTO, V.NO_APLICADO_EN_AMAZON): (A.CONTAR_INTENTO, E.PENDIENTE),
    (E.INCIERTO, V.ERROR_4XX_DETERMINISTA): (A.SELLAR_RECHAZADO, E.RECHAZADO),
    (E.INCIERTO, V.ERROR_5XX_O_RED): (A.MANTENER_INCIERTO, E.INCIERTO),
    (E.INCIERTO, V.RECHAZO_IDENTIDAD): (A.ABORTAR_FASE, E.INCIERTO),
    (E.INCIERTO, V.RECHAZO_IDENTIDAD_DEFINITIVO): (A.SELLAR_RECHAZADO, E.RECHAZADO),
    (E.INCIERTO, V.PAUSA_MANUAL_DUENO): (A.SELLAR_PAUSADO_DUENO, E.PAUSADO_POR_DUENO),
    (E.INCIERTO, V.ENTIDAD_ARCHIVADA): (A.SELLAR_TERMINAL, E.ARCHIVADO),
    (E.INCIERTO, V.ENTIDAD_SIN_IDENTIDAD): (A.SELLAR_TERMINAL, E.SIN_IDENTIDAD),
    (E.INCIERTO, V.DUENO_DESPLAZADO): (A.ABORTAR_FASE, E.INCIERTO),
    (E.INCIERTO, V.INTENTOS_AGOTADOS): (A.LEER_RESULTADO, E.INCIERTO),
    (E.INCIERTO, V.OPERACION_EN_CURSO): (A.NO_TOCAR, E.EN_VUELO),
    # ARREGLOS 05 B: la lectura de un intento abierto atribuye el objeto
    (E.INCIERTO, V.IDENTIDAD_SUSTITUIDA): (A.SELLAR_TERMINAL, E.AJENO),
    (E.INCIERTO, V.IDENTIDAD_DUDOSA): (A.MANTENER_INCIERTO, E.INCIERTO),
    (E.INCIERTO, V.CAMBIO_MANUAL_DUENO): (A.SELLAR_TERMINAL, E.MOVIDO_POR_DUENO),
    # EN_VUELO
    **{
        (E.EN_VUELO, ev): (
            (A.ABORTAR_FASE, E.EN_VUELO)
            if ev in (V.RECHAZO_IDENTIDAD, V.DUENO_DESPLAZADO)
            else (A.NO_TOCAR, E.EN_VUELO)
        )
        for ev in V
    },
    # Terminales: absorbentes salvo fallos globales
    **{
        (estado, ev): (_ESPERADO_TERMINAL[ev], estado)
        for estado in (
            E.APLICADO,
            E.RECHAZADO,
            E.PAUSADO_POR_DUENO,
            E.ARCHIVADO,
            E.SIN_IDENTIDAD,
            E.TOPE_INTENTOS,
            E.AJENO,
            E.MOVIDO_POR_DUENO,
        )
        for ev in V
    },
}


def test_arreglos05_a_tabla_estados_completa_y_sin_ambiguedad():
    """Toda combinacion `(estado, evento)` existe una sola vez en la tabla y en la expectativa."""
    esperadas = set(itertools.product(EstadoCambio, EventoCambio))
    assert set(TABLA_TRANSICIONES.keys()) == esperadas
    assert set(CASILLAS_ESPERADAS.keys()) == esperadas
    assert len(TABLA_TRANSICIONES) == len(EstadoCambio) * len(EventoCambio) == 176


@pytest.mark.parametrize(
    ("estado", "evento"),
    list(itertools.product(EstadoCambio, EventoCambio)),
    ids=lambda x: x.value,
)
def test_arreglos05_a_tabla_casilla_accion_y_estado_siguiente(
    estado: EstadoCambio, evento: EventoCambio
):
    """Recorre TODA la tabla `estado x evento` y afirma accion y estado siguiente exactos."""
    transicion = decidir(estado, evento)
    assert isinstance(transicion, Transicion)
    accion_esperada, siguiente_esperado = CASILLAS_ESPERADAS[(estado, evento)]
    assert (transicion.accion, transicion.estado_siguiente) == (
        accion_esperada,
        siguiente_esperado,
    )


def test_arreglos05_a_invariantes_globales_de_la_tabla():
    """Los invariantes de los bloques A (1 a 6) y B (7 y 8) valen sobre TODAS las
    casillas de la tabla."""
    # 1. Lo incierto se lee antes de reenviar: ninguna casilla de INCIERTO envia,
    #    y ninguna casilla con intento abierto que no lo sella puede dejar el
    #    origen completo (`origen_incompleto` obligatorio cuando `sella_intento` es False).
    for ev in EventoCambio:
        tr = decidir(E.INCIERTO, ev)
        assert tr.accion != A.ENVIAR, f"INCIERTO + {ev} no puede enviar"
        if not tr.sella_intento:
            assert tr.origen_incompleto is True, (
                f"INCIERTO + {ev} sin sellar no puede dejar origen_incompleto=False"
            )
    assert decidir(E.INCIERTO, V.NUEVO_HORARIO).accion == A.LEER_RESULTADO
    assert decidir(E.INCIERTO, V.INTENTOS_AGOTADOS).accion == A.LEER_RESULTADO
    tr_5xx = decidir(E.INCIERTO, V.ERROR_5XX_O_RED)
    assert (tr_5xx.accion, tr_5xx.origen_incompleto, tr_5xx.sella_intento) == (
        A.MANTENER_INCIERTO,
        True,
        False,
    )

    # 2. Nunca mas de 3 intentos: TOPE_INTENTOS nunca envia y la fuente es TOPE_INTENTOS_CAMBIO.
    assert TOPE_INTENTOS_CAMBIO == 3
    for ev in EventoCambio:
        assert decidir(E.TOPE_INTENTOS, ev).accion != A.ENVIAR
    assert decidir(E.PENDIENTE, V.INTENTOS_AGOTADOS) == Transicion(A.NO_TOCAR, E.TOPE_INTENTOS)
    assert decidir(E.INCIERTO, V.INTENTOS_AGOTADOS).accion == A.LEER_RESULTADO

    # 3. Lo pausado por el dueno no se reactiva y nunca se sella como APLICADO.
    for ev in EventoCambio:
        tr = decidir(E.PAUSADO_POR_DUENO, ev)
        assert tr.accion != A.ENVIAR
        assert tr.estado_siguiente == E.PAUSADO_POR_DUENO
    tr_dueno = decidir(E.INCIERTO, V.PAUSA_MANUAL_DUENO)
    assert (tr_dueno.accion, tr_dueno.estado_siguiente, tr_dueno.sella_intento) == (
        A.SELLAR_PAUSADO_DUENO,
        E.PAUSADO_POR_DUENO,
        True,
    )

    # 4. Un rechazo de identidad (401/403) aborta la fase en todo estado: ninguna mutacion mas.
    for estado in EstadoCambio:
        tr = decidir(estado, V.RECHAZO_IDENTIDAD)
        assert tr.accion == A.ABORTAR_FASE and tr.propaga_aborto is True
    assert decidir(E.INCIERTO, V.RECHAZO_IDENTIDAD).sella_intento is False
    # Un 401/403 ya guardado en el ACK de un intento abierto es definitivo:
    # se sella y admite otro intento registrado, sin tocar el 5xx incierto.
    trans_def = decidir(E.INCIERTO, V.RECHAZO_IDENTIDAD_DEFINITIVO)
    assert (
        trans_def.accion,
        trans_def.estado_siguiente,
        trans_def.sella_intento,
        trans_def.propaga_aborto,
    ) == (A.SELLAR_RECHAZADO, E.RECHAZADO, True, False)

    # 5. Un dueno desplazado jamas sella en ningun estado.
    for estado in EstadoCambio:
        tr = decidir(estado, V.DUENO_DESPLAZADO)
        assert tr.accion == A.ABORTAR_FASE
        assert tr.sella_intento is False
        assert tr.propaga_aborto is True

    # 6. Una operacion en vuelo en otra ruta nunca se concilia ni reenvia.
    for ev in EventoCambio:
        if ev not in (V.RECHAZO_IDENTIDAD, V.DUENO_DESPLAZADO):
            tr = decidir(E.EN_VUELO, ev)
            assert (tr.accion, tr.estado_siguiente, tr.sella_intento, tr.origen_incompleto) == (
                A.NO_TOCAR,
                E.EN_VUELO,
                False,
                True,
            )

    # 7. (ARREGLOS 05 B) Lo ajeno nunca se toca: AJENO es absorbente y ninguna
    #    casilla con IDENTIDAD_SUSTITUIDA envia; un intento abierto lo sella
    #    como terminal sin confirmarlo.
    for ev in EventoCambio:
        tr = decidir(E.AJENO, ev)
        assert tr.accion != A.ENVIAR and tr.estado_siguiente == E.AJENO
        assert decidir(E.PENDIENTE, V.IDENTIDAD_SUSTITUIDA).accion != A.ENVIAR
    assert decidir(E.INCIERTO, V.IDENTIDAD_SUSTITUIDA) == Transicion(
        A.SELLAR_TERMINAL, E.AJENO, sella_intento=True
    )
    # Y lo dudoso queda pendiente: nada se sella y el origen sigue incompleto.
    assert decidir(E.INCIERTO, V.IDENTIDAD_DUDOSA) == Transicion(
        A.MANTENER_INCIERTO, E.INCIERTO, origen_incompleto=True
    )
    assert decidir(E.PENDIENTE, V.IDENTIDAD_DUDOSA) == Transicion(
        A.NO_TOCAR, E.PENDIENTE, origen_incompleto=True
    )

    # 8. (ARREGLOS 05 B) Una puja que otra mano movio no se revierte:
    #    MOVIDO_POR_DUENO es absorbente y CAMBIO_MANUAL_DUENO nunca ordena
    #    enviar ni confirmar.
    for ev in EventoCambio:
        tr = decidir(E.MOVIDO_POR_DUENO, ev)
        assert tr.accion != A.ENVIAR and tr.estado_siguiente == E.MOVIDO_POR_DUENO
    for estado in EstadoCambio:
        tr = decidir(estado, V.CAMBIO_MANUAL_DUENO)
        assert tr.accion in (A.NO_TOCAR, A.SELLAR_TERMINAL), (estado, tr.accion)


def test_arreglos05_a_clasificadores_puros_del_modelo():
    """Los clasificadores traducen hechos del ledger, HTTP y lecturas sin ambiguedad."""
    assert (
        estado_inicial_desde_hechos(
            en_vuelo_ajeno=True,
            tiene_intento_abierto=True,
            resultado_terminal=None,
            intentos_totales=0,
        )
        == E.EN_VUELO
    )
    # Con 3 intentos pero el 3o abierto, primero se lee (INCIERTO), no se omite por tope.
    assert (
        estado_inicial_desde_hechos(
            en_vuelo_ajeno=False,
            tiene_intento_abierto=True,
            resultado_terminal=None,
            intentos_totales=3,
        )
        == E.INCIERTO
    )
    assert (
        estado_inicial_desde_hechos(
            en_vuelo_ajeno=False,
            tiene_intento_abierto=False,
            resultado_terminal=RESULTADO_OK,
            intentos_totales=1,
        )
        == E.APLICADO
    )
    assert (
        estado_inicial_desde_hechos(
            en_vuelo_ajeno=False,
            tiene_intento_abierto=False,
            resultado_terminal=RESULTADO_PAUSADA_POR_DUENO,
            intentos_totales=1,
        )
        == E.PAUSADO_POR_DUENO
    )
    assert (
        estado_inicial_desde_hechos(
            en_vuelo_ajeno=False,
            tiene_intento_abierto=False,
            resultado_terminal="fallo:entidad_archived",
            intentos_totales=1,
        )
        == E.ARCHIVADO
    )
    assert (
        estado_inicial_desde_hechos(
            en_vuelo_ajeno=False,
            tiene_intento_abierto=False,
            resultado_terminal=None,
            intentos_totales=3,
        )
        == E.TOPE_INTENTOS
    )
    assert (
        estado_inicial_desde_hechos(
            en_vuelo_ajeno=False,
            tiene_intento_abierto=False,
            resultado_terminal=None,
            intentos_totales=2,
        )
        == E.PENDIENTE
    )
    # Nivel tipos (/correct): ningun hecho obligatorio puede omitirse.
    with pytest.raises(TypeError):
        estado_inicial_desde_hechos(  # type: ignore[call-arg]
            en_vuelo_ajeno=False,
            tiene_intento_abierto=False,
            resultado_terminal=None,
        )

    err_403 = AdsApiErrorMutacion(
        "status=403", cuerpo="{}", status=403, method="PUT", path="/sp/productAds"
    )
    err_400 = AdsApiErrorMutacion(
        "status=400", cuerpo="{}", status=400, method="PUT", path="/sp/keywords"
    )
    err_500 = AdsApiError("status=500")
    err_500.status = 500
    assert evento_desde_error(err_403, es_mutacion_determinista=True) == V.RECHAZO_IDENTIDAD
    assert evento_desde_error(err_400, es_mutacion_determinista=True) == V.ERROR_4XX_DETERMINISTA
    assert evento_desde_error(err_500, es_mutacion_determinista=False) == V.ERROR_5XX_O_RED
    assert evento_desde_status(401) == V.RECHAZO_IDENTIDAD
    assert evento_desde_status(400, es_rechazo_explicito=True) == V.ERROR_4XX_DETERMINISTA
    assert evento_desde_status(503) == V.ERROR_5XX_O_RED
    assert transicion_desde_error(
        E.PENDIENTE, err_403, es_mutacion_determinista=True
    ).propaga_aborto
    assert not transicion_desde_error(
        E.PENDIENTE, err_400, es_mutacion_determinista=True
    ).propaga_aborto
    assert transicion_desde_status(E.INCIERTO, 401).propaga_aborto
    assert not transicion_desde_status(E.INCIERTO, 500).propaga_aborto

    assert evento_desde_lectura_estado(None, destino="ENABLED") == V.ERROR_5XX_O_RED
    assert evento_desde_lectura_estado("ENABLED", destino="ENABLED") == V.OK_CONFIRMADO
    assert (
        evento_desde_lectura_estado(
            "PAUSED",
            destino="ENABLED",
            cache_previo_estado="ENABLED",
            cache_previo_posterior_al_intento=True,
        )
        == V.PAUSA_MANUAL_DUENO
    )
    assert (
        evento_desde_lectura_estado(
            "PAUSED",
            destino="ENABLED",
            cache_previo_estado="PAUSED",
            cache_previo_posterior_al_intento=True,
        )
        == V.NO_APLICADO_EN_AMAZON
    )
    assert evento_desde_lectura_estado("ARCHIVED", destino="ENABLED") == V.ENTIDAD_ARCHIVADA

    # ARREGLOS 05 B: el estado AJENO sale del desenlace sellado del ledger.
    assert (
        estado_inicial_desde_hechos(
            en_vuelo_ajeno=False,
            tiene_intento_abierto=False,
            resultado_terminal=RESULTADO_IDENTIDAD_SUSTITUIDA,
            intentos_totales=1,
        )
        == E.AJENO
    )
    assert RESULTADO_NO_APLICADO == "ok:no_aplicado"
    assert RESULTADOS_TERMINALES_REVERSA == ("ok", "omitida:pausada_tras_habilitar")
    assert frozenset({401, 403}) == STATUS_IDENTIDAD_RECHAZADA


def _objeto(kid: str, *, grupo="7101", texto="term", match="EXACT", estado="ENABLED") -> dict:
    return {
        "keywordId": kid,
        "adGroupId": grupo,
        "keywordText": texto,
        "matchType": match,
        "state": estado,
    }


def test_arreglos05_a_clasificadores_de_identidad_y_de_puja():
    """ARREGLOS 05 B: el resolver unico de identidad y el de puja traducen una
    lectura a un evento de la tabla, con literales esperados por caso."""
    propia = [_objeto("k-1")]
    nada = _objeto("x-9", texto="otro")

    # Creacion (destino vivo): lo leido antes del POST.
    assert (
        evento_desde_lectura_objeto(
            [], id_propio=None, ad_group="7101", texto="term", match="EXACT", destino="vivo"
        )
        == V.NO_APLICADO_EN_AMAZON
    )
    assert (
        evento_desde_lectura_objeto(
            propia, id_propio=None, ad_group="7101", texto="term", match="EXACT", destino="vivo"
        )
        == V.OK_CONFIRMADO
    )
    assert (
        evento_desde_lectura_objeto(
            [_objeto("k-1"), _objeto("k-2")],
            id_propio=None,
            ad_group="7101",
            texto="term",
            match="EXACT",
            destino="vivo",
        )
        == V.IDENTIDAD_DUDOSA
    )
    assert (
        evento_desde_lectura_objeto(
            [nada], id_propio=None, ad_group="7101", texto="term", match="EXACT", destino="vivo"
        )
        == V.NO_APLICADO_EN_AMAZON
    )

    # Lectura de vuelta del ID del ACK.
    kw = dict(ad_group="7101", texto="term", match="EXACT", destino="vivo", id_propio="k-1")
    assert evento_desde_lectura_objeto(propia, **kw) == V.OK_CONFIRMADO
    assert (
        evento_desde_lectura_objeto([_objeto("k-1", estado="ARCHIVED"), _objeto("ajena")], **kw)
        == V.IDENTIDAD_SUSTITUIDA
    )
    assert (
        evento_desde_lectura_objeto(
            [_objeto("k-1", estado="ARCHIVED"), _objeto("a-1"), _objeto("a-2")], **kw
        )
        == V.IDENTIDAD_DUDOSA
    )
    assert evento_desde_lectura_objeto([_objeto("k-1", texto="otro")], **kw) == V.IDENTIDAD_DUDOSA
    assert (
        evento_desde_lectura_objeto([_objeto("k-1", estado="ARCHIVED")], **kw)
        == V.ENTIDAD_ARCHIVADA
    )
    assert evento_desde_lectura_objeto([], **kw) == V.NO_APLICADO_EN_AMAZON
    # La identidad es completa: otro grupo u otro match es otro objeto.
    assert evento_desde_lectura_objeto([_objeto("k-1", grupo="9999")], **kw) == V.IDENTIDAD_DUDOSA
    assert evento_desde_lectura_objeto([_objeto("k-1", match="PHRASE")], **kw) == V.IDENTIDAD_DUDOSA
    assert (
        evento_desde_lectura_objeto(
            [_objeto("n-1", match="NEGATIVE_EXACT")],
            id_propio="n-1",
            ad_group="7101",
            texto="term",
            match="NEGATIVE_EXACT",
            destino="vivo",
        )
        == V.OK_CONFIRMADO
    )

    # Reversa (destino ausente): lo leido antes del DELETE.
    rv = dict(ad_group="7101", texto="term", match="EXACT", destino="ausente", id_propio="k-1")
    assert evento_desde_lectura_objeto(propia, **rv) == V.NO_APLICADO_EN_AMAZON
    assert evento_desde_lectura_objeto([_objeto("k-1", estado="ARCHIVED")], **rv) == V.OK_CONFIRMADO
    assert evento_desde_lectura_objeto([], **rv) == V.OK_CONFIRMADO
    assert (
        evento_desde_lectura_objeto([_objeto("k-1", estado="ARCHIVED"), _objeto("ajena")], **rv)
        == V.IDENTIDAD_SUSTITUIDA
    )
    assert (
        evento_desde_lectura_objeto(
            [_objeto("n-1", match="NEGATIVE_EXACT")],
            id_propio=None,
            ad_group="7101",
            texto="term",
            match="NEGATIVE_EXACT",
            destino="ausente",
        )
        == V.NO_APLICADO_EN_AMAZON
    )

    # Lo leido antes de enviar solo lleva a ENVIAR cuando el cambio no esta en Amazon.
    assert envia_tras_lectura(E.PENDIENTE, V.NO_APLICADO_EN_AMAZON) is True
    for evento in (
        V.OK_CONFIRMADO,
        V.IDENTIDAD_SUSTITUIDA,
        V.IDENTIDAD_DUDOSA,
        V.CAMBIO_MANUAL_DUENO,
        V.ERROR_5XX_O_RED,
    ):
        assert envia_tras_lectura(E.PENDIENTE, evento) is False
    assert envia_tras_lectura(E.INCIERTO, V.NO_APLICADO_EN_AMAZON) is False
    assert envia_tras_lectura(E.TOPE_INTENTOS, V.NO_APLICADO_EN_AMAZON) is False

    # Puja: destino es el valor al que se vuelve; escritos, los que dejo Orbit.
    uno, dos, tres = (Decimal(x) for x in ("1.00", "2.00", "3.00"))
    assert evento_desde_lectura_bid(None, destino=uno, escritos=frozenset({dos})) == (
        V.ERROR_5XX_O_RED
    )
    assert evento_desde_lectura_bid(uno, destino=uno, escritos=frozenset({dos})) == V.OK_CONFIRMADO
    assert evento_desde_lectura_bid(dos, destino=uno, escritos=frozenset({dos})) == (
        V.NO_APLICADO_EN_AMAZON
    )
    # Orbit movio la hoja despues de esta decision: sigue siendo suyo.
    assert evento_desde_lectura_bid(tres, destino=uno, escritos=frozenset({dos, tres})) == (
        V.NO_APLICADO_EN_AMAZON
    )
    # Un valor que Orbit nunca escribio lo puso otra mano.
    assert evento_desde_lectura_bid(tres, destino=uno, escritos=frozenset({dos})) == (
        V.CAMBIO_MANUAL_DUENO
    )
    assert evento_desde_lectura_bid(dos, destino=uno, escritos=frozenset()) == V.CAMBIO_MANUAL_DUENO


def _violaciones_chequeo_dueno_unico(fuentes: dict[str, str]) -> list[str]:
    """Chequeo estructural `/correct`: impide duplicar el literal de pausa
    manual, `STATUS_IDENTIDAD_RECHAZADA`, `es_rechazo_de_identidad` o la
    consulta `HAVING` ad hoc fuera de `app/apply_estados.py`."""
    errores: list[str] = []
    for rel, texto in fuentes.items():
        if rel == "app/apply_estados.py":
            continue
        if "omitida:pausada_tras_habilitar" in texto:
            errores.append(
                f"{rel}: literal 'omitida:pausada_tras_habilitar' duplicado; "
                "usa app.apply_estados.RESULTADO_PAUSADA_POR_DUENO"
            )
        if "fallo:identidad_" in texto:
            errores.append(
                f"{rel}: literal 'fallo:identidad_' duplicado; "
                "usa app.apply_estados.RESULTADO_IDENTIDAD_SUSTITUIDA"
            )
        if "_SQL_REVERSA_TERMINAL" in texto:
            errores.append(
                f"{rel}: consulta ad hoc _SQL_REVERSA_TERMINAL prohibida; "
                "usa app.apply_estados.estado_inicial_desde_hechos + decidir"
            )
        if rel == "app/apply_cola_ejecuta.py" and "HAVING" in texto.upper():
            errores.append(
                f"{rel}: decision SQL con HAVING prohibida; "
                "deriva el estado desde hechos con app.apply_estados"
            )
        if rel in ("app/apply_cola_ejecuta.py", "app/cycle_apply.py") and (
            "excluir_queue_ids" in texto
        ):
            errores.append(
                f"{rel}: exclusion condicional `excluir_queue_ids` prohibida; "
                "reconcilia_reversas_pause lee todo intento abierto de forma incondicional"
            )
        arbol = ast.parse(texto)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Assign):
                for target in nodo.targets:
                    if isinstance(target, ast.Name) and target.id in (
                        "STATUS_IDENTIDAD_RECHAZADA",
                        "RESULTADO_PAUSADA_TRAS_HABILITAR",
                    ):
                        errores.append(
                            f"{rel}: redefine {target.id}; importalo de app.apply_estados"
                        )
            elif isinstance(nodo, ast.FunctionDef) and nodo.name == "es_rechazo_de_identidad":
                errores.append(
                    f"{rel}: redefine es_rechazo_de_identidad; usa app.apply_estados.decidir"
                )
            elif (
                isinstance(nodo, ast.FunctionDef)
                and rel == "app/apply_cola_ejecuta.py"
                and nodo.name == "aplica_reactivaciones"
            ):
                for stmt in nodo.body:
                    if isinstance(stmt, ast.For):
                        break
                    if isinstance(stmt, ast.If) and any(
                        isinstance(sub, ast.Return) for sub in ast.walk(stmt)
                    ):
                        test_src = ast.unparse(stmt.test)
                        if "temporada." in test_src or test_src == "config is None":
                            errores.append(
                                f"{rel}: aplica_reactivaciones retorna por apagador de "
                                "temporada antes de leer intentos abiertos"
                            )
            elif isinstance(nodo, ast.Attribute) and nodo.attr == "estado_siguiente":
                errores.append(
                    f"{rel}: ramifica por .estado_siguiente; los llamadores actuan solo "
                    "segun transicion.accion"
                )
            elif isinstance(nodo, ast.Compare) and rel == "app/apply_cola_ejecuta.py":
                nodos_cmp = [nodo.left, *nodo.comparators]
                if any(
                    isinstance(n, ast.Attribute) and n.attr == "ESTADO_WIRE_ENABLED"
                    for n in nodos_cmp
                ):
                    errores.append(
                        f"{rel}: compara directamente con ESTADO_WIRE_ENABLED; "
                        "usa evento_desde_lectura_estado + decidir"
                    )
            elif (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Name)
                and nodo.func.id == "estado_inicial_desde_hechos"
            ):
                for kw in nodo.keywords:
                    if (
                        kw.arg == "intentos_totales"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value == 0
                    ):
                        errores.append(
                            f"{rel}: pasa intentos_totales=0 literal a "
                            "estado_inicial_desde_hechos; pasa el conteo real del ledger"
                        )
    return errores


def test_arreglos05_a_chequeo_correct_dueno_unico_en_codigo_y_muerde_violaciones():
    """Nivel chequeo (/correct): el codigo actual cumple dueno unico y el
    detector atrapa muestras reales del codigo anterior (regla 9)."""
    modulos = [
        "app/apply.py",
        "app/apply_cola.py",
        "app/apply_cola_ejecuta.py",
        "app/apply_reversa.py",
        "app/apply_producto.py",
        "app/retiro_anuncios.py",
        "app/apply_harvest.py",
        "app/apply_harvest_hermanas.py",
        "app/apply_harvest_reversa.py",
        "app/apply_harvest_reconciliacion.py",
        "app/apply_harvest_contexto.py",
        "app/apply_negative.py",
        "app/apply_reconcilia.py",
        "app/apply_store.py",
        "app/cycle_apply.py",
        "app/temporada_io.py",
        "app/ads/archivar.py",
        "app/ads/reposicion.py",
    ]
    fuentes = {rel: (RAIZ / rel).read_text(encoding="utf-8") for rel in modulos}
    assert _violaciones_chequeo_dueno_unico(fuentes) == []

    # El chequeo muerde sobre muestras reales del codigo anterior (antes de migrar
    # y de los defectos de integracion de la 7a a 9a pasada de Astra).
    violaciones = _violaciones_chequeo_dueno_unico(
        {
            "app/apply_cola_ejecuta.py": (
                "_SQL_REVERSA_TERMINAL = (\n"
                "    'SELECT 1 FROM apply_attempt GROUP BY 1 HAVING count(*) >= 3'\n"
                ")\n"
                "RESULTADO_PAUSADA_TRAS_HABILITAR = 'omitida:pausada_tras_habilitar'\n"
                "def _mal(tr, estado):\n"
                "    return (tr.estado_siguiente, estado == apply.ESTADO_WIRE_ENABLED)\n"
                "def aplica_reactivaciones(decisiones):\n"
                "    if temporada.modo_desde_settings({}, 'us') != 'live':\n"
                "        return []\n"
                "    for d in decisiones:\n"
                "        pass\n"
            ),
            "app/cycle_apply.py": "x = {'excluir_queue_ids': set()}\n",
            "app/apply_reversa.py": (
                "estado_inicial_desde_hechos(\n"
                "    en_vuelo_ajeno=False,\n"
                "    tiene_intento_abierto=False,\n"
                "    resultado_terminal=None,\n"
                "    intentos_totales=0,\n"
                ")\n"
            ),
            "app/apply_store.py": (
                "STATUS_IDENTIDAD_RECHAZADA = frozenset({401, 403})\n"
                "def es_rechazo_de_identidad(exc):\n"
                "    return False\n"
            ),
            "app/apply_negative.py": "resultado = 'fallo:identidad_sustituida'\n",
        }
    )
    assert len(violaciones) == 12
