# W-P — La confirmación siempre corresponde a la previa visible (TN04)

Bloque W-P de `plans/arreglos-04.md`, con el contrato `## W-P` de
`docs/evidencia/arreglos-04/DISENO.md`.

- Base: `1fb9ff6d` (master, con W-N desplegado).
- Código entregado: `bd6e91f269bb6c5f3cbc58490119ea7f30871cdd`.
- Rama: `hermes/arreglos-04-w-p`.
- Cero escrituras reales en Amazon y cero secretos: el transporte de escritura
  va interceptado y el POST nunca sale del banco.

## Qué cambió

`app/static/js/productos.js` deja de guardar la previa en dos globales sueltas
(`vistaGuardada`, `retiroGuardado`) y pasa a un estado por flujo (retiro e
impulso separados) con `revision`, `solicitud`, `grupos`, `previa` y `fase`
(`vacia`, `cargando`, `lista`, `enviando`, `incierta`, `resultado`).

- Pedir una previa o cambiar la selección sube la revisión e invalida plan,
  huella, frase de confirmación y casilla de presupuesto de W-N.
- Solo una respuesta cuya revisión y solicitud siguen vigentes puede pasar a
  lista; la tardía se descarta, incluido su error.
- El render y el cuerpo del POST leen el mismo estado `lista`.
- `Confirmar` pasa a `enviando` de forma sincrónica antes de esperar la red:
  congela la solicitud y apaga el botón, así que un doble clic no manda dos.
- Un rechazo inequívoco esconde el formulario y exige una previa nueva.
- Un envío sin respuesta (timeout o desconexión) queda `incierta`: dice que no
  se sabe, deja el reenvío deshabilitado y no reintenta solo.

El backend de retiro e impulso no se toca: huellas, autenticación por header y
la confirmación de presupuesto alto de W-N siguen igual.

## El defecto, antes y después

Con A a la vista, se cambia la selección a B y falla la previa de B. En la base
`1fb9ff6d` el formulario de A seguía a la vista y su botón enviaba la solicitud
de A, cuya huella ya no correspondía a nada visible.

Rojo en `1fb9ff6d`, capturado con `ui.browser()` sobre Chromium real y el POST
interceptado (`seleccionActual` es lo que quedó marcado en la pantalla):

```json
{"modo": "retiro",
 "escrituras": [{"url": "/api/fabrica/retiro/ejecutar",
   "body": {"plataforma": "amazon_mx", "listing_ids": [11], "motivo": "sin_pedidos",
            "huella": "4fe823748546a407fb55901deb2be56ce4d3a14cc4b54adff61ec8ebceb38a42",
            "confirmacion": "SACAR 1 ANUNCIOS", "actor": "review-local"}}],
 "dom": {"previaA": "UI-1 · g-ui-1", "errorB": "Error: Fallo controlado de la nueva consulta.",
         "ejecutable": true, "seleccionActual": ["12"],
         "estado": "Error: Escritura capturada: no ejecutada."}}
```

```json
{"modo": "impulso",
 "escrituras": [{"url": "/api/fabrica/impulso/lanzar",
   "body": {"solicitud": {"plataforma": "amazon_mx", "listing_ids": [11],
                          "subfamilia_por_listing": {"11": "ui"}},
            "huella": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "confirmacion": "LANZAR IMPULSO DE 1 PRODUCTOS", "actor": "review-local"}}],
 "dom": {"previaA": "UI-1 · uiTope 100.00 MXN · 5.00 al día · 0 keywords · 0 negativos",
         "errorB": "Error: Fallo controlado de la nueva consulta.",
         "ejecutable": true, "seleccionActual": ["12"],
         "estado": "Error: Escritura capturada: no ejecutada."}}
```

La huella del retiro la calcula el plan sobre la base desechable de esa corrida;
en cada corrida cambia.

Verde en `bd6e91f`:

```
2 passed, 4 deselected   (test_04 y test_04_vecino_impulso)
6 passed                 (con test_15, test_16 y los dos controles de auth/CSP)
```

En verde, la misma corrida del navegador deja `escrituras_capturadas` vacío en
los dos flujos.

## Cómo reproducir el banco TN

Requiere PostgreSQL 16 desechable en `127.0.0.1:55449/postgres` (el lanzador
rechaza cualquier otro destino antes de conectarse) y Chromium real.

```bash
cd <checkout>
# ORBIT_TEST_DSN ya viene configurado en el entorno con el PG16 desechable del
# laboratorio (127.0.0.1, puerto 55449, base postgres). El valor no va al repo.
export ORBIT_CHROMIUM='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
export ORBIT_ACCEPT_PYTHON="$PWD/.venv/bin/python"

# TN04 y su vecino de impulso
uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh T ui "test_04"

# Grupo UI completo de ARREGLOS 04
uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh \
  T ui "test_04 or test_15 or test_16 or test_control"

# Pruebas JS y de contrato del bloque
ORBIT_TEST_DSN="$ORBIT_TEST_DSN" CI=1 PYTHONPATH=. \
  uv run --frozen --python 3.12 python -m pytest -q -rs \
  tests/test_ui_productos.py tests/test_retiro_contrato.py
```

`comprobar.sh` arranca por sí solo el runner
`docs/evidencia/arreglos-05/revision/ui.py`, que crea y elimina su base
`thermo_ui_current`, carga la cadena de migraciones sin `0011` ni reversas, y
usa rutas, plantillas y `productos.js` reales. El navegador es Chrome headless
por `--dump-dom`; el POST de escritura no se reenvía a la app: el servidor falso
de la prueba lo captura y responde 503. El laboratorio se comparte bajo el
candado `~/.ao/data/locks/orbit-tn` y las corridas van en serie, sin `pytest -n`.
El DSN del laboratorio tiene que traer contraseña porque
`tests/test_impulso_io.py::test_r09r5_b1a_conexion_propia_lleva_credencial` la
exige; con un DSN sin contraseña esa prueba falla aunque el resto pase. El valor
no vive en el repo: lo pone el entorno de la corrida.

## Cambio en el arnés y su razón

`docs/evidencia/arreglos-05/revision/ui.py` suma una corrida ok de
`accounting_ledger_events` (`ingest_run`) antes de abrir el cliente de pruebas.
Sin ella, el plan de retiro de W-M responde `ventas_al_dia: false` y no hay
previa con la que ejercer TN04: el caso de retiro terminaba en
`RuntimeError: Primera previa no disponible` en lugar de medir el defecto. El
cambio solo siembra el dato que el flujo pide; no toca las aserciones de
`test_accept_ui.py` ni la frontera HTTP. El original del arnés sigue en
`revision/historico/ui.py` y su SHA-256 coincide con la entrada `ui.py` de
`revision/manifest.json`.

## Pruebas del bloque

`tests/test_ui_productos.py` ejecuta el JS real con Node y transporte
controlado, un guion por flujo:

- `test_arreglos04_w_p_retiro_no_ejecuta_una_seleccion_cuya_previa_fallo`
- `test_arreglos04_w_p_impulso_no_ejecuta_una_seleccion_cuya_previa_fallo`
- `test_arreglos04_w_p_impulso_409_no_rearma_una_vista_que_ya_no_se_ve`
- `test_arreglos04_w_p_409_no_rearma_una_seleccion_que_cambio_en_el_envio`
- `test_arreglos04_w_p_impulsar_aparte_invalida_su_previa_en_la_tabla_sin_pedido`

Cubren, en los dos flujos, A a la vista con B fallando, respuestas invertidas
(la de B primero y la de A tarde), cambio durante la carga, doble clic, previa
nueva con envío pendiente, envío incierto, el 409 con vista nueva y la tabla
compartida de «Impulsar aparte», con aserciones sobre el payload capturado, el
detalle que ve la persona y el botón.

`tests/test_ui_productos.py` y `tests/test_retiro_contrato.py` con `CI=1` dan
38 pruebas en verde.

Contra el `productos.js` de `1fb9ff6d` las dos del caso central fallan en el
primer corte real (`cambiar la seleccion esconde el formulario de A`), no por un
detalle del arnés; contra el de `bd6e91f`, las tres de la ronda de revisión
fallan en su propio corte. En el entregado pasan.

La prueba de forma
`test_r09d_error_objeto_muestra_mensaje_y_fallos_sin_object_object` baja de
trece a doce sitios de `mensajeError(r.data)` porque pedir la previa ahora es
una sola función que sirve a los dos flujos. El comentario de la prueba lo
explica.

## Pantalla

`launch`, `doctor`, `drive-productos` y `cleanup` con el helper
`.cursor/skills/verify-orbit/helpers/orbit-verify` sobre instancia propia (run
`20261120T022913Z`): los tres en verde, `drive-productos` con sus 46
comprobaciones en `true` (`evidence/20261120T022913Z/productos/PROOF.json`) y la
base desechable eliminada al final. La ficha
`.cursor/skills/verify-orbit/features/productos.md` documenta
`productos-previa-ejecutable` con los textos nuevos.

El baseline de `drive-productos` no envía retiro ni impulso: ese recorrido lo
cubre TN04 con el POST interceptado, sin declarar ninguna mutación de Amazon
probada.

## Ronda de revisión de Astra sobre efec598

Cuatro bloqueos, los cuatro reales, corregidos en el mismo PR. Cada uno quedó
demostrado con el transporte interceptado: la prueba falla contra
`bd6e91f` y pasa con la corrección.

1. **La tabla de sin pedido alimenta dos rutas.** «Impulsar aparte» toma sus
   IDs de `[data-marcar-sin-pedido]`, pero el manejador de `change` solo
   invalidaba el retiro: la previa del impulso seguía ejecutable con productos
   ya desmarcados. Ahora ese cambio invalida las dos previas, también mientras
   carga. Rojo en `bd6e91f`: `marcar en la tabla sin pedido esconde el impulso`.
2. **Ninguna entrada rearma `Confirmar` con un envío pendiente o incierto.**
   Antes, pedir una previa nueva durante el POST cambiaba la revisión y volvía
   a habilitar el botón, y tras una desconexión también destrababa el reenvío;
   además, la selección vacía pasaba por el mismo camino. Ahora
   `envioPendiente()` corta `verLanzamiento`, `verRetiro` y `cambioDeSeleccion`
   mientras la fase es `enviando` o `incierta`, y deja el motivo en
   `[data-estado]`. Rojo en `bd6e91f`: `no se pide previa mientras envia`.
3. **El 409 solo rearma si la selección sigue siendo la que pidió el plan.**
   Durante el envío el cambio de selección se ignora a propósito; si el 409
   devolvía la vista, quedaba ejecutable una selección que ya no estaba
   marcada. Ahora el flujo guarda el selector que usó y `mismaSeleccion()`
   decide; si no coincide, esconde el formulario y pide una previa nueva.
   La comprobación cubre los IDs, el mapa de subfamilia por publicación y los
   marcados que se quedaron sin subfamilia, porque cambiar «aros» por
   «collares» o vaciarlo deja la vista vieja sin mover un ID. Rojo en
   `bd6e91f`: `con otra seleccion el 409 no rearma el plan`. Rojo en `873746e`,
   que solo comparaba IDs: `con otra subfamilia el 409 no rearma el plan` y
   `sin subfamilia el 409 tampoco rearma`.
4. **El 409 borra el literal escrito.** Era de la revisión anterior y con un
   solo clic lanzaba el plan nuevo. Ahora se limpia y hay que escribirlo otra
   vez. Rojo en `bd6e91f`: `el literal viejo no sirve`.

El caso vecino `test_flujo_js_plan_lanza_y_repone_con_literales` se ajusta al
contrato nuevo: después del 409 comprueba que un clic sin escribir no manda
nada y escribe el literal para el segundo intento.

## Lo que ya fallaba en master

Con el DSN del laboratorio,
`tests/test_impulso_io.py::test_r09r3_v17_pausador_real_pausa_en_amazon` y
`tests/test_architecture.py::test_presupuesto_de_tamano_por_modulo`
(`app/ledger.py` con 1000 líneas) fallan igual en `1fb9ff6d` sin este cambio,
comprobado en un checkout limpio del mismo commit. No los toca W-P.
