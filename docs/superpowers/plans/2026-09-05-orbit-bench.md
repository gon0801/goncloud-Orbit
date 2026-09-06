# Orbit Bench Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development para las
> dos piezas independientes y review final antes del PR.

**Goal:** Dejar una evaluacion reproducible lista para correr modelo por modelo.

**Architecture:** Catalogo de casos y calificadores privados separados del
CLI que prepara intentos, registra evidencia y genera informes. Los candidatos
trabajan en paquetes exportados y el operador conserva metadatos y resultados.

**Tech Stack:** Python >=3.12, biblioteca estandar, pytest para desarrollo.

**Spec:** docs/superpowers/specs/2026-09-05-orbit-bench-design.md

## Global Constraints

- Sin llamadas a modelos ni operaciones sobre produccion.
- Espanol, sin acentos en codigo; sin dependencias nuevas.
- Bateria completa una vez al final, en CI del PR.
- Rama desde origin/master actualizada; no incorporar trabajo ajeno.

## Task 1: Casos y evaluacion automatica

Files: tools/orbit_bench/catalog.py, tools/orbit_bench/graders.py,
tools/orbit_bench/cases/, tests/test_orbit_bench_cases.py.
Produce CASES, VERSION y grade segun el contrato de la spec; no consume el CLI.

- [ ] Escribir tests con candidatos literales correctos y mutantes: dinero,
  maduracion, replay congelado, timeout, JSON invalido, archivos ausentes.
- [ ] Ejecutar solo `pytest -q tests/test_orbit_bench_cases.py` y comprobar rojo.
- [ ] Crear seis tareas y la extension, con contexto suficiente y expectativas
  independientes. Ejemplo de frontera: dia de corte D-10 si; D-9 no.
- [ ] Verificar verde del mismo archivo y ruff en archivos propios.

## Task 2: Intentos, revision e informes

Files: tools/orbit_bench/__init__.py, tools/orbit_bench/__main__.py,
tools/orbit_bench/runs.py, tools/orbit_bench/report.py,
tests/test_orbit_bench.py.
Consume el contrato de CASES y grade. Produce los comandos descritos en la spec.

- [ ] Escribir tests de prepare -> grade -> review -> report con tmp_path,
  rechazo de sobrescritura, extension sin mutar padre, exportacion sin identidad,
  rechazo de notas/metricas invalidas y resultados obsoletos.
- [ ] Ejecutar solo `pytest -q tests/test_orbit_bench.py` y comprobar rojo.
- [ ] Implementar CLI stdlib, JSON UTF-8, IDs aleatorios y huellas SHA256;
  rechazar rutas que salgan del paquete y symlinks en entregas.
- [ ] Agrupar comparaciones equivalentes, conservar nulls y denominadores;
  no convertir resultados faltantes en aprobaciones o ceros.
- [ ] Verificar verde del mismo archivo y ruff en archivos propios.

## Task 3: Integracion y entrega

Files: docs/orbit-bench.md; los anteriores solo por hallazgos de integracion.

- [ ] Documentar comandos copiables, rubrica por dimension, control de limites,
  aislamiento, captura de tiempos/coste, revision ciega y extension de codigo.
- [ ] Revisar ambos entregables y probar el flujo CLI con un intento sintetico.
- [ ] Revision independiente de calidad y cobertura; corregir hallazgos.
- [ ] Verificar `git log origin/master..HEAD`, commit y push; abrir PR a master.
- [ ] Leer Quality CI: debe incluir pre-commit y toda la bateria pytest.
  No ejecutar la bateria completa localmente salvo que CI no la ejecute.
