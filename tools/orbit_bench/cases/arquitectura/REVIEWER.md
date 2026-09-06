# Clave privada: arquitectura

Aceptar una propuesta que cubra concretamente:

- componentes pequenos dentro de Orbit: ingesta/snapshot, decision pura,
  propuesta/ledger, aplicador Amazon, reconciliador y lectura/API;
- PostgreSQL como coordinador durable, sin Redis/cola por defecto;
- estado/observaciones con moneda y fecha, abstencion ante costo/stock faltante
  y una consulta as-of o version equivalente que evite lookahead;
- idempotencia/claim por listing y version de politica/datos congelada;
- MeLi proposal-only y Amazon con modo off/shadow/live, topes y confirmacion
  para mas de 50 cambios;
- ledger pre-HTTP, readback, resultado ambiguo reconciliado y valor anterior;
- reversa implementada y probada antes de live, sin borrar auditoria;
- fallos parciales, frescura, metricas/alertas y rollout verificable.

Penalizar microservicios o infraestructura sin necesidad, escritura directa del
motor, fallback inventado, mezcla de monedas, MeLi live, reversa sin identidad o
un diagrama nominal que no explique estados y propietarios.
