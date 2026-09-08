# ORBIT 17 — Brief para Cursor: corregir cuatro hallazgos del PR #177

Corrige los cuatro hallazgos de la revision del lead en el **PR #177**,
`feat(fabrica): creacion, registro, reversa y reconciliacion (tareas 7-10)`.
Trabaja sobre la rama existente **fabrica-01-7-10-ejecucion** y actualiza ese PR.
No abras otro PR ni implementes funcionalidades adicionales.

**Commit revisado:** `d27fa806d19a39f5a5fc861b9e395cef1c9aa9e8`.
**Base:** `41bfabbf1b8def1c91f4eb57dcf0baf1f0971ad4` (#176).
CI del commit revisado: **1307 passed, 1 skipped**. Ruff y pre-commit verdes.
Los cuatro escenarios siguientes se reprodujeron con fakes, sin tocar Amazon
ni produccion. CI verde actualmente no significa que estos casos esten cubiertos.

## Preparacion y alcance

1. Lee `AGENTS.md`, `docs/CONTEXTO.md`, el spec
   `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md`,
   `plans/brief-fabrica-01-7-10-glm.md` y las tareas 7–10 de
   `plans/fabrica-01.md`. Usa el codigo actual para firmas y fixtures.
2. Inspecciona `git status --short`, rama y log; ejecuta `git fetch origin`.
   Ya existe un cambio local ajeno en `.saikit/decisiones/fabrica-01-7-10.tsv`
   y briefs sin seguimiento. Preservalos; no hagas reset, limpieza ni commits
   que los incluyan por accidente. Si la rama avanzo, verifica que cada defecto
   siga presente antes de corregirlo.
3. Archivos previstos: `tools/fabrica_campanas.py`,
   `tests/test_fabrica_campanas.py`, `plans/fabrica-01.md` y
   `docs/CHAT-CONTEXT.md`. Ajusta `tests/test_architecture.py` solo si los imports
   necesarios cambian; no relajes su frontera.
4. No cambies 0018 ni inventes estados: el esquema permite external_id/ack
   en pasos no applied; applied exige external_id + ack + readback_estado.
   Las correcciones son de la herramienta y su cobertura, no de migraciones.
5. No hagas merge/deploy, SSH, AppFlowy, llamadas a Amazon ni pruebas contra
   la base viva. El lead lleva produccion y tracker. La tarea 11 sigue pendiente.

## F1 · P1 — Registro de un lote incompleto como applied

**Ubicacion revisada:** `_SQL_CAMPANAS_APPLIED` (lineas 255–259),
`_registrar_cmd` (921–944) en `tools/fabrica_campanas.py`.

**Problema:** la consulta solo devuelve campaign y ad_group applied. La guarda
solo comprueba que esten los ad groups de los cinco roles. Si falla el product
ad o una negativa del ultimo rol, `--registrar` ignora esa parte incompleta,
crea los goals y sella applied. Tambien ignora pasos que nunca llegaron a
insertarse porque una creacion anterior detuvo el flujo.

**Reproduccion:** con plan live de un producto, lote failed y solo las diez
filas applied campaign/ad_group, `_registrar_cmd` devuelve 0, crea cinco goals
live y sella applied. Los tests actuales de reintento e idempotencia incluso
usan ese fixture incompleto como camino feliz.

**Correccion exigida:**

- Reconstruir los pasos esperados desde el plan congelado usando
  `fp.pasos_del_rol` y `fp.ROLES_ORDEN_CREACION`; no inventar otro generador.
- Cotejar el conjunto completo con el ledger, incluyendo product ads, keywords,
  targets y negativas. Exigir la identidad del paso esperado y estado applied
  con evidencia; un conteo global no distingue un recurso faltante de otro
  duplicado o ajeno. Usar el orden/rol/recurso/payload y los padres conocidos
  conforme al contrato existente, sin comparar texto JSON serializado.
- Abortos por pasos failed, planeado o ausentes ANTES de sync, registro y goals.
  No basta consultar pendientes=0: puede haber pasos nunca intentados.
- Explicar que parte falta y mantener el lote sin applied. No recrear recursos
  ni promoverlos a ciegas para completar el registro.
- Conservar el reintento del registro cuando TODOS los recursos ya estan
  verificados; conservar rechazo de lote desarmado e idempotencia del registro.

**Tests obligatorios:**

1. Ultimo product_ad failed tras cinco campaign/ad_group applied: aborta,
   cero goals nuevos y cero sello applied.
2. Negativa o semilla final planeada/fallida: mismo resultado.
3. Paso requerido nunca insertado, sin filas pendientes: tambien aborta.
4. Conjunto completo aplicado: registro exitoso e idempotente; actualizar los
   fixtures viejos para sembrar todos los pasos, no relajar la nueva guarda.
5. SQL/ledger contra Postgres REAL desechable; no conformarse con un fake que
   devuelve filas preseleccionadas sin ejecutar el filtro nuevo.

## F2 · P1 — ID confirmado perdido ante readback malformado

**Ubicacion revisada:** `_readback` (607–623), `_ejecuta_paso` (741–771) y
captura de fallos de creacion en `_mutar`.

**Problema:** el POST confirma el ID, pero no se guarda hasta DESPUES del
readback. Su parseo JSON esta fuera del try. LIST 200 con `not json` produce
JSONDecodeError, sin sello del paso/lote ni lote_detenido. Una campana ya
creada queda sin ID durable para recuperarla o pausarla.

**Fake minimo, reutilizando `_Amazon` del archivo de tests:**

```python
class AmazonReadbackMalformado(_Amazon):
    def _list(self, path, body):
        return httpx.Response(200, content=b"not json")
```

Resultado anterior: 1 campana simulada creada, 0 sellos de paso,
0 sellos de lote y 0 eventos lote_detenido.

**Correccion exigida:**

- Guardar ID y ACK confirmado, con COMMIT, ANTES del sleep/LIST. Conservar
  estado no applied hasta que el readback verifique. No falsificar readback.
- Controlar JSON invalido y shapes malformados de LIST. Ninguno puede escapar
  como excepcion cruda que evite el cierre de fallo y la evidencia.
- En esos fallos dejar paso failed CON external_id y ACK, lote failed,
  mensaje/evento con lote e ID conocido, sin ejecutar el paso siguiente.
- La reversa y reconciliacion deben conservar acceso al ID ya conocido.
  Documentar la transicion intermedia elegida con los estados existentes.
- Conservar el rechazo de readbacks validos pero divergentes de #177.

**Tests obligatorios:** POST exitoso con ID seguido de JSON invalido; raiz
JSON de tipo incorrecto; contenedor/fila de tipo incorrecto. Verificar que
ID/ACK YA son durables cuando se invoca LIST, mediante otra conexion a la base
si la prueba usa Postgres. Tras el fallo, comprobar sello failed con ID,
lote_detenido, cero POST siguientes y que el filtro de desarmar incluye esa
campana. Un mock que solo cuenta commits al final no demuestra el orden.

## F3 · P2 — ORBIT_DSN_INGEST se valida despues de crear todo

**Ubicacion revisada:** `_mutar` (950–965), `_sync` (810–816).
Spec §5.1 exige comprobar esta configuracion antes de la creacion.

**Reproduccion:** `monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)` y
camino de mutacion con `_registrar` REAL (el helper permite
`stub_registrar=False`). Crea cinco campanas/22 recursos simulados antes de
abortar con `registro interno incompleto: ORBIT_DSN_INGEST no esta en el entorno`.

**Correccion exigida:** validar presencia/valor no vacio de la configuracion
requerida al entrar al camino autorizado de `_mutar`, antes de token/lote/POST.
Reutiliza `_dsn_ingest`; no pongas esta exigencia en el dry-run ni dupliques
credenciales. Esto no pide ejecutar sync ni consultar Amazon como preflight.

**Tests obligatorios:** variable ausente y vacia abortan sin lote ni pasos
escritos y sin HTTP. Control positivo con DSN ficticio valido en los tests de
mutacion; adaptar sus helpers a la nueva precondicion. El dry-run de #176 debe
seguir funcionando sin NINGUN DSN de escritura ni credenciales de Amazon.

## F4 · P2 — HTTP 5xx declarado como rechazo definitivo

**Ubicacion revisada:** `_ejecuta_paso` (730–740).

**Problema:** solo las excepciones reciben la clasificacion INCERTO. Un POST
puede crear en Amazon y devolver 500/502/503/504; el codigo dice rechazado,
creadas=[] y conocidos=[], sin advertir que no debe repetirse.

**Fake de reproduccion:**

```python
class AmazonCreaYDevuelve500(_Amazon):
    def __call__(self, request):
        respuesta = super().__call__(request)
        if str(request.url).endswith("/sp/campaigns"):
            return httpx.Response(500, json={"error": "internal"})
        return respuesta
```

**Correccion exigida:** tratar respuestas 5xx como resultado INCERTO, conservar
ACK/status, detener el lote, declarar que Amazon pudo crear y advertir que no
se debe repetir el POST automaticamente. No inventar un external_id; sin ID,
explicar el limite de `--desarmar`. Conservar el rechazo explicito de 4xx y los
tratamientos actuales de timeout y ACK exitoso sin ID. No agregar retries.

**Tests obligatorios:** parametrizar 500/502/503/504; un solo intento de POST,
ningun recurso posterior, paso/lote failed con evidencia, mensaje INCERTO y
advertencia de no repetir. Control 400 sigue como rechazo; timeout y ACK sin
ID siguen como inciertos. No convertir toda respuesta fallida en exito.

## Evidencia y proceso

- Documenta decisiones y adaptaciones antes del codigo en el bloque de revision
  de tareas 7–10 de `plans/fabrica-01.md`.
- Cada regresion se demuestra roja contra `d27fa80` y verde con el fix. Para los
  repros F1/F2 debe fallar la asercion de seguridad que hoy falta, no un fixture
  roto por la nueva exigencia del DSN de F3. Aisla los cuatro escenarios.
- Los repros diagnosticos del lead estan en este workspace en
  `/private/tmp/orbit-177-repros.py` y `/private/tmp/orbit-177-repros.json`.
  Son evidencia auxiliar: afirman el comportamiento defectuoso y NO sustituyen
  los tests permanentes que deben exigir el comportamiento corregido. Los
  escenarios de este brief son suficientes aunque esos temporales ya no existan.
- Regla 8: usa evidencia de tablas/estados ya registrada en el plan. Si necesitas
  un SELECT nuevo de produccion, pide al lead la consulta concreta; no lo ejecutes.
- Local: rojo/verde solo del archivo de tests que cambias, con Postgres desechable
  cuando corresponda. La bateria completa corre en Quality en el PR existente;
  comprueba el resultado del ultimo SHA, no uno anterior.
- No habilites tests que toquen servicios reales, no inventes credenciales y
  no modifiques estado local ajeno. Un skip de SQL no cuenta como validacion.

```bash
.venv/bin/python -m pytest tests/test_fabrica_campanas.py -q -rs
.venv/bin/ruff check .
.venv/bin/ruff format --check tools/fabrica_campanas.py tests/test_fabrica_campanas.py
PATH="$PWD/.venv/bin:$PATH" pre-commit run --all-files
git diff --check
```

Adapta solo las rutas de ejecutables si tu venv es Windows. Si cambias
`tests/test_architecture.py`, ejecuta tambien el ciclo de ese archivo. Nunca
--no-verify ni silenciar candados para obtener verde.

Conserva las regresiones de #176 (NULL, UTC, MXN/USD, cierre y CLI por stdin),
los headers/readback del cliente real ya probados en #177, las autorizaciones,
reversa e idempotencia. No resuelvas en este PR el residual de bloquear otra
creacion por la misma huella ni otros cambios de producto no solicitados.

## Entrega

Actualiza el PR #177 con commits de correccion acotados y descripcion que
explique los cuatro resultados. Reporta por F1–F4: test rojo, fix, test verde y
residual si existe; SHA final, conteos reales y enlace a CI verde del ultimo SHA.
Alinea evidencia del plan y CHAT-CONTEXT; no afirmes sonda, merge ni deploy.

**DoD:** los cuatro defectos corregidos con regresiones permanentes, SQL contra
Postgres real, Ruff/pre-commit y suite completa en CI verdes. Si un defecto
sigue abierto, declaralo como pendiente; no cerrar con un LGTM de otro revisor.
El lead revisara de nuevo antes de autorizar el merge/deploy de #177.
