# Orbit Bench

Evaluacion local, preparada para correr **un modelo por vez**. Compara resultados
de trabajo en Orbit: razonamiento aplicado, correccion, limpieza, arquitectura,
diseno, revision y mantenibilidad. Coste y autonomia se registran por separado.
No mide inteligencia general y no produce un ganador antes de tener evidencia.

## Que incluye

Seis ejercicios sinteticos basados en reglas de Orbit y una segunda fase de codigo.
Son problemas nuevos de evaluacion, no una lista de defectos encontrados en
produccion. Este piloto mide tareas acotadas; la navegacion por el repositorio
completo y los cambios de multiples modulos necesitaran casos posteriores.

| Caso | Entrega | Evaluacion principal |
|---|---|---|
| codigo | Programa ejecutable y explicacion | Dinero, monedas, datos ausentes y maduracion |
| depuracion | Arreglo ejecutable y evidencia del fallo | Reproduccion historica fiel |
| arquitectura | Propuesta tecnica | Responsabilidades, alternativas, reversa y simplicidad |
| diseno | Interfaz navegable local | Entender decisiones y sus efectos, accesibilidad |
| revision | Hallazgos sobre un cambio preparado | Defectos reales y falsas alarmas |
| criterio | Decision argumentada | Entender reglas y reconocer cuando no cambiar codigo |
| mantenibilidad | Segundo cambio sobre la entrega de codigo | Adaptabilidad y conservacion del comportamiento |

Los contratos exactos y archivos esperados estan en el `TASK.md` de cada paquete.
Las cifras del ejercicio estan fijadas en ese paquete: no se actualizan con reglas
futuras de produccion durante una comparacion.

## Preparar el primer intento

Desde la raiz del repositorio, con Python >=3.12 (no requiere dependencias nuevas):

```bash
python -m tools.orbit_bench list
python -m tools.orbit_bench prepare \
  --case codigo \
  --model 'nombre-y-version-exactos' \
  --harness 'entorno-y-version' \
  --config '{"reasoning_effort":"high"}' \
  --attempt 1 \
  --time-limit 1800 \
  --budget-usd 5 \
  --output /tmp/orbit-bench/intento-01
```

`--time-limit` y `--budget-usd` declaran limites para el operador. Este comando
no lanza modelos ni aplica automaticamente esos limites. Usar el control de
presupuesto/timeout del entorno que los ejecute y registrar cualquier exceso.
El presupuesto no se interpreta como gasto real.
En `--config` registrar ajustes reproducibles (esfuerzo, temperatura, version de
herramientas), nunca claves API, tokens de acceso ni credenciales.

Cada intento contiene:

- `run.json`: identidad, configuracion y procedencia del paquete.
- `candidate/`: unica carpeta que recibe el modelo.
- `review.json`: plantilla para la evaluacion humana.
- `metrics.json`: medidas registradas por el operador; empiezan desconocidas.
- `grade.json`: resultado de comprobaciones, despues de calificar.
- `baseline/`: solo en mantenibilidad, copia verificada de la entrega original.

Los intentos no se sobrescriben. Usar un destino nuevo para cada modelo/caso/repeticion.
Guardar los resultados fuera del checkout evita subirlos por accidente al PR.

## Correr un modelo

1. Crear una sesion nueva, sin memoria ni contexto de intentos anteriores.
2. Dar acceso solo a `candidate/`. El prompt comun es:

   > Lee TASK.md y completa sus entregables. Trabaja solo con los archivos del
   > paquete. Si necesitas asumir algo, documentalo; no inventes hechos ni
   > afirmes verificaciones que no ejecutaste. No incluyas el nombre de tu modelo.

3. Registrar el inicio y aplicar los limites declarados en el entorno ejecutor.
4. Conservar transcripcion y capturas fuera de `candidate/`. No dar pistas de
   los checks privados ni corregir al modelo durante un intento sin registrarlo.
5. Al terminar, registrar duracion, tokens, gasto, intervenciones y retrabajo.

Cada modelo debe recibir los mismos archivos iniciales, herramientas permitidas,
limites y politica de ayuda. Comparar modelos en un mismo ejecutor aproxima una
comparacion del modelo; cambiar el ejecutor mide **modelo + entorno**.

Separar carpetas no restringe permisos. El operador debe montar solo `candidate/`
en un contenedor/VM sin credenciales ni acceso al checkout del evaluador. El
calificador ejecuta codigo del candidato: tambien debe correr en ese entorno
desechable, con CPU, memoria y red controladas. El timeout del CLI es una defensa
contra bloqueos normales, no un sandbox de seguridad.

## Calificar y revisar

```bash
python -m tools.orbit_bench grade --run /tmp/orbit-bench/intento-01
python -m tools.orbit_bench blind \
  --run /tmp/orbit-bench/intento-01 \
  --output /tmp/orbit-blind/paquete-01
```

La parte automatica valida salidas y casos limite en codigo. En texto y UI,
encontrar un archivo no equivale a aprobarlo: queda pendiente de revision humana.
Cambiar la entrega despues de calificar invalida la calificacion anterior.

La exportacion crea `packet/` con entregables y plantilla, sin metadatos del
modelo ni costes, y `operator-map.json` fuera de ese paquete. Entregar al revisor
**solo `packet/`**, en orden aleatorio entre intentos. El operador conserva el mapa.
Dentro de `packet/`, `submission/` conserva la entrega completa con sus modulos
auxiliares, `context/` contiene los archivos iniciales y `baseline/` conserva la
version padre cuando se evalua mantenibilidad. La tarea autoritativa esta en
`TASK.md`; la rubrica y la plantilla en `RUBRIC.md` y `review.json`. Abrir la UI
desde `submission/index.html` y comparar `baseline/` con `submission/` para el
segundo cambio.
Revisar tambien cualquier firma o nombre
del modelo dentro de sus propios entregables: la exportacion no puede garantizar
anonimato frente a contenido que se identifica a si mismo.

Completar la plantilla humana y devolver sus valores a `review.json` del intento
correspondiente, conservando la huella de la entrega. No usar al propio candidato
como unico juez. Un juez automatico puede ayudar a revisar; sus notas necesitan
calibracion contra tus preferencias y ejemplos que tu hayas puntuado.

## Rubrica humana

Puntuar cada dimension pertinente entre 0 y 4, con evidencia concreta (archivo,
linea, captura, check o decision). No asignar notas a dimensiones ajenas al caso.

| Nota | Ancla comun |
|---|---|
| 0 | Ausente o inutil; no satisface la tarea |
| 1 | Errores graves; exige rehacer gran parte |
| 2 | Aprovechable con correcciones importantes |
| 3 | Aceptable con ajustes menores |
| 4 | Listo para aceptar, completo y justificado |

| Dimension | Evidencia que importa |
|---|---|
| Razonamiento | Identifica restricciones, deriva consecuencias y considera casos que refutarian su solucion; explica decisiones verificables, sin exigir razonamiento interno privado |
| Correccion | Cumple el contrato, conserva invariantes y resuelve fronteras; distingue cero de desconocido y no afirma pruebas inexistentes |
| Limpieza | Nombres claros, responsabilidades coherentes, dependencias justificadas, poca duplicacion y estilo adecuado; no se puntua por contar lineas, clases o comentarios |
| Arquitectura | Alternativas concretas, duenos de datos/decisiones claros, interfaces suficientes, fallos y reversa definidos; complejidad proporcional al problema |
| Diseno | Jerarquia visual, legibilidad, estados vacios/error, teclado y foco; tipo de accion, efecto y evidencia comprensibles; evaluar en navegador, no solo una captura bonita |
| Revision | Hallazgos reproducibles y priorizados, explicacion de impacto y arreglo; contabilizar defectos esperados encontrados, omitidos y falsas alarmas por separado |
| Mantenibilidad | La segunda peticion encaja sin romper lo anterior ni introducir excepciones dispersas; revisar el diff, el resultado y el retrabajo |
| Autonomia | Entrega completa dentro de limites, decisiones razonables y bloqueos bien comunicados; contrastar con transcripcion e intervenciones, sin premiar actuar sin permiso |

Ejemplo de limpieza: una funcion larga pero coherente puede merecer mejor nota
que diez helpers que ocultan el flujo. Ejemplo de criterio: justificar con el
contrato que la peticion ya esta resuelta puede merecer 4 aunque no escriba codigo.

`accepted` significa **aceptaria esta entrega para la tarea**. Dejarlo en `null`
si no se ha revisado. Una revision requiere revisor identificado y evidencia en
todas las dimensiones aplicables. Registrar en `critical_failures` violaciones
graves demostradas: inventar datos, mezclar monedas, romper invariantes o falsear
verificaciones. No compensarlas con una buena nota visual.

Las claves del evaluador estan en `tools/orbit_bench/cases/*/REVIEWER.md`,
fuera de los paquetes del candidato. Usarlas para revisar los defectos sembrados
y las decisiones esperadas. En revision, anotar en la evidencia tres conteos:
defectos confirmados encontrados, defectos omitidos y falsas alarmas. Las claves
orientan la revision; un hallazgo adicional valido se acepta si es reproducible.

## Medidas del operador

Editar `metrics.json` con valores observados. No rellenar desconocidos con cero.

| Campo | Unidad y regla |
|---|---|
| duration_seconds | Tiempo transcurrido desde inicio hasta entrega, incluidos reintentos del modelo |
| cost_usd | Gasto total atribuible al intento en USD, obtenido del proveedor; null si no se conoce |
| interventions | Numero de ayudas/correcciones del operador durante el intento |
| rework_minutes | Minutos reales para llevar la entrega a aceptable; no una estimacion presentada como medida |
| input_tokens | Tokens de entrada reportados por el entorno |
| output_tokens | Tokens de salida reportados por el entorno |

Con suscripciones sin coste por intento verificable, conservar `cost_usd: null`.
El trabajo del mantenedor es otro intento: no mezclar su gasto con el del autor.

## Segunda fase: mantenibilidad

Primero terminar, calificar y revisar `codigo`. Despues:

```bash
python -m tools.orbit_bench extend \
  --run /tmp/orbit-bench/intento-01 \
  --output /tmp/orbit-bench/intento-01-mantenimiento
```

Dar solo el nuevo `candidate/` al modelo. El original permanece intacto y la
nueva tarea contiene su entrega como punto de partida. Repetir calificacion y
revision para este intento. No mostrar la segunda peticion durante la primera.

Para separar habilidad del autor y facilidad de mantenimiento, repetir sobre
esa misma entrega con un mantenedor fijo:

```bash
python -m tools.orbit_bench extend \
  --run /tmp/orbit-bench/intento-01 \
  --model 'mantenedor-fijo-version' \
  --harness 'entorno-y-version' \
  --config '{"reasoning_effort":"high"}' \
  --output /tmp/orbit-bench/intento-01-mantenedor-fijo
```

No atribuir automaticamente un fallo del mantenedor a la limpieza del autor:
revisar el diff y repetir sobre la misma base cuando el resultado sea dudoso.

## Comparar resultados

```bash
python -m tools.orbit_bench report \
  --runs /tmp/orbit-bench \
  --output /tmp/orbit-bench-informe.md
```

El informe separa configuraciones y condiciones, cuenta intentos y notas conocidas,
conserva pendientes y muestra media/rango por dimension. La huella del paquete
permite detectar cuando dos intentos no partieron de los mismos archivos.
No combinar versiones, casos o presupuestos distintos como si fueran equivalentes.

Arrancar con un modelo y un caso permite validar el procedimiento. El piloto
propuesto es 6 casos x 3 modelos x 3 intentos = 54 ejecuciones, mas las fases de
mantenibilidad que se decida comparar. Tres intentos ayudan a ver variacion,
pero son pocos para declarar diferencias pequenas como concluyentes. Conservar
tanto resultados fallidos como exitosos; no elegir solo la mejor repeticion.

La decision final es por funcion: que aceptas para codigo, arquitectura, revision
y diseno; cuanto retrabajo exige y que coste conocido tiene. Ampliar despues con
tareas completas de repositorio y casos reservados que no se usen para ajustar
prompts. No presentar las notas de este piloto como una medida universal.

Si el informe detecta dos carpetas con el mismo `run_id`, rechaza la comparacion:
una copia o backup no es una repeticion independiente. Mantener backups fuera del
directorio de resultados y crear cada intento con `prepare` o `extend`.

## Desarrollo y verificacion

Durante cambios, ejecutar solo el archivo de test que se esta trabajando:

```bash
python -m pytest -q tests/test_orbit_bench.py
python -m pytest -q tests/test_orbit_bench_cases.py
```

La bateria completa de Orbit, ruff y pre-commit corren en Quality CI del PR.
La construccion de este benchmark no requiere ejecutar ni contratar modelos.
