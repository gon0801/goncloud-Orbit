# TEMPORADA 01: invertir según la temporada (contrato de diseño v5)

Contrato de diseño del plan `plans/temporada-01.md`. Donde el plan y este documento difieren, vale el
plan. Repo leído en `289f9dd`; las citas `archivo:línea` se revisaron contra `de895d18` el 2026-11-12.
Las piezas de ARREGLOS 01 que todavía no están en el código (P, T, U, V, W, X) se citan por su plan
(`plans/arreglos-01.md`) y se marcan así: *(contrato de ARREGLOS 01)*.

Este documento es el único contrato. No hay anexos que manden sobre el cuerpo.

---

## Problema

David vende productos de temporada y todos comparten los mismos meses buenos y malos. Quiere que
Orbit invierta según el mes:

- al entrar a un mes bajo, bajar todos los bids un porcentaje fijo (30 %), una sola vez;
- al entrar a un mes normal, o unos días antes de un mes alto, regresar los bids a como estaban;
- en los meses altos, competir más fuerte, con meta de ACoS de 25 %;
- subir rápido los bids recortados cuando la evidencia lo justifica, con topes;
- los presupuestos solo se proponen y él los aprueba;
- lo último que haga él siempre gana;
- calendario y parámetros editables en Ajustes;
- Amazon MX y Amazon US encendidos juntos desde el principio.

Lo que hace que la forma no sea obvia, comprobado en el código:

1. **La meta que gobierna no es el setting de 20 %.** La cascada pone el margen de la plataforma antes
   que el setting (`app/optimizer/goals.py:319-328`). La meta de margen es una fracción del margen neto
   (`goals.py:983`), y la fracción vale `0.5` en MX y `0.8` en US (medido en `config_version` el 2026-11-12;
   `ads_target_fraccion_margen_amazon_{mx,us}`). Medido el 2026-11-12
   en `target_acos_ciclo`: las 136 keywords de MX deciden con `margen_plataforma` = 20.08 % y las 89
   de US con 19.16 %. El equilibrio que recibe el motor es el margen neto (`app/cycle.py:3318`), así que
   el 2026-11-12 era 40.26 % en MX y 23.81 % en US (`optimizer_cycle.notes.target.margen_neto_pct`, ciclos 185 y 184), calculado con el costo de cada venta pasada (ver «Punto de equilibrio con el costo vigente»). Las metas de `target_acos_ciclo` (20.08 y 19.16) y las de `notes.target` (20.13 y 19.05, la mitad y el 0.8 de esos márgenes) son ciclos distintos del mismo día; el ejemplo del año usa 20.08 y 40.16, y las pruebas 20.13 y 19.05. Por eso la meta de alta es **absoluta (25 %) y se aplica
   después de la cascada**. Un escalón nuevo en la cascada no tocaría ninguna keyword, y sumar puntos no
   daría 25.
2. **Un bid no puede bajar más de 30 % en una decisión.** `_paso` corta el factor a `[-0.30, +0.20]`
   (`app/optimizer/politica.py:30-31`, `:612-622`). Pero el envío redondea a centavos con
   `ROUND_HALF_EVEN` después de decidir (`app/ads/write.py:187-198`). Con 0.15 USD, `_paso` da 0.1050 y
   Amazon recibe 0.10: un recorte de 33.3 %. El destino de temporada se resuelve en centavos antes de
   congelarlo.
3. **R1 leería la reducción como un recorte que tiró el tráfico.** R1 solo mira el último cambio con
   origen `motor` y dirección −1 (`politica.py:280-282`). `v_cambio_bid` solo distingue
   `regreso_por_desplome` (`migrations/0067_bids02_campana_ajuste.sql:65-77`). Sin un origen propio, R1
   deshace la reducción planeada y deja un `piso_aprendido`.
4. **Orbit no ve la historia del bid fuera de sus propios cambios.** El sync solo pisa el bid del caché
   (`app/ads/structure.py:199-210`). `CambioBid` guarda la fecha sin la hora (`app/optimizer/caso.py:131`,
   `app/lecturas_caso.py:404`). Si David cambia un bid en Amazon después de que Orbit decidió, el
   aplicador hoy manda el destino viejo sin releer (`app/apply.py:1370`, `:1409-1411`).
5. **El ciclo no puede escribir mientras decide.** TX2 es de solo lectura (`app/cycle.py:19-24`). La
   confirmación de un bid vive en `decision_application`, escrita dentro de una transacción junto con
   el sello del ledger (`app/apply.py:1436-1441`, `_confirma_resumen` en `:1091-1105`, también llamada
   desde `reconcilia_bids` en `:814` y `:826`). La identidad de un episodio de temporada tiene que
   viajar por TX3 y por esa confirmación, no por TX2.
6. **La pausa decide antes que todo y una pausa en vuelo congela la hoja.** R0 corre primero
   (`politica.py:206-224`). Una pausa en la cola salta la hoja entera, sin llegar a `decide`
   (`app/cycle.py:1915-1929`). Si el día de la salida R0 pausa antes de restaurar, el bid se queda
   reducido.
7. **La evidencia de la baja contamina lo que sigue.** La ventana va de D−90 a D−10
   (`app/lecturas_caso.py:795-805`). Las métricas son diarias, así que el día del movimiento mezcla dos
   precios. Los cortes exigen 7 fechas completas (`app/optimizer/windows.py:137`, `:189`).
8. **Las inertes no llegan a `decide`.** El ciclo salta las hojas sin impresiones en 14 días
   (`app/cycle.py:1930-1935`): 222 de 358 keywords en MX y 145 de 235 en US. Son las que más necesitan
   regresar su bid.
9. **El presupuesto ya tiene su camino aprobado por el dueño, con un hueco.** `planea_ajuste` →
   `GET /campana-ajuste/plan` → `POST /campana-ajuste/aplicar` con el literal `APLICAR AJUSTE`
   (`app/apply.py:1929`, `:2208`; `app/api_write.py:679`). El candado es por huella (`apply.py:2111`) y la
   unicidad también (`migrations/0067_bids02_campana_ajuste.sql:13`). Dos aprobaciones de la misma
   campaña pueden aplicarse al revés: la orden vieja de 375 termina después y pisa la nueva de 270. Esto
   existe hoy.
10. **Reactivar choca con una regla vieja.** `_SQL_PAUSE_PROPIO` cuenta cualquier pausa verificada
    (`app/apply_cola.py:263-270`). Si Orbit reactiva su propia pausa y después la cola ve la hoja
    ENABLED, lo lee como reactivación del dueño y deja de pausarla (`apply_cola.py:1124-1128`).
11. **«Keywords dañadas» confundiría parte de la reducción con daño.** La pantalla marca una racha de
    recortes en hojas que vendían y cuyos clics de 14 días cayeron bajo 30 % (`app/pantalla_danadas.py:3-7`).
    No todas las keywords aparecerían el primer día de la baja, pero sí las que vendían y perdieron
    clics con la reducción.

Restricciones del repo: `decide(caso)` es pura y lee solo `CasoHoja`; todo insumo nuevo se congela en
`decision.inputs.caso` detrás de un marcador opcional (los casos viejos se rejuegan igual,
`app/optimizer/replay.py:137-145`); un solo escritor de settings (`config_write.guarda_config`,
`app/config_write.py:534-562`); interruptores `ads_<algo>_<plataforma>` con `shadow`/`live` y ausente =
apagado; pruebas nuevas solo en los 4 casos del repo; migraciones con reversa y `-- sonda-aplicada:`;
nada de llaves en el repo.

---

## Uso

### 1. Ajustes: lo que ve y hace David

Tarjeta nueva **«Temporadas de venta»** en `/settings` (`app/ui.py:805-813`), arriba de las tarjetas
por plataforma. Un solo botón guarda todo lo de la tarjeta.

```
┌ Temporadas de venta ───────────────────────────────────────────────────────────────────────┐
│ Tus productos venden más en unos meses que en otros. Marca cada mes y Orbit ajusta la        │
│ inversión: gasta menos en los meses bajos, deja todo listo antes de los altos y compite más  │
│ fuerte en los altos. Vale para todos tus productos.                                          │
│                                                                                              │
│  Ene   Feb   Mar   Abr   May   Jun   Jul   Ago   Sep   Oct   Nov   Dic                        │
│ [Baja][Baja][Alta][Alta][Baja][Baja][Norm][Norm][Norm][Alta][Alta][Norm]                      │
│  52    48    75    72    55    47    60    69    64    48    20*   41   ← pedidos Amazon US     │
│  14    48    75    94    49    37    76    40    52    84    —     44   ← pedidos Mercado Libre │
│                                                        (* noviembre todavía en curso)        │
│                                                                                              │
│ Al empezar la temporada baja, bajar todos los bids           [ 30 ] %     (de 5 a 30)          │
│ Empezar a preparar la temporada alta                         [ 21 ] días antes (de 7 a 42)     │
│ En temporada baja, pedir                                     [  2 ] veces más clics y gasto    │
│    antes de pausar una keyword o bloquear una búsqueda                    (de 1 a 3)           │
│ En temporada alta, sugerir subir el presupuesto              [ 25 ] %     (de 0 a 100;         │
│    a las campañas que se quedan sin presupuesto                            0 = no sugerir)     │
│ Una campaña se queda sin presupuesto si gasta al menos       [ 90 ] % de su presupuesto        │
│    en                                                         [  5 ] de los últimos 7 días     │
│    (lo usan también el aviso de Telegram y «Dónde poner el dinero»)                           │
│                                                                                              │
│                     Meta de ACoS en temporada alta   Modo                                     │
│ Amazon MX           [ 25 ] %  (de 10 a 45)           ( ) Apagado (•) Práctica ( ) En vivo       │
│    Hoy tu meta es 20.1 % (sale de tu margen). En temporada alta: 25 %.                       │
│    Punto de equilibrio con el costo de hoy: 40.3 %. Arriba de ese ACoS pierdes dinero.       │
│ Amazon US           [ 25 ] %  (de 10 a 45)           ( ) Apagado (•) Práctica ( ) En vivo       │
│    Hoy tu meta es 19.2 % (sale de tu margen). En temporada alta: 25 %.                       │
│    Punto de equilibrio con el costo de hoy: 23.8 %. Arriba de ese ACoS pierdes dinero.       │
│    ⚠ Con 25 % en temporada alta aceptas ventas que pierden dinero en Amazon US (ACoS de 23.8 a 25 %). │
│ 3 campañas con meta propia no cambian.                                                       │
│ Práctica: Orbit calcula lo que haría y te lo enseña cada día, sin tocar Amazon.              │
│                                                                                              │
│ Así queda tu año (hora UTC, la del motor)                                                     │
│  Hoy (12 nov): temporada alta. Meta de ACoS 25 % en MX y US.                                 │
│  1 dic       La meta de ACoS regresa a la normal.                                            │
│  1 ene       Empieza la temporada baja: Orbit baja todos los bids 30 %.                       │
│  8 feb       Empieza la preparación: los bids regresan a como estaban antes de la baja y      │
│              Orbit reactiva las keywords que pausó durante la baja.                          │
│  1 mar       Empieza la temporada alta: meta de ACoS 25 %.                                    │
│  1 may       Empieza la temporada baja: Orbit baja todos los bids 30 %.                       │
│  1 jul       Termina la baja: los bids regresan. Sigue un mes normal.                         │
│                                                                                              │
│ [ Guardar temporadas ]                                                                       │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Durante una baja, la tarjeta suma un bloque de estado y un botón:

```
│ Temporada baja desde el 1 ene (Amazon MX)                                                    │
│  Bids bajados y confirmados en Amazon: 212   Pendientes: 9   Sin tocar: 4                      │
│  Sin tocar: 3 porque cambiaste tú el bid, 1 porque usa el bid de su grupo.  [ Ver lista ]    │
│  Keywords que Orbit pausó en la baja y va a reactivar al terminar: 6        [ Ver lista ]    │
│     funda impermeable 30L ……………………………………………… [ Mantener pausada ]                          │
│ [ Terminar la baja hoy ]  Orbit regresa los bids en el siguiente ciclo. Tu calendario no      │
│                           cambia para el año que viene.                                      │
```

Al terminar una baja, el bloque dice cuántos bids regresaron confirmados, cuántos siguen pendientes y
por qué («3 esperan presupuesto: su campaña se queda sin presupuesto; revisa la propuesta en Dónde
poner el dinero»). Nunca dice «listo» por decisiones generadas: cuenta confirmaciones de Amazon.

En la tarjeta de cada plataforma, bajo «Motor de bids», dos campos más (valen todo el año, no dependen
del calendario):

```
│ Subida rápida de bids recortados   ( ) Apagada (•) Práctica ( ) En vivo                         │
│ Recordar el bid más alto de los últimos  [ 365 ] días  (de 90 a 730)                            │
│ La subida rápida usa la misma «confianza para subir» de arriba.                                 │
```

En «Goals de campaña» y en el goal de plataforma, cada meta escrita a mano muestra: «Esta meta no
cambia en temporada alta».

**Campos, claves y rangos.** Los valores por defecto solo prellenan el formulario: nada decide hasta
que David guarda.

| Campo | Clave en `config_version.settings` | Default | Rango | Unidad |
| --- | --- | --- | --- | --- |
| Tipo de cada mes | `ads_temporada.meses` | baja, baja, alta, alta, baja, baja, normal, normal, normal, alta, alta, normal | `baja`/`normal`/`alta` | — |
| Reducción al entrar a la baja | `ads_temporada.reduccion_baja_pct` | `"30"` | 5 a 30 | % |
| Días de preparación | `ads_temporada.dias_preparacion` | `21` | 7 a 42 | días |
| Multiplicador de cortes en baja | `ads_temporada.mult_cortes_baja` | `"2"` | 1 a 3 | veces |
| Aumento sugerido de presupuesto en alta | `ads_temporada.aumento_presupuesto_alta_pct` | `"25"` | 0 a 100 | % |
| Terminar la baja antes | `ads_temporada.fin_anticipado` | `null` | hoy, solo en baja | fecha |
| Mantener pausadas | `ads_temporada.mantener_pausadas` | `[]` | ids de keywords, ≤ 500 | — |
| Campaña sin presupuesto: % gastado | `ads_campana_topada.pct` | `"90"` | 50 a 100 | % |
| Campaña sin presupuesto: días | `ads_campana_topada.dias` | `5` | 1 a 7 | de 7 |
| Meta de ACoS en alta | `ads_temporada_meta_alta_<plat>` | `"25"` | 10 a 45 | % |
| Modo de temporada | `ads_temporada_<plat>` | ausente | `shadow`/`live` | — |
| Modo de subida rápida | `ads_bid_recuperacion_<plat>` | ausente | `shadow`/`live` | — |
| Memoria del bid más alto | `ads_bid_recuperacion_memoria_dias_<plat>` | `365` | 90 a 730 | días |

El rango de la meta de alta (10 a 45) es el mismo que ARREGLOS X fija para una meta manual
(`exige_banda_meta_manual`, `plans/arreglos-01.md:320`) *(contrato de ARREGLOS 01)*. Los 5 de 7 días son
el umbral del aviso de hoy (`app/avisos_campana.py:79`) y el 90 % el de «Dónde poner el dinero»
(`app/pantalla_dinero.py:473`); los dos pasan a leer `ads_campana_topada`. Las esperas y clics del
motor que no son de temporada (R2, R14, R15) se quedan como están y no aparecen aquí.

**Mensajes de validación.** Los escribe `temporada.valida`, el mismo lector que usan el ciclo, la
vista previa y el guardado. El endpoint los devuelve tal cual en el 422.

| Qué mandó | Mensaje |
| --- | --- |
| Faltan meses o uno con otro valor | «Marca cada uno de los 12 meses como baja, normal o alta.» |
| Los 12 meses en baja | «Si todos los meses son baja, Orbit nunca regresaría los bids. Marca al menos un mes como normal o alta.» |
| Reducción fuera de rango | «La reducción puede ser de 5 % a 30 %: es lo más que Orbit mueve un bid de una sola vez.» |
| Días de preparación fuera de rango | «Los días de preparación van de 7 a 42.» |
| La preparación se come la baja | «La temporada baja de febrero dura 28 días y una preparación de 25 la deja en 3. Necesitas al menos 7 días de baja: acorta la preparación o marca otro mes como baja.» (mínimo: 7 días efectivos, con febrero de 28) |
| Multiplicador fuera de rango | «El multiplicador de la temporada baja va de 1 a 3. Con 1, Orbit pausa igual que en un mes normal.» |
| Aumento de presupuesto fuera de rango | «El aumento sugerido de presupuesto va de 0 % a 100 %.» |
| Criterio de campaña fuera de rango | «El porcentaje va de 50 a 100 y los días de 1 a 7.» |
| Meta de alta fuera de rango | «La meta de ACoS de temporada alta va de 10 % a 45 %.» |
| Terminar la baja fuera de una baja | «Solo puedes terminar la baja mientras estás en temporada baja.» |
| Encender sin meses guardados | «Para encender la temporada primero guarda los meses.» |
| Número con coma, texto, NaN o infinito | «Escribe solo el número, por ejemplo 30.» |
| Sí/no en un campo de número (`true`/`false`) | «Escribe un número, no sí o no.» |

**Vista previa.** «Así queda tu año» se recalcula en cada cambio con
`GET /api/ads-optimizer/temporada/vista-previa` (sin token y de solo lectura, como `/campana-ajuste/plan`
en `app/api_write.py:731-741`). Llama a `temporada.resumen`, la misma función pura que pinta la página
y que devuelve el guardado. El JS no repite ninguna regla. Si la edición cambia la fase de hoy, el aviso
sale en amarillo antes de guardar:

> Ojo: con este cambio hoy pasa a temporada baja. En el siguiente ciclo Orbit baja los bids 30 %.

Al guardar, el mensaje verde dice lo que sigue:

> Guardado. Próximo cambio: el 1 de diciembre la meta de ACoS regresa a la normal. La próxima
> preparación empieza el 8 de febrero.

La configuración nueva rige desde el siguiente ciclo y no reescribe lo que ya pasó: los episodios y las
cercas de evidencia salen de movimientos confirmados, no del calendario.

### 2. Lo que guarda el endpoint (un solo escritor)

```python
# app/api_write.py — POST /api/ads-optimizer/settings/{platform} (app/api_write.py:537); la tarjeta
# global se guarda desde cualquier plataforma, como precio_modo_global.
config_write.guarda_config(
    conn, platform="amazon_mx", base_config_version_id=51, ahora=ahora,
    temporada={
        "meses": ["baja", "baja", "alta", "alta", "baja", "baja",
                  "normal", "normal", "normal", "alta", "alta", "normal"],
        "reduccion_baja_pct": "30", "dias_preparacion": 21, "mult_cortes_baja": "2",
        "aumento_presupuesto_alta_pct": "25", "fin_anticipado": None, "mantener_pausadas": [],
    },
    campana_topada={"pct": "90", "dias": 5},
    temporada_modo={"amazon_mx": "shadow", "amazon_us": "shadow"},
    meta_alta={"amazon_mx": "25", "amazon_us": "25"},
)
# -> {"config_version_id": 52, "created": True, "label": "...", "resumen_temporada": {...}}
```

Queda así (fila nueva, append-only):

```json
"ads_temporada": {
  "version": "temporada_v1",
  "meses": ["baja","baja","alta","alta","baja","baja","normal","normal","normal","alta","alta","normal"],
  "reduccion_baja_pct": "30",
  "dias_preparacion": 21,
  "mult_cortes_baja": "2",
  "aumento_presupuesto_alta_pct": "25",
  "fin_anticipado": null,
  "mantener_pausadas": []
},
"ads_campana_topada": {"pct": "90", "dias": 5},
"ads_temporada_amazon_mx": "shadow",
"ads_temporada_amazon_us": "shadow",
"ads_temporada_meta_alta_amazon_mx": "25",
"ads_temporada_meta_alta_amazon_us": "25",
"ads_bid_recuperacion_amazon_mx": "shadow",
"ads_bid_recuperacion_amazon_us": "shadow",
"ads_bid_recuperacion_memoria_dias_amazon_mx": 365,
"ads_bid_recuperacion_memoria_dias_amazon_us": 365
```

`CuerpoSettings` suma estos campos con tipo `Any`: Pydantic no los convierte. `temporada.valida` revisa
el tipo crudo antes que nada (rechaza `true`/`false` en un campo numérico, NaN e infinito) y después los
rangos. Así un booleano nunca llega como 0 o 1.

### 3. Lo que llama el ciclo

**a) `cycle._recorre_plataforma`, una vez por plataforma, en TX2 (solo lectura):**

```python
config_t = temporada.desde_settings(settings)                     # None sin meses guardados
modo_t = temporada.modo_desde_settings(settings, platform)        # None | "shadow" | "live"
modo_r = recuperacion.modo_desde_settings(settings, platform)
hoy = decided_at.astimezone(dt.UTC).date()
estacion = temporada.resuelve(config_t, hoy) if config_t is not None and modo_t is not None else None
plat_t = None
if estacion is not None:
    plat_t = TemporadaPlataforma(
        estacion=estacion, modo=modo_t, config=config_t,
        meta_alta_pct=temporada.meta_alta_desde_settings(settings, platform),
        estado=temporada_io.lee_estado(conn, platform, hoy),      # episodio abierto, efectos abiertos
    )                                                             # clasificados, exclusiones, cercas
lecturas = lee_plataforma(
    conn, platform, decided_at, economia=economia,
    version_economia=version_economia,                            # (contrato de ARREGLOS 01, U)
    temporada=plat_t,
    recuperacion=recuperacion.insumos_desde_settings(settings, platform, modo_r),
    campana_topada=temporada.campana_topada_desde_settings(settings),
)
reactivaciones = (
    temporada_io.reactivaciones_pendientes(conn, platform, config_t.mantener_pausadas)
    if plat_t is not None else ()
)
```

**b) `cycle._procesa_decisora`, por hoja.** La frontera de la hoja se calcula antes de resolver los
cortes, porque de ella salen el multiplicador y la cerca de la ventana de pausa.

```python
frontera = lecturas.frontera(entidad_id, current_bid)            # None sin temporada; puro, sin consulta
if entidad_id in inertes:
    if not (plat_t and plat_t.modo == "live" and lecturas.tiene_pendiente(entidad_id, current_bid)):
        if plat_t and plat_t.modo == "shadow":
            _anota_sombra_inerte(contadores, lecturas, entidad_id, current_bid)   # solo notes
        contadores.skips_entidad[MOTIVO_ENTIDAD_INERTE] += 1
        tick()
        return
mult = frontera.mult_cortes if frontera is not None else None
clave = (ad_group_id, mult)                                       # hoy la caché es por grupo (cycle.py:1944-1947)
if clave not in corte_pause_por_grupo:
    evidencia = evidencia_ad_groups.get(ad_group_id)
    corte_pause_por_grupo[clave] = (cortes.umbral_corte(evidencia, "pause", mult_baja=mult), evidencia)
...
caso, sombra = lecturas.casos(
    conn, hoja_id=entidad_id, ad_group_id=ad_group_id, bid=..., target_acos_pct=target,
    procedencia=procedencia, pausa=pausa, equilibrio_acos_pct=...,
)
veredicto = decide(caso)               # live: el caso trae temporada; shadow: va sin ella
if sombra is not None:
    _anota_sombra_temporada(contadores, caso_sombra=sombra, actual=veredicto)
```

En práctica la elegibilidad del camino vivo no cambia: una inerte sigue saltándose y su veredicto de
temporada solo se anota. En vivo, la entrada y el regreso llegan a toda keyword habilitada con bid
propio, también las inertes. El juicio económico de las inertes sigue saltado: con un pendiente, la
hoja entra a `decide` y RT decide antes que el juicio.

**c) TX3 (la única escritura de la fase de decisión).** Antes del `executemany` de decisiones:

```python
ep = temporada_io.registra_ciclo(conn, platform, cycle_id=cycle_id, decidido_el=decided_at,
                                 plataforma=plat_t)              # reconcilia, abre o cierra episodio
temporada_io.registra_fines(conn, platform, cycle_id=cycle_id, episodio_abierto=ep,
                            fines=fines)                          # de TODAS las hojas leídas en TX2
pendientes = [temporada_io.estampa(p, ep) for p in pendientes]  # inputs.temporada_episodio_id
ids = _inserta_decisiones(conn, pendientes)                      # como hoy
temporada_io.abre_efectos(conn, ep, pendientes, ids)            # uno por decisión de temporada
```

`fines` no sale de las decisiones: TX2 arma `fines: dict[int, FinReduccion]` con `FronteraHoja.fin`
de cada hoja que leyó con temporada, también las que terminan en `Mantener` (`cycle.py:2209` regresa
antes de agregar una decisión), las inertes y las que no deciden nada. Igual arma `exclusiones: dict[int,
Decimal]` con `FronteraHoja.exclusion_hoy` (la mano sin fecha vista hoy, con su bid). En práctica no se
escribe ningún fin ni exclusión.

**d) Fase de apply (`cycle.py:3523-3531`).** `apply.reconcilia_bids` y `aplicador.aplica_bids` como hoy,
con la lectura fresca dentro del aplicador. Después, `aplicador.aplica_reactivaciones(...)` con las
decisiones `resume` del ciclo.

**e) Revalidación de la cola al liberar (`apply_cola.libera_vencidos`, `app/apply_cola.py:1567`).** La
pausa y el negative se revalidan con `temporada_io.frontera_hoja(conn, ad_entity_id, hoy)`, que llama a
la misma función pura `temporada.frontera` que usó el ciclo.

**f) `pantalla_dinero.filas_campana`.** La propuesta de presupuesto se calcula al pintar «Dónde poner el
dinero». El botón «Revisar» abre el mismo diálogo de hoy:

```js
// app/static/js/dinero.js
//   GET  /api/ads-optimizer/campana-ajuste/plan?campana_id=..&clase=presupuesto&presupuesto=210
//   POST /api/ads-optimizer/campana-ajuste/aplicar {huella, confirmacion: "APLICAR AJUSTE", actor}
```

### 4. Qué ve David (veredictos de ejemplo, Amazon MX)

| Situación | Hoy | Con el diseño |
| --- | --- | --- |
| 1 ene, keyword en 8.00 MXN | Nada | `Mover(-0.30, 5.60, "entra_temporada_baja")` en el primer ciclo |
| 1 ene, keyword inerte en 8.00 | Saltada | La misma entrada: también baja |
| 1 ene, keyword en 1.55 MXN | — | Destino 1.09 (el centavo que no pasa de 30 %); nunca 1.08 |
| 1 ene, Orbit decidió 5.60 y David puso 7.00 en Amazon antes del envío | Orbit mandaría 5.60 | No manda nada; la keyword queda excluida de esta baja y sigue en 7.00 |
| 1 feb, la misma, 400 MXN sin venta desde la entrada | R9 −12 % | `Mantener("temporada_baja")` |
| 1 feb, impresiones −70 % tras la reducción | R1 regresaría | Nada: el último cambio tiene origen `temporada` |
| 8 feb, keyword en 5.60 que estaba en 8.00 | Nada | `Regresar(8.00, "sale_temporada_baja")`, aunque sea inerte o haya vendido ayer |
| 8 feb, la misma con 200 clics, 1,000 MXN y 0 pedidos en la ventana | — | Primero el regreso; la pausa no se juzga con datos de la baja |
| 8 feb, David la puso a mano en 6.50 el 20 ene | — | No la toca |
| 15 mar (alta), meta por margen 20.08 %, ACoS 23 % | R4 −12 % si el grupo pierde | Meta del juicio 25 %: no recorta |
| 15 mar, campaña con meta propia de 15 % | — | Juzga con 15 % |
| 15 mar, campaña que gasta ≥ 90 % de su presupuesto 5 de 7 días | Aviso | Las subidas esperan; propuesta «subir de 300 a 375 MXN al día» con botón Revisar |
| 1 may → baja; 1 jul → normal | — | Reducción el 1 may; regreso el 1 jul, sin esperar a septiembre |

### 5. Avisos por Telegram

Por el canal de avisos de campaña que ya existe, el día de cada cambio y 3 días antes de cada entrada y
cada regreso. Cuentan confirmaciones, no decisiones. Sin costo, margen ni meta en el texto (regla de
`avisos_campana`).

> Hoy empezó la temporada baja en Amazon MX. Orbit bajó 212 bids 30 % y Amazon lo confirmó. Quedan 9
> para mañana. No tocó 4: 3 los cambiaste tú y 1 usa el bid de su grupo. Hay 12 campañas con un
> presupuesto sugerido más bajo en «Dónde poner el dinero».

> Hoy empezó la preparación de la temporada alta. Orbit regresó 205 bids a como estaban antes de la
> baja. 3 esperan presupuesto: su campaña se queda sin presupuesto. Va a reactivar 6 keywords que pausó
> en la baja.

---

## Forma

### Decisiones que sostienen el diseño

1. **El calendario dice lo que David quiere; el episodio dice qué ajuste sigue abierto; la
   confirmación dice qué pasó.** El calendario de 12 meses es la única fuente de la fase del día. Un
   episodio de temporada es una fila durable por plataforma, abierta en TX3 el primer día de baja y
   cerrada en TX3 el primer día que la fase deja de ser baja. Cada decisión de temporada lleva el id del
   episodio en `decision.inputs.temporada_episodio_id`. La prueba de que una keyword entró o regresó es
   su `decision_application` confirmada, que llega a `v_cambio_bid`. Editar los meses no reescribe la
   identidad de un recorte ya confirmado.
2. **La entrada es un movimiento con origen propio, resuelto en centavos.** El destino es el centavo
   más bajo que no baja más que la reducción: `ceil_centavo(bid × (1 − r))`, dentro de piso y techo. Si
   no hay centavo factible (bid en el piso), la keyword se excluye con motivo. Lo construye
   `_paso_a_centavo`, hermano de `_paso` en `politica.py`, con la misma validación de dirección,
   magnitud y rango. Lo congelado, lo enviado y lo confirmado coinciden.
3. **El origen `temporada` en `v_cambio_bid`.** Las decisiones `entra_temporada_baja`,
   `ajusta_temporada_baja` y `sale_temporada_baja` quedan con `origen = 'temporada'`. R1 no las ve,
   `piso_aprendido` no las cuenta, «Keywords dañadas» no las mete en su racha y los frenos de repetición
   no las juzgan.
4. **Lo pendiente se deriva cada día y nada se hace dos veces.** Por keyword, `temporada.pendiente`
   compara el calendario con la cadena confirmada y con el episodio. Una keyword reducida (su último
   movimiento de temporada es una bajada o un ajuste, no un regreso) nunca recibe otra entrada, aunque
   cambien los meses o se abra otro episodio. En la base, un índice único impide dos efectos de
   temporada abiertos para la misma keyword.
5. **Lo último de David gana, con tres señales.** (a) Un regreso del dueño en el ledger o una
   discontinuidad de la cadena (un `bid_antes` que no coincide con el `bid_despues` anterior, tolerancia
   0.005) posterior a la frontera que importa; (b) para el regreso, un bid vigente distinto del último
   confirmado; (c) una exclusión durable que escribe el aplicador cuando el bid fresco de Amazon ya no es
   el que Orbit vio al decidir. Con cualquiera, la temporada deja esa keyword en paz en ese episodio.
6. **El regreso pendiente va antes que la pausa.** Si una keyword tiene que regresar, RT devuelve el
   `Regresar` antes de R0. Los cortes se vuelven a juzgar solo con evidencia desde el primer día UTC
   completo después del regreso confirmado. Si el regreso queda retenido (campaña sin presupuesto), la
   hoja sigue en «baja» para los cortes: umbrales ×2 y la cerca de su entrada.
7. **Durante la baja, cero recortes por evidencia y cero R1.** Todo candidato con factor < 0 da
   `Mantener("temporada_baja")`. Las subidas siguen, con un techo de baja común a R6 y R6r: el techo
   económico recuperable por `(1 − reducción)`. Pausas y negatives siguen con el multiplicador del dueño.
8. **Cerca de evidencia por hechos, no por fechas del calendario.** Ninguna ventana de evidencia de
   falla de una keyword empieza antes del día UTC siguiente a su último movimiento de temporada
   confirmado. Grupo y cuenta usan la cerca de la plataforma: el día siguiente a la primera confirmación
   de la última transición. La cerca caduca a los 90 días.
9. **Alta: meta absoluta por plataforma, después de la cascada, solo para el juicio de bids.** En alta,
   `decide` juzga R3–R13 con `max(base, meta_alta)`. La meta es libre: puede pasar el equilibrio. Si lo
   pasa, R3 usa la meta como límite de pérdida (el dueño aceptó perder hasta ahí) y Ajustes lo avisa en
   rojo. No toca R0, negatives ni harvest. Las metas escritas a mano (`goal_campana`,
   `goal_plataforma`) no cambian.
10. **Presupuesto: Orbit propone montos absolutos por episodio, David aprueba.** La temporada no escribe
    presupuestos. El escritor de presupuestos se coordina por campaña y campo, para que una orden vieja
    nunca pise una nueva.
11. **Reactivar es la reversa que ya existe, con decisión propia y guardas propias.** Solo pausas de
    Orbit confirmadas que la hoja decidió en «baja» y cuyo episodio ya cerró. `_SQL_PAUSE_PROPIO` deja de
    contar las pausas que la temporada deshizo.
12. **Todo detrás de marcadores congelados.** `CasoHoja.temporada`, `.historia`, `.recuperacion`,
    `.presupuesto` y `InsumosPausa.exceso_minimo` son opcionales; ausentes = semántica de hoy. En
    práctica, el caso que decide va sin marcadores y se decide un segundo caso con marcadores; solo las
    diferencias se guardan, con el caso completo, en `notes.temporada`.

### Tipos

```python
# app/optimizer/temporada.py — PURO (sin IO, sin reloj, Decimal)
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

VERSION_TEMPORADA = "temporada_v1"
CLAVE_TEMPORADA = "ads_temporada"                       # global, como precio_modo_global
CLAVE_CAMPANA_TOPADA = "ads_campana_topada"             # global; también la leen avisos y dinero
TipoMes = Literal["baja", "normal", "alta"]
Fase = Literal["baja", "normal", "alta"]                 # la preparación es "normal"

REDUCCION_MIN, REDUCCION_MAX = Decimal("0.05"), Decimal("0.30")   # MAX = -politica.CLAMP_FACTOR_MIN
DIAS_PREPARACION_MIN, DIAS_PREPARACION_MAX = 7, 42
BAJA_EFECTIVA_MIN_DIAS = 7
MULT_CORTES_MIN, MULT_CORTES_MAX = Decimal("1"), Decimal("3")
AUMENTO_PRESUPUESTO_MAX = Decimal("1")
META_ALTA_MIN, META_ALTA_MAX = Decimal("10"), Decimal("45")
MANTENER_PAUSADAS_MAX = 500
DIAS_CERCA = 90                                          # caso.DIAS_LOOKBACK
DIAS_PROPUESTA_FIN_BAJA = 30
CENTAVO = Decimal("0.01")
MESES_POR_DEFECTO: tuple[TipoMes, ...] = (
    "baja", "baja", "alta", "alta", "baja", "baja",
    "normal", "normal", "normal", "alta", "alta", "normal",
)  # solo prellena el formulario; nunca decide sin guardarse


@dataclass(frozen=True)
class ConfigTemporada:
    """Lo que fijó David. Solo `valida` la construye: si existe, es válida."""
    version: str
    meses: tuple[TipoMes, ...]           # 12; índice = mes - 1
    reduccion_baja: Decimal              # fracción en [0.05, 0.30]
    dias_preparacion: int
    mult_cortes_baja: Decimal            # en [1, 3]
    aumento_presupuesto_alta: Decimal    # fracción en [0, 1]
    fin_anticipado: dt.date | None       # «Terminar la baja hoy»; solo vale dentro de su bloque de baja
    mantener_pausadas: frozenset[int]    # ad_entity_id que David no quiere reactivar


@dataclass(frozen=True)
class CampanaTopada:
    """Una sola definición para aviso, propuesta y freno de subidas."""
    pct: Decimal                          # 0.90
    dias: int                             # de los últimos 7 con ingesta


@dataclass(frozen=True)
class Estacion:
    """Lo que el calendario dice del día D para TODA la plataforma. Se congela en el caso."""
    version: str
    dia: dt.date
    fase: Fase
    previa_alta: bool                     # dentro de los días de preparación de un bloque alto
    reduccion_baja: Decimal               # parámetros vigentes ese día (el replay no relee settings)
    mult_cortes_baja: Decimal
    inicio_bloque: dt.date                # primer día del bloque de fase vigente; cierra un episodio de otra baja


@dataclass(frozen=True)
class EpisodioVigente:
    id: int
    abierto_el: dt.datetime


@dataclass(frozen=True)
class FronteraHoja:
    """Lo que la temporada dice de UNA keyword para cortes y juicio. Sale de `frontera`."""
    fase_hoja: Fase                       # "baja" si el calendario dice baja o la hoja sigue reducida
    mult_cortes: Decimal                  # 1 salvo fase_hoja == "baja"
    cerca_hoja: dt.date | None            # día UTC siguiente a su último movimiento de temporada confirmado
    cerca_plataforma: dt.date | None      # para grupo, cuenta y términos
    episodio_id: int | None               # el de su reducción vigente, o el abierto
    fin: FinReduccion | None = None       # la reducción vieja termina en este ciclo (TX3 lo guarda)
    exclusion_hoy: Decimal | None = None  # mano sin fecha vista hoy en baja: TX3 la guarda con este bid_visto


@dataclass(frozen=True)
class TemporadaCaso:
    """Presente sii ads_temporada_<plat> decide este caso (live) o en el caso de sombra."""
    estacion: Estacion
    episodio: EpisodioVigente | None      # None: no hay episodio abierto (o se abre en este ciclo)
    excluida_en: frozenset[int]           # episodios donde el aplicador o TX3 vieron la mano de David
    meta_alta_pct: Decimal
    meta_manual: bool                     # procedencia goal_campana o goal_plataforma
    frontera: FronteraHoja


EventoTipo = Literal["entra_baja", "prepara_alta", "restaura", "entra_alta", "sale_alta"]


@dataclass(frozen=True)
class Evento:
    fecha: dt.date
    tipo: EventoTipo
    frase: str                            # español llano, sin jerga


@dataclass(frozen=True)
class ResumenTemporada:
    hoy: dt.date
    fase_hoy: Fase
    frase_hoy: str
    proximos: tuple[Evento, ...]          # próximos 12 meses, en orden
    aviso_cambio: str | None              # solo si la edición cambia la fase de hoy

    def como_dict(self) -> dict: ...


# Lo pendiente de una keyword (sin tipos de veredicto: no importa politica)
@dataclass(frozen=True)
class EntradaPendiente:
    destino: Decimal                      # ya en centavos, dentro de piso y techo

@dataclass(frozen=True)
class AjustePendiente:
    destino: Decimal                      # base × (1 − r nueva), en centavos; David editó la reducción

@dataclass(frozen=True)
class RestauracionPendiente:
    nivel: Decimal                        # base del episodio, acotada por piso y techo
    episodio_id: int

SinPendienteMotivo = Literal[
    "fuera_de_temporada", "ya_entro", "ya_arriba", "dueno_decidio",
    "bid_heredado", "sin_centavo_factible", "dato_faltante",
]

@dataclass(frozen=True)
class SinPendiente:
    motivo: SinPendienteMotivo

Pendiente = EntradaPendiente | AjustePendiente | RestauracionPendiente | SinPendiente


@dataclass(frozen=True)
class EpisodioPresupuesto:
    """Hechos de un episodio para la pantalla de dinero."""
    id: int
    abierto_el: dt.date
    cerrado_el: dt.date | None


@dataclass(frozen=True)
class PropuestaPresupuesto:
    actual: Decimal
    propuesto: Decimal                    # monto absoluto, en centavos y bajo el tope humano de X
    motivo: Literal["baja", "fin_de_baja", "alta_sin_presupuesto"]
    episodio_id: int | None
    frase: str                            # «Temporada baja: bajar de 300 a 210 MXN al día»
```

```python
# app/optimizer/caso.py — campos opcionales nuevos (la forma vieja se sigue aceptando)

OrigenCambio = Literal[
    "motor", "regreso_por_desplome", "regreso_del_dueno", "ajuste_de_campana", "temporada",
]

@dataclass(frozen=True)
class CambioFechado:
    """Un cambio de v_cambio_bid con su instante y su id: el orden de intervenciones del mismo día."""
    confirmado_el: dt.datetime
    decision_id: int | None
    bid_antes: Decimal | None             # None en el regreso del dueño (rama 2 de la vista)
    bid_despues: Decimal
    origen: OrigenCambio
    motivo_temporada: Literal["entra", "ajusta", "sale"] | None
    episodio_id: int | None

@dataclass(frozen=True)
class ManoExterna:
    """Una mano de David que no pasa por v_cambio_bid, con fecha: una fila de temporada_exclusion con
    bid_visto (del aplicador o de TX3) o una reactivación manual (reactivacion_manual.detectada_en)."""
    instante: dt.datetime
    bid_visto: Decimal | None             # None en la reactivación manual
    origen: Literal["exclusion", "reactivacion_manual"]

@dataclass(frozen=True)
class HistoriaBid:
    """Todos los cambios confirmados de la hoja desde `desde`, con instante. `desde` es D−365, o
    antes si la hoja sigue reducida por un episodio más viejo (un episodio abierto no caduca).
    La leen solo recuperacion.cadena_bid y la temporada; Trayectoria (90 días) no cambia."""
    desde: dt.date
    cambios: tuple[CambioFechado, ...]    # ascendentes por (confirmado_el, decision_id)
    fines: frozenset[int] = frozenset()   # episodios con fila en temporada_fin_reduccion para la hoja
    manos_externas: tuple[ManoExterna, ...] = ()   # exclusiones con bid_visto y reactivaciones manuales desde `desde`

@dataclass(frozen=True)
class PresupuestoCampana:
    """Presupuesto diario vigente de la campaña de la hoja y cuántos de los últimos 7 días con
    ingesta gastó al menos `topada.pct` de él. None por campo = desconocido (no se inventa)."""
    presupuesto_diario: Decimal | None
    dias_medidos: int
    dias_topados: int | None
    topada: CampanaTopada                 # la definición vigente, congelada

@dataclass(frozen=True)
class InsumosRecuperacion:
    version: Literal["recuperacion_v1"]
    memoria_dias: int

@dataclass(frozen=True)
class InsumosPausa:
    ...                                    # campos de hoy (umbral_clics y gasto_minimo ya resueltos)
    exceso_minimo: Decimal | None = None   # None = bid.EXCESO_MINIMO[moneda]; en baja, ×mult

@dataclass(frozen=True)
class CasoHoja:
    ...                                    # campos de hoy
    temporada: TemporadaCaso | None = None
    historia: HistoriaBid | None = None    # presente si temporada o recuperación deciden
    recuperacion: InsumosRecuperacion | None = None
    presupuesto: PresupuestoCampana | None = None   # presente si temporada o recuperación deciden
    # desde_json: _objeto_con_opcionales(datos, _CLAVES_CASO,
    #                                    {"temporada", "historia", "recuperacion", "presupuesto"})
```

```python
# app/optimizer/recuperacion.py — PURO

TOLERANCIA_CADENA = Decimal("0.005")   # write.py redondea a 0.01 HALF_EVEN: más que esto es otra mano
ORIGENES_ORBIT = frozenset({"motor", "regreso_por_desplome", "temporada"})

@dataclass(frozen=True)
class ReduccionVigente:
    """La reducción de temporada que la hoja todavía lleva: su último movimiento de temporada es
    una bajada o un ajuste, no un regreso."""
    episodio_id: int
    base: Decimal                     # bid_antes del primer movimiento de temporada de ese episodio
    primer_el: dt.datetime            # instante de esa entrada confirmada
    ultimo_bid: Decimal               # bid_despues del último movimiento de temporada
    ultimo_el: dt.datetime
    solo_temporada_despues: bool      # ningún otro cambio de Orbit después del último de temporada

@dataclass(frozen=True)
class CadenaBid:
    """La historia del bid vista como cadena. Derivada, no se guarda."""
    cambios: tuple[CambioFechado, ...]            # sin ajuste_de_campana, ascendentes
    manos_del_dueno: tuple[dt.datetime, ...]      # regreso_del_dueno o discontinuidad, fechadas
    bid_de_la_ultima_mano: Decimal | None
    dueno_al_final: bool                          # bid vigente != último bid conocido (bid_despues de Orbit o bid_visto de una exclusión)
    reduccion: ReduccionVigente | None
    ultimo_salto_recuperacion: bool               # el último cambio fue un recupera_nivel
```

### Firmas y seudocódigo, por módulo

```python
# app/optimizer/temporada.py

def valida(crudo: Mapping) -> ConfigTemporada:
    """Frontera. ValueError(mensaje de la tabla de Uso).
    # 1. tipos crudos: bool en campo numérico -> «Escribe un número, no sí o no.»;
    #    string decimal o número JSON finito; dias_* enteros; fin_anticipado "AAAA-MM-DD" o null;
    #    mantener_pausadas lista de enteros >= 1, <= 500
    # 2. meses: 12 en {baja, normal, alta}; no todos baja
    # 3. rangos de la tabla
    # 4. por cada bloque de baja: dias(inicio, R) >= BAJA_EFECTIVA_MIN_DIAS, con febrero de 28
    #    (el peor año; la validación no cambia entre años)"""
    raise NotImplementedError

def desde_settings(settings: Mapping) -> ConfigTemporada | None:
    """Lector único: ciclo, config_write, pantalla, vista previa y cola. Ausente -> None.
    Presente -> valida(); corrupta -> ValueError (el ciclo falla cerrado como con los demás)."""
    raise NotImplementedError

def modo_desde_settings(settings: Mapping, plataforma: str) -> Literal["shadow", "live"] | None:
    """ads_temporada_<plat>: ausente -> None; shadow|live; otro -> ValueError. Presente sin
    ads_temporada -> ValueError("Para encender la temporada primero guarda los meses.")."""
    raise NotImplementedError

def meta_alta_desde_settings(settings: Mapping, plataforma: str) -> Decimal:
    """ads_temporada_meta_alta_<plat>; ausente -> Decimal("25"); fuera de [10, 45] -> ValueError."""
    raise NotImplementedError

def campana_topada_desde_settings(settings: Mapping) -> CampanaTopada:
    """ads_campana_topada; ausente -> CampanaTopada(Decimal("0.90"), 5) (los umbrales de hoy)."""
    raise NotImplementedError

def resuelve(config: ConfigTemporada, dia: dt.date) -> Estacion:
    """La fase de `dia`, sobre fechas reales (años y-1, y, y+1).
    # bloques = rachas circulares de meses del mismo tipo, instanciadas en fechas
    # para cada bloque alto: A = día 1 de su primer mes; prep = A - dias_preparacion
    #   (un bloque alto de varios meses se prepara una vez: abril no es otro pico tras marzo)
    # para cada bloque de baja con inicio B y fin natural F (día 1 del siguiente mes no baja):
    #   R = min(F, prep del siguiente bloque alto)   # aunque haya meses normales en medio
    #   si config.fin_anticipado cae en [B, R): R = fin_anticipado
    # prioridad: alta > preparación > baja > normal
    #   fase = "alta" si el mes de dia es alta; "baja" si B <= dia < R; si no "normal"
    #   previa_alta = prep <= dia < A para algún bloque alto, y el mes de dia no es alta
    # sin meses alta: nunca hay preparación; sin meses baja: nunca hay baja"""
    raise NotImplementedError

def eventos(config: ConfigTemporada, desde: dt.date, hasta: dt.date) -> tuple[Evento, ...]:
    """entra_baja (B), restaura (R; «preparación» en la frase si sigue alta), prepara_alta (prep que
    no viene de baja: solo aviso), entra_alta, sale_alta. Las frases nombran fechas y porcentajes."""
    raise NotImplementedError

def resumen(config: ConfigTemporada, hoy: dt.date, *, antes: ConfigTemporada | None,
            metas_hoy: Mapping[str, Decimal], meta_alta: Mapping[str, Decimal]) -> ResumenTemporada:
    """eventos(config, hoy, hoy + 365 d) + la frase de hoy. Si `antes` da otra fase para hoy,
    aviso_cambio dice qué pasa en el siguiente ciclo. Lo llaman la página, la vista previa y el POST."""
    raise NotImplementedError

def destino_entrada(bid: Decimal, reduccion: Decimal, piso: Decimal, techo: Decimal) -> Decimal | None:
    """El centavo más bajo que no baja más de `reduccion`.
    # d = (bid * (1 - reduccion)).quantize(CENTAVO, ROUND_CEILING)
    # d = min(max(d, piso), techo)
    # None si bid - d < CENTAVO (sin centavo factible)
    # 0.15 USD, 30 % -> 0.11 (26.7 %); 1.55 MXN -> 1.09 (29.7 %); 8.00 -> 5.60; 0.10 USD con piso 0.10 -> None"""
    raise NotImplementedError

def frontera(estacion: Estacion, cadena: CadenaBid | None, *, episodio: EpisodioVigente | None,
             excluida_en: frozenset[int], cerca_plataforma: dt.date | None,
             bid: Decimal | None, piso: Decimal, techo: Decimal) -> FronteraHoja:
    """Una sola función para el ciclo (decidir) y la cola (liberar).
    # red, fin = reduccion_vigente(cadena, en_baja=estacion.fase == "baja", dia=estacion.dia, episodio=episodio, bid=bid, piso=piso, techo=techo)
    # p = _pendiente_desde(estacion, cadena, episodio, excluida_en, bid, piso, techo)   # usa la misma
    # fase_hoja = "baja" si estacion.fase == "baja" o isinstance(p, RestauracionPendiente)
    #             si no estacion.fase
    # mult = estacion.mult_cortes_baja si fase_hoja == "baja" si no 1
    # ultimo = último movimiento de temporada confirmado de la cadena
    # cerca_hoja = ultimo.confirmado_el (fecha UTC) + 1 día; None si no hay o si < estacion.dia - 90
    # episodio_id = red.episodio_id si red, si no episodio.id si hay   (red resuelta, no cadena.reduccion)
    # fin = fin   (FronteraHoja.fin: TX3 lo guarda con temporada_io.registra_fines)
    # exclusion_hoy = bid si estacion.fase == "baja" y cadena.dueno_al_final
    #                 y (episodio is None or episodio.id not in excluida_en); si no None
    #                 (mano sin fecha vista hoy: TX3 la guarda en temporada_exclusion con este bid_visto)"""
    raise NotImplementedError

def inicio_episodio(episodio: EpisodioVigente | None, dia: dt.date) -> dt.datetime:
    """Desde cuándo una mano del dueño cuenta como de esta baja: el inicio del día UTC en que se abrió
    el episodio. El día en que TX3 lo abre, TX2 todavía no lo ve y usa `dia`. Así una mano de esa
    mañana protege la keyword ese día y los siguientes, aunque `abierto_el` sea posterior.
    # return datetime.combine(episodio.abierto_el.date() if episodio else dia, time(0), UTC)"""
    raise NotImplementedError

@dataclass(frozen=True)
class FinReduccion:
    episodio_id: int                                   # episodio de la reducción que termina
    motivo: Literal["ya_arriba", "mano_del_dueno"]
    excluye_abierto: bool                              # la mano se vio con otro episodio abierto
    bid_visto: Decimal | None = None                   # bid vigente al verse la mano; va a temporada_exclusion

def reduccion_vigente(cadena: CadenaBid, *, en_baja: bool, dia: dt.date, episodio: EpisodioVigente | None, bid: Decimal,
                      piso: Decimal, techo: Decimal) -> tuple[ReduccionVigente | None, FinReduccion | None]:
    """La única resolución de «¿esta hoja sigue reducida?». La usan pendiente, frontera (y por ella
    estampa y la revalidación de la cola) y TX3. cadena.reduccion ya omite las reducciones con fila en
    temporada_fin_reduccion (HistoriaBid.fines, congelado en inputs), así que un fin guardado no revive.
    # red = cadena.reduccion
    # if red is None or (episodio is not None and red.episodio_id == episodio.id): return red, None
    # manos = [m for m in cadena.manos_del_dueno if m > red.ultimo_el]
    # if manos or cadena.dueno_al_final:
    #     ahora = cadena.dueno_al_final or (en_baja and max(manos) >= inicio_episodio(episodio, dia))
    #     # en_baja sin episodio = TX3 lo abre en este ciclo; la exclusión va a ese episodio
    #     return None, FinReduccion(red.episodio_id, "mano_del_dueno", excluye_abierto=en_baja and ahora, bid_visto=bid)
    # if bid >= nivel_de_regreso(red, bid, piso, techo):
    #     return None, FinReduccion(red.episodio_id, "ya_arriba", excluye_abierto=False)
    # return red, None          # su regreso sigue pendiente (p. ej. retenido): sigue reducida"""
    raise NotImplementedError

def nivel_de_regreso(red: ReduccionVigente, bid: Decimal, piso: Decimal, techo: Decimal) -> Decimal:
    """A dónde regresa una hoja reducida. Con la temporada encendida nadie recorta en la baja (HOLD), así
    que es la base. Si Orbit recortó por evidencia después de la entrada (temporada apagada o en práctica
    a media baja), el regreso conserva esos recortes en proporción; nunca baja.
    # factor = 1 if red.solo_temporada_despues or bid >= red.ultimo_bid else bid / red.ultimo_bid
    # -> min(max((red.base * factor).quantize(CENTAVO, ROUND_FLOOR), piso), techo)
    # 8 -> 5.60 (entrada) -> 3.15 (dos recortes con la temporada apagada): 8 × 3.15 / 5.60 = 4.50, no 8.00"""
    raise NotImplementedError

def pendiente(caso: CasoHoja) -> Pendiente:
    """Decisiones 4 y 5. Requiere caso.temporada y caso.historia; delega en _pendiente_desde.
    # t = caso.temporada; e = t.estacion; bid = caso.bid.valor
    # bid None                                   -> SinPendiente("bid_heredado")
    # cad = recuperacion.cadena_bid(caso); None  -> SinPendiente("dato_faltante")
    # red, fin = reduccion_vigente(cad, en_baja=e.fase == "baja", dia=e.dia, episodio=t.episodio, bid=bid, piso=piso, techo=techo)
    # if fin is not None and fin.excluye_abierto                 -> SinPendiente("dueno_decidio")
    # if red is not None:                                        # la hoja lleva una reducción
    #     if red.episodio_id in t.excluida_en                   -> SinPendiente("dueno_decidio")
    #     if algún cad.manos_del_dueno > red.ultimo_el or cad.dueno_al_final
    #                                                            -> SinPendiente("dueno_decidio")
    #     if e.fase == "baja":
    #         if t.episodio and red.episodio_id == t.episodio.id and red.solo_temporada_despues:
    #             d = destino_entrada(red.base, e.reduccion_baja, piso, techo)
    #             if d is not None and abs(d - bid) >= CENTAVO -> AjustePendiente(d)
    #         -> SinPendiente("ya_entro")                       # nunca otra entrada
    #     nivel = nivel_de_regreso(red, bid, piso, techo)
    #     if bid >= nivel                                        -> SinPendiente("ya_arriba")
    #     -> RestauracionPendiente(nivel, red.episodio_id)
    # if e.fase != "baja"                                        -> SinPendiente("fuera_de_temporada")
    # if t.episodio and t.episodio.id in t.excluida_en           -> SinPendiente("dueno_decidio")
    # if algún cad.manos_del_dueno >= inicio_episodio(t.episodio, e.dia)
    #                                                            -> SinPendiente("dueno_decidio")
    # if cad.dueno_al_final                                      -> SinPendiente("dueno_decidio")
    #     # cambio sin registro: Amazon no da su fecha, cuenta como mano de esta baja; frontera la deja
    #     # en exclusion_hoy y TX3 la guarda fechada con bid_visto, así en la siguiente baja ya es vieja
    # d = destino_entrada(bid, e.reduccion_baja, piso, techo); None -> SinPendiente("sin_centavo_factible")
    # -> EntradaPendiente(d)"""
    raise NotImplementedError

def techo_de_baja(caso: CasoHoja) -> Decimal | None:
    """Techo común de R6 y R6r mientras la hoja está en baja (fase_hoja == "baja").
    # None si caso.temporada is None, fase_hoja != "baja", la cadena no tiene reducción o
    #   frontera.fin is not None (la reducción termina este ciclo: mano de David o ya arriba)
    #   (sin entrada no hay ahorro planeado que proteger; p. ej. excluida por David)
    # red = cadena.reduccion
    # r_efectiva = 1 - red.ultimo_bid / red.base           (la reducción real, con su centavo)
    # techo_eco = max(recuperacion.techo_conocido(caso, hasta=red.primer_el) or red.base, red.base)
    # -> (techo_eco * (1 - r_efectiva)).quantize(CENTAVO, ROUND_FLOOR)
    # 20 -> 10 (motor) -> 7 (entrada): techo_eco 20, r 0.30 -> 14.00
    # 8 -> 5.60 (entrada, sin recortes previos): techo_eco 8 -> 5.60 (en baja no hay qué recuperar)"""
    raise NotImplementedError

def meta_del_juicio(caso: CasoHoja) -> Decimal:
    """La meta con la que se juzgan R3-R13 (decisión 9). R0 y R1 siguen con la base.
    # base = caso.economia.target_acos_pct
    # t = caso.temporada
    # if t is None or t.frontera.fase_hoja != "alta" or t.meta_manual: return base
    # return max(base, t.meta_alta_pct)        # libre: puede pasar el equilibrio (decisión 4 del plan)
    # MX hoy: max(20.13, 25) = 25; US: max(19.05, 25) = 25 (arriba de su equilibrio 23.81: Ajustes avisa)"""
    raise NotImplementedError

def equilibrio_del_juicio(caso: CasoHoja) -> Decimal | None:
    """El límite de R3 en el juicio. En alta, si el dueño puso una meta arriba del equilibrio, aceptó perder
    hasta esa meta: R3 recorta solo arriba de ella. Fuera de alta, o con meta manual, el equilibrio de hoy.
    # eq = caso.economia.plataforma.equilibrio_acos_pct
    # if eq is None or eq <= 0: return eq
    # t = caso.temporada
    # if t is None or t.frontera.fase_hoja != "alta" or t.meta_manual: return eq
    # return max(eq, meta_del_juicio(caso))
    # US hoy en alta: max(23.81, 25) = 25; MX: max(40.26, 25) = 40.26"""
    raise NotImplementedError

def propuesta_presupuesto(
    *,
    estacion: Estacion | None,
    config: ConfigTemporada | None,
    episodio: EpisodioPresupuesto | None,         # el abierto, o el último cerrado
    historial: tuple[tuple[dt.date, Decimal], ...], # (vigente_desde, presupuesto) de la campaña
    hoy: dt.date,
    inicio_bloque_alto: dt.date | None,           # inicio de la preparación o del bloque alto vigente
    dias_topados_7d: int | None,
    topada: CampanaTopada,
    techo_humano: Decimal,                        # X: TECHOS_HUMANOS_POR_MONEDA (contrato de ARREGLOS 01)
    moneda: str,
) -> PropuestaPresupuesto | None:
    """Sección «Presupuestos». Sin dato -> None (no se inventa). Montos absolutos, uno por momento.
    # antes = presupuesto vigente al abrir el episodio (del historial)
    # cambios_desde(f) = cambios del historial con vigente_desde >= f
    # baja (episodio abierto): obj = q(antes * (1 - reduccion)); si hoy_vigente > obj y
    #     cambios_desde(episodio.abierto_el) vacío -> «baja»
    # fin de baja (hoy - episodio.cerrado_el < 30): si el único cambio desde abierto_el es
    #     exactamente obj (la propuesta aprobada) -> «fin_de_baja» a `antes`
    # previa o alta: dias_topados_7d >= topada.dias, aumento > 0 y ningún aumento por encima de `antes`
    #     desde inicio_bloque_alto (sin episodio: ningún aumento; volver a `antes` por «fin de la baja»
    #     no cuenta) -> min(q(vigente * (1 + aumento)), techo_humano) «alta_sin_presupuesto»
    # si aplican fin_de_baja y alta_sin_presupuesto, gana el monto mayor"""
    raise NotImplementedError

@dataclass(frozen=True)
class ConteoTemporada:
    """Lo que contó el ciclo (notes.temporada): confirmados, pendientes y excluidos por motivo."""
    entradas_confirmadas: int
    regresos_confirmados: int
    pendientes: int
    retenidos_por_presupuesto: int
    excluidas: Mapping[str, int]          # dueno_decidio, bid_heredado, sin_centavo_factible
    reactivaciones: int

def aviso_del_dia(config: ConfigTemporada, estacion: Estacion, *, conteo: ConteoTemporada,
                  propuestas: int) -> str | None:
    """Telegram en los días de evento y 3 días antes de entra_baja y restaura; None los demás."""
    raise NotImplementedError
```

```python
# app/optimizer/recuperacion.py

def modo_desde_settings(settings: Mapping, plataforma: str) -> Literal["shadow", "live"] | None:
    """ads_bid_recuperacion_<plat>; corre todo el año, con o sin temporada."""
    raise NotImplementedError

def insumos_desde_settings(settings, plataforma, modo) -> InsumosRecuperacion | None:
    """None sin modo; memoria_dias de ads_bid_recuperacion_memoria_dias_<plat> (default 365)."""
    raise NotImplementedError

def cadena_bid(caso: CasoHoja) -> CadenaBid | None:
    """historia.cambios sin ajustes de campaña, intercalados por instante con historia.manos_externas.
    None sin historia.
    # prev = None; manos = []; conocido = None        # conocido: el último bid que Orbit sabe que hubo
    # for e in ordenados(cambios + manos_externas):
    #   if e es ManoExterna: manos.append(e.instante); mano_bid = e.bid_visto
    #       if e.bid_visto is not None: conocido = e.bid_visto     # la exclusión fecha la mano
    #       continue
    #   c = e
    #   if c.origen == "regreso_del_dueno": manos.append(c.confirmado_el); mano_bid = c.bid_despues
    #   elif conocido is not None and abs(c.bid_antes - conocido) > TOLERANCIA_CADENA:
    #       manos.append(c.confirmado_el); mano_bid = c.bid_antes   # se fecha en el cambio posterior
    #   prev = c; conocido = c.bid_despues
    # dueno_al_final = conocido is not None and caso.bid.valor is not None
    #                  and abs(caso.bid.valor - conocido) > TOLERANCIA_CADENA
    #   (una mano ya guardada en temporada_exclusion con bid_visto deja de ser «sin fecha»: en la
    #    siguiente baja es una mano vieja y la entrada sí sale; plan B.2, casos 2 y 7)
    # reduccion: último c con origen "temporada"; si su motivo es "entra" o "ajusta" y su
    #   episodio_id no está en historia.fines ->
    #   ReduccionVigente(episodio_id=c.episodio_id,
    #                    base=bid_antes del primer c de ese episodio, primer_el=su instante,
    #                    ultimo_bid=c.bid_despues, ultimo_el=c.confirmado_el,
    #                    solo_temporada_despues=(no hay cambio de Orbit después de c))
    # ultimo_salto_recuperacion = el último cambio de Orbit lleva motivo recupera_nivel"""
    raise NotImplementedError

def techo_conocido(caso: CasoHoja, *, hasta: dt.datetime | None = None) -> Decimal | None:
    """El bid más alto que la hoja tuvo desde la última mano del dueño, dentro de memoria_dias.
    # dueno_al_final -> None (terreno de David: solo el R6 de hoy)
    # ancla = última mano del dueño, o el inicio de la memoria
    # niveles = [bid al ancla] + [bid_despues de cada cambio de Orbit posterior al ancla y < hasta]
    #   (bid al ancla = bid_de_la_ultima_mano, o bid_antes del primer cambio si no hubo mano)
    # -> min(max(niveles), caso.bid.techo); None sin cambios"""
    raise NotImplementedError

def multiplicador_sostenible(a_subida: Decimal, arg_subida: Decimal, confianza: Decimal) -> Decimal:
    """m* tal que gamma_q(a_subida, m* × arg_subida) == confianza. `arg_subida` es el argumento que
    politica._p_bajo pasa a gamma_q al CPC de hoy: clics_sub × cpc / (0.85 × meta/100 × ticket_sub)
    (politica.py:433-446). gamma_q es decreciente en x.
    # x* = bisección de 60 iteraciones en [0, a_subida + 40] bajo localcontext(PRECISION) sobre
    #      gamma_q(a_subida, x) >= confianza; determinista, replay fiel
    # -> x* / arg_subida   (arg_subida > 0 porque _fondo_juicio exige pedidos y venta > 0)"""
    raise NotImplementedError

def destino_recuperacion(caso: CasoHoja, *, a_subida: Decimal, arg_subida: Decimal) -> Decimal | None:
    """R6r. None = decide el R6 de hoy (la recuperación solo acelera, nunca frena).
    # if caso.recuperacion is None: return None
    # t = caso.temporada
    # cad = cadena_bid(caso)
    # if t and ((t.episodio and t.episodio.id in t.excluida_en) or (cad and cad.dueno_al_final)):
    #     return None                                   # terreno del dueño (decisión 7 del plan)
    # bid = caso.bid.valor; techo = techo_conocido(caso)
    # if techo is None or bid is None: return None
    # tb = temporada.techo_de_baja(caso)
    # techo = min(techo, caso.bid.techo, tb if tb is not None else techo)
    # if techo <= bid * (1 + PASO_SUBIDA): return None
    # m = multiplicador_sostenible(a_subida, arg_subida, caso.economia.plataforma.confianza_subida)
    # if m <= 1 + PASO_SUBIDA: return None
    # d = min(bid * m, techo).quantize(CENTAVO, ROUND_FLOOR)
    # return d if d > bid * (1 + PASO_SUBIDA) else None"""
    raise NotImplementedError

def presupuesto_topado(caso: CasoHoja) -> bool | None:
    """True si dias_topados >= presupuesto.topada.dias; None si se desconoce; False sin caso.presupuesto."""
    raise NotImplementedError
```

```python
# app/optimizer/politica.py — decide(caso); lo que no se nombra queda como hoy

MOTIVO_ENTRA_BAJA = "entra_temporada_baja"        # v_cambio_bid -> origen 'temporada', motivo 'entra'
MOTIVO_AJUSTA_BAJA = "ajusta_temporada_baja"      # David editó la reducción a media baja
MOTIVO_SALE_BAJA = "sale_temporada_baja"          # origen 'temporada', motivo 'sale'
MOTIVO_TEMPORADA_BAJA = "temporada_baja"          # recorte retenido en la baja
MOTIVO_TECHO_DE_BAJA = "techo_de_baja"            # subida que pasaría el techo de baja
MOTIVO_RECUPERA_NIVEL = "recupera_nivel"          # R6r
MOTIVO_PRESUPUESTO_TOPADO = "presupuesto_topado"
MOTIVO_REGRESO_ESPERA_PRESUPUESTO = "regreso_espera_presupuesto"   # solo para notes; no es veredicto final

def _paso_a_centavo(bid, destino: Decimal, motivo: str, nivel: NivelQueJuzga) -> Mover | Mantener:
    """Mover a un destino ya en centavos. Misma cola que _paso: valor > 0, destino en [piso, techo],
    |delta| >= MIN_DELTA_ABSOLUTO, -delta <= -CLAMP_FACTOR_MIN × valor, delta <= CLAMP_FACTOR_MAX ×
    valor. factor = destino / valor - 1 (informativo). Solo _paso y _paso_a_centavo crean un Mover."""
    raise NotImplementedError

def decide(caso: CasoHoja) -> Veredicto:
    # validación de target como hoy
    # t = caso.temporada; p = temporada.pendiente(caso) if t is not None else None
    #
    # RTs  regreso antes que la pausa (decisión 6)
    #      if isinstance(p, RestauracionPendiente):
    #          if presupuesto_topado(caso) is True: anota MOTIVO_REGRESO_ESPERA_PRESUPUESTO y sigue
    #              (fase_hoja ya es "baja": cortes ×2, cerca de la entrada, HOLD y techo de baja)
    #          else: return Regresar(p.nivel, MOTIVO_SALE_BAJA)
    #          (sin P: la espera por venta reciente no retiene un regreso planeado; sin R2)
    #
    # R0   PAUSE como hoy, con la meta BASE y con caso.pausa ya resuelta por la frontera
    #      (umbral, piso y exceso económico × mult si fase_hoja == "baja")
    #
    # RTe  if isinstance(p, EntradaPendiente):
    #          return _paso_a_centavo(caso.bid, p.destino, MOTIVO_ENTRA_BAJA, "hoja")
    #      if isinstance(p, AjustePendiente):
    #          bajada: _paso_a_centavo(...)
    #          subida: si presupuesto_topado(caso) is True -> sigue; si no Regresar(p.destino, MOTIVO_AJUSTA_BAJA)
    #      (la entrada no espera R2, P ni presupuesto: es una bajada planeada)
    #
    # R1   como hoy, salvo t and t.frontera.fase_hoja == "baja" -> no aplica
    # R2   como hoy
    # caso_j = replace(caso, economia=replace(caso.economia,
    #                  target_acos_pct=temporada.meta_del_juicio(caso),
    #                  plataforma=replace(caso.economia.plataforma,
    #                                     equilibrio_acos_pct=temporada.equilibrio_del_juicio(caso))))
    # juicio R3-R13 con caso_j; en R6:
    #     d = recuperacion.destino_recuperacion(caso_j, a_subida=..., arg_subida=...)
    #     si d: candidato = Recupera(d)                      (tipo interno, no sale de decide)
    #     si no: factor = PASO_SUBIDA con techo_extra = temporada.techo_de_baja(caso)
    # HOLD  t and fase_hoja == "baja" and factor < 0       -> Mantener(MOTIVO_TEMPORADA_BAJA)
    # P     subida (factor > 0 o Recupera) en espera de venta -> Mantener (contrato de ARREGLOS 01, P)
    # BUD   subida y caso.presupuesto presente:
    #           presupuesto_topado(caso) in (True, None)   -> Mantener(MOTIVO_PRESUPUESTO_TOPADO)
    # FRENOS
    #   ultimo.origen == "temporada"                      -> solo R17 (R2 ya pasó)
    #   Recupera tras recorte o sin cambio previo          -> solo R14; sin R15, R16, R16b
    #   Recupera tras subida (otro salto)                  -> R14-R16b como R6 (evidencia nueva al precio nuevo)
    #   resto                                              -> _freno_trayectoria como hoy
    # Recupera(d) -> Regresar(d, MOTIVO_RECUPERA_NIVEL)
    # _paso(caso.bid, factor, motivo, nivel, piso_aprendido, techo_extra=techo_de_baja si factor > 0)
    #   (si techo_extra deja el delta bajo un centavo -> Mantener(MOTIVO_TECHO_DE_BAJA))
    raise NotImplementedError
```

```python
# app/optimizer/cortes.py — el multiplicador es keyword OBLIGATORIA: ningún camino de corte puede
# olvidarlo sin que truene la llamada. Llamadores: cycle (pausa y negative) y apply_cola (al liberar).
def umbral_corte(evidencia, regla: Regla, *, mult_baja: Decimal | None) -> UmbralResuelto: ...
    # como hoy (cortes.py:118); con mult: ceil(umbral × mult)
def piso_corte(evidencia, platform: str, *, mult_baja: Decimal | None) -> PisoResuelto: ...
def piso_pausa(platform: str, *, mult_baja: Decimal | None) -> Decimal: ...
    # bid.PAUSE_COST_MIN[platform] × mult; sustituye la lectura directa de cycle.py:2270
def exceso_minimo(moneda: str, *, mult_baja: Decimal | None) -> Decimal: ...
    # bid.EXCESO_MINIMO[moneda] × mult (bid.py:135); llega a _decide_pause por InsumosPausa

# app/optimizer/bid.py — _decide_pause (bid.py:212) recibe `exceso_minimo: Decimal | None = None`;
# None = EXCESO_MINIMO[moneda] (semántica de hoy, replay intacto).

# app/optimizer/windows.py — la cerca entra en las dos ventanas de falla
def ventana_cortes(conn, ad_entity_id, decided_at, *, cerca: dt.date | None) -> AgregadoMetricas | None: ...
    # inicio = max(inicio_ventana(window_end), cerca); la completitud sigue pidiendo 7 fechas
def terminos_cortes(conn, ad_entity_id, decided_at, *, cerca: dt.date | None) -> TerminosCortes: ...
```

```python
# app/lecturas_caso.py

def lee_plataforma(conn, plataforma, decidido_el, *, economia, visto_el=None,
                   version_economia: str | None = None,          # (contrato de ARREGLOS 01, U)
                   temporada: TemporadaPlataforma | None = None,
                   recuperacion: InsumosRecuperacion | None = None,
                   campana_topada: CampanaTopada | None = None) -> LecturasPlataforma:
    """Lo de hoy (lecturas_caso.py:774), más, solo con algún interruptor encendido:
    # - historia: _SQL_CAMBIOS desde min(D-365, inicio del episodio más viejo con una hoja aún
    #   reducida), con instante, decision_id, motivo_temporada y episodio_id. La trayectoria de 90
    #   días se deriva como hoy.
    # - tramos con cerca: la hoja con su cerca (unnest(hoja_id[], cerca[]) en _SQL_TRAMOS; el rango
    #   empieza en greatest(desde, cerca)); grupo y cuenta con cerca_plataforma. Rango vacío = cero
    #   medido. En práctica _SQL_TRAMOS corre dos veces (con y sin cerca).
    # - presupuesto por campaña: último presupuesto_diario de ads_campana_config_observation y, por
    #   día con ingesta de los últimos 7, la suma del gasto de sus hojas contra topada.pct."""
    raise NotImplementedError

class LecturasPlataforma:
    def frontera(self, hoja_id: int, bid_vigente: Decimal | None) -> FronteraHoja | None:
        """temporada.frontera sobre lo leído, sin consulta."""
    def tiene_pendiente(self, hoja_id: int, bid_vigente: Decimal | None) -> bool:
        """¿Entrada, ajuste o regreso pendiente? Lo usa el ciclo para no saltar una inerte en vivo."""
    def casos(self, conn, *, hoja_id, ad_group_id, bid, target_acos_pct, procedencia, pausa,
              equilibrio_acos_pct=None) -> tuple[CasoHoja, CasoHoja | None]:
        """(caso que decide, caso de sombra). Reemplaza a `caso` (se migran sus dos llamadores,
        _procesa_decisora y _juicio_hoja). Live: el que decide trae los marcadores y la sombra es
        None. Shadow: el que decide va sin marcadores (byte a byte lo de hoy) y la sombra los trae,
        con su propia pausa resuelta (×mult y cerca)."""
```

```python
# app/temporada_io.py — lecturas en TX2 y la escritura de TX3. Nada más escribe episodios.

@dataclass(frozen=True)
class EstadoTemporada:
    episodio: EpisodioVigente | None
    efectos_abiertos: Mapping[int, Literal["en_vuelo", "obsoleto"]]  # por hoja
    excluidas: Mapping[int, frozenset[int]]                          # hoja -> episodios
    cerca_plataforma: dt.date | None

def lee_estado(conn, platform: str, hoy: dt.date) -> EstadoTemporada:
    """Solo SELECT (TX2). Un efecto abierto es 'obsoleto' si su decisión ya tiene desenlace
    (decision_application, decision_sin_aplicar) o si es de un ciclo anterior sin intento en el
    ledger (cycle.py:3529-3530: solo se envían los bids del ciclo); 'en_vuelo' si su ledger quedó
    sin sello (ya lo bloquea K: apply.hojas_con_bid_sin_sello, apply.py:742). Una hoja con efecto
    'en_vuelo' no recibe decisión de temporada; una con efecto 'obsoleto' sí (TX3 lo cierra antes).
    cerca_plataforma = día siguiente a la primera confirmación de la última transición (entrada o
    salida) de la plataforma, si cae en los últimos 90 días."""
    raise NotImplementedError

def registra_ciclo(conn, platform: str, *, cycle_id: int, decidido_el: dt.datetime,
                   plataforma: TemporadaPlataforma | None) -> int | None:
    """TX3, antes de insertar decisiones. Solo en modo live. Devuelve el episodio abierto o None.
    # 1. reconcilia: UPDATE temporada_efecto SET cerrado_el, desenlace para cada efecto abierto cuya
    #    decisión tiene desenlace (confirmado | divergencia | sin_aplicar) o quedó abandonada
    # 2. fase == "baja" y sin episodio abierto -> INSERT temporada_episodio (índice único por
    #    plataforma: un segundo INSERT concurrente falla y el ciclo falla cerrado)
    #    fase != "baja" y episodio abierto      -> UPDATE cerrado_el, cerrado_en_ciclo
    #    fase == "baja" y episodio abierto con abierto_el < estacion.inicio_bloque (es de otra baja: la
    #    temporada estuvo apagada o en práctica entre las dos) -> cerrarlo e INSERT uno nuevo"""
    raise NotImplementedError

def registra_fines(conn, platform: str, *, cycle_id: int, episodio_abierto: int | None,
                   fines: Mapping[int, FinReduccion], exclusiones: Mapping[int, Decimal],
                   moneda: str) -> None:
    """TX3, en la misma transacción que el INSERT de decisiones. Solo en modo live.
    # por cada (ad_entity_id, fin): INSERT temporada_fin_reduccion ON CONFLICT DO NOTHING
    # si fin.excluye_abierto: INSERT temporada_exclusion(episodio_abierto, ad_entity_id,
    #                         'mano_del_dueno', fin.bid_visto, moneda) ON CONFLICT DO NOTHING
    # por cada (ad_entity_id, bid) de exclusiones (mano sin fecha vista hoy, sin reducción previa):
    #     INSERT temporada_exclusion(episodio_abierto, ad_entity_id, 'mano_del_dueno', bid, moneda)
    #     ON CONFLICT DO NOTHING
    # Con bid_visto, esa mano queda fechada: HistoriaBid.manos_externas la lee y en la siguiente baja
    # ya no bloquea la entrada (plan B.2, casos 2 y 7)."""
    raise NotImplementedError

def estampa(pendiente: _Pendiente, episodio_id: int | None) -> _Pendiente:
    """inputs.temporada_episodio_id = frontera.episodio_id o el abierto en este TX3, en las
    decisiones de temporada y en las pausas con fase_hoja == "baja". Fuera de inputs.caso: el replay
    no lo lee."""
    raise NotImplementedError

def abre_efectos(conn, episodio_id, pendientes, decision_ids) -> None:
    """TX3, después del INSERT de decisiones: una fila temporada_efecto por decisión entra/ajusta/sale.
    Índice único 'uno pendiente por hoja': si choca, es un bug (TX2 ya excluyó hojas en vuelo) y el
    ciclo falla cerrado."""
    raise NotImplementedError

def frontera_hoja(conn, ad_entity_id: int, hoy: dt.date) -> FronteraHoja | None:
    """La misma temporada.frontera para UNA hoja, para la revalidación de la cola al liberar."""
    raise NotImplementedError

def episodios(conn, platform: str) -> tuple[EpisodioPresupuesto, ...]:
    """El abierto y el último cerrado, para la pantalla de dinero."""
    raise NotImplementedError

@dataclass(frozen=True)
class Reactivacion:
    ad_entity_id: int
    queue_id: int
    pausa_decision_id: int
    episodio_id: int
    def como_json(self) -> dict: ...

_SQL_PAUSAS_DE_BAJA = """
SELECT q.ad_entity_id, q.id, q.decision_id, e.id
  FROM apply_queue q
  JOIN decision d               ON d.id = q.decision_id
  JOIN decision_application da  ON da.decision_id = d.id AND da.verify_ok IS TRUE
  JOIN temporada_episodio e     ON e.id = (d.inputs->>'temporada_episodio_id')::bigint
                               AND e.cerrado_el IS NOT NULL                -- su baja ya terminó
  JOIN ad_entity_state s        ON s.ad_entity_id = q.ad_entity_id
 WHERE q.platform = %s::platform AND q.kind = 'pause' AND q.modo = 'live' AND q.estado = 'applied'
   AND d.inputs->'caso'->'temporada'->'frontera'->>'fase_hoja' = 'baja'
   AND s.status = 'PAUSED'                                                  -- ENABLED: la reactivó David
   AND NOT (q.ad_entity_id = ANY(%s))                                       -- «Mantener pausada»
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                    WHERE a.decision_id = q.decision_id AND a.tipo = 'reversa'
                    GROUP BY a.decision_id
                   HAVING bool_or(a.resultado = 'ok') OR count(*) >= 3)
   AND NOT EXISTS (SELECT 1 FROM reactivacion_manual r
                    WHERE r.ad_entity_id = q.ad_entity_id AND r.detectada_en > q.applied_at)
   AND q.id = (SELECT q2.id FROM apply_queue q2
                WHERE q2.ad_entity_id = q.ad_entity_id AND q2.kind = 'pause' AND q2.estado = 'applied'
                ORDER BY q2.applied_at DESC LIMIT 1)
"""

def reactivaciones_pendientes(conn, platform: str, mantener: frozenset[int]) -> tuple[Reactivacion, ...]:
    """Solo SELECT. Idempotente: una reversa ok o agotada sale del conjunto."""
    raise NotImplementedError
```

```python
# app/cycle.py
def pendiente_reactivacion(r: Reactivacion, estacion: Estacion, *, decidido_el) -> _Pendiente:
    """decision kind 'resume' (ya en el enum, migrations/0001_initial.sql:40), sin dinero;
    inputs = {"motor": "temporada", "version": "temporada_v1", "pausa_decision_id", "queue_id",
    "temporada_episodio_id", "estacion": como_json(estacion)}."""
    raise NotImplementedError

# app/apply.py
def prioridad_bajo_cupo(veredicto_kind: str, motivo: str) -> int:
    # (apply.py:562) 0: Regresar (sale_temporada_baja, ajusta hacia arriba, recupera_nivel) y
    #    regreso_por_desplome; 1: entra_temporada_baja junto a pierde_dinero* y gasto_sin_venta*;
    #    resto igual. Con el cupo de hoy (1,000,000 operaciones) casi nunca decide nada.
    raise NotImplementedError

MOTIVO_BID_CAMBIADO_FUERA = "bid_cambiado_fuera"     # nuevo en MOTIVOS_SIN_APLICAR (apply.py:262)

class Aplicador:
    def aplica_bids(self, decisiones, *, escalera_global=None) -> ResultadoAplicador:
        """Como hoy, más la lectura fresca ANTES de la cuota y del PUT, para toda decisión de bid:
        # leido = self._readback(cliente, identidad)       (el mismo GET del readback)
        # if leido is None: registra_sin_aplicar(..., MOTIVO_SIN_RESPUESTA); continue
        # if abs(leido - decision.old_value) > TOLERANCIA_CADENA:
        #     with conn.transaction():
        #         registra_sin_aplicar(..., MOTIVO_BID_CAMBIADO_FUERA, detalle={"leido": leido})
        #         if decision es de temporada:
        #             INSERT temporada_exclusion(episodio, hoja, 'mano_del_dueno', leido, decision.id)
        #         _actualiza_cache(conn, decision.ad_entity_id, leido)
        #     continue                                       # cero PUT del destino viejo
        # luego cuota, ledger, PUT y readback como hoy (apply.py:1356-1441)
        Amazon no ofrece compare-and-set: entre este GET y el PUT queda una ventana corta."""
        raise NotImplementedError

    def aplica_reactivaciones(self, conn, decisiones, *, tick=None) -> ResumenReactivacion:
        """Por decisión resume: revalida (temporada live; campaña y grupo ENABLED; goal habilitado y
        no 'off'; sin pausa de producto en vuelo del grupo; campaña no topada; hoja PAUSED y fuera de
        mantener_pausadas); cuota 'pause' (consume_quota_y_sello, como apply_cola.py:1725); luego
        apply_cola.reversa_pause(conn, self._cliente(), fila) (apply_cola.py:1765): ledger 'reversa'
        sobre la pausa original, readback, tope de 3 que no se reinicia aunque haya otra decisión
        resume. Sin cupo o con una guarda en contra, queda sin aplicar con motivo y mañana se vuelve
        a decidir."""
        raise NotImplementedError

def _confirma_resumen(conn, decision_id, ack, verify_ok, cycle_id_ejecutor) -> None:
    """(apply.py:1091) Como hoy, más, en la MISMA transacción:
    # UPDATE temporada_efecto SET cerrado_el = now(),
    #        desenlace = CASE WHEN verify_ok THEN 'confirmado' ELSE 'divergencia' END
    #  WHERE decision_id = %s AND cerrado_el IS NULL
    # si divergencia y la decisión es de temporada: INSERT temporada_exclusion(..., 'divergencia', leido)
    #   solo si el bid leído difiere también del old_value de la decisión. Si Amazon tiene el bid de
    #   antes, el cambio no llegó (caída entre el intento y el PUT, o 5xx): sin exclusión, el efecto
    #   cierra y mañana la hoja se deriva otra vez.
    Cubre los tres caminos de confirmación: aplica_bids (:1439) y reconcilia_bids (:814, :826)."""

def registra_sin_aplicar(conn, decision_id, cycle_id, motivo, *, detalle=None) -> None:
    """(apply.py:285) Como hoy, más el cierre del efecto con desenlace 'sin_aplicar'
    ('mano_del_dueno' si motivo == MOTIVO_BID_CAMBIADO_FUERA). Idempotente."""

# app/apply_cola.py
_SQL_PAUSE_PROPIO = """
SELECT EXISTS (
    SELECT 1 FROM decision_application da JOIN decision d ON d.id = da.decision_id
     WHERE d.ad_entity_id = %s AND d.kind = 'pause' AND da.verify_ok IS TRUE
       AND NOT EXISTS (
           SELECT 1 FROM decision r
            WHERE r.kind = 'resume' AND r.inputs->>'motor' = 'temporada'
              AND (r.inputs->>'pausa_decision_id')::bigint = d.id
              AND EXISTS (SELECT 1 FROM apply_attempt a
                           WHERE a.decision_id = d.id AND a.tipo = 'reversa' AND a.resultado = 'ok'))
)
"""
# La reversa con el botón del dueño sigue contando como reactivación del dueño (semántica de hoy).
# La revalidación de pausa y negative usa temporada_io.frontera_hoja: mult y cerca de esa hoja.

# app/apply.py — escritor de presupuestos (H1)
def _aplica_y_confirma(conn, cliente, plataforma, clase, destino, ajuste, *, ajuste_id, llave, previa):
    """(apply.py:2087) La llave del candado pasa a ser por campaña y campo:
    # llave = f"campana:{destino.campana_id}:{campo_del_ajuste(ajuste)}"   (también al regresar)
    # bajo el candado y ANTES del PUT:
    #   si existe un campana_ajuste aceptado posterior (id mayor) de la misma campaña y campo
    #     -> la orden quedó vieja: sin PUT, se marca invalidada y el endpoint responde 409 con
    #        «Ya mandaste otra orden para esta campaña después de esta. Recarga.»
    #   si el campo vigente no es el de `previa` -> HuellaDesactualizada (re-planea), sin PUT
    # después, PUT + readback + sello como hoy (apply.py:2138-2153)"""

# app/config_write.py
def proxima_config(actual, platform, *, ...,                        # parámetros de hoy
                   temporada: Mapping | Literal["off"] | None = None,
                   campana_topada: Mapping | None = None,
                   temporada_modo: Mapping[str, str] | None = None,   # plataforma -> off|shadow|live
                   meta_alta: Mapping[str, str] | None = None,
                   recuperacion_modo: str | None = None,
                   recuperacion_memoria_dias: int | None = None) -> tuple[dict, list[str]]:
    """"off" quita la clave. La resultante se valida con los mismos lectores del ciclo
    (temporada.desde_settings, modo_desde_settings, meta_alta_desde_settings,
    campana_topada_desde_settings, recuperacion.modo_desde_settings) de las dos plataformas.
    guarda_config agrega `resumen_temporada` a su respuesta cuando la edición toca la temporada."""

# GET /api/ads-optimizer/temporada/vista-previa?meses=...&reduccion_baja_pct=...  (sin token)
#   -> temporada.resumen(temporada.valida(query), hoy, antes=temporada.desde_settings(vigente), ...)
#   -> 422 con el mensaje de valida

# app/optimizer/replay.py — reproduce() (replay.py:137)
#   motor == "temporada" (resume) -> ("resume", None, None)
#   politica niveles_v3: igual (decide lee los marcadores congelados)
```

### Migración

`migrations/NNNN_temporada01_episodio.sql` y su reversa. El número se elige al integrar: hoy 0084 y
0085 los toman ARREGLOS Q e Y (`plans/arreglos-01.md:229`, `:333`), así que será la primera libre
después de ARREGLOS 01. Sin `BEGIN/COMMIT`: el despliegue la aplica en una sola transacción, como 0080.

```sql
-- sonda-aplicada: SELECT CASE WHEN to_regclass('public.temporada_episodio') IS NULL OR to_regclass('public.temporada_fin_reduccion') IS NULL THEN false ELSE has_table_privilege('app_decide', 'public.temporada_episodio', 'INSERT') AND pg_get_viewdef('public.v_cambio_bid'::regclass) LIKE '%temporada%' END
-- Consulta previa (0 filas antes de desplegar; los motivos nacen en este plan):
--   SELECT count(*) FROM decision
--    WHERE inputs->>'motivo' IN ('entra_temporada_baja','ajusta_temporada_baja','sale_temporada_baja');

-- 1. Origen 'temporada'. Se redefine la vista de 0067 (0067:65-100); las dos columnas nuevas van al
--    final (CREATE OR REPLACE VIEW solo deja agregar columnas al final); las otras ramas dan NULL.
CREATE OR REPLACE VIEW v_cambio_bid AS
SELECT d.ad_entity_id AS hoja_id, da.confirmed_at AS confirmado_el, d.old_value AS bid_antes,
       d.new_value AS bid_despues, d.value_currency AS moneda,
       CASE WHEN d.inputs->>'motivo' = 'regreso_por_desplome' THEN 'regreso_por_desplome'
            WHEN d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja',
                                         'sale_temporada_baja') THEN 'temporada'
            ELSE 'motor' END AS origen,
       d.id AS decision_id,
       CASE d.inputs->>'motivo' WHEN 'entra_temporada_baja' THEN 'entra'
                                WHEN 'ajusta_temporada_baja' THEN 'ajusta'
                                WHEN 'sale_temporada_baja' THEN 'sale' END AS motivo_temporada,
       (d.inputs->>'temporada_episodio_id')::bigint AS episodio_id
  FROM ...   -- igual que 0067; las ramas 2 y 3 agregan NULL, NULL

-- 2. Episodio: uno abierto por plataforma; solo se cierra.
CREATE TABLE temporada_episodio (
    id                 BIGSERIAL PRIMARY KEY,
    platform           platform    NOT NULL,
    abierto_el         TIMESTAMPTZ NOT NULL,
    abierto_en_ciclo   BIGINT      NOT NULL REFERENCES optimizer_cycle(id),
    config_version_id  BIGINT      NOT NULL REFERENCES config_version(id),
    cerrado_el         TIMESTAMPTZ NULL,
    cerrado_en_ciclo   BIGINT      NULL REFERENCES optimizer_cycle(id),
    CHECK ((cerrado_el IS NULL) = (cerrado_en_ciclo IS NULL))
);
CREATE UNIQUE INDEX temporada_episodio_uno_abierto ON temporada_episodio (platform)
    WHERE cerrado_el IS NULL;

-- 3. Efecto: la decisión de temporada de una hoja mientras no tiene desenlace. Uno abierto por hoja.
CREATE TABLE temporada_efecto (
    id            BIGSERIAL PRIMARY KEY,
    episodio_id   BIGINT      NOT NULL REFERENCES temporada_episodio(id),
    ad_entity_id  BIGINT      NOT NULL REFERENCES ad_entity(id),
    decision_id   BIGINT      NOT NULL UNIQUE REFERENCES decision(id),
    tipo          TEXT        NOT NULL CHECK (tipo IN ('entra', 'ajusta', 'sale')),
    abierto_el    TIMESTAMPTZ NOT NULL DEFAULT now(),
    cerrado_el    TIMESTAMPTZ NULL,
    desenlace     TEXT        NULL CHECK (desenlace IN
                      ('confirmado', 'divergencia', 'sin_aplicar', 'mano_del_dueno', 'abandonado')),
    CHECK ((cerrado_el IS NULL) = (desenlace IS NULL))
);
CREATE UNIQUE INDEX temporada_efecto_uno_pendiente ON temporada_efecto (ad_entity_id)
    WHERE cerrado_el IS NULL;

-- 3b. Fin de una reducción sin enviar nada (ya_arriba o mano del dueño). Append-only: un fin no revive.
CREATE TABLE temporada_fin_reduccion (
    episodio_id   BIGINT      NOT NULL REFERENCES temporada_episodio(id),
    ad_entity_id  BIGINT      NOT NULL REFERENCES ad_entity(id),
    motivo        TEXT        NOT NULL CHECK (motivo IN ('ya_arriba', 'mano_del_dueno')),
    cycle_id      BIGINT      NOT NULL,
    cerrado_el    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (episodio_id, ad_entity_id)
);

-- 4. Exclusión durable: la mano de David vista por el aplicador. Append-only.
CREATE TABLE temporada_exclusion (
    episodio_id   BIGINT      NOT NULL REFERENCES temporada_episodio(id),
    ad_entity_id  BIGINT      NOT NULL REFERENCES ad_entity(id),
    motivo        TEXT        NOT NULL CHECK (motivo IN ('mano_del_dueno', 'divergencia')),
    bid_visto     NUMERIC(14,4) NULL,
    bid_visto_moneda currency   NULL,
    decision_id   BIGINT      NULL REFERENCES decision(id),
    detectada_el  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (episodio_id, ad_entity_id),
    CHECK ((bid_visto IS NULL) = (bid_visto_moneda IS NULL))
);

-- Triggers solo-cierre en episodio y efecto (patrón de 0069/0078: el único UPDATE válido pasa
-- cerrado_el de NULL a un valor; con search_path fijo como 0080).
-- 5. decision_sin_aplicar admite 'bid_cambiado_fuera' (CHECK de 0044, ampliado por 0045 y 0081), con el
--    mismo DROP/ADD CONSTRAINT decision_sin_aplicar_motivo_check de 0081. MOTIVOS_SIN_APLICAR lo suma en el
--    mismo bloque (test_apply_schema exige que coincidan).
-- 6. Permisos mínimos:
GRANT SELECT, INSERT ON temporada_episodio, temporada_efecto, temporada_exclusion, temporada_fin_reduccion TO app_decide;
GRANT UPDATE (cerrado_el, cerrado_en_ciclo) ON temporada_episodio TO app_decide;
GRANT UPDATE (cerrado_el, desenlace) ON temporada_efecto TO app_decide;
GRANT USAGE ON SEQUENCE temporada_episodio_id_seq, temporada_efecto_id_seq TO app_decide;
GRANT SELECT ON temporada_episodio, temporada_efecto, temporada_exclusion, temporada_fin_reduccion TO app_read, app_admin;
```

**Reversa.** Falla con mensaje claro si hay filas en `temporada_episodio` o decisiones con los motivos
nuevos («ya hubo episodios de temporada; desactiva la temporada y conserva el rastro»). Si no hay,
restaura la vista de 0067, el CHECK anterior de `decision_sin_aplicar` y borra las cuatro tablas, primero
`temporada_fin_reduccion` y `temporada_exclusion` (apuntan al episodio).

### Mapa de módulos

| Módulo | Qué cambia | Dueño de |
| --- | --- | --- |
| `app/optimizer/temporada.py` (nuevo, puro) | config y lectores, `resuelve`, `eventos`, `resumen`, `destino_entrada`, `frontera`, `pendiente`, `techo_de_baja`, `meta_del_juicio`, `propuesta_presupuesto`, `aviso_del_dia` | qué día es qué fase y qué le toca a cada keyword y campaña |
| `app/optimizer/recuperacion.py` (nuevo, puro) | `cadena_bid`, `techo_conocido`, `multiplicador_sostenible`, `destino_recuperacion`, `presupuesto_topado`, lectores | la memoria del bid |
| `app/optimizer/caso.py` | `OrigenCambio` + `temporada`; `CambioFechado`, `HistoriaBid`, `PresupuestoCampana`, `InsumosRecuperacion`, `TemporadaCaso`; `InsumosPausa.exceso_minimo`; `_objeto_con_opcionales` | el formato congelado |
| `app/optimizer/politica.py` | RTs antes de R0, RTe, `_paso_a_centavo`, guarda de R1, meta del juicio, HOLD, techo de baja, R6r, BUD, frenos | el orden de las reglas |
| `app/optimizer/cortes.py`, `bid.py`, `windows.py` | `mult_baja` obligatorio, exceso económico, `cerca` | cuánto exige un corte y qué ventana mira |
| `app/lecturas_caso.py` | historia con instantes, cerca por hoja, presupuesto, `frontera`, `tiene_pendiente`, `casos` | lo que se lee una vez por plataforma |
| `app/temporada_io.py` (nuevo) | `lee_estado`, `registra_ciclo`, `estampa`, `abre_efectos`, `registra_fines`, `frontera_hoja`, `episodios`, `reactivaciones_pendientes` | el episodio y sus efectos |
| `app/cycle.py` | estación, frontera, inertes en vivo, sombra, TX3 de temporada, reactivaciones, aviso, `notes.temporada` | orquestación |
| `app/apply.py`, `app/apply_cola.py` | lectura fresca, exclusión, cierre de efectos en la confirmación, `aplica_reactivaciones`, `_SQL_PAUSE_PROPIO`, revalidación con frontera, candado de presupuesto por campaña | escribir a Amazon |
| `migrations/NNNN` | vista, episodio, efecto, exclusión, motivo, permisos | la identidad durable |
| `app/pantalla_danadas.py` | la racha ignora `origen = 'temporada'` | qué keyword está dañada |
| `app/pantalla_dinero.py`, `app/avisos_campana.py`, `static/js/dinero.js` | `propuesta_temporada`, botón «Revisar», `ads_campana_topada` | dónde poner el dinero |
| `app/config_write.py`, `app/api_write.py`, `app/api_dashboard.py`, `templates/settings.html`, `static/js/settings.js` | tarjeta, campos de plataforma, vista previa, «Terminar la baja hoy», «Mantener pausada» | settings |
| `app/optimizer/replay.py` | motor `temporada` (resume) | replay |
| `.cursor/skills/verify-orbit/features/` | fichas de Ajustes, Dónde poner el dinero y Keywords dañadas | verificación de pantallas |

Para seguir una decisión de bid: `cycle._procesa_decisora` → `politica.decide` → `temporada` /
`recuperacion` (los dos puros). Para seguir un episodio: `temporada_io.registra_ciclo` (TX3) →
`apply._confirma_resumen` (confirmación) → `v_cambio_bid`.

### Invariantes y dónde viven

| Invariante | Dónde |
| --- | --- |
| Lo enviado nunca baja más que la reducción (≤ 30 %) | `temporada.destino_entrada` (centavo hacia arriba) + `_paso_a_centavo` (magnitud) + `valida` (rango) |
| Una keyword reducida nunca recibe otra entrada en el mismo episodio | `pendiente`: con `cadena.reduccion` vigente solo hay ajuste del mismo episodio o regreso; la reducción de un episodio anterior que ya llegó a su nivel o que el dueño movió termina en `reduccion_vigente` (la única resolución, compartida por `pendiente`, `frontera`, `estampa` y TX3) y queda en `temporada_fin_reduccion`, que no se borra |
| Dos decisiones de temporada de la misma hoja nunca están abiertas a la vez | índice `temporada_efecto_uno_pendiente` + `lee_estado` (en vuelo) |
| Un episodio abierto por plataforma | índice `temporada_episodio_uno_abierto` |
| Una caída después de confirmar no produce otro recorte | el efecto se cierra en la misma transacción que `decision_application`; la cadena confirmada dice «reducida» |
| El regreso nunca baja un bid ni pisa a David | `pendiente` (`ya_arriba`, `dueno_decidio`) + `Regresar` solo si sube |
| Si Amazon ya no tiene el bid que Orbit vio, no se manda nada y esa keyword queda fuera del episodio | `Aplicador.aplica_bids` (lectura fresca) + `temporada_exclusion` |
| Un regreso pendiente no lo tapa una pausa | RTs antes de R0 en `decide` |
| Los cortes solo juzgan evidencia posterior al último movimiento de temporada de la hoja | `temporada.frontera` (cerca = día siguiente) + `windows` + tramos |
| La baja multiplica todos los cortes, al decidir y al liberar | `cortes.py` con `mult_baja` obligatorio; `frontera_hoja` en la cola |
| R1 nunca regresa un movimiento de temporada | origen `temporada` en `v_cambio_bid` + guarda de fase |
| En baja no hay recortes por evidencia ni subidas sobre el techo de baja | HOLD + `techo_de_baja` en R6 y R6r |
| En alta la meta del juicio es `max(base, meta_alta)` y R3 no recorta debajo de la meta que aceptó el dueño | `meta_del_juicio`, `equilibrio_del_juicio` |
| Ningún presupuesto cambia sin `APLICAR AJUSTE`; una orden vieja nunca pisa una nueva | la temporada solo produce `PropuestaPresupuesto`; candado por campaña y campo en `_aplica_y_confirma` |
| Solo se reactivan pausas de Orbit, confirmadas, decididas en baja, de un episodio cerrado | `_SQL_PAUSAS_DE_BAJA` + guardas de `aplica_reactivaciones` |
| El replay de filas viejas no cambia | marcadores opcionales; consulta previa en 0 para la vista |

### Lo que el diseño no hace

- No escribe presupuestos solo. No estima índices estacionales.
- No revierte negatives de la baja: quedan marcados (`inputs.caso.temporada`). Deshacer uno es una
  llamada a `POST /reversa/negative` (`app/api_write.py:409`), que hoy no tiene botón en ninguna
  pantalla; la temporada no agrega esa lista.
- No toca bids heredados del ad group (`bid.valor` None, `app/optimizer/caso.py:296`): quedan como
  exclusión visible.
- No cambia la meta de pausas, negatives ni harvest en alta. No adelanta la meta de alta a la
  preparación.
- No reactiva pausas de producto (AUTO-03 sigue en práctica) ni pausas anteriores al encendido.
- No separa en «Ruido» la parte planeada del encogimiento del bid: esa pantalla mide el bid real.

---

## Recuperación rápida (R6r)

Corre todo el año, con o sin temporada, detrás de `ads_bid_recuperacion_<plat>`.

### Por qué hace falta

Hoy la única subida es R6: +15 % por paso (`politica.py:28`, `:340`) si P(ACoS < 0.85 × meta) ≥
`confianza_subida` (`politica.py:338-340`, `_p_bajo` en `:433-446`). Cada paso pide 7 días (R2), 20
clics nuevos al bid nuevo (R15, `politica.py:36`), +10 % de impresiones (R16, `politica.py:39`) y que
el juicio económico M6 no frene. Para volver de 27 % a 100 % del bid hay que multiplicar por 3.70:
`ln 3.70 / ln 1.15 = 9.36`, o sea 10 pasos y al menos 70 días. Con un bid recortado, una keyword escasa
recibe 1 a 2 clics por día, así que juntar 20 clics toma 10 a 20 días por paso. Y con 2 pedidos en 90
días, la semana posterior a una subida casi siempre tiene 0 ventas; la v2 del juicio económico frena
cuando la semana posterior no vende más que la anterior, aunque las dos tengan 0 *(contrato de
ARREGLOS 01, U: `plans/arreglos-01.md:281`)*. En la práctica, la keyword no regresa.

### La regla

R6r calcula el bid más alto que la evidencia sostiene, con la misma gamma y la misma previa de R6:

- `arg_subida` = `clics_sub × cpc / (0.85 × meta / 100 × ticket_sub)`, el argumento que `_p_bajo` pasa a
  `gamma_q` al CPC de hoy (`politica.py:444-446`; `gamma_q` en `app/optimizer/evidencia.py:46`). La meta
  es la del juicio (25 % en alta).
- `m*` es el multiplicador del CPC con el que `gamma_q(a_subida, m* × arg_subida)` cae exactamente a
  `confianza_subida`. El CPC se proyecta lineal con el bid, lo cual es pesimista: en una subasta de
  segundo precio el CPC crece menos que el bid.
- Destino = `floor_centavo(min(bid × m*, techo_conocido, techo del goal, techo_de_baja))`.
- Solo actúa si el destino pasa de `bid × 1.15`; si no, decide el R6 de hoy. R6r nunca es más lenta que
  hoy.
- `techo_conocido` = el bid más alto que la hoja tuvo desde la última mano de David, dentro de la memoria
  (365 días por defecto). Si el bid vigente no es el último de Orbit, es terreno de David y no hay R6r.

**Propiedad clave.** Con la misma evidencia, `bid × m*` es el mismo número sin importar cuántos pasos se
den: repetir no acumula riesgo. Solo sube más si llega evidencia nueva.

### Frenos

- Se quedan: R2 (7 días desde cualquier cambio), R14 (dirección contraria sin 20 clics nuevos y menos
  de 14 días), P (espera por venta reciente) *(contrato de ARREGLOS 01)*, BUD y R17.
- El **primer salto** después de un recorte se salta R15, R16 y el juicio económico: esos frenos piden
  repetir pasos chicos para medir, y aquí el destino ya sale de la evidencia.
- Un **segundo salto** seguido (el último cambio fue una subida, incluido otro `recupera_nivel`) pasa por
  todos los frenos de R6: 20 clics nuevos al precio nuevo y un juicio económico comparable. El CPC del
  primer salto tiene que estar medido antes de otro.
- Si el último cambio fue de temporada, solo R17: un movimiento planeado no se juzga como si buscara un
  efecto.

### Topes de dinero (todos a la vez)

- **Meta:** el ACoS proyectado en el destino queda bajo 0.85 × meta con la confianza de subida.
- **Techo:** nunca más arriba del bid más alto conocido ni del techo del goal (45 MXN / 2.50 USD).
- **Baja:** en baja, ni R6 ni R6r pasan de `techo_de_baja`.
- **Presupuesto:** si la campaña se queda sin presupuesto (definición única, 90 % en 5 de 7 días por
  defecto) o no se sabe, no hay subida: el bid más alto solo encarece el clic.
- **Reversible:** si la evidencia nueva sale peor, R3/R4 recortan como hoy.

### Aritmética

Caso tipo, calculado con `evidencia.gamma_q` del repo: bid de 8.00 MXN antes de su cadena de recortes,
hoy en 2.16 (27 %), CPC de 1.90 al bid de hoy, ticket de 450, meta de 20 % (límite de subida 17 %),
confianza 0.70, previa de cuenta CVR 3 % / AOV 450.

| Caso | Pedidos pesados | ACoS hoy | P(ACoS < 17 %) hoy | m* | R6r | R6 de hoy para llegar igual |
| --- | --- | --- | --- | --- | --- | --- |
| A | 2.5 | 10.1 % | 0.59 | 0.83 | no sube (igual que hoy) | no sube |
| B | 5 | 5.9 % | 0.95 | 1.76 | 3.80 (47 %) en 1 paso | 5 pasos, ≥ 35 días |
| C | 8 | 4.8 % | 0.996 | 2.36 | 5.09 (64 %) en 1 paso | 7 pasos, ≥ 49 días |
| D | 12 | 3.5 % | 1.000 | 3.29 | 7.10 (89 %) en 1 paso | 9 pasos, ≥ 63 días |

«1 paso» es el primer ciclo en que R2 y R14 ya se cumplen; para una keyword recortada hace más de dos
semanas, es el día siguiente. Después del paso, la misma evidencia da `bid × m* ≈ bid`: no hay otro
salto hasta que llegue evidencia nueva. En el caso D, el 11 % restante hasta 8.00 lo decide R6 cuando la
evidencia del bid nuevo madure.

**El techo de baja compartido (20 → 10 → 7).** Antes de la baja, el motor recortó una keyword de 20 a 10
MXN. La entrada la deja en 7 (30 %). `techo_conocido` = 20, reducción efectiva 0.30, techo de baja =
`floor(20 × 0.70)` = 14.00.

- Con la evidencia del caso D (m* = 3.29): `7 × 3.29 = 23.03` → `min(23.03, 20, 45, 14)` = **14.00**.
- R6 sin R6r: 7 → 8.05 → 9.26 → 10.65 → 12.25 → 14.00 (el último paso topa); nunca 16.10.
- Al salir de la baja, el nivel a restaurar es 10 (la base de la entrada). La keyword ya está en 14, así
  que el regreso no hace nada (`ya_arriba`) y nunca la baja. Fuera de la baja, un segundo salto de R6r
  hasta 20 necesita evidencia nueva al precio de 14 y pasar el juicio económico.
- Una keyword que entró de 8 a 5.60 sin recortes previos tiene techo de baja 5.60: en baja no hay nada
  que recuperar.

**Para quién no sirve.** La mayoría de las keywords tiene ≤ 3 pedidos pesados (caso A). Ahí la evidencia
no alcanza para subir, ni hoy ni con R6r, y eso es correcto. Para ellas, la palanca es el regreso de la
temporada, que no pide evidencia porque lo pidió David y porque el bid de destino ya corrió antes.

---

## Temporada baja, salida y alta

### Calendario

Con el calendario por defecto, en 2027:

| Bloque | Fechas | Por qué |
| --- | --- | --- |
| Baja | 1 ene – 7 feb (38 días) | marzo es alta: la preparación de 21 días empieza el 8 feb |
| Preparación (normal) | 8 – 28 feb | regreso el 8 feb |
| Alta | 1 mar – 30 abr | un solo bloque: abril no se prepara otra vez |
| Baja | 1 may – 30 jun (61 días) | sigue julio normal: regreso el 1 jul |
| Normal | jul – sep; previa del 10 al 30 sep | la previa no mueve bids (no viene de baja) |
| Alta | 1 oct – 30 nov | |
| Normal | dic | 1 dic: la meta regresa a la normal |

En 2028 la preparación de marzo empieza el 9 de febrero (año bisiesto). Con baja en agosto, normal en
septiembre, alta en octubre y 42 días de preparación, el regreso es el 20 de agosto, aunque haya un mes
normal en medio. Una baja de diciembre a febrero es un solo bloque: enero no abre otro episodio. El reloj
es UTC, el mismo del motor (`app/lecturas_caso.py:795`, `app/optimizer/windows.py:457`).

### Entrada a la baja

El primer ciclo de un día de baja, en vivo:

1. TX3 abre el episodio de la plataforma si no hay uno abierto.
2. Cada keyword con bid propio, habilitada, aunque sea inerte, con entrada pendiente recibe
   `Mover(destino_entrada, "entra_temporada_baja")`. R0 va antes (una keyword que ya cumple la pausa con
   el multiplicador se pausa en lugar de reducirse; esa pausa se reactiva al final de la baja). La
   entrada no espera R2, ni venta reciente, ni presupuesto: es una bajada planeada.
3. El aplicador relee el bid en Amazon antes de mandar. Si ya no es el que Orbit vio, no manda nada y
   escribe la exclusión de esa keyword para ese episodio.
4. El cupo de hoy es de 1,000,000 operaciones (medido el 2026-11-12), así que la entrada suele salir en
   un ciclo. Lo que no sale (fallo HTTP, ledger sin sello) queda pendiente y se deriva otra vez mañana;
   una hoja con un bid sin sello espera a reconciliarse (`app/cycle.py:2213-2218`).

**Si David edita la reducción a media baja** (30 → 20), el destino se recalcula desde la base, no sobre el
bid reducido: `ceil_centavo(8.00 × 0.80)` = 6.40, con `ajusta_temporada_baja`, solo si el último cambio de
la keyword es el de temporada. Si después hubo otro movimiento de Orbit o una mano de David, se queda como
está.

**Si David cambia los meses a media baja** y hoy sigue en baja, el episodio sigue abierto y nada se
recorta dos veces (mayo 8 → 5.60 no llega a 3.92). Si hoy deja de ser baja, el episodio se cierra en el
siguiente ciclo y empiezan los regresos.

**Si se enciende a media baja** (20 de enero), la entrada sale sobre el bid confirmado de ese día y la
cerca de cada keyword es el día siguiente a su movimiento real, no el 1 de enero.

### Durante la baja

- Ningún recorte por evidencia: R3, R4, R9 y R11 dan `Mantener("temporada_baja")`. R1 no aplica.
- Subidas con techo de baja (R6 y R6r).
- Pausas y negatives con el multiplicador del dueño (×2 por defecto) en clics, gasto mínimo y exceso de
  la pausa económica (`EXCESO_MINIMO`, `app/optimizer/bid.py:135`, `:253-260`), al decidir y al liberar.
  Con ×2, la pausa pide 200 clics y 1,000 MXN (80 USD) sin pedidos en la ventana, y la pausa económica un
  exceso de 2,000 MXN (160 USD). La protección por venta reciente de O sigue igual.
- Por qué ×2: con conversión `c`, la probabilidad de ver 0 pedidos por azar en `n` clics es `(1 − c)^n`.
  Si la baja reduce la conversión, pedir más clics devuelve la tasa de pausas por azar hacia la de un
  mes normal. Cuánto cae la conversión no está medido (los pedidos de US en los meses bajos son ×0.55 a
  ×0.63 del pico, y eso mezcla tráfico y conversión), por eso el multiplicador es editable y no una
  constante con una justificación que no se puede comprobar.
- Toda pausa decidida con `fase_hoja = "baja"` lleva en sus inputs el episodio y su frontera congelada:
  es lo que la marca como «pausa de la baja».

### Salida de la baja

El primer ciclo en que la fase deja de ser baja (o el siguiente a «Terminar la baja hoy»):

1. TX3 cierra el episodio.
2. Cada keyword reducida con regreso pendiente recibe `Regresar(nivel, "sale_temporada_baja")` **antes de
   R0**, aunque sea inerte y aunque haya vendido en los últimos 10 días: la espera por venta reciente
   (`DIAS_PROTECCION_VENTA = 10`, `app/optimizer/caso.py:34`) no retiene un regreso planeado. El nivel
   es la base de la entrada; solo si Orbit recortó por evidencia después de la entrada (temporada
   apagada o en práctica a media baja) el regreso conserva esos recortes en proporción
   (`nivel_de_regreso`: 5.60 → 3.15 regresa a 4.50, no a 8.00).
3. Ese día la pausa no se juzga con datos de la baja. Desde el regreso confirmado, la cerca de la keyword
   es el día UTC siguiente: si se confirmó el 1 de julio a las 23:55, la evidencia nueva empieza el 2 de
   julio y el día mixto queda fuera, en la hoja y en la revalidación de la cola. Con 7 fechas completas y
   la madurez de 10 días, los cortes vuelven a juzgar unas dos semanas y media después del regreso.
4. **Retenciones.** Si la campaña se queda sin presupuesto, el regreso espera: subir el bid solo
   encarecería el clic, y la propuesta de «fin de la baja» sale en «Dónde poner el dinero». Mientras
   espera, la keyword sigue en «baja» para los cortes (×2 y la cerca de su entrada), para el juicio (HOLD)
   y para el techo de subidas. La tarjeta lo cuenta como pendiente, no como terminado. Si hay una pausa
   de producto en vuelo en el grupo, el ciclo bloquea la subida como hoy (`app/cycle.py:2219-2228`).
5. **Una pausa en la cola congela la hoja** (`app/cycle.py:1915-1929`). Si una pausa decidida en los
   últimos días de la baja sigue en su veto de 48 horas el día de la salida, la hoja no llega a `decide`
   hasta que la cola la resuelva. Al liberar, la revalidación usa la frontera de esa hoja (todavía
   reducida, así que ×2): si se aplica, es una pausa de la baja y se reactiva; si se descarta, el regreso
   sale en el siguiente ciclo.
6. **Reactivaciones.** Las pausas de Orbit confirmadas, decididas con `fase_hoja = "baja"`, de un episodio
   ya cerrado, se reactivan con la reversa que ya existe, con decisión `resume` del motor `temporada`,
   cuota de pausas y sus guardas (campaña no topada, sin pausa de producto, no en «Mantener pausada»).
   Una keyword pausada no entra al juicio, así que el orden es: reactivar en el ciclo N, regresar el bid
   en el ciclo N+1. `_SQL_PAUSE_PROPIO` deja de contar las pausas que la temporada deshizo; la siguiente
   pausa válida de Orbit no queda bloqueada para siempre.

### Temporada alta

- `meta_del_juicio = max(base, meta_alta)` para R3–R13. La meta es libre: con las metas de hoy, 25 % en
  MX y en US. En US el equilibrio (23.81 %) queda debajo de 25: cada venta con ACoS entre 23.81 y 25
  pierde dinero, Ajustes lo dice en rojo y R3 usa 25 como límite (`equilibrio_del_juicio`). R6 sube cuando P(ACoS < 21.25 %) ≥
  la confianza de subida; R4 solo recorta con ACoS sobre 25 %.
- Las metas escritas a mano (`goal_campana`, `goal_plataforma`) no cambian: son la decisión más
  específica de David (`goals.py:319-320`). La pantalla lo dice junto a cada una.
- Las metas derivadas de producto y familia (V) sí reciben la meta de alta: se resuelve la cascada
  completa y después la temporada.
- La cascada, su procedencia y el CHECK de `target_acos_ciclo` (`migrations/0076_auto03_peldano_producto.sql:15-18`)
  no cambian. El caso congela la base, la meta de alta y la frontera; la meta del juicio se deriva
  dentro de `decide`.
- R0, negatives y harvest siguen con la meta base. La revalidación de la cola usa la misma meta que la
  decisión.

### Salida de la alta

Sin rampa. El 1 de diciembre la meta vuelve de 25 % a la base (~20 %). Eso reactiva R4 en keywords con
ACoS entre la base y 25 %, frena R6 y además puede endurecer R3: su recorte fuerte compara con 1.35 ×
meta (`politica.py:327-329`), que baja de 33.75 % a 27.1 %. Con el equilibrio de hoy (~40 %), una keyword
que abre R3 ya cae en el recorte fuerte en las dos fases (si P(ACoS > 40.16 %) pasa la confianza,
P(ACoS > 33.75 %) también). El cambio de −12 % a −25 % aparece en keywords cuyo equilibrio quede entre
27.1 % y 33.75 %, por ejemplo con una meta de producto o familia de otro margen. El motor ya se comporta
así con cualquier cambio de meta.

### Lo que hacen los frenos con un movimiento de temporada

R14, R15, R16 y R16b preguntan «¿el movimiento anterior tuvo el efecto buscado?». Una reducción o un
regreso planeado no buscaba un efecto. Si el último cambio tiene origen `temporada`, solo aplican R2 y
R17. Un movimiento de temporada nunca abre ni prolonga un episodio económico (U, con su caducidad de T)
*(contrato de ARREGLOS 01)*.

---

## Presupuestos

La temporada no escribe presupuestos. Calcula una propuesta por campaña al pintar «Dónde poner el
dinero», con montos absolutos ligados al episodio, y el botón «Revisar» abre el diálogo de ajuste que
ya existe (`GET /campana-ajuste/plan`, `POST /campana-ajuste/aplicar` con `APLICAR AJUSTE`). Razones:
el presupuesto es el tope duro del gasto y la palanca que controla David; ese camino exige su
confirmación por diseño; aplicarlo solo crearía una segunda semántica sobre el mismo escritor.

Una propuesta por momento:

| Momento | Propuesta | Cuándo deja de salir |
| --- | --- | --- |
| Baja (episodio abierto) | `q(antes × (1 − reducción))`, si el vigente es mayor. `antes` = presupuesto vigente al abrir el episodio | en cuanto hay cualquier cambio de presupuesto desde que abrió el episodio |
| Fin de la baja (30 días desde el cierre del episodio) | regresar a `antes` | si David puso otro monto: solo sale si el único cambio desde que abrió el episodio fue exactamente la propuesta de baja |
| Preparación y alta | `min(q(vigente × (1 + aumento)), tope humano de X)` en campañas sin presupuesto (5 de 7 días al 90 %) | en cuanto hay un aumento por encima de `antes` desde que empezó la preparación o el bloque alto (el regreso a `antes` por «fin de la baja» no cuenta): 300 → 375 una vez, nunca 468.75 al recargar |

Si aplican «fin de la baja» y «alta sin presupuesto», gana el monto mayor. Una propuesta sola hace cero
escrituras. La frase dice «baja de pujas» y muestra el gasto observado aparte: un presupuesto menor y un
bid menor no garantizan un ahorro exacto.

**Tope humano.** La propuesta se acota con la interfaz de ARREGLOS X: `TECHOS_HUMANOS_POR_MONEDA` y
`exige_bajo_techo` (el doble del presupuesto diario más alto de hoy, `plans/arreglos-01.md:318-320`)
*(contrato de ARREGLOS 01)*. Si el tope recorta la propuesta, la frase lo dice.

**Cobertura.** `antes` sale del historial de `ads_campana_config_observation`, el mismo que recorre
`pantalla_dinero._presupuesto_dia` (`app/pantalla_dinero.py:448`). Esa tabla solo guarda filas cuando
cambia la configuración. Sin fila anterior al episodio no hay propuesta (no se inventa) y la pantalla
muestra la cobertura de días medidos.

**El orden de dos aprobaciones (H1).** Hoy, si David aprueba 375 y después ordena 270 para la misma
campaña, la orden vieja puede terminar después y dejar 375. El candado de `_aplica_y_confirma` pasa a ser
por campaña y campo, también al regresar un ajuste. Bajo ese candado y antes del PUT, una orden con otra
aceptada posterior de la misma campaña y campo queda invalidada sin PUT, y si el campo vigente ya no es el
esperado se vuelve a planear. El intercalado del contraejemplo termina en 270 y no envía 375 después.

**Campaña sin presupuesto, una sola definición.** `ads_campana_topada` (90 % en 5 de 7 días por defecto)
la leen el aviso de Telegram (`avisos_campana.py:79`), la columna de «Dónde poner el dinero»
(`pantalla_dinero.py:473`), la propuesta y el freno de subidas (`PresupuestoCampana.topada`, congelada en
el caso). David no ve dos verdades.

---

## Punto de equilibrio con el costo vigente

**Hoy (comprobado en `868a8cd2`).** El punto de equilibrio es el margen neto antes de publicidad. La
vista de plataforma `v_target_margen_plataforma` (`migrations/0016_target_margen_correcciones.sql:25-155`,
leída en `cycle.py:525-530`) y la de producto `v_margen_producto`
(`migrations/0018_fabrica_campanas.sql:283-419`, leída en `productos_io.py:89-93`) calculan
`100 × (venta_cubierta + cargos_con_orden + cargos_sin_orden × cobertura − COGS) / venta_cubierta`, con
precio, comisiones, envío, devoluciones e ISR de `ledger_event`. El COGS es `sku_cost.cost_amount ×
quantity` con el costo vigente **en la fecha de cada venta** (`0016:45-48`, `0018:298-301`). La ventana de
plataforma es de hoy−105 a hoy−15; la de producto empieza fija el 2026-02-20. No hay caché: la vista se
recalcula en cada lectura. El resultado:

- Un costo que baja hoy entra a la ventana a los 16 días (costo publicado al día siguiente, `costs.py:586-588`,
  más 15 de maduración) y pesa por completo en la plataforma hasta los 106 días. En producto casi nunca
  pesa por completo, porque la ventana crece desde febrero.
- El equilibrio que ve el motor es un promedio de costos viejos y nuevos, ponderado por venta.
- Ajustes no muestra el margen ni el equilibrio (`settings.html:24-58`); solo la meta vigente.

**Lo que cambia.**

1. Las dos vistas agregan al final, sin quitar columnas, `cogs_vigente`, `cobertura_vigente` y
   `margen_vigente_pct`: la misma fórmula, los mismos cargos y las mismas guardas (moneda, `fees_sin_tipo`,
   días con venta, frescura del ledger), con COGS = Σ `quantity × cost_amount` del costo **de hoy** en la misma moneda de la venta:
   `c.valid_from <= hoy AND (c.valid_to IS NULL OR hoy < c.valid_to)` con `hoy = (now() AT TIME ZONE 'UTC')::date`,
   el mismo rango que hoy se aplica a la fecha de la venta (`0016:47-48`). `valid_to IS NULL` solo no sirve: el
   costo publicado hoy ya nace abierto con inicio mañana. `v_margen_familia` (respaldo de familia) cambia igual.
   Una venta cuyo producto no tiene costo de hoy en esa moneda queda no cubierta; con `cobertura_vigente < 0.95` el margen vigente es NULL. No se
   rellena ni se estima.
2. El margen que gobierna es el vigente. La meta derivada (fracción × margen, `goals.py:983`) y
   `equilibrio_acos_pct` (plataforma `cycle.py:2987` y `:3318`; producto `cycle.py:757`) salen de
   `margen_vigente_pct`. `notes.target` guarda los dos: `margen_neto_pct` (vigente) y
   `margen_historico_pct`. Los motivos de NULL (`cobertura_baja`, `ledger_rancio`, `ventana_corta`,
   `sin_margen`) se conservan; Ajustes distingue los cargos sin clasificar por la columna `fees_sin_tipo`.
3. Un costo nuevo mueve el equilibrio entero desde el ciclo siguiente a su publicación. Si el costo baja,
   la meta derivada sube de una vez; si sube, la meta baja 0.5 por ciclo como hoy (`goals.py:985-991`),
   pero R3 juzga desde el primer día con el equilibrio nuevo.
4. Precio, comisiones, envío y devoluciones siguen saliendo de la ventana. Escalan con el precio cobrado;
   si el precio cambió, el margen usa el precio de la ventana (límite declarado).
5. Antes de desplegar, la consulta nueva corre en solo lectura y deja en la evidencia el margen
   histórico y el vigente por plataforma y por producto con meta propia: cuánto se moverá cada meta.

**En Ajustes.** Por plataforma, junto a la meta:

> Punto de equilibrio con el costo de hoy: 23.8 %. Arriba de ese ACoS pierdes dinero en cada venta.

Si la meta vigente o la meta de alta es igual o mayor que el equilibrio, el aviso sale en rojo:

> Con una meta de 25 % en temporada alta, Orbit acepta ventas que pierden dinero en Amazon US: las que
> salen con un ACoS entre tu punto de equilibrio, 23.8 %, y 25 %.

Vale también cuando la banda [10, 45] (`goals.py:608-609`) deja la meta en 10 sobre un equilibrio menor.
Sin equilibrio, la línea dice por qué, en llano: «faltan costos de algunos productos»
(`cobertura_baja`), «los datos de ventas tienen más de 3 días» (`ledger_rancio`), «hay pocas ventas en los
últimos 90 días» (`ventana_corta`), «hay cargos de Amazon sin clasificar» (`fees_sin_tipo`). La vista
previa devuelve el equilibrio y el aviso, así que se ven al escribir la meta, antes de guardar. La
pantalla de productos muestra el margen vigente de cada producto donde hoy muestra el histórico
(`productos_sin_clics.html:169`).

**Prueba (dinero).** `test_temporada01_i_equilibrio_con_costo_vigente`, con base, producto MX,
fracción 0.5: 10 ventas maduras de 1,000 MXN con cargos de −300 cada una y costo 500 hasta el día D, 5
antes y 5 después de D.
- El costo baja a 300 desde D: histórico (10,000 − 3,000 − 4,000) / 10,000 = 30 %; vigente
  (10,000 − 3,000 − 3,000) / 10,000 = **40 %**. Equilibrio 40 y meta derivada 20.
- El costo sube a 700: vigente **0 %**. Equilibrio 0, y Ajustes avisa que la meta de 10 de la banda pierde
  dinero.
- Costo abierto en USD con venta en MXN, o producto sin costo abierto: la venta queda no cubierta.
- Un costo publicado hoy no cuenta hasta mañana.

## Ejemplo de una keyword durante un año

Amazon MX, calendario por defecto. Keyword «funda impermeable 30L», bid de 8.00 MXN el 31 de diciembre de
2026, meta por margen de plataforma 20.08 % (la medida hoy), equilibrio ~40.16 % (el margen neto: la
meta es 0.5 × margen), meta de alta 25 %, piso del goal 1.00, techo 45.00. Su campaña tiene 300 MXN al
día.

| Fecha | Fase | Qué pasa | Bid |
| --- | --- | --- | --- |
| 31 dic 2026 | normal | — | 8.00 |
| 1 ene 2027 | baja | TX3 abre el episodio E1. R0 no pausa. Entrada: `ceil_centavo(8.00 × 0.70)` → `Mover(-0.30, 5.60, "entra_temporada_baja")`. Amazon confirma ese ciclo. Cerca de la keyword: 2 ene | 5.60 |
| 1 feb | baja | Del 2 al 22 ene gastó 400 MXN sin venta: R9 daría −12 % → `Mantener("temporada_baja")`. R1 no mira el cambio (origen `temporada`) | 5.60 |
| 8 feb | normal (preparación) | E1 cierra. Regreso pendiente antes de R0: `Regresar(8.00, "sale_temporada_baja")`, aunque haya vendido ayer. Cerca: 9 feb | 8.00 |
| 21 mar | alta | Meta del juicio `max(20.08, 25)` = 25 % (libre: no la acota el equilibrio). Evidencia desde el 9 feb: 3 pedidos, ACoS 12 %; P(ACoS < 21.25 %) ≥ 0.70 → R6 +15 %. Los frenos de repetición no juzgan el regreso (solo R2, cumplido) | 9.20 |
| 30 abr | alta | Sin cambio: R15 pide 20 clics nuevos al bid de 9.20 | 9.20 |
| 1 may | baja | E2 abre. Entrada: 9.20 × 0.70 | 6.44 |
| jun | baja | Gasto sin venta: retenido. Una pausa sería posible solo con 200 clics y 1,000 MXN sin pedido desde el 2 may | 6.44 |
| 1 jul | normal | E2 cierra. Regreso a 9.20 antes de R0. Meta 20.08 %. Cerca: 2 jul | 9.20 |
| 16 ago | normal | Evidencia del 2 jul al 6 ago: ACoS 23 % > 20.08 % y el grupo pierde → R4 −12 %: `_paso` da 8.0960, Amazon recibe 8.10 | 8.10 |
| 10–30 sep | normal (previa) | Nada que regresar (no viene de baja). Aviso: el 1 oct empieza la alta; propuestas de presupuesto si la campaña topa | 8.10 |
| oct – nov | alta | Meta 25 %: con ACoS 23 % R4 ya no recorta, y R6 no sube (pide P(ACoS < 21.25 %) ≥ 0.70) | 8.10 |
| 10 dic | normal | Meta 20.08 % otra vez: R4 −12 % → 7.128, Amazon recibe 7.13. Si la keyword perdiera dinero (R3 abierto), su recorte fuerte compararía con 27.1 % en vez de 33.75 % | 7.13 |
| 1 ene 2028 | baja | Entrada: 7.13 × 0.70 = 4.991 → el centavo que no pasa de 30 % es 5.00 (29.87 %) | 5.00 |
| 9 feb 2028 | normal (preparación) | Regreso a 7.13 (año bisiesto: un día después que en 2027) | 7.13 |

La continuidad de la cadena tolera 0.005: el 16 de agosto Orbit guardó 8.0960 como `bid_despues` y Amazon
tiene 8.10; el siguiente `old_value` (8.10) difiere 0.004 y no cuenta como mano de David.

**Variantes de la misma keyword:**

- **David la mueve a mano a 7.00 el 20 de mayo.** El bid vigente ya no es el último de Orbit
  (`dueno_al_final`): el 1 de julio no regresa a 9.20. Se queda en 7.00 hasta que la evidencia diga otra
  cosa (R6; R6r no, porque es terreno de David).
- **David pone 7.00 en Amazon el 1 de enero, entre la decisión y el envío.** El aplicador lee 7.00, que no
  es el 8.00 que vio Orbit: cero PUT, `bid_cambiado_fuera`, exclusión (E1, keyword). El 2 de enero no hay
  otra entrada. En esa baja no recibe la entrada, el regreso ni la subida rápida; el R6 de hoy puede moverlo con evidencia, desde 7.00. El 8 de febrero no hay nada que regresar. Ajustes la lista
  como «sin tocar: cambiaste tú el bid».
- **Se queda inerte en junio** (0 impresiones en 14 días). El 1 de julio regresa a 9.20 igual.
- **El 1 de julio su campaña se queda sin presupuesto.** El regreso espera; la keyword sigue con cortes
  ×2 y la cerca del 2 de mayo; «Dónde poner el dinero» propone «Fin de la baja: subir de 210 a 300 MXN al
  día». Cuando David aprueba y la campaña deja de topar, el regreso sale.
- **El 1 de julio la ventana trae 200 clics, 1,000 MXN y 0 pedidos.** Primero sale el regreso; la pausa
  no se juzga con esos datos. Desde el 2 de julio la evidencia empieza de cero.
- **David marca junio como normal el 10 de junio.** El 11 de junio la fase es normal, E2 cierra y el
  regreso sale ese ciclo. La cerca es el 12 de junio (el día siguiente al regreso real), no el 1 de
  junio.
- **David baja la reducción a 20 % el 15 de mayo.** Ese ciclo: `Regresar(7.36, "ajusta_temporada_baja")`
  (9.20 × 0.80). El 1 de julio regresa a 9.20.
- **En marzo su campaña topa 5 de 7 días.** La subida del 21 de marzo espera (`presupuesto_topado`) y «Dónde
  poner el dinero» propone subir de 300 a 375 MXN con el botón Revisar.

**Presupuesto de la campaña (300 MXN al día el 31 de diciembre).** El 1 de enero: «Temporada baja: bajar
de 300 a 210 MXN al día [Revisar]». Si David la aprueba, el 8 de febrero: «Fin de la baja: subir de 210 a
300 MXN al día [Revisar]», durante 30 días. Si en enero puso 250 a mano, no hay propuesta de fin de baja.
En marzo, si topa: «Temporada alta: se queda sin presupuesto; subir de 300 a 375 MXN al día [Revisar]»,
una sola vez en ese bloque.

---

## Plan por bloques (después de ARREGLOS 01)

Plan TEMPORADA 01, entregado con `motor director encargar`. Los bloques van en cadena (comparten
`cycle.py`, `politica.py`, `lecturas_caso.py`, `apply.py`). Cada prueba se llama
`test_temporada01_<bloque>_<qué>` y debe fallar en el SHA previo al bloque. Una prueba por unidad nueva
que mueve dinero o escribe en producción o en Amazon; lo demás se comprueba corriendo lo real. Antes de
empezar: integrar sobre el SHA final de ARREGLOS 01 y releer las firmas de P, T, U, V, W y X en el código.

| Bloque | Contenido | Prueba (caso del repo) | Efecto en vivo |
| --- | --- | --- | --- |
| I — Equilibrio con el costo de hoy | migración `<n>_temporada01_margen_vigente.sql` y reversa; `cogs_vigente`, `cobertura_vigente` y `margen_vigente_pct` en `v_target_margen_plataforma`, `v_margen_producto` y `v_margen_familia`; meta derivada y `equilibrio_acos_pct` desde el margen vigente; `notes.target` con los dos márgenes; `migraciones_de` reconoce `temporada01` | instalar: `test_temporada01_i_migracion_en_migraciones_de` (una migración `<n>_temporada01_x.sql` con cabecera y reversa sale en `migraciones_de`). Dinero: `test_temporada01_i_equilibrio_con_costo_vigente` (los casos de «Punto de equilibrio con el costo vigente») | la meta derivada y el equilibrio de MX y US cambian desde el ciclo siguiente al despliegue; `I/antes.md` dice cuánto |
| A — Calendario y Ajustes | `temporada.py` (config, `valida` con tipos crudos, lectores, `resuelve`, `eventos`, `resumen`); `config_write` + `CuerpoSettings` + vista previa; tarjeta, campos de plataforma, «Terminar la baja hoy», «Mantener pausada»; `ads_campana_topada` leído por avisos y dinero; fichas en `features/` | dinero: `test_temporada01_a_fechas_del_calendario` (1 ene baja; 8 feb 2027 y 9 feb 2028 regreso; ago-sep-oct con 42 días → 20 ago; dic-feb un solo bloque; `fin_anticipado`). Escribe en producción: `test_temporada01_a_post_rechaza_booleanos` (`true`/`false` dan 422 y la config no cambia; `"25"` y `25` se aceptan) | ninguno (sin interruptores) |
| B — Episodio, origen e historia | migración y reversa; `OrigenCambio`, `CambioFechado`, `HistoriaBid`; `recuperacion.cadena_bid`; `temporada.pendiente`, `destino_entrada`, `frontera`; `temporada.reduccion_vigente`; `temporada_io` (`lee_estado`, `registra_ciclo`, `estampa`, `abre_efectos`, `registra_fines` en `temporada_fin_reduccion`); cierre de efectos en `_confirma_resumen` y `registra_sin_aplicar`; `pantalla_danadas` ignora `temporada` | dinero: `test_temporada01_b_pendiente` (entra una vez; cambiar mayo a normal con junio en baja no recorta 5.60 → 3.92; 30 → 20 da 6.40; mano de David no regresa; el regreso nunca baja). Escribe en producción: `test_temporada01_b_episodio_reparado_tras_caida` (con base: entrada confirmada y efecto abierto como tras una caída; el ciclo siguiente cierra el efecto, deja un episodio y un efecto, y no decide otra entrada). Escribe en producción: `test_temporada01_b_fin_de_reduccion` (los casos 1 a 7 de la fila B.2 del plan: fin guardado al salir sin regreso y entrada nueva en E2; exclusión por la mano de David con E2 abierto, también el día de la apertura; `Mantener` e inertes; mano de la mañana de la apertura; nada en práctica; `dueno_al_final` sin fecha; (8) temporada apagada a media baja con recortes 5.60 → 4.20 → 3.15: al encender, el regreso va a 4.50 y un episodio de otra baja se cierra; (9) Orbit pausó en la baja y David reactivó el día 10: el día 11 no hay entrada) | ninguno |
| C — Subida rápida (práctica) | `techo_conocido`, `multiplicador_sostenible`, `destino_recuperacion`, `presupuesto_topado`; R6r y BUD; frenos del primer y segundo salto; `MOTIVOS_ES`; campos de plataforma | dinero: `test_temporada01_c_recupera_con_evidencia` (caso D da `Regresar(7.10)`; topada 5 de 7 da `Mantener("presupuesto_topado")`; misma evidencia no salta dos veces) | ninguno hasta P, que pone `ads_bid_recuperacion_<plat>=shadow` en MX y US |
| D — Temporada en la política (práctica) | RTs antes de R0, RTe, `_paso_a_centavo`, guarda de R1, meta del juicio, HOLD, techo de baja en R6 y R6r, frenos; `cortes` con `mult_baja` y exceso económico; `windows` y tramos con cerca; revalidación de la cola con `frontera_hoja`; inertes en vivo; sombra en `notes.temporada` | dinero: `test_temporada01_d_salida_antes_de_pausa` (5.60, regreso pendiente, 200 clics, 1,000 MXN, 0 pedidos → `Regresar(8.00)` y ningún `Pausar`; al liberar con regreso pendiente usa ×2 y la cerca vieja; tras el regreso confirmado no hay pausa sin evidencia nueva; con la campaña topada el regreso no sale, se anota `regreso_espera_presupuesto` y la hoja sigue en baja con ×2 y HOLD). Dinero: `test_temporada01_d_dia_mixto_excluido` (regreso a las 23:55 del 1 jul; 150 clics y 750 MXN ese día; ni la decisión ni la revalidación de 48 h pausan; con la cerca del 2 jul hay 7 fechas sin gasto). Dinero: `test_temporada01_d_centavos_de_la_entrada` (0.15 USD → 0.11; 1.55 MXN → 1.09; 0.10 USD con piso 0.10 sin centavo factible → excluida; lo congelado = lo enviado). Dinero: `test_temporada01_d_techo_de_baja_compartido` (20 → 10 → 7: R6r ≤ 14 y R6 ≤ 14; 8 → 5.60 sin recortes previos no sube en baja). Dinero: `test_temporada01_d_baja_entra_y_retiene` (entrada −30 %; R9 retenido; R1 no regresa la entrada). Dinero: `test_temporada01_d_meta_alta` (MX 20.13 → 25; US 19.05 con equilibrio 23.81 → 25 y `equilibrio_del_juicio` 25; meta de campaña 15 sigue en 15; base 30 → 30; sin equilibrio → 25; fuera de alta R3 vuelve a 23.81). Dinero: `test_temporada01_d_cortes_por_multiplicador` (umbral, piso y exceso económico ×2 en baja, al decidir y al liberar; iguales fuera) | ninguno hasta P, que pone `ads_temporada_<plat>=shadow` con los meses de David en MX y US |
| P — Práctica encendida | `ads_bid_recuperacion_*` y `ads_temporada_*` en `shadow` con `escribe_api.sh`, con C y D ya desplegados | sin prueba nueva (lane gate) |  |
| E — Lectura fresca y mano de David | lectura antes de cuota y PUT para toda decisión de bid; `bid_cambiado_fuera`; `temporada_exclusion` por mano y por divergencia; prioridad bajo cupo | escribe en Amazon: `test_temporada01_e_mano_del_dueno_excluye` (bid 8.00, decisión 5.60, Amazon en 7.00 → cero PUT, exclusión guardada; al día siguiente no hay entrada y sigue en 7.00, y con evidencia que dispararía R6r no hay `Recupera`) | cambia el envío de los bids en vivo de hoy: un GET más por bid y cero PUT si David cambió el bid |
| F — Fin de la baja | `reactivaciones_pendientes`, `pendiente_reactivacion`, `aplica_reactivaciones` con sus guardas, `_SQL_PAUSE_PROPIO`, replay del motor `temporada`, `aviso_del_dia` | escribe en Amazon: `test_temporada01_f_reactiva_solo_pausas_de_baja` (con base: pausa de Orbit confirmada de la baja con episodio cerrado → `resume`; pausa del dueño, reactivada por el dueño, ya revertida, en «Mantener pausada» o de episodio abierto → no). Escribe en Amazon: `test_temporada01_f_reactivar_no_bloquea_pausas` (tras la reversa de temporada, una pausa nueva no inserta `reactivacion_manual`) | en práctica se listan en `notes.temporada.reactivaciones` |
| G — Presupuestos | `propuesta_presupuesto`; columna y botón en «Dónde poner el dinero» con `APLICAR AJUSTE`; candado por campaña y campo en `_aplica_y_confirma` (también al regresar); tope de X; ficha en `features/` | dinero: `test_temporada01_g_propuestas_absolutas` (baja 300 → 210; fin 210 → 300; alta 300 → 375 una vez y nada tras aprobar; fin de baja aprobado el 9 feb y campaña topada en marzo → 375; 250 a mano → sin fin de baja; tope de X). Escribe en Amazon: `test_temporada01_g_presupuesto_misma_campana_en_orden` (A aprueba 375, B ordena 270 después y termina primero; A no envía; final 270) | el candado corrige hoy el flujo de ajustes de David; las propuestas no escriben |
| H — En vivo, MX y US juntos | Subida rápida y temporada a `live` en las dos plataformas después de un ciclo completo de práctica, revisando en `notes.temporada` cuántos `recupera_nivel`, el m* máximo, el gasto extra proyectado, las entradas, los retenidos, las metas de alta y las reactivaciones. Se para si un bid enviado baja más que la reducción, si un regreso baja un bid, si sale un PUT después de una lectura fresca distinta, si una keyword recibe dos entradas en un episodio o si un presupuesto se escribe sin `APLICAR AJUSTE` | — | las cuatro claves a `live` con `escribe_api.sh`; chequeos con `at` el día siguiente al encendido y el día de cada transición del calendario y el siguiente; se deshace con «Terminar la baja hoy» (los bids regresan) o quitando la clave (apagar no deshace) |

Regla de replay del repo: todo lo que toca `decide` va detrás de marcadores congelados. La vista
`v_cambio_bid` cambia la clasificación de origen: la consulta previa de la migración da 0 filas con los
motivos nuevos, así que ninguna trayectoria histórica cambia. El cambio de `_SQL_PAUSE_PROPIO` (F) no es
una función de decisión pura pero cambia la revalidación en vivo: antes de desplegar, una consulta de
solo lectura confirma 0 decisiones `resume` del motor `temporada`.

---

## Riesgos y límites honestos

1. **Amazon no ofrece compare-and-set.** La lectura fresca reduce la carrera, no la elimina: si David
   cambia el bid entre el GET y el PUT (segundos), Orbit lo pisa, el readback ve el valor de Orbit y la
   cadena queda continua. No se promete atomicidad; se reconcilia con lo que Amazon confirma.
2. **Una mano de David sin fecha.** Amazon no dice cuándo cambió él un bid; Orbit solo ve que el bid
   vigente no es el último que confirmó (`dueno_al_final`, con la historia de 365 días). Para una keyword
   que Orbit movió en ese plazo, ese bid distinto cuenta como su mano de esta baja aunque sea de meses
   antes, y la keyword no recibe la entrada (plan B.2, caso 7). Para una keyword que Orbit no tocó en 365
   días no hay con qué comparar: la entrada baja 30 % el bid que tenga, sea de quien sea, y el regreso lo
   restituye. Medido en producción el 2026-11-12: 0 keywords con `dueno_al_final` en MX y en US; sin
   cambio de Orbit en 365 días, 271 de 353 en MX y 189 de 234 en US. Un cambio de menos de 0.005 no se
   distingue. No hay historia del bid fuera de Orbit (`app/ads/structure.py:199-210`).
3. **Un regreso retenido por presupuesto puede cruzar meses.** Mientras David no apruebe la propuesta, la
   keyword sigue reducida y con cortes ×2, también en plena alta: juzga con la meta base, no con 25, y
   no sube. La pantalla lo dice. Es deliberado: con la campaña topada, un
   bid más bajo da más clics por el mismo gasto.
4. **La cerca retrasa decisiones.** Después de cada entrada y cada regreso, los cortes esperan 7 fechas
   completas más la madurez de 10 días; el primer mes de la alta y de un mes normal tras la baja tienen
   menos subidas y recortes. A cambio, la evidencia de un precio no juzga otro.
5. **Grupo y cuenta usan la cerca de la plataforma.** Las pocas keywords que regresan tarde (retenidas)
   aportan unos días con el precio reducido a los agregados de su grupo.
6. **El ×2 no compensa exactamente la caída de conversión.** No está medida; el multiplicador es
   editable.
7. **La proyección del CPC de R6r en saltos de ×2 a ×3 no está medida.** Es lineal y pesimista; la
   práctica compara lo proyectado con lo medido antes de encender.
8. **La meta de alta puede perder dinero.** Es libre: si pasa el equilibrio, cada venta con ACoS entre
   el equilibrio y la meta pierde. Ajustes lo muestra en rojo con el equilibrio calculado con el costo
   de hoy. El aviso es por plataforma: una keyword que decida con meta de producto o de familia (V) y
   un equilibrio propio bajo 25 pierde sin aviso propio; en la lectura del 2026-11-12 ninguna decidía así.
9. **El cupo de 1,000,000 cuenta operaciones, no dinero.** La entrada suele salir en un ciclo, pero un
   fallo HTTP la deja para mañana; los conteos de la tarjeta son confirmaciones.
10. **Propuestas de presupuesto con huecos de historial.** Sin fila en `ads_campana_config_observation`
    anterior al episodio no hay propuesta.
11. **Apagar no es deshacer.** Apagar la temporada a media baja deja los bids reducidos y el episodio
    abierto; al volver a encender se retoma. Mientras está apagada (o en práctica) rigen las reglas de hoy
    y Orbit puede recortar por evidencia; al volver, el regreso conserva esos recortes en proporción
    (`nivel_de_regreso`) y un episodio de otra baja se cierra y se abre uno nuevo. Para terminar la baja
    está «Terminar la baja hoy».
12. **Las pausas anteriores al encendido no se reactivan.** No tienen episodio; «pausa de la baja» es un
    dato de la decisión, no una inferencia por fechas.
13. **Una pausa en la cola el día de la salida retrasa el regreso** hasta que la cola la resuelva (hasta
    48 horas).
14. **El contrato de ARREGLOS 01 no está en el código de `289f9dd` ni en `de895d18`.** P (`frena_subidas`), T, U
    (`version_economia`), V, W y X (`TECHOS_HUMANOS_POR_MONEDA`, `exige_bajo_techo`) se citan por su plan.
    `version_economia` viene del diseño que U.1 manda leer
    (`docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`), no del texto del plan de U; si U
    lo integra con otro nombre o conserva `con_economia`, vale el código real. El plan relee todas estas
    firmas antes del bloque I.
15. **El historial congelado crece.** `HistoriaBid` guarda hasta 365 días (o más, si una keyword sigue
    reducida) con instantes; es más JSON por decisión a cambio de no tocar la semántica de R1, R2,
    R14–R17 ni `piso_aprendido`.
16. **La práctica no prueba lo que pasa en Amazon.** La lectura fresca, la exclusión y las reactivaciones
    solo se ven en vivo; por eso tienen prueba con base y el encendido tiene criterios de paro.

---

## Historia de revisiones

Hubo dos diseños completos antes de este. El primero congelaba los bids en la baja; David lo rechazó
porque en la baja cada clic pierde dinero, y de él quedaron la cadena de `v_cambio_bid`, la subida rápida
R6r, la cerca de evidencia y la corrección de `_SQL_PAUSE_PROPIO`. El segundo (opus contra sonnet, con
base opus) introdujo el calendario de 12 meses, la reducción planeada con origen propio y los
presupuestos solo propuestos. La arena de Astra (dos candidatos nuevos) cambió la meta de alta de +5
puntos a 25 % absoluta, agregó el episodio durable, el calendario por intervalos reales, la guarda común
de destinos, el techo de baja compartido por R6 y R6r, el ×2 editable y extendido a la pausa económica, la
reactivación ligada al episodio, los presupuestos absolutos con `APLICAR AJUSTE` y la entrada también
para inertes, con MX y US juntos. El interrogate de Astra comprobó con corridas cinco fallas: presupuestos
fuera de orden (existe hoy), pausa antes del regreso, día mixto, redondeo sobre 30 % y booleanos como
porcentajes. La revisión cruzada de Kimi y Grok (las dos con cambios) pidió que el regreso pendiente vaya
antes de R0, que la mano de David vista al aplicar se guarde como exclusión, que el episodio tenga un solo
escritor ligado a la confirmación con reconciliación y su migración, una prueba por cada unidad nueva de
dinero o escritura, un solo contrato sin anexos, y corregir tres afirmaciones (R3 al salir de la alta, la
espera por venta reciente en el regreso y «todas dañadas el primer día»). La verificación del 2026-11-12
contra `de895d18` (tres lectores: anclas de código, trazabilidad de las decisiones y adversario de dinero)
fechó la mano sin registro al verse (antes excluía la keyword de todas las bajas siguientes), hizo mano
del dueño a la reactivación manual, conservó en proporción los recortes hechos con la temporada apagada,
cierra un episodio de otra baja, dejó que el regreso del presupuesto no bloquee la propuesta de alta, no
excluye por una escritura que no llegó a Amazon, y corrigió el riesgo 2, el ejemplo del 21 de marzo, el
aviso rojo, la lista de negatives, la tabla de bloques y la vigilancia de las transiciones en H.
