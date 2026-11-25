# Modelo del bloque F: la meta base avanza sin confundirse con temporada

Bloque F de ARREGLOS 05. Contrato: `docs/evidencia/arreglos-05/DISENO.md`,
seccion "F-G. Meta base y sello temporal" (parte F) e "Integracion con
TEMPORADA y migraciones". Base del bloque:
`8a094228cd9704b35195000b83e37157527f887c` (master con BASE, A, B, C, D y E
integrados y desplegados).

Este documento se escribe ANTES del primer commit de codigo. Fija el modelo por
camino y el origen exacto de la base y del overlay. El detalle de la
implementacion y la evidencia de cada fila viven en `F/resultado.md`.

## Premisa atacada

El codigo anterior resolvia el ancla de la meta base asi, en
`app/cycle_target.py::_resuelve_target_ciclo`:

```
ultimo = primer snapshot de un ciclo live+done, de los ultimos 200, cuya
         notes.target.procedencia == 'margen_plataforma'
         y notes.target.target_aplicado != null
si no hay: ultimo = setting
```

La premisa era "la base se reconoce por la procedencia de la medicion". Esa
premisa falla en cuanto el ledger envejece: con `ledger_rancio` el resolver se
abstiene, `procedencia` queda en `null`, y el ciclo rancio no puede ser el ancla
del siguiente. La cadena se rompe y cada ciclo vuelve al ultimo snapshot sano,
que es el mismo para todos: la meta se congela (29, 29, 29), en vez de caminar
hacia el destino (29, 28.5, 28).

La foto de produccion (`F/previa.txt`, 2026-11-25T00:36Z, solo lectura con
`lee_prod.sh`) confirma la forma del dato:

- 179 ciclos live+done, 30 shadow+done y 2 off+skipped.
- 175 snapshots con `notes.target`; **los 175** traen
  `procedencia = margen_plataforma` y `target_aplicado` no nulo.
- `notes.target.version` no existe en ninguno (0 snapshots versionados).
- Solo **2** ciclos live tienen `decision_committed_at` (el sello del bloque A
  desplegado hace poco): el mas reciente es el 210, base
  `23.87387092262181318210778656`.
- Temporada esta en `live` en MX y US; el mes en curso (noviembre) es de
  `alta` y no hay `ads_temporada_meta_alta_*` configurada, asi que el overlay
  de alta vale 25 % frente a una base de 23.87 %.

Es decir: hoy la base vive por debajo del overlay de alta y el primer ciclo con
ledger rancio la congelaria. No hay backfill que inventar: el sello del bloque A
ya distingue los ciclos nuevos, y los historicos conservan su semantica vieja.

## Decision de sintesis (dos candidatos)

Se compararon dos formas antes de codear:

- **Candidato 1, parche al predicado.** Aceptar como ancla un snapshot con
  `procedencia = null` cuando `convergiendo_a_setting` sea cierto. Descartado:
  sigue infiriendo la base desde un campo que describe la medicion, no la base;
  deja fuera el ciclo cuyo motivo es `sin_fraccion` o `ventana_corta`, y vuelve
  a congelar la meta en el siguiente motivo distinto. Es el mismo parche que
  las rondas A-D ya demostraron fragil.
- **Candidato 2, versionar el snapshot (elegido).** El snapshot declara
  explicitamente su `base` (valor convergente y de donde salio) y su `overlay`
  (meta de alta estacional y fase) por separado, bajo `version = target_v2`. El
  ancla se lee del ultimo ciclo live **con decision comprometida**
  (`decision_committed_at IS NOT NULL`), no de la procedencia. Un snapshot viejo
  (sin `version`) conserva su semantica original y nunca se reconstruye.

Se eligio el candidato 2 porque encoda el dominio en los datos: la base es un
hecho durable escrito en la misma TX3 que las decisiones, la procedencia de la
medicion deja de decidir, y la ausencia de base es un `null` explicito, no una
heuristica.

## Tipos y firmas (boceto implementado)

Todo vive en `app/cycle_target.py` (dueno de la resolucion del target de ciclo,
345 lineas de holgura bajo el presupuesto de 900). `app/cycle.py` no crece: su
techo del ratchet es 1148 y ya esta en 1148.

```python
VERSION_SNAPSHOT = "target_v2"          # version del objeto notes.target
VERSION_OVERLAY = "temporada_v1"        # version del subobjeto overlay

@dataclass(frozen=True)
class _OverlayTemporada:
    """Overlay estacional congelado del ciclo: configuracion de TEMPORADA leida
    una vez. TEMPORADA sigue siendo el dueno de sus umbrales y prioridad."""
    modo: str | None            # 'shadow' | 'live' | None (sin configurar)
    config: temporada.ConfigTemporada | None
    estacion: temporada.Estacion | None
    meta_alta_pct: Decimal | None

@dataclass(frozen=True)
class _BasePrevia:
    """Base convergente que deja un ciclo live anterior y su evidencia."""
    valor: Decimal | None       # None = ausencia explicita
    origen: str                 # 'ciclo:<id>' | 'setting' | 'ausencia'
    cycle_id: int | None

def _base_de_snapshot(target: dict) -> tuple[bool, Decimal | None]:
    """(decide, valor). Lee SOLO la base explicita del snapshot; decide=False
    cuando el snapshot no aporta evidencia sobre la base."""

def _base_previa(filas, setting_target: Decimal | None) -> _BasePrevia:
    """Recorre los snapshots de ciclos live con decision comprometida, del mas
    reciente al mas viejo, y devuelve la base con su origen."""

def _overlay_temporada(settings, platform, decided_at) -> _OverlayTemporada:
    """Configuracion estacional del ciclo (meta de alta, fase y estacion)."""

def _resuelve_target_ciclo(conn, *, platform, settings, setting_target,
                           cycle_id, decided_at) -> _TargetCiclo:
    """Sin cambio de firma: lee base previa + overlay y resuelve UNA vez."""
```

`_TargetCiclo` suma `overlay` para que `_recorre_plataforma` construya
`plat_t` con los mismos numeros (una lectura por ciclo, regla 2) y no vuelva a
resolver la configuracion estacional.

## Origen exacto de la base y del overlay

- **Base.** `res.aplicado` de `goals.resuelve_target_margen` en ESTE ciclo: el
  valor que entra a la cascada como `target_margen`. En un ciclo con ledger
  rancio es el paso de convergencia (`ultimo - 0.5` hacia el setting). Su ancla
  (`ultimo`) sale de `_base_previa`, que solo considera ciclos
  `motor = 'ads_optimizer' AND mode = 'live' AND decision_committed_at IS NOT
  NULL AND id < cycle_id`; cada fila decide una sola vez:
  - `notes.target.version = target_v2` con `base` dict: base = `base.valor`
    (puede ser `null`, ausencia explicita, y entonces se deja de buscar);
  - snapshot viejo (sin `version`) con `procedencia = margen_plataforma` y
    `target_aplicado` no nulo: base = `target_aplicado` (semantica original,
    byte a byte la del codigo anterior);
  - cualquier otra cosa (sin `target`, sin procedencia, `target_aplicado`
    nulo): no aporta evidencia y se sigue con el anterior, igual que antes.
  Si ninguna fila decide, la base es el `setting` (inicializacion ya aprobada).
- **Overlay.** Solo la configuracion estacional: `temporada.desde_settings`,
  `temporada.modo_desde_settings`, `temporada.resuelve(config, dia)` y
  `temporada.meta_alta_desde_settings`. No aporta ningun numero a la base. La
  meta efectiva de cada hoja sigue derivandose dentro de `decide`
  (`temporada.meta_del_juicio = max(base, meta_alta)` en alta, sin metas
  manuales), y el caso ya la congela con `meta_alta_pct` y la frontera
  (TEMPORADA 01 D); F no la recalcula ni la duplica.
- **Efectiva por hoja.** No se agrega un numero nuevo: la decision congela
  `target_acos_pct_usado` (la base del juicio) y `caso.temporada.meta_alta_pct`
  (el overlay). La efectiva se deriva de esos dos hechos con la misma regla del
  dueno, en el mismo lugar (`policy`/`decide`), no en el snapshot de ciclo.

## Caminos (estado, evento, accion de la tabla que decide, base y overlay)

`app/apply_estados.py` NO cambia. F no envia nada a Amazon ni agrega una
transicion: resolver la meta base es una lectura de TX2 y un hecho durable de
TX3, no un cambio aplicado. La tabla sigue en 176 casillas (11 x 16) y el
chequeo estructural de A sigue prohibiendo duenos paralelos. Lo que F modela es
la resolucion de la meta, y su "tabla" es la de `_base_de_snapshot` y
`_base_previa` de arriba.

| # | Camino | Evidencia durable que decide | Base resultante | Overlay | Snapshot y freeze |
|---|---|---|---|---|---|
| 1 | Ciclo normal con ledger fresco | ultimo ciclo live con `decision_committed_at`; su `notes.target.base.valor` (v2) o su `procedencia`+`target_aplicado` (v1) | `res.aplicado` del peldano margen_plataforma, con procedencia `margen_plataforma` | `overlay` de la config vigente (modo, fase, estacion, `meta_alta_pct`) | `notes.target.version = target_v2` con `base` y `overlay`; `target_aplicado` sigue siendo la base para los lectores actuales |
| 2 | Ledger rancio con overlay de alta 25 | la base del ciclo anterior aunque su `procedencia` sea `null`: el ciclo rancio sella `base.valor` en su TX3 | 29 -> 28.5 -> 28: cada ciclo converge 0.5 hacia el setting desde la base del anterior; el 25 de alta NO entra | fase `alta`, `meta_alta_pct = 25`, `aplica = true` | `base.valor` = 29, 28.5, 28; `overlay.meta_alta_pct = 25` separado; `target_aplicado` = base |
| 3 | Practica (temporada/global shadow) en medio | solo ciclos `mode = 'live'` son ancla; un ciclo shadow no aporta (misma regla que antes) | no cambia por la practica; el siguiente live ancla al ultimo live comprometido | la practica registra su `overlay` en su propio snapshot, sin tocar el ancla live | `base` del ciclo live intacta; `overlay.modo = 'shadow'` en el ciclo de practica |
| 4 | Snapshot viejo sin base inequivoca | v1 con `procedencia = null` o `target_aplicado = null` no decide; se conserva la semantica vieja (seguir buscando) y la ausencia | la del ultimo snapshot v1 inequivoca; si no hay, `setting` (inicializacion ya aprobada) | el del ciclo actual | sin reconstruir por resta ni `efectivo - overlay`; `base.origen = 'setting'` o `'ciclo:<id>'`, nunca un numero inferido |
| 5 | Replay de una decision antigua | `decision.inputs` congelados (caso, `target_snapshot`, `target_acos_pct_usado`) y su `version` | la frozen de su fecha; el replay no consulta settings ni la base de hoy | `caso.temporada.meta_alta_pct` y `frontera` congelados | cero diferencias no previstas; filas sin la clave rejuegan con su era (`replay.py`) |
| 6 | Recuperacion parcial (`resume_of`) | el origen live con `decision_committed_at` sigue siendo el ancla; el ejecutor cierra con `_SQL_CERRAR_ENVELOPE` (sin sello) y no decide | el lote nuevo ancla a la base del origen; el ejecutor no duplica decisiones ni abre otra base | el overlay lo fija el origen que decidio; el ejecutor no lo reescribe | `resume_of = origen`, `decisions_count = 0`, `base` del origen intacta |

Antes de cada empuje se recorren los seis caminos a mano sobre diffs y pruebas.

## Versionado e historia

- `notes.target.version = "target_v2"`: objeto nuevo, aditivo. `target_aplicado`,
  `procedencia`, `motivo_abstencion`, `convergiendo_a_setting`, `setting` y las
  claves de medicion se conservan tal cual para los lectores vigentes
  (`api_common._target_vigente`, `cycle_notifica`, `apply_revalidacion`,
  `familias`, dashboard y el test externo `test_12`).
- `notes.target.base = {valor, origen, cycle_id, version}`: la base convergente
  y de donde salio. `valor = null` es ausencia explicita.
- `notes.target.overlay = {version, modo, fase, estacion_dia, meta_alta_pct, aplica}`:
  el overlay estacional separado. `modo = null` significa Temporada sin
  configurar.
- Los 175 snapshots historicos conservan su JSON: no se reescribe `notes` ni se
  hace backfill. Un snapshot v1 se lee con su semantica original.
- Replay: sin cambios de codigo en `app/optimizer/replay.py`; la prueba F.2
  demuestra que consume su version y no la configuracion de hoy.
- Sin DDL: el versionado vive en el JSONB `optimizer_cycle.notes`, que ya
  existe. No se crea migracion en F (se dira en el PR). No se toca 0001 ni 0002.

## Limites

- F no enciende precios ni pausa por producto, no cambia umbrales ni la
  prioridad de TEMPORADA, y no toca `apply_queue` (bloque E).
- G (sello temporal de harvest), H (tope 150), I (Ajustes) y J (retiro de
  modos) no se implementan aqui.
- La meta efectiva de una hoja con meta manual (`goal_campana` /
  `goal_plataforma`) sigue sin recibir el overlay: lo decide
  `temporada.meta_del_juicio`, no F.
