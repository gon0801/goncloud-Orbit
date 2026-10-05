# 2.3 — cierre tras el primer ciclo posterior al despliegue (2026-10-05)

Despliegue `20261004-2253`, código `25cebe7`. El checklist de solo lectura se
corrió dos veces:

- Justo después de desplegar (`checklist-salida.txt`): 0 fallas, ciclo
  posterior pendiente.
- Después del primer ciclo (`checklist-post-ciclo.txt`, 2026-10-05 14:46 UTC):
  ciclos 102 (US) y 103 (MX) en `done`, ninguno fallido ni colgado.

La segunda corrida sale con 1 por dos líneas que ya no describen un daño del
despliegue. Las dos comprobaban "Jev sigue sin usarse", y eso fue cierto solo
hasta que el dueño empezó a usarlo:

| Línea | Esperaba | Hoy | Por qué |
| --- | --- | --- | --- |
| Clave TypeSafe en el contenedor | ausente | presente | El dueño la puso para el piloto 3.1 y el prototipo de JEV ADS 02. |
| Filas Jev (fichas, revocaciones, revisiones, eventos) | 0, 0, 0, 0 | 868, 0, 24, 11272 | Fichas de la tarea 0.2 de JEV ADS 02, piloto 3.1 y prototipo. Todo posterior al despliegue y corrido por el dueño. |

Lo que el despliegue debía dejar intacto sigue intacto: `/health` y `/cortes`
responden 200, los permisos pasan, y `app.cycle`, `app.apply_cola` y
`app.apply_harvest` no cargan ningún módulo `app.jev_*`.

Decisión del lead: la fila 2.3 se cierra. No se editó el checklist para que
saliera en verde.
