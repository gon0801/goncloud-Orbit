# Diseño: grupo precios-insumos (candidato sonnet)

Código leído: copia de solo lectura en `scratchpad/orbit-master` (master `3c841f4`). Todas las rutas `app/...`,
`migrations/...`, `tools/...` son relativas a esa copia. Sin `graphify-out/graph.json` en la copia: usé `rg` y lectura directa.
No corrí pruebas del repo. No toqué producción.

## Resumen en una pantalla

| ID | Causa raíz (una línea) | Cambio | Unidad |
|---|---|---|---|
| G-H1 | La "decisión anterior" del fusible incluye decisiones que la compuerta retuvo y nadie soltó. | `_SQL_MEDIR_PREVIA` salta las retenidas por compuerta no liberadas. | U1 |
| J-H3 | La corrida decide con la oferta vieja y el día queda consumido (UNIQUE por día). | Guarda en `_decidir_pendientes`: sin oferta fresca no se decide; el repaso de 2 h reintenta. | U2 |
| H-H1 | Una corrección de costo posterior a lo publicado no se puede representar; el SKU se rechaza y el rechazo apaga todo. | `_plan_sku` re-ancla la cola (costo vigente) en vez de rechazar; `ingest costs` sale 1 si quedan skips. | U3 |
| G-H3 | El vigía compara importes que dependen del precio que el propio motor puso. | Comparar tasas (importe / base de precio), no importes. | U4 |
| G-H2 | `_unidad_de_listing` arma una unidad Amazon (1 miembro, variante `None`) también para MeLi. | `Vitrina.unidad_de(conn, listing_id)`: cada plataforma arma su unidad. | U5 |
| H-H4 | `ledger_event.product_id` se fija una vez con el mapa de ese día y el ledger es inmodificable. | Trigger acotado (solo `product_id`, con auditoría obligatoria) + pase de reatribución en `sync_ledger`. | U6 |

Causas que resultaron distintas o más finas de lo descrito:
- **H-H1**: no es solo "congela el costo": el gate `rows_skipped = 0` vive en TRES sitios (`app/estimacion_insumos.py:447-466`,
  `:469-489` y el trigger `migrations/0073_precio02_meli_miembro_costo.sql:262-269`). Mi diseño no los toca.
- **J-H3**: el reporte propone esperar o acotar la espera. Ninguna de las dos cierra la causa: la causa es que `no_evaluado`
  por una fuente pasajera consume la decisión del día (`app/precio/corrida.py:797-804` + `UNIQUE` en `migrations/0039_precio.sql:246`).
- **G-H3**: `fee_variable` sale de la observación de fees (escala con el precio de la oferta); normalizo contra `p_actual` de la
  decisión. Que el fee se cotice exactamente a `p_actual` es **inferido** (`app/estimacion_repository.py:553` y siguientes), no lo comprobé línea por línea.

---

## U1. G-H1: la retención del fusible se suelta sola

### Causa raíz
`app/precio/corrida.py:953-960` (`_SQL_MEDIR_PREVIA`) toma la última decisión anterior de cada listing sin mirar si esa decisión
quedó retenida. Retener solo inserta `precio_retencion` (`corrida.py:1147-1158`), no `precio_cambio`; entonces el día 2:
(a) no hay cooldown, la decisión vuelve a salir `subir`; (b) su "anterior" es la `subir` retenida de ayer con el mismo `goal_id`, y
`es_nueva` (`app/precio/compuerta.py:127-141`) da `False`: 0 intenciones nuevas; (c) el costo malo ya está en la decisión de ayer,
así que tampoco hay salto de insumo (`corrida.py:1086-1088`). Veredicto `Abierta`; `_aplicar_pendientes` aplica en live.
El primer lugar donde el sistema se equivoca es la consulta: trata una decisión que NUNCA se aplicó como "movimiento ya en camino".

### Uso primero
Desde el operador no cambia nada que ya conozca: día 1 llega el aviso "no se aplicó nada"; día 2 llega el MISMO aviso
(la compuerta sigue `Retenida`) hasta que alguien aprieta «Soltar» sobre la corrida del día 1 o del día 2.
Sitios de llamada (no cambian de firma): `corrida.py:1043-1045` (`_medir_compuerta` lee `previas`), `corrida.py:1344+` (`correr` la llama
entre decidir y aplicar), `liberaciones.liberar` (`app/precio/liberaciones.py:62`) sigue siendo el único escritor de la liberación.

### Forma
Un solo cambio de SQL; ningún tipo nuevo.

```python
# app/precio/corrida.py  (reemplaza _SQL_MEDIR_PREVIA)
# Una decision cuya retencion por compuerta NO fue soltada es "lo que no pasó", no "lo que ya iba en camino".
# Se salta para que la anterior sea la ultima decision que si cuenta (o ninguna: es nueva).
# Solo las causas de ESTADO (movimiento_masivo, insumo_sistemico): apagador/corte_errores/goal_cambiado se reintentan solas.
_SQL_MEDIR_PREVIA = (
    "SELECT DISTINCT ON (d.listing_id) NULL, d.listing_id, NULL, d.resultado, d.motivo,"
    " d.goal_id, d.c_valor, d.c_currency::text, d.l_valor, d.l_currency::text,"
    " d.r_valor, d.r_currency::text, d.escenario_id"
    " FROM precio_decision d"
    " WHERE d.platform = %s AND d.decision_date < %s AND d.listing_id = ANY(%s)"
    " AND NOT EXISTS ("
    "   SELECT 1 FROM precio_retencion r"
    "   WHERE r.decision_id = d.id AND r.causa IN ('movimiento_masivo', 'insumo_sistemico')"
    "   AND NOT EXISTS (SELECT 1 FROM v_precio_decision_liberada v WHERE v.decision_id = d.id))"
    " ORDER BY d.listing_id, d.decision_date DESC, d.id DESC"
)
```

Efecto: el día 2 la previa de esas 91 unidades es la decisión anterior al evento (p. ej. `mantener` sin cooldown): `es_nueva` da `True`
(91 nuevas > 50 % -> `Retenida(movimiento_masivo)`) y el costo malo se compara contra el costo previo al evento (salto -> `insumo_sistemico`).
Invariante en estructura: ninguna (es una consulta). Invariante en borde: "retenida sin soltar no cuenta como previa" vive en esta única consulta;
`es_nueva` y `armar_deriva` no cambian.

Semántica al soltar (ya documentada, `liberaciones.py:62-66`): si el dueño suelta la corrida del día 1, esas decisiones pasan a contar como
previas (liberadas), el día 2 el mismo goal NO es nuevo y la compuerta queda `Abierta`: es exactamente "el dueño ya aprobó este movimiento".
Si la corrida del día 2 ya había retenido sus propias decisiones, el repaso del día 2 ve la liberación del día 1, mide `Abierta` y aplica
(`_aplicar_pendientes` relee `liberadas`, `corrida.py:1195-1207`).

### Datos que ya existen
Cero retenciones en 30 días (prod, lectura del 9-nov): nada que reparar. `precio_modo_global` está en shadow desde el 9-nov 16:09 UTC, así que
hoy el arreglo no cambia lo que se escribe.

### Cambio de comportamiento en vivo
Sí, en cuanto el modo vuelva a live: tras un evento masivo el motor ya NO aplica el día 2 solo; exige «Soltar». Se enciende solo (es código);
no necesita interruptor porque el comportamiento viejo es el defecto. Se deshace revirtiendo el despliegue. Riesgo operativo: un universo retenido
genera un aviso por día mientras nadie suelte (ver pregunta Q1).

### Idempotencia
Consulta pura. Correr la compuerta dos veces el mismo día da el mismo veredicto. Una caída a medias no deja estado nuevo.

### Prueba (una; dinero)
`tests/test_precio_corrida.py` (cerca de `:4392`, que hoy solo prueba lo soltado; archivo existente, línea del reporte G): sembrar 12 unidades FBA con el mismo
`goal_id`, decisiones `subir` del día 1 retenidas por `_medir_compuerta` + `_aplicar_pendientes`, decisiones `subir` del día 2, sin liberar.
Hoy `_medir_compuerta` devuelve `Abierta` y `_aplicar_pendientes` escribe; con el arreglo devuelve `Retenida` y no hay `precio_cambio`.
La sonda `G_fusible_dia2.py` es pura y solo demuestra la lógica de `evaluar`; no ejerce el SQL, por eso la prueba debe tocar base.

### Alternativas
- Tabla/estado de "retención viva" por plataforma y canal que la corrida consulta antes de medir: modela mejor el concepto pero añade
  una segunda fuente de verdad (la retención ya está en `precio_retencion`) y un escritor nuevo. Pierde por "split ownership".
- Cambiar `es_nueva` para ignorar anteriores sin `precio_cambio`: rompe el caso legítimo de la ola de 7 días (`mantener(cooldown)`), que sí tiene cambio aplicado
  antes; y no arregla la comparación de insumos. Pierde.

### Tamaño
1 archivo de producción (`app/precio/corrida.py`, 1 constante) + 1 prueba. Una unidad verificable.

---

## U2. J-H3: la corrida de 13:10 decide con ofertas viejas y el día se pierde

### Estado después de #561 (`3c841f4`)
`tools/refresh_estimacion.sh:21-27` espera al bridge hasta 80 x 15 s (20 min desde 12:45, o sea hasta 13:05); `:38-44` sale 1 si el bridge sigue corriendo
o no se pudo leer. Lo que falta: si el bridge termina entre ~13:02 y 13:05 la copia SÍ trae US fresco, el script sale 0, pero la ingesta tarda ~8 min
y el escenario nuevo queda escrito ~13:10:22 (sonda J4), después de la corrida de 13:10.

### Causa raíz
No es el reloj: es que `no_evaluado` por una fuente PASAJERA gasta la decisión del día. `app/precio/corrida.py:797-804` (`decididas`) salta toda publicación con fila hoy
(también las `no_evaluado`), y `migrations/0039_precio.sql:246` + el trigger de la fecha UTC (`:301-311`) hacen la fila inmodificable. Las fuentes que el lector invalida
(`app/estimacion_reader.py:124-134`: oferta > 6 h, `FRESHNESS_LIMIT` en `app/estimacion_insumos.py:41`) vuelven en horas. Aceptado como residual en `plans/claridad-01.md:99`
(según el reporte G); lo que no estaba aceptado era que salga con éxito y mudo. El primer lugar donde se equivoca: `_decidir_pendientes` decide aunque sepa que no hay oferta fresca.

### Uso primero
Operador: a las 13:10 con la estimación tarde, la corrida de esa plataforma imprime `precio: amazon_us sin decidir: oferta de estimacion vieja (ultima 06:36, limite 6h); el repaso reintenta`,
sale 1 (`cli_precio.py:178` ya hace `rc = 1` si hay `errores`) y `cron-avisa` avisa (el cron de precios Amazon va envuelto: `docs/DEPLOY.md:880` es la forma plana, en prod envuelto según J).
Las publicaciones quedan SIN fila hoy; el repaso de las 15:10 las decide con el escenario de 13:10 (oferta de ~13:03, edad 2 h).
Sitios de llamada: `correr` (`corrida.py:1396-1408`) ya consume `(decisiones, lineas, errores, saltadas)`; la guarda devuelve por ese mismo canal, sin tipo nuevo.

### Forma
```python
# app/estimacion_reader.py  (dueño de FRESHNESS_LIMIT y de "escenario de hoy")
def oferta_al_dia(conn, platform: str, *, as_of: datetime) -> tuple[bool, datetime | None]:
    """Hay al menos un escenario con valoracion HOY cuya oferta cumple FRESHNESS_LIMIT.
    Devuelve (al_dia, ultima_oferta). Solo mira oferta: costos/fx/etc. siguen siendo motivos por unidad.
    """
    raise NotImplementedError
    # SELECT max(o.fetched_at)
    #   FROM estimacion_escenario e JOIN estimacion_oferta_observation o ON o.id = e.oferta_observation_id
    #  WHERE o.platform = %s AND e.valoracion_date = (as_of AT TIME ZONE 'UTC')::date AND e.observed_at <= as_of
    # al_dia = ultima is not None and as_of - ultima <= FRESHNESS_LIMIT
```
```python
# app/precio/corrida.py::_decidir_pendientes, justo despues de `goals = [... not in decididas]` (corrida.py:803)
#   if goals:
#       al_dia, ultima = estimacion_reader.oferta_al_dia(conn, platform, as_of=ahora)
#       if not al_dia:
#           return 0, [], [f"{platform} sin decidir: oferta de estimacion vieja ({ultima:%H:%M} UTC, limite 6h); el repaso reintenta"], 0
```
`ahora` ya se pasa a `_decidir_pendientes` desde `correr` (`corrida.py:1396`); si la firma actual no lo trae, se agrega como parámetro (inferido: `_decidir_pendientes` recibe `hoy, ahora`).
Invariantes: "no se decide sin oferta fresca" en borde (esta guarda); "una decisión por listing y día" sigue en `UNIQUE` (estructura), intacta.
Profundidad: una función de lectura de 1 consulta; la corrida no aprende reglas de frescura (las tiene el reader).
MeLi: su oferta también se persiste en `estimacion_oferta_observation` por el mismo camino (`app/estimacion_ingest.py:476` sirve a Amazon y a `meli`), por lo que la guarda es válida para las 3 plataformas.
Si en algún momento MeLi dejara de escribir esa tabla la guarda lo bloquearía todo el día, pero lo haría con el aviso diario de arriba (falla ruidosa, no muda).

### Datos que ya existen
`amazon_us` evalúa 0 de 99 desde el 2-nov (prod): esas decisiones `no_evaluado(oferta_desactualizada)` quedan como están (inmodificables). Hoy mismo la guarda no repara el pasado; desde el primer
día que corra, un día malo pasa a "decide a las 15:10". Sin reparación de datos.

### Cambio de comportamiento en vivo
Indirecto: más decisiones reales los días de bridge tarde (hoy son 0). Entra en live al desplegar; el modo global está en shadow desde el 9-nov, así que primero se ve en shadow. Se deshace revirtiendo el despliegue.
Costo: un aviso por repaso mientras persista la oferta vieja (hasta 6 por día); aceptable porque es el estado real.

### Idempotencia
La guarda es lectura pura; la corrida que no decide no escribe filas de decisión. Un repaso posterior decide una sola vez (UNIQUE). Si el proceso muere entre guarda y decisión, el siguiente repaso repite.

### Prueba (una; dinero: decide precios)
Base: sembrar para `amazon_us` un escenario con valoración hoy y `fetched_at` = 06:36; `as_of` = 13:10 -> `oferta_al_dia` es `False` y `_decidir_pendientes` devuelve 0 decisiones y 1 error, sin filas en `precio_decision`.
Hoy: decide `no_evaluado(oferta_desactualizada)` para las 99 y las deja inmodificables. Con una oferta de 13:03 en el mismo escenario devuelve `True` y decide. (La sonda J4 prueba la aritmética de horarios, no la corrida.)

### Alternativas
- Que la corrida de 13:10 espere (poll) hasta que llegue el escenario: añade hasta N minutos de claim vivo, un segundo reloj que mantener y no cubre el caso en que el bridge llegue después de la espera.
- Mover cron de estimación/precios (p. ej. estimación 12:35, precios 13:30): sigue siendo "cuándo" y no "qué se consume"; el bridge ya derivó 12:44 -> 12:51 en 9 días.
- Permitir re-decidir `no_evaluado` el mismo día (quitar UNIQUE o versionar): toca una tabla inmodificable con triggers y todo el cooldown/readback. Pierde por tamaño y riesgo.

### Tamaño
`app/estimacion_reader.py` (1 función), `app/precio/corrida.py` (4 líneas), 1 prueba. Una unidad. No toca `refresh_estimacion.sh`.

---

## U3. H-H1: un costo corregido el mismo día congela el SKU y apaga los precios de las 3 plataformas

### Causa raíz
`app/costs.py:418-462` (`_plan_sku`) declara lo publicado "prefijo inmutable" y ante cualquier divergencia (costo distinto, cierre movido, fila desaparecida, vigencia retroactiva)
rechaza el SKU entero. Día D 06:00 se publica 110; a las 12:00 sube a 120; desde D+1 el origen colapsa el día D a 120 y choca con lo publicado: rechazo permanente (`costs.py:522-527`).
Dos daños salen de ese rechazo:
1. La vigencia 110 queda abierta y es la que leen `v_margen_producto` y la estimación (meta de ACoS por producto live) mientras el costo real es 120.
2. `rows_skipped >= 1` (`costs.py:532-539`, sale 0 en `:668`); la estimación exige corrida del día con 0 skips (`estimacion_insumos.py:447-466` y la consulta `:469-489`, y el trigger de `0073:262-269`);
   sin ella TODO producto es `costo_desactualizado` (`evaluar_costo_candidatos`, `:518-528`) y precios no evalúa nada. Ni costos ni estimación avisan.
El defecto de fondo: la representación (vigencia por día, inmutable) no puede expresar "el costo cambió dos veces el mismo día" ni "se corrigió historia", así que lo trata como corrupción global. Una corrección tardía de la cola ES legítima.

### Uso primero
Operador: contabilidad corrige un costo dos veces el mismo día. Mañana a las 08:15 `ingest costs` imprime `vigencias: reancladas=1` (cierra la 110 en D+1, abre 120 desde D+1), `rows_skipped=0`, y todo sigue: precios evalúa, margen usa 120.
Si de verdad quedó un SKU irrecuperable (fila inválida, solape en origen, SKU ausente), la corrida sale 1 con `skips: ...` y `cron-avisa` avisa (en vez de salir 0 mudo).
Sitios de llamada: `sync_costos` (`costs.py:~493`) llama `_plan_sku(vigencias_por_sku[sku], publicadas)`; el CLI `main` (`costs.py:~596-670`) imprime y devuelve el código.

### Forma
```python
# app/costs.py
@dataclass(frozen=True)
class PlanSku:
    cierres: list[Vigencia]
    inserciones: list[Vigencia]
    motivo_rechazo: str | None      # no None => skip real (el SKU queda como hoy)
    reanclado: bool = False         # informativo: NO cuenta en rows_skipped

def _plan_sku(serie, publicadas, *, hoy: dt.date) -> PlanSku:
    """Historia publicada inmutable y NUNCA bloquea; la COLA (costo vigente) converge al origen."""
    plan = _plan_prefijo(serie, publicadas)        # = cuerpo actual (costs.py:437-462), sin cambios
    if plan.motivo_rechazo is None:
        return plan
    return _reanclar_cola(serie, publicadas, hoy=hoy, motivo=plan.motivo_rechazo)

def _reanclar_cola(serie, publicadas, *, hoy, motivo) -> PlanSku:
    raise NotImplementedError
    # cola_org = serie[-1] si serie y serie[-1].hasta is None, si no None  -> sin costo actual en origen: devolver rechazo (skip real, como hoy)
    # cola_pub = la unica vigencia publicada con hasta None (el EXCLUDE de sku_cost garantiza <= 1)
    # si cola_pub y (costo, moneda, includes_tax) iguales a cola_org  -> PlanSku([], [], None, reanclado=False)   # historia divergio, costo vigente correcto
    # si max(publicadas) >= hoy  (corrida repetida el mismo dia; cierre vacio violaria sku_cost_rango) -> rechazo transitorio con `motivo`
    # cierres = [cola_pub con hasta=hoy] si cola_pub; inserciones = [Vigencia(desde=hoy, hasta=None, costo=cola_org.costo, moneda=cola_org.moneda)]
```
Detalles: `hoy` = fecha UTC de la corrida (parámetro de `sync_costos(..., hoy=None)` -> `dt.datetime.now(UTC).date()`), para que la prueba pueda fijarla.
La igualdad de cola compara costo/moneda/tax y NO `desde`: sin eso se re-ancla cada día porque tras re-anclar la cola de Orbit empieza en `hoy` y la del origen en D.
La historia no se reescribe: el día D queda con 110 en `sku_cost` aunque el costo real fue 120 desde mediodía (≤ 1 día de error, el mismo que el redondeo intradía ya acepta, `costs.py:305-311`).
Invariantes en estructura: no hay solape y `valid_to > valid_from` (EXCLUDE y CHECK de `migrations/0001_initial.sql:149-155`), importe/inicio publicados inmutables (`sku_cost_solo_cierra_vigencia`, `0001:1613`).
En borde: el re-anclaje solo toca la cola.

```python
# app/costs.py::main (CLI)
#   print(f"vigencias: ... reancladas={resultado.reanclados}")
#   return 1 if resultado.rows_skipped > 0 else 0     # ok=false/skip es bug (regla del dueño); cron-avisa solo avisa con != 0
```
`refresh_costos.sh:66-80` ya propaga `rc=1`. Dependencia: la línea cron de `refresh_costos.sh` hoy es plana (`docs/DEPLOY.md:382,674`, **inferido** que en prod sigue sin `cron-avisa`);
sin el envoltorio el exit 1 solo queda en `costos.log`. Eso es el grupo operación (J-H1); este diseño no lo resuelve.
Compuerta global (`rows_skipped = 0` x3 sitios) NO se toca: es fail-closed deliberado (regla 3) y el trigger exigiría migración; tras el re-anclaje solo la dispara dato realmente inválido.

### Datos que ya existen
Prod: 15 corridas, 0 saltos en 14 días; ningún SKU congelado hoy. La primera corrida con el código nuevo no re-ancla nada. Si un SKU estuviera ya divergente por algo anterior, el re-anclaje lo corrige solo en la primera corrida
(escribe `sku_cost` en producción: lo ejecuta el cron normal, no un agente aparte). Reparación manual: no hace falta.

### Cambio de comportamiento en vivo
Sí: `sku_cost` alimenta la meta de ACoS por producto (live MX/US) y los costos de precios. Efecto: tras una corrección el costo vigente converge al día siguiente en vez de congelarse. No hay interruptor (el estado viejo es el defecto).
Se deshace cerrando la vigencia nueva con una corrección normal; no se borra nada.

### Idempotencia
Misma corrida dos veces el mismo día: la segunda ve cola igual (costo igual) y no hace nada; si el costo cambió entre las dos, `max(publicadas) >= hoy` -> rechazo transitorio hasta mañana (skip visible, no silencioso).
Caída a medias: todo va en una `conn.transaction()` (`costs.py:~470`); rollback completo.

### Prueba (una; dinero)
Pura, sobre `colapsar` + `_plan_sku` (adaptar `sondas/H/h1_costo_congelado.py`): publicar 110 en D, origen dice 120 desde D, `_plan_sku(..., hoy=D+1)`:
hoy `motivo = "costo distinto para vigencia ya publicada"` y `inserciones == []`; con el arreglo `cierres=[110 hasta D+1]`, `inserciones=[120 desde D+1]`, sin rechazo.

### Alternativas
- Gate por producto en vez de global (tabla `costo_sku_pendiente` + `rows_skipped` ya no frena todo): estructuralmente distinta y más fiel a "un SKU no apaga el catálogo", pero exige migración del trigger `0073`, atribuir cada skip a su SKU en `colapsar` (hoy `Counter` por motivo) y tocar la consulta de validación. Pierde por tamaño; recomendada solo si los skips reales se vuelven rutina (Q2).
- Permitir reescribir la vigencia publicada (quitar la inmutabilidad): rompe la promesa de `0001:169-178` (el histórico de márgenes no se mueve hacia atrás). Pierde.
- Que contabilidad no corrija el mismo día: fuera de nuestro control (604 pares (SKU, día) con más de un costo, `plans/orbit-06.md:228-231`).

### Tamaño
`app/costs.py` (+ `tools/` no) y 1 prueba. Una unidad.

---

## U4. G-H3: el vigía cuenta ISR y `fee_variable` como deriva de insumo

### Causa raíz
`app/precio/corrida.py:986-988` vigila `retencion` (`r_valor`, ISR en dinero = tasa x ingreso) y `fee_variable` (fee que escala con el precio, `migrations/0054_precio02_base.sql:171-173`) como importes.
Los dos son función del precio que el propio motor puso ayer: +10 % aplicado a 60 de 158 -> "salto" del 10 % > 5 % en ambos -> `insumo_sistemico` falso, que retiene todo el universo (`compuerta.py:144-155`, `corrida.py:1086-1090`).
Un insumo vigilado debe ser algo que el motor no controla; hoy se vigila un importe derivado de lo que sí controla.

### Uso primero
El vigía sigue vigilando ISR y fee variable, pero como TASAS: `retencion = r_valor / i_valor` (ambas columnas de la misma decisión; `i_valor` ya existe en `precio_decision`, `corrida.py:505-510`),
`fee_variable = escenario.fee_variable / decision.p_actual`. Un cambio real de la tasa de ISR o de la tarifa de Amazon sigue disparando; un +10 % de precio no.
Sitios: `_medir_compuerta` (`corrida.py:1086-1097`) arma los pares; `armar_deriva` y `evaluar` (`compuerta.py`) NO cambian.

### Forma
```python
# app/precio/corrida.py
# _SQL_MEDIR_HOY y _SQL_MEDIR_PREVIA: AÑADIR AL FINAL (no mover indices existentes) i_valor, i_currency::text, p_actual, p_actual_currency::text  -> posiciones 13..16
_INSUMO_DECISION = {"costo": (6, 7), "envio": (8, 9)}                 # importes que el motor no controla
_INSUMO_TASA_DECISION = {"retencion": (10, 11, 13, 14)}                # (num, moneda_num, base, moneda_base)
_INSUMO_TASA_ESCENARIO = {"fee_variable": (4, 6)}                      # num del escenario, moneda; base = p_actual de la decision (15,16)

def _par_tasa(num_antes, base_antes, num_ahora, base_ahora, monedas_iguales: bool) -> tuple[Decimal | None, Decimal | None]:
    """(tasa_antes, tasa_ahora) o (None, None). Falta un lado, base <= 0 o monedas distintas = no comparable (regla 3)."""
    raise NotImplementedError
```
`_INSUMOS_MEDIR` conserva los 6 nombres; solo cambia cómo se arman los pares de dos de ellos. Los nombres de insumo no cambian, así que `precio_compuerta_medicion.insumo` y el aviso (`InsumoVigilado`, `compuerta.py:56`) quedan iguales.
Invariante en estructura: ninguna nueva; en borde: "un insumo vigilado no depende del precio" queda codificado en el único sitio que arma los pares.
Interacción con U1: tras U1, una retención falsa se vuelve más cara (exige «Soltar»), por eso U4 conviene justo después de U1 (ver orden).

### Datos que ya existen
Mediciones pasadas en `precio_compuerta_medicion` (con insumo `retencion`/`fee_variable`) quedan como histórico; no se reescriben. Nada que reparar.

### Cambio de comportamiento en vivo
Menos retenciones falsas; el fusible por movimiento masivo (`es_nueva`) NO cambia. Entra al desplegar (shadow primero por el modo global actual). Se deshace revirtiendo.

### Idempotencia
Pura sobre filas persistidas; repetible.

### Prueba (una; dinero)
Pura, adaptar `sondas/G_deriva_falsa.py`: 60 de 158 con precio +10 %, costo y tasas iguales. Hoy `evaluar` devuelve `Retenida(insumo_sistemico, insumo='retencion')`; con las derivas armadas por tasa devuelve `Abierta`. Y una variante: la tasa de ISR sube de 0.0125 a 0.02 en 60 -> `Retenida`.

### Alternativas
- Quitar `retencion` y `fee_variable` de los vigilados: más chica, pero deja de detectar un cambio real de tarifa o de ISR (un insumo sistémico legítimo). Pierde porque sacrifica detección.
- Excluir del universo medido las unidades con cambio aplicado en los últimos N días: segunda regla de "qué es nuevo" junto a `es_nueva`/cooldown (dos formas de hacer lo mismo). Pierde.

### Tamaño
`app/precio/corrida.py` (2 SQL + armado de pares), 1 prueba. Una unidad, pero comparte función con U1: secuenciar en PRs distintos.

---

## U5. G-H2: MeLi con variaciones nunca recibe escritura de precio

### Causa raíz
`app/precio/cambios.py:173-184` (`_unidad_de_listing`) construye siempre `Miembro(product_id, sku, None)`: forma Amazon (1 miembro, sin variante). La usan `aplicar` (`:368`), la reversa (`:486`), `revertir_lote` (`:814`), `cerrar_huerfanas` (`:924`) y `cerrar_por_observacion` (`:977`).
Para MeLi `MercadoMeli.escribir` (`app/meli/precio_mercado.py:605-611`) compara las variaciones vivas con las de la unidad (ninguna) y levanta `ValueError("variaciones vivas sin mapear ...; sin PUT")`: nunca escribe.
La unidad correcta (con `listing_miembro`) ya la arman en otros dos sitios: `MercadoMeli.catalogo` (`precio_mercado.py:226-259`, consulta `_SQL_MIEMBROS` `:68`) y `tools/precio_sonda.py:96-111` (por eso la sonda X.4 pasó y el camino real no). Tres copias de la regla "cómo se arma la unidad", una equivocada.

### Uso primero
```python
# app/precio/cambios.py: los 5 sitios pasan de
#   unidad = _unidad_de_listing(lid, platform, product_id, sku, asin)
# a
#   unidad = mercado.unidad_de(conn, lid)          # cada plataforma sabe armar la suya
# tools/precio_sonda.py: unidad = mercado.unidad_de(conn, listing_id)   (se borra su copia de la consulta)
```
Operador: tras el arreglo, un goal live de MeLi sobre una publicación con variaciones escribe el PUT con todas sus variantes (`['111','222']`), no `ValueError`.

### Forma
```python
# app/precio/puerto.py  (Vitrina: ya es el protocolo de lectura que Mercado extiende)
class Vitrina(Protocol):
    def unidad_de(self, conn: Conexion, listing_id: int) -> UnidadPrecio:
        """La unidad escribible de UN listing, tal como `catalogo` la arma. Levanta PublicacionSinSku si no hay ruta que escribir."""
        ...

# app/spapi/precio_mercado.py (Amazon): el cuerpo actual de _unidad_de_listing, leyendo listing por id.
# app/meli/precio_mercado.py (MeLi): miembros = _SQL_MIEMBROS (activos, ORDER BY seller_sku) para ese listing; sin miembros -> PublicacionSinSku
#   (la unidad vacia dejaria `variantes=[]` y todas las vivas huerfanas; mismo criterio que tools/precio_sonda.py:111).
# app/precio/cambios.py: borrar _unidad_de_listing (queda una sola forma de armar la unidad).
```
Complemento mínimo (misma unidad): en `MercadoMeli.escribir` asignar `self._ruta_error = f"/items/{unidad.externo}"` ANTES de `_variaciones_vivas` (hoy `:612`, después del chequeo): corrige el `error_code` que nombra la ruta de otra publicación (`cambios.py:287`).
Invariante en estructura: la unidad solo se arma en la plataforma que la entiende (el módulo genérico `cambios.py` deja de conocer `listing_miembro`). Fakes de tests que implementan `Vitrina`/`Mercado` necesitan el método nuevo (inferido; archivos `tests/test_precio02_mercado*.py`, `test_precio_write.py`).

### Datos que ya existen
`precio_cambio` con `estado='error'` y `error_code` de variaciones sin mapear quedan. Efecto cruzado: tres días seguidos de error dejan la publicación en `frenado(api_error)` "hasta un goal nuevo" (`corrida.py:184-221`).
Paso aparte (lo ejecuta otro agente con plan, ESCRIBE producción): lectura previa `SELECT listing_id, count(*) FROM precio_cambio WHERE platform='meli' AND estado='error' AND error_code LIKE '%variaciones%' GROUP BY 1` y `... precio_decision WHERE resultado='frenado' AND motivo='api_error'`; las frenadas se reanudan sembrando goal nuevo (camino existente de la API de goals), no editando filas.

### Cambio de comportamiento en vivo
SÍ, el más sensible del grupo: 33 de 45 goals live de MeLi (dato del 17-oct, `docs/CHAT-CONTEXT.md:18`) pasan de "nunca escribe" a escribir precio real. Cómo se enciende: desplegar con `precio_modo_universo` de `meli/meli` en shadow (hoy el global ya está en shadow, 9-nov);
correr `tools/precio_sonda.py` sobre UNA publicación con variaciones (la sonda ya carga miembros, así que prueba el camino ahora común); revisar un ciclo en shadow; y recién entonces pasar el universo a live por la config existente.
Los frenos existentes acotan: escalón 10 %, 1 cambio por 7 días, fusible 50 %, corte tras 5 errores. Se deshace volviendo el universo a shadow y `precio_reversa` para lo ya escrito (reversa existente, `cambios.py:486`).

### Idempotencia
El PUT es idempotente por precio (`cambios.aplicar` compara vivo contra `p_actual` y exige que no haya cambio abierto, `cambios.py:372-400`); sin cambios.

### Prueba (una; escribe en Amazon/MeLi)
`tests/test_precio02_mercado_meli.py` (existente): listing con dos `listing_miembro` (variantes 111, 222), lector falso con ambas variaciones vivas, `cambios.aplicar` -> el escritor recibe un PUT con `['111','222']`. Hoy: `ValueError ... sin PUT` y `precio_cambio` en `error`.
(La sonda `G_meli_variantes.py` ya reproduce el `ValueError` con fakes.)

### Alternativas
- Mantener `_unidad_de_listing` y añadirle una rama `if platform == 'meli'` con la consulta: es más chico, pero mete conocimiento de MeLi en el módulo genérico y deja la tercera copia (`catalogo`) y la cuarta (sonda). Pierde por "información filtrada" y "dos formas de hacer una cosa".
- Pasar las unidades armadas por la corrida (`catalogo`) hasta `aplicar` en vez de reconstruirlas: cambia las firmas de `aplicar`/reversa y de sus llamadores CLI; más grande. Pierde.

### Tamaño
`puerto.py`, `spapi/precio_mercado.py`, `meli/precio_mercado.py`, `precio/cambios.py` (5 sitios), `tools/precio_sonda.py`, fakes de pruebas. 5-6 archivos de producción: cabe en una unidad si el límite es "un solo cambio mecánico + una prueba"; si se quiere más chico, partir en (a) método nuevo + MeLi y (b) migrar sitios.

---

## U6. H-H4: re-mapear un ASIN deja las ventas pasadas en el producto anterior

### Causa raíz
`app/ledger.py:376-379` fija `ledger_event.product_id` desde `listings[(plataforma, asin)]` en la primera ingesta. La re-ingesta reevalúa lo mismo pero `app/ledger.py:504-510` (`ON CONFLICT ... DO NOTHING`) y el trigger `ledger_append_only`
(`migrations/0001_initial.sql:1554-1556`, `prohibir_mutacion`) impiden corregirlo, aunque `app/listings.py:333-350` documenta el re-mapeo como "corrección legítima del mapa". El `product_id` del ledger es una atribución DERIVADA de un mapa mutable guardada en una tabla inmutable pensada para hechos (monto, fecha). Daño: `v_margen_producto` (`0018:283-360`) y los demás consumidores agrupan por `ledger_event.product_id`.
Consumidores de `ledger_event.product_id` que leí o listé: `migrations/0006-0010, 0015, 0016, 0018, 0021, 0048, 0054` y `app/familias.py:158,241`, `app/envio_muestras.py:53-58`, `app/pantalla_productos.py:209,238` (por eso NO reescribo vistas).

### Uso primero
Operador/ingesta: el siguiente `ingest ledger` tras el re-mapeo imprime `reatribuidas=N` y escribe filas de auditoría; `v_margen_producto` y la meta de ACoS por producto ya usan el producto correcto, sin tocar una sola vista.
Sitios: `sync_ledger` (`app/ledger.py:645`) llama el pase nuevo dentro de su misma transacción, junto a `_atribuir_ventas` (`:602`, que ya reprocesa todas las ventas del plan, también las ya ingeridas: precedente directo).

### Forma
```sql
-- migrations/0083_ledger_reatribucion.sql  (nombre/numero: siguiente libre; en master el ultimo es 0082)
CREATE TABLE ledger_reatribucion (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ledger_event_id BIGINT NOT NULL REFERENCES ledger_event(id),
    product_antes   BIGINT NOT NULL REFERENCES product(id),
    product_despues BIGINT NOT NULL REFERENCES product(id),
    ingest_run_id   BIGINT REFERENCES ingest_run(id),
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (product_antes <> product_despues)
);  -- append-only: trigger prohibir_mutacion (+ TRUNCATE), como el resto de auditorias

-- Sustituye ledger_append_only: prohibe DELETE; UPDATE solo de product_id, solo ventas, solo non-null -> otro non-null,
-- y AUDITA en el mismo trigger (la auditoria no se puede omitir).
CREATE FUNCTION ledger_solo_reatribuye() RETURNS trigger ... AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'ledger_event es append-only' USING ERRCODE='restrict_violation'; END IF;
  IF OLD.kind <> 'sale' OR OLD.product_id IS NULL OR NEW.product_id IS NULL OR NEW.product_id = OLD.product_id
     OR (to_jsonb(NEW) - 'product_id') IS DISTINCT FROM (to_jsonb(OLD) - 'product_id') THEN
     RAISE EXCEPTION 'ledger_event: solo se reatribuye product_id de una venta' USING ERRCODE='restrict_violation';
  END IF;
  INSERT INTO ledger_reatribucion (ledger_event_id, product_antes, product_despues) VALUES (OLD.id, OLD.product_id, NEW.product_id);
  RETURN NEW;
END $$;
GRANT UPDATE (product_id) ON ledger_event TO app_ingest;
```
```python
# app/ledger.py
def _reatribuir_ventas(conn, ventas: list[EventoLedger], *, run_id: int) -> int:
    """Para cada venta del plan con product_id calculado ahora: UPDATE ledger_event SET product_id = %s
    WHERE id = %s AND product_id IS NOT NULL AND product_id <> %s.  Devuelve cuantas cambio.
    No toca ventas con product_id NULL (ver Q3) ni ventas cuyo producto ahora es NULL (conserva el viejo)."""
    raise NotImplementedError
# sync_ledger: despues de _atribuir_ventas -> reatribuidas = _reatribuir_ventas(...); ResultadoSync.reatribuidas; CLI imprime el conteo.
```
Invariantes en estructura: solo `product_id` de ventas es mutable y toda mutación deja auditoría (trigger); monto/fecha/cantidad siguen inmodificables. En borde: cuándo reatribuir (el mapa vigente manda) queda en `_reatribuir_ventas`.
Precedente de excepción acotada con trigger propio: `sku_cost_solo_cierra_vigencia` (`0001:1613`), y la documentación de `prohibir_mutacion` (`0001:1678-1685`) lista las excepciones deliberadas: se añade `ledger_event` ahí.
Alcance: solo Amazon. MeLi atribuye por `fila.sku` (`ledger.py:380-384`), no por el mapa de listings, así que un re-mapeo de listing no lo afecta (aplica a la excepción PEZ NH=SET de MeLi que mencionó el dueño: otra ruta).

### Datos que ya existen
Primera corrida con 0 re-mapeos (reporte H): el primer pase debe dar `reatribuidas=0`; si da > 0 es un re-mapeo anterior no detectado y las filas de auditoría lo documentan. Fotos históricas ya sacadas (`0021_economia_observada.sql`, snapshots) no cambian: es correcto (son lo que se vio entonces).
Reparación: no hace falta un paso aparte; el pase la hace al correr. Es escritura en producción: la ingesta de ledger la hace su cron normal; el primer arranque conviene correrlo a mano con plan y leer `reatribuidas` antes de dejarlo en cron.

### Cambio de comportamiento en vivo
Indirecto y real: la meta de ACoS por producto (`margen_producto`, live MX/US) cambia para NH- y SET- el día del siguiente `ingest ledger` tras un re-mapeo (sube/baja al valor correcto). Sin interruptor (es la corrección); se acota porque el re-mapeo es manual y raro.
Se deshace restaurando `product_id` desde `ledger_reatribucion` (la auditoría trae `product_antes`).

### Idempotencia
Segunda corrida: `product_id <> nuevo` es falso, 0 filas. Un mapa que vuelve atrás reatribuye de regreso (auditoría con ambos movimientos). Caída a medias: mismo `conn.transaction()` de `sync_ledger`; rollback completo.

### Prueba (una; escribe datos de producción + dinero)
Base: `sync_ledger` con mapa ASIN -> 101, luego con ASIN -> 202 sobre el mismo `source_event_id`: hoy `product_id` sigue 101 (sonda `h3_remapeo_ledger.py` lo muestra a nivel `mapear_destino`);
con el arreglo queda 202 y hay una fila en `ledger_reatribucion`; un `UPDATE ledger_event SET amount = ...` sigue fallando.

### Alternativas
- Tabla de overrides `ledger_event_producto` + vista que sustituya a `ledger_event` en cada consumidor: respeta el inmutable del todo, pero obliga a reescribir ~12 migraciones/vistas y 4 módulos app (lista arriba) y una vista nueva que cualquiera puede olvidar usar ("dos formas de leer"). Pierde por tamaño y riesgo de olvido.
- Solo detectar y avisar (registrar el re-mapeo en `listings` + Telegram) y reparar a mano: es lo mínimo si el dueño decide que no vale el trigger (Q4); deja el daño permanente hasta que alguien repare.
- Mover el ASIN al ledger (columna nueva) para poder reasignar desde la base: el ASIN solo está en el payload de origen; no ayuda con filas viejas y sigue necesitando la excepción de mutación.

### Tamaño
1 migración (+ reversa, patrón del repo `00xx_reversa_*.sql`), `app/ledger.py`, 1 prueba, 1 línea en `docs`. Una unidad (la migración y el pase se entregan juntos; sin el trigger el pase falla de forma ruidosa).

---

## Orden propuesto de las unidades

1. **U1 (G-H1)**: ALTO, 1 constante SQL, sin migración. Cierra la única falla de este grupo que mueve dinero sin que nadie lo suelte.
2. **U2 (J-H3)**: daño presente (US evalúa 0 de 99 desde el 2-nov); 1 función + 4 líneas. Independiente de U1 (misma archivo, otra función): PRs distintos, en cualquier orden entre sí.
3. **U3 (H-H1)**: ALTO latente (hoy 0 saltos). Archivo propio (`costs.py`); puede ir en paralelo a U1/U2.
4. **U4 (G-H3)**: después de U1 (comparten `_medir_compuerta`; y tras U1 una retención falsa cuesta un «Soltar» por día).
5. **U5 (G-H2)**: al final de precios porque ENCIENDE escrituras reales; hacerlo con el global en shadow y con la sonda X.4 sobre una publicación con variaciones.
6. **U6 (H-H4)**: independiente; el daño solo ocurre tras un re-mapeo manual que hoy no existe. Puede esperar la decisión Q4.

Dependencias duras: ninguna entre unidades. Dependencia blanda: U3 y su aviso dependen del grupo operación (envolver `refresh_costos.sh` con `cron-avisa`, J-H1) para que el exit 1 se convierta en Telegram.
Cada unidad lleva UNA prueba (todas caen en los 4 casos del repo: dinero o escribir producción). No hay baterías ni mutantes.

## Decisiones que solo el dueño puede tomar

- **Q1.** Tras un evento masivo, ¿aceptas un aviso de compuerta cada día hasta que alguien aprieta «Soltar» (U1), o prefieres que una retención sin soltar caduque después de N días? Recomiendo sin caducidad: soltar es una decisión consciente y caducar reabre el defecto.
- **Q2.** ¿Un solo SKU con dato realmente inválido (fila corrupta, solape, ausente del origen) debe seguir apagando los precios de todo el catálogo (comportamiento actual, fail-closed) o solo el de ese producto (U3, alternativa 1)? Recomiendo mantener fail-closed global + aviso, porque hoy los skips reales son 0 y cambiarlo exige migrar el trigger `0073`.
- **Q3.** ¿Las ventas históricas sin producto (`product_id` NULL porque el ASIN aún no estaba en `listing`) deben reatribuirse cuando aparezca el listing (U6)? Hoy quedan sin producto para siempre y el margen se abstiene. Recomiendo NO en esta unidad (cambia márgenes históricos sin que nadie lo haya pedido); decidir aparte.
- **Q4.** ¿Vale el trigger acotado sobre `ledger_event` (U6), o prefieres solo detectar y avisar hasta que ocurra un re-mapeo real? Recomiendo la reatribución, pero es la única unidad que debilita una tabla inmodificable; la excepción queda documentada y auditada.
- **Q5.** Para U5, ¿quién y cuándo pasa `meli/meli` de shadow a live, y se acepta que 33 goals empiecen a escribir a la vez? Recomiendo encender primero una sola publicación con variaciones (la sonda) y luego el universo.

## Riesgos

- **U1**: si algún flujo suelta corridas en bloque sin mirar, el día 2 vuelve a abrir; el arreglo depende de que «Soltar» siga siendo consciente.
- **U2**: la guarda asume que `estimacion_oferta_observation` recibe la oferta de las 3 plataformas (leído en `estimacion_ingest.py:476`, aplicado a MeLi por inferencia). Si no fuera así, bloquearía MeLi con aviso diario (ruidoso, no mudo).
- **U3**: el costo de un día queda hasta 1 día "viejo" por diseño (`desde = hoy`); y la rama "misma fecha" difiere hasta mañana si la corrida se repite el mismo día con un costo que cambió.
- **U4**: que `fee_variable` esté cotizado al `p_actual` de la decisión es **inferido**; si se cotizó a otro precio (p. ej. `p_aplicado`), la tasa sale desplazada y hay que normalizar contra el precio de cotización. Verificar antes de implementar con una lectura de `estimacion_escenario` frente a `precio_decision`.
- **U5**: cambia dinero real a escala (33 goals). El freno `api_error` acumulado (3 días de errores pasados) mantendrá algunas publicaciones frenadas hasta sembrar goal nuevo; hay que medirlo antes de encender.
- **U6**: el UPDATE sobre el ledger puede mover márgenes ya vistos en pantallas; el primer arranque debe leerse a mano.
