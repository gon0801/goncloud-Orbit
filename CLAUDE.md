# Orbit

Sistema **nuevo desde cero** (base de datos nueva) que reemplaza al stack de
motores de Amazon Ads (`goncloud-MCP-2`, cancelado 2026-08-21). No se reutiliza
código viejo; solo se migran credenciales y datos verificados.

**Leer primero:** `docs/CONTEXTO.md` — reglas de diseño innegociables, trampas
del dominio, qué se migra y qué no, fases. Las fuentes verbatim están en
`docs/traspaso/` (diseño v2 = fuente de verdad de reglas y umbrales).

<!-- >>> QUALITY-KIT CALIDAD SECTION START -- managed by quality-kit's init-repo.ps1. Do not hand-edit between these markers; re-running init-repo.ps1 will refresh this block cleanly. -->
## Calidad (quality-kit)

Este archivo `.pre-commit-config.yaml` tiene agregados propios (detectados por quality-kit) -- no lo pisamos.
Para ver que candados tiene realmente: `pre-commit run --all-files` (o mira `.pre-commit-config.yaml`).

Reglas de hierro:
1. Si un candado falla, se arregla el problema real -- JAMAS se usa `--no-verify` ni se saltea un candado.
2. Cada bug arreglado incluye, en el mismo cambio, una prueba que lo habria atrapado.
<!-- >>> QUALITY-KIT CALIDAD SECTION END -->
