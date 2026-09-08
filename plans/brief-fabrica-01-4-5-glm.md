# ORBIT 17 — Brief para GLM: tareas 4 y 5 de FABRICA 01

Implementa las tareas **4 → 5, en serie**, de `plans/fabrica-01.md`.
Entrega una rama, un PR contra `master` y dos commits funcionales (uno por tarea).
Este brief agrupa la entrega a pedido del dueno; sustituye, para estas dos tareas,
las instrucciones del plan de abrir una rama y un PR por tarea. No cambia el diseno.

## Resultado esperado

La fabrica tendra el camino unico para crear goals de campana y el nucleo puro
que calcula el grupo de cinco campanas, sus semillas, payloads y huella.
La herramienta CLI y su ejecucion en Amazon corresponden a las tareas siguientes.

**Base comprobada al preparar este brief:** `master` local en `b6eaeea`, PR #172,
con tareas 1–3 cerradas y `migrations/0018_fabrica_campanas.sql` presente.
Antes de implementar, actualiza las referencias remotas y comprueba el estado actual.

## Lectura obligatoria

1. `AGENTS.md` y `docs/CONTEXTO.md`.
2. `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`.
3. `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md`.
4. `plans/fabrica-01.md`: Global Constraints, tareas 4 y 5 COMPLETAS,
   interfaces de tareas 6–9, y Decisiones y evidencia de tareas 1–3.
5. `app/goals_write.py`, `tests/test_goals_write.py`, `tests/test_architecture.py`,
   `app/optimizer/goals.py`, `app/optimizer/hygiene.py` y migracion 0018 real.

El plan contiene los tests y el codigo completos: usa esos bloques como punto de
partida y compruebalos contra los archivos actuales. Las correcciones de la revision
de 0018 ya estan en master; no reemplaces esa migracion con la copia del plan.

## Preparacion y limites

- Usa `superpowers:executing-plans` si esta disponible; ejecuta tu mismo ambas tareas.
- Comprueba que no haya trabajo ajeno que puedas pisar. Desde un checkout limpio:

  ```bash
  git fetch origin
  git switch -c fabrica-01-4-5-goals-nucleo origin/master
  git log -3 --oneline
  ```

- Si 0018 no esta en la base de la rama, informa la discrepancia antes de programar.
- Escribe tus decisiones `D-GLM-4-5-N` ANTES del codigo en una seccion nueva
  `Tareas 4-5 — decisiones y evidencia` dentro del plan principal.
- Usa la evidencia de produccion ya registrada en la tarea 1. Cero SSH a goncloud,
  cambios de produccion, llamadas reales a Amazon o modificaciones de AppFlowy.
  Si falta evidencia para un invariante nuevo, identifica el SELECT requerido para
  que lo ejecute el lead; continua las partes independientes.
- No cambies reglas de negocio, migraciones, dependencias ni interfaces publicadas
  para tareas 6–9. Una incompatibilidad de contrato se informa al lead; ajustes
  mecanicos de imports, formato o fixtures se resuelven y documentan.
- No implementes las tareas 6–11 ni F2. El lead lleva revision, merge, despliegue y tracker.
- Commits en espanol con Conventional Commits. No copies trailers de autoria o de
  sesion de Claude que no correspondan a tu ejecucion como GLM.

## Tarea 4 — crear goals por el camino unico

**Archivos:** `app/goals_write.py`, `tests/test_goals_write.py` y
`tests/test_architecture.py`. Este ultimo SI forma parte de la tarea: aparece en
su Step 4 aunque el listado inicial de archivos y el `git add` del plan lo omitan.

Implementa esta interfaz exacta, con las anotaciones e imports del plan:

```python
def crea_goal(
    conn: psycopg.Connection,
    *,
    ad_entity_id: int,
    target_acos_pct: Decimal,
    bid_currency: str,
    mode: str,
    harvest_campaign_id: str,
    harvest_ad_group_id: str,
    harvest_default_bid: Decimal,
    created_at: dt.datetime,
    enabled: bool = True,
) -> dict:
```

- Reusa `_valida_pre_editar`, `_fila_respuesta`, `_COLUMNAS` y
  `resuelve_floor_ceiling`; INSERT solo en `app/goals_write.py`.
- Validacion antes de SQL: `shadow|live` explicito, target y bid Decimal finitos
  y positivos, moneda con defaults, IDs harvest no vacios, terna completa y
  `created_at` obligatorio con zona horaria.
- Guarda scope campana, defaults de SU moneda, modo, enabled y ambos timestamps.
  Devuelve el mismo shape de GET /goals, dinero como strings.
- Traduce los rechazos de duplicado y kind del esquema a `GoalInvalido`, segun
  el contrato del plan. Actualiza el docstring del modulo: INSERT y UPDATE
  pertenecen a este modulo.
- Extiende el candado de arquitectura al INSERT ademas del UPDATE.

Secuencia: tests del Step 1 → rojo real → implementacion → tests verdes y candado.
Demuestra el INSERT, defaults, duplicado y rechazo de ad group con Postgres real.
Comprueba MXN y USD. Las entradas invalidas deben fallar sin ejecutar SQL.
Registra el rojo exacto y los resultados; incluye arquitectura en el commit de la 4.

## Tarea 5 — nucleo puro de la fabrica

**Archivos nuevos:** `app/fabrica_plan.py` y `tests/test_fabrica_plan.py`.

Implementa TODOS los nombres de `Interfaces / Produces` de la tarea 5:
constantes, siete dataclasses, `PlanInvalido` y funciones de validacion, target,
nombres, semillas, JSON, huella, payloads, acks y presentacion del dry-run.
Las firmas y nombres son el contrato que consumen las tareas 6–9.

- Modulo puro, sin acceso a base ni red. No importes `app.ads.write` en runtime;
  el test de correspondencia de moneda puede consultarlo, como indica el plan.
- Target = fraccion por margen MINIMO del grupo, con procedencia, clamp y
  cuantizacion del plan. Reusa banda y defaults del motor; no inventes fraccion.
- Calculos monetarios con Decimal, moneda explicita y dinero como strings en el
  JSON persistible. Revisa la frontera `monto_wire` contra la regla de dinero del
  repositorio y el patron existente; documenta cualquier conflicto antes de
  cambiar el contrato, sin introducir calculos monetarios con float.
- Mantiene separados los tres periodos: margen por producto desde 2026-02-20
  hasta D-15 exclusivo y minimo 30 dias con venta; historial/biblioteca
  `[D-105, D-15)`; candidatos exact `[D-39, D-9)`, es decir hasta D-10 inclusive.
- Semillas exact evaluadas con su agregado separado y el criterio harvest del
  motor. ASINs separados de keywords y sin negativos ASIN-like.
- Orden fijo: exact → phrase → broad → product targeting → auto.
  Payloads ENABLED, un product ad por producto usando `seller_sku`.
- Huella determinista del contenido autorizado; ida y vuelta por JSON conserva
  la huella. Prueba que cambiar bids, productos, semillas o modo la modifica.
- Mantiene los comentarios `HIPOTESIS hasta la sonda` donde el plan los exige;
  un MockTransport no confirma el contrato vivo de Amazon.
- Agrega los tests de acoplamiento contra la migracion 0018 REAL (Step 5).
  No alteres el SQL aprobado para satisfacer una comparacion textual fragil.

Secuencia: tests del Step 1 → rojo por modulo ausente → implementacion → verde →
tests de acoplamiento y comprobacion de que discriminan → commit de la 5.
No vuelvas a ramificar desde master en el Step 6: continua sobre la tarea 4.

## Verificacion y entrega

Usa el interprete Python >=3.12 del entorno del proyecto. Activa el entorno para
que `python`, `pytest` y `ruff` resuelvan sus dependencias. Para los tests de DB,
`ORBIT_TEST_DSN` debe apuntar a Postgres 16 de pruebas, con permisos para crear
las bases temporales del fixture. **Skip de un test SQL no demuestra que pasa.**

```bash
python -m pytest tests/test_goals_write.py tests/test_fabrica_plan.py tests/test_architecture.py -q
ruff check .
ruff format --check .
pre-commit run --all-files
git diff --check
git log --oneline origin/master..HEAD
```

La suite completa corre en CI segun la politica del repo; no se omiten hooks ni
se usa `--no-verify`. Si un candado falla, corrige la causa y repite el afectado.
No fijes un conteo esperado de tests: entrega el conteo real y explica los skips.

- Dos commits funcionales, uno por tarea, incluyendo sus tests y evidencia.
  Correcciones posteriores de review pueden ir en commits adicionales.
- Actualiza los markers de tareas 4 y 5 en `plans/fabrica-01.md` solo al cumplir
  sus DoD y agrega una nota en `docs/CHAT-CONTEXT.md` en lenguaje de negocio.
  ORBIT 17 completo sigue en curso: aun falta la herramienta y la sonda real.
- Un PR: `feat(fabrica): crea_goal y nucleo puro del grupo (tareas 4-5)`.
- Entrega al lead: enlace del PR, SHA base, logs rojos, resultados finales de tests,
  ruff/pre-commit, estado de CI y decisiones/desviaciones `D-GLM-4-5-N`.
  Distingue pruebas locales de cualquier comprobacion en produccion, que no se
  realiza en esta entrega.

Tras revisar y fusionar este bloque sigue la tarea 6 (lecturas y dry-run). Las
tareas 7–9 deben entregarse juntas para que la mutacion tenga reversa y
reconciliacion; la 10 completa documentacion/candados y la 11 queda a cargo del lead.
