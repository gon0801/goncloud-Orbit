# B.2a — flag `ads_pause_sin_cooldown_bid` (contencion (a))

Rama `feat/ads-proteccion-b2a-flag`. El comportamiento PAUSE-nueva de B.2
(`beae9ec`) queda envuelto: con el flag apagado rige el gate pre-B.2
(cooldown generico bloquea tambien la PAUSE); encendido, rige B.2.
Lectura: `app/optimizer/goals.py::pause_sin_cooldown_bid_desde_settings`
(fail-closed: solo JSON true habilita; sin clave/NULL/otro valor -> False).
Resolucion una vez por ciclo en `_recorre_plataforma`; el camino de grupos
no lo usa (su gate sigue igual).

Aislamiento probado: `test_flag_apagado_bloquea_pause_nueva_como_antes_de_b2`
(rojo antes de implementar: kwarg inexistente; verde despues) + 43 passed en
`test_cycle_pause_cooldown.py` + `test_optimizer_goals.py` 2026-09-25.

## Encendido (H5, con go de deploy; comando literal)

config_version es append-only (UPDATE prohibida por trigger): encender es
insertar una fila nueva copiando los settings vigentes:

```bash
ssh goncloud "$PSQL -c \"INSERT INTO config_version (label, settings)
SELECT 'B.2a flag on (H5, go <cita literal>)',
       settings || '{\\\"ads_pause_sin_cooldown_bid\\\": true}'::jsonb
FROM config_version ORDER BY id DESC LIMIT 1 RETURNING id;\""
```

Readback (la fila mas reciente debe traer true):

```bash
ssh goncloud "$PSQL_READ -c \"SELECT id, label, settings->'ads_pause_sin_cooldown_bid' AS flag
FROM config_version ORDER BY id DESC LIMIT 1;\""
```

## Apagado (rollback del flag, con go)

Otra fila nueva con false (nunca UPDATE):

```bash
ssh goncloud "$PSQL -c \"INSERT INTO config_version (label, settings)
SELECT 'B.2a flag off (rollback H5, go <cita literal>)',
       settings || '{\\\"ads_pause_sin_cooldown_bid\\\": false}'::jsonb
FROM config_version ORDER BY id DESC LIMIT 1 RETURNING id;\""
```

Verificar con el mismo readback (debe traer false) + 1 ciclo sin PAUSE-nueva.
