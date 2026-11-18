"""Clientes de red de reputacion: MeLi solo-GET (A.2/A.4).

Partido de app/reputacion.py por el guardrail de 900 lineas (D-LEAD-A4-4):
este modulo es IO puro, sin planes ni SQL.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.ads.config import directorio_secretos
from app.redaction import install_scrub_filter, register_secret

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

MELI_BASE = "https://api.mercadolibre.com"

MELI_TOKENS_FILENAME = "meli_tokens.json"


class ReputacionError(Exception):
    """Error de la ingesta de reputacion. El mensaje NUNCA lleva secretos."""


# ---------------------------------------------------------------------------

# Credenciales (patron app/ads/config.py)
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise ReputacionError(f"no existe el archivo de credenciales: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ReputacionError(f"JSON invalido en {path.name}") from None
    if not isinstance(data, dict):
        raise ReputacionError(f"formato invalido en {path.name}: se esperaba objeto JSON")
    return data


def _require(data: dict, key: str, filename: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ReputacionError(f"falta la clave '{key}' (string no vacio) en {filename}")
    return value


@dataclass(repr=False)
class MeliCredentials:
    """Pase del bridge (opcion 1): SOLO el `access_token` que el cron del
    bridge deja en `meli_tokens.json` (junto a `user_id` y `scope`, que
    aqui no se usan). Orbit NUNCA refresca: no hay `refresh_token` ni
    `client_id`/`client_secret` que pedir, guardar o reescribir."""

    access_token: str

    def __repr__(self) -> str:
        return "MeliCredentials(***)"

    __str__ = __repr__

    @classmethod
    def from_secrets_dir(cls, secrets_dir: str | Path | None = None) -> MeliCredentials:
        base = Path(secrets_dir) if secrets_dir else directorio_secretos()
        data = _load_json(base / MELI_TOKENS_FILENAME)
        access = _require(data, "access_token", MELI_TOKENS_FILENAME)
        register_secret(access)
        return cls(access)


def _id_item_valido(valor: object) -> str | None:
    """Normaliza un ID de item; bool, estructura o vacio no son identidad."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, str):
        texto = valor.strip()
        return texto or None
    return None


def _total_valido(valor: object) -> int | None:
    """`paging.total` util para certificar: entero, no booleano, no negativo."""
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return None
    return valor


# ---------------------------------------------------------------------------
# Cliente MeLi: solo GET, default-deny (A.4 lo reutiliza)
#
# Pase del bridge (opcion 1): el access_token lo renueva el cron del
# bridge; Orbit jamas renueva tokens. Un 401 es "pase del bridge
# vencido" y aborta claro, sin refresh ni reintento.
# ---------------------------------------------------------------------------


class ClienteMeli:
    """GET a api.mercadolibre.com. Cualquier otro metodo levanta sin red.

    Ante 401 levanta `ReputacionError` ("pase del bridge vencido"):
    el token lo renueva el cron del bridge, Orbit nunca refresca.
    """

    def __init__(
        self,
        creds: MeliCredentials,
        transport: httpx.BaseTransport | None = None,
        max_intentos: int = 3,
        backoff_base: float = 0.5,
        timeout: float = 30.0,
    ) -> None:
        self._creds = creds
        self._max_intentos = max_intentos
        self._backoff_base = backoff_base
        self._client = httpx.Client(base_url=MELI_BASE, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _request(self, metodo: str, path: str, params: dict | None = None) -> dict:
        if metodo != "GET":
            raise ReputacionError(f"cliente MeLi solo-GET: metodo {metodo} bloqueado")
        headers = {"Authorization": "Bearer " + self._creds.access_token}
        for intento in range(self._max_intentos):
            resp = self._client.request(metodo, path, params=params, headers=headers)
            if resp.status_code == 401:
                raise ReputacionError(
                    f"MeLi GET {path}: 401 (pase del bridge vencido;"
                    " Orbit nunca refresca: lo renueva el cron del bridge)"
                )
            if resp.status_code in (429,) or resp.status_code >= 500:
                if intento + 1 < self._max_intentos:
                    time.sleep(self._backoff_base * (2**intento))
                    continue
                raise ReputacionError(
                    f"MeLi GET {path}: agotados {self._max_intentos} intentos"
                    f" (ultimo {resp.status_code})"
                )
            if resp.status_code != 200:
                raise ReputacionError(f"MeLi GET {path} -> {resp.status_code}")
            data = resp.json()
            if not isinstance(data, dict):
                raise ReputacionError(f"MeLi GET {path}: respuesta no-objeto")
            return data
        raise ReputacionError(f"MeLi GET {path}: sin respuesta")  # inalcanzable

    def get(self, path: str, params: dict | None = None) -> tuple[dict, dt.datetime]:
        """GET + instante de la respuesta (fetched_at). A.4 reusa este metodo."""
        data = self._request("GET", path, params)
        return data, dt.datetime.now(dt.UTC)

    def items_seller(self, seller_id: int, max_paginas: int = 50) -> tuple[list[str], bool]:
        """IDs unicos de items via scan paginado. Devuelve (ids, truncado).

        ARREGLOS 03 D-M (TN06): este metodo es el dueno de decidir si el
        scan termino; catalogo y reputacion consumen su certificado.
        `truncado=False` exige cobertura certificada: paginas validas
        (contenedor `results` lista e IDs legibles) que alcanzan exactamente
        un `paging.total` entero no booleano, no negativo y coherente entre
        paginas, contando IDs unicos (los duplicados no acreditan
        completitud). Total 0 con contenedor valido vacio demuestra
        catalogo vacio. Cualquier otra salida (scroll ausente o ilegible,
        token repetido sin avance, fin prematuro, pagina malformada, total
        cambiante o menor que lo observado unico, tope de paginas agotado)
        deja la cobertura desconocida: devuelve (ids_observados, True).
        Los errores de transporte conservan su tratamiento seguro (raise),
        nunca se convierten en lista vacia completa.
        XR-1.8: corte a max_paginas sin certificar = truncado para skip.
        Grok XR-1 MEDIA-4: al alcanzar el total no se pide la pagina extra
        con scroll vivo (daria 400/scan-expirado y tumbaria la corrida).
        """
        ids: list[str] = []
        vistos: set[str] = set()
        total_certificado: int | None = None
        params: dict = {"search_type": "scan", "limit": 100}
        for _ in range(max_paginas):
            data, _ = self.get(f"/users/{seller_id}/items/search", params)
            resultados = data.get("results")
            if not isinstance(resultados, list):
                return ids, True  # pagina malformada: contenedor invalido
            pagina: list[str] = []
            for crudo in resultados:
                item_id = _id_item_valido(crudo)
                if item_id is None:
                    return ids, True  # ID ilegible: la pagina no certifica nada
                pagina.append(item_id)
            paging = data.get("paging")
            if paging is not None and not isinstance(paging, dict):
                return ids, True  # pagina malformada: paging invalido
            total = _total_valido(paging.get("total")) if paging is not None else None
            if total is not None:
                if total_certificado is not None and total != total_certificado:
                    return ids, True  # total cambiante entre paginas
                total_certificado = total
            nuevos: list[str] = []
            for item_id in pagina:
                if item_id not in vistos:
                    vistos.add(item_id)
                    nuevos.append(item_id)
            ids.extend(nuevos)
            if total_certificado is not None:
                if len(vistos) > total_certificado:
                    return ids, True  # mas unicos que el total: incoherente
                if len(vistos) == total_certificado:
                    return ids, False  # cobertura certificada
            if not pagina:
                return ids, True  # fin prematuro: pagina vacia sin el total
            scroll = data.get("scroll_id")
            if not isinstance(scroll, str) or not scroll.strip():
                return ids, True  # scroll ausente/ilegible con paginas por leer
            if not nuevos:
                return ids, True  # token vivo sin avance: no certifica
            params = {"search_type": "scan", "scroll_id": scroll}
        return ids, True  # tope de paginas agotado sin certificar
