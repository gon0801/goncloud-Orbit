# R.1 — Revisión independiente (REPRICING 01, Fase 11)

Fila **R.1** del plan `plans/repricing-01.md` v1.3, verbatim:

> **Revisión independiente** (kimi sobre un SHA; lead audita): catálogo de mutantes del implementador (una por regla y borde de S4, candados, transiciones, dinero, cuota, lock, cobertura) re-ejecutado con base real; cero sobrevivientes o se cierran con test en el mismo PR.
>
> DoD: `docs/evidencia/repricing-01/R.1/` con catálogo, re-mutación y APPROVE de kimi y del lead sobre el SHA

Lead: claude (sesión `fase11-lead`, worktree `wt-fase11-lead`). Implementador de todo
el código revisado: muse. Runbook: `docs/runbooks/autopilot-fase11.md` de
goncloud-openclaw (carril R).

**Estado de este documento: parcial.** Cubre A.1, A.2, A.3, A.7 y A.5 (ya en `master`).
A.6 entra cuando su PR mergee (Q2), con el mismo método.

## Parte 1 — cruzada de kimi por SHA de squash

`cross-review.ps1` solo acepta `-Alcance last-commit` y trunca el diff a 60 000
caracteres. Cada squash se revisó parado en ese SHA y **partido por archivos** en
trozos de menos de ~58 KB, para que kimi viera el diff completo (guion:
`kimi/kimi-rondas.sh`; cada salida cruda en `kimi/`, con el SHA, los archivos, la
hora y el código de salida al pie). Dos archivos de prueba pasan solos del tope
(`tests/test_precio_reglas.py`, 82 KB; `tests/test_precio_write.py`, 105 KB): kimi
leyó el resto del archivo real en el repo y lo dice en su respuesta.

| Fila | PR | SHA de squash | Trozos | Altas | Medias | Bajas |
|---|---|---|---|---|---|---|
| A.1 | #303 | `662db38` | 2 | 0 | 0 | 11 |
| A.1 (cierre r1) | #307 | `eeefb72` | 1 | 0 | 0 | 2 |
| A.2 | #299 | `39cba88` | 3 | 0 | 0 | 12 |
| A.3 | #300 | `efc0555` | 3 | 0 | 0 | 13 |
| A.7 | #305 | `0d88cc8` | 2 | 0 | 0 | 9 |
| A.5 | #309 | `53c6067` | 3 | 0 | 0 | 9 (uno de los trozos, `LGTM`) |

**Veredicto de kimi: sin hallazgos altos ni medios en ningún SHA** (A.5: los tres trozos
«NO BLOQUEANTE» o `LGTM`; guion `kimi/kimi-A5-squash.sh`) (política de la
sección 4 del loop: una ronda sin altas ni medias cierra). Las bajas quedan como
residuales declarados; las que tocan un comportamiento, con su razón:

- A.2 (`kimi-A.2-c1`, 2): la regla 10 mete la distancia de hoy en la cadena monótona
  (`cadena = [...] + [distancia]`), así que tres cambios sin acercarse **no** frenan si
  hoy mejoró. Es la semántica sellada en A.2 (catálogo M10, N15; celda de A.2 «`no_converge`
  con distancias iguales cuenta como sin acercarse»); el caso «tres sin acercarse y hoy
  mejor» no tiene test propio. Residual, no bloquea.
- A.2 (`kimi-A.2-c1`, 1): `cotizar_a_precio` acepta `Decimal("100.000")` porque
  compara por valor; la identidad canónica y el request difieren en ese caso. El motor
  solo le pasa salidas de `techo_centavo` (exponente −2). Residual.
- A.3 (`kimi-A.3-c1`, 4): `cambiar_precio` no pasa `limitador` a sus lecturas de
  Pricing. **Lo cierra A.5 en esta misma fase** (punto (a) del carril A).
- A.3 (`kimi-A.3-c1`, 2): el go de `tools/precio_reversa.py` aborta el lote ante una
  excepción no prevista después de sellar la fila en `error` (sin corrupción de datos).
  Herramienta del dueño; residual.
- A.7 (`kimi-A.7-c1`, 4): el candado de imports de `app/precio/` (y el de `fuentes.py`)
  no prohíbe módulos de red de la stdlib (`socket`, `urllib`, `http.client`); el
  comentario «jamás red» promete más que el test. Hueco heredado de A.2, no regresión;
  residual para la fase que toque el candado.
- A.7 (`kimi-A.7-c1`, 1): «activa» = la última observación por SKU filtrada después por
  `BUYABLE`. Es la definición con la que se midió el readback de A.7
  (`A.7/readback.md`); residual documental.
- A.5 (`kimi-A.5-c1`, 1): `cotizar_y_decidir` itera `range(3)` y su guarda de «máximo dos»
  corre después de una tercera cotización; hoy la impide `PideCotizacion.intento in (1, 2)`
  de `tipos.py`, así que no se alcanza. Residual.
- A.5 (`kimi-A.5-c1`, 2): una cotización en error lleva `fee_total = 0` en el objeto; inerte
  porque `_revisar_cotizacion` sale antes de leerlo. Residual.
- A.5 (`kimi-A.5-c3`): fragilidades de tests (orden implícito en dos sabotajes, contadores de la
  red falsa sin lock, parámetro muerto). Residuales.

La cruzada del **PR** de A.5 (antes del merge) fue otra cosa: cuatro rondas con revisor distinto
(kimi, kimi, claude, glm; salidas y veredictos en el PR #309). Esta de R.1 es la de la fila,
sobre el squash.

## Parte 2 — re-mutación con base real

Andamio: `remutacion/muta.py`. Por mutante: aplica el cambio exacto (tiene que
aparecer exactamente `cuenta` veces; si no, `INFIEL` y no corre), corre sus tests con
`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`, caché de bytecode
nueva (`PYTHONPYCACHEPREFIX`) y `-p no:cacheprovider`, restaura los bytes originales
y exige `git status --porcelain` vacío del archivo. Código de pytest 1 = MUERTO,
0 = SOBREVIVE, otro = INVALIDO. Reproducir, desde la raíz del repo:

```
MUTA_RAIZ=$(pwd) python3 docs/evidencia/repricing-01/R.1/remutacion/muta.py \
  docs/evidencia/repricing-01/R.1/remutacion/cat_A2.json
```

Línea base antes de mutar (HEAD `d37143b` = `origin/master`):
`pytest tests/test_precio_goals.py tests/test_precio_reglas.py tests/test_precio_write.py
tests/test_spapi_write_client.py tests/test_precio_cobertura.py tests/test_architecture.py`
→ `482 passed in 25.13s`, **0 skipped**.

Los catálogos (`remutacion/cat_*.json`) transcriben cada mutante de
`docs/evidencia/repricing-01/<fila>/mutantes.md` a un cambio exacto sobre el código de
hoy; el resultado de cada uno está en `remutacion/cat_*.resultado.jsonl`.

| Catálogo | Mutantes | Muertos | Equivalentes | Sobrevivientes |
|---|---|---|---|---|
| A.1 | 31 | 31 | 0 | 0 |
| A.2 | 58 + 1 del lead | 58 | 0 | 1 del lead (`R1-A2-S2`) |
| A.3 | 51 + 2 equivalentes + 1 del lead | 51 | 2 (`L2b`, `G4`) | 1 del lead (`R1-A3-S3`) |
| A.7 | 32 + 1 del lead | 32 | 0 | 1 del lead (`R1-A7-S1`) |
| A.5 | 94 (catálogo de muse r0–r5, que incluye los del lead de la auditoría) | 93 | 1 (`C3-1-doble-claim`) | 0 |

**Todo mutante del catálogo de los implementadores que hoy puede morir, muere.**

A.5 se re-mutó sobre el squash `53c6067` (worktree aparte). Su catálogo (`cat_A5.json`) junta
los ids propios de muse (A5-1…15, B, C3, K) con los del lead de la auditoría del PR (LA, R1–R4),
en su última forma. Cuatro ids viejos quedaron fuera porque el código que mutaban ya no existe; la
razón y el id que los cubre hoy están en `cat_A5.obsoletos.json`. **Equivalente `C3-1-doble-claim`**
(quitar la guarda del claim en `_tomar_lock`): desde r4 la exclusión la da además
`pg_try_advisory_lock`, que rechaza al segundo proceso con el mismo `LockOcupado`; el resultado
observable no cambia.

Transcripciones que no son literales, declaradas:

- A.3 `C3`: el detector de verbo que mutaba el catálogo ya no existe (r6-C3 lo cambió
  por la llamada ejecutable `httpx.patch(`); el mutante equivalente de hoy busca `PATCH`
  en la línea cruda junto al prefijo. Muere.
- A.3 `P20r2`: el cierre escribe `confirmado_por = 'virtual'` solo en `no_confirmado`.
  Muere.
- A.2 `H1`: el emparejamiento por `id(decision)` se reproduce emparejando por la primera
  aparición del objeto. Muere.
- A.2 `R15` se partió en `R15a` (filtro de historial) y `R15b` (filtro del freno #6).
  Mueren los dos.

**Equivalentes (A.3 `L2b` y `G4`)**: desde r5-L2, `leer_precio_vivo` solo pide el
competitivo cuando las ofertas **no** traen la oferta propia, y en ese estado las dos
ramas (competitivo no JSON / no 200 tratado como respaldo vacío, o como
`PrecioVivoAusente`) terminan en el mismo `PrecioVivoAusente`; solo cambia el mensaje.
Con la oferta propia presente el competitivo ni se pide. Sin comportamiento observable
distinto: se declaran equivalentes, sin test.

**Sobrevivientes del lead** (mutantes propios de la re-mutación, fuera de los
catálogos; ningún test del archivo los mata):

| Id | Fila | Mutante | Qué no está probado |
|---|---|---|---|
| `R1-A7-S1` | A.7 | `tools/precio_cobertura.py`: `except (OrbitDbError, Abortar)` → `except (Abortar,)` | Que una base que no responde al conectar sale `exit 2` sin traceback (el test existente cubre solo el DSN ausente) |
| `R1-A2-S2` | A.2 | `objetivo.margen_a_precio`: `divisor = iva_divisor if incluye_iva else Decimal(1)` → `divisor = iva_divisor` | Que `margen_a_precio` no aplica el divisor de IVA cuando el precio no lo incluye (el catálogo O9 lo prueba solo en `precio_estrella`) |
| `R1-A3-S3` | A.3 | `tests/test_architecture.py`: sin `_sin_comentario` en el barrido de PATCH crudo | Que un comentario con `httpx.patch(` o `.patch(` no es fuga (el test existente solo usa `# PATCH`) |

Se cierran con test por un encargo bis a muse antes de Q4 (PR pendiente; se cita
aquí al mergear).

## Desviaciones de la fila R.1, declaradas

1. Los sobrevivientes se cierran en un PR bis y no «en el mismo PR», porque el carril R
   solo puede tocar `docs/evidencia/repricing-01/R.1/` (runbook de la Fase 11).
2. La cruzada corrió en un worktree aparte (`/Users/dn/dev/_wt/f11-kimi-r1`, detached
   en cada SHA) en vez de `checkout --detach` en `wt-fase11-lead`, para que la
   re-mutación corriera en paralelo en el worktree del lead. Mismo árbol, mismo SHA; el
   worktree aparte se quita al cierre.
3. A.1 tiene dos squashes (#303 y su cierre r1 #307): se revisaron los dos.
4. Cada squash se revisó en trozos por archivo (límite de 60 000 caracteres de
   `cross-review.ps1`); el veredicto por SHA es la unión de sus trozos.

## APPROVE

- **kimi**: sin altas ni medias sobre `662db38`, `eeefb72`, `39cba88`, `efc0555` y
  `0d88cc8`, y sobre `53c6067` (A.5) (salidas en `kimi/`). A.6: pendiente.
- **lead**: pendiente (se escribe al cerrar A.6 y el PR bis).
