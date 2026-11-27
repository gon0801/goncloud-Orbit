# ADS PROTECCIÓN P03 — pausa económica v2 (`economic_pause_v2`): plan y matriz de fallos

- **Fecha:** 2026-11-27
- **Rama base:** `master` (`3c9d7c4c`, `docs(jev-ads-03): cierra P08 tras evaluacion de 30 dias`)
- **Alcance:** corregir la pausa económica por hoja (`ads_pause_economica`) con una regla nueva
  versionada (`economic_pause_v2`) que **solo puede disparar donde `v1` ya disparaba**, retirar el
  flag global legado y dejarla en `off` con una compuerta verificable de 5 ciclos en `shadow`.
- **Decisión de producto fijada por David:**
  - `ads_propuestas_campana` (avisos manuales por campaña) **permanece apagado** (`off`). Este plan
    no lo toca ni lo enciende.
  - `ads_pause_economica` entra en **`off`** para los dos mercados (fail-closed).
  - Se valida en **`shadow`** durante 5 ciclos **sin afectar** las pujas `live`
    (`ads_optimizer_mode=live`), los caps diarios ni el motor AUTO-04 (`ads_ubicacion_*`).
  - Solo después de la compuerta de la sección 8 se enciende `live`, mercado por mercado.
- **Reglas duras de este bloque:**
  1. `v2` es un subconjunto de `v1` por construcción: la ventana inmadura **únicamente quita
     pausas, nunca agrega**. La Regla 6 (10 días de maduración, día en curso descartado) queda
     intacta.
  2. `shadow` no escribe `decision` ni `apply_queue` ni `v_cambio_bid`: su único rastro es
     `pausa_economica_evaluacion` (append-only).
  3. Cualquier error de lectura o escritura de la práctica se aísla en `SAVEPOINT` y **jamás**
     tumba las decisiones de puja de la plataforma.
  4. Un número tiene una sola fuente: la regla vive en una función pura y se invoca desde cinco
     puntos, no se reimplementa.
  5. Todo monto es `NUMERIC(14,4)` + `currency`; prohibido `float`.
- **Referencias:** `AGENTS.md`, `docs/CONTEXTO.md`, `docs/APPLY.md`,
  `migrations/0104_auto04_ubicacion.sql`, `migrations/0099_arreglos05_tope_ciclo.sql`,
  `app/optimizer/bid.py`, `app/proteccion_venta.py`, `tests/test_architecture.py`.

---

## 0. Verificación contra `origin/master` (`3c9d7c4c`)

Se revisó cada línea, firma, constante y conteo citado en el diseño v4 contra el árbol real. Estas
son las mediciones y las correcciones aplicadas al plan (los contratos y las decisiones no cambian):

| Dato del borrador v4 | Real en `3c9d7c4c` | Acción en este plan |
|---|---|---|
| `app/cycle_apply.py` ~560 líneas | **655** | Presupuesto corregido: `655 + 4 = 659 / 900` |
| `app/cli_productos.py` ~180 líneas | **191** | Presupuesto corregido: `191 + 18 = 209 / 900` |
| `app/optimizer/goals.py` ~160 líneas | **838** | Presupuesto corregido: `838 - 2 = 836 / 900` (no está en allowlist) |
| `app/optimizer/settings_lectura.py` 308 | 308 | `308 - 10 = 298 / 900` |
| `_flag_pause_economica` / `flag_economica_fn` | `app/apply_revalidacion.py:148-154`; kwarg en `:506` y `app/apply_cola.py:465` | Se reemplaza por `modo_economica_fn` (sección 3.4) |
| `revalida_pause` citada en `:606` | La **función** está en `app/apply_revalidacion.py:493-692`; `:606` es la llamada a `motor_bid.decide_pause` | Se cita el rango real |
| `_registra_evidencia` citada en `:635-658` | La **función** está en `app/apply_revalidacion.py:443-476`; `:635-658` es su llamada en `revalida_pause` | Se cita el rango real |
| `_proteccion_activa` citada en `:342-352` | La **función** es `app/cycle_serializacion.py:323-361`; la llamada `bid.decide_pause(...)` ocupa `:342-352` | Se cita el rango real |
| `libera_vencidos` citada en `:656-660` | La **función** es `app/apply_cola.py:632-777`; `:656-660` es donde se precargan cachés (aquí entra la nueva) | Se cita el rango real |
| `pendiente_bid` en `:232` | La **función** es `app/cycle_serializacion.py:185-252`; `:232` es el `"caso": caso.como_json()` que congela el replay | Se cita como la línea del freeze |
| 26 apariciones de `ads_pause_economica` en 8 archivos de tests | **15 apariciones literales en 6 archivos** (más 7 parches de `_flag_pause_economica` / `flag_economica_fn` en 3 archivos) | Lista exacta en la sección 3.7 |
| `_SQL_DESCARTA_ESPERAS` "parametrizado" | Hoy fija `= 'espera_proteccion'` en `app/apply_cola.py:193` | Se parametriza y se actualiza su llamador (`:550-552`) sin duplicar SQL |

Datos que **sí** coinciden y se usan tal cual:

- `tests/test_architecture.py`: `MAX_LINEAS_MODULO = 900` (`:46`), techo `app/cycle.py = 1148`
  (`:79`), techo `app/etiquetas.py = 1059` (`:77`); `app/cycle.py` mide **1144**.
- `app/optimizer/windows.py:413-421` = `fin_ventana_cortes` (`min(max - 3d, D - 10d)`).
- `app/optimizer/replay.py:139` = `CasoHoja.desde_json(inputs["caso"])` para `niveles_v3`.
- `app/cycle_recorrido.py`: `:400` llamada a `_proteccion_activa`, `:415-450` compuertas y emisión,
  `:205-212` salida por clave bloqueada, `:529` lectura del flag legado, `:632-635` conteo de pausas
  de producto.
- `app/cycle_sombra.py:203-216` = patrón `SAVEPOINT` (`with conn.transaction()` + `try/except`),
  `:456-463` = `_descarta_sin_evidencia` (retiro + ajuste de `contadores.decisiones`).
- `app/notifica.py:355` = `digest_ciclo`; `:402-404` = línea de `entidad_inerte`.
- `app/optimizer/bid.py:128-132` = `MOTIVO_PAUSE_ECONOMICA`, `POLITICA_PAUSE_ECONOMICA`,
  `EXCESO_MINIMO`, `MULT_PAUSE_ECONOMICA`; `:248-255` = precedencia de `proteccion`.
- `app/optimizer/politica.py:302` → `_frena_subida_normal` → `politica_temporada.subida_en_espera`
  (`politica_temporada.py:74-84`): es el consumidor real de `proteccion.motivo`.
- `app/apply_cuota.py:90-107` = `MOTIVOS_SIN_APLICAR` con **16** motivos en el orden del CHECK.
- `migrations/0099_arreglos05_tope_ciclo.sql:52-59` = CHECK de **16** motivos.
- `migrations/0104_auto04_ubicacion.sql:24-75` (tabla append-only), `:247-249` (GRANT),
  `:259-294` (bloque `DO $$` con `has_table_privilege`/`has_sequence_privilege`).
- `tools/despliegue/comun.sh:205-206` exige `-- sonda-aplicada:` en las primeras 10 líneas;
  `tests/test_despliegue_motor.py:675` verifica el aborto sin cabecera.

---

## 1. Diagnóstico empírico y formas comparadas

### 1.1 Hallazgo 1: `v1` pausa hojas que se están recuperando

Prototipo READ-ONLY sobre **443 hojas** y **46 días reales** de `ads_metric_observation`
(`2026-10-12` a `2026-11-27`), con desenlace maduro real (`+10d` sobre `v_metric_latest`):

| Regla | Hoja-días disparados | Hojas únicas | Verificables `+10d` | Falsos positivos `+10d` | Hojas FP |
|---|---:|---:|---:|---:|---:|
| **`v1` actual** (solo madura 30d `[D-39..D-10]`) | 86 | 10 | 59 | **12 (20.3%)** | **4** |
| `v2a` (madura + combinada `[D-39..D-1]`) | 73 | 7 | 47 | **0 (0.0%)** | **0** |
| **`v2b` (elegida)** (`v2a` + guarda rentable reciente) | **72** | **7** | **47** | **0 (0.0%)** | **0** |
| `v2c` (`v2a` + bloquear ante cualquier venta reciente) | 60 | 7 | 41 | 0 (0.0%) | 0 |

Casos reales en hojas activas (`ENABLED` en hoja, ad_group y campaña):

- **Salvadas por `v2b`:** MX `2891` (16.5% ACoS reciente el 26 nov), US `4919` (4.0% ACoS reciente
  5 días seguidos), US `5894` (25.3% reciente; hoy sana a 29.5%), US `5891` (21.9% en oct) y US
  `4924` del 4 al 6 nov (combinada 63% < 72.5%).
- **Atrapadas por `v2b` hoy:** US `4924` (80.8% ACoS en 2 meses: `$358.64` gastados para `$443.67`
  de venta) y US `5891` (86.7% combinado con 0 ventas recientes). **0 en MX.**

> El prototipo es indicativo en las 7 hojas que cruzan umbral (usa target vigente, no histórico).
> Lo **invariante por construcción** es el subconjunto: `v2` solo puede disparar donde `v1` ya
> disparaba, porque su única diferencia es *quitar* pausas cuando hay venta reciente que salva,
> recupera o no es medible.

### 1.2 Hallazgo 2: atribución tardía y el veto de 48h

- A `+1d` (`D-1`) la plataforma captura MX `77.4%` y US `77.0%` de la venta final.
- Al vencer el veto de 48h (cuando corre `revalida_pause`) MX sube a `89.1%` y US a **`95.5%`**.
- Correr **la misma función pura** en el ciclo y otra vez en `revalida_pause` absorbe la atribución
  tardía antes de tocar Amazon.

### 1.3 Forma A (ganadora) vs Forma B (descartada)

- **Forma A — motor puro `economic_pause_v2` + tabla append-only `pausa_economica_evaluacion`**
  (espejo de AUTO-04 `0104_auto04_ubicacion.sql`):
  - *Un dueño por estado:* `app/optimizer/pausa_economica.py` (puro) posee la regla, las constantes,
    el codec y la lectura/validación de settings por mercado; `app/pausa_economica_io.py` (IO) hace
    la lectura batch en **1 SQL por barrido** y escribe las evaluaciones en TX3 dentro de su propio
    `SAVEPOINT`.
  - *Cero interferencia en `shadow`:* `decide(caso)` recibe `politica_economica=None`, así que los
    recortes de puja `live` nunca se suprimen ni se tumban por errores de la práctica.
  - *Una sola función pura en 5 puntos:* evaluación `shadow`, `decide(caso)` en `live`,
    `_proteccion_activa`, `revalida_pause` y el replay/verificación de
    `tools/pausa_economica_verifica.py`.
- **Forma B (descartada) — encolar `shadow` en `decision` + `apply_queue`:** choca con las
  decisiones `live` de `bid` en la misma hoja y ciclo, no registra los casos salvados por venta
  reciente y ensucia `apply_queue` con filas que nunca se aplican.

---

## 2. Regla matemática exacta: `economic_pause_v2`

Entrada: una hoja activa (`ENABLED` en hoja, ad_group y campaña, no inerte), elegible con el mismo
alcance que `pause_umbral` — no existe un concepto aparte de `en_impulso` (eso es solo de
ubicaciones, `app/optimizer/ubicacion.py:311`). Una campaña nueva jamás cruza 30d maduros y
cualquier campaña protegida a mano usa `goal.mode = shadow` o `enabled = false`.

1. **Puerta madura (Regla 6).**
   - `cortes` (`windows.fin_ventana_cortes`, `windows.py:413-421`: ventana de hasta 30 días
     terminando en `min(ultima_fecha_hoja - 3d, D - 10d)`, con `>= 7` fechas y acotada por `cerca`
     si aplica) debe estar `completa`, en `moneda`, con `cost`, `ad_revenue`, `orders` y `clicks`
     conocidos, finitos y `>= 0`.
   - `cortes.cost > 3 * target * cortes.ad_revenue / 100`.
   - `exceso_mad = cortes.cost - target * cortes.ad_revenue / 100 >= exceso_minimo`
     (`EXCESO_MINIMO[moneda]`, ya multiplicado por `mult_cortes_baja` en temporada baja).
   - Si **no** cruza madura: `cruza_madura = False` y `evalua_pausa_economica` devuelve `mantener`
     con el motivo de la razón real (abajo). En era `v2`, `_decide_pause` devuelve `(None, <ese
     motivo>)` — o el de `pausa_umbral`/protección si ganan (paso 5) — y el juicio de pujas R1–R17
     sigue normal. En era `v1` nada de esto cambia: conserva sus motivos de hoy
     (`pause_cortes_incompleto`, `pause_orders_desconocido`, `pause_economica_dato_faltante`, …).
     - `cortes is None` (hoja elegible sin ninguna fecha de métrica, `windows.py:482-484`): no se
       juzga ni se escribe fila; se cuenta en `notes.pausa_economica.sin_cortes`.
     - `cortes` existe pero no es juzgable (`not completa`, moneda distinta, `cost`/`ad_revenue`/
       `orders`/`clicks` en `None`, no finitos o negativos): `mantener` +
       **`pause_economica_cortes_incompletos`**.
     - `cortes` juzgable y no cruza el umbral o el exceso: `mantener` +
       **`pause_economica_no_cruza_madura`**.
   - Qué hojas dejan fila en `pausa_economica_evaluacion` y qué cuenta `evaluadas`: sección 3.9.

2. **Integridad de la ventana reciente (Regla 3, fail-closed).**
   - Invariante validado **dentro** de la función pura: `reciente is not None`,
     `reciente.completa is True`, `reciente.moneda == moneda`,
     `reciente.desde == cortes.window_end + 1d`, `reciente.desde <= reciente.hasta`, y `cost`,
     `ad_revenue`, `orders` conocidos, finitos y `>= 0`.
   - Como `cortes.window_end = min(ultima_fecha_hoja - 3d, D - 10d)` puede ser anterior a `D - 10`,
     la ventana reciente `[cortes.window_end + 1d .. D - 1]` puede medir **más de 9 días**. El caché
     batch lee exactamente el mismo rango que `app/proteccion_venta.py:192`
     (`D - (DIAS_VENTANA + DIAS_MADUREZ_CORTES) .. D - 1` = `D-40..D-1`), que cubre cualquier hoja
     decidible. Invariante del reloj: con el mismo `evaluado_el = D`, `window_end <= D - 10` implica
     siempre `window_end + 1 <= D - 1`.
   - **Cobertura de plataforma completa en todos los días de `[desde .. hasta]`:** al menos una fila
     de `keyword`/`product_target` en `v_metric_latest` para `(platform, moneda)` por día (mismo
     helper de cobertura que usa `proteccion_venta`). Ausencia de fila **para la hoja** en un día
     cubierto = cero medido. Cualquier fila con `NULL`, negativo u otra moneda = envenenado →
     `completa = False`. Esto es un **campo explícito** de `LecturaRecienteEconomica`, no la
     propiedad `AgregadoMetricas.completa` (esa solo exige `>= 7` fechas de la entidad,
     `windows.py:180-183`).
   - Si falla integridad: `veredicto="mantener"`, `motivo="pause_economica_reciente_incompleto"`.

3. **Puerta combinada (`[cortes.window_start .. D - 1]`).**
   - `cost_tot = cortes.cost + reciente.cost`, `rev_tot = cortes.ad_revenue + reciente.ad_revenue`.
   - Exige **ambas**: `cost_tot > 3 * target * rev_tot / 100` y
     `exceso_tot = cost_tot - target * rev_tot / 100 >= exceso_minimo`.
   - Si no cruza: `veredicto="mantener"`, `motivo="pause_economica_salvada_reciente"`.

4. **Guarda de recuperación rentable reciente.**
   - Si `reciente.orders >= 1` **y** `reciente.cost <= target * reciente.ad_revenue / 100`:
     `veredicto="mantener"`, `motivo="pause_economica_recuperando_reciente"`.

5. **Veredicto y contrato con `_decide_pause` / `politica.decide`.**
   - Si pasa 1–4: `veredicto="pausar"`, `motivo="pause_economica"` → `_decide_pause` devuelve
     `(ResultadoBid("pause", "pause_economica", ...), None)` y `politica.decide` devuelve
     `Pausar("pause_economica")`.
   - **Semántica de no-pausa (sin congelar pujas):** los tres resultados `mantener`
     (`salvada_reciente`, `recuperando_reciente`, `reciente_incompleto`) **jamás** se convierten en
     `politica.Mantener(...)`; salen de `_decide_pause` como `(None, motivo)` para que
     `politica.decide` (R1–R17) siga su curso normal.
   - **Precedencia con `proteccion_venta_v1`** (`bid.py:248-255`): si además `pausa_umbral` calificó y
     quedó protegida por `proteccion.accion in ("esperar", "indeterminada")`, el motivo que devuelve
     `_decide_pause` sigue siendo **`proteccion.motivo`** y no el de `v2`. Ese motivo es el que
     `politica.py:302` pasa a `_frena_subida_normal` y `politica_temporada.subida_en_espera`
     consume para frenar subidas de puja; perderlo subiría la puja de una hoja con venta reciente.
   - `v1` queda **congelada** (`POLITICA_PAUSE_ECONOMICA = "economic_pause_v1"`) para el replay
     histórico; `v2` estrena `POLITICA_PAUSE_ECONOMICA_V2 = "economic_pause_v2"`.

Constantes (una sola fuente, en `app/optimizer/pausa_economica.py`):

```python
EXCESO_MINIMO = {"USD": Decimal("80"), "MXN": Decimal("1000")}   # movidas desde bid.py
MULT_PAUSE_ECONOMICA = Decimal("3")
POLITICA_PAUSE_ECONOMICA_V2 = "economic_pause_v2"
TOPE_PAUSA_ECONOMICA_CICLO = 5
MOTIVO_PAUSE_ECONOMICA = "pause_economica"
MOTIVO_SALVADA = "pause_economica_salvada_reciente"
MOTIVO_RECUPERANDO = "pause_economica_recuperando_reciente"
MOTIVO_INCOMPLETO = "pause_economica_reciente_incompleto"
MOTIVO_NO_CRUZA_MADURA = "pause_economica_no_cruza_madura"
MOTIVO_CORTES_INCOMPLETOS = "pause_economica_cortes_incompletos"
VEREDICTOS_PAUSA_ECONOMICA = ("mantener", "pausar")
```

---

## 3. Contratos exactos

### 3.1 DAG de imports en `app/optimizer/` (acíclico, cero IO)

```
windows.py (AgregadoMetricas)
   └─> caso.py (LecturaRecienteEconomica; InsumosPausa.reciente_economica opcional)
          └─> pausa_economica.py (constantes, EntradaPausaEconomica, VeredictoPausaEconomica,
                                  evalua_pausa_economica, modo_pausa_economica_desde_settings,
                                  aplica_modo_en_config)
                 └─> bid.py (re-exporta EXCESO_MINIMO, MULT_PAUSE_ECONOMICA,
                             POLITICA_PAUSE_ECONOMICA_V2; conserva v1; delega v2)
                        └─> politica.py (pasa caso.pausa.reciente_economica a _decide_pause)
```

`app/pausa_economica_io.py` importa de `app.optimizer.pausa_economica` y de `app.optimizer.caso`;
**nunca** al revés. `pausa_economica.py` no importa `psycopg`, `httpx`, `app.ads`, `app.db` ni
relativos de nivel ≥ 2 (`tests/test_architecture.py::test_motor_puro_sin_io`).

### 3.2 `app/pausa_economica_io.py` y aislamiento `SAVEPOINT` en TX2

- **`lee_cache_reciente(conn, platform, *, ahora) -> CacheRecienteEconomica`:** un SQL por barrido
  sobre `[D-40 .. D-1]` (mismo rango y mismo criterio de cobertura que
  `proteccion_venta.lee_cache`), con el conjunto de días con ingesta de plataforma
  (`_dias_plataforma`) y, por entidad, la serie diaria de `cost`/`ad_revenue`/`orders` más los días
  envenenados. `CacheRecienteEconomica.lectura(hoja_id, *, desde, hasta)` recorta **en memoria** y
  devuelve `LecturaRecienteEconomica` con `completa` calculado por cobertura + veneno.
  - `ahora` debe ser tz-aware (`ValueError` si no), igual que `proteccion_venta.lee_cache`.
- **Aislamiento TX2 (patrón `cycle_sombra.py:203-216`):** en `_recorre_plataforma`, si
  `modo != "off"`, la carga corre dentro de `with conn.transaction():` (nested → `SAVEPOINT`) envuelta
  en `try/except Exception`. Si falla: `contadores.pausa_economica_error = scrub(str(exc))` y
  `cache_reciente = None`.
  - En `shadow`: se omite el registro de evaluaciones de ese ciclo; las pujas `live` **intactas**.
  - En `live`: `reciente_economica = None` → `_decide_pause` devuelve
    `(None, "pause_economica_reciente_incompleto")` → cero pausas económicas ese ciclo y el juicio
    de pujas `live` **intacto**.

### 3.3 Contrato de TX3 (`app/pausa_economica_io.py` + `app/cycle.py`, 2 líneas)

Firma posicional compacta, espejo de `tx3_ubicaciones` (`app/cycle.py:774-783`), para respetar las
100 columnas de Ruff y el techo de `app/cycle.py`:

```python
def tx3_pausa_economica(
    conn: psycopg.Connection,
    cycle_id: int,
    config_id: int,
    contadores: _Contadores,
    pendientes: Sequence[_Pendiente],
    ids_decisiones: Sequence[int],
    cuerpo: dict,
) -> None:
    """Inserta evaluaciones en pausa_economica_evaluacion dentro de su propio SAVEPOINT.
    Si falla, anota error_tx3 en cuerpo['pausa_economica'] sin abortar TX3."""
```

- `app/cycle.py`: **1 línea de import** + **1 línea de llamada**, renombrando el local
  `ids_decisiones` a `ids` en `:784` y `:788` para que
  `tx3_pausa_economica(conn, cycle_id, config_id, contadores, pendientes, ids, cuerpo)` quepa en 100
  columnas. Total `1144 + 2 = 1146 / 1148`, **sin tocar la allowlist**.
- `config_id` llega desde TX3. El mapeo `ad_entity_id -> decision_id` se arma desde
  `(pendientes, ids_decisiones)` ya filtrado y estampado (`app/cycle.py:767-786`) y solo incluye
  `p.kind == "pause" and p.inputs.get("motivo") == "pause_economica"`.
- Cuando `modo == "live"` y `veredicto == "pausar"` **no** produjo decisión económica (ganó
  `pause_umbral`, precedió un `Regresar` de temporada, aplicó `cooldown`, bloqueó
  `producto_sin_evidencia` o saltó `anomalia_tope`), `decision_id` se persiste **`NULL`**. Una hoja
  con clave bloqueada en `apply_queue` sale antes de evaluarse (`cycle_recorrido.py:205-212`) y no
  genera fila ese ciclo.
- `SAVEPOINT` propio: un error de inserción se captura y se anota en
  `cuerpo["pausa_economica"]["error_tx3"]`; TX3 sigue y sella.

### 3.4 Cortacircuito `TOPE_PAUSA_ECONOMICA_CICLO = 5` por mercado y ciclo (solo `live`)

Durante el recorrido de `_SQL_DECISORAS` en `_recorre_plataforma`, cuando `modo == "live"` y una hoja
produce un `_Pendiente` con `kind == "pause"` y `motivo == "pause_economica"`, se guarda en memoria la
referencia mínima `(indice_en_pendientes, caso, contexto_hoja)` para poder re-decidir la puja.

Al terminar el bucle de esa plataforma (dentro de TX2, **antes** de TX3) y si el total de pausas
económicas emitidas excede `TOPE_PAUSA_ECONOMICA_CICLO` (`> 5`):

1. **Retiro y corrección de conteo:** se retiran esos `n` `_Pendiente` de `pendientes` y se resta
   `contadores.decisiones["pause"] -= n` (mismo patrón que `_descarta_sin_evidencia`,
   `cycle_sombra.py:456-463`).
2. **Re-juicio inmediato de pujas (cero pérdida de recortes y replay fiel):** por cada hoja retirada
   se construye
   ```python
   caso_sin_econ = replace(
       caso,
       pausa=replace(caso.pausa, politica_economica=None, reciente_economica=None),
   )
   ```
   se evalúa `decisor(caso_sin_econ)` y su veredicto re-entra desde `cycle_recorrido.py:400`
   (`_proteccion_activa` → compuertas `:415-450`), pasando **`caso_sin_econ`** a
   `pendiente_bid(..., caso=caso_sin_econ)` (`cycle_serializacion.py:185`) para que el freeze
   `"caso": caso.como_json()` (`:232`) y el replay determinista (`optimizer/replay.py:139`)
   coincidan byte a byte con el recorte emitido, y cualquier hoja protegida registre su episodio.
3. **Aviso inmediato por Telegram:** `contadores.skips_entidad["pausa_economica_anomalia_tope"] += n`
   y `notes["pausa_economica"]["anomalia_tope"] = True`; `notifica.digest_ciclo` añade una línea
   explícita junto a `entidad_inerte` (`app/notifica.py:402-404`) cuando esa clave está presente
   (`f"pausa economica frenada por tope: {skips_entidad['pausa_economica_anomalia_tope']}"`).
   Regla 3: clave ausente = no se menciona, nunca un `0` inventado.
4. **Auditoría:** las filas correspondientes en `pausa_economica_evaluacion` **igual** se insertan en
   TX3 con `modo = 'live'`, `veredicto = 'pausar'` y `decision_id = NULL`.

### 3.5 Tercer llamador: `_proteccion_activa` (`app/cycle_serializacion.py:323-361`)

`_proteccion_activa` pasa `reciente_economica=pausa.reciente_economica` a `bid.decide_pause(...)`
(`:342-352`) para que, ante `cortes.orders == 0` + pedido reciente + `proteccion_venta_v1` activa, no
descarte falsamente un `Pausar("pause_economica")` emitido por `decide(caso)` como
`veto_pendiente`. Sin esto, el ciclo pausaría y la serialización lo tiraría: dos caminos, dos
respuestas.

### 3.6 Entrada autorizada de configuración y guarda `D-H1`

- `app/cli_productos.py` (patrón actual `:108-141`): se agrega
  `--pausa-economica {off,shadow,live}` a `productos-modo` y se pasa
  `pausa_economica_modo=args.pausa_economica` a `config_write.guarda_config(..., actor="productos-modo")`.
  Como la clave es por plataforma (`--platform` es obligatorio), la edición afecta **solo** al mercado
  pedido; `ads_optimizer_mode`, `ads_propuestas_campana` y `ads_ubicacion_*` viajan intactos.
- **Guarda `D-H1` al bajar de `live`:** si el modo cambia a `off` o `shadow`, en la **misma
  transacción** (`confirma=False` + `commit` del llamador, patrón `:126-141`) se llama a
  `apply_cola.descarta_esperas_reciente_economica(conn, args.platform)`. Esa función reutiliza
  `_SQL_DESCARTA_ESPERAS` **parametrizado** (`app/apply_cola.py:182-195`, sin duplicar SQL) para
  descartar filas `released` cuyo último `motivo` sea `espera_reciente_economica`, con
  `discard_motivo = MOTIVO_PAUSA_ECONOMICA_APAGADA` (`"pausa_economica_apagada"`).
  - La parametrización cambia la línea `:193` a `= %s` y **actualiza el llamador existente**
    `descarta_esperas_proteccion` (`:550-552`) a
    `(MOTIVO_PROTECCION_APAGADA, platform, "espera_proteccion")`; el SQL sigue siendo uno.
- `config_write.proxima_config` gana el kwarg `pausa_economica_modo: str | None = None` y delega la
  validación y el texto del cambio a `pausa_economica.aplica_modo_en_config(nuevo, cambios, platform, modo)`
  (`off` **quita** la clave, igual que `_aplica_ubicacion`). `app/config_write.py` queda en
  `880 + 12 = 892 / 900`, sin allowlist.

### 3.7 Retiro del flag global legado `ads_pause_economica`

- **Fuente de verdad nueva:** `pausa_economica.modo_pausa_economica_desde_settings(settings, platform)`
  lee `ads_pause_economica_<platform>`; valor fuera de `{off, shadow, live}`, ausente, `None`, `bool`
  o entero → `SettingsInvalido` al escribir y `"off"` al leer (fail-closed).
- **Se retira:** `CLAVE_SETTING_PAUSE_ECONOMICA` y `pause_economica_desde_settings`
  (`app/optimizer/settings_lectura.py:111, 166-169`, −10 líneas) y sus re-exports
  (`app/optimizer/goals.py:97` y `:126`, −2 líneas). `apply_revalidacion._flag_pause_economica`
  (`:148-154`) y el re-export `app/apply_cola.py:91` desaparecen; `flag_economica_fn`
  (`apply_revalidacion.py:506`, `apply_cola.py:465`) pasa a `modo_economica_fn`.
- **Tests a migrar (lista exacta medida en `3c9d7c4c`).** Literal `ads_pause_economica`
  (**15 apariciones en 6 archivos**):
  `tests/test_apply_cola.py:2122, 2186, 2259, 2408, 2417`; `tests/test_arreglos05_a_ciclo.py:957, 1249`;
  `tests/test_cycle.py:2834`; `tests/test_cycle_apply.py:1058`;
  `tests/test_optimizer_goals.py:965-969` (se retiran: prueban la función retirada y se sustituyen
  por los casos de `modo_pausa_economica_desde_settings`); `tests/test_orbit07_m2.py:83`.
  Parches de la función del flag (**7 en 3 archivos**): `tests/test_apply_cola.py:2402, 2411, 2420`
  (`cola._flag_pause_economica`), `tests/test_pause_economica.py:127, 217, 435, 484, 533`
  (`monkeypatch.setattr(apply_cola, "_flag_pause_economica", ...)`) y
  `tests/test_temporada01_d.py:1396` (`flag_economica_fn=`), que pasan a parchear
  `modo_economica_fn` / `apply_cola.modo_economica_fn`.
- Los 16 usos de la regla `v1` en `tests/test_pause_economica.py` **se conservan**: `v1` sigue viva
  para el replay histórico y su verificación no cambia.
- La clave `ads_pause_economica` que hoy exista en la `config_version` vigente de producción **no se
  borra**: queda ignorada. El nuevo modo por mercado nace `off` por ausencia de clave.

### 3.8 Presupuesto de líneas contra `tests/test_architecture.py`

Medido con `splitlines()` en `3c9d7c4c`; techo general `900` (`:46`), `app/cycle.py` `1148` (`:79`),
`app/etiquetas.py` `1059` (`:77`).

| Archivo | Líneas hoy | Delta | Total / Techo | Cambio exacto |
|---|---:|---:|---:|---|
| `app/optimizer/pausa_economica.py` | nuevo | +240 | 240 / 900 | Regla pura `v2`, constantes, codec JSON, lector/escritor/validador de settings por mercado |
| `app/pausa_economica_io.py` | nuevo | +240 | 240 / 900 | `lee_cache_reciente` (1 SQL, `D-40..D-1`), `tx3_pausa_economica` (posicional, `SAVEPOINT`), `resumen_evals` (notas del ciclo) y `resumen_ultimo_ciclo` |
| `app/optimizer/caso.py` | 706 | +28 | 734 / 900 | `LecturaRecienteEconomica` + campo opcional `reciente_economica` en `InsumosPausa` (omitido del JSON si `None`: replay histórico intacto) |
| `app/optimizer/bid.py` | 362 | +26 | 388 / 900 | Re-exporta constantes; `_decide_pause`/`decide_pause` aceptan `reciente_economica`, preservan `v1` y delegan `v2` con `(None, motivo)`, conservando la precedencia de `proteccion.motivo` |
| `app/optimizer/politica.py` | 805 | +3 | 808 / 900 | Pasa `reciente_economica=caso.pausa.reciente_economica` a `_decide_pause` |
| `app/cycle_grupos.py` | 433 | +8 | 441 / 900 | `_Contadores`: `pausa_economica_evals`, `pausa_economica_resumen`, `pausa_economica_error` |
| `app/cycle_serializacion.py` | 427 | +6 | 433 / 900 | `_proteccion_activa` pasa `reciente_economica`; `_notas_cuerpo` incluye `pausa_economica` cuando `modo != "off"` |
| `app/cycle_recorrido.py` | 698 | +72 | 770 / 900 | Lee `modo` por plataforma; carga `CacheRecienteEconomica` en `SAVEPOINT`; `shadow` evalúa aparte de `decide(caso)`; `live` pasa `v2` + `reciente_economica` y ejecuta el cortacircuito con re-juicio congelando `caso_sin_econ` |
| `app/cycle_sombra.py` | 533 | +10 | 543 / 900 | Propaga `reciente_economica` en `_insumos_pausa` (`:146`) |
| `app/cycle_temporada.py` | 175 | +4 | 179 / 900 | Propaga `reciente_economica` en `_pausa_sombra` (`:81`) |
| `app/cycle.py` | 1144 | +2 | **1146 / 1148** | 1 import + 1 llamada `tx3_pausa_economica(...)`; renombra local `ids_decisiones`→`ids` en `:784, :788` |
| `app/notifica.py` | 772 | +3 | 775 / 900 | Línea explícita en `digest_ciclo` (`:402-404`) con `pausa_economica_anomalia_tope` |
| `app/cycle_apply.py` | **655** | +4 | 659 / 900 | Expone `espera_reciente` en `notas["cortes_liberados"]` junto a `espera_target` (`:538-545`) |
| `app/apply_cuota.py` | 688 | +8 | 696 / 900 | `MOTIVO_ESPERA_RECIENTE = "espera_reciente_economica"` **al final** de `MOTIVOS_SIN_APLICAR` (`:90-107`, posición 17) |
| `app/apply.py` | 842 | +1 | 843 / 900 | Re-export de `MOTIVO_ESPERA_RECIENTE` (`:50-77`) |
| `app/apply_revalidacion.py` | 746 | +44 | 790 / 900 | `modo_pausa_economica_desde_settings` en vez del flag; `MOTIVO_ESPERA_RECIENTE` en `MOTIVOS_ESPERA` (`:53`); `_registra_evidencia` (`:443-476`) con lectura reciente, `exceso_combinado` y motivo `v2` |
| `app/apply_cola.py` | 780 | +26 | 806 / 900 | Quita el re-export del flag; carga perezosa de `CacheRecienteEconomica` en `SAVEPOINT` (`:661`) cuando `modo == "live"`; rama de 3 vías (`:726-729`); `espera_reciente: int = 0` en `ResultadoLiberacion` (`:233-245`); parametriza `_SQL_DESCARTA_ESPERAS` y agrega `descarta_esperas_reciente_economica` |
| `app/config_write.py` | 880 | +12 | **892 / 900** | `pausa_economica_modo` delega en `pausa_economica.aplica_modo_en_config` |
| `app/cli_productos.py` | **191** | +18 | 209 / 900 | `--pausa-economica {off,shadow,live}` + descarte atómico `D-H1` |
| `app/dashboard_salud.py` | 242 | +12 | 254 / 900 | `pausa_economica: resumen_ultimo_ciclo(conn, plataforma)` (patrón `_ubicaciones_de`, `:190-196`) |
| `app/optimizer/settings_lectura.py` | 308 | −10 | 298 / 900 | Retira constantes y lector del flag global |
| `app/optimizer/goals.py` | **838** | −2 | 836 / 900 | Retira los dos re-exports (`:97`, `:126`) |
| `app/etiquetas.py` | 1059 | +8 | **1067** (techo +8 con razón) | 8 etiquetas: los 5 motivos `v2` (`salvada`, `recuperando`, `reciente_incompleto`, `no_cruza_madura`, `cortes_incompletos`), `espera_reciente_economica`, `pausa_economica_anomalia_tope` y `pausa_economica_apagada` |
| `tests/test_architecture.py` | 2462 | +0/−0 | — | Solo se sube `ALLOWLIST_TAMANO_TECHO["app/etiquetas.py"]` de `1059` a `1067` con la razón escrita (el techo es ratchet: crecer exige justificarlo) |
| `tools/pausa_economica_verifica.py` | nuevo | +160 | 160 | CLI read-only: replay determinista sobre `entrada` + verificación `+10d` contra `v_metric_latest` |

Ningún archivo nuevo ni modificado cruza su techo y **ninguno entra a la allowlist**.

Notas del presupuesto: las 8 etiquetas nuevas van en `MOTIVO_SALUD` (motivos de práctica y de
descarte de la evaluación), cada una en **una línea de ≤ 100 columnas**, así que el techo queda exacto
en `1067`; `pause_economica` ya tiene texto en `MOTIVO_DECISION` y no se toca. Si alguna necesitara dos
líneas se sube a `1068` con la razón escrita en el mismo PR, nunca se recorta el texto. La prueba
`tests/test_etiquetas.py::test_vocabulario_dueno_cubierto` es la que obliga a que ningún motivo quede
sin etiqueta (`faltan = set(vocabulario) - set(ETIQUETAS[dominio])`).

### 3.9 Qué hojas producen fila en `pausa_economica_evaluacion` y qué cuenta `evaluadas`

- **Regla de fila (una sola, igual en `shadow` y en `live`):** toda hoja elegible del recorrido (pasó
  las compuertas de `_procesa_decisora`: `ENABLED`, no inerte, clave de pausa no bloqueada) **cuya
  ventana de cortes esté medida** (`cortes_hoja is not None`, `windows.ventana_cortes`) produce
  **exactamente una fila** por ciclo, cruce o no la puerta madura. `UNIQUE (cycle_id, ad_entity_id)`
  garantiza una por hoja y ciclo.
  - Una hoja **sin** ventana de cortes (`cortes_hoja is None`, `windows.py:482-484`) **no** produce
    fila: no existirían ni `ventana_reciente_desde/hasta` ni `entrada` ciertas. Se cuenta aparte.
  - Una hoja que sale antes de evaluarse (clave bloqueada en `apply_queue`, `cycle_recorrido.py:205-212`;
    inerte; o `politica` apagada) no produce fila ese ciclo.
- **Veredicto y motivo por caso:** `pausar`/`pause_economica` (pasa 1–4), y los cinco `mantener`:
  `pause_economica_salvada_reciente`, `pause_economica_recuperando_reciente`,
  `pause_economica_reciente_incompleto`, `pause_economica_cortes_incompletos` y
  `pause_economica_no_cruza_madura`. `ventana_reciente_desde/hasta` siempre derivan de
  `cortes_hoja.window_end + 1d` y `D - 1`, así que el `NOT NULL` del esquema se cumple en todas las
  filas.
- **Una sola evaluación por hoja:** el recorrido evalúa `evalua_pausa_economica` con la
  `EntradaPausaEconomica` congelada; en `live` `_decide_pause` re-ejecuta la misma función pura con la
  misma entrada (determinista y sin IO). La fila guarda esa `entrada` en JSONB, que es exactamente lo
  que `tools/pausa_economica_verifica.py` re-ejecuta para el replay.
- **Qué cuenta `notes.pausa_economica`** (un contador, una fuente, en `_Contadores`):
  - `evaluadas`: filas que TX3 insertó en el ciclo = hojas juzgadas, incluidos todos los `mantener` y
    los dos motivos del paso 1. Es el número de la compuerta 3.1.
  - `pausar` y `mantener`: desglose del veredicto; pueden ser `0` y el ciclo sigue siendo válido.
  - `sin_cortes`: hojas elegibles descartadas por no tener ventana medida (sin fila).
  - `error` (lectura de caché o evaluación `shadow`) y `error_tx3` (inserción): si aparecen, el ciclo
    no cuenta para la compuerta.
  - `anomalia_tope`: `True` cuando el cortacircuito retiró pausas (las filas se insertan igual, con
    `decision_id NULL`).
  - El bloque se construye **una sola vez** con `pausa_economica_io.resumen_evals(evaluaciones)` a
    partir de la lista de evaluaciones del ciclo (`contadores.pausa_economica_evals`); `evaluadas` es
    su longitud y el desglose por motivo sale de la misma lista. No hay conteos paralelos.
- **Efecto en la compuerta de 5 ciclos:** MX puede cerrar los 5 ciclos con `pausar = 0` y
  `evaluadas > 0`; eso es lo que exige la compuerta 3.1 (sección 8.3), porque mide que la práctica
  juzgó hojas, no que haya pausas.

---

## 4. Pruebas (TDD: rojo antes, verde después — Regla 9)

Cada prueba nueva debe fallar contra `3c9d7c4c` y pasar después del cambio. Las E2E llaman a la
función de entrada real, no a una pieza con el mismo nombre.

### 4.1 Motor puro — `tests/test_pausa_economica_v2.py`

1. `test_v2_no_pausa_si_la_ventana_reciente_salva` (`v2a`).
2. `test_v2_no_pausa_con_recuperacion_rentable_reciente` (`v2b`, guarda del paso 4).
3. `test_v2_pausa_cuando_combinada_sigue_sobre_el_umbral` (caso US `4924`: 80.8% ACoS).
4. `test_v2_invariantes_de_ventana_reciente_fail_closed` (parametrizado: `desde` desalineado,
   `completa=False`, moneda distinta, negativo, `None` → `reciente_incompleto`).
5. `test_v2_es_subconjunto_de_v1` (propiedad: para toda entrada donde `v2` pausa, `v1` también).
6. `test_v2_no_es_subconjunto_pero_conserva_precedencia_de_proteccion` (con `pausa_umbral` +
   `proteccion.accion="esperar"`, el motivo devuelto es `proteccion.motivo`).
7. `test_v1_congelada_sigue_reproduciendo_decisiones_historicas` (era `economic_pause_v1`).
8. `test_v2_paso_1_distingue_no_cruza_madura_de_cortes_incompletos` (parametrizado: `cortes` medido
   bajo umbral → `mantener`/`pause_economica_no_cruza_madura`; `cortes` con `completa=False`, moneda
   distinta o campos `None`/negativos → `mantener`/`pause_economica_cortes_incompletos`).
9. `test_v2_toda_hoja_juzgada_tiene_un_motivo_del_vocabulario_cerrado` (ningún caso devuelve `motivo`
   vacío ni fuera de las 6 constantes; `veredicto` siempre en `VEREDICTOS_PAUSA_ECONOMICA`).

### 4.2 IO y esquema — `tests/test_pausa_economica_io.py`, `tests/test_apply_schema.py`

8. `test_lee_cache_reciente_cobertura_y_veneno` (día sin ingesta de plataforma → `completa=False`;
   día cubierto sin fila de la hoja → cero medido; fila `NULL`/negativa/otra moneda → veneno).
9. `test_tx3_pausa_economica_mapea_decision_id_y_persiste_null` (éxito y `anomalia_tope`).
10. `test_tx3_pausa_economica_error_no_aborta_tx3` (`SAVEPOINT` + `error_tx3`).
11. `test_pausa_economica_evaluacion_append_only` (UPDATE/DELETE/TRUNCATE revientan),
    `test_grants_de_pausa_economica_evaluacion` (4 roles `SELECT`, `app_decide` `INSERT`+secuencia,
    cero `UPDATE`/`DELETE`) y `test_motivo_veredicto_check_rechaza_combinaciones_fuera_del_vocabulario`
    (`pausar` con motivo distinto de `pause_economica`, `mantener` con motivo inventado).
12. `test_motivos_sin_aplicar_espejo_estatico_del_check` (`tests/test_apply_schema.py:989`) debe
    seguir verde con 17 motivos, mismo orden, apéndice al final.
13. `test_reversa_0105_aborta_con_datos_live_o_espera_reciente` (guarda Regla 5).

### 4.3 Ciclo y revalidación — `tests/test_pausa_economica_ciclo.py`, `tests/test_apply_cola.py`

14. `test_corre_ciclo_shadow_evalua_sin_tocar_decision_ni_cola` (`app.cycle.corre_ciclo` con
    `ads_optimizer_mode=live` + `pausa_economica_amazon_us=shadow`).
15. `test_corre_ciclo_lector_fallando_no_cambia_decisiones_de_puja` (mismas decisiones emitidas en
    `shadow` y en `live`, error anotado en `notes.pausa_economica`).
16. `test_corre_ciclo_hueco_en_d_minus_2_da_reciente_incompleto`.
17. `test_corre_ciclo_cortacircuito_retira_pausas_y_conserva_recortes` (6 hojas sangrantes en `live`:
    0 pausas, `contadores.decisiones["pause"] == 0`, mismos recortes que en `shadow`, replay exacto
    sobre `caso_sin_econ`, filas con `decision_id NULL`).
18. `test_digest_ciclo_menciona_anomalia_tope` y `test_digest_ciclo_omite_la_linea_sin_clave`
    (`app.notifica.digest_ciclo`).
19. `test_libera_vencidos_venta_tardia_descarta_sin_put` (`app.apply_cola.libera_vencidos`).
20. `test_libera_vencidos_espera_reciente_cuenta_y_no_gasta_cuota` (hueco o fallo del lector →
    `released` con `espera_reciente_economica`, `espera_reciente=1`, `negative`/`harvest` siguen).
21. `test_libera_vencidos_401_aborta_sin_descartar`.
22. `test_cli_productos_baja_a_shadow_descarta_esperas_en_la_misma_tx` (`app.cli_productos.main` con
    `ORBIT_DSN_ADMIN` de prueba) y `test_guarda_config_rechaza_modo_invalido`
    (`app.config_write.guarda_config`).
23. `test_editar_mx_no_toca_us_ni_optimizer_mode_ni_propuestas` (`guarda_config` + `proxima_config`).
24. `test_corre_ciclo_shadow_mercado_sin_candidatos_cumple_compuerta` (`app.cycle.corre_ciclo` con
    `ads_optimizer_mode=live` y `pausa_economica_amazon_mx=shadow`, mercado cuyas hojas no cruzan la
    puerta madura): **una fila por hoja elegible con `cortes_hoja is not None`**, todas con
    `veredicto='mantener'` y `motivo IN ('pause_economica_no_cruza_madura',
    'pause_economica_cortes_incompletos')`, `notes.pausa_economica.evaluadas == len(hojas juzgadas)`,
    `pausar == 0`, `sin_cortes` reportado sin fila y sin `error`/`error_tx3`: la compuerta 3.1 se
    cumple con cero candidatos y cero pausas.
25. `test_corre_ciclo_hoja_sin_ventana_no_escribe_fila` (hoja elegible sin ninguna fecha de métrica,
    `windows.py:482-484` → `cortes_hoja is None`): cero filas para esa hoja, `sin_cortes` la cuenta y
    el resto del ciclo no cambia.

---

## 5. Esquema DB

### 5.1 `migrations/0105_pausa_economica_v2.sql`

Primera línea obligatoria (`tools/despliegue/comun.sh:205-206`, `tests/test_despliegue_motor.py:675`):

```sql
-- sonda-aplicada: SELECT to_regclass('public.pausa_economica_evaluacion') IS NOT NULL AND EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decision_sin_aplicar_motivo_check' AND pg_get_constraintdef(oid) LIKE '%espera_reciente_economica%')
```

Sin `BEGIN`/`COMMIT` (el despliegue la aplica con `psql -1`). Reversa:
`0105_reversa_pausa_economica_v2.sql` (excluida del despliegue por `*_reversa_*`,
`verify/Launch.md:26-31`).

**Tabla append-only `pausa_economica_evaluacion`** (espejo de `ubicacion_evaluacion`,
`0104:24-75`):

```sql
CREATE TABLE pausa_economica_evaluacion (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cycle_id          BIGINT      NOT NULL REFERENCES optimizer_cycle (id),
    ad_entity_id      BIGINT      NOT NULL REFERENCES ad_entity (id),
    platform          platform    NOT NULL CHECK (platform IN ('amazon_mx', 'amazon_us')),
    modo              TEXT        NOT NULL CHECK (modo IN ('shadow', 'live')),
    politica          TEXT        NOT NULL CHECK (politica = 'economic_pause_v2'),
    config_version_id BIGINT      NOT NULL REFERENCES config_version (id),
    decided_at        TIMESTAMPTZ NOT NULL,
    ventana_reciente_desde DATE   NOT NULL,
    ventana_reciente_hasta DATE   NOT NULL,
    moneda            currency    NOT NULL,
    target_acos_pct   NUMERIC(7,4) NOT NULL,
    cortes_completa   BOOLEAN     NOT NULL,
    cost_maduro       NUMERIC(14,4) NULL,
    revenue_maduro    NUMERIC(14,4) NULL,
    cost_reciente     NUMERIC(14,4) NULL,
    revenue_reciente  NUMERIC(14,4) NULL,
    exceso_combinado  NUMERIC(14,4) NULL,
    exceso_minimo     NUMERIC(14,4) NOT NULL,
    entrada           JSONB       NOT NULL,
    veredicto         TEXT        NOT NULL CHECK (veredicto IN ('mantener', 'pausar')),
    motivo            TEXT        NOT NULL CHECK (motivo <> ''),
    decision_id       BIGINT      NULL REFERENCES decision (id),
    CONSTRAINT pausa_economica_evaluacion_ventana
        CHECK (ventana_reciente_desde <= ventana_reciente_hasta),
    -- Vocabulario cerrado: un `pausar` es siempre `pause_economica`; un `mantener`
    -- lleva uno de los cinco motivos de no-pausa (Regla 3: dato faltante,
    -- umbral no cruzado, salvada, recuperando e incompleto son distintos).
    CONSTRAINT pausa_economica_evaluacion_motivo_veredicto
        CHECK ((veredicto = 'pausar' AND motivo = 'pause_economica')
               OR (veredicto = 'mantener' AND motivo IN (
                       'pause_economica_salvada_reciente',
                       'pause_economica_recuperando_reciente',
                       'pause_economica_reciente_incompleto',
                       'pause_economica_cortes_incompletos',
                       'pause_economica_no_cruza_madura'))),
    -- Regla 3: un `mantener` no lleva decision; un `pausar` live puede no tenerla (tope).
    CONSTRAINT pausa_economica_evaluacion_veredicto_coherente
        CHECK ((veredicto = 'mantener' AND decision_id IS NULL)
               OR veredicto = 'pausar'),
    CONSTRAINT pausa_economica_evaluacion_modo_decision
        CHECK ((modo = 'shadow' AND decision_id IS NULL) OR modo = 'live'),
    UNIQUE (cycle_id, ad_entity_id)
);
```

- La tabla **solo** admite filas de hojas con ventana de cortes medida (sección 3.9): `entrada` lleva
  el `AgregadoMetricas` maduro y `cortes_completa` lo resume sin parsear JSON. Una hoja sin ventana no
  se inserta, por eso `ventana_reciente_desde/hasta` y `cortes_completa` pueden ser `NOT NULL`.

- Índice `pausa_economica_evaluacion_entidad_idx ON (platform, ad_entity_id, id DESC)`.
- Triggers `pausa_economica_evaluacion_append_only` (`BEFORE UPDATE OR DELETE ... FOR EACH ROW
  EXECUTE FUNCTION prohibir_mutacion()`) y `pausa_economica_evaluacion_append_only_truncate`
  (`BEFORE TRUNCATE ... FOR EACH STATEMENT`).
- `COMMENT ON TABLE` con la decisión (por qué `v2` y por qué `decision_id` puede ser `NULL`).

**Roles reales (espejo exacto de `0104:247-294`; nunca `orbit_*` ni `GRANT ALL`):**

```sql
GRANT SELECT ON pausa_economica_evaluacion TO app_read, app_decide, app_ingest, app_admin;
GRANT INSERT ON pausa_economica_evaluacion TO app_decide;
GRANT USAGE, SELECT ON SEQUENCE pausa_economica_evaluacion_id_seq TO app_decide;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'pausa_economica_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'pausa_economica_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'pausa_economica_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'INSERT')
       OR has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'UPDATE')
       OR has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'DELETE')
       OR has_table_privilege('app_read', 'pausa_economica_evaluacion', 'INSERT')
       OR has_table_privilege('app_ingest', 'pausa_economica_evaluacion', 'INSERT')
       OR NOT has_sequence_privilege('app_decide',
              'pausa_economica_evaluacion_id_seq', 'USAGE') THEN
        RAISE EXCEPTION '0105: privilegios de pausa_economica_evaluacion incorrectos';
    END IF;
END $$;
```

**CHECK `decision_sin_aplicar_motivo_check` (17 motivos, mismo orden que `MOTIVOS_SIN_APLICAR`):**
se reemplaza el nombrado de `0099:52-59` con el mismo bloque `DO $$` de verificación de unicidad,
agregando `espera_reciente_economica` **al final**:

```sql
CHECK (motivo IN (
    'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
    'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
    'espera_target', 'sin_respuesta', 'perdida', 'choque_clave',
    'espera_proteccion', 'bid_cambiado_fuera', 'vigencia_vencida',
    'tope_ciclo', 'espera_reciente_economica'
))
```

`COMMENT ON CONSTRAINT` actualizado con la razón del apéndice.

### 5.2 `migrations/0105_reversa_pausa_economica_v2.sql`

1. **Guarda Regla 5:** `RAISE EXCEPTION` si existe alguna fila con `modo = 'live'` en
   `pausa_economica_evaluacion` o alguna fila con `motivo = 'espera_reciente_economica'` en
   `decision_sin_aplicar` (con `IF EXISTS` + `to_regclass` para ser re-ejecutable).
2. Restaura `decision_sin_aplicar_motivo_check` **exacta de `0099`** (16 motivos).
3. `DROP TABLE IF EXISTS pausa_economica_evaluacion;` (los triggers y el índice caen con ella).

---

## 6. Matriz de fallos (5 dimensiones por frontera)

| Frontera | (1) Nivel de medición y por qué | (2) Timeout / 429 / 500 / 401 / 403 / vacío o incompleto | (3) Pieza vecina apagada o lectura fallida | (4) Cambio de modo, cierre de día o reinicio a mitad | (5) Prueba E2E (función real de entrada) |
|---|---|---|---|---|---|
| **Lectura batch reciente (`lee_cache_reciente`) y escritura TX3 (`tx3_pausa_economica`)** | Cobertura por `(platform, moneda, metric_date)` y métricas por `(ad_entity_id, metric_date)` sobre `[D-40..D-1]`: ausencia en día cubierto = `0` medido; día sin ingesta de plataforma = hueco real. Se mide por día porque la atribución tardía llega día por día y la regla decide sobre una suma. | Hueco en `[cortes.window_end+1d .. D-1]`, `NULL`, negativo u otra moneda → `completa=False` → `(None, "pause_economica_reciente_incompleto")`. No hay HTTP en esta frontera. | **Aislamiento `SAVEPOINT` (TX2 y TX3):** si `lee_cache_reciente` o `tx3_pausa_economica` lanzan error SQL, se captura en `notes.pausa_economica.error` / `error_tx3`; en `live` degrada a `reciente_incompleto` (cero pausas económicas) y **las pujas `live` de la plataforma se emiten y confirman intactas**. | TX3 es atómica: un fallo previo al commit no deja filas huérfanas. Un ejecutor `resume_of` **no** re-evalúa ni reescribe filas de evaluación (`UNIQUE (cycle_id, ad_entity_id)` y `ON CONFLICT DO NOTHING`). | `app.cycle.corre_ciclo`: (a) hueco de 1 día en `D-2` → `motivo='pause_economica_reciente_incompleto'`, cero pausas; (b) lector con excepción en `shadow` y en `live` → **mismas decisiones de puja emitidas**, error anotado en `notes`; (c) `tx3_pausa_economica` que revienta → `error_tx3` y ciclo sellado. |
| **Evaluación `shadow` vs `live`, `_proteccion_activa` y cortacircuito** | Por hoja elegible (`keyword`/`product_target`) en el ciclo de su plataforma, y **toda hoja elegible con ventana de cortes medida deja una fila** (sección 3.9), cruce o no la puerta madura: las que no cruzan se registran como `mantener` con su motivo (`no_cruza_madura` / `cortes_incompletos`). El cortacircuito se mide por plataforma y ciclo (`TOPE_PAUSA_ECONOMICA_CICLO = 5`) porque una ingesta rota se ve como «todas las hojas de ese mercado». | N/A: es puro en memoria, sin red ni reloj propio. | Si `ads_temporada_<plat>` está `off`, usa `exceso_minimo` base. Si `ads_proteccion_venta` está `on` y `cortes.orders == 0` con pedido reciente, `_proteccion_activa` recibe `reciente_economica` y coincide con `decide(caso)` sin descartar el `Pausar`; si no pausa económicamente, `proteccion.motivo` conserva precedencia (`politica.py:302`). Si `> 5` hojas disparan en `live`: se retiran todas las pausas económicas, se resta `contadores.decisiones["pause"]`, se re-evalúa `decisor(caso_sin_econ)` congelando el caso desde `cycle_recorrido.py:400` y se registra `skips.entidad["pausa_economica_anomalia_tope"]`, que `digest_ciclo` pinta junto a `:402-404`. | `off → shadow`: empieza el ciclo siguiente sin tocar `decision` ni `apply_queue`; pujas `live` intactas. Una hoja con fila `released` esperando (`espera_reciente_economica`) queda sin recortes de puja y sin fila de evaluación ese ciclo (`cycle_recorrido.py:205-212`) hasta resolverse. Reinicio a mitad de TX2: sin decisiones insertadas, el ciclo se recupera por `resume_of` y vuelve a decidir. | `app.cycle.corre_ciclo` + `app.notifica.digest_ciclo`: (a) `ads_optimizer_mode='live'` + `pausa_economica='shadow'` → hoja sangrante recibe recorte de bid `live` y guarda fila `shadow`; (a2) mercado cuyas hojas no cruzan la puerta madura → filas `mantener`/`pause_economica_no_cruza_madura` y `evaluadas > 0` con `pausar = 0`; (b) `orders_mad=0` + venta reciente cara + `proteccion_venta_v1` → sí encola `pause_economica`; (c) 6 hojas sangrantes en `live` → 0 pausas encoladas, **los mismos recortes de puja que en `shadow` con replay fiel (`caso_sin_econ`)**, `contadores.decisiones["pause"] == 0`, texto de alerta verificado en `digest_ciclo` y filas con `decision_id NULL`. |
| **Lectura de caché en el barrido (`libera_vencidos`) y revalidación 48h (`revalida_pause`)** | Carga perezosa de `CacheRecienteEconomica` en `libera_vencidos` (`apply_cola.py:656-665`) solo con `modo == "live"` y al llegar la primera fila económica; revalidación por fila `released` contra `LIST` fresco y métricas frescas, con evidencia completa en `_registra_evidencia` (`apply_revalidacion.py:443-476`). Se mide por fila porque cada `decision` vence a su hora. | **401/403** en LIST/PUT → `propaga_aborto=True` (detiene la corrida, **no** descarta ni marca perdida). **Timeout/429/500** → `MOTIVO_SIN_RESPUESTA` (queda `released`, 0 cuota). **Reciente incompleto** → `MOTIVO_ESPERA_RECIENTE` (en `MOTIVOS_ESPERA`, queda `released`, 0 cuota, reintenta el ciclo siguiente). **Venta reciente que salva o recupera** → `discarded` (`ya_no_califica`, 0 PUT). | **`SAVEPOINT` en el barrido:** si `lee_cache_reciente` falla dentro de `libera_vencidos`, las filas económicas de ese barrido quedan en `MOTIVO_ESPERA_RECIENTE` y el resto (`pause_umbral`, `negative`, `harvest`) sigue normal. Si falla el `target` de `margen_producto` → `MOTIVO_ESPERA_TARGET`. Si la hoja ya no está `ENABLED` o tiene gracia manual → descarta sin PUT. | Bajar `live → shadow/off` en `cli_productos.py` descarta en la misma TX las filas en `espera_reciente_economica` (`pausa_economica_apagada`); al vencer el veto de otras filas `pending_veto`, `revalida_pause` hace `LIST` y las descarta (`ya_no_califica`, **0 PUT**) salvo que también califiquen por `pause_umbral`. Cierre de día o reinicio tras `applying` se reconcilia por readback; `reversa_pause` reactiva y abre gracia de 7d. | `app.apply_cola.libera_vencidos`: (a) venta tardía en 48h → `discarded` (`ya_no_califica`, 0 PUT); (b) hueco reciente o fallo de `lee_cache_reciente` → `released` con `espera_reciente_economica`, 0 cuota, `espera_reciente=1`, mientras `negative`/`harvest` siguen; (c) baja a `shadow` sin calificar umbral → `discarded` (0 PUT); (d) 401 → aborta sin descartar; (e) caso real → PUT `PAUSED` + evidencia reciente + readback + `reversa_pause`. |
| **Configuración por mercado (`config_write` + `cli_productos`)** | Clave por plataforma (`ads_pause_economica_amazon_mx`, `ads_pause_economica_amazon_us`) porque el encendido es mercado por mercado y MX no debe moverse al tocar US. | Valor fuera de `{off, shadow, live}`, `None`, `bool` o entero → `SettingsInvalido` al escribir; `'off'` al leer (fail-closed). No hay red aquí. | `config_version` serializado con `pg_advisory_xact_lock` y `base_config_version_id` (409 si obsoleto): dos ediciones simultáneas no se pisan. | Editar MX jamás modifica US, `ads_optimizer_mode` ni `ads_propuestas_campana`. La guarda `D-H1` corre en la misma TX: si falla el descarte, `rollback` completo y el modo no baja. | `app.cli_productos.main` + `app.config_write.guarda_config`: MX a `shadow` deja US en `off`, rechaza valores inválidos y bajar `live → shadow` descarta `espera_reciente_economica` con `pausa_economica_apagada` en la misma TX; `404/409` por `base_config_version_id` obsoleto no deja cambio parcial. |

**Regla de prueba roja → verde:** cada caso de la columna (5) se escribe primero contra `3c9d7c4c`
(debe fallar), se pega la salida real del fallo en el PR del código y después se muestra pasando.

---

## 7. Fuera de alcance (y por qué)

1. **`ads_propuestas_campana`:** permanece `off` por decisión de producto; este bloque no lo toca.
2. **`ads_apply_cap_*_pause`:** no se baja. Se comparte con `pause_umbral` (pico histórico `live`
   1/día) y `ads_producto_pausa` (hasta 31/día en US); bajarla estrangularía ese motor a futuro. El
   cortacircuito de 5 pausas económicas por mercado y ciclo cubre el riesgo real.
3. **Forma B (encolar `shadow` en `decision`/`apply_queue`):** descartada por la sección 1.3.
4. **Cambiar la regla de pujas (R1–R17), `pause_umbral`, `negative`, `harvest` o el veto de 48h:** sin
   cambios; `v2` solo decide no-pausa y devuelve motivos que **no** congelan el juicio de pujas.
5. **AUTO-04 (`ads_ubicacion_*`) y su cortacircuito:** sin cambios; comparten `apply_queue` pero
   ninguna fila económica nueva se encola.
6. **Histórico:** las decisiones `economic_pause_v1` ya persistidas no se reinterpretan ni se
   reescriben (append-only); `v1` queda congelada para el replay.
7. **Reactivación automática de hojas pausadas por `v1`:** fuera de alcance; el desenlace de una
   pausa sigue siendo `reversa_pause` manual o por `revalida_pause` según el contrato vigente.
8. **Borrar la clave `ads_pause_economica` de la config de producción:** no se borra; se ignora. Una
   migración de datos sobre `config_version` sería una mutación no append-only.

---

## 8. Despliegue y compuertas cerradas

1. **Despliegue:** `0105_pausa_economica_v2.sql` entra en `off` para ambos mercados (fail-closed por
   ausencia de clave). Se aplica con `tools/despliegue/desplegar.sh` + `tools/despliegue/checklist.sh`
   según `docs/DEPLOY.md`; la reversa queda lista (`0105_reversa_pausa_economica_v2.sql`) antes de
   cualquier encendido (Regla 7).
2. **Activación de práctica:** `productos-modo --platform amazon_mx --pausa-economica shadow` y
   después lo mismo para `amazon_us`.
3. **Compuerta de 5 ciclos en `shadow` (medible al ciclo 5, por mercado):**
   1. Cobertura completa los 5 ciclos: `notes.pausa_economica.evaluadas > 0` (hojas juzgadas con fila
      escrita, sección 3.9) y sin `error` ni `error_tx3`. **Un mercado sin candidatos cumple esta
      compuerta con `pausar = 0`**: lo que se mide es que la práctica juzgó hojas
      (`pause_economica_no_cruza_madura` / `pause_economica_cortes_incompletos` cuentan),
      no que haya pausas. `sin_cortes` se reporta, sin mínimo exigido.
   2. Ningún ciclo muestra `> 5` `pausar` en ese mercado; si alguno lo supera, no se promueve hasta
      investigar.
   3. Replay 100% determinista en `tools/pausa_economica_verifica.py` sobre todas las filas de
      `pausa_economica_evaluacion`.
   4. Cero alteración en decisiones de pujas `live` (ausencia de `decision` económicas en `shadow`).
4. **Compuerta de maduración `+10d`:** `tools/pausa_economica_verifica.py` verifica `+10d` sobre
   cualquier candidato con madurez disponible (hoy MX tiene 0; US `4924` y `5891` llevan semanas
   sobre `3x`) antes de promover `live` por mercado.
5. **Encendido `live`:** mercado por mercado, con el cortacircuito de 5 activo y la línea de Telegram
   verificada en el primer ciclo. Si el cortacircuito se dispara, el ciclo conserva todos los
   recortes de puja y el mercado vuelve a `shadow` con `productos-modo` (que además descarta las
   esperas económicas en la misma TX).
