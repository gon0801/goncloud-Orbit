"""Plan, tope y veredicto puros del impulso de un producto (BIDS 02, I.2).

Un impulso son dos campanas (exact y automatica) con tope de gasto y
plazo de 14 dias. Sin reloj, sin base, sin red: la fecha llega en el
plan y el dinero va como Decimal hasta el wire.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app import fabrica_plan as fp

ROLES_IMPULSO = ("category_exact", "auto_discovery")
PLAZO_VEREDICTO_DIAS = 14
IMPRESIONES_MINIMAS = 2000
CLICS_MINIMOS = 20
DIAS_DE_PRESUPUESTO = 14
DIAS_DE_MARGEN_DEL_VIGIA = 2

PRESUPUESTO_MINIMO_CAMPANA: dict[str, Decimal | None] = {"MXN": None, "USD": None}

EstadoVeredicto = Literal[
    "en_curso", "vende", "clics_sin_ventas", "impresiones_sin_clics", "sin_impresiones"
]
AccionVeredicto = Literal["seguir", "pausar", "graduar"]


def presupuesto_diario(tope: Decimal, moneda: str) -> Decimal:
    """Presupuesto diario del impulso entero: el tope entre 14, o dos
    veces el minimo por campana si se midio y es mayor."""
    minimo = PRESUPUESTO_MINIMO_CAMPANA.get(moneda)
    base = tope / DIAS_DE_PRESUPUESTO
    if minimo is None:
        return base
    return max(base, 2 * minimo)


@dataclass(frozen=True)
class TopeAprendizaje:
    """Tope por producto. `presupuesto_diario` es del impulso ENTERO:
    cada campana lleva la mitad. Sin redondear: el wire cuantiza."""

    monto: Decimal
    moneda: str
    presupuesto_diario: Decimal
    plazo_dias: int = PLAZO_VEREDICTO_DIAS


@dataclass(frozen=True)
class AnuncioARetirar:
    """Un product ad del producto en una bolsa vieja."""

    ad_id_externo: str
    ad_group_externo: str
    campana_externa: str
    ad_group_nombre: str
    sku: str
    asin: str


@dataclass(frozen=True)
class PlanImpulso:
    """Un producto, dos campanas, un ad group en cada una. `margen_neto_pct`
    es el de la publicacion: sin margen positivo no hay objetivo medido y
    el impulso no se lanza."""

    plataforma: str
    listing_id: int
    product_id: int
    seller_sku: str
    asin: str
    subfamilia: str
    margen_neto_pct: Decimal | None
    fecha: dt.date
    objetivo: fp.ObjetivoPlanV2
    tope: TopeAprendizaje
    bid_por_rol: dict[str, Decimal | None]
    keywords_exact: tuple[str, ...]
    negatives: tuple[str, ...]
    retiros: tuple[AnuncioARetirar, ...] = ()
    bids_acotados: tuple[str, ...] = ()

    def como_json(self) -> dict:
        """schema_version 3. Dinero como string."""
        return {
            "schema_version": 3,
            "plataforma": self.plataforma,
            "listing_id": self.listing_id,
            "product_id": self.product_id,
            "seller_sku": self.seller_sku,
            "asin": self.asin,
            "subfamilia": self.subfamilia,
            "margen_neto_pct": str(self.margen_neto_pct)
            if self.margen_neto_pct is not None
            else None,
            "fecha": self.fecha.isoformat(),
            "objetivo": {
                "origen": self.objetivo.origen,
                "acos_pct": str(self.objetivo.acos_pct),
                "procedencia": self.objetivo.procedencia,
                "fraccion": str(self.objetivo.fraccion)
                if self.objetivo.fraccion is not None
                else None,
                "derivado": str(self.objetivo.derivado)
                if self.objetivo.derivado is not None
                else None,
            },
            "tope": {
                "monto": str(self.tope.monto),
                "moneda": self.tope.moneda,
                "presupuesto_diario": str(self.tope.presupuesto_diario),
                "plazo_dias": self.tope.plazo_dias,
            },
            "bid_por_rol": {
                rol: str(bid) if bid is not None else None
                for rol, bid in sorted(self.bid_por_rol.items())
            },
            "keywords_exact": list(self.keywords_exact),
            "negatives": list(self.negatives),
            "retiros": [
                {
                    "ad_id_externo": r.ad_id_externo,
                    "ad_group_externo": r.ad_group_externo,
                    "campana_externa": r.campana_externa,
                    "ad_group_nombre": r.ad_group_nombre,
                    "sku": r.sku,
                    "asin": r.asin,
                }
                for r in self.retiros
            ],
            "bids_acotados": list(self.bids_acotados),
        }

    def huella(self) -> str:
        """sha256 del JSON canonico."""
        return hashlib.sha256(
            json.dumps(self.como_json(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class LecturaImpulso:
    """Acumulado del impulso desde su lanzamiento, en las filas de sus
    dos campanas."""

    corte: dt.date
    dias_observados: int
    impresiones: int | None
    clics: int | None
    gasto: Decimal | None
    pedidos: int | None


@dataclass(frozen=True)
class VeredictoImpulso:
    estado: EstadoVeredicto
    final: bool
    accion: AccionVeredicto


def veredicto_impulso(lectura: LecturaImpulso, tope: TopeAprendizaje) -> VeredictoImpulso:
    """Primer caso que aplique, en este orden."""
    if (
        lectura.impresiones is None
        or lectura.clics is None
        or lectura.gasto is None
        or lectura.pedidos is None
    ):
        return VeredictoImpulso("en_curso", False, "seguir")
    if lectura.pedidos >= 1:
        return VeredictoImpulso("vende", True, "graduar")
    if lectura.gasto >= tope.monto - DIAS_DE_MARGEN_DEL_VIGIA * tope.presupuesto_diario:
        return VeredictoImpulso("clics_sin_ventas", True, "pausar")
    if lectura.dias_observados < tope.plazo_dias:
        return VeredictoImpulso("en_curso", False, "seguir")
    if lectura.impresiones < IMPRESIONES_MINIMAS:
        return VeredictoImpulso("sin_impresiones", True, "pausar")
    if lectura.clics < CLICS_MINIMOS:
        return VeredictoImpulso("impresiones_sin_clics", True, "pausar")
    return VeredictoImpulso("en_curso", False, "seguir")


def acota_bids(
    bid_por_rol: dict[str, Decimal | None], diario_por_campana: Decimal
) -> tuple[dict[str, Decimal], tuple[str, ...]]:
    """Baja al presupuesto diario de su campana cada bid sugerido mayor
    que el. Un bid None (sin sugerencia: la vista previa lo muestra y
    el dueno decide si lanza) sale al diario de su campana. Devuelve
    bids y roles acotados."""
    acotados = {}
    roles = []
    for rol, bid in bid_por_rol.items():
        if bid is None:
            acotados[rol] = diario_por_campana
        elif bid > diario_por_campana:
            acotados[rol] = diario_por_campana
            roles.append(rol)
        else:
            acotados[rol] = bid
    return acotados, tuple(roles)


def grupo_v2_de_plan(plan: PlanImpulso) -> fp.PlanGrupoV2:
    """PlanGrupoV2 de dos roles en modo shadow desde el plan de impulso,
    con bids acotados (None sale al diario de su campana). Es el unico
    constructor: lanza lo manda a la fabrica y los tests sacan los
    pasos con fp.pasos_del_rol."""
    mitad = plan.tope.presupuesto_diario / 2
    bids, _acotados = acota_bids(plan.bid_por_rol, mitad)
    return fp.PlanGrupoV2(
        plan.plataforma,
        plan.subfamilia,
        plan.seller_sku,
        plan.fecha,
        plan.tope.moneda,
        "shadow",
        (
            fp.PublicacionGrupoV2(
                plan.listing_id,
                plan.product_id,
                plan.asin,
                plan.seller_sku,
                plan.plataforma,
                plan.margen_neto_pct,
            ),
        ),
        {rol: fp.ParametrosRol(rol, mitad, bids[rol]) for rol in ROLES_IMPULSO},
        plan.objetivo,
        fp.Semillas((), (), plan.negatives, plan.keywords_exact),
        roles=ROLES_IMPULSO,
    )
