"""Tipos, parsers de wire SP v3 y lectura LIST paginada de harvest (ORBIT 04).

Extraido de `app/apply_harvest.py` (Ola D3b). Modulo hoja que concentra las
estructuras de datos, `ListaIncompleta`, `_lista_completa`, validacion de
identidad viva y evaluacion de acks de reversa sobre `app.ads.sp_wire`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from app import apply
from app.ads.client import AdsApiError, AdsClientError
from app.ads.sp_wire import (
    errores_de_ack as _errores_de_ack,
)
from app.ads.sp_wire import (
    estado_id_de_ack as _estado_id_de_ack,
)
from app.ads.sp_wire import (
    id_de_ack as _id_de_ack,  # noqa: F401 - re-export estable
)
from app.apply_estados import EventoCambio, evento_desde_lectura_objeto

if TYPE_CHECKING:
    from app.apply import CapSaturado

logger = logging.getLogger(__name__)

PATH_BID_SUGERIDO = "/sp/keywords/bidRecommendations"

_CONTENEDORES_LIST = {
    "/sp/keywords/list": "keywords",
    "/sp/negativeKeywords/list": "negativeKeywords",
}
TOPE_PAGINAS_LIST = 20

MOTIVO_SIN_CONFIG = "harvest_sin_config"
MOTIVO_ENTIDAD_INCOMPLETA = "entidad_incompleta"
MOTIVO_BID_DEFAULT_FALTANTE = "bid_default_faltante"
MOTIVO_MONEDA_INCOHERENTE = "moneda_incoherente"
MOTIVO_FALLO_NEGATIVE = "fallo_negative"
MOTIVO_FALLO_KEYWORD = "fallo_keyword"
MOTIVO_KEYWORD_AUSENTE = "keyword_ausente"
MOTIVO_TOPE_INTENTOS = "tope_intentos"
MOTIVO_ARCHIVADO_EN_VUELO = "archivado_en_vuelo"
MOTIVO_HERMANAS_PENDIENTES = "hermanas_pendientes"

ROLES_DISCOVERY = (
    "auto_discovery",
    "category_phrase",
    "category_broad",
    "product_targeting",
)
TOPE_CICLOS_HERMANAS = 3
TOPE_INTENTOS_HERMANA = 3


@dataclass(frozen=True)
class AlertaHarvest:
    motivo: str
    decision_id: int
    search_term: str
    plataforma: str
    job_id: int | None
    detalle: str
    envio_fallido: bool = False


@dataclass(frozen=True)
class ResultadoHarvest:
    estado: str
    alerta: AlertaHarvest | None = None
    caps_saturados: tuple[CapSaturado, ...] = ()


@dataclass(frozen=True)
class ResumenReconciliacion:
    jobs_done: int
    jobs_failed: int
    negativas_confirmadas: int
    negativas_fallidas: int
    jobs_cerrados_por_cola: int
    alertas: tuple[AlertaHarvest, ...]
    pausas_confirmadas: int = 0
    pausas_fallidas: int = 0
    harvest_huerfanas_cerradas: int = 0
    caps_saturados: tuple[CapSaturado, ...] = ()


@dataclass
class _Job:
    id: int
    decision_id: int
    search_term: str
    ad_entity_id: int
    fase: str
    external_ids: dict
    plataforma: str


@dataclass(frozen=True)
class _Contexto:
    plataforma: str
    grupo_ext: str
    campana_ext: str
    destino_grupo: str
    destino_campana: str
    default_bid: Decimal
    moneda: str
    floor: Decimal
    ceiling: Decimal
    resuelto_por: str | None = None
    grupo_id: int | None = None


@dataclass(frozen=True)
class PasoReversa:
    clase: str  # "keyword" | "negative"
    rol: str | None
    ad_group_ext: str
    objeto_id: str
    provisional: bool = False


def _job_de_fila(fila) -> _Job:
    return _Job(
        id=fila[0],
        decision_id=fila[1],
        search_term=fila[2],
        ad_entity_id=fila[3],
        fase=fila[4],
        external_ids=dict(fila[5] or {}),
        plataforma=fila[6],
    )


def bid_sugerido(cliente, keyword_id: str | int | None = None) -> Decimal | None:
    """Sugerencia de bid de Amazon para una keyword, o None (fail-open al default)."""
    path = PATH_BID_SUGERIDO if keyword_id is None else f"{PATH_BID_SUGERIDO}/{keyword_id}"
    try:
        resp = cliente.get_sellado(path)
        data = resp.json()
    except (AdsClientError, httpx.HTTPError, ValueError):
        logger.debug("bid sugerido no disponible (fail-open al default sellado): %s", path)
        return None
    return _sugerido_de(data)


def _sugerido_de(data: object) -> Decimal | None:
    if not isinstance(data, dict):
        return None
    candidatos = [data.get("suggestedBid"), data.get("bid")]
    anidados = data.get("recommendations")
    if isinstance(anidados, list) and anidados and isinstance(anidados[0], dict):
        candidatos.extend([anidados[0].get("suggestedBid"), anidados[0].get("bid")])
    for valor in candidatos:
        if isinstance(valor, str | int) and str(valor).strip():
            try:
                return Decimal(str(valor))
            except ArithmeticError:
                continue
    return None


def bid_efectivo(
    sugerido: Decimal | None,
    default: Decimal | None,
    floor: Decimal,
    ceiling: Decimal,
) -> Decimal:
    """Bid que sale al POST: sugerencia o default congelado, clampeado [floor, ceiling]."""
    base = sugerido if sugerido is not None else default
    if base is None:
        raise ValueError("sin sugerencia ni default de bid: no se inventa (regla 3)")
    return min(max(base, floor), ceiling)


class ListaIncompleta(AdsApiError):
    """Lectura LIST de Amazon Ads sin completitud probada (truncada o ambigua)."""

    def __init__(self, message: str) -> None:
        self.causa = "truncado" if message.startswith("truncado:") else "ambiguo"
        super().__init__(message)


def _valida_token_list(token) -> str:
    if token is None:
        return "fin"
    if isinstance(token, str) and token.strip():
        return "mas"
    return "ambiguo"


def _id_list_valido(valor) -> bool:
    return isinstance(valor, str | int) and not isinstance(valor, bool) and bool(str(valor).strip())


_MATCH_POR_CONTENEDOR = {
    "negativeKeywords": frozenset({"NEGATIVE_EXACT", "NEGATIVE_PHRASE"}),
    "keywords": frozenset({"EXACT", "PHRASE", "BROAD"}),
}
_ESTADOS_LIST = frozenset(
    {
        apply.ESTADO_WIRE_ENABLED,
        apply.ESTADO_WIRE_PAUSED,
        apply.ESTADO_WIRE_ARCHIVED,
    }
)


def _texto_list_valido(valor) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def _elemento_list_valido(item, contenedor: str) -> bool:
    if not isinstance(item, dict):
        return False
    if not _id_list_valido(item.get("keywordId")):
        return False
    if not _id_list_valido(item.get("adGroupId")):
        return False
    if not _texto_list_valido(item.get("keywordText")):
        return False
    match = item.get("matchType")
    if not isinstance(match, str) or match not in _MATCH_POR_CONTENEDOR.get(
        contenedor, frozenset()
    ):
        return False
    state = item.get("state")
    return isinstance(state, str) and state in _ESTADOS_LIST


def _valida_pagina_list(data, contenedor: str) -> tuple[list[dict], bool]:
    if not isinstance(data, dict):
        return ([], False)
    crudo = data.get(contenedor)
    if not isinstance(crudo, list):
        return ([], False)
    if not all(_elemento_list_valido(x, contenedor) for x in crudo):
        return ([], False)
    return (list(crudo), True)


def _lista_completa(
    cliente,
    path: str,
    *,
    grupos: tuple[str, ...] | None = None,
) -> list[dict]:
    """Unico lector paginado de listas SP sobre `list_sellado`."""
    contenedor = _CONTENEDORES_LIST[path]
    if grupos is not None:
        if not all(_id_list_valido(g) for g in grupos):
            raise ValueError("adGroupIdFilter requiere IDs validos")
        grupos_norm = tuple(str(g).strip() for g in grupos)
        if not grupos_norm:
            return []
        dentro: frozenset[str] | None = frozenset(grupos_norm)
    else:
        grupos_norm = None
        dentro = None
    items: list[dict] = []
    vistos: set[str] = set()
    token = None
    for _ in range(TOPE_PAGINAS_LIST):
        body: dict = (
            {} if grupos_norm is None else {"adGroupIdFilter": {"include": list(grupos_norm)}}
        )
        if token is not None:
            body["nextToken"] = token
        try:
            data = cliente.list_sellado(path, body).json()
        except ValueError:
            raise ListaIncompleta("ambiguo: JSON ilegible") from None
        pagina, valida = _valida_pagina_list(data, contenedor)
        if not valida:
            raise ListaIncompleta("ambiguo: contenedor o item invalido")
        if dentro is not None and any(str(x["adGroupId"]) not in dentro for x in pagina):
            raise ListaIncompleta("ambiguo: respuesta fuera del filtro")
        estado = _valida_token_list(data.get("nextToken"))
        if estado == "ambiguo":
            raise ListaIncompleta("ambiguo: token ilegible")
        items.extend(pagina)
        if estado == "fin":
            return items
        token = data["nextToken"]
        if token in vistos:
            raise ListaIncompleta("ambiguo: token repetido")
        vistos.add(token)
    raise ListaIncompleta("truncado: token vivo al agotar el tope")


def _identidad(items: list[dict], ad_group_id: str, keyword_text: str) -> dict | None:
    """IDENTIDAD COMPLETA viva: mismo adGroupId + mismo texto + match EXACT."""
    for item in items:
        if str(item.get("state", "")).upper() == apply.ESTADO_WIRE_ARCHIVED:
            continue
        if str(item.get("adGroupId", "")) != str(ad_group_id):
            continue
        if item.get("keywordText") != keyword_text:
            continue
        if str(item.get("matchType", "")).casefold().replace("negative_", "") != "exact":
            continue
        return item
    return None


def _coincidencias(items: list[dict], ad_group_id: str, keyword_text: str) -> list[dict]:
    """TODAS las identidades vivas coincidentes."""
    return [
        item
        for item in items
        if str(item.get("state", "")).upper() != apply.ESTADO_WIRE_ARCHIVED
        and str(item.get("adGroupId", "")) == str(ad_group_id)
        and item.get("keywordText") == keyword_text
        and str(item.get("matchType", "")).casefold().replace("negative_", "") == "exact"
    ]


def _solo_en_otro_ad_group(items: list[dict], ad_group_id: str, keyword_text: str) -> bool:
    """True si existe texto vivo solo en OTRO ad group (senuelo)."""
    if _identidad(items, ad_group_id, keyword_text) is not None:
        return False
    for item in items:
        if str(item.get("state", "")).upper() == apply.ESTADO_WIRE_ARCHIVED:
            continue
        if str(item.get("adGroupId", "")) == str(ad_group_id):
            continue
        if item.get("keywordText") == keyword_text:
            return True
    return False


def evento_de_identidad(
    items: list[dict],
    *,
    id_propio: str | None,
    ad_group: str,
    texto: str,
    match: str,
    destino: str = "vivo",
) -> EventoCambio:
    """El resolver unico de identidad (ARREGLOS 05 B) sobre una lectura
    completa. `id_propio` es el del ACK, o None antes de crear."""
    return evento_desde_lectura_objeto(
        items,
        id_propio=id_propio,
        ad_group=ad_group,
        texto=texto,
        match=match,
        destino=destino,
    )


def motivo_identidad(evento: EventoCambio) -> str:
    """Lo que la lectura concluyo cuando NO autoriza tocar el objeto."""
    if evento == EventoCambio.IDENTIDAD_SUSTITUIDA:
        return "sustituida: vive otra con la misma identidad bajo otro id y no se toca"
    if evento == EventoCambio.IDENTIDAD_DUDOSA:
        return "identidad dudosa o discordante: no se concluye nada y no se toca"
    return "la lectura no autoriza tocarlo"


def ausencia_previa(plataforma: str, lectura: str, ad_group: str, texto: str, match: str) -> dict:
    """Evidencia durable (0095): una lectura completa no encontro viva esa
    identidad inmediatamente antes del POST."""
    return {
        "lectura": lectura,
        "plataforma": plataforma,
        "identidad": {"adGroupId": str(ad_group), "keywordText": texto, "matchType": match},
    }


def _reversa_rechazada(ack: dict, clave_id: str, objeto_id) -> bool:
    """True si el ack de un delete NO confirma el borrado."""
    if _errores_de_ack(ack):
        return True
    presente, id_ack = _estado_id_de_ack(ack, clave_id)
    if presente:
        return id_ack != str(objeto_id)
    return False


def _resultado_reversa_rechazada(ack: dict) -> str:
    return f"fallo:reversa_rechazada: {str(ack)[:300]}"
