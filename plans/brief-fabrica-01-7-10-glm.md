# ORBIT 17 — Brief para GLM: tareas 7–10 de FABRICA 01

Implementa las tareas **7, 8, 9 y 10** de `plans/fabrica-01.md`, en ese orden,
en **una rama y un PR contra master**. El dueno aprobo entregar este bloque
junto: creacion, registro interno, reversa, reconciliacion y candados.
No entregues la mutacion aislada de su reversa.

Este es un brief de ejecucion del plan existente, no un rediseño. El codigo,
los tests y las interfaces detalladas estan en las cuatro tareas del plan.
Usa `superpowers:executing-plans` si esa skill esta disponible. No implementes
la tarea 11 ni F2.

## Resultado esperado

La herramienta conserva el dry-run de solo lectura y, con autorizacion explicita,
puede crear el grupo de cinco campanas, verificar cada recurso contra Amazon,
sincronizar y registrar el grupo con sus goals. Puede reintentar solo el registro,
pausar lo creado y reconciliar ejecuciones incompletas. Este PR prueba esos
caminos con HTTP simulado y Postgres desechable; la sonda real corresponde al lead.

## Base verificada

- **PR #176 fusionado y desplegado:** `41bfabbf1b8def1c91f4eb57dcf0baf1f0971ad4`.
- Tareas 1–6 implementadas; migracion 0018 ya aplicada en produccion.
- CI del PR y master: **1277 passed, 1 skipped**; Ruff y pre-commit verdes.
- Archivo actual `tests/test_fabrica_campanas.py`: **20 passed**, sin skips.
- CodeRabbit sin defecto bloqueante. Su sugerencia de comprobar el cierre de
  conexion en los abortos por fraccion y bid ya esta incorporada.
- Deploy de la herramienta verificado el 2026-09-06, 04:10 UTC. La imagen trae
  solo `app/`; el tool entra por stdin. Dry-runs reales MX/US y health correctos.
- Evidencia del lead, para referencia, sin acceder al servidor:
  `/mnt/data/appdata/orbit/backups/deploy-176-20260906T041042Z/verified.json`.
- AppFlowy: `ORBIT 17 — Fabrica de campanas por grupo (FABRICA 01)` sigue
  **In progress**; solo se cierra al terminar la tarea 11.

```bash
git fetch origin
git merge-base --is-ancestor 41bfabbf1b8def1c91f4eb57dcf0baf1f0971ad4 origin/master
git switch -c fabrica-01-7-10-ejecucion origin/master
```

Si ya existe esa rama, inspecciona su estado y retoma el trabajo propio sin
borrarla ni resetearla. Preserva cambios y archivos ajenos. Este brief sustituye
las instrucciones antiguas de una rama/PR por tarea para este bloque aprobado.

## Lectura obligatoria

1. `AGENTS.md`, `docs/CONTEXTO.md`, `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`.
2. `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md`.
3. `plans/fabrica-01.md`: restricciones globales, tareas 7–10 COMPLETAS,
   interfaces de las tareas anteriores y decisiones/evidencia de tareas 1–6.
4. Codigo actual: `tools/fabrica_campanas.py`, `app/fabrica_plan.py`,
   `app/goals_write.py`, `app/ads/client.py`, `app/ads/config.py`,
   `app/ads/structure.py`, `app/redaction.py`.
5. `migrations/0018_fabrica_campanas.sql`, `tests/test_fabrica_campanas.py`,
   `tests/test_fabrica_migracion.py`, `tests/test_fabrica_plan.py`,
   `tests/test_architecture.py`, `docs/DATABASE.md`.
6. Patrones existentes en `tools/archiva_inertes.py` y
   `tools/reactiva_campanas.py` para HTTP, vendors, autorizacion y readback.

El plan contiene ejemplos anteriores a las correcciones de #176. Las firmas
fusionadas son la referencia actual: adapta ejemplos y fakes, no reemplaces
archivos completos con bloques antiguos. Las instrucciones historicas para
implementadores de tareas 2–3 o 6 solo aplicaban a aquellas entregas.

`plans/manifest.json` incluye fabrica-01, pero su campo active aun apunta a
cortes-ui-01. La seleccion expresa del dueno para esta entrega es ORBIT 17;
no cambies otros planes ni el manifest para resolver esa diferencia.

## Archivos del PR

| Archivo | Cambio |
|---|---|
| `tools/fabrica_campanas.py` | Completar mutacion, registro, reversa, reconciliacion y argumentos. Sigue siendo UN archivo ejecutable por stdin. |
| `tests/test_fabrica_campanas.py` | Tests del bloque, HTTP simulado y SQL contra Postgres real desechable. |
| `tests/test_architecture.py` | Allowlist positiva, prohibicion del cliente de escritura, pureza y guard final. |
| `docs/DATABASE.md` | Documentar tablas, vista, roles y contratos de 0018, sin duplicar entradas existentes. |
| `docs/CHAT-CONTEXT.md` | Estado en lenguaje de negocio: implementado y pendiente de sonda, sin afirmar creaciones reales. |
| `plans/fabrica-01.md` | Decisiones antes del codigo, evidencia roja/verde y cierre de tareas 7–10. |

No modifiques migraciones ya aplicadas, contratos del nucleo ni otros escritores
para acomodar el tool. Si descubres un defecto en una dependencia, documenta
la evidencia y resuelvelo con el lead antes de ampliar el alcance.

## Secuencia y contratos de entrega

### 1. Tarea 7: mutacion y ledger

Completa las interfaces de `Task 7 / Produces`, especialmente `_mutar`, `_Ctx`,
`_valida_go`, `_post`, `_readback`, `_readback_cuadra`, `_ejecuta_rol` y los
helpers de lote/paso. Usa los payloads y constantes de `app.fabrica_plan`.

- Exige `--acepto-mutacion-real`, `--esperado 5`, huella coincidente con el plan
  recalculado y `--go` no vacio. Validacion antes de abrir el camino de mutacion.
- Perfil aceptado correspondiente a la plataforma y moneda. No mezcles MX/US.
- Orden: `category_exact`, `category_phrase`, `category_broad`,
  `product_targeting`, `auto_discovery`. Dentro del rol: campana, ad group,
  product ads y semillas. Campanas ENABLED, conforme a la decision sellada.
- Token LWA antes del lote; lote y cada paso escritos Y COMMIT antes del POST
  de mutacion correspondiente. El token no cuenta como mutacion publicitaria.
- HTTP propio con vendor v3 exacto en Content-Type y Accept, envoltura correcta,
  ids string y dinero Decimal/string. No importar `app.ads.write`.
- Readback por `AdsClient.list_objects`: exige identidad, estado, expresiones,
  bid/defaultBid y budget que correspondan al payload; un HTTP 2xx no basta.
- Ante rechazo, timeout o readback divergente: detener, conservar evidencia e
  IDs conocidos y declarar que quedo creado. No repetir un POST de creacion
  automaticamente cuando su resultado es incierto.
- Fallo del registro interno: rollback de la transaccion abortada ANTES de
  sellar el lote failed; indicar que `--registrar` recupera el registro.
- Cierra conexiones y clientes propios tambien en error. Conserva redaccion
  de secretos; no registres tokens ni DSN.

El stub de `_registrar` del ejemplo de tarea 7 es solo una etapa intermedia.
El PR final debe contener la implementacion de tarea 8; no puede sellar applied
un lote cuyo registro simplemente imprimio un mensaje.

### 2. Tarea 8: sync y registro recuperable

Implementa `_sync`, `_registrar`, `_registrar_cmd`, sus SQL y `--registrar`.

- `fetch_structure` + `sync_structure` con `ORBIT_DSN_INGEST` ANTES del registro.
  `sync_structure` sigue siendo el unico escritor de `ad_entity`.
- Resolver externos por plataforma y kind. Si sync no trajo la campana o el
  ad group esperado, abortar; no inventar entidades ni IDs.
- Registrar grupo, cinco roles y productos/listings/SKUs por el camino del plan.
  `goals_write.crea_goal` es el unico camino de creacion de goals.
- Cinco goals enabled, modo explicito del plan, moneda correcta y terna harvest
  completa apuntando a la exact del grupo, con bid-exact.
- Registro idempotente: segunda corrida y recuperacion parcial sin duplicados.
  Respetar que `crea_goal` puede dejar `row_factory=dict_row`; adaptar el consumo
  y fijar `tuple_row` donde el contrato de `_registrar_cmd` lo requiere.
- `--registrar` recupera desde el plan y los externos durables del lote; puede
  hacer LIST para sync, pero nunca vuelve a crear recursos en Amazon.
- Rechazar lotes desarmados; no reactivar metas o grupos por un reintento.

### 3. Tarea 9: reversa y reconciliacion

Implementa `_desarmar`, `_put_estado_campana`, `_reconciliar`,
`_reconciliar_cmd` y los SQL de sus interfaces.

- `--desarmar <lote>` sin go muestra el ensayo; con go pausa las campanas y
  verifica PAUSED por LIST, luego deshabilita sus goals por `edita_goal`.
- Pausar, nunca archivar. No marcar desarmado si la verificacion fallo.
- Fuente de lo creado: `fabrica_lote_paso`, incluso si nunca se registro un
  grupo. Incluye campaign applied y failed CON external_id; excluye failed
  sin external. Los joins al registro solo enriquecen con el goal existente.
- Reconciliar compara el payload del ledger contra el LIST real simulado en
  tests; promover solo lo verificado. Ausentes, divergentes y sin verificar
  deben producir salida de error explicita, no exito silencioso.
- Filtrar por lote/plataforma y usar su perfil; un lote MX no consulta ni
  cuenta pasos US. Si la plataforma se deriva del lote, ese filtro se aplica
  tambien al SQL.
- Reconciliar no es repetir creaciones ni adoptar recursos por nombre.

### 4. Tarea 10: candados y documentacion

Completa los tests de arquitectura del plan adaptados a los imports reales.
Prohibe `app.ads.write`, accesos alternativos a apply e imports dinamicos que
eludan el candado. Mantiene puro `app/fabrica_plan.py`. Todas las funciones van
ANTES del unico bloque final `if __name__ == "__main__":`.

Actualiza DATABASE, CHAT-CONTEXT y evidencia del plan. Las tareas 7–10 se marcan
completadas solo con su DoD comprobado. ORBIT 17 y la tarea 11 siguen pendientes.

## Regresiones que deben sobrevivir a este bloque

- Dry-run sin HTTP, ni OAuth/LIST, ni DSN de escritura: conserva su frontera.
- CLI por stdin y archivo, MXN y USD; ledger MXN tambien para US. El margen es
  porcentaje, no se filtra por la moneda de las pujas.
- Fechas UTC; historial `[D-105, D-15)` y exact `[D-39, D-9)`. Grano ad group,
  colapso bitemporal y desempate por source_report_id.
- Guardas por metrica: un NULL vigente mantiene desconocido orders/cost/revenue;
  no se fabrica rentabilidad mediante SUM parcial. Cero real y reobservacion
  completa siguen funcionando.
- Cierre de conexion en exito y ambos abortos de validacion de #176.
- El test que rechazaba opciones futuras era temporal de tarea 6: reemplazalo
  por tests de autorizacion y despacho de los caminos ahora implementados.
  No lo borres dejando esos caminos sin cobertura.

## Matriz minima de validacion

Usa TODOS los tests detallados en las tareas 7–10, adaptando sus fixtures al
codigo actual. Ademas de los casos anteriores, la evidencia debe demostrar:

| Camino | Evidencia necesaria |
|---|---|
| Autorizacion | Falta de esperado, valor distinto de 5, huella incorrecta y go vacio abortan antes de mutar. |
| Creacion | Orden fijo y secuencia global SQL/COMMIT/HTTP; rechazo detiene el siguiente paso; readback incorrecto conserva IDs y falla. |
| Frontera de cuenta | Perfil ausente/rechazado; MXN/USD con perfiles y moneda correctos. No usar un fake MX para probar US. |
| Registro | Postgres real; sync antes del registro; entidad ausente; cinco goals; doble corrida; recuperacion y rechazo de lote desarmado. |
| Error de registro | Rollback ocurre antes del sello failed y el error no queda como applied. |
| Reversa | Ensayo sin go; pausa verificada; fallo de readback; lote a medias sin grupo y campaign failed con external. |
| Reconciliacion | Verificado, divergente, ausente, sin verificar y aislamiento MX/US. SQL del ledger contra Postgres real. |
| Arquitectura | Import prohibido detectado, nucleo puro y main final. CLI ejecutado realmente, no solo importado. |

## Proceso y limites

1. Escribe decisiones `D-GLM-7-10-*` en el plan ANTES del codigo cuando adaptes
   una discrepancia tecnica. Resuelve firmas y fixtures con el codigo real;
   una duda de negocio o cambio de contrato se consulta al lead.
2. TDD por etapa: test, rojo, implementacion minima, verde. Conserva errores y
   comandos en la evidencia. Una regresion debe fallar contra el codigo previo;
   para reforzar cobertura de conducta ya correcta, demuestra un mutante que
   la nueva prueba detecta, sin presentarlo como un bug real anterior.
3. Postgres REAL desechable para SQL, nunca la base viva. `ORBIT_TEST_DSN` debe
   apuntar a esa instancia y permitir crear/borrar bases de test. Reutiliza los
   helpers de `test_fabrica_migracion`; un skip de esos casos no es validacion.
4. Regla 8: usa los SELECT y resultados de produccion ya registrados por el
   lead en el plan. Si introduces un invariante que exige evidencia nueva,
   pide al lead el SELECT concreto; no inventes el resultado ni accedas a prod.
5. Localmente ejecuta solo el archivo de tests que estes cambiando. La bateria
   COMPLETA corre en CI al abrir el PR, no se repite localmente. No basta push:
   debes abrir el PR y leer el resultado de Quality para el ultimo commit.
6. Ruff y pre-commit antes de cerrar; corrige fallos reales, nunca --no-verify,
   nuevos silenciamientos de lint ni relajacion de candados para lograr verde.
7. Sin SSH goncloud, Amazon real, credenciales reales, migraciones en vivo,
   cambios de cron, deploy, merge o AppFlowy. El lead lleva produccion y tracker.
8. Commit por etapa, Conventional Commits en español. Usa solo metadatos de
   autor/sesion reales; no copies atribuciones o URLs de sesiones antiguas.
9. Excluye F2, reruteo del harvest, escritura de bibliotecas por el motor,
   cambios de ventanas/umbrales, UI y sonda real. No reconstruyas tareas 1–6.

Comandos de verificacion en este entorno (adapta SOLO el ejecutable de la venv
si trabajas en Windows):

```bash
.venv/bin/python -m pytest tests/test_fabrica_campanas.py -q -rs
# Al modificar los candados, su propio ciclo rojo/verde:
.venv/bin/python -m pytest tests/test_architecture.py -q
.venv/bin/ruff check .
.venv/bin/ruff format --check tools/fabrica_campanas.py tests/test_fabrica_campanas.py tests/test_architecture.py
PATH="$PWD/.venv/bin:$PATH" pre-commit run --all-files
git diff --check
git fetch origin
git log --oneline origin/master..HEAD
```

Antes de abrir el PR, ese log debe contener SOLO commits de este bloque. Si la
base avanzo, integra los cambios necesarios conservando el trabajo ajeno. No
copiar los comandos antiguos que crean una rama nueva para cada tarea.

## Entrega al lead

PR: **`feat(fabrica): creacion, registro, reversa y reconciliacion (tareas 7-10)`**.

Entrega enlace del PR, SHA final, resumen de comportamiento, decisiones y
residuales, comandos/resultados rojos y verdes, skips explicados y enlace a
CI verde del ultimo commit. Documenta los shapes de Amazon aun no comprobados
como **HIPOTESIS hasta la sonda**, no como contratos validados en vivo.

Criterio de cierre: los cuatro caminos implementados sin stubs de exito,
regresiones de #176 conservadas, pruebas anteriores y nuevas verdes, Ruff y
pre-commit verdes, suite completa en CI y documentacion coherente. El lead
revisa, hace merge/deploy y prepara la sonda de tarea 11 con autorizacion del
conjunto concreto del dueno. Este brief no autoriza crear campanas reales.
