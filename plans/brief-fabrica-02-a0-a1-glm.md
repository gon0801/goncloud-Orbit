# Brief para GLM: FABRICA 02 (F2) — tareas A.0 y A.1

Base `origin/master` `0bd6efc` (plan v1.1 con las dos sondas cerradas; tras
`git fetch` usa el HEAD vigente). Rama desde `origin/master`, **jamás** desde tu
master local. Esta es la PRIMERA entrega de código de la fase: **dos tareas,
A.0 y A.1**. Nada fuera de ellas.

Contrato: filas **A.0** y **A.1** de `plans/fabrica-02.md` y la sección
«Diseño (lo que el implementador no decide)». Este brief no reabre ninguna
decisión: el plan pasó por cinco revisiones independientes y dos sondas.

## Antes de escribir una línea

1. `plans/fabrica-02.md` completo — tu contrato son A.0, A.1 y el «Diseño».
2. `docs/CONTEXTO.md` reglas 1–10. Innegociables.
3. `docs/evidencia/fabrica-02/0.2/reporte.md` — el inventario que fijó los
   casos de prueba de A.1. En particular: **241 de 246 campañas resuelven por
   el goal de plataforma**, no por uno propio.
4. `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md` §7 y
   sus «Precisiones de FABRICA 02».

## A.0 — Banco de pruebas (no depende de nadie)

Hoy **no existe** un fixture que tenga a la vez la maquinaria de harvest y las
tablas de grupo:

- `_db_temporal` (`tests/test_apply_harvest.py:106-127`) aplica solo 0001,
  0002, 0003 y 0014 — sin `campana_grupo`, `campana_grupo_rol`,
  `keyword_biblioteca`, `negative_biblioteca` ni `harvest_excepcion`.
- `db_fabrica` (`tests/test_fabrica_migracion.py:47-67`) sí llega a 0019 pero
  no tiene `_semilla` / `_handler_harvest` / `_aplicador` / `_encola_fila`.

Construye el unificado. Precedente de cadena larga reutilizable: `_ORDEN_DB` en
`tests/test_evaluacion_catalogo.py:639-655`.

Entregable:

- Fixture con `ORDEN` = 0001, 0002, 0003, 0004, 0013–0019 (la migración de F2 es
  **A.2**, no tuya: deja el hueco preparado).
- Los helpers de harvest **reutilizados**, jamás duplicados.
- `_semilla_grupo(conn)`: grupo, 5 roles, ad groups y goals.
- Handler de LIST que **honre `adGroupIdFilter`** y `nextToken` (para simular
  truncación). El handler actual (`tests/test_apply_harvest.py:428`) devuelve el
  store entero ignorando el filtro: si lo copias, A.3 no podrá discriminar nada.
- Flag de fallo **por hermana** (por `adGroupId`); los actuales son globales por
  endpoint.

**DoD**: el fixture levanta y siembra; un test humo por helper;
`tests/test_architecture.py` verde. Sin tests de comportamiento todavía.

## A.1 — Resolutor de destino

Módulo nuevo `app/optimizer/harvest_destino.py`, con el patrón **exacto** de
`hygiene.py`: `psycopg` solo bajo `TYPE_CHECKING`, la decisión pura testeable
sin base, y el SQL en **una sola** función lectora. `test_motor_puro_sin_io`
(`tests/test_architecture.py:153-170`) prohíbe `psycopg` en runtime en todo
`app/optimizer/`. No lo metas en `hygiene.py` ni en `apply_harvest.py`.

Firma: `resolver_destino(conn, platform, campaign_ad_entity_id)`. El parámetro
es la **campaña**. Ojo con esto, que es la trampa más fácil de la tarea:
`campana_grupo_rol.ad_entity_id` y `harvest_excepcion.ad_entity_id` son
campañas, pero `decision.ad_entity_id` y `harvest_job.ad_entity_id` son el **ad
group**. Cada caller resuelve el padre, como hace `_SQL_PADRE`
(`apply_harvest.py:217-219`). Si pasas el ad group, los dos SELECT devuelven
cero filas y **todo** resuelve `sin_destino_de_harvest`.

Orden de resolución (literal en el plan):

1. `campana_grupo_rol` → rol `category_exact` da `Skip("origen_es_destino")`; si
   no, la hermana exacta del mismo grupo, asertando
   `ad_entity.platform = platform`.
2. `harvest_excepcion`.
3. **La terna VIGENTE**, por el MISMO camino que `resuelve_goal`: goal de scope
   `campaign` si existe; si no, el de `platform`. Con `migracion_pendiente`.
4. `Skip("sin_destino_de_harvest")`.

**Resolver ≠ comparar.** El paso 3 resuelve con la terna vigente venga de donde
venga. La comparación «terna vs grupo» (`destino_inconsistente`) mira **solo**
`scope = campaign`: la terna de plataforma jamás contradice al grupo, porque es
el default de la cuenta y no una decisión sobre esa campaña.

Esto no es un matiz. El plan decía «nunca la de plataforma» hasta que la sonda
0.2 lo midió: así, **241 de 246 campañas** quedaban sin cosechar el día del
deploy, incluidas las 4 únicas que cosechan de verdad.

**Cableado en CUATRO sitios**, no tres:

- `app/cycle.py:1149-1167` (`_config_harvest_de`) — y **re-apunta el dedupe**:
  esa función devuelve también
  `keywords_campana_destino(conn, platform, goal.harvest_campaign_id)`, que
  tiene que mirar el destino **resuelto**. Si no, un término cosechado se
  re-propone cada día durante ~90 días.
- `app/apply_harvest.py:1129-1141` (re-validación pre-claim).
- `app/optimizer/replay.py:129-139` (congelado).
- `app/apply_harvest.py:637-665` (`_contexto`) — **el que de verdad decide dónde
  se POSTea**. Hoy lee el goal FRESCO; si no lo tocas, congelar el destino no
  cambia nada.

**Congelado**: `_goal_json` (`app/cycle.py:643-673`) recibe el destino resuelto
y congela `resuelto_por`; la bandera `completa` se deriva del **destino
resuelto**, no de la terna del goal (si no, tras D.2 congelaría `null` y el
replay de todo harvest posterior daría `harvest_sin_config`).

**`goals_write`** gana `harvest_limpia_destino=True` (NULL en campaign y ad
group, `harvest_default_bid` intacto), validado **después** de leer la fila. Y
el candado de escritor único (`tests/test_architecture.py:347-391`) se extiende
a `tools/`, que hoy queda fuera del escaneo.

**DoD**: los nueve casos rojo-primero de la fila A.1 del plan, tal como están
escritos — no los reformules. El **(c'')** es el caso **mayoritario** de la
cuenta, no un borde: un mutante que lo mande a `sin_destino_de_harvest` debe
morir.

## Reglas de proceso (el lead las verifica)

- **TDD real**: cada test rojo ANTES del código, y el rojo se cita en el PR.
- **El lead muta tus tests.** Un test que pasa igual sin el fix cuenta como no
  entregado. Dato duro del repo: en A.5 de SP-API sobrevivían **21 de 29**
  mutantes sobre una suite entera en verde.
- `pytest_focal` con `ORBIT_TEST_DSN` apuntado: N passed, **0 skipped**. Sin DSN
  los tests de base skipean en verde y la cobertura nueva desaparece sin avisar.
- Cero `--no-verify`. Si un candado falla, se arregla el problema real.
- **No toques**: `decide_hygiene` (umbrales), `app/ads/write.py` (ningún verbo
  nuevo), las migraciones selladas, el tracker, `plans/ROADMAP.md`,
  `docs/CHAT-CONTEXT.md`. La migración de F2 es A.2.
- Cero producción, cero ssh, cero escrituras a Amazon: todo con
  `httpx.MockTransport` y base de test.
- Máximo **una** ronda de cross-review tuya. El lead revisa una vez por bloque.

## Entrega

PR a `master` con: los rojos citados, la suite focal verde con **0 skipped**, y
en la descripción **qué mutación mata cada test nuevo**. A.0 tiene que estar
verde antes de que A.1 dependa de ella; puedes entregarlas en un PR o en dos.

Si algo del plan te parece mal, **dilo en el PR y párate** — no lo cambies por
tu cuenta. Las dos veces que algo se «simplificó» en esta fase salió caro: una
transcripción que perdió un escapado dejó un token expuesto en la evidencia, y
una regla acotada de más habría apagado la cosecha de 241 campañas.
