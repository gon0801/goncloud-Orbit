"""ARREGLOS 05 A: pruebas exhaustivas de la tabla `estado x evento` y chequeos `/correct`.

Recorre TODAS las casillas de `app.apply_estados.TABLA_TRANSICIONES` (9 estados
x 12 eventos = 108 casillas), comprueba los invariantes del bloque A sobre toda
la tabla, valida los clasificadores puros y ejecuta los chequeos estructurales
de `/correct` que impiden reintroducir la premisa rota (constantes, consultas
`HAVING` ad hoc o comprobaciones 401/403 duplicadas fuera del dueno unico).
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

from app.ads.client import AdsApiError
from app.ads.write import AdsApiErrorMutacion
from app.apply_estados import (
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
    estado_inicial_desde_hechos,
    evento_desde_error,
    evento_desde_lectura_estado,
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
    (E.PENDIENTE, V.PAUSA_MANUAL_DUENO): (A.SELLAR_PAUSADO_DUENO, E.PAUSADO_POR_DUENO),
    (E.PENDIENTE, V.ENTIDAD_ARCHIVADA): (A.SELLAR_TERMINAL, E.ARCHIVADO),
    (E.PENDIENTE, V.ENTIDAD_SIN_IDENTIDAD): (A.SELLAR_TERMINAL, E.SIN_IDENTIDAD),
    (E.PENDIENTE, V.DUENO_DESPLAZADO): (A.ABORTAR_FASE, E.PENDIENTE),
    (E.PENDIENTE, V.INTENTOS_AGOTADOS): (A.NO_TOCAR, E.TOPE_INTENTOS),
    (E.PENDIENTE, V.OPERACION_EN_CURSO): (A.NO_TOCAR, E.EN_VUELO),
    # INCIERTO
    (E.INCIERTO, V.NUEVO_HORARIO): (A.LEER_RESULTADO, E.INCIERTO),
    (E.INCIERTO, V.OK_CONFIRMADO): (A.SELLAR_APLICADO, E.APLICADO),
    (E.INCIERTO, V.NO_APLICADO_EN_AMAZON): (A.CONTAR_INTENTO, E.PENDIENTE),
    (E.INCIERTO, V.ERROR_4XX_DETERMINISTA): (A.SELLAR_RECHAZADO, E.RECHAZADO),
    (E.INCIERTO, V.ERROR_5XX_O_RED): (A.MANTENER_INCIERTO, E.INCIERTO),
    (E.INCIERTO, V.RECHAZO_IDENTIDAD): (A.ABORTAR_FASE, E.INCIERTO),
    (E.INCIERTO, V.PAUSA_MANUAL_DUENO): (A.SELLAR_PAUSADO_DUENO, E.PAUSADO_POR_DUENO),
    (E.INCIERTO, V.ENTIDAD_ARCHIVADA): (A.SELLAR_TERMINAL, E.ARCHIVADO),
    (E.INCIERTO, V.ENTIDAD_SIN_IDENTIDAD): (A.SELLAR_TERMINAL, E.SIN_IDENTIDAD),
    (E.INCIERTO, V.DUENO_DESPLAZADO): (A.ABORTAR_FASE, E.INCIERTO),
    (E.INCIERTO, V.INTENTOS_AGOTADOS): (A.LEER_RESULTADO, E.INCIERTO),
    (E.INCIERTO, V.OPERACION_EN_CURSO): (A.NO_TOCAR, E.EN_VUELO),
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
        )
        for ev in V
    },
}


def test_arreglos05_a_tabla_estados_completa_y_sin_ambiguedad():
    """Toda combinacion `(estado, evento)` existe una sola vez en la tabla y en la expectativa."""
    esperadas = set(itertools.product(EstadoCambio, EventoCambio))
    assert set(TABLA_TRANSICIONES.keys()) == esperadas
    assert set(CASILLAS_ESPERADAS.keys()) == esperadas
    assert len(TABLA_TRANSICIONES) == len(EstadoCambio) * len(EventoCambio) == 108


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
    """Los cinco invariantes del bloque A valen sobre TODAS las casillas de la tabla."""
    # 1. Lo incierto se lee antes de reenviar: ninguna casilla de INCIERTO envia.
    for ev in EventoCambio:
        tr = decidir(E.INCIERTO, ev)
        assert tr.accion != A.ENVIAR, f"INCIERTO + {ev} no puede enviar"
    assert decidir(E.INCIERTO, V.NUEVO_HORARIO).accion == A.LEER_RESULTADO
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


def test_arreglos05_a_clasificadores_puros_del_modelo():
    """Los clasificadores traducen hechos del ledger, HTTP y lecturas sin ambiguedad."""
    assert (
        estado_inicial_desde_hechos(en_vuelo_ajeno=True, tiene_intento_abierto=True) == E.EN_VUELO
    )
    # Con 3 intentos pero el 3o abierto, primero se lee (INCIERTO), no se omite por tope.
    assert estado_inicial_desde_hechos(tiene_intento_abierto=True, intentos_totales=3) == E.INCIERTO
    assert estado_inicial_desde_hechos(resultado_terminal=RESULTADO_OK) == E.APLICADO
    assert (
        estado_inicial_desde_hechos(resultado_terminal=RESULTADO_PAUSADA_POR_DUENO)
        == E.PAUSADO_POR_DUENO
    )
    assert estado_inicial_desde_hechos(resultado_terminal="fallo:entidad_archived") == E.ARCHIVADO
    assert estado_inicial_desde_hechos(intentos_totales=3) == E.TOPE_INTENTOS
    assert estado_inicial_desde_hechos(intentos_totales=2) == E.PENDIENTE

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
    assert RESULTADO_NO_APLICADO == "ok:no_aplicado"
    assert RESULTADOS_TERMINALES_REVERSA == ("ok", "omitida:pausada_tras_habilitar")
    assert frozenset({401, 403}) == STATUS_IDENTIDAD_RECHAZADA


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
        "app/apply_reconcilia.py",
        "app/apply_store.py",
        "app/temporada_io.py",
    ]
    fuentes = {rel: (RAIZ / rel).read_text(encoding="utf-8") for rel in modulos}
    assert _violaciones_chequeo_dueno_unico(fuentes) == []

    # El chequeo muerde sobre muestras reales del codigo anterior (antes de migrar).
    violaciones = _violaciones_chequeo_dueno_unico(
        {
            "app/apply_cola_ejecuta.py": (
                "_SQL_REVERSA_TERMINAL = (\n"
                "    'SELECT 1 FROM apply_attempt GROUP BY 1 HAVING count(*) >= 3'\n"
                ")\n"
                "RESULTADO_PAUSADA_TRAS_HABILITAR = 'omitida:pausada_tras_habilitar'\n"
            ),
            "app/apply_store.py": (
                "STATUS_IDENTIDAD_RECHAZADA = frozenset({401, 403})\n"
                "def es_rechazo_de_identidad(exc):\n"
                "    return False\n"
            ),
        }
    )
    assert len(violaciones) == 6
