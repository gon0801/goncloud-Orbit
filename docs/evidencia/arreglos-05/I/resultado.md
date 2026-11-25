# Resultado del bloque I: Ajustes rechaza una cuota que no puede guardar

- Base: `a9885448ee04a1fe38a3be25c69c13985e140c76` (master con BASE, A, B, C,
  D, E, F, G y H integrados y desplegados).
- Rama: `arreglos-05/i`.
- Modelo (antes del codigo): `566ea48f`.
- Cabeza de la implementacion: `eb8817077ae7ecd25f3ca706b6aba1cdda8e0417`
  (codigo + regresiones + ficha).
- Evidencia: el commit que contiene este archivo.
- Acepta corrida en `eb881707` con arbol con seguimiento limpio.
- Contrato: `docs/evidencia/arreglos-05/DISENO.md`, seccion "I-K. Configuracion,
  retiro de modos y fees" (parte I).
- Aviso del bloque: Ajustes muestra el error si una cuota no es un entero valido
  y deja de decir Guardado cuando la ignora.
- Modelo por camino: `I/estados.md`.

## Que cambia para el dueno y para quien mantiene

Para el dueno: si escribe `1,000`, `1.5`, `-1` o cualquier texto que no sea un
entero no negativo en una cuota de Ajustes, ve un error en la misma tarjeta y la
pantalla no guarda nada. Antes, `Number("1,000")` daba `NaN`, el JSON lo
convertia en `null`, el servidor lo saltaba y la pantalla decia `Guardado`
habiendo ignorado la cuota.

Para quien mantiene:

- **Un solo validador de cuotas.** `app/config_write._cap_entrante` pasa la
  cuota entrante por `apply_cuota.cap_desde_settings`, el mismo lector que ya
  usaba el motor y la validacion de la config resultante. `null` explicito,
  booleanos, fracciones, texto ilegible, negativos y valores fuera de
  `CAP_MAXIMO` se rechazan como `SettingsInvalido` (422).
- **Omitido no es null.** Una llave ausente en `caps` conserva el valor; una
  llave presente con `null` es invalida. `CuerpoSettings.caps` pasa el valor
  crudo (`Any`) a proposito: pydantic ya no convierte `true` en `1` ni borra la
  diferencia entre omitido e invalido.
- **Sin version ni snapshot en el rechazo.** `guarda_config` calcula la config
  antes de insertar; el rechazo ocurre en `proxima_config`, asi que no hay fila
  nueva. El camino optimista (`base_config_version_id` contra la vigente, 409) y
  el snapshot append-only no cambian.
- **El formulario conserva el texto crudo.** Los caps pasan de
  `input[type=number]` a `input[type=text][inputmode=numeric]`. Medido con Edge:
  en `type=number` un `1,000` se normaliza a `1000` y el navegador ya no puede
  distinguir la coma. El JS valida `^[0-9]+$` sobre lo tecleado y detiene el
  envio con un error visible antes de serializar.
- **`null` valido de otros campos sigue intacto.** El rechazo de `null` aplica
  solo a cuotas; `resumen_acos_objetivo` (quitar meta) y el interruptor del
  margen conservan su contrato.

## Fila I.1: validacion antes de serializar

| Fila | Comando | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| I.1 | `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N i` y los caminos HTTP de `tests/test_arreglos05_i_cuotas.py` | `eb881707` | `N i`: 19 passed. `null`/bool/fraccion/texto: 422 sin fila nueva; omitido conserva y guarda el resto; entero valido: 200 y una fila; version vieja: 409 y cero cambios. Formulario real (`I/adverso.py`): error visible, cero POST, sin version nueva. | La cuota que no se puede guardar ya no se ignora en silencio ni se convierte en `null`; el dueno ve el motivo. | No hay DDL que revertir. Un binario anterior lee y escribe las mismas claves de config; `tools/despliegue/rollback.sh` devuelve el binario previo. El cambio de `type=number` a `type=text` en el HTML es del mismo binario. |

## Fila I.2: control y ficha

| Fila | Comando | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| I.2 | `tests/test_config_write.py -k arreglos01_x` (control 01X); `orbit-verify launch`, `doctor`, `drive-settings`, `cleanup`; `I/adverso.py` con Edge | `eb881707` | 01X: 1 passed. `doctor`: `ok true`. `drive-settings`: 16 comprobaciones en `true`. Adverso: `error_visible`, `cero_post`, `sin_version_nueva`, `sin_recarga`, `cap_conserva_texto` y control con `7` en `true`. | La banda manual 01X sigue cerrada; TEMPORADA y apagadores no se tocan; la ficha de Settings refleja el nuevo campo y su error. | Igual que I.1: no hay datos nuevos que deshacer. |

## Rojo y verde

Rojo contra el padre real `a9885448`, en `I/rojo.txt`: `N i` = **4 failed, 15
passed**. Fallan los caminos que el defecto permite:

1. `test_arreglos05_i_proxima_config_rechaza_cuota_que_no_puede_guardar[None]`:
   el padre ignora la cuota nula y guarda el target que la acompanaba.
2. `test_arreglos05_i_proxima_config_normaliza_el_entero_valido`: el padre
   escribia `"007"` tal cual.
3. `test_arreglos05_i_api_null_explicito_en_cuota_rechaza_sin_version`: 200 con
   `created: true` y label `target manual 20 -> 25`; la cuota nula desaparecio.
4. `test_arreglos05_i_api_booleano_en_cuota_rechaza_sin_version`: 200 con
   `cap bid 10 -> 1` (pydantic convertia `true` en `1`).

Los otros caminos (fraccion/texto ilegible) ya los rechazaba pydantic o la
validacion final; quedan como guardas del camino nuevo y se documentan como
controles, no como rojos.

Verde en `eb881707`, en `I/verde.txt`: `N i` = **19 passed**.

## Acepta (salida completa en `I/acepta.txt`, partes en `I/acepta-1..3.txt`)

- Parte 1: `N i` = 19 passed; control 01X = 1 passed; arquitectura = 99 passed,
  2 skipped (la omision declarada de la regla 99:2).
- Parte 2: configuracion y settings de escritura = 52 passed; endpoint de
  escritura = 52 passed.
- Parte 3: dashboard de settings = 81 passed; UI aplicable (`-k "settings or
  config or cap"`) = 22 passed; gitleaks de `a9885448..eb881707` = sin fugas.
- Total: 0 comandos que no pasaron.

## Foto de produccion (solo lectura)

Comando: `bash docs/evidencia/arreglos-05/I/lee_previa.sh > I/previa.txt` (rol
`orbit_read`, transaccion `read_only`, por `docs/evidencia/arreglos-01/lee_prod.sh`;
2026-11-25T07:09Z, SHA `eb881707`). Lo medido:

- Config vigente: id 63, creada 2026-11-22, label de la pantalla de Settings.
- 8 cuotas vigentes (2 plataformas x 4 kinds), todas presentes y de tipo JSON
  `number` con valor `1000000`: `amazon_us` y `amazon_mx` en `bid`, `pause`,
  `negative` y `harvest`.
- `cuotas_por_tipo`: 8 numericas, 0 texto de digitos, 0 texto no entero, 0
  nulas, 0 ausentes.
- `cuotas_fuera_de_rango`: 0.
- Historicas: 440 claves de cuota en 60 versiones; 0 booleanas, 0 nulas, 0
  fracciones y 0 texto no entero.

Consecuencias: la forma actual es la que el lector canonico acepta; no hay deuda
historica que migrar ni backfill. El defecto era de la pantalla y de la
frontera HTTP, no de los datos guardados.

## Recorrido de Settings y caso adverso

Detalle en `I/ui.md` y archivos en `I/ui/`. El helper sin adaptar corrio
`launch`, `doctor`, `drive-settings` y `cleanup` en WSL (mismo SHA) porque su
introspeccion de proceso es POSIX. El caso adverso corrio con Edge real en
Windows contra una segunda instancia del mismo SHA: `1,000` deja
`Error: la cuota de bid debe ser un numero entero sin comas ni decimales (llego
1,000).`, cero POST, cero recarga y cero `config_version` nueva; con `7` el
formulario si intenta el POST.

## Migracion

I no crea migracion. No hay DDL: el cambio es validacion en `config_write.py`,
forma del modelo HTTP en `api_write.py`, el formulario en `settings.js` y el
`type` del input en `settings.html`. No se tocan `0001` ni `0002` ni ninguna
migracion existente; el selector del instalador sigue sin cambios.

## Reversa

1. **Reversa de datos:** no hace falta. No se reescribe historia; una cuota
   valida se sigue guardando como texto decimal en la misma clave
   `ads_apply_cap_<plataforma>_<kind>`. Un binario anterior lee esa forma.
2. **Reversa de despliegue:** `MOTOR_SHA=<sha desplegado> bash
   tools/despliegue/rollback.sh`. Solo deshace lo anotado en `ultimo.env` para
   ese `MOTOR_SHA` y sale 0 sin deshacer con otro SHA. Antes de degradar,
   detener nuevas mutaciones y conciliar.
3. **Reversa de UI:** volver a `type=number` reintroduce la perdida de la coma;
   no se recomienda sin restaurar tambien la validacion de texto.

## Archivos cambiados

- `app/config_write.py`: `_cap_entrante` como unico camino de la cuota entrante
  con `cap_desde_settings`; `null` explicito se rechaza, el entero valido se
  canonicaliza y el loop de `caps` deja de saltar `None`.
- `app/api_write.py`: `CuerpoSettings.caps` pasa el valor crudo al escritor; ya
  no coacciona booleanos ni `null` antes del validador canonico.
- `app/static/js/settings.js`: valida `^[0-9]+$` antes de serializar, muestra el
  error visible y retorna sin `postJson`; solo convierte a `Number` lo validado.
- `app/templates/settings.html`: caps `type="text"` con `inputmode="numeric"`
  para conservar el texto crudo.
- `tests/test_arreglos05_i_cuotas.py`: 19 regresiones del bloque.
- `.cursor/skills/verify-orbit/features/settings.md`: ficha actualizada con
  `settings-cuotas`, el nuevo tipo de input y el error visible.
- Evidencia en `docs/evidencia/arreglos-05/I/`.

No se toco `app/apply_cuota.py` (el lector canonico se reutiliza), ni
`app/optimizer/*`, ni TEMPORADA, ni migraciones, ni la banda 01X.

## Fallos previos de `master` en esta PC (no de este bloque)

- `test_fabrica_f2_hermanas` x24, `test_fabrica_f2_biblioteca` x16,
  `test_harvest_destino` x3, `test_d3_adversary` x2, `test_notifica` x3,
  `test_orbit07_m1` x1 y
  `test_precio02_escenario_meli.py::test_ingesta_meli_sin_cliente_salta_meli_y_sigue`
  x1: rojos previos declarados por el brief; no se ejecutaron aqui por ser de
  otros modulos, igual que en G.
- `WinError 206` de Node en las pruebas de frontend: el comando `node -e` supera
  el limite de Windows. Se reprodujo con el codigo del padre antes del commit:
  `test_ola_a2_frontend.py` + `test_verify_orbit.py` = 4 failed, 19 passed en
  ambos arboles, y el cuarto fallo (`css_390`) tambien es del navegador.
- `tests/test_ui.py` completo: 91 passed, 7 failed, los 7 con el mismo
  `WinError 206`; ninguno toca Settings.

## Seguimiento (P2)

- (a) Un JSON `1000.0` (float integral) ahora se rechaza; el lector canonico ya
  solo acepta `int` o texto de digitos. Es mas estricto y consistente con
  "cuotas son enteros"; la UI envia enteros.
- (b) `value="{{ valor or '' }}"` en el template sigue mostrando vacio una cuota
  de `0` (comportamiento previo, fuera de este bloque).
- (c) El helper `orbit-verify` sigue siendo POSIX en `_pid_vive`/`_cmdline`; el
  recorrido formal se hizo en WSL y el adverso con Edge en Windows. Una mejora
  de portabilidad del helper es de su propio dueno, no de I.
- (d) Los demas campos numericos de Settings (`target_manual_pct`, confianzas,
  ACoS del Resumen) no se migraron al patron de texto crudo; el contrato del
  bloque es cuotas y el backend ya los valida.
- (e) El mensaje heredado de `cap_desde_settings` dice "en la config vigente"
  aunque el valor venga de la edicion. Es texto del lector canonico y se prefirio
  no duplicar mensajes; el dueno ve el rechazo igual.

## Fase 2: despliegue y recibo

- PR #769 integrado. Merge `0244dc381c18b59b76112c68317738114d0b6ff8` en
  `master`; el revisor aprobo `3d9fd18b` sin P0/P1.
- `SHA_PREVIO=889fe5cac5a54178eff8f0562f6e418ecf6d4ef8` (bloque H).
- Entorno limpio: `env -i PATH=/ucrt64/bin:/usr/bin:/bin HOME=$HOME`.
- Fecha: 2026-11-25 (UTC). Respaldo: `predeploy-20261125-075842`.

### Ensayo de solo lectura

`MOTOR_SHA=0244dc38... bash tools/despliegue/desplegar.sh --ensayo`
(`I/despliegue.txt`):

- Guardas libres: `true|0|0|0|0`.
- Monotonia: habia que desplegar; migraciones 0076-0099 **aplicadas**, ninguna
  pendiente. I no agrega DDL: la `0099_arreglos05_tope_ciclo.sql` sigue siendo la
  ultima.
- `claves: ninguna cambia`; bloque de cron igual al canonico;
  `refresh_estimacion.sh` ya era el del SHA.
- `== ENSAYO OK`.

### Despliegue real

`MOTOR_SHA=0244dc38... bash tools/despliegue/desplegar.sh` (`I/despliegue.txt`):

- Respaldo `predeploy-20261125-075842` completo.
- Migraciones 0076-0099 `ya aplicada` (ninguna se ejecuto).
- `md5 OK: 304 archivos`; imagen construida aparte y contenedor recreado;
  `/health 200`; `despliegue/sha = 0244dc38...`.
- Cron igual al canonico; ninguna clave cambia.
- `== DESPLEGADO 0244dc38...`.

### Checklist

`MOTOR_SHA=0244dc38... bash tools/despliegue/checklist.sh` (`I/checklist.txt`):

- `== CHECKLIST VERDE 0244dc38...`: SHA desplegado, migraciones 0076-0099
  aplicadas (la ultima, `0099_arreglos05_tope_ciclo.sql`), bloque de cron
  canonico, `refresh_estimacion.sh` del SHA y `/health 200`.

### Prueba real sobre el SHA integrado

`I/prueba-real.txt`:

- `N i`: **19 passed** (6496 deselected).
- Control 01X (`tests/test_config_write.py -k arreglos01_x`): **1 passed**.

### Recorrido de Settings y caso adverso

`I/ui-fase2.md` y `I/ui-fase2/`: `launch`, `doctor` (`ok true`),
`drive-settings` (16 comprobaciones en `true`) y `cleanup` en WSL con el arbol
del SHA integrado; el adverso con Edge real en Windows (`I/adverso-fase2.txt`)
repite `1,000` -> error visible, cero POST, cero version nueva y el control con
`7`.

### Lectura RO de esquema y resultado

`I/previa-fase2.txt` (rol `orbit_read`, `read_only`, via `lee_prod.sh`;
2026-11-25T08:12Z):

- `config_version`: columnas `id`, `created_at`, `label`, `settings` (JSONB);
  triggers `config_append_only` y `config_append_only_truncate` con
  `prohibir_mutacion()`.
- Sonda de la 0099: `True` (el CHECK de `decision_sin_aplicar` admite
  `tope_ciclo`).
- 8 cuotas vigentes, todas `number` con `1000000`; 0 texto, 0 nulas, 0 ausentes.
- 60 versiones de config (ids 2..63).

### Huella del codigo desplegado

`I/huella-codigo.txt`: los cuatro archivos que cambia I
(`app/config_write.py`, `app/api_write.py`, `app/static/js/settings.js`,
`app/templates/settings.html`) tienen el **mismo md5 en local, host y
contenedor**. `sha_desplegado = 0244dc38...`, `ESTADO=verificado`,
`/health 200` y las marcas del bloque (`_cap_entrante`, `cuota de`,
`type="text" name="cap_`, `Cuotas por kind`) presentes en el contenedor.

### Migracion y reversa

- I no trae migracion; la 0099 sigue siendo la ultima aplicada. No hay DDL que
  deshacer.
- Reversa de despliegue: `MOTOR_SHA=0244dc381c18b59b76112c68317738114d0b6ff8
  bash tools/despliegue/rollback.sh` deshace solo lo anotado en `ultimo.env`
  para ese SHA (codigo, sin claves ni cron en esta corrida) y sale 0 sin
  deshacer con otro SHA. La reversa de efectos y la de despliegue son
  operaciones distintas.
- Reversa de datos: no hace falta; no se reescribio ninguna `config_version`.

### Seguimiento (P2) de la revision

Las cinco notas del revisor quedaron en el cuerpo del PR #769, seccion
"Seguimiento (P2)", sin commit: prueba de regresion del guard JS en la suite,
mandar el texto ya validado en vez de `Number(...)`, `if valor is None`
inalcanzable en `_cap_entrante`, caracteres corruptos en `I/previa.txt` linea 7
y el mensaje heredado de `cap_desde_settings` / rechazo de `1000.0`.

### Recibo

`despliegue.txt`, `checklist.txt`, `prueba-real.txt`, `previa-fase2.txt`,
`huella-codigo.txt`, `ui-fase2.md`, `ui-fase2/` y `adverso-fase2.txt` en esta
carpeta.
