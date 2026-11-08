"""ORBIT 07 M3/M4: sonda del aprendizaje agregado de AUTO-03.

Comprueba, sobre una base PostgreSQL desechable, que AUTO-03 H/I ya cubre el
resultado de M4 (pausa por producto sin ventas con el gasto agregado) y que el
agregado de M3 no duplica gasto: la reobservacion reemplaza la fecha y anadir
otro anuncio, grupo o campana del mismo producto no reinicia ni multiplica el
umbral. Con la medicion de produccion de AUTO-03 E.4 calcula la interseccion
"candidato a pausa" y "grupo exclusivo", que es el unico alcance donde M3
podria frenar una subida de puja.

No toca produccion ni Amazon. Uso:

    $env:ORBIT_TEST_DSN='postgresql://.../postgres'
    $env:PYTHONPATH='.;tests'
    uv run --frozen python docs/evidencia/orbit-07/m3m4/sonda_aprendizaje.py
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from psycopg.types.json import Jsonb
from test_cycle import _entidad, _estado
from test_productos_io import _anuncio, _entrada, _ventana
from test_productos_pausa import _db, _mundo_pausa

from app.optimizer import productos as p

RAIZ = Path(__file__).resolve().parents[4]
REFERENCIA = "ORBIT 07 M3/M4 sobre 4e0e78d (M2) / f86ed7dc (AUTO-03)"
E4_MX = RAIZ / "docs/evidencia/auto-03/ejecucion/E/elegibilidad-mx.json"
E4_US = RAIZ / "docs/evidencia/auto-03/ejecucion/E/elegibilidad-us.json"


def _observa(conn, run: int, fecha: dt.date, *, cost: str, clicks: int, ad_ids: list[str]) -> None:
    """Una reobservacion de productos de la MISMA clave y fecha, con observed_at
    posterior a la de la ingesta (rule 5: corregir es una fila nueva)."""
    conn.execute(
        "INSERT INTO ads_product_metric_observation"
        " (platform, advertised_asin, advertised_sku, metric_date, observed_at,"
        "  metric_currency, clicks, cost, purchases30d, sales30d,"
        "  purchases_same_sku30d, attributed_sales_same_sku_30d, ad_ids,"
        "  source_report_id, ingest_run_id)"
        " VALUES ('amazon_us', 'ASIN-SKU-A', 'SS-SKU-A', %s, now(), 'USD', %s, %s,"
        "  0, 0, 0, 0, %s, 'sonda-m3m4-reobservacion', %s)",
        (fecha, clicks, Decimal(cost), Jsonb(ad_ids), run),
    )


def _resumen(entrada: p.EntradaProducto) -> dict:
    actividad = entrada.actividad_pausa
    evaluacion = p.resolver_producto(entrada)
    assert isinstance(actividad, p.AdsCompletos), actividad
    return {
        "product_id": entrada.producto.product_id,
        "politica": evaluacion.politica,
        "gasto": str(actividad.gasto.valor),
        "moneda": actividad.gasto.moneda,
        "clics": actividad.clics,
        "compras": str(actividad.compras_total),
        "ventas": str(actividad.ventas_total.valor),
        "anuncios_elegibles": sorted(a.ad_entity_id for a in entrada.anuncios),
        "propuestas": sorted(x.anuncio.ad_entity_id for x in evaluacion.pausas),
        "motivos_pausa": list(evaluacion.motivos_pausa),
        "limites": {
            "clics_min": entrada.limites_pausa.clics_min,
            "gasto_min": str(entrada.limites_pausa.gasto_min.valor),
            "moneda": entrada.limites_pausa.gasto_min.moneda,
        },
    }


def _otro_grupo(conn, m) -> int:
    """Un segundo grupo exclusivo del MISMO producto con otro product ad. El
    reporte de productos es por asin/sku, asi que no aporta filas nuevas: solo
    otro anuncio elegible bajo el mismo gasto agregado."""
    camp = _entidad(conn, "amazon_us", "campaign", "c2")
    ag = _entidad(conn, "amazon_us", "ad_group", "g2", parent=camp)
    for entidad in (camp, ag):
        _estado(conn, entidad, synced_at=dt.datetime.now(dt.UTC))
    pa = _anuncio(conn, ag, m["lid"], "pa2", "ENABLED")
    conn.execute(
        "UPDATE ad_entity SET first_seen_at = now() - interval '200 days' WHERE id = %s",
        (pa,),
    )
    return pa


def _producto_vecino(conn, ag) -> int:
    """Un product ad de otro producto en el MISMO ad group: el grupo deja de
    ser exclusivo. El vecino no debe entrar en las propuestas del producto."""
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-B', 'SKU-B') RETURNING id"
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_us', 'ASIN-SKU-B', 'SS-SKU-B') RETURNING id",
        (pid,),
    ).fetchone()[0]
    pa = _anuncio(conn, ag, lid, "pa-b", "ENABLED")
    conn.execute(
        "UPDATE ad_entity SET first_seen_at = now() - interval '200 days' WHERE id = %s",
        (pa,),
    )
    return pa


def _resumen_e4(ruta: Path) -> dict:
    datos = json.loads(ruta.read_text(encoding="utf-8"))["resumen"]
    return {
        "productos_evaluados": datos["productos_evaluados"],
        "grupos_exclusivos": datos["grupos_aptos"],
        "candidatos_pausa": datos["candidatos_pausa"],
        "anuncios_afectables": datos["anuncios_afectables"],
        "ventana_pausa_completa": datos["ventana_pausa_completa"],
        "motivos_ventana_pausa": datos["motivos_ventana_pausa"],
    }


def main() -> dict:
    with _db("orbit_m3m4_sonda") as conn:
        m = _mundo_pausa(conn)
        fechas = _ventana()
        base = _resumen(_entrada(conn, m["pid"]))

        _observa(conn, m["run"], fechas[10], cost="5", clicks=4, ad_ids=["pa1"])
        reobservado = _resumen(_entrada(conn, m["pid"]))

        pa2 = _otro_grupo(conn, m)
        # El reporte de productos es por asin/sku: la fecha observada despues
        # conoce los dos anuncios del producto y reemplaza su costo, no lo suma.
        _observa(conn, m["run"], fechas[11], cost="2", clicks=4, ad_ids=["pa1", "pa2"])
        otro_grupo = _resumen(_entrada(conn, m["pid"]))

        ag2 = conn.execute("SELECT parent_id FROM ad_entity WHERE id = %s", (pa2,)).fetchone()[0]
        vecino = _producto_vecino(conn, ag2)
        compartido = _resumen(_entrada(conn, m["pid"]))

        assert base["gasto"] == "60.0000" and base["clics"] == 120
        assert base["propuestas"] == sorted([m["pa"]])
        # 29 fechas a 2 USD + la fecha reobservada a 5 USD: la reobservacion
        # REEMPLAZA, no suma (30 x 2 + 5 = 65 seria el doble conteo).
        assert reobservado["gasto"] == "63.0000"
        assert reobservado["clics"] == 120
        assert otro_grupo["gasto"] == "63.0000" and otro_grupo["clics"] == 120
        assert otro_grupo["propuestas"] == sorted([m["pa"], pa2])
        assert compartido["gasto"] == "63.0000" and compartido["clics"] == 120
        assert compartido["propuestas"] == sorted([m["pa"], pa2])
        assert vecino not in compartido["propuestas"]
        assert vecino not in compartido["anuncios_elegibles"]

        mx = _resumen_e4(E4_MX)
        us = _resumen_e4(E4_US)
        assert mx["candidatos_pausa"] == 0, mx
        assert us["grupos_exclusivos"] == 0, us

        return {
            "version": p.POLITICA_PRODUCTO,
            "referencia": REFERENCIA,
            "sonda": "docs/evidencia/orbit-07/m3m4/sonda_aprendizaje.py",
            "mundo": {
                "producto": "SKU-A",
                "ventana": "[D-60, D-30)",
                "gasto_y_clics_por_fecha": "2 USD y 4 clics, cero compras y cero ventas",
                "limites_por_omision": base["limites"],
            },
            "agregado_auto03": {
                "base": base,
                "reobservacion_reemplaza": reobservado,
                "segundo_grupo_del_mismo_producto": otro_grupo,
                "grupo_compartido": compartido,
                "vecino_de_otro_producto": {
                    "ad_entity_id": vecino,
                    "en_anuncios_elegibles_del_producto": False,
                    "en_propuestas_del_producto": False,
                },
            },
            "produccion_auto03_e4": {
                "fuente": "docs/evidencia/auto-03/ejecucion/E/elegibilidad-{mx,us}.json",
                "consulta": "2026-11-07 03:01 UTC, solo lectura, regla H contada sobre datos de D",
                "amazon_mx": mx,
                "amazon_us": us,
                "interseccion_candidato_y_grupo_exclusivo": {
                    "amazon_mx": 0,
                    "amazon_us": 0,
                    "regla": "si el conteo de candidatos o el de grupos exclusivos es cero,"
                    " la interseccion es cero",
                },
            },
            "m4_cubierto_por_auto03": {
                "propuesta_por_anuncio_elegible": True,
                "una_sola_actividad_por_producto": True,
                "alcance_compartido_solo_anuncios_del_producto": True,
            },
            "m3_freno_de_subidas": {
                "efecto_propio_en_la_medicion": False,
                "donde_ya_impide": "I.5 omite la subida con una pausa live en vuelo del"
                " grupo exclusivo (tests/test_apply_producto.py)"
                "::test_grupo_exclusivo_con_pausa_en_vuelo_omite_la_subida_y_aplica_la_bajada",
                "donde_no_impide": "con la pausa en practica (shadow) AUTO-03 no bloquea"
                " subidas; anadir un freno ahi seria una regla nueva y el plan reserva al"
                " dueno el gasto de aprendizaje",
            },
            "diferencia_funcional_pendiente": False,
            "decision": "omitir M3/M4 con esta evidencia; AUTO-03 H/I cubre la pausa y"
            " el agregado no duplica gasto",
        }


if __name__ == "__main__":
    import sys

    salida = None
    if "--salida" in sys.argv:
        salida = Path(sys.argv[sys.argv.index("--salida") + 1])
    texto = json.dumps(main(), indent=2, ensure_ascii=False)
    if salida is not None:
        salida.write_text(texto + "\n", encoding="utf-8")
    print(texto)
