# Plans — ORBIT 01: Levantar Postgres en Docker y aplicar migración en vivo

> Purpose: dejar la base de datos de Orbit VIVA en el servidor `goncloud`
> (junto a bridge y accounting, como manda `docs/CONTEXTO.md`), con la
> migración `0001` aplicada, los usuarios de conexión reales creados, y el
> test de integración corriendo contra ella — hoy skipea por falta de
> Postgres local. Registro: tarea `ORBIT 01` en EHV Tasks (AppFlowy).
>
> Contexto ya verificado (sesión 2026-08-22): ningún cluster de goncloud
> tiene rastro de Orbit; puerto 5432 del host libre; Docker Compose v5.1.3;
> la migración aplica limpia contra postgres:16 en CI (11 rechazos en vivo
> verdes). Lo que falta no es validar el SQL: es una base que PERSISTA.
>
> unknown declarado: el contenido de `.env.example` no se leyó (candado
> secret-read del harness) — la tarea 1.1 lo lee tras la aprobación.
> `not_observed != absent`.

## Phase 1 — Base viva en goncloud [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Crear `/mnt/data/appdata/orbit/` en goncloud; copiar `docker-compose.yml` del repo; leer `.env.example` y crear `.env` REAL con `POSTGRES_PASSWORD` generado (>=24 chars aleatorios). El valor NUNCA se imprime, ni se committea, ni entra al repo. `[tdd:skip:infra-no-code]` | `ssh goncloud 'docker compose -f /mnt/data/appdata/orbit/docker-compose.yml config -q'` sale 0; `.env` existe con permisos 600 | - | cc:完了 |
| 1.2 | Levantar el contenedor: `docker compose up -d` en el dir de deploy. Verificar que el bind quedó SOLO en 127.0.0.1 (el compose ya lo declara así; el acceso remoto es por túnel SSH, nunca puerto abierto). `[tdd:skip:infra-no-code]` | Contenedor `Up (healthy)` o acepta `pg_isready`; `ss -lntp` en goncloud muestra 5432 SOLO en 127.0.0.1 | 1.1 | cc:完了 |
| 1.3 | Aplicar `migrations/0001_initial.sql` a la base `orbit` (piped por ssh a `psql -v ON_ERROR_STOP=1 -1`). OJO: la migración NO es re-runnable (CREATE TYPE revienta a la segunda) — si falla a medias, la transacción única revierte todo; corregir y reintentar desde cero. `[tdd:skip:infra-no-code]` | `psql` sale 0; `\dt` en base orbit lista 19 tablas; los 4 roles `app_*` existen en `pg_roles`; `SELECT count(*) FROM pg_proc WHERE proname='prohibir_mutacion'` = 1 | 1.2 | cc:完了 |
| 1.4 | Crear usuarios LOGIN por servicio (`orbit_ingest`, `orbit_decide`, `orbit_read`, `orbit_admin`) con password generado cada uno (mismo trato que 1.1) y `GRANT app_<rol>` correspondiente. Los roles `app_*` del esquema son NOLOGIN a propósito: sin este paso nadie puede conectarse. Guardar los DSN en `/mnt/data/appdata/orbit/.env` (no en el repo). `[tdd:skip:infra-no-code]` | Conectado como `orbit_read`: `SELECT` sobre `product` funciona y `UPDATE` es rechazado por permiso. Conectado como `orbit_ingest`: `INSERT` en `ingest_run` funciona | 1.3 | cc:完了 |

## Phase 2 — Verificación real [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Correr la suite COMPLETA desde la máquina dev contra la base viva, vía túnel SSH (`ssh -L 5432:127.0.0.1:5432 goncloud`) con `ORBIT_TEST_DSN=postgresql://orbit:<pass>@localhost:5432/postgres`. El test de integración crea y borra una base temporal `orbit_schema_test_*` y un rol centinela en el cluster vivo — está diseñado para eso, NO toca la base `orbit`. Trampa conocida ya mitigada: el probe habla protocolo Postgres real (un túnel muerto skipea, no cuelga). `[tdd:skip:test-ya-existe-el-punto-es-correrlo]` | `pytest -q` = **30 passed, 0 skipped** (la integración corre y el test de Windows también); las bases/roles temporales no quedan huérfanos: `pg_database` sin `orbit_schema_test_%` al terminar | 1.4 | cc:完了 |
| 2.2 | Smoke manual de candados sobre la base `orbit` REAL (no la temporal): `UPDATE fx_rate` → RestrictViolation; `TRUNCATE decision` → RestrictViolation; `SELECT * FROM fx_resolve('2026-01-01','USD','MXN')` → cero filas (sin datos, sin constante). Es el "primer chequeo real del esquema" que pide la tarea. `[tdd:skip:smoke-manual]` | Los 3 comandos dan el resultado esperado, capturados en la evidencia de AppFlowy | 1.3 | cc:完了 |

## Phase 3 — Cierre [lane:release]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Runbook `docs/DEPLOY.md`: dónde vive la base (goncloud, dir de deploy), cómo levantarla, cómo aplicar migraciones (y que 0001 NO es re-runnable), formato de los DSN por servicio (sin valores), cómo abrir el túnel para tests, y el gotcha del túnel muerto. Commitear también este `Plans.md`. `[tdd:skip:docs-only]` | PR abierto contra master con CI verde y mergeado; `docs/DEPLOY.md` responde: ¿cómo reconstruyo esto desde cero? | 2.1, 2.2 | cc:完了 |
| 3.2 | (Recommended) Backup diario: cron en goncloud con `pg_dump -Fc` de la base `orbit` a un dir con rotación >3 copias — la lección de `competitive.db` (rotación de 3 se comió el histórico). Base hoy vacía: es barato ahora, caro después. `[tdd:skip:infra-no-code]` | Cron instalado; un dump manual existe y `pg_restore --list` lo lee | 1.3 | cc:完了 |
| 3.3 | Marcar `ORBIT 01` Done en AppFlowy con evidencia completa (comandos y salidas de 1.3, 1.4, 2.1, 2.2; PR ligado). Usar el nombre EXACTO existente — renombrar desde la UI desconecta la fila del script. `[tdd:skip:tracker-only]` | Fila `ORBIT 01` en Done con notas que cuentan el trabajo completo | 3.1 | cc:完了 |
