# ORBIT 07: estado de ejecución

Actualizado al 2026-11-08 (UTC). El plan que manda es
[`plans/orbit-07-motor-decisiones.md`](../../../plans/orbit-07-motor-decisiones.md).

| Fase | Estado | SHA / candidato | Evidencia | Decisión | Siguiente dependencia |
|---|---|---|---|---|---|
| Documentación | Verificado local | `0ed9636` | plan y evidencia de planificación copiados; manifest con su entrada y `active` conservado; matriz en [`arranque.md`](arranque.md) | Plan seleccionado explícitamente; no se tomó `manifest.active` | CI del commit |
| M0 (lo necesario para M1) | Verificado local; SELECT de producción pendiente de acceso | `f86ed7d` | [`m0/fuentes.md`](m0/fuentes.md), [`m0/contratos-temporales.md`](m0/contratos-temporales.md), [`m0/consulta-pedidos-recientes.sql`](m0/consulta-pedidos-recientes.sql), [`m0/presupuesto-rendimiento.json`](m0/presupuesto-rendimiento.json) | La ventana inmadura D-9..D-1 ya existe en `lecturas_caso`; el ancla usa `metric_date`; la fuente incompleta no se convierte en cero | Ejecutar el SELECT con `orbit_read` cuando exista ese acceso |
| M1 | Verificado local; integración en CI | `4d5ff82` | [`m1/antes-despues.json`](m1/antes-despues.json), [`m1/politica-implementacion.md`](m1/politica-implementacion.md), pruebas `test_optimizer_proteccion.py` y `test_orbit07_m1.py` | Episodio append-only con ancla fija; la pausa económica conserva precedencia; la espera no cobra cuota ni congela las pujas | CI verde del SHA final y revisión independiente |
| M2 | No iniciado | — | — | Se construye después de M1 | M0 de identidad/economía y M1 |
| M3/M4 | Condicionado | — | — | Solo si M0 acredita una diferencia funcional pendiente | M2 y la elección del dueño sobre el gasto de aprendizaje |
| M5/M6 | No iniciado | — | — | Entrega conjunta | M2 y episodios históricos maduros |

## Cómo continuar sin esta conversación

1. Leer `AGENTS.md`, `docs/CONTEXTO.md` y el plan; no reconstruir AUTO-03.
2. Revisar `git log` y este archivo; el candidato de cada entrega vive en su
   commit y en la PR.
3. M1 se verifica con:
   `uv run --frozen python -m pytest -q tests/test_optimizer_proteccion.py tests/test_orbit07_m1.py`
   y con el resto de la cadena de ciclo/cola contra PostgreSQL 16.
4. No desplegar, no escribir en Amazon y no encender la pausa: eso es del
   dueño.
