# Revisión cruzada de Kimi

2026-11-05. Pedida por David después de Astra. `kimi -p` (2.3.1) en una copia aparte de a5efac2f, que quedó sin cambios. Reporte tal cual, desde el veredicto; correcciones en `sintesis.md`, sección "Revisión cruzada de Kimi".

REQUEST_CHANGES

  1. **Bloqueante — Se puede perder el diario indispensable para revertir un despliegue.** Si `desplegar` termina con `ESTADO=ok`, el checklist falla y el motor muere antes del rollback, al reanudar ejecuta otra vez `desplegar` con el mismo SHA (`/Users/dn/dev/goncloud-motor/motor/effects.py:695-729`). B.1 no considera `ok` una reanudación: reemplaza `ultimo.env`, asigna `SHA_PREVIO=<MOTOR_SHA>` y activa la monotonía contra el propio SHA. Desaparecen `CODIGO`, `MIGRACION` y `CLAVE`; el rollback ya no deshace nada. También se pierde una reversa incompleta cuando B.3 obliga a entregar otro SHA (`plans/decisiones-producto-01.md:52-54`, `modelo-despliegue/modelo.py:64-75,109-121`).

     Reproduje el primer camino extrayendo las funciones del modelo con `python3 -B -`; salida:

     ```text
     desplegar = 0 rollback = 0
     MOTOR_SHA=Fm
     ESTADO=guardas
     SHA_PREVIO=Fm
     ESTADO=ok
     ```

     **Corrección para pegar:** «B.1/B.2 distinguen `ESTADO=ok` —terminó `desplegar`— de `ESTADO=verificado`, que solo escribe `checklist.sh` al pasar. Una corrida con el mismo `MOTOR_SHA` conserva su diario hasta estar verificada; en particular, un corte entre checklist rojo y rollback no reinicia `SHA_PREVIO` ni activa monotonía contra el propio SHA. El rollback anota `revirtiendo` antes del primer cambio y termina en `revertido` o `rollback_incompleto`. Un diario con cambios cuya reversa no terminó jamás se sobrescribe ni se marca `abortado_sin_cambios`: antes de aceptar otro `MOTOR_SHA` se completa esa reversa o se aborta sin tocar nada. B.5 y el modelo fuerzan ambos cortes —después de `ESTADO=ok` con checklist rojo y durante un rollback que falla— y demuestran que conservan `SHA_PREVIO`, `CODIGO`, `MIGRACION` y `CLAVE` hasta restaurar todo.»

  2. **Bloqueante — S6 puede convertir en cero una fecha que tenía actividad pero cuya fila fue descartada.** La ingesta elimina filas con métricas inválidas, sin ASIN/SKU o con clave envenenada (`app/ads/reports.py:1186-1194,1233-1267`), pero igual registra el reporte `written` y sella `ingest_run.ok=true` (`app/ads/reports.py:1732-1755`). Si el producto tiene otra fila válida en la ventana, S6 cuenta la fecha descartada como cero; una venta oculta puede terminar autorizando una pausa (`plans/decisiones-producto-01.md:19,83,87`).

     Reproduje `_planea_filas_productos` con una fecha válida y otra con `clicks="invalido"`:

     ```text
     fechas_escritas: ['2026-11-01']
     skips: {'fila de productos con metrica no numerica o fraccionaria': 1}
     ```

     **Corrección para pegar:** «D.7 agrega a `ads_report_result` una marca estructurada `cobertura_integra`, NULL en las filas anteriores. La ingesta solo la escribe `true` cuando no descartó ninguna fila que pueda representar actividad del rango: ASIN/SKU ausente, fecha o métrica inválida, fila sin métricas, `same_sku` incoherente o clave envenenada. Los duplicados por reingesta no la invalidan. S6 exige `cobertura_integra IS TRUE` además de `ok`, `written` y rango; una cobertura incompleta es dato desconocido, nunca cero.»

  3. **Bloqueante — La revalidación puede volver a pausar una reactivación manual dentro de los 7 días.** H.7 solo detecta la reactivación cuando el caché tiene `synced_at > retirado_el`; el retiro no actualiza ese caché (`app/retiro_anuncios.py:205-229`). Si David reactiva en Amazon antes del siguiente sync, la vista no abre gracia. I.3 lee `ENABLED` fresco, pero no ordena usar esa lectura para cerrar el retiro y abrir la gracia, así que puede enviar otro PUT (`plans/decisiones-producto-01.md:144,158`).

     **Corrección para pegar:** «Si el LIST fresco de I.3 devuelve ENABLED para un product ad con un retiro abierto de modo pausa, lo trata como `regresado_por_ti` aunque `v_anuncio_regresado` todavía no lo muestre: antes de cualquier PUT actualiza `ad_entity_state` con esa lectura y llama `cierra_retiro_reactivado`. En revalidación descarta antes de cobrar; si el candado del cierre está ocupado, tampoco envía el PUT y reintenta después. I.6 cubre reactivación manual con caché anterior al retiro.»

  4. **Bloqueante — C.6 permite encender la pausa antes de que exista su camino seguro de ejecución.** Desde C, `productos-modo --pausa live` y `proxima_config` admiten `live` cuando hay límites. En H ya se generan filas de cola, pero el ejecutor llega en I.3/I.4 y el descarte atómico de prácticas en I.7 (`plans/decisiones-producto-01.md:70-71,140-141,158-162`). Esas filas `live` son seleccionadas por el barrido (`app/apply_cola.py:172-177`); pueden romper H y quedar pendientes para ejecutarse al desplegar I sin que David haya usado el botón ni la simulación.

     **Corrección para pegar:** «Hasta I.7, `proxima_config` y `productos-modo` aceptan para la pausa únicamente `off|shadow`; `live` se rechaza aunque existan límites. I.7 habilita `live` exclusivamente mediante `productos_modo.cambia_pausa`, con límites guardados, candado y descarte atómico de prácticas. H fuerza `shadow` para toda fila `product_ad`, sin habilitar ejecución antes de I.3/I.4.»

  5. **Bloqueante — El nuevo permiso de inserción permite eludir el invariante de un solo retiro abierto.** I.1 concede `INSERT` en `anuncio_retiro` a `app_decide`, pero el trigger existente consulta relaciones sin cualificar y no fija `search_path` (`migrations/0066_bids02_retiro_anuncios.sql:59-74`). No hay revocación de `TEMP` en las migraciones. Una tabla temporal homónima puede ocultar el retiro abierto real y admitir otro (`plans/decisiones-producto-01.md:156`).

     **Corrección para pegar:** «La 0080 redefine `public.anuncio_retiro_sin_abierto()` con `SET search_path = pg_catalog, public, pg_temp` y referencias explícitas a `public.anuncio_retiro` y `public.anuncio_reposicion`; su reversa restaura la definición anterior. La prueba con `SET ROLE app_decide` crea un temporal homónimo vacío y demuestra que un segundo retiro abierto sigue rechazado.»

  6. **Bloqueante — Una muerte entre `apply_attempt` e intención deja la cola bloqueada para siempre.** I.3 ordena `apply_attempt` con commit y después `ejecuta_retiro`; la intención se abre dentro de este último (`app/retiro_anuncios.py:177-202`). Si el proceso muere entre ambos, no pudo salir ningún PUT, pero la fila queda `applying` sin intención. La reconciliación por `decision_id` no define ese caso y la sonda de I.8 queda roja (`plans/decisiones-producto-01.md:158,163`).

     **Corrección para pegar:** «Si una fila `applying` de `product_ad` no tiene intención con su `decision_id`, el orden intención-antes-del-PUT demuestra que no se envió el PUT: la reconciliación sella el `apply_attempt` como fallo sin HTTP y cierra la cola en `failed`, sin otra cuota ni intención nueva. I.6 cubre la muerte después del commit de `apply_attempt` y antes de abrir la intención.»

  7. **Bloqueante — Una pausa confirmada puede quedar `applied` sin sellar el ledger ni el resumen de aplicación.** I.3 solo dice crear `apply_attempt` y “cerrar la fila desde el resultado de su intención”; no exige los sellos de `apply_attempt` y `decision_application`. La sonda de I.8 solo comprueba que exista un intento con cuota, aunque siga abierto. Así puede dar verde sin `finished_at`, sin `decision_application.verify_ok` y sin `applied_cycle_id`, contraviniendo `docs/APPLY.md:184-191,242-285` y el patrón de `app/apply_cola.py:989-997`.

     **Corrección para pegar:** «Al confirmar PAUSED, tanto la ejecución como la reconciliación sellan en una misma transacción el `apply_attempt` (`ack`, resultado y `finished_at`), llaman `apply._confirma_resumen(..., verify_ok=True, cycle_id_ejecutor=...)`, actualizan `ad_entity_state` con el readback y cierran `apply_queue`. Los rechazos y divergencias sellan su fallo correspondiente. `pausa_lista` exige además intento terminado y `decision_application.verify_ok` coherente con el estado de la cola.»

  8. **Bloqueante — La primera ejecución de `--ensayo` contra producción no está precedida por una prueba de solo lectura.** B.5(a)-(l) no ejecuta `--ensayo`; un script que ignore ese argumento y haga migraciones, sustitución de código o `compose up` puede pasar todas las compuertas locales. Después, la `prueba-real` de B lo ejecuta contra producción y acepta cualquier escritura que termine en 0 (`plans/decisiones-producto-01.md:49,52,56`).

     **Corrección para pegar:** «B.5 agrega un escenario `--ensayo` con `ORBIT_SIMULACION=1` y los `ssh`/`docker` falsos: toma antes y después la huella de `$SRV`, del esquema y de la config; exige que no se escriba `ultimo.env` ni `sha`, que no haya `MIGRACION`, `CLAVE`, `CODIGO`, sustitución de archivos ni `compose up`, y que la base quede igual. El mismo escenario comprueba salida distinta de cero con `ssh` o `psql` fallando y con una sonda vacía. La `prueba-real` de B solo queda habilitada con esa demostración de solo lectura en verde.»

  9. **Bloqueante — El sondeo prescrito para `concilia` escribe en la base y falla con el rol de lectura.** `_sondear_intento` registra `downloaded` al completar (`app/ads/descarga.py:262-313`). Eso inserta en `ads_report_result`, cuyo `INSERT` pertenece exclusivamente a `app_ingest` (`migrations/0040_ads_report_result.sql:49-51`). Por tanto, E.3 contradice el requisito de solo lectura y hace fallar las evidencias E/F/I con `ORBIT_DSN_READ` (`plans/decisiones-producto-01.md:94,99,110,157`).

     **Corrección para pegar:** «E.3 no usa `_sondear_intento` ni ninguna función que reciba `conn` o `run_id`: emplea un sondeo HTTP puro con `get_report`, `_url_de_completado` y `descargar_filas`. `concilia` no crea `ingest_run` ni inserta `ads_report_result`, y mantiene un plazo total de 1500 s.»

  10. **Bloqueante — `meta_live` rechaza el comportamiento correcto cuando el producto se abstiene.** F.5 exige filas `margen_producto` para todas las hojas aptas sin target de goal. Pero una abstención sin ancla o sin destino devuelve correctamente `aplicado=None`, de modo que la hoja cae al siguiente peldaño o queda sin target (`plans/decisiones-producto-01.md:68-69,114`, `app/optimizer/goals.py:860-877`).

      Comprobé:

      ```text
      sin_ancla: ResolucionMargen(aplicado=None, ...)
      sin_destino: ResolucionMargen(aplicado=None, ...)
      ```

      La sonda haría fallar y revertir F aunque el motor esté decidiendo bien.

      **Corrección para pegar:** «F.5 (4): las filas `margen_producto` son exactamente las hojas aptas sin target de goal cuya `ResolucionMargen.aplicado` congelado no es NULL. Una hoja apta con abstención o sin destino aparece con su peldaño de respaldo o sin fila y no constituye una diferencia.»

  11. **Bloqueante — La conciliación que habilita la meta no revisa la ventana que determina esa meta.** S7 calcula identidad y halo sobre `[D-90, max(metric_date)-3]`, pero E.3 solo concilia `[D-60,D-30)`. F.1 puede obtener `replay: diferencias: 0` y `concilia: diferencias: 0` con datos incorrectos en el resto del halo; replay solo reproduce la entrada congelada, no concilia su fuente (`plans/decisiones-producto-01.md:20,99,110`).

      **Corrección para pegar:** «E.3/F.1: para productos con meta, `concilia` compara también el agregado S7 completo `[D-90,max(metric_date)-3]`, dividido en reportes de ≤31 días, incluyendo ventas totales y ventas del mismo SKU con la misma vinculación ASIN/SKU del resolutor; para candidatos a pausa conserva `[D-60,D-30)`. Los reportes necesarios se piden concurrentemente y el plazo total de `concilia` sigue acotado. Una conciliación exclusivamente de la ventana de pausa no habilita F.»

  12. **Bloqueante — I.3 exige usar el cliente protegido, pero declara firmas que no pueden recibirlo.** `revalida_pause_producto(conn, fila, ahora)`, `ejecuta_pause_producto(conn, fila)` y `reconcilia_pause_producto(conn, fila)` no reciben `aplicador`, aunque la misma fila exige `aplicador._cliente()`. Los despachadores sí tienen ese objeto (`app/apply_cola.py:901-925,1073-1080`, `app/apply_harvest_reconciliacion.py:247-259`). Implementar las firmas literales obliga a omitir la protección de dueño o inventar otro camino (`plans/decisiones-producto-01.md:158`).

      **Corrección para pegar:** «Las firmas son `revalida_pause_producto(conn, aplicador, fila, ahora)`, `ejecuta_pause_producto(conn, aplicador, fila)` y `reconcilia_pause_producto(conn, aplicador, fila)`. I.4 pasa el mismo `Aplicador` del ciclo a las tres; las lecturas y mutaciones de Amazon usan exclusivamente `aplicador._cliente()`.»

  13. **Bloqueante — La exclusión mutua del despliegue puede estar ausente y B.5 seguiría en verde.** B.5(e) ocupa una guarda funcional, no `$SRV/despliegue/.lock`; los demás escenarios son secuenciales. Una implementación sin `flock`, o que no lo conserve durante toda la corrida, pasaría las pruebas. Dos ejecutores podrían reemplazar `ultimo.env` y atribuir respaldos o claves a la corrida equivocada (`plans/decisiones-producto-01.md:52-56`).

      **Corrección para pegar:** «B.5 agrega un escenario con `$SRV/despliegue/.lock` tomado por otro proceso: con `AUTO03_ESPERA_S=1`, `desplegar` sale 75 dentro del plazo, no crea ni modifica `ultimo.env` y no ejecuta ninguna escritura; después de liberarlo, la reanudación conserva el diario. Otro escenario mantiene ocupado el candado durante un despliegue con cambios y comprueba que `rollback.sh` no lee ni revierte hasta adquirirlo, y que dos corridas concurrentes nunca comparten ni reemplazan sus líneas `CODIGO`, `MIGRACION` o `CLAVE`.»

  14. **Nota — F.1 e I.2 dicen guardar evidencia, pero sus comandos no crean el archivo.** Ambos afirman que la salida queda en `docs/evidencia/auto-03/ejecucion/{F,I}/evidencia.txt`, pero solo ejecutan `ssh` (`plans/decisiones-producto-01.md:110,157`).

      **Corrección para pegar:** «F.1 e I.2 crean su directorio de evidencia, ejecutan el `ssh` bajo `set -o pipefail` y terminan el pipeline con `| tee docs/evidencia/auto-03/ejecucion/<F|I>/evidencia.txt`; el `Acepta` falla si falla cualquier comando remoto o la escritura local.»

  15. **Nota — La síntesis conserva la fuente de gracia anterior a la corrección de H.7.** Todavía dice `reactivacion_manual.detectada_en`; el plan ahora cierra el retiro y usa `anuncio_reposicion.repuesto_el` (`docs/evidencia/auto-03/plan/sintesis.md:77`, `plans/decisiones-producto-01.md:30,144`).

      **Corrección para pegar:** «Corrección: fila **H.7** y supuesto **S17** —gracia de 7 días desde `anuncio_reposicion.repuesto_el`; si se reactivó directo en Amazon, el ciclo cierra el retiro con `actor='reactivacion_manual_amazon'` y esa misma `repuesto_el` abre la gracia—.»

  Confianza alta en los hallazgos estáticos y en las reproducciones puras. `motor valida` y el modelo actual salen verdes, pero no cubren los casos señalados. No ejecuté PostgreSQL, Amazon, SSH, despliegues ni UI; no verifiqué los privilegios efectivos ni los datos de producción.

