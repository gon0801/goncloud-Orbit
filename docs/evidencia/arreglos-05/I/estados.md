# Modelo por camino del bloque I: Ajustes rechaza una cuota que no puede guardar

Base: `a9885448ee04a1fe38a3be25c69c13985e140c76` (master con BASE, A, B, C, D, E,
F, G y H integrados y desplegados). Rama: `arreglos-05/i`.
Contrato: `docs/evidencia/arreglos-05/DISENO.md`, seccion "I-K. Configuracion,
retiro de modos y fees" (parte I). Aviso: Ajustes muestra el error si una cuota
no es un entero valido y deja de decir Guardado cuando la ignora.

Este documento se escribe ANTES del primer commit de codigo. Fija los caminos
nuevos y quien decide cada uno. La implementacion no puede abrir un segundo
validador en el navegador ni una API paralela.

## Hechos medidos antes de fijar el modelo

1. `settings.js` serializa las cuotas con `Number(el.value)`. Si el valor no es
   numerico, `Number` da `NaN`; `JSON.stringify` convierte `NaN` en `null`; el
   endpoint acepta `null` en una cuota y `proxima_config` lo salta con
   `if cap is None: continue`. Resultado: la escritura guarda el resto y la
   pantalla dice `Guardado`.
2. La validacion canonica de cuotas ya existe y tiene un solo lector:
   `app.apply_cuota.cap_desde_settings`. Acepta `int` que no sea `bool` o
   `str` ASCII de digitos, en `[0, CAP_MAXIMO = 2147483647]`; rechaza el resto
   con `ValueError`. `proxima_config` ya la usa al validar la config RESULTANTE.
3. `CuerpoSettings.caps` era `dict[str, int | None] | None`: pydantic convertia
   `true` en `1`, `1000.0` en `1000` y aceptaba `null`; esas conversiones
   ocurrian ANTES del lector canonico y borraban la diferencia entre una cuota
   invalida y una cuota omitida.
4. Medido con Edge headless y CDP (mismo navegador de `orbit-verify`): al
   teclear `1,000` en `input[type=number]`, el navegador normaliza el valor a
   `1000`, `validity.badInput` queda `false` y el formulario envia. Por eso la
   UI no puede detectar la coma mientras el campo sea `type=number`: la cuota
   debe conservar el texto crudo (`type=text`, `inputmode=numeric`) y validarlo
   antes de serializar.

## Dueno unico por paso

| Paso | Dueno | Decide |
|---|---|---|
| Texto crudo de la cuota | `app/templates/settings.html` + `app/static/js/settings.js` | Conservar exactamente lo tecleado y no convertirlo a `Number` hasta que pase la revision de sintaxis. |
| Mensaje visible y cero POST | `settings.js` (`guardarConfig`) | Si una cuota modificada no es `^[0-9]+$`, escribe `Error: ...` en `[data-estado]` y retorna antes de `postJson`. No llama `Number` ni `JSON.stringify` sobre el valor invalido. |
| Forma de la cuota en la frontera HTTP | `app/api_write.CuerpoSettings` | Pasa el valor crudo (`Any`) al escritor. No decide con `int`/`None`; no convierte `true` en `1`. |
| Aceptacion/rechazo de la cuota | `app/config_write.proxima_config`, via `cap_desde_settings` | Unico validador. Rechaza `null` explicito, `bool`, fracciones, texto ilegible, negativos y fuera de `CAP_MAXIMO`; un entero valido se guarda como texto decimal (`"7"`). |
| Version optimista y snapshot | `config_write.guarda_config` | Compara `base_config_version_id` con la vigente (409 si no) e inserta UNA fila nueva solo si `proxima_config` no levanto. Un rechazo no crea version ni fila. |

## Caminos y resultado exigido

### 1. Formulario con `1,000` en una cuota

- Campo `cap_<kind>` con texto `1,000`.
- `guardarConfig` revisa las cuotas que viajarian (`cambio(el)` verdadero):
  `/^[0-9]+$/` no acepta la coma.
- Resultado: `[data-estado]` muestra `Error: la cuota de <kind> debe ser un
  numero entero sin comas ni decimales (llego 1,000).`
- Cero llamadas a `postJson`: no hay `NaN`, no hay `null`, no hay POST.
- Si otra cuota valida cambio en el mismo envio, tampoco viaja: el error detiene
  el guardado completo. Es preferible no guardar a guardar a medias.

### 2. API con `null` explicito en una cuota

- `caps` con la llave del `kind` y valor `null`.
- `proxima_config` distingue llave ausente (no tocar) de llave presente con
  `null` (invalida) y levanta `SettingsInvalido`.
- Resultado: 422, cero filas nuevas, la config vigente intacta.

### 3. API con booleano o fraccion ilegible en una cuota

- `true`, `false`, `1.5`, `"1,000"`, `"1.5"` o cualquier forma que
  `cap_desde_settings` no acepte.
- `CuerpoSettings` ya no convierte `true` en `1`; el valor crudo llega al
  validador canonico.
- Resultado: 422, cero filas nuevas, config intacta. `false` tampoco se
  convierte en `0`.

### 4. API con el campo omitido

- `caps` sin la llave de un `kind`, o `caps` ausente.
- `proxima_config` no toca esa clave. Las demas ediciones validas se guardan.
- Resultado: 200 si hay al menos un cambio real; la cuota omitida conserva su
  valor y el snapshot se apila una vez por la escritura valida.

### 5. API con `null` valido en un campo que lo admite

- Ejemplo: `resumen_acos_objetivo = {"amazon_us": null}` (quitar la meta) o
  `margen.habilitado = false` (apagar).
- Ese `null` no es una cuota: su dueño (`_aplica_resumen_acos_objetivo` o el
  interruptor del margen) lo interpreta como "quitar la clave".
- Resultado: 200, el campo se guarda segun su contrato. No se generaliza el
  rechazo de `null` a otros campos.

### 6. Entero valido con version actual y con version vieja

- Version actual: `caps = {"bid": 25}` pasa la revision, `proxima_config`
  escribe `"25"`, `guarda_config` inserta UNA fila con label `settings UI ...`.
- Version vieja: `base_config_version_id` ya no es la vigente; `ConfigObsoleta`
  antes de calcular o insertar.
- Resultado: actual = 200 y exactamente una fila nueva; vieja = 409 y cero filas
  nuevas ni cambios.

## Fuera de alcance de I

- No se retiran modos (J).
- No se tocan fees (K).
- No se implementa Goals (04 W-I).
- No se reabre la banda manual 01X: queda como control.
- No hay DDL previsto. Si apareciera, seria aditivo, con reversa y sonda.

## Pruebas que fijan el modelo

- `tests/test_arreglos05_i_cuotas.py`: caminos 2 a 6 por HTTP simulado y PG
  desechable, mas el contrato de la frontera (la cuota cruda no se coacciona) y
  la validacion pura de `proxima_config`.
- Navegador real de `orbit-verify` (Edge/Chromium): camino 1 y el recorrido
  basico `launch`, `doctor`, `drive-settings`, `cleanup`.
- Control `tests/test_config_write.py -k arreglos01_x` (banda manual) en verde.
