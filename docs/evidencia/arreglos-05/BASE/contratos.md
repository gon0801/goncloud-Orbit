# BASE.0 — Contratos vigentes y firmas reales

Bloque `BASE` de `plans/arreglos-05.md`, fila `BASE.0`. Fecha: 2026-11-22 (America/Vancouver).

## SHA y precondiciones

- Rama: `arreglos-05/base`, creada desde `master` en `33844bb365d707e523fc7865dd56668d69047dc4` (merge de #744).
- `origin/master` tras `git fetch origin`: `33844bb365d707e523fc7865dd56668d69047dc4`. Al abrir el bloque era igual a `HEAD`.
- SHA del arbol cotejado: `33844bb3`. El commit `2639ff8fd7ce51b23926733d1faf4b9372222dbe` solo porta fixtures de `docs/evidencia/arreglos-05/revision/`; `app/`, `tools/`, `migrations/` y `tests/` son iguales en ambos. `firmas.py` da la misma salida leida desde `33844bb3` con git y desde el arbol.
- Comando de la columna `Acepta`, salida `0` (registro en [acepta.txt](acepta.txt)):

```bash
git merge-base --is-ancestor origin/master HEAD && (for f in plans/arreglos-05.md docs/evidencia/arreglos-05/{DISENO.md,COBERTURA.md,ACEPTACION.md,comprobar.sh}; do git cat-file -e "origin/master:$f" || exit 1; done)
```

Los cinco archivos existen en `origin/master` y su blob es igual al de `HEAD`: `plans/arreglos-05.md` `7a550b8c`, `DISENO.md` `09671726`, `COBERTURA.md` `202a86da`, `ACEPTACION.md` `6fb63b0e`, `comprobar.sh` `afed430f`.

## Evidencia de cierre que ya esta en master

| Plan | Evidencia en `origin/master` |
|---|---|
| ARREGLOS 03 | `docs/evidencia/arreglos-03/` con acta en sus 19 bloques (`D-A` a `D-N`, `P-A` a `P-G`) y su `BASE/{rebase,roja}.md`. `plans/ORDEN-IMPLEMENTACION.md` registra el despliegue verificado en `819fe1ff`. BASE.2 anade la prueba vigente: las sondas de sus cinco migraciones (0087 a 0091) dan `True` en produccion ([previa.txt](previa.txt)). |
| ARREGLOS 04 | `docs/evidencia/arreglos-04/CIERRE/` con `resultados.md`, `harness.json`, `tn02-writes.log` y `tn04-15-16-ui.log`. SHA integrado y desplegado `f5dc86d5b6cbb8f6ecaafb5c016a99f2d8c13683` (merge de #680, 2026-11-19), con los seis casos TN y el estado de los 21 bloques. |
| TEMPORADA 01 | `docs/evidencia/temporada-01/H/` (`despliegue.txt`, `checklist.txt`, `prueba-real.txt`, `practica.md`, `transiciones.txt`) y `OLA1/`, `OLA2/`, `OLA3/` (`checklist.txt`, `despliegue.txt`, `pruebas-reales.txt`). OLA 3 desplegada el 2026-11-23 02:45 UTC sobre el merge de #741 `f07ab667`; su evidencia entro por #743 (`f7a923a9`) y la de OLA 2 por #742 (`612fc037`). Las sondas de 0092 y de las dos 0093 dan `True` en produccion. |

## Cotejo de las firmas de TEMPORADA que nombra DISENO

Lineas tomadas del AST en `33844bb3` con [firmas.py](firmas.py). La salida completa esta al final.

| Simbolo | Donde | Lo que afirma DISENO | Cotejo |
|---|---|---|---|
| `temporada_io` | `app/temporada_io.py` | Episodio, efecto, fin y exclusion. Unico registro estacional. Los efectos se cierran en `_confirma_resumen` y `registra_sin_aplicar`. | Coincide. `registra_ciclo` (294) es el unico escritor de episodios y corre en TX3 antes de insertar decisiones. `abre_efectos` (411) abre una fila por decision entra, ajusta o sale. Los cierres estan en `app/apply_store.py:318` y `app/apply_cuota.py:119`. |
| `OrigenCambio` | `app/optimizer/caso.py:136-142` | Origen de un cambio de bid con `temporada` entre sus valores. | Coincide. Es `Literal['motor', 'regreso_por_desplome', 'regreso_del_dueno', 'ajuste_de_campana', 'temporada']`. |
| `HistoriaBid` | `app/optimizer/caso.py:202-211` | Historia de cambios de la hoja con instantes, fines y manos externas. | Coincide. La leen `recuperacion.cadena_bid` y la temporada. |
| `recuperacion.cadena_bid` | `app/optimizer/recuperacion.py:182-185` | La reconciliacion de bids sigue siendo solo lectura. | Coincide. `cadena_bid(caso: CasoHoja) -> CadenaBid | None` no recibe conexion ni cliente. |
| `Aplicador.aplica_bids` | `app/apply.py:317-460` | TEMP E aporta la lectura previa al PUT y su valor real congelado. El cupo se cuenta desde el ledger. | Parcial. La lectura previa existe (363-395), antes de la cuota y del PUT. El valor leido no queda en `apply_attempt`. Ver diferencias D-2 y D-3. |
| `_confirma_resumen` | `app/apply_store.py:304-320` | Confirmacion y efecto de temporada siguen atomicos. | Coincide. UPSERT del resumen, sello del ejecutor y cierre del efecto en la misma transaccion. |
| `registra_sin_aplicar` | `app/apply_cuota.py:95-119` | Desenlace no aplicado del ciclo ejecutor. | Coincide. Idempotente por `(decision_id, cycle_id)`. `cycle_id` es el ejecutor. Cierra el efecto en la misma transaccion. |
| `aplica_reactivaciones` | `app/apply_cola_ejecuta.py:324-397` | Recuperar no repite entrada ni regreso. | Coincide con la fila F.2 de TEMPORADA. Guardas, cuota `pause` y `reversa_pause(..., durable=True)`. |
| `proxima_config` | `app/config_write.py:543-696` | Validacion y version optimista existentes. Se elimina `NaN → null → Guardado`. | Coincide con el defecto descrito, sin delta de firma. Con `cap is None` el bucle salta la cuota (658-666) y el valor se serializa con `str(cap)`. Trae `temporada`, `temporada_modo`, `meta_alta`, `recuperacion_modo` y `recuperacion_memoria_dias`, que el bloque J conserva. |
| `_sello_bitemporal` | `app/cycle_serializacion.py:109-122` | G lo aplica tambien a harvest sin corte. | Coincide. Hoy se aplica en `pendiente_bid` (249), `_pendiente_pause_apagada` (308) y en `_pendiente_termino` solo cuando hay corte (365-369). |

## Lo que DISENO supone y el codigo integrado no trae

- **La conciliacion del ledger no corre antes de TX2.** `apply_harvest.reconcilia_harvest` y `apply.reconcilia_bids` se llaman en `app/cycle_apply.py:240` y `:243`, dentro de `_fase_apply`. `_corre_fases` la invoca en `app/cycle.py:631`, despues de `_fase_lecturas` (546) y de la transaccion de decisiones (575-629). Lo que TEMPORADA B.2 corre antes de decidir es `_reconcilia_efectos` (`app/temporada_io.py:282`), la de los efectos de temporada, y ocurre dentro de TX3. Es la diferencia D-1 de [diferencias.md](diferencias.md).
- **Las operaciones del boceto no existen.** `atender_plataforma`, `reponer_keyword` y `cotizacion_desde_evidencia` no aparecen en `app/`, `tools/`, `migrations/` ni `tests/`. Tampoco `keyword_reposicion_intento`, `decision_committed_at` ni `resume_of`. Son trabajo de A y C.

## Salida de `firmas.py`

````text
Firmas tomadas del AST en `33844bb3` (leido desde git).

### API publica de `app/temporada_io.py`

| Linea | Simbolo | Firma |
|---|---|---|
| 55 | `EstadoTemporada` | `class EstadoTemporada` |
| 69 | `TemporadaPlataforma` | `class TemporadaPlataforma` |
| 84 | `Reactivacion` | `class Reactivacion` |
| 169 | `lee_estado` | `def lee_estado(conn: psycopg.Connection, platform: str, hoy: dt.date) -> EstadoTemporada` |
| 192 | `episodio_vigente_en_estacion` | `def episodio_vigente_en_estacion(episodio: EpisodioVigente \| None, estacion: temporada.Estacion) -> EpisodioVigente \| None` |
| 294 | `registra_ciclo` | `def registra_ciclo(conn: psycopg.Connection, platform: str, *, cycle_id: int, decidido_el: dt.datetime, plataforma: TemporadaPlataforma \| None, config_version_id: int \| None) -> EpisodioVigente \| None` |
| 340 | `registra_fines` | `def registra_fines(conn: psycopg.Connection, platform: str, *, cycle_id: int, episodio_abierto: int \| None, fines: dict[int, FinReduccion], exclusiones: dict[int, Decimal], moneda: str \| None) -> None` |
| 390 | `estampa` | `def estampa(pendiente: Any, episodio_id: int \| None) -> Any` |
| 411 | `abre_efectos` | `def abre_efectos(conn: psycopg.Connection, episodio_id: int \| None, pendientes, ids) -> None` |
| 449 | `episodios` | `def episodios(conn: psycopg.Connection, platform: str) -> tuple[EpisodioPresupuesto, ...]` |
| 462 | `sql_pausas_de_baja` | `def sql_pausas_de_baja(*, select_cols: str, joins_extra: str='', where_extra: str='', order_by: str='') -> str` |
| 509 | `reactivaciones_pendientes` | `def reactivaciones_pendientes(conn: psycopg.Connection, platform: str, mantener: frozenset[int]) -> tuple[Reactivacion, ...]` |
| 523 | `cierra_efecto_resumen` | `def cierra_efecto_resumen(conn: psycopg.Connection, *, decision_id: int, verify_ok: bool, leido: Decimal \| None) -> None` |
| 553 | `excluye_mano_del_aplicador` | `def excluye_mano_del_aplicador(conn: psycopg.Connection, *, decision_id: int, leido: Decimal) -> None` |
| 575 | `cierra_efecto_sin_aplicar` | `def cierra_efecto_sin_aplicar(conn: psycopg.Connection, *, decision_id: int, motivo: str) -> None` |
| 596 | `ContextoRevalidacion` | `class ContextoRevalidacion` |
| 617 | `lee_contexto_revalidacion` | `def lee_contexto_revalidacion(conn: psycopg.Connection, platform: str, hoy: dt.date) -> ContextoRevalidacion \| None` |
| 640 | `mult_negativa` | `def mult_negativa(conn: psycopg.Connection, platform: str, hoy: dt.date) -> Decimal \| None` |
| 651 | `cerca_plataforma` | `def cerca_plataforma(conn: psycopg.Connection, platform: str, hoy: dt.date) -> dt.date \| None` |
| 661 | `frontera_hoja` | `def frontera_hoja(conn: psycopg.Connection, ad_entity_id: int, hoy: dt.date, *, piso: Decimal, techo: Decimal, contexto: ContextoRevalidacion \| None=None) -> FronteraHoja \| None` |

Constantes publicas: `MOTIVO_ENTRA` = `'entra_temporada_baja'`, `MOTIVO_AJUSTA` = `'ajusta_temporada_baja'`, `MOTIVO_SALE` = `'sale_temporada_baja'`, `MOTIVOS_TEMPORADA` = `(MOTIVO_ENTRA, MOTIVO_AJUSTA, MOTIVO_SALE)`, `_TIPO_POR_MOTIVO` = `{MOTIVO_ENTRA: 'entra', MOTIVO_AJUSTA: 'ajusta', MOTIVO_SALE: 'sale'}`, `_SQL_EPISODIO_ABIERTO` = `'\nSELECT id, abierto_el FROM temporada_episodio\n WHERE platform = %s::platform AND cerrado_el IS NULL\n'`, `_SQL_EFECTOS_ABIERTOS` = `"\nSELECT d.ad_entity_id,\n       CASE WHEN da.decision_id IS NOT NULL OR sa.decision_id IS NOT NULL THEN 'obsoleto'\n            WHEN EXISTS (SELECT 1 FROM apply_attempt a\n                          WHERE a.decision_id = e.decision_id\n                            AND a.tipo = 'normal'\n                            AND a.resultado = 'fallo:readback_ambiguo') THEN 'en_vuelo'\n            WHEN EXISTS (SELECT 1 FROM apply_attempt a\n                          WHERE a.decision_id = e.decision_id\n                            AND a.tipo = 'normal'\n                            AND a.finished_at IS NULL) THEN 'en_vuelo'\n            WHEN EXISTS (SELECT 1 FROM apply_attempt a\n                          WHERE a.decision_id = e.decision_id\n                            AND a.tipo = 'normal') THEN 'obsoleto'\n            WHEN oc.status IS DISTINCT FROM 'running' THEN 'obsoleto'\n            ELSE 'en_vuelo' END\n  FROM temporada_efecto e\n  JOIN decision d         ON d.id = e.decision_id\n  JOIN ad_entity k        ON k.id = d.ad_entity_id AND k.platform = %s::platform\n  JOIN optimizer_cycle oc ON oc.id = d.cycle_id\n  LEFT JOIN decision_application da ON da.decision_id = e.decision_id\n  LEFT JOIN decision_sin_aplicar sa ON sa.decision_id = e.decision_id\n WHERE e.cerrado_el IS NULL\n"`, `_SQL_EXCLUIDAS` = `'\nSELECT x.ad_entity_id, x.episodio_id\n  FROM temporada_exclusion x\n  JOIN temporada_episodio e ON e.id = x.episodio_id\n WHERE e.platform = %s::platform\n'`, `_SQL_CERCA_PLATAFORMA` = `"\nSELECT max(primera)\n  FROM (\n    SELECT min(c.confirmado_el) AS primera\n      FROM v_cambio_bid c\n      JOIN temporada_episodio e ON e.id = c.episodio_id AND e.platform = %s::platform\n     WHERE c.origen = 'temporada'\n       AND c.motivo_temporada IN ('entra', 'sale')\n     GROUP BY e.id, c.motivo_temporada\n  ) t\n"`, `_SQL_RECONCILIA_DESENLACE` = `"\nUPDATE temporada_efecto e SET cerrado_el = now(),\n       desenlace = CASE\n           WHEN da.decision_id IS NOT NULL THEN\n               CASE WHEN da.verify_ok IS TRUE THEN 'confirmado' ELSE 'divergencia' END\n           ELSE CASE WHEN sa.motivo = 'bid_cambiado_fuera' THEN 'mano_del_dueno'\n                     ELSE 'sin_aplicar' END END\n  FROM decision d\n  JOIN ad_entity k ON k.id = d.ad_entity_id AND k.platform = %s::platform\n  LEFT JOIN decision_application da ON da.decision_id = d.id\n  LEFT JOIN decision_sin_aplicar sa ON sa.decision_id = d.id\n WHERE e.decision_id = d.id AND e.cerrado_el IS NULL\n   AND (da.decision_id IS NOT NULL OR sa.decision_id IS NOT NULL)\n"`, `_SQL_RECONCILIA_ABANDONADO` = `"\nUPDATE temporada_efecto e SET cerrado_el = now(), desenlace = 'abandonado'\n  FROM decision d\n  JOIN ad_entity k        ON k.id = d.ad_entity_id AND k.platform = %s::platform\n  JOIN optimizer_cycle oc ON oc.id = d.cycle_id\n WHERE e.decision_id = d.id AND e.cerrado_el IS NULL\n   AND NOT EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = d.id)\n   AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa WHERE sa.decision_id = d.id)\n   AND NOT EXISTS (SELECT 1 FROM apply_attempt a WHERE a.decision_id = d.id)\n   AND oc.status IS DISTINCT FROM 'running'\n"`, `_SQL_RECONCILIA_SELLADO_SIN_DESENLACE` = `"\nUPDATE temporada_efecto e SET cerrado_el = now(), desenlace = 'sin_aplicar'\n  FROM decision d\n  JOIN ad_entity k ON k.id = d.ad_entity_id AND k.platform = %s::platform\n WHERE e.decision_id = d.id AND e.cerrado_el IS NULL\n   AND NOT EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = d.id)\n   AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa WHERE sa.decision_id = d.id)\n   AND EXISTS (SELECT 1 FROM apply_attempt a\n                WHERE a.decision_id = d.id AND a.tipo = 'normal' AND a.finished_at IS NOT NULL)\n   AND NOT EXISTS (SELECT 1 FROM apply_attempt a\n                    WHERE a.decision_id = d.id AND a.tipo = 'normal' AND a.finished_at IS NULL)\n   AND NOT EXISTS (SELECT 1 FROM apply_attempt a\n                    WHERE a.decision_id = d.id AND a.tipo = 'normal'\n                      AND a.resultado = 'fallo:readback_ambiguo')\n"`, `_SQL_INSERT_EPISODIO` = `'\nINSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo, config_version_id)\nVALUES (%s::platform, %s, %s, %s) RETURNING id\n'`, `_SQL_CIERRA_EPISODIO` = `'\nUPDATE temporada_episodio SET cerrado_el = %s, cerrado_en_ciclo = %s\n WHERE id = %s AND cerrado_el IS NULL\n'`, `_SQL_INSERT_FIN` = `'\nINSERT INTO temporada_fin_reduccion (episodio_id, ad_entity_id, motivo, cycle_id)\nVALUES (%s, %s, %s, %s)\nON CONFLICT (episodio_id, ad_entity_id) DO NOTHING\n'`, `_SQL_INSERT_EXCLUSION` = `'\nINSERT INTO temporada_exclusion (episodio_id, ad_entity_id, motivo, bid_visto,\n                                 bid_visto_moneda, decision_id)\nVALUES (%s, %s, %s, %s, %s, %s)\nON CONFLICT (episodio_id, ad_entity_id) DO NOTHING\n'`, `_SQL_EPISODIOS` = `'\nSELECT id, abierto_el, cerrado_el\n  FROM temporada_episodio\n WHERE platform = %s::platform\n ORDER BY cerrado_el IS NOT NULL, cerrado_el DESC NULLS FIRST, id DESC\n LIMIT 2\n'`, `_SQL_PAUSAS_DE_BAJA` = `sql_pausas_de_baja(select_cols='q.ad_entity_id, q.id, q.decision_id, e.id', joins_extra="  JOIN temporada_episodio e     ON e.id = (d.inputs->>'temporada_episodio_id')::bigint\n                               AND e.cerrado_el IS NOT NULL")`, `_SQL_BID_HOJA` = `'\nSELECT s.current_bid\n  FROM ad_entity k\n  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id\n WHERE k.id = %s\n'`.

### `OrigenCambio`

- Archivo: `app/optimizer/caso.py`, lineas 136-142.
- Lo consumen los bloques: A, B, H.

```python
OrigenCambio = Literal['motor', 'regreso_por_desplome', 'regreso_del_dueno', 'ajuste_de_campana', 'temporada']
```

### `HistoriaBid`

- Archivo: `app/optimizer/caso.py`, lineas 202-211.
- Lo consumen los bloques: A, B, F.

```python
@dataclass(frozen=True)
class HistoriaBid:
    desde: dt.date
    cambios: tuple[CambioFechado, ...]
    fines: frozenset[int] = frozenset()
    manos_externas: tuple[ManoExterna, ...] = ()
```

### `cadena_bid`

- Archivo: `app/optimizer/recuperacion.py`, lineas 182-185.
- Lo consumen los bloques: A, B.

```python
def cadena_bid(caso: CasoHoja) -> CadenaBid | None
```

### `Aplicador.aplica_bids`

- Archivo: `app/apply.py`, lineas 317-460.
- Lo consumen los bloques: A, B, H.

```python
def aplica_bids(self, decisiones: list[DecisionBid], *, escalera_global: str) -> ResultadoAplicador
```

### `_confirma_resumen`

- Archivo: `app/apply_store.py`, lineas 304-320.
- Lo consumen los bloques: A, B.

```python
def _confirma_resumen(conn: psycopg.Connection, decision_id: int, ack: dict, verify_ok: bool, cycle_id_ejecutor: int, *, leido: Decimal | None=None) -> None
```

### `registra_sin_aplicar`

- Archivo: `app/apply_cuota.py`, lineas 95-119.
- Lo consumen los bloques: A, H.

```python
def registra_sin_aplicar(conn: psycopg.Connection, decision_id: int, cycle_id: int, motivo: str, *, detalle: dict | None=None) -> None
```

### `aplica_reactivaciones`

- Archivo: `app/apply_cola_ejecuta.py`, lineas 324-397.
- Lo consumen los bloques: A, B, E.

```python
def aplica_reactivaciones(conn: psycopg.Connection, aplicador: Aplicador, decisiones: list[DecisionResume], *, tick=None) -> ResumenReactivacion
```

### `proxima_config`

- Archivo: `app/config_write.py`, lineas 543-696.
- Lo consumen los bloques: I, J.

```python
def proxima_config(actual: Mapping, platform: str, *, target_manual_pct: Decimal | None=None, margen_habilitado: bool | None=None, fraccion: Decimal | None=None, caps: Mapping[str, int | None] | None=None, ack_respaldo: bool=False, confianza_recorte: Decimal | None=None, confianza_subida: Decimal | None=None, motor_bid: str | None=None, precio_modo_global: str | None=None, precio_modo_universo: Mapping[str, str] | None=None, resumen_acos_objetivo: Mapping[str, Decimal | str | None] | None=None, producto_modo: Mapping[str, str] | None=None, limites_pausa: productos.LimitesPausa | None=None, proteccion_venta: str | None=None, economia_producto: str | None=None, economia_cambio: str | None=None, enciende_pausa: bool=False, temporada: Mapping | Literal['off'] | None=None, campana_topada: Mapping | None=None, temporada_modo: Mapping[str, str] | None=None, meta_alta: Mapping[str, str | int | Decimal] | None=None, recuperacion_modo: str | None=None, recuperacion_memoria_dias: int | float | str | None=None) -> tuple[dict, list[str]]
```

### `_sello_bitemporal`

- Archivo: `app/cycle_serializacion.py`, lineas 109-122.
- Lo consumen los bloques: G.

```python
def _sello_bitemporal(decided_at: dt.datetime, obs_directo: dt.datetime | None, evidencia: windows.EvidenciaAdGroup | None) -> dt.datetime | None
```

````
