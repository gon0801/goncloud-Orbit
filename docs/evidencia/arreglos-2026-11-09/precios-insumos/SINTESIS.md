# Síntesis — frente precios e insumos (G-H1, G-H3, J-H3, H-H1, G-H2, H-H4)

Candidatos: `opus/DISENO.md` y `sonnet/DISENO.md`. Sin juez cruzado.
Base: **opus** en cuatro de cinco unidades, **sonnet** en H-H4.

## Comprobado por mí contra el código y producción (2026-11-09, solo lectura)

- La oferta de Amazon se baja a las 00:35, 06:35, 12:35 y 18:35 UTC y Orbit la copia a los :45
  (`estimacion_oferta_observation`, 7 días medidos). El límite de frescura es de 6 h
  (`app/estimacion_insumos.py:41`).
- La causa de «Estados Unidos evalúa 0 de 99» ya está diagnosticada en
  `docs/evidencia/claridad-01/F/causa.md`: la copia de las 12:45 salía antes de que el bridge
  terminara US. #561 hace que la copia espere al bridge hasta 20 min y avise si no termina
  (`tools/refresh_estimacion.sh`). Desplegado el 9-nov 21:28 UTC. **Sin probar hasta la corrida
  del 10-nov 13:10 UTC.**
- Lo que #561 no cubre: si el bridge tarda más de 20 min, la corrida de las 13:10 gasta la única
  decisión del día con la oferta vieja, aunque a las 18:45 llegue una fresca y haya corridas a las
  19:10, 21:10 y 23:10. Eso es lo que arregla J-H3.
- Ventas del ledger sin producto: amazon_mx 180 de 1587, amazon_us 63 de 622, meli 532 de 1103.
- 0 publicaciones de MeLi `frenado / api_error` en 7 días. Nada que volver a sembrar para G-H2.
- 0 retenciones de precio en 30 días. `precio_modo_global` en shadow desde el 9-nov 16:09 UTC.
- Costos: 15 corridas con 0 saltos en 14 días. `refresh_costos.sh` ya lleva `cron-avisa`.
- `migraciones_de` (`tools/despliegue/comun.sh:138-150`) solo aplica archivos con la etiqueta
  `_(auto03|orbit07|claridad01)_`, con encabezado `-- sonda-aplicada:` y con reversa. Toda
  migración nueva de este frente lleva la etiqueta del plan y el patrón se amplía en la misma unidad.

## Decisión por hallazgo

| Hallazgo | Base | Injerto | Rechazado |
|---|---|---|---|
| G-H1 + G-H3 la compuerta mide mal | opus, las dos juntas. `_SQL_MEDIR_PREVIA` saca el día completo del universo (plataforma, canal, fecha) cuando hubo una retención por `movimiento_masivo` o `insumo_sistemico` que no está en `v_precio_decision_liberada`. Las tasas se comparan sobre el ingreso de la misma decisión (`_INGRESO = (13, 14)`, `_INSUMOS_POR_INGRESO = frozenset({"retencion", "fee_variable"})`) | ninguno | sonnet: sacar solo las filas retenidas (el resto del día sigue contaminando la medida) y partirlo en dos PR sobre la misma consulta |
| J-H3 el día se pierde con insumo atrasado | opus: `MOTIVOS_INSUMO_ATRASADO`, `ESPERA_INSUMOS_HASTA = time(21, 0)`, `insumo_atrasado(motivos, *, ahora_utc) -> str \| None` en `app/precio/compuerta.py`. En `_decidir_pendientes`, si la mayoría del lote no trae el insumo del día y es antes de las 21:00 UTC, la corrida regresa sin escribir y la siguiente decide | ninguno | sonnet: `oferta_al_dia` (mira un solo insumo; la lista de motivos de opus cubre los demás) |
| H-H1 los costos se frenan o se cierran en bloque | opus: `Cierre`, `PlanSku(cierres, inserciones, convergencia, sin_costo)`, `recortar(serie, *, corte)`, `planear_sku(serie, publicadas, *, corte)`, `sync_costos(conn, ruta_sqlite, *, corte, acepto_cierre_masivo=False)` con tope de 10 %. Se quita la compuerta global `rows_skipped` (`app/estimacion_insumos.py:459,479` y el trigger de `migrations/0073_precio02_meli_miembro_costo.sql:265`). Aviso por `notifica.envia_aviso` | ninguno | sonnet: re-anclar solo la cola (`_reanclar_cola`) y que el salto real salga 1 para avisar. Un solo SKU irrecuperable seguiría dejando sin evaluar a todos, ahora con aviso |
| G-H2 MeLi con variaciones | opus: `Vitrina.unidades(conn, listing_ids) -> Mapping[int, UnidadPrecio]` en `app/precio/puerto.py`. Migran todos los que arman la unidad a mano, incluidos `app/precio/corrida.py:884` y `app/precio/goals_write.py:427`. Se borra `_unidad_de_listing`. `_ruta_error` a la entrada de `escribir` | ninguno | opus: candado de texto en `tests/test_architecture.py` (el repo no admite pruebas de texto). sonnet: `unidad_de` de una sola publicación en vez del lector por lote |
| H-H4 reatribución sin rastro | sonnet: tabla `ledger_reatribucion`; el trigger `ledger_solo_reatribuye` escribe él mismo la auditoría; solo producto→producto; solo Amazon. `_reatribuir_ventas(conn, ventas, *, run_id) -> int` | ninguno | opus: la fila de auditoría la escribe el código y el trigger comprueba que exista (dos pasos que deben coincidir), e incluye ventas sin producto, que es decisión del dueño |

## Unidades y orden

1. **U1 compuerta (G-H1 + G-H3).** Sin cambio en vivo hoy (precios en shadow, 0 retenciones en 30 días). Prueba con base en `tests/test_precio_corrida.py` cerca de `:4392`: un día con retención masiva no liberada no entra a la medida.
2. **U2 J-H3.** Mismo archivo que U1, va en serie después. Cambia cuándo se decide, no qué se decide. Prueba: lote con mayoría `oferta_desactualizada` a las 13:10 UTC no escribe decisiones; a las 21:10 sí.
3. **U3 H-H1.** Migración primero, código después. Cambia datos de producción (costos). Prueba: un SKU sin costo en la copia no frena a los demás, y un cierre de más del 10 % de los SKU aborta sin escribir.
4. **U4 G-H2.** Después de U1. Cambia una escritura a MeLi. Prueba en `tests/test_precio_write.py`: publicación con miembros `111` y `222` llama `poner_precio('MLM123', ['111','222'], ...)`.
5. **U5 H-H4.** Al final. Migración con reversa. Prueba: un UPDATE que cambia el producto de una venta deja una fila en `ledger_reatribucion`; un UPDATE de otro campo sigue rechazado.

## Decisiones del dueño

1. H-H1: «un costo nuevo entra al día siguiente, no el mismo día» (`recortar`). Cambia tres letras
   previas (`plans/orbit-06.md:233-236`, docstring de `app/costs.py`, hallazgo 5 en
   `costs.py:524-527`). Recomiendo sí. Si dices no, se quita `recortar` y el resto queda igual.
2. H-H1: tope de 10 % de SKU cerrados en una corrida; más que eso aborta y avisa. Recomiendo sí.
3. H-H4: ventas que hoy no tienen producto (775 medidas) ¿se pueden atribuir después a un producto?
   Recomiendo dejarlo fuera de esta unidad y decidirlo aparte; el diseño solo cubre producto→producto.
4. J-H3: esperar hasta las 21:00 UTC por el insumo del día antes de decidir sin él. Recomiendo sí.

## Riesgos

- J-H3 se apoya en que hay corridas de precios cada 2 h hasta las 23:10 (leído en `causa.md`, no en el crontab de hoy).
- G-H2: no medí cómo responde MeLi cuando las variaciones tienen precios distintos.
- U3 toca un trigger de la migración 0073; la reversa debe restaurarlo textual.
