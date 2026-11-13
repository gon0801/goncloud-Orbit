# ARREGLOS 02: diseño

Base: master `a4644aad`, lo mismo que corre en producción. Fuentes: `docs/evidencia/arreglos-2026-11-12/{politica,ciclo,apply,estimacion}.md`.
Para quién: David. Hoy el motor puede regresar una keyword al bid donde ya perdió su tráfico, frenar subidas tras un ajuste de campaña, y fija en México y en Estados Unidos una meta de ACoS más baja de la que manda su regla, porque cuenta dos veces la comisión de Amazon. El recorte de una keyword por el gasto de su ad group cuando el grupo es casi solo ella se revisó y no entra: con el margen corregido por D, esas keywords pierden dinero (sección «C1»).

Cómo se midió. Todas las consultas a producción son de solo lectura con `docs/evidencia/arreglos-01/lee_prod.sh` y están escritas en cada sección. Las 49 decisiones `niveles_v3` de producción (todas, del 2026-10-12 al 2026-11-12) se bajaron con:

    echo "SELECT id, ad_entity_id, decided_at::date, kind, new_value, value_currency, inputs::text FROM decision WHERE inputs->>'politica'='niveles_v3' ORDER BY id" | bash docs/evidencia/arreglos-01/lee_prod.sh

y se rejugaron en local con `reproduce()` del código de `a4644aad` (49 de 49 salen idénticas) y con un prototipo de cada forma candidata, que sustituye `_paso`, `_freno_trayectoria` y `estado_grupo` sin tocar el repo. Límite de esa medición: los `Mantener` no se guardan como decisión, así que solo se ve qué decisiones guardadas cambian, no qué `Mantener` se volverían movimiento.

Letras: los bloques se llaman A a D, como en `plans/arreglos-02.md`. Las pruebas usan la letra en minúscula: `test_arreglos02_a_`, `_b_`, `_c_` y `_d_`.

---

## B. El piso aprendido se compara con un bid que Amazon nunca recibe

Cubre `politica.md` F1, `ciclo.md` F5 y la primera viñeta de la falla 12 de `apply.md`.

**Qué pasa hoy.** `_paso` (`app/optimizer/politica.py:622-645`) calcula el bid nuevo con 4 o 6 decimales y lo compara con el piso aprendido (`:636`). El apply lo redondea después a centavos con `ROUND_HALF_EVEN` (`app/ads/write.py:187-198`), así que 0.2904 pasa el piso de 0.29 y Amazon recibe 0.29. Hay dos números para el mismo bid: el decidido, que se guarda en `decision.new_value` (`app/cycle.py:1063`) y en `v_cambio_bid.bid_despues`, y el enviado.

**Forma elegida. El motor decide en centavos.** La política entrega siempre un bid de dos decimales. Lo decidido es lo que recibe Amazon, y `_bid_payload` deja de cambiar nada para lo que decide el motor.

Uso del llamador:

```python
# app/optimizer/politica.py, dentro de _paso y de R1
nuevo = centavo_hacia(min(max(valor * (_UNO + factor_c), bid.piso), bid.techo), desde=valor)
# 0.33 x 0.88 = 0.2904 -> 0.30 (un recorte redondea hacia arriba: nunca baja más que el paso)
# 0.33 x 1.15 = 0.3795 -> 0.37 (una subida redondea hacia abajo: nunca sube más que el paso)
```

Firmas y seudocódigo:

```python
# app/optimizer/centavos.py (módulo nuevo, puro, sin imports de app)
CENTAVO = Decimal("0.01")

def centavo_hacia(destino: Decimal, *, desde: Decimal) -> Decimal:
    """El centavo más cercano a `destino` que no se aleja de `desde` más que `destino`.
    Un recorte redondea hacia arriba y una subida hacia abajo: el bid que recibe Amazon
    nunca se mueve más que el paso decidido ni cruza un piso por redondeo."""
    # modo = ROUND_CEILING if destino < desde else ROUND_FLOOR
    # return destino.quantize(CENTAVO, rounding=modo)
```

```python
# app/optimizer/politica.py
def _paso(bid, factor, motivo, nivel, piso_aprendido, *, centavos: bool) -> Mover | Mantener:
    # valor = bid.valor; None o <= 0 -> Mantener(dato_faltante)       (como hoy)
    # factor_c = clamp(factor, [-0.30, +0.20])                          (como hoy)
    # nuevo = min(max(valor * (1 + factor_c), bid.piso), bid.techo)     (como hoy)
    # if centavos: nuevo = centavo_hacia(nuevo, desde=valor)            (NUEVO)
    # R17: factor_c < 0 y piso_aprendido y nuevo <= piso_aprendido -> Mantener(piso_aprendido)
    # delta mínimo, dirección y magnitud                                (como hoy, sobre `nuevo`)

def decide(caso):
    # centavos = caso.revision >= 1
    # R1: destino = min(max(ultimo.bid_antes, piso), techo)
    #     if centavos: destino = centavo_hacia(destino, desde=valor)    (NUEVO; hoy es identidad
    #                  porque bid_antes ya viene en centavos, pero deja el invariante completo)
    # ... return _paso(caso.bid, factor, motivo, nivel, caso.trayectoria.piso_aprendido,
    #                  centavos=centavos)

def _freno_trayectoria(...):
    # se BORRA el bloque final `if factor < 0: ... bid * (1 + factor) <= piso -> Mantener(piso_aprendido)`
    # (`:423-427`). El piso aprendido queda en un solo lugar: `_paso`, después de los límites y
    # del redondeo. El docstring de `decide` mueve R17 al paso 4.
```

```python
# app/optimizer/caso.py
REVISION_POLITICA = 1   # ARREGLOS 02 B: bid en centavos.

@dataclass(frozen=True)
class CasoHoja:
    ...                       # campos de hoy
    revision: int = 0         # ausente en las decisiones viejas = 0 = semántica de su era

# como_json: escribe "revision" solo si es > 0 (un caso viejo serializa idéntico).
# desde_json: d = _objeto_con_opcionales(datos, _CLAVES_CASO, frozenset({"revision"}), "caso")
#             revision = _entero(d["revision"], "revision") if "revision" in d else 0

def _objeto_con_opcionales(datos, obligatorias: frozenset, opcionales: frozenset, nombre) -> dict:
    """Como `_objeto`, pero acepta cualquier subconjunto de `opcionales`. Clave ajena = error."""
    # if not isinstance(datos, dict) or not (obligatorias <= set(datos) <= obligatorias | opcionales):
    #     raise ValueError(...)
```

```python
# app/lecturas_caso.py:506, el único constructor de CasoHoja
return CasoHoja(..., revision=REVISION_POLITICA)

# app/ads/write.py:198
return str(bid.quantize(CENTAVO, rounding=ROUND_HALF_EVEN))   # CENTAVO de app.optimizer.centavos
```

`_bid_payload` conserva `ROUND_HALF_EVEN` porque también codifica bids que no decide el motor (fábrica, reversas, harvest). Para un bid del motor ya es identidad. `app.ads` puede importar de `app.optimizer` (el candado de `tests/test_architecture.py:42` solo prohíbe el sentido contrario).

**Por qué un módulo propio y no `politica.py`.** TEMPORADA 01 crea `app/optimizer/temporada.py`, que `politica.py` importa, y ese módulo necesita el mismo centavo (en su diseño, la constante de `temporada.py` y `destino_entrada`). Si la función viviera en `politica.py`, habría un import circular.

**Otra forma, descartada. `ROUND_HALF_EVEN` en la política**, o sea, mover `_bid_payload` a la capa pura y comparar contra eso. Medido sobre las 49 decisiones: bloquea los 4 recortes que cruzan el piso (2846, 2865, 2866, 2904) y no cambia ningún otro bid. Pero rompe el límite de paso en bids chicos: 0.06 USD con −25 % da 0.045, que redondea a 0.04 (−33 %), y `_paso` lo tendría que rechazar con `rango_bloquea_ajuste`, así que esa keyword ya no se podría recortar nunca. Hoy pasa y Amazon recibe −33 % (el diseño de TEMPORADA lo documenta con 0.15 USD, punto 2 de «Lo que hace que la forma no sea obvia»). Además TEMPORADA ya eligió redondear hacia el bid de hoy (`ceil_centavo` en su entrada); con dos reglas de redondeo habría dos respuestas para el mismo bid.

**Otra forma, descartada. Comparar solo el piso contra el bid redondeado** y dejar los 4 decimales. Arregla el síntoma de F1, pero deja los dos números del mismo bid y el `bid_despues` falso de `v_cambio_bid`.

**Qué cambia, medido.** Con `centavo_hacia`, ninguna de las 49 decisiones cambia de veredicto. 21 mandarían un centavo distinto: los recortes quedan un centavo más arriba y las subidas un centavo más abajo. Las cuatro hojas del círculo reciben un bid arriba de su piso, no en él:

| Decisión | Hoja | Piso | Hoy decide → recibe Amazon | Con B |
|---|---|---|---|---|
| 2865 | 4934 | 0.29 | 0.2904 → 0.29 | 0.30 |
| 2866 | 5336 | 0.42 | 0.4224 → 0.42 | 0.43 |
| 2904 | 4924 | 0.26 | 0.2625 → 0.26 | 0.27 |
| 2846 | 5895 | 0.12 | 0.1232 → 0.12 | 0.13 |

Un recorte siguiente desde 0.30 da 0.264 → 0.27, que ya está bajo el piso de 0.29: `Mantener(piso_aprendido)`. El círculo se corta. Si 0.30 también tumba el tráfico, R1 regresa y el piso aprendido pasa a 0.30.

**Regla de replay.** Hace falta marcador: 21 de 49 bids cambian. El marcador es `CasoHoja.revision` (0 en todo lo guardado hasta hoy). Una decisión vieja se rejuega con su semántica y sus 4 decimales.

**Qué se borra.** El segundo piso aprendido de `_freno_trayectoria` (`:423-427`). Medido: quitarlo no cambia ningún veredicto de las 49 (el de `_paso` lo cubre; solo difiere cuando el piso del goal sube el recorte por encima del piso aprendido, y entonces el bid no cruza el piso). Quitarlo no necesita marcador: R17 solo produce `Mantener`, que no se guarda como decisión, así que ninguna decisión guardada pasó por él.

**Cruces con ARREGLOS 01.** T.2 cambia el juicio de repetición dentro de `_freno_trayectoria` (`plans/arreglos-01.md:269`). B solo borra el último bloque `if factor < 0` de esa función, anclado por su contenido. Conviven. U, V, W y X no tocan `_paso`, R1 ni `CasoHoja`.

**Cruces con TEMPORADA 01, aplicados en este PR.** El diseño de TEMPORADA se escribió antes de este arreglo. Estos cambios ya están hechos en `docs/evidencia/temporada-01/DISENO.md`; cada uno se ubica por su texto, no por número de línea.
1. En las constantes de `temporada.py`, la línea `CENTAVO = Decimal("0.01")` pasó a `from app.optimizer.centavos import CENTAVO, centavo_hacia`.
2. En `destino_entrada`, `# d = (bid * (1 - reduccion)).quantize(CENTAVO, ROUND_CEILING)` pasó a `# d = centavo_hacia(bid * (1 - reduccion), desde=bid)`.
3. Al final del punto 2 de «Lo que hace que la forma no sea obvia» se agregó que `_paso` ya decide en centavos con `centavo_hacia` (0.15 USD con −30 % da 0.11, no 0.1050) y que el destino de temporada usa la misma función.
4. En `CasoHoja`, el comentario de `desde_json` suma la opcional `"revision"` a las cuatro de temporada y dice que `_objeto_con_opcionales` ya existe. En el «Mapa de módulos», la fila de `caso.py` ya no dice que TEMPORADA crea esa función.
5. «Solo R17» pasó a «ningún freno de `_freno_trayectoria`; el piso aprendido (R17) lo aplica `_paso`» en tres lugares: los FRENOS del seudocódigo de `decide`, la última viñeta de «Frenos» en «Recuperación rápida (R6r)» y «Lo que hacen los frenos con un movimiento de temporada».
6. La llamada a `_paso` del seudocódigo de `decide` pasa `centavos=caso.revision >= 1`, y una línea dice que `techo_extra` se aplica antes de `centavo_hacia`, junto con piso y techo.
7. En «Ejemplo de una keyword durante un año», los recortes del 16 ago y del 10 dic dicen el centavo que decide `_paso` (8.10 y 7.13), el mismo que Amazon ya recibía.
8. `TOLERANCIA_CADENA`, sin cambio: los `bid_despues` anteriores a B siguen con 4 decimales y los nuevos ya son centavos exactos; la tolerancia sirve para los dos.

Ninguno depende de una revisión 2: la única revisión de este plan es la 1.

---

## C1. «Heredar del ad group» incluye a la propia hoja

**No entra al plan. Esta sección queda como registro.** Lo midió el lector adversario de la verificación del 2026-11-12 (`docs/evidencia/arreglos-2026-11-12/lectores/adversario-dinero.md`, punto 1). El bloque D se despliega antes que C y cambia el punto de equilibrio. Con esa economía (metas de 22.75 y 23.19, equilibrios de 45.5 y 29.0), C1 solo cambia 3 de las 49 decisiones reales, no 8, y las tres son keywords con ACoS sobre su equilibrio, es decir, que pierden dinero:

| Decisión | Hoja | ACoS de la política | Equilibrio con D | P(ACoS sobre el equilibrio) | Hoy | Con C1 |
|---|---|---|---|---|---|---|
| 2865 | 4934 | 33.0 % | 30.0 % | 0.47 | Mover −12 % | Mantener |
| 2867 | 5567 | 34.7 % | 30.0 % | 0.33 | Mover −12 % | Mantener |
| 2902 | 5891 | 30.2 % | 29.4 % | 0.40 | Mover −12 % | Mantener |

R3 no las recorta porque con 6 a 13 pedidos la confianza no llega a 0.80. Con C1, R4 tampoco, mientras el resto de su grupo no junte 3 pedidos. El recorte de hoy es correcto: quitarlo costaría unos 248 USD cada 40 días contra el equilibrio de 29 %, cerca de 186 USD al mes (4934: 104 USD, 5891: 112 USD, 5567: 32 USD). Sin C1 no hay revisión 2: `REVISION_POLITICA` se queda en 1, y `Tramo.sin`, `EvidenciaNivel.sin` y `_estado_heredado` no se escriben.

Queda como pregunta de regla para el dueño: ¿una keyword que es casi todo su ad group debe juzgarse con el grupo completo, como hoy, o sin ella? Si un día se retoma, la forma de abajo necesita además la guarda que propone el lector adversario: si la keyword pierde dinero (ACoS sobre el equilibrio), hereda del grupo completo.

Cubre `politica.md` F3.

**Qué pasa hoy.** `decide` llama `estado_grupo(caso.grupo, ...)` (`politica.py:262`) con el grupo completo, que incluye a la hoja (`caso.py:393`, `lecturas_caso.py:857-861`). Si la hoja es casi todo el grupo, R4 (`politica.py:352`) la recorta por su propio ACoS crudo. La previa de la subida sí la excluye (`_previa`, `:499-501`): la misma idea con dos criterios.

**Forma elegida. El veredicto heredado sale del resto del grupo, y una vendedora solo hereda si el resto tiene pedidos.**

Uso del llamador:

```python
# app/optimizer/politica.py, decide
crudos = propia.pedidos_crudos() if propia is not None else 0      # se calcula antes, como dato puro
if crudos is None: return Mantener(MOTIVO_DATO_FALTANTE)
estado = _estado_heredado(caso, vendedora=crudos >= 1)              # antes: estado_grupo(caso.grupo, ...)
```

Firmas:

```python
# app/optimizer/caso.py
class Tramo:
    def sin(self, parte: Tramo) -> Tramo | None:
        """Este tramo menos `parte`, campo por campo. None en un campo si falta en cualquiera de
        los dos (desconocido). None entero si algún campo queda negativo (datos incoherentes)."""

class EvidenciaNivel:
    def sin(self, parte: EvidenciaNivel | None) -> EvidenciaNivel | None:
        """El nivel sin `parte`, tramo por tramo, con el mismo esquema. `parte` None -> self."""

# app/optimizer/politica.py
def _estado_heredado(caso: CasoHoja, *, vendedora: bool) -> EstadoGrupo:
    # if caso.revision < 2: return estado_grupo(caso.grupo, caso.economia)   (semántica vieja)
    # resto = caso.grupo.sin(caso.propia) if caso.grupo is not None else None
    # if vendedora and (resto is None or resto.pedidos_crudos() is None
    #                   or resto.pedidos_crudos() < PEDIDOS_GRUPO):
    #     return "sin_veredicto"          # sin otras hojas con pedidos no hay veredicto que heredar
    # return estado_grupo(resto, caso.economia)

def _previa(cuenta, grupo, propia):
    # el resto se calcula con grupo.sin(propia) en vez de restar los crudos a mano (`:499-503`)
```

`estado_grupo` no cambia: sigue siendo la única definición de la regla del dueño. Solo cambia qué grupo recibe.

**Qué decide `estado_grupo` sin la hoja cuando el resto no llega a 3 pedidos (pregunta d).** Con 1 o 2 pedidos en el resto, `sin_veredicto`. Con 0 pedidos, `sangra` si el resto gastó al menos `gasto_para_concluir` (350 MXN / 36 USD), y si no `sin_veredicto` (`politica.py:513-543`). Para una vendedora, `_estado_heredado` corta antes: si el resto no tiene 3 pedidos, no hay veredicto que heredar. Sin ese corte se abriría un camino de recorte que hoy no existe: una vendedora recortada porque sus hermanas gastan sin vender. Hoy ese camino no se alcanza, porque el grupo incluye los pedidos de la propia hoja. Las hermanas ya se recortan solas por R9. Para una hoja sin pedidos (R11), el resto se juzga completo: su propio gasto ya no la empuja a «grupo que sangra».

**Otra forma, descartada. Armar el grupo sin la hoja en `lecturas_caso.py`.** Cambia el significado del campo congelado `grupo`, rompe el replay de todo lo guardado y obliga a cambiar `_previa`, que hoy resta la hoja. Restar en la política deja el caso igual y la regla en un solo lugar.

**Medido en producción (pregunta d).** Decisiones `grupo_sangra_vendedora` de los últimos 60 días: 15, sobre 11 hojas. En 6 de las 15 (4 hojas: 4934 dos veces, 5891 dos veces, 2457 y 2874) la hoja era más de la mitad del gasto pesado de su grupo. Con la forma elegida cambian 8 de las 49 decisiones, todas de recorte a `Mantener`:

| Decisión | Hoja | Hoja / grupo | Pedidos del resto | Estado del resto |
|---|---|---|---|---|
| 2835 | 5336 | 0.41 | 9 | cumple |
| 2842 | 4934 | 0.96 | 0 | sin veredicto |
| 2854 | 2457 | 0.77 | 3 | sin veredicto |
| 2856 | 5891 | 0.88 | 5 | cumple |
| 2863 | 4902 | 0.25 | 2 | sin veredicto |
| 2865 | 4934 | 0.98 | 0 | sin veredicto |
| 2902 | 5891 | 0.91 | 5 | cumple |
| 2908 | 2874 | 0.68 | 2 | sin veredicto |

Las otras 7 `grupo_sangra_vendedora` siguen igual: el resto de su grupo sangra con 7 a 15 pedidos. La 2865 pasa a `Mantener(vende_dentro_del_margen)`. La previa de la subida calculada con `sin` da lo mismo que hoy en las 49.

**Regla de replay.** Hace falta marcador: 8 de 49 cambian. `REVISION_POLITICA` sube a 2 y `_estado_heredado` respeta la revisión congelada.

**Qué se borra.** La resta a mano de `_previa` (`politica.py:499-503`, tres restas y su chequeo de negativos), reemplazada por `grupo.sin(propia)`.

**Cruces.** ARREGLOS 01 T, U, V, W y X no tocan `estado_grupo` ni `_previa`. TEMPORADA 01 no los nombra (búsqueda de `estado_grupo` y `_previa` en su diseño: 0 resultados). Su HOLD retiene R4 en la baja; con C, R4 dispara menos. Sin parche.

---

## C2. Un ajuste de campaña cuenta como subida

Cubre `politica.md` F4.

**Qué pasa hoy.** `CambioBid.direccion` (`caso.py:136-138`) da +1 con `bid_antes == bid_despues`. Tras un ajuste de ubicación, `_freno_trayectoria` (`politica.py:392`) lo trata como subida: frena la siguiente subida por `subida_sin_trafico` y hace esperar un recorte como inversión (20 clics o 14 días).

**Forma elegida. La dirección de un cambio sin cambio de bid es 0, y con 0 no hay frenos de repetición.**

```python
# app/optimizer/caso.py
@property
def direccion(self) -> Literal[-1, 0, 1]:
    # 0 si bid_despues == bid_antes (ajuste de campaña); 1 si sube; -1 si baja

# app/optimizer/politica.py, al inicio de _freno_trayectoria
if ultimo.direccion == 0:
    return None    # un ajuste solo hace esperar R2 (ya cumplido): ni R14, R15, R16 ni R16b
```

Los demás lectores de `direccion` no cambian de resultado: `_evalua_r1` pregunta `!= -1`, `piso_aprendido` pregunta `== 1` solo para regresos (que siempre mueven el bid) y `tools/rejuega_niveles.py:295` pregunta `== -1`.

**Otra forma, descartada. Preguntar `bid_antes == bid_despues` dentro de `_freno_trayectoria`** sin tocar `direccion`. Deja el dato mintiendo: cualquier lector nuevo de `direccion` repetiría la falla.

**Medido en producción.**

    echo "SELECT (SELECT count(*) FROM campana_ajuste) ajustes, (SELECT count(*) FROM decision WHERE inputs->>'politica'='niveles_v3' AND inputs->'caso'->'trayectoria'->'cambios' @> '[{\"origen\":\"ajuste_de_campana\"}]') decisiones_con_ajuste" | bash docs/evidencia/arreglos-01/lee_prod.sh
    0|0

**Regla de replay.** Consulta previa en 0 filas: ninguna decisión guardada tiene un ajuste en su trayectoria. El bloque la corre antes de desplegar y la guarda. Si da más de 0, el bloque para y escribe la diferencia: no se despliega sin marcador.

**Pruebas de hoy que cambian.** Tres pruebas fijan el comportamiento viejo y se cambian en el bloque. Medido con un prototipo de esta forma sobre `a4644aad` (solo C2, sin C1): `tests/test_optimizer_caso.py::test_direccion_subida_bajada_y_ajuste` falla porque 10.00 → 10.00 ya da 0; `tests/test_optimizer_politica.py::test_ajuste_no_dispara_r1` y `::test_ajuste_escalera_no_proyecta` fallan porque `_caso_ajuste(10)` ya no da `Mantener("esperando_precio_medido")` sino `Mover(-0.12, 8.80, "gasto_sin_venta", "hoja")`. La primera pasa a afirmar 0. La segunda conserva que no es `Regresar` y espera ese `Mover`. La tercera afirma lo que su nombre promete: `_precio_de_hoy(caso)` sale de `ventana_madura`, no de la escalera. Las demás 146 pruebas de `test_optimizer_caso.py`, `test_optimizer_politica.py`, `test_rejuega_niveles.py` y `test_cycle.py` pasan igual.

**Cruces.** T.2 también edita `_freno_trayectoria`. El retorno temprano va antes de todo lo que T agregue. TEMPORADA excluye `ajuste_de_campana` de su `CadenaBid` (en su diseño, el campo `cambios`: «sin ajuste_de_campana, ascendentes») y no lee `direccion`. Sin parche.

---

## D. La comisión de Amazon se cuenta dos veces

Cubre `estimacion.md` F1. La revisión solo lo midió en México; también pasa en Estados Unidos.

**Qué pasa hoy.** Contabilidad manda una comisión estimada por pedido (`amazon|fee_estimate|referral_fee|<pedido>`) antes de la liquidación, y luego la real (`amazon|finance|fee|...|Commission` en México, `amazon_us|finance|fee|...|Commission` en Estados Unidos). Tienen llaves distintas, así que el libro, que es de solo agregar (`app/ledger.py:506-525`), guarda las dos. Las cuatro vistas que suman cargos las restan las dos. La revisión buscó la real con el prefijo `amazon|finance`, que en Estados Unidos no existe, y por eso no vio el duplicado de Estados Unidos.

**Medido en producción.** Ventana de la meta (hoy−105 a hoy−15), solo cargos de pedidos cubiertos, que son los que suma la vista:

    -- archivo q_margen.sql, corrido con lee_prod.sh
    WITH ventana AS (SELECT CURRENT_DATE - 105 AS desde, CURRENT_DATE - 15 AS hasta),
    ventas AS (
      SELECT l.platform, l.order_id, l.amount,
             CASE WHEN c.id IS NOT NULL AND c.cost_currency = l.amount_currency THEN c.cost_amount * l.quantity END AS cogs_linea
        FROM ledger_event l CROSS JOIN ventana v
        LEFT JOIN sku_cost c ON c.product_id = l.product_id AND l.event_date >= c.valid_from AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
       WHERE l.kind = 'sale' AND l.event_date >= v.desde AND l.event_date < v.hasta),
    oc AS (SELECT DISTINCT platform, order_id FROM ventas WHERE cogs_linea IS NOT NULL AND order_id IS NOT NULL),
    est AS (
      SELECT l.platform, l.order_id, l.amount,
             EXISTS (SELECT 1 FROM ledger_event f WHERE f.platform = l.platform AND f.order_id = l.order_id
                       AND f.kind = 'fee' AND f.fee_type = 'referral_fee' AND split_part(f.source_event_id, '|', 2) <> 'fee_estimate') AS con_real
        FROM ledger_event l JOIN oc o ON o.platform = l.platform AND o.order_id = l.order_id
       WHERE l.source_event_id LIKE 'amazon|fee_estimate|%'),
    vc AS (SELECT platform, sum(amount) FILTER (WHERE cogs_linea IS NOT NULL) AS cubierta FROM ventas GROUP BY 1)
    SELECT t.platform, round(t.margen_neto_pct, 2) AS margen_hoy, vc.cubierta,
           coalesce(sum(e.amount) FILTER (WHERE e.con_real), 0) AS dup_monto, count(*) FILTER (WHERE e.con_real) AS dup_n,
           coalesce(sum(e.amount) FILTER (WHERE NOT e.con_real), 0) AS est_sin_real_monto, count(*) FILTER (WHERE NOT e.con_real) AS est_sin_real_n,
           round(t.margen_neto_pct - 100.0 * coalesce(sum(e.amount) FILTER (WHERE e.con_real), 0) / vc.cubierta, 2) AS margen_sin_dup
      FROM v_target_margen_plataforma t JOIN vc USING (platform) LEFT JOIN est e USING (platform)
     GROUP BY t.platform, t.margen_neto_pct, vc.cubierta ORDER BY 1

| Plataforma | Margen hoy | Venta cubierta | Comisión duplicada | Pedidos | Estimadas sin real | Margen sin duplicado |
|---|---|---|---|---|---|---|
| amazon_mx | 40.33 % | 537,542.93 | −27,800.42 | 205 | −488.64 (4) | **45.50 %** |
| amazon_us | 23.64 % | 429,171.67 | −22,943.96 | 80 | −618.78 (2) | **28.99 %** |
| meli | sin margen | | 0 | 0 | 0 | sin margen |

La vista se recalcula en cada lectura y su ventana avanza un día cada día. Por eso el ciclo del 2026-11-12 guardó 40.26 % y 23.81 % y la lectura de la tarde dio 40.33 % y 23.64 %. D.3 vuelve a medir justo antes de desplegar.

Meta derivada (fracción × margen; fracciones en `config_version`: 0.5 en México y 0.8 en Estados Unidos; último ciclo, 185 y 184 del 2026-11-12):

| Plataforma | Meta hoy (`notes.target`) | Meta sin duplicado | Cómo llega |
|---|---|---|---|
| amazon_mx | 20.13 % | 22.75 % | sube de una vez en el siguiente ciclo (la bajada es la que camina 0.5 por ciclo, `goals.py:610-615`) |
| amazon_us | 19.05 % | 23.19 % | sube de una vez en el siguiente ciclo |

El equilibrio que usa R3 es el mismo margen: pasa de 40.3 % a 45.5 % en México y de 23.6 % a 29.0 % en Estados Unidos. R3 («pierde dinero») recorta menos.

Por producto (`v_margen_producto`, ventana desde el 2026-02-20): de 7 productos con margen en México, 6 tienen duplicado y su margen sube de 2.07 a 4.25 puntos. De 3 en Estados Unidos, los 3, de 1.34 a 3.48 puntos. `v_margen_familia` no tiene filas hoy. Consulta: `productos.sql`, en «Consultas de D.3».

**Pregunta abierta para el contador.** No cambia ninguna fila; la decisión 2 del dueño la deja al contador. En México, Amazon retiene la mitad del IVA de cada venta. Ejemplo: pedido 701-5496313-1052217, venta de 2,288.00 MXN con IVA, retención de 157.80, el 8 % de 1,972.41. La otra mitad (157.80) la paga el vendedor al SAT, menos su IVA acreditable, y el libro no la resta. En la ventana de la meta, la venta de MX es de 539,231 MXN y la retención de 36,837: esa otra mitad pesa hasta 6.9 puntos de margen. Si el contador dice que es costo, el margen de MX sin duplicado queda entre 38.6 % y unos 42 % (no 45.5 %), y la meta, entre 19.3 % y 21 % (no 22.75 %). Medido por el lector adversario de la verificación (`docs/evidencia/arreglos-2026-11-12/lectores/adversario-dinero.md`, punto 3): una keyword vendedora de MX tiene hoy un ACoS en esa banda (451 MXN de gasto y 1,076 de venta en 40 días), y con D, R3 no la recortaría. Estados Unidos no tiene este problema: su impuesto se retiene completo.

**Forma elegida. Una vista base de cargos que ignora la estimada cuando ya llegó la real del mismo pedido.**

```sql
-- migrations/0086_arreglos02_cargos.sql
CREATE VIEW v_ledger_cargo AS
SELECT l.*
  FROM ledger_event l
 WHERE l.kind IN ('fee', 'refund', 'withholding')
   AND NOT (
         split_part(l.source_event_id, '|', 2) = 'fee_estimate'
         AND EXISTS (
             SELECT 1 FROM ledger_event r
              WHERE r.platform = l.platform
                AND r.order_id = l.order_id
                AND r.kind = l.kind
                AND r.fee_type IS NOT DISTINCT FROM l.fee_type
                AND split_part(r.source_event_id, '|', 2) <> 'fee_estimate'));
```

Uso: las cuatro vistas que suman cargos cambian `FROM ledger_event l` por `FROM v_ledger_cargo l` en sus CTE de cargos, y nada más. Mismas columnas, mismo orden, `CREATE OR REPLACE VIEW`.

| Vista (definición vigente) | CTE de cargos que cambian | Quién la lee |
|---|---|---|
| `v_target_margen_plataforma` (`0016:25-155`) | `cargos`, ramas con orden `:65` y sin orden `:72` | `cycle.py:528` (meta de plataforma y equilibrio de R3), `goals.py:639-690` |
| `v_margen_producto` (`0018:283-419`) | `:321` y `:351` | `productos_io.py:92` (meta por producto), `fabrica_plan.py`, `fabrica_web.py:58`, `tools/fabrica_campanas.py:90,99`, `economia_observada.py` |
| `v_economia_producto` (`0021:33-163`) | `:67` y `:92` (muestra propia) | `economia_observada.py:82,105`, `evaluacion_catalogo.py` |
| `v_margen_familia` (`0048:29-210`) | `:60` y `:152` | `cycle.py:537` (meta de familia), `economia_revalidacion_io.py:47` (cola) |

Las cuatro heredan hoy el doble conteo. No lo heredan:
- `v_tacos` (`0012`): suma ventas y el gasto de métricas, no cargos.
- `v_contribucion_entidad` (`0010`) y `v_contribucion_cobertura`: son «pre-cargos» por diseño (`cargos_excluidos = fee,refund,withholding`).
- `v_desfase_gasto_ads` (`0006`): solo `fee_type = 'ads'`.
- `v_precio_venta_unidad` (`0054`): solo ventas.
- El motor de precios y las pantallas de precios: usan la estimación de comisiones por publicación (`estimacion_ingest`), no el libro.
- Lectores Python del libro (`pantalla_productos.py:210,239`, `familias.py:158,241`, `envio_muestras.py:53`): suman unidades o pedidos, no cargos.

**Las estimadas sin real siguen contando (pregunta b).** Son el mejor costo conocido de un pedido que aún no se liquida. Quitarlas inflaría el margen de los pedidos recientes. En la ventana de la meta son 4 en México (−488.64) y 2 en Estados Unidos (−618.78), porque la ventana ya termina 15 días atrás. La regla de la vista las deja contar mientras no llegue la real.

**Otra forma, descartada. Arreglarlo en la ingesta.** No importar las estimadas no limpia la historia: el libro no deja borrar ni editar filas (solo reatribuir producto desde ARREGLOS 01 Y), y las 285 estimadas duplicadas de la ventana de la meta ya están dentro. Escribir filas de compensación inventaría movimientos que contabilidad no tiene. Además, sin estimadas, un pedido sin liquidar contaría comisión cero. La vista base deja la regla en un lugar, no escribe datos de producción y se deshace con la reversa.

**Límite declarado.** Un pedido con varias líneas de producto donde solo una ya tiene comisión real pierde la estimada completa. Hoy la estimada es una por pedido: de los 362 pedidos con estimada (265 en MX y 97 en US), 0 tienen más de un producto (`estimadas.sql`, corrida el 2026-11-12). Otro caso: una fila real `referral_fee` de una devolución (`RefundCommission` o la de finanzas de tipo `refund`) borraría la estimada si llegara sola, sin la `Commission`. Hoy no pasa: los 5 pedidos con estimada que tienen una de esas filas también tienen su `Commission`. La comisión real es mayor que la estimada en 257 de 257 pedidos de MX, así que quitar la estimada nunca deja menos que la real.

**Consultas de D.3.** Se guardan en `docs/evidencia/arreglos-02/D/` y se corren con `bash docs/evidencia/arreglos-01/lee_prod.sh < docs/evidencia/arreglos-02/D/<nombre>.sql`. Las cuatro se corrieron el 2026-11-12 en producción y dieron las cifras de esta sección.
- `q_margen.sql`: el bloque SQL de «Medido en producción», arriba, sin su línea de comentario.
- `productos.sql`: compara `v_margen_producto` de hoy con la misma vista sin el duplicado. Para armarlo se copia el cuerpo de `0018:284-419` (desde `WITH ventana AS (` hasta el final, sin el `;`), se agrega como primer CTE `v_ledger_cargo AS (<el SELECT de la vista de abajo>)` y las líneas `:321` y `:351` pasan a `FROM v_ledger_cargo l`. Ese cuerpo va en el lugar de `<cuerpo>`:

      SELECT h.platform, h.product_id, round(h.margen_neto_pct, 2) AS hoy, round(n.margen_neto_pct, 2) AS sin_dup,
             round(n.margen_neto_pct - h.margen_neto_pct, 2) AS sube
        FROM v_margen_producto h
        JOIN (<cuerpo>) n ON n.platform = h.platform AND n.product_id = h.product_id
       WHERE h.margen_neto_pct IS NOT NULL
       ORDER BY 1, 2;

- `estimadas.sql`: pedidos con estimada por plataforma y cuántos tienen más de un producto (el límite de arriba).

      WITH est AS (
        SELECT platform, order_id FROM ledger_event
         WHERE split_part(source_event_id, '|', 2) = 'fee_estimate' AND order_id IS NOT NULL
         GROUP BY 1, 2),
      prod AS (
        SELECT l.platform, l.order_id, count(DISTINCT l.product_id) AS productos
          FROM ledger_event l JOIN est e ON e.platform = l.platform AND e.order_id = l.order_id
         WHERE l.kind = 'sale'
         GROUP BY 1, 2)
      SELECT e.platform, count(*) AS pedidos_con_estimada, count(*) FILTER (WHERE p.productos > 1) AS con_varios_productos
        FROM est e LEFT JOIN prod p ON p.platform = e.platform AND p.order_id = e.order_id
       GROUP BY 1 ORDER BY 1;

- `meta.sql`: la meta y el margen de `notes.target` del último ciclo de cada plataforma. `optimizer_cycle.notes` es texto, por eso el cast a `jsonb`, y solo se toman las notas que son JSON con `target`.

      SELECT c.id, c.platform, (c.notes::jsonb)->'target'->>'margen_neto_pct' AS margen_neto_pct,
             (c.notes::jsonb)->'target'->>'target_aplicado' AS target_aplicado
        FROM optimizer_cycle c
       WHERE c.id IN (SELECT max(id) FROM optimizer_cycle
                       WHERE platform IN ('amazon_mx', 'amazon_us')
                         AND CASE WHEN notes LIKE '{%' THEN (notes::jsonb) ? 'target' ELSE false END
                       GROUP BY platform)
       ORDER BY c.platform;

**Rendimiento medido.** La lógica de la vista base sobre todo el libro corre en 36 ms en producción (`EXPLAIN ANALYZE`, índice `ledger_event (order_id)` de `0001:611`).

**Regla de replay.** No aplica: `decide` no cambia. Cada decisión congela su meta y su equilibrio en `caso.economia`.

**Cruces con ARREGLOS 01.** V cambia cómo la cola usa la meta de familia (`economia_revalidacion_io.py`, fila V.3 en `plans/arreglos-01.md:296`), pero no la vista `v_margen_familia`. Y agrega un trigger a `ledger_event` sin columnas nuevas. Conviven.

**Cruce con TEMPORADA 01, bloque I (pregunta c), aplicado en este PR.** I.2 recrea `v_target_margen_plataforma`, `v_margen_producto` y `v_margen_familia` para agregar el margen con el costo de hoy, con «los mismos cargos» y citando las definiciones de `0016` y `0018`. Si las recrea desde esos archivos, vuelve a leer `ledger_event` y regresa el doble conteo, también en su `margen_vigente_pct`. Si las recrea desde la definición de D, lo hereda arreglado. Estos cambios ya están hechos; cada uno se ubica por su texto:
1. `plans/temporada-01.md`, al final de la fila I.2: «Las tres vistas se recrean desde su definición en `migrations/0086_arreglos02_cargos.sql` (o el número que tomó el bloque D de ARREGLOS 02): sus cargos salen de `v_ledger_cargo`, nunca de `ledger_event`. La reversa restaura esa misma definición, no la de `0016`, `0018` o `0048`.»
2. Diseño de TEMPORADA, punto 1 de «Punto de equilibrio con el costo vigente»: «la misma fórmula, los mismos cargos (de `v_ledger_cargo`, ARREGLOS 02 bloque D) y las mismas guardas».
3. Equilibrio sin el duplicado, con el costo de cada venta (el de hoy lo mide I.2): **45.5 % en México y 29.0 % en Estados Unidos** (antes 40.3 % y 23.8 %). Con la meta de alta de 25 %, las dos plataformas quedan debajo de su equilibrio: el caso «US pierde dinero en alta» deja de existir con los datos de hoy. Textos cambiados:
   - `plans/temporada-01.md`, decisión 4: dice el equilibrio medido sin la comisión duplicada (45.5 % y 29.0 %), que con 25 % las dos quedan debajo, y que antes se leían 40.3 % y 23.8 %.
   - Diseño de TEMPORADA, punto 1 de «Lo que hace que la forma no sea obvia»: después de «40.26 % en MX y 23.81 % en US» dice que traían la comisión contada dos veces, que sin ella son 45.5 % y 29.0 %, y que las metas de `notes.target` pasan a cerca de 22.75 y 23.19.
   - Maqueta de la tarjeta de Ajustes: MX «Hoy tu meta es 22.8 %» y «Punto de equilibrio con el costo de hoy: 45.5 %»; US «Hoy tu meta es 23.2 %» y «29.0 %». Sin la línea roja de US: 25 % ya no pasa su equilibrio.
   - «Temporada alta», primera viñeta: el 23.81 % es «por ejemplo, un equilibrio de 23.81 %», y dice que con los datos del 2026-11-12 no pasa en ninguna plataforma.
   - Comentarios de `meta_del_juicio` y `equilibrio_del_juicio`: lo que decía «hoy» con 19.05, 23.81 y 40.26 ahora es «ejemplo», con las cifras sin duplicado al lado.
   - «En Ajustes», en «Punto de equilibrio con el costo vigente»: una frase antes de las citas dice que el 23.8 % es un ejemplo, no el equilibrio de hoy.
   - «Salida de la alta»: «el equilibrio de hoy (~40 %)» pasó a «un equilibrio de ~40 %», con el 45.5 % de hoy al lado. En «Ejemplo de una keyword durante un año», el 20.08 % dice que es la meta medida antes de D y que el ejemplo la conserva.
   - Las pruebas de TEMPORADA (`test_temporada01_d_meta_alta` con 19.05 y 23.81) usan números sintéticos y siguen valiendo como caso de prueba. No cambian.

---

## A. Base del plan

**Regla vieja de 10 días (decisión 1 del dueño).** `docs/CONTEXTO.md:84-89` describe la anti-inversión D.2 («N = 10», `inversion_sin_evidencia`), que BIDS 02 borró del código. La regla vigente está en `CONTEXTO.md:148-149` («invertirla pide 20 clics o 14 dias») y en `politica.py:35-36`. Se borran solo las frases de D.2, desde «D.2 (anti-inversión» hasta «(`inversion_sin_evidencia`).», y se conserva el resto del párrafo de ADS PROTECCION 01 («El corte economico de hojas…» empieza en la misma línea 89). Las pruebas F2 de la revisión (`test_f2_inversion_antes_de_10_dias_caso_real`) prueban la regla que se borra: no se promueven.

**Migraciones del plan.** `tools/despliegue/comun.sh:202` filtra las migraciones por etiqueta: `_(auto03|orbit07|claridad01|arreglos01)_`. Sin `arreglos02`, el despliegue no aplicaría la de D y no avisaría. Se suma la etiqueta, igual que hizo la fila A.2 de ARREGLOS 01, con su prueba de instalación. Los textos de `comun.sh:196-197` y `desplegar.sh:4,50,183` que listan las etiquetas se actualizan. TEMPORADA I.1 suma después `temporada01` en la misma línea: conviven.

---

## Decisión 5 del dueño: borrar los modos que producción no usa

Pregunta e. **Recomendación: no va en este plan. Va en un plan propio, después de TEMPORADA 01.** No se escribe bloque E.

Lo que borraría (`ciclo.md`, movimiento 3) y lo que depende de cada parte, medido:

| Camino | Valor en producción (`config_version`, 2026-11-12) | Qué depende de él |
|---|---|---|
| Política de bids apagada (`politica is None`, `cycle.py:2057-2120`, `_pendiente_pause_apagada` `:1099`, `MOTIVO_POLITICA_APAGADA` `:302`) | `niveles_v3` en MX y US | Es el valor por omisión de las bases de prueba de 10 archivos (`tests/test_cycle.py`, `test_cycle_apply.py`, `test_cycle_target_ciclo.py`, `test_optimizer_goals.py`, `test_orbit07_m5m6.py`, `test_config_write.py`, `test_goals_write.py`, `test_apply_producto.py`, `test_preflight_1_4.py`, `test_pantalla_ruido.py`). Borrarlo es reescribir esas bases. |
| Meta por producto en práctica (`_anota_sombra` `:2478`, `_juicio_hoja` `:2290`) | `live` en MX y US | TEMPORADA migra `_procesa_decisora` y `_juicio_hoja` a `casos()` y agrega `_anota_sombra_temporada` y `_anota_sombra_inerte` junto a `_anota_sombra` (`DISENO.md:316`, `:332`, `:1149`). |
| Economía `auto03` (`_margen_familia_de_hoja`, `apply_cola`) | `producto_directo_v1` en MX y US | V usa `ads_producto_economia_<plat> = auto03` como su forma de deshacerse (`ads-decision/opus/DISENO.md:548`). Borrarla antes de que V corra en producción le quita la reversa. |
| `ads_pause_sin_cooldown_bid` | `true` | Nadie lo lee fuera de su prueba (`goals.py:483-496`, `tests/test_optimizer_goals.py:327-333`). Código muerto. |

Lo que no se toca aunque suene parecido. `ads_producto_pausa_*` está en `shadow` en producción y W trabaja sobre esa práctica. `ads_bid_economia_*` es el interruptor que U usa para encender `economia_cambio_v2`.

Por qué después de TEMPORADA y no antes. Este plan es chico y mueve dinero hoy; el borrado no mueve dinero y es una refactorización de 350 a 450 líneas de `cycle.py` más 10 bases de prueba. Si va antes, TEMPORADA corre sobre un `cycle.py` distinto del que citó su diseño y V pierde su reversa a la semana de entrar. Después de TEMPORADA el borrado incluye también la práctica de temporada si para entonces ya está en vivo. El único pedazo sin dependencias es `ads_pause_sin_cooldown_bid`, y no vale un bloque solo.

---

## Lo que queda abierto

- El margen con el costo de hoy, sin el duplicado, no se midió con una vista. El lector adversario lo calculó a mano el 2026-11-12: 46.37 % en MX y 29.25 % en US. Lo deja medido TEMPORADA I.2 sobre las vistas de D.
- Los `Mantener` que C volvería movimiento solo aparecen después de un ajuste de campaña aprobado, y hoy no hay ninguno. Se verán en vivo, en los conteos de `notes.skips` del ciclo siguiente a un ajuste.
- C1 (sección «C1») no entra: queda como pregunta de regla para el dueño.
- El IVA de México dentro del margen espera al contador (sección «D», «Pregunta abierta para el contador»).
- `fabrica_bids.py:163,191` y `fabrica_plan.py:341` redondean bids de la fábrica con `ROUND_HALF_EVEN` por su cuenta. No son decisiones del motor y no entran en este plan.
