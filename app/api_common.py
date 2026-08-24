"""Helpers compartidos de la capa API (ORBIT 16 - DASHBOARD 01, task 1.3).

Extraccion sellada por el plan (decision 6 del header de plans/dashboard-01.md):
los helpers de app/api.py se EXTRAEN y comparten con app/api_dashboard.py,
jamas dos copias. Este modulo nace con la serializacion de dinero a STRING
(regla 4); `_parse_notes` y el SQL de ultimo ciclo por plataforma se suman
aqui cuando 1.5 (salud) los necesite, con los tests de 3.2 intactos.

NOTA (desviacion declarada): la conexion de lectura `_conexion_lectura` SIGUE
en app/api.py. El test de superficie 3.2 (test_conexion_de_lectura_corre_en_autocommit)
la introspecciona como `api._conexion_lectura` y parchea `api.connect`: moverla
romperia ese candado. app/api_dashboard.py reutiliza el tipo `ConexionLectura`
importandolo de app.api (una sola implementacion, cero copias).
"""

from __future__ import annotations


def _dec_str(valor) -> str | None:
    """Decimal -> string TAL CUAL sale de la DB (regla 4: dinero como str,
    jamas float; la escala del string es artefacto deterministico del
    NUMERIC de origen, mismo criterio que _dec_str de app/cycle)."""
    return str(valor) if valor is not None else None
