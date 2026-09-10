"""Salud y alertas de las ingestas SP-API (SP-API 01 A.5, ultima de Fase A).

`ingest_run` es la UNICA fuente de la salud y de las alertas (regla 2):
las 4 ingestas sellan cada corrida con su source + platform (migracion
0036) y el motivo con prefijo taxonomico (lwa_fallido, http_429,
http_5xx, red, contrato; ver `prefijo_motivo` en client.py). Sin
plataforma (corridas pre-A.5) = invisible para este modulo, nunca error.

`bloque_salud` lo consume /salud (API + pagina); `evaluar_alertas` la
llaman las 4 ingestas justo despues de sellar y es fail-silent: ningun
fallo suyo (ni del canal Telegram) rompe la ingesta que la llamo.

Aislamiento Ads: este modulo no importa app.ads DIRECTO en ningun
nivel (AST); en runtime solo entra app.ads.config (constantes inertes,
cero IO) via app.notifica — nunca app.ads.write/client. El ciclo de Ads
corre en otro proceso con otras credenciales, asi que un fallo SP-API
—incluido LWA caido— jamas lo tumba.
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
 WHERE source = %s AND platform = %s AND ok IS NOT NULL AND id <= %s
 ORDER BY id DESC LIMIT 3
"""

_SQL_ULTIMA_MOTIVO = f"""
SELECT {_COLUMNAS_RUN} FROM ingest_run
 WHERE source = %s AND platform = %s AND starts_with(skip_reason, %s)
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

    ASIMETRIA DOCUMENTADA con `evaluar_alertas`: aqui la "ultima" PUEDE
    ser una corrida ABIERTA (ok NULL: informativa, la plantilla muestra
    "—"); en cambio el historial de alertas solo ve corridas SELLADAS
    (`ok IS NOT NULL`) y ancladas al run recien sellado.
    """
    bloque: dict[str, dict] = {}
    for fuente in FUENTES_SPAPI:
        bloque[fuente] = {
            "ultima": _una(conn, _SQL_ULTIMA, (fuente, platform)),
            "ultima_429": _una(conn, _SQL_ULTIMA_MOTIVO, (fuente, platform, "http_429")),
            "ultimo_lwa": _una(conn, _SQL_ULTIMA_MOTIVO, (fuente, platform, "lwa_fallido")),
        }
    return bloque


def _racha_fallida(ultimas: list) -> bool:
    """Flanco de fallo sostenido: las dos ultimas ok=false y la tercera
    (si existe) NO estaba en racha fallida. Una alerta por racha.

    Entrada: SOLO corridas selladas (`_historial` filtra ok NULL), con
    la recien sellada en [0]."""
    if len(ultimas) < 2:
        return False
    if ultimas[0][3] is not False or ultimas[1][3] is not False:
        return False
    return len(ultimas) < 3 or ultimas[2][3] is not False


def _clase_inmediata(motivo: str) -> str | None:
    """Clase inmediata del motivo (LWA/429), o None si es del resto."""
    if motivo.startswith(MOTIVO_LWA_FALLIDO):
        return MOTIVO_LWA_FALLIDO
    if motivo.startswith(MOTIVO_HTTP_429):
        return MOTIVO_HTTP_429
    return None


def _historial(conn: Any, fuente: str, platform: str, run_id: int) -> list:
    """Ultimas 3 corridas SELLADAS (ok NOT NULL) hasta `run_id` inclusive.

    Solo selladas: una huerfana abierta (incidentes 145/147) entre dos
    fallidas NO debe enmascarar la racha. Ancladas a `run_id`: una
    corrida abierta MAS NUEVA (re-corrida manual) no mueve la evaluacion
    de la que se acaba de sellar.
    """
    return conn.execute(_SQL_ULTIMAS, (fuente, platform, run_id)).fetchall()


def evaluar_alertas(conn: Any, fuente: str, platform: str, run_id: int) -> None:
    """Dispara notifica_spapi_fallo SOLO en flanco; fail-silent siempre.

    UNA alerta por racha, sin esperar dos fallos en las clases
    inmediatas: alerta si y solo si la corrida `run_id` recien sellada
    ABRE una situacion nueva —
    - clase inmediata (lwa_fallido/http_429): la corrida sellada anterior
      NO era fallida de esa misma clase (sin historia, ok, u otra clase);
    - resto: se cumple `_racha_fallida` (segunda fallida consecutiva).

    Las dos rutas son EXCLUYENTES: nunca dos mensajes por la misma
    corrida. Un CAMBIO DE CLASE dentro de una racha (contrato ->
    lwa_fallido) SI re-alerta: es informacion nueva para el operador
    (otra causa, otra accion). La tercera fallida seguida NO re-alerta.

    La lectura va en su propio `with conn.transaction()` y la
    notificacion Telegram FUERA de el: la conexion queda IDLE y el sello
    ya commiteado nunca depende del canal. La ingesta la llama justo
    despues de sellar; nada de aqui puede romperla.
    """
    try:
        with conn.transaction():
            ultimas = _historial(conn, fuente, platform, run_id)
        if not ultimas:
            return
        motivo = ultimas[0][6] or ""
        clase = _clase_inmediata(motivo)
        if clase is not None:
            anterior = ultimas[1] if len(ultimas) > 1 else None
            if (
                anterior is None
                or anterior[3] is not False
                or not (anterior[6] or "").startswith(clase)
            ):
                notifica_spapi_fallo(fuente, platform, motivo)
        elif _racha_fallida(ultimas):
            notifica_spapi_fallo(fuente, platform, motivo)
    except Exception as exc:  # noqa: BLE001 - fail-silent (contrato A.5)
        logger.warning(
            "spapi salud: evaluar_alertas(%s/%s) no pudo correr: %s",
            fuente,
            platform,
            scrub(str(exc)),
        )
