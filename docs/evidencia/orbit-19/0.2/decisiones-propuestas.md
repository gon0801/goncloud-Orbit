# ORBIT 19 / 0.2 — D1–D4

Estado: **cerradas** 2026-09-06 21:31 UTC. Acta: `confirmacion.md`.
El codigo F1 de produccion no cambia hasta la fase A.

Conteos: SELECT 2026-09-06 21:24:52 UTC, `orbit_read`, ventana
`[2026-02-20, 2026-08-22)`. Detalle: `select-margen-signo.out.txt`.
Selector exclusivo (0.1): `docs/evidencia/orbit-19/0.1/reporte.md`.

Correccion: el "175 sin margen maduro" de la propuesta anterior era solo
MX (84+91, productos de un listing). No se suma MX+US. Abajo todo va
**por mercado**.

## Conteos por mercado (no mezclar)

Dos lecturas. No son el mismo numero.

**A. Bloqueo del selector actual** (mutuamente excluyente, orden de
`fabrica_web.py`: multilisting → sin margen → sin SKU):

| Mercado | Productos | Elegibles hoy | Multilisting | Sin ventas (un listing) | &lt;30 fechas (un listing) |
|---|---:|---:|---:|---:|---:|
| amazon_mx | 249 | 3 | 71 | 84 | 91 |
| amazon_us | 119 | 2 | 44 | 48 | 25 |

MX 3+71+84+91=249. US 2+44+48+25=119.

**B. Margen maduro de `v_margen_producto.margen_neto_pct`** (todos los
productos con listing; un producto puede ser multilisting **y** sin margen):

| Mercado | Positivo | Cero | Negativo | Null | de esos null: sin ventas | de esos null: &lt;30 fechas |
|---|---:|---:|---:|---:|---:|---:|
| amazon_mx | 5 | 0 | 0 | 244 | 108 | 136 |
| amazon_us | 2 | 0 | 0 | 117 | 66 | 51 |

MX 5+244=249. US 2+117=119. De los 5 positivos MX, 3 tienen un listing
(elegibles) y 2 son multilisting. US: los 2 positivos son de un listing.
Hoy no hay margen 0 ni negativo; el contrato los cubre igual.

No se usa un total combinado MX+US para decidir.

## D1 — Prioridad de comparacion

**Cerrada:**

1. **Metrica de orden inicial:** `margen_neto_pct` de `v_margen_producto`
   (porcentaje neto, grano producto+plataforma). No es pesos de
   contribucion, no es ACoS, no es ventas en dinero.
2. **Ventana del porcentaje:** `[2026-02-20, D-15)` UTC, la de la vista.
   Hoy: `[2026-02-20, 2026-08-22)`. Visible junto al numero.
3. **Muestra:** `dias_con_venta` siempre visible. El orden D1 usa solo
   el porcentaje **maduro** (`dias_con_venta >= 30` y el resto de guardas
   de la vista). La muestra 1–29 es D4 y **no** entra al sort.
4. **Datos ausentes:** `margen_neto_pct` NULL al **final**, en las dos
   direcciones. NULL no es 0%, no es peor que un negativo, no es "malo".
   Sin fila de la vista = ausente, no cero. MX y US no se mezclan (moneda
   distinta). Desempate `listing_id`.
5. **Por probar** exige evidencia de cobertura. Solo significa «sin
   actividad Ads en la ventana consultada» cuando el reporte de esa
   ventana esta **verificado** (request COMPLETED y cobertura del
   contrato 0.4). Si falta el reporte o la cobertura no esta
   verificada: **Sin datos**, no Por probar. Tampoco demuestra que
   nunca se haya anunciado. Va en seccion propia, seleccionable.

Motivo: MX 244/249 y US 117/119 no tienen porcentaje maduro. Si el
ausente se ordena como 0%, todos caen al fondo y se leen como ruina.
Los 5+2 que si tienen numero (MX 33–43%, US 37–39%) pueden ir primero
sin inventar ranking para el resto.

Cambio de orden inicial = nueva decision; no se toca el motor.

## D2 — Lanzamientos sin margen, con margen cero o negativo

Propuesta del plan: ACoS manual del grupo, presupuestos/bids explicitos.

**Cerrada:** `objetivo.origen=manual_lanzamiento` es
**obligatorio** si **algun** producto del grupo esta en cualquiera de
estos casos:

| Caso | Que es | Por que no se deriva target |
|---|---|---|
| Ausente | `margen_neto_pct` NULL (sin ventas, &lt;30 fechas u otra guarda) | No hay medicion. Omitir nulls para tomar el minimo de los que si tienen numero inventa un grupo mas sano. |
| Cero | `margen_neto_pct = 0` | `fraccion × 0 = 0` y el clamp a 10% (`MARGEN_BANDA_MIN`) presentaria el piso como rentabilidad. |
| Negativo | `margen_neto_pct < 0` | El minimo seria negativo; el mismo clamp a 10% mentiria. Seleccionable (AC3), no se bloquea. |

Hoy: 0 filas con cero o negativo. MX 244 y US 117 son ausente. El
contrato vale igual cuando aparezca un 0 o un negativo.

Tambien manual si el margen es positivo pero `fraccion × margen` al
clampear **supera** el margen conocido (piso 10% sobre un margen p.ej.
de 8%).

El objetivo manual **no acredita rentabilidad**. Es intencion de ACoS,
no medicion de margen. En preview y revision, si hay margen cero,
negativo o **inferior al objetivo**, esa situacion se muestra
explicita (numero + etiqueta). El producto sigue seleccionable. No
se presenta el grupo como rentable por el hecho de tener target
manual. Sin default 25%. Presupuestos y bids siguen explicitos.
`v_margen_producto` y el motor no se relajan.

Margen proyectado previo queda fuera (haria falta replanificar fuentes).

## D3 — Nuevas o existentes

**Cerrada:** solo campanas nuevas en este plan. Existentes = otro plan
con reversa. `fabrica_lote` esta vacia.

## D4 — Detalle economico con poca muestra

**Cerrada.** Conteos por mercado:

- Muestra limitada: integridad OK y `dias_con_venta` 1–29. Se muestra el
  % con etiqueta y el conteo de fechas. **No** gobierna target ni el
  sort D1.
- Maduro: la vista actual (>=30).
- No calculable: sin ventas u otras guardas.

Hoy, por mercado (lectura B, no el cubo exclusivo del selector):
MX 136 con &lt;30 fechas; US 51. El cubo exclusivo (un listing, despues
de sacar multilisting) sigue siendo MX 91 / US 25.

## Precisiones de revision (contrato, cerradas)

1. Por probar requiere evidencia de cobertura. Reporte faltante =
   Sin datos. No implica «nunca anunciado».
2. Objetivo manual no acredita rentabilidad. Margen cero, negativo o
   inferior al objetivo se declara en la revision; el producto sigue
   seleccionable.
3. Confirmar D1–D4 no bastaba: el contrato tecnico (API/CLI v2,
   compatibilidad, migraciones, reversa con recuperacion v2) se cierra
   en `confirmacion.md` junto con el negocio.

## Cierre 0.2

(1) y (2) cumplidos el 2026-09-06 21:31 UTC. Detalle en `confirmacion.md`.
Cero implementacion de A/B en esta tarea.
