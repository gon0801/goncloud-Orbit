"""Modelo explicito de estados para cambios enviados a Amazon (ARREGLOS 05 A y B).

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

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
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
# ARREGLOS 05 B: la lectura de vuelta encontro viva otra cosa donde estaba el
# objeto propio. Prefijo que `estado_inicial_desde_hechos` lee como `AJENO`.
RESULTADO_IDENTIDAD_SUSTITUIDA = "fallo:identidad_sustituida"
# ARREGLOS 05 C: resultados propios de la reposicion de keywords. Un rechazo
# definitivo no cierra la operacion del archivo: admite otro intento registrado
# (con tope). El prefijo `fallo:entidad_` lo lee la derivacion como ARCHIVADO.
RESULTADO_REPOSICION_RECHAZO = "fallo:rechazo"
RESULTADO_REPOSICION_ENTIDAD_ARCHIVADA = "fallo:entidad_archivada"

# Estados del WIRE en las listas (sellado por probe 2.5, 2026-08-26). Fuente
# unica: `apply_store` los re-exporta.
# ARCHIVED = operativamente muerto: NO confirma entidad viva ni pause verificado.
ESTADO_WIRE_ENABLED = "ENABLED"
ESTADO_WIRE_PAUSED = "PAUSED"
ESTADO_WIRE_ARCHIVED = "ARCHIVED"


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
    AJENO = "ajeno"
    MOVIDO_POR_DUENO = "movido_por_dueno"
    # ARREGLOS 05 H: el ciclo de origen ya intento sus 150 pujas ordinarias.
    # Terminal para esa decision: no se envia y su desenlace auditable es
    # `decision_sin_aplicar.motivo = 'tope_ciclo'`.
    CUPO_CICLO_AGOTADO = "cupo_ciclo_agotado"

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
        EstadoCambio.AJENO,
        EstadoCambio.MOVIDO_POR_DUENO,
        EstadoCambio.CUPO_CICLO_AGOTADO,
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
    RECHAZO_IDENTIDAD_DEFINITIVO = "rechazo_identidad_definitivo"
    PAUSA_MANUAL_DUENO = "pausa_manual_dueno"
    ENTIDAD_ARCHIVADA = "entidad_archivada"
    ENTIDAD_SIN_IDENTIDAD = "entidad_sin_identidad"
    DUENO_DESPLAZADO = "dueno_desplazado"
    INTENTOS_AGOTADOS = "intentos_agotados"
    OPERACION_EN_CURSO = "operacion_en_curso"
    IDENTIDAD_SUSTITUIDA = "identidad_sustituida"
    IDENTIDAD_DUDOSA = "identidad_dudosa"
    CAMBIO_MANUAL_DUENO = "cambio_manual_dueno"
    # ARREGLOS 05 H: hecho del ledger, hermano de `intentos_agotados`: el
    # ciclo ORIGEN llego a su tope de pujas ordinarias.
    CUPO_CICLO_AGOTADO = "cupo_ciclo_agotado"


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
        # ARREGLOS 05 C: el 401/403 ya quedo en el ACK durable de un intento
        # abierto. Es un rechazo definitivo, distinto de un 5xx incierto: se
        # sella y permite otro intento registrado en una fase posterior.
        (E.PENDIENTE, V.RECHAZO_IDENTIDAD_DEFINITIVO): T(
            A.ABORTAR_FASE,
            E.PENDIENTE,
            origen_incompleto=True,
            sella_intento=False,
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
        # ARREGLOS 05 B: antes de enviar se relee. Lo que vive con la identidad
        # propia bajo OTRO id es ajeno (no se adopta ni se toca); lo que no se
        # puede atribuir queda pendiente; una puja que otra mano movio no se
        # revierte.
        (E.PENDIENTE, V.IDENTIDAD_SUSTITUIDA): T(A.NO_TOCAR, E.AJENO, origen_incompleto=True),
        (E.PENDIENTE, V.IDENTIDAD_DUDOSA): T(A.NO_TOCAR, E.PENDIENTE, origen_incompleto=True),
        (E.PENDIENTE, V.CAMBIO_MANUAL_DUENO): T(
            A.NO_TOCAR, E.MOVIDO_POR_DUENO, origen_incompleto=True
        ),
        # ARREGLOS 05 H: el tope del ciclo no envia; la decision queda con su
        # desenlace auditable (`tope_ciclo`) y no se reabre otro ciclo.
        (E.PENDIENTE, V.CUPO_CICLO_AGOTADO): T(A.NO_TOCAR, E.CUPO_CICLO_AGOTADO),
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
        (E.INCIERTO, V.RECHAZO_IDENTIDAD_DEFINITIVO): T(
            A.SELLAR_RECHAZADO, E.RECHAZADO, sella_intento=True
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
        # ARREGLOS 05 B: la lectura de un intento abierto atribuye el objeto.
        # Sustituido o movido por el dueno: desenlace terminal, sellado, sin
        # confirmar. Dudoso: sigue abierto, se relee el horario siguiente.
        (E.INCIERTO, V.IDENTIDAD_SUSTITUIDA): T(A.SELLAR_TERMINAL, E.AJENO, sella_intento=True),
        (E.INCIERTO, V.IDENTIDAD_DUDOSA): T(
            A.MANTENER_INCIERTO, E.INCIERTO, origen_incompleto=True
        ),
        (E.INCIERTO, V.CAMBIO_MANUAL_DUENO): T(
            A.SELLAR_TERMINAL, E.MOVIDO_POR_DUENO, sella_intento=True
        ),
        # ARREGLOS 05 H: un intento ya abierto se lee, nunca se reenvia; el
        # tope del ciclo no decide sobre el.
        (E.INCIERTO, V.CUPO_CICLO_AGOTADO): T(A.LEER_RESULTADO, E.INCIERTO, origen_incompleto=True),
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


def transicion_de_reserva_cupo(*, agotado: bool) -> Transicion:
    """La tabla decide si el cupo del ciclo deja enviar (ARREGLOS 05 H).

    Con el tope agotado, un cambio pendiente no envia (`no_tocar` -> terminal
    `cupo_ciclo_agotado`); con lugar, el envio sigue su camino normal
    (`nuevo_horario` -> `enviar`). El llamador ramifica solo por
    `transicion.accion`: `ENVIAR` es la unica que autoriza seguir al cupo
    diario y a la intencion durable."""
    if agotado:
        return decidir(EstadoCambio.PENDIENTE, EventoCambio.CUPO_CICLO_AGOTADO)
    return decidir(EstadoCambio.PENDIENTE, EventoCambio.NUEVO_HORARIO)


def envia_tras_lectura(estado: EstadoCambio, evento: EventoCambio) -> bool:
    """Si, leido `evento` en Amazon justo antes del envio, corresponde ENVIAR.

    Solo cuando la lectura dice que el cambio todavia no esta en Amazon
    (`NO_APLICADO_EN_AMAZON`) y la tabla, consultada de nuevo con
    `NUEVO_HORARIO`, ordena `ENVIAR`. Todo lo demas leido antes de enviar
    (ya aplicado, ajeno, dudoso, movido por el dueno, sin lectura legible) se
    atiende con la transicion de ese evento y nunca lleva a un envio."""
    if evento != EventoCambio.NO_APLICADO_EN_AMAZON:
        return False
    return decidir(estado, EventoCambio.NUEVO_HORARIO).accion == AccionCambio.ENVIAR


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
    3. Desenlace terminal sellado -> `APLICADO`, `PAUSADO_POR_DUENO`, `ARCHIVADO`
       o `AJENO` (otro objeto ocupa hoy su identidad).
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
    if resultado_terminal == RESULTADO_IDENTIDAD_SUSTITUIDA:
        return EstadoCambio.AJENO
    if intentos_totales >= tope_intentos:
        return EstadoCambio.TOPE_INTENTOS
    return EstadoCambio.PENDIENTE


def es_rechazo_de_identidad(exc: BaseException) -> bool:
    """True si `exc` es un rechazo de identidad o permisos (401/403) en lectura o mutacion."""
    return isinstance(exc, AdsApiError) and exc.status in STATUS_IDENTIDAD_RECHAZADA


def evento_desde_ack_identidad(ack: dict | None) -> EventoCambio | None:
    """El ACK durable de un intento abierto es un 401/403 definitivo.

    Amazon rechazo la identidad antes de procesar la mutacion: el intento se
    puede sellar como rechazo y admitir otro intento registrado (ARREGLOS 05 C).
    Un 5xx o una respuesta perdida no dejan ese ACK y siguen siendo inciertos.
    """
    if isinstance(ack, dict) and ack.get("status") in STATUS_IDENTIDAD_RECHAZADA:
        return EventoCambio.RECHAZO_IDENTIDAD_DEFINITIVO
    return None


def resultado_terminal_reposicion(resultado: str | None) -> str | None:
    """Traduce el ultimo resultado de un intento de reposicion a lo que la tabla
    considera terminal (ARREGLOS 05 C).

    Un rechazo definitivo NO cierra la operacion del archivo: el archivo sigue
    en `applied` (o en `repuesto` para la reversa) y admite otro intento
    registrado, siempre que el conteo no agote el tope. Todo lo demas (`ok`,
    `fallo:entidad_*`, identidad sustituida) si es terminal.
    """
    if resultado == RESULTADO_REPOSICION_RECHAZO:
        return None
    return resultado


def evento_reposicion_desde_lectura(
    items: list[dict],
    *,
    id_propio: str | None,
    ad_group: str,
    texto: str,
    match: str,
    destino: str,
    campaign_esperada: str | None = None,
    bid_esperado: Decimal | None = None,
    estado_esperado: str | None = None,
    abierto: bool = False,
) -> EventoCambio:
    """El resolver unico de identidad, con los hechos propios de la reposicion.

    Sobre `evento_desde_lectura_objeto` agrega las reglas de la reposicion
    (ARREGLOS 05 C):

    - Si el intento tiene ID propio (ACK) y la lectura COMPLETA no trae ninguna
      fila con ese ID, el evento es `IDENTIDAD_DUDOSA`: una omision nunca
      autoriza otro POST. Lo mismo vale para el ID ausente de una reversa.
    - Si el intento no tiene ID (ACK perdido) y la lectura encuentra viva una
      identidad igual, es `IDENTIDAD_DUDOSA`: no se adopta un objeto que pudo
      preexistir.
    - Si el ID propio vive con su identidad pero hay mas de un vivo con esa
      identidad, `IDENTIDAD_DUDOSA` (preexistente o duplicado).
    - Si la lectura confirma el ID propio, campana y bid deben coincidir con el
      payload exacto; si no, `IDENTIDAD_DUDOSA`.
    - En una reversa ABIERTA, un objetivo que sigue vivo es incertidumbre
      (`ERROR_5XX_O_RED`), nunca un "no aplicado" que autorice reenviar.
    """
    base = evento_desde_lectura_objeto(
        items,
        id_propio=id_propio,
        ad_group=ad_group,
        texto=texto,
        match=match,
        destino=destino,
    )
    if id_propio is None:
        if destino == "vivo" and base == EventoCambio.OK_CONFIRMADO:
            return EventoCambio.IDENTIDAD_DUDOSA
        return base
    propio = next(
        (x for x in items if isinstance(x, dict) and str(x.get("keywordId")) == str(id_propio)),
        None,
    )
    if propio is None:
        return EventoCambio.IDENTIDAD_DUDOSA
    if destino == "ausente" and str(propio.get("state", "")).upper() == ESTADO_WIRE_ARCHIVED:
        # La reversa archivo SU id propio: un duplicado ajeno vivo con la misma
        # identidad no impide confirmar que el objeto propio ya no vive.
        return EventoCambio.OK_CONFIRMADO
    if base == EventoCambio.NO_APLICADO_EN_AMAZON and destino == "ausente" and abierto:
        return EventoCambio.ERROR_5XX_O_RED
    if base != EventoCambio.OK_CONFIRMADO or destino != "vivo":
        return base
    match_norm = str(match).casefold().replace("negative_", "")
    vivos = [
        x
        for x in items
        if isinstance(x, dict)
        and str(x.get("state", "")).upper() != ESTADO_WIRE_ARCHIVED
        and _identidad_esperada(x, ad_group, texto, match_norm)
    ]
    if len(vivos) != 1:
        return EventoCambio.IDENTIDAD_DUDOSA
    if estado_esperado is not None and str(propio.get("state", "")).upper() != estado_esperado:
        return EventoCambio.IDENTIDAD_DUDOSA
    if campaign_esperada is not None and str(propio.get("campaignId")) != str(campaign_esperada):
        return EventoCambio.IDENTIDAD_DUDOSA
    if bid_esperado is not None:
        try:
            leido = Decimal(str(propio.get("bid"))).quantize(Decimal("0.01"))
        except (ArithmeticError, ValueError):
            return EventoCambio.IDENTIDAD_DUDOSA
        if leido != Decimal(str(bid_esperado)).quantize(Decimal("0.01")):
            return EventoCambio.IDENTIDAD_DUDOSA
    return base


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


def _match_normal(item: dict) -> str:
    return str(item.get("matchType", "")).casefold().replace("negative_", "")


def _identidad_esperada(item: dict, ad_group: str, texto: str, match: str) -> bool:
    """Misma identidad que el resolver: adGroupId, keywordText y match."""
    return (
        str(item.get("adGroupId")) == str(ad_group)
        and item.get("keywordText") == texto
        and _match_normal(item) == match
    )


def evento_desde_lectura_objeto(
    items: list[dict],
    *,
    id_propio: str | None,
    ad_group: str,
    texto: str,
    match: str,
    destino: str,
) -> EventoCambio:
    """Clasifica una lectura COMPLETA de lista respecto del objeto propio
    (ARREGLOS 05 B). Es el unico resolver de identidad: lo usan la creacion, la
    lectura de vuelta, la conciliacion y las tres reversas.

    La identidad de un objeto es (adGroupId, keywordText, matchType). `destino`
    es el cambio en curso: `"vivo"` cuando se creo o se conserva, `"ausente"`
    cuando se retiro o se debe retirar. `id_propio` es el del ACK; `None`
    cuando el intento no devolvio ninguno.

    - El propio vive con su identidad: `OK_CONFIRMADO` si es el destino,
      `NO_APLICADO_EN_AMAZON` si falta.
    - El propio vive con OTRA identidad, o hay dos o mas vivos con la esperada
      y ninguno es el propio: `IDENTIDAD_DUDOSA` (no se concluye nada).
    - El propio no vive y queda exactamente uno vivo con la identidad esperada:
      `IDENTIDAD_SUSTITUIDA` (es ajeno).
    - Sin id propio, un vivo con la identidad cuenta como el efecto pedido.
    - Nadie vive con la identidad: `OK_CONFIRMADO` si el destino es ausente;
      si era vivir, `ENTIDAD_ARCHIVADA` cuando el propio aparece archivado y
      `NO_APLICADO_EN_AMAZON` cuando no aparece.
    """
    match = match.casefold().replace("negative_", "")

    def esperada(item: dict) -> bool:
        return _identidad_esperada(item, ad_group, texto, match)

    vivos = [x for x in items if str(x.get("state", "")).upper() != ESTADO_WIRE_ARCHIVED]
    propio = (
        next((x for x in items if str(x.get("keywordId")) == str(id_propio)), None)
        if id_propio is not None
        else None
    )
    if propio is not None and esperada(propio) and propio in vivos:
        if destino == "vivo":
            return EventoCambio.OK_CONFIRMADO
        return EventoCambio.NO_APLICADO_EN_AMAZON
    if propio is not None and propio in vivos:
        return EventoCambio.IDENTIDAD_DUDOSA
    ajenos = [x for x in vivos if esperada(x)]
    if len(ajenos) > 1:
        return EventoCambio.IDENTIDAD_DUDOSA
    if id_propio is None:
        if ajenos:
            return (
                EventoCambio.OK_CONFIRMADO
                if destino == "vivo"
                else EventoCambio.NO_APLICADO_EN_AMAZON
            )
        return (
            EventoCambio.NO_APLICADO_EN_AMAZON if destino == "vivo" else EventoCambio.OK_CONFIRMADO
        )
    if ajenos:
        return EventoCambio.IDENTIDAD_SUSTITUIDA
    if destino == "ausente":
        return EventoCambio.OK_CONFIRMADO
    if propio is not None:
        return EventoCambio.ENTIDAD_ARCHIVADA
    return EventoCambio.NO_APLICADO_EN_AMAZON


def evento_desde_lectura_bid(
    bid_vivo: Decimal | None, *, destino: Decimal, escritos: Collection[Decimal]
) -> EventoCambio:
    """Clasifica el bid leido en Amazon respecto del cambio en curso de UNA
    puja (ARREGLOS 05 B). Los valores llegan ya cuantizados al wire.

    `destino` es el bid que el cambio deja. `escritos` son los bids que Orbit
    escribio en esa hoja: el de la decision que se revierte y el de sus
    decisiones posteriores (el regreso del dueno revierte la primera de una
    racha aunque Orbit la haya movido despues). Sin lectura legible es
    `ERROR_5XX_O_RED`. Un valor vivo que no es ninguno de esos lo puso otra
    mano: `CAMBIO_MANUAL_DUENO`, y no se toca."""
    if bid_vivo is None:
        return EventoCambio.ERROR_5XX_O_RED
    if bid_vivo == destino:
        return EventoCambio.OK_CONFIRMADO
    if bid_vivo in escritos:
        return EventoCambio.NO_APLICADO_EN_AMAZON
    return EventoCambio.CAMBIO_MANUAL_DUENO
