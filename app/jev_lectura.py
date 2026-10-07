"""Nucleo PURO de JEV ADS 02: la senal por busqueda-en-grupo.

Tres hechos, una lectura: roster (que se anuncia), relevancia (que
corresponde) y economia (que hizo con el dinero); `leer` es funcion pura
de los tres. Sin red, sin base, sin reloj (candado AST en
tests/test_jev_ads.py). `MAX_EDAD_SYNC` no se importa de
app.optimizer.windows: llega como parametro a `probar_roster`.

Contrato: docs/evidencia/jev-ads-02/diseno/bosquejo.py (seccion A).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
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

    def como_dict(self) -> dict:
        """Contrato de pantalla (S.5): importes como texto, instantes en ISO
        UTC, listas ordenadas. Sin plantillas: esto es lo que la UI pinta."""
        aqui = self.economia.aqui
        historial = self.economia.historial
        return {
            "senal_id": str(self.senal_id),
            "clave": {
                "plataforma": self.clave.plataforma,
                "ad_group_id": self.clave.ad_group_id,
                "termino": self.clave.termino,
            },
            "lectura": self.lectura,
            "motivos": sorted(self.motivos),
            "economia": {
                "moneda": self.economia.moneda,
                "aqui": (
                    None
                    if aqui is None
                    else {
                        "desde": aqui.desde.isoformat(),
                        "hasta": aqui.hasta.isoformat(),
                        "clics": aqui.clics,
                        "gasto": None if aqui.gasto is None else str(aqui.gasto),
                        "ordenes": aqui.ordenes,
                    }
                ),
                "otros_que_venden": [
                    {
                        "ad_group_id": v.ad_group_id,
                        "desde": v.en_ventana.desde.isoformat(),
                        "hasta": v.en_ventana.hasta.isoformat(),
                        "clics": v.en_ventana.clics,
                        "gasto": (None if v.en_ventana.gasto is None else str(v.en_ventana.gasto)),
                        "ordenes": v.en_ventana.ordenes,
                    }
                    for v in self.economia.otros_que_venden
                ],
                "otros_sin_venta": self.economia.otros_sin_venta,
                "otros_sin_dato": self.economia.otros_sin_dato,
                "historial": (
                    None
                    if historial is None
                    else {
                        "desde": historial.desde.isoformat(),
                        "hasta": historial.hasta.isoformat(),
                        "clics": historial.clics,
                        "ordenes_conocidas": historial.ordenes_conocidas,
                        "dias_sin_dato": historial.dias_sin_dato,
                    }
                ),
                "datos_hasta": (
                    None
                    if self.economia.datos_hasta is None
                    else self.economia.datos_hasta.astimezone(UTC).isoformat()
                ),
            },
            "relevancia": self.relevancia_texto,
            "satisfacen": self.satisfacen,
            "evaluados": self.evaluados,
            "miembros": self.miembros,
            "productos_ok": list(self.productos_ok),
            "regla_version": self.regla_version,
            "roster_probado": self.roster_probado,
            "motivos_roster": sorted(self.motivos_roster),
            "calculada_el": self.calculada_el.astimezone(UTC).isoformat(),
            "valida_hasta": self.valida_hasta.astimezone(UTC).isoformat(),
            "vigente": self.vigente,
        }


@dataclass(frozen=True)
class SenalPropuesta:
    """Origen y destino de una propuesta con sus senales. None = el job
    todavia no la sello."""

    origen: SenalVista | None
    destino: SenalVista | None
    destino_ilegible: bool

    def como_dict(self) -> dict:
        return {
            "origen": None if self.origen is None else self.origen.como_dict(),
            "destino": None if self.destino is None else self.destino.como_dict(),
            "destino_ilegible": self.destino_ilegible,
        }


@dataclass(frozen=True)
class PantallaGasto:
    """GET /gasto-sin-venta (S.5): senales vigentes con gasto y cero ordenes,
    por gasto descendente, con totales por lectura. La llena `jev_salud`."""

    plataforma: PlataformaAmazon
    calculado_el: datetime | None  # cierre de la ultima corrida; None sin corridas
    filas: tuple[SenalVista, ...]  # aqui.ordenes == 0 y aqui.gasto > 0
    totales: Mapping[Lectura, tuple[int, Decimal]]  # busquedas y gasto por lectura

    def como_dict(self) -> dict:
        return {
            "plataforma": self.plataforma,
            "calculado_el": (
                None if self.calculado_el is None else self.calculado_el.astimezone(UTC).isoformat()
            ),
            "filas": [fila.como_dict() for fila in self.filas],
            "totales": {
                lectura: {"busquedas": n, "gasto": str(gasto)}
                for lectura, (n, gasto) in self.totales.items()
            },
        }


def _json_dict(valor: object) -> dict:
    """JSONB de psycopg (dict) o su forma serializada (str); None es {}.

    Falla cerrado ante otro tipo: una pantalla no inventa contenido."""

    def _falla() -> dict:
        raise ValueError(f"json de fila ilegible: {type(valor).__name__}")

    if valor is None:
        return {}
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        decodificado = json.loads(valor)
        return decodificado if isinstance(decodificado, dict) else _falla()
    return _falla()


def _json_lista(valor: object) -> list:
    """Como `_json_dict` para los JSONB que son listas (`otros_que_venden`)."""

    def _falla() -> list:
        raise ValueError(f"json de fila ilegible: {type(valor).__name__}")

    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    if isinstance(valor, str):
        decodificado = json.loads(valor)
        return decodificado if isinstance(decodificado, list) else _falla()
    return _falla()


def _como_fecha(valor: object) -> date | None:
    if valor is None or isinstance(valor, date):
        return valor  # type: ignore[return-value]
    if isinstance(valor, str):
        return date.fromisoformat(valor)
    raise ValueError(f"fecha de fila ilegible: {valor!r}")


def _como_instante(valor: object) -> datetime | None:
    if valor is None or isinstance(valor, datetime):
        return valor  # type: ignore[return-value]
    if isinstance(valor, str):
        return datetime.fromisoformat(valor)
    raise ValueError(f"instante de fila ilegible: {valor!r}")


def _como_dinero(valor: object) -> Decimal | None:
    if valor is None or isinstance(valor, Decimal):
        return valor  # type: ignore[return-value]
    if isinstance(valor, (int, str)):
        return Decimal(str(valor))
    if isinstance(valor, float):
        return Decimal(repr(valor))
    raise ValueError(f"importe de fila ilegible: {valor!r}")


def relevancia_de_texto(
    texto: str,
    *,
    productos_ok: tuple[int, ...],
    evaluados: int,
    miembros: int,
    motivos_jev: tuple[str, ...],
) -> Relevancia:
    """Reconstruye la relevancia guardada en `jev_senal` (S.5, solo lectura).

    Inversa exacta de `_texto_relevancia` del job: `corresponde` guarda
    `productos_ok`/`evaluados`/`miembros`; `sin_veredicto` guarda sus motivos
    en `motivos_jev`; `no_evaluada` guarda su motivo en `motivos_jev[0]`
    (una fila real siempre lo trae; ausente es `sin_cupo`)."""
    conjunto: RelevanciaConjunto | NoEvaluada
    if texto == "corresponde":
        conjunto = HayCompatible(tuple(productos_ok), evaluados, miembros)
    elif texto == "ajena":
        conjunto = NingunoCompatible(miembros)
    elif texto == "sin_veredicto":
        conjunto = Indeterminado(frozenset(motivos_jev))
    elif texto == "no_evaluada":
        motivo = motivos_jev[0] if motivos_jev else "sin_cupo"
        conjunto = NoEvaluada(motivo)  # type: ignore[arg-type]
    else:
        raise ValueError(f"relevancia guardada ilegible: {texto!r}")
    return Relevancia(conjunto, len(productos_ok), evaluados, miembros, tuple(productos_ok), ())


def vista_de_dict(fila: Mapping[str, object]) -> SenalVista:
    """Una fila de `jev_senal` (+`vigente` de la vista) a `SenalVista` (S.5).

    Puro: recibe el dict ya normalizado, sin tocar la base. `juicio_ids` no
    viaja a la pantalla (son rastro, no lectura).

    `otros_sin_venta` NO se guarda en la fila y se expone en 0: ninguna
    pantalla lo lee y `leer` no lo usa (hay prueba que lo fija); inventar una
    cuenta seria peor que declarar su ausencia."""
    aqui = None
    if fila.get("ventana_inicio") is not None:
        aqui = Gasto(
            desde=_como_fecha(fila["ventana_inicio"]),  # type: ignore[arg-type]
            hasta=_como_fecha(fila["ventana_fin"]),  # type: ignore[arg-type]
            clics=fila.get("clics"),  # type: ignore[arg-type]
            gasto=_como_dinero(fila.get("gasto")),
            ordenes=fila.get("ordenes"),  # type: ignore[arg-type]
        )
    otros = tuple(
        VentaEnOtroGrupo(
            ad_group_id=extra["ad_group_id"],
            en_ventana=Gasto(
                desde=_como_fecha(extra["desde"]),  # type: ignore[arg-type]
                hasta=_como_fecha(extra["hasta"]),  # type: ignore[arg-type]
                clics=extra.get("clics"),
                gasto=_como_dinero(extra.get("gasto")),
                ordenes=extra.get("ordenes"),
            ),
        )
        for extra in _json_lista(fila.get("otros_que_venden"))
    )
    crudo_historial = fila.get("historial")
    historial = None
    if crudo_historial is not None:
        decodificado = _json_dict(crudo_historial)
        historial = Historial(
            desde=_como_fecha(decodificado["desde"]),  # type: ignore[arg-type]
            hasta=_como_fecha(decodificado["hasta"]),  # type: ignore[arg-type]
            clics=decodificado.get("clics"),
            ordenes_conocidas=decodificado["ordenes_conocidas"],
            dias_sin_dato=decodificado["dias_sin_dato"],
        )
    prueba = _json_dict(fila.get("roster_prueba"))
    if prueba.get("probado"):
        motivos_roster: frozenset = frozenset()
    else:
        motivos_roster = frozenset(prueba.get("motivos", []))
    productos_ok = tuple(fila.get("productos_ok") or ())
    return SenalVista(
        senal_id=fila["senal_id"],  # type: ignore[arg-type]
        clave=ClaveBusqueda(
            plataforma=fila["plataforma"],  # type: ignore[arg-type]
            ad_group_id=fila["ad_group_id"],  # type: ignore[arg-type]
            termino=fila["termino"],  # type: ignore[arg-type]
        ),
        lectura=fila["lectura"],  # type: ignore[arg-type]
        motivos=frozenset(fila.get("motivos_lectura") or ()),
        economia=Economia(
            moneda=fila["moneda"],  # type: ignore[arg-type]
            aqui=aqui,
            otros_que_venden=otros,  # type: ignore[arg-type]
            otros_sin_venta=0,
            otros_sin_dato=fila.get("otros_sin_dato", 0),  # type: ignore[arg-type]
            historial=historial,
            datos_hasta=_como_instante(fila.get("datos_hasta")),
        ),
        relevancia_texto=fila["relevancia"],  # type: ignore[arg-type]
        satisfacen=len(productos_ok),
        evaluados=fila.get("evaluados", 0),  # type: ignore[arg-type]
        miembros=fila.get("miembros", 0),  # type: ignore[arg-type]
        productos_ok=productos_ok,  # type: ignore[arg-type]
        regla_version=fila.get("regla_version", REGLA_VERSION),  # type: ignore[arg-type]
        roster_probado=bool(fila.get("roster_probado")),
        motivos_roster=motivos_roster,  # type: ignore[arg-type]
        calculada_el=_como_instante(fila["created_at"]),  # type: ignore[arg-type]
        valida_hasta=_como_instante(fila["valida_hasta"]),  # type: ignore[arg-type]
        vigente=bool(fila.get("vigente")),
    )
