# Revisión cruzada de Astra, pasada 2

2026-11-05. `gpt-6-astra` vía `codex exec -s read-only` sobre el diff 6c886e53..f993515b. Reporte tal cual; correcciones en `sintesis.md`.

REQUEST_CHANGES

1. **Bloqueante — H.7 prescribe un `SECURITY DEFINER` con resolución de tablas insegura.** En [plan:144](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:144), `SET search_path = public` permite que PostgreSQL busque primero tablas temporales. Un llamador con permiso TEMP puede suplantar `ad_entity_state` o `v_ad_group_impulso_abierto`, conceder acceso a `app_admin` y hacer que la función cierre un retiro real usando condiciones falsificadas.

   Comando: `grep -n -o 'SECURITY DEFINER[^:]*' plans/decisiones-producto-01.md`. Salida relevante: ``144:SECURITY DEFINER` con dueño `app_admin` y `SET search_path = public`, EXECUTE solo a `app_decide` ``. Es un hallazgo estático; no ejecuté PostgreSQL.

   **Frase exacta para corregirlo:** «`cierra_retiro_reactivado` cualifica todas las relaciones con `public.` y fija `SET search_path = pg_catalog, public, pg_temp`; revoca EXECUTE de PUBLIC y lo concede solo a `app_decide`. La prueba con ese rol crea tablas temporales homónimas y demuestra que no permiten cerrar un retiro que las tablas reales rechazan.»

2. **Bloqueante — La vista entrega candidatos que la función rechaza, abortando TX3.** [H.7:144](/Users/dn/dev/orbit-lead-auto03-plan/plans/decisiones-producto-01.md:144) selecciona cualquier retiro abierto cuyo caché diga ENABLED; solamente la función exige `modo='pausa'` y una sincronización posterior al retiro. El retiro existente sella y confirma sin actualizar ese caché ([retiro_anuncios.py:205](/Users/dn/dev/orbit-lead-auto03-plan/app/retiro_anuncios.py:205)). Basta sincronizar ENABLED, retirar desde fábrica o impulso y ejecutar un ciclo antes del siguiente sync: H.7 llama a la función y esta lanza. Un retiro `archivo` con caché ENABLED produce la misma contradicción.

   Comando: `grep -n -oE 'si no, lanza|por cada recién detectado fuera de impulso abierto' plans/decisiones-producto-01.md`. Salida:
   ```text
   144:si no, lanza
   144:por cada recién detectado fuera de impulso abierto
   ```

   **Frase exacta para corregirlo:** «La detección de reactivaciones pendientes exige los mismos predicados que el cierre: retiro abierto de modo pausa, ENABLED observado después de `retirado_el` y ausencia de impulso abierto. Un caché anterior al retiro o un retiro de modo archivo no dispara cierre ni gracia. Si las condiciones dejan de cumplirse antes de TX3, la función devuelve NULL sin escribir ni abortar el ciclo. La prueba cubre estos casos con retiros de fábrica e impulso.»

3. **Bloqueante — El nuevo cierre omite la exclusión mutua de las reposiciones.** H.7 introduce otro escritor de `anuncio_reposicion` sin exigir el candado existente. `repone` puede leer el retiro abierto, ejecutar su operación y encontrar que H.7 insertó entretanto: su INSERT falla por clave primaria, después de una posible mutación en Amazon. Si la reposición gana primero, el «si no, lanza» de H.7 aborta el ciclo.

   Comando: `grep -nE 'with _bloqueados|INSERT INTO anuncio_reposicion|retiro_id.*PRIMARY KEY' app/retiro_anuncios.py migrations/0066_bids02_retiro_anuncios.sql`. Salida relevante:
   ```text
   app/retiro_anuncios.py:461:    with _bloqueados(conn, [f"retiro-ad:{f[3]}" for f in pendientes]):
   app/retiro_anuncios.py:480:                "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo,"
   migrations/0066_bids02_retiro_anuncios.sql:44:    retiro_id        BIGINT      PRIMARY KEY REFERENCES anuncio_retiro (id),
   ```

   **Frase exacta para corregirlo:** «Antes de validar e insertar, `cierra_retiro_reactivado` toma el advisory lock transaccional de la misma llave `retiro-ad:<ad_entity_id>` que usan retiro y reposición, y relee bajo ese candado. Si el retiro ya está cerrado, devuelve su `repuesto_el` sin insertar ni cambiar actor. Una prueba concurrente con `repone` demuestra un solo cierre, sin errores de duplicidad ni pérdida de su resultado.»

Confianza alta en los hallazgos estáticos; sin ejecución de PostgreSQL. `python3 docs/evidencia/auto-03/plan/modelo-despliegue/modelo.py` quedó impedido por el entorno: `No usable temporary directory found`.