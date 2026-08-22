"""Credenciales de Amazon Ads (LWA), cargadas desde `ORBIT_SECRETS_DIR`.

Formato verificado contra docs/traspaso/TRASPASO-1-ACCESOS-E-INFRAESTRUCTURA.md
§1.1; la corrida real de 1.2 lo confirma. `amazon_ads_config.json` trae la app
LWA (`client_id`, `client_secret`, ...) y `amazon_ads_tokens.json` trae el
`refresh_token` vivo. Se toleran claves extra en ambos archivos.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.redaction import register_secret

DEFAULT_SECRETS_DIR = "/mnt/data/appdata/orbit/secrets"
CONFIG_FILENAME = "amazon_ads_config.json"
TOKENS_FILENAME = "amazon_ads_tokens.json"


class AdsConfigError(Exception):
    """Error cargando credenciales de Amazon Ads. El mensaje NUNCA lleva valores."""


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise AdsConfigError(f"no existe el archivo de credenciales: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise AdsConfigError(f"JSON invalido en {path.name}") from None


def _require(data: dict, key: str, filename: str) -> str:
    value = data.get(key)
    if not value:
        raise AdsConfigError(f"falta la clave '{key}' en {filename}")
    return value


@dataclass(repr=False)
class AdsCredentials:
    """Credenciales LWA de Amazon Ads. `repr`/`str` siempre redactados."""

    client_id: str
    client_secret: str
    refresh_token: str

    def __repr__(self) -> str:
        return "AdsCredentials(client_id=***, client_secret=***, refresh_token=***)"

    __str__ = __repr__

    @classmethod
    def from_secrets_dir(cls, secrets_dir: str | Path | None = None) -> AdsCredentials:
        base = Path(secrets_dir or os.environ.get("ORBIT_SECRETS_DIR", DEFAULT_SECRETS_DIR))
        config = _load_json(base / CONFIG_FILENAME)
        tokens = _load_json(base / TOKENS_FILENAME)

        client_id = _require(config, "client_id", CONFIG_FILENAME)
        client_secret = _require(config, "client_secret", CONFIG_FILENAME)
        refresh_token = _require(tokens, "refresh_token", TOKENS_FILENAME)

        register_secret(client_secret)
        register_secret(refresh_token)

        return cls(client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)
