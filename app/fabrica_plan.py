"""Nucleo PURO de la fabrica de campanas por grupo (FABRICA 01, spec
docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md).

Sin psycopg ni httpx: recibe lo leido de la base, devuelve el plan, sus
payloads y su huella. tools/fabrica_campanas.py (que entra por stdin al
contenedor y debe ser UN archivo) hace todo el IO alrededor de esto.

Sellos que se REUSAN, jamas se copian (regla 2): banda [10, 45] y defaults
de bid por moneda de app.optimizer.goals; criterio HARVEST de
app.optimizer.hygiene; moneda por plataforma pineada contra
app.ads.write.PLATAFORMA_MONEDA por test.

HIPOTESIS hasta la sonda (tarea 11 del plan): los shapes de POST
/sp/campaigns, /sp/adGroups y /sp/targets nunca se ejercitaron en vivo en
este repo; el camino feliz de /sp/productAds tampoco. Los de keywords y
negativeKeywords si (probe 2.5, archiva_inertes). Cualquier campo que la
sonda corrija se sella aqui con su evidencia en out/.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from app.optimizer import goals as g
from app.optimizer import hygiene
from app.optimizer.bid import PLATAFORMAS_MONEDA

# Orden FIJO de creacion (spec §5.3): la exact primero — si el lote muere a
# medias, jamas queda una discovery sin destino de harvest.
ROLES_ORDEN_CREACION = (
    "category_exact",
    "category_phrase",
    "category_broad",
    "product_targeting",
    "auto_discovery",
)
MATCH_POR_ROL = {"category_exact": "EXACT", "category_phrase": "PHRASE", "category_broad": "BROAD"}
TARGETING_POR_ROL = {rol: "MANUAL" for rol in ROLES_ORDEN_CREACION}
TARGETING_POR_ROL["auto_discovery"] = "AUTO"

# Regla 2 (un numero, una fuente), aplicada a la moneda: el mapa vive en el
# motor (app/optimizer/bid.py) y se IMPORTA — el nucleo no define el suyo
# (candado test_una_sola_fuente_de_moneda_por_plataforma); pineado contra
# app/ads/write.PLATAFORMA_MONEDA por test. D-GLM-4-5-5.
MONEDA_POR_PLATAFORMA = PLATAFORMAS_MONEDA
MODOS_GOAL = ("shadow", "live")
ESTADO_NUEVO = "ENABLED"  # decision 8: nacen ENABLED
VENTANA_DIAS = (105, 15)  # [D-105, D-15): biblioteca y terminos (NO el margen por producto)
# Candidatos a semilla EXACT: la ventana de CORTES del motor (spec §6: "mismo
# criterio HARVEST... ventana y madurez ya selladas"), NO la del margen.
# [D-39, D-10] inclusive = 30 dias (DIAS_VENTANA) con madurez >= 10d
# (DIAS_MADUREZ_CORTES, regla 6) de app/optimizer/windows.py; en el SQL:
# metric_date >= CURRENT_DATE - 39 AND metric_date < CURRENT_DATE - 9.
VENTANA_CORTES_DIAS = (39, 9)  # pineada contra windows.py por test (regla 2)
# Margen por producto (v_margen_producto): decision ESCRITA del dueno 2026-09-05
# (tarea 1, "Decisiones y evidencia"): guard de 30 dias con venta sobre la
# ventana [MARGEN_VENTANA_DESDE, D-15) con arranque FIJO = primer valid_from
# de sku_cost. Ambos pineados contra el SQL de 0018 por test (regla 2).
MARGEN_VENTANA_DESDE = dt.date(2026, 2, 20)
MARGEN_DIAS_MIN_PRODUCTO = 30

PATRON_TIPO_PRODUCTO = re.compile(r"^[a-z0-9_]+$")
PATRON_ASIN = re.compile(r"^b0[a-z0-9]{8}$", re.IGNORECASE)

PATH_CREATE = {
    "campaign": "/sp/campaigns",
    "ad_group": "/sp/adGroups",
    "product_ad": "/sp/productAds",
    "keyword": "/sp/keywords",
    "target": "/sp/targets",
    "negative_keyword": "/sp/negativeKeywords",
}
VENDOR_POR_PATH = {
    "/sp/campaigns": "application/vnd.spcampaign.v3+json",
    "/sp/adGroups": "application/vnd.spadgroup.v3+json",
    "/sp/productAds": "application/vnd.spproductad.v3+json",
    "/sp/keywords": "application/vnd.spkeyword.v3+json",
    "/sp/targets": "application/vnd.sptargetingclause.v3+json",
    "/sp/negativeKeywords": "application/vnd.spnegativekeyword.v3+json",
}
ENVOLTURA_POR_PATH = {
    "/sp/campaigns": "campaigns",
    "/sp/adGroups": "adGroups",
    "/sp/productAds": "productAds",
    "/sp/keywords": "keywords",
    "/sp/targets": "targetingClauses",
    "/sp/negativeKeywords": "negativeKeywords",
}
CLAVE_ID_POR_PATH = {
    "/sp/campaigns": "campaignId",
    "/sp/adGroups": "adGroupId",
    "/sp/productAds": "adId",
    "/sp/keywords": "keywordId",
    "/sp/targets": "targetId",
    "/sp/negativeKeywords": "keywordId",
}
LIST_POR_PATH = {path: f"{path}/list" for path in VENDOR_POR_PATH}
FILTRO_ID_POR_LIST = {
    "/sp/campaigns/list": "campaignIdFilter",
    "/sp/adGroups/list": "adGroupIdFilter",
    "/sp/productAds/list": "adIdFilter",
    "/sp/keywords/list": "keywordIdFilter",
    "/sp/targets/list": "targetIdFilter",
    "/sp/negativeKeywords/list": "negativeKeywordIdFilter",
}
CONTENEDOR_POR_LIST = {f"{path}/list": clave for path, clave in ENVOLTURA_POR_PATH.items()}


class PlanInvalido(ValueError):
    """Fail-closed del plan: falta un dato o un monto esta fuera de ley."""


@dataclass(frozen=True)
class ProductoGrupo:
    product_id: int
    odoo_sku: str
    listing_id: int
    asin: str
    seller_sku: str
    margen_neto_pct: Decimal


@dataclass(frozen=True)
class ParametrosRol:
    rol: str
    budget: Decimal
    bid: Decimal


@dataclass(frozen=True)
class TerminoProducto:
    texto: str
    is_asin_like: bool
    orders: int | None
    cost: Decimal | None
    revenue: Decimal | None


@dataclass(frozen=True)
class Semillas:
    keywords: tuple[str, ...]
    asins: tuple[str, ...]
    negativos: tuple[str, ...]
    exact: tuple[str, ...]


@dataclass(frozen=True)
class ResultadoTarget:
    aplicado: Decimal
    derivado: Decimal
    minimo: Decimal
    fraccion: Decimal
    procedencia: str


@dataclass(frozen=True)
class PlanGrupo:
    platform: str
    tipo_producto: str
    nombre_base: str
    fecha: Any  # datetime.date
    moneda: str
    modo: str
    productos: tuple[ProductoGrupo, ...]
    parametros: dict[str, ParametrosRol]
    target: ResultadoTarget
    semillas: Semillas
    existentes: tuple[dict, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PublicacionGrupoV2:
    """Snapshot de una publicacion elegida en un plan v2.

    El margen es un dato observado: None conserva que no se pudo medir y no
    se sustituye por cero. Un producto puede aportar varias publicaciones.
    """

    listing_id: int
    product_id: int
    asin: str
    seller_sku: str
    platform: str
    margen_neto_pct: Decimal | None


@dataclass(frozen=True)
class ObjetivoPlanV2:
    """Objetivo confirmado para un lanzamiento, sin rentabilidad ficticia."""

    origen: str
    acos_pct: Decimal
    procedencia: str
    fraccion: Decimal | None = None
    derivado: Decimal | None = None


@dataclass(frozen=True)
class PlanGrupoV2:
    """Plan v2 por publicacion; no altera el formato ni la huella v1."""

    platform: str
    tipo_producto: str
    nombre_base: str
    fecha: Any
    moneda: str
    modo: str
    publicaciones: tuple[PublicacionGrupoV2, ...]
    parametros: dict[str, ParametrosRol]
    objetivo: ObjetivoPlanV2
    semillas: Semillas
    existentes: tuple[dict, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Paso:
    """Un POST del lote: recurso + path + payload SIN los ids del padre
    (campaignId/adGroupId los completa el tool con los externos creados)."""

    rol: str
    recurso: str
    path: str
    payload: dict
    descripcion: str


# ---------------------------------------------------------------------------
# Validaciones puras
# ---------------------------------------------------------------------------


def valida_tipo_producto(tipo: str) -> str:
    if not isinstance(tipo, str) or not PATRON_TIPO_PRODUCTO.match(tipo):
        raise PlanInvalido(
            f"tipo_producto {tipo!r} invalido: etiqueta ascii minuscula [a-z0-9_]+ (decision 7)"
        )
    return tipo


def target_del_grupo(margenes: list[Decimal | None], fraccion: Decimal | None) -> ResultadoTarget:
    """target = clamp(fraccion x min(margenes), [MARGEN_BANDA_MIN, MARGEN_BANDA_MAX]).
    fraccion None = interruptor apagado -> PlanInvalido; invalida -> ValueError
    (config corrupta, misma ley que el motor: g._valida_fraccion). El aplicado
    se cuantiza a NUMERIC(6,2) UNA sola vez aqui (0.5 x 38.21 = 19.105 ->
    19.10 por HALF_EVEN): huella, JSON, DB y goal ven el MISMO numero; el
    derivado queda crudo (NUMERIC(10,4)) para la procedencia."""
    if fraccion is None:
        raise PlanInvalido("sin fraccion: setting ads_target_fraccion_margen_<platform> ausente")
    fraccion = g._valida_fraccion(fraccion)
    if not margenes or any(m is None for m in margenes):
        raise PlanInvalido("todo producto del grupo necesita margen medible (regla 3)")
    minimo = min(margenes)
    derivado = fraccion * minimo
    clampeado = min(max(derivado, g.MARGEN_BANDA_MIN), g.MARGEN_BANDA_MAX)
    aplicado = clampeado.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    procedencia = f"margen_minimo_grupo: {fraccion} x {minimo} = {derivado}"
    if clampeado != derivado:
        procedencia += f" -> clamp [{g.MARGEN_BANDA_MIN}, {g.MARGEN_BANDA_MAX}] = {clampeado}"
    if aplicado != clampeado:
        procedencia += f" -> redondeo NUMERIC(6,2) = {aplicado}"
    return ResultadoTarget(aplicado, derivado, minimo, fraccion, procedencia)


def valida_parametros(parametros: dict[str, ParametrosRol], moneda: str) -> None:
    """Bids dentro de [piso, techo] de SU moneda (DEFAULTS_POR_MONEDA, regla 4)
    y budgets > 0 y >= bid; los 5 roles presentes."""
    try:
        piso, techo = g.DEFAULTS_POR_MONEDA[moneda]
    except KeyError:
        raise PlanInvalido(f"moneda {moneda!r} sin piso/techo en DEFAULTS_POR_MONEDA") from None
    for rol in ROLES_ORDEN_CREACION:
        p = parametros.get(rol)
        if p is None:
            raise PlanInvalido(f"faltan --bid/--budget de {rol}")
        for nombre, valor in (("bid", p.bid), ("budget", p.budget)):
            if not isinstance(valor, Decimal) or not valor.is_finite() or valor <= 0:
                raise PlanInvalido(f"{nombre} de {rol} debe ser Decimal finito > 0: {valor!r}")
        if not piso <= p.bid <= techo:
            raise PlanInvalido(f"bid de {rol} = {p.bid} fuera de [{piso}, {techo}] {moneda}")
        if p.budget < p.bid:
            raise PlanInvalido(f"budget de {rol} = {p.budget} < bid {p.bid}: no compra ni un clic")


def monto_wire(monto: Decimal) -> float:
    """Encoding final del wire (2 decimales, HALF_EVEN), mismo criterio que
    _bid_wire del write client: float SOLO aqui, jamas para guardar o decidir."""
    if not isinstance(monto, Decimal):
        raise TypeError(f"monto debe ser Decimal, no {type(monto).__name__}")
    return float(monto.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


# ---------------------------------------------------------------------------
# Nombres (la API los exige; el vinculo es campana_grupo_rol, no el nombre)
# ---------------------------------------------------------------------------


def nombre_campana(tipo: str, base: str, rol: str, fecha) -> str:
    return f"{tipo} | {base} | {rol} | {fecha.isoformat()}"


def nombre_ad_group(tipo: str, base: str, rol: str, fecha) -> str:
    return f"{nombre_campana(tipo, base, rol, fecha)} | ag"


# ---------------------------------------------------------------------------
# Semillas (spec §6)
# ---------------------------------------------------------------------------


def _cumple_harvest(t: TerminoProducto, target: Decimal) -> bool:
    """Criterio HARVEST del motor (hygiene.py camino (6), SIN dividir):
    orders >= HARVEST_ORDERS_MIN y cost * 100 <= min(tope_fijo, target) *
    revenue (el motor salta cuando cost * _CIEN > tope * ad_revenue).
    orders/cost/revenue None o revenue <= 0 -> no (regla 3).
    Divergencia declarada y conservadora (r2 glm 5): el motor harvestea
    "ventas gratis" (cost=0, revenue=0 pasa su cruce); la fabrica NO siembra
    exact con revenue <= 0 — sembrar menos, jamas de mas."""
    if t.orders is None or t.orders < hygiene.HARVEST_ORDERS_MIN:
        return False
    if t.cost is None or t.revenue is None or t.revenue <= 0:
        return False
    tope = min(hygiene.HARVEST_ACOS_TOPE_FIJO_PCT, target)
    return t.cost * 100 <= tope * t.revenue


def semillas_desde_terminos(
    terminos: list[TerminoProducto],
    biblioteca_keywords: list[str],
    biblioteca_negativos: list[str],
    target_acos_pct: Decimal,
    *,
    terminos_exact: list[TerminoProducto] | None = None,
) -> Semillas:
    """phrase/broad = biblioteca + terminos con orders >= 1 (sin ASIN-like);
    product targeting = ASIN-like de terminos + entradas ASIN de biblioteca;
    exact = terminos_exact que YA cumplen HARVEST; auto = negativos de
    biblioteca, SOLO keywords (sin ASIN-like: un ASIN no se niega con
    negativeKeywords; revisión PR 174). `terminos_exact` es el MISMO agregado pero sobre la ventana
    de CORTES del motor (VENTANA_CORTES_DIAS, regla 6: madurez >= 10d); None
    = evaluar exact sobre `terminos` (compat hacia atras en tests del nucleo).
    Todo normalizado (strip, keywords en minusculas, ASIN en mayusculas),
    deduplicado y ordenado (determinismo para la huella)."""
    keywords: set[str] = set()
    asins: set[str] = set()
    exact: set[str] = set()
    for texto in biblioteca_keywords:
        limpio = texto.strip()
        if PATRON_ASIN.match(limpio):
            asins.add(limpio.upper())
        elif limpio:
            keywords.add(limpio.lower())
    for t in terminos:
        limpio = t.texto.strip()
        if not limpio or not t.orders or t.orders < 1:
            continue
        if t.is_asin_like or PATRON_ASIN.match(limpio):
            asins.add(limpio.upper())
            continue
        keywords.add(limpio.lower())
    for t in terminos_exact if terminos_exact is not None else terminos:
        limpio = t.texto.strip()
        if not limpio or t.is_asin_like or PATRON_ASIN.match(limpio):
            continue
        if _cumple_harvest(t, target_acos_pct):
            exact.add(limpio.lower())
    # Negativos = SOLO keywords (spec §6): las entradas ASIN-like de la
    # biblioteca se excluyen con el MISMO criterio con que la biblioteca de
    # keywords separa keywords/ASINs (D-GLM-4-5-6, revision PR 174).
    negativos = sorted(
        {
            n.strip().lower()
            for n in biblioteca_negativos
            if n.strip() and not PATRON_ASIN.match(n.strip())
        }
    )
    return Semillas(
        tuple(sorted(keywords)), tuple(sorted(asins)), tuple(negativos), tuple(sorted(exact))
    )


# ---------------------------------------------------------------------------
# Huella y JSON del plan (dinero como STRING, regla 4)
# ---------------------------------------------------------------------------


def plan_como_json(plan: PlanGrupo) -> dict:
    return {
        "platform": plan.platform,
        "tipo_producto": plan.tipo_producto,
        "nombre_base": plan.nombre_base,
        "fecha": plan.fecha.isoformat(),
        "moneda": plan.moneda,
        "modo": plan.modo,
        "productos": [
            {
                "product_id": p.product_id,
                "odoo_sku": p.odoo_sku,
                "listing_id": p.listing_id,
                "asin": p.asin,
                "seller_sku": p.seller_sku,
                "margen_neto_pct": str(p.margen_neto_pct),
            }
            for p in plan.productos
        ],
        "parametros": {
            rol: {"budget": str(p.budget), "bid": str(p.bid)}
            for rol, p in sorted(plan.parametros.items())
        },
        "target_acos_pct": str(plan.target.aplicado),
        "target_derivado_pct": str(plan.target.derivado),
        "fraccion": str(plan.target.fraccion),
        "target_procedencia": plan.target.procedencia,
        "semillas": {
            "keywords": list(plan.semillas.keywords),
            "asins": list(plan.semillas.asins),
            "negativos": list(plan.semillas.negativos),
            "exact": list(plan.semillas.exact),
        },
    }


def huella_plan(plan: PlanGrupo) -> str:
    """sha256 del JSON canonico del plan: cambia si cambia CUALQUIER cosa que
    se va a crear (productos, bids, budgets, semillas, target, modo)."""
    canonico = json.dumps(plan_como_json(plan), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


CLAVE_CREACION = "fabrica.creacion"
VERSIONES_CREACION = ("v1", "v2")


def version_creacion_desde_settings(settings: dict) -> str:
    """Version de altas nuevas; ausencia o corrupcion se quedan en v1.

    Es un interruptor fail-closed: no afecta lectura, registro,
    reconciliacion ni pausa de lotes existentes v2.
    """
    version = settings.get(CLAVE_CREACION, "v1")
    return version if version in VERSIONES_CREACION else "v1"


def _valida_plan_v2(plan: PlanGrupoV2) -> None:
    if plan.platform not in MONEDA_POR_PLATAFORMA:
        raise PlanInvalido(f"plataforma v2 invalida: {plan.platform!r}")
    if plan.moneda != MONEDA_POR_PLATAFORMA[plan.platform]:
        raise PlanInvalido("moneda v2 no corresponde a la plataforma")
    if not plan.publicaciones:
        raise PlanInvalido("v2 requiere al menos una publicacion")
    listing_ids = [p.listing_id for p in plan.publicaciones]
    seller_skus = [p.seller_sku.strip() for p in plan.publicaciones]
    if len(listing_ids) != len(set(listing_ids)):
        raise PlanInvalido("listing_id repetido en el grupo v2")
    if not all(p.platform == plan.platform for p in plan.publicaciones):
        raise PlanInvalido("publicacion v2 de otra plataforma")
    if not all(seller_skus) or len(seller_skus) != len(set(seller_skus)):
        raise PlanInvalido("seller_sku ausente o repetido en el grupo v2")
    if plan.objetivo.origen not in ("margen_medido", "manual_lanzamiento"):
        raise PlanInvalido("origen de objetivo v2 invalido")
    if not plan.objetivo.acos_pct.is_finite() or plan.objetivo.acos_pct <= 0:
        raise PlanInvalido("objetivo ACoS v2 debe ser Decimal finito > 0")
    if plan.objetivo.origen == "margen_medido":
        if plan.objetivo.fraccion is None or plan.objetivo.derivado is None:
            raise PlanInvalido("objetivo por margen v2 requiere fraccion y derivado")
    elif plan.objetivo.fraccion is not None or plan.objetivo.derivado is not None:
        raise PlanInvalido("objetivo manual v2 no lleva fraccion ni derivado")
    valida_tipo_producto(plan.tipo_producto)
    valida_parametros(plan.parametros, plan.moneda)


def _publicacion_v2_como_json(publicacion: PublicacionGrupoV2) -> dict:
    return {
        "listing_id": publicacion.listing_id,
        "product_id": publicacion.product_id,
        "asin": publicacion.asin,
        "seller_sku": publicacion.seller_sku,
        "platform": publicacion.platform,
        "margen_neto_pct": (
            str(publicacion.margen_neto_pct) if publicacion.margen_neto_pct is not None else None
        ),
    }


def plan_v2_como_json(plan: PlanGrupoV2) -> dict:
    """Serializa v2 sin reusar el shape v1 ni convertir ausencias en cero."""
    _valida_plan_v2(plan)
    return {
        "schema_version": 2,
        "platform": plan.platform,
        "tipo_producto": plan.tipo_producto,
        "nombre_base": plan.nombre_base,
        "fecha": plan.fecha.isoformat(),
        "moneda": plan.moneda,
        "modo": plan.modo,
        "publicaciones": [
            _publicacion_v2_como_json(p)
            for p in sorted(plan.publicaciones, key=lambda publicacion: publicacion.listing_id)
        ],
        "parametros": {
            rol: {"budget": str(parametro.budget), "bid": str(parametro.bid)}
            for rol, parametro in sorted(plan.parametros.items())
        },
        "objetivo": {
            "origen": plan.objetivo.origen,
            "acos_pct": str(plan.objetivo.acos_pct),
            "procedencia": plan.objetivo.procedencia,
            "fraccion": str(plan.objetivo.fraccion) if plan.objetivo.fraccion is not None else None,
            "derivado": str(plan.objetivo.derivado) if plan.objetivo.derivado is not None else None,
        },
        "semillas": {
            "keywords": list(plan.semillas.keywords),
            "asins": list(plan.semillas.asins),
            "negativos": list(plan.semillas.negativos),
            "exact": list(plan.semillas.exact),
        },
    }


def huella_plan_v2(plan: PlanGrupoV2) -> str:
    """Huella de altas v2: el orden visual no cambia lo que se crea."""
    canonico = json.dumps(plan_v2_como_json(plan), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def plan_v2_desde_json(datos: dict) -> PlanGrupoV2:
    """Lector de lotes v2 para registro, reconciliacion y pausa despues del rollback."""
    if datos.get("schema_version") != 2:
        raise PlanInvalido("schema_version v2 requerida")
    publicaciones = tuple(
        PublicacionGrupoV2(
            p["listing_id"],
            p["product_id"],
            p["asin"],
            p["seller_sku"],
            p["platform"],
            Decimal(p["margen_neto_pct"]) if p["margen_neto_pct"] is not None else None,
        )
        for p in datos["publicaciones"]
    )
    objetivo_json = datos["objetivo"]
    objetivo = ObjetivoPlanV2(
        objetivo_json["origen"],
        Decimal(objetivo_json["acos_pct"]),
        objetivo_json["procedencia"],
        Decimal(objetivo_json["fraccion"]) if objetivo_json["fraccion"] is not None else None,
        Decimal(objetivo_json["derivado"]) if objetivo_json["derivado"] is not None else None,
    )
    semillas = datos["semillas"]
    plan = PlanGrupoV2(
        datos["platform"],
        datos["tipo_producto"],
        datos["nombre_base"],
        dt.date.fromisoformat(datos["fecha"]),
        datos["moneda"],
        datos["modo"],
        publicaciones,
        {
            rol: ParametrosRol(rol, Decimal(valor["budget"]), Decimal(valor["bid"]))
            for rol, valor in datos["parametros"].items()
        },
        objetivo,
        Semillas(
            tuple(semillas["keywords"]),
            tuple(semillas["asins"]),
            tuple(semillas["negativos"]),
            tuple(semillas["exact"]),
        ),
    )
    _valida_plan_v2(plan)
    return plan


def plan_desde_json(d: dict) -> PlanGrupo:
    """Inverso de plan_como_json (fabrica_lote.plan -> PlanGrupo) para el
    reintento del registro (--registrar): dinero vuelve a Decimal, fecha a
    date. `existentes` no se congela (solo informa)."""
    productos = tuple(
        ProductoGrupo(
            p["product_id"],
            p["odoo_sku"],
            p["listing_id"],
            p["asin"],
            p["seller_sku"],
            Decimal(p["margen_neto_pct"]),
        )
        for p in d["productos"]
    )
    parametros = {
        rol: ParametrosRol(rol, Decimal(v["budget"]), Decimal(v["bid"]))
        for rol, v in d["parametros"].items()
    }
    target = ResultadoTarget(
        Decimal(d["target_acos_pct"]),
        Decimal(d["target_derivado_pct"]),
        min(p.margen_neto_pct for p in productos),
        Decimal(d["fraccion"]),
        d["target_procedencia"],
    )
    s = d["semillas"]
    return PlanGrupo(
        d["platform"],
        d["tipo_producto"],
        d["nombre_base"],
        dt.date.fromisoformat(d["fecha"]),
        d["moneda"],
        d["modo"],
        productos,
        parametros,
        target,
        Semillas(tuple(s["keywords"]), tuple(s["asins"]), tuple(s["negativos"]), tuple(s["exact"])),
    )


# ---------------------------------------------------------------------------
# Pasos por rol (payloads SIN ids del padre)
# ---------------------------------------------------------------------------


def _payload_campana(plan: PlanGrupo, rol: str) -> dict:
    p = plan.parametros[rol]
    return {  # HIPOTESIS hasta la sonda: startDate ISO, budget anidado, dynamicBidding
        "name": nombre_campana(plan.tipo_producto, plan.nombre_base, rol, plan.fecha),
        "targetingType": TARGETING_POR_ROL[rol],
        "state": ESTADO_NUEVO,
        "budget": {"budgetType": "DAILY", "budget": monto_wire(p.budget)},
        "startDate": plan.fecha.isoformat(),
        "dynamicBidding": {"strategy": "LEGACY_FOR_SALES"},
    }


def _payload_ad_group(plan: PlanGrupo, rol: str) -> dict:
    return {  # HIPOTESIS hasta la sonda: defaultBid numero
        "name": nombre_ad_group(plan.tipo_producto, plan.nombre_base, rol, plan.fecha),
        "state": ESTADO_NUEVO,
        "defaultBid": monto_wire(plan.parametros[rol].bid),
    }


def _pasos_keywords(plan: PlanGrupo, rol: str) -> list[Paso]:
    bid = monto_wire(plan.parametros[rol].bid)
    textos = plan.semillas.exact if rol == "category_exact" else plan.semillas.keywords
    return [
        Paso(
            rol,
            "keyword",
            PATH_CREATE["keyword"],
            {"keywordText": kw, "matchType": MATCH_POR_ROL[rol], "state": ESTADO_NUEVO, "bid": bid},
            f"keyword {MATCH_POR_ROL[rol]} {kw!r}",
        )
        for kw in textos
    ]


def _semillas_del_rol(plan: PlanGrupo, rol: str) -> list[Paso]:
    if rol in MATCH_POR_ROL:
        return _pasos_keywords(plan, rol)
    if rol == "product_targeting":
        bid = monto_wire(plan.parametros[rol].bid)
        return [
            Paso(
                rol,
                "target",
                PATH_CREATE["target"],
                {  # HIPOTESIS hasta la sonda: expressionType MANUAL + ASIN_SAME_AS
                    "expressionType": "MANUAL",
                    "expression": [{"type": "ASIN_SAME_AS", "value": asin}],
                    "state": ESTADO_NUEVO,
                    "bid": bid,
                },
                f"target ASIN {asin}",
            )
            for asin in plan.semillas.asins
        ]
    return [  # auto_discovery: negativos (shape sellado por el probe 2.5)
        Paso(
            rol,
            "negative_keyword",
            PATH_CREATE["negative_keyword"],
            {"keywordText": neg, "matchType": "NEGATIVE_EXACT", "state": ESTADO_NUEVO},
            f"negative EXACT {neg!r}",
        )
        for neg in plan.semillas.negativos
    ]


def pasos_del_rol(plan: PlanGrupo, rol: str) -> list[Paso]:
    """campana -> ad group -> un product ad por producto -> semillas del rol."""
    pasos = [
        Paso(
            rol, "campaign", PATH_CREATE["campaign"], _payload_campana(plan, rol), f"campana {rol}"
        ),
        Paso(
            rol,
            "ad_group",
            PATH_CREATE["ad_group"],
            _payload_ad_group(plan, rol),
            f"ad group {rol}",
        ),
    ]
    pasos.extend(
        Paso(
            rol,
            "product_ad",
            PATH_CREATE["product_ad"],
            {"sku": p.seller_sku, "state": ESTADO_NUEVO},
            f"product ad sku {p.seller_sku}",
        )
        for p in plan.productos
    )
    pasos.extend(_semillas_del_rol(plan, rol))
    return pasos


# ---------------------------------------------------------------------------
# Acks 207 (mismo criterio que el aplicador y archiva_inertes)
# ---------------------------------------------------------------------------


def errores_207(ack: dict) -> list:
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return [{"no_json": ack.get("texto")}]
    errores: list = []
    for valor in cuerpo.values():
        if isinstance(valor, dict):
            errores.extend(valor.get("error") or [])
    return errores


def ack_ok(ack: dict) -> bool:
    return ack.get("status") in (200, 207) and not errores_207(ack)


def id_creado(ack: dict, clave: str) -> str | None:
    """El id del objeto creado segun el ack (success plano o anidado por
    recurso). None = sin id legible (regla 3: jamas inventado)."""
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return None
    for valor in cuerpo.values():
        if not isinstance(valor, dict) or not isinstance(valor.get("success"), list):
            continue
        for item in valor["success"]:
            if not isinstance(item, dict):
                continue
            if item.get(clave) is not None:
                return str(item[clave])
            for sub in item.values():
                if isinstance(sub, dict) and sub.get(clave) is not None:
                    return str(sub[clave])
    return None


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def lineas_dry_run(plan: PlanGrupo) -> list[str]:
    s = plan.semillas
    conteo = {
        "category_exact": len(s.exact),
        "category_phrase": len(s.keywords),
        "category_broad": len(s.keywords),
        "product_targeting": len(s.asins),
        "auto_discovery": len(s.negativos),
    }
    lineas = [
        f"grupo {plan.platform} | tipo={plan.tipo_producto} | base={plan.nombre_base!r} | "
        f"target={plan.target.aplicado} ({plan.target.procedencia}) | modo_goal={plan.modo} | "
        f"moneda={plan.moneda}",
    ]
    lineas.extend(
        f"producto {p.product_id} {p.odoo_sku} | asin={p.asin} | sku={p.seller_sku} | "
        f"margen={p.margen_neto_pct}"
        for p in plan.productos
    )
    for rol in ROLES_ORDEN_CREACION:
        p = plan.parametros[rol]
        lineas.append(
            f"{rol} | {nombre_campana(plan.tipo_producto, plan.nombre_base, rol, plan.fecha)} | "
            f"targeting={TARGETING_POR_ROL[rol]} | budget={p.budget} | bid={p.bid} | "
            f"product_ads={len(plan.productos)} | semillas={conteo[rol]}"
        )
    for e in plan.existentes:
        lineas.append(
            f"existente (solo se informa, decision 9): {e.get('external_id')} | "
            f"{e.get('name')} | status={e.get('status')}"
        )
    return lineas
