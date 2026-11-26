"""Ciclo de vida canonico de filas en `ingest_run`.

Consolida la apertura, sellado, formateo de `skip_reason` y sellado de fallo
ante `BaseException` (A9) que antes se duplicaban en 14 ingestores distintos.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from psycopg import pq

from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

_SQL_ABRIR_SIMPLE = "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id"
_SQL_ABRIR_PLATAFORMA = "INSERT INTO ingest_run (source, platform) VALUES (%s, %s) RETURNING id"
_SQL_ABRIR_CON_INICIO = "INSERT INTO ingest_run (source, started_at) VALUES (%s, %s) RETURNING id"
_SQL_SELLAR = """
UPDATE ingest_run
   SET finished_at = now(),
       rows_written = %s,
       rows_skipped = %s,
       skip_reason = %s,
       ok = %s
 WHERE id = %s
"""
_SQL_SELLAR_CON_LLAMADAS = """
UPDATE ingest_run
   SET finished_at = now(),
       rows_written = %s,
       rows_skipped = %s,
       skip_reason = %s,
       ok = %s,
       llamadas = %s
 WHERE id = %s
"""


def sanitizar_motivo(texto: str | None) -> str | None:
    """Elimina bytes NUL (prohibidos por Postgres TEXT) y redacta secretos."""
    if texto is None:
        return None
    limpio = scrub(str(texto)).replace("\x00", "")
    return limpio


def formato_skip_reason(skips: Counter) -> str | None:
    """Formatea conteos positivos de skips ordenados alfabeticamente."""
    activos = {k: v for k, v in skips.items() if v > 0}
    if not activos:
        return None
    bruto = ", ".join(f"{n}x {motivo}" for motivo, n in sorted(activos.items()))
    return sanitizar_motivo(bruto)


# Saltos de toda corrida normal de spapi_orders (ENVIOS 01 relee 30 dias):
# quedan en el sello como cualquier otro, pero no son un aviso en /salud.
SALTO_RESUMEN_CUBIERTO = "resumen_ya_cubierto"
SALTO_ENVIO_SIN_CAMBIO = "envio_sin_cambio"
_SALTOS_ESPERADOS = frozenset({SALTO_RESUMEN_CUBIERTO, SALTO_ENVIO_SIN_CAMBIO})
_CONTEO_SKIP = re.compile(r"(\d+)x (.+)")


def saltos_inesperados(rows_skipped: int, skip_reason: str | None) -> int:
    """Saltos de `rows_skipped` que `skip_reason` no explica como esperados.

    Lee el formato de `formato_skip_reason`, tambien tras un prefijo `...; `.
    Lo ausente, cortado o que no cuadra con el total cuenta como inesperado.
    """
    esperados = 0
    for parte in re.split(r"[;,] ", skip_reason or ""):
        conteo = _CONTEO_SKIP.fullmatch(parte)
        if conteo and conteo[2] in _SALTOS_ESPERADOS:
            esperados += int(conteo[1])
    return rows_skipped - esperados if esperados <= rows_skipped else rows_skipped


def abrir_run(
    conn: Any,
    source: str,
    *,
    platform: str | None = None,
    started_at: Any = None,
) -> int:
    """Inserta una fila abierta en `ingest_run` y devuelve su `id`."""
    if platform is not None:
        row = conn.execute(_SQL_ABRIR_PLATAFORMA, (source, platform)).fetchone()
    elif started_at is not None:
        row = conn.execute(_SQL_ABRIR_CON_INICIO, (source, started_at)).fetchone()
    else:
        row = conn.execute(_SQL_ABRIR_SIMPLE, (source,)).fetchone()
    return int(row[0])


def sellar_run(
    conn: Any,
    run_id: int,
    *,
    ok: bool,
    rows_written: int | None = 0,
    rows_skipped: int = 0,
    skip_reason: str | None = None,
    llamadas: int | None = None,
    redactar_motivo: bool = True,
) -> None:
    """Sella `ingest_run` con `finished_at = now()` y `skip_reason` saneado.

    Con `redactar_motivo=False`, el llamador ya redacto la parte libre y pasa
    marcas estructuradas validadas (p. ej. `as_of=<epoch>; snap=<sha256>`)
    que no deben alterarse si un secreto registrado coincide con digitos.
    """
    if skip_reason is None:
        motivo = None
    elif redactar_motivo:
        motivo = sanitizar_motivo(skip_reason)
    else:
        motivo = str(skip_reason).replace("\x00", "")
    if llamadas is not None:
        conn.execute(
            _SQL_SELLAR_CON_LLAMADAS,
            (rows_written, rows_skipped, motivo, ok, llamadas, run_id),
        )
    else:
        conn.execute(_SQL_SELLAR, (rows_written, rows_skipped, motivo, ok, run_id))


def sellar_fallo_seguro(
    conn: Any,
    run_id: int,
    exc: BaseException,
    *,
    rows_written: int | None = 0,
    rows_skipped: int = 0,
    motivo: str | None = None,
    llamadas: int | None = None,
    tras_sellar: Callable[[], None] | None = None,
) -> None:
    """Sella `ok=False` tras una excepcion (incluido BaseException) sin ocultar `exc`.

    Revierte primero si la conexion quedo en estado INERROR y ejecuta el sello
    en una transaccion propia; si tambien falla el sellado, deja WARNING con
    scrub para que la excepcion original siga propagando.
    """
    texto_motivo = motivo if motivo is not None else (scrub(str(exc)) or type(exc).__name__)
    try:
        info = getattr(conn, "info", None)
        if info is not None and info.transaction_status == pq.TransactionStatus.INERROR:
            conn.rollback()
        with conn.transaction():
            sellar_run(
                conn,
                run_id,
                ok=False,
                rows_written=rows_written,
                rows_skipped=rows_skipped,
                skip_reason=texto_motivo,
                llamadas=llamadas,
            )
        if tras_sellar is not None:
            tras_sellar()
    except Exception:
        logger.warning(
            "ingest_run %s quedo ABIERTA: fallo tambien su sello; error: %s",
            run_id,
            scrub(str(exc)),
        )
