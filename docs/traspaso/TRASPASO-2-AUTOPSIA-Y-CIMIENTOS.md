# Traspaso 2 — Autopsia y cimientos para el sistema nuevo

> **Proyecto:** goncloud-MCP-2 (motores de Amazon Ads) — **CANCELADO el 2026-08-21**
> tras casi un año de reparaciones.
>
> Este documento es lo que vale la pena rescatar. La parte 3 (**trampas del dominio**) es
> la más importante: son errores que **no vienen de este código** sino de cómo Amazon y
> MercadoLibre entregan los datos. Un sistema nuevo, escrito perfecto, se estrella contra
> todas ellas igual.
>
> Todo está medido contra producción. Donde algo no se pudo verificar, lo dice.

---

## 1. El diagnóstico: no era suciedad, era cableado

El sistema no falló porque los datos estuvieran sucios. Falló porque **los tubos estaban
conectados al lugar equivocado**: cada componente hacía bien su trabajo con el insumo
equivocado.

| el campo decía | lo que traía adentro |
|---|---|
| `break_even` | una **copia literal** de la meta de ACoS escrita por el dueño (`campaign_bea_pct = target_acos * 100.0`) |
| `same_sku_revenue_source: "same_sku"` | el total **con halo**, en **2,301 de 2,301** decisiones |
| `direct_revenue` (la protección anti-halo) | **0 en el 100%** de las filas Amazon → caía siempre a un fallback |
| el margen contra el que se comparaba el ACoS | calculado sobre ingreso **bruto** (con IVA y envío) mientras Amazon reporta ACoS sobre **ItemPrice** solo → **15 puntos** de error en US |
| el gasto de ads | congelado en una foto **D+7** que se sobrescribía |

Y el que resume todo: **el motor usó diez márgenes distintos en un solo día** (de 41.9% a
55.7%) desde **tres fuentes** diferentes. "El margen del motor" no existía como una cosa.

**Consecuencia medida:** el 94% de las decisiones de puja se tomaban con menos evidencia
de la que el propio motor calculaba que necesitaba (cobertura mediana: 0.03). Y el gate de
corte de términos (`NEGATIVE_EVIDENTIAL`) estuvo `live` sin disparar **nunca**: 0 de
220,494 decisiones.

### Por qué no se pudo arreglar reparando

Cuatro revisiones independientes en un día encontraron defectos, y **tres encontraron
defectos introducidos por el arreglo anterior**. Reparar tubería mal conectada un tubo a la
vez no converge. La única salida es re-plomear desde la decisión hacia atrás, y eso es
construir, no reparar.

---

## 2. Los tres patrones que produjeron todo

Estos importan más que los bugs. Si el sistema nuevo los repite, termina igual.

### Patrón A — "se arregló una copia y no la otra" (7 instancias medidas)

`sync_amazon.py` y `sync_amazon_finance_v2024.py` eran **dos escritores de la misma tabla
con políticas distintas**, y cada arreglo aterrizaba en uno solo. Además había **tres
copias físicas** de `fx_helper.py` en disco (2,725 / 2,725 / 11,271 bytes) con un docstring
que juraba que eran idénticas.

**Contramedida:** ninguna regla de negocio puede vivir en dos lugares. Si hay dos
escritores de una tabla, comparten la capa de resolución o cada regla lleva un test
parametrizado que corre contra **ambos**. Y ningún docstring promete un invariante que
nadie verifica.

### Patrón B — la suite prueba formas de dato que no existen

Con 94 pruebas en verde, **dos mutaciones que borraban ramas vivas del código
sobrevivieron**. Un test verificaba `order_id = NULL` cuando producción escribe **cadena
vacía** (12,297 filas con valor, **569 vacías, 0 NULL**). El guardarraíl del único bug que
vivió tres meses era fantasma.

Tres causas repetidas: se prueba la forma **imaginada** en vez de la real, se clava un
literal en vez de parametrizar, y se afirma sobre el **texto fuente** en vez del
comportamiento.

**Contramedida:** antes de escribir el test de un invariante, correr el `SELECT` que
confirma **la forma real de esa columna en producción**. Y toda prueba de regresión debe
demostrarse fallando contra el código anterior — si pasa antes y después, no prueba nada.

### Patrón C — cuando falta un dato, se inventa una constante y se sigue en silencio

Cinco instancias medidas. El tipo de cambio caía a `20.5` sin avisar (**77 ventas**
convertidas a 20.5/20.0 cuando ninguna tasa real del periodo pasó de **18.6495** →
**+28,549 MXN** de revenue inflado). El motor de salud de SKU usaba precios de **$50 USD /
$800 MXN** hardcodeados contra reales de ~$155 y ~$1,371.

El repo **ya tenía la regla correcta escrita** (`ADS-BUG-059`: *"fail-loud: KPI no medible
= None, NUNCA 0"*) pero aplicada sólo a los KPIs del dashboard, nunca a la economía que
decide el gasto.

**Contramedida:** dato faltante = `None` y la fila no se escribe. Un hueco visible es
infinitamente mejor que un número inventado que se ve igual de real que los demás.

---

## 3. Las trampas del dominio — esto es lo que hay que leer

**No dependen del código.** Cualquier sistema nuevo las hereda intactas.

### 3.1 Maduración: hay tres relojes distintos y desalineados

**Venta atribuida** (cohorte fija, 1,098 llaves mx / 147 us, junio–julio 2026; 100% = valor
a 21 días):

| | día 1 | día 2 | día 4 | día 7 | día 8 |
|---|---|---|---|---|---|
| amazon_mx | 70.7% | 87.9% | 94.7% | 96.8% | **100%** |
| amazon_us | 76.9% | 89.4% | 97.5% | — | (cierra día 5) |

**Costo:** se estabiliza exactamente en el **día 15** en ambas plataformas.

**El día en curso es basura:** muestra **19.9%** del costo y **11.5%** del revenue finales
en amazon_mx (30.4% / 18.6% en us).

**El costo madura hacia ABAJO** — Amazon reporta de más y hace *clawback* de clicks
inválidos: día 1 = 108.5% → día 15 = 100% en mx. La corrección llega en dos escalones
(días 1–3 y días 13–15).

> **El efecto combinado es el peligroso:** con datos frescos el numerador está inflado ~7%
> y el denominador desinflado ~30% **al mismo tiempo**. Un ACoS de día 1 en amazon_mx sale
> **~1.5× peor que el real**, y toda regla de corte dispara de más.

**Tercer reloj — el P&L:** los fees de una venta sólo están **42–65% registrados al día 1**
y necesitan 15–30 días en Amazon. MeLi cierra prácticamente en el día. El margen que un
motor de ads usa sale de este tercer reloj, desalineado con los otros dos.

**Regla:** cada métrica lleva su edad. Ninguna decisión económica lee datos de menos de 15
días sin un factor de maduración explícito. El día en curso se descarta, no se pondera.

### 3.2 Cuánto cuesta decidir temprano (curva medida)

Regla simulada *"gastó y cero conversiones → cortar"*, evaluada a distintas edades del dato
contra el valor final:

| edad del dato | falsos cortes amazon_mx | revenue real que se mataba |
|---|---|---|
| día 0 | **8.7%** (11/127) | 11,847.78 MXN |
| día 1 | 2.40% (14/583) | 16,038.66 MXN |
| día 3 | 1.21% | 8,750.24 MXN |
| día 5 | 0.53% | 3,850.00 MXN |
| día 7 | 0.18% | 1,283.62 MXN |
| **día 10** | **0.00%** | **0** |

**Regla:** la edad mínima del dato depende del **tipo** de decisión, no es una sola. Cortar
o pausar exige **≥10 días** —es irreversible en la práctica, porque una entidad pausada ya
no genera la señal que la revertiría—. Subir puja tolera menos. Y **toda acción de corte
necesita su camino de reactivación implementado ANTES de encenderse.**

### 3.3 Halo: 56–58% del ingreso es de otros SKUs, y varía muchísimo

| ventana | amazon_us | amazon_mx |
|---|---|---|
| 30d | 55.1% | 57.6% |
| 90d | 58.5% | 57.4% |
| 180d | 58.6% | 66.7% |
| meli | **0.0%** (no reporta halo) | |

> **Corrección:** el **61.3%** que circuló durante la auditoría **no se reproduce** en
> ninguna ventana estándar. El número estable es **56–58.5%**.

El promedio esconde una varianza enorme: por mes amazon_mx va de **41.2%** a **72.4%**. A
nivel campaña el rango es 0%–100%, con campañas que venden **más** de otros SKUs que del
anunciado.

**Regla:** traer el halo por entidad desde el reporte de *purchased-product*, nunca asumir
un ratio. Y declarar explícitamente que **el halo es atribución de Amazon, no causalidad**:
sin holdout no prueba incrementalidad.

### 3.4 `sales_same_sku` parece lo correcto y mata campañas

Cubre sólo **28.1%–37.8%** del ad_revenue. Usarlo de numerador convierte una campaña
rentable en una que "no vende".

**Regla:** `sales_same_sku` sirve para **atribuir** (qué SKU se llevó el crédito), **nunca
para decidir rentabilidad**. El numerador de cualquier ROAS/POAS es `ad_revenue` completo,
y el desglose se guarda aparte con su propia bandera de cobertura.

### 3.5 Tablas point-in-time: inflan 13–17× **e invierten el signo**

`keyword_metrics_pit` guarda una fila **por día de observación** del mismo metric-day
(16.8 observaciones promedio en mx, 12.35 en us). Sumarla en crudo infla **13.2×–17.0×**.

Y lo peor —la inversión de tendencia— porque el número de observaciones crece linealmente
con la antigüedad, así que **el pasado siempre pesa más**:

```
Costo amazon_us, 7 días recientes vs 7 previos:
  CRUDO:  3,057.21 vs 6,531.13  →  −53%   "el gasto se derrumba"
  DEDUP:    585.48 vs   519.56  →  +12.7% "el gasto sube"
```

**Regla:** ninguna tabla point-in-time se consulta sin filtro de *vintage* explícito.

### 3.6 Monedas: el error de 18.66× y las 48 tablas sin defensa

`sales_history.currency = 'MXN'` para las **tres** plataformas, incluido amazon_us. Pero
`ad_daily_metrics.currency = 'USD'` para amazon_us. Un POAS que divida una contra otra se
equivoca **18.66×** y siempre en la dirección *"todo es rentabilísimo"*.

Peor: hay **48 tablas** con columnas de dinero y de plataforma **y sin columna de moneda**.
Las 12 revisadas mezclan las dos monedas en la misma columna — incluidas las tablas de
outcomes que los motores leen para aprender de sus propias decisiones. Un `SUM()` sobre
ellas no falla: **devuelve un número.**

Y la conversión es **irreversible**: de 477 ventas de amazon_us en el ledger, **0 guardan
el monto original en USD**. Si la tasa fue mala, no hay forma de recuperar el valor real.

**Regla:** prohibir columnas de dinero sin moneda. Cada monto se guarda como
`(valor, moneda, fecha_fx)`, y guardar **siempre** el importe original además del
convertido. Un `SUM()` sobre montos de plataformas distintas debe ser **imposible por
schema**, no por disciplina. El CI debe fallar ante una tabla nueva con plataforma + dinero
y sin moneda.

### 3.7 Retenciones fiscales

- **IVA MX:** Amazon retiene la mitad del 16% = **6.897% del bruto**. Verificado mes a mes
  (6.64%–7.00%) y contra el `ItemTax` de la API: 49.6% de lo cobrado, exactamente la mitad.
- **US:** no hay IVA. Es *sales tax* de marketplace facilitator, **tasa variable por
  estado** (~6.45% del bruto medido) — no se puede fijar en una constante.
- **ISR:** 1.68%–2.02% del bruto.

> **La trampa, confirmada al 100%:** el ISR de Amazon **nunca trae `order_id`** (13/13 filas
> en MX, 7/7 en US) y llega en **bultos quincenales**, no por venta. Por eso se cae de
> cualquier cálculo por-orden y no se contaba en el margen. En MeLi es al revés: 950/950
> filas **sí** traen `order_id`. **Asimetría total entre plataformas.**

**Regla:** los costos que llegan sin `order_id` (ISR, gasto de ads) existen y hay que
prorratearlos explícitamente, o declarar por escrito que se excluyen y por qué.

### 3.8 Lookahead: el histórico se sobrescribe y **no produce ningún síntoma**

`keyword_daily_metrics` hace UPSERT in-place. Cada vez que el sync repide un `metric_date`,
la fila vieja se pisa con la atribución **madura**. La fila de hace tres meses contiene hoy
el número que el motor **jamás pudo ver** el día que decidió.

Comprobado: 2,297 de 2,297 filas de `keyword_daily_metrics` coinciden **exactamente** con
la última observación de `keyword_metrics_pit`. Magnitud del engaño: el motor veía
**68.9%** del revenue que la tabla muestra hoy.

> Esta es la trampa **más grave** para un sistema nuevo, porque el backtest sale
> espectacular y nadie sospecha.

**Regla:** la clave de una métrica es `(entidad, metric_date, observed_at)`, append-only.
Sin eso, **ningún backtest es válido** — y el experimento que iba a medir si los motores
servían nunca tuvo un baseline limpio por exactamente esto.

### 3.9 MeLi es estructuralmente incomparable con Amazon

Cero halo, cero desglose fiscal, lag de atribución de 1 día en vez de 8–15, y el ISR con
`order_id`. **No se pueden aplicar las mismas reglas ni los mismos umbrales.** Además, la
escritura de MeLi Ads está bloqueada a nivel cuenta: es *proposal-only*.

---

## 4. Qué datos son confiables — con su verificación

> Esta sección pasó por refutadores adversariales cuyo trabajo era tumbarla. **Cuatro de
> cinco "problemas" reportados por la auditoría inicial resultaron falsos** — cometían los
> mismos errores de procedencia que este documento denuncia (mezclar USD con MXN, población
> equivocada, cobertura sesgada). Lo que queda abajo es lo que sobrevivió.

### ✅ Confiable y migrable

| dato | verificación |
|---|---|
| **Las 4 tablas de métricas de ads** | 0 duplicados y **cero huecos de días**. El activo más limpio del proyecto. |
| **`currency_rates`** (accounting.db) | 204 filas, cadencia diaria continua desde 2025-10-31, sin tasas nulas/cero/negativas. Huecos máximos de 5 días. |
| **`product_economics_expected.listing_price`** | 749/749 con precio; 514/514 coinciden con la fuente. |
| **`own_items.current_qty`** — Amazon | **441/441** aguantan (no 218 como se había dicho). |
| **`ledger_events`** | 12,875 filas, moneda verificada `MXN` en el 100%. El desglose fiscal existe en `raw_payload` para el esquema Orders API. |
| **Los tokens y credenciales** | ver Traspaso 1. |

**Cobertura del desglose fiscal — corregida:** no es una propiedad del canal, **es un
parser incompleto**. Hay **dos esquemas de payload**: el de Orders API (con `ItemPrice`) y
uno de settlement (`{orderItemId, proceeds, product, quantityOrdered}`) presente sólo entre
dic-2025 y feb-2026. Sobre el esquema Orders API la cobertura es **99.8% MX / 100% US**. El
"84.9%" promediaba un mes roto (enero-2026 = 0/136).

### ❌ No migrar

| dato | por qué |
|---|---|
| **`sales_history.cogs` de MeLi** | **392 de 800 grupos sin costo (49%)**, con un muro exacto: 100% sin costo de mar-2025 a ene-2026. Esas filas suman **483,410 MXN de venta reportando 284,411 MXN de utilidad con costo CERO** (faltan ~139k MXN). **El `cogs` nunca es NULL, siempre es 0** — por eso ningún filtro `IS NOT NULL` lo detecta y la auditoría lo marcó "limpio". |
| **`snapshots`** | no es historia de competidores: 88,321 filas, 100% propias. |
| **214,269 filas de tablas de backup** | una de ellas más grande que la tabla viva. |
| **`decision_audit`** | 117,827 filas congeladas desde el 2026-05-19. Un análisis sobre ella parece válido y es historia muerta. |
| **`own_items.current_qty` de MeLi** | 45 de 73 items sin respaldo en bridge. |

### Márgenes reales que el motor leía (población correcta, ventana 90d)

```
amazon_mx  44.83%
amazon_us  42.57%
meli       37.39%
```

> El trabajo de base ex-IVA (que habría subido amazon_us a ~57.8%) **nunca se desplegó**:
> el `main.py` de producción tiene 0 ocurrencias de `sale_ex_tax`. Quedó en la rama `claude`.

---

## 5. Reglas de diseño para el sistema nuevo

Destiladas de todo lo anterior. Cada una tiene atrás un error que ya se pagó.

1. **Una decisión, un camino, un dueño.** Elegí las decisiones que importan (cuánto pujar,
   qué pausar, cuánto presupuesto) y construí **sólo** su camino. Lo que no está en ese
   camino no se construye. El sistema viejo tenía 62 módulos de motor (7 MB), 206 flags y
   147 jobs para tomar tres decisiones.
2. **Un número, una fuente.** Si el margen viene de tres lugares, no tenés un margen.
3. **Dato faltante = `None` y la fila no se escribe.** Nunca una constante.
4. **Toda cantidad de dinero lleva moneda y fecha de FX, por schema.** Y se guarda el
   importe original además del convertido.
5. **Toda métrica lleva su edad** (`observed_at`), append-only. Sin esto ningún backtest
   vale.
6. **La edad mínima del dato depende del tipo de decisión.** Cortar ≥10 días; subir puja
   tolera menos.
7. **Ninguna acción irreversible sin su reversa implementada antes.**
8. **Antes de escribir el test de un invariante, correr el `SELECT` que confirma la forma
   real de esa columna en producción.**
9. **Toda prueba de regresión debe demostrarse fallando contra el código anterior.**
10. **Conciliar contra la fuente externa, no contra uno mismo.** Los 12 invariantes del
    sistema viejo verificaban consistencia interna; **nunca** se conciliaron los números
    contra los reportes de liquidación de Amazon. Ese es el único chequeo que termina la
    regresión de revisiones, porque no depende del criterio de nadie.

---

## 6. La pregunta que ningún sistema nuevo resuelve escribiendo mejor código

La cuenta de Amazon US produce entre **+1,671 y −2,238 USD** de contribución en 91 días
sobre 4,867 USD de gasto, **según se cuente o no el halo**. No se sabe ni el signo.

Eso no viene del código: viene de que **56–58% de la venta atribuida es de otros SKUs** y
nadie sabe si esa venta existe sin el anuncio. Un sistema nuevo hereda esa pregunta
intacta.

**Hay que decidir cómo responderla ANTES de escribir lógica de decisión encima.** Las
opciones son tres y sólo una da una respuesta real:

1. **Acotarla sin resolverla** (barato): correr cada decisión bajo **ambos** supuestos y
   actuar sólo donde coinciden. Dice cuánto gasto es genuinamente ambiguo.
2. **Un holdout** (semanas, cuesta dinero): apagar anuncios de un grupo de SKUs y ver si la
   venta de los hermanos persiste. Es lo único que mide **incrementalidad** de verdad.
3. **Decidir por TACoS** y no por atribución: gasto total de ads sobre venta total del
   negocio. **No necesita ninguna suposición de atribución.** Si la meta es 8–12% y el
   número real está adentro, el programa está sano aunque no sepas qué campaña específica
   funciona.

La opción 3 es la más barata y la que estaba disponible todo el tiempo.
