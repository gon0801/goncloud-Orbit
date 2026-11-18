# Un dueño y una comprobación por hallazgo

Reemplaza el reparto tentativo de las candidaturas e inventario, **no** los hechos
ni hashes del diagnóstico `revision/matriz.tsv`. Todos los siguientes siguen por
implementar al redactar el plan; asignarlos no significa corregirlos.

| ID | Impacto | Único dueño | Cierre observable |
|---|---|---|---|
| 02 | Duplicar creación por asumir ausencia | 04 B-C ampliado | Contenedor presente, lista válida y fin completo; cero POST ante unknown |
| 03 | Archivar keyword ajena en reversa | 05 B | ID propio inmutable, ajena/adoptada nunca hereda autoría |
| 04 | Enviar retiro/impulso de otra selección | 04 W-P nuevo | Revisión de previa/payload/confirmación coherente, incluidas respuestas tardías |
| 05 | Publicar subtotal corrupto | 03 D-G reforzado | Clave contaminada no reaparece ni produce agregado válido |
| 06 | Dar bajas a publicaciones por scan parcial | 03 D-M nuevo | Solo cobertura completa certifica ausencia |
| 07 | Cotización MeLi inutilizable o incompleta | 03 P-G nuevo | Fecha y árbol de fees conservados en fresca/cache |
| 10 | Negative confirmado sin reversa | 05 B | Reversa manual por identidad/procedencia inequívocas, una sola vez |
| 11 | Duplicar keyword al reponer | 05 C | Intención antes del POST, ACK durable y recuperación solo lectora |
| 12 | Meta congelada con libro vencido | 05 F | Base29/28.5/28 independiente de overlay y apply |
| 13 | Rollback de todo el ciclo por sello temporal | 05 G | Lote mixto confirma, madurez/observación/replay se conservan |
| 14 | Éxito y contadores de escrituras revertidas | 03 D-M nuevo | Delta solo después del savepoint; resultado externo después del commit |
| 15 | ACoS engañoso con gasto desconocido | 04 W-E ampliado | Cobertura coherente de numerador/denominador, ratio None si incompleto |
| 16 | «Sin ventas» cuando faltan datos | 04 W-D.2/W-D.4 | Mensaje distingue dato desconocido de cero |
| S1 | Persistir clave devuelta en error externo | 03 D-N nuevo | Redacción antes del límite durable; dos variantes del proveedor |

Los comandos exactos están en [ACEPTACION.md](ACEPTACION.md). La prueba S1 usa una
clave ficticia registrada: condición reproducida, no fuga real observada.

## Controles ya resueltos

| ID/control | Evidencia vigente | Uso futuro |
|---|---|---|
| 01 | 01K: reconciliación bid solo lee | Mantener durante recuperación A/B/H |
| 08 | 01R: unidades reales/variantes en precio y reversa | Control de03 precios y cierre global |
| 09 | `de895d18`: Jev cierra su TX implícita y conserva la ajena | Control de03 D-N, no otra reescritura de transacción |
| D2 | 01E: fusible no libera al cambiar de día | Control de03 P-D y cierre global |
| Web1, Ajustes manual | 01X: banda canónica en `config_write.py`; prueba corrida en esta entrega | Control de05 I; Goals sigue04 W-I |
| D1 | Contraste entre fórmula prior y frase «solo frena» | Nota de contrato, no cambio de política ni bloqueo nuevo |

## Legado del 12-nov: asignación completa sin doble conteo

La matriz histórica [HALLAZGOS.md](../arreglos-2026-11-12/HALLAZGOS.md) conserva las
53 fallas, su origen y las decisiones del dueño. La falla ciclo5 duplica política1;
política3 fue descartada con datos. No sumar53+14 como si fueran independientes.

| Área histórica | Dueños vigentes de todas sus filas |
|---|---|
| Política1/2/4; ciclo5 duplicado | 02 B/A/C, integrados; política3 descartada |
| Ciclo1/2/3 | 05 A |
| Ciclo4 | TEMPORADA B.2; 05 BASE/A comprueba, no reimplementa |
| Apply1 | TEMPORADA E lectura previa; 05 B reversa/comprobación posterior |
| Apply2 | 05 D |
| Apply3 | 04 B-A (archivo anuncios); no confundir con reposición keyword TN11 |
| Apply4/8/9 | 04 B-B/B-D según matriz histórica |
| Apply5 | 05 E |
| Apply6 | 05 H:150 ordinarias por origen, exención entrada/regreso |
| Apply7/11 | 05 B/C, mismos TN10/11 |
| Apply10 | 04 B-C, mismo TN02 ampliado |
| Apply12 | 02 B redondeo integrado; 04 B-B ACK por ítem |
| Ingesta1/2/3/4/5 | 03 D-E/D-F/D-G/D-I/D-H respectivamente |
| Precio1/5 | 03 P-C |
| Precio2 | 03 P-B; fecha/detalle se completa en P-G |
| Precio3/4 | 03 P-D, sin reabrir D2 ya resuelto |
| Fábrica1/5 | 04 W-M |
| Fábrica2 | 04 W-K, aviso conforme decisión de conservar semillas |
| Fábrica3/4 | 04 W-L/W-J |
| Estimación1 | 02 D integrado; no duplicar comisión otra vez |
| Estimación2/3/4 | 03 P-F/D-J/D-J |
| Estimación5/7 | 03 D-K |
| Estimación6/8/9 | 03 D-L |
| Web1 | Fábrica/Ajustes01X resueltos; Goals04 W-I; 05 I solo conserva control |
| Web2/3/4/5/6/7/8/9/10 | 04 W-H/W-D/W-C/W-E/W-E/W-G/W-F; 05 I; 04 W-D |

Decisiones adicionales: retirar código sin uso fuera del ciclo es03 D-A/B/C/P-A y
04 W-A/B/B-A; dentro del ciclo es05 J. Sospecha fiscal es05 K con adjudicación
externa, no tasa asumida. Variantes sin existencias siguen como decidió el dueño;
no se agregan al plan. El límite de50 precios diarios corresponde03 P-D; no es el
de150 pujas ni permiso para activar precios. No se toca el crontab de root para
borrar corridas de estimación; conserva su exclusión original.

## Contratos que se cruzan y deben comprobarse juntos

| Cruce | Criterio de cierre |
|---|---|
| 03 D-E → 04 W-C/D | Mismo día cerrado por plataforma y misma población |
| 03 D-M/D-L | Reputación y catálogo consumen el mismo scan completo/parcial |
| 03 P-B/F/G →05 K | Error contenido, target calculado y cotización íntegra antes de conciliar |
| 04 B-C →05 B/C | Un lector estricto en creación, conciliación y reversa |
| 04 W-M/N/P →TEMP G | Previa, presupuesto y confirmación usan el mismo pedido vigente |
| TEMP B/E/F →05 A/B/H | Conciliación predecisión, bid previo, episodios/reversa y cupo de origen |
| TEMP I/D →05 F/G | Meta base separada de efectiva, sello/madurez/replay coherentes |

Z adjunta SHA, prueba y estado final a cada fila. Los seguimientos operativos y
planes futuros están en [la cola consolidada](../../../plans/ORDEN-IMPLEMENTACION.md);
no equivalen a implementaciones nuevas ni quedan cerrados por aprobar este código.
