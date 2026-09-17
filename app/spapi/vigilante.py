"""Vigilante del cron SP-API: el silencio avisa.

Si una ingesta FALLA, ella misma sella su `ingest_run` y `salud.py`
avisa. Pero si el cron NO dispara (crontab pisado, `flock` atorado,
reinicio en la ventana), no hay fila que fallar y nadie se entera.
Este modulo convierte el silencio en aviso: lee la ventana de
`ingest_run` (UNICA fuente, regla 2 — igual que salud.py) y reporta
los pares (fuente, plataforma) sin corrida terminada. Limite: si el
contenedor esta caido, `docker exec` falla en el host y NO hay aviso
(solo el error de docker en el log; ver docs/DEPLOY.md).

Solo lee (`ORBIT_DSN_READ`); sin migracion; sin canal nuevo (reusa
`_envia_texto` via los senders de app.notifica). En verde es
silencioso: cuando no falta nada no se envia nada.

Aislamiento Ads: igual que salud.py — cero import directo de app.ads
(el candado de arquitectura lo verifica).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime

from app.db import connect
from app.notifica import (
    _envia_texto,
    aviso_spapi_vigilante_ciego,
    notifica_spapi_silencio,
)
from app.redaction import install_scrub_filter, scrub
from app.spapi.salud import FUENTES_SPAPI

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

# Las dos plataformas del wrapper /mnt/data/appdata/orbit/spapi-diario.sh
# (fuente del numero ocho: 4 fuentes x 2 plataformas). Si el wrapper
# cubre otro mercado, esto cambia con el.
PLATAFORMAS_SPAPI = ("amazon_mx", "amazon_us")


def faltantes(filas, *, desde: datetime, hasta: datetime) -> list[tuple[str, str]]:
    """Pares (fuente, plataforma) SIN corrida terminada en la ventana.

    `filas`: tuplas (source, platform, started_at, finished_at, ok) tal
    cual las trae `lee_ventana`. Presente = `started_at` en [desde,
    hasta) Y `finished_at IS NOT NULL`. `ok = false` CUENTA como
    presente (ya aviso por su camino); empezada y no terminada cuenta
    como ausente (colgada es silencio). Source ajena o platform NULL
    (pre-A.5): invisibles, nunca error. Orden estable fuente→plataforma.
    """
    presentes: set[tuple[str, str]] = set()
    for source, platform, started_at, finished_at, _ok in filas:
        if source not in FUENTES_SPAPI:
            continue
        if platform is None or platform not in PLATAFORMAS_SPAPI:
            continue
        if started_at is None or not (desde <= started_at < hasta):
            continue
        if finished_at is None:
            continue
        presentes.add((source, platform))
    return [
        (fuente, plataforma)
        for fuente in FUENTES_SPAPI
        for plataforma in PLATAFORMAS_SPAPI
        if (fuente, plataforma) not in presentes
    ]


_SQL_VENTANA = """
SELECT source, platform, started_at, finished_at, ok FROM ingest_run
 WHERE source = ANY(%s) AND started_at >= %s AND started_at < %s
"""


def lee_ventana(conn, *, desde: datetime, hasta: datetime) -> list:
    """Un solo SELECT sobre `ingest_run`: (source, platform, started_at,
    finished_at, ok) de las fuentes SP-API en [desde, hasta). Solo
    lectura; el rol de lectura basta (SET ROLE app_read en tests)."""
    return conn.execute(_SQL_VENTANA, (list(FUENTES_SPAPI), desde, hasta)).fetchall()


def avisa_ciego(motivo: str) -> bool:
    """Aviso ciego (el vigilante no pudo leer): APAGON 2026-09-16, el texto
    queda en el log local via _envia_texto (-> True); excepcion -> warning
    con scrub + False; jamas levanta. Vive aqui (no en notifica.py) porque
    el brief fija un solo sender nuevo alla; el builder si es compartido."""
    try:
        return _envia_texto(aviso_spapi_vigilante_ciego(motivo))
    except Exception as exc:  # noqa: BLE001 - fail-silent (contrato SP-API)
        logger.warning("vigilante: fallo el aviso ciego: %s", scrub(str(exc)))
        return False


def _fecha_utc(texto: str) -> datetime:
    """ISO 8601 UTC (--desde/--hasta). Naive se asume UTC (el cron y el
    runbook usan Z); formato invalido -> ValueError y argparse sale 2."""
    momento = datetime.fromisoformat(texto)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento


def main(argv: list[str] | None = None) -> int:
    """`spapi_vigilante`: evalua la ventana, imprime y avisa.

    Salida: una linea por par (`- fuente / plat: presente|ausente`) +
    resumen `faltan N de 8`. Exits: 0 sin faltantes (silencioso, no
    envia nada), 1 con faltantes (tras intentar el aviso), 2 sin
    lectura (tras intentar el aviso ciego). `--dry-run`: mismos
    codigos, cero envios. Ventana invalida (desde >= hasta): exit 2
    ANTES de abrir la base y sin enviar nada.
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.cli spapi_vigilante",
        description=(
            "Vigilante del cron SP-API 05:00 UTC: avisa por Telegram si la"
            " ventana no trae las 8 corridas en ingest_run (runbook:"
            " docs/DEPLOY.md). Solo lectura (ORBIT_DSN_READ)."
        ),
    )
    parser.add_argument(
        "--desde",
        type=_fecha_utc,
        default=None,
        help="ISO UTC de inicio (default: hoy 04:30 UTC)",
    )
    parser.add_argument(
        "--hasta",
        type=_fecha_utc,
        default=None,
        help="ISO UTC de fin, exclusivo (default: ahora)",
    )
    parser.add_argument("--dry-run", action="store_true", help="evalua e imprime, no envia")
    args = parser.parse_args(argv)
    ahora = datetime.now(UTC)
    hasta = args.hasta if args.hasta is not None else ahora
    desde = (
        args.desde
        if args.desde is not None
        else ahora.replace(hour=4, minute=30, second=0, microsecond=0)
    )
    if desde >= hasta:
        print(
            f"ventana invalida: desde ({desde.isoformat()}) debe ser"
            f" < hasta ({hasta.isoformat()}); sin lectura y sin aviso",
            file=sys.stderr,
        )
        return 2
    dsn = os.environ.get("ORBIT_DSN_READ")
    try:
        if not dsn:
            raise RuntimeError("ORBIT_DSN_READ no esta definido")
        # connect_timeout: un host blackholeado cae al camino ciego en
        # segundos en vez de colgar ~2 min con el flock tomado.
        conn = connect(dsn, connect_timeout=10)
        try:
            filas = lee_ventana(conn, desde=desde, hasta=hasta)
        finally:
            conn.close()
    except Exception as exc:
        motivo = scrub(str(exc)) or type(exc).__name__
        print(f"vigilante ciego: {motivo}", file=sys.stderr)
        if not args.dry_run:
            avisa_ciego(motivo)
        return 2
    ausentes = faltantes(filas, desde=desde, hasta=hasta)  # orden catalogo
    ausentes_set = set(ausentes)
    for fuente in FUENTES_SPAPI:
        for plataforma in PLATAFORMAS_SPAPI:
            estado = "ausente" if (fuente, plataforma) in ausentes_set else "presente"
            print(f"- {fuente} / {plataforma}: {estado}")
    total = len(FUENTES_SPAPI) * len(PLATAFORMAS_SPAPI)
    print(f"faltan {len(ausentes)} de {total}")
    if not ausentes:
        return 0
    if not args.dry_run:
        notifica_spapi_silencio(ausentes, desde, hasta)
    return 1
