# Revision de arranque por Opus

Modelo comprobado: `claude-opus-5-5`. Revision de solo lectura.

**Dictamen: APPROVE_START.** M0 + M1 pueden empezar. Nada de lo que encontré cambia el programa ni pide editar antes el plan. Hay cuatro puntos que sí cambian el primer trabajo de DeepSeek y deben ir escritos en su encargo.

Tu objetivo es arrancar ORBIT 07 sobre AUTO-03 ya entregado, sin pruebas largas. Necesitabas saber si el primer encargo se puede lanzar y dónde termina. Solo leí archivos y usé `git show origin/master:` y `graphify query`. No ejecuté tests, no entré por SSH y no modifiqué nada. Tampoco pude escribir el archivo de plan, porque esta sesión solo tiene herramientas de lectura.

## Dependencias satisfechas

- **AUTO-03 está terminado, integrado y desplegado en `f86ed7dc`.** Lo dice el prearranque (`prearranque-2026-11-07/README.md:3-22`) y coincide con lo que me dijiste de PC y Mac. Con eso quedan cumplidas las casillas 31-32 del plan.
- **La revisión cruzada ya está en el plan.**
  - R1 (economía fresca en la cola) quedó en la línea 233.
  - R2 (la espera bloquea solo la pausa por cero ventas) quedó en las líneas 186-187.
  - R3 (la meta entra directa, con excepción a la rampa) quedó en la línea 229.
  - El hash del plan cambió (de `9818ff…` a `6ade42…`), lo que confirma que se ajustó después de la revisión.
- **El código de M1 en `origin/master` es el mismo que revisó el plan.** AUTO-03 no tocó `politica.py`, `caso.py`, `bid.py`, `lecturas_caso.py`, `eras.py` ni `apply.py`. Sí tocó `apply_cola.py`, `cycle.py` y `replay.py`, pero sin cambiar las piezas que M1 modifica. Por eso la sonda del Apéndice A sigue valiendo.
- **Existen en `origin/master` todos los archivos y tests que M0 y M1 nombran.**
- **El problema que M1 debe corregir está confirmado en el código:**
  - La pausa por umbral se decide antes que la venta reciente (`app/optimizer/politica.py:194` frente a `:325`).
  - La revalidación en la cola usa `bid.decide_pause` y solo la ventana madura (`app/apply_cola.py:886`). Un pedido de los últimos 9 días no frena la pausa. El caso aparte también falta: el pedido que llega durante el veto no la descarta.
  - La fila `released` bloquea toda la hoja (`app/apply_cola.py:143`, `app/cycle.py:1784`).

## Puntos que cambian el primer encargo

1. **El plan y su evidencia solo existen en esta carpeta vieja, no en `origin/master`.**
   - **Impacto.** Si el encargo copia `plans/manifest.json` de aquí, borra entradas que sí están en master: `decisiones-producto-01`, `pez-01`, `pez-02`, y el `active` vigente, que es `resumen-ads-multicanal`. Además, el plan dice que `active` conserva `fabrica-01` (`plans/orbit-07-motor-decisiones.md:62-63`), pero ese es el valor de la rama vieja (`plans/manifest.json:199`).
   - **Corrección.** Primer commit de la rama, separado del código:
     - copiar `plans/orbit-07-motor-decisiones.md` y `docs/evidencia/orbit-07-plan/` (son 48 archivos y los enlaces relativos del plan apuntan ahí);
     - añadir solo la entrada `orbit-07-motor-decisiones` al manifest de master, sin tocar `active`;
     - nombrar en el encargo la ruta del plan, porque `AGENTS.md` manda seguir `manifest.active`.
2. **El plan dice que AUTO-03 sigue "ejecutándose en E" (`plans/orbit-07-motor-decisiones.md:14-16`), y la casilla 31 pide consultar la PC.**
   - **Impacto.** DeepSeek no tiene acceso a la PC. Intentaría esa consulta o se pararía.
   - **Corrección.** El encargo debe dar las casillas 31-32 por cumplidas con el prearranque del 7 de noviembre. Si el lanzamiento ocurre otro día, la consulta la repite el orquestador, no DeepSeek.
3. **M0 pide SELECT de producción con el rol de lectura antes de sellar invariantes** (`plans/orbit-07-motor-decisiones.md:121,135`; regla 8 de `AGENTS.md`). El prearranque no midió nada de M1; `comprobaciones.json` no tiene datos de pedidos inmaduros.
   - **Impacto.** Si DeepSeek corre por API sin túnel, no puede hacer esa medición.
   - **Corrección.** Según la vía que elijas, darle un túnel con `orbit_read` o que el orquestador corra y entregue esa única consulta. Ningún otro paso de M1 necesita producción.
4. **El rendimiento de M1 pide alternar base y candidato "sobre la misma copia de M0"** (`plans/orbit-07-motor-decisiones.md:160-161,206`).
   - **Impacto.** Leído literal, obliga a sacar una copia de la base de producción. Eso va en contra de "sin pruebas largas".
   - **Corrección.** Fijar el presupuesto con la duración de los ciclos recientes en `optimizer_cycle` (una lectura). Comprobar en la PostgreSQL desechable que la lectura nueva es una consulta por lote y por plataforma, no una por hoja. Medir el primer ciclo real después de activar.

**Pistas de reutilización, no bloqueantes.** AUTO-03 I.5 ya frena solo la subida cuando hay una pausa de producto en vuelo (`app/cycle.py:2015`). Ese es el patrón para la línea 186: bloquear solo la pausa por cero ventas, no toda la hoja. La protección debe vivir en una función pura que llamen los cinco caminos:
- `politica.decide`;
- los tres `bid.decide_pause` de `cycle.py` (líneas 1923, 1945 y 2102);
- `_revalida_pause` en la cola.

El motivo nuevo de `decision_sin_aplicar` sigue el patrón del CHECK de `migrations/0045_sin_aplicar_choque_clave.sql`. La siguiente migración libre es la 0081.

## Límite exacto del primer encargo

Una sola rama desde `origin/master` `f86ed7dc`, en un checkout propio, con este contenido:

1. **Commit de documentación.** El plan, la evidencia de la revisión y la entrada del manifest, como dice el punto 1. Además, crear `docs/evidencia/orbit-07/arranque.md` (filas M1-M6 con lo heredado y lo pendiente) y `estado.md`.
2. **La parte de M0 que M1 necesita, nada más:**
   - mapa de fuentes de pedidos recientes con fecha (`lecturas_caso.py`, ventana D-9..D-1, `app/optimizer/caso.py:253`);
   - el contrato temporal solo de hojas, en `m0/contratos-temporales.md`;
   - el SELECT de lectura de pausas candidatas con pedidos recientes (`metric_date` frente a fecha de compra);
   - el presupuesto de rendimiento del punto 4.
3. **M1 completo según las líneas 169-212 del plan:**
   - la prueba de la regresión central, roja contra la base;
   - el recorrido de dos ciclos más una liberación en PostgreSQL desechable;
   - `m1/antes-despues.json`;
   - la PR contra `master` con CI verde.

Queda fuera del primer encargo:
- lo de M0 que solo sirve a otras fases: cobertura y conciliación por producto (incluida la diferencia del producto 263 en US), episodios de puja, el análisis de M3 e inventario de freeze;
- M2 a M6;
- cualquier escritura en Amazon;
- la integración y la activación, que siguen siendo tuyas.

## Decisiones de negocio para fases posteriores

M1 no necesita ninguna. El vencimiento de 10 días con ancla fija ya está fijado en el plan, y la integración que tú hagas es la autorización.

| Fase | Decisión pendiente | Fuente |
|---|---|---|
| M3/M4 | Cuánto gasto de aprendizaje por producto se acepta y cuándo se renueva | plan 141, 275-276, 501 |
| M4 | Encender la pausa por producto, hoy en práctica, desde la pantalla | prearranque 30-33; plan 343 |
| M6 | Aprobar la regla de repetición y sus parámetros, porque puede aumentar el gasto | plan 73, 397 |
| M2 | Ninguna nueva. La excepción a la rampa ya está decidida y la meta por producto ya está en vivo | plan 229; prearranque 26 |
