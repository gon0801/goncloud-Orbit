# Diseño de arreglos: grupo ads-decision (candidato opus)

Código leído: copia `orbit-master` en `3c841f4`. Todas las citas `archivo:línea` son de esa copia
y las leí yo; lo que no leí va marcado como **inferido**. No hay `graphify-out/graph.json` en la copia,
así que usé `rg`. Corrí tres sondas puras (`E_piso_aprendido_subida.py`, `A_venta_reciente.py`,
`C_h2_pre_sin_venta.py`) y las tres reproducen el defecto en `3c841f4`.

## Restricciones que aplican a todas las unidades

1. **El replay tiene que seguir siendo fiel** (`app/optimizer/replay.py:17-22`: "el replay LEE lo
   congelado, JAMAS recalcula"). `politica.decide`, `hygiene.decide_hygiene`, `proteccion.evalua` y
   `economia_cambio.juicio_repeticion` son las mismas funciones que reproducen decisiones viejas.
   Un cambio en esas funciones puras tiene que cumplir una de dos: (a) no tocar ninguna decisión ya
   escrita, lo que se confirma con una consulta de solo lectura antes de desplegar, o (b) depender de
   un marcador que viaja congelado en el caso. Para (b) ya hay un patrón: `_objeto_versionado` en
   `app/optimizer/caso.py:965-974` y "Sin marcador en los insumos no hay protección" en
   `app/optimizer/proteccion.py:21-22`. Un cambio en las **lecturas** (lo que se congela) no afecta
   el replay.
2. **Interruptores existentes** que sirven de reversa: `ads_proteccion_venta` (M1, global,
   `proteccion.py:41-60`), `ads_bid_economia_<plat>` (M5/M6, `goals.py:883-899`),
   `ads_producto_economia_<plat>` (M2). M4 no tiene interruptor (`activacion-orbit07.md:54-57`).
3. Pruebas: solo en los 4 casos del repo. Todo lo de aquí mueve bids, pausas o negatives en Amazon,
   así que cuenta como "dinero" o "escribir en producción". Propongo una sola comprobación por
   hallazgo. Las pruebas con base corren en Linux (motor), no en esta Mac.

## Causas raíz que resultaron distintas o más precisas que en HALLAZGOS

- **E-S1.** HALLAZGOS dice que "la rama harvest sí compara contra `keywords_norm`". Pero
  `keywords_norm` (`hygiene.py:291`) son las keywords EXACT de la **campaña destino**
  (`cycle.py:1715,1730` → `hygiene.keywords_campana_destino`), no las del ad group de origen. La
  causa raíz es que `decide_hygiene` **no recibe ningún insumo** con las keywords propias del ad
  group (`hygiene.py:253-262`). Eso afecta también al harvest, que crea un negative EXACT en el
  origen (`apply_harvest.py:14-15`).
- **D-H1.** HALLAZGOS apunta a `cycle.py:1249-1254,3560-3564`. El primer lugar donde el sistema se
  equivoca es la cola: `apply_cola.py:1576-1578` (sin M1 no hay caché) y
  `apply_cola.py:1155-1163` (revalida con `proteccion=None`). Ahí se reinterpreta con la semántica
  histórica una fila que nació esperando por M1.
- **A-H1.** Son dos lugares. `proteccion.py:83-88` tira un ancla conocida cuando `pedidos is None`,
  y `proteccion_venta.py:190-201` borra el ancla cuando falta cobertura. Las dos cosas contradicen
  la "Regla" de `docs/evidencia/orbit-07/m1/politica-implementacion.md:9-11` ("pedido reciente
  conocido → espera").
- **B-H1.** HALLAZGOS dice que el código "cumple la letra" del plan. Hoy solo cumple la mitad:
  anclar la familia en el destino de plataforma (25.5) y bajar 0.5 por ciclo hasta 12 **es**
  interpolar entre dos economías, y `plans/orbit-07-motor-decisiones.md:237` dice "No interpolar
  entre economías incompatibles" y "resolver la economía completa de respaldo **en ese ciclo**". El
  arreglo que propongo se apega más a la letra que el código actual.
- **C-H1.** Además de la falta de muestra mínima, la causa es estructural: el freno se perpetúa a sí
  mismo. `subida_sin_economia` impide justo el cambio de bid que generaría evidencia nueva, y el
  episodio sigue "aplicable" hasta que el cambio sale de la ventana de 90 días
  (`lecturas_caso.py:716-722`).

---

## E-S1 (ALTO). El negative apaga una keyword EXACT de su propio ad group

**Causa raíz.** `hygiene.decide_hygiene` (`app/optimizer/hygiene.py:253-262`) no recibe las keywords
del ad group que decide. En la rama negative (`hygiene.py:315-338`), un término con `orders == 0`,
`clicks >= umbral_negative` y `cost >= piso` sale `kind="negative"` aunque su texto sea el de una
keyword EXACT de ese mismo ad group. El negative EXACT a nivel ad group bloquea la única búsqueda que
esa keyword puede servir: la apaga sin pausarla. Lo hace con umbrales de término (20 clics / 8 USD,
o el adaptativo) en lugar de los de pausa (100 / 40 USD) y sin pasar por M1. Pasa lo mismo en los
otros dos lugares que llaman: la revalidación de la cola (`apply_cola.py:1282-1290`, con
`keywords_existentes=frozenset()`) y el harvest (`apply_harvest_reconciliacion.py:231-239`). El
diseño v2 ya tiene esta regla para ASIN ("negativizar un ASIN propio es apagarte a ti mismo",
`hygiene.py:37-40`). Falta su gemela para texto.

**Uso primero.**

```python
# app/cycle.py, TX2, junto a evidencia_ad_groups (cycle.py:3249): UNA consulta por plataforma
exactas = hygiene.keywords_exactas_por_grupo(conn, platform)
comunes = dict(..., keywords_propias=exactas)          # cycle.py:3304-3322

# app/cycle.py, _procesa_grupo (cycle.py:2798)
resultados = hygiene.decide_hygiene(
    platform=platform, terminos=terminos, target_acos_pct=target,
    config_harvest=config_harvest, keywords_existentes=keywords,
    keywords_propias=keywords_propias.get(grupo_id, frozenset()),
    umbral_negative=corte_negativo.umbral, piso_negative=piso_neg.piso_cost,
)
# -> el término "arras de boda" sale skip 'termino_es_keyword_propia' (notes.skips.termino)

# app/apply_cola.py, _revalida_negative (apply_cola.py:1282)
propias = hygiene.keywords_exactas_por_grupo(conn, platform, solo_grupo=grupo).get(grupo, frozenset())
(resultado,) = hygiene.decide_hygiene(..., keywords_propias=propias, ...)
# -> la fila 2869, si sigue viva al liberar, se descarta con discard_motivo='termino_es_keyword_propia'
```

**Forma.**

```python
# app/optimizer/hygiene.py
MOTIVO_KEYWORD_PROPIA = "termino_es_keyword_propia"   # vocabulario cerrado (skip)

def decide_hygiene(
    *,
    platform: str,
    terminos: TerminosCortes,
    target_acos_pct: Decimal,
    config_harvest: ConfigHarvest | None,
    keywords_existentes: frozenset[str],   # campaña DESTINO (dedupe de harvest), sin cambio
    keywords_propias: frozenset[str],      # NUEVO, obligatorio y SIN default: EXACT del ad group de ORIGEN
    umbral_negative: int = LEGACY_NEGATIVE,
    piso_negative: Decimal | None = None,
) -> tuple[ResultadoTermino, ...]:
    # propias_norm = frozenset(_normaliza_texto(k) for k in keywords_propias)
    # Orden por término:
    #   (2) asin_like            -> skip (sin cambio)
    #   (2b) NUEVO: _normaliza_texto(t.search_term) in propias_norm
    #                            -> skip MOTIVO_KEYWORD_PROPIA (antes de dividir negative/harvest)
    #   (3) orders None ...      -> sin cambio
    raise NotImplementedError

_SQL_EXACTAS_POR_GRUPO = """
SELECT k.parent_id, k.keyword_text
  FROM ad_entity k
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group' AND ag.platform = k.platform
 WHERE k.kind = 'keyword' AND k.match_type = 'EXACT' AND k.platform = %s::platform
   {filtro_grupo}
"""

def keywords_exactas_por_grupo(
    conn: psycopg.Connection, platform: str, *, solo_grupo: int | None = None
) -> Mapping[int, frozenset[str]]:
    """Textos EXACT por ad group (LECTURA). Igual que keywords_campana_destino, no mira el estado
    a propósito: una keyword PAUSED o ARCHIVED que el dueño reactive no debe encontrarse
    ya negada. Un ad group sin keywords no aparece en el resultado (regla 3)."""
    raise NotImplementedError
```

- **Invariante que queda en la estructura.** `keywords_propias` es keyword-only y obligatorio. Si un
  llamador nuevo lo olvida, falla con `TypeError` al llamar. Esto cierra justo la forma en que nació
  el defecto: un llamador que pasa `frozenset()` sin darse cuenta. `replay.py:96-104` lo pasa vacío
  y explícito, con su razón: la decisión congelada existió solo porque el término no era propio, así
  que el replay sigue fiel.
- **Validación de borde.** La comparación es normalizada (`_normaliza_texto`, strip + casefold),
  igual que el dedupe. Las close variants de EXACT (plural, faltas de ortografía) no se igualan: un
  negative de una variante solo quita esa variante y no apaga la keyword.
- **Alcance del guard sobre el harvest** (decisión del dueño, Q5). Lo puse antes de dividir
  negative/harvest, así que también salta el harvest de un término que ya es keyword EXACT en su
  origen. Ese harvest crearía un negative EXACT en el origen (`apply_harvest.py:14-15`) y movería la
  keyword al destino con el `default_bid`. Si el dueño quiere conservar ese movimiento, el paso (2b)
  se mueve dentro de la rama `if t.orders == 0`.

**Datos que ya existen.** Son tres pasos aparte. Escribir en producción lo hace otro agente con plan.

1. **Urgente, antes del código: vetar la fila de la decisión 2869** (`pending_veto`, live) con la
   ruta existente `POST /api/ads-optimizer/veto` (`api_write.py:289-330`, rol admin). Si se libera
   antes del despliegue, `_revalida_negative` la aplicaría, porque hoy no conoce las keywords
   propias. El veto dura 30 días por clave de efecto y se puede repetir.
2. **Después del despliegue, revisar los negatives de las decisiones 2321 y 2322**, aplicados el
   16-sep. Primero hay que leerlos en Amazon: el `negativeKeywordId` está en el ack del intento
   `normal ok` (`apply.py:1602-1606`, `_negative_id_del_ledger`). Si siguen ahí, se revierten con
   la ruta existente `POST /api/ads-optimizer/reversa/negative {queue_id}`
   (`api_write.py:409-421` → `apply.reversa_manual(tipo="negative")` → `apply_cola.reversa_negative`,
   que borra con un registro tipo `reversa` exento de cuota). Es idempotente: una segunda reversa
   ya confirmada da `ReversaYaHecha` (`apply.py:1611-1623`). Si alguien ya lo borró a mano en Amazon,
   la reversa queda como fallo en el registro y no hace daño. Va **después** del despliegue para que
   el ciclo siguiente no vuelva a proponer el mismo negative.
3. Consulta de solo lectura para clasificar las 14 decisiones (estado de su fila y modo):
   la sonda `E_negativo_sobre_keyword_propia.sql`, sin cambios. Y para la Q5 del harvest: la misma
   consulta con `d.kind='harvest'`.

**Cambio de comportamiento en vivo.** Sí: el motor deja de crear negatives (y harvests, si el dueño
dice que sí en Q5) cuyo texto sea una keyword EXACT propia. Es directo al desplegar y solo resta
escrituras. Se deshace revirtiendo el despliegue. La reversa de 2321/2322 sí es una escritura a
Amazon que hay que autorizar (Q4).

**Prueba** (dinero, y la falla ya pasó en producción). Va en `tests/test_optimizer_hygiene.py`:
`decide_hygiene` con un término "Arras de Boda " (0 pedidos, 25 clics, cost sobre el piso) y
`keywords_propias=frozenset({"arras de boda"})` tiene que dar `kind None, motivo
'termino_es_keyword_propia'`. Hoy da `negative`, y con la firma actual ni siquiera compila
(`TypeError` por el argumento nuevo).

**Alternativas.**
- *Filtrar los términos en el ciclo antes de llamar a hygiene* (en `_procesa_grupo`, como el veto de
  `bloqueadas`). Pierde porque son tres llamadores (ciclo, cola, harvest) y cada uno tendría que
  acordarse de filtrar. Fue justo un llamador "que no sabía" el que creó el defecto.
- *Candado aguas abajo en `apply_cola`* (no ejecutar el negative). Pierde porque el estado no es
  legítimo: la decisión no debería existir, y además el replay la seguiría reproduciendo como válida.

**Tamaño.** `app/optimizer/hygiene.py`, `app/cycle.py` (TX2 y `_procesa_grupo`), `app/apply_cola.py`,
`app/apply_harvest_reconciliacion.py`, `app/optimizer/replay.py` y una prueba. Cabe en una sola
unidad verificable.

---

## E-H2 (MEDIO). Revertir una SUBIDA deja un `piso_aprendido` arriba del bid

**Causa raíz.** `Trayectoria.piso_aprendido` (`app/optimizer/caso.py:250-260`) toma el `bid_antes` de
**todo** `regreso_del_dueno` como "el bid desde el que se perdió el tráfico". Esa lectura solo es
cierta cuando el regreso deshace un recorte (dirección +1). La vista `v_cambio_bid`
(`migrations/0067_bids02_campana_ajuste.sql:76-86`) mete toda reversa `ok` como `regreso_del_dueno`,
y `_cambios_por_hoja` (`lecturas_caso.py:382-399`) le deriva `bid_antes` = el bid subido. El
resultado es un piso por encima del bid vigente, y R17 (`politica.py:402-406`) junto con `_paso`
(`politica.py:615`) bloquean todo recorte. `plans/bids-02-construccion.md:732` daba por hecho que
"regreso_del_dueno tiene dirección +1". `reversa_manual` acepta cualquier dirección
(`apply.py:1698-1724`). `regreso_por_desplome` siempre es +1, porque R1 solo corre después de un
recorte del motor (`politica.py:282`).

**Uso primero.** Desde el operador: revierte una subida en `POST /reversa/bid`, la keyword sigue
perdiendo dinero y el siguiente ciclo la recorta de nuevo (`Mover(-0.25, pierde_dinero_fuerte)` en la
sonda) en lugar de quedarse en `Mantener(piso_aprendido)`. Los llamadores no cambian:
`politica.py:277`, `politica.py:403` y `politica.py:606-616` siguen leyendo
`caso.trayectoria.piso_aprendido`.

**Forma.**

```python
# app/optimizer/caso.py, Trayectoria
@property
def piso_aprendido(self) -> Decimal | None:
    """El bid más alto desde el que hubo que REGRESAR HACIA ARRIBA (por desplome o por el dueño):
    a ese bid la hoja perdió su tráfico. Revertir una subida (dirección -1) no enseña ningún piso."""
    # pisos = [c.bid_antes for c in self.cambios
    #          if c.origen in ("regreso_por_desplome", "regreso_del_dueno") and c.direccion == 1]
    raise NotImplementedError
```

El invariante queda en la única derivación (una línea). La vista no cambia.

**Datos que ya existen.** Producción tiene 9 reversas manuales, todas de recortes (HALLAZGOS), así
que no hay reparación. Antes de desplegar va esta consulta de solo lectura; se espera 0 filas. Si
sale 0, ninguna decisión congelada cambia su replay.

```sql
SELECT d.id FROM apply_attempt a JOIN decision d ON d.id = a.decision_id AND d.kind = 'bid'
 WHERE a.tipo = 'reversa' AND a.resultado = 'ok' AND d.new_value > d.old_value;
```

**Cambio de comportamiento en vivo.** Hoy ninguno (no hay reversas de subidas). Es directo al
desplegar. Esta unidad tiene que ir **antes** de cualquier reversa manual de subidas, incluida la
limpieza que propone C-H4.

**Prueba** (dinero). Va en `tests/test_optimizer_caso.py` y toma la forma de la sonda
`E_piso_aprendido_subida.py`: `Trayectoria` con un cambio del motor 1.00→1.15 y un
`regreso_del_dueno` 1.15→1.00 tiene que dar `piso_aprendido is None`. Hoy da 1.15.

**Alternativas.**
- *Separar el origen en la vista* (`'reversa_de_subida'` para `new_value > old_value`). Pierde:
  necesita migración y reversa, un literal nuevo en `OrigenCambio` (`caso.py:120`), cambiar el
  deserializador (`caso.py:768`) y revisar `pantalla_danadas.py` y `apply.py:1856-1861`, que filtran
  por ese origen. Todo eso para una regla que vive en una sola propiedad.
- *Rechazar en `reversa_manual` la reversa de una subida.* Pierde porque quita un derecho del dueño
  (regla 7: toda acción con su reversa).

**Tamaño.** `app/optimizer/caso.py` y una prueba. Es la unidad más chica.

---

## A-H1 (MEDIO). Un día con `orders` NULL deja pasar la pausa pese a un pedido conocido

**Causa raíz.** Son dos lugares que hacen lo mismo: tirar un pedido conocido.
1. `app/optimizer/proteccion.py:83-88`: sin episodio, si `pedidos is None` (basta un día venenoso)
   se aplica la pausa **antes** de mirar `lectura.ancla`, aunque el SQL sí calculó el ancla de un día
   con pedido (`proteccion_venta.py:34-42`).
2. `app/proteccion_venta.py:190-201`: si falta cobertura, se borra el ancla de todas las hojas.

La regla de M1 es "un **pedido reciente conocido** espera" (`m1/politica-implementacion.md:9-11`). La
decisión 6 ("fuente incompleta sin episodio → la pausa sigue") solo cubre el caso sin pedido conocido.

## A-H2 (MEDIO). Una venta en el hueco entre la ventana de cortes y la reciente

**Causa raíz.** Las dos ventanas se anclan en relojes distintos. La de cortes termina en
`min(max(metric_date) DE LA HOJA − 3, D−10)` (`windows.py:413-421`, sellada en
`docs/CONTEXTO.md:102-107`). La reciente es fija en `D−9..D−1` (`proteccion_venta.py:32,159-161`).
Si la hoja deja de tener filas en A, los días `(A−3, D−10]` no los lee ninguna de las dos.

### Forma compartida (una unidad: A-H1 + A-H2)

La ventana de cortes es sellada y no se toca. Lo que cambia es la ventana reciente: **empieza al día
siguiente de que termina la ventana de cortes de esa hoja**. Los dos llamadores ya le pasan esa
ventana a `CacheProteccion.insumos` (`cycle.py:2264-2268`, `apply_cola.py:1155-1163`), así que el
fin de la ventana de cortes sigue teniendo una sola fuente (`fin_ventana_cortes`). Para una hoja
normal (`ventana_hasta = D−10`) la ventana reciente sale idéntica a hoy (`D−9..D−1`).

**Uso primero.**

```python
# app/cycle.py:2264 y app/apply_cola.py:1156, sin cambio en la llamada
proteccion = proteccion_cache.insumos(
    entidad_id, ventana_desde=cortes_hoja.window_start, ventana_hasta=cortes_hoja.window_end)
# A-H2: hoja con última fila 10-30 y venta 10-30, D=11-09 -> reciente = (10-27, 11-08]
#       -> ancla 10-30 -> esperar (antes: pause pause_umbral)
# A-H1: pedido 11-06 y orders NULL el 11-02 -> LecturaReciente(pedidos=None, ancla=11-06)
#       -> esperar (antes: aplicar venta_reciente_indeterminada)

# app/cycle.py:1250, la compuerta deja de usar hay_venta (se borra)
if pausa.proteccion.episodio is None and pausa.proteccion.reciente.ancla is None:
    return False
```

```python
# app/proteccion_venta.py
DIAS_LECTURA = 40  # = DIAS_VENTANA (30) + DIAS_MADUREZ_CORTES (10). Una hoja cuyo fin de cortes
                   # quede antes de D-41 no tiene filas en ~38 días: es inerte y no se decide
                   # (cycle.py:1925). Así hay margen de sobra sobre la regla de 14 días.

_SQL_RECIENTES = """   -- MISMA consulta única por plataforma, rango D-40..D-1, devuelve listas compactas
SELECT v.ad_entity_id,
       array_agg(v.metric_date ORDER BY v.metric_date) FILTER (WHERE v.orders > 0),
       array_agg(v.orders      ORDER BY v.metric_date) FILTER (WHERE v.orders > 0),
       array_agg(v.metric_date) FILTER (WHERE v.orders IS NULL OR v.orders < 0),  -- días venenosos
       max(v.observed_at),
       array_agg(DISTINCT v.metric_date)                                            -- cobertura
  FROM v_metric_latest v JOIN ad_entity k ON k.id = v.ad_entity_id
 WHERE k.platform = %s::platform AND k.kind IN ('keyword','product_target')
   AND v.metric_currency = %s AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.ad_entity_id
"""

@dataclass(frozen=True)
class _HistoriaReciente:            # privada: nadie fuera del módulo la ve
    con_pedido: tuple[tuple[dt.date, int], ...]
    venenosos: tuple[dt.date, ...]
    observado_al: dt.datetime | None

@dataclass(frozen=True)
class CacheProteccion:
    plataforma: str
    evaluado_el: dt.date
    _lee_desde: dt.date                     # D - DIAS_LECTURA
    _dias_plataforma: frozenset[dt.date]    # cobertura de plataforma en el rango leído
    _historia: dict[int, _HistoriaReciente]
    _episodios: dict[int, EpisodioProteccion]

    def insumos(self, hoja_id, *, ventana_desde, ventana_hasta) -> InsumosProteccion:
        # desde = min(ventana_hasta + 1d, D-9) si hay ventana; si no, D-9   (nunca más corta que hoy)
        # hasta = D-1
        # lectura = self._lectura(hoja_id, desde, hasta)
        raise NotImplementedError

    def _lectura(self, hoja_id, desde, hasta) -> LecturaReciente:
        # Fuera de rango (desde < _lee_desde): imposible para una hoja decidible.
        #   -> LecturaReciente(pedidos=None, ancla=<ancla en lo leído o None>, cubierta=False)
        # cubierta = _completo(desde, hasta, _dias_plataforma)
        # ancla    = la fecha más antigua de con_pedido dentro de [desde, hasta]
        #            (A-H1: NO se borra por veneno ni por falta de cobertura: un pedido visto es conocido)
        # pedidos  = None si (not cubierta) o hay un venenoso en [desde, hasta]; si no, la suma
        raise NotImplementedError

    def tiene_proteccion(self, hoja_id) -> bool:
        # episodio persistido, o un pedido conocido en D-9..D-1 (misma puerta de pujas que hoy;
        # ahora basada en el ancla, no en `pedidos > 0`)
        raise NotImplementedError
    # hay_venta se BORRA (su único llamador, cycle.py:1252, pasa a leer pausa.proteccion)
```

```python
# app/optimizer/proteccion.py, evalua(): solo cambia el bloque sin episodio y _abre_otro
#   if episodio is not None: ...                              (sin cambio)
#   if lectura.ancla is not None:                             # A-H1/A-H2: el pedido conocido manda
#       return _espera(lectura.ancla, _vence(lectura.ancla), MOTIVO_PROTEGIDA, abre=True)
#   if not lectura.cubierta or lectura.pedidos is None:       # decisión 6 intacta (sin pedido conocido)
#       return _aplica(MOTIVO_INDETERMINADA, None, None)
#   if lectura.pedidos == 0: return _aplica(MOTIVO_SIN_VENTA, None, None)
#   return _indeterminada(None, None)                         # pedidos>0 sin ancla: imposible por SQL
# _abre_otro: exige solo `lectura.ancla is not None` (deja de exigir cubierta y pedidos conocidos)
```

- **Invariante.** `LecturaReciente` conserva su forma (`caso.py:303-314`); el deserializador no
  cambia. Su significado pasa a ser "pedidos después de la ventana de cortes de esta hoja". La
  anchura de la ventana sale de un solo lugar (el `ventana_hasta` que llega). El ancla de un episodio
  abierto por una venta del hueco puede vencer el mismo día; eso no rompe nada: el trigger exige
  solo `vence_el = ancla + 10` (`0081:55-58`) y no pide `vence_el > abierta_el`. En el ciclo
  siguiente el episodio está vencido con el ancla aún fuera de la ventana madura, y da
  `indeterminada` (espera sin gasto) hasta que la hoja vuelve a tener filas
  (`proteccion.py:75-76`).
- **Idempotencia.** La escritura sigue siendo la inserción append-only con
  `ON CONFLICT DO NOTHING` (`proteccion_venta.py:59-64`). Si un ciclo se cae a la mitad, TX3 revierte
  la transacción entera (`cycle.py:3792-3817`).

**Datos que ya existen.** La tabla `proteccion_venta_reciente` no cambia (0 episodios según
HALLAZGOS). El cambio en `evalua` modifica el replay solo para casos congelados con
`pedidos=None` y `ancla` no nulo. Antes de desplegar va esta consulta de solo lectura; se espera 0
filas, porque producción no tiene filas con `orders` NULL en 30 días.

```sql
SELECT id FROM decision
 WHERE inputs->'caso'->'pausa'->'proteccion'->'reciente'->>'pedidos' IS NULL
   AND inputs->'caso'->'pausa'->'proteccion'->'reciente'->>'ancla' IS NOT NULL;
```

El comentario de la tabla (`0081:38`, "D-9..D-1") queda desactualizado. Se corrige en
`m1/politica-implementacion.md` y en `docs/CONTEXTO.md`; no hace falta una migración solo para un
comentario.

**Cambio de comportamiento en vivo.** Sí: menos pausas (las de hojas con venta reciente conocida),
con dinero real. Va directo al desplegar, bajo el interruptor existente `ads_proteccion_venta`; con
`off` vuelve la semántica histórica, después de D-H1. Se deshace revirtiendo el despliegue.

**Prueba** (dinero).
- A-H1, pura, en `tests/test_optimizer_proteccion.py`:
  `evalua(InsumosProteccion(reciente=LecturaReciente(pedidos=None, ancla=D-3, cubierta=True), episodio=None, ...))`
  tiene que dar `accion="esperar", abre_episodio=True`. Hoy da `aplicar`.
- A-H2, con base, en `tests/test_orbit07_m1.py`: hoja con última fila en A, pedido en A,
  `D = A+10`, ventana madura en cero pedidos y sobre el umbral. El ciclo live no crea la pausa y abre
  un episodio con `ancla = A`. Hoy crea `pause pause_umbral`.

**Alternativas.**
- *Anclar `fin_ventana_cortes` en el watermark de plataforma, no en la hoja.* Cierra el hueco de
  raíz, pero cambia una regla sellada (`CONTEXTO.md:102-107`) para todos los cortes (pausa, negative,
  harvest) y cambia las ventanas congeladas desde el día siguiente. Pierde por su alcance.
- *Abstener la pausa cuando la ventana de cortes de la hoja termina antes de D−10.* Es más simple,
  pero mete la fecha D en `bid._decide_pause`: hace falta un marcador para el replay y una regla nueva
  de pausa que no es de M1. Además deja sin pausar hojas esporádicas que sí gastan.

**Tamaño.** `app/proteccion_venta.py`, `app/optimizer/proteccion.py`, `app/cycle.py` (la compuerta
de `_proteccion_activa`, `cycle.py:1250-1254`) y dos pruebas. Cabe en una unidad.

---

## A-H3 (MEDIO, hoy solo shadow). M4 descarta en silencio un día reciente con una métrica NULL

**Causa raíz.** `app/productos_io.py:310`: `dias_recientes, _ = _dias_halo(...)` tira el indicador de
faltantes. `_dias_halo` (`productos_io.py:568-590`) salta el día si **cualquiera** de las cinco
métricas es NULL. Para preguntar "¿hubo venta?" solo importan `purchases30d` y `sales30d`. Un día con
compra conocida pero `attributed_sales_same_sku30d` NULL desaparece, y
`venta_reciente_producto` (`productos.py:395-420`) concluye "sin venta".

**Uso primero.**

```python
# app/optimizer/productos.py, motivos_economicos_pausa (v3, reciente=True)
motivos = motivos_economicos_pausa(entrada)          # -> ("venta_reciente_sin_medir",)
# app/apply_producto.py:182-187, la revalidación devuelve ese motivo como discard_motivo
```

**Forma.**

```python
# app/optimizer/productos.py, EntradaProducto (mismo patrón que el bloque H y M4: campo con default
# para leer entradas congeladas antes)
dias_recientes_sin_medir: tuple[dt.date, ...] = ()

def motivos_economicos_pausa(entrada, *, reciente: bool = True) -> tuple[str, ...]:
    # ... (sin cambio) ...
    # if reciente and entrada.dias_recientes_sin_medir and venta_reciente_producto(entrada) is None:
    #     motivos.append("venta_reciente_sin_medir")
    raise NotImplementedError

# app/productos_io.py
def _recientes_sin_medir(ventana, por_fecha) -> tuple[dt.date, ...]:
    """Días de la ventana reciente que _dias_halo descartó y que PUEDEN tener venta: compra o venta
    desconocida, o conocida > 0 en un día al que le falta otra métrica. Un día con compra=0 y venta=0
    conocidas no cuenta (no esconde una venta)."""
    raise NotImplementedError
# llamada en productos_io.py:310: dias_recientes_sin_medir=_recientes_sin_medir(ventana_reciente, mismo_sku)
```

No lo pongo en `impedimentos`: ahí bloquearía la elegibilidad de la meta M2 y dispararía el respaldo
de B-H1 por un dato de la pausa.

**Datos que ya existen.** No hay: las entradas congeladas no traen el campo y se leen con `()`.

**Cambio de comportamiento en vivo.** No. La pausa por producto está en shadow en MX y US. Va directo
al desplegar (M4 no tiene interruptor) y solo resta pausas. Tiene que estar desplegado **antes** de
que el dueño encienda la pausa por producto en live.

**Prueba.** No va prueba nueva en el repo, porque hoy no escribe en producción. La comprobación es la
sonda `A_venta_reciente.py` §P5: con el arreglo, `motivos_economicos_pausa` tiene que salir
`('venta_reciente_sin_medir',)`. Hoy sale `()`.

**Alternativas.**
- *Reescribir `dias_recientes` con días parciales (solo compra/venta).* Cambia la forma de
  `DiaProducto` que se congela y su deserializador. Pierde por tamaño.
- *Un solo booleano "faltó algo".* Bloquea también los días sin venta posible (compra=0 y venta=0
  conocidas con el costo NULL). Pierde por falsos bloqueos.

**Tamaño.** `app/productos_io.py`, `app/optimizer/productos.py`. Es una unidad chica.

---

## B-H1 (MEDIO, decisión del dueño). Al perder la meta de producto, la keyword salta a la economía de plataforma

**Causa raíz.**
- **Escenarios A y C** (el producto pierde elegibilidad por un motivo suyo, o la meta pasa a
  off/shadow). `app/cycle.py:3136-3138`: con `producto_directo_v1` y un previo `margen_producto`, el
  ancla de la familia es `familias.destino` (la plataforma, 25.5). Desde ahí,
  `resuelve_target_margen` (`goals.py:996-1000`) baja 0.5 por ciclo y tarda 26 ciclos en llegar al 12
  de la familia. La cola tiene la misma regla copiada a mano en
  `economia_revalidacion_io.py:151-152`.
- **Escenario B** (ledger rancio 4 días). `productos.meta_de_hoja` (`productos.py:656-660`) en M2
  devuelve `aplicado=None` ante cualquier `SinDato`, incluido `ledger_rancio`. La familia y la
  plataforma comparten la misma frescura, así que la familia también se abstiene. La hoja cae al
  25.5 de plataforma y R3 queda apagado.

**Uso primero.**

```python
# app/cycle.py:3136 (y economia_revalidacion_io.py:151, que pasa a usar el MISMO helper)
ultimo, tiene_previo = productos.ancla_familia(previo, familias.destino, version=familias.economia)
res, ganadora = g.resuelve_target_margen_familia(etiqueta, padre, fraccion, hoy, ultimo,
                                                 familias.destino, tiene_previo=tiene_previo)
# Escenario A: previo 4 (margen_producto), familia 12 -> 12 en ESE ciclo (antes 25.0 -> ... -> 12)

# app/cycle.py:2421, sin cambio en la llamada; cambia meta_de_hoja por dentro
resolucion = productos.meta_de_hoja(evaluacion.meta, ultimo=previo.target, destino=destino,
                                    tiene_previo=tiene_previo, version=productos_ciclo.modo_economia)
# Escenario B: SinDato('ledger_rancio'), previo 4 -> 4.5 (converge, freno kimi-H1), no 25.5
```

**Forma.**

```python
# app/optimizer/productos.py
def ancla_familia(
    previo: tuple[Decimal, str] | None,   # (target, procedencia) del último ciclo live de la hoja
    destino: Decimal | None,
    *,
    version: str,
) -> tuple[Decimal | None, bool]:
    """(ultimo, tiene_previo) del peldaño margen_familia. ÚNICA fuente para el ciclo y la cola.
    M2 con previo margen_producto -> (None, False): la familia aplica su propio derivado en ese
    ciclo, en cualquier dirección (plan 237: 'resolver la economía completa de respaldo en ese
    ciclo', 'no interpolar'). Si la familia no mide, None cae a la plataforma como hoy.
    auto03 -> (previo.target, procedencia == 'margen_familia'), sin cambio."""
    raise NotImplementedError

MOTIVOS_DATO_TRANSITORIO = frozenset({"ledger_rancio"})   # misma etiqueta que goals.ETIQUETA_ABSTENCION

def meta_de_hoja(meta, *, ultimo, destino, tiene_previo, version=ECONOMIA_AUTO03):
    # if isinstance(meta, SinDato):
    #     motivo = meta.motivos[0]
    #     if tiene_previo and (version == ECONOMIA_AUTO03 or motivo in MOTIVOS_DATO_TRANSITORIO):
    #         return goals.converge_al_destino(ultimo, destino, motivo, piso=Decimal(0))
    #     return goals.ResolucionMargen(None, None, motivo, False)
    # ... resto sin cambio
    raise NotImplementedError
```

- **Invariante.** Una sola función decide el ancla de la familia tras un previo de producto, y la usan
  el ciclo y la cola. Hoy son dos copias que ya difieren: la de la cola no mira la versión
  (`economia_revalidacion_io.py:151`).
- Con el escenario B en convergencia, la procedencia sigue siendo `margen_producto`, pero el
  equilibrio del producto está rancio y R3 queda apagado (`cycle.py:2975`). Es el mismo resultado que
  AUTO-03 en la sonda (`-12 %` en lugar de `+15 %`).

**Datos que ya existen.** Hoy hay 0 keywords bajo meta de producto
(`prearranque-2026-11-07/README.md:26-28`). `target_acos_ciclo` es append-only y el ciclo siguiente
recalcula. No hay reparación.

**Cambio de comportamiento en vivo.** Sí, en live MX y US (M2 encendido), cuando se dispare.
- Escenario A: la keyword pasa al 12 de su familia de una vez, en lugar de pasar 26 días arriba.
  Hacia abajo también es de una vez: si el producto tenía 30 y la familia 12, el target baja 30→12 en
  un ciclo. La puja se sigue moviendo como máximo −30 % por decisión, con R2 de 7 días.
- Escenario B: sin salto a 25.5.
- Va directo al desplegar. Se deshace con `ads_producto_economia_<plat>` = `auto03` (interruptor
  existente) o revirtiendo el despliegue.

**Prueba** (dinero). Hay que cambiar la expectativa de `tests/test_orbit07_m2.py:402-438`, que hoy
fija la caminata 29.5→29.0→28.5, es decir, el comportamiento que se corrige. La comprobación nueva
toma la forma de la sonda `B_respaldo_familia.py`: en el escenario A, el ciclo 1 tiene que dar
`target=12 (margen_familia)`. Hoy da 25.0.

**Alternativas.**
- *Anclar en `min(previo, destino)`* (lo que sugiere el reporte). Hacia arriba da lo mismo que
  `ultimo=None`, pero hacia abajo camina desde el producto: interpola y contradice la letra del plan.
- *Conservar la meta de producto vigente ante cualquier pérdida de elegibilidad* (otra lectura de
  `plan:234`, "conservar la economía vigente completa"). Pierde porque deja vivo un target de producto
  cuyo producto ya no es atribuible (halo de otro SKU).
- *Un tope "nunca por encima de la familia"* en la cascada. Es un candado aguas abajo sobre un estado
  que no es legítimo (el ancla mal elegida). Pierde.

**Tamaño.** `app/optimizer/productos.py`, `app/cycle.py` (`_margen_familia_de_hoja`),
`app/economia_revalidacion_io.py` y el ajuste de una prueba. Cabe en una unidad.

---

## C-H1 (MEDIO). Una semana ruidosa frena subidas del día x+17 al x+90

**Causa raíz.** Son tres cosas.
1. `app/optimizer/economia_cambio.py:114-116` frena si `post.venta <= pre.venta` sin muestra mínima en
   la ventana que se juzga (`[x+1, x+7]`). El mínimo de 20 clics de R15 (`politica.py:380-381`) se
   cuenta en `[x+1, D−3]` (`lecturas_caso.py:570`), otra ventana mucho más larga. La política
   documentada sí pide "20 clics al bid nuevo y ventanas completas"
   (`m6/politica-implementacion.md:40`).
2. Hay causas propias de Orbit que contaminan `[x+1, x+7]` y no invalidan el episodio. La principal:
   los negatives y harvests aplicados en el mismo ad group (el harvest crea un negative EXACT en el
   origen, `apply_harvest.py:14-15`). Las fuentes de invalidación (`lecturas_caso.py:829-839`) solo
   leen configuración de campaña, ajustes, precios y anuncios nuevos.
3. No hay vencimiento: el freno bloquea el cambio que traería evidencia nueva, y el episodio sigue
   aplicable (`lecturas_caso.py:716-722`) hasta que el cambio sale de `_SQL_CAMBIOS` (90 días).

**Uso primero.**

```python
# app/optimizer/politica.py:385, _freno_trayectoria
juicio = economia_cambio.juicio_repeticion(
    caso.trayectoria.episodio, target_acos_pct=caso.economia.target_acos_pct,
    factor=factor, clics_minimos=CLICS_NUEVOS)       # NUEVO: el 20 sale de su única fuente
# post con 3 clics / 0 ventas, episodio v2 -> sin_evidencia -> R16 decide (antes: frena 74 días)

# operador: activa la regla corregida por plataforma con el escritor existente
#   python -m app.cli productos-modo --platform amazon_us --bid-economia economia_cambio_v2
```

**Forma.**

```python
# app/optimizer/caso.py
VERSION_ECONOMIA_CAMBIO = "economia_cambio_v1"          # existente (replay de lo congelado)
VERSION_ECONOMIA_CAMBIO_V2 = "economia_cambio_v2"       # NUEVA
VERSIONES_ECONOMIA_CAMBIO = (VERSION_ECONOMIA_CAMBIO, VERSION_ECONOMIA_CAMBIO_V2)
# _episodio_economico_desde (caso.py:869): _literal(..., VERSIONES_ECONOMIA_CAMBIO, ...)

# app/optimizer/goals.py:883  (renombrada; un solo llamador en cycle.py)
def version_economia_cambio_desde_settings(settings, plataforma) -> str | None:
    """Ausente -> None (apagada); v1 o v2 -> esa versión; otro valor -> ValueError."""
    raise NotImplementedError

# app/lecturas_caso.py
def lee_plataforma(conn, plataforma, decidido_el, *, economia, visto_el=None,
                   version_economia: str | None = None) -> LecturasPlataforma:   # antes con_economia: bool
    # _episodio_economico estampa version=lect._version_economia
    raise NotImplementedError

_SQL_CORTES_GRUPO = """   -- 5ª fuente de invalidación, en el MISMO lote de eventos_grupo (lecturas_caso.py:837)
SELECT d.ad_entity_id, a.finished_at
  FROM apply_attempt a
  JOIN decision d  ON d.id = a.decision_id AND d.kind IN ('negative','harvest')
  JOIN ad_entity g ON g.id = d.ad_entity_id AND g.kind = 'ad_group'
 WHERE g.platform = %s::platform AND a.resultado = 'ok' AND a.tipo IN ('normal','reversa')
   AND a.finished_at >= %s AND a.finished_at <= %s
"""

# app/optimizer/economia_cambio.py
def juicio_repeticion(episodio, *, target_acos_pct: Decimal, factor: Decimal,
                      clics_minimos: int) -> JuicioRepeticion:
    # if not _utilizable(episodio): return SIN_EVIDENCIA      (_utilizable acepta v1 y v2)
    # ...
    # if episodio.version == V2 and post.clics < clics_minimos: return SIN_EVIDENCIA   # C-H1.1 (subida Y recorte)
    # return _JUICIOS[episodio.version](episodio, pre, post, target_acos_pct, factor)
    raise NotImplementedError

def _juicio_v1(...): ...   # el cuerpo actual, literal, para el replay de episodios v1
def _juicio_v2(...): ...   # recorte igual; subida con C-H2 (abajo)
```

- **Por qué hay versión y no un cambio directo.** El juicio decide bids congelados desde el 11-08 y el
  replay tiene que reproducirlos. Sigue el patrón del repo (`auto03`/`producto_directo_v1`,
  pausa v2/v3). Las fuentes de invalidación nuevas (punto 2) y C-H3 cambian la **lectura**, que queda
  congelada en `estado`, así que valen para v1 y v2 sin versión.
- **Vencimiento (punto 3): decisión del dueño (Q2).** Si dice que sí, va en la lectura (sin tocar el
  replay): en `_episodio_economico`, `aplicable=False, motivo_aplicabilidad='evidencia_vencida'` cuando
  `d > x + 17 + N`.

**Datos que ya existen.** Los episodios no se persisten, viven congelados en `decision.inputs`. No hay
reparación.

**Cambio de comportamiento en vivo.** Sí, en bids live.
- Las fuentes nuevas y C-H3: directo al desplegar. Solo vuelven no utilizables algunos episodios, así
  que R16 decide como antes de ORBIT 07.
- El juicio v2: se enciende cuando el dueño (o el agente con plan) escribe `economia_cambio_v2` por
  plataforma. Se deshace con `economia_cambio_v1` u `off`.

**Prueba** (dinero). Pura, en `tests/test_orbit07_m5m6.py`. Toma el caso de la sonda
`C_m6_politica.py` §2: un episodio v2 maduro y aplicable, con `post` de 3 clics y 0 ventas, `pre` con
1 venta de 30 y R6 pidiendo +15 %. `decide` tiene que dar `Mover(+0.15)`. Hoy (y con v1) da
`Mantener(subida_sin_economia)`.

**Alternativas.**
- *Contar el mínimo de 20 clics de R15 en `[x+1, x+7]`.* Cambia R15 para todos los casos, no solo para
  M6. Pierde.
- *Solo el vencimiento, sin muestra mínima.* Deja vivo el freno por ruido durante el plazo. Pierde
  como arreglo único; puede ir como complemento (Q2).

**Tamaño.** C-H1 + C-H2 + C-H3 son dos unidades:
- **U6a, lectura:** `app/lecturas_caso.py` (fuente nueva + C-H3).
- **U6b, juicio v2:** `app/optimizer/economia_cambio.py`, `app/optimizer/politica.py` (pasa
  `clics_minimos`), `app/optimizer/caso.py`, `app/optimizer/goals.py`, `app/cycle.py` (el selector
  pasa la versión a `lee_plataforma`), `app/cli_productos.py:53` (agrega `economia_cambio_v2` a
  `choices`) y `docs/evidencia/orbit-07/activacion-orbit07.md`.

---

## C-H2 (MEDIO). El freno de subida no actúa si la semana previa tuvo 0 ventas

**Causa raíz.** `app/optimizer/economia_cambio.py:112-113`: `if pre.venta <= 0: return SIN_EVIDENCIA`
corre **antes** de la regla "no creció la venta" (`:114-116`) y de la de contribución (`:117-120`).
La política dice que se frena si no creció la venta (`m6/politica-implementacion.md:19-22`): de 0 a 0
no creció.

**Uso primero.** Igual que en C-H1. Con la semana previa en 0 ventas y la posterior en 0 ventas con
≥20 clics, el episodio v2 da `Mantener(subida_sin_economia)`.

**Forma.**

```python
def _juicio_subida_v2(episodio, pre, post, target_acos_pct) -> JuicioRepeticion:
    # (el mínimo de clics ya se comprobó en juicio_repeticion)
    # if post.venta <= pre.venta:                       -> frena   (ahora ANTES del pre.venta <= 0)
    # if contribucion_pre/post conocidas:               -> sin_evidencia si mejoró; si no, frena
    # if pre.venta <= 0:                                -> sin_evidencia (no hay ACoS previo que comparar)
    # ACoS post <= target o <= ACoS pre                 -> sin_evidencia; si no, frena
    raise NotImplementedError
```

El recorte no cambia: M6 nunca frena recortes (`economia_cambio.py:84-102`).

**Datos que ya existen.** No hay.

**Cambio de comportamiento en vivo.** Hay más frenos de subidas en hojas de poco volumen. Llega con
el juicio v2 (U6b); se enciende y se deshace igual que C-H1.

**Prueba** (dinero). Pura, en `tests/test_orbit07_m5m6.py`. Toma la forma de la sonda
`C_h2_pre_sin_venta.py`, con 25 clics post: el episodio v2 con pre 0 ventas/10 de gasto y post 0
ventas/20 de gasto tiene que dar `Mantener(subida_sin_economia)`. Hoy da `Mover(+0.15)`.

**Alternativas.** *Tratar `pre.venta == 0` como ACoS infinito.* Mete un valor inventado (regla 3).
Pierde.

**Tamaño.** Va dentro de U6b.

---

## C-H3 (MEDIO). Un episodio contaminado se vuelve "maduro" al pasar D−90

**Causa raíz.** Los eventos de puja del episodio salen solo de `cambios`
(`lecturas_caso.py:714`), que se leen desde `D−90` (`lecturas_caso.py:797`, `_SQL_CAMBIOS:149-158`).
Cuando `x−7 < D−90`, un cambio dentro de `[x−7, x−1]` deja de verse y el episodio pasa a `maduro`
(`lecturas_caso.py:727-737`).

**Uso primero.** Ningún llamador cambia. El episodio sale con `estado='incomparable',
motivo='historia_incompleta'` en la auditoría (`notes`) y R16 decide.

**Forma.**

```python
# app/lecturas_caso.py
MOTIVO_ECONOMIA_HISTORIA_INCOMPLETA = "historia_incompleta"

@dataclass(frozen=True)
class LecturasPlataforma:
    ...
    _historia_desde: dt.date      # NUEVO: el `desde` con el que se leyó _SQL_CAMBIOS (d - DIAS_LOOKBACK)

# _episodio_economico, después de `madura` y antes de `en_ventana`:
#   elif desde < lect._historia_desde:
#       estado, motivo = "incomparable", MOTIVO_ECONOMIA_HISTORIA_INCOMPLETA
```

El invariante queda en la lectura: un episodio solo es comparable si toda su ventana `[x−7, x+7]` cae
dentro de la historia que se leyó. La vida útil del episodio queda en `x+17..x+83`.

**Datos que ya existen.** No hay.

**Cambio de comportamiento en vivo.** Mínimo: entre `x+84` y `x+90`, R16 decide en lugar de M6. Va
directo al desplegar (U6a).

**Prueba** (dinero). En `tests/test_lecturas_caso.py`, con la forma de la sonda `C_m5_episodio.py` §2:
con `D = x+88` y un cambio en `x−3`, el estado tiene que dar `incomparable`. Hoy da `maduro`.

**Alternativas.** *Leer `cambios` desde `D−97`.* Esos cambios entrarían también a la `Trayectoria`
(R2, R14-R17 y `piso_aprendido` con semántica de 90 días), o habría que separar dos listas. Pierde
por tamaño y por riesgo.

**Tamaño.** Va dentro de U6a.

---

## C-H4 (MEDIO, decisión del dueño). Una hoja que se pausaba recibe +15 % durante la espera de M1

**Causa raíz.** `app/optimizer/politica.py:205-228`: si la pausa por cero pedidos se suprime por
venta reciente (`_motivo_bloqueado` igual a `venta_reciente_protegida` o
`venta_reciente_indeterminada`, `bid.py:261-267`), el juicio sigue normal. R6 (`politica.py:339`)
puede subir con pedidos de D−90..D−41, aunque la ventana madura de cortes diga "0 pedidos con gasto
sobre el umbral". M1 pidió **esperar**, no subir (`m1/politica-implementacion.md:9-11`).

**Uso primero.** Para una hoja protegida cuyo juicio da `bajo_target`,
`decide(caso)` devuelve `Mantener('subida_en_espera_de_venta')` (sonda `C_m1_subida_coherente.py`).
Los recortes siguen autorizados (R8 los frena por los pedidos inmaduros, como hoy) y R1 (regreso por
desplome) también.

**Forma.**

```python
# app/optimizer/caso.py, InsumosProteccion: marcador congelado, mismo patrón que M1
frena_subidas: bool = False      # ausente en casos viejos -> semántica de su era
# _proteccion_desde (caso.py:1000-1025): _objeto_versionado(viejo=claves actuales, nuevo=claves | {"frena_subidas"})

# app/proteccion_venta.py, CacheProteccion.insumos: frena_subidas=True

# app/optimizer/politica.py
MOTIVO_SUBIDA_EN_ESPERA = "subida_en_espera_de_venta"
# después de `factor, motivo, nivel = fallo` (politica.py:270):
#   if factor > 0 and _subida_en_espera(caso.pausa.proteccion, _motivo_bloqueado):
#       return Mantener(MOTIVO_SUBIDA_EN_ESPERA)
def _subida_en_espera(proteccion: InsumosProteccion | None, motivo_bloqueado: str | None) -> bool:
    raise NotImplementedError
```

**Datos que ya existen.** Puede haber bids ya aplicados que subieron durante una espera desde el
11-08. Consulta de solo lectura: decisiones `bid` live con `new_value > old_value` y
`inputs->'caso'->'pausa'->'proteccion'` no nulo, cuya ventana de cortes congelada tenga 0 pedidos
con clics ≥ el umbral congelado. Si salen filas, revertirlas es decisión del dueño, con
`POST /reversa/bid`, **después** de E-H2. No hay reparación automática.

**Cambio de comportamiento en vivo.** Sí: menos subidas en hojas protegidas, con dinero real. Va
directo al desplegar (el marcador hace que solo afecte casos nuevos). Se deshace con
`ads_proteccion_venta` off (que apaga todo M1, después de D-H1) o revirtiendo el despliegue.

**Prueba** (dinero). Pura, en `tests/test_optimizer_proteccion.py`, junto al caso de
"puja autorizada durante la espera" (`:191`). Con la forma de la sonda `C_m1_subida_coherente.py`, la
hoja tiene que dar `Mantener('subida_en_espera_de_venta')`. Hoy da `Mover(+0.15)`.

**Alternativas.**
- *`Mantener` total durante la espera.* Es justo lo que el código evita a propósito
  (`politica.py:225-228`: "congelaría la hoja"). También bloquearía R1.
- *Que R6 solo use pedidos de la ventana de cortes.* Cambia R6 para todas las hojas. Pierde.

**Tamaño.** `app/optimizer/caso.py`, `app/proteccion_venta.py`, `app/optimizer/politica.py` y una
prueba. Va después de A-H1/A-H2 porque toca el mismo `insumos`.

---

## D-H1 (MEDIO). Apagar M1 (o `rollback.sh`) aplica de golpe las pausas en espera

**Causa raíz.** Una fila `released` que **nació esperando** por M1 (`espera_proteccion`) sobrevive a
un cambio de semántica. Con M1 apagado, `libera_vencidos` corre sin caché
(`apply_cola.py:1576-1578`), `_revalida_pause` decide con `proteccion=None`
(`apply_cola.py:1155-1174`) y sale `pause_umbral`. `vendio_en_ventana` no la salva porque la venta
reciente está fuera de la ventana madura. Al final cobra cuota y manda el PUT
(`apply_cola.py:1664-1679`). Con `rollback.sh` hacia un código sin M1 pasa lo mismo, y ese código ya
no se puede arreglar.

**Uso primero.**

```bash
# El operador corre la reversa documentada (activacion-orbit07.md:64-72), sin cambios en el comando
docker exec orbit-app-1 python -m app.cli productos-modo --platform amazon_us --proteccion off
# salida nueva: "config_version N: ...; 7 pausas en espera por venta reciente descartadas (proteccion_apagada)"
# El ciclo siguiente vuelve a decidir con la semántica histórica: cada pausa que siga calificando
# nace pending_veto con 48 h de veto, visible en la pantalla.
```

```python
# app/apply_cola.py, libera_vencidos (apply_cola.py:1574-1578)
if not proteccion_activa:
    descartadas.extend([MOTIVO_PROTECCION_APAGADA] * descarta_esperas_proteccion(conn, platform))
filas = [FilaCola(*f) for f in conn.execute(_SQL_VENCIDAS, (platform, ahora)).fetchall()]
```

**Forma.**

```python
# app/apply_cola.py: dueño de las transiciones de apply_queue
MOTIVO_PROTECCION_APAGADA = "proteccion_apagada"

_SQL_DESCARTA_ESPERAS = """
UPDATE apply_queue q
   SET estado = 'discarded', discarded_at = now(), discard_motivo = %s
  FROM ad_entity e
 WHERE e.id = q.ad_entity_id AND e.platform = %s::platform
   AND q.estado = 'released' AND q.modo = 'live' AND q.kind = 'pause'
   AND EXISTS (SELECT 1 FROM decision_sin_aplicar s
                WHERE s.decision_id = q.decision_id AND s.motivo = 'espera_proteccion')
RETURNING q.id
"""

def descarta_esperas_proteccion(conn: psycopg.Connection, platform: str) -> int:
    """Descarta, antes del claim (sin cuota ni HTTP), las pausas released que esperaban por venta
    reciente. Es idempotente: la segunda corrida no encuentra filas. Si el llamador revierte su
    transacción, la fila sigue released y la próxima corrida la vuelve a encontrar."""
    raise NotImplementedError

# app/cli_productos.py, corre(): si args.proteccion == "off"
#   resultado = config_write.guarda_config(conn, ..., proteccion_venta="off", confirma=False)
#   n = sum(apply_cola.descarta_esperas_proteccion(conn, p) for p in PLATAFORMAS_MONEDA)
#   conn.commit()          # config nueva + descartes en UNA transacción
#   (mismo patrón que app/productos_modo.py:101, confirma=False)
```

- **Por qué hay dos llamadores para una sola función.** `libera_vencidos` cubre cualquier forma de
  apagar M1, incluida una edición a mano de la config. La CLI cubre `rollback.sh`: la receta ya exige
  apagar con la CLI nueva antes de revertir el código (`activacion-orbit07.md:76-80`), así que al
  revertir ya no quedan filas en espera.
- `discard_motivo` no tiene CHECK (`migrations/0002_apply.sql:82`) y el trigger permite
  `released → discarded` en filas live (`0002:245-261`). No hace falta migración. Que `app_admin`
  tenga UPDATE sobre `apply_queue` es **inferido**: el veto lo usa con el DSN admin
  (`api_write.py:296-300`).

**Datos que ya existen.** Para medir las filas en espera hoy está la consulta del reporte D, de solo
lectura. No hay reparación mientras M1 siga encendido.

**Cambio de comportamiento en vivo.** Solo cuando alguien apaga M1: esas pausas no salen en bloque;
vuelven a proponerse con veto de 48 h. Va directo al desplegar. Se deshace revirtiendo el despliegue.
Hay que agregar un párrafo a la receta de reversa en `activacion-orbit07.md`.

**Prueba** (escribe en producción: una pausa en Amazon). Con base, en `tests/test_orbit07_m1.py`.
Parte del caso "cola que espera sin cuota" y agrega una `config_version` sin `ads_proteccion_venta`.
Corre otro ciclo live. La fila tiene que quedar `discarded/proteccion_apagada`, sin PUT y sin cobro
de cuota. Hoy pasa a `applying/applied`.

**Alternativas.**
- *Mantener la protección de la cola para las filas que ya esperaban, aunque M1 esté apagado.* Sería
  una regla apagada que sigue mandando, y no cubre `rollback.sh`. Pierde.
- *Solo documentar en la receta que se separe la reversa de M1.* No cubre `rollback.sh` ni a quien
  apague M1 por otra razón. Pierde como único arreglo.

**Tamaño.** `app/apply_cola.py`, `app/cli_productos.py`, `docs/evidencia/orbit-07/activacion-orbit07.md`
y una prueba. Cabe en una unidad.

---

## Orden propuesto de las unidades

| # | Unidad | Hallazgos | Por qué en este lugar |
|---|---|---|---|
| R1 | Vetar la fila de la decisión 2869 (sin código) | E-S1 | Su veto de 48 h puede vencer antes de cualquier despliegue. |
| U1 | Guard de keyword propia en hygiene | E-S1 | Es ALTO, ya pasó en producción y es independiente. |
| R2 | Revisar y revertir los negatives de 2321/2322 | E-S1 | Después de U1, para que el motor no los vuelva a proponer. |
| U2 | `piso_aprendido` solo de regresos hacia arriba | E-H2 | Es una línea y tiene que ir antes de cualquier reversa de subidas (limpieza de C-H4). |
| U5 | Descartar esperas al apagar M1 | D-H1 | Deja segura la vía de escape **antes** de cambiar el comportamiento de M1/M6. |
| U3 | Lectura reciente desde el fin de cortes; el ancla manda | A-H1, A-H2 | Es dinero en live hoy. |
| U7 | Sin subidas durante la espera | C-H4 | Toca el `insumos` de U3; decisión del dueño (Q3). |
| U6a | Fuente de negatives/harvest y historia incompleta | C-H1 (punto 2), C-H3 | Es de lectura; no necesita interruptor. |
| U6b | Juicio v2 (muestra mínima y orden de C-H2) | C-H1 (punto 1), C-H2 | El dueño lo enciende con `economia_cambio_v2`. |
| U8 | Ancla de familia y convergencia por ledger rancio | B-H1 | Espera la respuesta del dueño (Q1); hoy no se dispara. |
| U4 | M4: día reciente sin medir | A-H3 | Es shadow; tiene que estar antes de encender en live la pausa por producto. |

**Dependencias.**
- U7 depende de U3 (mismo `CacheProteccion.insumos`).
- U6b va después de U6a: ambas tocan `lecturas_caso.py` y así se evitan conflictos.
- Revertir subidas de C-H4 requiere U2.
- R2 requiere U1.
- U5 va antes de U3, U7 y U6 por la vía de escape.
- Las demás unidades son independientes entre sí.

## Decisiones que solo el dueño puede tomar

- **Q1 (B-H1).** Cuando una keyword pierde la meta de su producto, ¿salta en ese ciclo a la economía
  de su familia (12 % en el ejemplo, hacia arriba o hacia abajo) en lugar de pasar 26 días cerca del
  25 % de plataforma? Y si el ledger se queda viejo, ¿se queda cerca de su meta de producto (4 → 4.5
  → 5…) en lugar de saltar a 25.5? **Recomiendo sí a las dos.** La primera se apega al plan ("resolver
  el respaldo en ese ciclo, sin interpolar"); la segunda es el mismo freno que la plataforma ya tiene
  ante datos malos.
- **Q2 (C-H1).** ¿Cuánto debe durar el freno `subida_sin_economia` una vez que madura el episodio?
  Hoy dura hasta 74 días (hasta x+83 con C-H3). **Recomiendo 14 días después de la madurez (x+31)**,
  el mismo plazo de "salida por tiempo" que el motor ya usa al invertir dirección (`DIAS_SALIDA`,
  `politica.py:35`). Así no se inventa un número nuevo.
- **Q3 (C-H4).** Mientras una pausa espera por venta reciente, ¿el motor tiene prohibido subir la puja
  de esa keyword? **Recomiendo sí.** La pausa ya dice que no vende en la ventana madura; subir con
  pedidos de hace 41 a 90 días contradice esa misma evidencia.
- **Q4 (E-S1, datos).** ¿Se revierten en Amazon los negatives de las decisiones 2321 ("arras de boda":
  9 pedidos y 1,094.96 USD en los 71 días previos) y 2322, si siguen activos? **Recomiendo sí**, por la
  ruta de reversa existente, después de U1. ¿Se veta ya la 2869? **Sí, hoy.**
- **Q5 (E-S1, alcance).** ¿El mismo guard también impide el harvest de un término que ya es keyword
  EXACT en su ad group de origen? Ese harvest negaría la keyword en su origen y la recrearía en el
  destino con el bid por defecto. **Recomiendo sí**, porque no hay nada que cosechar de una keyword que
  ya existe. Si el dueño usa ese harvest para mover keywords a propósito, el guard queda solo en la
  rama negative.
- **Q6 (E-H2, sin arreglo propuesto).** Cuando el dueño revierte una subida, ¿el motor debe tener
  prohibido volver a subirla (un "techo aprendido")? Hoy puede volver a subir pasados 14 días o con 20
  clics (R14). **Recomiendo no agregar esa regla.** El dueño ya tiene el techo del goal.

## Riesgos

- **Que el replay deje de ser fiel.** E-H2 y A-H1 cambian funciones puras sin marcador. Eso depende de
  que las consultas previas den 0 filas, como indica la evidencia de producción (0 reversas de
  subidas; 0 filas con `orders` NULL en 30 días). Si alguna devuelve filas, esa unidad necesita un
  marcador congelado (`_objeto_versionado`) antes de desplegarse.
- **A-H2 y la cota de lectura.** `DIAS_LECTURA = 40` depende de que una hoja sin filas por ~38 días sea
  inerte y no se decida (`cycle.py:1925`). Si alguien relaja la regla de inertes, una hoja fuera de
  rango cae en `cubierta=False` y, por la decisión 6, la pausa sigue (el lado inseguro). El margen
  (40 contra 17) es amplio, pero es una lista que se sincroniza a mano con `v_entidad_inerte`.
- **B-H1 hacia abajo.** Si el producto tenía una meta alta y la familia una baja, el target baja de una
  vez. Puede venir una ronda de recortes (cada uno de −30 % como máximo y con 7 días de espera). Es lo
  que dice el plan, pero es un efecto visible.
- **D-H1.** Al apagar M1, el dueño ve de golpe una tanda de pausas propuestas con veto de 48 h. Es lo
  que se busca (visibles y vetables en lugar de aplicadas), pero conviene avisarlo en la receta.
- **U6b no hace nada hasta que se enciende.** Mientras el dueño no escriba `economia_cambio_v2`, C-H1
  (punto 1) y C-H2 siguen como hoy. Queda `economia_cambio_v1` vivo como segunda versión, que solo
  hace falta para el replay; se puede retirar cuando ya no haya decisiones v1 en la ventana de
  auditoría.
- **E-S1 y la espera de 7 días.** Revertir 2321/2322 no limpia la espera del ad group ("una reversa NO
  limpia el cooldown", `apply.py:1688-1690`): los términos de ese grupo quedan congelados 7 días. No
  cuesta dinero; solo retrasa otras decisiones de ese grupo.

## Siguiente paso de implementación

R1 (vetar la 2869) y luego U1. Lo primero que se escribe es el parámetro obligatorio
`keywords_propias` en `decide_hygiene`, con la prueba de "arras de boda" fallando primero.
