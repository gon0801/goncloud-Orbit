"""ORBIT 07 M3/M4: sonda del aprendizaje agregado y de la venta reciente.

Comprueba, sobre una base PostgreSQL desechable, que el agregado de M3 no
duplica gasto (la reobservacion reemplaza la fecha y anadir otro anuncio, grupo
o campana del mismo producto no reinicia ni multiplica el umbral). Sobre la
medicion de produccion de AUTO-03 E.4 muestra la diferencia de M4: la regla
nueva v3 deja en espera los productos que vendieron en [D-29, D-1] y conserva
la pausa solo en el que no tiene una venta inmadura.

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
from test_productos_pausa import (
    DECIDIDO,
    _actividad,
    _actividad_en,
    _db,
    _dia,
    _entrada_mx,
    _mundo_pausa,
)

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


def _entradas_e4(ruta: Path) -> tuple[dict, list[p.EntradaProducto]]:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    entradas = [p._de_json(e, p.EntradaProducto) for e in datos["entradas"]]
    return datos["resumen"], entradas


def _cumple_v2(entrada: p.EntradaProducto) -> bool:
    actividad = entrada.actividad_pausa
    limites = entrada.limites_pausa
    return (
        isinstance(actividad, p.AdsCompletos)
        and actividad.compras_total == 0
        and actividad.ventas_total.valor == 0
        and actividad.clics >= limites.clics_min
        and actividad.gasto.valor >= limites.gasto_min.valor
    )


def _juicio(entrada: p.EntradaProducto, politica: str) -> dict:
    evaluacion = p.resolver_producto(entrada, politica=politica)
    venta = p.venta_reciente_producto(entrada)
    return {
        "politica": politica,
        "pausas": sorted(x.anuncio.ad_entity_id for x in evaluacion.pausas),
        "motivos_pausa": list(evaluacion.motivos_pausa),
        "venta_reciente": None
        if venta is None
        else {
            "ancla": venta.ancla.isoformat(),
            "vence_el": venta.vence_el.isoformat(),
            "libera_el": venta.libera_el.isoformat(),
            "compras": str(venta.compras),
            "ventas": str(venta.ventas.valor),
        },
    }


def _m4_puro() -> dict:
    casos = {}
    for dias in (15, 5):
        fecha = DECIDIDO.date() - dt.timedelta(days=dias)
        entrada = _entrada_mx(
            _actividad(),
            dias_halo=(_dia(fecha, compras="2", ventas="900", mismo="900"),),
        )
        casos[f"d-{dias}"] = {
            "fecha_venta": fecha.isoformat(),
            "v2": _juicio(entrada, "v2"),
            "v3": _juicio(entrada, "v3"),
        }
    return casos


def _hueco_dia_30() -> dict:
    """El dia de madurez (venta+30) sigue en la reciente; al dia siguiente la
    venta entra a la madura. No hay hueco entre [D-30, D-1] y [D-60, D-30)."""
    venta = dt.date(2026, 10, 9)
    salida = {}
    for offset in (29, 30, 31):
        D = dt.datetime.combine(venta + dt.timedelta(days=offset), dt.time(12), tzinfo=dt.UTC)
        vp = p.ventana_pausa(D)
        en_ventana = vp.contiene(venta)
        entrada = _entrada_mx(
            _actividad_en(
                D,
                compras="1" if en_ventana else "0",
                ventas="900" if en_ventana else "0",
                mismo="900" if en_ventana else "0",
            ),
            decidido_el=D,
            dias_halo=(_dia(venta, compras="1", ventas="900", mismo="900"),),
        )
        ev = p.resolver_producto(entrada)
        reciente = p.ventana_venta_reciente(D)
        salida[f"D=venta+{offset}"] = {
            "dia": D.date().isoformat(),
            "ventana_pausa": [vp.desde.isoformat(), vp.hasta_exclusivo.isoformat()],
            "ventana_reciente": [reciente.desde.isoformat(), reciente.hasta_exclusivo.isoformat()],
            "pausas": sorted(x.anuncio.ad_entity_id for x in ev.pausas),
            "motivos_pausa": list(ev.motivos_pausa),
        }
    return salida


def _m4_produccion() -> dict:
    _, entradas = _entradas_e4(E4_US)
    candidatos = []
    for entrada in entradas:
        pid = entrada.producto.product_id
        if pid not in (273, 315, 356):
            continue
        ventas = [
            {
                "fecha": dia.fecha.isoformat(),
                "compras": str(dia.compras),
                "ventas": str(dia.ventas.valor),
                "ventas_mismo_sku": str(dia.ventas_mismo_sku.valor),
            }
            for dia in sorted(entrada.dias_halo, key=lambda d: d.fecha)
            if dia.compras != 0 or dia.ventas.valor != 0
        ]
        candidatos.append(
            {
                "product_id": pid,
                "anuncios": len(entrada.anuncios),
                "ventas": ventas,
                "v2": _juicio(entrada, "v2"),
                "v3": _juicio(entrada, "v3"),
            }
        )
    return {
        "candidatos_v2": sum(1 for e in entradas if _cumple_v2(e)),
        "anuncios_afectables_v2": sum(len(e.anuncios) for e in entradas if _cumple_v2(e)),
        "candidatos_v3": sum(1 for e in entradas if p.cumple_limites_pausa(e)),
        "anuncios_afectables_v3": sum(
            len(e.anuncios) for e in entradas if p.cumple_limites_pausa(e)
        ),
        "productos": candidatos,
    }


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
        assert us["grupos_exclusivos"] == 0, us
        m4 = _m4_produccion()
        assert m4["candidatos_v2"] == 3 and m4["candidatos_v3"] == 1, m4
        por_id = {x["product_id"]: x for x in m4["productos"]}
        assert por_id[273]["v2"]["pausas"] and not por_id[273]["v3"]["pausas"]
        assert por_id[315]["v2"]["pausas"] and not por_id[315]["v3"]["pausas"]
        assert por_id[356]["v2"]["pausas"] and por_id[356]["v3"]["pausas"]
        puro = _m4_puro()
        assert puro["d-15"]["v2"]["pausas"] and not puro["d-15"]["v3"]["pausas"]
        assert puro["d-5"]["v2"]["pausas"] and not puro["d-5"]["v3"]["pausas"]
        hueco = _hueco_dia_30()
        assert hueco["D=venta+29"]["motivos_pausa"] == ["venta_reciente_producto"]
        assert hueco["D=venta+30"]["motivos_pausa"] == ["venta_reciente_producto"]
        assert hueco["D=venta+31"]["motivos_pausa"] == ["vendio_en_ventana"]

        return {
            "version": p.POLITICA_PRODUCTO,
            "referencia": REFERENCIA,
            "sonda": "docs/evidencia/orbit-07/m3m4/sonda_aprendizaje.py",
            "m3_agregado": {
                "mundo": {
                    "producto": "SKU-A",
                    "ventana": "[D-60, D-30)",
                    "gasto_y_clics_por_fecha": "2 USD y 4 clics, cero compras y cero ventas",
                    "limites_por_omision": base["limites"],
                },
                "base": base,
                "reobservacion_reemplaza": reobservado,
                "segundo_grupo_del_mismo_producto": otro_grupo,
                "grupo_compartido": compartido,
                "vecino_de_otro_producto": {
                    "ad_entity_id": vecino,
                    "en_anuncios_elegibles_del_producto": False,
                    "en_propuestas_del_producto": False,
                },
                "interseccion_candidato_y_grupo_exclusivo": {
                    "amazon_mx": 0,
                    "amazon_us": 0,
                    "regla": "si el conteo de candidatos o el de grupos exclusivos es cero,"
                    " la interseccion es cero",
                },
            },
            "m4_venta_reciente": {
                "ventana": "[D-30, D-1]; la fuente de productos reobserva 30 dias",
                "madurez_dias": 30,
                "puro": puro,
                "hueco_dia_30": hueco,
                "produccion_e4": m4,
                "revalidacion": "tests/test_apply_producto.py"
                "::test_venta_reciente_inmadura_descarta_la_pausa_al_revalidar",
            },
            "produccion_auto03_e4": {
                "fuente": "docs/evidencia/auto-03/ejecucion/E/elegibilidad-{mx,us}.json",
                "consulta": "2026-11-07 03:01 UTC, solo lectura, datos de la regla v2",
                "amazon_mx": mx,
                "amazon_us": us,
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
            "decision": "M3 omitido: el agregado y el freno de subidas no acreditan una"
            " diferencia funcional propia; M4 implementado: la pausa espera una venta"
            " inmadura del producto con ancla fija, ventana [D-30, D-1] contigua y"
            " madurez de 30 dias; libera al entrar a [D-60, D-30)",
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
