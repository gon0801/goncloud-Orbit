# M5: contrato del episodio económico del último cambio

Fecha: 2026-11-08 (UTC). Base: `4e0e78d` (M2), sobre `f86ed7dc` (AUTO-03).
Plan que manda: `plans/orbit-07-motor-decisiones.md`, M5.

## Qué se congela

El episodio del último cambio de bid real de la hoja se guarda dentro de
`decision.inputs.caso.trayectoria.episodio` (no hay tabla nueva). El replay
lee el caso congelado y no vuelve a consultar métricas ni configuración
vigente. Las filas históricas no traen la clave y conservan su semántica.

- `pre`: `[x-7, x-1]`, siete fechas completas antes del cambio.
- `post`: `[x+1, x+7]`, siete fechas completas después.
- El día del cambio `x` no entra en ninguna ventana.
- Madurez: `x+7 <= D-10`, es decir, D+17 como pronto. Una ventana posterior
  sin sus diez días de madurez queda `inmaduro`, nunca se rellena con cero.
- `gasto`, `venta`, `venta_mismo_sku`, `pedidos` y `clics` son observaciones
  de `v_metric_latest`/`metrics_as_of`; una métrica desconocida viaja `None`.
- `contribucion_*` = `venta × margen_pct / 100 - gasto` **solo** con un
  margen compatible ya resuelto para el caso (el equilibrio efectivo del
  producto o de la plataforma). Sin margen, la contribución es `None`: no se
  llama utilidad real a una cuenta inventada.

## Estados

| Estado | Significado |
|---|---|
| `maduro` | Ventana posterior completa y con diez días de madurez; métricas de ambas ventanas conocidas. |
| `inmaduro` | `x+7 > D-10`: la ventana posterior todavía no maduró. |
| `incomparable` | La comparación está contaminada: un cambio de puja, presupuesto, placement, precio o alcance dentro de `[x-7, x+7]`, o falta una métrica necesaria. |

`aplicable` es independiente de `estado`. Un cambio de condiciones posterior
a `x` deja el episodio como historia (`aplicable = false`,
`motivo_aplicabilidad = cambio_posterior`), aunque sus ventanas sigan siendo
comparables. M6 solo consume episodios `maduro`, con `motivo = null` y
`aplicable = true`.

## Fuentes de invalidación

Se leen en lote por plataforma, con el corte bitemporal `visto_el` de la
lectura y solo cuando el interruptor está encendido:

- `ads_campana_config_observation`: cada fila es un cambio de configuración
  (la escritura solo inserta cuando la huella cambia) de presupuesto,
  estrategia, placements o fuera de Amazon.
- `campana_ajuste`: ajustes confirmados de cualquier clase.
- `precio_cambio`: cambios de precio aplicados (`aplicado IS TRUE`,
  `estado IN ('enviado','confirmado')`), atados al ad group por sus
  `product_ad` y `listing_id`.
- `ad_entity` (`kind='product_ad'`): un anuncio nuevo con `first_seen_at`
  dentro del rango cambia el alcance del grupo.
- La propia trayectoria (`v_cambio_bid`) aporta los cambios de puja y los
  ajustes de ubicación confirmados. Se excluye el cambio ancla.

Las fuentes históricas que no existen (por ejemplo, la secuencia de estados
`ENABLED/PAUSED` de cada anuncio) no se inventan: el episodio declara la
evidencia que sí observó y M6 no promete estabilidad más allá de ella.

## Evidencia

- [`episodios.json`](episodios.json): sonda reproducible
  [`sonda_episodios.py`](sonda_episodios.py) sobre PostgreSQL 16 desechable.
  Muestra un episodio útil (maduro, aplicable, contribución con margen 8 %) y
  otro no comparable por un cambio de configuración dentro de la ventana, sin
  fabricar un valor para el segundo.
- [`../m6/politica-implementacion.md`](../m6/politica-implementacion.md): la
  regla que consume el episodio.
- Cobertura de producción pendiente: este encargo no tiene acceso
  `orbit_read`. La consulta concreta para medir episodios históricos por
  mercado queda en [`consulta-episodios.sql`](consulta-episodios.sql); su
  resultado no se afirma.
