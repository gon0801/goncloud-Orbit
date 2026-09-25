# Review C.4 — draft PR 342 @ 6f6838b (opus, independiente)

Revisor: `claude -p --model opus`, NO autor del cambio. Fecha: 2026-09-24.
Objeto: `origin/feat/ads-proteccion-c4` @ `6f6838b` vs `origin/master`
`f31058d`. Metodo: copia `git archive` en /tmp, `.venv` del repo,
Postgres local. (B1/B2 re-derivados en 2a pasada por corte de salida;
mismo objeto y metodo.)

## Bloqueantes

**B1 — Sin aviso Telegram de propuesta nueva.** El runbook exige aviso
con contrato pending/sent y fallo visible en /salud. El PR solo importa
`notifica` (cycle.py:189); `_persiste` no llama ni encola entrega; 0042
sin columnas de entrega; /salud intacto; sin tests de aviso/reintento.
Repro: el diff no agrega ninguna linea notifica./telegram/pending/sent.

**B2 — A1U/AU2 no quedan visibles; superficie distinta a la del runbook.**
A1U/AU2 estan PAUSED y `transicion` devuelve `mantener` sin fila previa
(propuestas_campana.py:105-106, 228-237): no hay fila, no aparecen.
El PR solo agrega GET /api/ads-optimizer/campaign-proposals (filtra
open, sin sales30d —devuelve revenue—, motivo anidado, sin close_reason);
no toca ui.py ni api_dashboard.py. Sin test de PAUSED visibles.
Repro: `transicion(None, riesgo=True, status='PAUSED', reset=False)` ->
`mantener`.

**B3 — Sin aislamiento off-by-default.** `lee_evaluaciones` (TX2) y
`guarda_evaluaciones` (TX3) corren sin condicion en todo ciclo no-off;
sin flag, al llegar el aviso (B1) el deploy mandaria mensajes. H6.2 lo
exige probado antes de merge. Repro: `grep -n propuestas_campana
app/cycle.py`. Si C.4 no lo necesita, enmendar el runbook.

## Observaciones (no bloquean)

1. TX3 a REPEATABLE READ sin prueba ni justificacion; con C.5 (descarte
   concurrente) abortaria el ciclo (SerializationFailure). Quitar o probar.
2. Test del endpoint con conexion falsa: M14/M16 sobreviven; falta
   integracion contra PG real (verificado a mano: funciona).
3. Goal sin prueba y cascada duplicada: usar
   `goals.cascada_target_acos_con_procedencia` (M6/M7 sobreviven).
4. `acos_pct` sin redondear (134.6145789694176790950984499).
5. Cruce con C.5: `paused_observed` vs `paused_external`; reactivada con
   riesgo no se repropone; ARCHIVED/goal-off/estado-viejo sin actualizar.
6. Falta `profile_id` en registro y evidencia (C.5 pide campaignId/profile).
7. 0042 sin bloque DO $$ (verificado a mano: permisos correctos).
8. CI incompleto (completa/pesada skipping en draft).

## Lo verificado OK

0042 propia sin UPDATE/DELETE/FK, un open por (campaign,risk), CHECKs,
permisos app_decide/app_read correctos, 0041 libre para A.3; cero HTTP a
Amazon (GET lectura); dinero de una fuente con vintage (M8/M9 mueren);
episodios incl. reaparicion/descarte/freeze (M10-M13/M18 mueren); ruff
limpio; 9/9 propuestas + 109 focalizadas + 128/91/355 en arbol mergeado.

Coincide con el PR ("NO lista", esperando C.2b).

VEREDICTO: NO APROBADO
