# M1: política de protección por venta reciente

Fecha: 2026-11-07 (UTC). Base: `f86ed7dcd77f0af7b540217c3df9422ebe5c7154`.
Plan: `plans/orbit-07-motor-decisiones.md`. Migración:
`0081_orbit07_proteccion_venta.sql`.

## Regla

Una pausa por **cero pedidos maduros** que además tenga un **pedido reciente
conocido** en `[D-9, D-1]` espera. El ancla es la `metric_date` más antigua con
pedido en esa lectura y el vencimiento es `ancla + 10 días UTC`. Ni la corrida,
ni una reobservación, ni una propuesta, ni una venta posterior mueven esos
valores.

La subpolítica es una función pura (`app/optimizer/proteccion.py`) y la usan:

- `politica.decide` (ciclo con la política de bids encendida);
- `bid.decide_pause` (ciclo con la política apagada y la revalidación de la
  cola);
- la lectura por lote `app/proteccion_venta.py` arma los mismos insumos.

## Estados y transiciones

| Estado | Condición | Efecto |
|---|---|---|
| Ausente | Sin pedido reciente conocido | La pausa sigue su curso histórico |
| Activa | `hoy < vence_el` con pedido ancla conocido | Esperar; solo se suprime la pausa |
| Indeterminada | Fuente incompleta o ancla aún fuera de la ventana madura | Esperar; no se abre gracia nueva |
| Consumida | `hoy >= vence_el` y el ancla ya está madura | La pausa procede si sus requisitos se cumplen |

La única escritura es la apertura del episodio (append-only). El cierre se
deriva de `vence_el` y de la ventana madura; no se borra ni se actualiza una
fila.

## Decisiones de implementación

1. **Una pausa, una espera.** La espera conserva la fila `released` original, su
   ancla y su vencimiento. No cobra cuota, no entra a `applying` y no reinicia
   el veto de 48 horas.
2. **Sin renovación indefinida.** Un pedido inmaduro posterior no renueva el
   episodio consumido. Solo se abre otro episodio si el ancla anterior ya salió
   de la ventana madura (condición nueva, separada por al menos la ventana de
   cortes).
3. **Pujas no congeladas.** La fila `released` deja de bloquear la hoja cuando
   hay venta reciente o un episodio persistido (activo o vencido): el ciclo
   evalúa el bid y descarta un `Pausar` duplicado. El veto pendiente, el veto
   del dueño y `applying` siguen bloqueando la hoja entera.
4. **Precedencia económica.** Si la pausa económica califica con datos
   completos, la venta reciente no la oculta.
5. **Historia.** La ausencia del marcador en `pausa.proteccion` conserva la
   semántica anterior: el replay de las decisiones viejas no adopta la regla
   nueva. El caso congela `evaluado_el`, la lectura, la ventana de cortes y el
   episodio.
6. **Fuente incompleta sin episodio.** No se inventa un cero ni se abre una
   gracia; la pausa histórica sigue. Con un episodio persistido (activo o
   vencido), la lectura incompleta no lo cancela.

## Verificación

- `tests/test_optimizer_proteccion.py`: 12 casos puros (regresión histórica,
  ancla fija, vencimiento, corrección a cero, ausencia de datos, precedencia
  económica, puja autorizada durante la espera y freeze/replay).
- `tests/test_orbit07_m1.py`: ciclo con venta reciente (no pausa, abre
  episodio), cola que espera sin cuota, vencimiento que aplica y cobra, y puja
  que se mueve durante la espera sin duplicar la pausa.
- `tests/test_apply_schema.py`: tabla append-only, vencimiento por trigger,
  unicidad y permisos.
- La sonda de regresión está en [`antes-despues.json`](antes-despues.json): la
  base pausa; el candidato espera con el ancla fija.

## Límites

- El SELECT de producción con `orbit_read` no pudo ejecutarse: el encargo no
  tiene ese acceso concedido. Queda el SQL concreto en
  `docs/evidencia/orbit-07/m0/consulta-pedidos-recientes.sql`; no se inventaron
  resultados.
- No se despliega, no se enciende la pausa y no se escribe en Amazon: eso sigue
  reservado al dueño.
