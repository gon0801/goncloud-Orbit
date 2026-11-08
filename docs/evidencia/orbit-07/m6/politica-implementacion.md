# M6: evaluación económica de la repetición

Fecha: 2026-11-08 (UTC). Base: `4e0e78d` (M2), sobre `f86ed7dc` (AUTO-03).
Plan que manda: `plans/orbit-07-motor-decisiones.md`, M6.

## Regla

La trayectoria vigente (`R14`-`R17`) sigue decidiendo cuando no hay evidencia
económica madura, comparable y aplicable. Con un episodio M5 en ese estado,
la repetición de la misma dirección se juzga así:

- **Recorte** (`factor < 0`): se permite repetir si la ventana posterior
  conservó las ventas (`venta_post >= venta_pre`) y mejoró la economía
  (`ACoS_post < ACoS_pre`). En ese caso el freno de tráfico de R16 no aplica:
  el recorte podó tráfico que no vendía. Si no conservó ventas o no mejoró el
  ACoS, `Mantener(recorte_sin_economia)`.
- **Subida** (`factor > 0`): se frena con `Mantener(subida_sin_economia)`
  cuando el tráfico adicional no compensó el gasto: no creció la venta, o con
  margen compatible la contribución estimada no mejoró, o sin margen el ACoS
  empeoró y superó el target. Si compensa, R16 sigue aplicando su criterio de
  tráfico; la regla nueva no relaja una subida sin tráfico.

ACoS se calcula sobre `gasto / venta` de cada ventana; no necesita margen. La
contribución estimada (`venta × margen_pct / 100 - gasto`) solo se calcula y
se usa para la subida cuando el margen efectivo del caso está resuelto por
M2. Sin margen, se conserva como observación y decide el ACoS contra el
target.

## Parámetros y por qué

| Parámetro | Valor | Razón |
|---|---|---|
| Ventanas | `[x-7, x-1]` y `[x+1, x+7]`, sin el día del cambio | Siete fechas completas a cada lado, igual longitud; el día del cambio mezcla el precio viejo y el nuevo. |
| Madurez | `x+7 <= D-10` (D+17 como pronto) | Es la madurez de cortes del repositorio (`DIAS_MADUREZ = 10`): la venta posterior todavía se reobserva antes de D+17. |
| Ventas conservadas | `venta_post >= venta_pre` | Un recorte que pierde ventas no puede repetirse aunque baje el ACoS. No es un umbral monetario nuevo: es no perder. |
| Mejor economía | `ACoS_post < ACoS_pre` | Es la misma métrica del target, calculada con las dos ventanas; no se inventa un porcentaje de mejora. |
| Subida compensada | venta crece y (contribución sube si hay margen; si no, ACoS dentro del target o no peor) | El target ya expresa cuánto gasta el dueño por venta; no se define otro umbral de riesgo. |
| Mínimo de evidencia | 20 clics al bid nuevo (R15) y ventanas completas | Se conserva el mínimo vigente de repetición; una ventana incompleta no demuestra nada. |
| Selección | `ads_bid_economia_<plat> = economia_cambio_v1` | Ausente/`off` conserva la trayectoria vigente y no lee ni congela episodios. |

## Casos que sustentan la regla

`tests/test_orbit07_m5m6.py` demuestra, con el camino real de
`_procesa_decisora`:

1. Recorte con ventas conservadas y ACoS de 20 % a 15 %: repite `-12 %`
   aunque el tráfico haya caído a 0.50 (R16 habría frenado).
2. Recorte con ventas de 1000 a 700: `recorte_sin_economia`.
3. Recorte con la misma venta y ACoS de 20 % a 25 %: `recorte_sin_economia`.
4. Subida con venta 200 a 210 y ACoS de 20 % a 28.6 %: `subida_sin_economia`.
5. Subida con venta 200 a 500 y ACoS 12 % (dentro del target): repite
   `+15 %`.
6. Subida con contribución estimada que cae con margen 8 %:
   `subida_sin_economia`.
7. Episodio inmaduro, incomparable, no aplicable o ausente: R16 conserva el
   freno de tráfico.

## Dónde la regla sería incorrecta (y cómo se guarda)

- **Estacionalidad o un pico de ventas en la ventana previa**: el episodio
  puede atribuir al recorte una caída que no causó. Se guarda con la
  comparabilidad estricta (cambio en ventana = `incomparable`) y con R17
  (piso aprendido); no se promete causalidad, solo evidencia observada.
- **Efecto retardado de una subida**: una subida permitida por la trayectoria
  en D+8 puede ocurrir antes de conocer su efecto económico en D+17. La regla
  **no** la impide y el plan prohíbe afirmar que lo hace. Solo juzga la
  repetición cuando el episodio ya maduró.
- **Cambio de precio, presupuesto, placement o alcance**: invalida la
  comparación o deja el episodio como historia; no se usa para una decisión
  nueva.
- **Fuente incompleta**: una ventana sin sus siete fechas y diez días de
  madurez no se rellena con cero; el episodio queda `incomparable` o
  `inmaduro` y la trayectoria vigente decide.

## Activación y reversa

Desplegar este código no enciende la regla: `ads_bid_economia_<plat>` está
ausente por defecto. El dueño la activa con una `config_version` nueva
(`economia_cambio_v1`) después de integrar y desplegar. Quitar la clave la
apaga sin regresar el código ni borrar decisiones: cada fila conserva su
`inputs.caso` congelado y el replay sigue reproduciéndola. Las entidades sin
un episodio maduro conservan la evaluación de trayectoria.

## Seguimiento operativo

Desde el primer ciclo con la regla encendida se registran, por mercado y
moneda: decisiones cambiadas (`recorte_sin_economia`, `subida_sin_economia`),
episodios `maduro`/`inmaduro`/`incomparable`, cobertura de ventanas, gasto y
venta de las ventanas y contribución estimada (solo con margen compatible).
Ante un defecto comprobado se corrige o se apaga la clave; no se espera a
completar un experimento. No se atribuye causalidad a una comparación antes
y después.

## Rendimiento

[`rendimiento-comparado.json`](rendimiento-comparado.json) mide base y
candidato alternados sobre la misma carga fija con 200 cambios de bid. La
regla agrega 4 consultas por plataforma (eventos de configuración, ajustes,
precios y anuncios nuevos) y engorda la consulta del efecto del último cambio
con las dos ventanas económicas; no agrega una consulta por hoja. La medición
se contrasta con el presupuesto de M0 (`150 ms` de mediana).
