"""Helpers compartidos de la capa API (ORBIT 16 - DASHBOARD 01).

Extraccion sellada por el plan (decision 6 del header de plans/dashboard-01.md):
los helpers de app/api.py se EXTRAEN y comparten con app/api_dashboard.py,
jamas dos copias. Bloque 1 (1.3): serializacion de dinero a STRING (regla 4).
Bloque 2 (1.5): `_parse_notes` (formato mixto), el SQL de ultimo ciclo por
plataforma y `_fila_ciclo`, reutilizados por /salud — con los tests de 3.2
intactos (api.py los re-importa y el test de superficie los sigue viendo).

NOTA (desviacion declarada): la conexion de lectura `_conexion_lectura` SIGUE
en app/api.py. El test de superficie 3.2 (test_conexion_de_lectura_corre_en_autocommit)
la introspecciona como `api._conexion_lectura` y parchea `api.connect`: moverla
romperia ese candado. app/api_dashboard.py reutiliza el tipo `ConexionLectura`
importandolo de app.api (una sola implementacion, cero copias).
"""

from __future__ import annotations

import json


def _dec_str(valor) -> str | None:
    """Decimal -> string TAL CUAL sale de la DB (regla 4: dinero como str,
    jamas float; la escala del string es artefacto deterministico del
    NUMERIC de origen, mismo criterio que _dec_str de app/cycle)."""
    return str(valor) if valor is not None else None


def _parse_notes(notes) -> dict | None:
    """Notes del envelope: FORMATO MIXTO (residual declarado del plan).

    JSON en ciclos normales -> se devuelve el dict estructurado (ahi viven
    los skips que el orquestador persiste); texto plano `rastro: ...` en
    ciclos muertos reclamados -> se devuelve bajo la clave `texto`. Ninguna
    forma puede reventar el endpoint (tambien tolera JSON no-dict, p.ej. una
    lista: cae bajo `texto`).
    """
    if notes is None:
        return None
    try:
        data = json.loads(notes)
    except (ValueError, TypeError):
        return {"texto": notes}
    return data if isinstance(data, dict) else {"texto": notes}


# Ultimo ciclo POR PLATAFORMA (regla 2: la MISMA fuente del snapshot de /status
# y del /salud del dashboard). Leida por INDICE (row_factory por defecto): la
# columna de la izquierda en el ORDER BY sin alias.
_SQL_ULTIMO_CICLO_POR_PLATAFORMA = """
SELECT DISTINCT ON (platform) id, mode, platform, started_at, finished_at,
       decisions_count, applied_count, status, notes
  FROM optimizer_cycle
 WHERE platform IS NOT NULL
   AND motor = 'ads_optimizer'
 ORDER BY platform, id DESC
"""


def _fila_ciclo(fila) -> dict:
    """Tupla de _SQL_ULTIMO_CICLO_POR_PLATAFORMA -> dict de la respuesta
    (notes parseado con _parse_notes: formato mixto tolerado)."""
    return {
        "id": fila[0],
        "mode": fila[1],
        "platform": fila[2],
        "started_at": fila[3],
        "finished_at": fila[4],
        "decisions_count": fila[5],
        "applied_count": fila[6],
        "status": fila[7],
        "notes": _parse_notes(fila[8]),
    }
