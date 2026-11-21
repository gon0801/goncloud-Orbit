"""Cliente HTTP de Mercado Libre solo lectura (solo GET, pase del bridge).

Hogar canonico del cliente MeLi (movido desde `app/reputacion_clientes.py` en
Fase 3.1 para eliminar fugas de `app/meli/*` hacia el dominio de reputacion).
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
from app.http_base import validar_ruta_relativa
from app.redaction import install_scrub_filter, register_secret, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

MELI_BASE = "https://api.mercadolibre.com"
MELI_TOKENS_FILENAME = "meli_tokens.json"


class ReputacionError(Exception):
    """Error del cliente MeLi/reputacion. El mensaje NUNCA lleva secretos."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


MeliClientError = ReputacionError


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
    """Pase del bridge: SOLO el `access_token` que el cron del bridge deja en
    `meli_tokens.json`. Orbit nunca refresca tokens de MeLi."""

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
        register_secret(creds.access_token)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._creds = creds
        self._max_intentos = max_intentos
        self._backoff_base = backoff_base
        self._client = httpx.Client(base_url=MELI_BASE, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _request(self, metodo: str, path: str, params: dict | None = None) -> dict:
        if metodo != "GET":
            raise ReputacionError(f"cliente MeLi solo-GET: metodo {metodo} bloqueado")
        validar_ruta_relativa(path, error_cls=ReputacionError, etiqueta="cliente MeLi path")
        headers = {"Authorization": "Bearer " + self._creds.access_token}
        for intento in range(self._max_intentos):
            try:
                resp = self._client.request(metodo, path, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if intento + 1 < self._max_intentos:
                    time.sleep(self._backoff_base * (2**intento))
                    continue
                raise ReputacionError(
                    f"MeLi GET {path}: error de red tras {self._max_intentos} intentos"
                    f" ({type(exc).__name__}: {scrub(str(exc))})"
                ) from None
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
            try:
                data = resp.json()
            except ValueError:
                raise ReputacionError(f"MeLi GET {path}: respuesta no JSON") from None
            if not isinstance(data, dict):
                raise ReputacionError(f"MeLi GET {path}: respuesta no-objeto")
            return data
        raise ReputacionError(f"MeLi GET {path}: sin respuesta")  # inalcanzable

    def get(self, path: str, params: dict | None = None) -> tuple[dict, dt.datetime]:
        """GET + instante de la respuesta (fetched_at)."""
        data = self._request("GET", path, params)
        return data, dt.datetime.now(dt.UTC)

    def items_seller(self, seller_id: int, max_paginas: int = 50) -> tuple[list[str], bool]:
        """IDs unicos de items via scan paginado. Devuelve (ids, truncado)."""
        ids: list[str] = []
        vistos: set[str] = set()
        total_certificado: int | None = None
        params: dict = {"search_type": "scan", "limit": 100}
        for _ in range(max_paginas):
            data, _ = self.get(f"/users/{seller_id}/items/search", params)
            resultados = data.get("results")
            if not isinstance(resultados, list):
                return ids, True
            pagina: list[str] = []
            for crudo in resultados:
                item_id = _id_item_valido(crudo)
                if item_id is None:
                    return ids, True
                pagina.append(item_id)
            paging = data.get("paging")
            if paging is not None and not isinstance(paging, dict):
                return ids, True
            total = _total_valido(paging.get("total")) if paging is not None else None
            nuevos: list[str] = []
            for item_id in pagina:
                if item_id not in vistos:
                    vistos.add(item_id)
                    nuevos.append(item_id)
            ids.extend(nuevos)
            if total is not None:
                if total_certificado is not None and total != total_certificado:
                    return ids, True
                total_certificado = total
            if total_certificado is not None:
                if len(vistos) > total_certificado:
                    return ids, True
                if total is not None and len(vistos) == total_certificado:
                    return ids, False
            if not pagina:
                return ids, True
            scroll = data.get("scroll_id")
            if not isinstance(scroll, str) or not scroll.strip():
                return ids, True
            if not nuevos:
                return ids, True
            params = {"search_type": "scan", "scroll_id": scroll}
        return ids, True
