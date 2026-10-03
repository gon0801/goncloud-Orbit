"""Tipos puros y composicion de relevancia para la asesoria Jev en Ads.

JEV ADS 01 (1.1). El diseno manda:
docs/superpowers/specs/2026-10-03-jev-ads-design.md, secciones "Tipos y
modulos" y "Catalogo y reglas de composicion".

Modulo PURO: sin red ni DB. El censo llega congelado y los pares ya traen su
juicio; el adaptador de catalogo es `app/jev_catalogo.py` y el transporte
TypeSafe vivira en `app/jev_juicios.py`.

La unidad de juicio es TERMINO LITERAL + VERSION DE FICHA. `componer` aplica
las reglas del diseno sobre un censo congelado y los pares evaluados:

- Un producto compatible observado permite HayCompatible, incluso con
  cobertura parcial; la limitacion viaja en el resultado.
- NingunoCompatible exige universo no vacio, exhaustivo, fichas de todos los
  miembros y `no_satisface` en cada par. Es la unica via a un negativo de
  exclusion.
- En el resto de casos, Indeterminado con motivos explicitos: vacio no prueba
  exclusion, la ausencia de evidencia no es incompatibilidad y un fallo del
  proveedor jamas se presenta como incompatibilidad.
- No se multiplican probabilidades: la clasificacion es apreciacion del
  modelo, no un hecho validado por la base.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

Relacion = Literal["satisface", "no_satisface", "informacion_insuficiente"]
Finalidad = Literal["exclusion", "ruteo", "keyword"]
PlataformaAmazon = Literal["amazon_mx", "amazon_us"]

MotivoIndeterminado = Literal[
    "ficha_ausente",
    "juicio_ausente",
    "juicio_insuficiente",
    "universo_desconocido",
    "universo_vacio",
    "fallo_proveedor",
    "texto_no_aplica",
]
MotivoNoAplica = Literal["asin_like"]

# ASIN: 10 alfanumericos con al menos una letra y un digito. Un termino
# puramente numerico no es ASIN-like (queda en el clasificador de texto).
_ASIN_RE = re.compile(r"(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]{10}")


def es_asin_like(termino: str) -> bool:
    """True si el termino parece un ASIN. ASIN-like queda FUERA del
    clasificador de texto (contrato del plan); el llamador construye
    NoAplicaTexto con ese par."""
    return _ASIN_RE.fullmatch(termino.strip().upper()) is not None


@dataclass(frozen=True)
class HechoConFuente:
    """Un hecho de catalogo con su fuente (ficha manual aprobada)."""

    texto: str
    fuente: str


@dataclass(frozen=True)
class FichaVersion:
    """Version inmutable de ficha aprobada. Cada correccion inserta una
    version nueva; la vigencia la decide el catalogo (no revocada, cubre el
    listing y revisar_antes_de no vencio)."""

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
    """Clave de reutilizacion: hash del texto literal UTF-8, ficha exacta y
    hash del contrato completo. No fusiona acentos, numeros ni terminos
    parecidos."""

    termino_literal_sha256: str
    ficha_version_id: UUID
    contrato_sha256: str


DistribucionRelacion = Mapping[Relacion, Decimal]


@dataclass(frozen=True)
class Juicio:
    """Respuesta validada del proveedor para un par. La clasificacion es
    apreciacion del modelo, no un hecho validado."""

    intento_id: UUID
    clave: ClavePar
    relacion: Relacion
    probabilidades: DistribucionRelacion
    confidence: Decimal
    observado_at: datetime  # UTC


@dataclass(frozen=True)
class FichaFaltante:
    """El producto observado no tiene ficha aplicable (no la tiene, o la que
    tiene es de otra variante y no lo acredita)."""

    producto_id: int | None


@dataclass(frozen=True)
class NoAplicaTexto:
    """El termino no entra al clasificador de texto (ASIN-like)."""

    motivo: MotivoNoAplica


@dataclass(frozen=True)
class FalloProveedor:
    """Fallo de transporte o de validacion para ese par. JAMAS es evidencia
    de incompatibilidad."""

    producto_id: int | None
    motivo: str


EstadoPar = Juicio | FichaFaltante | NoAplicaTexto | FalloProveedor


@dataclass(frozen=True)
class MiembroCenso:
    """Miembro del universo congelado. La identidad de cada anuncio se
    conserva ANTES de deduplicar productos. `producto_id` None = anuncio sin
    listing resuelto; se conserva y no acredita ficha jamas."""

    anuncio_ids: tuple[int, ...]
    producto_id: int | None
    listing_ids: frozenset[int]
    ficha_version_id: UUID | None = None


@dataclass(frozen=True)
class CensoCongelado:
    """Universo congelado de la revision. `exhaustivo` solo puede ser True
    con prueba de enumeracion completa del universo: en V1 los grupos Amazon
    quedan False (universo 'desconocido'); el plan de fabrica si conoce su
    conjunto explicito."""

    miembros: tuple[MiembroCenso, ...]
    exhaustivo: bool


@dataclass(frozen=True)
class HayCompatible:
    """Al menos un producto compatible observado. `miembros_con_juicio` contra
    `miembros_totales` expone la cobertura parcial para mostrarla siempre."""

    producto_ids: tuple[int, ...]
    miembros_con_juicio: int
    miembros_totales: int


@dataclass(frozen=True)
class NingunoCompatible:
    """Universo no vacio, exhaustivo, con ficha y juicio no_satisface en cada
    miembro. Unico camino a un negativo de exclusion."""

    miembros_totales: int


@dataclass(frozen=True)
class Indeterminado:
    """No se puede concluir. Los motivos dicen por que: ficha ausente,
    juicio ausente o insuficiente, universo desconocido o vacio, fallo del
    proveedor, texto que no aplica."""

    motivos: frozenset[MotivoIndeterminado]


RelevanciaConjunto = HayCompatible | NingunoCompatible | Indeterminado


@dataclass(frozen=True)
class Vigente:
    """La ficha sigue aprobada, cubre el listing y no vencio su revision."""


@dataclass(frozen=True)
class Obsoleta:
    """La revision describe el uso actual: ficha revocada o destino cambiado
    la marca obsoleta sin reescribir su resultado historico."""


@dataclass(frozen=True)
class NoComprobable:
    """La vigencia no pudo comprobarse con los IDs y versiones disponibles."""


Vigencia = Vigente | Obsoleta | NoComprobable


def componer(censo: CensoCongelado, pares: tuple[EstadoPar, ...]) -> RelevanciaConjunto:
    """Aplica las reglas de composicion del diseno. Determinista y sin IO.

    Un par de Juicio acredita a un miembro SOLO si su clave coincide con la
    ficha de ese miembro en el censo: la ficha de otra variante no acredita.
    Un miembro con ficha pero sin par aporta motivo `juicio_ausente`; con
    par informacion_insuficiente aporta `juicio_insuficiente`.

    Un censo con la misma ficha en dos miembros (o un miembro con ficha y sin
    producto) es error estructural del llamador y levanta ValueError.
    """
    total = len(censo.miembros)
    por_ficha: dict[UUID, MiembroCenso] = {}
    for miembro in censo.miembros:
        if miembro.ficha_version_id is None:
            continue
        if miembro.ficha_version_id in por_ficha:
            raise ValueError("censo con la misma ficha en dos miembros")
        if miembro.producto_id is None:
            raise ValueError("miembro con ficha pero sin producto_id")
        por_ficha[miembro.ficha_version_id] = miembro

    motivos: set[MotivoIndeterminado] = set()
    if total == 0:
        motivos.add("universo_vacio")
    elif not censo.exhaustivo:
        motivos.add("universo_desconocido")
    if any(m.ficha_version_id is None for m in censo.miembros):
        motivos.add("ficha_ausente")

    compatibles: list[int] = []
    juzgados: set[UUID] = set()
    for par in pares:
        if isinstance(par, NoAplicaTexto):
            motivos.add("texto_no_aplica")
            continue
        if isinstance(par, FichaFaltante):
            motivos.add("ficha_ausente")
            continue
        if isinstance(par, FalloProveedor):
            motivos.add("fallo_proveedor")
            continue
        miembro = por_ficha.get(par.clave.ficha_version_id)
        if miembro is None:
            motivos.add("ficha_ausente")
            continue
        if par.clave.ficha_version_id in juzgados:
            raise ValueError("dos juicios para la misma ficha")
        juzgados.add(par.clave.ficha_version_id)
        if par.relacion == "satisface":
            compatibles.append(miembro.producto_id)
        elif par.relacion == "informacion_insuficiente":
            motivos.add("juicio_insuficiente")

    miembros_con_ficha = sum(1 for m in censo.miembros if m.ficha_version_id is not None)
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
