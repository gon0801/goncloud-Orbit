# ORBIT 07: estado de ejecución

Actualizado al 2026-11-08 (UTC). Implementacion reunida en
[PR #547](https://github.com/gon0801/goncloud-Orbit/pull/547). Esa PR conserva
el veredicto independiente, CI y el recibo de integracion/despliegue cuando
se completen. El usuario autorizo integrar, desplegar y limpiar el 8 de
noviembre; se conservan los modos y limites de pausa existentes.

El plan que manda es
[`plans/orbit-07-motor-decisiones.md`](../../../plans/orbit-07-motor-decisiones.md).

| Fase | Estado | SHA / candidato | Evidencia | Decisión | Siguiente dependencia |
|---|---|---|---|---|---|
| Documentación | Verificado local | `0ed9636` | plan y evidencia de planificación copiados; manifest con su entrada y `active` conservado; matriz en [`arranque.md`](arranque.md) | Plan seleccionado explícitamente; no se tomó `manifest.active` | CI del commit |
| M0 (lo necesario para M1) | Verificado local y consulta real por hoja ejecutada | `f86ed7d` | [`m0/fuentes.md`](m0/fuentes.md), [`m0/contratos-temporales.md`](m0/contratos-temporales.md), [`m0/consulta-pedidos-recientes.sql`](m0/consulta-pedidos-recientes.sql), [`m0/produccion-2026-11-08.json`](m0/produccion-2026-11-08.json), [`m0/presupuesto-rendimiento.json`](m0/presupuesto-rendimiento.json), [`m0/rendimiento-comparado.json`](m0/rendimiento-comparado.json), [`m0/rendimiento-apagado.json`](m0/rendimiento-apagado.json) | La ventana inmadura D-9..D-1 ya existe en `lecturas_caso`; el ancla usa `metric_date`; la fuente incompleta no se convierte en cero; la muestra de Astra (2 hojas por mercado con pedido reciente y cero maduros en la ventana fija) acredita el disparador, no pausas elegibles; rendimiento medido con base y candidato alternados sobre la misma plantilla fija (`tools/bench_orbit07_proteccion.py`) | Consulta completada: [muestra por hoja](m0/produccion-por-hoja-2026-11-08.json), 8 hojas US y 13 MX con pedidos recientes; dos por mercado con cero maduros, sin afirmar elegibilidad de pausa |
| M1 | Verificado local; CI del candidato | `4d5ff82` | [`m1/antes-despues.json`](m1/antes-despues.json), [`m1/politica-implementacion.md`](m1/politica-implementacion.md), pruebas `test_optimizer_proteccion.py` y `test_orbit07_m1.py` (incluye la espera vencida, la reversa por `ads_proteccion_venta` y la sombra sin target que aplica la proteccion), `m0/produccion-2026-11-08.json` | Episodio append-only con ancla fija; la pausa económica conserva precedencia; la espera no cobra cuota ni congela las pujas; la regla se apaga por setting sin borrar episodios | Revisión independiente y CI verde del SHA final |
| M2 | Implementado; integracion y produccion en PR #547 | `c4121c5` + cambio M2 | [`m2/politica-implementacion.md`](m2/politica-implementacion.md), [`m2/economias.json`](m2/economias.json), [`m2/sonda_economias.py`](m2/sonda_economias.py), [`m2/rendimiento-comparado.json`](m2/rendimiento-comparado.json), `tests/test_orbit07_m2.py`, `tests/test_productos_meta.py` | La meta del producto se aplica directa (excepción a la rampa); al perder elegibilidad el ciclo resuelve el respaldo completo; la cola revalida con la evaluación fresca del ciclo ejecutor y conserva el 4 % solo si la economía sigue siendo elegible; el setting ads_producto_economia_<plat> (ausente/auto03 por omision) elige la version: auto03 conserva rampa, cola congelada y descarte por alcance; producto_directo_v1 activa M2; el caso juzga con el equilibrio del producto y la cola usa el mismo lector familiar del ciclo | Revisión independiente y CI verde del SHA final |
| M3/M4 | M3 omitido; M4 implementado; integracion y produccion en PR #547 | `4953bc1` + cambio M4 | [`m3m4/comparacion-auto03.md`](m3m4/comparacion-auto03.md), [`m3m4/aprendizaje.json`](m3m4/aprendizaje.json), [`m3m4/sonda_aprendizaje.py`](m3m4/sonda_aprendizaje.py), [`m3m4/regresion-venta-reciente.txt`](m3m4/regresion-venta-reciente.txt), [`m3m4/regresion-venta-reciente-verde.txt`](m3m4/regresion-venta-reciente-verde.txt), [`m3m4/regresion-frescura-venta.txt`](m3m4/regresion-frescura-venta.txt), [`m3m4/regresion-frescura-venta-verde.txt`](m3m4/regresion-frescura-venta-verde.txt) | M3 se omite: el agregado de AUTO-03 ya colapsa por fecha y no duplica, y el freno de subidas no tiene efecto propio (intersección candidato×exclusivo 0 en MX y US; I.5 ya omite la subida con la pausa live en vuelo). M4 se implementa con el contrato temporal de producto: la pausa espera una venta en `[D-30, D-1]` con ancla fija y madurez de 30 días (libera al día 31), lee el reporte hasta D-1, al decidir y al revalidar. La muestra historica E.4 solo observa hasta el 2 de noviembre: no demuestra ausencia de ventas del 3 al 6 ni una pausa elegible del producto 356; el contrato completo se verifica con las regresiones de frescura | Revisión independiente y CI verde del SHA final |
| M5/M6 | Implementado; integracion y produccion en PR #547 | `4e0e78d` + cambio M5/M6 | [`m5/contrato-episodio.md`](m5/contrato-episodio.md), [`m5/episodios.json`](m5/episodios.json), [`m5/sonda_episodios.py`](m5/sonda_episodios.py), [`m5/consulta-episodios.sql`](m5/consulta-episodios.sql), [`m6/politica-implementacion.md`](m6/politica-implementacion.md), [`m6/rendimiento-comparado.json`](m6/rendimiento-comparado.json), `tests/test_orbit07_m5m6.py` | Episodio congelado en `inputs.caso`; recorte repetido con ventas conservadas y ACoS mejor; subida frenada si el tráfico adicional no compensa; sin episodio maduro aplicable, R14-R17; `ads_bid_economia_<plat>` enciende y apaga sin reescribir historia | Revisión independiente, CI verde e integración/activación del dueño |

## Cómo continuar sin esta conversación

1. Leer `AGENTS.md`, `docs/CONTEXTO.md` y el plan; no reconstruir AUTO-03.
2. Revisar `git log` y este archivo; el candidato de cada entrega vive en su
   commit y en la PR.
3. M1 se verifica con:
   `uv run --frozen python -m pytest -q tests/test_optimizer_proteccion.py tests/test_orbit07_m1.py`
   y con el resto de la cadena de ciclo/cola contra PostgreSQL 16.
4. Consultar el recibo operativo de la PR #547 antes de repetir el despliegue.
   La autorizacion de cierre conserva las pausas por producto en practica y
   sus limites; no autoriza cambiar ese modo.
