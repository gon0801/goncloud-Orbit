"""Miniaturas MAIN de SP-API Catalog Items, separadas por ASIN y marketplace.

Contrato verificado: GET /catalog/2022-04-01/items/{asin}, includedData=images.
La cache es acotada y efimera; nunca sustituye fotos faltantes por otro producto.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.redaction import register_secret

MERCADOS = {"amazon_mx": "A1AM78C64UM0Y8", "amazon_us": "ATVPDKIKX0DER"}
MAX_BYTES = 256 * 1024


class FotoNoDisponible(Exception):
    """Fallo temporal sin respuestas externas ni secretos en el mensaje."""


def _url_main(datos: dict, asin: str, mercado: str) -> str | None:
    if datos.get("asin") != asin:
        return None
    candidatas = []
    for grupo in datos.get("images", []):
        if grupo.get("marketplaceId") != mercado:
            continue
        for foto in grupo.get("images", []):
            if foto.get("variant") != "MAIN":
                continue
            url = foto.get("link", "")
            partes = urlsplit(url)
            if (
                partes.scheme == "https"
                and partes.netloc in {"m.media-amazon.com", "images-na.ssl-images-amazon.com"}
                and partes.path.startswith("/images/I/")
            ):
                lado = max(int(foto.get("width", 0)), int(foto.get("height", 0)))
                # Menor variante suficiente para la miniatura; si no, la mayor disponible.
                candidatas.append(((0, lado) if lado >= 160 else (1, -lado), url))
    return min(candidatas)[1] if candidatas else None


def _es_imagen(datos: bytes, mime: str) -> bool:
    return (
        (mime == "image/jpeg" and datos.startswith(b"\xff\xd8\xff"))
        or (mime == "image/png" and datos.startswith(b"\x89PNG\r\n\x1a\n"))
        or (mime == "image/webp" and datos[:4] == b"RIFF" and datos[8:12] == b"WEBP")
    )


class FotosPublicacion:
    """Un solo fetch en vuelo, maximo 128 miniaturas y 256 KiB por miniatura."""

    def __init__(self, transport=None):
        self._transport = transport
        self._lock = threading.Lock()
        self._cache = OrderedDict()
        self._token = None
        self._vence_token = 0.0
        self._proxima_consulta = 0.0

    def _acceso(self, client: httpx.Client) -> str:
        if self._token and time.monotonic() < self._vence_token:
            return self._token
        base = Path(os.environ.get("ORBIT_SECRETS_DIR", "/mnt/data/appdata/orbit/secrets"))
        config = json.loads((base / "amazon_credentials.json").read_text())
        campos = [config[k] for k in ("lwa_app_id", "lwa_client_secret", "refresh_token")]
        if not all(isinstance(v, str) and v.strip() for v in campos):
            raise ValueError("credenciales incompletas")
        for valor in campos:
            register_secret(valor)
        response = client.post(
            "https://api.amazon.com/auth/o2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": campos[0],
                "client_secret": campos[1],
                "refresh_token": campos[2],
            },
        )
        response.raise_for_status()
        datos = response.json()
        token = datos["access_token"]
        if not isinstance(token, str) or not token:
            raise ValueError("token ausente")
        register_secret(token)
        self._token = token
        self._vence_token = time.monotonic() + max(0, int(datos["expires_in"]) - 60)
        return token

    def _descargar(self, client: httpx.Client, url: str) -> tuple[bytes, str]:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            mime = response.headers.get("content-type", "").split(";", 1)[0]
            datos = bytearray()
            for parte in response.iter_bytes(16384):
                datos.extend(parte)
                if len(datos) > MAX_BYTES:
                    raise ValueError("imagen demasiado grande")
            if not _es_imagen(datos, mime):
                raise ValueError("formato de imagen invalido")
            return bytes(datos), mime

    def _consultar(self, plataforma: str, asin: str) -> tuple[bytes, str] | None:
        with httpx.Client(timeout=8, follow_redirects=False, transport=self._transport) as client:
            token = self._acceso(client)
            time.sleep(max(0, self._proxima_consulta - time.monotonic()))
            self._proxima_consulta = time.monotonic() + 0.6
            response = client.get(
                f"https://sellingpartnerapi-na.amazon.com/catalog/2022-04-01/items/{asin}",
                params={"marketplaceIds": MERCADOS[plataforma], "includedData": "images"},
                headers={"x-amz-access-token": token},
            )
            if response.status_code == 404:
                return None
            if response.status_code in (401, 403):
                self._token = None
            response.raise_for_status()
            url = _url_main(response.json(), asin, MERCADOS[plataforma])
            return self._descargar(client, url) if url else None

    def obtener(self, plataforma: str, asin: str) -> tuple[bytes, str] | None:
        if plataforma not in MERCADOS or not re.fullmatch(r"[A-Z0-9]{10}", asin or ""):
            return None
        if not self._lock.acquire(timeout=10):
            raise FotoNoDisponible("Foto temporalmente no disponible.")
        clave = (plataforma, asin)
        try:
            guardado = self._cache.get(clave)
            if guardado and guardado[0] > time.monotonic():
                self._cache.move_to_end(clave)
                if guardado[2]:
                    raise FotoNoDisponible("Foto temporalmente no disponible.")
                return guardado[1]
            try:
                foto = self._consultar(plataforma, asin)
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError):
                self._guardar(clave, None, 60, fallo=True)
                raise FotoNoDisponible("Foto temporalmente no disponible.") from None
            self._guardar(clave, foto, 86400 if foto else 900)
            return foto
        finally:
            self._lock.release()

    def _guardar(self, clave, foto, ttl, fallo=False):
        self._cache[clave] = (time.monotonic() + ttl, foto, fallo)
        self._cache.move_to_end(clave)
        while len(self._cache) > 128:
            self._cache.popitem(last=False)


fotos_publicacion = FotosPublicacion()
