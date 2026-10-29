# Cross review Grok sobre `0c48417` (RESUMEN-ADS-01)

Revisor: Grok 4.7. El proveedor `xai` devolvió `invalid_grant`, así que la
revisión corrió en el Grok 4.7 disponible en OpenCode Go. Diversidad reducida:
una sola familia y un solo revisor.

## Hallazgos verificados y corregidos

### 1. Mismo as_of legado con contenido distinto (bloqueante)

Una observación legada sin marca real, con el mismo as_of y otra versión,
dejaba entrar un snapshot distinto y pisaba el costo. El piso excluía el as_of
entrante y no había aceptación que lo rechazara.

Antes:
```text
LEGACY_MISMO_ASOF {'error': None, ... 'costo_vigente': '99.0000',
                   'restaurar_original': 'RECHAZO: ...'}
```
Después:
```text
LEGACY_MISMO_ASOF {'error': 'MeliAdsError: snapshot con el mismo as_of y
                   contenido distinto...', 'costo_vigente': '10.0000',
                   'restaurar_original': 'ACEPTO'}
```
Fix: `_observaciones_en_as_of` lee las versiones ya escritas con el as_of
entrante. Si el conjunto no coincide con el snapshot, se rechaza antes de
decidir el reloj. Si coincide, es un duplicado concurrente y se deduplica.

### 2. Día interior con gasto y sin campañas (bloqueante)

La conciliación limitaba el control diario a las fechas que ya tenían campaña.
Un día intermedio con spend 500 y cero campañas quedaba fuera y el script
imprimía `CONCILIACION OK`.

Antes:
```text
dias control: 2, dias Orbit: 2 ...
CONCILIACION OK
CONCILIAR_HUECO_EXIT 0
```
Después:
```text
dias control: 3, dias Orbit: 2 ...
FALTA el dia 2026-10-28 en Orbit: origen=(9, 8, 500.0000, 900.0000)
CONCILIACION FALLA
CONCILIAR_HUECO_EXIT 1
```
Fix: el control incluye todo día dentro de la ventana de campañas que tenga
actividad, aunque no tenga filas de campaña. Un día interior en cero y sin
campañas no se considera diferencia monetaria.

### 3. Runbook con archivo no vacío equivocado (media)

El `grep` de la línea `corre meli-ads meli_ads.log` no detenía el bloque: un
script viejo no vacío podía instalarse.

Antes:
```text
test_s_rc=0
grep_rc=1
bash_n_rc=0
INSTALL_CORRERIA
```
Después, con `set -euo pipefail`:
```text
test_s_rc=0
script_rc=1
```
El bloque se detiene en `grep` y no llega al `install`.

## Pruebas

- `rojo.txt`: las dos pruebas nuevas fallan contra `0c48417`
  (`DID NOT RAISE` y `assert 0 != 0`).
- `verde.txt`: las mismas dos pasan en el árbol corregido.
- `runbook.txt`: caso del archivo no vacío sin la línea de MeLi.

## Hallazgos descartados

Grok descartó 9: dos épocas legadas, fila futura mezclada, suma diaria distinta
en un día con campañas, revisión con as_of posterior, recopia parcial, nombre
fuera de la huella, datos futuros visibles en `v_metric_latest`, `campaign_id`
entero contra texto y `orders`/`acos` fuera de la fuente ingerida. Ninguno
rompía el contrato con una ejecución real.

## Segunda pasada Grok sobre `910e347`

### 4. Duplicado futuro sin aceptación borraba el piso legado (bloqueante)

Un snapshot futuro que solo repetía una observación legada ya escrita con ese
as-of sellaba `futuro=` sin `piso=`. El piso legado quedaba excluido y un
snapshot real más viejo entraba con costo 99.

Antes:
```text
CON_DUP_RELOJ_REAL ... 'marcas': [('futuro=...; snap=...')],
'stale_error': None, 'costo': '99.0000'
```
Después:
```text
CON_DUP_RELOJ_REAL ... 'marcas': [('reintento=...; 1x misma version...')],
'stale_error': 'snapshot atrasado ...', 'costo': '10.0000'
```
Fix: un futuro que coincide exactamente con observaciones legadas del mismo
as-of es un no-op. No crea aceptación temporal y conserva el piso. Un futuro
sin observaciones previas sigue creando su marca y no bloquea correcciones
reales, como exige el Caso A.

### 5. Día posterior a la última campaña o sin campañas (bloqueante)

El control diario seguía ignorando un día con gasto posterior a la última
campaña, y un origen sin campañas con gasto en el control.

Antes:
```text
CONCILIAR_trailing_gasto_EXIT 0 OK
CONCILIAR_sin_campanas_con_gasto_EXIT 0 OK
```
Después:
```text
CONCILIAR_trailing_gasto_EXIT 1 FALLA
CONCILIAR_sin_campanas_con_gasto_EXIT 1 FALLA
```
Fix: desde el primer día de campaña se incluye cualquier día con actividad,
aunque no tenga filas de campaña. Sin campañas, cualquier día del control con
actividad es un hueco. La historia anterior a la primera campaña sigue fuera
de la comparación, como estaba declarado.

### Pruebas de la segunda pasada

- `rojo-2.txt`: las tres pruebas nuevas fallan contra `910e347`.
- `verde-2.txt`: las tres pasan en el árbol corregido.
- `recheck-2.txt`: salida de los repros de Grok contra el árbol corregido.

## Tercera pasada Grok sobre `42121208`

### 6. Dos versiones al mismo as-of colapsaban en el dict (media)

Dos observaciones de la misma campaña/día con distinta versión y el mismo
as-of se pisaban en un dict. El costo no se perdía por el índice único, pero la
clasificación del snapshot quedaba invertida.

Después del fix, `_observaciones_en_as_of` devuelve un CONJUNTO de
`(campaña, día, versión)`. Un snapshot de una sola versión no coincide con un
conjunto de dos, así que ambos se rechazan y no se sella una aceptación de la
versión que no es la vigente. Regresión:
`test_dos_versiones_legadas_al_mismo_as_of_fallan_cerrado`.

### 7. `mktemp` con XXXXXX antes de la extensión (media)

En macOS `mktemp /tmp/refresh_costos.XXXXXX.sh` crea el path literal. Si el
`grep` falla, el `rm` final no se alcanza y el reintento muere en `mktemp`.

Después del fix, el bloque usa `mktemp /tmp/refresh_costos.XXXXXX` (XXXXXX al
final) y `trap 'rm -f "$ARCHIVO_LOCAL"' EXIT`. `runbook.txt` muestra primera
corrida `rc=1`, segunda `rc=0` con la línea de MeLi y `restos=0` en zsh y bash.
