# REPUTACION 01 / A.3 — Cierre como ampliación (sin implementar)

Fecha: 2026-09-08. Decisión: lead + dueño (acta 0.5 D2/D6).

## Por qué no se implementó

El DoD de A.3 exigía: sonda 0.2 verifica actor de texto + la
recurrencia cabe en el tope D6 ($10 USD/mes). Ninguna se cumplió:

- 0.2 solo verificó `junglee~Amazon-crawler` (rating/count, sin
  texto). Ningún actor de texto quedó verificado dentro del tope.
- La cuenta Apify FREE está agotada ($0.000017, verificado en A.7);
  el plan de pago cubre junglee semanal (~$5.20/mes), no un actor
  de texto adicional sin cotizar.

## Consecuencias asumidas en v1 (ya construidas)

- Amazon aporta rating + count semanal, cero texto → pantalla muestra
  "Sin verificar" (`api_reputacion.py`, `amazon_texto`).
- Alerta `resena_1` solo MeLi (texto oficial A.4).
- A.3 no bloqueó A.5/A.6 (plan §A.3, acta §10).

## Reapertura

Si se verifica actor de texto en tope (nueva sonda 0.2x) o el dueño
amplía presupuesto: A.3 se reabre como task de implementacion con su
propio DoD (dedupe, aborto por costo, costo registrado). Mientras
tanto, queda cerrada como ampliación.
