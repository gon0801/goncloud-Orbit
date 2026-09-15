# E/D.1 — Despliegue de 0038 y del código de F2 (2026-09-15)

**Qué es esto.** Evidencia de la fila D.1 de `plans/fabrica-02.md`. El
dueño corrió el runbook §F2 de `docs/DEPLOY.md` con `!` entre las 16:35 y
las 16:41 UTC del 2026-09-15 (ventana permitida: después de las 16:00 UTC,
lejos del ciclo de las 08:40 y de las ingestas). Lo que sigue lo verificó el
lead **por readback independiente** como `orbit_read` y por HTTP de solo
lectura entre las 17:15 y las 17:35 UTC del mismo día; las salidas
literales de la terminal del dueño (el `psql -1` de la migración, la línea
`md5 OK`, el `Recreated` del compose) no están anexadas aquí — ver
«Residuales».

## SHA desplegado

- Árbol del server (`app/`, `Dockerfile`, `.dockerignore`, `pyproject.toml`,
  `uv.lock`, `tools/fabrica_campanas.py`, `tools/harvest_excepcion.py`,
  `tools/reversa_harvest.py`) comparado por md5 contra `git archive` de tres
  candidatos: **`359f1f8` idéntico (109 archivos)**; `858c767` y `7384152`
  difieren en 4 líneas (los archivos del PR #283). Es decir: lo desplegado
  es el squash del PR #280, el último de la cola cuando se corrió D.1.
- Respaldo previo del código: `predeploy-20260915-1638/` en
  `/mnt/data/appdata/orbit`. Contenedor `orbit-app-1` creado
  `2026-09-15T16:38:46Z`.
- **Consecuencia declarada:** el código en producción **no incluye el PR
  #283** (`goals set --mode`, `tools/goals_modo_grupo.py`), mergeado a las
  16:55 UTC, después del rebuild. El paso 1 de D.3 exige otro rebuild desde
  master `≥ 858c767` (hoy `7384152`) antes del 19-sep.

## Precondiciones (D.1.0), readback posterior

| consulta | resultado |
|---|---|
| `apply_queue` kind=harvest no terminales | 0 |
| `harvest_job` en vuelo (`pending`, `negative_created`, `exact_created`, `hermanas_negadas`) | 0 |
| goals del grupo 1 (`mode`, `enabled`) | 5 filas, todas `shadow` / `t` |

## Cap decidido (D.1.1)

`config_version` **19**, `2026-09-15 16:41:07 UTC`, label
`F2 D.1 caps harvest bajados: 2/2 (propuesta del lead autorizada en D.1)`:
`ads_apply_cap_amazon_mx_harvest = 2`, `ads_apply_cap_amazon_us_harvest = 2`
(tipo JSON número, igual que la fila 18), `ads_optimizer_mode = "live"`.
`/api/dashboard/salud` → `plataformas.amazon_us.quota.harvest = {cap: 2,
fuente: config_vigente}`; `amazon_mx.quota.harvest = {used: 1, cap: 5,
fuente: fila_del_dia}` porque la fila de quota de hoy nació con el cap
viejo: el 2 rige desde la fila de mañana, como declara el runbook.

## Backup del schema (D.1.2)

`/mnt/data/appdata/orbit/backups/pre0038_harvest_biblio_20260915-163550.sql`
(24 406 bytes, `-rw-------` root), del patrón staging + verificación.

## Migración 0038 (D.1.3), verificada como `orbit_read`

```
constraints presentes : attempt_tipo_valido, harvest_job_fase_check   (goal_harvest_completo ausente)
harvest_job_fase_check: CHECK (fase = ANY ('{pending,negative_created,exact_created,hermanas_negadas,done,failed}'))
attempt_tipo_valido   : CHECK (tipo = ANY ('{normal,reversa,probe,hermana}'))
harvest_job_en_vuelo  : predicado (fase = ANY ('{pending,negative_created,exact_created,hermanas_negadas}'))
triggers habilitados  : ads_optimizer_goal.ads_optimizer_goal_harvest_coherente
                        campana_grupo_rol.campana_grupo_rol_destino_protegido
GRANTs (decide_inserta_kw, decide_inserta_neg, decide_toca_updated_at, decide_borra_kw,
        decide_actualiza_neg, read_inserta_kw, decide_seq_kw, decide_seq_neg) = t,t,t,f,f,f,t,t
```

Todo coincide con el esperado literal del runbook.

## Deploy y smoke (D.1.4)

- `GET /health` → `{"status":"ok"}`; `GET /cortes` → 200.
- `GET /api/dashboard/salud` → `plataformas.amazon_mx.harvest_destino =
  {resueltos: {grupo: 4, excepcion: 0, terna: 28}, saltos_grupo: []}`;
  `amazon_us.harvest_destino = null` (US no tiene grupo). Es el «antes» de
  D.2. Nota: el snippet del runbook leía `harvest_destino` en la raíz del
  JSON y por eso imprimía `null`; corregido en este PR (el bloque vive
  dentro de `plataformas`).

## Verificación de apagado (D.1.5)

Ciclo **61** `amazon_mx`, `2026-09-15 16:41:24 UTC` (posterior al rebuild).
Conteos después del ciclo: `jobs_grupo1 = 0`, `intentos_grupo1 = 0`,
`hermanas_grupo1 = 0`, `kw_biblio = 0`, `neg_biblio = 0`. Los cinco eran 0
por construcción antes del despliegue (F2 nunca había corrido), así que
antes = después. `notes.decisiones = {}` en el ciclo: por la regla 6 el
optimizador no decide sobre las campañas del grupo 1 (nacidas el
2026-09-09) antes de diez días, de modo que la segunda lectura del gate
(«campañas del grupo saltadas por `shadow`») **no es observable hoy**:
`saltos_grupo` viene vacío porque no hubo decisión que saltar, no porque
algo saliera a HTTP. Se declara; se vuelve a leer en el primer ciclo con
decisiones del grupo (19-sep en adelante).

## Residuales

- Salidas literales de la terminal del dueño no anexadas (migración,
  `md5 OK`, `Recreated`). El estado final se verificó por readback
  independiente; si el dueño las pega en el PR, se agregan aquí.
- Vigilante del cron SP-API (PR #280): el módulo está en el contenedor,
  pero la **línea de crontab no está instalada** y la prueba del silencio
  no se corrió. Queda como pendiente aparte de D.1 (no es fila del plan).
- Producción sin el PR #283: rebuild pendiente antes de D.3 (arriba).
