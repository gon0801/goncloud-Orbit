"""Redaccion centralizada de secretos.

Un solo modulo para toda la app: DSNs de Postgres, URLs firmadas de
descarga (query string) y cualquier otro valor secreto que se registre en
tiempo de ejecucion (`register_secret`). `scrub()` es la ultima linea de
defensa (defensa en profundidad) sobre texto arbitrario -- logs y mensajes
de excepcion deberian evitar tocar secretos por construccion, pero si algo
se cuela, `scrub()` lo limpia.
"""

from __future__ import annotations

import logging
import re
import threading
from urllib.parse import urlsplit, urlunsplit

_lock = threading.Lock()
_secrets: list[str] = []

#  El grupo password es codicioso (`.*`) y el suffix exige el ULTIMO `@`
#  (`@[^@]*$`, sin `@` internos, anclado a fin de cadena): el backtracking
#  codicioso empuja el corte al ultimo `@` de la cadena, asi que una
#  password con `@` literal (ej. `fake@pw@123`) se captura COMPLETA en vez
#  de cortarse en el primer `@` (bug real: dejaba el resto sin redactar).
_DSN_URL_PASSWORD_RE = re.compile(
    r"^(?P<prefix>\w[\w+]*://[^:/@]*:)(?P<password>.*)(?P<suffix>@[^@]*)$"
)
_DSN_KV_PASSWORD_RE = re.compile(r"(?i)(password)\s*=\s*('(?:[^'\\]|\\.)*'|\S+)")

REDACTED = "***REDACTED***"


def register_secret(value: str | None) -> None:
    """Registra un valor secreto para que `scrub()` lo limpie de cualquier texto."""
    if not value:
        return
    with _lock:
        if value not in _secrets:
            _secrets.append(value)


def scrub(text: str) -> str:
    """Reemplaza cualquier valor secreto registrado dentro de `text`."""
    if not text:
        return text
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, REDACTED)
    return result


def redact_dsn(dsn: str | None) -> str | None:
    """Redacta la password de un DSN de Postgres.

    Cubre las dos formas: URL (`postgresql://user:pass@host/db`) y conninfo
    (`... password=... ...`). La password encontrada se registra tambien
    como secreto (defensa en profundidad para `scrub`).
    """
    if not dsn:
        return dsn
    result = dsn

    match = _DSN_URL_PASSWORD_RE.match(result)
    if match and match.group("password"):
        register_secret(match.group("password"))
        result = f"{match.group('prefix')}***{match.group('suffix')}"

    def _kv_replace(m: re.Match[str]) -> str:
        value = m.group(2)
        stripped = value[1:-1] if value.startswith("'") and value.endswith("'") else value
        register_secret(stripped)
        return f"{m.group(1)}=***"

    return _DSN_KV_PASSWORD_RE.sub(_kv_replace, result)


def redact_url(url: str | None) -> str | None:
    """Conserva scheme+host+path de una URL; ELIMINA query y fragment.

    Las URLs firmadas (descargas de reportes) llevan el secreto en la
    query string (`X-Amz-Signature=...`); nunca debe aparecer en logs ni
    mensajes de error.
    """
    if not url:
        return url
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class SecretScrubFilter(logging.Filter):
    """Filtro de logging que aplica `scrub()` al mensaje ya formateado."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - nunca debe tumbar el logging
            return True
        record.msg = scrub(message)
        record.args = ()
        return True


def install_scrub_filter(logger: logging.Logger) -> None:
    """Instala `SecretScrubFilter` en `logger` si todavia no lo tiene."""
    if not any(isinstance(f, SecretScrubFilter) for f in logger.filters):
        logger.addFilter(SecretScrubFilter())
