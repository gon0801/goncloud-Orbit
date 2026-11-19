"""Prueba real del bloque W-H (web.md F2), para en_prod.sh.

Funciones puras: sin base, sin red, sin escritura.
"""

import datetime as dt

from app import cli

ahora = dt.datetime.now(dt.UTC)
futuro = ahora + dt.timedelta(days=40)
try:
    obtenido = cli._parse_decided_at(futuro.isoformat())
except ValueError as exc:
    assert "futuro" in str(exc), exc
else:
    raise AssertionError(f"_parse_decided_at acepto fecha futura: {obtenido.isoformat()}")

hace_una_hora = ahora - dt.timedelta(hours=1)
assert cli._parse_decided_at(hace_una_hora.isoformat()) == hace_una_hora

fijo = dt.datetime(2026, 11, 12, 18, 0, tzinfo=dt.UTC)
en_tolerancia = fijo + dt.timedelta(minutes=5)
assert cli._parse_decided_at(en_tolerancia.isoformat(), ahora=fijo) == en_tolerancia
try:
    cli._parse_decided_at((fijo + dt.timedelta(minutes=5, seconds=1)).isoformat(), ahora=fijo)
except ValueError:
    pass
else:
    raise AssertionError("superar TOLERANCIA_RELOJ deberia lanzar ValueError")

print("W-H OK")
