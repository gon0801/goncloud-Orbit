# AUTO-03 candidato A: politica por producto dentro del ciclo

## Problema

Orbit decide hoy sobre `keyword` y `product_target`; esos controles pertenecen al ad group y
pueden mover trafico de varios productos. El dato de producto, en cambio, suma todas sus
campanas y no permite repartir gasto. Ademas, pausar un `product_ad` no desactiva el listing y
ya existe un unico escritor reversible para hacerlo. El diseno debe agregar un limite economico
por producto sin abrir otra cascada, otro aplicador ni un presupuesto ficticio por producto.

## Uso (vista del llamador)

> **Aclaracion 2026-11-04:** este candidato conserva el boceto historico, no una API
> ejecutable ni la alternativa final adoptada. `cascada_target_acos` vigente acepta cinco
> argumentos; el sexto del ejemplo representa una extension propuesta. El
> [diseno sintetizado](../../../../superpowers/specs/2026-10-24-decisiones-producto-design.md)
> propone extender `cascada_target_acos_con_procedencia` con `target_margen_producto`.
> Su implementacion debe migrar tambien `peldano_target_acos` y los llamadores de negativos
> para conservar una sola precedencia y congelar el mismo valor y procedencia.

El ciclo carga una vez los insumos en TX2 y llama una sola interfaz pura:

```python
entrada = cargar_entrada_productos(conn, platform, decided_at, settings)
resultado = resolver_productos(entrada)
```

Al decidir una hoja, usa la proyeccion ya resuelta para su ad group; no consulta producto por
hoja ni reimplementa precedencias:

```python
restriccion = resultado.por_ad_group.get(ad_group_id)
target = goals.cascada_target_acos(
    goal.target_acos_pct,
    setting_target,
    margen_plataforma,
    goal.scope,
    target_margen_familia,
    restriccion.target_margen_producto if restriccion else None,
)
veredicto = decide(lecturas.caso(..., target_acos_pct=target))
```

Los cortes exactos al anuncio entran al ledger y a la cola existentes; el ciclo no hace HTTP:

```python
for corte in resultado.cortes_product_ad:
    pendientes.append(pendiente_pause_product_ad(corte, modo=modo, decidido_el=decided_at))
```

## Forma

### Tipos y firmas propuestos

```python
@dataclass(frozen=True)
class ClaveProducto:
    platform: str
    product_id: int


@dataclass(frozen=True)
class Dinero:
    valor: Decimal
    moneda: str


@dataclass(frozen=True)
class AdsProductoMaduro:
    ventana: tuple[date, date]  # [D-60, D-30)
    observed_at_max: datetime
    fechas: int
    gasto: Dinero | None
    compras_total30d: Decimal | None
    compras_mismo_sku30d: Decimal | None
    ventas_total30d: Dinero | None
    ventas_mismo_sku30d: Dinero | None
    ad_ids_contribuyentes: frozenset[str]
    maduro: bool


@dataclass(frozen=True)
class EconomiaProductoMadura:
    margen_pre_ads_pct: Decimal | None
    cobertura: Decimal | None
    dias_con_venta: int | None
    ledger_fresco_at: datetime | None
    ventana: tuple[date, date] | None


@dataclass(frozen=True)
class StockProducto:
    estado: Literal["positivo", "cero", "desconocido"]
    observado_at: datetime | None
    cantidades_por_fuente: Mapping[str, int | None]


@dataclass(frozen=True)
class ProductoEnAds:
    clave: ClaveProducto
    listing_ids: tuple[int, ...]
    product_ad_ids: tuple[int, ...]
    ad_group_ids: tuple[int, ...]
    economia: EconomiaProductoMadura
    ads: AdsProductoMaduro
    stock: StockProducto


@dataclass(frozen=True)
class PoliticaProducto:
    estado: Literal["aplica", "abstiene"]
    motivo: str
    target_margen_producto: Decimal | None
    snapshot: Mapping[str, object]


@dataclass(frozen=True)
class RestriccionAdGroup:
    product_id: int | None  # None = compartido/no resoluble
    target_margen_producto: Decimal | None
    motivo: str


@dataclass(frozen=True)
class CorteProductAd:
    ad_entity_id: int
    clave_producto: ClaveProducto
    motivo: Literal["auto_sin_pedidos"]
    ventana: tuple[date, date]
    data_observed_at: datetime
    snapshot: Mapping[str, object]


@dataclass(frozen=True)
class ResultadoProductos:
    por_producto: Mapping[ClaveProducto, PoliticaProducto]
    por_ad_group: Mapping[int, RestriccionAdGroup]
    cortes_product_ad: tuple[CorteProductAd, ...]


def resolver_productos(entrada: EntradaProductos) -> ResultadoProductos: ...
```

`EntradaProductos` lleva `fraccion_margen`, `gasto_para_concluir`, moneda, productos y el mapa
real `ad_group -> product_ads -> listing -> product`. Se valida moneda e identidad en el borde;
la funcion pura recibe tipos de dominio, no filas SQL ni JSON de Amazon. La superficie publica es
una funcion; oculta colapso bitemporal, madurez, halo, campanas compartidas y seleccion de cortes.

### Regla economica exacta propuesta

Verificacion SQL en `origin/master`:

- `migrations/0018_fabrica_campanas.sql:323-325,353-355` excluye
  `fee_type='ads'` de cargos; `:408-412` calcula
  `100 * (venta_cubierta + cargos_no_ads - cogs) / venta_cubierta`.
- `migrations/0021_economia_observada.sql:140-162` proyecta ese numero sin redefinirlo.
  Por tanto `margen_neto_pct` es **antes de Ads**. El gasto Ads no se vuelve a restar.

Para un producto con margen maduro positivo `M` y la fraccion vigente `f` de
`ads_target_fraccion_margen_<platform>`:

```text
L_crudo = f * M
L_producto = min(L_crudo, MARGEN_BANDA_MAX=45)
```

No se aplica piso de 10: subir `L_crudo=4` a 10 permitiria gastar mas que la politica economica
midio. No aparece una fraccion nueva ni una meta de "conservar la mitad" nueva: se reutiliza
exactamente la fraccion configurada. El numero es un limite de ACoS, no una afirmacion de
utilidad exacta por venta atribuida.

Se habilita `L_producto` solo si: margen no nulo y positivo, cobertura `>=0.95`, `>=30` dias con
venta, ledger fresco por la regla existente de 3 dias; Ads completo y maduro; halo 30d cero
observado; y stock positivo observado no antes del fin de ventana. Ausencia produce abstencion.

Precedencia unica, implementada solo en `goals._nucleo_target_acos`:

```text
goal resuelto (campana pisa plataforma)
  -> margen_producto -> margen_familia -> margen_plataforma -> setting
```

Un goal manual explicito sigue ganando, aunque sea mayor al limite del producto; se muestra como
intencion manual que no acredita rentabilidad. AUTO-03 no crea goals manuales por producto.

### Ads, halo, cero pedidos y madurez

- Ventana Ads: `[D-60, D-30)` UTC. De cada `metric_date` se toma la observacion con mayor
  `observed_at`; cada fecha usada exige `observed_at >= metric_date + 30 dias`. No se exige fila
  para dias sin actividad: ausencia de fila sigue siendo ausencia, no cero.
- Halo: `ventas_total30d - ventas_mismo_sku30d` solo si ambas existen. Si es positivo o alguna
  columna falta, el producto no gobierna bids: no se conoce el margen de los otros SKU. No se
  aplica el margen propio al halo.
- Cero pedidos exige: `gasto >= gasto_para_concluir` vigente, `compras_total30d == 0`,
  `ventas_total30d == 0`, moneda completa, stock positivo y `ad_ids_contribuyentes` resolubles.
  Pausa cada `product_ad` ENABLED contribuyente, no campana, grupo, listing ni anuncios ajenos.
- `compras_mismo_sku30d == 0` con compras totales positivas es halo-only: abstencion, no corte.
- Stock cero, desconocido o anterior al fin de ventana produce `abstiene_stock_*`; no se llama
  mal rendimiento y no se pausa "por inventario". Featured Offer sigue sin verificar.

### Campanas compartidas

Un ad group es aislado solo si todos sus `product_ad` vivos y mapeados apuntan al mismo
`product_id`. Solo entonces recibe `margen_producto`. Con dos productos, uno sin mapping o un
anuncio sin listing, `target_margen_producto=None`: no se toma minimo, no se promedia y no se
reparte gasto. El motor vigente cae por su cascada actual. El corte por cero pedidos si puede
pausar el `product_ad` contribuyente dentro de una bolsa compartida porque ese control si es
individual; los demas anuncios quedan intactos. Presupuesto sigue siendo de campana; no se
declara cap duro ni presupuesto por producto.

### Modulos y propiedad

- **Nuevo `app/optimizer/productos.py`**: tipos y `resolver_productos`; puro, sin psycopg/HTTP.
- **Extender `app/cycle.py`**: SELECT por lote en TX2, resolver una vez, congelar
  `decision.inputs.producto` y escribir decisiones solo en TX3.
- **Extender `app/optimizer/goals.py`**: un argumento opcional y un peldaño en la cascada unica;
  nada de una cascada paralela.
- **Extender `app/apply_cola.py`**: `pause` sobre entidad `product_ad` usa ruta
  `retiro_product_ad`, revalida la regla completa al vencer 48 h y descarta si cambio la evidencia.
- **Extender `app/retiro_anuncios.py`**: unico dueño del PUT/readback de `product_ad`; recibe la
  decision liberada, escribe intencion antes de HTTP y sella despues. No hay otro llamador directo.
- **`app/apply.py`** conserva en exclusiva los writes de bids. AUTO-03 no escribe budgets.

### Modelo de datos propuesto

No se crea `politica_producto_actual`: seria estado derivado duplicado. Para conservar historial
se agrega `evaluacion_producto_ciclo` append-only con PK
`(cycle_id, platform, product_id)`, `estado`, `motivo`, `target_margen_producto`, `moneda`,
`window_start/end`, `data_observed_at` y `snapshot JSONB`; no se consulta para decidir. La escribe
TX3 junto con las decisiones y sirve a auditoria/UI. Los targets usados se copian tambien a
`decision.inputs.producto`, que es el freeze autoritativo de la accion.

Se reutilizan `decision` (`kind='pause'`, `ad_entity_id` de `product_ad`) y `apply_queue` (familia
`entity_cut`, veto 48 h). Una migracion agrega `decision_id BIGINT NULL UNIQUE` a
`anuncio_retiro_intencion`, amplia ambos CHECK de motivo con `auto_sin_pedidos`, y exige
`decision_id IS NOT NULL` para ese motivo. Los retiros viejos quedan validos. `app_decide` recibe
solo los INSERT/UPDATE de cierre necesarios; no UPDATE/DELETE amplio. La reversa de migracion
aborta si hay intenciones abiertas o filas nuevas sin exportar.

### Flujo y reversa

```text
TX2 snapshot -> resolver -> evaluacion + decision (TX3) -> apply_queue[pending_veto]
    -> vetoed
    -> released -> revalidar -> discarded si cambio
                             -> intencion retiro -> PUT PAUSED -> readback
                             -> retiro sellado -> applied
```

La reversa debe existir antes del primer live. La firma vigente es
`retiro_anuncios.repone(conn, retiro_ids=(retiro_id,), go=go)`; exige el literal del dueno,
consume retiros sellados y es idempotente por `anuncio_reposicion`. AUTO-03 debe limitar el
llamador a retiros propios confirmados, sin adoptar una pausa ajena como propia, y conservar
la exclusion bajo la llave `retiro-ad:<id>` de la entidad.

> **Aclaracion 2026-11-04:** hoy `_repone_uno` recrea un anuncio `ARCHIVED`. Evitarlo en
> AUTO-03 es una guarda propuesta que debe quedar implementada antes de habilitar este
> llamador: bajo la misma llave, comprobar el estado y devolver `requiere_recreacion` antes
> de llamar al camino que recrea. Ese resultado no existe hoy en `repone`. Crear otro anuncio
> requiere una autorizacion distinta; autorizar una reposicion no autoriza esa creacion.

No hay auto-retorno temporal: un anuncio pausado deja de producir señal y David no ha aprobado
un plazo de reactivacion. Reponer es manual o una futura politica explicita; el veto y la reversa
nunca desactivan el listing.

## Synthesis decision

Elijo evaluacion integrada al ciclo. Mantiene un snapshot REPEATABLE READ, congela la evidencia
en la decision que actua y reutiliza lock, veto, quota, readback y ledger. La evaluacion
append-only preserva el historial, pero nunca se convierte en entrada obligatoria del siguiente
ciclo.

## Tradeoffs aceptados

- Aceptamos cobertura automatica pequena a cambio de no tratar ausencia como cero ni atribuir
  margen propio al halo.
- Aceptamos que bolsas compartidas no tengan target por producto a cambio de no perjudicar un
  producto con datos de otro.
- Aceptamos reversa manual a cambio de no inventar un reloj de reactivacion sin nueva evidencia.
- Aceptamos mas trabajo en el ciclo a cambio de un solo snapshot y cero sincronizacion previa.

## Alternativas consideradas

**Controlador previo materializado.** Flujo: `job -> snapshot append-only -> ciclo lector`. El job
llamaria el mismo resolver y escribiria
`producto_politica_snapshot(platform, product_id, calculated_at, valid_until, target, accion,
evidence)` y el ciclo consumiria la ultima fila. Facilita consultas historicas, pero introduce un
segundo reloj, vigencia/TTL, carrera entre snapshot y decision, un writer adicional y dos lugares
que parecen ser la politica. Su interfaz es mayor: el ciclo debe entender freshness, reemplazo y
conflictos. Pierde frente al integrado, cuyo resultado nace del mismo snapshot que la decision.

**Minimo entre productos compartidos.** Oculta la mezcla en un numero sencillo, pero una hoja no
tiene atribucion por producto y el minimo puede recortar a productos sanos. Se rechaza.

## Preguntas abiertas y riesgos

- ¿David confirma que un goal manual explicito debe seguir ganando al limite economico del
  producto, con advertencia visible, o quiere convertir el limite en guardrail absoluto?
- ¿Se autoriza `auto_sin_pedidos` live tras shadow y veto, o la primera fase debe ser proposal-only?
- ¿La fuente de stock se conciliara contra una semantica fulfillable antes de permitir una accion
  operativa por inventario? Este diseno no la presupone.
- La cobertura real de margen maduro individual es pequena; no debe inferirse cobertura total de
  un reporte `COMPLETED` ni fijarse un umbral de salida de shadow sin decision del dueño.

## Siguiente paso de implementacion

Construir primero los tipos puros y una prueba monetaria de `resolver_productos` que demuestre que
margen 8% con fraccion 0.5 produce 4%, nunca el piso 10%.
