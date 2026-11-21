"""Re-export del cliente MeLi para el dominio de reputacion.

La implementacion canonica vive en `app/meli/client.py` (Fase 3.1).
"""

from __future__ import annotations

from app.meli.client import (
    MELI_BASE,
    MELI_TOKENS_FILENAME,
    ClienteMeli,
    MeliClientError,
    MeliCredentials,
    ReputacionError,
    _id_item_valido,
    _total_valido,
)

__all__ = [
    "MELI_BASE",
    "MELI_TOKENS_FILENAME",
    "ClienteMeli",
    "MeliClientError",
    "MeliCredentials",
    "ReputacionError",
    "_id_item_valido",
    "_total_valido",
]
