# S.7: senal de ventas por unidad

Fila **S.7** de `plans/repricing-02.md` (carril S, se despliega en D.3).
`evaluar_senal` recibe `HistoriaUnidad`, evidencia `trafico` tras subida
propia, `evaluar_cohorte` (aviso), `decidir(lineal=True)`, 4 submotivos
viejos fuera y cron D.3.

## Decisiones

- Orden: `ledger_hueco` (ceros) -> `venta_sin_publicacion` (ceros: las
  ventanas estan incompletas) -> `historia_corta` -> evidencia unidades
  (ventana, `u60_min`, `n15`, disponibilidad de contados15, caida,
  racha) -> evidencia trafico. Sin subida, el fallo de unidades manda;
  con subida pero trafico corto, `evidencia_insuficiente`.
- Trafico: pre `[subida-28, subida-1]`, post `(subida, hoy-1]` (hoy se
  descarta). Solo dias con exposiciones Y clics. Caida relativa >
  `caida_ventas_pct` con z > 2 exacto (`Fraction`, z^2 > 4: el candado
  prohibe floats en el motor). `perdiendo` de trafico no exige racha
  (el rigor es el z); la racha sigue contando (`previa + 1`).
- `ultima_subida`: max `enviado_en` con direccion `subir`, estado
  `enviado`/`confirmado` y sin `sin_efecto` (lo demas no abre
  antes/despues).
- `trafico_min` (clave H) lo lee `insumos` una vez de la config vigente
  y falla fuerte sin ella: el protocolo `Vitrina.insumos` es contrato
  y no se toca (habria sido FRENADO).
- Cohorte: `1 - tasa_sub/tasa_ctrl` con tasas `(u14/14)/(u60/60)`; cada
  grupo exige `min_unidades` en 60 dias. Pura y probada; el cableado a
  `EstadoSeguridad.cohorte` queda pendiente (fuera de los Cambios).
- Lineal: `decidir(lineal=True)` cierra con el P* del `PideCotizacion`
  (regla 11, umbral y escalon igual) y `verificacion = "lineal"`; la
  fila lo guarda (`_persistir_uno` prefiere el de la decision).
- `VentasInsumos` queda en `tipos.py` sin uso (los Cambios solo mandan
  borrar los 4 submotivos); `_insumos_ventas` se borra (lo reemplaza
  `historia_unidad`). r3-K5 vive en el adaptador (F.4 H1/H2).
- Cron D.3: `--platform amazon_us` en las dos lineas + muestras 12:15
  MX / 12:20 US; tabla de horas UTC al dia.

## Desviaciones (para el PR)

- `_lectura_unidad` (carril F) cambia 6 lineas: la senal sale de
  `Lectura.historia` (ya la armaba F.4) en vez de `_insumos_ventas`.
  Sin ese cableado el motor seguiria por producto; el constructor de
  F.4 no se toca.
- `VENTAS` de corrida/pantalla migran a atribuir (`lid`): ventas sin
  atribuir darian `venta_sin_publicacion` y los viejos veredictos
  (`perdiendo`, racha 3, u15=15) se perderian.

## Comprueba

`comprobar.sh` (puro, sin base). Salida en `comprobar-salida.txt`:

- unidades: `no_perdiendo` con u15=90 u60=600 (igual que por producto).
- trafico: `perdiendo` con caida 10% -> 4% y z > 2.
- sin atribuir: `sin_dato(venta_sin_publicacion)`.
- cohorte: alarma con caida 0.9.
- lineal: `subir` con P* 131.68 y `verificacion = lineal`.

## Pruebas

- `tests/test_precio_reglas.py`: tipos (evidencia, vocabulario,
  verificacion) + 18 de senal reescritas a `HistoriaUnidad` con los
  mismos veredictos (+ `evidencia`) + 4 de trafico + 3 de cohorte + 1
  lineal: 181 passed.
- `tests/test_precio02_mercado.py`: W1 unidad vs producto, W2 subida +
  trafico punta a punta, W3 clave ausente falla fuerte, Wu helper
  puro, W4 fila guarda `lineal`: 19 passed.
- `tests/test_precio_corrida.py`: 122 passed (F6/M9/M9b/M54/racha/
  cobertura/UTC migradas a atribuir + cron D.3).
- `tests/test_precio_pantalla.py` + sonda + arq_f + notifica: 173 passed.
