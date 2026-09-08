"""Clientes de red de reputacion: MeLi solo-GET + junglee pagado (A.2/A.4).

Partido de app/reputacion.py por el guardrail de 900 lineas (D-LEAD-A4-4):
este modulo es IO puro, sin planes ni SQL.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.redaction import install_scrub_filter, register_secret

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

MELI_BASE = "https://api.mercadolibre.com"
APIFY_BASE = "https://api.apify.com/v2"
ACTOR_JUNGLEE_DEFAULT = "junglee~Amazon-crawler"
# Medido E/0.2 con 2 corridas chicas; la primera corrida real fija el numero.
TARIFA_JUNGLEE_DEFAULT = 0.0025

DEFAULT_SECRETS_DIR = "/mnt/data/appdata/orbit/secrets"
MELI_TOKENS_FILENAME = "meli_tokens.json"
APIFY_TOKEN_FILENAME = "apify_token.json"


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
    """Token MeLi + app para refresh. Un solo refrescador de SU archivo."""

    access_token: str
    refresh_token: str
    client_id: str
    client_secret: str
    ruta: Path

    def __repr__(self) -> str:
        return "MeliCredentials(***)"

    __str__ = __repr__

    @classmethod
    def from_secrets_dir(cls, secrets_dir: str | Path | None = None) -> MeliCredentials:
        base = Path(secrets_dir or os.environ.get("ORBIT_SECRETS_DIR", DEFAULT_SECRETS_DIR))
        ruta = base / MELI_TOKENS_FILENAME
        data = _load_json(ruta)
        access = _require(data, "access_token", MELI_TOKENS_FILENAME)
        refresh = _require(data, "refresh_token", MELI_TOKENS_FILENAME)
        client_id = _require(data, "client_id", MELI_TOKENS_FILENAME)
        client_secret = _require(data, "client_secret", MELI_TOKENS_FILENAME)
        register_secret(access)
        register_secret(refresh)
        register_secret(client_secret)
        return cls(access, refresh, client_id, client_secret, ruta)

    def guardar_tokens(self, access_token: str, refresh_token: str) -> None:
        """Reescritura ATOMICA (tmp + rename) tras un refresh."""
        data = _load_json(self.ruta)
        data["access_token"] = access_token
        data["refresh_token"] = refresh_token
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.rename(self.ruta)
        self.access_token = access_token
        self.refresh_token = refresh_token
        register_secret(access_token)
        register_secret(refresh_token)


@dataclass(repr=False)
class ApifyCredentials:
    """Token Apify (solo lectura de datasets + corridas junglee pagadas)."""

    token: str

    def __repr__(self) -> str:
        return "ApifyCredentials(***)"

    __str__ = __repr__

    @classmethod
    def from_secrets_dir(cls, secrets_dir: str | Path | None = None) -> ApifyCredentials:
        base = Path(secrets_dir or os.environ.get("ORBIT_SECRETS_DIR", DEFAULT_SECRETS_DIR))
        data = _load_json(base / APIFY_TOKEN_FILENAME)
        token = _require(data, "token", APIFY_TOKEN_FILENAME)
        register_secret(token)
        return cls(token)


# ---------------------------------------------------------------------------
# Cliente MeLi: solo GET, default-deny (A.4 lo reutiliza)
# ---------------------------------------------------------------------------


class ClienteMeli:
    """GET a api.mercadolibre.com. Cualquier otro metodo levanta sin red."""

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
        intento_401_hecho = False
        for intento in range(self._max_intentos):
            resp = self._client.request(metodo, path, params=params, headers=headers)
            if resp.status_code == 401:
                if intento_401_hecho:
                    raise ReputacionError(f"MeLi GET {path}: 401 tras refresh")
                intento_401_hecho = True
                self._refresh()
                headers = {"Authorization": "Bearer " + self._creds.access_token}
                continue
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

    def _refresh(self) -> None:
        resp = self._client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self._creds.client_id,
                "client_secret": self._creds.client_secret,
                "refresh_token": self._creds.refresh_token,
            },
        )
        if resp.status_code != 200:
            raise ReputacionError(f"MeLi refresh -> {resp.status_code}")
        data = resp.json()
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not access or not refresh:
            raise ReputacionError("MeLi refresh sin tokens en respuesta")
        self._creds.guardar_tokens(access, refresh)

    def get(self, path: str, params: dict | None = None) -> tuple[dict, dt.datetime]:
        """GET + instante de la respuesta (fetched_at). A.4 reusa este metodo."""
        data = self._request("GET", path, params)
        return data, dt.datetime.now(dt.UTC)

    def items_seller(self, seller_id: int, max_paginas: int = 50) -> list[str]:
        """IDs de items via scan paginado (E/0.3: 65 con scroll_id)."""
        items: list[str] = []
        params: dict = {"search_type": "scan", "limit": 100}
        for _ in range(max_paginas):
            data, _ = self.get(f"/users/{seller_id}/items/search", params)
            resultados = data.get("results") or []
            items.extend(str(i) for i in resultados)
            scroll = data.get("scroll_id")
            if not scroll or not resultados:
                break
            params = {"search_type": "scan", "scroll_id": scroll}
        return items


# ---------------------------------------------------------------------------
# Cliente junglee: run-sync pagado, topes duros (cada intento gasta)
# ---------------------------------------------------------------------------


class ClienteJunglee:
    """Snapshots Amazon via junglee/Amazon-crawler (E/0.2 verificado)."""

    def __init__(
        self,
        creds: ApifyCredentials,
        actor: str = ACTOR_JUNGLEE_DEFAULT,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 300.0,
        tarifa_usd_por_producto: float = TARIFA_JUNGLEE_DEFAULT,
    ) -> None:
        self._creds = creds
        self._actor = actor
        self._tarifa = tarifa_usd_por_producto
        self._client = httpx.Client(base_url=APIFY_BASE, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def scrape_productos(
        self,
        urls: list[str],
        max_items_por_url: int = 1,
        max_productos: int = 600,
        tope_usd: float = 2.0,
    ) -> tuple[list[dict], float]:
        """Corre junglee sobre URLs /dp/ASIN. Devuelve (items, costo_est).

        Topes ANTES de gastar: n productos y costo estimado. Sin
        reintentos automaticos: cada intento cobra. `productPageReviews`
        se ignora (hallazgo no verificado E/0.2).
        """
        if len(urls) > max_productos:
            raise ReputacionError(
                f"junglee: {len(urls)} productos exceden max_productos={max_productos}"
            )
        costo_est = len(urls) * self._tarifa
        if costo_est > tope_usd:
            raise ReputacionError(
                f"junglee: costo est ${costo_est:.2f} excede tope_usd=${tope_usd:.2f}"
            )
        resp = self._client.post(
            f"/acts/{self._actor}/run-sync-get-dataset-items?token={self._creds.token}",
            json={
                "categoryOrProductUrls": [{"url": u} for u in urls],
                "maxItemsPerStartUrl": max_items_por_url,
            },
        )
        if resp.status_code not in (200, 201):
            raise ReputacionError(f"junglee run-sync -> {resp.status_code}")
        items = resp.json()
        if not isinstance(items, list):
            raise ReputacionError("junglee: respuesta no-lista")
        return [i for i in items if isinstance(i, dict)], costo_est


# ---------------------------------------------------------------------------
