"""Salud y alertas de las ingestas SP-API (SP-API 01 A.5, ultima de Fase A).

`ingest_run` es la UNICA fuente de la salud y de las alertas (regla 2):
las 4 ingestas sellan cada corrida con su source + platform (migracion
0036) y el motivo con prefijo taxonomico (lwa_fallido, http_429,
http_5xx, red, contrato; ver `prefijo_motivo` en client.py). Sin
plataforma (corridas pre-A.5) = invisible para este modulo, nunca error.

`bloque_salud` lo consume /salud (API + pagina); `evaluar_alertas` la
llaman las 4 ingestas justo despues de sellar y es fail-silent: ningun
fallo suyo (ni del canal Telegram) rompe la ingesta que la llamo.

Aislamiento Ads: este modulo no importa app.ads en ningun nivel (test);
el ciclo de Ads corre en otro proceso con otras credenciales, asi que un
fallo SP-API —incluido LWA caido— jamas lo tumba.
"""

from __future__ import annotations

import logging
from typing import Any

from app.notifica import notifica_spapi_fallo
from app.redaction import install_scrub_filter, scrub
from app.spapi.client import MOTIVO_HTTP_429, MOTIVO_LWA_FALLIDO

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

FUENTES_SPAPI = ("spapi_orders", "spapi_pricing", "spapi_listings", "spapi_inventario")

_COLUMNAS_RUN = "id, started_at, finished_at, ok, rows_written, rows_skipped, skip_reason, llamadas"

_SQL_ULTIMA = f"""
SELECT {_COLUMNAS_RUN} FROM ingest_run
 WHERE source = %s AND platform = %s
 ORDER BY id DESC LIMIT 1
"""

_SQL_ULTIMAS = f"""
SELECT {_COLUMNAS_RUN} FROM ingest_run
 WHERE source = %s AND platform = %s
 ORDER BY id DESC LIMIT 3
"""

_SQL_ULTIMA_MOTIVO = f"""
SELECT {_COLUMNAS_RUN} FROM ingest_run
 WHERE source = %s AND platform = %s AND skip_reason LIKE %s
 ORDER BY id DESC LIMIT 1
"""


def _fila(fila: tuple) -> dict:
    id_, inicio, fin, ok, escritas, skips, motivo, llamadas = fila
    return {
        "id": id_,
        "started_at": inicio.isoformat() if inicio is not None else None,
        "finished_at": fin.isoformat() if fin is not None else None,
        "ok": ok,
        "rows_written": escritas,
        "rows_skipped": skips,
        "skip_reason": motivo,
        "llamadas": llamadas,
    }


def _una(conn: Any, sql: str, params: tuple) -> dict | None:
    fila = conn.execute(sql, params).fetchone()
    return _fila(fila) if fila is not None else None


def bloque_salud(conn: Any, platform: str) -> dict:
    """Bloque "spapi" de /salud para UNA plataforma.

    Por fuente: ultima corrida + ultima con 429 + ultima con fallo LWA
    (fechas y motivo). Fuente sin corridas (o solo pre-A.5 sin platform)
    = entradas en None, nunca error.
    """
    bloque: dict[str, dict] = {}
    for fuente in FUENTES_SPAPI:
        bloque[fuente] = {
            "ultima": _una(conn, _SQL_ULTIMA, (fuente, platform)),
            "ultima_429": _una(conn, _SQL_ULTIMA_MOTIVO, (fuente, platform, "http_429%")),
            "ultimo_lwa": _una(conn, _SQL_ULTIMA_MOTIVO, (fuente, platform, "lwa_fallido%")),
        }
    return bloque


def _racha_fallida(ultimas: list) -> bool:
    """Flanco de fallo sostenido: las dos ultimas ok=false y la tercera
    (si existe) NO estaba en racha fallida. Una alerta por racha."""
    if len(ultimas) < 2:
        return False
    if ultimas[0][3] is not False or ultimas[1][3] is not False:
        return False
    return len(ultimas) < 3 or ultimas[2][3] is not False


def _historial(conn: Any, fuente: str, platform: str) -> list:
    return conn.execute(_SQL_ULTIMAS, (fuente, platform)).fetchall()


def evaluar_alertas(conn: Any, fuente: str, platform: str) -> None:
    """Dispara notifica_spapi_fallo SOLO en flanco; fail-silent siempre.

    Flancos: (a) motivo LWA en la ultima (alerta inmediata + matiz Ads);
    (b) motivo 429 persistente en la ultima (inmediata); (c) fallo
    sostenido: dos ultimas ok=false abriendo racha. La ingesta la llama
    justo despues de sellar; nada de aqui puede romperla.
    """
    try:
        ultimas = _historial(conn, fuente, platform)
        if not ultimas:
            return
        motivo = ultimas[0][6] or ""
        # Flanco: LWA y 429 alertan en la primera (inmediatos); el resto
        # solo al abrir racha de dos fallidas (la tercera NO re-alerta).
        inmediato = motivo.startswith((MOTIVO_LWA_FALLIDO, MOTIVO_HTTP_429))
        if inmediato or _racha_fallida(ultimas):
            notifica_spapi_fallo(fuente, platform, motivo)
    except Exception as exc:  # noqa: BLE001 - fail-silent (contrato A.5)
        logger.warning(
            "spapi salud: evaluar_alertas(%s/%s) no pudo correr: %s",
            fuente,
            platform,
            scrub(str(exc)),
        )
