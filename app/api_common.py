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


def bloque_target_margen(ultimo_ciclo: dict | None, hoy=None) -> dict:
    """Bloque target del ULTIMO ciclo para /salud (ORBIT 06 2.3, spec §9):
    target vigente + procedencia + margen medido + fraccion + ventana + edad
    del ledger + motivo con etiqueta. Fuente UNICA: notes.target del ciclo
    (lo que el ciclo resolvio, no una re-resolucion viva). Sin ciclo, sin
    notes o sin la clave -> todo null (regla 3: lo ausente no se menciona
    ni revienta el endpoint). `hoy` por parametro para tests puros
    (default: hoy UTC)."""
    import datetime as dt

    vacio = {
        "target_vigente": None,
        "procedencia": None,
        "margen_neto_pct": None,
        "fraccion": None,
        "ventana_desde": None,
        "ventana_hasta": None,
        "ledger_edad_dias": None,
        "motivo_abstencion": None,
        "motivo_etiqueta": None,
    }
    if not isinstance(ultimo_ciclo, dict):
        return vacio
    notes = ultimo_ciclo.get("notes")
    if not isinstance(notes, dict):
        return vacio
    bloque = notes.get("target")
    if not isinstance(bloque, dict):
        return vacio
    from app.optimizer import goals as g

    motivo = bloque.get("motivo_abstencion")
    fresco = bloque.get("ledger_fresco_at")
    edad = None
    if isinstance(fresco, str) and fresco:
        try:
            dia = dt.datetime.fromisoformat(fresco).date()
            ref = hoy if hoy is not None else dt.datetime.now(dt.UTC).date()
            edad = (ref - dia).days
        except ValueError:
            edad = None
    # Vigente = el resultado DEL PELDANO a nivel plataforma: el aplicado
    # si gano, null si se abstuvo (la cascada por entidad puede haber usado
    # el setting o un goal: eso vive en inputs, no aqui; regla 3).
    return {
        "target_vigente": bloque.get("target_aplicado"),
        "procedencia": bloque.get("procedencia"),
        "margen_neto_pct": bloque.get("margen_neto_pct"),
        "fraccion": bloque.get("fraccion"),
        "ventana_desde": bloque.get("ventana_desde"),
        "ventana_hasta": bloque.get("ventana_hasta"),
        "ledger_edad_dias": edad,
        "motivo_abstencion": motivo,
        "motivo_etiqueta": g.ETIQUETA_ABSTENCION.get(motivo, motivo) if motivo else None,
    }
