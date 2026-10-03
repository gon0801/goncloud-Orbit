# Prototipo sintético Jev Ads

Artefacto experimental independiente del motor Ads. Sólo usa Python stdlib y fichas inventadas; no importa la aplicación ni modifica campañas. Las expectativas de `fixtures.json` se fijaron antes de ejecutar el modelo. No se ajustan para conseguir aciertos.

## Uso

Desde esta carpeta:

```sh
python3 probe.py --self-check --output self-check.json
python3 probe.py --live --output resultado-live.json
```

Sin argumentos ejecuta el chequeo local. `--live` pide la clave sin mostrarla mediante `getpass`; alternativamente lee `TYPESAFE_API_KEY` del entorno. No poner claves en comandos, archivos o evidencia. Nunca guarda headers, cuerpos de errores ni mensajes crudos de excepciones. Los errores HTTP sólo conservan el código; los de transporte y contrato, un estado. Se rechazan redirecciones.

Una ejecución viva realiza **como máximo 24 solicitudes**, 12 casos × 2 órdenes de opciones, sin reintentos. Cada llamada incluye todas las preguntas independientes de los productos de un caso pequeño. Modelo fijo: `jev-1.13.0`. Timeout por llamada: 30 segundos. El chequeo local hace cero solicitudes.

## Contrato y expectativas

Por par término/producto: `satisface`, `no_satisface`, `informacion_insuficiente`. Las instrucciones describen explícitamente qué término y ficha evaluar, con rutas de estado; los IDs sólo correlacionan respuestas. Las expectativas y sus motivos no se envían al modelo.

La completitud se declara sólo para los fixtures sintéticos: ausencia de mappings no acredita un censo productivo completo; éste requeriría confirmación explícita. La composición del grupo se ejecuta en código: un producto documentado que satisface basta; todos negativos sólo producen `no_satisface` con censo completo y cobertura total; sin testigo positivo y con evidencia faltante produce `informacion_insuficiente`. Una ficha ausente fuerza insuficiencia para ese producto al componer, aunque el modelo invente una respuesta positiva. Se conserva la respuesta original para contar ese error. Una respuesta faltante o inválida invalida toda la llamada, sin fabricar un juicio del grupo.

| Caso | Expectativa del grupo |
| --- | --- |
| 01 ES directo / 02 EN directo | satisface |
| 03 modelo incompatible / 04 otra categoría | no_satisface |
| 05 atributo desconocido | informacion_insuficiente |
| 06 dos productos, uno compatible | satisface |
| 07 censo completo, ambos incompatibles | no_satisface |
| 08 censo completo, una ficha ausente | informacion_insuficiente |
| 09 origen incompatible / 10 destino diferente compatible | no_satisface / satisface |
| 11 instrucciones hostiles en término / 12 en ficha | no_satisface |

El chequeo local comprueba cobertura, rechaza entradas incompletas y respuestas omitidas, ejercita resultados literales de composición y censo incompleto, y demuestra que cambiar identidad de grupo, término, miembros o fichas cambia la huella. La huella incluye estado, modelo y versión de preguntas. No implementa un caché ni demuestra una política de caducidad temporal.

El informe vivo conserva solicitud sintética, huella, respuestas por producto, distribución Choice individual, confianza, uso, latencia y resultado de grupo derivado. `pair_matches` y `group_matches` permiten detectar errores sin esconder discrepancias. Comparar filas `normal` y `reversed` del mismo caso revela sensibilidad al orden. No hay consulta extra por grupo ni probabilidades conjuntas; una confianza alta no autoriza una acción.

El experimento puede refutar que este prompt discrimina las 12 expectativas, conserva la separación entre datos ausentes y contradicción, resiste estos dos textos hostiles o mantiene la etiqueta al invertir opciones. No demuestra precisión en catálogo real, calibración, seguridad general frente a inyección, beneficio económico, repetibilidad ni cobertura productiva. No define umbrales, negativos, harvest ni permisos. El self-check valida el contrato local; no acredita resultados del modelo. Fallos HTTP o de contrato son resultados fallidos separados de los desacuerdos semánticos.

Contrato contrastado el 2026-10-03 con documentación oficial: [API](https://docs.typesafe.ai/api), [Choice](https://docs.typesafe.ai/primitives/choice), [preguntas independientes](https://docs.typesafe.ai/patterns/fan-out). El formato usa `state`, `model`, `questions` y cada Choice usa `instructions` y `criteria`.

## Ensayo adicional: pares aislados (diseño B)

`probe_pairs.py` reutiliza cuatro pares de los casos 06 y 07 y conserva sus expectativas. Cada solicitud lleva exclusivamente `term` y `sheet` en el estado y una pregunta `relation`; los IDs del caso y producto sólo aparecen en el informe local. Así se puede contrastar el juicio aislado contra el juicio contextual del ensayo original. Son **8 solicitudes máximas adicionales**, dos órdenes por par, sin reintentos y con timeout de 30 segundos.

```sh
python3 probe_pairs.py --self-check --output self-check-pairs.json
python3 probe_pairs.py --live --output resultado-pairs-live.json
```

Su versión es `jev-ads-isolated-pair-1`. El hash canónico incluye la solicitud, la versión y una lista explícita del orden Choice (ordenar claves JSON por sí solo borraría esa diferencia). Cambia al cambiar término, ficha u orden; el mismo par en dos grupos comparte hash. El self-check verifica aislamiento, cambios de hash y reutilización entre grupos. No hay caché real ni composición de grupo en este segundo ensayo: se comparan sus etiquetas con los pares del informe original por caso, producto y orden. La suma de ambos pilotos tiene un máximo de 32 solicitudes.
