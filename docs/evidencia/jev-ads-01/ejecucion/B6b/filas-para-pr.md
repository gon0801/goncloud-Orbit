# Cuerpo para la PR de cierre (B6b) — material para Claw

PR propuesta: `docs: Jev Ads B6b cierre de ledger (cc:DONE + R1-R20 + triage)`.
Regla 4 de quality-kit: el cuerpo nombra todas las filas R1-R20 y enlaza el
triage. Título de fila = texto corto para el cuerpo; el detalle completo vive
en `plans/jev-ads-01.md`, sección "Seguimientos de la revisión".

Filas cerradas en cc:DONE (TAREA 1): 0.1, 0.2 (B1 #390 afc1f3b), 1.1, 1.2
(B2 #391 224449f), 1.3, 1.4 (B3 #392 1136d78), 2.1, 2.2 (B4 #393 a470866),
2.R (B5 #394 58d1ea4). B0 #389 fa2039ee creó el plan; B6a #395 8d8cdca es el
arreglo de concurrencia del CI, fuera de tabla. 2.3, 3.1 y 3.2 siguen cc:TODO.

## Filas R1-R20

- R1 (cc:TODO, Depends: -): migración NUEVA con `created_at DEFAULT
  clock_timestamp()`; no se edita 0049; re-emite el COMMENT del encabezado y
  corrige el typo del catálogo.
- R2 (cc:TODO, Depends: -; el requisito vive en 3.1): BLOQUE DE OPERACIÓN: camino de
  producción para crear revisiones de decisiones (`--decision-id` rechazado
  hoy, faltan término y censos de origen/destino); pegamento CLI del export de
  fábrica (`/api/fabrica/export-semillas`); absorbe endurecimiento del CLI y
  el perímetro sin token de `/export-semillas` y `/asesoria/{huella}`.
- R3 (cc:TODO, Depends: R2): vigencia: destino modificado no marca la revisión
  obsoleta; va con el bloque que cree revisiones de harvest.
- R4 (cc:TODO, Depends: -): decidir la reutilización entre revisiones (el
  spec la permite; 0049 y el asesor la prohíben; cambiarla exige migración).
- R5 (cc:TODO): `tools/jev_fichas.py revocar` escribe sin `--aplicar`.
- R6 (cc:TODO): `componer` no valida término y contrato compartidos.
- R7 (cc:TODO): `registrar_ficha` sin UniqueViolation concurrente;
  `revocar_ficha` sin savepoint.
- R8 (cc:TODO): producto con 2 listings queda `ficha_ausente`.
- R9 (cc:TODO): reanudación recalcula fichas vigentes; debe retomar del
  contexto congelado.
- R10 (cc:TODO): `_exito_previo` toma el último éxito; el spec dice el primero.
- R11 (cc:TODO): `ORBIT_SECRETS_DIR` vacía cae al cwd (4 archivos);
  `or DEFAULT_SECRETS_DIR` con prueba.
- R12 (cc:TODO): `/cortes`: indicador "asesoría no disponible"; rótulo "grupo"
  en negativos y "origen/destino" solo en harvest; truncado del detalle.
- R13 (cc:TODO): vigencia: 1 consulta por listing por GET; vigilar.
- R14 (cc:TODO, Depends 2.3): partir `app/jev_ads.py` (núcleo/asesor/vista) y
  sacarlo de `ALLOWLIST_TAMANO`.
- R15 (cc:TODO): `hechos`/`listings` como generador, sin prueba.
- R16 (cc:TODO): reordenar solo `opciones`, sin prueba propia.
- R17 (cc:TODO): guarda de pureza: `__import__`/importlib, import en cuerpo de
  clase, doble rotulado, prefijo `app.` fijo, recorrido redundante.
- R18 (cc:TODO): guarda estática de GRANT multi-tabla; alinear o borrar.
- R19 (cc:DONE B6b): cifras de `B2-r1/NOTAS.md` corregidas en este PR (14 y
  32 combinadas, no 15/17).
- R20 (cc:DONE B6b): rutas `/Users/dn` anonimizadas en
  `B5-r1/reporte-g4-pantallas.txt`.

## Enlaces para el cuerpo

- Triage de los 100 hallazgos NO BLOQUEANTE/VERIFICAR de G1-G6 (B5-r1), todos
  con destino: `docs/evidencia/jev-ads-01/ejecucion/B6b/triage-no-bloqueantes.md`.
- Verificación de evidencia por fila: `docs/evidencia/jev-ads-01/ejecucion/B6b/NOTAS.md`.
