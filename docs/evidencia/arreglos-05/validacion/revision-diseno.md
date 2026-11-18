# Revisión final del diseño ARREGLOS 05

**Dictamen final: PLAN APTO. Cero bloqueos confirmados; N1 y N2 resueltas; sin notas nuevas.** No acredita implementación, CI, despliegue ni código de la aplicación arreglado. La ejecución sigue condicionada a BASE y al cierre de 03/04/TEMPORADA.

Actualización del 15-nov-2026 en `/Users/dn/dev/goncloud-Orbit-arreglos-05-plan`. HEAD observado: `ff03ed61f7a847ab190b1695c2efe8d785baa364`. Los hashes finales identifican los documentos y el lanzador revisados; HEAD por sí solo no identifica estos cambios.

## Alcance conservado

La revisión inicial aplicó los criterios de `pstack:architect` y `pstack:arena` al diseño de propietarios existentes frente al coordinador transversal, sin delegación. No encontró bloqueos en origen/ejecutor, claims, ledger de cupo150, identidad de reversas, vigencia de decisiones, migraciones ni asignación del cierre 14TN/legado/postTEMPORADA.

Esta actualización revisó únicamente N1/N2, la aclaración temporal G, `comprobar.sh` y sus instrucciones de aceptación/Chromium. Reutiliza el dictamen anterior para los contratos no modificados. El plan mantiene sus 13 bloques; no se revisó código de la aplicación ni se amplió a 03/04/orden.

Todas las rutas siguientes son relativas al directorio revisado.

## Bloqueos

Ninguno sustentado en los deltas. No se exige que las APIs, migraciones o regresiones de implementación existan hoy.

## Notas anteriores resueltas

- **N1 — resuelta.** `plans/arreglos-05.md:77` distingue la prohibición de nuevos envíos/sellos del owner vencido del HTTP ya enviado que puede terminar. Incluye respuesta retenida y conciliación sin repetir. Desaparece la exigencia aparente de cancelar efectos remotos mediante un lease local.
- **N2 — resuelta.** `plans/arreglos-05.md:218–221` permite Z cuando K está evaluado/documentado pero pendiente de fuente. K permanece abierto y no hay pase global; no se fuerza un cierre ficticio para publicar la evidencia.

No quedan correcciones mínimas pendientes de esta revisión.

## Deltas adicionales revisados

| Delta | Evidencia y resultado |
|---|---|
| Sello temporal G | `docs/evidencia/arreglos-05/DISENO.md:206–211`: el límite corresponde a `decision.data_observed_at <= decided_at`; `observed_at` real permanece intacto en inputs/métricas aunque sea posterior. Se mantienen trigger, madurez y ventanas. La aclaración elimina la ambigüedad del nombre de campo sin introducir otro contrato. |
| Shell nuevo | `plans/arreglos-05.md:35–40`, `docs/evidencia/arreglos-05/ACEPTACION.md:55–62`, `docs/evidencia/arreglos-05/comprobar.sh:17–27,44–73`: raíz calculada desde el script, variables exportadas y selección T/N/ALL coherentes. Argumentos y filtros se pasan entre comillas; `exec` conserva el resultado de pytest. No depende de las funciones de otra sesión. |
| Prerrequisitos del lanzador | `comprobar.sh:12–15,29–42`: help sale antes de exigir entorno; comprueba Python3.12 y la tupla de laboratorio del DSN sin abrir conexión. La receta conserva la obligación de verificar PG16, propiedad del laboratorio y ausencia de bases ajenas; el lanzador no instala servicios. |
| ALL y evidencia | `comprobar.sh:62–70`: exige destino XML explícito y mantiene el orden writes/ingest/optimizer/pricing/ui del runner revisado. `ACEPTACION.md:111–124` conserva destino fuera de originales y exige revisar resultados; colección o skips no equivalen a pase. |
| Chromium en CI | `ACEPTACION.md:133–137`: promover casos de navegador exige instalar y ejecutar Chromium en CI, sin skip por ausencia. Conservarlos en el arnés externo exige adjuntar su ejecución como aceptación obligatoria. Ambas opciones mantienen la evidencia de navegador y obligan a registrar la elegida. |

## Último ajuste de metadatos: revisado y cerrado

Se releyó completo `plans/arreglos-05.md` antes de fijar su hash final. Los metadatos `despliegue` y `prueba-real` de A–J corresponden a las letras `N a` a `N j` del lanzador revisado. Se mantienen 13 bloques y el mismo orden de dependencias; no hay cambio de arquitectura ni código de aplicación.

`plans/arreglos-05.md:48–52` declara expresamente que la prueba de laboratorio no acredita despliegue y exige recibo productivo separado. Los nuevos metadatos no permiten afirmar una validación productiva ni una activación realizada. No se encontraron contradicciones nuevas.

La validación local del DAG fue reportada por el usuario. La CLI del motor no está disponible localmente: **no se afirma que el motor validó el plan**. No se ejecutaron herramientas de despliegue ni se amplió la revisión a ellas.

## Comprobaciones y límites

Lectura estática de los deltas y del script completo; comprobación de hashes antes de escribir este informe. Se reutiliza lo reportado por el usuario: `bash -n`, help, colección de los dos casos02 y rechazo de DSN externo antes de conectar, sin PG. No se presentan esas comprobaciones como ejecutadas por este revisor ni como evidencia de comportamiento de la aplicación.

Sin ediciones del repositorio, suite, red, producción ni delegación. Solo se actualizó este informe fuera del repositorio. Pre-commit queda pendiente de la corrida anunciada por el usuario tras terminar los agentes; no se afirma verde. Los gates de integración siguen vigentes.

En la revisión inicial no existía `graphify-out/graph.json`. Se conserva la regla para futuras exploraciones: si existe, ejecutar `graphify query` antes de buscar o leer código y transmitir esa condición a cualquier agente que explore. Esta actualización no requirió explorar la aplicación.

## Hashes finales

SHA-256 de los archivos revisados; se confirmó que no cambiaron entre lectura y emisión:

```text
93ba383da0682ffcb92a757ebb96b51089f82a4776e95f8449b1439d36f447c6  docs/evidencia/arreglos-05/DISENO.md
a42297107b4d52c49c4d4a1e7fe4bb320ffafa38b81ff70785c24947227b0673  docs/evidencia/arreglos-05/COBERTURA.md
216e7aa914f04765965da83e6f87584227120b796d42f7ec4024eafa2242f029  docs/evidencia/arreglos-05/ACEPTACION.md
0ffd436297918b76c5e614a547ef45a18647c08980d2a5920b0e84ae559f4ed3  docs/evidencia/arreglos-05/comprobar.sh
fe0acd6dd0630411369e0675aff3200b2bbb5db8bdcdf78213f5c68fdd83b68d  plans/arreglos-05.md
```
