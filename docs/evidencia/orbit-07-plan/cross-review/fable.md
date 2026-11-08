Objetivo del dueño, en mis palabras: que el motor publicitario decida mejor (proteger ventas recientes, economía por producto, gasto agregado de aprendizaje, evaluación de pujas) y que cada mejora quede activa tras verificación técnica, sin pilotos ni esperas globales. Mi entrega es solo el dictamen sobre el plan; no toqué código ni plan.

**Dictamen asesor.** El plan es construible en M1, M2, M4 y M5+M6 con correcciones puntuales. M3, tal como está escrito, no cambia ninguna decisión. Encontré seis defectos verificados. Dos afectan el comportamiento (M3 vacío, M1 con un caso indefinido que pausa una hoja que vende). Dos son elecciones monetarias que el plan calla y debe hacer explícitas (congelación de bids durante la espera de M1, rampa del target en M2). Dos son referencias erróneas a módulos y pruebas. La secuencia no introduce esperas globales. No cito principios de poteto porque no leí sus skills hoja en esta sesión.

## Hallazgos verificados

**1. M3 bloquea subidas que la política ya no puede producir.** Severidad alta.
- Referencias. Plan líneas 232 y 255 ("impedir subidas ordinarias", "evita aumentar exposición"); M4 línea 267 define agotado como cero compras y cero ventas. Código `app/optimizer/politica.py:238-246` (la subida R6 solo vive en `_juzga_vendedora`, que exige pedidos propios ≥ 1) y `politica.py:320-344` (`_juzga_sin_pedidos` solo devuelve recortes o Mantener). `app/impulso.py:177` cierra el aprendizaje con un pedido.
- Comando.
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests .venv/bin/python $TMPDIR/orbit07-sondas/sonda_c_m3.py
```
- Salida. `casos evaluados: 1344` ... `subidas (Mover con factor > 0) con pedidos propios = 0: 0`. Los veredictos fueron dato_faltante, sin_gasto, grupo_cumple, sin_evidencia, hoja_delgada y tres recortes.
- Exigido por la intención. Una entrega activable debe cambiar al menos una decisión.
- Impacto (inferencia). Una PR, una activación y un seguimiento sin efecto en pujas ni gasto. El agregado por producto sí sirve, pero solo como insumo de M4.
- Corrección mínima. En M0 definir "agotado" admitiendo productos con ventas bajo equilibrio (entonces el bloqueo de subidas tiene efecto y debe conciliarse con R6), o declarar M3 como preparación de datos de M4 y quitar la afirmación de mejora activa.

**2. M1 no define qué pasa con un segundo pedido reciente cuando el pedido ancla se corrige a cero.** Severidad media.
- Referencias. Plan 146 ("un pedido reciente conocido abre una protección"), 147 ("venta posterior no extiende ese plazo"), 148 ("si el pedido se corrigió a cero, la protección queda consumida y la pausa puede proceder"). La reactivación automática queda excluida en 382.
- Comprobación. Inspección textual. Las tres frases son compatibles con dos construcciones opuestas para el caso "ancla D-9 corregida a cero, pedido real en D-1": pausar (148) o abrir protección nueva (146).
- Exigido por la intención. Una regla única que el constructor no tenga que elegir.
- Impacto (inferencia). La lectura literal de 148 pausa una hoja con una venta de dos días y nadie la reactiva. Frecuencia desconocida; exige una corrección de atribución a cero.
- Corrección mínima. Una frase en 148: al consumir, si existe otro pedido reciente conocido posterior al ancla, cerrar sin pausar y permitir a lo sumo un episodio nuevo anclado a él (cada fecha de pedido ancla una sola vez), o decir explícitamente que la pausa procede.

**3. La espera de M1 en la cola congela los bids de la hoja hasta diez días y repite un LIST por ciclo.** Severidad media.
- Referencias. Plan 150 y 165 ("esperar" sin cobrar cuota ni reiniciar veto). Código `app/cycle.py:128-135` y `cycle.py:1673-1679` (una clave entity_cut no terminal salta la hoja entera del motor de bids), `app/apply_cola.py:140-146` (released cuenta como bloqueo), `apply_cola.py:172-178` (cada barrido reintenta las released), `apply_cola.py:748` (LIST fresco en cada revalidación). Precedente `tests/test_apply_cola.py:2288-2311` (espera_target queda released, vetable, sin cuota).
- Comprobación. Inspección de código. La máquina de estados de `migrations/0002_apply.sql:212-221` no permite volver a pending_veto, así que "esperar" solo puede ser quedarse en released.
- Exigido por la intención. Proteger la venta reciente sin frenar el resto del motor para esa hoja.
- Impacto (inferencia). Si la venta aparece durante el veto, la hoja no recibe movimientos de bid hasta nueve días. Si la venta era conocida al decidir, sí los recibe. Dos caminos, dos comportamientos. Además una llamada LIST por fila en espera y por ciclo.
- Corrección mínima. Descartar en revalidación con motivo propio y dejar que el camino de decisión protegido sea dueño del episodio (ancla persistida), aceptando un veto nuevo si la pausa procede después. O declarar la congelación como elección en M1.

**4. M2 salta la banda del target y calla sobre la rampa de bajada.** Severidad media.
- Referencias. Plan 191 declara el salto de la banda (4 % no sube a 10 %). `docs/CONTEXTO.md:336` ("baja como máximo 0.5 puntos por ciclo"). Código `app/optimizer/goals.py:569-576` y `goals.py:924-932`. El plan no contiene "rampa", "0.5 puntos", "por ciclo" ni "vaivén" (grep sin resultado).
- Comando.
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests .venv/bin/python $TMPDIR/orbit07-sondas/sonda_d_freeze_rampa.py
```
- Salida. `margen 8 x 0.5 = derivado 4.0; ultimo=None -> aplicado 10` y `ultimo=20.72 -> aplicado 20.22`.
- Exigido por la intención. Un target por producto con la misma protección contra bajadas bruscas y vaivén que los demás peldaños, o una excepción declarada con razón.
- Impacto (inferencia). El target de una hoja pasa de 20.72 a 4 en un ciclo y vuelve a 20.72 cuando la elegibilidad cambia (halo o identidad, plan 192). Es el vaivén que la rampa existe para evitar.
- Corrección mínima. Decir en M2 si el peldaño de producto respeta `MARGEN_PASO_MAX_BAJADA` anclado al último aplicado de la hoja y si la pérdida de elegibilidad converge en vez de saltar.

**5. M0 y M3 apuntan al módulo y a las pruebas equivocadas para el colapso y la madurez de 30 días.** Severidad baja-media.
- Referencias. Plan 97 (lista de lectura sin `app/evaluacion_catalogo.py`), 228 (colapso por fecha), 241 (reutilizar casos de consolidación de `tests/test_ads_producto_ingesta.py`), 268 (reobservación desde treinta días). Código existente `app/evaluacion_catalogo.py:125-131` (madurez por fecha a D+30) y `evaluacion_catalogo.py:155-160` (colapso a la última observación).
- Comprobación. `grep -n "^def test_" tests/test_ads_producto_ingesta.py` lista pruebas de body, plan, dedupe y pipeline; ninguna de colapso ni madurez. Las que existen están en `tests/test_evaluacion_catalogo.py` líneas 350, 390 y 407.
- Impacto. Riesgo de una segunda implementación del colapso (regla 2) y de "reutilizar" pruebas que no existen.
- Corrección mínima. Añadir el módulo a la lista de M0 y de reuso de M3; apuntar las pruebas de M3 a test_evaluacion_catalogo.

**6. M1 lista `eras.py` entre los archivos a modificar y a la vez exige que no consuma la regla nueva.** Severidad baja.
- Referencias. Plan 140 contra 151. `app/optimizer/eras.py:7-8` ("si un ajuste futuro cambia este archivo, la prueba dorada de replay grita"). eras.py llama `_decide_pause` (línea 258) y solo tendría que cambiar si M1 altera esa firma.
- Corrección mínima. Mover eras.py a "no tocar" y añadir la protección como paso puro aparte en politica, sin cambiar `_decide_pause`.

## Hipótesis y limitaciones

- **Dependencia M5+M6 sobre M2 parece blanda** (inferencia). La regla de M6 (ventas conservadas, mejor economía) se calcula con gasto, ventas y pedidos. M5 ya declara contribución desconocida sin margen (plan 312). Serializar detrás de M2 puede retrasar sin necesidad.
- **El estado "indeterminada" no es un borde.** Sonda E: un solo día sin ingesta en D-9..D-1 deja `pedidos_inmaduros` en None para todas las hojas (`app/lecturas_caso.py:491,501-502`). Sonda B: con None y 120 clics hoy se pausa. Bajo la regla 149 la pausa seguiría procediendo. No puedo medir frecuencia en producción. El plan insinúa cobertura por fecha (145) pero debería decirlo.
- **Contrato de freeze.** Sonda D: `desde_json` rechaza clave extra o faltante, y el golden de `tests/test_cycle.py:966` pinea los inputs. No es defecto del plan, pero M1 y M2 deben presupuestar una versión del caso o claves opcionales.
- No consulté producción ni Amazon. No afirmo frecuencias ni resultados de negocio.

## Comprobaciones ejecutadas

Todas desde mi copia, con `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests` y el Python del proyecto. Sondas en `$TMPDIR/orbit07-sondas/`.

| Comprobación | Resultado |
|---|---|
| `graphify query` sobre pausas, gracia, atribución, retiro y reversa | 543 nodos; guió la lectura |
| Sonda A, reproduce Apéndice A | 99 clics → Mantener(venta_reciente); 120 → Pausar(pause_umbral) |
| Sonda B, pedidos inmaduros None | 99 → Mantener(dato_faltante); 120 → Pausar |
| Sonda C, subidas con cero pedidos propios | 1344 casos, 0 subidas |
| Sonda D, freeze y rampa | ValueError con clave extra o faltante; 4 → 10 por banda; 20.72 → 20.22 por rampa |
| Sonda E, hueco de ingesta en D-9..D-1 | inm_ok False con un solo día faltante |
| `pytest tests/test_optimizer_politica.py tests/test_optimizer_caso.py` | 76 passed |
| Lectura de AGENTS, CONTEXTO, diseño v2, borrador AUTO-03, plan, skill poteto | hecha |
| Lectura de politica, caso, bid, replay, eras, lecturas_caso, apply_cola, cycle (tramos), goals (tramos), retiro_anuncios (tramos), impulso, economia_observada, evaluacion_catalogo, migraciones 0002/0020/0046/0066/0067 | hecha |
