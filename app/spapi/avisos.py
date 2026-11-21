"""Avisos Telegram de salud y vigilancia SP-API.

Extraido de `app/notifica.py` en Fase 3.1 para ubicar los builders y senders
SP-API junto a `salud.py` y `vigilante.py`, manteniendo `app/notifica.py`
enfocado en el transporte y alertas del ciclo.

Aislamiento Ads: cero import directo de `app.ads` (el candado de arquitectura
verifica `app/spapi/`).
"""

from __future__ import annotations

import datetime as dt

import httpx

from app import notifica
from app.redaction import scrub

# Total de pares (fuente, plataforma) del cron SP-API: 4 fuentes x 2
# plataformas del wrapper spapi-diario.sh (app/spapi/vigilante.py).
_TOTAL_PARES_SPAPI = 8

# Texto unico del aviso del vigilante: el par no dejo una corrida ok en
# `ingest_run`, porque fallo (salud solo avisa en la segunda falla
# seguida) o porque el cron no corrio.
MOTIVO_FALLO_O_FALTA = (
    "El cron de las 05:00 UTC no dejó estas corridas ok en ingest_run: "
    "falló o no corrió. Revisar crontab de gon, flock, spapi-diario.log "
    "y el aviso de salud de cada fuente."
)


def aviso_spapi_fallo(fuente: str, platform: str, motivo: str) -> str:
    """Builder PURO del aviso de fallo SP-API (A.5): fuente, plataforma y
    motivo ya redactado en el sello (doble scrub por si acaso). Sin fecha
    (patron aviso_cap_agotado: la fecha es la del run, visible en /salud).
    Con motivo LWA, matiz explicito de que el ciclo de Ads sigue."""
    lineas = [
        "[Orbit] ALERTA fallo SP-API",
        f"fuente: {fuente}",
        f"plataforma: {platform}",
        f"motivo: {scrub(motivo)}",
    ]
    if motivo.startswith("lwa_fallido"):
        lineas.append("El ciclo de Ads no se afecta (procesos y credenciales distintos).")
    return "\n".join(lineas)


def notifica_spapi_fallo(
    fuente: str, platform: str, motivo: str, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de fallo SP-API en flanco (A.5)."""
    return notifica._enviar_construido(
        "el aviso SP-API", aviso_spapi_fallo, fuente, platform, motivo, transport=transport
    )


def aviso_spapi_recuperada(fuente: str, platform: str) -> str:
    """Builder PURO del aviso de recuperacion SP-API: una linea llana."""
    return f"[Orbit] SP-API recuperada: {fuente} / {platform} ya corrio bien otra vez."


def notifica_spapi_recuperada(
    fuente: str, platform: str, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de recuperacion SP-API (flanco ok tras fallida)."""
    return notifica._enviar_construido(
        "el aviso de recuperacion SP-API",
        aviso_spapi_recuperada,
        fuente,
        platform,
        transport=transport,
    )


def aviso_spapi_silencio(faltantes, desde, hasta) -> str:
    """Builder PURO del aviso de silencio o falla SP-API (vigilante): una
    linea por par faltante en el orden del caller (FUENTES_SPAPI x
    PLATAFORMAS_SPAPI). `desde`/`hasta` aware se normalizan a UTC para que
    la etiqueta no mienta con otro offset. Sin secretos."""
    desde_utc = desde.astimezone(dt.UTC)
    hasta_utc = hasta.astimezone(dt.UTC)
    ventana = f"{desde_utc:%Y-%m-%d %H:%M} UTC → {hasta_utc:%Y-%m-%d %H:%M} UTC"
    lineas = [
        "[Orbit] ALERTA SP-API sin corrida ok",
        f"ventana: {ventana}",
        f"faltan {len(faltantes)} de {_TOTAL_PARES_SPAPI}:",
    ]
    lineas.extend(f"- {fuente} / {plataforma}" for fuente, plataforma in faltantes)
    lineas.append(MOTIVO_FALLO_O_FALTA)
    return "\n".join(lineas)


def aviso_spapi_vigilante_ciego(motivo: str) -> str:
    """Builder PURO del aviso ciego: el vigilante no pudo leer
    `ingest_run` (DB caida, DSN roto) y no sabe si el cron corrio. El
    motivo viaja con scrub (el DSN roto puede traer password)."""
    return "\n".join(
        [
            "[Orbit] ALERTA vigilante SP-API sin lectura",
            f"no pude leer ingest_run: {scrub(motivo)}",
            "No sé si el cron corrió. Revisar Postgres y el DSN de lectura.",
        ]
    )


def notifica_spapi_silencio(
    faltantes, desde, hasta, *, transport: httpx.BaseTransport | None = None
) -> bool:
    """Aviso de silencio SP-API (vigilante)."""
    return notifica._enviar_construido(
        "el aviso SP-API", aviso_spapi_silencio, faltantes, desde, hasta, transport=transport
    )
