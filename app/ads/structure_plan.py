from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.ads.structure_api import EstructuraAds

# Etiquetas legibles para los motivos de skip (sin acronimos de tabla).
_ETIQUETA_KIND = {
    "campaign": "campana",
    "ad_group": "ad group",
    "keyword": "keyword",
    "product_target": "target",
    "product_ad": "product ad",
}
_ETIQUETA_PADRE = {
    "campaign": "campana",
    "ad_group": "campana",
    "keyword": "ad group",
    "product_target": "ad group",
    "product_ad": "ad group",
}
# Solo estos estados se materializan. ARCHIVED (75% MX) queda fuera: no
# upsert, no listing_id. Cualquier otro valor es "estado desconocido".
_ESTADOS_PRODUCT_AD_VIVOS = frozenset({"ENABLED", "PAUSED"})
ESTADO_ARCHIVED = "ARCHIVED"


@dataclass
class _ItemEntidad:
    """Un item planificado: lo que se escribe si ningun gate lo salta."""

    platform: str
    kind: str
    external_id: str
    parent_ref: tuple[str, str, str] | None  # (platform, kind, external_id) del padre
    name: str | None
    match_type: str | None
    keyword_text: str | None
    status: str | None
    targeting_type: str | None
    bid: Decimal | None
    bid_currency: str | None
    asin: str | None = None  # solo product_ad; llave de join a listing.external_id


# ---------------------------------------------------------------------------
# Planificacion pura (sin DB): items validos + skips con motivo
# ---------------------------------------------------------------------------


def _bid_decimal(valor: object, campo: str) -> Decimal | None:
    """Convierte un bid del payload a Decimal (via str, sin meter float a NUMERIC).

    None (ausente o null) -> None (regla 3: en keywords de campanas AUTO y en
    parte de los targets la API NO trae `bid`; corrida real 2026-08-22).
    Un valor presente pero no numerico (o no finito) es dato corrupto:
    ValueError para que el item se salte con motivo, no un numero inventado.
    Un bid <= 0 tambien es payload invalido (hallazgo CodeRabbit): Amazon no
    publica pujas no positivas y el esquema sella la positividad donde puede
    (goal_bids_positivos); para el cache, la puerta es esta.
    """
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str, Decimal)):
        raise ValueError(f"{campo} no numerico")
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{campo} no numerico") from None
    if not numero.is_finite() or numero <= 0:
        raise ValueError(f"{campo} no numerico o no positivo")
    return numero


def _nombre_target(expression: object) -> str | None:
    """JSON compacto y determinista de `expression` (None si no viene)."""
    if expression is None:
        return None
    return json.dumps(expression, separators=(",", ":"), sort_keys=True)


def _item_product_ad(
    payload: dict,
    *,
    platform: str,
    conocidos: set[tuple[str, str, str]],
    campana_del_ad_group: dict[str, str],
) -> _ItemEntidad | str:
    """Filtra un product ad en el borde: item listo o motivo de skip."""
    external = payload.get("adId")
    if external is None:
        return "product ad sin adId"
    asin_crudo = payload.get("asin")
    asin = asin_crudo.strip() if isinstance(asin_crudo, str) else ""
    if not asin:
        return "product ad sin asin"
    estado = payload.get("state")
    if estado == ESTADO_ARCHIVED:
        return "product ad archivado"
    if estado not in _ESTADOS_PRODUCT_AD_VIVOS:
        return "product ad estado desconocido"
    ad_group_id = str(payload.get("adGroupId"))
    if (platform, "ad_group", ad_group_id) not in conocidos:
        return "product ad sin ad group planificado"
    campaign_id = payload.get("campaignId")
    if campaign_id is not None and str(campaign_id) != campana_del_ad_group[ad_group_id]:
        return "product ad con campaignId distinto al de su ad group (payload incoherente)"
    return _ItemEntidad(
        platform=platform,
        kind="product_ad",
        external_id=str(external),
        parent_ref=(platform, "ad_group", ad_group_id),
        name=asin,
        match_type=None,
        keyword_text=None,
        status=estado,
        targeting_type=None,
        bid=None,
        bid_currency=None,
        asin=asin,
    )


def _archivados_por_plataforma(estructura: EstructuraAds) -> dict[str, list[str]]:
    """adIds que el payload reporta ARCHIVED, por plataforma.

    Los usa `sync_structure` para poner al dia el cache de los product ads
    que YA seguimos (ver _SQL_MARCAR_ARCHIVADOS). Se leen del payload crudo
    y no de los items porque `_plan_items` descarta los archivados al borde.
    """
    archivados: dict[str, list[str]] = {}
    for est in estructura.estructuras:
        platform = est.perfil.platform or ""
        ids = [
            str(p.get("adId"))
            for p in est.product_ads
            if p.get("state") == ESTADO_ARCHIVED and p.get("adId") is not None
        ]
        if ids:
            archivados.setdefault(platform, []).extend(ids)
    return archivados


def _plan_items(estructura: EstructuraAds) -> tuple[list[_ItemEntidad], Counter[str]]:
    """Valida la coherencia de cada item ANTES de tocar la base.

    Orden por perfil: campanas -> ad groups -> product ads -> keywords ->
    targets (el padre siempre se planifica antes que el hijo). Un item solo
    entra si su padre esta entre los items ya planificados DE ESTA CORRIDA y,
    para keywords, targets y product ads, si el campaignId que el payload
    trae DE MAS no contradice al del ad group padre planificado (hallazgo
    cross-review codex, ronda 3). Claves v3: ids string planos, defaultBid
    escalar, bid OPCIONAL (su ausencia no es skip). Product ads: solo
    ENABLED/PAUSED; asin obligatorio; bid tipicamente ausente.
    """
    items: list[_ItemEntidad] = []
    skips: Counter[str] = Counter()

    for est in estructura.estructuras:
        platform = est.perfil.platform or ""
        moneda = est.perfil.moneda
        conocidos: set[tuple[str, str, str]] = set()
        # adGroupId -> campaignId con el que se planifico el ad group (para
        # validar la coherencia del campaignId que keyword/target traen DE
        # MAS en su payload; hallazgo cross-review codex, ronda 3).
        campana_del_ad_group: dict[str, str] = {}

        for payload in est.campanas:
            external = payload.get("campaignId")
            if external is None:
                skips["campana sin campaignId"] += 1
                continue
            items.append(
                _ItemEntidad(
                    platform=platform,
                    kind="campaign",
                    external_id=str(external),
                    parent_ref=None,
                    name=payload.get("name"),
                    match_type=None,
                    keyword_text=None,
                    status=payload.get("state"),
                    targeting_type=payload.get("targetingType"),
                    bid=None,  # budget sin moneda: no se guarda (docstring)
                    bid_currency=None,
                )
            )
            conocidos.add((platform, "campaign", str(external)))

        for payload in est.ad_groups:
            external = payload.get("adGroupId")
            if external is None:
                skips["ad group sin adGroupId"] += 1
                continue
            if (platform, "campaign", str(payload.get("campaignId"))) not in conocidos:
                skips["ad group sin campana planificada"] += 1
                continue
            try:
                bid = _bid_decimal(payload.get("defaultBid"), "defaultBid")
            except ValueError:
                skips["ad group con defaultBid no numerico o no positivo"] += 1
                continue
            items.append(
                _ItemEntidad(
                    platform=platform,
                    kind="ad_group",
                    external_id=str(external),
                    parent_ref=(platform, "campaign", str(payload.get("campaignId"))),
                    name=payload.get("name"),
                    match_type=None,
                    keyword_text=None,
                    status=payload.get("state"),
                    targeting_type=None,
                    bid=bid,
                    bid_currency=moneda if bid is not None else None,
                )
            )
            conocidos.add((platform, "ad_group", str(external)))
            campana_del_ad_group[str(external)] = str(payload.get("campaignId"))

        for payload in est.product_ads:
            resultado = _item_product_ad(
                payload,
                platform=platform,
                conocidos=conocidos,
                campana_del_ad_group=campana_del_ad_group,
            )
            if isinstance(resultado, str):
                skips[resultado] += 1
                continue
            items.append(resultado)
            conocidos.add((platform, "product_ad", resultado.external_id))

        for payload in est.keywords:
            external = payload.get("keywordId")
            if external is None:
                skips["keyword sin keywordId"] += 1
                continue
            keyword_text = payload.get("keywordText")
            if not keyword_text:
                skips["keyword sin keywordText"] += 1
                continue
            match_type = payload.get("matchType")
            if not match_type:
                skips["keyword sin matchType"] += 1
                continue
            ad_group_id = str(payload.get("adGroupId"))
            if (platform, "ad_group", ad_group_id) not in conocidos:
                skips["keyword sin ad group planificado"] += 1
                continue
            campaign_id = payload.get("campaignId")
            if campaign_id is not None and str(campaign_id) != campana_del_ad_group[ad_group_id]:
                skips[
                    "keyword con campaignId distinto al de su ad group (payload incoherente)"
                ] += 1
                continue
            try:
                bid = _bid_decimal(payload.get("bid"), "bid")
            except ValueError:
                skips["keyword con bid no numerico o no positivo"] += 1
                continue
            items.append(
                _ItemEntidad(
                    platform=platform,
                    kind="keyword",
                    external_id=str(external),
                    parent_ref=(platform, "ad_group", ad_group_id),
                    name=None,  # keyword_text es la fuente unica (regla 2)
                    match_type=match_type,
                    keyword_text=keyword_text,
                    status=payload.get("state"),
                    targeting_type=None,
                    bid=bid,
                    bid_currency=moneda if bid is not None else None,
                )
            )
            conocidos.add((platform, "keyword", str(external)))

        for payload in est.targets:
            external = payload.get("targetId")
            if external is None:
                skips["target sin targetId"] += 1
                continue
            ad_group_id = str(payload.get("adGroupId"))
            if (platform, "ad_group", ad_group_id) not in conocidos:
                skips["target sin ad group planificado"] += 1
                continue
            campaign_id = payload.get("campaignId")
            if campaign_id is not None and str(campaign_id) != campana_del_ad_group[ad_group_id]:
                skips["target con campaignId distinto al de su ad group (payload incoherente)"] += 1
                continue
            try:
                bid = _bid_decimal(payload.get("bid"), "bid")
            except ValueError:
                skips["target con bid no numerico o no positivo"] += 1
                continue
            items.append(
                _ItemEntidad(
                    platform=platform,
                    kind="product_target",
                    external_id=str(external),
                    parent_ref=(platform, "ad_group", ad_group_id),
                    name=_nombre_target(payload.get("expression")),
                    match_type=None,
                    keyword_text=None,
                    status=payload.get("state"),
                    targeting_type=None,
                    bid=bid,
                    bid_currency=moneda if bid is not None else None,
                )
            )
            conocidos.add((platform, "product_target", str(external)))

    return items, skips


def _formato_skip_reason(skips: Counter[str]) -> str | None:
    """Concatena motivos con contador, deterministicamente (mas frecuentes primero)."""
    if not skips:
        return None
    partes = [
        f"{cantidad}x {motivo}"
        for motivo, cantidad in sorted(skips.items(), key=lambda par: (-par[1], par[0]))
    ]
    return ", ".join(partes)
