# ORBIT 19 - Deploy final y auditoria de integracion

Fecha: 2026-09-08 UTC (2026-09-07 en America/Vancouver).

## Codigo, revision y calidad

- PR [#195](https://github.com/gon0801/goncloud-Orbit/pull/195) integrado a
  master en `eead84e777f26d5d10425984f652cfd0baa467a0` (00:17:41 UTC).
- Codigo revisado final: `46b4c74676046e2b4ba6e5ab7cf2896ca050b542`.
  Grok/Kimi: tres hallazgos corregidos; regresiones RED/GREEN y revision
  independiente APPROVE. Detalle en `../B.R/correcciones-grok-kimi.md`.
- Suite completa en [CI 34172263746](https://github.com/gon0801/goncloud-Orbit/actions/runs/34172263746):
  **1583 passed, 1 skipped, 2 warnings**. Ruff y pre-commit verdes.

## Deploy y comprobacion productiva

- Backup previo validado:
  `/mnt/data/appdata/orbit/backups/pre_pr195_20260908-001827.tgz`.
  SHA256: `dae2b496ecdaa4f2103b12ddb17fd83b83b2f044ba9a2ab0f2eb39d53165ac49`.
- Artefacto git archive del merge; SHA256 del tar:
  `426b2e1819ebb2a88cf9ff6963a4ab01f35ef8b57a6c9674f682b16425ba1765`.
- `docker compose up -d --no-deps --build app`. Solo se recreo la app:
  `45556d59aa68`. DB conserva `1af72662ec06`; bridge sin recreacion.
- Hashes SHA256 de **78 archivos** de app y herramienta en el contenedor:
  coincidencia exacta con el merge, cero diferencias.
- GET `/health`: `status=ok`; GET `/campanas/nuevas`, catalogos MX/US y
  `/api/fabrica/evaluacion?plataforma=amazon_us&objetivo=25`: HTTP 200.
- Caso US listing 1461: Ads `cost=13.2000`, `moneda=USD`; economia del ledger
  `moneda=MXN`. Se preservan las fuentes monetarias independientes.
- SELECT READ ONLY: config 16, `fabrica.creacion=v2`; 0 lotes, 0 grupos.
  Presentes los cinco triggers de moneda y append-only de Ads/disponibilidad.
  No se aplicaron migraciones ni se hicieron escrituras comerciales.

## Auditoria de Git y resguardo

- No quedaron PR abiertos tras integrar #195 (antes de este PR documental).
- Las ramas antiguas con commits no ancestrales se cotejaron contra sus PR
  integrados por squash: UI #181, assets #182, Revenue Ads #183, fotos #184,
  propuestas #162, favicon #170, FABRICA #171/#174/#176/#177,
  benchmark #175 y verificacion #189. No son implementaciones pendientes
  por el solo hecho de conservar hashes diferentes al squash.
- Revenue Ads: patch-id del rango completo y del squash #183 identicos.
  Benchmark: archivos de implementacion/pruebas iguales a master.
- Cambios sin commit resguardados antes de limpiar: pruebas temporales RED,
  formato de ejemplos del plan FABRICA, una senal local de herramienta,
  informes locales Grok/Kimi y evidencia/borradores futuros de Reputacion.
  No se incorporo SQL provisional a migrations ni se declaro A.1 implementada.
- Resguardo local fuera del checkout:
  `/Users/dn/dev/orbit-resguardos/20260908-cierre-orbit19/`.
  Incluye `resguardos.json` (worktree y SHA de stash), patches, archivos
  untracked, `refs-y-stashes.bundle` y auditoria de ramas.
  Recuperacion: `git stash apply <SHA>` desde el worktree indicado limpio.
  Las referencias historicas se conservan; no se borraron ramas con trabajo.

## Estado y limites

ORBIT 19 queda cerrado en su nucleo A+B. El manifest deja de anunciar que no
ha comenzado y registra REPUTACION 01 sin iniciar su implementacion. Su acta
0.5 sigue pendiente. Los borradores resguardados requieren revision frente
al plan oficial antes de reutilizarlos.

Siguen vigentes las limitaciones documentadas en `cierre.md`: Featured Offer
Sin verificar, cobertura Ads solo-actividad no demostrada para ausentes y
sonda comercial diferida. Este deploy no las convierte en completadas.
