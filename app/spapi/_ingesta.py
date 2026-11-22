"""Infraestructura compartida de las 4 ingestas SP-API (pricing, listings, orders, inventario).

Consolida politicas de umbral de fallos, apertura/sellado de `ingest_run` con
alertas en flanco (`evaluar_alertas`), paginacion con guardas, ciclo por
entidad y utilidades puras repetidas.
"""

from __future__ import annotations

import datetime
import os
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.ingest_run import abrir_run, formato_skip_reason, sellar_fallo_seguro, sellar_run
from app.redaction import scrub
from app.spapi.client import CuboTasa, SpapiClient, prefijo_motivo, sanear, siguiente_token
from app.spapi.salud import evaluar_alertas

# Politica de fallos por entidad (F2): 401/429 son fatales; 403/404 omiten;
# 5xx/red/contrato abortan al superar 20% (con piso de 5 muestras si el
# universo tiene >= 5) o 25 fallos seguidos.
UMBRAL_FALLOS_PORC = 0.20
UMBRAL_FALLOS_RACHA = 25
HTTP_OMITIDO = frozenset({403, 404})
HTTP_FATAL = frozenset({401, 429})

MONEDAS_SPAPI = frozenset({"MXN", "USD"})
_MAX_DINERO = Decimal(10) ** 10
_MAX_DECIMALES = Decimal("0.0001")


class FalloHttp(Exception):
    """Una llamada SP-API volvio status != 200; el pase decide por codigo (F2)."""

    def __init__(self, fuente: str, clave: str, status: int) -> None:
        super().__init__(f"{fuente} {clave} status={status}")
        self.clave = clave
        self.status = status


@dataclass
class AvanceEntidad:
    """Contadores mutables de un pase entidad-por-entidad (pricing/listings)."""

    escritas: int = 0
    vistas: int = 0
    fallos: int = 0
    racha: int = 0
    llamadas: int = 0
    skips: Counter = field(default_factory=Counter)
    ultimo_status: int | None = None
    total: int | None = None


def texto_limpio(valor: Any) -> str | None:
    if isinstance(valor, str) and valor.strip():
        return valor.strip()
    return None


def entero_no_negativo(valor: Any) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return None
    return valor


def parsear_dinero_spapi(
    obj: Any, *, aceptar_camel: bool = False
) -> tuple[Decimal | None, str | None]:
    """Normaliza objeto Money de Amazon (`Amount`/`CurrencyCode`, opcional camelCase)."""
    if not isinstance(obj, dict):
        return None, None
    moneda = obj.get("CurrencyCode")
    if moneda is None and aceptar_camel:
        moneda = obj.get("currencyCode")
    if not isinstance(moneda, str) or moneda.strip() not in MONEDAS_SPAPI:
        return None, None
    bruto = obj.get("Amount")
    if bruto is None and aceptar_camel:
        bruto = obj.get("amount")
    try:
        monto = Decimal(str(bruto))
    except (InvalidOperation, ValueError, TypeError):
        return None, None
    if not monto.is_finite() or abs(monto) >= _MAX_DINERO:
        return None, None
    try:
        monto = monto.quantize(_MAX_DECIMALES)
    except InvalidOperation:
        return None, None
    return monto, moneda.strip()


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


def llamar_json_spapi(
    client: SpapiClient,
    cubo: CuboTasa,
    *,
    path: str,
    params: dict,
    fuente: str,
    clave: str,
    error_cls: type[Exception],
) -> Any:
    """GET con limitador + verificacion de status 200 + saneamiento JSON."""
    resp = client.get(path, params=params, limitador=cubo)
    if resp.status_code != 200:
        raise FalloHttp(fuente, clave, resp.status_code)
    try:
        return sanear(resp.json(), fuente)
    except ValueError:
        raise error_cls(f"{fuente} {clave} respuesta no JSON") from None


def ejecutar_con_politica_f2(
    *,
    clave: str,
    fuente: str,
    error_cls: type[Exception],
    omitido_cls: type[Exception],
    av: AvanceEntidad,
    obtener_y_parsear: Callable[[], Any],
    vigilar: Callable[..., None],
) -> tuple[bool, Any]:
    """Ejecuta consulta+parseo de una entidad bajo politica F2 (pricing/listings)."""
    try:
        return True, obtener_y_parsear()
    except omitido_cls as exc:
        av.skips[getattr(exc, "motivo", str(exc))] += 1
        av.racha = 0
        return False, None
    except FalloHttp as exc:
        if exc.status in HTTP_OMITIDO:
            av.skips[f"http_{exc.status}"] += 1
            av.racha = 0
            return False, None
        if exc.status in HTTP_FATAL:
            raise error_cls(f"{fuente} {clave} status={exc.status} persistente") from exc
        av.fallos += 1
        av.racha += 1
        av.skips[f"http_{exc.status}"] += 1
        av.ultimo_status = exc.status
        vigilar(clave, av.fallos, av.racha, av.vistas, av.ultimo_status, total=av.total)
        return False, None
    except error_cls:
        av.fallos += 1
        av.racha += 1
        av.skips["contrato"] += 1
        vigilar(clave, av.fallos, av.racha, av.vistas, av.ultimo_status, total=av.total)
        return False, None
    except httpx.HTTPError:
        av.fallos += 1
        av.racha += 1
        av.skips["red"] += 1
        vigilar(clave, av.fallos, av.racha, av.vistas, av.ultimo_status, total=av.total)
        return False, None


def recorrer_paginas_spapi(
    client: SpapiClient,
    *,
    ruta: str,
    params_base: dict,
    param_token: str,
    fuente: str,
    error_cls: type[Exception],
    extraer_lote: Callable[[Any], list[dict]],
    capacidad: int,
    tasa: float,
    pagina_vacia: Callable[[Any, list[dict]], bool] | None = None,
    max_paginas: int = 100,
    cubo: CuboTasa | None = None,
    medidor: Counter | None = None,
) -> tuple[list[dict], dict]:
    """Recorrido paginado con guardas compartidas (orders e inventario)."""
    if max_paginas < 1:
        raise error_cls("--max-paginas debe ser >= 1")
    if cubo is None:
        cubo = CuboTasa(
            sleep=client._sleep,
            clock=client._clock,
            capacidad=capacidad,
            tasa=tasa,
        )
    acumulados: list[dict] = []
    vistos: set[str] = set()
    token: str | None = None
    aviso: str | None = None
    siguiente: str | None = None
    paginas = 0
    llamadas = 0
    for pagina in range(1, max_paginas + 1):
        params = dict(params_base)
        if token is not None:
            params[param_token] = token
        resp = client.get(ruta, params=params, limitador=cubo)
        llamadas += 1
        if medidor is not None:
            medidor["llamadas"] += 1
        if resp.status_code != 200:
            raise error_cls(f"{fuente} status={resp.status_code}")
        try:
            carga = resp.json()
        except ValueError:
            raise error_cls(f"{fuente} respuesta no JSON") from None
        carga = sanear(carga, fuente)
        lote = extraer_lote(carga)
        paginas = pagina
        acumulados.extend(lote)
        siguiente = siguiente_token(carga)
        if siguiente is None:
            break
        if siguiente in vistos:
            aviso = "next_token_repetido"
            break
        esta_vacia = pagina_vacia(carga, lote) if pagina_vacia is not None else not lote
        if esta_vacia:
            aviso = "pagina_vacia_con_token"
            break
        vistos.add(siguiente)
        token = siguiente
    else:
        if siguiente is not None:
            aviso = "limite_max_paginas"
    return acumulados, {
        "paginas": paginas,
        "conteo": len(acumulados),
        "aviso_paginacion": aviso,
        "llamadas": llamadas,
    }


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
    llamadas: int | None = None,
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


def sellar_con_aviso_paginacion(
    conn: Any,
    run_id: int,
    *,
    aviso: str | None,
    escritas: int,
    skips: Counter,
    llamadas: int | None = None,
) -> None:
    """Sella un pase paginado: con aviso queda `ok=False` preservando filas escritas."""
    motivo = formato_skip_reason(Counter({k: v for k, v in skips.items() if v > 0}))
    if aviso is not None:
        motivo_aviso = f"paginacion_incompleta:{aviso}"
        if motivo:
            motivo_aviso = f"{motivo_aviso}; {motivo}"
        sellar_spapi(
            conn,
            run_id,
            ok=False,
            escritas=escritas,
            skips=skips,
            motivo=motivo_aviso,
            llamadas=llamadas,
        )
    else:
        sellar_spapi(
            conn,
            run_id,
            ok=True,
            escritas=escritas,
            skips=skips,
            motivo=motivo,
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
    llamadas: int | None = None,
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


def ejecutar_cli_spapi(
    *,
    source: str,
    correr: Callable[[Any, SpapiClient], Any],
    cliente_factory: Callable[[], SpapiClient] = SpapiClient,
) -> tuple[int, Any | None]:
    """Abre DB con ORBIT_DSN_INGEST, ejecuta `correr(conn, cliente_factory())` y cierra."""
    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir (fail-closed)",
            file=sys.stderr,
        )
        return 2, None
    try:
        from app.db import connect

        conn = connect(dsn)
        try:
            return 0, correr(conn, cliente_factory())
        finally:
            conn.close()
    except Exception as exc:
        print(f"ingesta {source} fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1, None


__all__ = [
    "AvanceEntidad",
    "FalloHttp",
    "HTTP_FATAL",
    "HTTP_OMITIDO",
    "MONEDAS_SPAPI",
    "UMBRAL_FALLOS_PORC",
    "UMBRAL_FALLOS_RACHA",
    "abrir_run_spapi",
    "dia_utc",
    "ejecutar_cli_spapi",
    "ejecutar_con_politica_f2",
    "entero_no_negativo",
    "formato_skip_reason",
    "llamar_json_spapi",
    "parsear_dinero_spapi",
    "recorrer_paginas_spapi",
    "sellar_con_aviso_paginacion",
    "sellar_fallo_spapi",
    "sellar_spapi",
    "texto_limpio",
    "vigilar_umbral",
]
