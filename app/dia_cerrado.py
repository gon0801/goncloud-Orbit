"""Una sola regla de "dia cerrado" por plataforma (ARREGLOS 03 D-E, diseno DC).

Cada marketplace cierra su dia en su propia zona: Amazon MX y Mercado Libre
(site MLM) en Ciudad de Mexico, Amazon US en Los Angeles. El "ayer" que se
guarda y que exige la guarda del ciclo es el ultimo dia que YA termino alla,
no "hoy UTC menos 1": en invierno, a las 07:10 UTC en Los Angeles todavia son
las 23:10 del dia anterior.

Solo biblioteca estandar, sin base ni red: lo importan la ingesta, las
guardas y el Resumen sin crear ciclos. La tabla ZONAS es la unica fuente de
las zonas (un numero, una fuente).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from zoneinfo import ZoneInfo

# Amazon: el perfil v2 trae `timezone` (app/ads/structure.py:7), pero Orbit no
# la guarda y la zona de un marketplace no cambia. MeLi (site MLM): el sync pide
# fechas con el reloj UTC, pero el dia que publica la API es el dia local; el
# 2026-08-21 visto a las 19:19 UTC traia 16% del gasto final (evidencia ADS-01).
# El Resumen (app/dashboard_resumen.py) lee estas mismas zonas (ARREGLOS 03 D-F).
ZONAS: Mapping[str, str] = {
    "amazon_mx": "America/Mexico_City",  # el perfil MX de Amazon cierra su dia aqui
    "amazon_us": "America/Los_Angeles",  # el perfil US cierra su dia aqui
    "meli": "America/Mexico_City",  # site MLM
}


def _zona(plataforma: str) -> ZoneInfo:
    if plataforma not in ZONAS:
        raise ValueError(f"plataforma sin zona: {plataforma}")
    return ZoneInfo(ZONAS[plataforma])


def dia_local(plataforma: str, instante: dt.datetime) -> dt.date:
    """El dia que corre en la zona de `plataforma` en `instante`."""
    if instante.tzinfo is None:
        # astimezone lo tomaria como hora de la maquina
        raise ValueError("instante sin zona")
    return instante.astimezone(_zona(plataforma)).date()


def ultimo_dia_cerrado(plataforma: str, instante: dt.datetime) -> dt.date:
    """El ultimo dia que ya termino en la zona de `plataforma`."""
    return dia_local(plataforma, instante) - dt.timedelta(days=1)


def fin_del_dia(plataforma: str, fecha: dt.date) -> dt.datetime:
    """Instante UTC en que termina `fecha` en la zona de `plataforma` (las
    00:00 locales del dia siguiente)."""
    siguiente = fecha + dt.timedelta(days=1)
    return dt.datetime.combine(siguiente, dt.time.min, tzinfo=_zona(plataforma)).astimezone(dt.UTC)
