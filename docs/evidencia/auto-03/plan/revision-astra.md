# Revisión cruzada de Astra, pasada 1

2026-11-05. Pedida por David. `gpt-6-astra` vía `codex exec -s read-only` sobre 6c886e53. Reporte tal cual; las correcciones y su comprobación están en `sintesis.md`, sección "Revisión cruzada de Astra".

REQUEST_CHANGES

1. **Bloqueante — El rollback abandona un despliegue parcialmente aplicado.** En B.1–B.2, una reanudación que encuentra las guardas ocupadas escribe `abortado_sin_cambios` aunque la corrida anterior ya hubiera cambiado código o interruptores. B.2 entonces devuelve éxito sin restaurarlos. Evidencia: [plan:52](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:52), [modelo.py:64](/Users/dn/dev/orbit-lead-auto03-plan/docs/evidencia/auto-03/plan/modelo-despliegue/modelo.py:64) y [modelo.py:105](/Users/dn/dev/orbit-lead-auto03-plan/docs/evidencia/auto-03/plan/modelo-despliegue/modelo.py:105).

   Ejecuté esta reproducción en memoria, sin escribir archivos:

   ```bash
   python3 -B -c '
   import ast
   from pathlib import Path
   p=Path("docs/evidencia/auto-03/plan/modelo-despliegue/modelo.py")
   t=ast.parse(p.read_text())
   j=["MOTOR_SHA=Fm","ESTADO=guardas","SHA_PREVIO=E","CODIGO=predeploy-1","CLAVE=ads_producto_meta_amazon_mx:shadow"]
   n={"ult":lambda:j,"ultimo":lambda k,L:[x.split("=",1)[1] for x in L if x.startswith(k+"=")][-1],"anota":j.append,"OCUPADO":{"Fm"}}
   exec(compile(ast.Module(body=[x for x in t.body if isinstance(x,ast.FunctionDef) and x.name in ("desplegar","rollback")],type_ignores=[]),str(p),"exec"),n)
   print(n["desplegar"]("Fm"),n["rollback"]("Fm"),j[-1])
   '
   ```

   Salida: `75 0 ESTADO=abortado_sin_cambios`.

   **Texto para pegar:** «B.1 solo escribe `abortado_sin_cambios` cuando el diario conservado no contiene `CODIGO`, `MIGRACION` ni `CLAVE`; si una reanudación agota las guardas después de registrar cambios, conserva el estado recuperable y B.2 deshace lo registrado. B.5 y el modelo cubren corte después de la primera clave → guarda ocupada → rollback con restauración completa. Una salida 124 tampoco demuestra ausencia de cambios.»

2. **Bloqueante — El escritor elegido omite la protección contra un ciclo que perdió su turno.** I.3 exige el escritor de `archivar.preparar_escritor`, que construye un cliente sin el control de propiedad del ciclo. El camino vigente comprueba esa propiedad antes de cada HTTP. Tras una suspensión y recuperación del proceso, el ciclo antiguo podría enviar un PUT después de que otro ciclo tomara el control. Evidencia: [I.3, plan:158](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:158), [archivar.py:129](/Users/dn/dev/orbit-lead-auto03-plan/app/ads/archivar.py:129), [apply.py:1318](/Users/dn/dev/orbit-lead-auto03-plan/app/apply.py:1318) y [cycle.py:1163](/Users/dn/dev/orbit-lead-auto03-plan/app/cycle.py:1163). Comprobación estática; no ejecuté HTTP.

   **Texto para pegar:** «I.3 recibe el escritor del `Aplicador` del ciclo, mediante su cliente protegido, y lo pasa a `ejecuta_retiro`; conserva el heartbeat y la comprobación de propietario antes de cada HTTP. I.6 demuestra que perder el propietario después de la revalidación y antes del PUT impide enviar la mutación.»

3. **Bloqueante — Los permisos de I.1 no permiten ejecutar el retiro.** `_SQL_YA_RETIRADO` consulta `anuncio_reposicion`, cuyo SELECT sigue revocado a `app_decide`. Además, el trigger de inserción de `anuncio_retiro` consulta esa misma tabla y no es `SECURITY DEFINER`. Por tanto, arreglar solo la consulta Python no basta: el sello también falla. Evidencia: [I.1, plan:156](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:156), [retiro_anuncios.py:163](/Users/dn/dev/orbit-lead-auto03-plan/app/retiro_anuncios.py:163), [0066:59](/Users/dn/dev/orbit-lead-auto03-plan/migrations/0066_bids02_retiro_anuncios.sql:59) y [0066:100](/Users/dn/dev/orbit-lead-auto03-plan/migrations/0066_bids02_retiro_anuncios.sql:100). No ejecuté PostgreSQL.

   **Texto para pegar:** «La 0080 concede también `SELECT (retiro_id)` sobre `anuncio_reposicion` a `app_decide`, necesario para `_SQL_YA_RETIRADO` y `anuncio_retiro_sin_abierto`. La prueba de I.6 ejecuta el retiro y su reconciliación con `SET ROLE app_decide` sobre la cadena completa de migraciones; confirma el cierre de intención y cola.»

4. **Bloqueante — La simulación puede ocultar precisamente los anuncios que se activarán.** H.3 excluye de `AnuncioElegible` las claves bloqueadas. Estas incluyen propuestas `shadow` abiertas. H.8 reutiliza esas entradas, e I.8 muestra esa simulación antes de que I.7 descarte las prácticas. Caso: un anuncio elegible ya tiene propuesta de práctica → simulación muestra cero → confirmar libera la clave → el siguiente ciclo propone su pausa real. La confirmación de David muestra un alcance incorrecto. Evidencia: [H.3, plan:140](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:140), [H.8, plan:145](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:145), [I.8, plan:163](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:163) y [apply_cola.py:140](/Users/dn/dev/orbit-lead-auto03-plan/app/apply_cola.py:140). Comprobación estática.

   **Texto para pegar:** «La simulación de encendido calcula el alcance posterior al descarte de I.7: ignora únicamente los bloqueos de filas `shadow` no terminales que ese encendido descartará y conserva los vetos vigentes y bloqueos `live`. La prueba de dinero incluye un candidato con práctica abierta: aparece en la simulación y simular no modifica la cola.»

5. **Bloqueante — Una línea `CODIGO=` puede acreditar un respaldo incompleto.** B.1 la escribe antes de hacer el respaldo y prohíbe repetir ese paso si encuentra la línea. Una interrupción durante la copia deja un respaldo parcial; al reanudar se despliega encima del código anterior y se pierde una reversa completa. El modelo solo interrumpe después de terminar la copia, por lo que sus escenarios actuales no detectan este caso. Evidencia: [B.1/B.5, plan:52](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:52) y [modelo.py:74](/Users/dn/dev/orbit-lead-auto03-plan/docs/evidencia/auto-03/plan/modelo-despliegue/modelo.py:74). Comprobación estática.

   **Texto para pegar:** «El respaldo tiene un marcador de finalización escrito después de completar y verificar todos sus archivos; `CODIGO=` por sí solo no acredita un respaldo válido. Ninguna migración ni sustitución de código empieza sin ese marcador. Al reanudar un respaldo incompleto se completa antes de continuar. B.5 interrumpe durante la copia y demuestra que la posterior reversa restaura todos los archivos.»

6. **Bloqueante — La reactivación manual deja un retiro abierto que impide volver a pausar después de la gracia.** H.7 registra `reactivacion_manual`, pero no cierra el retiro anterior. A los ocho días puede generar otra propuesta; `ejecuta_retiro` encuentra el retiro abierto y omite el anuncio. Si se elimina ese salto sin resolver el ledger, el trigger rechaza el nuevo retiro. Esto contradice el comportamiento aprobado de S17. Evidencia: [H.7, plan:144](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:144), [retiro_anuncios.py:319](/Users/dn/dev/orbit-lead-auto03-plan/app/retiro_anuncios.py:319) y [0066:62](/Users/dn/dev/orbit-lead-auto03-plan/migrations/0066_bids02_retiro_anuncios.sql:62). No ejecuté PostgreSQL.

   **Texto para pegar:** «H.7 incluye el cierre auditable del retiro anterior cuando se confirma una reactivación manual en Amazon; `_SQL_YA_RETIRADO` y `anuncio_retiro_sin_abierto` consumen ese mismo cierre. I.6 comprueba retiro → reactivación manual → siete días de gracia → nuevo PUT y nuevo retiro, con los permisos reales de `app_decide`.»

7. **Nota — I.7 describe siempre escribir `live`, aunque I.8 usa la función para volver a práctica.** La firma recibe `modo`, pero el procedimiento solo desarrolla el encendido. El ejecutor debe inferir las ramas `shadow` y `off`. Referencias: [I.7–I.8, plan:162](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:162).

   **Texto para pegar:** «`cambia_pausa` persiste el `modo` solicitado; solo la rama `live` exige límites guardados y descarta prácticas. `shadow` vuelve a práctica y `off` elimina la clave. Todas las ramas validan `base_config_version_id`; repetir el modo vigente no inserta otra config.»

8. **Nota — La síntesis todavía autoriza que I encienda automáticamente.** Contradice S8 y el encabezado actualizado de I, aunque la sección final de decisiones sí los recoge. Referencia: [sintesis.md:50](/Users/dn/dev/orbit-lead-auto03-plan/docs/evidencia/auto-03/plan/sintesis.md:50).

   **Texto para pegar:** «F enciende la meta por producto; I entrega la pausa en práctica, lista para que David guarde sus límites, revise la simulación y confirme su encendido desde la pantalla.»

Confianza alta en los hallazgos estáticos; no verifiqué producción, PostgreSQL ni CI. El modelo completo no pudo ejecutarse porque el entorno impide crear temporales.