# Runbook ADS PROTECCION 01 — ejecucion del plan corregido (PR #336)

Para el lead que ejecuta el plan: este documento es tu contrato operativo.
David (el dueno) no esta y no se le pregunta nada fuera de los gos y
decisiones nombrados abajo; cada uno llega como mensaje literal citado en
evidencia. Sin ese literal, el hito no avanza.

Base: `plans/ads-proteccion-01.md` en la rama `docs/ads-proteccion-plan-correccion`
(PR #336). El plan manda en alcance, DoD y gates; este runbook concreta
comandos, parametros de observacion, superficie visible y orden de gos. Si
difieren, gana el plan y se pausa a corregir este runbook (fila 9 de "Cuando
algo se atora").
No existe `docs/runbooks/base-orbit.md`; este runbook es autocontenido y cita
`docs/DEPLOY.md` por seccion en vez de restatearlo.

Estado medido el 2026-09-24 (no inferido): B.2 mergeado en `beae9ec` (PR #332)
sin deploy verificado; PR #333 (A.2), #334 (C.2), #335 (B.3) abiertos sin merge;
PR #336 (plan corregido) abierto; ramas locales `feat/ads-proteccion-a3`,
`feat/ads-proteccion-c3`, `feat/ads-proteccion-c4` sin PR; A.3 pausado por
bloqueante repetido; rama `fix/ads-report-timeout` con 2 commits fuera de
master (`7f78345` poll 25 min en `app/ads/reports.py` + test, `e2cc267`
evidencia gaps Exact US). Las ramas `plan/ads-proteccion-01` y
`spec/ads-proteccion-01` estan abandonadas (su contenido entro por #330/#331);
no retomarlas. Ninguna pieza cuenta como integrada, desplegada o aprobada por
existir.

## Quien

- Dueno (David): emite los gos H0a, H0b, cada go de merge/deploy/live, y las
  decisiones C.0, A.3d, C.2b; ejecuta la pausa manual C.5. Nada mas.
- Lead: ejecuta los hitos en orden, verifica cada gate con el comando citado,
  guarda evidencia y pide cada go por separado. El lead es un rol, no un modelo.
- Implementadores: los que abrieron #333/#334/#335 y las ramas locales; siguen
  en sus PRs/ramas tras H0. Las ramas locales no se pushean antes de H0b
  (regla 8); C.3/C.4 no se retoman ni mergean antes de C.2b, C.5 antes del
  merge de C.4, A.3-nuevo antes de A.3d.
- Reviewer: independiente por bloque (Quality Kit regla 4); excluye a todos
  los autores efectivos del cambio. Nunca se hardcodea un nombre en `-Excluir`.

## Arranque

0.0 Posicionate: checkout limpio de `origin/master`, registra el SHA.

```bash
git fetch -q origin && git status --short | head -n 5
git rev-parse origin/master
```

Si el checkout esta sucio, se limpia o se para; nunca se ejecuta encima de
cambios sin commitear. Repos y rutas (una fila por repo; `unknown` permitido):

| repo | ruta local | default branch | ruta en destino |
| --- | --- | --- | --- |
| goncloud-Orbit | raiz de este checkout | `master` | `/mnt/data/appdata/orbit` en `goncloud` (NO es checkout git: se copia por `git archive`, DEPLOY.md paso 1) |

0.1 Define una vez por terminal (`PSQL` del runbook FABRICA 02, DEPLOY.md
seccion FABRICA 02 (F2); `PSQL_READ` corregido y probado en prod 2026-09-24:
la forma con `sh -c` del precedente pierde el `-c` porque tras `sh -c` los
args se vuelven `$0`/`$1` y nunca llegan a psql):

```bash
PSQL='docker exec -i orbit-db-1 psql -U orbit -d orbit -X -P pager=off'
PSQL_READ='docker exec -i orbit-db-1 psql "$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)" -X -P pager=off'
```

Prueba (no imprime el DSN, exito = `1 row`):

```bash
ssh goncloud "$PSQL_READ -c 'SELECT 1 AS ok;'"
```

0.2 Horas: el server esta en UTC y esas horas SON UTC (DEPLOY.md, tabla
"Crons de Orbit"). Reloj: `ssh goncloud date -u`. Cronos que no se pisan:
ingestas 06:45/07:10/07:20, ciclo Ads 08:40, precio MX 13:10, backup root 03:30.
Ventana de deploy (del runbook FABRICA 02, DEPLOY.md seccion FABRICA 02 (F2)):
09:30-15:00 UTC o despues de 16:00 UTC, excepto 13:00-13:20 UTC (margen del
runbook sobre la corrida de precios 13:10), y lejos de 05:00-07:20 y 08:40.

## Gos requeridos (ninguno preaprobado)

| # | Operacion | Gate previo | Alcance |
| --- | --- | --- | --- |
| H0a | Merge PR #336 (plan corregido) | CI verde + review sin bloqueantes | Solo el plan; no autoriza ejecutar |
| H0b | Merge del PR de este runbook | H0a mergeado; CI verde; cross-review del runbook sin bloqueantes | Solo el runbook; habilita H1-H7 |
| C.0 | Aceptar cotejo de autorizacion | Cita literal por opcion o `decision_needed` resuelto | Gate para C.2/C.3/C.4 |
| A.3d | Resolver bloqueo repetido de A.3 | Caso reproducible de ambas secuencias | Gate para reintentar A.3 |
| Merge | Merge de cada PR de codigo | DoD + review + CI sobre SHA final | #333, #335, #334, reporte-timeout (si H3 lo pide), A.3, B.2a, C.3, C.4, C.5 |
| Deploy | Deploy de `orbit-app-1` | Hito correspondiente + B.2a resuelto + backup + rollback listo | A.4; B.4/C.6 (cubre deploy + flip todos-a-shadow + encendido flag, con efecto declarado) |
| Live | Activar cortes nuevos en live | Shadow + riesgo aceptado (C.2b para C) | B.4, C.6 (cubre el flip de vuelta a live) |
| C.2b | Aceptar riesgo economico medido | Reporte C.2 con horizonte y tolerancia fijados | Gate para merge C.3/C.4 y live C |
| C.5 | Pausa manual de campana en Amazon | Propuesta concreta visible (hito C.4) | La ejecuta el dueno; Orbit solo verifica |

La autorizacion puntual del PR #329 (24-sep-2026) no se extiende a nada de
esta tabla. CI verde o `APPROVE` no sustituyen ningun go.

## Desviaciones declaradas (no son gos)

- C.5 deploy (2026-09-25): codigo C.4/C.5 + migraciones 0041-0043
  desplegados con hito C.5 + backup + rollback listo, pero sin go literal
  registrado del dueno. Evidencia en `docs/evidencia/ads-proteccion-01/H6/deploy-c5.md`.

## Prohibido

Mergear, desplegar o activar live sin su go literal; inferir permiso de la
frase "en los 3 usa lo recomendado" o de cualquier mensaje que no nombre la
operacion; desplegar cualquier SHA >= `beae9ec` antes de B.2a; tocar `bridge`
y `accounting`, los crons de accounting/root/EHV/heartbeat; cambiar targets o
parametros de goals, crear goals, cambiar presupuestos o reactivar campanas
(el flip de `mode` shadow/live de H5/H6 va con su go y se registra, no es
libre); escribir PAUSE/RESUME de campana
desde Orbit (C.5 es solo lectura + registro); sumar reportes campaign+leaf
para el mismo dinero; usar `--no-verify` o saltear candados; preguntarle al
dueno algo que este runbook ya responde.

## Reglas de trabajo

1. Un hito avanza solo con su gate en verde y su go citado; el go nombra la
   operacion y el SHA/PR exacto. Sin literal, no hay avance.
2. Deploy = DEPLOY.md paso 1 (copiar por `git archive` — aqui de
   `$APROBADO`, no de `origin/master`, para fijar el SHA; verificar md5,
   backup `app.bak-predeploy-<STAMP>`, build, `Recreated` con digest
   distinto) + precondiciones D.1.0 del runbook FABRICA 02 adaptadas (SHA en
   `$APROBADO`, backup nocturno presente; DEPLOY.md seccion FABRICA 02
   (F2)). Citar la seccion, no copiarla.
3. Rollback de codigo = `git revert` del merge en rama `revert/<hito>`,
   PR con CI, go de merge, deploy por la regla 2, y 1 ciclo Ads (08:40 UTC)
   o 1 ingesta (07:10 UTC) observados sin el comportamiento revertido.
   Rollback de datos = solo el backup previo al deploy (DEPLOY.md paso (b)
   Backup del schema, patron pre0003 adaptando tabla/STAMP).
4. Cada hito deja evidencia en `docs/evidencia/ads-proteccion-01/<hito>/`
   (comando, salida, fecha UTC, SHA): sin evidencia no hay go siguiente.
5. Bateria completa una vez por SHA final en CI; durante implementacion solo
   focalizados; `ruff check` + `ruff format` antes de cada push
   (line-length 100). Cada bug, con prueba que falla en codigo previo.
6. Espejo en AppFlowy (EHV Tasks, skill `appflowy-ehv-task`): el hito en
   curso en `In progress`, al cerrar en `Done` con notas y enlaces a PR/evidencia.
7. Telegram (fallos, atrasos, recovery, propuestas C.4): destino desde el
   cron existente, nunca pegado en el repo; contrato `pending/sent` de A.3
   para toda entrega (fallo de canal = `pending` + reintento acotado).
8. Ramas locales `feat/ads-proteccion-{a3,c3,c4}`: tras H0b, quien las tenga
   en su checkout las pushea a origin como PR **draft** para visibilidad; a
   `ready` solo cuando su gate lo permite (A.3d para A.3, C.2b para C.3/C.4).
   Un draft no satisface gates. Si el lead no las tiene, las pide al autor;
   nunca las reimplementa por su cuenta.

## Hitos

### H0a — aprobar el alcance del plan

Gate: PR #336 con CI verde y review sin bloqueantes (ver con
`gh pr checks 336` y `gh pr view 336 --json reviews`). Go literal de merge
del dueno. Tras el merge, el plan en `master` es la base y este runbook lo
cita por SHA: si el merge cambio el plan respecto a lo revisado aqui, H0b
se rehace.

### H0b — aprobar este runbook

Gate: este PR con CI verde + cross-review independiente sin bloqueantes.
Go literal de merge. Efecto: habilita H1-H7; no autoriza merge/deploy/live
de codigo (cada uno pide su go).

### H1 — C.0 cotejo de autorizacion (gate del plan)

El lead abre `docs/evidencia/ads-proteccion-01/C.0/cotejo.md` con una fila
por opcion — (1) PAUSE madura sin cooldown de BID, (2) limite economico de
hoja incl. `revenue=0` medido, (3) propuesta de campana manual — y por cada
una: cita literal del mensaje del dueno + fecha, o `decision_needed`. El
dueno resuelve cada `decision_needed` con mensaje literal. Sin las tres
citas, no hay merge de C.2 (#334) ni se retoma trabajo en C.3/C.4/C.5.

### H2 — B.2a contencion de B.2 (bloquea TODO deploy)

Hacerlo ya tras H0b; nada con SHA >= `beae9ec` se despliega antes.

1. Registrar SHA productivo y modo efectivo (solo lectura). El server no es
   checkout git: el SHA se infiere comparando md5 de testigos contra el
   candidato (metodo DEPLOY.md paso 1); los testigos incluyen los 2 archivos
   que B.2 toco (`app/cycle.py`, `app/optimizer/goals.py` — sin ellos el
   chequeo no dice si B.2 esta en prod) mas `app/ads/reports.py` (ingesta).
   Si algun testigo difiere, el SHA productivo queda `unknown` y se anota
   el `app.bak` mas reciente:

```bash
CAND=$(git rev-parse origin/master)  # o el SHA que se va a verificar
for f in app/cycle.py app/optimizer/goals.py app/ads/reports.py; do
  echo "== $f"
  ssh goncloud "md5sum /mnt/data/appdata/orbit/$f" | cut -d' ' -f1
  git show $CAND:$f | md5sum | cut -d' ' -f1
done
ssh goncloud 'ls -td /mnt/data/appdata/orbit/app.bak-predeploy-* | head -n 3'
ssh goncloud "$PSQL_READ -c \"SELECT mode, count(*) FROM ads_optimizer_goal GROUP BY mode;\""
ssh goncloud "$PSQL_READ -c \"SELECT id, scope, platform, ad_entity_id, mode, enabled
  FROM ads_optimizer_goal ORDER BY id;\""
```

2. Elegir contencion aprobada por el dueno: (a) flag off por defecto en PR
   nuevo (go de merge) con test que demuestra que el deploy no activa la
   PAUSE nueva, o (b) revert de `beae9ec` por regla 3. B.2a deja registrado
   el metodo elegido MAS los comandos literales para verificarlo y (en el
   caso (a)) para encenderlo; H4/H5/H6 usan esos literales, no los inventan.
   Sin evidencia de aislamiento, el deploy queda bloqueado (el plan lo
   exige).

### H3 — merges gate: #333 (A.2), #335 (B.3), #334 (C.2)

Orden libre entre ellos; cada merge pide su go con PR + SHA final. Gates:

- #333: DoD A.2 + review + CI. Tras merge, habilita A.3d (H4).
- #335: DoD B.3 + review + CI. Tras merge, habilita B.4 (H5).
- #334: DoD C.2 + review + CI **mas H1 cerrado** (C.2 depends C.0). Tras
  merge, habilita C.2b (H6).
- `fix/ads-report-timeout`: clasificar ANTES del deploy A.4 (toca la ingesta
  que A.4 observa). El lead decide "aporta algo distinto" con
  `git diff origin/master origin/fix/ads-report-timeout --stat` y el diff de
  `app/ads/reports.py` (`43ddd93` ya entro por otra via): si aporta, PR con
  review + CI + go de merge, y su merge precede al deploy A.4; si no, se
  abandona con registro en
  `docs/evidencia/ads-proteccion-01/H3/reporte-timeout.md`.

### H4 — A.3d, A.3 y A.4 (ingesta vigilada)

1. A.3d: el lead documenta el caso reproducible de ambas secuencias
   (recuperacion pending que impide fallo/atraso nuevo) en
   `docs/evidencia/ads-proteccion-01/A.3d/`; el dueno decide literalmente
   si se autoriza una nueva correccion. Sin go, A.3/A.4/C.6 siguen pausadas.
2. A.3: PR nuevo con DoD (incluye estados terminal/superseded, cadencia por
   episodio y chequeo 10:30 observable); go de merge.
3. A.4 deploy (go de deploy): regla 2 + B.2a resuelto + aislamiento B.2
   recomprobado en el SHA final por el metodo que B.2a dejo registrado.
   Readback diario (7 dias naturales, app en `127.0.0.1:8010` del server,
   DASHBOARD.md modelo de acceso):

```bash
ssh goncloud 'curl -s http://127.0.0.1:8010/api/dashboard/salud'
ssh goncloud "$PSQL_READ -c \"SELECT id, source, started_at, finished_at, ok,
  rows_written, rows_skipped FROM ingest_run ORDER BY id DESC LIMIT 10;\""
```

   Cierra con (i) exito principal del dia en /salud >=3 de 7 dias y cero
   falsos recovery, y (ii) si hubo fallo real, aviso + recovery observados.
   Sin fallo real en 7 dias, cierra como "desplegado, recovery pendiente de
   fallo real" con fila de seguimiento abierta; ese parcial cuenta como A.4
   satisfecha para C.6, y C.6 lo registra como riesgo (no bloquea H5/H6).

### H5 — B.4 (PAUSE sin cooldown: deploy, shadow, luego live)

El shadow corre en produccion (los goals viven ahi); el deploy va primero.
`mode` es por goal: el shadow exige TODOS los goals live en `shadow`, asi
que durante 5 dias hay cero applies en todo Ads (bids, negativos y harvest
pausados). Ese es el efecto declarado del go de deploy. Si FABRICA 02 D.3
sigue en curso (go 2 pendiente), el go nombra el acuerdo con D.3 (esperar el
go 2 o sombreamiento coordinado); sin acuerdo, H5 no arranca.

1. Pre: lista de goals live de H2. Registrar inicio del shadow y los IDs
   live (el flip de vuelta usa los IDs, nunca `WHERE mode='shadow'`: eso
   subiria a live goals que ya estaban en shadow). Guardar ambos en
   `docs/evidencia/ads-proteccion-01/H5/inicio.txt` y releerlos de ahi en
   cada terminal nueva:

```bash
INICIO_SHADOW=$(ssh goncloud date -u +%Y-%m-%dT%H:%M:%SZ); echo $INICIO_SHADOW
IDS_LIVE=$(ssh goncloud "$PSQL_READ -tA -c \"SELECT string_agg(id::text, ',') FROM ads_optimizer_goal WHERE mode='live';\""); echo $IDS_LIVE
printf 'INICIO_SHADOW=%s\nIDS_LIVE=%s\n' "$INICIO_SHADOW" "$IDS_LIVE" > docs/evidencia/ads-proteccion-01/H5/inicio.txt
source docs/evidencia/ads-proteccion-01/H5/inicio.txt  # en cada terminal nueva
```

2. Deploy (go de deploy): regla 2, mas flip de los IDs live a `shadow`
   (registrar antes/despues), mas encendido del comportamiento nuevo:

```bash
ssh goncloud "$PSQL_READ -c \"SELECT id, scope, mode, enabled FROM ads_optimizer_goal ORDER BY id;\""
ssh goncloud "$PSQL -c \"UPDATE ads_optimizer_goal SET mode='shadow', updated_at=now() WHERE id IN ($IDS_LIVE);\""
ssh goncloud "$PSQL_READ -c \"SELECT mode, count(*) FROM ads_optimizer_goal GROUP BY mode;\""
```

   El encendido depende de la contencion B.2a: caso (a) flag, con el comando
   literal que B.2a dejo registrado; caso (b) revert, re-mergear B.2 primero
   (PR + go de merge) y el rollback del paso 4 apunta al re-merge, no a
   `beae9ec`.
3. 5 ciclos Ads (5 dias): comparar candidatos PAUSE-nueva vs replay B.3.
   Parada si: (a) candidato no explicado por B.3, (b) el ciclo falla o no
   marca `done`, (c) mutacion real (cero `applied` nuevos desde el inicio;
   nadie esta en live, asi que cualquier applied es del comportamiento
   nuevo o un flip mal hecho):

```bash
ssh goncloud "$PSQL_READ -c \"SELECT count(*) AS applied_nuevos FROM apply_queue
  WHERE applied_at > '$INICIO_SHADOW';\""
```

   Evidencia por ciclo.
4. Go de live separado (flip de vuelta a `live` SOLO de `$IDS_LIVE`,
   registrado con el mismo par antes/despues):

```bash
ssh goncloud "$PSQL -c \"UPDATE ads_optimizer_goal SET mode='live', updated_at=now() WHERE id IN ($IDS_LIVE) AND mode='shadow';\""
ssh goncloud "$PSQL_READ -c \"SELECT id, mode FROM ads_optimizer_goal WHERE id IN ($IDS_LIVE) ORDER BY id;\""
```

   Rollback por regla 3 (revert `beae9ec`, o del re-merge en el caso (b)) +
   1 ciclo sin PAUSE-nueva.

### H6 — C.2b, C.3, C.4, C.5 y C.6 (proteccion economica)

1. C.2b: el dueno fija por mensaje literal, sobre el reporte C.2: horizonte
   de maduracion en dias, tolerancia en N falsos positivos, si el target
   requiere piso propio, y acepta o cambia la regla (falso positivo =
   candidato que deja de cruzar el limite con atribucion madurada al
   horizonte). Sin literal, no hay merge C.3/C.4 ni live de C.
2. C.3/C.4: PRs (uno por task) con DoD + aislamiento off por defecto
   probado antes de merge; go de merge por PR. C.4 deja A1U/AU2 visibles en
   la superficie del runbook (ver Decisiones 1) con costo, `sales30d`,
   target+procedencia, ventana, estado y motivo.
3. C.5: PR con DoD + descarte por el canal del runbook (ver Decisiones 2);
   go de merge. La pausa en Amazon la ejecuta el dueno sobre propuesta
   concreta; Orbit cierra con readback PAUSED mismo campaignId/profile.
4. C.6: mismo recorrido que H5 (INICIO_SHADOW, IDS_LIVE en su propio
   `docs/evidencia/ads-proteccion-01/H6/inicio.txt`, nunca el de H5; flip a
   `shadow` acotado a IDs con antes/despues, encendido con el literal de
   aislamiento off que C.3 dejo registrado, 5 ciclos, paradas (a)(b)(c) de
   H5.3, flip de vuelta acotado a IDs): el go de
   deploy declara el efecto cero-applies y el acuerdo con D.3 si sigue en
   curso; el go de live cita ademas el riesgo aceptado en C.2b; rollback por
   regla 3. Cierra con comparacion candidatos vs applies sin lookahead;
   registra "senal A.3 sin recovery observado" como riesgo si H4 cerro
   parcial.

### H7 — cierre

Ledger del plan (decisiones, gos con cita, SHAs, fechas UTC), AppFlowy a
`Done`, y `plans/ads-proteccion-01.md` con estados finales en PR de cierre
con CI verde (go de merge). Lo no bloqueante pendiente va a fila del plan y
se nombra en el PR; un bloqueante nunca se mergea abierto.

## Decisiones fijadas por este runbook (el plan las delega)

1. Superficie C.4: pantalla `/propuestas` (alias `/cortes`, `app/ui.py`) +
   endpoint JSON (extension de `/api/dashboard/cortes` o hermano que C.4
   agregue; la UI consume el endpoint, regla 22 de `docs/DASHBOARD.md`) +
   aviso Telegram de
   propuesta nueva con contrato `pending/sent` (entrega fallida visible en
   `/salud`, regla 7). Cierra el DoD ver A1U/AU2 ahi.
2. Canal C.5: endpoint autenticado con header `x-orbit-token`
   (`app/api_write.py`, mecanismo sellado ORBIT 04, como el veto Rechazar);
   el PR documenta rol DB usado y justifica minimo (sin GRANTs nuevos salvo
   revision). Descarte no muta Amazon.
3. Plazos: A.4 observa 7 dias (H4); B.4/C.6 shadow 5 ciclos (H5/H6).
4. Formatos: C.0/A.3d/C.2b deciden por mensaje literal citado en
   `docs/evidencia/ads-proteccion-01/<hito>/`; `decision_needed` no es go.

## Cuando algo se atora

| # | Situacion | Accion |
| --- | --- | --- |
| 1 | CI rojo en un PR | Se arregla el problema real; jamas `--no-verify`; sin verde no hay go |
| 2 | Review trae bloqueante | Ronda de correccion por bloque (Quality Kit regla 4); solo bloqueante reabre; mismo bloqueante 2 rondas = decide el dueno |
| 3 | `origin/master` avanzo sobre `$APROBADO` | No desplegar; revalidar el SHA nuevo (DoD+CI+review) y pedir go nuevo |
| 4 | B.2a no demuestra aislamiento | Deploy bloqueado; proponer revert `beae9ec` al dueno; sin go, todo release parado |
| 5 | Fallo real durante observacion A.4 | Registrar aviso+recovery como evidencia; si el aviso falla, A.4 no cierra y se abre correccion como PR nuevo con su go de merge |
| 6 | Parada en shadow H5/H6 | Congelar live; diagnosticar contra replay; reanudar o revertir solo con go nuevo |
| 7 | Dueno pide cambio de alcance | Pausar hito; PR al plan (como #336) + ajuste a este runbook; retomar tras H0a/H0b nuevos |
| 8 | Sesion/lead muere a mitad | Estado en `docs/evidencia/ads-proteccion-01/<hito>/` + AppFlowy; el siguiente lead retoma desde el ultimo hito sin go |
| 9 | Runbook contradice al plan | Gana el plan; pausar hito; PR de correccion al runbook; retomar tras merge |

## Inventario y cierre del runbook

- Alcance: ejecutar `plans/ads-proteccion-01.md` (PR #336) hasta H7. Fuera:
  budgets automaticos, pausa automatica de campana, ACoS intradia decisorio,
  pacing intradia (otro bloque), reactivar A1U/AU2, cambiar goals.
- Presupuesto: sin costo infra nuevo (mismo `orbit-app-1`/DB); quota Amazon
  Ads sin cambio (revalidaciones usan el camino existente). Si un hito agota
  quota, fila 6 (parada) y se retoma al liberarse.
- Entrega: este PR mergeado (H0b) + evidencia por hito + PR de cierre (H7).
