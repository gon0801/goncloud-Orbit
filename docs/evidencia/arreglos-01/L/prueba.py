"""L (J-H3): el codigo desplegado espera el insumo del dia hasta las 21:00 UTC.
Funcion pura: sin base ni red.
Se corre con `bash docs/evidencia/arreglos-01/en_prod.sh .../L/prueba.py`.
"""

import datetime as dt

from app.precio.compuerta import ESPERA_INSUMOS_HASTA, insumo_atrasado


def _ahora(hora, minuto):
    return dt.datetime(2026, 11, 9, hora, minuto, tzinfo=dt.UTC)


# Lote de 100 publicaciones: 90 con la oferta/valoracion del dia sin llegar.
motivos = ["oferta_desactualizada"] * 50 + ["valoracion_desactualizada"] * 40 + [None] * 10

espera = insumo_atrasado(motivos, ahora_utc=_ahora(13, 10))
assert espera is not None, "a las 13:10 con mayoria atrasada tiene que esperar"
assert "insumo del dia sin llegar" in espera, espera
assert "90 de 100" in espera, espera
print(f"13:10 UTC: espera={espera!r}")

assert ESPERA_INSUMOS_HASTA == dt.time(21, 0), ESPERA_INSUMOS_HASTA
assert insumo_atrasado(motivos, ahora_utc=_ahora(21, 10)) is None, (
    "desde las 21:00 UTC decide con lo que haya"
)
print("21:10 UTC: decide (None)")

# Un motivo atrasado aislado no detiene el lote.
aislado = insumo_atrasado(["oferta_desactualizada"] + [None] * 99, ahora_utc=_ahora(13, 10))
assert aislado is None, f"un motivo aislado no espera, dio {aislado!r}"
print("motivo aislado: decide (None)")
print("L OK")
