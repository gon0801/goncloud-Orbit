# Brief para GLM: FABRICA 02 (F2) — tarea A.3

Base `origin/master` `614f659` (A.0, A.1, A.2 y A.3a cerradas; tras
`git fetch` usa el HEAD vigente). Rama desde `origin/master`, **jamás** desde
tu master local. **Una sola tarea: A.3, fase `hermanas_negadas`.**

Contrato: fila A.3 de `plans/fabrica-02.md`, §7 del spec de FABRICA y
§6.1/§7 de `docs/APPLY.md`, incluidas las precisiones A.3 del 2026-09-13. Este
brief es ejecutable y subordinado a esos documentos.

`team_validation_mode: subagent`: producto, arquitectura, seguridad, QA y una
perspectiva escéptica revisaron el alcance. La revisión encontró y cerró antes
de implementar cuatro contradicciones: tres hermanas nuevas, no cuatro; dos
barridos LIST lógicos por ciclo, no uno; `TOPE_CICLOS_HERMANAS = 3`; y reversa
reanudable con procedencia segura tras un crash.

## Resultado exacto

Cuando un harvest resuelto por grupo confirma por readback la keyword exacta:

1. sella durablemente decisión, resumen, cola `applied`, cooldown y el roster;
2. entra en `hermanas_negadas`;
3. bloquea el mismo término en los roles restantes del grupo;
4. reintenta solo pendientes, sin recobrar quota ni poner el harvest en
   `failed`;
5. termina `done`, con todas las hermanas resueltas o con pendientes explícitas
   y una alerta veraz;
6. ofrece una reversa manual, segura y reanudable.

Un grupo tiene cinco roles. `category_exact` es el destino y el origen ya fue
negado; por eso hay cuatro roles discovery cubiertos en total y **como máximo
tres identidades de hermana objetivo por job**. Cada identidad admite hasta
tres intentos POST, uno por ciclo y siempre después de un LIST que demuestre
ausencia. `product_targeting` participa: un
rechazo se trata como pendiente, no como exclusión permanente.

## Antes de escribir una línea

Lee completos, en este orden:

1. `docs/CONTEXTO.md`, reglas 1–10.
2. `plans/fabrica-02.md`: diseño de `hermanas_negadas`, fila A.3, propiedad de
   archivos, aceptación y estado siguiente.
3. `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md` §7.
4. `docs/APPLY.md` §6.1 y §7.
5. `app/apply_harvest.py` y `app/apply_harvest_reconciliacion.py` completos.
6. `app/apply.py`: `_ledger`, quota, `_cliente_reversa` y sellos.
7. `app/notifica.py`: `AlertaHarvest` y remitentes actuales.
8. `tests/test_fabrica_f2.py`, `tests/test_apply_harvest.py` y
   `tests/test_architecture.py`.

La búsqueda de memoria de proyecto no encontró una decisión anterior que
resuelva las precisiones A.3; mandan el spec, APPLY, el plan actualizado y este
brief. El repo ya tiene baseline de Ruff, pre-commit y CI.

Antes del primer rojo registra con un DSN de prueba real:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_apply_harvest.py tests/test_fabrica_f2.py \
  tests/test_harvest_destino.py tests/test_fabrica_0038.py
```

El baseline recolectado es 88 tests focales. El resultado válido debe decir
`88 passed, 0 skipped`; sin DSN, un verde con skips no sirve como evidencia.

## Decisiones cerradas

### Roster congelado

Antes del primer POST de hermana, deriva el roster por `grupo_id`, nunca por
nombre, y persístelo junto con el sello de la keyword. Excluye
`category_exact` y el rol de origen. El orden canónico de roles discovery es:

```python
ROLES_DISCOVERY = (
    "auto_discovery",
    "category_phrase",
    "category_broad",
    "product_targeting",
)
```

El estado del job al cierre con pendientes usa este contrato:

```json
{
  "hermanas_objetivo": {
    "category_phrase": {
      "campaign_id": "external-campaign-id",
      "ad_group_id": "external-ad-group-id"
    }
  },
  "hermanas": {
    "category_phrase": {"negative_id": "id", "creada": true},
    "category_broad": {"negative_id": "id", "creada": false},
    "product_targeting": {"motivo": "http_400"}
  },
  "hermanas_ciclos": 3,
  "hermanas_pendientes": {"product_targeting": "http_400"}
}
```

`hermanas_objetivo` es inmutable durante el job. Los reintentos consumen ese
roster congelado; un cambio posterior de membresía no agrega ni sustituye
objetivos. En cada `_avanza`, pasa el diccionario completo de `hermanas`, pues
el merge actual es superficial.

### Sello durable antes de hermanas

El orden es inamovible:

```text
negativo origen → keyword exacta → LIST/readback keyword → COMMIT del sello
→ fase hermanas_negadas → LIST previo → POSTs → LIST posterior
```

Antes del primer POST de hermana, otra conexión debe poder ver: keyword
confirmada, `decision_application.verify_ok`, cola `applied`, cooldown, fase
`hermanas_negadas` y roster congelado. `applied_count` aumenta exactamente una
vez y `confirmed_at` no cambia durante reintentos de higiene.

Una vez confirmado el evento de valor, ningún error de hermana, de LIST, del
gate de ancestros ni de reconciliación puede llamar el camino genérico que
pone el job o la cola en `failed`, modificar el resumen o revertir la keyword.
Si el origen deja de estar ENABLED durante la higiene, la hermana queda
pendiente con `ancestro_no_enabled`; al tope, el job termina `done` con alerta.

### LIST, identidad y truncación

"Un LIST" significa un **barrido lógico paginado**, no una sola petición HTTP.
Cada ciclo admite como máximo:

- un barrido previo, filtrado por todos los ad groups pendientes;
- un barrido posterior, también batched, después de todos los POST intentados;
  se omite si el previo no permite POST o todo ya estaba resuelto.

Está prohibido hacer un LIST por hermana. Conserva `adGroupIdFilter` en todas
las páginas. Añade un helper específico que devuelva elementos y completitud;
no rompas el contrato de `_lista_todos` para sus callers históricos. Si queda
`nextToken` al tope, el token se repite, el filtro parece ignorado o la
paginación es ambigua, el resultado es `unknown`: cero POST y motivo
`list_truncado` o `list_ambiguo`, según corresponda.

La identidad viva es plataforma/profile sellado + `adGroupId` + texto +
`NEGATIVE_EXACT`; ignora `ARCHIVED`. No uses `_solo_en_otro_ad_group` sin
corregir/probar el eje `matchType`: `NEGATIVE_PHRASE` nunca se adopta.

No se afirma que `adGroupIdFilter` esté probado en vivo. A.3 lo cubre con
`MockTransport`; cualquier señal de que Amazon no lo honra debe fallar cerrado.

### Ledger, crash y procedencia

No reutilices `_ledger` sin separar sus responsabilidades:

- el presupuesto normal cuenta solo `tipo = 'normal'`;
- todo intento nuevo usa `seq = max(seq) + 1` por decisión;
- cada POST de hermana crea antes del HTTP una fila `tipo = 'hermana'`,
  `quota_cobrada = false`, con identidad completa en `request_payload`;
- el tope es tres intentos de hermana por `(decision_id, adGroupId)`;
- tres intentos normales no bloquean hermanas ni reversas, y las hermanas no
  amplían el presupuesto normal.

Si el proceso cae después del POST y antes del sello, nada se marca
propio por inferencia. La procedencia exige coincidencia exacta entre la
ID del ACK duradero y la ID viva del readback (r4: la regla anterior
"intento abierto + hallazgo = propia" queda eliminada — atribuia ajenas):

- todo ACK aceptado con `negativeKeywordId` queda durable en el ledger
  ANTES del readback posterior (fila abierta con ack; el posterior solo
  sella el resultado), incluso si el posterior falla, trunca o ambigua;
- posterior con la id del ACK presente → `creada = true`;
- posterior con otra id → se adopta la viva (`creada = false`, jamas se
  borra) y la id del ACK queda durable para prueba tardia o reversa;
- posterior vacio o unknown → pendiente, con la id del ACK durable;
- crash sin ACK (respuesta perdida): ningun hallazgo posterior prueba
  propiedad → pendiente visible hasta el tope, jamas `creada = true`;
- en el previo, un hallazgo solo se registra propio si su id coincide con
  el ACK duradero de un intento previo (prueba tardia); sin ACK que lo
  respalde se adopta (sin intento propio) o queda pendiente (con intento
  abierto sin ack);
- toda id de ACK aceptado aun no resuelta entra al plan de reversa como
  provisional: pre-readback fail-closed (unknown detiene, ausente omite,
  viva coincidente borra con ledger/readback).
No uses `_sella_pendientes`, porque cerraría intentos
ambiguos de otras hermanas.

### Ciclos, pendientes y alerta

Fija `TOPE_CICLOS_HERMANAS = 3`. El paso inicial después del sello cuenta como
ciclo 1. Un ciclo es una invocación completa de `_paso_hermanas`: barrido
previo, como máximo un POST por identidad ausente y, si hubo POST, barrido
posterior. `hermanas_ciclos` aumenta exactamente una vez cuando el resultado
completo de esa invocación queda persistido, incluido un precheck truncado; no
aumenta por cada barrido ni por hermana. Un crash antes de ese sello atómico no
cuenta. El cap de tres intentos POST sigue siendo independiente y por hermana;
todo reintento exige primero un LIST filtrado que confirme ausencia.

Motivos mínimos y estables: `http_400`, `http_5xx`, `ack_sin_id`,
`red_ambigua`, `list_truncado`, `list_ambiguo`, `tope_intentos`,
`ancestro_no_enabled` y `pt_no_acepta_negative_keyword` cuando aplique. No
reemplaces éxitos previos al guardar pendientes.

`hermanas_pendientes` solo se escribe al cierre. Al tercer ciclo, termina
`done`, copia allí solo las pendientes y emite una `AlertaHarvest` específica.
El mensaje no puede decir "harvest failed": el evento de valor quedó aplicado. La
reconciliación debe recoger esta alerta aunque `estado == "done"`.

### Reversa manual y reanudable

Crea `tools/reversa_harvest.py --job <id>` con dry-run por defecto. La mutación
real exige juntos `--acepto-mutacion-real`, `--esperado`, huella coincidente y
`--go` no vacío. Usa `ORBIT_DSN_DECIDE` y `apply._cliente_reversa`; no construye
`AdsWriteClient`, no acepta profile/IDs arbitrarios y no usa DSN admin.

La herramienta solo acepta un job `done`, decisión confirmada y cola
`applied`. Deriva todo de la base y ejecuta en orden canónico:

```text
keyword → hermanas con creada=true → negativo de origen
```

Las ids de ACK aceptado aun no resueltas viajan en el plan como pasos
provisionales fail-closed (r4): pre-readback antes de tocarlas (unknown
detiene toda la reversa, ausente omite, viva coincidente borra con
ledger/readback); la confirmacion post-delete es por ID exacta (una
adoptada coincidente no revive al borrado).

Cada delete tiene fila pre-HTTP `tipo = 'reversa'`, sin quota, y readback
`ARCHIVED` o ausente antes de seguir. Las adoptadas (`creada = false`) nunca se
tocan. Se detiene al primer fallo. Al reejecutar, salta cada objeto cuya reversa
ya está confirmada y continúa con el siguiente; no uses el guard global
`_reversa_ya_hecha`. Nunca borra el origen mientras falte confirmar una hermana
propia. La ejecución real se ensaya en D.3 con un go nuevo del dueño; A.3 solo
usa `MockTransport`.

## Archivos y fronteras

Cambios esperados:

- `app/apply_harvest.py`: roster/sello, helper LIST filtrado, ledger de hermana,
  `_paso_hermanas`, despacho de fase y lógica compartida de reversa.
- `app/apply_harvest_reconciliacion.py`: fases en vuelo, reanudación y recogida
  de alerta `done`.
- `app/apply.py`: separar presupuesto normal, secuencia global y cap por
  hermana; conservar aquí la única fábrica de cliente mutador.
- `app/notifica.py`: sender específico y veraz para hermanas pendientes.
- `tools/reversa_harvest.py`: CLI sellada y fina sobre la lógica del módulo.
- `tests/test_fabrica_f2_hermanas.py`: comportamiento nuevo, reutilizando
  `db_f2`, `_semilla_grupo` y `_handler_harvest`.
- `tests/test_reversa_harvest.py`: orden, candados y reanudación.
- `tests/test_notifica.py`: texto y envío de la alerta `done`.
- `tests/test_apply_harvest.py`: solo regresiones transversales que no quepan
  en el fixture F2.
- `tests/test_architecture.py`: solo si hace falta ampliar un candado de
  construcción/import; no aumentes el allowlist de tamaño.

No cambies migraciones, `app/ads/write.py`, verbos HTTP, bibliotecas de A.4,
visibilidad general de A.6, tracker, `plans/ROADMAP.md` ni deploy. No arregles
en A.3 el residual preexistente de una keyword exacta adoptada que una reversa
podría confundir con propia; decláralo en el PR.

## Orden TDD obligatorio

### 1. Sello, roster y compatibilidad

Escribe rojos para demostrar el commit visible desde otra conexión antes del
primer POST; `applied_count`/`confirmed_at` estables; las tres hermanas según
cada uno de los cuatro posibles roles discovery de origen; y excepción/terna
en `exact_created → done` sin consulta de grupo, ledger ni alerta de hermanas.

### 2. LIST e identidad

Escribe rojos para filtro preservado en cada página, `ENABLED +
NEGATIVE_EXACT` adoptado, `ARCHIVED`/`NEGATIVE_PHRASE` ignorados, dos barridos
batched como máximo y truncación/ambigüedad con cero POST.

### 3. Ledger y recuperación de crash

Escribe rojos para `seq` global monotónico, presupuesto normal aislado, cap de
tres por ad group y crash POST-antes-de-sello que conserva `creada = true` sin
sellar filas ajenas.

### 4. Reintentos y cierre

Escribe rojos para 400, 5xx, red ambigua y ack sin id; merge que conserva
hermanas previas; PT como pendiente; gate de ancestro que no falla el harvest;
reanudación desde `hermanas_negadas`; SQL de jobs en vuelo y huérfanas; ciclo
3 en `done` con `hermanas_pendientes` y alerta entregada.

### 5. Reversa

Escribe rojos para mezcla propia/adoptada, orden por roles estable, readback
entre deletes, stop al primer fallo, crash después de keyword, crash después
de una hermana y reanudación sin repetir. Prueba todos los candados de CLI y
que el tool no importa ni construye el cliente de escritura.

En cada bloque: demuestra el rojo contra el código anterior, implementa el
mínimo verde y ejecuta solo los archivos focales afectados.

## Mutantes que deben morir

Como mínimo:

- cerrar `exact_created → done` para `resuelto_por = grupo`;
- confirmar la decisión después del primer POST de hermana;
- derivar roster vivo o por nombre;
- incluir exacta u origen, excluir PT o emitir una cuarta hermana;
- hacer LIST por hermana, perder el filtro o aceptar paginación truncada;
- adoptar `ARCHIVED` o `NEGATIVE_PHRASE`;
- marcar como adoptado un negativo creado por Orbit antes de un crash;
- sellar todas las filas abiertas de la decisión;
- repetir `seq`, contar hermanas como normales o usar cap global;
- reemplazar el diccionario y perder éxitos anteriores;
- fallar/revertir el harvest por una hermana o por el gate posterior;
- recobrar quota al reconciliar;
- omitir la fase nueva de cualquiera de los SQL/runtime;
- perder la alerta porque el job terminó `done`;
- borrar una adoptada, invertir el orden o repetir un delete confirmado;
- mandar un harvest de excepción/terna a la fase nueva.

## DoD binario

1. Un harvest de grupo confirma durablemente keyword, decisión, cola, cooldown,
   fase y roster antes del primer POST de hermana.
2. Los cuatro posibles roles discovery de origen dejan exactamente las otras
   tres identidades objetivo; PT participa y exacta/origen nunca se repiten.
3. Cada ciclo usa un barrido previo y como máximo uno posterior, batched,
   paginado, filtrado y fail-closed.
4. Adoptadas y propias quedan diferenciadas incluso tras crash post-POST; no se
   sella un intento ajeno.
5. Ledger tiene secuencia global única, máximo tres intentos POST por identidad,
   caps separados y una sola unidad de quota por harvest.
6. Fallos y gate posteriores al sello solo dejan pendientes; al ciclo 3 el job
   queda `done` y la alerta llega con texto veraz.
7. Reconciliación, barrido de huérfanas y creación/dedupe reconocen
   `hermanas_negadas` sin duplicar jobs.
8. Excepción/terna, veto y shadow conservan exactamente la conducta previa.
9. La reversa solo borra keyword, hermanas propias y origen, en ese orden, con
   readback, stop y reanudación; los candados de CLI impiden una mutación
   accidental.
10. Rojos previos y mutantes críticos están documentados; focales, Ruff y
    pre-commit pasan, y la batería completa pasa una sola vez en CI del PR.

## Verificación y entrega

Durante implementación:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_fabrica_f2_hermanas.py tests/test_apply_harvest.py
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_reversa_harvest.py tests/test_notifica.py
uv run --frozen python -m pytest -q tests/test_architecture.py
uv run --frozen ruff check app/apply.py app/apply_harvest.py \
  app/apply_harvest_reconciliacion.py app/notifica.py tools/reversa_harvest.py \
  tests/test_fabrica_f2_hermanas.py tests/test_reversa_harvest.py \
  tests/test_notifica.py
uv run --frozen ruff format --check app/apply.py app/apply_harvest.py \
  app/apply_harvest_reconciliacion.py app/notifica.py tools/reversa_harvest.py \
  tests/test_fabrica_f2_hermanas.py tests/test_reversa_harvest.py \
  tests/test_notifica.py
pre-commit run --all-files
```

Antes del PR, `git log origin/master..HEAD` debe mostrar solo el commit de A.3.
Abre PR a `master`; la batería completa corre **una sola vez en CI** sobre el
SHA final. No la repitas localmente si CI ya la validó.

La descripción del PR incluye: baseline y rojos, resultados focales sin skips,
shape exacto de `external_ids`, prueba de durabilidad desde segunda conexión,
secuencia HTTP y ledger, matriz crash/reanudación, prueba de reversa, salida del
log de commits, residuales y enlace al CI verde.

Cero producción, SSH, secretos o Amazon en A.3. La revisión del lead se agrupa
en una sola ronda por bloque; una segunda solo si la primera encuentra
severidad alta y nunca una tercera.
