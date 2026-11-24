"""Modelo explicito de estados para cambios enviados a Amazon (ARREGLOS 05 A).

Un solo dueno y una sola tabla de datos (`TABLA_TRANSICIONES`) gobiernan que
hacer ante cada resultado de Amazon, cambio del dueno, tope de intentos o
relevo de claim. Quienes ejecutan (pujas en `apply.py`, reactivaciones en
`apply_cola_ejecuta.py`, reversa manual en `apply_reversa.py`, pausa de
producto en `apply_producto.py`/`retiro_anuncios.py`, cortes y cosecha en
`apply_cola.py`, `apply_harvest_reversa.py` y `apply_harvest_reconciliacion.py`)
consultan `decidir(estado, evento)` en vez de repartir `if`/`except` o
consultas ad hoc.

Modulo puro (cero IO, cero imports de `app.ads.write` ni `psycopg`): cualquier
capa del motor puede importarlo sin ciclos.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.ads.client import AdsApiError

# Fuente unica del tope de intentos por decision/pausa (regla 2: un numero, una
# fuente). `apply_store.TOPE_INTENTOS` y `temporada_io` consumen esta constante.
TOPE_INTENTOS_CAMBIO = 3

# Un 403, o un 401 que sigue tras refrescar el token, rechaza la identidad o
# los permisos de toda la corrida, en lectura o en mutacion.
STATUS_IDENTIDAD_RECHAZADA = frozenset({401, 403})

# Literales canonicos del ledger `apply_attempt.resultado` compartidos entre
# `apply_cola_ejecuta.py`, `apply_reversa.py` y `temporada_io.py`.
RESULTADO_OK = "ok"
RESULTADO_NO_APLICADO = "ok:no_aplicado"
RESULTADO_PAUSADA_POR_DUENO = "omitida:pausada_tras_habilitar"
RESULTADOS_TERMINALES_REVERSA = (RESULTADO_OK, RESULTADO_PAUSADA_POR_DUENO)


class EstadoCambio(StrEnum):
    """Estado de un cambio (o intento de reversa) frente a Amazon."""

    PENDIENTE = "pendiente"
    EN_VUELO = "en_vuelo"
    INCIERTO = "incierto"
    APLICADO = "aplicado"
    RECHAZADO = "rechazado"
    PAUSADO_POR_DUENO = "pausado_por_dueno"
    ARCHIVADO = "archivado"
    SIN_IDENTIDAD = "sin_identidad"
    TOPE_INTENTOS = "tope_intentos"

    @property
    def es_terminal(self) -> bool:
        return self in _ESTADOS_TERMINALES


_ESTADOS_TERMINALES = frozenset(
    {
        EstadoCambio.APLICADO,
        EstadoCambio.RECHAZADO,
        EstadoCambio.PAUSADO_POR_DUENO,
        EstadoCambio.ARCHIVADO,
        EstadoCambio.SIN_IDENTIDAD,
        EstadoCambio.TOPE_INTENTOS,
    }
)


class EventoCambio(StrEnum):
    """Evento observado sobre el cambio desde Amazon, cron, dueno o claim."""

    NUEVO_HORARIO = "nuevo_horario"
    OK_CONFIRMADO = "ok_confirmado"
    NO_APLICADO_EN_AMAZON = "no_aplicado_en_amazon"
    ERROR_4XX_DETERMINISTA = "error_4xx_determinista"
    ERROR_5XX_O_RED = "error_5xx_o_red"
    RECHAZO_IDENTIDAD = "rechazo_identidad"
    PAUSA_MANUAL_DUENO = "pausa_manual_dueno"
    ENTIDAD_ARCHIVADA = "entidad_archivada"
    ENTIDAD_SIN_IDENTIDAD = "entidad_sin_identidad"
    DUENO_DESPLAZADO = "dueno_desplazado"
    INTENTOS_AGOTADOS = "intentos_agotados"
    OPERACION_EN_CURSO = "operacion_en_curso"


class AccionCambio(StrEnum):
    """Accion unica que ordena cada casilla `(estado, evento)` de la tabla."""

    ENVIAR = "enviar"
    LEER_RESULTADO = "leer_resultado"
    SELLAR_APLICADO = "sellar_aplicado"
    SELLAR_RECHAZADO = "sellar_rechazado"
    CONTAR_INTENTO = "contar_intento"
    MANTENER_INCIERTO = "mantener_incierto"
    SELLAR_PAUSADO_DUENO = "sellar_pausado_dueno"
    SELLAR_TERMINAL = "sellar_terminal"
    NO_TOCAR = "no_tocar"
    ABORTAR_FASE = "abortar_fase"


@dataclass(frozen=True)
class Transicion:
    """Resultado canonico de una casilla `(estado, evento)`.

    - `accion`: unica accion que debe ejecutar el llamador.
    - `estado_siguiente`: estado del cambio tras ejecutar la accion.
    - `origen_incompleto`: si True, el origen no puede marcarse `apply.completo`.
    - `sella_intento`: si True, corresponde sellar la fila abierta del ledger
      (siempre bajo cerca del claim en ruta automatica). Nunca True cuando el
      dueno fue desplazado ni cuando el resultado quedo incierto.
    - `propaga_aborto`: si True, es fallo global (401/403 o claim perdido) y la
      fase debe abortar sin emitir mas mutaciones.
    """

    accion: AccionCambio
    estado_siguiente: EstadoCambio
    origen_incompleto: bool = False
    sella_intento: bool = False
    propaga_aborto: bool = False


def _fila_terminal(estado: EstadoCambio) -> dict[tuple[EstadoCambio, EventoCambio], Transicion]:
    """Transiciones de un estado terminal: absorbente ante eventos ordinarios,
    y fallo global ante rechazo de identidad o desplazamiento de dueno."""
    fila: dict[tuple[EstadoCambio, EventoCambio], Transicion] = {}
    for evento in EventoCambio:
        if evento in (EventoCambio.RECHAZO_IDENTIDAD, EventoCambio.DUENO_DESPLAZADO):
            fila[(estado, evento)] = Transicion(
                AccionCambio.ABORTAR_FASE,
                estado,
                origen_incompleto=True,
                sella_intento=False,
                propaga_aborto=True,
            )
        else:
            fila[(estado, evento)] = Transicion(AccionCambio.NO_TOCAR, estado)
    return fila


def _construye_tabla() -> Mapping[tuple[EstadoCambio, EventoCambio], Transicion]:
    E = EstadoCambio
    V = EventoCambio
    A = AccionCambio
    T = Transicion

    tabla: dict[tuple[EstadoCambio, EventoCambio], Transicion] = {
        # --- PENDIENTE: listo para decidir envio o cerrar antes de mutar ---
        (E.PENDIENTE, V.NUEVO_HORARIO): T(A.ENVIAR, E.INCIERTO),
        (E.PENDIENTE, V.OK_CONFIRMADO): T(A.SELLAR_APLICADO, E.APLICADO, sella_intento=True),
        (E.PENDIENTE, V.NO_APLICADO_EN_AMAZON): T(A.NO_TOCAR, E.PENDIENTE),
        (E.PENDIENTE, V.ERROR_4XX_DETERMINISTA): T(
            A.SELLAR_RECHAZADO, E.RECHAZADO, sella_intento=True
        ),
        (E.PENDIENTE, V.ERROR_5XX_O_RED): T(
            A.MANTENER_INCIERTO, E.INCIERTO, origen_incompleto=True
        ),
        (E.PENDIENTE, V.RECHAZO_IDENTIDAD): T(
            A.ABORTAR_FASE,
            E.RECHAZADO,
            origen_incompleto=True,
            sella_intento=True,
            propaga_aborto=True,
        ),
        (E.PENDIENTE, V.PAUSA_MANUAL_DUENO): T(
            A.SELLAR_PAUSADO_DUENO, E.PAUSADO_POR_DUENO, sella_intento=True
        ),
        (E.PENDIENTE, V.ENTIDAD_ARCHIVADA): T(A.SELLAR_TERMINAL, E.ARCHIVADO, sella_intento=True),
        (E.PENDIENTE, V.ENTIDAD_SIN_IDENTIDAD): T(
            A.SELLAR_TERMINAL, E.SIN_IDENTIDAD, sella_intento=True
        ),
        (E.PENDIENTE, V.DUENO_DESPLAZADO): T(
            A.ABORTAR_FASE,
            E.PENDIENTE,
            origen_incompleto=True,
            sella_intento=False,
            propaga_aborto=True,
        ),
        (E.PENDIENTE, V.INTENTOS_AGOTADOS): T(A.NO_TOCAR, E.TOPE_INTENTOS),
        (E.PENDIENTE, V.OPERACION_EN_CURSO): T(A.NO_TOCAR, E.EN_VUELO, origen_incompleto=True),
        # --- INCIERTO: intento abierto sin sello; exige lectura antes de reenviar ---
        (E.INCIERTO, V.NUEVO_HORARIO): T(A.LEER_RESULTADO, E.INCIERTO, origen_incompleto=True),
        (E.INCIERTO, V.OK_CONFIRMADO): T(A.SELLAR_APLICADO, E.APLICADO, sella_intento=True),
        (E.INCIERTO, V.NO_APLICADO_EN_AMAZON): T(A.CONTAR_INTENTO, E.PENDIENTE, sella_intento=True),
        (E.INCIERTO, V.ERROR_4XX_DETERMINISTA): T(
            A.SELLAR_RECHAZADO, E.RECHAZADO, sella_intento=True
        ),
        (E.INCIERTO, V.ERROR_5XX_O_RED): T(A.MANTENER_INCIERTO, E.INCIERTO, origen_incompleto=True),
        (E.INCIERTO, V.RECHAZO_IDENTIDAD): T(
            A.ABORTAR_FASE,
            E.INCIERTO,
            origen_incompleto=True,
            sella_intento=False,
            propaga_aborto=True,
        ),
        (E.INCIERTO, V.PAUSA_MANUAL_DUENO): T(
            A.SELLAR_PAUSADO_DUENO, E.PAUSADO_POR_DUENO, sella_intento=True
        ),
        (E.INCIERTO, V.ENTIDAD_ARCHIVADA): T(A.SELLAR_TERMINAL, E.ARCHIVADO, sella_intento=True),
        (E.INCIERTO, V.ENTIDAD_SIN_IDENTIDAD): T(
            A.SELLAR_TERMINAL, E.SIN_IDENTIDAD, sella_intento=True
        ),
        (E.INCIERTO, V.DUENO_DESPLAZADO): T(
            A.ABORTAR_FASE,
            E.INCIERTO,
            origen_incompleto=True,
            sella_intento=False,
            propaga_aborto=True,
        ),
        # Incluso con 3 intentos, si el 3o quedo abierto se lee su resultado
        # antes de entrar en PENDIENTE y comprobar el tope de intentos.
        (E.INCIERTO, V.INTENTOS_AGOTADOS): T(A.LEER_RESULTADO, E.INCIERTO, origen_incompleto=True),
        (E.INCIERTO, V.OPERACION_EN_CURSO): T(A.NO_TOCAR, E.EN_VUELO, origen_incompleto=True),
        # --- EN_VUELO: otra ruta tiene la exclusion activa sobre esta decision ---
        **{
            (E.EN_VUELO, ev): (
                T(
                    A.ABORTAR_FASE,
                    E.EN_VUELO,
                    origen_incompleto=True,
                    sella_intento=False,
                    propaga_aborto=True,
                )
                if ev in (V.RECHAZO_IDENTIDAD, V.DUENO_DESPLAZADO)
                else T(A.NO_TOCAR, E.EN_VUELO, origen_incompleto=True)
            )
            for ev in EventoCambio
        },
    }
    for terminal in _ESTADOS_TERMINALES:
        tabla.update(_fila_terminal(terminal))
    return tabla


TABLA_TRANSICIONES: Mapping[tuple[EstadoCambio, EventoCambio], Transicion] = _construye_tabla()


def decidir(estado: EstadoCambio, evento: EventoCambio) -> Transicion:
    """Funcion pura de decision sobre la tabla unica `estado x evento`."""
    return TABLA_TRANSICIONES[(estado, evento)]


def estado_inicial_desde_hechos(
    *,
    en_vuelo_ajeno: bool,
    tiene_intento_abierto: bool,
    resultado_terminal: str | None,
    intentos_totales: int,
    tope_intentos: int = TOPE_INTENTOS_CAMBIO,
) -> EstadoCambio:
    """Deriva el `EstadoCambio` actual a partir de hechos observables del ledger.

    Los cuatro hechos observables son obligatorios (sin valor por omision) para
    que ningun llamador pueda descartar el conteo de intentos ni un resultado
    terminal. Prioridad fija:
    1. Otra ruta operando (`en_vuelo_ajeno`) -> `EN_VUELO` (ni concilia ni envia).
    2. Intento abierto sin sello -> `INCIERTO` (siempre se lee antes de mirar el tope).
    3. Desenlace terminal sellado -> `APLICADO`, `PAUSADO_POR_DUENO` o `ARCHIVADO`.
    4. Conteo de intentos >= `tope_intentos` -> `TOPE_INTENTOS`.
    5. En cualquier otro caso -> `PENDIENTE`.
    """
    if en_vuelo_ajeno:
        return EstadoCambio.EN_VUELO
    if tiene_intento_abierto:
        return EstadoCambio.INCIERTO
    if resultado_terminal == RESULTADO_OK:
        return EstadoCambio.APLICADO
    if resultado_terminal == RESULTADO_PAUSADA_POR_DUENO:
        return EstadoCambio.PAUSADO_POR_DUENO
    if isinstance(resultado_terminal, str) and resultado_terminal.startswith("fallo:entidad_"):
        return EstadoCambio.ARCHIVADO
    if intentos_totales >= tope_intentos:
        return EstadoCambio.TOPE_INTENTOS
    return EstadoCambio.PENDIENTE


def es_rechazo_de_identidad(exc: BaseException) -> bool:
    """True si `exc` es un rechazo de identidad o permisos (401/403) en lectura o mutacion."""
    return isinstance(exc, AdsApiError) and exc.status in STATUS_IDENTIDAD_RECHAZADA


def evento_desde_status(
    status: int | None,
    *,
    es_rechazo_explicito: bool = False,
) -> EventoCambio:
    """Clasifica un codigo HTTP recibido de Amazon en su `EventoCambio`."""
    if status in STATUS_IDENTIDAD_RECHAZADA:
        return EventoCambio.RECHAZO_IDENTIDAD
    if es_rechazo_explicito and status is not None and status < 500:
        return EventoCambio.ERROR_4XX_DETERMINISTA
    return EventoCambio.ERROR_5XX_O_RED


def evento_desde_error(
    exc: BaseException,
    *,
    es_mutacion_determinista: bool = False,
) -> EventoCambio:
    """Clasifica una excepcion de Amazon Ads en su `EventoCambio`."""
    if es_rechazo_de_identidad(exc):
        return EventoCambio.RECHAZO_IDENTIDAD
    status = getattr(exc, "status", None) if isinstance(exc, AdsApiError) else None
    return evento_desde_status(status, es_rechazo_explicito=es_mutacion_determinista)


def transicion_desde_error(
    estado: EstadoCambio,
    exc: BaseException,
    *,
    es_mutacion_determinista: bool = False,
) -> Transicion:
    """Clasifica una excepcion y consulta la tabla unica en un solo paso."""
    return decidir(
        estado,
        evento_desde_error(exc, es_mutacion_determinista=es_mutacion_determinista),
    )


def transicion_desde_status(
    estado: EstadoCambio,
    status: int | None,
    *,
    es_rechazo_explicito: bool = False,
) -> Transicion:
    """Clasifica un status HTTP y consulta la tabla unica en un solo paso."""
    return decidir(
        estado,
        evento_desde_status(status, es_rechazo_explicito=es_rechazo_explicito),
    )


def evento_desde_lectura_estado(
    estado_vivo: str | None,
    *,
    destino: str,
    cache_previo_estado: str | None = None,
    cache_previo_posterior_al_intento: bool = False,
) -> EventoCambio:
    """Clasifica el estado leido en Amazon durante la conciliacion de un intento abierto."""
    if estado_vivo is None:
        return EventoCambio.ERROR_5XX_O_RED
    if estado_vivo == destino:
        return EventoCambio.OK_CONFIRMADO
    if estado_vivo == "PAUSED" and destino == "ENABLED":
        if cache_previo_estado == "ENABLED" and cache_previo_posterior_al_intento:
            return EventoCambio.PAUSA_MANUAL_DUENO
        return EventoCambio.NO_APLICADO_EN_AMAZON
    return EventoCambio.ENTIDAD_ARCHIVADA
