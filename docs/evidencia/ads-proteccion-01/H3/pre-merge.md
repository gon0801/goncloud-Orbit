# H3 — pre-verificacion de merges (2026-09-25T00:57Z, solo lectura)

Comandos base (exit 0): `gh pr view <n> --json
headRefOid,state,mergeable,reviews` + `gh pr checks <n>` + `gh api
.../pulls/<n>/{reviews,comments}` + `git diff` rama-vs-master y
rama-vs-merge-base. No se mergea nada; esto habilita los gos, no los sustituye.

## #333 — A.2 `resultado de ingesta por perfil y reporte`

- SHA final: `9378401aef16cfb103b2ea658c98504fa158e94a`, OPEN, MERGEABLE.
- CI: gate pass, rapido pass, completa/pesada skipping (carril PR),
  CodeRabbit pass. Sobre el SHA final.
- Aporte real (base `f98b30a`): `app/ads/reports.py` +136,
  `migrations/0040_ads_report_result.sql` (nueva, justificada en header:
  ingest_run agregado insuficiente), tests +178, DEPLOY +3. Master no toco
  esos archivos tras la base: merge limpio tambien en semantica.
- DoD: cubierto en sustancia — sello `global_failed` (3 aserciones),
  clock-read dentro del try con test, resultado por reporte/perfil.
- Review: bot Major (reloj fuera del try) ATENDIDO en HEAD (commit
  "sellar fallo del reloj" mueve la lectura + test). Sin reviewer humano.
- Veredicto: LISTO para go de merge (nota: review solo bot).

## #335 — B.3 `replay y version de cooldown PAUSE tras BID`

- SHA final: `4bfdf1eff8f261b669ced13d3fd2ce5b39ee508c`, OPEN, MERGEABLE.
- CI: gate pass, rapido pass, resto skipping/pass. Sobre el SHA final.
- Aporte real (base `beae9ec`): `cooldown_policy_version=pause_after_bid_v1`
  congelado en inputs (+5 cycle.py), `replay-4925.sql`, `reporte.md`, tweak
  de regresion. Sin solape con master posterior: merge limpio.
- DoD: cubierto — tabla 14-sep con candidato nuevo, decisiones antiguas
  intactas, era congelada, readback real 4925 (cola 15 applied 21-sep,
  PAUSED verificado 24-sep), sin fecha de apply contrafactual ni ahorro
  ("revalidacion futura impide fijar..."). Lo no reconstruible
  (quota/veto/estado historico) se declara como limite y no se cuenta;
  el token literal "unknown" no aparece (observacion, no bloqueante).
  El reporte dice "faltan review cruzada, CI del SHA final, shadow":
  el CI ya esta verde (posterior al reporte); cross-review y shadow
  pertenecen a B.4 por el plan corregido.
- Review: bot Minor (filtro SQL en evidencia). Sin reviewer humano.
- Veredicto: LISTO para go de merge (nota: review solo bot).

## #334 — C.2 `medir replay economico sin lookahead`

- SHA final: `97d7438838901007054272f0f6362d18f6526b0d`, OPEN, CONFLICTING.
- CI: verde sobre el SHA final, pero el SHA no mergea: CONFLICTING (el
  toque de 4 lineas a `plans/ads-proteccion-01.md` choca con #336).
- Aporte real (base `f98b30a`): `tools/replay_ads_economico.py` (nuevo),
  7 tests (vintage/lookahead, fronteras, UTC), reporte con tabla por ciclo,
  veto 48h, FP transitorio y "No se estima ahorro". Peldano: el reporte
  declara abstencion cuando no puede atribuir peldano gobernante
  ("coincidir en el numero no demuestra..."), en linea con as-of/unknown.
- Gates: H1 cerrado en sustancia (C.0 confirmado 25-sep), pero la evidencia
  vive en PR #338 aun OPEN: mergear #338 antes deja el registro en orden.
- Review: 3 bot Minors con isResolved=true (verificado por GraphQL); el de
  la frontera 3x es falso positivo del bot (el caso cost==3*esperado existe
  en tests:18-22). Pendientes del autor sin bloquear (el NO LISTO descansa
  en CONFLICTING, no en ellos).
- Veredicto: NO LISTO — rebase sobre master + resolver plan + CI de nuevo.
  Tras eso, repite esta pre-verificacion (SHA nuevo).

## #339 — B.2a `flag off para PAUSE sin cooldown de BID`

- SHA final: `58e9d043ac60fd66ae41fa5490a86d4e97ea2cc7`, OPEN, MERGEABLE.
- CI: gate pass, rapido pass, completa/pesada skipping (carril PR) +
  dispatch Quality `36080871456` sobre `58e9d043`: rapido/pesada/completa/
  gate success (2026-09-25T01:21Z). Paridad con 333/334/335.
- Aporte: flag `ads_pause_sin_cooldown_bid` fail-closed + test de
  aislamiento + literal H5 (docstring y `B.2a/flag.md`).
- DoD: cubierto — B.2 ausente de prod por md5 (3 testigos) + 9 goals live
  en `H2/medicion.md` (PR #338 OPEN); SHA productivo exacto = unknown (no
  observado, distinto de ausente). Test rojo->verde, readback registrado
  (ejecucion en deploy, no antes).
- Review: cross-review opus APROBADO CON OBSERVACIONES MENORES (ninguna
  bloquea el merge ni la contencion; obs 1-2 aplicadas doc-only tras el
  veredicto, sin bloqueantes nuevos). Sin comentarios bot.
- Veredicto: LISTO para go de merge.

## Resumen

| PR | SHA final | CI | DoD | Review | Veredicto |
| --- | --- | --- | --- | --- | --- |
| 333 | 9378401a | verde | cubierto | bot Major atendido, sin humano | LISTO |
| 335 | 4bfdf1ef | verde | cubierto (unknown implicito) | bot Minor, sin humano | LISTO |
| 334 | 97d74388 | verde pero CONFLICTING | cubierto (peldano abstencion) | 3 Minors resolved (1 FP bot) | NO LISTO: rebase |
| 339 | 58e9d043 | verde | cubierto | opus APROBADO CON OBS. MENORES | LISTO |
