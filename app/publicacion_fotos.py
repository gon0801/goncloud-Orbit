"""Miniaturas MAIN de SP-API Catalog Items, separadas por ASIN y marketplace.

Contrato verificado: GET /catalog/2022-04-01/items/{asin}, includedData=images.
La cache es acotada y efimera; nunca sustituye fotos faltantes por otro producto.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx

from app.spapi.client import SpapiClient, SpapiError, cliente_compartido

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
    """Un solo fetch en vuelo, maximo 128 miniaturas y 256 KiB por miniatura.

    El refrescador LWA es el unico de `app/spapi` (D5): el token sale de un
    `SpapiClient` compartido por proceso. El throttle (0.6 s), la cache y el
    manejo de 404/401/403 quedan intactos.
    """

    def __init__(self, transport=None, spapi_client: SpapiClient | None = None):
        self._transport = transport
        self._cache_lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._cache = OrderedDict()
        self._proxima_consulta = 0.0
        self._spapi: SpapiClient | None = spapi_client
        self._http: httpx.Client | None = None

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _cliente_http(self) -> httpx.Client:
        if self._http is None or self._http.is_closed:
            self._http = httpx.Client(timeout=8, follow_redirects=False, transport=self._transport)
        return self._http

    def _spapi_cliente(self) -> SpapiClient:
        # Creacion perezosa: los tests fijan ORBIT_SECRETS_DIR antes de usar.
        # Sin transporte (produccion) se usa la instancia compartida por
        # proceso; con transporte mock, instancia privada. El reloj se
        # resuelve tarde (`time.monotonic` por atributo) para que el
        # monkeypatch de tiempo de los tests expire el token igual que antes.
        if self._spapi is None:
            if self._transport is None:
                self._spapi = cliente_compartido(timeout=30.0)
            else:
                self._spapi = SpapiClient(
                    transport=self._transport,
                    clock=lambda: time.monotonic(),
                    sleep=lambda s: time.sleep(s),
                )
        return self._spapi

    def _acceso(self, client: httpx.Client | None = None) -> str:
        # `client` se conserva por compatibilidad (antes hacia el POST LWA);
        # el token ahora sale del refrescador unico. Solo fallos de auth/red
        # se traducen a ValueError (obtener los mapea a no disponible); un
        # bug inesperado propaga para no enmascarar el diagnostico.
        try:
            return self._spapi_cliente().token_acceso()
        except ValueError:
            raise
        except (SpapiError, httpx.HTTPError, OSError) as exc:
            raise ValueError("token ausente") from exc

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
        client = self._cliente_http()
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
            self._spapi_cliente().invalidar_token()
        response.raise_for_status()
        url = _url_main(response.json(), asin, MERCADOS[plataforma])
        return self._descargar(client, url) if url else None

    def _leer_cache(self, clave: tuple[str, str]):
        with self._cache_lock:
            guardado = self._cache.get(clave)
            if guardado and guardado[0] > time.monotonic():
                self._cache.move_to_end(clave)
                return True, guardado[1], guardado[2]
        return False, None, False

    def obtener(self, plataforma: str, asin: str) -> tuple[bytes, str] | None:
        if plataforma not in MERCADOS or not re.fullmatch(r"[A-Z0-9]{10}", asin or ""):
            return None
        clave = (plataforma, asin)
        hit, foto_cache, fallo_cache = self._leer_cache(clave)
        if hit:
            if fallo_cache:
                raise FotoNoDisponible("Foto temporalmente no disponible.")
            return foto_cache
        if not self._fetch_lock.acquire(timeout=10):
            raise FotoNoDisponible("Foto temporalmente no disponible.")
        try:
            hit, foto_cache, fallo_cache = self._leer_cache(clave)
            if hit:
                if fallo_cache:
                    raise FotoNoDisponible("Foto temporalmente no disponible.")
                return foto_cache
            try:
                foto = self._consultar(plataforma, asin)
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError):
                self._guardar(clave, None, 60, fallo=True)
                raise FotoNoDisponible("Foto temporalmente no disponible.") from None
            self._guardar(clave, foto, 86400 if foto else 900)
            return foto
        finally:
            self._fetch_lock.release()

    def _guardar(self, clave, foto, ttl, fallo=False):
        with self._cache_lock:
            self._cache[clave] = (time.monotonic() + ttl, foto, fallo)
            self._cache.move_to_end(clave)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)


fotos_publicacion = FotosPublicacion()
