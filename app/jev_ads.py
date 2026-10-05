"""Nucleo PURO del asesor Jev para Ads (JEV ADS 01): tipos, `componer`,
sujetos y serializacion del censo.

El diseno manda: docs/superpowers/specs/2026-10-03-jev-ads-design.md. Este
modulo no hace IO (candado AST en tests/test_jev_ads.py); la IO vive en
`app/jev_asesor.py` y `app/jev_libro.py`, y la presentacion pura en
`app/jev_vista.py` (R14).

Unidad de juicio: TERMINO LITERAL + VERSION DE FICHA. Reglas de `componer`:
un compatible permite HayCompatible con cobertura visible; NingunoCompatible
exige universo no vacio, exhaustivo, fichas de todos y `no_satisface` en
cada par; el resto es Indeterminado con motivos (vacio no prueba exclusion,
un fallo jamas es incompatibilidad); no se multiplican probabilidades.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

Relacion = Literal["satisface", "no_satisface", "informacion_insuficiente"]
PlataformaAmazon = Literal["amazon_mx", "amazon_us"]
Finalidad = Literal["exclusion", "ruteo", "keyword"]

MotivoIndeterminado = Literal[
    "ficha_ausente",
    "juicio_ausente",
    "juicio_insuficiente",
    "universo_desconocido",
    "universo_vacio",
    "fallo_proveedor",
    "texto_no_aplica",
    "no_anunciado",
    "missing_state",
]
MotivoNoAplica = Literal["asin_like"]

# ASIN-like: misma regla que fabrica_plan.PATRON_ASIN y el sellado
# is_asin_like de search_term_observation (10 alfanumericos empezando en b0,
# sin distinguir mayusculas). La equivalencia queda fijada por test.
_ASIN_RE = re.compile(r"b0[a-z0-9]{8}", re.IGNORECASE)


def es_asin_like(termino: str) -> bool:
    """True si parece ASIN; ASIN-like queda FUERA del clasificador de texto."""
    return _ASIN_RE.fullmatch(termino.strip()) is not None


@dataclass(frozen=True)
class HechoConFuente:
    """Hecho de catalogo con su fuente."""

    texto: str
    fuente: str


@dataclass(frozen=True)
class FichaVersion:
    """Ficha aprobada INMUTABLE por version; cada correccion inserta una nueva."""

    id: UUID
    producto_id: int
    plataforma: PlataformaAmazon
    listings: frozenset[int]
    hechos: tuple[HechoConFuente, ...]
    desconocidos: frozenset[str]
    aprobador: str
    observado_at: datetime  # UTC
    revisar_antes_de: datetime  # UTC
    sha256: str


@dataclass(frozen=True)
class ClavePar:
    """Clave de reutilizacion exacta: hash del termino literal + ficha + contrato."""

    termino_literal_sha256: str
    ficha_version_id: UUID
    contrato_sha256: str


DistribucionRelacion = Mapping[Relacion, Decimal]


@dataclass(frozen=True)
class Juicio:
    """Respuesta validada del proveedor para un par (apreciacion, no hecho)."""

    intento_id: UUID
    clave: ClavePar
    relacion: Relacion
    probabilidades: DistribucionRelacion
    confidence: Decimal
    observado_at: datetime  # UTC


@dataclass(frozen=True)
class FichaFaltante:
    """Producto sin ficha aplicable (o de otra variante que no lo acredita)."""

    producto_id: int | None


@dataclass(frozen=True)
class NoAplicaTexto:
    """Termino fuera del clasificador de texto (ASIN-like)."""

    motivo: MotivoNoAplica


@dataclass(frozen=True)
class FalloProveedor:
    """Fallo de transporte/validacion: JAMAS evidencia de incompatibilidad."""

    producto_id: int | None
    motivo: str


EstadoPar = Juicio | FichaFaltante | NoAplicaTexto | FalloProveedor

# Estado conocido activo: solo estos cuentan como anunciado (spec: el censo
# observa ENABLED/PAUSED; ARCHIVED conocido no se anuncia). Estado ausente no
# descarta (conserva como incidencia missing_state), no activa.
ESTADOS_ACTIVOS = frozenset({"ENABLED", "PAUSED"})


@dataclass(frozen=True)
class EstadoAnuncio:
    """Estado del anuncio observado (ad_entity_state). status None = estado
    ausente; se conserva y se reporta como incidencia, jamas se descarta."""

    status: str | None = None
    synced_at: datetime | None = None


@dataclass(frozen=True)
class MiembroCenso:
    """Miembro del universo congelado (identidad de anuncios conservada).
    producto_id None = anuncio sin listing, jamas acredita ficha. `estados`
    es paralelo a `anuncio_ids` (vacio = sin dato de estado)."""

    anuncio_ids: tuple[int, ...]
    producto_id: int | None
    listing_ids: frozenset[int]
    estados: tuple[EstadoAnuncio, ...] = ()
    ficha_version_id: UUID | None = None


@dataclass(frozen=True)
class CensoCongelado:
    """Universo congelado; exhaustivo=True solo con enumeracion probada
    (grupos Amazon: False en V1; plan de fabrica: True)."""

    miembros: tuple[MiembroCenso, ...]
    exhaustivo: bool


@dataclass(frozen=True)
class HayCompatible:
    """Compatible observado; con_juicio/total expone la cobertura parcial."""

    producto_ids: tuple[int, ...]
    miembros_con_juicio: int
    miembros_totales: int


@dataclass(frozen=True)
class NingunoCompatible:
    """Negativo de exclusion: unico camino posible, con todo verificado."""

    miembros_totales: int


@dataclass(frozen=True)
class Indeterminado:
    """No se puede concluir; los motivos dicen por que."""

    motivos: frozenset[MotivoIndeterminado]


RelevanciaConjunto = HayCompatible | NingunoCompatible | Indeterminado


@dataclass(frozen=True)
class Vigente:
    """Ficha aprobada, cubre el listing, no vencida."""


@dataclass(frozen=True)
class Obsoleta:
    """Uso actual invalido (ficha revocada/vencida/sustituida): la historia queda."""


@dataclass(frozen=True)
class NoComprobable:
    """Sin fichas congeladas que comparar: vigencia desconocida."""


Vigencia = Vigente | Obsoleta | NoComprobable


def _solo_no_activo(estados: tuple[EstadoAnuncio, ...]) -> bool:
    """True si todos los estados son CONOCIDOS y ninguno activo (p. ej. un
    unico anuncio ARCHIVED). Estado ausente no excluye: es missing_state."""
    return bool(estados) and all(
        estado.status is not None and estado.status not in ESTADOS_ACTIVOS for estado in estados
    )


def _con_estado_ausente(miembro: MiembroCenso) -> bool:
    """missing_state solo con anuncios cuyo estado falta (sin anuncios, no)."""
    if not miembro.anuncio_ids:
        return False
    if not miembro.estados:
        return True
    return any(estado.status is None for estado in miembro.estados)


def _universo_anunciado(
    censo: CensoCongelado,
) -> tuple[tuple[MiembroCenso, ...], set[UUID]]:
    """Universo sin los solo-no-activos + fichas excluidas; valida paralelidad."""
    if any(len(miembro.estados) not in (0, len(miembro.anuncio_ids)) for miembro in censo.miembros):
        raise ValueError("censo con estados no paralelos a anuncio_ids")
    excluidas = {
        miembro.ficha_version_id
        for miembro in censo.miembros
        if miembro.ficha_version_id is not None and _solo_no_activo(miembro.estados)
    }
    universo = tuple(miembro for miembro in censo.miembros if not _solo_no_activo(miembro.estados))
    return universo, excluidas


def _absorber_pares(
    pares: tuple[EstadoPar, ...],
    por_ficha: dict[UUID, MiembroCenso],
    excluidas_fichas: set[UUID],
) -> tuple[list[int], set[UUID], set[MotivoIndeterminado]]:
    """Clasifica los pares: compatibles, juzgados y motivos (ValueError si la
    relacion esta fuera del contrato)."""
    compatibles: list[int] = []
    juzgados: set[UUID] = set()
    motivos: set[MotivoIndeterminado] = set()
    for par in pares:
        if isinstance(par, NoAplicaTexto):
            motivos.add("texto_no_aplica")
        elif isinstance(par, FichaFaltante):
            motivos.add("ficha_ausente")
        elif isinstance(par, FalloProveedor):
            motivos.add("fallo_proveedor")
        else:
            miembro = por_ficha.get(par.clave.ficha_version_id)
            if miembro is None:
                motivos.add(
                    "no_anunciado"
                    if par.clave.ficha_version_id in excluidas_fichas
                    else "ficha_ausente"
                )
            elif par.clave.ficha_version_id in juzgados:
                raise ValueError("dos juicios para la misma ficha")
            else:
                juzgados.add(par.clave.ficha_version_id)
                if par.relacion == "satisface":
                    compatibles.append(miembro.producto_id)
                elif par.relacion == "informacion_insuficiente":
                    motivos.add("juicio_insuficiente")
                elif par.relacion != "no_satisface":
                    raise ValueError(f"relacion fuera del contrato: {par.relacion}")
    return compatibles, juzgados, motivos


def componer(censo: CensoCongelado, pares: tuple[EstadoPar, ...]) -> RelevanciaConjunto:
    """Composicion determinista y sin IO (reglas: docstring del modulo).

    Un Juicio acredita a un miembro SOLO si su clave coincide con la ficha
    de ese miembro en el censo; estados ARCHIVED salen del universo
    (`no_anunciado`) y el estado ausente se conserva (`missing_state`).
    Errores estructurales del llamador: ValueError.
    """
    universo, excluidas_fichas = _universo_anunciado(censo)
    total = len(universo)
    por_ficha: dict[UUID, MiembroCenso] = {}
    for miembro in universo:
        if miembro.ficha_version_id is None:
            continue
        if miembro.ficha_version_id in por_ficha:
            raise ValueError("censo con la misma ficha en dos miembros")
        if miembro.producto_id is None:
            raise ValueError("miembro con ficha pero sin producto_id")
        por_ficha[miembro.ficha_version_id] = miembro

    motivos: set[MotivoIndeterminado] = set()
    if not censo.miembros:
        motivos.add("universo_vacio")
    if censo.miembros and (total == 0 or total < len(censo.miembros)):
        motivos.add("no_anunciado")
    if censo.miembros and not censo.exhaustivo:
        motivos.add("universo_desconocido")
    if any(_con_estado_ausente(m) for m in universo):
        motivos.add("missing_state")
    if any(m.ficha_version_id is None for m in universo):
        motivos.add("ficha_ausente")

    claves = {
        (par.clave.termino_literal_sha256, par.clave.contrato_sha256)
        for par in pares
        if isinstance(par, Juicio)
    }
    if len(claves) > 1:
        raise ValueError("juicios de distinto termino y contrato en una composicion")
    compatibles, juzgados, motivos_pares = _absorber_pares(pares, por_ficha, excluidas_fichas)
    motivos |= motivos_pares

    miembros_con_ficha = sum(1 for m in universo if m.ficha_version_id is not None)
    if len(juzgados) < miembros_con_ficha:
        motivos.add("juicio_ausente")

    if compatibles:
        return HayCompatible(
            producto_ids=tuple(compatibles),
            miembros_con_juicio=len(juzgados),
            miembros_totales=total,
        )
    if total > 0 and censo.exhaustivo and not motivos and len(juzgados) == total:
        return NingunoCompatible(miembros_totales=total)
    return Indeterminado(frozenset(motivos))


# ===========================================================================
# 1.4: sujetos de revision y el asesor (une catalogo, juicios y revision)
# ===========================================================================


@dataclass(frozen=True)
class DecisionARevisar:
    """Decision guardada + termino + censo congelados (leerla es de 2.1).
    Para harvest, `destino_censo` congela el universo del DESTINO y se
    compone por separado del origen (`censo`): la UI muestra ambos ambitos
    sin mezclar universos ni heredar conclusiones entre ellos."""

    decision_id: int
    termino: str
    censo: CensoCongelado
    plataforma: PlataformaAmazon
    decided_at: datetime | None = None
    destino_censo: CensoCongelado | None = None


@dataclass(frozen=True)
class SemillasARevisar:
    """Plan de fabrica congelado: hash, canonico, fuentes y terminos a
    cotejar contra el censo del NUEVO plan (sin heredar conclusiones)."""

    plan_sha256: str
    plan_canonico: Mapping[str, object]
    fuentes_semillas: Mapping[str, object]
    terminos: tuple[str, ...]
    censo: CensoCongelado
    plataforma: PlataformaAmazon


Sujeto = DecisionARevisar | SemillasARevisar


@dataclass(frozen=True)
class PlanSeco:
    """Lo que `evaluar` haria (dry-run del CLI): pares por clave unica."""

    miembros: int
    fichas: int
    pares_nuevos: int
    pares_reutilizables: int
    pagaria: int
    agotaria: bool


@dataclass(frozen=True)
class Revision:
    """Salida de evaluar; presupuesto_agotado = lote retomable (misma
    solicitud). `destinos` trae la composicion del censo destino (harvest),
    un resultado por termino; vacia cuando el sujeto no lleva destino."""

    solicitud: UUID
    resultados: tuple[tuple[str, RelevanciaConjunto], ...]
    presupuesto_agotado: bool
    destinos: tuple[tuple[str, RelevanciaConjunto], ...] = ()


def _censo_a_json(censo: CensoCongelado) -> dict:
    """Congela el censo (con las fichas ya resueltas) para jev_revision."""

    def iso(valor):
        return valor.isoformat() if valor else None

    return {
        "exhaustivo": censo.exhaustivo,
        "miembros": [
            {
                "anuncio_ids": list(m.anuncio_ids),
                "estados": [{"status": e.status, "synced_at": iso(e.synced_at)} for e in m.estados],
                "ficha_version_id": str(m.ficha_version_id) if m.ficha_version_id else None,
                "listing_ids": sorted(m.listing_ids),
                "producto_id": m.producto_id,
            }
            for m in censo.miembros
        ],
    }


def _identidad_del_censo(censo: CensoCongelado) -> list:
    """Lo que hace distinto a un universo: anuncios, productos, listings y
    estados; no la hora de sincronizacion."""
    return [
        (m.anuncio_ids, m.producto_id, sorted(m.listing_ids), [e.status for e in m.estados])
        for m in censo.miembros
    ]


def _mismo_origen(congelado: CensoCongelado, crudo: CensoCongelado) -> bool:
    """El sujeto que se retoma trae el MISMO censo: misma identidad de
    universo y mismo `exhaustivo` (`synced_at` no cuenta: una
    resincronizacion no es otro payload), y cada ficha que el llamador ya
    traia resuelta es la que la revision congelo."""
    return (
        _identidad_del_censo(congelado) == _identidad_del_censo(crudo)
        and congelado.exhaustivo == crudo.exhaustivo
        and all(
            traida.ficha_version_id in (None, guardado.ficha_version_id)
            for guardado, traida in zip(congelado.miembros, crudo.miembros, strict=True)
        )
    )


def _canonico(objeto: object) -> str:
    return json.dumps(objeto, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _juicio_de_respuesta(respuesta: dict, clave: ClavePar, evento_id: UUID) -> Juicio:
    """Reconstruye el Juicio de un exito guardado (mismo intento: el evento)."""
    return Juicio(
        intento_id=evento_id,
        clave=clave,
        relacion=respuesta["relacion"],
        probabilidades={k: Decimal(v) for k, v in respuesta["probabilidades"].items()},
        confidence=Decimal(respuesta["confidence"]),
        observado_at=datetime.fromisoformat(respuesta["observado_at"]),
    )


def _censo_de_json(datos: dict) -> CensoCongelado:
    miembros = [
        MiembroCenso(
            anuncio_ids=tuple(m["anuncio_ids"]),
            producto_id=m["producto_id"],
            listing_ids=frozenset(m["listing_ids"]),
            estados=tuple(
                EstadoAnuncio(
                    status=e["status"],
                    synced_at=datetime.fromisoformat(e["synced_at"]) if e["synced_at"] else None,
                )
                for e in m["estados"]
            ),
            ficha_version_id=UUID(m["ficha_version_id"]) if m["ficha_version_id"] else None,
        )
        for m in datos["miembros"]
    ]
    return CensoCongelado(miembros=tuple(miembros), exhaustivo=datos["exhaustivo"])
