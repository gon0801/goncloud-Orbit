# FABRICA UI 01 — Implementacion

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development.
> Spec: docs/superpowers/specs/2026-09-06-fabrica-ui-design.md
> Base: origin/master ba24c44; worktree feat/fabrica-ui.

## Restricciones globales

No ejecutar mutaciones Amazon: sonda diferida. Reutilizar motor existente. Dinero
Decimal/string, cero valores inventados. Token solo-header y confirmacion explicita.
No modificar otros proyectos ni cambios locales del dueno. Suite completa en CI PR;
local solo archivo de test trabajado. Cada regresion demuestra rojo contra codigo anterior.

### Task 1: API y adaptador

Crear app/api_fabrica.py, app/fabrica_web.py y tests/test_api_fabrica.py.
Implementar contrato de spec con catalogo/preview/crear/lotes/recuperacion; proteger
idempotencia con lock asesor y PK estable, validar auth antes de conexion admin.
Reutilizar motor por import tools.fabrica_campanas. `_mutar(..., lote=...)` lo agrega
Task 3. Registrar router en main lo hace Task 3. Tests montan router si aun no existe.
Escribir tests primero y ejecutar archivo focalizado. Autorevision y revision host.

### Task 2: Pantalla

Crear app/templates/fabrica.html, app/static/js/fabrica.js, app/static/css/fabrica.css,
tests/test_ui_fabrica.py. Editar app/ui.py, templates/campanas.html y base.html.
Implementar contrato exacto: formularios, productos, preview, confirmacion token,
historial/detalle/acciones. CSP sin inline, textContent para datos externos, accesibilidad.
No defaults monetarios. Probar ruta y comportamiento en navegador con frontera API
simulada, sin Amazon. Autorevision y revision host.

### Task 3: Motor y empaquetado

Modificar tools/fabrica_campanas.py solo para parametro opcional lote de `_mutar`,
mantener CLI y ledger previos; test focalizado en tests/test_fabrica_campanas.py.
Docker COPY tools/fabrica_campanas.py ./tools/fabrica_campanas.py; registrar router
en app/main.py. Documentar uso en docs/DEPLOY.md y plan/manifest.

### Task 4: Integracion, revision y entrega

Integrar pruebas UI navegador y API, Ruff/pre-commit. Revision independiente de
seguridad y comportamiento sobre diff completo, corregir hallazgos. Crear PR desde
base limpia y leer suite CI completa; merge/deploy segun autorizacion vigente.
Verificar health, enlace visible, catalogo y preview productivos sin crear campanas.
AppFlowy In progress al inicio, Done al entregar UI, sonda original sigue pendiente.
