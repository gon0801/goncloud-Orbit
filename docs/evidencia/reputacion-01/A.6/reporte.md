# REPUTACION 01 / A.6 — API + pantalla (harness-work Solo, lead)

Fecha: 2026-09-08. Rama: `feat/reputacion-01-a6` (PR por abrir).

## Decisiones

- D-A6-1: pantalla server-rendered SIN JS (anchors + secciones, no
  tabs JS): el "textContent para texto externo" del DoD se cumple via
  autoescape Jinja `{{ }}`; CSP `default-src 'self'` heredada del
  middleware (cero inline, verificado en captura).
- D-A6-2: URLs allowlist solo Amazon dp https (mismo patron A.2);
  MeLi sin permalink guardado = sin enlace (no inventar URLs).
  Imagenes: cero `<img>` en v1 (test dedicado).
- D-A6-3: `GET /api/reputacion/resumen` devuelve JSON crudo (strings
  intactos); el escape es del template. `ui.py` reusa
  `carga_resumen(conn)` (patron dash.*, jamas reimplementa queries).
- D-A6-4: "escape segun parse_mode": Telegram sin parse_mode =
  texto plano; el marcado viaja literal (test fija contrato).
- D-A6-5: sidebar segun plan (chip Reviews retirado, pestana dentro);
  el [CONFIRMAR] del acta §6 se procede por plan liberado (reversible
  en 1 linea; flag para A.R si el dueno quiere el chip de vuelta).
- Tendencia: sube/baja/estable/sin-dato (redondeo 1 decimal); previo
  con el mismo criterio que `caida_rating` A.5.
- Topes: 50 pendientes/alertas (+total), 20 reviews; DISTINCT ON mas
  reciente por observed_at en reviews y preguntas.
- `amazon_texto: "sin-verificar"` fijo en v1 (A.3 bloqueada).

## Evidencia

- TDD 2 vueltas: RED literal (4 failed API 404; 4 failed pagina 404)
  + GREEN (10 passed `tests/test_ui_reputacion.py`).
- Regresion: 211 passed (UI/API/reputacion incl. base.html).
- Navegador real (agent-browser, Chromium): /reputacion 200 con
  seed; sidebar Reputacion activa; 5 secciones con datos; anchors
  funcionan. Capturas (inspeccionadas por el lead):
  - `captura-arriba.png` (sidebar + listings + reviews)
  - `captura-completa.png` (full page: las 5 secciones)
- Review interno R1: APPROVE (sin critical/major) + 1 minor y 3
  recommendations, atendidos minor-1 (test DISTINCT ON preguntas),
  rec-2 (LIMIT bound) y rec-3 (assert cero `<script>` sin src);
  rec-4 (full-table documentado) se deja como esta.
- ruff + pre-commit + CI: ver PR.
