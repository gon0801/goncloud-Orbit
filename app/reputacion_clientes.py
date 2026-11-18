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
        """IDs de items via scan paginado. Devuelve (ids, truncado).

        XR-1.8: corte a 50 paginas sin agotar = truncado para skip.
        Grok XR-1 MEDIA-4: corta tambien si paging.total ya se alcanzo
        (la pagina extra con scroll vivo puede dar 400/scan-expirado
        y tumbaria la corrida entera).
        """
        items: list[str] = []
        params: dict = {"search_type": "scan", "limit": 100}
        for _ in range(max_paginas):
            data, _ = self.get(f"/users/{seller_id}/items/search", params)
            resultados = data.get("results") or []
            items.extend(str(i) for i in resultados)
            total = (data.get("paging") or {}).get("total")
            if isinstance(total, int) and len(items) >= total:
                return items, False
            scroll = data.get("scroll_id")
            if not scroll or not resultados:
                return items, False
            params = {"search_type": "scan", "scroll_id": scroll}
        return items, True
