# I. Fábrica de campañas, presupuestos, API y CLI que escriben dinero: resultado

Copia revisada: `orbit07-wt` en master `3c841f4`. Sondas en `orbit07-review/sondas/I_*.py`
(corridas con `/Users/dn/dev/goncloud-Orbit/.venv/bin/python`, `PYTHONPATH=<copia>`).
La comprobación de Postgres se hizo con un SELECT de solo lectura en el contenedor local
`orbit-pg-pruebas`, no en producción.

Conteo: CRÍTICO 0 · ALTO 1 · MEDIO 3.

## Hallazgos confirmados

### H1. Un tope diario grande guardado en la pantalla de settings tumba toda la fase de apply en cada ciclo
- Severidad: ALTO
- Frente: motor
- Dónde: `app/api_write.py:253` (`caps: dict[str, int ge=0]`, sin máximo) → `app/config_write.py:487-497` (solo revisa `cap < 0`) → `app/apply.py:330-348` (`int(valor)` en Python, sin límite) → `app/apply.py:391-401` (INSERT a `apply_quota_state.cap INTEGER`, `migrations/0001_initial.sql:823`) con el trigger `apply_cap_de_config`, que hace `(settings ->> clave)::INTEGER` (`migrations/0039_precio.sql:1175`). La llamada en `app/apply.py:1401` no está dentro de ningún try; el error sube hasta el `except Exception` de `app/cycle.py:3560`, que aborta la fase entera.
- Escenario: el dueño quiere quitar el tope de bids de MX y escribe `9999999999` (o cualquier valor ≥ 2,147,483,648) en «cap bid» → la API responde 200 y guarda una config nueva → en el siguiente ciclo, el primer bid que intenta cobrar cupo hace el INSERT de la fila del día, y Postgres responde `value "..." is out of range for type integer` → `_fase_apply` cae al `except` genérico: `apply_error`, `fallo=True`, rollback. Como `reconcilia_harvest` corre antes y `aplica_bids` lanza el error, tampoco corre nada de lo que viene después: no se aplica ningún bid ni se liberan los cortes de la cola (`apply_cola.libera_vencidos`, `app/cycle.py:3505`). Pasa igual en cada ciclo diario hasta que alguien corrija la config. El cap de pause/negative revienta en `app/apply_cola.py:1665` y el de harvest en `app/apply_harvest.py:2220`, con el mismo resultado. Lo correcto sería rechazar en el guardado (422) cualquier valor que no quepa en INTEGER, igual que ya se hace con lo que el motor rechazaría como config corrupta (docstring de `config_write`: «lo que el motor rechazaría… jamás llega a una fila»).
- Alcance: solo pasa si alguien guarda ese valor (desde la pantalla, la API o el CLI). Desde ese momento, todos los ciclos live de esa plataforma quedan sin apply. Se ve como `apply_error` en las notas del ciclo, pero la pantalla dijo «guardado».
- Evidencia (sonda ejecutada):
  ```
  $ PYTHONPATH=$PWD .venv/bin/python sondas/I_cap_gigante.py
  pydantic acepta caps: {'bid': 3000000000}
  proxima_config acepta: 3000000000 ['cap bid 50 -> 3000000000']
  _cap_de_config del motor devuelve: 3000000000
  $ docker exec orbit-pg-pruebas psql ... -c "SELECT ('{\"c\":\"3000000000\"}'::jsonb ->> 'c')::INTEGER"
  ERROR:  value "3000000000" is out of range for type integer
  (con 2147483647 devuelve 2147483647)
  ```
  En `config_version` no hay ningún trigger que valide los caps (solo los de append-only, `migrations/0001_initial.sql:1559,1591`).
- Cómo reproducir: `PYTHONPATH=<copia> .venv/bin/python orbit07-review/sondas/I_cap_gigante.py`. Para probarlo contra la base (no ejecutado): guardar settings con `{"base_config_version_id": N, "caps": {"bid": 3000000000}}` en una base de pruebas, correr `python -m app.cli cycle --platform amazon_mx --forzar` en live con una decisión de bid y mirar `optimizer_cycle.notes->>'apply_error'`.

### H2. Si una persona pausa un impulso desde la pantalla de la fábrica, el vigía falla todos los días y el impulso no se cierra nunca
- Severidad: MEDIO
- Frente: motor
- Dónde: `app/impulso_io.py:973-976` (si el veredicto es «pausar» y el lote no dice `pausado`, llama a `pausador.pausa`) → `tools/fabrica_campanas.py:1727` (`Abortar("lote … desarmado: nunca revive")`) → `app/impulso_io.py:187` (`_lote_no_pausable` lo convierte en `ErrorImpulso(409)`) → `app/impulso_io.py:1162` (el vigía lo atrapa, hace rollback y no guarda lectura). El botón que provoca el estado `desarmado` es «PAUSAR GRUPO» de la fábrica (`app/static/js/fabrica.js:14` → `app/api_fabrica.py:389` → `app/fabrica_web.py:720` → `fc._desarmar`). Ese botón aparece también para los lotes `impulso-*`, porque `fabrica_web.lotes` lista todos los lotes de la plataforma (`app/fabrica_web.py:632-639`).
- Escenario: el dueño ve el lote de un impulso en la lista de la fábrica y lo pausa con «PAUSAR GRUPO» → el lote queda `desarmado`, las campañas pausadas y los goals apagados. Sin gasto, el impulso llega tarde o temprano a un veredicto «pausar» (`sin_impresiones` al día 14, por ejemplo) → el vigía intenta pausar, le sale 409 y no guarda nada. Al día siguiente pasa lo mismo, y así para siempre. Las consecuencias: (1) el impulso sigue «vivo» en `_SQL_VIVOS`, así que el producto ya no puede impulsarse otra vez (`_falla_si_hay_vivo`, `app/impulso_io.py:714`: «el producto ya tiene un impulso vivo»); (2) si es el único impulso vivo, `impulso-vigia` sale 1 todos los días (`app/cli_bids.py:137-143`), y ese ruido tapa fallas reales. Lo correcto sería que, con el lote `desarmado` (ya pausado y con goals apagados), el vigía guarde la lectura final como pausada en lugar de volver a pausar.
- Alcance: solo cuando una persona usa «PAUSAR GRUPO» sobre un lote de impulso. No pierde dinero porque las campañas ya están pausadas. El efecto es sobre el funcionamiento del vigía y deja el producto bloqueado.
- Evidencia (sonda ejecutada; conexión falsa con las respuestas SQL de ese estado y la primera línea real de `_cambia_estado_lote`):
  ```
  $ PYTHONPATH=$PWD .venv/bin/python sondas/I_vigia_desarmado.py
  dia 0 FALLA: ErrorImpulso 409 lote impulso-5-abcd1234 desarmado: nunca revive
  dia 1 FALLA: ErrorImpulso 409 lote impulso-5-abcd1234 desarmado: nunca revive
  dia 2 FALLA: ErrorImpulso 409 lote impulso-5-abcd1234 desarmado: nunca revive
  lecturas guardadas: 0
  ```
- Cómo reproducir: `PYTHONPATH=<copia> .venv/bin/python orbit07-review/sondas/I_vigia_desarmado.py`.

### H3. Si la ingesta de las 07:10 se retrasa, el vigía del impulso (y el ciclo) se saltan el día entero sin reintento, y el mensaje dice «espera al siguiente horario» aunque no hay ninguno
- Severidad: MEDIO
- Frente: motor
- Dónde: crontab instalado, `docs/DEPLOY.md:851-855` y bloque instalador `docs/DEPLOY.md:1184-1199` (también en `docs/evidencia/ingesta-01/ejecucion/D.1/instalador-salida-20261021-183311.txt:58-70`): ingesta a las 07:10, 10:40 y 13:15, pero `impulso-vigia` solo a las 08:20 y `cycle` solo a las 08:40/08:41. Guardas: `app/cli_bids.py:125-127` y `app/cli.py:113-115` salen 0 imprimiendo `TEXTO_ESPERA` (`app/metricas_listas.py:26`). El plan aprobado sí tenía reintentos del vigía y del ciclo a las 10:08/10:10, 13:08/13:10 y 16:08/16:10 (`plans/ingesta-01.md:54-60`), pero no llegaron a `DEPLOY.md` ni a la prueba `tests/test_ingesta_cron.py`.
- Escenario: Amazon tarda más de ~70 min en entregar los reportes (el sondeo puede durar hasta 2 h 30 min, `app/ads/diario.py:56-60`) o la corrida de las 07:10 falla y la de las 10:40 sí sale → a las 08:20 el vigía no encuentra las métricas de ayer, imprime «espera al siguiente horario» y sale 0. El ciclo hace lo mismo a las 08:40. Ninguno vuelve a correr ese día. Resultado: ningún bid cambia, no se libera ningún corte y los impulsos gastan un día más sin revisión (cada impulso tiene 2 días de margen, `app/impulso.py:24`; dos días seguidos así pasan el tope). Nadie avisa que el ciclo no corrió: `ads-salud` solo revisa el atraso de la ingesta y queda contento cuando la de las 10:40 sale bien. Lo correcto sería reinstalar los reintentos que el plan prometía, o que la ingesta, al terminar, dispare a los consumidores.
- Alcance: cron y defaults actuales en live. En la medición de 7 días (`docs/evidencia/estado-planes-2026-11-04/ingesta-7d.txt`) la ingesta terminó siempre entre 07:14 y 07:27, así que pasa solo los días en que Amazon se retrasa. El plan aceptó por escrito el caso «las tres descargas fallan» (`plans/ingesta-01.md:64-66`), pero no el caso «falla la primera y sale una de las siguientes». Esto cruza a la zona del ciclo: lo anoto porque el vigía del impulso es de esta zona.
- Evidencia: camino leído línea por línea (crontab instalado, plan, guardas). La guarda exige una corrida `ok` de hoy con filas de D-1 (`app/metricas_listas.py:31-42`), y una corrida que sigue en curso todavía no tiene `ok=true`.
- Cómo reproducir (no ejecutado, necesita base): en una base de pruebas sin `ingest_run` ok de hoy, correr `python -m app.cli impulso-vigia` → imprime `sin métricas de ayer: espera al siguiente horario`, exit 0. Después: `crontab -l | grep -E 'impulso-vigia|app.cli cycle'` en goncloud muestra un solo horario de cada uno.

### H4. Ninguna cifra de dinero que teclea una persona tiene tope superior; un cero de más pasa si se confirma
- Severidad: MEDIO
- Frente: dinero
- Dónde: presupuesto de la fábrica, hasta 9,999,999,999.99 (`app/api_fabrica.py:113-125`; `app/fabrica_plan.py:360-382` solo exige > 0 y ≥ bid); cambio de presupuesto de campaña (`app/api_write.py:675` solo `gt=0`; `app/campana_ajustes.py:137-147`); tope del impulso, hasta 9,999,999,999.9999 (`app/api_fabrica.py:455-468`); techo de bid del goal (`app/api_write.py:214`, `app/goals_write.py:217-226`); target manual de la plataforma, sin banda (`app/api_write.py:251`, `app/optimizer/goals.py:202-221`), cuando el target derivado del margen se recorta a [10, 45] y el manual de la fábrica exige esa misma banda (`app/fabrica_plan.py:261-276`).
- Escenario: el dueño quiere un presupuesto de 50.00 MXN y escribe 5000 → la vista previa dice «Orbit cambia el presupuesto diario de 100 MXN a 5000 MXN», el literal fijo es «APLICAR AJUSTE» (o «CREAR 5 CAMPAÑAS», que no menciona el monto) → se aplica. En la fábrica las campañas nacen ENABLED y gastan desde ese mismo día. Otro caso: un target manual de 200 en lugar de 20 se guarda; si el margen se abstiene (ledger rancio > 3 días), gobierna toda la plataforma y empuja los bids hacia el techo. La única defensa es que la persona lea la vista previa: ninguna regla pide confirmar dos veces un monto fuera de lo normal.
- Alcance: solo con una acción humana confirmada (token + huella + literal). Funciona así por diseño (no encontré una decisión escrita sobre topes superiores), pero responde «no» a las preguntas 1 y 2 del encargo: no hay tope por lote ni por presupuesto.
- Evidencia (sonda ejecutada):
  ```
  $ PYTHONPATH=$PWD .venv/bin/python sondas/I_entradas_dinero.py
  ACEPTA  ajuste presupuesto=1e25: 1E+25
  ACEPTA  planea presupuesto=1e25: Orbit cambia el presupuesto diario de 100 MXN a 10000000000000000000000000 MXN en campaña 123
  ACEPTA  settings target=100000: 100000
  ACEPTA  goal ceiling=4500: 4500
  ACEPTA  impulso tope=9999999999: 9999999999
  RECHAZA ajuste presupuesto=NaN / Infinity / -1 / 0   (bien)
  RECHAZA planea ubicacion 901: porcentaje fuera de 0..900   (bien)
  ```
- Cómo reproducir: `PYTHONPATH=<copia> .venv/bin/python orbit07-review/sondas/I_entradas_dinero.py`.

## Sospechas sin confirmar
1. **Impulso sin tope de gasto si una métrica viene NULL.** `_acumula` (`app/impulso_io.py:919-926`) devuelve None si cualquier fila de cualquier día trae NULL, y entonces `veredicto_impulso` responde «en_curso» antes de comparar el gasto contra el tope (`app/impulso.py:166-180`). Una sola fila de campaña con `orders` o `cost` NULL que nunca se vuelva a observar dejaría el impulso gastando tope/14 al día sin cierre. Para confirmarlo, en producción: `SELECT count(*) FROM v_metric_latest v JOIN ad_entity e ON e.id=v.ad_entity_id WHERE e.kind='campaign' AND (v.cost IS NULL OR v.orders IS NULL OR v.clicks IS NULL OR v.impressions IS NULL);`. Si da 0 y la ingesta nunca escribe NULL en campañas, se descarta.
2. **Impulso lanzado sin su fila de vigilancia.** Si `_sella_impulso` falla después de que `_mutar` dejó el par ENABLED y `applied` (`app/impulso_io.py:638-660`, INSERT en la línea 656; la excepción no se atrapa en `_lanza_par_nuevo` y la API responde 500), el par gasta sin vigía hasta que `reconcilia_huerfanos` lo pause en la siguiente corrida del vigía (≥ 60 min, en la práctica las 08:20 del día siguiente). Faltaría provocar un error de base en ese INSERT para medir cuánto queda encendido.
3. **El despliegue puede revertir un interruptor que el dueño cambió en pantalla.** `claves_a_converger` (`tools/despliegue/comun.sh:100-117`) escribe con `productos-modo` toda clave cuya línea cambie en `interruptores.env` entre commits, sin comparar contra lo que el dueño puso en pantalla. La guarda solo frena que el archivo encienda la pausa (`tools/despliegue/desplegar.sh:23-26`). Si el dueño encendió la pausa en live y un commit cambia esa línea a `shadow`/`off`, el deploy la apaga sin preguntar. Faltaría un caso real de commit que toque esa línea.

## Zonas revisadas sin hallazgos (una línea cada una, con qué comprobaste)
- **Autenticación de escritura (pregunta 4)**: las 27 rutas POST, una por una. Llevan `exige_token` (solo header `x-orbit-token`, `hmac.compare_digest`, 503 fail-closed): `/api/ads-optimizer/{veto, propuestas-campana/{id}/descartar, reversa/bid|pause|negative, bid/regresar, bid/regresar-todas, goals/{id}, settings/{platform}, productos/pausa/{platform}, campana-ajuste/aplicar, campana-ajuste/{id}/regresar}`, `/api/familias{,/asignar}`, `/api/jev-bloqueo/{aprobar,rechazar,quitar,reanudar}`, `/api/precios/{goals/plan, goals/aplicar, compuerta/{id}/liberar}`, `/api/fabrica/{crear, lotes/{lote}/{accion}, impulso/lanzar, impulso/{id}/pausar, retiro/ejecutar|reponer|recrear}`. Van sin token, pero con la conexión de solo lectura (`ORBIT_DSN_READ`) y sin escribir: `/api/fabrica/{plan, export-semillas, bids-sugeridos, impulso/plan, retiro/plan}`. No hay PUT, PATCH ni DELETE. No hay CORS, así que otra página no puede mandar el header propio (sin CSRF). El servicio escucha solo en 127.0.0.1 y en la VPN (`docker-compose.yml`).
- **Fábrica, doble clic y reintento (pregunta 1)**: lote `web-<huella>` + candado de sesión + relectura (`app/fabrica_web.py:684-717`): el segundo clic recibe 409 o el mismo lote. La moneda la fija la plataforma. Los bids quedan dentro de [piso, techo] de su moneda. El lote que muere a medias deja las campañas ENABLED sin vigía: residual aceptado (decisión 8, `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md:57`, `plans/fabrica-01.md:46`), y lo sigue considerando un riesgo de gasto.
- **Impulso, tope y regreso (pregunta 2)**: nace pausado y se enciende al final, con pausa si falla (R09). Hay tope por producto. Los bids se validan con `_valida_plan_v2`. Los huérfanos los pausa `reconcilia_huerfanos`. El vigía pausa en tope − 2 días. Si el vigía no corre, el presupuesto sigue gastando tope/14 al día (aceptado en `plans/ingesta-01.md:64-66`; ver H2 y H3). La pantalla avisa «Sin sugerencia (salen al diario de su campaña)».
- **Ajuste de placement y presupuesto**: el porcentaje se acota a 0..900 (901 rechazado en la sonda). Rechaza NaN, Infinity, negativos y cero. Pide huella + literal y es idempotente por huella con candado. El regreso restaura solo la clase.
- **Concurrencia de settings (pregunta 5)**: `pg_advisory_xact_lock` + `base_config_version_id`, con 409. La config resultante se revisa con los lectores del motor (salvo el tope de H1).
- **JEV con --aplicar (pregunta 6)**: solo escribe tablas `jev_*` con `ORBIT_DSN_JEV` (`app/jev_senales.py:317,494,565,581,709`). No toca bids, tiene tope por corrida y guarda. Solo llega a Amazon cuando el dueño aprueba un bloqueo con token. No puede pelear contra el motor de bids.
- **CLI (pregunta 7)**: `archivar-anuncios` y `reponer-anuncios` hacen un ensayo por default y solo mutan con `--confirmar live` exacto. La herramienta de la fábrica exige `--acepto-mutacion-real` + `--esperado` + `--huella` + `--go`. `productos-modo` solo enciende la pausa en live por `cambia_pausa`, con límites guardados. `cycle`, `goals` y `precio` rechazan argumentos extra. `jev-senales` va en seco salvo con `--aplicar`. No hay plataforma por default en ningún comando que escriba.
- **Pantalla contra realidad (pregunta 8)**: la fábrica muestra el presupuesto diario total antes de crear. El impulso muestra el tope, el diario y los roles sin sugerencia. No encontré modos mostrados distinto de como están (aparte de la sospecha 3).

Nota para el lead: no escribí `.saikit/findings/`, porque el brief marca este archivo como única salida.
