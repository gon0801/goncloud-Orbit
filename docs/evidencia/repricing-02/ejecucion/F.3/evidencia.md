# F.3: universos FBM de MX y US en la estimacion

Fila **F.3** de `plans/repricing-02.md`, seccion 4. Registro de universos
(`app/estimacion_universo.py`), cotizador (`app/estimacion_amazon.py`),
partido de fees (`partir_fees`), logistica con origen, FX con techo de 3
dias para US, migracion 0055 y las politicas FBM (paso 4b de
`desplegar.sh` en `D.3/`, se prueba ahi).

## Decisiones

- Motivos viejos (`logistica_fbm_pendiente`, `us_sin_politica_prospectiva`)
  se retiran: fuera del registro da `universo_no_soportado`.
- Motivos nuevos: `fx_desactualizado` (US, `max_dias=3`), `envio_sin_historia`.
- `ReferralFee` unico y positivo es el variable; el fijo es
  `total - variable` (conclusiones R01/F.0).
- MX convierte: costo y envio de MXN a USD con la tasa del dia para US.
- La 0055 es `NOT VALID`: no valida filas viejas, solo exige a lo nuevo.
- `Envio -> campos del escenario` vive en `universo.py` para no pasar el
  presupuesto de lineas de `repository.py`.

## Comprueba

`comprobar.sh`: base local con ORDEN_F3, una oferta FBM de MX y una de US,
siembra y muestra. Salida en `comprobar-salida.txt`:

- MX FBM: `disponible`, origen `propio`, variable 5.0000, fijo 0.0000.
- US FBM: `disponible`, origen `propio`, variable 4.5000, fijo 2.1000.

## Pruebas

- `tests/test_estimacion_insumos.py`: MX/US FBM resuelven oferta, fuera del
  registro da `universo_no_soportado`, tasa vieja da `fx_desactualizado`.
- `tests/test_estimacion_fees.py`: US cotiza con `MARKETPLACE_US`, FBM sin
  comision de logistica.
- `tests/test_estimacion_amazon.py`: cotizador pide MFN en FBM MX y
  marketplace US; fuera del registro no sale a red; 7 del partido.
- `tests/test_precio02_escenario_fbm.py`: disponible MX/US con origen y
  partidos, `envio_sin_historia`, `fx_ausente`, `fx_desactualizado`,
  FBA intacto con origen `politica`, las dos 0055.
- `tests/test_estimacion_venta.py`: builder con L=5 y logistica en moneda.
- `tests/test_precio_reglas.py`: `test_cotizar_a_precio_no_amplia_universo`
  ahora rechaza US FBA (FBM cotiza desde F.3). Unica asercion tocada; la
  bateria de MX FBA pasa igual.
- Candado en `tests/test_arq_precio_f.py`: paridad registro <-> fees.

Bateria: `test_estimacion_venta + insumos + fees + amazon +
test_precio02_escenario_fbm + test_precio_reglas + test_arq_precio_f`:
351 passed. Vecinas (atribucion, envio, muestras, corrida, base,
contratos, retencion, venta_schema, arq g/m/s): 228 passed.

## Lecciones de mutantes

- El primero apunto a `construir_request_body`, pero el camino real a red
  es `serializar_request_body` (`ProductFeesClient.cotizar`): mutante
  re-apuntado al que si sale a red.
- Las 0055 parecian verdes pero el trigger `referencias_coherentes`
  rechazaba antes que el CHECK (huella distinta). Se aislo cada prueba a
  su CHECK (huella real + `match=` al nombre del constraint) y ahi si los
  mutantes enrojecen.
