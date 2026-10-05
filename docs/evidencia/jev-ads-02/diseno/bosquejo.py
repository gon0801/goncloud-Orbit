"""JEV ADS 02: bosquejo sintetizado de tipos y firmas. NO es implementacion.

Acompana a docs/superpowers/specs/2026-10-04-jev-ads-02-design.md. Base: el
candidato A de la arena, con los injertos que registra juicio.md. Nada de
esto vive en app/: es el contrato que la implementacion debe cumplir.

Modelo: TRES HECHOS, UNA LECTURA. Para una busqueda-en-grupo hay tres hechos,
cada uno con un dueno y una sola fuente de verdad:

  1. quien esta en el ad group     -> Roster (censo + prueba de listado)
  2. si corresponde a cada producto -> juicio por par, global por ClavePar
  3. que hizo con el dinero         -> Economia (ventana madura + historial)

y una lectura, funcion PURA de los tres, congelada en `jev_senal`. Regla de
precedencia: los hechos de venta mandan; Jev solo desempata cuando la busqueda
no vende en ningun ad group.

Archivos que este bosquejo reparte (ver diseno.md, "Mapa de modulos"):
  app/jev_lectura.py   nucleo puro (seccion A)
  app/jev_libro.py     libro de juicios por par + tope diario (seccion B)
  app/jev_senales.py   el job y las lecturas de pantalla (seccion C)
  app/jev_vista.py     textos puros (seccion D, se agrega al modulo existente)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

# Tipos que YA existen en app/jev_ads.py y se reutilizan sin cambio.
from app.jev_ads import (
    CensoCongelado,
    FalloProveedor,
    FichaVersion,
    Juicio,
    PlataformaAmazon,
    RelevanciaConjunto,  # HayCompatible | NingunoCompatible | Indeterminado
)

# ===========================================================================
# A. app/jev_lectura.py -- NUCLEO PURO (sin red, sin base, sin reloj)
# ===========================================================================

Lectura = Literal[
    "vendio_aqui",  # ya vendio en ESTE ad group (historial): bloquear es riesgo
    "vende_en_otro",  # aqui no vende, en otro ad group si: bloquear aqui consolida
    "relevante_sin_venta",  # corresponde al grupo y no vende en NINGUNO
    "ajena",  # no corresponde a ningun producto (roster probado) y no vende
    "sin_lectura",  # no se puede afirmar nada; los motivos dicen por que
]
LecturaDestino = Literal["destino_corresponde", "destino_ajeno", "sin_lectura"]

MotivoSinLectura = Literal[
    "dato_de_venta_faltante",  # orders None aqui o en otro grupo: None no es cero
    "sin_observaciones",  # el grupo no tiene ventana de cortes
    "jev_sin_veredicto",  # la relevancia quedo Indeterminado (ver sus motivos)
    "jev_no_evaluada",  # no se le pregunto a Jev: sin cupo, tope 0 o sin api key
]

# Version de la regla `leer`. Se guarda en cada fila de jev_senal; una prueba
# recorre toda fila guardada y exige leer(insumos) == lectura para SU version.
REGLA_VERSION = 1


@dataclass(frozen=True)
class ClaveBusqueda:
    """LA unidad de todo el diseno: una busqueda en un ad group.

    `termino` es el texto literal de search_term_observation (sin normalizar:
    es la misma clave que usa el motor y la misma que hashea ClavePar).
    `ad_group_id` es ad_entity.id (interno), nunca el id externo de Amazon."""

    plataforma: PlataformaAmazon
    ad_group_id: int
    termino: str


@dataclass(frozen=True)
class Gasto:
    """Sumas de UNA busqueda en UN ad group sobre un rango de fechas, colapsadas
    a la ultima observacion por dia (misma regla que windows.terminos_cortes).
    None = dato faltante en alguna observacion del rango (regla 3)."""

    desde: date
    hasta: date
    clics: int | None
    gasto: Decimal | None
    ordenes: int | None


@dataclass(frozen=True)
class VentaEnOtroGrupo:
    """Otro ad group de la MISMA plataforma donde la MISMA busqueda tiene
    ordenes > 0 en su ventana madura de cortes. Solo existe con venta
    afirmada: un grupo sin venta o con dato faltante no produce este tipo."""

    ad_group_id: int
    en_ventana: Gasto  # ordenes > 0 garantizado por quien lo construye


@dataclass(frozen=True)
class Historial:
    """Todo lo observado de UNA busqueda en UN ad group, colapsado a la ultima
    observacion por dia. A diferencia de `Gasto`, NO se anula por un dia sin
    dato: `ordenes_conocidas` suma las ordenes de los dias que si traen dato
    y `dias_sin_dato` cuenta los demas. Una venta es un hecho positivo; un dia
    con orders NULL no puede borrar la venta de otro dia.

    Lo arma jev_senales sobre la subconsulta de colapso que expone
    app/optimizer/windows.py (una sola definicion de "ultima observacion por
    dia"); el agregado del motor no sirve aqui porque devuelve NULL si falta
    un solo dia."""

    desde: date
    hasta: date
    clics: int | None
    ordenes_conocidas: int
    dias_sin_dato: int


@dataclass(frozen=True)
class Economia:
    """Hecho 3. Todo "no vende" se afirma SOLO sobre la ventana madura de
    cortes (30 dias que terminan 10 atras); una venta es un hecho positivo y
    vale aunque sea inmadura o anterior a la ventana.

    Invariantes:
    - `aqui` es None si el ad group no tiene observaciones (sin ventana).
    - `otros_sin_dato` cuenta ad groups donde la busqueda aparece con
      ordenes None en ventana: mientras sea > 0 NO se puede afirmar
      "no vende en ninguno".
    - `historial` cubre todo lo observado en este ad group, incluida la cola
      inmadura; solo se usa para afirmar "vendio", jamas "no vendio".
    - `moneda` es la de la plataforma (MXN para amazon_mx, USD para
      amazon_us): todo importe de la economia va en ella (regla 4)."""

    moneda: Literal["MXN", "USD"]
    aqui: Gasto | None
    otros_que_venden: tuple[VentaEnOtroGrupo, ...]
    otros_sin_venta: int  # ad groups donde aparece con ordenes == 0 en ventana
    otros_sin_dato: int
    historial: Historial | None
    datos_hasta: datetime | None  # max(observed_at) usado


# --- hecho 1: roster probado ------------------------------------------------

MotivoSinProbar = Literal[
    "sin_corrida_reciente",  # ninguna corrida de estructura ok en MAX_EDAD_SYNC
    "plataforma_no_listada",  # la corrida no trae acta de esta plataforma
    "listado_sin_total",  # Amazon no declaro totalResults: sin prueba de fin
    "grupo_no_listado",  # el ad group no vino o no se escribio en la corrida
    "anuncios_descartados",  # algun product ad no archivado del grupo se salto
    "anuncios_sin_grupo",  # hubo product ads sin adGroupId: pueden ser de aqui
    "huella_distinta",  # los anuncios vivos en base no son los del listado
    "estado_ausente",  # un anuncio del grupo no tiene fila de estado
    "anuncio_sin_producto",  # un anuncio vivo no liga a producto
]


@dataclass(frozen=True)
class ActaDeListado:
    """Lo que la ingesta de estructura deja escrito de UNA corrida para UNA
    plataforma y UN ad group (tablas ads_listado_plataforma / ads_listado_grupo).
    Es dato de la base, no inferencia de `synced_at`.

    `del_grupo` es None si el ad group no tiene fila en esa corrida."""

    ingest_run_id: int
    finished_at: datetime
    ad_groups_declarados: int | None  # totalResults de adGroups; None = no declarado
    ad_groups_recibidos: int
    declarados: int | None  # totalResults de productAds; None = no declarado
    recibidos: int
    sin_grupo: int  # product ads no archivados sin adGroupId atribuible
    del_grupo: ActaDeGrupo | None


@dataclass(frozen=True)
class ActaDeGrupo:
    vivos: int  # product ads ENABLED/PAUSED del payload, escritos
    huella: str  # sha256 de sus adId externos ordenados
    descartados: int  # no archivados del payload que NO se escribieron


@dataclass(frozen=True)
class RosterProbado:
    """Se conocen TODOS los productos anunciados del ad group. Solo
    `probar_roster` lo construye (candado AST + CHECK de la base)."""

    ingest_run_id: int
    listado_de: datetime
    anuncios_vivos: int


@dataclass(frozen=True)
class RosterSinProbar:
    motivos: frozenset[MotivoSinProbar]


PruebaRoster = RosterProbado | RosterSinProbar


def probar_roster(
    censo: CensoCongelado,
    acta: ActaDeListado | None,
    *,
    huella_en_base: str,
    ahora: datetime,
    max_edad: object,  # windows.MAX_EDAD_SYNC (48 h): un numero, una fuente
) -> PruebaRoster:
    """Regla de roster probado. FALLA CERRADO: cualquier duda es SinProbar.

    Probado si y solo si TODO se cumple:
      1. hay acta, de una corrida ok, con finished_at > ahora - max_edad;
      2. los totales declarados son int e igualan a los recibidos, en ad
         groups Y en product ads (listados cerrados con prueba; un ad group
         saltado arrastra a sus anuncios sin dejar marca individual);
      3. acta.sin_grupo == 0;
      4. acta.del_grupo existe, con descartados == 0;
      5. acta.del_grupo.huella == huella_en_base (los anuncios ENABLED/PAUSED
         que la base cree que hay SON los que Amazon listo; ni uno mas, ni uno
         menos; no depende de synced_at);
      6. ningun miembro del censo tiene estado ausente;
      7. ningun miembro activo tiene producto_id None.
    Cada condicion fallida agrega SU motivo (se reportan todos, no el primero).
    """
    raise NotImplementedError


def anunciados_hoy(censo: CensoCongelado) -> CensoCongelado:
    """Puro. Quita a los miembros solo-no-activos: los que tienen anuncios y
    TODOS con estado conocido y no activo (p. ej. ARCHIVED). Es el complemento
    exacto de `_solo_no_activo` de app/jev_ads.py, la misma regla con que
    `componer` arma su universo; asi `Relevancia.miembros` coincide con el
    total de `componer`. Un miembro con estado ausente NO se quita: debe seguir
    visible como `estado_ausente` en `probar_roster` y `missing_state` en
    `componer`. No cambia `exhaustivo`."""
    raise NotImplementedError


@dataclass(frozen=True)
class Roster:
    """Hecho 1 congelado: el censo con sus fichas resueltas y su prueba.

    Invariante: censo.exhaustivo == isinstance(prueba, RosterProbado). Es la
    UNICA via por la que un censo de ad group llega a exhaustivo=True.

    El censo de un Roster pasa SIEMPRE por `anunciados_hoy`: el universo es
    lo anunciado hoy. `censo_grupo` devuelve tambien los productos cuyos
    anuncios estan todos archivados, y con ellos `componer` agrega
    `no_anunciado` y jamas da NingunoCompatible (8 de los 22 grupos con gasto
    de MX conservan 622 de esos). La huella del acta ya cuenta solo anuncios
    vivos.
    `sha256` identifica miembros + fichas (sin synced_at, sin la prueba): el
    mismo roster de ayer es la misma fila de jev_roster."""

    plataforma: PlataformaAmazon
    ad_group_id: int
    censo: CensoCongelado
    fichas: Mapping[UUID, FichaVersion]
    prueba: PruebaRoster
    sha256: str


# --- hecho 2: relevancia ------------------------------------------------------


@dataclass(frozen=True)
class NoEvaluada:
    """No se le pregunto a Jev por ningun par de esta busqueda en esta corrida
    y no habia juicios guardados: sin cupo, tope 0 o sin api key. Es distinto
    de Indeterminado ("Jev no pudo concluir") y jamas es evidencia."""

    motivo: Literal["sin_cupo", "tope_cero", "sin_api_key", "proveedor_caido"]


@dataclass(frozen=True)
class Relevancia:
    """Hecho 2 compuesto para un ad group: `componer` de jev_ads sobre el
    censo del roster. `satisfacen/evaluados/miembros` y `productos_ok` van
    SIEMPRE, tambien cuando el conjunto es Indeterminado: la proporcion es lo
    mas informativo que da Jev ("20 de 208" y "177 de 177" no son la misma
    busqueda), y `productos_ok` deja ver que ficha produjo un falso "si".
    Se evalua el grupo completo; no se para al primer `satisface`."""

    conjunto: RelevanciaConjunto | NoEvaluada
    satisfacen: int
    evaluados: int
    miembros: int
    productos_ok: tuple[int, ...]  # producto_id con juicio `satisface`
    juicio_ids: tuple[UUID, ...]  # eventos 'resultado' citados, exitos y fallos


# --- la lectura ---------------------------------------------------------------


def leer(economia: Economia, relevancia: Relevancia) -> tuple[Lectura, frozenset[MotivoSinLectura]]:
    """LA regla. Orden estricto; el primero que aplica gana:

      1. historial.ordenes_conocidas > 0            -> vendio_aqui
      2. otros_que_venden no vacio                  -> vende_en_otro
         (hecho positivo: basta uno aunque otros grupos tengan dato faltante)
      3. aqui None                                  -> sin_lectura{sin_observaciones}
      4. aqui.ordenes None
         u otros_sin_dato > 0                       -> sin_lectura{dato_de_venta_faltante}
         --- desde aqui "no vende en ninguno" esta AFIRMADO sobre ventana madura ---
      5. relevancia HayCompatible                   -> relevante_sin_venta
      6. relevancia NingunoCompatible               -> ajena
      7. relevancia Indeterminado                   -> sin_lectura{jev_sin_veredicto}
      8. relevancia NoEvaluada                      -> sin_lectura{jev_no_evaluada}

    Consecuencias que las pruebas fijan: un juicio jamas tapa una venta (1 y 2
    van antes que Jev); `ajena` exige NingunoCompatible, que `componer` solo da
    con censo exhaustivo, o sea con RosterProbado; un fallo del proveedor cae
    en 7, nunca en 6; y la falta de cupo cae en 8 sin impedir 1 ni 2."""
    raise NotImplementedError


def leer_destino(
    relevancia: Literal["corresponde", "ajena", "sin_veredicto", "no_evaluada"],
) -> LecturaDestino:
    """Harvest: del ad group destino, al dueno le importa si algun producto
    atiende la busqueda. Recibe la relevancia tal como se guarda en
    jev_senal.relevancia: corresponde -> destino_corresponde; ajena ->
    destino_ajeno; sin_veredicto o no_evaluada -> sin_lectura. Un destino que
    no se pudo leer (`destino_ilegible`) se anuncia como sin_lectura.

    Solo PRESENTACION: la fila de jev_senal del destino es una senal completa
    con la `lectura` que da `leer`; esto se deriva de su relevancia al pintar
    y al avisar, y es lo que guarda jev_aviso.lectura en un harvest."""
    raise NotImplementedError


# --- que se trabaja y en que orden ---------------------------------------------

Tramo = Literal["propuesta", "desempate", "resto"]


@dataclass(frozen=True)
class PropuestaEnVeto:
    """Fila de apply_queue (negative|harvest, pending_veto|released) vista por
    el lector. `destino` solo en harvest; `destino_ilegible` si la decision no
    trae inputs.goal.harvest.ad_group_id o ese ad group no esta en ad_entity
    (no revienta: la propuesta se avisa con ese motivo)."""

    cola_id: int
    decision_id: int
    kind: Literal["negative", "harvest"]
    origen: ClaveBusqueda
    destino: ClaveBusqueda | None
    destino_ilegible: bool
    vence_el: datetime


@dataclass(frozen=True)
class Unidad:
    """Unidad de trabajo del job: una busqueda-en-grupo con su economia ya
    leida y la razon por la que entra. Su identidad es `clave`; no hay UUID
    de solicitud que alguien tenga que recordar."""

    clave: ClaveBusqueda
    tramo: Tramo
    economia: Economia
    vence_el: datetime | None  # solo tramo 'propuesta'


@dataclass(frozen=True)
class Ajustes:
    """Config vigente ya validada. Sin defaults en codigo."""

    config_version_id: int
    tope_diario: int  # >= 0; 0 = senales solo con hechos de venta, cero TypeSafe
    min_clics: int  # >= 1; piso de la lista de gasto sin venta
    avisos: bool  # Telegram


@dataclass(frozen=True)
class Apagado:
    motivo: str  # "jev.senales ausente", "jev.tope_diario corrupto: 'mil'", ...


def ajustes_desde_settings(config_version_id: int, settings: object) -> Ajustes | Apagado:
    """Interruptor fail-closed. Encendido SOLO si:
      settings["jev.senales"] is True            (el JSON true; "true" no)
      settings["jev.tope_diario"] es int >= 0    (bool no cuenta como int)
      settings["jev.min_clics"]   es int >= 1
    `jev.avisos` is True enciende Telegram; cualquier otra cosa lo apaga sin
    apagar el job. Ausente o corrupto en las tres primeras -> Apagado(motivo)."""
    raise NotImplementedError


def planear(
    propuestas: tuple[PropuestaEnVeto, ...],
    economia: Mapping[ClaveBusqueda, Economia],
    con_senal_vigente: frozenset[ClaveBusqueda],
    cortes_aplicados: frozenset[ClaveBusqueda],
    ajustes: Ajustes,
) -> tuple[Unidad, ...]:
    """Seleccion y ORDEN, puros. Devuelve cada clave una sola vez.

    Entran:
      a. origen y destino de cada propuesta                     (tramo propuesta)
      b. candidatas de gasto sin venta: aqui.ordenes == 0, aqui.gasto > 0,
         aqui.clics >= ajustes.min_clics, termino no ASIN-like, sin corte
         aplicado por Orbit en esa clave
      c. toda clave con senal vigente que ya no cumple (b): se vuelve a sellar
         para que la pantalla no muestre "sin venta" de algo que ya vendio
    Tramo de (b) y (c): 'desempate' si leer() necesitaria a Jev (pasos 5 a 7:
    no vende en ninguno y nunca vendio aqui); 'resto' si los hechos ya deciden.

    Orden:
      1. propuestas por vence_el ascendente (destino antes que origen)
      2. desempate por gasto descendente, ALTERNANDO plataformas por rango
         (1a de MX, 1a de US, 2a de MX...): no se comparan MXN con USD
      3. resto, mismo criterio
    Desempate final por (plataforma, ad_group_id, termino): orden total, la
    misma entrada da el mismo plan."""
    raise NotImplementedError


# ===========================================================================
# B. app/jev_libro.py -- EL LIBRO DE JUICIOS (unico que escribe jev_par_evento)
# ===========================================================================


@dataclass(frozen=True)
class SinCupo:
    """El tope diario no deja pagar este par hoy. No es fallo ni evidencia."""


class Libro:
    """Juicio por par, GLOBAL por ClavePar (cambia la decision R4).

    Fuente de verdad del juicio de un par: el PRIMER evento 'resultado' con
    respuesta para su ClavePar, de cualquier revision, por (created_at, id).
    Las tablas son append-only, asi que ese primero no cambia nunca.

    Fuente de verdad del tope: count(jev_par_evento tipo='intencion' del dia
    UTC). La intencion se confirma ANTES del HTTP, asi que cuenta toda llamada
    hecha y toda llamada que pudo haberse hecho antes de una caida. No hay
    tabla de contador que mantener en sincronia.
    """

    def __init__(self, escritor, *, pedir, contrato, ahora: Callable[[], datetime]):
        raise NotImplementedError

    def juicio(
        self, termino: str, ficha: FichaVersion, *, lote: UUID, tope_diario: int
    ) -> Juicio | FalloProveedor | SinCupo:
        """Dame el juicio de este par; paga solo si hace falta y hay cupo.

        # 1. exito = primer exito GLOBAL de clave_de(termino, ficha, contrato)
        #    -> devolver Juicio (cero HTTP, cero filas)
        # 2. no cabe en el contrato (bytes) -> FalloProveedor("contexto_excedido")
        #    sin intencion: es determinista, no gasta cupo ni se reintenta pagando
        # 3. BEGIN; pg_advisory_xact_lock(hashtext('jev:cupo'));
        #    si intenciones_de_hoy >= tope_diario: ROLLBACK -> SinCupo
        #    INSERT intencion (revision_id = lote); COMMIT      <- antes del HTTP
        # 4. HTTP (sin transaccion abierta)
        # 5. INSERT resultado (respuesta o error); COMMIT
        # Un fallo NO se reintenta en la misma corrida (memo por clave).
        # Sin api key el job NO llama a este metodo (ver `correr`, paso 6): una
        # clave ausente no abre intenciones ni quema el tope.
        """
        raise NotImplementedError

    def pagar(
        self, termino: str, ficha: FichaVersion, *, revision: UUID
    ) -> Juicio | FalloProveedor:
        """Intencion -> HTTP -> resultado bajo `revision`, sin reutilizar y sin
        tope. Es lo que hoy hace AsesorAds a mano (_intencion/_resultado);
        el asesor manual pasa a llamar aqui y conserva SU politica de
        reutilizar solo dentro de su revision."""
        raise NotImplementedError

    def llamadas_de_hoy(self) -> int:
        raise NotImplementedError


# ===========================================================================
# C. app/jev_senales.py -- EL JOB Y LAS LECTURAS DE PANTALLA
#    Unico modulo que conoce jev_senal, jev_roster, jev_aviso, jev_aviso_entrega
#    y jev_corrida.
# ===========================================================================

MotivoCierre = Literal[
    "apagado",  # interruptor: cero filas, cero HTTP
    "ocupado",  # otra corrida tiene el candado: cero filas
    "completa",  # todas las unidades con sus pares
    "tope",  # se acabo el cupo; el resto quedo sellado con lo que habia
    "proveedor_caido",  # N fallos seguidos: se dejo de pagar, se siguio sellando
    "sin_api_key",  # corrida solo con hechos de venta
]


@dataclass(frozen=True)
class CierreCorrida:
    corrida_id: UUID | None  # None en apagado/ocupado (no se escribio nada)
    motivo: MotivoCierre
    unidades: int
    llamadas: int
    fallos: int
    senales_nuevas: int
    avisos_enviados: int


def correr(
    lector,
    escritor,
    *,
    ahora: datetime,
    aplicar: bool = False,
    pedir=None,
    avisar: Callable[[str], bool] | None = None,
    api_key: str = "",
) -> CierreCorrida:
    """UNICA funcion que paga a TypeSafe y escribe senales.

    `lector` (ORBIT_DSN_READ) lee el mundo; `escritor` (ORBIT_DSN_JEV, login
    miembro solo de app_jev) solo toca jev_*. Ningun dato cruza al reves.

    # 0. aplicar=False (seco, por omision): hace los pasos 1, 3 y 4, imprime el
    #    plan y cuantas llamadas pagaria, y termina SIN escribir ni llamar.
    # 1. ajustes = ajustes_desde_settings(config vigente leida con lector)
    #    Apagado -> CierreCorrida(None, "apagado") SIN abrir escritor.transaction
    # 2. pg_try_advisory_lock(hashtext('jev:senales')) en escritor; ocupado -> "ocupado"
    # 3. con lector en UNA transaccion REPEATABLE READ (foto coherente):
    #      propuestas  = _propuestas(lector, None)
    #      economia    = _economia(lector, ahora)   # windows.terminos_cortes por
    #                    ad group + historial propio sobre la subconsulta de
    #                    colapso de windows, indexado por clave
    #      aplicados   = claves con apply_queue.estado = 'applied'
    #      rosters     = captura perezosa por ad group (ver _roster)
    # 4. plan = planear(...)                                       [puro]
    # 5. lote = INSERT jev_revision(sujeto_tipo='lote', censos=resumen del plan,
    #           contrato, captured_at=ahora); COMMIT          <- antes del 1er HTTP
    # 6. por unidad, en orden:
    #      roster = _roster(grupo)       # INSERT jev_roster ON CONFLICT DO NOTHING
    #      pares  = [libro.juicio(termino, ficha, lote=lote, tope_diario=...)
    #                for miembro activo con ficha]   # se omite si ya no hay cupo,
    #                                                # no hay api_key o el
    #                                                # proveedor esta caido
    #      relevancia = Relevancia(componer(roster.censo, pares), ...)
    #      lectura    = leer(unidad.economia, relevancia)
    #      insumos_sha256 = hash canonico de: REGLA_VERSION, contrato, roster.sha256,
    #             roster probado o no, ventana, numeros de venta (aqui, otros,
    #             historial), relevancia con juicio_ids y motivos, y la FECHA UTC
    #             de hoy (asi cada dia hay fila nueva aunque nada cambie)
    #      valida_hasta = min(ahora + 36 h, menor revisar_antes_de de las fichas)
    #      INSERT jev_senal ... ON CONFLICT (ad_group_id, termino_sha256,
    #             insumos_sha256) DO NOTHING; COMMIT      <- progreso por unidad
    # 7. si ajustes.avisos y avisar: por propuesta cuya (cola_id, lectura) no
    #    tiene fila en jev_aviso_entrega:
    #      INSERT jev_aviso(cola_id, lectura, texto, senal ids)
    #             ON CONFLICT (cola_id, lectura) DO NOTHING; COMMIT   <- antes de enviar
    #      si avisar(texto del jev_aviso guardado):
    #          INSERT jev_aviso_entrega(aviso_id); COMMIT
    #    Un aviso sin entrega se reintenta en la corrida siguiente con el MISMO
    #    texto guardado; canal inactivo no cuenta como entrega.
    # 8. INSERT jev_corrida(lote, 'fin', motivo, resumen); COMMIT
    #    (el 'inicio' se inserta junto con el lote, en el paso 5)
    """
    raise NotImplementedError


def main(argv: list[str]) -> int:
    """`python -m app.cli jev-senales`. Salidas: 0 corrio, apagado u ocupado;
    1 fallo (mensaje con scrub); 2 falta ORBIT_DSN_READ u ORBIT_DSN_JEV.
    Imprime UNA linea de latido: `jev-senales motivo=... unidades=N llamadas=N
    fallos=N senales=N avisos=N` (no hay logging.basicConfig en app/)."""
    raise NotImplementedError


# --- lecturas de pantalla: SOLO SELECT, conexion de lectura, cero HTTP -------------


@dataclass(frozen=True)
class SenalVista:
    """Una fila de jev_senal_vigente (la mas reciente de su clave) con todo lo
    que la pantalla pinta. `vigente` lo calcula la VISTA SQL, no Python."""

    senal_id: UUID
    clave: ClaveBusqueda
    lectura: Lectura
    motivos: frozenset[str]
    economia: Economia
    relevancia_texto: Literal["corresponde", "ajena", "sin_veredicto", "no_evaluada"]
    satisfacen: int
    evaluados: int
    miembros: int
    productos_ok: tuple[int, ...]  # para desplegar que productos dijeron "si"
    regla_version: int
    roster_probado: bool
    motivos_roster: frozenset[MotivoSinProbar]
    calculada_el: datetime
    valida_hasta: datetime
    vigente: bool

    def como_dict(self) -> dict:
        raise NotImplementedError


@dataclass(frozen=True)
class SenalPropuesta:
    origen: SenalVista | None  # None = el job todavia no la sello
    destino: SenalVista | None  # solo harvest
    destino_ilegible: bool

    def como_dict(self) -> dict:
        raise NotImplementedError


def de_propuestas(conn, decision_ids: Iterable[int]) -> dict[int, SenalPropuesta]:
    """GET /cortes. Resuelve origen y destino con la MISMA consulta que usa el
    job (`_propuestas`) y trae la senal mas reciente de cada clave."""
    raise NotImplementedError


@dataclass(frozen=True)
class PantallaGasto:
    plataforma: PlataformaAmazon
    calculado_el: datetime | None  # cierre de la ultima corrida
    filas: tuple[SenalVista, ...]  # aqui.ordenes == 0 y aqui.gasto > 0, por gasto desc
    totales: Mapping[Lectura, tuple[int, Decimal]]  # busquedas y gasto por lectura


def gasto_sin_venta(conn, *, plataforma: PlataformaAmazon) -> PantallaGasto:
    """GET /gasto-sin-venta."""
    raise NotImplementedError


@dataclass(frozen=True)
class SaludJev:
    interruptor: Ajustes | Apagado
    ultima_corrida: CierreCorrida | None
    ultima_inicio: datetime | None
    ultima_sin_cierre: bool  # se cayo o sigue corriendo
    llamadas_hoy: int
    pendientes_de_jev: int  # senales vigentes cuyo veredicto falta por cupo
    grupos_con_gasto: int
    grupos_probados: int
    motivos_sin_probar: Mapping[MotivoSinProbar, int]
    ajena_impedida_por_abstencion: int  # todo no_satisface salvo abstenciones o fallos
    avisos_sin_entrega: int
    fichas_por_vencer_14d: int


def salud(conn, *, ahora: datetime) -> SaludJev | None:
    """Bloque `jev` de GET /api/dashboard/salud. None + warning si falla
    (patron _spapi_de): la pantalla no muere."""
    raise NotImplementedError


# ===========================================================================
# D. app/jev_vista.py (se agrega) -- TEXTOS PUROS
# ===========================================================================


def texto_aviso(
    propuesta: PropuestaEnVeto,
    senal: SenalPropuesta,
    nombre_grupo: Mapping[int, str],
) -> str:
    """Texto plano del aviso de Telegram de UNA propuesta. Siempre dice la
    fecha y hora UTC en que se aplica si el dueno no hace nada (el aviso no
    detiene el reloj). No dice "faltan n horas": el texto se guarda en
    jev_aviso y puede reenviarse tal cual en la corrida siguiente. Nunca
    dice "ninguno" ni "ajena" sin RosterProbado: ese caso no llega aqui porque
    `leer` no lo produce; el texto solo traduce `lectura`."""
    raise NotImplementedError


Banda = Literal["ninguno", "pocos", "una_parte", "todos", "sin_dato"]


def banda_de_proporcion(satisfacen: int, evaluados: int) -> Banda:
    """SOLO PRESENTACION, para agrupar y ordenar la pantalla de gasto sin
    venta: ninguno (0), pocos (hasta 15%), todos (95% o mas), una_parte (el
    resto), sin_dato (evaluados == 0). No se guarda y ningun efecto la lee: los
    cortes son de pantalla, no umbrales de aceptacion. La unica clase que un
    efecto puede leer es `lectura`."""
    raise NotImplementedError


# ===========================================================================
# E. Ingesta de estructura (app/ads/*) -- lo que hoy NO registra
# ===========================================================================


def huella_anuncios(ad_ids_externos: Iterable[str]) -> str:
    """app/ads/structure_plan.py (puro). sha256 de los adId ordenados y unidos
    por salto de linea. La usan la ingesta (sobre el payload) y jev_senales
    (sobre la base): una definicion, dos lados de la comparacion."""
    raise NotImplementedError


@dataclass(frozen=True)
class ListadoProbado:
    """app/ads/structure_api.py. Lo devuelve `listar_con_prueba`, nueva.
    `listar_todo` conserva su firma como envoltura (`listar_con_prueba(...)
    .items`) para sus otros consumidores. `total_declarado` es el
    totalResults de la PRIMERA pagina que lo declare como int (comprobado en
    produccion: viene en la primera), cotejado al final contra el acumulado;
    None si Amazon no lo declaro en ninguna."""

    items: list[dict]
    total_declarado: int | None
