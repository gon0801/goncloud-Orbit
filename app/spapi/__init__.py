"""Lecturas SP-API solo lectura (SP-API 01 Fase A).

Un solo paquete y un solo refrescador LWA (D5): este modulo es el unico
que sabe pedir `access_token` a `api.amazon.com/auth/o2/token` para SP-API.
`app/estimacion_fees.py` y `app/publicacion_fotos.py` lo reutilizan; la
sonda `tools/sonda_spapi.py` importa de aqui (una sola copia).
"""

from app.spapi.client import SpapiClient, cliente_compartido

__all__ = ["SpapiClient", "cliente_compartido"]
