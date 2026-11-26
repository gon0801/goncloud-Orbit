# Orden de implementación de Orbit

Actualizado el **25-nov-2026** tras el cierre final del bloque Z de ARREGLOS 05 sobre `c16c8c55`. La cola completa **ARREGLOS 04 → TEMPORADA 01 → ARREGLOS 05** quedó **cerrada, integrada y desplegada**: 04 desplegado en `f5dc86d5`, TEMPORADA desplegada en `f07ab667`, y 05 cerrado completo (`BASE`, `A–K` y `Z`) en `5caa67cd` / `c16c8c55` con aceptación integrada **31/31**, regresiones **N a–k (438/438)** y migración `0100` (S4 Amazon US) aplicada en producción. **No quedan planes de implementación pendientes en esta cola.** El [ROADMAP](ROADMAP.md) conserva la historia y los seguimientos operativos.

`manifest.active = arreglos-05` marca el último plan cerrado de la cola; no iniciar otro plan sin nueva autorización y brief.

Fuentes: [inventario completo](../docs/evidencia/arreglos-05/inventario/inventario.md), [inventario JSON](../docs/evidencia/arreglos-05/inventario/inventario.json) y [matriz final de cobertura](../docs/evidencia/arreglos-05/COBERTURA.md). El inventario conserva la foto de origen; las asignaciones finales de esta cola y de COBERTURA prevalecen sobre sus propuestas anteriores. Esos artefactos y el plan ARREGLOS 05 se incorporan en la misma entrega documental; sus enlaces no acreditan implementación.

## Base integrada: no reejecutar

El lead comprobó en solo lectura que los **202 archivos de runtime** de `app/` (`.py`, `.js`, `.css`, `.html`) coinciden con el snapshot `ff03ed61`: [huella local](../docs/evidencia/arreglos-05/base/huella-local.json) y [huella productiva](../docs/evidencia/arreglos-05/base/huella-prod.json), SHA256 conjunto `e13a6f0f34002079079fddd623952ad8f1d3b84c41c9d917efcf781e3a597967`.

| Base | Integración comprobada | Resto separado de la implementación |
|---|---|---|
| ARREGLOS 01 | A-Z integrados, desde `3524b41b` hasta X `976df4dc`; Y `ac539365`, Z `f53ad3f8`, S `de895d18`. | Consolidar actas y sondas pendientes; A, C y U ya tienen evidencia operativa propia. No repetir reversas o encendidos documentados. |
| ARREGLOS 02 | A `dabfac13`, D `9f0181f6`, B `5b73e2ed`, C `8d5a84c9`. | 0086 existe en Git; acreditar su aplicación con esquema/sonda y resultado D.4. Replay B/C documenta `distintas 0`. El script de D.4 no acredita ejecución y sus referencias vencen tras tres días. |
| [ARREGLOS 03](arreglos-03.md) | 20 bloques integrados. Despliegue verificado en `819fe1ff`, con 220 archivos cotejados y cinco migraciones aplicadas. | Las dos observaciones programadas para el 19-nov a las 08:45 UTC siguen por separado. 04 puede arrancar. |
| ORBIT 07 | `7cf7c9ab`: M1/M2/M4/M5/M6 integrados; M3 omitido con justificación. | Actas/configuración separadas; checks antiguos no reabren el desarrollo. La activación posterior de v2 está documentada en ARREGLOS 01 U. |
| CLARIDAD 01 | Entregas `c44bd995`, `4be5081c`, `0e5abaf0`, manual y evidencia. | Consolidar recibos residuales sin volver a lanzar la implementación. |
| DISEÑOS 01 | A `19c2c7b3`, B `f15ffc77`, C `c6cb744f`. | Runtime comprobado; comparaciones locales/copia RO no sustituyen actas productivas A.4/B.4/C.3. |
| DECISIONES PRODUCTO 01 / AUTO-03 | A-I integrados, cierre `f86ed7dc`. | Pausa lista para vivo, en práctica por decisión documentada. Su encendido por el dueño es una operación, no código por construir. |

La igualdad del runtime **no prueba** por sí sola todas las migraciones aplicadas, crons, configuración ni efectos en Amazon/MeLi. Revisar las actas residuales en el bloque BASE del plan que las necesita; no usar su ausencia documental para reiniciar planes entregados. Las fotografías históricas de modos no sustituyen una lectura vigente.

## Todos los planes de la cola (cerrados al 25-nov-2026)

| Orden | Plan | Estado final y evidencia |
|---|---|---|
| 1 | [ARREGLOS 04](arreglos-04.md) | **CERRADO y desplegado (`f5dc86d5`).** Pantallas, fábrica y bloqueos; **B-C** cerró **02**, **W-P** cerró **04**, **W-E** cerró **15** y **W-D** cerró **16**. |
| 2 | [TEMPORADA 01](temporada-01.md) | **CERRADO y desplegado (`f07ab667`).** Bloques I → A → B → C → D → P → E → F → G → H y olas OLA1–OLA3. |
| 3 | [ARREGLOS 05](arreglos-05.md) | **CERRADO y desplegado (`5caa67cd` / `c16c8c55`).** `BASE`, `A–K` y `Z` completos: 14 hallazgos + controles verdes **31/31** en `c16c8c55`, regresiones **N a–k (438/438)**, mapa UI verificado, K-US adjudicado con SP-API Finances v2024 (`164/164` ítems USD: `15% + 16% IVA = 17.40%`), migración `0100` aplicada en producción y 99 metas US re-ancladas. Evidencia en `docs/evidencia/arreglos-05/Z/`. |

No quedan otros planes de implementación autorizada fuera de esta lista en el inventario. Los briefs y guías acompañan sus planes padres; no suman entregas independientes. Los conteos y dependencias internas se mantienen en cada plan, sin duplicarlos aquí.

### Propiedad final de los 14 hallazgos

Los IDs son los de la revalidación sobre `ff03ed61`, **no** los números por área de la revisión del 12-nov.

| ID | Dueño único | Resultado que debe comprobarse |
|---|---|---|
| 02 | ARREGLOS 04 B-C | Lista truncada, tokens inválidos/repetidos o contenedor ausente no acreditan ausencia: no repetir POST ni borrar por una lectura incompleta. |
| 03 | ARREGLOS 05 B | Reversa solo archiva la keyword cuya creación propia esté acreditada. |
| 04 | ARREGLOS 04 W-P | Retiro e impulso invalidan previa/selección anterior, confirmación y payload ante fallo o respuesta tardía. |
| 05 | ARREGLOS 03 D-G | Fuente corrupta no publica subtotal completo ni recrea la clave invalidada. |
| 06 | ARREGLOS 03 D-M | Scan incompleto de MeLi no desactiva publicaciones ausentes. |
| 07 | ARREGLOS 03 P-G | Cotización conserva fecha y desglose, fresca y persistida, hasta la corrida. |
| 10 | ARREGLOS 05 B | Negative reconciliado conserva identidad durable para una reversa ejecutable. |
| 11 | ARREGLOS 05 C | Reposición registra intención antes del HTTP y no repite POST tras ACK y readback ambiguo. |
| 12 | ARREGLOS 05 F | Target de respaldo avanza desde la última meta base live comprometida en TX3, independiente de apply y del overlay estacional, con ledger rancio; no se queda en 29,29,29 ni inventa margen fresco. |
| 13 | ARREGLOS 05 G | Reobservación de término maduro no aborta el lote por un sello temporal incoherente. |
| 14 | ARREGLOS 03 D-M | Catálogo cuenta solo escrituras que sobreviven al savepoint. |
| 15 | ARREGLOS 04 W-E | Gasto desconocido no produce ACoS numérico; cobertura y madurez se comprueban por separado. |
| 16 | ARREGLOS 04 W-D | Ventas conocidas con ACoS incompleto se muestran como datos faltantes, no como ausencia de ventas. |
| S1 | ARREGLOS 03 D-N | Errores de proveedor/model/choice se redactan antes de persistir en Jev. Caso condicional, sin filtración real demostrada. |

**05 Z** acepta los 14 sobre el commit combinado y conserva controles **01/08/09/D2**, ya corregidos. No duplica los arreglos de 03/04. D1 sigue como nota de contrato, no bloqueo. La [matriz final](../docs/evidencia/arreglos-05/COBERTURA.md) enlaza cada caso y su evidencia.

ARREGLOS 05 H aplica el tope real por ciclo elegido, sin contar entrada/regreso de temporada; no confundirlo con cuota diaria. J conserva replay, apagadores y reversas vigentes: pausa de producto en práctica no es modo muerto. K resuelve fees con evidencia de la fuente y conserva la decisión de que el dueño no paga más impuestos que los retenidos; la sospecha de IVA de comisiones no se convierte en fallo confirmado por aparecer en el plan.

### Por qué la cola es serial

| Cruce | Archivos/contrato compartidos | Orden elegido |
|---|---|---|
| 03 D-E y 04 W-C/W-D | `dia_cerrado.py`, `dashboard_resumen.py`, `api_dashboard.py` | Primero una fuente de día cerrado; después sus consumidores. |
| 03 y 04 | `cli.py`, `notifica.py`, `ui.py`, cortes, crontab y despliegue | Integrar 03 antes de editar esos consumidores en 04. |
| 04 y TEMPORADA | Presupuesto/confirmación, `api_write.py`, `campana_ajustes.py`, `dinero.js` | G de TEMPORADA consume W-N ya integrado. |
| TEMPORADA y 05 | `cycle.py`, `apply.py`, `apply_cola.py`, `lecturas_caso.py`, política/caso, settings y configuración | 05 se diseña ahora; revalida firmas, temporización y reversas después de TEMPORADA. |
| 04 B-C y 05 B/D | Lector de listas y propiedad en harvest/reconciliación/bloqueos | Reutilizar lectura completa; coincidencia textual no acredita creación propia. |
| Todas las migraciones | Numeración y etiquetas de `tools/despliegue/comun.sh` | 0086 ya pertenece a ARREGLOS 02; asignar siguientes números sobre base integrada y conservar reversas. |

La secuencia completa es la decisión de esta entrega, aunque algunos bloques serían técnicamente separables. No ejecutar dos planes en paralelo por una cabecera antigua.

## Seguimientos operativos y dependencias externas

Los P del [ROADMAP](ROADMAP.md) conservan su evidencia fechada. «Falta comprobar» no significa que el código falte o que la operación haya fallado.

| Seguimiento | Pendiente real / dependencia |
|---|---|
| Actas de la base integrada | Migraciones, configuración y cierres específicos de 01/02/ORBIT 07/CLARIDAD/DISEÑOS. Runtime ya coincide; no reejecutar implementaciones. |
| P02 — FABRICA 01/UI/02 | Un cierre conjunto: paso 4 de tarea 11, primer harvest natural por grupo y ensayo de reversa. No repetir la sonda de creación del 9-sep. Operar tras cerrar 02 en 04 B-C y 03/10 en 05 B. |
| P03 — ADS PROTECCION C.6 | Completar P2 (cobertura, elegibilidad y maduración); después expediente P3, cinco ciclos propios P4 y live P5 con sus gates. No superponer sombra global cero-applies con encendidos de TEMPORADA. |
| P05 — reseñas Amazon | La última foto no tiene ingesta exitosa; **03 D-B retira Apify**. Seguir esa retirada, no recargar crédito ni activar el camino viejo. Una fuente nueva para Reputación 02 requiere brief propio. No declarar retirada ejecutada hasta su evidencia. |
| P06 — ORBIT 02/cloudflared | Externo: inventario de rutas y ventana de rotación. No reabrir migración/apagado ya cerrados. |
| P07 — BIDS 01 | Primer lote manual de archivo, con datos actuales y go específico. Antes, cerrar reposición 11 en 05 C; reponer no restaura identidad/historia archivada. |
| P08 — JEV ADS 03 | Medición de 30 días programada para 17-nov-2026 09:00 UTC; recuperar resultado cuando exista. Fecha prevista no demuestra ejecución. |
| P09 — ORBIT 19 | Conciliación 0.3, cobertura solo-actividad y Featured Offer en ese consumidor. Tener cliente SP-API no prueba su cierre. |
| P13 — PEZ 02 | Externo: solo MLM2727437671, inhabilitada por categoría; ocho descripciones publicadas, 22 mm ya confirmado. No recostear ni repetir pregunta de medidas. |
| SP-API 02 | Recuperar evidencia de nocturna posterior al despliegue del 22-oct; el acta conserva la anterior. Código/despliegue documentados, sin afirmar que nunca corrió después. |
| RESUMEN ADS 04 | Revisión visual productiva no acreditada en su plan; runtime y conciliación son evidencias distintas. P01 ya cerrado. |
| Pausa por producto / AUTO-03 | Encendido del dueño separado de implementación y de ORBIT 07; conservar el modo documentado hasta evidencia de cambio. |

**P01 y P04 ya están cerrados con evidencia**; no son pendientes. P10, P11 y P12 no son órdenes de implementación: se clasifican abajo. Los bloqueos P06/P13 y el calendario P08 no detienen toda la cola.

## Futuros sin implementación autorizada

- AUTO-04 placements automáticos; AUTO-05 redistribución/pacing/intradía; AUTO-06 reactivación/limpieza automática. BIDS 01/02 ya entregaron las herramientas manuales. Las propuestas de TEMPORADA no autorizan el automatismo completo de presupuestos.
- `meli-ads-01` (propuestas, sin escritura publicitaria) y AUTO-08/promociones: sin plan ejecutable. ENVIOS 01 (envíos Amazon/MeLi) está entregado: PR #780, `52cb4b02`, [acta](../docs/evidencia/envios-01/cierre-produccion.md).
- AUTO-09/REPUTACION 02: stub sin brief. Su fuente Amazon debe redefinirse después de retirar Apify; P05 no es permiso para reactivarlo.
- Integraciones cruzadas reputación/campañas/promociones/envíos; JEV E.1/E.3/fábrica excluidos de cierres anteriores.
- P10 atribución de reseñas compartidas: opcional. P11 lotes Apify: propuesta histórica superada por la retirada de 03 D-B, sin trabajo vigente de lotes ni recarga. P12 cifrado/observabilidad: delimitar alcance antes de una implementación.
- Refactorización global posterior a 05 y notas antiguas de estilo/escala de ADS PROTECCION: no son nuevos bloqueos ni autorización. Revisar solo por impacto demostrado.

## Histórico cerrado y archivos auxiliares

El inventario y el ROADMAP conservan los cierres de ORBIT 01/03/04/05/preflight/06, GUARDRAILS, DASHBOARD, CORTES, CAMPANAS, CAMPANA ACTIVA, ESTRUCTURA, CORTES UI, ORBIT UI, MARGEN ESTIMADO, BIDS 02, SP-API 01, INGESTA, JEV 01/02 y PEZ 01. BIDS 01, Fábrica, Reputación 01, ORBIT 19, JEV 03 y SP-API 02 conservan solo los seguimientos indicados; sus defectos nuevos se asignan a esta cola, no reabren todo el módulo.

REPRICING 01 fue sustituido por REPRICING 02, ya entregado; los modos live documentados en octubre no certifican el estado actual. `bids-02-construccion`, `repricing-02-construccion`, ADS PROTECCION fase D, JEV02 bloque S, briefs y validaciones son auxiliares de sus padres. `jev-ads-03.md-E` es copia antigua. Orbit Bench y PLAN-DB-GROK están entregados. Checks antiguos abiertos o texto de cierre sin evidencia no deciden por sí solos el estado.

Al integrar una entrega, actualizar esta cola, su plan, el manifest y el seguimiento afectado con SHA/evidencia. Mantener CI y revisión de secretos del commit final; reutilizar evidencia válida y ampliar pruebas según el comportamiento, riesgo y evidencia faltante. No cerrar operación solo por merge o huella.
