# Resultado del bloque J: retirar modos sin uso conservando historia y apagadores

- Base: `db9d2041c3250a145615b0249d8b809593c33a75` (master con BASE, A, B, C,
  D, E, F, G, H e I integrados y desplegados).
- Rama: `arreglos-05/j`.
- Modelo (escrito antes del commit de codigo): `J/estados.md`.
- Cabeza de la implementacion: `55568efa` (codigo + regresiones + fixture y
  las correcciones de revision).
- Evidencia de fase 1: el commit que contiene este archivo, sobre `55568efa`.
- Fase 2: este archivo y los recibos de `J/` sobre el master integrado
  `7cc88976`.
- Contrato: `docs/evidencia/arreglos-05/DISENO.md:243-248` y la decision 5 de
  `docs/evidencia/arreglos-02/DISENO.md:417-432`.
- Aviso del bloque: se retiraron caminos obsoletos comprobados, conservando los
  controles y la reproduccion de decisiones antiguas.

## Que cambia para el dueno y para quien mantiene

Para el dueno: si la configuracion de una plataforma pierde
`ads_bid_politica_<plataforma>` o trae un valor ilegible, el motor ya no mueve
ni pausa ninguna hoja de esa plataforma. Antes, con la clave ausente, seguia
creando pausas por la era bandas. La meta por producto deja de ofrecer
`practica`: se queda apagada o en vivo, y la pausa por producto conserva su
practica.

Para quien mantiene:

- **Una sola decision viva.** `politica.decide(CasoHoja)` es el unico camino de
  hoja. Desaparecieron `_pendiente_pause_apagada`, la rama `politica is None` de
  `_procesa_decisora`, la rama equivalente de `_juicio_hoja` y el modo shadow de
  la meta por producto con sus tres `decide_con` por hoja.
- **Cosecha sin ancla congelada.** Aunque el motor de bids este apagado, la hoja
  sigue resolviendo y guardando su target en `target_acos_ciclo`; la cosecha no
  se queda con la ultima meta familiar. Solo se omite la decision de hoja.
- **Historia intacta.** `decide_bid_era_bandas`, `_replay_hygiene` y las ramas de
  producto/temporada de `replay.py` no se tocaron. El fixture
  `tests/fixtures/arreglos05_j_pausa_historica.json` es una pausa real de
  produccion (decision 2736, 2026-10-05) y sigue reproduciendo
  `("pause", None, None)`.
- **Reversas y apagadores conservados.** `auto03`, `ads_producto_pausa_*`,
  `ads_optimizer_mode`, `ads_bid_economia_*`, la proteccion de venta y TEMPORADA
  no se retiran. `ads_pause_sin_cooldown_bid` era el unico flag sin lector.
- **UI honesta.** Productos ya no anuncia "se calcula y no cambia ninguna puja";
  `productos-modo --meta` solo acepta `off` y `live`, y `config_write` rechaza
  `shadow` para meta. La pausa conserva `off`, `shadow` y `live`.

## Fila J.1: inventario antes de borrar

| Fila | Comando | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| J.1 | `bash docs/evidencia/arreglos-05/J/previa.sh > J/previa.txt` (rol `orbit_read`, transaccion `read_only`, via `lee_prod.sh`, 2026-11-25) y `J/modos.tsv` | `db9d2041` (foto) | Config 63: `niveles_v3` MX/US, meta `live`, pausa `shadow`, economia `producto_directo_v1`, `economia_cambio_v2`, `ads_pause_sin_cooldown_bid=true`. Decisiones: 36 pause y 2723 bid sin `inputs.politica` (2026-08-24..2026-10-10); 68 bid y 2 pause con `inputs.politica`; 41 pause `motor=producto`. 42 ciclos con productos en `modo_meta=live`, 0 en shadow. | Separa usado, retirable y conservado antes de borrar. Rechaza retirar `auto03` (reversa de B-H1), la pausa shadow y TEMPORADA. | Solo lectura; no muta nada. |

## Fila J.2: retiro y conservacion

| Fila | Comando | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| J.2 | `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N j` y `J/acepta.sh` partes 1-3 | `55568efa` | `N j`: 6 passed. Acepta: parte 1 (N j, arquitectura 99+2s, estados 209, ciclo 53), parte 2 (politica/eras/cooldown/config 131, settings/API 77, economia 15, notificaciones 74), parte 3 (productos 117, proteccion 15, aplicador/F/G 65, UI 5, instalador 4). Cero comandos fallidos. | Sin politica no hay mutacion; la meta por hoja se sigue guardando para la cosecha; meta shadow retirada; flag muerto retirado; replay, reversas, apagadores y pausa shadow conservados. | No hay DDL. Un binario anterior lee las mismas claves y decisiones; `tools/despliegue/rollback.sh` devuelve el binario previo. Los datos historicos no se reescriben. |

## Rojo y verde

Rojo contra el padre real `db9d2041`, en `J/rojo.txt`: `N j` = **3 failed, 2
passed**. Fallan:

1. `test_arreglos05_j_sin_politica_no_muta_y_cuenta_el_skip`: el padre seguia
   creando `(4, 'pause')` sin `ads_bid_politica`.
2. `test_arreglos05_j_meta_shadow_retirada_y_pausa_shadow_conservada`: el padre
   aceptaba `ads_producto_meta_amazon_us = shadow`.
3. `test_arreglos05_j_ui_productos_no_anuncia_la_meta_en_practica`: el padre
   servia la frase del modo retirado.

Los dos verdes del padre son controles de conservacion: politica ilegible ya
abortaba y la pausa historica ya reproducia.

Verde en `55568efa`, en `J/verde.txt`: `N j` = **6 passed**.

## Correccion de revision (Devin, ronda 1)

Devin aviso que al apagar `ads_bid_politica` tambien se dejaba de escribir
`target_acos_ciclo`, que la cosecha usa como ancla. Reproduccion:
`test_arreglos05_j_sin_politica_conserva_el_target_para_cosecha` fallo en
`73787dd8` con `assert 3 in {}`. Correccion en `74e4aebe`: la hoja resuelve y
guarda su target antes del skip de `politica_apagada`; solo no crea decision.
La misma prueba y `N j` (6 passed) quedan verdes; ciclo, target, G y proteccion
pasan 92/92.

## Correccion de revision (notificaciones, ronda 2)

El revisor aviso que `tests/test_notifica.py::test_ciclo_canal_ok_avisos_por_corte_y_digest_unico_sin_nota`
sembraba `_siembra_maestra` sin politica y esperaba tres cortes con pausa. La
prueba quedo migrada a `ads_bid_politica_amazon_us = niveles_v3`, como el resto
de los vecinos que prueban la pausa viva. `test_notifica.py` completo da
**74 passed, 3 failed**: los tres son los rojos previos de master declarados en
el brief (`test_avisar_campanas_*`), ajenos a J. `J/notifica.txt` guarda esa
corrida completa; `J/acepta.sh` parte 2 corre el archivo sin esos tres rojos
previos y da 74 passed.

## Acepta (salida completa en `J/acepta-1.txt`, `-2.txt` y `-3.txt`)

- Parte 1: `N j` 5 passed; arquitectura 99 passed, 2 skipped (omision declarada
  de la regla 99:2); estados 209 passed; ciclo 53 passed.
- Parte 2: politica/eras/cooldown/config 131 passed; settings/API 77 passed;
  economia/orbit07_m2 15 passed.
- Parte 3: productos 117 passed; proteccion 15 passed; aplicador/target/F/G 65
  passed; UI render puro 5 passed; instalador (`-k arreglos05`) 4 passed.
- Total: 0 comandos que no pasaron.

## Foto de produccion (solo lectura)

`J/previa.txt` (2026-11-25, config 63):

- `ads_optimizer_mode=live`; `ads_bid_politica` `niveles_v3` en MX y US.
- `ads_producto_meta` `live` en MX y US; 42 ciclos con productos en `live`, 0 en
  `shadow`; las 2 configs historicas `shadow` no llegaron a un ciclo.
- `ads_producto_pausa` `shadow` en MX y US; 41 decisiones `motor=producto` en
  shadow (2026-11-08..2026-11-25).
- `ads_producto_economia` `producto_directo_v1` en MX y US; `auto03` ausente en
  45/46 versiones y nunca escrito.
- `ads_pause_sin_cooldown_bid=true` en 41 versiones; ninguna decision lo congela.
- Decisiones sin `inputs.politica`: 36 pause y 2723 bid. Con `inputs.politica`:
  68 bid y 2 pause. La pausa real 2736 se guardo como fixture.

## Migracion

J no crea migracion. No hay DDL: el cambio es codigo de ciclo, lectores de
config, validacion de producto y una frase de plantilla. `config_version` es
append-only y conserva la clave historica `ads_pause_sin_cooldown_bid`; borrarla
no cambia ninguna decision. No se tocan `0001` ni `0002` ni ninguna migracion
existente.

## Reversa

1. **Reversa de datos:** no hace falta. No se reescribe historia; las decisiones
   antiguas siguen en `decision.inputs` y las evaluaciones en
   `evaluacion_producto_ciclo.resultado.sombra_hojas`.
2. **Reversa de despliegue:** `MOTOR_SHA=<sha desplegado> bash
   tools/despliegue/rollback.sh`. Solo deshace lo anotado en `ultimo.env` para
   ese `MOTOR_SHA`; antes de degradar, detener nuevas mutaciones y conciliar.
3. **Reversa de comportamiento:** volver a habilitar la era bandas o la meta en
   practica exige un binario anterior; no hay interruptor nuevo que las reviva.
   El contrato J decide que no se conservan como modos.

## Archivos cambiados

- `app/cycle_recorrido.py`: sin rama `politica is None`; skip
  `politica_apagada` antes de resolver target; sin `decide_con` por hoja.
- `app/cycle_serializacion.py`: fuera `_pendiente_pause_apagada`.
- `app/cycle_sombra.py`: fuera `_juicio_hoja` y la rama shadow de
  `_anota_sombra`; queda el registro live de `sombra_hojas`.
- `app/cycle.py`: fuera los re-exports de las funciones retiradas.
- `app/optimizer/productos.py`: `MODOS_POR_TIPO`; meta solo `off|live`, pausa
  `off|shadow|live`.
- `app/optimizer/settings_lectura.py` y `app/optimizer/goals.py`: fuera el lector
  y el re-export de `ads_pause_sin_cooldown_bid`.
- `app/optimizer/bid.py`: docstring de `decide_pause` actualizado.
- `app/config_write.py`: rechaza `shadow` para meta antes de escribir.
- `app/cli_productos.py`: `--meta` solo `off|live`.
- `app/templates/productos_sin_clics.html`: sin la frase del modo practica.
- `tests/test_arreglos05_j_modos.py` y
  `tests/fixtures/arreglos05_j_pausa_historica.json`: regresiones y fixture.
- Tests vecinos actualizados para usar `niveles_v3` donde probaban la pausa viva,
  y retirados los que solo probaban los caminos retirados.
- Evidencia en `docs/evidencia/arreglos-05/J/`.

## Fallos previos o de entorno (no de este bloque)

- `test_ui_productos.py` completo: 15 fallos por `WinError 206` (el comando de
  Node supera el limite de Windows), ya documentados en I. Los renders puros
  pasan.
- `test_notifica.py` completo: 3 fallos previos de master en
  `test_avisar_campanas_*` (`tuple index out of range` del mock de conexion),
  declarados en el brief. El resto, 74 pruebas, pasa; la prueba migrada de J
  esta entre ellas.
- `test_despliegue_motor.py` completo: falla en el paso de respaldo del simulador
  con `cp: cannot stat 'app'` y en `awk` de Windows; no toca el codigo de J. El
  selector del plan (`-k arreglos05`) pasa 4/4.

## Fase 2: despliegue y recibo

- PR #771 integrado. Merge `7cc88976bf509c47bfb08224287432be08377542` en
  `master`; el revisor aprobo `a84a9c63` sin P0/P1 y la cabeza de codigo era
  `55568efa`.
- `SHA_PREVIO=0244dc381c18b59b76112c68317738114d0b6ff8` (bloque I).
- Fecha: 2026-11-25 (UTC). Respaldo: `predeploy-20261125-121402`.

### Ensayo de solo lectura

`MOTOR_SHA=7cc88976... bash tools/despliegue/desplegar.sh --ensayo`
(`J/despliegue.txt`):

- Guardas libres: `true|0|0|0|0`.
- Monotonia: habia que desplegar; migraciones 0076-0099 **aplicadas**, ninguna
  pendiente. J no agrega DDL: la `0099_arreglos05_tope_ciclo.sql` sigue siendo la
  ultima.
- `claves: ninguna cambia`; bloque de cron igual al canonico;
  `refresh_estimacion.sh` ya era el del SHA.
- `== ENSAYO OK`.

### Despliegue real

`MOTOR_SHA=7cc88976... bash tools/despliegue/desplegar.sh`
(`J/despliegue.txt`):

- Respaldo `predeploy-20261125-121402` completo.
- Migraciones 0076-0099 `ya aplicada` (ninguna se ejecuto).
- `md5 OK: 304 archivos`; imagen construida aparte y contenedor recreado;
  `/health 200`; `despliegue/sha = 7cc88976...`.
- Cron igual al canonico; ninguna clave cambia.
- `== DESPLEGADO 7cc88976...`.

### Checklist

`MOTOR_SHA=7cc88976... bash tools/despliegue/checklist.sh`
(`J/checklist.txt`):

- `== CHECKLIST VERDE 7cc88976...`: SHA desplegado, migraciones 0076-0099
  aplicadas (la ultima, `0099_arreglos05_tope_ciclo.sql`), bloque de cron
  canonico, `refresh_estimacion.sh` del SHA y `/health 200`.

### Prueba real sobre el SHA integrado

`J/prueba-real.txt`:

- `N j`: **6 passed**.
- UI de productos: 4 passed (render de la plantilla y `test_ui.py -k productos`).

### Produccion: modos retirados y conservados

`J/produccion.txt` (solo lectura, 2026-11-25T12:20Z):

- La frase retirada "se calcula y no cambia ninguna puja" aparece **0** veces.
- `Meta por producto` y `Pausa por producto` siguen en la pantalla.
- API del ultimo ciclo: `modo_meta=live`, `modo_pausa=shadow`, `con_meta=1`.
- Settings: `motor_bid` solo `niveles_v3` y `apagado`.
- CLI: `--meta {off,live}` y `--pausa {off,shadow,live}`.

### Lectura RO de esquema

`J/ro_esquema.txt` (rol `orbit_read`, `read_only`):

- `current_database=orbit`, `current_user=orbit_read`.
- Tablas `decision`, `config_version` y `evaluacion_producto_ciclo` presentes.
- `config_version` conserva `id`, `created_at`, `label`, `settings` (jsonb) y los
  triggers `config_append_only` y `config_append_only_truncate`.
- La sonda de la 0099 da `tope_ciclo` en
  `decision_sin_aplicar_motivo_check`; la 0099 sigue siendo la ultima.

### Huella del codigo

`J/huella-codigo.txt`:

- Los 11 archivos que cambia J tienen el **mismo md5 en local, host y
  contenedor**.
- `sha_desplegado = 7cc88976...`, `ESTADO=verificado`, `/health 200`.
- En el contenedor: `MODOS_POR_TIPO=2`, `_pendiente_pause_apagada=0` y
  `frase_meta_practica=0`.

### UI

`J/ui.md`: `orbit-verify` no se pudo ejecutar en esta sesion Windows (helper
POSIX y WSL sin Postgres 16/sudo). El equivalente fue el render de la plantilla y
el HTML/API real de produccion; no se declara un pase que no se hizo.

### Migracion y reversa

- J no trae migracion; la 0099 sigue siendo la ultima aplicada. No hay DDL que
  deshacer.
- Reversa de despliegue: `MOTOR_SHA=7cc88976... bash
  tools/despliegue/rollback.sh` deshace solo lo anotado en `ultimo.env` para ese
  SHA (codigo; sin claves, cron ni migraciones en esta corrida) y sale 0 sin
  deshacer con otro SHA.
- Reversa de datos: no hace falta; no se reescribio ninguna `config_version` ni
  ninguna decision.

### Seguimiento (P2) de la revision

- (a) `J/verde.txt` y `J/notifica.txt` estaban en UTF-16 con BOM y GitHub los
  mostraba como binarios. En esta rama de evidencia se reescriben en UTF-8.
- (b) El comentario viejo de `tests/test_notifica.py:1048` queda anotado; no
  cambia comportamiento y no se toca en un PR de solo docs.
- (c) El docstring de `_aplica_producto_modo` y la ficha
  `.cursor/skills/verify-orbit/features/productos.md` aun mencionan la meta en
  practica; quedan anotados para una limpieza posterior, fuera de este PR de
  docs.

## Seguimiento (P2) del contrato

- La sonda historica `tools/auto03/sondas/meta_sombra.sql` se conserva como
  lectura documental de la era shadow; ya no hay modo que la produzca.
- Un binario anterior que reciba una config nueva con meta `shadow` fallaria al
  validar; produccion ya esta en `live` y el contrato J retira el modo.
