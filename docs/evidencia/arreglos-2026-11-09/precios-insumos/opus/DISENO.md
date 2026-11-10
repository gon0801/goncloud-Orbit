# Diseño de arreglos: grupo precios-insumos (candidato opus)

Código leído: copia de master `3c841f4` en `scratchpad/orbit-master`. No hay `graphify-out/graph.json`
en esa copia, así que usé `rg` y lecturas directas. Corrí las sondas puras del grupo y reproducen lo
que dicen sus reportes: `G_fusible_dia2.py` (DIA 2 `Abierta`), `G_deriva_falsa.py` (`Retenida(insumo='retencion')`),
`G_meli_variantes.py` (`ValueError ... sin PUT`), `H/h1_costo_congelado.py` (`costo distinto para vigencia
ya publicada` en D+1 y D+10), `H/h3_remapeo_ledger.py` (101 contra 202) y `J/J4_...` (13:02:30 → escenario
13:10:22).

Cinco unidades. Dos hallazgos comparten unidad porque tocan la misma función y uno empeora si el otro
llega solo (G-H1 + G-H3).

| Unidad | Hallazgos | Escribe dinero real | Migración |
|---|---|---|---|
| U1 compuerta | G-H1, G-H3 | sí (deja de aplicar lo no soltado) | no |
| U2 costos | H-H1 | indirecto (costo → precios y meta de ACoS) | sí (trigger del escenario) |
| U3 espera de insumos | J-H3 | sí (decide más tarde en vez de no decidir) | no |
| U4 unidad de MeLi | G-H2 | sí (empieza a escribir 33 publicaciones) | no |
| U5 producto de la venta | H-H4 | indirecto (margen → meta de ACoS) | sí (tabla de auditoría + trigger) |

---

## G-H1 · La retención del freno se suelta sola al día siguiente

### Causa raíz
`app/precio/corrida.py:953-960` (`_SQL_MEDIR_PREVIA`) toma como «decisión anterior» la última decisión
de días previos **aunque la compuerta la haya retenido y nadie la haya soltado**. Esa fila alimenta dos
cosas en `_medir_compuerta`:
- `es_nueva` (`corrida.py:1072-1082` → `app/precio/compuerta.py:127-141`): la `subir` retenida de ayer con el
  mismo `goal_id` hace que la de hoy «no sea nueva» → 0 intenciones nuevas.
- la deriva de insumos (`corrida.py:1086-1101`): el costo malo ya está en la decisión de ayer → 0 saltos.

Retener solo inserta `precio_retencion` (`corrida.py:1157`, `_SQL_RETENER` en `:1119`), no `precio_cambio`, y el
cooldown sale de `precio_cambio` (`app/precio/fuentes.py:372-380`, según reporte G; no releído). Resultado:
día 2 `Abierta` y `_aplicar_pendientes` escribe. El primer error es tratar un día que el dueño no aprobó
como línea base. `app/precio/liberaciones.py:64-66` ya dice la regla correcta: solo lo **soltado** deja de
contar.

### Uso primero
Desde el operador:
- Día 1 13:10: Telegram «costo saltó en 91 de 158 … no se aplicó nada» (igual que hoy).
- Día 2 13:10 sin «Soltar»: **otra vez** `Retenida(insumo_sistemico)` y otro aviso (el índice
  `precio_medicion_un_aviso_por_dia`, `migrations/0054_precio02_base.sql:313-315`, permite uno por día y
  canal). Nada se aplica.
- El dueño aprieta «Soltar» en `/precios` (`POST /api/precios/compuerta/{id}/liberar`,
  `app/api_precios_write.py:332`) sobre la corrida retenida más reciente → el siguiente repaso aplica lo de
  hoy, y desde mañana ese día ya cuenta como línea base.
- Si la causa desaparece sin soltar (contabilidad corrige el costo), la deriva contra el último día
  admitido da 0 y la retención se disuelve sola. Eso es legítimo: ya no hay nada masivo que confirmar.

Sitios de llamada sin cambio de firma: `corrida.correr` → `_medir_compuerta(conn, mercado, hoy, motor,
corrida_id)` (`corrida.py:1418`); `liberaciones.liberar(...)` (sin cambio).

### Forma
Un día de un universo (`platform`, `canal`, `decision_date`) es **admitido** si ninguna de sus decisiones
retenidas por `movimiento_masivo`/`insumo_sistemico` sigue sin soltar. La previa se busca solo entre días
admitidos. Se excluye **el día completo del universo**, no solo las `subir`/`bajar` retenidas: si solo se
saltaran esas, las `mantener` del mismo día (que también traen el costo malo) se volverían línea base y
la deriva del día 2 se diluiría (ejemplo: 60 costos malos, 20 quieren moverse; día 2 solo 20 de 158 se
comparan contra el día 0 → no dispara y las 20 salen con el costo malo).

```python
# app/precio/corrida.py — reemplaza _SQL_MEDIR_PREVIA (corrida.py:953-960)
_SQL_MEDIR_PREVIA = (
    "SELECT DISTINCT ON (d.listing_id) NULL, d.listing_id, NULL, d.resultado, d.motivo,"
    " d.goal_id, d.c_valor, d.c_currency::text, d.l_valor, d.l_currency::text,"
    " d.r_valor, d.r_currency::text, d.escenario_id, d.i_valor, d.i_currency::text"  # +2 de G-H3
    " FROM precio_decision d"
    " WHERE d.platform = %s AND d.decision_date < %s AND d.listing_id = ANY(%s)"
    # Dia retenido y no soltado del mismo universo: no es linea base (G-H1).
    # Regla espejo de liberaciones.py:64-66: solo lo SOLTADO deja de contar.
    " AND NOT EXISTS ("
    "   SELECT 1 FROM precio_retencion r"
    "   JOIN precio_decision dr ON dr.id = r.decision_id"
    "   WHERE r.causa IN ('movimiento_masivo', 'insumo_sistemico')"
    "     AND dr.platform = d.platform AND dr.decision_date = d.decision_date"
    "     AND dr.canal = d.canal"
    "     AND r.decision_id NOT IN (SELECT decision_id FROM v_precio_decision_liberada))"
    " ORDER BY d.listing_id, d.decision_date DESC, d.id DESC"
)
```

Sin tipos nuevos ni migración. La invariante («lo no soltado no es historia») vive en una sola
consulta; la decisión de qué es «soltado» sigue en `v_precio_decision_liberada`
(`0054_precio02_base.sql:480-484`), que ya es la única fuente. Una decisión con `canal` NULL (frenos) no
choca con `dr.canal = d.canal` y se comporta como hoy.

Idempotencia: es una lectura. Correrla dos veces en el mismo día da el mismo veredicto. Una corrida que
muere a la mitad no deja estado nuevo.

### Datos que ya existen
0 retenciones en 30 días (prod). Nada que reparar. Retenciones viejas soltadas siguen admitidas.

### Cambio de comportamiento en vivo
Sí: hoy el día 2 aplica en live lo no soltado; con el arreglo queda retenido hasta «Soltar». Se enciende
directo (es la semántica del diseño, `docs/superpowers/specs/2026-10-08-repricing-02-design.md:118-133`).
Momento ideal: ahora, mientras `precio_modo_global` está en shadow (desde el 9-nov 16:09) y antes de volver
a live. Deshacer: revertir el commit; no hay datos que limpiar.

### Prueba (dinero)
Una en `tests/test_precio_corrida.py`, junto a `test_s3_liberar_aplica_lo_retenido_de_hoy_y_no_lo_de_ayer`
(`:4392`): sembrar 12 unidades FBA con el mismo goal, día 1 `subir` retenidas por `insumo_sistemico` sin
liberar; día 2 `subir` con el mismo `goal_id` y el mismo costo malo. `_medir_compuerta` debe dar
`Retenida` y `_aplicar_pendientes` debe dejar `escritas == 0`. Hoy da `Abierta` y escribe (lo mismo que
`G_fusible_dia2.py`).

### Alternativas
- **Retención pegajosa por universo**: `evaluar` recibe «hay una retención sin soltar en este universo» y
  devuelve `Retenida` sin medir. Pierde: necesita definir qué corrida suelta a cuál (la liberación es por
  corrida y los repasos crean corridas nuevas), y no se disuelve sola cuando la causa desaparece.
- **Excluir solo las decisiones retenidas** (no el día): más chico, pero deja colar el caso parcial descrito
  arriba.
- **Escribir un `precio_cambio` virtual al retener** para que el cooldown frene: mezcla «retenido» con
  «aplicado» y rompe cooldown, resumen y reversa.

### Tamaño
`app/precio/corrida.py` (una constante) + una prueba. Una unidad con G-H3.

---

## G-H3 · ISR y fee variable cuentan como deriva de insumo

### Causa raíz
`app/precio/corrida.py:986-988` vigila `retencion` con `r_valor` (ISR en dinero = `isr_tasa · I`,
comprobado en `app/precio/reglas.py:123`) y `fee_variable` del escenario («fee que escala con el precio»,
`migrations/0054_precio02_base.sql:171-173`). Los dos son importes proporcionales al precio que el propio
motor movió. El diseño solo pedía vigilar lo que el motor no controla: la lista G4 del spec
(`...repricing-02-design.md:649`) dice «costo, envío, retención, FX o fee fijo»; `fee_variable` se agregó
en el código y `retencion` se midió como importe y no como tasa.

### Uso primero
Sin cambio para quien llama. El operador deja de recibir «retencion saltó en 60 de 158» el día después de
una ola de +10 %, y un cambio real de tasa (política de ISR editada, referral de Amazon que sube) sigue
disparando. Sitio: `corrida.correr` → `_medir_compuerta` (`corrida.py:1418`).

### Forma
Se vigila la **tasa** (importe ÷ ingreso `I` de la misma decisión), no el importe. `I = P / divisor`
(`reglas.py:103`), así que `isr/I = isr_tasa` y `fee_variable/I = referral · divisor`: los dos quedan
quietos cuando solo se mueve el precio.

```python
# app/precio/corrida.py
# _SQL_MEDIR_HOY y _SQL_MEDIR_PREVIA agregan d.i_valor, d.i_currency::text en 13, 14.
_INGRESO = (13, 14)
# Insumos que escalan con el precio: se comparan como tasa sobre I (G-H3).
_INSUMOS_POR_INGRESO = frozenset({"retencion", "fee_variable"})


def _tasa(importe: Decimal | None, ingreso: Decimal | None) -> Decimal | None:
    """`importe / ingreso`, o None si falta un lado o el ingreso no es positivo
    (regla 3: no comparable, nunca inventado)."""
    raise NotImplementedError


def _sobre_ingreso(fila, importe: Decimal | None, moneda_importe: str | None) -> Decimal | None:
    """Tasa del importe sobre el I de la misma fila; None si la moneda del
    importe no es la de I (el fee_variable vive en la moneda del escenario)."""
    raise NotImplementedError

# En _medir_compuerta, despues de obtener (antes, ahora) de cada insumo:
#   if insumo in _INSUMOS_POR_INGRESO:
#       antes = _sobre_ingreso(previa, antes, moneda_previa)
#       ahora = _sobre_ingreso(fila, ahora, moneda_hoy)
```

`compuerta.py` no cambia (sigue puro y recibe pares). El detalle del aviso dice «tasa de retencion».

### Datos que ya existen
Ninguno. `precio_compuerta_medicion` histórica queda como está.

### Cambio de comportamiento en vivo
Retiene menos (solo quita retenciones falsas). Directo, con U1. Deshacer: revertir commit.

### Prueba
Fuera de los 4 casos por sí sola (retiene de más, no pierde dinero). Comprobación: adaptar
`G_deriva_falsa.py` para pasar tasas y ver `Abierta`. Va en la misma unidad que G-H1 porque **con G-H1
arreglado y G-H3 sin arreglar, la retención falsa del día siguiente a una ola se vuelve pegajosa**: el día
de la ola deja de ser línea base y cada día vuelve a ver el salto de ISR, hasta que el dueño suelte.

### Alternativas
- **Quitar `retencion` y `fee_variable` de la lista**: más chico, pero un cambio real de referral de Amazon
  (evento sistémico clásico) deja de detectarse.
- **Comparar `politica_version_id`** del escenario para el ISR: detecta el cambio de política pero no el de
  referral; dos mecanismos para lo mismo.

### Tamaño
Mismo archivo y función que G-H1. Juntas son una unidad verificable (U1).

Residual declarado: un fee por tramos de precio (fee fijo de MeLi bajo 299 MXN, tramos de referral en
algunas categorías de Amazon) puede seguir dando un salto de tasa si una ola cruza el tramo. Con U1, eso
retiene hasta «Soltar» y avisa cada día.

---

## H-H1 · Costo congelado por corrección del mismo día o de historia, y precios apagado sin aviso

### Causa raíz
Son tres errores encadenados:

1. **Se publica un día que todavía no termina.** La regla D2 de `plans/orbit-06.md:213-236` (y
   `app/costs.py:14-17`) dice «el costo del día D es el de la ÚLTIMA fila del día». La corrida de las 08:15
   publica `[D, abierta)` (`costs.py:461`, `inserciones` sin corte de fecha), así que publica un valor del
   día D antes de que D termine. Cuando contabilidad cambia otra vez ese día, D+1 colapsa D a otro valor y
   `_plan_sku` lo rechaza para siempre (`costs.py:441-442`). Lo confirma `h1_costo_congelado.py`: la corrida 1
   (D) inserta `desde=2026-11-03, hasta=None` el mismo 3-nov.
2. **El rechazo de un SKU deja su vigencia abierta en uso.** `_plan_sku` devuelve motivo y el bucle hace
   `continue` (`costs.py:507-509`); la vigencia abierta sigue cubriendo cada día nuevo en
   `v_margen_producto` (`migrations/0018_fabrica_campanas.sql:292-301`). Es justo el «caso mudo y peligroso»
   que describe el trigger de `sku_cost` (`migrations/0001_initial.sql:1628-1633`: extender la última
   vigencia cambia márgenes sin señal). Igual pasa con historia editada o borrada (`costs.py:437-454`) y con un
   SKU que desaparece del origen (`costs.py:526-529`).
3. **La validez del costo es global por corrida.** `app/estimacion_insumos.py:459` y `:479` exigen
   `rows_skipped = 0` a la corrida de costos, y el trigger del escenario repite la regla
   (`migrations/0073_precio02_meli_miembro_costo.sql:262-269`). Un SKU rechazado deja a **todos** los
   productos en `costo_desactualizado` (`estimacion_insumos.py:524-525`). La corrida sale 0
   (`costs.py:668`), así que `cron-avisa` calla.

El tercero es consecuencia del segundo: `rows_skipped = 0` es un proxy de «cada costo abierto coincide con
el origen». Si esa invariante se cumple por construcción, el proxy sobra.

### Uso primero
Salida del pipeline (lo que ve el log de `refresh_costos.sh` y Telegram):
```
$ python -m app.cli ingest costs --sqlite /tmp/accounting-snapshot.XXXX.db
run_id=812 ok=True rows_written=3 rows_skipped=0
corte=2026-11-10 (el dia de hoy queda para mañana: D2, ultimo valor del dia)
vigencias: insertadas=2 cerradas=1 ...
convergencias=1 (NH-ARRAS-16: el origen reescribio historia publicada; sigue al origen desde 2026-11-09)
sin_costo_en_origen=0
```
Telegram, solo si hubo convergencias o cierres por falta de costo: «Costos: 1 producto con historia
corregida en contabilidad (NH-ARRAS-16). Orbit usa el costo nuevo desde ayer; lo anterior queda como se
publicó.»

Lectura de precios (`app/estimacion_insumos.py:543`, sin cambio de firma):
```python
costo = resolver_costo(conn, product_id=pid, fecha_escenario=hoy, now_utc=ahora)
# un SKU sin costo en el origen sale costo_no_vigente; los demás siguen evaluando
```

### Forma
```python
# app/costs.py

class Cierre(NamedTuple):
    valid_from: dt.date      # la vigencia publicada abierta
    valid_to: dt.date        # el corte que se le pone (> valid_from, CHECK sku_cost_rango)


class PlanSku(NamedTuple):
    cierres: list[Cierre]
    inserciones: list[Vigencia]
    convergencia: str | None   # informativo: el origen reescribio dias ya publicados
    sin_costo: bool            # el origen no da costo para el ultimo dia cerrado


def recortar(serie: list[Vigencia], *, corte: dt.date) -> list[Vigencia]:
    """Lo firme de la serie: quita las vigencias que empiezan en `corte` o despues
    y deja abierta (hasta=None) la que llega a `corte` o lo pasa. `corte` = dia
    UTC de la corrida: ese dia todavia puede cambiar (D2: ultimo valor del dia)."""
    raise NotImplementedError


def planear_sku(
    serie: list[Vigencia] | None,          # None: SKU ausente o solapado en el origen
    publicadas: dict[dt.date, tuple[Decimal, dt.date | None, str, bool]],
    *,
    corte: dt.date,
) -> PlanSku:
    """Lo publicado es inmutable; lo que Orbit no ha fijado sigue al origen.

    1. firme = recortar(serie, corte)
    2. Si lo publicado es prefijo de `firme`: cierres + inserciones como hoy
       (_plan_sku de costs.py:416-462, sin cambio de reglas).
    3. Si no (historia reescrita, borrada, SKU ausente o solapado): converger
       hacia adelante desde U = max(corte - 1 dia, abierta.valid_from + 1 dia):
         v = vigencia de `firme` que cubre U (o None)
         - v is None          -> cerrar la abierta en U; sin_costo=True
         - v igual a abierta  -> nada que escribir; convergencia informativa
         - v distinta         -> cerrar la abierta en U; insertar [U, None) con v
       Nunca toca dias < U: lo ya publicado se queda como se publico.
    4. Un estado imposible (abierta con valid_from >= U, o una cerrada con
       valid_to > U) levanta CostsError: aborta la corrida (ok=false, exit 1,
       cron-avisa) en vez de dejar un SKU con costo viejo abierto.
    """
    raise NotImplementedError


def sync_costos(conn, ruta_sqlite, *, corte: dt.date, acepto_cierre_masivo: bool = False) -> ResultadoSync:
    """Igual que hoy, con `planear_sku` por producto (los del origen Y los de
    Orbit ausentes del origen, un solo bucle). Antes de escribir: si los cierres
    por `sin_costo` pasan del 10 % de los productos con vigencia abierta y no
    viene `acepto_cierre_masivo`, levanta CostsError (copia truncada o sync de
    contabilidad a medias). Los rechazos por fila (costo 0, moneda...) se
    siguen contando en rows_skipped como dato informativo."""
    raise NotImplementedError
```

`main` pasa `corte = datetime.now(UTC).date()`, agrega la bandera `--acepto-cierre-masivo` (mismo patrón que
`--acepto-mutacion-real` de `tools/precio_reversa.py`) e imprime las líneas nuevas. Si `convergencias` o
`sin_costo` > 0, manda un aviso con `notifica.envia_aviso` (`app/notifica.py:237`), una vez por corrida.

Validación (`app/estimacion_insumos.py`):
```python
def _corrida_validacion_costos_aceptable(source, ok, finished_at, *, fecha_escenario, now_utc) -> bool:
    """Corrida GLOBAL accounting_sku_costs ok del mismo dia UTC, terminada <= now_utc.
    Ya no mira rows_skipped: tras una corrida ok, cada costo abierto coincide con
    el origen o no existe (planear_sku lo garantiza)."""
    raise NotImplementedError
# _buscar_corrida_validacion_costos: quitar " AND ir.rows_skipped = 0" (estimacion_insumos.py:479)
```

Migración (nombre con la etiqueta del plan que la entregue; ver riesgos sobre `migraciones_de`):
```sql
-- 0083_<plan>_costo_validacion_por_sku.sql
-- CREATE OR REPLACE FUNCTION estimacion_escenario_referencias_coherentes() con el
-- mismo cuerpo de 0073 menos "OR v_run_rows_skipped IS DISTINCT FROM 0" (0073:265).
-- Reversa: la definicion de 0073 tal cual.
```

Dónde queda cada invariante:
- Estructura: `sku_cost_solo_cierra_vigencia` y el EXCLUDE (sin cambio) siguen haciendo imposible reescribir
  lo publicado. `planear_sku` solo produce cierres `NULL → fecha` e inserciones nuevas.
- Función pura: «cada costo abierto coincide con el origen al último día cerrado» lo cumple `planear_sku`;
  el estado imposible aborta la corrida.
- Borde: la proporción de cierres masivos (copia rota) se valida antes de escribir.

Idempotencia: correr dos veces el mismo día con el mismo snapshot da cero escrituras la segunda vez (las
vigencias ya están). Caída a la mitad: todo va en una transacción (`costs.py:479`); se revierte entero.

### Datos que ya existen
Prod: 15 corridas, 0 saltos en 14 días → no hay SKUs congelados hoy. No hace falta reparación. Primer día
tras el despliegue: si la corrida de ese mismo día ya publicó `[hoy, abierta)` con el código viejo, D+1 lo
reconcilia (prefijo si coincide; si no, convergencia en U = D+1).

### Cambio de comportamiento en vivo
- Un costo nuevo entra a Orbit **al día siguiente** de su último cambio, no el mismo día. Para Ads no cambia
  nada (la ventana de `v_margen_producto` termina en hoy−15, `0018:290`). Para precios, el motor decide un
  día más con el costo anterior.
- Un SKU con historia reescrita deja de quedar congelado (meta de ACoS por producto en live).
- Un SKU que desaparece de contabilidad pierde su costo desde ayer: sale `costo_no_vigente` en precios y baja
  la cobertura de su margen en Ads (que ya se abstiene bajo 95 %).
- Directo, sin interruptor (es corrección de datos de entrada). Deshacer: revertir commit y migración; las
  vigencias escritas son correctas bajo las dos versiones.

### Prueba (dinero)
Una en `tests/test_costs_sync.py`, pura sobre `planear_sku` con el escenario de `h1_costo_congelado.py`:
corrida en D con corte D (cambio a 110 a las 06:00) no inserta nada que empiece en D; corrida en D+1 con el
origen ya en 120 cierra la vigencia de 100 en D e inserta `[D, None) = 120`. Hoy `_plan_sku` publica 110 en D
y en D+1 devuelve «costo distinto para vigencia ya publicada».

### Alternativas
- **Tabla de confirmación por SKU** (`sku_cost_confirmacion(ingest_run_id, sku_cost_id)`) y validación
  por producto, dejando el rechazo como hoy. Aísla el radio de daño pero deja el costo viejo abierto en
  `v_margen_producto` (Ads live) y suma ~1,100 filas al día. Pierde porque no cierra el error 2.
- **Permitir corregir el importe de una vigencia publicada del mismo día** (abrir el trigger). Rompe la
  regla que la tabla existe para cuidar (`0001_initial.sql:1624-1633`).
- **Mover la corrida de costos a otra hora**: cambia la banda, no la causa (contabilidad sincroniza cada
  hora).

### Tamaño
`app/costs.py`, `app/estimacion_insumos.py`, una migración (función del trigger) con su reversa, una
prueba. Una unidad.

---

## J-H3 · La corrida de 13:10 decide con ofertas viejas y no vuelve a decidir ese día

### Lo que hizo #561 y lo que sigue faltando
`tools/refresh_estimacion.sh:21-27` espera al bridge hasta 20 min y sale 1 si no termina (`:40-43`), así que
`cron-avisa` avisa. Siguen dos huecos:
- Bridge entre ~13:02 y 13:05: la copia trae US fresco, la ingesta termina después de las 13:10, el script
  sale 0, y la corrida de 13:10 ya decidió `no_evaluado(oferta_desactualizada)` para las 99 de US.
- Bridge después de 13:05: hay aviso, pero el día se pierde igual aunque la estimación de 18:45 traiga US
  fresco.

### Causa raíz
`app/precio/corrida.py:797-804`: los repasos saltan toda publicación con decisión de hoy, también las
`no_evaluado` por un insumo que **todavía no llega**. Así, un dato faltante temporal ocupa la única decisión
del día. La regla 3 de `docs/CONTEXTO.md:187` dice lo contrario: dato faltante = la fila no se escribe.
El error está en persistir el `no_evaluado` de toda una plataforma cuando el insumo del día no ha llegado y
quedan repasos que lo pueden usar.

### Uso primero
Log de precios del día del hueco:
```
13:10 amazon_mx: 112 decisiones ...
13:10 amazon_us: 99 de 99 con la oferta del dia sin llegar; decide el siguiente repaso
15:10 amazon_us: 99 decisiones, 3 cambios ...
```
Bridge muy tarde (después de 13:05): `refresh_estimacion` avisa como hoy; US espera en los repasos de
15:10 y 17:10, y decide a las 19:10 con la estimación de 18:45. Desde las 21:00 UTC la corrida decide con lo
que haya: las `no_evaluado` quedan escritas y el aviso de días sin evaluar sigue funcionando.

Sitio: `corrida._decidir_pendientes` (`corrida.py:906`), justo después de `lecturas = mercado.insumos(...)`.

### Forma
```python
# app/precio/compuerta.py (puro)
MOTIVOS_INSUMO_ATRASADO = frozenset({"oferta_desactualizada", "valoracion_desactualizada"})
# Ultima hora UTC en que un repaso todavia espera: la ultima estimacion util es
# la de 18:45 (cron 45 */6) y la toma el repaso de 19:10; 21:10 y 23:10 deciden
# con lo que haya. Atado al cron de docs/DEPLOY.md:861-881.
ESPERA_INSUMOS_HASTA = time(21, 0)


def insumo_atrasado(motivos: Sequence[str | None], *, ahora_utc: datetime) -> str | None:
    """Texto de espera si la MAYORIA del lote no tiene el insumo del dia y aun hay
    repasos que lo pueden usar; None = decidir ya. Un motivo atrasado aislado no
    detiene nada: esa publicacion sale no_evaluado como hoy."""
    raise NotImplementedError

# app/precio/corrida.py, _decidir_pendientes, tras mercado.insumos(...):
#   espera = insumo_atrasado([l.motivo for l in lecturas.values()], ahora_utc=ahora)
#   if espera is not None:
#       lineas.append(f"{platform}: {espera}; decide el siguiente repaso")
#       return decisiones, lineas, errores, saltadas   # los frenos ya persistidos se quedan
```

Lo que no cambia: una decisión por publicación y día (UNIQUE + trigger de `0039`), los frenos se escriben
antes (`corrida.py:812-876`), la compuerta mide el día completo en cada corrida.

Idempotencia: no escribe nada cuando espera; el repaso siguiente vuelve a evaluar. Si dos repasos se
encimaran, el `flock` de la línea de cron y el candado de la corrida (`corrida.py:156-162`) ya lo impiden.

### Datos que ya existen
Ninguno.

### Cambio de comportamiento en vivo
Sí: US se decide y aplica unas horas más tarde en vez de no decidirse. La compuerta y el freno funcionan
igual. Directo; deshacer: revertir commit. Antes de construir, leer en producción la consulta q4 de
`docs/evidencia/claridad-01/F/consultas.txt` de los días posteriores al 9-nov para ver si #561 ya alcanzó
(dato para priorizar, no bloquea).

### Prueba (falla que ya pasó en producción)
US perdió el día del 2 al 9-nov por esta misma cadena. Una prueba en `tests/test_precio_corrida.py`: escenario
de US con oferta de 6 h 34 min a las 13:10 → `_decidir_pendientes` no escribe filas de US; mismo listado con
escenario fresco a las 15:10 → decide. Hoy la primera corrida escribe 99 `no_evaluado` y la segunda no decide
nada.

### Alternativas
- **Candado compartido** (`flock`) entre `refresh_estimacion.sh` (root) y la línea de precios (gon): cierra la
  banda 13:02–13:05 pero el bridge después de 13:05 sigue perdiendo el día, mete dependencia entre usuarios
  de cron y un refresh colgado detiene precios.
- **Permitir que el repaso reemplace un `no_evaluado` transitorio**: choca con el UNIQUE por día, con
  decisiones append-only y con el conteo del fusible.
- **No escribir nunca `no_evaluado` transitorios por publicación**: se pierde el aviso de días sin evaluar.

### Tamaño
`app/precio/compuerta.py`, `app/precio/corrida.py`, una línea en `docs/DEPLOY.md` (tabla de horarios), una
prueba. Unidad chica.

---

## G-H2 · Las publicaciones de MeLi con variaciones nunca reciben precio

### Causa raíz
La unidad que se escribe se arma a mano y distinta de la que se decide. `app/precio/cambios.py:173-184`
(`_unidad_de_listing`) siempre arma un solo `Miembro(product_id, sku, None)`, con variante `None`. El escritor
de MeLi exige que las variantes de la unidad cubran las variaciones vivas (`app/meli/precio_mercado.py:605-611`)
y levanta `ValueError` sin PUT. La forma correcta ya existe en `MercadoMeli.catalogo` (`precio_mercado.py:226-256`,
`_SQL_MIEMBROS` en `:68-72`) y copiada en `tools/precio_sonda.py:96-111` (por eso la sonda X.4 pasó). Hay
**ocho** sitios que construyen `UnidadPrecio` a mano: `cambios.py:368, 486, 814, 924, 977`,
`corrida.py:884-891`, `goals_write.py:427-433` y `tools/precio_sonda.py:112`. Solo `catalogo` sabe de
`listing_miembro`.

Efecto lateral (b) del reporte: `_ruta_error` se asigna después del chequeo (`precio_mercado.py:612`) y
`cambios.py:286` lee el valor de la escritura anterior.

### Uso primero
```python
# app/precio/cambios.py — aplicar (hoy cambios.py:368)
unidad = mercado.unidades(conn, [listing_id]).get(listing_id)
if unidad is None:
    raise PublicacionSinSku(f"listing_sin_unidad: listing {listing_id}")
mercado.validar_destino(unidad)

# app/precio/corrida.py — _decidir_pendientes arma el lote con la misma unidad
unidades = mercado.unidades(conn, [g[1] for g in goals_para_lote])

# tools/precio_sonda.py — _unidad desaparece; usa mercado.unidades
```

### Forma
```python
# app/precio/puerto.py — Vitrina (puerto.py:210-241)
def unidades(self, conn: Conexion, listing_ids: Sequence[int]) -> Mapping[int, UnidadPrecio]:
    """La unidad de cada listing, por lote, con sus miembros reales. UNICA
    construccion de UnidadPrecio fuera de las pruebas: la que decide, la que
    escribe, la que revierte y la que confirma son la misma. Un listing que no
    existe o no es de esta plataforma no viene en el mapa."""
    ...

# app/meli/precio_mercado.py
def _unidades(conn, filas_listing) -> dict[int, UnidadPrecio]:
    """listing + listing_miembro activos (la consulta de _SQL_MIEMBROS).
    La usan `catalogo` y `unidades`: una sola consulta de miembros."""
    raise NotImplementedError

# app/spapi/precio_mercado.py: un miembro (product_id, seller_sku, None), como hoy.
```

`escribir` asigna `self._ruta_error = f"/items/{unidad.externo}"` al entrar, antes del chequeo de huérfanas.
`_unidad_de_listing` se borra en el mismo cambio. Para que no vuelvan a aparecer constructores a mano, una
línea en `tests/test_architecture.py` (que ya tiene candados de este tipo, `liberaciones.py:3-4`): fuera de
`app/meli/precio_mercado.py`, `app/spapi/precio_mercado.py` y `tests/`, nadie llama `UnidadPrecio(`.

Idempotencia: sin cambio; `aplicar` sigue siendo compare-and-set contra el vivo (`cambios.py:369-400`).

### Datos que ya existen
- Filas `precio_cambio` en `error` con `PATCH /items/... excepcion:ValueError`: historia, se quedan.
- Publicaciones en `frenado(api_error)`: `freno_por_error` (`corrida.py:184-221`) solo se reanuda con un goal
  nuevo. Reparación aparte (otro agente, con plan): listar en producción
  `SELECT DISTINCT listing_id FROM precio_decision WHERE platform='meli' AND resultado='frenado' AND motivo='api_error' AND decision_date >= current_date - 7`
  y que el dueño vuelva a sembrar esos goals con el mismo valor por el camino de goals
  (`tools/precio_goal.py`), lo que reinicia `desde`.

### Cambio de comportamiento en vivo
Sí, el más grande del grupo: 33 de 45 goals live de MeLi (dato del 17-oct, `docs/CHAT-CONTEXT.md:18`, no
remedido) empiezan a recibir PUT de precio, todas sus variaciones al mismo precio (lo aceptado en
`spec:542-543`). La decisión también puede cambiar: con miembros reales, `insumos` de MeLi marca
`variante_sin_mapear` (`precio_mercado.py:308-318`) donde hoy pasaba con un miembro falso. Encendido:
desplegar con `precio_modo_global` en shadow (ya lo está); un PUT de prueba con `tools/precio_sonda.py`
sobre una publicación con variaciones por el camino nuevo; luego `meli/meli` a live con la compuerta de U1
ya corregida (el primer día pueden querer moverse muchas a la vez y el fusible las retiene). Deshacer:
`precio_modo_universo['meli/meli'] = 'shadow'` y `tools/precio_reversa.py` por fecha.

### Prueba (escribe en MeLi)
Una en `tests/test_precio_write.py`: listing de MeLi con dos miembros activos (variantes `111`, `222`) y un
escritor falso; `cambios.aplicar` debe mandar `poner_precio('MLM123', ['111','222'], ...)`. Hoy levanta
`ValueError ... sin PUT` (lo mismo que `G_meli_variantes.py`).

### Alternativas
- **Arreglar solo `_unidad_de_listing` para que lea `listing_miembro`**: arregla la escritura pero deja otros
  siete constructores a mano; el siguiente sitio nuevo copia el más cercano.
- **Pasar la unidad del catálogo por la decisión** (guardar miembros en `precio_decision`): la escritura usaría
  lo decidido aunque el mapeo cambie entre decidir y aplicar; el vivo ya manda (`precio_mercado.py:600-604`), así
  que la copia guardada no añade seguridad y sí columnas.

### Tamaño
`app/precio/puerto.py`, `app/meli/precio_mercado.py`, `app/spapi/precio_mercado.py`, `app/precio/cambios.py`,
`app/precio/corrida.py`, `app/precio/goals_write.py`, `tools/precio_sonda.py`, `tests/test_architecture.py`, una
prueba. Mecánico; cabe en una unidad.

---

## H-H4 · Re-mapear un ASIN deja las ventas pasadas en el producto anterior

### Causa raíz
El producto de una venta es un dato **derivado** de un mapa que se puede corregir, pero se guarda una sola
vez y no se puede volver a derivar:
- `app/ledger.py:374-378` deriva `product_id` del ASIN con el mapa de `listing` del momento.
- `app/ledger.py:505-509`: re-ingesta con `ON CONFLICT ... DO NOTHING`; el valor nuevo se descarta.
- `migrations/0001_initial.sql:1554-1556`: `ledger_append_only` prohíbe todo UPDATE.

La ingesta del ledger ya relee **todo** el origen cada día (`ledger.py:187-193`, sin filtro de fecha) y ya
calcula el producto correcto con el mapa corregido (`ledger.py:668-682`). Solo no puede guardarlo. La
corrección del mapa sí es legítima (`app/listings.py:333-335`).

### Uso primero
Salida de la ingesta del ledger (log de `refresh_costos.sh`):
```
ledger: insertadas=214 ventas_reasignadas=37 (amazon_mx: 37 ventas de B0X pasan de NH-ARRAS-16 a SET-ARRAS-22)
```
Telegram cuando `ventas_reasignadas > 0`: «37 ventas pasadas de B0X ahora cuentan para SET-ARRAS-22 (antes
NH-ARRAS-16), porque se corrigió su producto. La meta de ACoS de los dos productos cambia en el próximo ciclo.»

Lectores sin cambio: `v_margen_producto`, `v_margen_familia`, `v_target_margen_plataforma`,
`v_precio_venta_unidad`, `pantalla_productos.py`, etc. leen `ledger_event.product_id` y ven el valor
corregido.

### Forma
Una transición controlada, con el mismo patrón que `sku_cost_solo_cierra_vigencia`
(`0001_initial.sql:1613-1661`): de una venta solo se puede cambiar `product_id`, a un valor no nulo, y solo si
la misma transacción dejó su fila de auditoría.

```sql
-- 0084_<plan>_ledger_corrige_producto.sql
CREATE TABLE ledger_producto_correccion (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ledger_event_id  BIGINT NOT NULL REFERENCES ledger_event (id),
    producto_antes   BIGINT REFERENCES product (id),          -- NULL: venta sin producto
    producto_despues BIGINT NOT NULL REFERENCES product (id),
    ingest_run_id    BIGINT NOT NULL REFERENCES ingest_run (id),
    corregido_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ledger_correccion_cambia CHECK (producto_antes IS DISTINCT FROM producto_despues)
);
-- append-only: prohibir_mutacion() en UPDATE/DELETE/TRUNCATE.

DROP TRIGGER ledger_append_only ON ledger_event;
CREATE TRIGGER ledger_no_se_borra BEFORE DELETE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE FUNCTION ledger_solo_corrige_producto() RETURNS trigger ...
-- Permite el UPDATE solo si:
--   OLD.kind = 'sale' AND NEW.product_id IS NOT NULL
--   AND to_jsonb(NEW) - 'product_id' = to_jsonb(OLD) - 'product_id'   -- nada mas cambia
--   AND EXISTS (SELECT 1 FROM ledger_producto_correccion c
--                WHERE c.ledger_event_id = OLD.id
--                  AND c.producto_antes IS NOT DISTINCT FROM OLD.product_id
--                  AND c.producto_despues = NEW.product_id
--                  AND c.corregido_at = now())                     -- misma transaccion
-- Si no, RAISE restrict_violation.
CREATE TRIGGER ledger_solo_corrige_producto BEFORE UPDATE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION ledger_solo_corrige_producto();
GRANT INSERT ON ledger_producto_correccion TO app_ingest;
GRANT UPDATE (product_id) ON ledger_event TO app_ingest;   -- privilegio por columna
-- Reversa: aborta si ledger_producto_correccion tiene filas (patron 0076-0082);
-- si esta vacia, restaura ledger_append_only y borra lo nuevo.
```

```python
# app/ledger.py

class Reasignacion(NamedTuple):
    ledger_event_id: int
    antes: int | None
    despues: int


def reasignaciones(
    eventos: Sequence[EventoLedger],
    guardados: Mapping[tuple[str, str], tuple[int, int | None]],  # (platform, source_event_id) -> (id, product_id)
) -> list[Reasignacion]:
    """Ventas cuyo producto derivado HOY difiere del guardado. Solo ventas con
    source_event_id; un derivado None nunca pisa un guardado (regla 3)."""
    raise NotImplementedError

# sync_ledger, dentro de la misma transaccion (ledger.py:668-695), tras los INSERT:
#   guardados = SELECT id, product_id ... WHERE kind='sale' AND source_event_id = ANY(...)
#   for r in reasignaciones(ventas, guardados):
#       INSERT INTO ledger_producto_correccion (...)   # corregido_at = now()
#       UPDATE ledger_event SET product_id = r.despues WHERE id = r.ledger_event_id
#   ResultadoSync gana ventas_reasignadas; main imprime y avisa si > 0.
```

Invariantes: en la estructura, «solo cambia `product_id` de una venta, con auditoría» (trigger + privilegio
por columna); en la función pura, «un `None` no pisa un dato». Idempotencia: la segunda corrida ve el producto
ya corregido y no genera reasignaciones. Caída a la mitad: misma transacción que los INSERT; se revierte junto.

Atribución a publicación (`ledger_event_atribucion`): un re-mapeo de ASIN no cambia el `listing_id` (el ASIN es
el mismo listing), así que la atribución ya escrita sigue bien; las ventas que hoy no tienen producto se
atribuyen cuando la ingesta las corrige (`_atribuir_ventas`, `ledger.py:691`, recibe el producto nuevo).

### Datos que ya existen
- Re-mapeos: 0 en la primera corrida (reporte H); no medido después.
- Ventas Amazon con `product_id` NULL cuyo ASIN hoy sí está mapeado: **no medido**. La primera corrida tras el
  despliegue las corregirá todas a la vez y moverá márgenes. Paso aparte antes de encender (otro agente, solo
  lectura): correr `plan_eventos` sobre un snapshot con el mapa actual y contar contra lo guardado
  (reasignaciones por producto y por plataforma), dejando la salida en la evidencia del plan.

### Cambio de comportamiento en vivo
Sí: cambia el margen por producto y de plataforma que alimenta la meta de ACoS en live (`margen_producto`,
`tools/despliegue/interruptores.env` según reporte H). Las decisiones del motor congelan su meta en la fila
(`plans/orbit-06.md:983`), así que el replay sigue fiel. Encendido: primero la medición de arriba; si el número
es chico, directo; si es grande, el dueño lo ve antes. Deshacer: la auditoría guarda `producto_antes`; una
reparación con plan puede volver cada venta (la misma transición controlada, en sentido contrario).

### Prueba (escribe datos de producción)
Una en `tests/test_ledger_sync.py`: ingesta con el mapa B0X → NH; luego mapa B0X → SET y segunda ingesta. La
venta queda en SET con una fila en `ledger_producto_correccion`; una tercera corrida no agrega filas. Hoy queda
en NH.

### Alternativas
- **Capa de corrección + vista resuelta** (`v_ledger_event` con el último producto corregido) y mover todos los
  lectores a ella: respeta al pie de la letra la regla 5, pero obliga a recrear unas diez vistas, incluidas las
  que mandan la meta de ACoS en live, y mientras la migración no termine hay dos fuentes del producto de una
  venta (regla 2).
- **Guardar el ASIN por evento y derivar el producto al leer**: correcto en teoría, pero los mismos ~10 lectores
  cambian y MeLi resuelve por SKU, no por listing.
- **Corrección manual por herramienta** (`tools/corrige_ledger_remapeo.py --asin --producto` con go): no depende
  del día a día, pero la ingesta ya calcula el producto correcto cada día; una herramienta aparte es una segunda
  forma de hacer lo mismo y depende de que alguien se acuerde.

### Tamaño
Una migración con reversa, `app/ledger.py`, una prueba. Una unidad.

---

## Orden propuesto

1. **U1 compuerta (G-H1 + G-H3).** ALTO y es la única red contra un movimiento masivo. Hay que tenerla antes de
   que `precio_modo_global` vuelva a live y antes de U4, que abre escrituras nuevas en MeLi.
2. **U2 costos (H-H1).** ALTO. Hoy no está disparado (0 saltos), pero el día que pase apaga precios en las tres
   plataformas y congela una entrada de la meta de ACoS en live. No depende de nada.
3. **U3 espera de insumos (J-H3).** Chica. Toca `corrida.py` como U1, pero otra función: va después de U1 para no
   chocar en el mismo archivo.
4. **U4 unidad de MeLi (G-H2).** Abre escrituras reales en 33 publicaciones. Va después de U1 (fusible correcto
   para su primer día) y necesita el PUT de prueba más la reparación de frenados.
5. **U5 producto de la venta (H-H4).** Su disparador es raro y manual; necesita antes la medición de ventas sin
   producto.

Dependencias duras: U4 después de U1. G-H3 nunca después de G-H1 (por eso van juntas). U1 y U3 en serie por el
archivo. U2 y U5 son independientes entre sí y de las demás.

## Decisiones del dueño

1. **G-H1.** ¿Una retención que no sueltas sigue reteniendo cada día, con un aviso diario, hasta que la sueltes o
   la causa desaparezca? *Recomiendo sí: es lo que dice el diseño.* Y si al día siguiente quieren moverse menos
   productos que el umbral del fusible, ¿se aplican sin «Soltar»? *Recomiendo sí: es la misma regla de cualquier
   día.*
2. **H-H1, día siguiente.** ¿Aceptas que un costo nuevo entre a Orbit al día siguiente de su último cambio, y no el
   mismo día? *Recomiendo sí: es la única forma de cumplir «último valor del día» (D2) sin congelar SKUs. Ads no lo
   nota; precios decide un día con el costo anterior.*
3. **H-H1, historia corregida.** Si contabilidad corrige o borra historia ya publicada, ¿Orbit sigue al origen
   desde ayer sin reescribir lo viejo, y te avisa? *Recomiendo sí.* Esto cambia la letra de
   `plans/orbit-06.md:233-236` («costo distinto ⇒ skip contado») y del docstring de `app/costs.py:20-25`.
4. **H-H1, SKU ausente.** Si un SKU desaparece de contabilidad, ¿Orbit deja de usar su costo desde ayer, con un
   tope del 10 % por corrida que solo se salta a mano con `--acepto-cierre-masivo`? *Recomiendo sí.* Contradice la
   decisión «hallazgo 5» de `app/costs.py:522-525` (no cerrar la vigencia huérfana), que a su vez choca con la
   regla del mismo módulo de no estirar un costo sobre un hueco (`costs.py:269-270`).
5. **H-H4, retroactivo.** ¿Un re-mapeo de ASIN en Amazon corrige también las ventas pasadas? *Recomiendo sí: en
   Amazon un ASIN no cambia de producto físico, así que un re-mapeo es corregir un error.* ¿Y las ventas que hoy no
   tienen producto y ya se pueden mapear? *Recomiendo sí, después de ver la medición.* Esto es una excepción
   declarada a «append-only» de `ledger_event` (la regla 5 de `docs/CONTEXTO.md:195-198` habla de métricas; aquí
   cambia una etiqueta y queda auditoría completa).
6. **G-H2.** ¿Vuelves a sembrar con el mismo valor los goals de MeLi frenados por `api_error` después del PUT de
   prueba? *Recomiendo sí; es la única forma de reanudarlos sin tocar el freno.*
7. **J-H3.** ¿Hasta qué hora espera un repaso al insumo del día? *Recomiendo 21:00 UTC (el repaso de 19:10 toma
   la estimación de 18:45; 21:10 y 23:10 deciden con lo que haya).*

## Riesgos

- **Las migraciones nuevas no se aplicarían solas.** `migraciones_de` (`tools/despliegue/comun.sh:138-141`) solo
  aplica nombres `_(auto03|orbit07|claridad01)_`. El plan que entregue U2 y U5 debe agregar su etiqueta a ese
  patrón en la misma unidad, o el código sale sin su trigger y el checklist da verde (sospecha 3 del reporte J).
- **U2 cambia qué se considera costo validado** en código y en trigger a la vez; si sale solo uno, toda
  estimación de precios falla (`check_violation`). Van en el mismo despliegue y el trigger primero.
- **U4 en live**: el precio vivo de un item con variaciones se lee de `price` del item (`app/meli/precio_mercado.py:551-565`).
  Si las variaciones tienen precios distintos, el compare-and-set contra `p_actual` puede saltar o igualar todas;
  lo aceptado es «todas al precio de la de menor margen» (`spec:542-543`). El PUT de prueba lo confirma (inferido:
  no leí la respuesta de MeLi para variaciones con precios distintos).
- **U1 vuelve pegajosa toda causa falsa que quede**: un fee por tramos que cruza el umbral retiene hasta «Soltar».
  Mitigado por G-H3 y por el aviso diario.
- **U3 retrasa decisiones** de una plataforma entera hasta 6 h en días malos. El resumen diario de precios puede
  salir antes de que US decida (no leí cuándo sella `avisar` el resumen; inferido).
- **U5 mueve márgenes en live de golpe** la primera corrida si hay muchas ventas sin producto mapeables. Por eso la
  medición va antes.
- Las pruebas del repo dan fallas falsas en esta Mac (reloj de colima); se corren en Linux.
