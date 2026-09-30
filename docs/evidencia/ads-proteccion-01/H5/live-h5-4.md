# H5.4 — regreso de nueve goals a live

Go del dueno en la conversacion del 2026-09-29 Vancouver, despues del
preflight de solo lectura de H5.4: `**O H5.4 live para los goals 4–12**`.
Se interpreto como el go de live solicitado expresamente para los IDs 4-12;
no incluye C.6 ni otros goals. La operacion se ejecuto el 2026-09-30
03:04:56 UTC.

Fuente de IDs: `H5/inicio.txt`,
`IDS_LIVE=6,7,4,5,11,9,10,8,12`. La sombra H5 termino 5/5 dias en
`H5/ciclo-1..5.txt`: ciclos 82-91 `done`, cero PAUSE y cero mutaciones.
No hubo candidato natural que ejercitara la nueva rama PAUSE.

Preflight del 2026-09-30 02:59-03:04 UTC: nueve goals `shadow`, cero
`live`, todos enabled; config_version 21 con
`ads_pause_sin_cooldown_bid=true`; cero `apply_queue.applied_at`,
`apply_attempt.started_at` y `decision_application.attempted_at` desde
`INICIO_SHADOW=2026-09-25T06:13:41Z`; ultimos ciclos 90 y 91 `done`.
`harvest_job` en fase no terminal = 0; `apply_queue` harvest en estado no
terminal = 0. `/health` = `{"status":"ok"}`; imagen de `orbit-app-1`
`sha256:f84b0a350b89a7e64c97a3fdc943b44d6f7c2dea372b502184d8f5b1d01ba122`
(misma imagen observada en los ciclos 4-5).

Comando ejecutado como `orbit` en la base productiva, exit 0. La transaccion
aborta si cambian los nueve goals, el flag, el estado de ciclos/cosecha o si
hubo un apply durante shadow. El SQL siguiente se paso por stdin con este
comando, sin imprimir credenciales:

```bash
ssh -T -o BatchMode=yes goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -X -A -F " | " -P pager=off -v ON_ERROR_STOP=1' <<'SQL'
```

```sql
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';
LOCK TABLE ads_optimizer_goal IN SHARE ROW EXCLUSIVE MODE;
SELECT id, scope, mode, enabled FROM ads_optimizer_goal ORDER BY id;
DO $$
BEGIN
    IF (SELECT count(*) FROM ads_optimizer_goal) <> 9
       OR (SELECT count(*) FROM ads_optimizer_goal
           WHERE id IN (6,7,4,5,11,9,10,8,12) AND mode='shadow' AND enabled) <> 9 THEN
        RAISE EXCEPTION 'H5.4 abortado: goals o modos cambiaron';
    END IF;
    IF (SELECT settings->>'ads_pause_sin_cooldown_bid'
        FROM config_version ORDER BY id DESC LIMIT 1) IS DISTINCT FROM 'true' THEN
        RAISE EXCEPTION 'H5.4 abortado: flag B.2a no esta activo';
    END IF;
    IF EXISTS (SELECT 1 FROM optimizer_cycle WHERE status='running') THEN
        RAISE EXCEPTION 'H5.4 abortado: ciclo en curso';
    END IF;
    IF EXISTS (SELECT 1 FROM harvest_job WHERE fase NOT IN ('done','failed'))
       OR EXISTS (SELECT 1 FROM apply_queue
                  WHERE kind='harvest' AND estado IN ('pending_veto','released','applying')) THEN
        RAISE EXCEPTION 'H5.4 abortado: cosecha en vuelo';
    END IF;
    IF EXISTS (SELECT 1 FROM apply_queue
               WHERE applied_at > TIMESTAMPTZ '2026-09-25 06:13:41+00')
       OR EXISTS (SELECT 1 FROM apply_attempt
                  WHERE started_at > TIMESTAMPTZ '2026-09-25 06:13:41+00')
       OR EXISTS (SELECT 1 FROM decision_application
                  WHERE attempted_at > TIMESTAMPTZ '2026-09-25 06:13:41+00') THEN
        RAISE EXCEPTION 'H5.4 abortado: hubo aplicaciones durante shadow';
    END IF;
END $$;
UPDATE ads_optimizer_goal
SET mode='live', updated_at=now()
WHERE id IN (6,7,4,5,11,9,10,8,12) AND mode='shadow'
RETURNING id, scope, mode, enabled, updated_at AT TIME ZONE 'UTC' AS updated_at_utc;
DO $$
BEGIN
    IF (SELECT count(*) FROM ads_optimizer_goal
        WHERE id IN (6,7,4,5,11,9,10,8,12) AND mode='live' AND enabled) <> 9
       OR (SELECT count(*) FROM ads_optimizer_goal WHERE mode='shadow') <> 0 THEN
        RAISE EXCEPTION 'H5.4 abortado: readback en transaccion distinto de 9 live / 0 shadow';
    END IF;
END $$;
COMMIT;
```

Salida relevante, exit 0:

```text
BEGIN
SET
SET
LOCK TABLE
id | scope | mode | enabled
4 | platform | shadow | t
5 | platform | shadow | t
6 | campaign | shadow | t
7 | campaign | shadow | t
8 | campaign | shadow | t
9 | campaign | shadow | t
10 | campaign | shadow | t
11 | campaign | shadow | t
12 | campaign | shadow | t
(9 rows)
DO
id | scope | mode | enabled | updated_at_utc
6 | campaign | live | t | 2026-09-30 03:04:56.041978
7 | campaign | live | t | 2026-09-30 03:04:56.041978
4 | platform | live | t | 2026-09-30 03:04:56.041978
5 | platform | live | t | 2026-09-30 03:04:56.041978
11 | campaign | live | t | 2026-09-30 03:04:56.041978
9 | campaign | live | t | 2026-09-30 03:04:56.041978
10 | campaign | live | t | 2026-09-30 03:04:56.041978
8 | campaign | live | t | 2026-09-30 03:04:56.041978
12 | campaign | live | t | 2026-09-30 03:04:56.041978
(9 rows)
UPDATE 9
DO
COMMIT
```

Readback independiente como `orbit_read`, `BEGIN READ ONLY` y `ROLLBACK`,
2026-09-30 03:05:08 UTC, exit 0: IDs 4-12 `live`, enabled, todos con
`updated_at=03:04:56.041978`; `mode=live` 9, `shadow` 0; flag 21 `true`;
`optimizer_cycle.status=running` 0; applies desde inicio shadow 0.
`/health` siguio `{"status":"ok"}`.

Reversa preparada para un problema antes del primer ciclo: devolver solo
los IDs de `H5/inicio.txt` a `shadow` con `WHERE id IN
(6,7,4,5,11,9,10,8,12) AND mode='live'`, verificar 9 `shadow` / 0 `live`.
Si el primer ciclo muestra PAUSE nueva no explicada, aplicar la parada y el
rollback de B.2 descritos en H5.4 del runbook; no inferir reversa de una
mutacion externa solo por el modo del goal.

Pendiente: observar los primeros ciclos live previstos a las 08:40 UTC
(US) y 08:41 UTC (MX) del 30-sep. Confirmar `done`, decisiones y applies
contra las fuentes externas y criterio de parada H5.3 antes de cerrar B.4.
Consulta preparada: `H5/primer-live-readback.sql` (rol `orbit_read`, solo lectura).
