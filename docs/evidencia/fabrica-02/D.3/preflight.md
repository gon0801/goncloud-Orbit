# FABRICA 02 D.3: preflight de solo lectura (2026-09-23 UTC)

Consulta de producción como lector y dry-run de `tools/goals_modo_grupo.py`:

- Grupo 1, `kit_arras`: cinco goals habilitados en `shadow`, terna de harvest
  `NULL` y bid 11.6200 MXN en cada uno. La envolvente `ads_optimizer_mode`
  está en `live`.
- Cap diario de harvest: 2 en MX y 2 en US, `config_version` 20.
- Cola harvest no terminal: 0. Jobs en curso: 0.
- Jobs históricos `done`: 4, IDs 1-4; ninguno tiene
  `external_ids.hermanas`. No sirven para probar una reversa F2 con hermanas.
- Dry-run de `tools/goals_modo_grupo.py --grupo 1 --mode live`: cinco
  candidatas (goals 11, 9, 10, 8, 12); huella observada
  `2bfabc992adf0951`. Repetirlo y usar su huella actual justo antes del go.

La reversa F2 ya está implementada y probada en `tests/test_reversa_harvest.py`:
keyword → hermanas propias → origen, readback entre deletes, parada en fallo y
reanudación. Así se cumple la regla 7 antes de encender. No hay un job F2 con
IDs reales de hermanas mientras los goals sigan en `shadow`, por lo que el
ensayo con IDs reales solo se puede hacer después del primer harvest natural.

El dueño aprobó el 2026-09-23 UTC el orden de `docs/DEPLOY.md` D.3:

1. **Go 1 literal y específico:** encender los cinco goals a `live` bajo el
   cap 2/2. Este go todavía no se ha dado; la aprobación del orden no lo
   sustituye.
2. Esperar el primer harvest natural hasta `done` y verificar sus IDs,
   keyword, negativas, biblioteca, ledger y `/cortes`.
3. **Go 2 literal y separado:** revertir ese mismo job con
   `tools/reversa_harvest.py`; dry-run y huella nueva antes de ejecutar.

El kill switch vuelve cada goal a `shadow` mediante `app.cli goals set`.
No se hizo ninguna mutación durante este preflight.
