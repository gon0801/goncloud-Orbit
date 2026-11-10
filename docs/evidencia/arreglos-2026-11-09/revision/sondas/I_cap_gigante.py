"""Sonda I-1: la pantalla de settings guarda un cap que el motor no puede usar."""
from app.api_write import CuerpoSettings
from app import config_write, apply

cuerpo = CuerpoSettings(base_config_version_id=1, caps={"bid": 3000000000})
print("pydantic acepta caps:", cuerpo.caps)
actual = {"ads_target_acos_amazon_mx": "20", "ads_apply_cap_amazon_mx_bid": "50"}
nuevo, cambios = config_write.proxima_config(actual, "amazon_mx", caps=cuerpo.caps)
print("proxima_config acepta:", nuevo["ads_apply_cap_amazon_mx_bid"], cambios)

class Fila:
    def __init__(self, v): self.v = v
    def fetchone(self): return self.v
class ConnFalsa:
    def execute(self, sql, params=None):
        return Fila((nuevo,))
print("_cap_de_config del motor devuelve:", apply._cap_de_config(ConnFalsa(), "amazon_mx", "bid"))
