"""Candado de cobertura (ORBIT 06 0.7): que fraccion del GASTO publicitario
real puede costearse de punta a punta.

La cadena completa es anuncio → listing (0.2/0.4) → costo vigente (0.1) →
FX utilizable (0.5). El gasto vive en las metricas de keyword/product_target
(los product ads NO tienen metricas propias), asi que la unidad de
clasificacion es el AD GROUP: su gasto maduro se pondera por el estado de la
cadena de SUS product ads vivos.

Vocabulario cerrado de estados (un grupo cae en EXACTAMENTE uno):

- cubierto_unico      la cadena resuelve completa y el grupo anuncia UN
                      producto: margen atribuible directo.
- cubierto_multi_asin la cadena resuelve completa pero el grupo anuncia
                      VARIOS productos: margen computable a nivel grupo; la
                      atribucion por producto es decision de la vista 1.1.
                      El resumen publica DOS numeros — cobertura a nivel
                      grupo (unico+multi) y estricta (solo mono-producto) —
                      porque el DoD original listaba multi-ASIN como no
                      cubierto ANTES de que la 0.4 midiera que casi todo el
                      gasto es multi-ASIN (MX 4/32, US 0/48 mono-producto).
                      El umbral lo decide el dueno viendo LOS DOS.
- sin_costo           todos los ads con listing, pero algun producto sin
                      costo VIGENTE (valid_to IS NULL) en sku_cost.
- sin_listing         algun ad vivo sin listing_id: el producto anunciado ni
                      siquiera esta identificado.
- sin_anuncios_vivos  el grupo no tiene ningun product ad ENABLED/PAUSED.
                      OJO: clasifica, NO desaparece — la medicion manual del
                      lead (2026-08-31) filtraba el estado en WHERE y estos
                      grupos se esfumaban con su gasto.
- grupo_desconocido   defensa: gasto cuyo padre no aparece como ad_group
                      (no deberia ocurrir; si ocurre, se VE).

Ponderacion por GASTO y jamas un SUM mezclando monedas (regla 4): todo sale
por (plataforma, moneda). `gasto_sin_fx` cuenta el gasto no-MXN de dias sin
tasa utilizable via fx_resolve (sellado 3: la funcion no se toca).

SOLO LECTURA con el rol de lectura (`ORBIT_DSN_READ`, orbit_read: SELECT
si, escritura no — minimo privilegio, hallazgo cross-review codex). La
ventana son N dias de metricas MADURAS (v_metric_mature, corte D-15): el dia
en curso miente (~1.5x) y no se pondera.

LIMITACION DECLARADA (hallazgo codex 2): la clasificacion usa el estado
ACTUAL de la cadena (product ads vivos HOY, costo vigente HOY). El gasto
historico de la ventana hereda esa clasificacion — no existe historia de
membresia de product ads para reconstruir que anunciaba el grupo el dia del
gasto. Es la aproximacion honesta disponible, y va declarada en la evidencia.
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from typing import Any

import psycopg

from app.db import connect
from app.redaction import scrub

ESTADOS = (
    "cubierto_unico",
    "cubierto_multi_asin",
    "sin_costo",
    "sin_listing",
    "sin_anuncios_vivos",
    "grupo_desconocido",
)

_CUBIERTOS = frozenset({"cubierto_unico", "cubierto_multi_asin"})

# El filtro de estado de los product ads vive EN EL JOIN, no en WHERE: en
# WHERE, un grupo cuyos ads son todos ARCHIVED perdia todas sus filas y
# desaparecia de la clasificacion (bug de la medicion manual del lead).
SQL_COBERTURA = """
WITH grupo AS (
    SELECT ag.id AS ad_group_id,
           count(pa.id)                  AS ads_vivos,
           count(pa.listing_id)          AS con_listing,
           count(sc.product_id)          AS con_costo,
           count(DISTINCT li.product_id) AS productos
      FROM ad_entity ag
      LEFT JOIN (
           ad_entity pa
           JOIN ad_entity_state pas
             ON pas.ad_entity_id = pa.id
            AND pas.status IN ('ENABLED', 'PAUSED')
      ) ON pa.parent_id = ag.id AND pa.kind = 'product_ad'
      LEFT JOIN listing li ON li.id = pa.listing_id
      LEFT JOIN LATERAL (
            SELECT sc.product_id
              FROM sku_cost sc
             WHERE sc.product_id = li.product_id
               AND sc.valid_to IS NULL
             LIMIT 1
      ) sc ON li.product_id IS NOT NULL
     WHERE ag.kind = 'ad_group'
     GROUP BY 1
), clasificado AS (
    SELECT ad_group_id,
           CASE
               WHEN ads_vivos = 0             THEN 'sin_anuncios_vivos'
               WHEN con_listing < ads_vivos   THEN 'sin_listing'
               WHEN con_costo   < con_listing THEN 'sin_costo'
               WHEN productos = 1             THEN 'cubierto_unico'
               ELSE                                'cubierto_multi_asin'
           END AS estado
      FROM grupo
), gasto AS (
    SELECT e.platform,
           m.metric_currency,
           e.parent_id AS ad_group_id,
           m.cost,
           (m.metric_currency = 'MXN'::currency OR fx.rate IS NOT NULL) AS fx_ok
      FROM v_metric_mature m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      LEFT JOIN LATERAL (
            SELECT r.rate
              FROM fx_resolve(m.metric_date, m.metric_currency, 'MXN'::currency) r
      ) fx ON m.metric_currency <> 'MXN'::currency
     WHERE e.kind IN ('keyword', 'product_target')
       AND m.cost IS NOT NULL AND m.cost > 0
       AND m.metric_date > (now() AT TIME ZONE 'UTC')::date - 15 - %(dias)s
)
SELECT g.platform,
       g.metric_currency,
       COALESCE(c.estado, 'grupo_desconocido')                        AS estado,
       count(DISTINCT g.ad_group_id)                                  AS grupos,
       sum(g.cost)                                                    AS gasto,
       COALESCE(sum(g.cost) FILTER (WHERE NOT g.fx_ok), 0)            AS gasto_sin_fx
  FROM gasto g
  LEFT JOIN clasificado c ON c.ad_group_id = g.ad_group_id
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 5 DESC
"""


def medir_cobertura(conn: psycopg.Connection, dias: int) -> list[tuple]:
    """Filas (platform, moneda, estado, grupos, gasto, gasto_sin_fx)."""
    if dias <= 0:
        raise ValueError(f"ventana invalida: {dias} dias")
    return list(conn.execute(SQL_COBERTURA, {"dias": dias}))


def resumen_por_plataforma(filas: list[tuple]) -> dict[tuple[str, str], dict[str, Any]]:
    """Por (plataforma, moneda): total, cubierto y % ponderado por gasto."""
    resumen: dict[tuple[str, str], dict[str, Any]] = {}
    for plataforma, moneda, estado, _grupos, gasto, gasto_sin_fx in filas:
        clave = (plataforma, moneda)
        fila = resumen.setdefault(
            clave,
            {
                "gasto_total": Decimal(0),
                "gasto_cubierto": Decimal(0),
                "gasto_cubierto_unico": Decimal(0),
                "gasto_sin_fx": Decimal(0),
                "pct": 0.0,
                "pct_estricta": 0.0,
            },
        )
        fila["gasto_total"] += gasto
        fila["gasto_sin_fx"] += gasto_sin_fx
        if estado in _CUBIERTOS:
            # El gasto sin FX no cuenta como cubierto aunque la cadena de
            # producto resuelva: sin tasa no hay margen comparable.
            fila["gasto_cubierto"] += gasto - gasto_sin_fx
        if estado == "cubierto_unico":
            fila["gasto_cubierto_unico"] += gasto - gasto_sin_fx
    for fila in resumen.values():
        if fila["gasto_total"]:
            fila["pct"] = float(100 * fila["gasto_cubierto"] / fila["gasto_total"])
            fila["pct_estricta"] = float(100 * fila["gasto_cubierto_unico"] / fila["gasto_total"])
    return resumen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli report cobertura",
        description=(
            "Candado de cobertura 0.7: fraccion del gasto maduro que puede"
            " costearse de punta a punta (SOLO LECTURA)."
        ),
    )
    parser.add_argument(
        "--ventana-dias",
        type=int,
        default=90,
        help="dias de metricas MADURAS a ponderar (default 90, la ventana de margen)",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        print(
            "ORBIT_DSN_READ no esta definido: no se puede medir (fail-closed)",
            file=sys.stderr,
        )
        return 2
    try:
        conn = connect(dsn)
        try:
            filas = medir_cobertura(conn, dias=args.ventana_dias)
        finally:
            conn.close()
    except Exception as exc:
        print(f"medicion de cobertura fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1

    print(f"== Cobertura del gasto (ventana {args.ventana_dias}d de metricas maduras) ==")
    for plataforma, moneda, estado, grupos, gasto, gasto_sin_fx in filas:
        extra = f"  sin_fx={gasto_sin_fx}" if gasto_sin_fx else ""
        print(f"  {plataforma:10} {moneda} {estado:22} grupos={grupos:4} gasto={gasto}{extra}")
    for (plataforma, moneda), fila in sorted(resumen_por_plataforma(filas).items()):
        print(
            f">> {plataforma} ({moneda}): cobertura a nivel GRUPO {fila['pct']:.1f}%"
            f"  ({fila['gasto_cubierto']} de {fila['gasto_total']})"
            f"  |  estricta (grupo mono-producto) {fila['pct_estricta']:.1f}%"
        )
    print(
        "NOTA: 'a nivel grupo' = la cadena resuelve para TODOS los productos"
        " del grupo; la atribucion por-producto de los multi-ASIN la define"
        " la vista 1.1. 'estricta' = solo grupos mono-producto."
    )
    return 0
