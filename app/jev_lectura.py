"""Nucleo PURO de JEV ADS 02: la senal por busqueda-en-grupo.

Tres hechos, una lectura: roster (que se anuncia), relevancia (que
corresponde) y economia (que hizo con el dinero); `leer` es funcion pura
de los tres. Sin red, sin base, sin reloj (candado AST en
tests/test_jev_ads.py). `MAX_EDAD_SYNC` no se importa de
app.optimizer.windows: llega como parametro a `probar_roster`.

Contrato: docs/evidencia/jev-ads-02/diseno/bosquejo.py (seccion A).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.jev_ads import (
    ESTADOS_ACTIVOS,
    CensoCongelado,
    FichaVersion,
    HayCompatible,
    Indeterminado,
    NingunoCompatible,
    PlataformaAmazon,
    RelevanciaConjunto,
    _con_estado_ausente,
    _solo_no_activo,
    es_asin_like,
)

Lectura = Literal[
    "vendio_aqui",
    "vende_en_otro",
    "relevante_sin_venta",
    "ajena",
    "sin_lectura",
]
LecturaDestino = Literal["destino_corresponde", "destino_ajeno", "sin_lectura"]

MotivoSinLectura = Literal[
    "dato_de_venta_faltante",
    "sin_observaciones",
    "jev_sin_veredicto",
    "jev_no_evaluada",
]

REGLA_VERSION = 1


@dataclass(frozen=True)
class ClaveBusqueda:
    """Una busqueda en un ad group: plataforma, id interno y termino literal."""

    plataforma: PlataformaAmazon
    ad_group_id: int
    termino: str


@dataclass(frozen=True)
class Gasto:
    """Sumas de una busqueda en un ad group sobre un rango, colapsadas a la
    ultima observacion por dia. None = dato faltante en el rango."""

    desde: date
    hasta: date
    clics: int | None
    gasto: Decimal | None
    ordenes: int | None


@dataclass(frozen=True)
class VentaEnOtroGrupo:
    """Otro ad group de la misma plataforma donde la misma busqueda tiene
    ordenes > 0 en su ventana madura. Solo existe con venta afirmada."""

    ad_group_id: int
    en_ventana: Gasto


@dataclass(frozen=True)
class Historial:
    """Todo lo observado de una busqueda en un ad group, colapsado por dia.
    No se anula por un dia sin dato: suma las ordenes conocidas y cuenta los
    dias sin dato aparte. Una venta no se borra por un NULL."""

    desde: date
    hasta: date
    clics: int | None
    ordenes_conocidas: int
    dias_sin_dato: int


@dataclass(frozen=True)
class Economia:
    """Hecho 3. "No vende" solo sobre la ventana madura de cortes; "vendio
    aqui" sobre todo lo observado; "vende en otro" en ventana madura ajena.
    `aqui` None = el grupo no tiene observaciones."""

    moneda: Literal["MXN", "USD"]
    aqui: Gasto | None
    otros_que_venden: tuple[VentaEnOtroGrupo, ...]
    otros_sin_venta: int
    otros_sin_dato: int
    historial: Historial | None
    datos_hasta: datetime | None


MotivoSinProbar = Literal[
    "sin_corrida_reciente",
    "plataforma_no_listada",
    "listado_sin_total",
    "listado_no_cuadra",
    "grupo_no_listado",
    "anuncios_descartados",
    "anuncios_sin_grupo",
    "huella_distinta",
    "estado_ausente",
    "anuncio_sin_producto",
]


@dataclass(frozen=True)
class ActaDeListado:
    """Lo que la ingesta deja escrito de una corrida para una plataforma y
    un ad group. `del_grupo` None = el grupo no tiene fila en la corrida."""

    ingest_run_id: int
    finished_at: datetime
    ad_groups_declarados: int | None
    ad_groups_recibidos: int
    declarados: int | None
    recibidos: int
    sin_grupo: int
    del_grupo: ActaDeGrupo | None


@dataclass(frozen=True)
class ActaDeGrupo:
    vivos: int
    huella: str
    descartados: int


@dataclass(frozen=True)
class RosterProbado:
    """Se conocen TODOS los productos anunciados del ad group. Solo
    `probar_roster` lo construye."""

    ingest_run_id: int
    listado_de: datetime
    anuncios_vivos: int


@dataclass(frozen=True)
class RosterSinProbar:
    motivos: frozenset[MotivoSinProbar]


PruebaRoster = RosterProbado | RosterSinProbar


def _motivos_de_acta(
    acta: ActaDeListado, *, huella_en_base: str, ahora: datetime, max_edad
) -> set[MotivoSinProbar]:
    motivos: set[MotivoSinProbar] = set()
    if ahora - acta.finished_at > max_edad:
        motivos.add("sin_corrida_reciente")
    declarados = (acta.ad_groups_declarados, acta.declarados)
    recibidos = (acta.ad_groups_recibidos, acta.recibidos)
    if any(type(d) is not int for d in declarados):
        motivos.add("listado_sin_total")
    elif any(d != r for d, r in zip(declarados, recibidos, strict=True)):
        motivos.add("listado_no_cuadra")
    if acta.sin_grupo > 0:
        motivos.add("anuncios_sin_grupo")
    grupo = acta.del_grupo
    if grupo is None:
        motivos.add("grupo_no_listado")
    else:
        if grupo.descartados > 0:
            motivos.add("anuncios_descartados")
        if grupo.huella != huella_en_base:
            motivos.add("huella_distinta")
    return motivos


def _motivos_de_censo(censo: CensoCongelado) -> set[MotivoSinProbar]:
    motivos: set[MotivoSinProbar] = set()
    if any(_con_estado_ausente(m) for m in censo.miembros):
        motivos.add("estado_ausente")
    if any(
        m.producto_id is None and any(e.status in ESTADOS_ACTIVOS for e in m.estados)
        for m in censo.miembros
    ):
        motivos.add("anuncio_sin_producto")
    return motivos


def probar_roster(
    censo: CensoCongelado,
    acta: ActaDeListado | None,
    *,
    huella_en_base: str,
    ahora: datetime,
    max_edad,
) -> PruebaRoster:
    """Regla de roster probado. Falla cerrado: cada condicion fallida agrega
    su motivo y cualquier duda es SinProbar. `max_edad` es
    `windows.MAX_EDAD_SYNC` (48 h; exactas todavia valen), que este modulo
    no puede importar. Sin acta no hay plataforma listada."""
    motivos = _motivos_de_censo(censo)
    if acta is None:
        motivos.add("plataforma_no_listada")
        return RosterSinProbar(motivos=frozenset(motivos))
    motivos |= _motivos_de_acta(acta, huella_en_base=huella_en_base, ahora=ahora, max_edad=max_edad)
    if motivos:
        return RosterSinProbar(motivos=frozenset(motivos))
    grupo = acta.del_grupo
    assert grupo is not None
    return RosterProbado(
        ingest_run_id=acta.ingest_run_id,
        listado_de=acta.finished_at,
        anuncios_vivos=grupo.vivos,
    )


def anunciados_hoy(censo: CensoCongelado) -> CensoCongelado:
    """Quita a los miembros solo-no-activos (todos sus anuncios con estado
    conocido y no activo): el complemento exacto de `_solo_no_activo`, la
    misma regla con que `componer` arma su universo. No cambia `exhaustivo`."""
    return replace(
        censo,
        miembros=tuple(m for m in censo.miembros if not _solo_no_activo(m.estados)),
    )


@dataclass(frozen=True)
class Roster:
    """Hecho 1 congelado: censo pasado por `anunciados_hoy`, fichas resueltas
    y su prueba. `sha256` identifica miembros + fichas, sin la prueba."""

    plataforma: PlataformaAmazon
    ad_group_id: int
    censo: CensoCongelado
    fichas: Mapping[UUID, FichaVersion]
    prueba: PruebaRoster
    sha256: str

    def __post_init__(self) -> None:
        if self.censo.exhaustivo != isinstance(self.prueba, RosterProbado):
            raise ValueError("roster exhaustivo exige RosterProbado y viceversa")


@dataclass(frozen=True)
class NoEvaluada:
    """No se le pregunto a Jev por ningun par: sin cupo, tope 0 o sin api
    key. Distinto de Indeterminado y jamas evidencia."""

    motivo: Literal["sin_cupo", "tope_cero", "sin_api_key", "proveedor_caido"]


@dataclass(frozen=True)
class Relevancia:
    """Hecho 2 compuesto para un ad group. Los conteos van SIEMPRE, tambien
    en Indeterminado; se evalua el grupo completo."""

    conjunto: RelevanciaConjunto | NoEvaluada
    satisfacen: int
    evaluados: int
    miembros: int
    productos_ok: tuple[int, ...]
    juicio_ids: tuple[UUID, ...]


def leer(economia: Economia, relevancia: Relevancia) -> tuple[Lectura, frozenset[MotivoSinLectura]]:
    """LA regla. Orden estricto; el primero que aplica gana: las ventas
    mandan y Jev solo desempata cuando nada vende en ningun grupo."""
    historial = economia.historial
    if historial is not None and historial.ordenes_conocidas > 0:
        return "vendio_aqui", frozenset()
    if economia.otros_que_venden:
        return "vende_en_otro", frozenset()
    aqui = economia.aqui
    if aqui is None:
        return "sin_lectura", frozenset({"sin_observaciones"})
    if aqui.ordenes is None or economia.otros_sin_dato > 0:
        return "sin_lectura", frozenset({"dato_de_venta_faltante"})
    conjunto = relevancia.conjunto
    if isinstance(conjunto, HayCompatible):
        return "relevante_sin_venta", frozenset()
    if isinstance(conjunto, NingunoCompatible):
        return "ajena", frozenset()
    if isinstance(conjunto, Indeterminado):
        return "sin_lectura", frozenset({"jev_sin_veredicto"})
    if isinstance(conjunto, NoEvaluada):
        return "sin_lectura", frozenset({"jev_no_evaluada"})
    raise ValueError(f"conjunto fuera del contrato: {conjunto!r}")


def leer_destino(
    relevancia: Literal["corresponde", "ajena", "sin_veredicto", "no_evaluada"],
) -> LecturaDestino:
    """Harvest: lo que al dueno le importa del destino. Solo presentacion:
    se deriva de la relevancia guardada al pintar y al avisar."""
    return {
        "corresponde": "destino_corresponde",
        "ajena": "destino_ajeno",
        "sin_veredicto": "sin_lectura",
        "no_evaluada": "sin_lectura",
    }[relevancia]


Tramo = Literal["propuesta", "desempate", "resto"]


@dataclass(frozen=True)
class PropuestaEnVeto:
    """Fila de apply_queue vista por el lector. `destino` solo en harvest."""

    cola_id: int
    decision_id: int
    kind: Literal["negative", "harvest"]
    modo: Literal["live", "shadow"]
    origen: ClaveBusqueda
    destino: ClaveBusqueda | None
    destino_ilegible: bool
    vence_el: datetime


@dataclass(frozen=True)
class Unidad:
    """Unidad de trabajo del job: una busqueda-en-grupo con su economia ya
    leida. Su identidad es `clave`; `vence_el` solo en tramo propuesta."""

    clave: ClaveBusqueda
    tramo: Tramo
    economia: Economia
    vence_el: datetime | None


@dataclass(frozen=True)
class Ajustes:
    """Config vigente ya validada. Sin defaults en codigo."""

    config_version_id: int
    tope_diario: int
    min_clics: int
    avisos: bool


@dataclass(frozen=True)
class Apagado:
    motivo: str


_FALTA = object()


def ajustes_desde_settings(config_version_id: int, settings: object) -> Ajustes | Apagado:
    """Interruptor fail-closed. Enciende SOLO con el JSON `true` en
    `jev.senales`, entero >= 0 en `jev.tope_diario` y entero >= 1 en
    `jev.min_clics` (un bool no es entero). `jev.avisos` distinto de `true`
    deja avisos en False sin apagar el job."""
    if not isinstance(settings, Mapping):
        return Apagado("settings sin forma de objeto")
    senales = settings.get("jev.senales", _FALTA)
    if senales is _FALTA:
        return Apagado("jev.senales ausente")
    if senales is not True:
        return Apagado(f"jev.senales corrupto: {senales!r}")
    tope = settings.get("jev.tope_diario", _FALTA)
    if tope is _FALTA:
        return Apagado("jev.tope_diario ausente")
    if type(tope) is not int or tope < 0:
        return Apagado(f"jev.tope_diario corrupto: {tope!r}")
    minimo = settings.get("jev.min_clics", _FALTA)
    if minimo is _FALTA:
        return Apagado("jev.min_clics ausente")
    if type(minimo) is not int or minimo < 1:
        return Apagado(f"jev.min_clics corrupto: {minimo!r}")
    return Ajustes(
        config_version_id=config_version_id,
        tope_diario=tope,
        min_clics=minimo,
        avisos=settings.get("jev.avisos", False) is True,
    )


def _orden_clave(clave: ClaveBusqueda) -> tuple[str, int, str]:
    return (clave.plataforma, clave.ad_group_id, clave.termino)


def _necesita_a_jev(eco: Economia) -> bool:
    """Espejo de `leer` 1-4: True cuando los hechos no deciden y la lectura
    necesitaria a Jev. En planificacion no se distingue el paso 8."""
    if eco.historial is not None and eco.historial.ordenes_conocidas > 0:
        return False
    if eco.otros_que_venden:
        return False
    if eco.aqui is None or eco.aqui.ordenes is None:
        return False
    return eco.otros_sin_dato == 0


def _es_candidata(
    clave: ClaveBusqueda, eco: Economia, ajustes: Ajustes, cortes: frozenset[ClaveBusqueda]
) -> bool:
    aqui = eco.aqui
    if aqui is None or aqui.ordenes != 0:
        return False
    if aqui.gasto is None or aqui.gasto <= 0:
        return False
    if aqui.clics is None or aqui.clics < ajustes.min_clics:
        return False
    if es_asin_like(clave.termino):
        return False
    return clave not in cortes


def _orden_gasto(unidad: Unidad):
    aqui = unidad.economia.aqui
    gasto = aqui.gasto if aqui is not None else None
    if gasto is None:
        return (True, 0, _orden_clave(unidad.clave))
    return (False, -gasto, _orden_clave(unidad.clave))


def _por_plataforma(unidades: list[Unidad]) -> list[Unidad]:
    """Cada plataforma ordenada por gasto desc, alternadas por rango: no se
    comparan MXN con USD."""
    grupos: dict[str, list[Unidad]] = {}
    for unidad in unidades:
        grupos.setdefault(unidad.clave.plataforma, []).append(unidad)
    for grupo in grupos.values():
        grupo.sort(key=_orden_gasto)
    salida: list[Unidad] = []
    ronda = 0
    while any(len(grupo) > ronda for grupo in grupos.values()):
        for plataforma in sorted(grupos):
            if len(grupos[plataforma]) > ronda:
                salida.append(grupos[plataforma][ronda])
        ronda += 1
    return salida


def planear(
    propuestas: tuple[PropuestaEnVeto, ...],
    economia: Mapping[ClaveBusqueda, Economia],
    con_senal_vigente: frozenset[ClaveBusqueda],
    cortes_aplicados: frozenset[ClaveBusqueda],
    ajustes: Ajustes,
) -> tuple[Unidad, ...]:
    """Seleccion y ORDEN, puros. Cada clave una sola vez: primero propuestas
    por vencimiento (destino antes que origen), luego desempate y resto por
    gasto alternando plataformas. La misma entrada da el mismo plan."""
    unidades: dict[ClaveBusqueda, Unidad] = {}
    pares = []
    for propuesta in propuestas:
        if propuesta.destino is not None:
            pares.append((propuesta.vence_el, 0, propuesta.destino))
        pares.append((propuesta.vence_el, 1, propuesta.origen))
    pares.sort(key=lambda p: (p[0], p[1], _orden_clave(p[2])))
    for vence_el, _, clave in pares:
        eco = economia.get(clave)
        if eco is None or clave in unidades:
            continue
        unidades[clave] = Unidad(clave=clave, tramo="propuesta", economia=eco, vence_el=vence_el)
    candidatas = sorted(set(economia) | set(con_senal_vigente), key=_orden_clave)
    for clave in candidatas:
        if clave in unidades:
            continue
        eco = economia.get(clave)
        if eco is None:
            continue
        if not _es_candidata(clave, eco, ajustes, cortes_aplicados) and (
            clave not in con_senal_vigente
        ):
            continue
        tramo = "desempate" if _necesita_a_jev(eco) else "resto"
        unidades[clave] = Unidad(clave=clave, tramo=tramo, economia=eco, vence_el=None)
    por_tramo: dict[str, list[Unidad]] = {"propuesta": [], "desempate": [], "resto": []}
    for unidad in unidades.values():
        por_tramo[unidad.tramo].append(unidad)
    return tuple(
        por_tramo["propuesta"]
        + _por_plataforma(por_tramo["desempate"])
        + _por_plataforma(por_tramo["resto"])
    )


@dataclass(frozen=True)
class SenalVista:
    """Una fila de senal vigente con todo lo que la pantalla pinta. La llena
    `jev_senales`; vive aqui porque `jev_vista` no puede importar IO."""

    senal_id: UUID
    clave: ClaveBusqueda
    lectura: Lectura
    motivos: frozenset[str]
    economia: Economia
    relevancia_texto: Literal["corresponde", "ajena", "sin_veredicto", "no_evaluada"]
    satisfacen: int
    evaluados: int
    miembros: int
    productos_ok: tuple[int, ...]
    regla_version: int
    roster_probado: bool
    motivos_roster: frozenset[MotivoSinProbar]
    calculada_el: datetime
    valida_hasta: datetime
    vigente: bool


@dataclass(frozen=True)
class SenalPropuesta:
    """Origen y destino de una propuesta con sus senales. None = el job
    todavia no la sello."""

    origen: SenalVista | None
    destino: SenalVista | None
    destino_ilegible: bool
