# MARGEN ESTIMADO 01 · B.2 — Smoke navegador (Playwright/Chromium)

Fecha: 2026-09-08. Viewport **390×844**, `deviceScaleFactor=2`.
APIs `/api/fabrica/*` mockeadas; pagina real `GET /campanas/nuevas` via uvicorn local.

## Como reproducir

```bash
cd docs/evidencia/margen-estimado-01/B.2
npm install   # playwright (ver package.json)
ORBIT_SMOKE_PORT=8767 node smoke_playwright.mjs
```

## Comprobado

| Chequeo | Resultado |
|---|---|
| Etiqueta «Contribución estimada por venta · Antes de Ads» | OK |
| Motivos legibles (`fee_ausente`, `politica_ausente` → español) | OK; sin codigos crudos |
| Overflow horizontal a 390 px (`documentElement.scrollWidth ≤ clientWidth`) | OK |
| Detalle abierto dentro de la card (bbox detalle ≤ card; no solapa siguiente) | OK |
| Tema dia / noche (`data-tema`) + capturas | OK |
| Foco teclado en `summary` del desglose + Enter abre | OK |
| Desglose muestra fecha / vigencia / estado / pertenencia | OK |
| `GET /evaluacion` reusa `as_of` del catalogo | OK (assert en mock) |

## Artefactos

- `screenshots/dia-390.jpg`
- `screenshots/noche-390.jpg`
- `screenshots/detalle-abierto-noche-390.jpg`
- `smoke-resultado.json`
