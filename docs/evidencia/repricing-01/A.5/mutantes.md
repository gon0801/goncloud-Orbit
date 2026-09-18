# A.5 — Catálogo de mutantes (REPRICING 01, carril A)

## r2 (BRIEF-r2, sobre el árbol final de r2)

7 mutantes del lead que sobrevivian sobre `052aa42`, sembrados uno por uno
sobre el árbol final (idéntico a lo commiteado) con restauración verificada
por hash (`sha256` pre/post por archivo) y pycache fresca por mutante
(`PYTHONPYCACHEPREFIX=$(mktemp -d)`), revertidos sin commit. **Cero
sobrevivientes.**

| Id | Lo que fija | Cambio exacto sembrado | Test que lo mata | Veredicto |
|---|---|---|---|---|
| R1-reuso-sin-guarda | `_cotizacion_guardada` compara oferta, precio y moneda | el `if` guarda → `if False:` | `test_relanzar_con_otro_pedido_no_reusa_cotizacion` | MUERTO (exit 1): con otro P* reusa y mezcla |
| R1-fase3-traga-db | `psycopg.Error` en fase 3 sale de `correr` (infra, no publicación) | se quita `except psycopg.Error: raise` | `test_error_de_base_en_fase3_aborta_y_libera_lock` | MUERTO (exit 1): el error queda registrado y no sale |
| R1-parche-sigue | la rama de parche sin sellar corta el PATCH | `if compite and parche_sin_sellar:` → `if False:` | `test_parche_sin_sellar_persiste_filas_sin_reintentar` | MUERTO (exit 1): dos intentos de PATCH |
| R1-parche-no-corta | la rama `FormaParcheSinSellar` activa el parche | devuelve el `parche_sin_sellar` de entrada en vez de `True` | `test_parche_sin_sellar_persiste_filas_sin_reintentar` | MUERTO (exit 1): dos intentos de PATCH |
| R1-noconf-orden | `freno_no_confirmado` manda el último cambio | `ORDER BY id DESC` → `ASC` | `test_no_confirmado_viejo_mas_confirmado_nuevo_no_frena` | MUERTO (exit 1): frena con el viejo |
| R1-cli-errores | el CLI imprime cada error del resumen en stderr, con `scrub` | se quita el `for error in resumen.errores` | `test_cli_precio_imprime_errores_en_stderr` | MUERTO (exit 1): stderr vacío |
| R1-cubierto-sin-source | cobertura solo de `accounting_ledger_events` | se quita `r.source = 'accounting_ledger_events'` | `test_cobertura_ignora_ledger_de_otra_fuente` | MUERTO (exit 1): la otra fuente cubre |

## r1 (BRIEF-r1, sobre el HEAD nuevo de r1)

15 mutantes del lead + revalidación de los 15 de A.5 (el árbol cambió bajo
sus pies: fase 3 extraída a `_fase3_uno`, cotización con reuso, frenos con
motivo). Sembrados sobre el árbol final (idéntico a lo commiteado), uno por
uno con restauración verificada por hash, `-p no:cacheprovider` y pycache
fresca por mutante (`mktemp -d`). Sembrados a mano, revertidos sin commit.
**Cero sobrevivientes.**

A5-11 sobrevivió primero: con K4 (reversas contadas al reservar) el
`extra` fresco niega igual aunque la fase 2 planee con `reversas = 0`
(defensa en profundidad que enmascara el planeo). Se cerró espiando el
`cupo` que entra a `repartir` (`test_cupo_descuenta_reversas_al_repartir`).
LA-freno-sin-dias-cfg lo mata el test B.6 (config 2 + 2 días de error).

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| LA-huerf-plataforma | Huérfanas por plataforma (el `OR true` las cierra todas) | `_SQL_HUERFANAS`: `WHERE platform = %s` → `WHERE (platform = %s OR true)` | `test_huerfana_otras_plataformas_intactas` | MUERTO: la `pendiente` de `amazon_us` amanece `error` |
| LA-freno-estado | El freno cuenta solo `error` | freno: `AND estado = 'error'` → `AND estado <> 'imposible'` | `test_freno_solo_cuenta_error` | MUERTO: 3 `confirmado` frenan |
| LA-cuota-le | `used + extra < cap` (el `=` también niega) | `reservar`: `<` → `<=` | `test_reservar_cupo_exacta_N` | MUERTO: la 3a reserva con `cap = 2` pasa |
| LA-cuota-cap0 | `cap = 0` (o `extra = cap`) no inserta | quitar el `return False` | `test_reservar_cap_cero_no_inserta` | MUERTO: inserta y devuelve `True` |
| LA-reversas-todas | Solo `es_reversa` cuenta como reversa | `reversas_hoy`: `AND es_reversa` → `AND (es_reversa OR true)` | `test_cambio_real_de_hoy_no_es_reversa` | MUERTO: el cambio real consume el cupo (`mantener(cuota)`) |
| LA-ingreso60-ventana | `ingreso_60d` suma [hoy-75, hoy-16] (la de `u60`; S4 #12 no la define) | `BETWEEN %s - 75 AND %s - 16` → `... %s - 1` | `test_ingreso_60d_ventana_declarada` | MUERTO: cuenta la venta de ayer (150 en vez de 50) |
| LA-goal-cerrado-hoy | `valid_to = hoy` no se decide (`>` estricto) | `g.valid_to > %s` → `g.valid_to >= %s` | `test_goal_cerrado_hoy_no_se_decide` | MUERTO: decide el goal cerrado |
| LA-goal-futuro | `valid_from` mañana no se decide | `AND g.valid_from <= %s` → `AND (g.valid_from <= %s OR true)` | `test_goal_futuro_no_se_decide` | MUERTO: decide el goal futuro |
| LA-previos-reversas | La reversa no entra a previos (A.2 R14: no enfría) | cambios: `AND NOT es_reversa` → `AND (NOT es_reversa OR true)` | `test_reversa_confirmada_no_enfria` | MUERTO: la reversa de ayer pone `mantener(cooldown)` |
| LA-prioridad-none | Sin prioridad al fondo (`is None` primero) | `prioridad is None` → `prioridad is not None` | `test_sin_prioridad_va_al_fondo` | MUERTO: `lineas` sale `[B, A]` |
| LA-racha-previa | La racha de ayer entra a `evaluar_senal` | `racha_previa=...` → `racha_previa=0` | `test_racha_previa_entra_a_la_senal` | MUERTO: `racha_senal` sale 1 en vez de 3 |
| LA-buybox | `buy_box` sale de la observación del día | `buy_box = pricing[3]` → `buy_box = True` | `test_buybox_de_la_observacion_va_a_la_decision` | MUERTO: la decisión dice `true` con `false` observado |
| LA-freno-sin-dias-cfg | Los días salen de `precio_freno_dias_error` | `dias=freno_dias` → `dias=3` | `test_freno_dos_dias_con_config_2` (B.6) | MUERTO: con config 2 y 2 días no frena |
| LA-cli-escritas | El CLI imprime el `escritas` real | `escritas={resumen.escritas}` → `escritas=0` | `test_cli_precio_imprime_escritas_reales` | MUERTO: imprime `escritas=0` con 2 escritas |
| LA-escritas-cuenta | `escritas` cuenta cambios reales (saltado no cuenta) | `1 if res.id_cambio is not None else 0` → `1` | `test_live_saltado_no_cuenta_escrita` | MUERTO: el saltado cuenta 1 |

Revalidación A5 sobre r1: A5-1..A5-15 MUERTOS (A5-3 con el `or True` en el
`reservar` de `_fase3_uno`; A5-9 con `limitador=None` en `cambiar_precio`;
A5-11 con el test espía del cupo).

## r0 (sobre `32ccf41`)

Un mutante por regla y borde que la corrida protege (fila A.5 del plan +
puntos a–h del brief). Corridos con base real (`ORBIT_TEST_DSN` local),
uno por uno con restauración verificada por hash, `-p no:cacheprovider` y
caché de bytecode fresca por corrida (`PYTHONPYCACHEPREFIX=$(mktemp -d)`;
reusar el prefijo dio una muerte falsa en A5-6: el `.pyc` viejo hizo pasar
el test con el mutante puesto; con prefijo fresco muere). Sembrados a mano
y **revertidos sin commit**. **Cero sobrevivientes.**

A5-3 y A5-4 sobrevivieron primero: `repartir_cupo` (reglas puras, defensa
en profundidad) re-ordena y pre-limita, así que el test de resultado no
discrimina el cableado de la fase 2/3. Se cerraron con tests de cableado
(`test_fase3_respeta_reserva_denegada`, `test_fase2_aplica_en_orden_de_prioridad`)
y contra ellos los mutantes mueren.

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| A5-1 | Lock: dos hilos, exactamente un PATCH (claim + advisory) | `corrida._tomar_lock`: `return` antes del claim | `test_dos_hilos_exactamente_un_patch` | MUERTO (exit 1): el segundo hilo no sale `ocupado` |
| A5-2 | Orden S5: el cambio nace `pendiente` (el ack decide `enviado`) | `precio_write.cambiar_precio`: INSERT nace `'enviado'` en vez de `'pendiente'` | `test_live_subir_aplica_un_patch` | MUERTO (exit 1): el trigger `precio_cambio_nacimiento` revienta (`CheckViolation`) |
| A5-3 | Cuota: la fase 3 cobra reserva atómica por candidata | `or True` al `cuota_mod.reservar(...)` de fase 3 | `test_fase3_respeta_reserva_denegada` (cierre; el de resultado no discrimina: `repartir_cupo` ya pre-limita) | MUERTO (exit 1): con reserva negada aplica igual (`escritas=1`) |
| A5-4 | Prioridad: fase 2 ordena `\|m-goal\|*ingreso` desc | quitar el `-` del sort de fase 2 | `test_fase2_aplica_en_orden_de_prioridad` (cierre; con prioridades empatadas el mutante es invisible) | MUERTO (exit 1): `lineas` sale `[B, A]` en vez de `[A, B]` |
| A5-5 | Huérfana: la `pendiente` no-reversa se cierra a `error` al arrancar | `huerfanas = 0` (sin `cerrar_huerfanas`) | `test_huerfana_se_cierra_sin_get_y_sale_en_resumen` | MUERTO (exit 1): la huérfana sigue `pendiente` |
| A5-6 | Cierre: el `enviado` de ayer se cierra ANTES de decidir | `cerrados = {}` (sin `cerrar_por_observacion`) | `test_enviado_de_ayer_se_cierra_antes_de_decidir` | MUERTO (exit 1): el cambio de ayer queda `enviado` |
| A5-7 | Freno S6: `error` en TODOS los días previos, no en uno | `freno_por_error`: `all(...)` → `any(...)` | `test_freno_tres_dias_de_error_unidad` | MUERTO (exit 1): con 2 días de error ya frena |
| A5-8 | Sombra: el virtual nace `confirmado`/`virtual`, cero PATCH | sombra nace `'pendiente'` en vez de `'confirmado'` | `test_shadow_subir_crea_virtual_sin_patch` | MUERTO (exit 1): el virtual no nace `confirmado` |
| A5-9 | Cubo: la corrida aplica con el limitador inyectado (uno por plataforma) | `limitador=None` en la llamada a `cambiar_precio` | `test_corrida_usa_un_solo_cubo_por_plataforma` | MUERTO (exit 1): cero consumos en el cubo espiado |
| A5-10 | Segunda corrida del día no decide (UNIQUE + salto) | `if lid in decididas:` → `if False:` | `test_segunda_corrida_del_dia_no_decide` | MUERTO (exit 1): decide de más |
| A5-11 | Reversas del día consumen cupo (las cuenta el dueño) | `reversas = 0` (sin `reversas_hoy`) | `test_reversa_del_dia_consume_cupo` | MUERTO (exit 1): la reversa no reduce el cupo |
| A5-12 | Candado: corrida solo importa lo permitido (ni `httpx`) | `corrida.py` + `import httpx` | `test_corrida_solo_importa_permitido` | MUERTO (exit 1) |
| A5-13 | Candado: cero literales float en `app/precio` (dinero = Decimal) | `corrida.py` + `__MUTANTE__ = 0.5` | `test_reglas_sin_decimal_no_hay_float` | MUERTO (exit 1) |
| A5-14 | Crontab pinzado: 13:10 UTC con flock y log | `docs/DEPLOY.md`: `10 13` → `11 13` | `test_linea_crontab_en_deploy` | MUERTO (exit 1) |
| A5-15 | Umbral: cap fuera de cota [0, 20] aborta al arrancar | `cuota.validar_cap`: `if not 0 <= cap <= 20:` → `if False:` | `test_umbral_fuera_de_cota_aborta_al_arrancar` | MUERTO (exit 1): cap 21 no aborta |

## Notas

- A5-6 pasó una vez con el mutante puesto por `.pyc` viejo (mismo
  `PYTHONPYCACHEPREFIX` para toda la corrida). Con prefijo fresco por
  mutante muere. Lección: pycache fresca por corrida, como pide el brief.
- A5-2 lo mata la base (trigger `precio_cambio_nacimiento`), no el
  código: defensa en profundidad declarada.
- A5-9 valida la inyección del cubo: la corrida no construye `CuboTasa`
  (el 0.5/s de Pricing vive en el CLI); el mutante simula ignorar el
  inyectado.
