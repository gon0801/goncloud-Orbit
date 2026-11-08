# ORBIT 07 M3/M4: comparación con AUTO-03 y decisión de omisión

Fecha: 2026-11-08 (UTC). Base: `4e0e78d` (M2) sobre `f86ed7dc` (AUTO-03).
Plan que manda: [`plans/orbit-07-motor-decisiones.md`](../../../../plans/orbit-07-motor-decisiones.md).

## Decisión

M3 y M4 se omiten. M0 no acredita una diferencia funcional pendiente respecto
de AUTO-03:

1. **M4 ya está entregado por AUTO-03 H/I.** La pausa por producto sin ventas
   usa el gasto agregado del producto, ventana madura `[D-60, D-30)`, cero
   compras y cero ventas totales explícitas, y los límites que el dueño guarda
   desde la pantalla. Propone una pausa por anuncio elegible y conserva el
   único escritor, la cola, el veto, la revalidación y la reversa de AUTO-03.
2. **El agregado de M3 ya está entregado.** `productos_io` colapsa las
   observaciones por `(asin, sku, fecha)` a la última observación y suma por
   producto. Una reobservación reemplaza su fecha; añadir keywords, campañas,
   grupos o anuncios del mismo producto no suma otra vez el gasto ni reinicia
   el umbral.
3. **El freno de subidas de M3 no tiene efecto propio en el alcance medido.**
   Solo aplica en grupos exclusivos. La medición de producción de AUTO-03 E.4
   da cero candidatos a pausa en MX (con 6 grupos exclusivos) y cero grupos
   exclusivos en US (con 3 candidatos): la intersección es cero en ambos
   mercados. Donde la pausa sí está en vivo y en vuelo, AUTO-03 I.5 ya omite
   la subida (`pausa_producto_pendiente`). Con la pausa en práctica no hay
   freno, y añadirlo sería una regla nueva que el plan no autoriza sin la
   elección del dueño sobre el gasto de aprendizaje.

No se inventó un umbral monetario nuevo: el gasto de aprendizaje es el que el
dueño ya elige como límites de la pausa de AUTO-03
(`ads_producto_pausa_clics_<plat>` y `ads_producto_pausa_gasto_<plat>`).

## Qué cubre AUTO-03, por requisito

| Requisito de M3/M4 | Implementación recibida | Evidencia |
|---|---|---|
| Un solo gasto agregado por producto y mercado | `productos_io` lee el reporte de productos, colapsa por `(asin, sku, fecha)` con `DISTINCT ON` y suma por producto (`_SQL_METRICAS`, `_actividad`, `_suma_dia`) | `tests/test_evaluacion_catalogo.py::test_relecturas_de_la_misma_fecha_se_colapsan_no_se_suman`; `tests/test_ads_producto_ingesta.py::test_pipeline_productos_en_vivo`; sonda `m3m4/sonda_aprendizaje.py` |
| Agregar entidades nuevas no reinicia ni multiplica | El reporte de productos es por asin/sku; las propuestas son una por anuncio elegible bajo la misma actividad del producto | Sonda: segundo grupo del mismo producto mantiene `gasto 63`, `clics 120` y dos propuestas |
| Pausa con aprendizaje agotado, cero compras y cero ventas | `productos.py::motivos_economicos_pausa` y `_regla_pausa` con `cumple_limites_pausa` | `tests/test_productos_pausa.py::test_100_clics_y_500_mxn_sin_ventas_dan_una_propuesta_por_anuncio_elegible`; sonda |
| Ventana madura `[D-60, D-30)` y datos completos | `productos.py::ventana_pausa`; `productos_io._actividad` con `fecha_madura` y cobertura S6 | `tests/test_productos_io.py`; E.4 declara los huecos S6 |
| Límites del dueño, sin otro cupo de gasto | `productos.py::limites_pausa_desde_settings` sobre las claves de AUTO-03 | `tests/test_productos_meta.py`; H.9 de AUTO-03 |
| Alcance por anuncio en grupos compartidos | `productos_io` incluye solo los product ads del producto; el vecino nunca entra | Sonda: `grupo_compartido` propone `[pa1, pa2]` y excluye al vecino |
| Escritor, cola, veto, revalidación y reversa únicos | `cycle._pendientes_pausa_producto`, `apply_producto`, `apply_cola`, `retiro_anuncios` | `tests/test_apply_producto.py`, `tests/test_productos_pausa.py` |
| Subida omitida con pausa en vuelo | `cycle._SQL_GRUPOS_PAUSA_PRODUCTO` y el skip `pausa_producto_pendiente` | `tests/test_apply_producto.py::test_grupo_exclusivo_con_pausa_en_vuelo_omite_la_subida_y_aplica_la_bajada` |
| Excluir impulsos abiertos y anuncios regresados | `productos_io._Alcance` y `v_ad_group_impulso_abierto`; `productos.excluidos` | `tests/test_productos_pausa.py` (impulso y regresados) |

## Medición de producción reutilizada

La corrida de AUTO-03 E.4 (2026-11-07 03:01 UTC, solo lectura, código D) ya
midió el embudo por mercado. Se reutiliza tal cual; este encargo no tiene
`orbit_read` y no consultó producción.

| Dato | amazon_mx | amazon_us |
|---|---|---|
| Productos evaluados | 249 | 119 |
| Grupos exclusivos | 6 | 0 |
| Candidatos a pausa (límites por omisión) | 0 | 3 |
| Anuncios afectables | 0 | 29 |
| Ventana de pausa completa (S6) | 177 | 53 |

La intersección "candidato a pausa y grupo exclusivo", que es el único alcance
donde M3 podría frenar una subida, es cero en ambos mercados. Los 3 candidatos
US están fuera de grupos exclusivos (US no tiene ninguno); MX no tiene
candidatos. Los huecos de S6 (`fecha_desconocida` 47/31 y
`sin_filas_en_ventana` 25/35) son el límite de cobertura ya declarado por
AUTO-03, no una diferencia que M3/M4 agregue.

## Qué no se afirma

- No se afirma que la pausa esté encendida ni que existan ahorros: el
  encendido sigue reservado al dueño y esta entrega no despliega.
- No se afirma cobertura total del catálogo: E.4 declara los huecos S6.
- No se construyó otro escritor, otra cola, otra acción ni otro `kind`; no se
  tocó el modo de pausa ni sus límites.

## Reproducir

```bash
export ORBIT_TEST_DSN='postgresql://orbit:orbit@127.0.0.1:5433/postgres'
export PYTHONPATH='.:tests'
uv run --frozen python docs/evidencia/orbit-07/m3m4/sonda_aprendizaje.py \
    --salida docs/evidencia/orbit-07/m3m4/aprendizaje.json
```

La sonda crea su PostgreSQL desechable, siembra el producto que gastó sin
vender, comprueba el colapso y el alcance por anuncio, y lee los dos JSON de
E.4. Su salida está en [`aprendizaje.json`](aprendizaje.json).
