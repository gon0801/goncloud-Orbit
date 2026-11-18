# D-E.7 — arnés TN de ingesta con la firma nueva

Diseño: `docs/evidencia/arreglos-03/DISENO.md`, sección D-G, «Traspaso D-E.7 → D-G.4».
Contrato: `docs/evidencia/arreglos-05/ACEPTACION.md`.

## Copias históricas (byte a byte) y su SHA-256

D-E cambia `hoy=` por `ultimo_cerrado=` en los planificadores de `app/ads/reports.py`.
Antes de adaptar los fixtures se guardó el original de cada archivo del arnés que llama a
esos planificadores, en `docs/evidencia/arreglos-05/revision/historico/` (fuera de
`PYTHONPATH`: el lanzador agrega `revision/`, no sus subcarpetas, y no hay `__init__.py`):

| Archivo | SHA-256 de `historico/` | Entrada en `revision/manifest.json` (`artefactos_sha256`) |
| --- | --- | --- |
| `ingest.py` | `09bf32902de63b72b40fcbb32cdcc89c4bbad3ded55a5ed5bdf0c0d239268fa9` | igual |
| `ui.py` | `cebfaa20859c435fd26e03669d9f607561752697619f6ab2b6d1cc00538fb761` | igual |

`ui.py` no lo nombra el diseño, pero su fixture llama `_planea_filas(..., hoy=...)` y con
la firma nueva reventaría con `TypeError` (casos `test_15`, `test_16` y `test_control` de
UI). Se trató igual que `ingest.py`: copia histórica y un solo cambio, el nombre del
argumento (`hoy=dt.date.today()` → `ultimo_cerrado=dt.date.today()`); la fila sembrada es
de hace 40 días, así que el resultado no cambia.

Comprobación (Python, desde la raíz):

```
python -c "import hashlib,json,pathlib; R=pathlib.Path('docs/evidencia/arreglos-05/revision'); m=json.loads((R/'manifest.json').read_text(encoding='utf-8'))['artefactos_sha256']; [print(f, hashlib.sha256((R/'historico'/f).read_bytes()).hexdigest()==m[f]) for f in ('ingest.py','ui.py')]"
```

## Cambios en el `revision/ingest.py` operativo

Correspondencia caso original → adaptado (las aserciones de `test_accept_ingest.py` no
cambian):

- `caso05`: la llamada a `_planea_filas_terminos` pasa
  `ultimo_cerrado=ultimo_dia_cerrado("amazon_mx", ahora)`, con el mismo `ahora` UTC con
  zona que arma las fechas del caso. Se conservan los dos órdenes de filas, los datos
  corruptos y el recorrido por `escribir_grupo`, PostgreSQL y `windows.terminos_cortes`.
- `control_productos`: el instante es `dt.datetime.now(dt.UTC)` (antes `dt.date.today()`,
  la fecha local de la máquina); `hoy` sale de él y `_planea_filas_productos` recibe
  `ultimo_cerrado=ultimo_dia_cerrado("amazon_mx", instante)`.
- `caso09`: reloj de la guarda y del fixture fijados al mismo instante,
  2026-11-12 07:30 UTC (invierno: US y MX tienen distinto último día cerrado);
  `metricas_listas._ahora` parchado a ese instante; la `ingest_run` lleva
  `started_at = finished_at = instante` (su fecha UTC es la que exige la guarda) y cada
  plataforma siembra `ultimo_dia_cerrado(plataforma, instante)`. La prueba de transacción
  ajena se conserva (la guarda del vecino recibe `ahora=` el mismo instante).

### Diferencia con el diseño en `caso09`: la llave de JEV

El diseño pide dos verdes en `test_control or test_09` sin tocar aserciones. En
`origin/master` `a6ffa32` (sin D-E) `test_09` ya da rojo por otra causa:

```
E       AssertionError: {'exit': 1, 'cierres': [('sin_api_key',)], 'avisos': 0, 'tx_ajena_conservada': 1, ...}
E       assert (1 == 0)
docs\evidencia\arreglos-05\revision\test_accept_ingest.py:17: AssertionError
```

`caso09` simula `leer_api_key → ""`, y desde ARREGLOS 03 D-J una corrida que cierra
`sin_api_key` sale 1 a propósito (para que `cron-avisa` avise). Siguiendo la regla 3 de
ACEPTACION («adaptar la entrada del fixture … mantener el efecto observable; no quitar la
aserción»), la entrada pasa a una llave ficticia `LLAVE_FICTICIA_TN09` y
`jev_senales.pedir_juicio` queda parchado para fallar si alguien lo llamara (el plan está
vacío porque no se siembran search terms, así que no hay llamadas y nada sale a la red).
La aserción `exit == 0` y las demás quedan igual. Si el lead prefiere otra forma, este es
el único punto a revisar.

## Corridas (PG desechable 127.0.0.1:55449, bajo el candado `locks/orbit-tn`)

Entorno: el de ENCARGO-COMUN (`TN=postgresql://orbit@127.0.0.1:55449/postgres` en los
cinco DSN, `ORBIT_REVIEW_REPO/ROOT`, `ORBIT_ACCEPT_EVIDENCE`, `PYTHONPATH`) más
`PYTHONUTF8=1` (en Windows `read_text()` sin encoding leería las migraciones UTF-8 como
cp1252 y el fixture fallaría antes de probar nada).

Comando (`-k` según la fila):

```
CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "<seleccion>"
```

### Antes de D-E: origin/master `a6ffa3251136af769751cf29356f382b4da6f3ef`

`-k "test_control or test_09 or test_05"` → `2 failed, 1 passed`:
- `test_control_productos_no_publica_subtotal_en_ningun_orden`: verde.
- `test_09…`: rojo por D-J (`exit 1`, cierre `sin_api_key`), ver arriba.
- `test_05…`: rojo por el subtotal (abajo).

### Después de D-E: `36473e95e0a58e0882f6077bf07b05bc06457256` (rebasado sobre `a6ffa32`)

`Acepta` de D-E.7, `-k "test_control or test_09"` → **`2 passed, 4 deselected`, salida 0**
(2026-11-18 11:19 UTC):
- `test_control_productos_no_publica_subtotal_en_ningun_orden`: verde.
- `test_09_jev_cron_cierra_corrida_sin_forzar_y_preserva_tx_ajena`: verde con el reloj
  fijado, la siembra por plataforma y la llave ficticia (guarda real `[True]`,
  `_leer_mundo` entra `IDLE`, cierre completo, cero avisos, transacción ajena conservada).

TN05, `-k "test_05"` → `1 failed`, salida 1. Sigue rojo **por el subtotal**, no por firma,
conexión ni fixture (el caso recorre `escribir_grupo` → PostgreSQL →
`windows.terminos_cortes` con la firma nueva y llega a la aserción):

```
E       AssertionError: {'consumidor_windows': [('collar', '910.0000', 0, '0.0000')], 'entidad_completa': True, 'planes_por_orden': [1, 0], 'sello': (True, 10, 10)}
E       assert (not [('collar', '910.0000', 0, '0.0000')])
docs\evidencia\arreglos-05\revision\test_accept_ingest.py:23: AssertionError
```

Es la misma aserción y los mismos datos que en `a6ffa32`: el término `collar` queda con
910.0000 de gasto y 0 pedidos como entidad completa, y el orden `[a, b, buena]` todavía
publica un plan. D-G lo lleva a verde con estos mismos casos y sin tocar las aserciones.

Vecino de UI (fixture adaptado de `revision/ui.py`): `test_accept_ui.py -k "test_control"`
→ `2 passed, 4 deselected`, salida 0 (arma el escenario que llama `_planea_filas` con
`ultimo_cerrado=`).
