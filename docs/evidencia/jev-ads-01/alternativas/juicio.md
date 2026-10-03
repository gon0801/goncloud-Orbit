# Dictamen cruzado de diseños Jev Ads v1

Leí completos ambos diseños, `rubric.md`, `grounding.md` y `catalog.md`. Contrasté sus contratos con el prototipo y el censo de `docs/evidencia/jev-ads-01`. El alcance es diseño de asesoría; ninguna propuesta autoriza acciones Ads.

## Puntuación

| Criterio de la rúbrica | A | B | Motivo principal |
| --- | ---: | ---: | --- |
| Fidelidad de datos | 4 | 5 | Ambos preservan `LEFT JOIN`, huecos y falta de prueba de roster completo. B identifica `product.id` como PK y distingue la cobertura de variantes; A llama canónico a `odoo_sku`, que es una clave única, pero no la PK. |
| Límites de autoridad | 5 | 5 | Ambos dejan la evaluación fuera de `cycle`, `apply_cola` y `apply_harvest`; GET lee y la cola de veto de 48 h conserva su autoridad. |
| Profundidad y simplicidad | 3 | 4 | Los callers son legibles y ambos usan tres módulos. A añade solicitud, trabajo mutable, intento y evaluación para una asesoría explícita. B separa el juicio semántico de la interpretación Ads, aunque aún debe cerrar el contrato del POST. |
| Reproducibilidad e invalidación | 4 | 4 | Ambos congelan sujetos, versiones y tiempos; B añade historial por par y asociación por IDs. La reutilización de B necesita probar aislamiento de cada par; A evita esa dependencia, a costa de repetir llamadas. |
| Implementabilidad y verificación | 2 | 3 | A exige IDs de hechos que la respuesta Choice comprobada no entrega. B tiene pruebas discriminantes y un contrato de par explícito, pero el prototipo todavía no lo ejercita de forma aislada y su operación POST no define si responde sincrónicamente o deja una solicitud durable. |
| **Total** | **18/25** | **21/25** | |

## Base e injertos

Elegiría **B** como base. Su unidad término más ficha versionada permite recomponer origen, destino y semillas sin pedir al modelo una conclusión sobre el grupo. `componer` conserva la regla decisiva: un compatible observado es testigo positivo; el universal negativo exige censo demostrado, conjunto no vacío y juicio vigente para todos. La decisión Ads y su contexto quedan congelados en la revisión, no dentro de la clave reutilizable.

Injertaría de A estos contratos concretos:

- Ficha por `listing_id` y plataforma como opción inicial. B puede agrupar publicaciones sólo mediante una aprobación explícita que certifique que los hechos aplican a cada variante. Esto sigue `catalog.md`, que propone una revisión por listing.
- Ticket, idempotencia y estados visibles para la petición operativa. La síntesis debe escoger si POST evalúa sincrónicamente o acepta un trabajo durable y luego devolver un contrato coherente. Si se escoge trabajo durable, tomar de A el claim con lease y cierre atómico; justificar entonces la tabla mutable `jev_trabajo`.
- Distinción explícita entre hechos conocidos al decidir y hechos capturados para una revisión posterior. Mostrar ambos tiempos en la UI y no describir una ficha posterior como evidencia histórica de la decisión.
- Error `contexto_excedido` sin truncar productos cuando se agrupan preguntas. Aun si B envía un par por llamada, el límite de bytes debe aplicarse antes de HTTP.

Rechazaría de A la exigencia de IDs de hechos devueltos por Jev y la confianza como veto sin umbral calibrado. Choice entrega una categoría, probabilidades y confianza; no entrega una lista verificable de hechos usados. La UI puede enlazar la ficha y sus hechos de entrada, pero no atribuirle al modelo una cita que no produjo. Rechazaría también la cola adicional como requisito previo del diseño: sólo se justifica si se adopta procesamiento asíncrono durable. No injertaría su clave de resultado por contexto, pues elimina la reutilización que fundamenta B.

## Contratos por cerrar en la síntesis

1. **A pide una respuesta inexistente.** En A, sección «Relaciones y composición», el parser exige «identificadores de los hechos utilizados». La ejecución viva de `probe.py` conserva en `resultado-live.json` únicamente `choice`, `confidence` y `probabilities` por respuesta. Reproducción desde la raíz del repo:

   ```sh
   python3 - <<'PY'
   import json
   x = json.load(open('docs/evidencia/jev-ads-01/prototipo/resultado-live.json'))
   row = next(r for r in x['rows'] if r['status'] == 'evaluated')
   print(sorted(next(iter(row['answers'].values())).keys()))
   PY
   ```

   Resultado observado: `['choice', 'confidence', 'probabilities']`. Adoptar A sin cambiar ese contrato impediría aceptar una respuesta válida del proveedor. Esta es una objeción bloqueante para A tal como está escrito.

2. **B no tiene aún evidencia de independencia entre pares.** B promete que la petición de un par incluye una sola ficha. `probe.py:74-88` envía todas las fichas del caso en el mismo `state` y crea preguntas por producto; `README.md` lo declara. Los 24/24 aciertos sintéticos, incluidos cambios de orden, prueban ese formato agrupado. No prueban que el juicio sobre una ficha sea igual cuando aparecen otras fichas o cuando se consulta sola. Antes de usar la clave `ClavePar` como caché transversal, ejecutar casos que envíen el mismo término y ficha solos y dentro de conjuntos distintos, con el mismo modelo y contrato. Si la categoría cambia, no reutilizarla como resultado independiente. Esta falta de prueba limita una afirmación de B; no demuestra que el diseño sea imposible.

3. **Las cuatro etiquetas no están validadas por el prototipo.** El ensayo sólo distingue `satisface`, `no_satisface` e `informacion_insuficiente`; `no_satisface` reúne otra categoría y contradicción explícita. A y B proponen separar `fuera_categoria` y `contradiccion`. Deben ensayar esa separación con ejemplos etiquetados y conservar ambas como una sola clase negativa hasta tener evidencia. No afirmar precisión calibrada ni ahorro con los fixtures sintéticos.

4. **B mezcla solicitud y ejecución.** `AsesorAds.evaluar` parece ejecutar proveedor y persistencia; el texto de POST dice que sólo solicita asesoría, mientras un comando procesa un lote. El diseño final debe fijar quién ejecuta, cuándo se confirma la solicitud y qué recupera una repetición de `clave` tras crash o timeout. Mantener HTTP fuera de la transacción y GET sin llamadas externas.

5. **El censo no certifica universales productivos.** `catalogo-grupos.json` sustenta los 1/33 grupos MX y 24/48 US con anuncios ENABLED/PAUSED sin listing. `catalog.md` también advierte que el sync no borra anuncios ausentes del payload. Ninguno de los candidatos puede derivar `cobertura_fuente="completa"` de cero huecos y estados recientes. Se necesita evidencia del roster por grupo o dejar el resultado `indeterminado`.

El prototipo no implementa caché, vencimiento, integración Ads ni permisos. Sus resultados son evidencia del prompt sintético y de la composición local; esas piezas quedan como contratos de diseño y pruebas futuras, no como capacidades ya verificadas.
