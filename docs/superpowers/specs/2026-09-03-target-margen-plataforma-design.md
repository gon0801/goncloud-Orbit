# ORBIT 06 Fase 2 — target de ACoS derivado del margen neto medido por plataforma

**Estado: APROBADO por el dueño (2026-09-03).** Literales del dueño en §1.
Precedencia: `docs/CONTEXTO.md` (reglas 1-10) > este spec > `plans/orbit-06.md`.

## 1. Decisiones literales del dueño (2026-09-03)

- Arranque: «ok hay que arrancar el diseño de orbit 06 fase 2».
- Fracción del margen para publicidad, pregunta guiada con cuatro opciones
  (mitad / tres cuartos / todo / distinto por país): **«La mitad (utilidad)»**.
- Diseño completo (camino A, §3-§9): **«ok va asi»**.
- Pregunta del dueño que fija un requisito: «ese margen se va a ir ajustando
  automaticamente no? no es estatico, si cambian [COGS] si suben precios etc»
  → §6 (frescura) y §7 (lag declarado) son obligatorios, no opcionales.

## 2. Estado medido que origina el diseño (regla 8, lead, 2026-09-03, base viva)

Ledger de 90 días maduros (`event_date` en `[D-105, D-15)`), todo en MXN
(el ledger de US también reporta en MXN; el costo está en MXN: la razón es
adimensional, **sin FX**). Cobertura de costo: MX 260/263 ventas, US 154/154.

| | amazon_mx | amazon_us |
|---|---|---|
| Venta | 318,066 | 394,406 |
| Cargos de Amazon SIN publicidad (referral, envío, FBA, IVA/ISR retenido, refunds, otros) | −33.6 % | −48.9 % (envío solo: −21.0 %: guía cross-border ~454 MXN + cargo Amazon ~94 MXN por pedido; refunds 12/154 pedidos) |
| COGS | 26.2 % | 15.2 % |
| **Margen neto antes de publicidad** | **40.2 %** | **35.9 %** |
| Publicidad (`fee_type = ads`) sobre venta total | 10.4 % | 22.3 % |
| Target vigente del motor | 20 (setting, manual) | 20 (setting, manual) |

Hechos que cambian el planteamiento original de la 2.1:

1. **La contribución por campaña es uniforme** (`v_contribucion_entidad`:
   ≈ 0.68-0.70 MX, ≈ 0.78-0.81 US en TODAS las campañas con ingreso): un
   target por campaña no discrimina nada hoy. La palanca real es por
   plataforma.
2. **El 20 manual ≈ la mitad del margen** (20/40 MX, 20/36 US): la decisión
   del dueño («la mitad») deja los números casi donde están (≈ 20.1 MX /
   ≈ 17.9 US). El valor del cambio no es mover pujas hoy: es que el target
   quede **medido, con procedencia y auto-ajustable**.
3. `v_contribucion_entidad` sigue **no decisoria** (D1.bis de
   `docs/MARGEN-ENTIDAD.md`, intacto). La fuente del target es el **ledger
   por plataforma**: dinero real (venta, cargos, costo) sin supuestos de
   atribución ni `listing_price`, y sin el par con/sin halo que en US
   invierte el signo (−1,318 vs +5,101 USD).

## 3. D1 · Fuente y fórmula — vista `v_target_margen_plataforma`

Una fila por `platform` (solo `amazon_mx`, `amazon_us`; sellado 9: MeLi
fuera). Ventana fija de **90 días maduros**: `event_date >= hoy − 105` y
`event_date < hoy − 15` (madurez D-15 del ledger, hallazgo 0.6: el costo
madura al día 15). `hoy` = `CURRENT_DATE` UTC en el momento del ciclo.

```
venta            = Σ amount  WHERE kind = 'sale'
cargos_sin_ads   = Σ amount  WHERE kind IN ('fee','refund','withholding')
                             AND coalesce(fee_type,'') <> 'ads'        -- con o sin order_id
cogs             = Σ cost_amount × quantity  (costo vigente a la fecha de la venta,
                   MISMA moneda que la venta; línea con costo en otra moneda o sin
                   costo → cuenta como SIN costo, jamás se convierte ni se rellena)
ventas_con_costo / ventas_totales = cobertura
margen_neto_pct  = 100 × (venta + cargos_sin_ads − cogs) / venta     -- cargos son negativos
dias_con_venta   = count(DISTINCT event_date) WHERE kind = 'sale'
ledger_fresco_at = max(observed_at) del ledger de la plataforma
```

Columnas: `platform, ventana_desde, ventana_hasta, venta, cargos_sin_ads,
cogs, cobertura, dias_con_venta, margen_neto_pct, ledger_fresco_at,
moneda` (`moneda` = la única `amount_currency` de la ventana; si hay mezcla
→ **NULL fail-loud**, mismo patrón que `v_entidad_inerte.moneda`).
Cualquier condición de §5 deja `margen_neto_pct` **NULL** (regla 3: hueco,
jamás cero). La vista NO conoce la fracción ni el target: solo mide.

`v_margen_plataforma` (0001) **no se reusa**: no tiene ventana, no separa la
publicidad de los demás cargos y su `margen_contribucion` exige cobertura
100 %. Se deja intacta (superficie de lectura).

## 4. D2 · Fracción y target derivado

`target_derivado_pct = fraccion × margen_neto_pct`, con `fraccion` en
`config_version.settings` bajo la clave
**`ads_target_fraccion_margen_<platform>`** (string decimal, como los demás
settings; hoy `"0.5"` para ambas plataformas por decisión literal del dueño).
Se cambia como los caps: `config_version` nueva, append-only, `app_admin`.
Clave ausente o no numérica en (0, 1] → el peldaño **no aplica** (§5), no
revienta el ciclo.

## 5. D3 · Peldaño nuevo en la cascada de targets

Cascada sellada de `app/optimizer/goals.py`, hoy: `goal_campana →
goal_plataforma → setting_plataforma → cache_estado → default`. Pasa a
**SEIS** peldaños con nombre EXACTO nuevo `margen_plataforma`:

```
goal_campana → goal_plataforma → margen_plataforma → setting_plataforma → cache_estado → default
```

- `goal_campana` **sigue pisando** siempre que exista (override manual por
  campaña: lanzamientos, defensa). `goal_plataforma` con target explícito
  también pisa. El peldaño nuevo solo gana cuando los dos anteriores son
  None: es la fuente **medida** que reemplaza al 20 manual, y el setting
  queda como red de seguridad.
- Las DOS variantes (`cascada_target_acos` del motor y
  `cascada_target_acos_con_procedencia` del dashboard) ganan el peldaño; el
  test de equivalencia existente se extiende (regla 2: un número, una
  fuente). El nombre `margen_plataforma` entra al vocabulario cerrado de
  procedencias del dashboard y a `MOTIVOS_ES_SALUD`/etiquetas.
- El peldaño **se abstiene** (devuelve None y la cascada sigue al setting)
  ante CUALQUIERA de: `margen_neto_pct` NULL; `cobertura < 0.95`;
  `dias_con_venta < 60`; `venta <= 0`; fracción ausente/inválida;
  `ledger_fresco_at < hoy − 3 días` (ledger rancio, §6);
  `target_derivado_pct` fuera de la **banda dura [10, 45]**. Cada abstención
  deja su motivo en `notes.target.motivo_abstencion` del ciclo (vocabulario
  cerrado: `sin_margen`, `cobertura_baja`, `ventana_corta`, `sin_fraccion`,
  `ledger_rancio`, `fuera_de_banda`) y una línea en el digest. **Nunca
  cae a cero**; cae al setting de hoy.
- **Paso máximo**: `target_aplicado = clamp(target_derivado, ultimo − 0.5,
  ultimo + 0.5)` puntos por ciclo (≈ 3.5 por semana natural; el promedio de
  90 días se mueve mucho menos). `ultimo` = `notes.target.aplicado` del
  último ciclo **live** `done` de la misma plataforma cuyo peldaño fue
  `margen_plataforma`; si no existe (primer ciclo), `ultimo` = el target que
  la cascada habría dado sin el peldaño (el setting), así la entrada es
  gradual desde 20 y no un salto. Sin tabla nueva: la memoria es el propio
  `optimizer_cycle.notes`.

## 6. D4 · Frescura obligatoria (requisito literal del dueño)

Medido 2026-09-03: `ledger_event` última ingesta **2026-08-31** (una sola
corrida por plataforma), `fx_rate` última **2026-08-28**; los costos sí se
refrescan a diario (`refresh_costos.sh`, 07:30 UTC). Sin cron, el margen se
congela y el target «auto-ajustable» sería mentira. Tarea de infra ANTES
del código del motor: `ingest ledger` e `ingest fx` leen el MISMO snapshot
SQLite de accounting que ya usa `refresh_costos.sh` a diario (07:30 UTC);
se extiende ese script para correr los tres pipelines con el mismo snapshot
(flags exactos según `docs/DEPLOY.md` y `app/cli.py`, regla 8), ANTES del
ciclo de 08:40 y DESPUÉS del sync de accounting (`sync_ads_to_ledger.py`,
cada 6 h); un pipeline caído no tumba a los otros.
La guarda `ledger_rancio` (§5) es el candado de código que detecta si el
cron muere.

## 7. Lag declarado (para el dueño; va al digest y a `/salud`)

Una venta entra al cálculo 15 días después de ocurrir; la ventana es de 90
días, así que un cambio de costo/precio/envío se refleja a la mitad en ~6
semanas y por completo en ~3 meses; el paso máximo suaviza además el
target. No es un defecto: es el reloj honesto de un promedio maduro.

## 8. D5 · Freeze y replay

El ciclo ya congela `target_acos_pct_usado` en `decision.inputs`; se añade
`target_procedencia` (nombre del peldaño) y, cuando el peldaño es
`margen_plataforma`, el snapshot `{margen_neto_pct, fraccion, cobertura,
ventana_desde, ventana_hasta, target_derivado, target_aplicado}`.
`reproduce()` sigue leyendo SOLO `target_acos_pct_usado` → las decisiones
históricas replayean idénticas **por construcción** (golden: la suite de
replay no cambia). `notes.target` del ciclo lleva el mismo snapshot por
plataforma (fuente del paso máximo y de `/salud`).

## 9. D6 · Superficie

- `/salud`: por plataforma, target vigente + procedencia + margen medido +
  fracción + ventana + edad del ledger; etiqueta española del peldaño.
- Digest (Telegram): línea cuando el target aplicado cambia ≥ 1 punto
  respecto al ciclo anterior o cuando el peldaño se abstiene (motivo).
- `docs/CONTEXTO.md`: la cascada pasa a seis peldaños con el literal del
  dueño (DoD de la 2.1).

## 10. D7 · Entrada (reemplaza el DoD original de la 2.2)

El criterio «solo entra si AUMENTA la tasa de acción útil» no aplica: con
fracción 0.5 el target derivado ≈ el manual y la diferencia de decisiones es
casi nula por diseño. Criterio nuevo, sellado con el dueño:

1. Replay golden intacto (§8).
2. **Un ciclo sombra comparado**: sobre las mismas entradas del ciclo del
   día, la tabla de decisiones con el target manual vs el derivado (cuántas
   cambian de banda, cuáles, y los dos targets), con el `SELECT` en la
   evidencia. Un solo ciclo basta (enmienda de cadencia del dueño,
   2026-09-03: «un test es suficiente»).
3. **Go literal del dueño** con los números en la mano → `config_version`
   nueva con `ads_target_fraccion_margen_*` = "0.5" (sin la clave, el
   peldaño no existe: ese es el interruptor).

## 11. Reject (con razón)

- **Target por campaña desde `v_contribucion_entidad`**: no discrimina
  (margen uniforme), señal sellada no decisoria, y el par con/sin halo se
  contradice en US. Queda como peldaño futuro (`margen_campana`, entre
  `goal_plataforma` y `margen_plataforma`) si el catálogo se diversifica.
- **Reusar `v_margen_plataforma` tal cual**: sin ventana ni separación de
  la publicidad (contaría el gasto de ads dos veces contra el ACoS).
- **Convertir moneda dentro de la razón**: innecesario (MXN/MXN) y abre la
  trampa del par invertido de `fx_resolve` (nota de sello de la 1.2).
- **Rellenar costo faltante / cobertura parcial con promedio**: trampa
  pagada (`sales_history.cogs`); cobertura < 95 % = abstenerse.
- **Tabla nueva de estado del target**: `optimizer_cycle.notes` ya es la
  memoria auditable; una tabla más es una fuente más (regla 2).
- **Mover el target sin tope**: un mes con devoluciones altas en US
  bajaría el target 5-8 puntos de golpe y el motor recortaría pujas en
  masa por ruido.

## 12. Reparto y proceso

- **Infra (2.2)**: cron diario de ledger + fx (Grok o lead; es ops).
- **GLM (2.3)**: migración 0015 (vista), peldaño en ambas cascadas, guardas,
  paso máximo, freeze, `notes.target`, `/salud`, digest, tests rojos
  (regla 9) y el SELECT de comparación sombra. Prohibido tocar producción;
  la corrida real es del lead.
- **Lead (2.4)**: review contra la base viva + CodeRabbit (1 ronda), deploy,
  ciclo sombra comparado, go del dueño, `config_version` con la fracción,
  verificación del primer ciclo con procedencia `margen_plataforma`,
  CONTEXTO sellado, AppFlowy.
