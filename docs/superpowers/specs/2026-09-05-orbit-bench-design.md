# Orbit Bench v1

> Spec histórico, insumo de `tools/orbit_bench/`. El índice de
> planes/pendientes se escribe solo en `plans/ROADMAP.md`.

El dueno aprobo implementar el diseno conversado: comparar modelos mediante
tareas de Orbit y separar razonamiento, correccion, limpieza, arquitectura,
diseno, mantenibilidad, autonomia y coste. No se ejecutan modelos en esta tarea.

## Alcance

CLI Python sin dependencias nuevas, separada de la aplicacion de produccion.
Seis casos sinteticos basados en invariantes documentados de Orbit: codigo,
depuracion, arquitectura, diseno, revision y criterio. Una fase adicional de
mantenibilidad parte de una entrega congelada de codigo. No son bugs reales
descubiertos ni representan toda la complejidad del repositorio: es un piloto.

Cada intento tiene identidad aleatoria, modelo y entorno declarados, configuracion,
version del benchmark, huella del paquete inicial, limites de tiempo/presupuesto,
artefactos, calificacion automatica y revision humana por separado. No hay llamadas
a proveedores, credenciales, acceso a produccion ni telemetria automatica ficticia.

El candidato recibe solo `candidate/`, sin soluciones, pruebas privadas, historial
git, identidad de otros modelos ni la siguiente fase. El operador conserva el
resto fuera del entorno visible del candidato. Separar carpetas NO es un sandbox:
al ejecutar candidatos se usa un contenedor/VM sin secretos y con limites externos.

## Contrato de los casos

`tools/orbit_bench/catalog.py`: `VERSION: str`, `CASES: dict[str, dict]`.
Cada caso: `title`, `category`, `prompt`, `files: dict[str,str]`,
`artifacts: list[str]`, `dimensions: list[str]`, `automatic: bool`,
`extension_of: str | None`. IDs: codigo, depuracion, arquitectura, diseno,
revision, criterio, mantenibilidad (extension_of=codigo).

Dimensiones posibles: razonamiento, correccion, limpieza, arquitectura, diseno,
revision, mantenibilidad, autonomia. Se usan solo las pertinentes a cada caso.

`tools/orbit_bench/graders.py`: `grade(case_id: str, candidate: Path,
timeout: float = 5.0) -> dict`, con `checks: list[dict]`, cada check con `name`,
`passed: bool`, `detail: str`; `automatic_pass: bool | None`. Los casos de texto
o UI siguen pendientes de evaluacion humana incluso si existen los archivos.
Los checks privados nunca se exportan al candidato. Los casos ejecutables leen
un objeto JSON en stdin y escriben un objeto JSON en stdout en `solution.py`.
Las expectativas numericas se derivan a mano, con entradas de frontera y moneda.

## CLI y ciclo de vida

`python -m tools.orbit_bench list`

`prepare --case codigo --model MODELO --harness ENTORNO --config '{}'`
`--attempt 1 --output DIRECTORIO [--time-limit 1800] [--budget-usd 5]`:
crea run.json, candidate/TASK.md, archivos iniciales, review.json y metrics.json.
Rechaza destinos existentes para no sobrescribir intentos. Los limites son
metadatos para el operador, no limites de un proceso de modelo que no ejecutamos.

`grade --run DIRECTORIO [--timeout 5]`: califica entrega en proceso separado,
guarda grade.json junto con la huella de la entrega. Fallos y timeouts son fallos
de evaluacion, no aprobaciones. No crea una revision humana automaticamente.

`extend --run ORIGINAL --output NUEVO [--model MANTENEDOR] [--harness ENTORNO]`
`[--config '{}']`: congela/copia la entrega de codigo en un intento de
mantenibilidad y registra identidad, huella y resultado previo del padre.
No revela la extension antes de crearla y nunca modifica el intento padre.

`blind --run DIRECTORIO --output PAQUETE`: exporta solo tarea, entregables y
rubrica/plantilla humana con un ID anonimo; conserva el mapa de identidad con
el operador. No copia run.json, metrics.json ni grade.json al paquete ciego.

El operador completa review.json: reviewer, accepted (bool o null), scores
(dimension -> score de 0 a 4 y evidence), critical_failures (lista de textos).
metrics.json contiene duration_seconds, cost_usd, interventions, rework_minutes,
input_tokens, output_tokens; todos desconocidos inicialmente (null).

`report --runs DIRECTORIO [--output INFORME.md]`: agrega carpetas de intentos,
valida metricas/notas, distingue pendiente/incompleto/rechazado/aceptado, agrupa
por modelo+entorno+configuracion+version+huella inicial+caso+limites, informa
cobertura, n, rango y media de cada dimension con su denominador, coste conocido
y fallos criticos. No calcula ganador ni nota global. Solo acepta un caso cuando
se entregaron los artefactos, paso la parte automatica si aplica y la revision
humana esta completa sin fallos criticos. Una entrega cambiada invalida su nota.

## Rubrica

Escala anclada 0..4: 0 ausente/inutil, 1 errores graves, 2 util con correcciones
importantes, 3 aceptable con ajustes menores, 4 listo y justificado. Cada dimension
tiene criterios concretos en docs/orbit-bench.md. Limpieza no se deriva del numero
de lineas. Revision mide hallazgos reales y falsas alarmas; criterio incluye una
tarea donde no cambiar codigo es la respuesta correcta. No se afirma medir IQ.

## Verificacion

Tests nuevos limitados a tests/test_orbit_bench.py y
tests/test_orbit_bench_cases.py: pruebas de ciclo de vida, integridad de entregas,
anonimizado, datos ausentes, notas invalidas y evaluacion real de candidatos
correctos/defectuosos. Red/green local solo del archivo trabajado. Bateria completa,
ruff y pre-commit en el PR mediante Quality CI con PostgreSQL 16.
