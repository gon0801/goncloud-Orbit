#!/usr/bin/env python3
"""P.6: el contrato da los mismos numeros que las consultas de control.

Sobre la copia local de prod (este script solo hace SELECT), corre
`lee_busquedas` para la plataforma y el dia
dados y compara ASIN, referencia de azar y palabras contra las tres
consultas de `control.sql` (tablas base, sin usar el codigo del paso).
Las fechas impresas son las que recibe `control.sql` por -v.

Uso: desde la raiz del repo,
  ORBIT_COPIA_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_p1 \
    .venv/bin/python docs/evidencia/bids-02/ejecucion/P.6/numeros.py \
      --plataforma amazon_mx --hoy 2026-10-07
Sale 0 con "NUMEROS OK" si todo coincide, 1 con el diff si no.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

import psycopg

from app.pantalla_busquedas import lee_busquedas

_ULTIMA = """
WITH ultima AS (
    SELECT DISTINCT ON (o.ad_entity_id, o.search_term, o.metric_date)
           o.ad_entity_id, o.search_term, o.is_asin_like, o.cost, o.clicks, o.orders,
           c.id AS campana_id
      FROM search_term_observation o
      JOIN ad_entity ag ON ag.id = o.ad_entity_id
      JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
      JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
     WHERE o.platform = %s::platform AND o.metric_date BETWEEN %s AND %s
     ORDER BY o.ad_entity_id, o.search_term, o.metric_date,
              o.observed_at DESC, o.source_report_id DESC NULLS LAST
), busqueda AS (
    SELECT ad_entity_id, search_term, campana_id, bool_or(is_asin_like) AS es_asin,
           sum(cost) AS gasto, sum(clicks) AS clics, sum(orders) AS pedidos
      FROM ultima GROUP BY 1, 2, 3
    HAVING bool_and(cost IS NOT NULL AND clicks IS NOT NULL AND orders IS NOT NULL))
"""

CONTROL_ASIN = (
    _ULTIMA
    + """
SELECT upper(btrim(search_term)) AS asin, sum(gasto), sum(clics), sum(pedidos),
       count(DISTINCT campana_id),
       bool_or(upper(btrim(search_term)) IN (SELECT upper(external_id) FROM listing
                                              WHERE platform IN ('amazon_mx', 'amazon_us')))
  FROM busqueda WHERE es_asin GROUP BY 1 ORDER BY 2 DESC, 1
"""
)

CONTROL_REFERENCIA = (
    _ULTIMA
    + """
, cuenta AS (
    SELECT sum(gasto) AS gasto, sum(clics) AS clics, sum(pedidos) AS pedidos,
           sum(pedidos) / NULLIF(sum(clics), 0) AS p FROM busqueda)
SELECT c.gasto, c.clics, c.pedidos,
       round(100 * sum(b.gasto) FILTER (WHERE b.pedidos = 0) / NULLIF(c.gasto, 0)),
       round(100 * sum(b.gasto * power(1 - c.p, b.clics)) / NULLIF(c.gasto, 0))
  FROM busqueda b CROSS JOIN cuenta c GROUP BY c.gasto, c.clics, c.pedidos, c.p
"""
)

CONTROL_PALABRAS = (
    _ULTIMA
    + """
, ficha AS (
    SELECT b.ad_entity_id, b.search_term, b.gasto, b.clics, b.pedidos, t.n, t.m[1] AS palabra
      FROM busqueda b
     CROSS JOIN LATERAL regexp_matches(lower(b.search_term), '[a-záéíóúñü0-9]+', 'g')
       WITH ORDINALITY AS t(m, n)
     WHERE NOT b.es_asin
), entrada AS (
    SELECT DISTINCT ad_entity_id, search_term, gasto, clics, pedidos, palabra FROM ficha
     WHERE length(palabra) > 2 AND palabra <> ALL (string_to_array(
           'de la el los las para y con en un una por del a al que'
           ' the of for and to in on with', ' '))
    UNION ALL
    SELECT DISTINCT a.ad_entity_id, a.search_term, a.gasto, a.clics, a.pedidos,
           a.palabra || ' ' || s.palabra
      FROM ficha a JOIN ficha s ON s.ad_entity_id = a.ad_entity_id
       AND s.search_term = a.search_term AND s.n = a.n + 1)
SELECT palabra, count(DISTINCT search_term), sum(clics), sum(gasto)
  FROM entrada GROUP BY 1 HAVING sum(pedidos) = 0 AND sum(gasto) >= %s / 2 ORDER BY 4 DESC, 1
"""
)


def _minimo(conn, plataforma: str) -> Decimal:
    fila = conn.execute(
        "SELECT settings->>%s FROM config_version ORDER BY id DESC LIMIT 1",
        (f"ads_gasto_para_concluir_{plataforma}",),
    ).fetchone()
    if fila is not None and fila[0] is not None:
        return Decimal(fila[0])
    return Decimal("350") if plataforma == "amazon_mx" else Decimal("36")


def compara(conn, plataforma: str, hoy: dt.date) -> list[str]:
    diffs: list[str] = []
    pantalla = lee_busquedas(conn, plataforma=plataforma, hoy=hoy)
    minimo = _minimo(conn, plataforma)
    args = (plataforma, pantalla.desde, pantalla.hasta)
    print(f"== {plataforma} {pantalla.desde}..{pantalla.hasta} moneda={pantalla.moneda}")
    print(
        f"   minimo={minimo} (psql -v plataforma={plataforma} -v desde={pantalla.desde}"
        f" -v hasta={pantalla.hasta} -v minimo={minimo})"
    )

    ctrl_asin = {f[0]: f[1:] for f in conn.execute(CONTROL_ASIN, args).fetchall()}
    entran = {k: v for k, v in ctrl_asin.items() if v[2] == 0 and v[0] >= minimo}
    medio = sum(1 for v in ctrl_asin.values() if v[2] == 0 and minimo / 2 <= v[0] < minimo)
    vistos = {}
    for a in pantalla.asins:
        vistos[a.asin] = a
        ctrl = entran.get(a.asin)
        marca = (
            ctrl is not None
            and a.gasto == ctrl[0]
            and a.clics == ctrl[1]
            and len(a.campanas) == ctrl[3]
            and a.es_propio == ctrl[4]
        )
        print(
            f"   ASIN {a.asin}: gasto {a.gasto} clics {a.clics} propio {a.es_propio}"
            f" vs {ctrl} [{'ok' if marca else 'DIFF'}]"
        )
        if not marca:
            diffs.append(f"{plataforma} ASIN {a.asin}: contrato {a} vs control {ctrl}")
    for asin in entran:
        if asin not in vistos:
            diffs.append(f"{plataforma}: control trae ASIN que el contrato no: {asin}")
    if pantalla.asins_a_medio_camino != medio:
        diffs.append(
            f"{plataforma}: asins a medio camino {pantalla.asins_a_medio_camino} vs {medio}"
        )

    fila_ref = conn.execute(CONTROL_REFERENCIA, args).fetchone()
    gasto_c, _clics_c, pedidos_c, visto_c, azar_c = fila_ref if fila_ref else (None,) * 5
    ref = pantalla.referencia
    if ref.visto_pct is None:
        marca = pedidos_c in (0, None) or gasto_c in (0, None)
        print(
            f"   referencia: None (control pedidos={pedidos_c} gasto={gasto_c})"
            f" [{'ok' if marca else 'DIFF'}]"
        )
        if not marca:
            diffs.append(f"{plataforma}: referencia None contra control {fila_ref}")
    else:
        marca = int(ref.visto_pct) == int(visto_c) and int(ref.azar_pct) == int(azar_c)
        print(
            f"   referencia: visto {ref.visto_pct} vs {visto_c} | azar {ref.azar_pct}"
            f" vs {azar_c} [{'ok' if marca else 'DIFF'}]"
        )
        if not marca:
            diffs.append(f"{plataforma}: referencia {ref} vs control {fila_ref}")

    ctrl_pal = {f[0]: f[1:] for f in conn.execute(CONTROL_PALABRAS, (*args, minimo)).fetchall()}
    entran_p = {k: v for k, v in ctrl_pal.items() if v[2] >= minimo}
    medio_p = len(ctrl_pal) - len(entran_p)
    vistas = {}
    for p in pantalla.palabras:
        vistas[p.palabra] = p
        ctrl = entran_p.get(p.palabra)
        marca = ctrl is not None and (p.busquedas, p.clics, p.gasto) == ctrl
        print(
            f"   palabra {p.palabra!r}: {p.busquedas} busq {p.clics} clics gasto {p.gasto}"
            f" vs {ctrl} [{'ok' if marca else 'DIFF'}]"
        )
        if not marca:
            diffs.append(f"{plataforma} palabra {p.palabra!r}: contrato {p} vs control {ctrl}")
    for palabra in entran_p:
        if palabra not in vistas:
            diffs.append(f"{plataforma}: control trae palabra que el contrato no: {palabra!r}")
    if pantalla.palabras_a_medio_camino != medio_p:
        diffs.append(
            f"{plataforma}: palabras a medio camino {pantalla.palabras_a_medio_camino} vs {medio_p}"
        )
    print(
        f"   a medio camino: asins {pantalla.asins_a_medio_camino} vs {medio}"
        f" | palabras {pantalla.palabras_a_medio_camino} vs {medio_p}"
    )
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plataforma", required=True, choices=("amazon_mx", "amazon_us"))
    parser.add_argument("--hoy", required=True, type=dt.date.fromisoformat)
    args = parser.parse_args()
    dsn = os.environ.get(
        "ORBIT_COPIA_DSN", "postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_p1"
    )
    with psycopg.connect(dsn) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        diffs = compara(conn, args.plataforma, args.hoy)
    if diffs:
        print("DIFFS:")
        for diff in diffs:
            print(" -", diff)
        return 1
    print("NUMEROS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
