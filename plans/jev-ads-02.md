# JEV ADS 02 — de avisar a proponer y rutear

Estado: plan propuesto el 2026-10-04; espera el visto bueno del dueño. Nace
del acta 3.2 de [JEV ADS 01](jev-ads-01.md) ("seguir y ampliar"). Base de
planificación: commit `25cebe7`. `team_validation_mode: manual-pass`.

**Resultado:** Jev revisa sola cada propuesta de corte antes de que venza su
veto, señala las búsquedas ajenas que gastan sin vender y dice a qué campaña
pertenece cada búsqueda. Ningún efecto sobre Ads nace del juicio de Jev solo:
pasa por una aprobación del dueño, salvo que una medición justifique otra cosa
y el dueño selle el cambio de regla.

**Spec:** el [diseño de Jev Ads](../docs/superpowers/specs/2026-10-03-jev-ads-design.md)
sigue vigente para tipos, fichas, composición y persistencia. Este plan cambia
contratos de ese diseño (sección "Contratos"). La tarea 0.1 escribe los de las
fases de aviso en un spec delta antes de cualquier código; cada efecto sobre
Ads tiene además su propia fila de spec y sello (1.4, 2.5, 3.1, 4.3).
Precedencia: `docs/CONTEXTO.md` > `docs/APPLY.md` > diseño Jev Ads y su delta >
este plan.

**Alcance:** las cinco ampliaciones que el dueño pidió el 2026-10-04. Reseñas,
generación de keywords nuevas y cambios a los umbrales económicos del motor
quedan fuera.

## Tamaño medido

Lectura de producción del 2026-10-04 con `orbit_read`; consulta y salida en
`docs/evidencia/jev-ads-02/planificacion/`. La ventana es la de cortes del
motor: 30 días que terminan 10 días atrás. "Ficha vigente" se cuenta como la
cuenta el asesor: sin revocar, sin vencer y que cubra ese listing.

| Dato | Amazon MX | Amazon US |
| --- | ---: | ---: |
| Pares grupo-búsqueda de texto con gasto en la ventana | 622 | 774 |
| De esos, sin venta | 597 | 748 |
| Sin venta y con 3 clics o más | 161 | 143 |
| Parte del gasto de búsquedas que fue a pares sin venta | 44.9% | 51.0% |
| Parte del gasto que fue a pares sin venta con 3 clics o más | 33.2% | 39.7% |
| Anuncios ENABLED / sin producto ligado | 4,773 / 1,168 | 4,090 / 920 |
| Productos con anuncio ENABLED / con ficha que cubre todos sus anuncios | 249 / 11 | 119 / 66 |
| Grupos con anuncios ENABLED / con todos sus anuncios ligados a producto | 33 / 32 | 48 / 24 |
| Grupos ligados y con ficha de todos sus productos | 5 | 23 |
| Productos por grupo: mediana / máximo | 50 / 237 | 71 / 91 |
| Llamadas para evaluar las búsquedas sin venta con 3 clics o más (hoy / si se reutilizan pares entre grupos) | 15,282 / 12,161 | 3,976 / 3,239 |

- **La cola de cortes es chica.** En toda su historia en live entraron 4
  `negative` (2 aplicados, 2 descartados) y 7 harvest (6 aplicados, 1 vetado);
  en sombra hubo 4 harvest más (2 vetados, 2 descartados). El histórico de
  decisiones tiene 52 `negative` sobre 18 pares grupo-búsqueda distintos (16 en
  US, 2 en MX) y 11 harvest sobre 10 pares (8 en MX, 2 en US).
- **El gasto está en las búsquedas que el motor todavía no propone.** "Sin
  venta en la ventana" no significa ajena: Jev sirve para separar cuáles lo
  son.
- **Costo.** El piloto midió 1,215 tokens por par en MX, 966 en US y 259 ms de
  mediana. Una primera pasada de la opción 2 con el contrato de hoy son unos
  18.6 M de tokens en MX y 3.8 M en US (14.8 M y 3.1 M si se reutilizan pares).
  La evidencia del diseño anota un precio de documentación de 0.042 USD por
  millón de tokens de entrada, con la salida gratis
  (`docs/evidencia/jev-ads-01/how-why.md`). Con ese precio el piloto costó unos
  0.03 USD y esa primera pasada menos de 1 USD. Falta confirmarlo y versionarlo
  con fuente y fecha (tarea 0.3).
- **Antecedente de la opción 1.** Las tres propuestas `negative` reales del
  piloto eran sobre búsquedas que sí corresponden a los productos del grupo
  (135 de 135 pares `satisface`).
- **La opción 5 hoy no tiene qué filtrar.** `keyword_biblioteca` y
  `negative_biblioteca` están vacías. Las semillas de la fábrica salen de
  términos que ya vendieron, y el export actual sólo manda a Jev los términos
  de biblioteca (`app/fabrica_web.py`, `terminos_a_cotejar`).

## Contratos

**Se heredan de JEV ADS 01 sin cambio:**

- El juicio recibe sólo el término literal y una versión de ficha. Tres
  valores: `satisface`, `no_satisface`, `informacion_insuficiente`. Un fallo
  del proveedor es fallo, nunca evidencia.
- `cycle.py`, `apply_cola.py` y `apply_harvest.py` no importan al asesor ni a
  sus módulos; la guarda `test_los_consumidores_no_importan_al_asesor` sigue
  en pie y se amplía a los módulos nuevos de Jev, a `app/optimizer/` y a
  `apply_harvest_reconciliacion.py`.
- En las fases de aviso (1.1 a 1.3, 2.1 a 2.4, 4.1, 4.2), un fallo o apagado de
  Jev deja igual la decisión y su aplicación, incluido el veto de 48 horas.
- El rol `app_jev` no gana permisos: no lee ni escribe decisiones, cola,
  ledger, goals ni bibliotecas. El job lee esas tablas con el login de
  lectura que ya existe y escribe revisiones con un login propio de `app_jev`.
- Un "ninguno corresponde" de grupo exige universo exhaustivo con ficha y
  juicio vigentes de cada miembro. `censo_grupo` entrega `exhaustivo=False`
  hasta que la tarea 0.4 pruebe lo contrario, grupo por grupo.
- Revisiones y eventos append-only. GET no escribe ni llama a TypeSafe.
- Un negativo de ruteo no alimenta `negative_biblioteca`. ASIN-like queda
  fuera del clasificador.
- La madurez no cambia (regla 6): todo bloqueo usa la ventana de cortes con 10
  días de madurez. "Temprano" quiere decir antes del umbral de clics y gasto,
  no antes de los 10 días.

**Cambian con este plan:**

- **Jev corre sola** (spec delta 0.1). Un job propio, fuera del ciclo, llama a
  TypeSafe sin que nadie lo pida y retoma solo una revisión a medias. JEV ADS
  01 decía "ninguna tarea automática llama a Jev" y "V1 no reintenta
  automáticamente". Límite nuevo: tope diario de llamadas en config,
  fail-closed.
- **Una aprobación del dueño puede crear un bloqueo o un ruteo** (2.5, 3.1).
  El bloqueo aprobado no cumple el umbral económico, así que no puede ir "por
  el camino de siempre": la liberación de hoy re-evalúa la regla completa y lo
  descartaría (`_revalida_negative`; `docs/APPLY.md` §3.1: "jamás se limita a
  `orders>0`"). Hay dos caminos y el spec de 2.5 elige uno:
  a) herramienta del dueño con ledger antes del HTTP, readback y reversa, como
  `tools/archiva_inertes.py`: no toca el ciclo ni la cola. **Recomendado.**
  b) aprobación como insumo del ciclo: exige cambio sellado de `docs/APPLY.md`
  §3.1, una rama propia en la re-validación y congelar la aprobación en
  `inputs` para que el replay reproduzca la decisión.
  Esto es un cambio de postura y el plan lo dice: el diseño v2 rechazó una cola
  de aprobación humana ("la proposal-only con aprobación humana era parte del
  problema del stack viejo", `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`) y
  `cortes-ui-01` rechazó un botón «aprobar». La diferencia es que aquí la
  aprobación no frena nada que el motor ya decidió: agrega una acción que el
  motor no propone. El dueño decide en 2.5 y 3.1 si acepta ese cambio.
- **Que Jev frene sola un bloqueo es condicional y cambia una regla sellada**
  (1.4, 4.3). Retener en la cola rompe "default al vencer = APLICAR; el
  silencio del dueño no bloquea" (`docs/CONTEXTO.md`, `docs/APPLY.md` §1.1) y
  el contrato de JEV ADS 01 sobre la aplicación; no se propone. El camino que
  `docs/CONTEXTO.md` sí prevé es una señal al decidir (Fase 4: "señales, no
  gates… máximo comportamiento: abstenerse"): el ciclo lee un juicio ya
  guardado, como dato, y el motor se abstiene de proponer el corte. Exige
  demostrar antes en sombra que mejora la tasa de acción útil, y un sello del
  dueño porque el juicio de Jev pasa a cambiar una decisión. Sin juicio
  guardado el motor decide como hoy.
- **La fábrica coteja también las semillas de términos vendedores** (5.1). JEV
  ADS 01 sólo cotejaba términos heredados de biblioteca.
- **Un botón que pide una revisión** (5.3) crea un encolado que el diseño de
  JEV ADS 01 dejó fuera. Es condicional.

## Interruptores y reversa

Las claves viven en `config_version.settings`; ausente o corrupta significa
apagado, como `fabrica.creacion`.

| Efecto | Interruptor | Apagar deja | Reversa de lo ya hecho |
| --- | --- | --- | --- |
| Aviso (1 y 4) | `jev.revision_automatica` | Sin job; `/cortes` muestra lo ya guardado | No hay efecto que revertir |
| Lista de ajenas (2) | `jev.candidatas` | Sin lista nueva | No hay efecto que revertir |
| Bloqueo aprobado (2.6) | el que fije 2.5 | Ninguna aprobación produce efecto | Revocar la aprobación; negativo aplicado: delete del negativo (`docs/APPLY.md` §7) |
| Ruteo aprobado (3.4) | el que fije 3.1 | Ningún ruteo produce efecto | Revocar; aplicado: reversa de harvest completo (keyword primero, negativo después) |
| Abstención (1.4, 4.3) | `jev.abstencion` | El motor decide como hoy | Apagar; nada se escribió en Amazon |
| Exclusión en fábrica (5.3) | `fabrica.exclusion_jev` | El plan sale con todas sus semillas | Antes de confirmar no hay efecto; después, la pausa de lote que ya existe |

## Tareas y dependencias

Cada fila termina con evidencia en `docs/evidencia/jev-ads-02/ejecucion/<id>/`.
`cc:TODO` significa que no ha empezado. Una migración toma el siguiente número
libre al crearse. CONDICIONAL: si su medición no cumple el criterio, la fila se
cierra como "no se construye", con el motivo.

### Fase 0 — bases

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 0.2 | `[stage:medicion] [lane:release]` Completar fichas. Faltan 238 productos en MX y 53 en US (28 sin ficha y 25 con ficha que no cubre todos sus listings). Mismo método del piloto: el lead pregunta, el dueño contesta, el lead arma y el dueño registra. | Todo producto con anuncio ENABLED tiene una ficha vigente que cubre todos sus listings de esa plataforma (el asesor cuenta también los de anuncios pausados o archivados), o un motivo escrito; conteo por mercado antes y después con la consulta de planificación; ningún nombre ni SKU en el repo. | - | cc:TODO |
| 0.3 | `[stage:investigacion] [lane:fast]` Confirmar y versionar la tarifa de TypeSafe (la evidencia del diseño anota 0.042 USD por millón de tokens de entrada) y calcular el costo en USD del piloto y de cada fase. Decidir con esa cifra si se permite reutilizar pares entre revisiones (hoy prohibido, R4). | Tarifa con su fuente y fecha; costo del piloto calculado con su `usage`; tope diario propuesto; decisión de reutilización escrita. | - | cc:TODO |
| 0.4 | `[stage:investigacion] [lane:gate]` Roster probado: averiguar si la ingesta de estructura puede demostrar que trae todos los anuncios de un grupo, y por qué 1,168 anuncios de MX y 920 de US no tienen producto ligado. | Regla escrita de cuándo `censo_grupo` puede decir `exhaustivo=True`; lista de grupos que la cumplirían por mercado; causa de los anuncios sin producto con su conteo. | - | cc:TODO |
| 0.1 | `[stage:planificacion] [lane:gate]` Spec delta de las fases de aviso: job automático y su reanudación, login propio de Jev, tope diario, reutilización según 0.3, regla de roster según 0.4, y los criterios medidos de abajo sellados por el dueño. | Documento en `docs/superpowers/specs/` con los contratos que cambian y los que no; umbrales sellados por el dueño. | 0.3, 0.4 | cc:TODO |
| 0.5 | `[stage:medicion] [lane:release]` Medir lo que el piloto dejó fuera: sensibilidad al orden de las opciones Choice, con una muestra etiquetada. | Reporte con denominadores: cuántos pares cambian de respuesta al invertir el orden; si pasa del umbral sellado en 0.1, se corrige el contrato antes de 1.1. | 0.1 | cc:TODO |

### Fase 1 — opción 1: avisar a tiempo de un bloqueo equivocado

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 1.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Job `jev-revisa` fuera del ciclo: toma las propuestas `negative` y harvest en `pending_veto` o `released` (las dos siguen vetables) sin revisión y las evalúa con `AsesorAds.evaluar`. Vive en `app/`, o lleva su línea en el `Dockerfile` y en la prueba que fija los tools copiados. Lee con el login de lectura y escribe con un login propio miembro sólo de `app_jev`. Cron con `flock` después del ciclo. Arregla la reanudación: hoy compara el censo con `synced_at`, que la ingesta de estructura reescribe a diario, y al día siguiente falla con "misma solicitud con otro payload". | Pruebas: con el interruptor ausente no hace nada; respeta el tope diario; un fallo de TypeSafe deja fallo visible y no toca la cola; una revisión a medias se retoma después de una resincronización sin pagar dos veces (la prueba falla contra el código de hoy); el login de escritura no puede leer ni escribir `apply_queue`, `decision` ni ledger. Salud muestra última corrida, fallos y llamadas del día. | 0.1, 0.5 | cc:TODO |
| 1.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Aviso por Telegram cuando Jev termina una propuesta: qué búsqueda, a cuántos productos del grupo corresponde, cuántos evaluó y cuánto falta para que venza el veto. `/cortes` ya lo muestra. | Prueba del texto en los tres casos (corresponde, no corresponde a los evaluados, no se pudo evaluar) con la cobertura a la vista; nunca dice "ninguno" con universo desconocido; fallo del canal no rompe el job. | 1.1 | cc:TODO |
| 1.3 | `[stage:medicion] [lane:release]` Medición retrospectiva con el CLI que ya existe (`evaluar --decision-id`): los 18 pares `negative` históricos. El dueño dice de cada búsqueda si el bloqueo era correcto. | Reporte por mercado con denominadores: bloqueos sobre búsquedas que sí corresponden, acuerdo del dueño con Jev, fallos y abstenciones. | 0.2 | cc:TODO |
| 1.4 | `[stage:planificacion] [lane:gate]` CONDICIONAL. Abstención: el motor no propone un `negative` sobre una búsqueda que Jev ya juzgó que sí corresponde. Señal al decidir, primero en sombra (el ciclo registra qué se habría abstenido, sin cambiar la decisión). | Spec propio y sello del dueño del cambio de contrato; el spec define la "tasa de acción útil" que la sombra debe mejorar (Fase 4 de `docs/CONTEXTO.md`), porque el acuerdo del dueño no la sustituye; contrafactual: interruptor apagado o juicio ausente dejan el ciclo idéntico; la decisión guarda en `inputs` el juicio usado y el replay la reproduce. | 1.3, 2.4 | cc:TODO |

### Fase 2 — opción 2: detectar pronto las búsquedas ajenas

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 2.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Selección pura de candidatas: par grupo-búsqueda con gasto y cero ventas en la ventana madura de cortes, clics desde el mínimo de config, no ASIN-like, bajo el umbral del motor y sin clave bloqueada (veto vigente, en cola o negativo ya puesto). | Antes del test, el `SELECT` que confirma la forma real del dato (regla 8). Pruebas de cada exclusión y de la frontera de madurez; la función no importa red ni DB. | 0.1 | cc:TODO |
| 2.2 | `[stage:implementacion] [lane:gate] [tdd:required]` El job evalúa las candidatas contra los productos de su grupo, de mayor a menor gasto, dentro del tope diario. | Pruebas: orden por gasto; el tope corta y la revisión se retoma al día siguiente; una candidata ya evaluada con las mismas fichas no se paga otra vez; candidata sin fichas completas queda marcada, no juzgada. | 1.1, 2.1 | cc:TODO |
| 2.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Pantalla "Búsquedas ajenas" (sólo GET): búsqueda, grupo, clics, gasto, resultado de Jev y cobertura ("0 de 22 evaluados corresponden; faltan 3 fichas; roster sin probar"). | Tests de API: cero HTTP externo y cero escrituras; la cobertura siempre visible; sin fichas completas no aparece como candidata firme. | 2.2 | cc:TODO |
| 2.4 | `[stage:medicion] [lane:release]` Medir con el dueño: de cada candidata dice si es ajena. Incluye una prueba hacia atrás: se arma la lista como habría salido hace 30 días y se mira si esas búsquedas vendieron después. | Reporte por mercado, contado por búsqueda distinta: candidatas, acuerdo, falsos "ajena" (el error caro: bloquear una búsqueda buena), cuántas de las marcadas vendieron en los 30 días siguientes, gasto de las confirmadas. | 0.2, 2.3 | cc:TODO |
| 2.5 | `[stage:planificacion] [lane:gate]` CONDICIONAL. Spec del bloqueo aprobado: elegir entre los caminos a) y b) de "Contratos", con su interruptor, cuota, reversa y qué pasa si la búsqueda vende después de aprobar. | Spec aprobado y sellado por el dueño. Si elige b), el cambio a `docs/APPLY.md` §3.1 va en el mismo documento. | 0.4, 2.4 | cc:TODO |
| 2.6 | `[stage:implementacion] [lane:gate] [tdd:required]` CONDICIONAL. Bloqueo aprobado según 2.5: el dueño aprueba una candidata y se crea el negativo. Sólo en grupos con roster probado y fichas completas. | Pruebas: interruptor apagado no produce efecto y deja ciclo y cola idénticos; ledger antes del HTTP y readback; una venta posterior a la aprobación impide el bloqueo; revocar la aprobación lo impide; reversa probada en el mismo PR; el negativo no entra a `negative_biblioteca`. | 2.5 | cc:TODO |

### Fase 4 — opción 4: revisar el destino de un harvest

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 4.1 | `[stage:implementacion] [lane:gate] [tdd:required]` El job de 1.1 ya evalúa origen y destino por separado. Falta que el tope alcance un destino grande (hasta 237 productos) y que el aviso de 1.2 diga a cuántos productos del destino corresponde. | Prueba con destino mayor que el tope: retoma sin pagar dos veces y el aviso sale completo antes de vencer el veto, o avisa que no alcanzó. | 1.2 | cc:TODO |
| 4.2 | `[stage:medicion] [lane:release]` Retrospectiva con el CLI: los 10 pares de harvest históricos, 3 de cuyas propuestas vetó el dueño. | Reporte: qué dijo Jev del destino de cada uno y si coincide con lo que el dueño hizo. | 0.2 | cc:TODO |
| 4.3 | `[stage:planificacion] [lane:gate]` CONDICIONAL. Abstención de harvest cuyo destino no tiene producto que corresponda. Mismo mecanismo que 1.4, con su propio criterio. | Igual que 1.4; exige destino con roster probado. | 0.4, 4.2 | cc:TODO |

### Fase 5 — opción 5: filtrar semillas en la fábrica

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 5.1 | `[stage:implementacion] [lane:gate] [tdd:required]` El export de fábrica coteja también las semillas que salen de términos vendedores, con su rol. Hoy `evaluar-plan` aborta con "no hay terminos que cotejar" porque las bibliotecas están vacías. | Pruebas: un plan sin biblioteca produce términos a cotejar; el rol de cada término viaja con él; el plan y su huella no cambian. | 0.1 | cc:TODO |
| 5.2 | `[stage:medicion] [lane:release]` Piloto con un plan de fábrica real, por CLI. | Reporte: semillas, cuántas marca Jev como ajenas a todos los productos del plan, acuerdo del dueño, costo. Decide si 5.3 se construye. | 0.2, 5.1 | cc:TODO |
| 5.3 | `[stage:implementacion] [lane:gate] [tdd:required]` CONDICIONAL. La pantalla de fábrica muestra la asesoría de la huella y un botón para pedir la revisión, que el job atiende. Una semilla que no corresponde a ningún producto del plan aparece marcada y el dueño decide quitarla. Aquí el universo sí es exhaustivo: son los productos del plan. | Pruebas: pedir la revisión no llama a TypeSafe dentro del request; sin exclusiones la huella es idéntica a la de hoy; excluir cambia huella y preview y el dueño confirma el plan nuevo; una semilla con ficha faltante o juicio insuficiente no se puede excluir por Jev; nada toca Amazon antes de la confirmación. | 1.1, 5.2 | cc:TODO |

### Fase 3 — opción 3: mandar cada búsqueda a su campaña

Va al final: hoy no existe ningún ruteo fuera del harvest y es la única opción
que crea una acción publicitaria nueva.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 3.1 | `[stage:planificacion] [lane:gate]` Spec de ruteo: contra qué grupos se busca el destino y su costo, qué es "mejor destino", y la acción (negativo exact en el origen y keyword exact en el destino). El harvest de hoy no sirve tal cual: resuelve el destino por campaña, no por búsqueda, y su re-validación exige ventas. | Spec aprobado y sellado por el dueño, con el costo por búsqueda, el camino de escritura (herramienta o ciclo, como en 2.5), su reversa y la relación con el harvest por grupo de `fabrica-02`. | 2.4 | cc:TODO |
| 3.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Reporte "búsquedas en la campaña equivocada" en la pantalla de 2.3: no corresponde a los productos de su grupo y sí a los de otro. | Tests: muestra testigo y cobertura de origen y destino; nunca propone destino con universo de origen desconocido sin decirlo. | 2.3, 3.1 | cc:TODO |
| 3.3 | `[stage:medicion] [lane:release]` Medir el reporte con el dueño. | Reporte por mercado: casos, acuerdo sobre el destino, falsos. | 3.2 | cc:TODO |
| 3.4 | `[stage:implementacion] [lane:gate] [tdd:required]` CONDICIONAL. Ruteo aprobado según 3.1. | Interruptor apagado no produce efecto; reversa completa probada en el mismo PR; el negativo de ruteo no entra a `negative_biblioteca`. | 0.4, 3.3 | cc:TODO |

### Cierre

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| R | `[stage:revision] [lane:gate]` Cada bloque de código pasa `cross-review.ps1` con un revisor distinto del autor. | Regla 4 de quality-kit: sólo un bloqueante reproducible abre otra ronda; ninguno queda abierto. | cada bloque | cc:TODO |
| D | `[stage:cierre-pr] [lane:release]` Despliegue por fase, apagado, con scripts nuevos que siguen el patrón de `jev-ads-01/ejecucion/2.3` (los de 2.3 llevan fijas las migraciones 0049 y 0050) y rollback ensayado. | Checklist verde tras el primer ciclo; el interruptor se enciende en un cambio de config aparte. | cada fase | cc:TODO |

## Criterios medidos

Valores propuestos por el autor del plan, sin derivación; el dueño los sella
en 0.1. Se cuentan por búsqueda distinta y por mercado. Las etiquetas se ponen
a ciegas: el dueño contesta sin ver la respuesta de Jev. Las búsquedas de una
medición no pueden ser las que se usaron para corregir fichas: en el piloto los
lotes perfectos repitieron las búsquedas ya corregidas.

| Paso | Criterio para avanzar |
| --- | --- |
| Encender 1.1 y 4.1 (aviso) | 0.2 cubre los grupos con propuestas; tope diario fijado con costo conocido. |
| 1.4 (abstención de `negative`) | Al menos 50 búsquedas etiquetadas entre 1.3 y 2.4; de las que Jev dice que sí corresponden, el dueño coincide en 95% o más; ningún fallo contado como señal. |
| 2.5 y 2.6 (bloqueo aprobado) | Al menos 50 búsquedas etiquetadas; de las que Jev marca ajenas a todos los productos evaluados en grupos con ficha completa, 95% o más confirmadas; ninguna de esas vendió en los 30 días siguientes de la prueba hacia atrás. |
| 4.3 (abstención de harvest) | Al menos 20 pares de harvest medidos; acuerdo de 90% o más. |
| 3.4 (ruteo aprobado) | Al menos 30 casos; destino confirmado en 90% o más. |
| 5.3 (exclusión) | 5.2 encuentra semillas ajenas confirmadas por el dueño y ninguna semilla buena marcada. |

Lo que el volumen de hoy no alcanza, dicho de frente: el histórico trae 18
pares `negative` (2 en MX) y 10 de harvest, así que el criterio de 4.3 no se
cumple pronto y el de 1.4 depende de las etiquetas de 2.4. Mientras no se
cumplan, el aviso se queda como está.

## Huecos conocidos

Los encontró la lectura del código al explicar el plan. Ninguno está resuelto;
cada uno lo cierra el spec de la tarea indicada antes de escribir código.

| Hueco | Lo cierra |
| --- | --- |
| "Negativo ya puesto" no tiene fuente: los negative keywords no se ingieren como estructura. | 2.1 |
| Un `negative` aplicado no bloquea su clave; pasado el cooldown el motor puede volver a proponerlo. | 2.1 |
| La prueba hacia atrás lee la última observación conocida hoy, no la que existía hace 30 días. | 2.4 |
| `jev_revision` sólo admite los sujetos `decision` y `semillas`; las candidatas reutilizan `semillas` o piden migración. | 0.1, 2.2 |
| "No pagar dos veces" entre días exige reutilizar entre revisiones o una solicitud estable por candidata. | 0.3, 2.2 |
| El ciclo no puede leer `jev_*` (falta grant a `app_decide`) y no existe la consulta "juicio vigente de un grupo y una búsqueda". | 1.4, 4.3 |
| Si `app/notifica.py` importa módulos de Jev, la guarda de imports no lo ve: el ciclo importa `notifica`. | 1.2 |
| Las semillas del plan van normalizadas y los términos vendedores crudos; la clave del juicio es el texto literal. | 5.1 |
| El apply de un `negative` escribe en `negative_biblioteca` si el grupo es de fábrica, y el ledger del motor va por `decision_id`. | 2.5 |
| Juzgar un término contra dos grupos sólo existe atado a una decisión de harvest. | 3.1 |
| Una decisión de harvest sin destino legible hace fallar su revisión; cuántas de las 10 históricas, sin dato. | 4.2 |
| El CLI manual no tiene lock; puede correr a la vez que el job. | 1.1 |
| No hay aviso de fichas por vencer; al vencer, la asesoría pasa a "obsoleta" sin que nadie escriba. | 0.1 |

## Pruebas que deben discriminar

1. Interruptor ausente: el ciclo y la cola producen lo mismo que hoy.
2. Una aprobación revocada no produce efecto.
3. Una venta que llega después de aprobar impide el bloqueo antes del HTTP.
4. Un grupo sin roster probado nunca da "ninguno corresponde", ni en pantalla
   ni como base de un bloqueo.
5. El login que escribe revisiones no puede leer ni escribir fuera de lo que
   `app_jev` ya tiene.
6. Sin exclusiones, la huella de un plan de fábrica no cambia.
7. Una revisión a medias se retoma después de la ingesta de estructura.

Cada bug encontrado en el camino trae la prueba que lo habría atrapado.

## Orden y salida

Orden de ejecución: 0 → 1 → 2 → 4 → 5 → 3. Las retrospectivas 1.3 y 4.2 sólo
necesitan fichas y pueden correr en cuanto cierre 0.2. Las mediciones (1.3,
2.4, 4.2, 5.2, 3.3) son compuertas: sin su reporte no se construye el efecto
que sigue.

El plan cierra cuando cada opción quedó encendida con su medición, o cerrada
con el motivo escrito. Ninguna fila autoriza por sí sola encender un
interruptor en producción: eso es un cambio de config aparte, con el go del
dueño.
