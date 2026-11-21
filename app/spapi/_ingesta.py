"""Infraestructura compartida de las 4 ingestas SP-API (pricing, listings, orders, inventario).

Consolida politicas de umbral de fallos, apertura/sellado de `ingest_run` con
alertas en flanco (`evaluar_alertas`), y utilidades puras repetidas.
"""

from __future__ import annotations

import datetime
from collections import Counter
from typing import Any

from app.ingest_run import abrir_run, formato_skip_reason, sellar_fallo_seguro, sellar_run
from app.redaction import scrub
from app.spapi.client import prefijo_motivo
from app.spapi.salud import evaluar_alertas

# Politica de fallos por entidad (F2): 401/429 son fatales; 403/404 omiten;
# 5xx/red/contrato abortan al superar 20% (con piso de 5 muestras si el
# universo tiene >= 5) o 25 fallos seguidos.
UMBRAL_FALLOS_PORC = 0.20
UMBRAL_FALLOS_RACHA = 25
HTTP_OMITIDO = frozenset({403, 404})
HTTP_FATAL = frozenset({401, 429})


def texto_limpio(valor: Any) -> str | None:
    if isinstance(valor, str) and valor.strip():
        return valor.strip()
    return None


def dia_utc(momento: datetime.datetime) -> datetime.date:
    """Dia UTC de un datetime aware (o del naive asumido UTC)."""
    if momento.tzinfo is None:
        return momento.date()
    return momento.astimezone(datetime.UTC).date()


def vigilar_umbral(
    clave: str,
    fallos: int,
    racha: int,
    vistos: int,
    ultimo_status: int | None,
    *,
    fuente: str,
    error_cls: type[Exception],
    total: int | None = None,
) -> None:
    """Aborta si los fallos superan el 20% (con piso de muestra) o la racha tope."""
    piso = 5 if total is not None and total >= 5 else 1
    if (vistos >= piso and fallos > UMBRAL_FALLOS_PORC * vistos) or racha >= UMBRAL_FALLOS_RACHA:
        status = ultimo_status if ultimo_status is not None else "desconocido"
        raise error_cls(
            f"{fuente} {clave}: umbral de fallos superado ({fallos}/{vistos},"
            f" racha {racha}, ultimo status={status})"
        )


def abrir_run_spapi(conn: Any, source: str, platform: str) -> int:
    with conn.transaction():
        return abrir_run(conn, source, platform=platform)


def sellar_spapi(
    conn: Any,
    run_id: int,
    *,
    ok: bool,
    escritas: int,
    skips: Counter,
    motivo: str | None,
    llamadas: int,
) -> None:
    sellar_run(
        conn,
        run_id,
        ok=ok,
        rows_written=escritas,
        rows_skipped=sum(v for v in skips.values() if v > 0),
        skip_reason=motivo,
        llamadas=llamadas,
    )


def sellar_fallo_spapi(
    conn: Any,
    run_id: int,
    exc: BaseException,
    *,
    source: str,
    platform: str,
    escritas: int = 0,
    skips: Counter | None = None,
    llamadas: int = 0,
) -> None:
    """Sella `ok=False` con prefijo taxonomico y dispara `evaluar_alertas`."""
    motivo = f"{prefijo_motivo(exc)}: {scrub(str(exc)) or type(exc).__name__}"
    omitidas = sum(v for v in (skips or Counter()).values() if v > 0)
    sellar_fallo_seguro(
        conn,
        run_id,
        exc,
        rows_written=escritas,
        rows_skipped=omitidas,
        motivo=motivo,
        llamadas=llamadas,
        tras_sellar=lambda: evaluar_alertas(conn, source, platform, run_id),
    )


__all__ = [
    "HTTP_FATAL",
    "HTTP_OMITIDO",
    "UMBRAL_FALLOS_PORC",
    "UMBRAL_FALLOS_RACHA",
    "abrir_run_spapi",
    "dia_utc",
    "formato_skip_reason",
    "sellar_fallo_spapi",
    "sellar_spapi",
    "texto_limpio",
    "vigilar_umbral",
]
