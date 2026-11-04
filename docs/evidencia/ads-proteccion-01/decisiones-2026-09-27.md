# Decisiones del dueno y cierre de los puntos del analisis Kimi (2026-09-27)

Registro de las decisiones tomadas en el turno con el lead del 26/27-sep-2026 y de
las lecturas (solo SELECT) que cerraron los puntos 5 y 6 del analisis de Kimi del
24-sep. Nada de esto cambia codigo ni configuracion de Orbit.

## Decisiones

| Tema | Decision literal / efecto |
| --- | --- |
| Campana US 3913 (`A1U - Auto Discovery - US`, campaignId `138625505369345`) | El dueno la pauso a mano en Amazon el 2026-09-27. 3909 (`A1U - Category Exact - US`) y 3926 (`AU2 - Category Exact - US`) ya estaban `PAUSED` en el cache de estructura del 27-sep. Readback de 3913: job `at` 4 en goncloud, 2026-09-28 07:20 UTC, aviso por Telegram. |
| Campana MX 150 (`AC - Auto Discovery - MX`, campaignId `6725297929896`) | Opcion 2 del dueno: se queda `ENABLED` como descubrimiento de terminos, aceptando ~150 MXN/semana sin venta. No califica para el corte economico (exceso < 1000 MXN). |
| R-H5-1 | Aceptado con condicion (obs-4): la lectura de B.4 cuenta tambien bids aplicados (`apply_attempt`, `decision_application`) desde `INICIO_SHADOW`, no solo `apply_queue.applied_at`. La condicion va en el recordatorio del 29-sep (job `at` 1). |
| R-C3-1 | Va dentro de D.1b como agregado (test del camino de exito de `_mezcla_evidencias_persistidas` y mutante del dedupe en `cycle.py:1182`). No se mete en D.2 porque D.2 tiene fecha. |
| R-C4-1 | Aceptado como falla de proceso (#342 mergeado 46 s despues de `gate` y 8 min antes de terminar `review`; ver abajo). |
| Proteccion de `master` | Activada 2026-09-27 via API: checks obligatorios `gate` y `review` (GitHub Actions), `enforce_admins=true`, sin force push ni borrado, sin aprobacion humana (todos los agentes usan la cuenta `gon0801`). Emergencia: `gh api -X DELETE repos/gon0801/goncloud-Orbit/branches/master/protection`. |
| D.2 | N = 10 dias (decision final; ver `D.2/decision.md`, que entra con el PR de D.2). |

### Linea de tiempo de #342 (R-C4-1), UTC 2026-09-25

```text
06:49:04  merge C.3 (#341)
06:49:39  #342 ready_for_review
06:50:14  commit del rebase (timestamp del commit)
06:50:48  force-push de cb193a1 (C.4/review-c4-rb3-opus.md:4)
06:51:25  gate success
06:52:11  merge #342 (cuenta gon0801)
06:56:55  commit de la aceptacion C.2b (28d34d8)
07:00:16  review (DeepSeek) completed
```

`master` no tenia proteccion ("Branch not protected", HTTP 404), asi que nada
obligaba a esperar `review`.

## Punto 5 de Kimi: "2 negatives + 1 harvest live sin aplicar (14/19-sep)"

Lectura del 2026-09-27 (decisiones `negative`/`harvest` de ciclos live 13-20 sep):

```text
  id  |   kind   | ciclo | platform  |    dia     | modo_cola |  estado   |  discard_motivo   | verify_ok
 2321 | negative |    57 | amazon_us | 2026-09-14 | live      | applied   |                   | t
 2322 | negative |    57 | amazon_us | 2026-09-14 | live      | applied   |                   | t
 2323 | negative |    57 | amazon_us | 2026-09-14 | live      | discarded | ya_no_califica    |
 2340 | harvest  |    65 | amazon_mx | 2026-09-16 | live      | applied   |                   | t
 2360 | harvest  |    69 | amazon_mx | 2026-09-18 | live      | applied   |                   | t
 2367 | negative |    70 | amazon_us | 2026-09-19 | live      | discarded | vendio_en_ventana |
```

- Los 2 negatives no aplicados SI tienen explicacion registrada: la revalidacion
  al liberar los descarto (`apply_queue.discard_motivo` = `ya_no_califica` y
  `vendio_en_ventana`). Kimi no leyo esa columna.
- Todos los harvest de septiembre (2276, 2311, 2340, 2360) quedaron `applied`,
  `harvest_job.fase = done`, `verify_ok = t`. No existe un harvest live sin
  aplicar: ese tercio del punto no se reproduce.
- `v_decision_huerfana` no tiene ningun corte: las 197 `sin_registro` son todas
  `bid` (el hueco que cerro D.1).

## Punto 6 de Kimi: "propuestas contradictorias el mismo dia sobre la misma entidad"

- Misma entidad, mismo ciclo, >1 decision: 2 casos, ambos legitimos (terminos
  distintos): ciclo 57 entidad 3989 negatives "arras de boda" / "arras for
  wedding ceremony"; ciclo 16 entidad 342 harvests "arras matrimoniales
  cristianas" / "arras matrimoniales personalizadas". No rompe "una decision por
  entidad/ciclo" (la clave de los cortes incluye el termino).
- Contradicciones reales (misma entidad y dia con kinds distintos o bids en
  direccion opuesta): 5 casos, todos del 2026-08-24 al 2026-08-29, en ciclos
  shadow re-corridos varias veces el mismo dia durante la sombra previa al live.
  Cero desde el live del 2026-09-02. No es un bug del motor.

## Estado de los 10 puntos del analisis Kimi

| # | Punto | Estado |
| --- | --- | --- |
| 1 | Pausar 3913 | Hecho (27-sep); readback 28-sep |
| 2 | Propuesta automatica de campana | C.4/C.5 en prod; live con C.6 |
| 3 | US no converge solo con bids | C.3 (hoja economica) + B.2; live con B.4/C.6 |
| 4 | Decisiones live huerfanas por cuota | D.1 en prod (0044), verificado 26-sep |
| 5 | 2 negatives + 1 harvest sin explicacion | Cerrado: descartes con motivo; harvest no se reproduce |
| 6 | Shadow sin dedupe / contradicciones | Cerrado: sombra pre-live re-corrida, cero desde live |
| 7 | Vaiven de bids (3835) | D.2, N = 10, antes del ~12-oct |
| 8 | `campana_no_enabled` masivo | Cerrado: campanas PAUSED/ARCHIVED, no faltan goals |
| 9 | MX 150 sin venta | Decision: se queda ENABLED |
| 10 | Estrategia US | Medir ~4-oct tras las pausas |
