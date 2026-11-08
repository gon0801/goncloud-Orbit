"""ORBIT 07 M2: lectura fresca de la economia del producto al revalidar la cola.

Adaptador de `app/optimizer/productos.py` y `app/optimizer/goals.py`: aqui
vive el SQL de la revalidacion (producto exclusivo del ad group, evaluacion
del ciclo ejecutor y respaldo familiar fresco); el resolutor puro decide. No
crea otra tabla, otro resolutor ni otro peldano: comparte la evaluacion
append-only y la medicion de familia de AUTO-03.

Separado de `app/productos_io.py` para no mezclar la lectura por lote del
ciclo con las consultas puntuales de la revalidacion y mantener el
presupuesto de tamano por modulo.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import psycopg

from app import productos_io
from app.optimizer import goals as g
from app.optimizer import productos as p

_SQL_EVALUACION_CICLO = """
SELECT id, entrada, resultado
  FROM evaluacion_producto_ciclo
 WHERE cycle_id = %s AND platform = %s::platform AND product_id = %s
"""

_SQL_PAREJAS_FAMILIA_CAMPANA = """
SELECT pf.familia_id
  FROM ad_entity camp
  JOIN ad_entity ag ON ag.parent_id = camp.id AND ag.kind = 'ad_group'
  JOIN ad_entity pa ON pa.parent_id = ag.id AND pa.kind = 'product_ad'
  JOIN ad_entity_state es ON es.ad_entity_id = pa.id AND es.status IN ('ENABLED', 'PAUSED')
  LEFT JOIN listing li ON li.id = pa.listing_id AND li.platform = camp.platform
  LEFT JOIN producto_familia pf
    ON pf.product_id = li.product_id AND pf.platform = camp.platform
 WHERE camp.id = %s AND camp.platform = %s::platform AND camp.kind = 'campaign'
"""

_SQL_MARGEN_FAMILIA = """
SELECT familia_id, padre_id, margen_neto_pct, cobertura, dias_con_venta,
       venta_cubierta, ledger_fresco_at, moneda, ventana_desde, ventana_hasta
  FROM v_margen_familia
 WHERE platform = %s::platform AND familia_id = ANY(%s)
"""


def producto_exclusivo(conn: psycopg.Connection, ad_group_id: int) -> int | None:
    """El producto dueno de un ad group exclusivo, o None. Misma regla que la
    lectura por lote (S5): TODOS sus product ads no archivados, incluidos los
    pausados y los de estado desconocido, tienen listing del mismo producto.
    Una identidad desconocida deja el grupo sin dueno (nunca se adivina)."""
    productos = productos_io.productos_del_grupo(conn, ad_group_id)
    if len(productos) != 1:
        return None
    (producto,) = productos
    return producto


def lee_evaluacion_ciclo(
    conn: psycopg.Connection, cycle_id: int, platform: str, product_id: int
) -> tuple[int, p.EvaluacionProducto] | None:
    """La evaluacion persistida del producto en el ciclo EJECUTOR (M2). El
    revalidador de la cola usa sus insumos frescos y su meta; los inputs
    originales de la decision siguen congelados para el replay."""
    fila = conn.execute(_SQL_EVALUACION_CICLO, (cycle_id, platform, product_id)).fetchone()
    if fila is None:
        return None
    return (fila[0], productos_io.evaluacion_de_fila(fila[1], fila[2]))


def familia_fresca(
    conn: psycopg.Connection,
    platform: str,
    campaign_id: int,
    *,
    ahora: dt.datetime,
    fraccion: Decimal | None,
    destino: Decimal | None,
) -> tuple[Decimal | None, dict]:
    """El peldano margen_familia de la campana con datos frescos (M2).

    Respaldo completo de la economia del producto: cuando la hoja venia del
    peldano individual y este dejo de ser elegible, la familia arranca desde
    `destino` (plataforma o setting), no desde el target individual, y su
    propia medicion decide. Devuelve (aplicado | None, detalle para la
    evidencia). Sin familia efectiva o con dato invalido, `aplicado` None y
    la cascada conserva el respaldo de plataforma/setting."""
    pares = [
        (campaign_id, fila[0])
        for fila in conn.execute(_SQL_PAREJAS_FAMILIA_CAMPANA, (campaign_id, platform))
    ]
    familia = g.familia_efectiva_por_campana(pares).get(campaign_id)
    detalle = {"familia": familia}
    if familia is None or fraccion is None:
        detalle["motivo"] = "sin_familia" if familia is None else "sin_fraccion"
        return (None, detalle)
    filas = {fila[0]: fila for fila in conn.execute(_SQL_MARGEN_FAMILIA, (platform, [familia]))}
    fila = filas.get(familia)
    if fila is None:
        detalle["motivo"] = "sin_margen"
        return (None, detalle)
    padre_id = fila[1]
    if padre_id is not None and padre_id not in filas:
        filas.update({f[0]: f for f in conn.execute(_SQL_MARGEN_FAMILIA, (platform, [padre_id]))})
    medicion = g.MedicionMargen(
        margen_neto_pct=fila[2],
        cobertura=fila[3],
        dias_con_venta=fila[4],
        venta_cubierta=fila[5],
        ledger_fresco_at=fila[6],
        moneda=str(fila[7]) if fila[7] is not None else None,
        ventana_desde=fila[8],
        ventana_hasta=fila[9],
    )
    padre = (
        g.MedicionMargen(
            margen_neto_pct=filas[padre_id][2],
            cobertura=filas[padre_id][3],
            dias_con_venta=filas[padre_id][4],
            venta_cubierta=filas[padre_id][5],
            ledger_fresco_at=filas[padre_id][6],
            moneda=str(filas[padre_id][7]) if filas[padre_id][7] is not None else None,
            ventana_desde=filas[padre_id][8],
            ventana_hasta=filas[padre_id][9],
        )
        if padre_id is not None and padre_id in filas
        else None
    )
    resolucion, ganadora = g.resuelve_target_margen_familia(
        medicion,
        padre,
        fraccion,
        ahora.astimezone(dt.UTC).date(),
        destino,
        destino,
        tiene_previo=False,
    )
    detalle.update(
        {
            "motivo": resolucion.motivo,
            "derivado": str(resolucion.derivado) if resolucion.derivado is not None else None,
            "aplicado": str(resolucion.aplicado) if resolucion.aplicado is not None else None,
            "usada": ganadora,
            "margen_neto_pct": str(medicion.margen_neto_pct)
            if medicion.margen_neto_pct is not None
            else None,
            "moneda": medicion.moneda,
        }
    )
    return (resolucion.aplicado, detalle)
