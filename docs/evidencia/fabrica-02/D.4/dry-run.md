# E/D.4 — Dry-run de `--solo-origen` sobre el job 2 (2026-10-01/02)

**Que es esto.** Salida del dry-run de la remediacion sellada del job 2
(`tools/reversa_harvest.py --job 2 --solo-origen`, rama
`fix/fabrica02-d4-origen-es-destino`) corrido contra produccion en modo
solo lectura. La corrida real (`--acepto-mutacion-real ... --go`) es del
dueno, despues del merge y el deploy, con huella y go frescos.

## Como se corrio (cero HTTP, cero escrituras)

Codigo de la rama copiado a scratch del server (`/tmp/d4orbit`, borrado
despues), ejecutado en contenedor efimero sobre la red `orbit_default`
con `ORBIT_DSN_DECIDE`, sin `--acepto-mutacion-real` (el camino dry-run
hace solo SELECT e imprime; jamas crea el cliente ni toca Amazon):

```
$ python3 tools/reversa_harvest.py --job 2 --solo-origen
job: 2 platform: amazon_mx termino: arras matrimoniales de oro decision: 2311
alcance: solo negativo de origen (la keyword destino queda intacta)
[pendiente] negative negative ad_group=272585315669297 id=92333897493675
pendientes: 1 huella: afc9ef3591ca6200
dry-run: sin --acepto-mutacion-real no se toca Amazon
```

Reproduccion del "pendientes: 0" de la reversa completa (misma corrida,
sin el flag: el job 2 no trae flags `*_creada` y el plan completo no lo
autoriza, por diseno):

```
$ python3 tools/reversa_harvest.py --job 2
job: 2 platform: amazon_mx termino: arras matrimoniales de oro decision: 2311
pendientes: 0 huella: e3b0c44298fc1c14
dry-run: sin --acepto-mutacion-real no se toca Amazon
```

## Verificacion de cero escrituras (orbit_read, despues de los dry-runs)

- `apply_attempt` de la decision 2311: 2 filas, max id 209 (las originales
  208/209; ninguna fila nueva).
- Filas tipo `reversa` de la decision 2311: 0.

## Hechos de produccion que sostienen el plan (orbit_read)

- `harvest_job` 2: decision 2311, `done`, `ad_entity_id` 297 = ad group
  `272585315669297` (Arras Manual, campana `97835222467967`).
- Congelado de la 2311: destino `97835222467967` / `272585315669297` =
  origen (firma D.4).
- Attempt 208: tipo normal, ok, `NEGATIVE_EXACT` en `272585315669297` con
  ack `negativeKeywordId = 92333897493675` (procedencia probada).
- Attempt 209: tipo normal, ok, `EXACT` con ack `keywordId =
  197174507964917` (queda intacta: no entra al plan).
- `verify_ok` true, ultima cola `applied`.
- Jobs 1/3/4: origen != destino (auditoria del lead confirmada).

## Pendiente (dueno, post-merge + deploy)

Re-correr el dry-run (huella fresca), autorizar con go literal, ejecutar
la mutacion real con `--esperado 1`, y verificar por LIST que el negativo
`92333897493675` quedo ARCHIVED y la keyword `197174507964917` sirve.
