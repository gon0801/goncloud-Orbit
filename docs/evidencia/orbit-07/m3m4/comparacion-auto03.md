# ORBIT 07 M3/M4: comparación con AUTO-03 y decisión

Fecha: 2026-11-08 (UTC). Base: `4e0e78d` (M2) sobre `f86ed7dc` (AUTO-03).
Plan que manda: [`plans/orbit-07-motor-decisiones.md`](../../../../plans/orbit-07-motor-decisiones.md).

## Decisión

M3 se omite: su agregado ya está en AUTO-03 y su freno de subidas no acredita
un efecto propio en la medición. M4 **sí se implementa**: AUTO-03 H/I pausa
productos que vendieron después de `[D-60, D-30)`, porque su ventana madura no
ve la venta inmadura. Falta el contrato temporal de producto que piden el plan
y M0, y esa es una diferencia funcional pendiente.

La política de producto sube a `v3` con la espera de venta reciente inmadura;
`v2` conserva el replay histórico sin la espera y `v1` la meta sin pausa.

### M3, por qué se omite

1. **El agregado ya está entregado.** `productos_io` colapsa las observaciones
   por `(asin, sku, fecha)` a la última y suma por producto. Una reobservación
   reemplaza su fecha; añadir keywords, campañas, grupos o anuncios del mismo
   producto no vuelve a sumar el gasto ni reinicia el umbral.
2. **El freno de subidas no tiene efecto propio en el alcance medido.** Su
   disparador es una pausa **live en vuelo** de un grupo exclusivo que además
   iba a subir. La medición de producción de AUTO-03 E.4 mide otra cosa:
   candidatos a pausa y grupos exclusivos por separado. Da cero candidatos a
   pausa en MX (con 6 grupos exclusivos) y cero grupos exclusivos en US (con 3
   candidatos), así que la intersección es cero en ambos mercados y además la
   pausa está en práctica, no live: no hubo pausa en vuelo que frenar. No se
   midió el efecto del freno porque ese alcance no tiene caso, no porque una
   medición del freno haya dado cero. Donde la pausa sí está live y en vuelo,
   I.5 ya omite la subida (`pausa_producto_pendiente`). No se inventó un
   umbral de gasto nuevo: el gasto de aprendizaje es el que el dueño ya guarda
   como límites de la pausa.

### M4, contrato temporal de producto

La fuente de productos reobserva cada fecha hasta 30 días (AUTO-03 E.4 midió
una reatribución en el día 31). Una venta con menos de 30 días todavía puede
cambiar: la pausa del producto espera, al decidir y al revalidar, sin renovar
el ancla y sin reutilizar los 10 días de M1. La espera lee las observaciones
del reporte de productos hasta D-1, no el watermark de hojas.

| Elemento | Regla |
|---|---|
| Ventana reciente inmadura | `[D-30, D-1]`: una compra o venta total observada ahí deja la pausa en espera |
| Ancla | la fecha más antigua con venta en esa ventana |
| Madurez | 30 días: `vence_el = ancla + 30` |
| Reobservación | corregir es una fila nueva; una reobservación o una venta posterior no mueven el ancla |
| Liberación | `libera_el = vence_el + 1`: la venta entra a `[D-60, D-30)` (`vendio_en_ventana`) o se corrige a cero |
| Contigüidad | el día de madurez sigue en la reciente; no hay hueco entre `[D-30, D-1]` y `[D-60, D-30)` |
| Revalidación | la misma subpolítica pura; la fila en cola se descarta con `venta_reciente_producto`, sin PUT ni cuota |
| Escritor y cola | sin cambios: único `kind='pause'` sobre `product_ad`, `apply_producto`, `apply_cola` y `retiro_anuncios` |

Una venta de otro SKU también espera: no se puede afirmar cero ventas. No se
añadió una elección monetaria nueva; los límites de la pausa siguen siendo los
del dueño.

## Qué cubre AUTO-03, por requisito

| Requisito | Implementación | Evidencia |
|---|---|---|
| Un solo gasto agregado por producto y mercado | `productos_io` colapsa por `(asin, sku, fecha)` con `DISTINCT ON` y suma por producto (`_SQL_METRICAS`, `_actividad`, `_suma_dia`) | `tests/test_evaluacion_catalogo.py::test_relecturas_de_la_misma_fecha_se_colapsan_no_se_suman`; `tests/test_ads_producto_ingesta.py::test_pipeline_productos_en_vivo`; sonda `m3m4/sonda_aprendizaje.py` |
| Agregar entidades nuevas no reinicia ni multiplica | El reporte es por asin/sku; las propuestas son una por anuncio elegible bajo la misma actividad | Sonda: segundo grupo del mismo producto conserva `gasto 63`, `clics 120` y dos propuestas |
| Pausa con límites, cero compras y cero ventas | `productos.py::motivos_economicos_pausa` y `_regla_pausa` con `cumple_limites_pausa` | `tests/test_productos_pausa.py`; sonda |
| Venta reciente inmadura del producto (M4) | `productos.py`: `ventana_venta_reciente`, `venta_reciente_producto` y `MOTIVO_VENTA_RECIENTE`; `ancla`, `vence_el` y `libera_el`; `productos_io` lee el reporte hasta D-1 | `tests/test_productos_pausa.py -k "venta_reciente or ancla or madura"`; sonda `m4_venta_reciente` |
| La revalidación descarta sin escritura (M4) | `apply_producto._revalida` usa la subpolítica pura y el motivo nuevo | `tests/test_apply_producto.py::test_venta_reciente_inmadura_descarta_la_pausa_al_revalidar` |
| Alcance por anuncio en grupos compartidos | `productos_io` incluye solo los product ads del producto; el vecino nunca entra | Sonda: `grupo_compartido` propone los dos anuncios propios y excluye al vecino |
| Escritor, cola, veto, revalidación y reversa únicos | `cycle._pendientes_pausa_producto`, `apply_producto`, `apply_cola`, `retiro_anuncios` | `tests/test_apply_producto.py`, `tests/test_productos_pausa.py` |
| Subida omitida con pausa en vuelo | `cycle._SQL_GRUPOS_PAUSA_PRODUCTO` y `pausa_producto_pendiente` | `tests/test_apply_producto.py::test_grupo_exclusivo_con_pausa_en_vuelo_omite_la_subida_y_aplica_la_bajada` |

## Medición de producción (AUTO-03 E.4)

La corrida de AUTO-03 E.4 (2026-11-07 03:01 UTC, solo lectura, datos de la
regla v2) se reutiliza tal cual. Este encargo no tiene `orbit_read` y no
consultó producción.

| Dato | amazon_mx | amazon_us |
|---|---|---|
| Productos evaluados | 249 | 119 |
| Grupos exclusivos | 6 | 0 |
| Candidatos a pausa con v2 | 0 | 3 |
| Anuncios afectables con v2 | 0 | 29 |
| Candidatos a pausa con v3 | 0 | 1 |
| Anuncios afectables con v3 | 0 | 12 |

Con v3, los productos 273 y 315 quedan en espera: el 273 vendió el
2026-10-09 (ancla 10-09, vence 11-08, libera 11-09) y el 315 vendió el 10-18 y
el 10-26 (ancla 10-18, vence 11-17, libera 11-18; la venta del 10-26 mantiene
la espera hasta 11-25). El 356 no tiene ventas observadas en `[D-30, D-1]`
hasta el 11-02 (vendió 08-28 y 08-30) y conserva la pausa de sus 12 anuncios.
La fuente congelada de E.4 declara `ventana_halo.hasta_exclusivo = 2026-11-03`,
así que la ventana reciente `[2026-10-08, 2026-11-06]` solo se observó hasta
el 11-02: los días 11-03..11-06 **no se leyeron** y el "no tiene ventas" del
356 está probado hasta 11-02, no en toda la ventana. La ventana de pausa
`[D-60, D-30)` de los tres está en cero.

El día de madurez (D = venta + 30) la venta sigue en la ventana reciente, así
que la pausa espera; al día siguiente entra a `[D-60, D-30)` y la pausa no se
propone (`vendio_en_ventana`). No hay hueco entre las dos ventanas.

La intersección "candidato a pausa y grupo exclusivo" de M3 sigue siendo cero
en ambos mercados (MX no tiene candidatos; US no tiene grupos exclusivos).

## Regresión

La primera regresión de M4 (base `4953bc1`) falla y pasa en el candidato:

- `tests/test_productos_pausa.py::test_venta_reciente_inmadura_deja_la_pausa_en_espera`
  falla con `assert (PropuestaPausa...) == ()` (la base propone la pausa).
- `tests/test_productos_pausa.py::test_base_venta_reciente_inmadura_deja_la_pausa_en_espera`
  falla en la base con la pausa propuesta.
- `tests/test_apply_producto.py::test_venta_reciente_inmadura_descarta_la_pausa_al_revalidar`
  falla en la base porque la fila se aplica (`descartadas == []`).

Salida: [`regresion-venta-reciente.txt`](regresion-venta-reciente.txt) (rojo) y
[`regresion-venta-reciente-verde.txt`](regresion-venta-reciente-verde.txt)
(verde).

La corrección de frescura y del día 30 (base `57a05d8`) falla y pasa en el
candidato:

- `test_base_venta_reciente_inmadura_deja_la_pausa_en_espera` con días 1, 2 y 3
  falla en la base: el lector no veía la venta (el `dias_halo` terminaba en
  `watermark - 3`).
- `test_venta_reciente_inmadura_descarta_la_pausa_al_revalidar` con días 1, 2 y
  3 falla en la base porque la fila se aplica; con días 4 y 15 ya pasaba.
- `test_la_venta_reciente_madura_y_la_pausa_vuelve_a_decidir` falla en la base
  en `D = venta + 30` (la ventana reciente comenzaba en D-29 y dejaba el hueco).

Salida: [`regresion-frescura-venta.txt`](regresion-frescura-venta.txt) (rojo) y
[`regresion-frescura-venta-verde.txt`](regresion-frescura-venta-verde.txt)
(verde).

## Rendimiento

[`bench_venta_reciente.py`](bench_venta_reciente.py) corre v2 y v3 sobre las
mismas 368 entradas congeladas de E.4 (24 180 días de halo), 25 rondas por
versión. La mediana de la resolución completa es 16.786 ms en v2 y 17.226 ms
en v3: +0.44 ms, dentro del ruido de las rondas. La regla nueva no agrega SQL:
usa los días recientes ya leídos (o `dias_halo` en una entrada congelada), por
producto y no por hoja, dentro del presupuesto de 150 ms de M0. Salida en
[`rendimiento-rule.json`](rendimiento-rule.json).

## Qué no se afirma

- No se afirma que la pausa esté encendida ni que existan ahorros: el
  encendido sigue reservado al dueño y esta entrega no despliega.
- No se afirma cobertura total del catálogo: E.4 declara los huecos S6.
- No se construyó otro escritor, otra cola, otra acción ni otro `kind`; no se
  tocó el modo de pausa ni sus límites.
- M3 no tiene entrega propia; su evidencia queda como insumo de M4.

## Reproducir

```bash
export ORBIT_TEST_DSN='postgresql://orbit:orbit@127.0.0.1:5433/postgres'
export PYTHONPATH='.:tests'
uv run --frozen python docs/evidencia/orbit-07/m3m4/sonda_aprendizaje.py \
    --salida docs/evidencia/orbit-07/m3m4/aprendizaje.json
```

La sonda crea su PostgreSQL desechable, comprueba el colapso y el alcance por
anuncio, y lee los dos JSON de E.4 para mostrar v2 contra v3. Su salida está en
[`aprendizaje.json`](aprendizaje.json).
