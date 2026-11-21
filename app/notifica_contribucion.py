"""Lectura SQL y formateo de contribucion economica para el digest Telegram.

Extraido de `app/notifica.py` en Fase 3.1 para separar las consultas de
`v_contribucion_entidad` / `v_contribucion_cobertura` / `v_tacos` del
transporte generico de Telegram.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal

from app.db import connect, preparar_lectura_contribucion
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

ETIQUETA_CONTRIBUCION = "contribucion pre-cargos · no decisoria"

SQL_CONTRIB_RANGO = """
SELECT metric_currency::text,
       count(*)::int,
       sum(contrib_sin_halo),
       sum(contrib_con_halo),
       bool_or(rango_invertido) AS rango_invertido,
       bool_or(precio_min_multilisting) AS precio_min_multilisting
  FROM v_contribucion_entidad
 WHERE platform = %s::platform
 GROUP BY metric_currency
"""

_CONTRIB_CONNECT_TIMEOUT = 5
_CONTRIB_STATEMENT_TIMEOUT_MS = 10_000

SQL_CONTRIB_AUSENTES = """
SELECT motivo, count(*)::int AS n
  FROM v_contribucion_cobertura
 WHERE platform = %s::platform
 GROUP BY motivo
 ORDER BY n DESC, motivo
"""

SQL_RESIDUAL_TACOS = """
SELECT gasto_campaign_sin_contraparte
  FROM v_tacos
 WHERE platform = %s::platform
   AND mes = date_trunc(
           'month',
           ((now() AT TIME ZONE 'UTC')::date - 15)
       )::date
"""


@dataclass(frozen=True)
class RangoContribucion:
    moneda: str
    entidades: int
    sin_halo: Decimal
    con_halo: Decimal
    invertido: bool = False
    entidades_maduras: int | None = None
    # 0008: alguna entidad publicada uso el precio MENOR de un producto US
    # multilisting (enmienda D1.bis) — la linea del digest lo declara.
    precio_min_multilisting: bool = False


@dataclass(frozen=True)
class SinDatoContribucion:
    total_ausentes: int
    por_motivo: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ResidualTacos:
    monto: Decimal


@dataclass(frozen=True)
class ContribucionDigest:
    rango: RangoContribucion | None
    sin_dato: SinDatoContribucion | None
    residual_tacos: ResidualTacos | None
    lectura_fallida: bool = False


def formatea_monto(valor: Decimal) -> str:
    return format(valor.quantize(Decimal("0.01")), "f")


def linea_contribucion(datos: ContribucionDigest) -> str | None:
    if datos.lectura_fallida:
        return f"{ETIQUETA_CONTRIBUCION}: lectura no disponible"
    if datos.rango is not None:
        r = datos.rango
        sufijo = ""
        if r.entidades_maduras is not None and r.entidades_maduras > r.entidades:
            sufijo = f" de {r.entidades_maduras} entidades maduras"
        if r.precio_min_multilisting:
            sufijo += " · precio min multilisting"
        if r.invertido:
            cuerpo = (
                f"totales sin_halo={formatea_monto(r.sin_halo)},"
                f" con_halo={formatea_monto(r.con_halo)} {r.moneda}"
                f" ({r.entidades} entidades{sufijo}; rango_invertido en alguna)"
            )
        else:
            cuerpo = (
                f"{formatea_monto(r.sin_halo)} .. {formatea_monto(r.con_halo)}"
                f" {r.moneda} ({r.entidades} entidades{sufijo})"
            )
    elif datos.sin_dato is not None:
        s = datos.sin_dato
        motivos = ", ".join(f"{m} {n}" for m, n in s.por_motivo)
        cuerpo = f"sin dato ({s.total_ausentes} entidades ausentes: {motivos})"
    else:
        return None
    return f"{ETIQUETA_CONTRIBUCION}: {cuerpo}"


def _arma_contribucion_digest(
    filas_rango: list[tuple],
    filas_ausentes: list[tuple],
    residual: Decimal | None,
) -> ContribucionDigest | None:
    rango: RangoContribucion | None = None
    sin_dato: SinDatoContribucion | None = None
    por_motivo = tuple((m, n) for m, n in filas_ausentes)
    total_ausentes = sum(n for _, n in por_motivo)
    if len(filas_rango) == 1:
        moneda, entidades, sin_h, con_h, invertido, multilisting = filas_rango[0]
        if entidades and sin_h is not None and con_h is not None:
            maduras = entidades + total_ausentes if total_ausentes else None
            rango = RangoContribucion(
                moneda,
                entidades,
                sin_h,
                con_h,
                bool(invertido),
                maduras,
                bool(multilisting),
            )
    elif len(filas_rango) > 1 and total_ausentes > 0:
        sin_dato = SinDatoContribucion(total_ausentes, por_motivo)
    if rango is None and sin_dato is None and total_ausentes > 0:
        sin_dato = SinDatoContribucion(total_ausentes, por_motivo)
    if rango is None and sin_dato is None:
        return None
    res_tacos = ResidualTacos(residual) if residual is not None and residual != 0 else None
    return ContribucionDigest(rango=rango, sin_dato=sin_dato, residual_tacos=res_tacos)


def _contrib_conn(dsn: str):
    conn = connect(dsn, connect_timeout=_CONTRIB_CONNECT_TIMEOUT)
    conn.execute(f"SET statement_timeout = {_CONTRIB_STATEMENT_TIMEOUT_MS}")
    return conn


def carga_contribucion_digest(plataforma: str, *, conn=None) -> ContribucionDigest | None:
    """Lee v_contribucion_entidad / cobertura / v_tacos (ORBIT_DSN_READ).

    Fail-silent hacia arriba: el caller omite la seccion si devuelve None.
    Lectura fallida devuelve ContribucionDigest(lectura_fallida=True) para
    distinguirla de 'sin dato'. Solo lectura; sin ORBIT_DSN_READ -> None."""
    propia = conn is None
    try:
        if conn is None:
            dsn = os.environ.get("ORBIT_DSN_READ")
            if not dsn:
                return None
            conn = _contrib_conn(dsn)
        with conn.transaction():
            preparar_lectura_contribucion(conn)
            filas_rango = conn.execute(SQL_CONTRIB_RANGO, (plataforma,)).fetchall()
            filas_ausentes = conn.execute(SQL_CONTRIB_AUSENTES, (plataforma,)).fetchall()
        residual_row = conn.execute(SQL_RESIDUAL_TACOS, (plataforma,)).fetchone()
        residual = residual_row[0] if residual_row else None
        return _arma_contribucion_digest(filas_rango, filas_ausentes, residual)
    except Exception as exc:
        logger.warning("telegram: fallo leyendo contribucion para digest: %s", scrub(str(exc)))
        return ContribucionDigest(
            rango=None, sin_dato=None, residual_tacos=None, lectura_fallida=True
        )
    finally:
        if propia and conn is not None:
            conn.close()
