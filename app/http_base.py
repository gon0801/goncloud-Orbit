"""Primitivas HTTP compartidas por los clientes externos (Ads, SP-API, MeLi).

Unifica la validacion anti-SSRF / anti-traversal de rutas relativas y el
parseo seguro (finito y acotado) de `Retry-After`.
"""

from __future__ import annotations

import math
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


def validar_ruta_relativa(
    path: str,
    *,
    error_cls: type[Exception] = ValueError,
    etiqueta: str = "ruta HTTP",
    prohibir_query: bool = False,
    prohibir_percent_barra: bool = False,
) -> str:
    """Rechaza URLs absolutas, esquemas, backslashes, control chars y `..`."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise error_cls(f"{etiqueta} invalido: debe empezar con '/'")
    if path.startswith("//"):
        raise error_cls(f"{etiqueta} invalido: doble slash inicial prohibido")
    if "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise error_cls(f"{etiqueta} invalido: caracteres de control o backslash prohibidos")
    if "://" in path:
        raise error_cls(f"{etiqueta} invalido: no se permiten URLs absolutas")
    if prohibir_query and ("?" in path or "#" in path):
        raise error_cls(f"{etiqueta} invalido: query o fragment no permitidos")
    if prohibir_percent_barra and "%2f" in path.lower():
        raise error_cls(f"{etiqueta} invalido: encoding de barra no permitido")
    partes = urlsplit(path)
    if partes.scheme or partes.netloc:
        raise error_cls(f"{etiqueta} invalido: no se permiten URLs absolutas")
    segmentos = partes.path.split("/")
    if ".." in segmentos:
        raise error_cls(f"{etiqueta} invalido: traversal ('..') prohibido")
    return path


def parse_retry_after_finito(
    valor: str | None,
    *,
    techo: float | None = 60.0,
    ahora_ts: float | None = None,
) -> float | None:
    """Parsea `Retry-After` (segundos o HTTP-date) rechazando NaN/Inf/negativos."""
    if not valor:
        return None
    texto = valor.strip()
    if not texto:
        return None
    segundos: float | None = None
    try:
        candidato = float(texto)
        if math.isfinite(candidato) and candidato >= 0:
            segundos = candidato
    except ValueError:
        try:
            fecha = parsedate_to_datetime(texto)
            if fecha is not None and ahora_ts is not None:
                delta = fecha.timestamp() - ahora_ts
                if math.isfinite(delta):
                    segundos = max(0.0, delta)
        except (TypeError, ValueError, OverflowError):
            segundos = None
    if segundos is None:
        return None
    if techo is not None:
        return min(segundos, techo)
    return segundos
