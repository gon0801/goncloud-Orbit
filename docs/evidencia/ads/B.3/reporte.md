# B.3 — replay de PAUSE tras BID, keyword 4925

Lectura de produccion del 24-sep-2026 como `orbit_read`, sin escrituras a
Amazon ni cambio de goals. Consulta reproducible:
[`replay-4925.sql`](replay-4925.sql). La consulta escoge, para cada fecha y
entidad, la ultima observacion con `observed_at <= optimizer_cycle.started_at`;
el desempate es `source_report_id DESC`, como en `windows.py`. Fija la fecha
UTC y excluye hojas vistas por primera vez despues del ciclo. Conserva los
IDs de reportes usados, el maximo `observed_at`, el target aplicado y su
procedencia guardados en `optimizer_cycle.notes`.

## Resultado por ciclo

La moneda fue USD, las ventas atribuidas y pedidos fueron cero medidos, y
hubo al menos 22 fechas maduras en cada ciclo. El piso de gasto vigente era
USD 40. Los dos goals que regian a AU2 (campana 3926 y plataforma US)
estaban habilitados en live y su `updated_at` era 2-sep, anterior al replay;
ninguno tenia un target explicito. En los nueve ciclos el target provino de
`margen_plataforma`, segun la nota congelada de cada ciclo.

| Ciclo / dia | Corte hasta | USD | Clics / umbral | BID en cooldown | Califica en la era real | Califica con B.2, aislado |
| --- | --- | ---: | ---: | --- | --- | --- |
| 51 / 11-sep | 1-sep | 105.20 | 131 / 150 | no | no | no |
| 53 / 12-sep | 2-sep | 110.73 | 139 / 153 | si | no | no |
| 55 / 13-sep | 3-sep | 118.85 | 152 / 155 | si | no | no |
| 57 / 14-sep | 4-sep | 127.94 | 164 / 157 | si | no | **si** |
| 59 / 15-sep | 5-sep | 140.30 | 183 / 160 | si | no | si* |
| 64 / 16-sep | 6-sep | 153.36 | 199 / 153 | si | no | si* |
| 66 / 17-sep | 7-sep | 159.16 | 207 / 155 | si | no | si* |
| 68 / 18-sep | 8-sep | 164.03 | 213 / 157 | si | no | si* |
| 70 / 19-sep | 9-sep | 173.32 | 225 / 159 | no | **si** | si* |

`*` Es elegibilidad aislada sobre el estado real. Si el 14-sep se hubiera
encolado la propuesta, el veto en vuelo habria impedido proponer de nuevo
durante al menos 48 horas; una aplicacion confirmada cambiaria otros gates.
No son cinco propuestas ni cinco aplicaciones contrafactuales. En particular,
la falta de snapshot historico completo de estado externo, veto, cuota y
revalidacion futura impide fijar una fecha de apply contrafactual o un ahorro.

## Era, cola y readback reales

- BID 2269 del ciclo 51: confirmado el 11-sep a las 08:40:03 UTC con
  `verify_ok=true`. Por comparador estricto `confirmed_at > started_at-7d`,
  enfrio los ciclos 53–68, pero ya no el 70. Las decisiones reales previas
  a B.2 no llevan `cooldown_policy_version`; su gate se interpreta con la
  politica de esa era. Las decisiones nuevas congelan
  `cooldown_policy_version=pause_after_bid_v1`, ademas de la procedencia del
  target, umbral, piso y ventanas ya congelados en `inputs`.
- La unica PAUSE real de 4925 fue la decision 2361 del ciclo 70, tomada el
  19-sep 08:40:01 UTC. Cola 15 entro en `pending_veto`, vencio el
  21-sep 08:40:01, salio a `released` a las 08:40:06 y quedo `applied` a
  las 08:40:07. El intento 277 fue `normal`, `resultado=ok`; el ledger
  registro `verify_ok=true` a las 08:40:07. El ciclo ejecutor 74 registro
  una PAUSE aplicada. El snapshot de estructura sincronizado de Amazon del
  24-sep 06:45 UTC muestra la keyword 4925 en `PAUSED`; el readback del
  aplicador confirma el estado en el instante del apply.
- La integracion de cola ya cubre vencimiento, revalidacion fresca antes de
  quota/claim, descarte por cambio de umbral, aplicacion con readback y
  reversa (`tests/test_apply_cola.py`). La regresion B.3 del freeze falla
  antes del cambio (`KeyError: cooldown_policy_version`) y pasa con el
  cambio: `tests/test_cycle_pause_cooldown.py`, 9 passed. Los tests de cola
  con PostgreSQL quedan para CI: el puerto local 5432 no estaba disponible
  en este worktree.

El replay as-of se limita a 4925: la estructura no guarda versiones de
`ad_entity_state` ni cada gate de ancestros para todos los ciclos, y el
registro de quota/veto observado pertenece a la linea temporal real. Esta
evidencia no habilita por si sola live. Faltan review cruzada, CI del SHA
final, shadow y comprobacion live del bloque B.3.
