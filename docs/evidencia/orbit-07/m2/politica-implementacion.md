# ORBIT 07 M2: economía compatible y fresca del producto

Estado: implementado y verificado en local sobre `c4121c5` (M1) / `f86ed7dc`
(AUTO-03). No desplegado, sin merge y sin escrituras en Amazon. La activación
sigue en manos del dueño.

## Regla implementada

En un grupo exclusivo y elegible, el peldaño `margen_producto` conserva la
fórmula de AUTO-03:

```text
meta = min(fraccion_configurada x margen_neto_del_producto, 45)
```

Sin el piso de 10 % de la plataforma (8 % x 0.5 = 4 %). La elección de la
meta queda separada de la disponibilidad de un margen válido: si la medición
no cumple sus guards (cobertura, días con venta, moneda, ledger fresco,
identidad, halo), la evaluación publica `SinDato` con su motivo y no se
inventa un margen ni un cero.

### Diferencia de M2 respecto de AUTO-03

1. **Meta directa.** Cuando gana el peldaño de producto, la meta calculada se
   aplica de una vez desde el primer ciclo elegible y en cada cambio posterior
   del margen. Es una excepción explícita a la rampa de 0.5 puntos del
   peldaño: desde un target previo de 25 % y una meta nueva de 4 %, la hoja
   aplica 4 %, no 24.5 %. Los límites y pasos de cambio de PUJA no cambian.
2. **Respaldo completo.** Si el producto pierde elegibilidad, el peldaño de
   producto no aporta un valor en transición (`aplicado = None`). La cascada
   resuelve en ese mismo ciclo la familia fresca y, si tampoco mide, el margen
   de plataforma o el setting. El target de producto no se convierte en ancla
   de la familia ni de la plataforma y no se interpola hacia otra economía.
3. **Revalidación con la economía fresca.** Una pausa decidida con
   procedencia `margen_producto` se revalida en la cola con la evaluación
   `evaluacion_producto_ciclo` del ciclo EJECUTOR y el mismo resolutor puro
   (`productos.meta_de_hoja` + `goals.cascada_target_acos*`). Se conservan las
   reglas de goal, identidad, atribución, moneda y respaldo. La meta puede
   cambiar por insumos nuevos, pero no por usar otro camino de resolución.
   Los inputs originales de la decisión no se reescriben: el detalle va a
   `notes.revalidaciones_economicas`.
4. **Versión y reversa.** La economía congelada en la decisión lleva
   `target_snapshot.version = "producto_directo_v1"`, meta calculada,
   procedencia, punto de equilibrio, moneda, ventanas del margen y de las
   ventas evaluadas, y huella del alcance. La evaluación persistida conserva
   su versión `v2`; el replay lee la evaluación y el caso congelados. Apagar
   la regla (`ads_producto_meta_<plat>` en `off` o `shadow`) restaura la
   cascada previa sin borrar evaluaciones ni reescribir decisiones.

### Selección de versión y reversa

El setting `ads_producto_economia_<plat>` elige la regla economica sin tocar
el codigo:

- ausente o `auto03`: cascada previa de AUTO-03 (rampa y convergencia de la
  meta, respaldo solo cuando el producto no mide, cola con el target
  congelado y descarte por `alcance_cambio`). Es el valor por omision, asi que
  desplegar este cambio no altera la meta de AUTO-03 hasta que el dueno lo
  elija.
- `producto_directo_v1`: M2 (meta directa, respaldo completo, economia fresca
  en la cola).

`ads_producto_meta_<plat>` sigue siendo el interruptor de la meta individual
(`off`/`shadow`/`live`); es independiente de la version economica. Un valor
ajeno en cualquiera de los dos falla cerrado (`ValueError`, ciclo `failed`).
La economia efectiva viaja completa: el caso juzga la perdida con el equilibrio del producto cuando su meta gana (8 % en el caso de 8 % x 0.5), no con un promedio de plataforma; y la cola reutiliza el lector familiar del ciclo (subfamilia -> padre y ancla previa de la hoja), no un segundo camino.

Activar M2 es una decision del dueno: `ads_producto_meta_<plat>=live` y
`ads_producto_economia_<plat>=producto_directo_v1`. La receta revisable de
[`activacion-orbit07.md`](../activacion-orbit07.md) enciende solo la segunda:
no toca `ads_producto_meta_<plat>` ni la pausa, asi que M2 queda inerte si la
meta no estaba ya en `live`. Volver a `auto03` conserva la meta live, las
evaluaciones y las decisiones; solo cambia la regla de aplicacion.

## Evidencia reproducible

`docs/evidencia/orbit-07/m2/sonda_economias.py` crea PostgreSQL 16 desechable,
corre dos ciclos con margen 8 % y fracción 0.5 (meta 4 %), revalida una pausa
económica (5000 USD de gasto, 10000 USD de ventas, un pedido y 120 clics) y
después vuelve inválido el margen del producto. Salida guardada en
[`economias.json`](economias.json):

- ciclo 1: meta 4 %, equilibrio 8 %, target 4 % con procedencia
  `margen_producto`, snapshot `producto_directo_v1`;
- revalidación sin cambio: target 4 % fresco, la pausa económica califica;
- revalidación después de perder cobertura: meta y familia `null` por
  `cobertura_baja`, respaldo al setting 30 %, la pausa económica ya no
  califica y no se aplica el 4 % congelado.

La regresión se demuestra fallando contra el código anterior (`c4121c5`):
`tests/test_orbit07_m2.py` y las pruebas de `tests/test_productos_meta.py`
fallan en la base y pasan en el candidato. El detalle de las salidas quedó en
la evidencia del encargo y en el PR.

## Rendimiento

La comparación usa la misma carga de M0 extendida con **40 grupos exclusivos**
de producto (2040 keywords: 2000 del banco + 40 del peldaño individual),
18000 filas de métricas recientes, 200 episodios y 200 filas de cola. Cada
corrida clona la MISMA plantilla y los roles alternan por ronda (5 rondas por
rol). Salida en [`rendimiento-comparado.json`](rendimiento-comparado.json),
generada con `tools/bench_orbit07_proteccion.py preparar --productos 40` y
`comparar`:

| Medición | Base `c4121c5` | Candidato |
|---|---|---|
| Consultas TX2 (fase de lecturas) | 3798 | 3798 |
| Consultas del ciclo en shadow | 3848 | 3848 |
| Mediana TX2 | 3581.42 ms | 3607.00 ms (+0.71 %) |
| Mediana del ciclo | 10419.52 ms | 10270.74 ms (-1.43 %) |

No hay consultas nuevas por producto ni por keyword: el peldaño de producto
se resuelve con la misma lectura por lote y funciones puras. La diferencia de
tiempo está dentro del presupuesto de 150 ms del banco y no es una mejora
atribuible a M2. La cola agrega, solo al revalidar una pausa con procedencia
`margen_producto`, hasta cuatro lecturas puntuales (producto exclusivo,
evaluación del ciclo, huella y familia de respaldo); no multiplica trabajo
por cada combinación de producto y keyword.

## Límites

- No se despliega ni se enciende la meta o la pausa: el dueño conserva merge,
  despliegue y activación.
- No se añade tabla, resolutor ni peldaño: se reutilizan
  `evaluacion_producto_ciclo`, la vista de margen y la medición de familia.
- La muestra local acredita el comportamiento y las cuentas; no demuestra una
  mejora comercial futura ni cuantifica pérdidas.
