# Revisión global de ARREGLOS 05 (bloque Z)

Revisor: Claude Opus 5.5, distinto del implementador. Fecha: 2026-11-25. Solo lectura.

## Alcance

- **SHA revisado:** `7b06778d376eb5631b0a2863beea6a90ab804da0` (master con BASE y A a K). El árbol de trabajo estaba limpio y no se modificó.
- **Contexto leído:** `AGENTS.md`, `docs/CONTEXTO.md`, `docs/evidencia/arreglos-05/{README,DISENO,COBERTURA}.md` y los `resultado.md` de A a K (BASE solo por encima). También el crontab real (`tools/despliegue/crontab-orbit.txt`): el ciclo corre a las 9:40, 10:40, 13:40 y 16:40 UTC por plataforma.
- **Código leído línea por línea**, priorizado por riesgo:
  - Ciclo y recuperación (A): `app/cycle.py`, `app/cycle_apply.py`, `app/lock.py`, `app/cli.py` (`cycle`), `app/metricas_listas.guarda`, `migrations/0094_*` y la vista `v_decision_huerfana` (0044).
  - Pujas, cupo de 150 y conciliación (A y H): `app/apply.py`, `app/apply_cuota.py` (`reserva_cupo_ciclo`, `expira_no_intentadas`, `bids_recuperables`, `gate_ancestros`) y `app/apply_reconcilia.py`.
  - Identidad y reversas (B): `app/apply_reversa.py`, `app/apply_store.py` (ledger, `ultima_escritura_en_la_hoja`, `bid_previo_comprobado`), `app/apply_negative.py`, `app/apply_harvest.py` (keyword, lectura de vuelta, conducción por fases) y `app/apply_harvest_reconciliacion.py` (negatives y cosecha).
  - Reposición (C): `app/ads/reposicion.py`.
  - Escritor único (D): `app/ads/write.py` (allowlist, constructor, perfil sellado) y `app/ads/client.py` (envoltura de errores de red).
  - Cola y veto (E): `app/apply_cola.py` (encolado, liberación, aislamiento por fila), `app/apply_cola_ejecuta.aplica_reactivaciones`, `app/apply_veto.py` y `migrations/0098_*`.
  - Meta base (F) y sello (G): `app/cycle_target.py`, `app/cycle_serializacion._sello_bitemporal` y los constructores de `data_observed_at`.
  - TEMPORADA: `app/temporada_io.py` (episodio, efectos, cierres y estampado).
  - Ajustes (I) y retiro de modos (J): `app/config_write._cap_entrante` y `app/optimizer/goals` (resolución de modo).
  - Otras fronteras: `app/redaction.py`, `app/jev_libro.py` (S1), `app/fabrica_motor.py` (autenticación a mitad de lote), `app/apply_regreso_dueno.py` y `app/estimacion_venta.py` (multiplicador de comisión, para K).
- **Revisado por encima:** dependencias de token en las rutas mutantes de `app/api_write.py`, `app/api_fabrica.py`, `app/api_precios_write.py` y `app/api_temporada.py`.
- **Fuera de esta revisión:**
  - La lógica pura de `app/optimizer/` (`politica.py`, `temporada.py`, `replay.py`).
  - Repricing (`app/precio/`), MeLi (catálogo, precio de mercado), reputación, SP-API, el pipeline de estimación y la ingesta.
  - UI y JS (`productos.js`, pantallas) y `tools/despliegue/*.sh`.
  - Las migraciones 0095 a 0097 y 0099: las conocí solo por la evidencia y por el código que las usa.
  - Cosecha de hermanas y su reversa automática en detalle, `archivar_inertes` y `retiro_anuncios`.
  - Las filas de `COBERTURA.md` que son de 03 y 04 (02, 04, 05, 06, 07, 14, 15, 16). De ellas solo miré S1.

## Hallazgos P0/P1

**Ninguno** en el alcance revisado.

Hipótesis de la clase de defecto que dominó el plan, contrastadas con el código y refutadas:

| Hipótesis | Por qué no se sostiene |
|---|---|
| Una `ListaIncompleta` (identidad ambigua) de un solo job anula toda la conciliación previa y deja la plataforma sin aplicar nada. | `ListaIncompleta` hereda de `AdsApiError` (`app/apply_harvest_list.py:184`). Cada job la atrapa y sigue con `continue`, y solo se propaga un 401/403 (`app/apply_harvest_reconciliacion.py:379-386`, `:719-723`). |
| Un ejecutor de recuperación vuelve a decidir, abre otro cupo o reenvía una puja. | No inserta decisiones y deja `decisions_count` en 0 (`app/cycle.py:811-813`). El cupo se cuenta con `decision.cycle_id`, que es el origen (`app/apply_cuota.py:213-222`). Una decisión que ya tiene un intento sale como `ya_intentada` sin enviar nada (`app/apply.py:586-590`). |
| Dos orígenes el mismo día cuando vence el lease durante TX2. | TX3 bloquea la fila del claim con `FOR UPDATE` y exige que el owner siga siendo el mismo antes de sellar (`app/cycle.py:403-443`). El perdedor deshace su transacción. |
| Un owner vencido gasta cuota o cupo, o sella un intento. | `_tick` valida al dueño antes de crear la fila del ledger (`app/apply.py:503-513`). El sello pasa por `exige_dueno` con `FOR UPDATE` (`app/apply.py:687-712`). La reserva del cupo, la cuota y la intención quedan en una sola transacción. |
| Una puja incierta se reenvía. | La conciliación solo lee y sella (`app/apply_reconcilia.py:148-235`). El aplicador no vuelve a enviar una decisión que ya tiene intento. |
| Un veto se salta por una decisión calculada antes del veto o por el claim del motor. | El trigger de 0098 y `veta_efecto` toman el mismo candado por clave. El veto vuelve a comprobar `applying` después de su UPDATE (`app/apply_veto.py:96-127`). |
| La reversa de una puja pisa un cambio manual del dueño. | Antes del PUT relee la hoja y solo autoriza si el valor vivo coincide con la última escritura confirmada de Orbit (`app/apply_reversa.py:296-331`, `app/apply_store.py:191-202`). |
| El negative conciliado adopta un objeto preexistente. | Exige `ausencia_previa` durable con la misma identidad. Si no la hay, queda pendiente (`app/apply_harvest_reconciliacion.py:452-462`). |
| Reponer dos veces crea dos keywords. | Intención con lease y commit antes del POST. Un 5xx deja el intento abierto, el ACK y el ID se guardan antes del readback, y un 401/403 sella no aplicado y detiene la fase (`app/ads/reposicion.py:356-450`). |
| Un rechazo de autenticación deja el lote a medias. | En la cola, `_fase_apply` hace rollback de claim y cuota que no se habían comprometido (`app/cycle_apply.py:480-489`). La fábrica pide el token antes de registrar el lote y sella `failed` con rollback explícito (`app/fabrica_motor.py:565-568`, `:585-627`). |
| Una cuota escrita como `1,000` se guarda como `null`. | Hay un solo validador, `cap_desde_settings`. `null`, booleanos y texto ilegible se rechazan con 422 (`app/config_write.py:193-209`). |
| Un término reobservado tumba el lote de TX3. | El sello bitemporal se aplica a todo término (`app/cycle_serializacion.py:104-117`, `:308`). Los demás constructores usan `decided_at` o ya pasan por el sello. |
| Recuperar abre un segundo episodio de temporada o repite la entrada o el regreso. | `registra_ciclo` y `abre_efectos` solo corren en la TX3 del origen (`app/cycle.py:750-793`). La exención del cupo exige el marcador congelado (`app/apply_cuota.py:488-493`). |

## Hallazgos P2

1. `app/cycle_target.py:71`: el ancla por hoja de los peldaños de familia y grupo (`_SQL_TARGETS_PREVIOS`) sigue filtrando `c.status = 'done'`. Un ciclo `degraded` con TX3 comprometida no ancla esa rampa. F solo corrigió la base de plataforma. Efecto: la convergencia se frena tras ciclos degradados, sin pérdida de dinero.
2. `app/apply.py:426-454`: si falla la lectura fresca de una puja (5xx), o si un 429 corta el resto del lote, esas pujas quedan con `sin_respuesta`. Eso es desenlace, así que el ejecutor no las reintenta el mismo día. El diseño lo documenta; el costo es un día de retraso en la puja.
3. `app/apply_cola.py:587-607`: el rollback de `_aisla_fila` también descarta los descartes y registros de filas anteriores que todavía no se habían comprometido. Se reevalúan en el siguiente barrido y no se pierde nada.
4. `app/apply_cola.py:756`: si se pierde la carrera del claim después de cobrar la cuota, esa unidad de cuota se pierde. Es anterior a este plan.
5. `app/apply_cola.py:424-428`: cuando encola un ejecutor, el `choque_clave` se atribuye al ciclo de origen y no al ejecutor. Solo afecta la auditoría.
6. `migrations/0098_arreglos05_indice_shadow.sql:106`: una práctica shadow vigente también bloquea el alta live de la misma clave durante 48 h como máximo. Coincide con el comportamiento previo y solo aparece al pasar una campaña de shadow a live.
7. `app/cycle.py:172-185`: el plan toma el último origen del día (`LIMIT 1`). Si un origen shadow forzado sigue a un origen live con pendientes, no se abre ejecutor. Requiere `--forzar` y además un cambio de modo.
8. `app/cli.py` y `app/cycle.py:1094`: con `--forzar` se abre un segundo origen, que trae otro cupo de 150. Es manual y está documentado como la única forma de decidir dos veces.
9. `app/apply_harvest.py:574`: después de un 5xx en el POST, la keyword que sí se creó se adopta como `incierta` y la reversa no la borra. Es conservador y hacen falta dos eventos.
10. `app/apply_reconcilia.py:222-234`: si un HTTP tardío llega después de que se perdió el lease, el sucesor puede sellar divergencia mientras Amazon aplica el valor. Requiere dos fallos encadenados y ya está declarado en `DISENO.md`.
11. `app/apply_regreso_dueno.py`: la acción manual de regresar corre fuera del claim del ciclo. Si coincide en el tiempo con un PUT del ciclo en la misma hoja, gana el regreso del dueño, por la regla de última escritura.
12. `app/apply.py:660-668`: si la lectura de vuelta de una puja falla, el ACK del PUT no se guarda. Para pujas solo se pierde evidencia de auditoría, porque la conciliación sella con su propia lectura.
13. `tests/test_notifica.py:1843`, `:1876` y `:1918`: tres pruebas rojas previas en master (`test_avisar_campanas_*`). El fake `_Conn9` tiene respuestas en orden fijo que ya no coinciden con las consultas (`tuple index out of range` dentro del fake). No hay evidencia de que la ruta real esté rota, pero conviene alinear el fake o probar contra base de datos.
14. `tests/test_precio_reglas.py::test_r4_g8_barrido_float_entra_a_subpaquetes`: falla solo en Windows por el separador de ruta (declarado en K).
15. K-MX: 3 de 113 productos caen fuera del 0.1% de la razón 1.16 (declarado en K, no cambia la mediana).

## Veredicto

**PARCIAL.**

- **Código integrado (A a J y controles):** no encontré bloqueos P0/P1 en el alcance revisado de este SHA.
  - El ciclo, la recuperación, el tope de 150, la identidad y las reversas, la reposición, el escritor único, el veto, la meta base, el sello y los modos cumplen sus contratos.
  - Ningún camino de mutación revisado evita la tabla de estados, adelanta una guarda a una lectura, pierde el ID entre intención, ACK y sello, o deja un veto o un cupo expuestos a una carrera simple.
- **K está abierto para Amazon US** (pendiente declarado, no hallazgo de esta revisión).
  - La fuente no permite adjudicar el impuesto sobre comisiones en US: la razón cobrado/cotizado es 0.8586 y cambia de régimen entre meses.
  - Por eso el plan no puede recibir pase global.
  - Hoy US conserva la cotización sin multiplicador (`app/estimacion_venta.py:352-374`, sin política S4 para US) y la abstención `impuesto_fee_pendiente` solo salta ante impuesto explícito. No se inventa tasa.
  - Según la evidencia, ninguna decisión live depende de esa estimación: el target derivado usa el libro real y precios sigue en shadow efectivo. Lo tomé de la evidencia; no lo comprobé en producción.
  - Cerrar US requiere la tarea de evidencia registrada en `K/resultado.md`: cotizaciones de 2025-12 a 2026-02 o detalle de Finances, y confirmación documental del IVA sobre la comisión.
- **Pendiente de Z:** adjuntar SHA, prueba y estado final a cada fila de `COBERTURA.md`. Esta revisión no corrió la aceptación sobre este SHA.

## Notas

- **No ejecuté nada:** ni pruebas, ni la aceptación (`comprobar.sh`), ni build, ni la app. El rol es de revisión sin ejecutar código del repo. Los resultados de pruebas citados vienen de la evidencia de cada bloque y no los volví a correr sobre `7b06778d`.
- **No leí producción.** La configuración vigente (`niveles_v3` en MX y US, meta `live`, pausa por producto `shadow`, precios en shadow efectivo) la tomé de `J/previa.txt`, `K/resultado.md` y el `README` de diseño.
- **La revisión es por muestreo con prioridad por riesgo,** sobre unas 23 mil líneas de los módulos mutantes. No certifica ausencia de bugs fuera del alcance listado, en particular en la lógica pura del optimizador, MeLi, repricing, reputación, SP-API y la UI.
- **La carrera con un HTTP tardío tras perder el lease** (P2-10) queda como límite aceptado por diseño: ningún candado local puede cancelar un envío que ya salió a Amazon.
- **Las 3 pruebas rojas de `test_notifica`** (P2-13) son previas a J. No comprobé contra base de datos ni logs que el cron «Avisos de campañas» funcione en producción.


---

## Evidencia de aceptacion sobre el mismo SHA (implementador)

Esta seccion la agrega el implementador, no el revisor. El revisor declaro que no
ejecuto pruebas por su rol. Sobre el mismo `7b06778d`:

- Aceptacion integrada con `comprobar.sh ALL`: 31 casos, cero errores y cero
  omisiones (`Z/acceptance.xml`).
- `N a`..`N j`: 429 passed. `N k`: sin casos por decision K.2.
- Vecinos por causa: 341 passed (ciclo/apply/reversa) y 573 passed con un fallo
  solo-Windows ya documentado (temporada/estimacion/precios/API).
- UI del mapa y adversos sin fallo de producto (`Z/ui.md`).
- Control 01X, arquitectura y Ruff verdes.

Los 15 P2 del revisor quedan en "Seguimiento (P2)" de `Z/resultado.md`, sin
commit, y ninguna fila se reclasifica.
