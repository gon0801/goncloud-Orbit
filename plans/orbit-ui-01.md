# ORBIT 18 — Rediseno UI

Referencia: `docs/diseno/orbit-ui.md`; ZIP entregado por el dueno el 2026-09-06.
Base de la rama: `origin/master` f21e862; trabajo aislado en `goncloud-Orbit-ui`.

## Alcance

Rediseñar las nueve pantallas existentes con el handoff dia/noche. Conservar
flujos, monedas, seguridad y fuentes. Repricing, Reviews y Reputacion se
identifican como proximos modulos; no se agregan integraciones ni motores.

## Tareas

- [x] Sidebar, encabezados, tokens, tipografia, tablas y navegacion movil.
- [x] KPI por moneda, cobertura por metrica, sparklines y franja inmadura.
- [x] Resumen, Campanas, Decisiones, Salud, Contribucion, Propuestas, Inertes,
  Settings y Fabrica; presupuesto total con centavos exactos.
- [x] Pruebas focalizadas y regresiones en rojo/verde, Ruff y pre-commit.
- [x] Navegador con base temporal: nueve rutas a 390/1440 px, dia y noche.
- [x] Revision independiente: APPROVE tras corregir ACoS incompatible.
- [ ] PR con bateria completa y harness en Quality CI.

## DoD

CI verde, controles anteriores conservados, ausencia visible, cero visible,
monedas separadas, CSP sin inline y ningun scroll global horizontal.
La entrega es un PR revisable; no incluye despliegue ni mutaciones Amazon.
