# Hallazgos a arreglar (revisión de Orbit completo, 2026-11-09)

Código revisado: master `3c841f4`. Copia de solo lectura: `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit-master`.
Reportes de origen (leer el del hallazgo antes de diseñar): `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-review/<letra>-*.md`.
Sondas que reproducen: `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-review/sondas/`. Correr con
`PYTHONPATH=/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit-master /Users/dn/dev/goncloud-Orbit/.venv/bin/python <sonda>`
(las de `F/` y `J/` reciben la ruta de la copia como argumento; `D_*` igual, con `-I`).


"Prod" = lo que el lead midió en producción el 9-nov con lectura. Nadie más consulta producción.

## Grupo ads-decision (qué decide el motor de Ads)

| ID | Sev | Qué pasa | Dónde | Sonda | Prod |
|---|---|---|---|---|---|
| E-S1 | ALTO | La regla de negative niega el texto exacto de una keyword EXACT de su propio ad group. La apaga de hecho con umbrales más bajos que la pausa y sin la protección de venta reciente (M1 de ORBIT 07). La keyword sigue ENABLED. | `app/optimizer/hygiene.py:296-350` (la rama negative no compara contra `keywords_norm`; la rama harvest sí). Reporte E, sospecha 1. | `E_negativo_sobre_keyword_propia.sql` | 14 decisiones así. 2 aplicadas en vivo el 16-sep (2321 "arras de boda": 9 pedidos y 1,094.96 USD en 71 días previos; 2322). 1 en cola `pending_veto` live (2869, keyword sin ventas). No se sabe si los negatives de sep siguen en Amazon. |
| A-H1 | MEDIO | Un día reciente con `orders` NULL deja pasar una pausa por cero pedidos pese a un pedido reciente conocido. | `app/proteccion_venta.py:33-41,190-201`, `app/optimizer/proteccion.py:83-92`. Reporte A. | `A_venta_reciente.py` | 0 filas NULL en 30 días. |
| A-H2 | MEDIO | Una hoja que vendió y dejó de recibir filas cae en el hueco entre ventanas. | Reporte A. | `A_venta_reciente.py` | Tabla `proteccion_venta_reciente` con 0 episodios. |
| A-H3 | MEDIO (solo shadow) | M4 descarta en silencio un día reciente con métrica NULL. | Reporte A. | idem | shadow |
| B-H1 | MEDIO | Con `producto_directo_v1`, una keyword que pierde la meta de su producto salta a ~meta de plataforma (4% → 25%) y tarda 26 ciclos en llegar al 12% de familia. Igual si el ledger lleva 4 días viejo. Cumple la letra de `plans/orbit-07-motor-decisiones.md:237`. **Decisión del dueño.** | Reporte B. | `B_meta_directa.py`, `B_respaldo_familia.py` | 0 keywords bajo meta de producto hoy. |
| C-H1 | MEDIO | Una semana ruidosa bloquea subidas del día x+17 al x+90. | `app/optimizer/economia_cambio.py:106-131`. Reporte C. | `C_*.py` | no medido |
| C-H2 | MEDIO | El freno no actúa si la semana previa tuvo 0 ventas. | Reporte C. | `C_h2_pre_sin_venta.py` | no medido |
| C-H3 | MEDIO | Un episodio contaminado se vuelve "maduro". | Reporte C. | `C_m5_episodio.py` | no medido |
| C-H4 | MEDIO | Una hoja antes pausable puede recibir +15% con pedidos de hace 41 a 90 días. | Reporte C. | `C_m6_politica.py` | no medido |
| D-H1 | MEDIO | La reversa documentada de ORBIT 07 (`--proteccion off`) o `rollback.sh` hace que el siguiente ciclo aplique las pausas en espera de hojas que vendieron hace poco. | `app/cycle.py:1249-1254,3560-3564`. Reporte D. | `D_sonda_motor.py` | 0 pausas con `espera_proteccion`. |
| E-H2 | MEDIO | Revertir a mano una SUBIDA del motor deja `piso_aprendido` por encima del bid actual y bloquea recortes 90 días. Ruta `POST /api/ads-optimizer/reversa/bid`. | `migrations/0067_bids02_campana_ajuste.sql:70-92` (`v_cambio_bid`: toda reversa ok entra como `regreso_del_dueno` con `bid_despues = old_value`). Reporte E. | `E_piso_aprendido_subida.py` | 9 reversas manuales, todas de recortes. |

## Grupo ads-escritura (cómo se aplica a Amazon y qué se acepta de entrada)

| ID | Sev | Qué pasa | Dónde | Sonda | Prod |
|---|---|---|---|---|---|
| I-H1 | ALTO | Settings acepta un cap diario >= 2,147,483,648. La fase de aplicar de esa plataforma falla en cada ciclo. | `app/config_write.py:487-497`, `app/apply.py:330-348,391-401`. Reporte I. | `I_cap_gigante.py` | Caps en 1,000,000 (no dispara; tampoco limita nada). |
| E-H1 = F-H2 | MEDIO | La guarda de "estructura vieja" de 48 h lee `max(synced_at)`, que el aplicador también escribe. Una escritura del motor la hace ver fresca. | `app/optimizer/windows.py:358,589`; `app/apply.py:647`, `app/apply_cola.py:309`, `app/apply_producto.py:65`, `app/apply_harvest_reconciliacion.py:120`. Reportes E y F. | `E_guarda_synced_at.py` | max = mediana = 2026-11-09 06:45:55 en MX y US (sana). |
| E-H3 | MEDIO | Un intento de bid sin sellar (error ambiguo de Amazon) deja que el siguiente ciclo recorte otra vez la misma keyword (−44% en ~24 h), porque el ciclo decide antes de reconciliar. Leído, no reproducido. | `app/cycle.py:3760` contra `:3501-3502`. Reporte E. | no hay | 0 intentos sin sellar. |
| F-H1 | MEDIO | `reconcilia_bids` reenvía el bid viejo sin mirar si la meta está en shadow/off ni si hay un bid más nuevo. | `app/apply.py:705-822`. Reporte F. | `F/F1_reconcilia_bids_ignora_goal.py` | 0 intentos sin sellar. |
| I-H2 | MEDIO | «PAUSAR GRUPO» sobre un lote de impulso hace fallar `impulso-vigia` con 409 cada día y bloquea re-impulsar. | Reporte I. | `I_vigia_desarmado.py` | no medido |
| I-H4 | MEDIO | No hay tope superior para dinero tecleado por una persona (presupuestos, cap de impulso, techo de bid, meta manual). | Reporte I. | `I_entradas_dinero.py` (no recorrida por el lead) | no medido |

## Grupo precios-insumos (motor de precios y los datos que lo alimentan)

| ID | Sev | Qué pasa | Dónde | Sonda | Prod |
|---|---|---|---|---|---|
| G-H1 | ALTO | La retención del freno de precios (fusible / insumo sistémico) se suelta sola al día siguiente sin «Soltar». | `app/precio/corrida.py:953-960,1060-1100,1146-1158`, `app/precio/compuerta.py:127-141`. Reporte G. | `G_fusible_dia2.py` | 0 retenciones en 30 días. `precio_modo_global` pasó a shadow el 9-nov 16:09 UTC. |
| H-H1 | ALTO | Un costo corregido dos veces el mismo día UTC (con la corrida de 08:15 en medio), o historia editada/borrada, congela el costo de ese SKU para siempre (alimenta la meta de ACoS por producto, live). Además cada corrida de costos queda con `rows_skipped >= 1`, todos los productos quedan `costo_desactualizado` y precios no evalúa nada en las 3 plataformas, sin aviso. | `app/costs.py:440-462,522-540`, `app/estimacion_insumos.py:452-482,518-528`. Reporte H. | `H/h1_costo_congelado.py` | 15 corridas, 0 saltos en 14 días. |
| G-H2 | MEDIO | Las publicaciones de MeLi con variaciones nunca reciben escritura de precio. | `app/precio/cambios.py:173-184`, `app/meli/precio_mercado.py:605-611`. Reporte G. | `G_meli_variantes.py` | no medido |
| G-H3 | MEDIO | El vigía cuenta ISR y `fee_variable` como deriva de insumo y marca "insumo sistémico" falso. | Reporte G. | `G_deriva_falsa.py` | no medido |
| H-H4 | MEDIO | Re-mapear un ASIN (NH- contra SET-, productos distintos) deja las ventas pasadas en el producto anterior para siempre (ledger append-only). | Reporte H. | `H/h3_remapeo_ledger.py` | no medido |
| J-H3 | MEDIO | Si el bridge de US termina después de ~13:02 UTC, la corrida de precios de 13:10 decide con ofertas viejas, no vuelve a decidir ese día y sale con éxito. | `app/estimacion_insumos.py:376-379`, `app/estimacion_ingest.py:162-181`. Reporte J. | `J/J4_estimacion_vs_precio_1310.py` | `amazon_us` evalúa 0 de 99 desde el 2-nov por `oferta_desactualizada`. #561 (espera al bridge hasta 20 min + aviso) desplegado el 9-nov 21:28 UTC, sin probar todavía. |

## Grupo operacion (horarios, avisos, despliegue)

| ID | Sev | Qué pasa | Dónde | Sonda | Prod |
|---|---|---|---|---|---|
| H-H2 = I-H3 = J-H2 | MEDIO | El ciclo (08:40/08:41) y `impulso-vigia` (08:20) corren una vez al día. Si la ingesta de 07:10 llega tarde imprimen "sin métricas de ayer: espera al siguiente horario", salen con éxito y no reintentan. `cron-avisa` no dispara con salida 0. `plans/ingesta-01.md:52-62,114` prometía reintentos a las 10:10/13:10/16:10. | `app/cli.py:108-115`, `app/metricas_listas.py:26-42`. Reportes H, I, J. | `J/J1_ciclo_un_solo_horario.py` | 15 ciclos MX y 15 US completos en 14 días. |
| H-H3 | MEDIO | La primera falla de SP-API (5xx/red) queda sin aviso si la repetición se salta después de las 06:15 y el vigilante cuenta `ok=false` como presente. | Reporte H. | `H/h2_spapi_primer_fallo_mudo.py` | 0 corridas fallidas en 14 días. |
| J-H1 | MEDIO | El instalador de cron de `docs/DEPLOY.md:1200-1212` reinstala las líneas sin `cron-avisa` (usa `grep -v "app.cli cycle"` y vuelve a poner líneas planas). En prod hoy ciclo, impulso-vigia y precios SÍ están envueltos; `ingest diario` x3 y `spapi-diario.sh` no. `cron-avisa` vive solo en el host (`/usr/local/bin/cron-avisa`), no en el repo. | Reporte J. | `J/J5_instalador_quita_cron_avisa.py` | envueltos hoy |
| J-H4 | MEDIO | El deploy revisa las guardas una vez y recrea el contenedor minutos después. `revierte` y `rollback.sh` no revisan. Un ciclo que arranca en esa ventana muere a media escritura. Leído, no simulado. | `tools/despliegue/desplegar.sh:34,106,201`, `tools/despliegue/comun.sh:48,182,281`. Reporte J. | no hay | no medido |
