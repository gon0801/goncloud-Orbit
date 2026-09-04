"""Etiqueta humana de una ad_entity para el dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass

TIPOS_TARGET: dict[str, str] = {
    "ASIN_SAME_AS": "mismo ASIN",
    "ASIN_ACCESSORY_RELATED": "accesorio",
    "ASIN_SUBSTITUTE_RELATED": "sustituto",
    "ASIN_ACCESSORY": "accesorio",
    "ASIN_BRAND_SAME_AS": "misma marca",
    "ASIN_CATEGORY_SAME_AS": "misma categoria",
    "ASIN_EXPANDED_FROM": "expandido de",
    "ASIN_AGE_RANGE_SAME_AS": "misma edad",
    "ASIN_GENRE_SAME_AS": "mismo genero",
    "ASIN_IS_PRIME_SHIPPING_ELIGIBLE": "Prime",
    "ASIN_PRICE_LESS_THAN": "precio menor a",
    "ASIN_PRICE_GREATER_THAN": "precio mayor a",
    "ASIN_PRICE_BETWEEN": "precio entre",
    "ASIN_REVIEW_RATING_LESS_THAN": "rating menor a",
    "ASIN_REVIEW_RATING_GREATER_THAN": "rating mayor a",
    "ASIN_REVIEW_RATING_BETWEEN": "rating entre",
    "QUERY_BROAD_REL_MATCHES": "query amplia",
    "QUERY_HIGH_REL_MATCHES": "query cercana",
}


@dataclass(frozen=True, slots=True)
class EtiquetaEntidad:
    hoja: str | None
    campana: str | None

    def linea(self) -> str | None:
        partes: list[str] = []
        for parte in (self.hoja, self.campana):
            if parte and parte not in partes:
                partes.append(parte)
        return " · ".join(partes) if partes else None


def etiqueta_entidad(
    *,
    kind: str | None,
    name: str | None,
    keyword_text: str | None,
    campana: str | None,
) -> EtiquetaEntidad:
    """Resuelve la etiqueta segun kind. Dato faltante = None, nunca un default."""
    if kind == "keyword":
        return EtiquetaEntidad(hoja=_texto_limpio(keyword_text), campana=_texto_limpio(campana))
    if kind == "product_target":
        return EtiquetaEntidad(hoja=_hoja_target(name), campana=_texto_limpio(campana))
    if kind == "product_ad":
        return EtiquetaEntidad(hoja=_texto_limpio(name), campana=_texto_limpio(campana))
    if kind == "ad_group":
        return EtiquetaEntidad(hoja=_texto_limpio(name), campana=_texto_limpio(campana))
    if kind == "campaign":
        return EtiquetaEntidad(hoja=None, campana=_texto_limpio(campana) or _texto_limpio(name))
    return EtiquetaEntidad(
        hoja=_hoja_target(name) if _parece_expression(name) else _texto_limpio(name),
        campana=_texto_limpio(campana),
    )


def linea_entidad(
    *,
    kind: str | None,
    name: str | None,
    keyword_text: str | None,
    campana: str | None,
) -> str | None:
    return etiqueta_entidad(
        kind=kind, name=name, keyword_text=keyword_text, campana=campana
    ).linea()


def _texto_limpio(valor: str | None) -> str | None:
    if valor is None:
        return None
    texto = valor.strip()
    if not texto or _parece_expression(texto):
        return None
    return texto


def _parece_expression(valor: str | None) -> bool:
    if valor is None:
        return False
    cabeza = valor.lstrip()
    return cabeza.startswith("[") or cabeza.startswith("{")


def _hoja_target(name: str | None) -> str | None:
    if name is None:
        return None
    texto = name.strip()
    if not texto:
        return None
    if not _parece_expression(texto):
        return texto
    try:
        parsed = json.loads(texto)
    except json.JSONDecodeError:
        return None
    predicados = parsed if isinstance(parsed, list) else [parsed]
    partes: list[str] = []
    for pred in predicados:
        parte = _predicado(pred)
        if parte:
            partes.append(parte)
    return " + ".join(partes) if partes else None


def _predicado(pred: object) -> str | None:
    if not isinstance(pred, dict):
        return None
    tipo = pred.get("type")
    if not isinstance(tipo, str) or not tipo.strip():
        return None
    etiqueta = TIPOS_TARGET.get(tipo) or _tipo_desconocido(tipo)
    valor = pred.get("value")
    if valor is None or valor == "":
        return etiqueta
    return f"{etiqueta} {valor}"


def _tipo_desconocido(tipo: str) -> str:
    texto = tipo
    for prefijo in ("ASIN_", "QUERY_"):
        if texto.startswith(prefijo):
            texto = texto[len(prefijo) :]
            break
    return texto.replace("_", " ").lower() or tipo
