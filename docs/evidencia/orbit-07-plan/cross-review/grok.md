El dueño quiere decisiones mejores del motor, activas tras una comprobación técnica, sin pilotos ni esperas para todo el catálogo. Esta revisión solo juzga el plan. No propone implementación.

**Dictamen.** El plan se puede construir. M1 cambia la pausa del caso de 120 clics. M3 y M4 cambian subidas y retiros por producto. M5 no decide sola y viaja con M6. El recorte de M6 sí puede cambiar una decisión más adelante, sin frenar al resto de las hojas. Hay dos reglas cuya redacción no se puede implementar de una sola manera. El desacuerdo restante con el código es un cambio que el plan ya ordena.

**La meta de 4 % no es el target aplicado.** Severidad alta.

M2 documenta `min(fracción × margen, MARGEN_BANDA_MAX)` y el caso 8 % × 0,5 = 4 %, sin subirlo al piso. El borrador reutilizado dice que ese cambio se limita al piso de 10 % del peldaño nuevo.

La sonda llamó a `resuelve_target_margen` con margen 8 y fracción 0,5. La fórmula del plan dio 4,0. Sin ancla, el aplicado fue 10 y el derivado 4,0. Con ancla 25, el aplicado fue 24,5 y el derivado 4,0. El paso de bajada impreso fue 0,5. El 24,5 es 25 − 0,5. El piso de 10 no produce ese resultado. Quitar el piso, que el plan sí ordena, deja el mismo 24,5 mientras exista un target previo de 25.

La intención exige que la economía del producto decida al entregar. Lo medido es que el primer aplicado sigue en 24,5. Inferencia: las pujas de ese producto siguen el target anterior en ese ciclo. No medí cuántos productos tienen ancla ni cuánto dinero mueven.

Corrección mínima. Una frase debe decir si el aplicado de la entrega es 4 en el primer ciclo, o si 4 es solo el destino y el paso de 0,5 por ciclo sigue vigente.

**El freno de subida de M6 llega después de la subida.** Severidad alta.

El plan conserva los siete días entre ajustes. M5 fija que siete días posteriores, con madurez de diez, caben como pronto en D+17. Hasta entonces manda conservar la evaluación vigente. Un cambio posterior de puja invalida la comparación. M6 quiere frenar la repetición de una subida cuyo tráfico adicional no compensa el gasto.

La sonda usó el cambio del 2026-10-01, `fin_ventana_cortes` y la cuenta de `dias_post`. El 2026-10-09 el `dias_post` llegó a 7, la espera de efecto ya no bloqueaba y el cooldown tampoco. El `window_end` fue 2026-09-29. El último día del post, 2026-10-08, quedó fuera. El 2026-10-18 el `window_end` fue 2026-10-08 y el post ya cupo. El D+17 del plan es correcto.

En esa misma sonda, `decide` con `dias_post` 7 y razón de tráfico 1,1 devolvió `Mover` de 10 a 11,50, motivo `bajo_target`. Con razón 0,5 devolvió `Mantener` por `recorte_costo_trafico`.

La evaluación vigente, que el plan manda conservar mientras el episodio no está maduro, autoriza esa subida nueve días antes de que la ventana entre en cortes. Si esa puja cuenta como cambio posterior, el episodio deja de servir para frenarla. El recorte no choca con el reloj. La regla vigente lo detiene y no hay otra puja que invalide la ventana.

Lo medido es el veredicto y las fechas. Inferencia: el freno económico no alcanza la subida que el plan describe. No medí cuántas hojas repetirían una subida ni el gasto que moverían.

Corrección mínima. Elegir una regla. O el freno no sustituye una repetición que la trayectoria vigente autorice antes de D+17. O esa repetición espera a que su episodio sea comparable, y la puja hecha bajo la regla vigente no invalida los siete días que terminaron el día anterior.

**Hipótesis excluidas.** No entran al dictamen. El piso que convierte 4 en 10 sin ancla ya está ordenado por el plan. La cascada actual, que devuelve `margen_familia` y no tiene peldaño de producto, también. El orden nuevo está en el borrador que el plan reutiliza. El caso de 500 MXN no dispara la pausa económica, porque el exceso fue 500 frente a un mínimo de 1000. Por eso el ejemplo de protección de M1 no contradice la precedencia económica. Con 1500 el código devuelve `pause_umbral` aunque el exceso ya pasa de 1000. El plan ya manda evaluar los dos motivos antes de proteger. No probé con una sonda una contradicción de M3, de la reversa de M4 ni de un retraso de ingesta en el ancla de M1.

**Comprobaciones.** Una sonda local, con el Python del proyecto, `PYTHONDONTWRITEBYTECODE=1` y `PYTHONPATH=.:tests`, terminó en cero. Reprodujo el apéndice: 99 clics dan `Mantener(venta_reciente)` y 120 dan `Pausar(pause_umbral)`. Midió el peldaño de margen, la cascada, el reloj D+8 contra D+17 y los dos veredictos de repetición. No consulté producción ni ejecuté la suite.

Ataqué la premisa de que todo desacuerdo con el código es un fallo del plan, y dejé fuera lo que el plan ya manda cambiar. Los dos hallazgos que quedan están en la salida de esa sonda.
