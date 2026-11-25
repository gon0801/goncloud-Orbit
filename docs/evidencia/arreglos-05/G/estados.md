# Modelo del bloque G: un termino reobservado no revierte todo el lote

Bloque G de ARREGLOS 05, fila G.1. Contrato: `docs/evidencia/arreglos-05/DISENO.md`,
seccion "F-G. Meta base y sello temporal" (parte G) e "Integracion con TEMPORADA
y migraciones". Base del bloque:
`2825fb036be82d118acfeb4e53e07e490cdd4557` (master con BASE, A, B, C, D, E y F
integrados y desplegados).

Este documento se escribe ANTES del primer commit de codigo. Fija el modelo por
camino y el recorrido exacto del sello bitemporal. El detalle de la
implementacion y la evidencia de cada fila viven en `G/resultado.md`.

## Premisa atacada

El sello bitemporal existe y es uno solo: `_sello_bitemporal` en
`app/cycle_serializacion.py`. En `_pendiente_termino` se aplicaba SOLO para la
rama `negative`, porque vivia dentro de `if corte is not None`. La rama
`harvest` no lleva corte (`corte=None`, `evidencia=None`), asi que copiaba el
`resultado.data_observed_at` crudo del motor de higiene. Ese valor es
`t.observed_at_max` del termino (`app/optimizer/hygiene.py`, rama harvest).

La premisa era "el sello del lote se aplica cuando hay corte". Falla en cuanto un
termino maduro del lote se reobserva con un `observed_at` posterior al reloj de
la decision: el `data_observed_at` crudo del harvest queda en el futuro,
`decision_dato_no_del_futuro` (`CHECK data_observed_at <= decided_at`) rechaza la
fila, y como las decisiones se insertan juntas con `executemany` en la TX3, el
`CheckViolation` revierte TODO el lote: tambien los `pause`, `negative` y `bid`
que ya eran validos.

Es el mismo patron que las rondas A-E: una condicion previa suelta decide, en vez
del sello comun. La foto de produccion lo confirma como riesgo real y latente:
hay lotes mixtos y hay reobservaciones (abajo).

## Foto de produccion (solo lectura)

Comando: `bash docs/evidencia/arreglos-05/G/lee_previa.sh > G/previa.txt` (rol
`orbit_read`, transaccion `read_only`, por `docs/evidencia/arreglos-01/lee_prod.sh`;
2026-11-25T03:18Z, SHA del padre). Lo medido:

- Lotes: **179** live+done, **30** shadow+done y **2** off+skipped.
- **21 lotes mixtos** (traen a la vez decisiones de termino y de entidad); 23
  lotes con termino, 141 con entidad, 143 con decisiones.
- Decisiones: **2788** bid, **69** pause, **54** negative, **16** harvest.
- **70 decisiones** con al menos una observacion del mismo termino entrando
  despues de `decided_at` (**45244 pares** observacion posterior). Hoy todas son
  `negative`, protegidas por el sello de su rama; el `harvest` no lo estaba.
- `harvest`: 16 en total, **0** con insumo posterior y **0** con sello en el
  futuro. El defecto es latente: la primera reobservacion de un termino maduro en
  un lote harvest sin corte abortaria ese lote, y con el los demas.
- `decision_dato_no_del_futuro`: **0** violaciones historicas; no hay backfill que
  inventar ni filas que corregir.

Consecuencias: el invariante existe en los datos (hay reobservaciones y lotes
mixtos) y no hay deuda historica; no se reescribe ninguna fila.

## Decision de sintesis (dos candidatos)

Se compararon dos formas antes de codear:

- **Candidato 1, rama nueva para harvest.** Envolver el harvest en su propio
  `if resultado.kind == "harvest": data_observed = min(decided_at, data_observed)`.
  Descartado por `Attack the Premise`: deja dos caminos de sello (uno por kind) y
  el proximo kind de termino repetiria el hueco; es una condicion previa suelta
  mas.
- **Candidato 2, sello comun e incondicional (elegido).** El sello
  `_sello_bitemporal(decided_at, data_observed, evidencia)` se aplica siempre,
  fuera del `if corte is not None`. Para `negative` el resultado es identico
  (evidencia no nula, mismo max y mismo clamp). Para `harvest` (evidencia nula) el
  sello queda en `min(decided_at, t.observed_at_max)`. Un solo camino, un solo
  dueno.

Se eligio el candidato 2 porque encoda el dominio en el dato: el sello del lote es
una propiedad de toda decision, no de la rama con corte. No crea API ni fachada
paralela; reusa el sello existente.

## Tipos y firmas (sin cambios de interfaz)

`_sello_bitemporal(decided_at, obs_directo, evidencia)` no cambia de firma ni de
semantica: `min(decided_at, max(obs_directo, evidencia.observed_at_max))` con
`evidencia=None` para harvest. `_Pendiente` no cambia. `_pendiente_termino` no
cambia de firma; solo se mueve la linea del sello fuera del `if corte is not None`.

`app/apply_estados.py` NO cambia. G no envia nada a Amazon ni agrega una
transicion: el sello de `decision.data_observed_at` es un campo de la fila que
inserta la TX3, no una accion aplicada. La tabla de A queda igual y su chequeo
estructural sigue prohibiendo duenos paralelos.

## Recorrido del sello

Para toda decision de termino (`negative` o `harvest`) en `_pendiente_termino`:

```
obs_directo = resultado.data_observed_at          # harvest: t.observed_at_max
evidencia   = evidencia (solo negative) | None    # harvest: None
base        = max(obs_directo, evidencia.observed_at_max)   # si evidencia
sello       = min(decided_at, base)                # LEAST clampea al reloj
decision.data_observed_at = sello
inputs["termino"]["observed_at_max"] = t.observed_at_max   # SIN tocar
window_start/window_end = resultado.window_start/window_end  # SIN tocar
```

- El insumo real (`inputs.termino.observed_at_max`) conserva la fecha original,
  aunque sea posterior al reloj de la decision.
- Las metricas siguen append-only en `search_term_observation` por
  `(entidad, search_term, metric_date, observed_at)`; el sello no las toca.
- La ventana de madurez y el trigger `decision_madurez_corte` no cambian: un dia
  inmaduro sigue fuera y el dia en curso sigue descartado.

## Caminos (estado, evento, accion de la tabla que decide)

`app/apply_estados.py` NO cambia; la "tabla" de G es el sello comun de arriba y
la fila `decision` que escribe la TX3. Camino por camino:

| # | Camino | Estado / evento | Accion (sello, fila, TX3) |
|---|---|---|---|
| 1 | Harvest sin corte en un lote mixto con un termino maduro reobservado | lote en `running`, termino con `observed_at_max > decided_at` | El lote se confirma `done`: el harvest y las decisiones validas (`pause`, `negative`, `bid`) se insertan; el sello del harvest clampea a `decided_at` y no viola el CHECK |
| 2 | El mismo lote con el sello nuevo | `decision.data_observed_at` para cada fila | `data_observed_at = min(decided_at, ...)` en cada decision (`<= decided_at`); `inputs.termino.observed_at_max` queda intacto con la fecha posterior; metricas sin reescribir |
| 3 | Termino inmaduro o dia en curso | higiene no emite kind (`motivo`), o el trigger de madurez | Sigue fuera del lote; no se rejuvenece el dato ni se mueve la ventana; el trigger no se relaja |
| 4 | Replay del lote | `replay.reproduce(inputs)` de cada decision | Mismo `(kind, old_value, new_value)` que la corrida original; el insumo congelado alimenta el replay sin diferencias |
| 5 | Caida entre el sello y el commit | TX3 atomica: insercion de decisiones + `decision_committed_at` + cierre `done` | No deja lote a medias ni sello sin decision: o se comprometen decisiones y sello juntos, o nada; no hay fila de sello separada |
| 6 | Correccion de un dato | backfill append-only de `search_term_observation` | Fila nueva con `observed_at` posterior; la original permanece; la decision anterior no se reescribe; la siguiente corrida decide con el dato nuevo |

Antes de cada empuje se recorren los seis caminos a mano sobre diff y pruebas.

## Limites

- G no crea migracion: el sello es codigo; `decision.data_observed_at` y el CHECK
  ya existen en `0001_initial.sql` (no se tocan).
- G no relaja `decision_madurez_corte`, no cambia ventanas ni madurez, no excluye
  dias inmaduros para pasar, no rejuvenece datos y no hace backfill.
- H (tope 150), I (Ajustes), J (retiro de modos) y K (fees) no se implementan
  aqui. G no enciende precios ni pausa por producto.
