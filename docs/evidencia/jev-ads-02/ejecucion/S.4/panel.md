# S.4: panel de revisión (interrogate adaptado)

Sin herramientas de delegación en este contexto no hubo hijos Muse ni
comment-sicko (este runtime no concede `subagent_spawn` ni `workflow`):
deslop/no-comments se aplicaron a mano sobre el diff (quitaron el
parámetro muerto `unidad` de `_relevancia_de`, la tupla `_PLATAFORMAS`
duplicada y 3 líneas de aire). Panel GLM por `opencode run`
(zai-coding-plan), 3 revisores en paralelo con el mismo prompt
solo-lectura (`/tmp/s4-panel-prompt.txt`, salidas completas en
`panel-glm-*.txt` de este directorio):

- `zai-coding-plan/glm-5.3` (rc=0): sin bloqueantes; 8 no bloqueantes.
- `zai-coding-plan/glm-5.2` (rc=0): sin bloqueantes; 8 no bloqueantes.
- `zai-coding-plan/glm-4.7` (rc=0): sin bloqueantes; 6 no bloqueantes.
  Verificó además los 6 puntos uno por uno (tope atómico + flock, apagado
  antes del lote, seco sin escritor, un pago por ClavePar con índice,
  SQL idéntico con BETWEEN dentro, guarda en las 3 formas de import).

Síntesis del lead (consenso = 2+ modelos):

Aplicados (8):

- Reloj vivo para el tope (3/3): el `Libro` congelaba `ahora` y una
  corrida que cruza medianoche UTC contaba el día viejo; ahora
  `llamadas_de_hoy` usa el reloj real (`app/jev_senales.py`, `_aplicar`
  y `_seco`).
- Seco descuenta contexto excedido (1/3: 4.7): `pagaria` aplicaba solo
  `exito_global`; ahora también `_exceso_contexto`.
- Tope-cero reporta "tope" (1/3: 4.7): con `tope_diario = 0` el cierre
  decía "completa" sin pagar nada; ahora "tope".
- Fila del cron en orden de hora en DEPLOY.md (1/3: 4.7).
- `grupos_con_gasto` (1/3: 5.3): era "grupos con señal"; ahora cuenta
  distintos con `gasto > 0` (`salud`).
- Censo vacío (1/3: 5.3): pasaba por `NoEvaluada("sin_cupo")` sin haber
  cupo de por medio; ahora pasa por `componer` →
  `Indeterminado(universo_vacio)` (`_relevancia_de`).
- `main` imprime `type(exc).__name__` (1/3: 5.2).
- La guarda de imports deja dicho que `TYPE_CHECKING` no corre en
  runtime (1/3: 5.3, comentario en el test).

Desestimados con razón (11):

- `FichaFaltante`-solo → `NoEvaluada` (5.3): contradice S.2, que sella
  `FichaFaltante` → `Indeterminado`; `pendientes_de_jev` cuenta por
  diseño solo "veredicto falta por cupo".
- `_exceso_contexto` duplica `pedir_juicio` (5.3): cierto, pero
  `jev_juicios.py` no es editable en S.4; va a fila del plan (S.5+).
- `AHORA` fijo + trigger de futuro (5.3): discutible; el instante ya
  pasó (el tiempo solo avanza) y el suite corre después.
- Rosters fuera del snapshot (5.2): el diseño dice captura perezosa;
  la mezcla queda registrada por señal vía `roster.sha256`.
- `datos_hasta` fuera de `insumos_sha256` (5.2): la lista de insumos es
  la del diseño; mismos números no deben resellar.
- `fichas_por_vencer_14d` cuenta sustituidas (5.2): el diseño dice
  "versiones", no vigentes por listing.
- `_economia_de` O(claves × grupos) (5.2): batch 2×/día, escala actual
  en segundos; prematuro.
- Prueba de reservas concurrentes (5.2): el candado de sesión impide
  dos escritores del job; la serialización la garantiza el advisory
  lock de Postgres; el plan fija 13 pruebas.
- Historial perezoso (4.7, misma familia que 5.2 perf): batch 2×/día,
  escala actual en segundos.
- 8 conteos de `_jev_de` por página (4.7): tablas jev chicas (señales
  por día) y consultas acotadas; vigilar si crecen.

Para fila del plan (no bloqueantes que no se corrigen aquí):

- Partir `app/jev_senales.py` (job vs lecturas de pantalla) antes de
  S.5 (2/3: 5.3, 5.2; 892/900 líneas).
- Helper único de límites de bytes en `jev_juicios` (5.3).
- Precedencia no-pago vs `FichaFaltante` (auto-review F1, PR 406, Low
  no bloqueante): roster mixto + sin_api_key/sin_cupo sella
  `sin_veredicto` (ficha_ausente) en vez de `no_evaluada`, y
  `pendientes_de_jev` no la cuenta. Cambiarlo contradice S.2
  sellado (`FichaFaltante` → `Indeterminado`) y el diseño
  (`pendientes` = solo "veredicto falta por cupo"); requiere
  decisión del dueño + prueba de roster mixto.
