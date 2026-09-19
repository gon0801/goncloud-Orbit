# R.1 — Revisión independiente (REPRICING 01, Fase 11)

Fila **R.1** del plan `plans/repricing-01.md` v1.3, verbatim:

> **Revisión independiente** (kimi sobre un SHA; lead audita): catálogo de mutantes del implementador (una por regla y borde de S4, candados, transiciones, dinero, cuota, lock, cobertura) re-ejecutado con base real; cero sobrevivientes o se cierran con test en el mismo PR.
>
> DoD: `docs/evidencia/repricing-01/R.1/` con catálogo, re-mutación y APPROVE de kimi y del lead sobre el SHA

Lead: claude (sesión `fase11-lead`, worktree `wt-fase11-lead`). Implementador de todo
el código revisado: muse. Runbook: `docs/runbooks/autopilot-fase11.md` de
goncloud-openclaw (carril R).

**Estado de este documento: completo.** Cubre A.1, A.2, A.3, A.7, A.5 y A.6 (todas en `master`)
y el bis que cerró los sobrevivientes (PR #313, `de8c54f`).

## Parte 1 — cruzada de kimi por SHA de squash

`cross-review.ps1` solo acepta `-Alcance last-commit` y trunca el diff a 60 000
caracteres. Cada squash se revisó parado en ese SHA y **partido por archivos** en
trozos de menos de ~58 KB, para que kimi viera el diff completo (guion:
`kimi/kimi-rondas.sh`; cada salida cruda en `kimi/`, con el SHA, los archivos, la
hora y el código de salida al pie). Tres archivos de prueba pasan solos del tope
(`tests/test_precio_reglas.py`, 82 KB; `tests/test_precio_write.py`, 105 KB;
`tests/test_precio_pantalla.py`, 112 KB): kimi leyó el resto del archivo real en el repo y lo
dice en su respuesta.

| Fila | PR | SHA de squash | Trozos | Altas | Medias | Bajas |
|---|---|---|---|---|---|---|
| A.1 | #303 | `662db38` | 2 | 0 | 0 | 11 |
| A.1 (cierre r1) | #307 | `eeefb72` | 1 | 0 | 0 | 2 |
| A.2 | #299 | `39cba88` | 3 | 0 | 0 | 12 |
| A.3 | #300 | `efc0555` | 3 | 0 | 0 | 13 |
| A.7 | #305 | `0d88cc8` | 2 | 0 | 0 | 9 |
| A.5 | #309 | `53c6067` | 3 | 0 | 0 | 9 (uno de los trozos, `LGTM`) |
| A.6 | #312 | `510beda` | 3 | 0 | 0 | 15 |

**Veredicto de kimi: sin hallazgos altos ni medios en ningún SHA** (A.5 y A.6: todos los trozos
«NO BLOQUEANTE» o `LGTM`; guiones `kimi/kimi-A5-squash.sh` y `kimi/kimi-A6-squash.sh`) (política de la
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

- A.6 (`kimi-A.6-c2`, 2): el aviso `buy_box_perdida` agrupa hoy y ayer por
  `COALESCE(seller_sku, external_id)`, y `seller_sku` no es único por plataforma (el `COMMENT` de
  `listing` en la 0001 documenta 2–4 publicaciones por SKU): dos publicaciones con el mismo SKU se
  confunden y la pérdida de una puede quedar escondida o mezclar su flanco con la otra. Lo robusto
  es agrupar por `listing_id`. Residual con nombre en la celda de A.6.
- A.6 (`kimi-A.6-c2`, 1 y 3): un `frenado(no_confirmado)` manda el aviso de grupo `frenado` y el
  aviso por producto `no_confirmado` (lo piden la fila y AC5; son dos mensajes por el mismo
  evento); una decisión `no_evaluado` con `buy_box_is_own = false` genera un aviso
  `buy_box_perdida` con estado «no evaluado». Residuales.
- A.6 (`kimi-A.6-c1`, 1–3): la frase del bloque (c) con un cambio `no_confirmado` dice «subió»
  sin matiz (el revisor del merge encontró lo mismo con `error` y `pendiente`); un `mantener` sin
  `p_actual` sale «None»; el docstring del módulo dice que `/salud` va «sin recuadro» y hoy lleva
  `cobertura`. Residuales de redacción, con nombre en la celda de A.6.

Dos más de A.5, vistas por el lead al auditar el carril B (no las trajo la cruzada):

- `correr(conn, platform)` llama `cerrar_por_observacion(conn, hoy)`, que no filtra por
  plataforma: la corrida de una plataforma cierra los cambios `enviado` de todas, y
  `resumen.cerrados` y el log los atribuyen a la que corre. El cierre en sí es correcto (cada
  cambio se compara con la observación de su propia plataforma); lo que se desvía es la cuenta.
  Residual.
- AC16 pide `no_evaluado(precio_divergente)` «con ambos precios y horas»: `reglas.decidir` los
  pone en el `diagnostico` de la decisión, pero la corrida no lo lee (solo registra el de sus
  propios `_no_evaluado`/`_frenado`) y la 0039 no tiene columna para guardarlo, así que no
  quedan ni en la base ni en el log, y la pantalla de A.6 no puede mostrarlos. **Media**, fuera
  del alcance del carril R (solo toca esta carpeta): se nombra como salvedad en la celda de A.5
  y en la de A.6 del cierre, para la fila que toque la corrida o la 0039.

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
| A.6 | 75 (catálogo de muse r0–r3, que incluye los del lead de la auditoría; 73 por `muta.py` y 2 de dos ediciones por `muta_dos_A6.py`) | 75 | 0 | 0 |

**Todo mutante del catálogo de los implementadores que hoy puede morir, muere.**

A.5 se re-mutó sobre el squash `53c6067` (worktree aparte). Su catálogo (`cat_A5.json`) junta
los ids propios de muse (A5-1…15, B, C3, K) con los del lead de la auditoría del PR (LA, R1–R4),
en su última forma. Cuatro ids viejos quedaron fuera porque el código que mutaban ya no existe; la
razón y el id que los cubre hoy están en `cat_A5.obsoletos.json`. **Equivalente `C3-1-doble-claim`**
(quitar la guarda del claim en `_tomar_lock`): desde r4 la exclusión la da además
`pg_try_advisory_lock`, que rechaza al segundo proceso con el mismo `LockOcupado`; el resultado
observable no cambia.

A.6 se re-mutó sobre el squash `510beda` (worktree aparte). `cat_A6.json` junta los ids de muse
(P, Q, R, S del r0; R1*, R1B*, R2*, R3* de las correcciones, que incluyen los del lead) en su última
forma. S3 y R1C2 mueven el import tardío de `avisar_precio` (dos ediciones en `app/cli.py`): los
siembra `muta_dos_A6.py`, con el mismo método, y su resultado está en `cat_A6.dos.resultado.txt`.
Dos ids quedan fuera, con su razón en `cat_A6.obsoletos.json`: Q8 (duplica a R1B-B9a) y R6 (el
filtro de plantilla que mutaba ya no existe; lo cubre R1B-A5b). Transcripciones no literales: R3 y
R9 («sección eliminada») se siembran cambiando el `id` del bloque; R4, vaciando la frase de cada
fila; R5, sacando la traducción del motivo del JSON (la plantilla ya no traduce). La primera
transcripción del lead de R1B-T3a ponía una frase fija sin el texto «aviso: puente» y sobrevivía:
era infiel (el test busca ese texto); con el formato real del aviso muere por el caso bajo 5 %.

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

**Cerrados** con test por el encargo bis a muse antes de Q4: PR #313, squash `de8c54f`
(`bis.md`). Los tres mueren sobre el commit de muse (`54b46f9`) y sobreviven sobre la base
`6eeaf2f` (control). Ronda cruzada del bis: glm, `LGTM`. A.6 no dejó sobrevivientes.

## Desviaciones de la fila R.1, declaradas

1. Los sobrevivientes se cierran en un PR bis y no «en el mismo PR», porque el carril R
   solo puede tocar `docs/evidencia/repricing-01/R.1/` (runbook de la Fase 11).
2. La cruzada corrió en un worktree aparte (`/Users/dn/dev/_wt/f11-kimi-r1`, detached
   en cada SHA) en vez de `checkout --detach` en `wt-fase11-lead`, para que la
   re-mutación corriera en paralelo en el worktree del lead. Mismo árbol, mismo SHA; el
   worktree aparte se quita al cierre. A.5 y A.6 se re-mutaron también en un worktree aparte
   (`/Users/dn/dev/_wt/f11-rev-A`, detached en su squash), para no sembrar mutantes donde kimi
   revisaba.
5. El bis va en una sola rama (`fase11/r1-cierre-r1`) para los tres sobrevivientes, en vez de
   una por fila (`fase11/<fila>-cierre-r<K>`): salen de la misma re-mutación y son tests sueltos.
3. A.1 tiene dos squashes (#303 y su cierre r1 #307): se revisaron los dos.
4. Cada squash se revisó en trozos por archivo (límite de 60 000 caracteres de
   `cross-review.ps1`); el veredicto por SHA es la unión de sus trozos.

## APPROVE

- **kimi**: sin altas ni medias sobre `662db38`, `eeefb72`, `39cba88`, `efc0555`, `0d88cc8`,
  `53c6067` (A.5) y `510beda` (A.6) (salidas en `kimi/`).
- **lead**: APPROVE sobre los mismos siete squashes y el bis `de8c54f`: todo mutante de los
  catálogos que puede morir muere con base real (A.1 31/31, A.2 58/58, A.3 51/51 + 2
  equivalentes, A.7 32/32, A.5 93/94 + 1 equivalente, A.6 75/75) y los tres sobrevivientes
  propios del lead quedaron cerrados con test en el bis. El `APPROVE lead <sha>` de este
  documento va en el comentario del PR, sobre su head.
