# Diseño sintetizado: D.2 → D.1b → C.2a (sin implementar)

Base: origin/master `c6e3fc1`. Síntesis de tres diseños independientes (opus, fable y sonnet)
sobre un mismo grounding del código. Plan de pasos:
[`plans/ads-proteccion-01-fase-d.md`](../../../../plans/ads-proteccion-01-fase-d.md).

## Secuencia (los tres coinciden)

| Orden | Cambio | Migración | Por qué en este lugar |
| --- | --- | --- | --- |
| 1 | D.2 anti-inversión | ninguna | Única con fecha (prod antes del ~12-oct). Sin migración, así no pelea números. |
| 2 | D.1b choque/perdida/R-C3-1 | 0045 | Toma 0045. Arregla la cadena de migraciones de `tests/test_cycle.py` (hoy sin 0044). |
| 3 | C.2a freeze de target | 0046 | La que más tests toca (cadenas de 8 archivos). Aditiva. |

Tres PRs, sin fusionar. D.2 y C.2a comparten `_procesa_decisora` (`cycle.py:1539-1603`)
pero en hunks distintos: C.2a captura en el punto donde HOY se calcula el target
(`:1542-1545`, antes de `decide_bid` `:1550`), y D.2 agrega su `return` después del
cooldown B.2 (`:1573-1579`). Verificado: todos los `return` tempranos (veto `:1520`,
inerte `:1526`, B.2 `:1578`, no-op `:1582`) están después de las salidas por gate y
el freeze queda arriba de B.2/no-op/D.2.

## D.2 — gate de orquestador, no dentro de `decide_bid`

Base: sonnet (sum type), con la fecha UTC en Python de opus/fable y el
`policy_version` de opus/fable.

```python
# app/optimizer/goals.py, junto a en_cooldown / COOLDOWN
DIAS_EVIDENCIA_INVERSION = 10          # D.2/decision.md: literal del dueño "N = 10"
POLITICA_INVERSION = "inversion_n10_v1"

class SinHistoriaBid: ...                                  # D.2 no aplica
@dataclass(frozen=True)
class HistoriaBidRota: ...                                 # algún campo None o new==old → fail-closed
@dataclass(frozen=True)
class UltimoBidAplicado:
    direccion: Literal[-1, 1]                              # jamás 0
    fecha_cambio: dt.date                                  # confirmed_at convertido a UTC en Python
HistoriaUltimoBid = SinHistoriaBid | HistoriaBidRota | UltimoBidAplicado

_SQL_ULTIMO_BID_APLICADO = """  -- mismos filtros que _SQL_EN_COOLDOWN
SELECT d.old_value, d.new_value, da.confirmed_at
  FROM decision_application da
  JOIN decision d ON d.id = da.decision_id
  JOIN optimizer_cycle oc ON oc.id = da.applied_cycle_id AND oc.mode = 'live'
 WHERE d.ad_entity_id = %s AND d.kind = 'bid' AND da.verify_ok IS TRUE
 ORDER BY da.confirmed_at DESC LIMIT 1"""

def ultimo_bid_aplicado(conn, ad_entity_id: int) -> HistoriaUltimoBid:
    raise NotImplementedError   # única pieza impura; parcheable desde el harness B.2 (conn=object())

def permite_reversa_bid(historia: HistoriaUltimoBid, *, nueva_direccion: Literal[-1, 1],
                        fin_ventana_bids: dt.date | None) -> bool:
    raise NotImplementedError
    # SinHistoriaBid → True; HistoriaBidRota → False; misma dirección → True;
    # fin_ventana_bids None → False; si no: (fin_ventana_bids - fecha_cambio).days >= 10
```

En `cycle.py` (`_procesa_decisora`): después del bloque de cooldown B.2 y del no-op,
**solo si `resultado.kind == "bid"`**: `historia = g.ultimo_bid_aplicado(...)`; si
`not g.permite_reversa_bid(historia, nueva_direccion=signo(resultado), fin_ventana_bids=ventanas.bids.window_end)`
→ `contadores.skips_entidad[MOTIVO_INVERSION_SIN_EVIDENCIA] += 1; return`. Si pasa,
la decisión lleva `inputs.inversion_policy_version = POLITICA_INVERSION`.
`fin_ventana_bids` ya está a la mano (`cycle.py:1539`, por entidad). PAUSE, no-op y
cortes no llegan a la consulta. Sin flag: la regla solo quita decisiones y es
fail-closed.

Tests (rojo primero): caso 3835 (D+9 bloquea, D+10 emite); misma dirección pasa;
PAUSE y no-op sin consulta (spy); `SinHistoriaBid` pasa; `HistoriaBidRota` y
`fin_ventana_bids=None` bloquean; solo cuenta el último BID aplicado; `verify_ok`
NULL/FALSE no cuenta; ciclo shadow no cuenta como aplicado; `confirmed_at` cerca de
medianoche en otra TZ de sesión da la fecha UTC correcta; orden cooldown → inversión.
Mutantes: días de reloj, `>` en vez de `>=`, bloquear misma dirección, tocar PAUSE,
primer BID en vez del último, NULL como aplicado, None que deja pasar.

## D.1b — choque, perdida, docs, espejos, R-C3-1

Base: fable (helper de migración vigente), con el "solo envelope live" de opus/fable.

- **Choque**: en el `except UniqueViolation` de `encola_cortes` (`apply_cola.py:~561`),
  FUERA del savepoint por fila (ya existe, `:530-565`) y dentro de TX4, **solo si
  `modo_envelope == "live"`**: `registra_sin_aplicar(conn, dec_id, cycle_id,
  MOTIVO_CHOQUE_CLAVE, detalle={kind, entidad, termino})`. En shadow no se escribe
  (la vista solo mira ciclos live, y `tests/test_cycle.py` no aplica 0044).
- **perdida**: quitar el `_registra` de `apply_cola.py:1183-1185`; se queda en
  vocabulario y CHECK (puede haber filas). Invertir `tests/test_apply_harvest.py:2513-2557`
  (sin fila + cola `vetoed`).
- **0045**: localiza el CHECK anónimo de 0044 en `pg_constraint`, aborta si no hay
  exactamente uno, y lo recrea con nombre `decision_sin_aplicar_motivo_check` y la
  lista + `choque_clave` (al final: el orden es el espejo).
- **Espejos**: helper `_ultima_migracion_con(marcador)` para que los tests lean la
  definición VIGENTE del CHECK y de la vista (no SQL44 fijo). Test nuevo: kinds de
  `v_decision_huerfana` == `KINDS_QUOTA`.
- **docs/DATABASE.md**: fichas de `decision_sin_aplicar` y `v_decision_huerfana`.
- **R-C3-1 es un bug latente, no solo un test** (los tres coinciden): en el camino de
  éxito `destino` es una tupla (`cycle.py:2116-2117`); con el mutante, o con cualquier
  asimetría futura memoria/persistido, el `append` da `AttributeError` fuera de todo
  `try` y el ciclo se sella `failed`. Arreglo: normalizar a lista dentro de
  `_mezcla_evidencias_persistidas`. Tests: unitario sin DB (hoy rojo) + camino de
  éxito completo con exactamente una entrada por `decision_id`.

## C.2a — tabla append-only del target por entidad y ciclo

Base: fable/opus para la tabla; alcance de opus/sonnet (solo hojas).

```sql
CREATE TABLE target_acos_ciclo (
    cycle_id        BIGINT NOT NULL REFERENCES optimizer_cycle(id),
    ad_entity_id    BIGINT NOT NULL REFERENCES ad_entity(id),
    decided_at      TIMESTAMPTZ NOT NULL,           -- reloj del ciclo aunque no haya decisión
    target_acos_pct NUMERIC NOT NULL,               -- sin escala: el valor exacto usado
    procedencia     TEXT NOT NULL CHECK (procedencia IN (...PELDANOS_CASCADA...)),
    PRIMARY KEY (cycle_id, ad_entity_id)
);  -- append-only por GRANTs; ON CONFLICT DO NOTHING
```

- Se captura en memoria (`_Contadores.targets`) en el punto donde hoy se calcula el
  target (`cycle.py:1542-1545`), para toda hoja que llega ahí (incluye no-op y las que
  luego salen por cooldown/D.2), y se escribe en TX3 junto a `_inserta_decisiones`.
  No se sube por encima de veto/inerte: `cascada_target_acos` revienta con cache ≤0 y
  metería fallas en hojas que hoy salen limpias.
- `tools/replay_ads_economico.py` lee esta tabla y **borra** el fallback de
  `ads_optimizer_goal.updated_at` (`:134-139`, `:176-183`). Ciclos pre-0046 sin
  evidencia quedan `sin_target_historico`.
- Tests: freeze de no-ops y de hojas en cooldown; goal editado después del ciclo no
  cambia el replay; coherencia `decision.inputs.target_acos_pct_usado` = tabla;
  permisos (`InsufficientPrivilege` para UPDATE/DELETE).

## Rechazado

- D.2 dentro de `decide_bid` (mezcla historia de applies con la regla pura) o como
  cooldown direccional por calendario (cuenta días de reloj, que es justo el error).
- D.1b como fila terminal falsa en `apply_queue` (miente sobre el estado de la cola).
- C.2a como historial SCD-2 de goals (el cache de target también es mutable), mapa JSON
  en `notes` (no consultable) o decisión "hold" (ensucia `decision`).

## Preguntas para el dueño (resueltas 2026-09-27: "sí a las 4 recomendaciones")

1. R-C3-1 pasa de "solo test" a "arreglo de 3 líneas en `cycle.py`": ¿aprobado dentro de D.1b?
2. C.2a borra el fallback `updated_at` del replay: los ciclos viejos sin decisión quedan
   `sin_target_historico` y la cobertura de C.2 baja. ¿Aceptado, o se re-mide C.2 antes?
3. C.2a congela solo hojas (el único consumidor no mide ad_group). ¿OK?
4. D.2 sin aviso por Telegram (solo contador en `notes.skips`). ¿OK?
