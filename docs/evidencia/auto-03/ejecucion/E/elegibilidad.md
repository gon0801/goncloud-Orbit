# Medición real de AUTO-03 por mercado (E.4)

Corrida el 2026-11-07 a las 03:01 UTC en producción, en solo lectura y sin esperar
ciclo. El comando es el de E.4, con el código desplegado de D (`cc3a487`). Para
cada mercado corre `docker exec orbit-app-1 python -m tools.auto03_verifica evalua --platform P --json /tmp/e.json`.
Usa el rol de lectura (`ORBIT_DSN_READ`) en una transacción READ ONLY. La salida
completa está en `elegibilidad-mx.json` y `elegibilidad-us.json`, con
`{resumen, entradas, evaluaciones}`. Las cifras de abajo salen de esos archivos con
`mide.py`, que cuenta sobre las evaluaciones congeladas sin recalcular nada:

```bash
E=docs/evidencia/auto-03/ejecucion/E
PYTHONPATH=. uv run --frozen --python 3.12 python $E/mide.py $E/elegibilidad-mx.json $E/elegibilidad-us.json
```

`replay --json` sobre los dos archivos da `diferencias: 0` en MX y en US, en local
y en el contenedor (`concilia.txt`).

## Embudo

| Etapa | amazon_mx | amazon_us |
|---|---|---|
| Productos evaluados (con product ad no archivado y listing) | 249 | 119 |
| Con margen válido (cobertura, 30 días con venta, ledger fresco, > 0) | 7 | 3 |
| Con ad group exclusivo | 2 | 0 |
| Con margen y ad group exclusivo | 1 | 0 |
| Ventana de la pausa completa según S6 | 177 | 53 |
| Ventana completa según la regla literal de la spec (informativo) | 224 | 84 |
| Sin halo (`halo_sin_margen` ausente) | 160 | 94 |
| Con margen y sin halo | 1 | 0 |
| Con meta | 1 | 0 |
| Aptos para meta (meta y ad group exclusivo) | 0 | 0 |
| Grupos exclusivos | 6 | 0 |
| Keywords y targets ENABLED bajo grupo exclusivo | 104 | 0 |
| De ellos pisados por un goal con target (S1) | 0 | 0 |
| De ellos efectivos (recibirían la meta) | 0 | 0 |
| Candidatos a pausa (límites por omisión: 100 clics, 500 MXN / 40 USD) | 0 | 3 |
| Anuncios afectables de los candidatos (S4, sin excluir impulso ni claves en vuelo) | 0 | 29 |
| Anuncios elegibles en todo el mercado (S4) | 1157 | 386 |

Abstenciones de meta por motivo. MX tiene `ventana_corta` 151, `sin_margen` 91 y
`halo_sin_margen` 89. US tiene `ventana_corta` 55, `sin_margen` 61 y
`halo_sin_margen` 25. Un producto puede tener más de un motivo.

Motivos de ventana incompleta para la pausa. MX tiene `fecha_desconocida` 47 y
`sin_filas_en_ventana` 25. US tiene `fecha_desconocida` 31 y
`sin_filas_en_ventana` 35.

La regla literal es la de la spec antes de S6: una fecha sin fila cuenta como cero
aunque ninguna ingesta demuestre que la pidió. Sigue exigiendo una fila en la
ventana y madurez por fecha. Se mide solo para comparar. S6 no aplica a la meta, y
la pausa usa S6.

## Lectura

1. **La meta por producto no alcanza hoy ninguna keyword.** Hay 6 grupos exclusivos
   en MX con 104 keywords y targets. Son de dos productos y ninguno tiene meta. El
   1621 tiene margen de 44.54 % pero halo, con ventas de otro SKU en el tramo del
   halo. El 294 no tiene margen medido. El único producto con meta es el 333. Su
   margen de 41.26 % por la fracción 0.5 da una meta de 20.63 %, pero no anuncia en
   ningún grupo exclusivo (`sin_grupo_apto`), así que la meta no llega a ninguna puja.
   US no tiene grupos exclusivos. Encender la meta en vivo (F) promete hoy cero
   keywords en los dos mercados. Cero es un resultado válido. La palanca es armar
   grupos de un solo producto, no código.
2. **S1 sigue sin dejar nada fuera.** Ningún goal resuelto de esos grupos trae target
   (0 pisadas), como midió A.3.
3. **La pausa con los límites por omisión alcanza 3 productos US y 29 anuncios, y
   nada en MX.** Coincide con A.3. Los 3 candidatos US (273, 315 y 356) tienen la
   ventana completa por S6 porque tienen fila en sus 30 fechas. No necesitan la
   cobertura de D.7, que empezó a registrarse con el despliegue de D. La cifra de 29
   anuncios todavía no excluye impulso abierto ni claves en vuelo. Eso llega en H,
   así que es un techo. Encender la pausa sigue reservado a David (I.8).
4. **S6 es más estricto que la regla literal, como se esperaba.** Deja fuera 47
   productos en MX y 31 en US, todos por `fecha_desconocida`: fechas sin fila que
   ninguna ingesta con rango guardado cubre todavía. Esas fechas se vuelven
   conocidas a medida que la ingesta con rango de D.7 acumula 30 días.

## Contraste con A.3 (`docs/evidencia/auto-03/plan/elegibilidad.txt`)

| Etapa de A.3 | A.3 MX | E MX | A.3 US | E US |
|---|---|---|---|---|
| (1) Productos con margen > 0 y ledger ≤ 3 días | 7 | 7 | 3 | 3 |
| (3) Grupos exclusivos | 6 | 6 | 0 | 0 |
| (3) Hojas ENABLED bajo grupos exclusivos | 104 | 104 | 0 | 0 |
| (5) Productos con margen y halo 0 | 0 | 1 | 0 | 0 |
| (6) Candidatos a pausa | 0 | 0 | 3 | 3 |
| (6) Anuncios S4 de los candidatos | 0 | 0 | 29 | 29 |

La única diferencia es la etapa (5) de MX. El SQL de A.3 cuenta el halo solo de
productos con filas en el tramo. El código cuenta como halo cero un producto sin
filas, porque una fecha sin fila no tuvo clics y no puede tener ventas atribuidas
(S7). Ese producto es el 333, sin actividad en el tramo y sin grupo exclusivo. No
cambia ninguna conclusión.

## Conciliación con Amazon (E.3, `concilia.txt`)

`concilia --n 5 --json` sobre estos mismos archivos pide a Amazon el reporte de
productos de `[2026-09-08, 2026-10-07]` y `[2026-08-09, 2026-09-07]`. Compara
gasto, clics, compras, ventas y ventas del mismo SKU por producto.

- MX tiene `diferencias: 0`. El producto con meta (333) da cero contra cero. El
  relleno informativo son los 4 de más gasto en el tramo maduro del halo (1621,
  207, 383 y 140, con 4116.16, 2508.83, 1986.12 y 893.00 MXN de gasto). Los cuatro
  son iguales al centavo en las cinco cifras.
- US tiene `diferencias: 0`. Los 3 candidatos a pausa son iguales en
  `[2026-09-08, 2026-10-08)`: 48.48, 79.92 y 67.56 USD de gasto, 103, 143 y 108
  clics, y cero compras y ventas. Del relleno, el 345 es igual. El 263 es distinto
  en una fecha.
- **Hallazgo.** Para el 263 el 2026-09-20, Amazon hoy reporta 1 compra de 138.00
  USD (del mismo SKU), con los mismos 8 clics y 5.41 USD de gasto. Orbit guardó 30
  observaciones de esa fecha. La última es del 2026-10-21 07:51 UTC (día 31) y trae
  0 compras. El cron diario pide D-31..D-1, así que deja de reobservar una fecha a
  los 31 días. Amazon todavía movió la atribución en el borde. La madurez +30
  (`fecha_madura`) no siempre es final. En la pausa, un candidato con cero ventas
  al día 31 podría recibir una venta tardía. Corregirlo sería reobservar más allá
  del día 31 o exigir más madurez. Eso cambia la ingesta o el plan, así que queda
  como decisión de David y fuera de E. `concilia` imprime esas diferencias del
  relleno con su fecha y no las cuenta en su salida. Solo cuenta los productos con
  meta y los candidatos, que es la muestra que pide E.3. La misma reatribución
  tardía puede tocar a un candidato a pausa. Entonces `concilia` sale 1 con
  razón, y la evidencia previa de F.1 o I.2 lo mostraría con su fecha antes de
  encender nada.
- El tramo `[2026-10-08, 2026-11-02]` no se concilia porque Amazon todavía
  reatribuye sus ventas. La salida lo nombra.
