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

## Límites

- No se despliega ni se enciende la meta o la pausa: el dueño conserva merge,
  despliegue y activación.
- No se añade tabla, resolutor ni peldaño: se reutilizan
  `evaluacion_producto_ciclo`, la vista de margen y la medición de familia.
- La muestra local acredita el comportamiento y las cuentas; no demuestra una
  mejora comercial futura ni cuantifica pérdidas.
