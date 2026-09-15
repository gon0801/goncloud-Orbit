# E/D.2 — Terna del grupo 1 limpiada (2026-09-15)

**Qué es esto.** Evidencia de la fila D.2 de `plans/fabrica-02.md`. El
dueño corrió `tools/harvest_excepcion.py --limpiar-terna --grupo 1` con la
ceremonia del tool (dry-run, `--esperado`, `--huella`, `--go`) a las
16:57 UTC del 2026-09-15, después de la verificación de apagado de D.1
(ciclo 61, 16:41 UTC). Lo que sigue es el readback independiente del lead
como `orbit_read` (17:30 UTC).

## Goals del grupo 1 después de D.2

| rol | goal_id | mode | harvest_campaign_id | harvest_ad_group_id | harvest_default_bid | updated_at (UTC) |
|---|---|---|---|---|---|---|
| category_exact | 8 | shadow | NULL | NULL | 11.6200 | 2026-09-15 16:57:11 |
| category_phrase | 9 | shadow | NULL | NULL | 11.6200 | 2026-09-15 16:57:11 |
| category_broad | 10 | shadow | NULL | NULL | 11.6200 | 2026-09-15 16:57:11 |
| product_targeting | 11 | shadow | NULL | NULL | 11.6200 | 2026-09-15 16:57:11 |
| auto_discovery | 12 | shadow | NULL | NULL | 11.6200 | 2026-09-15 16:57:11 |

Los cinco quedaron en **bid-solo** (terna NULL, bid intacto), el estado que
0038 legaliza para campañas en grupo y el que el trigger
`ads_optimizer_goal_harvest_coherente` protege. El mismo `updated_at` en los
cinco es consistente con la corrida del tool goal por goal dentro de una
sola transacción.

## Migración a `harvest_excepcion`

`SELECT count(*) FROM harvest_excepcion` → **0**. El dueño no migró ninguna
campaña: las 4 candidatas naturales y el resto de las 241 siguen
resolviendo por la terna del goal de plataforma con `migracion_pendiente`,
estado legítimo y declarado en la fila. En el ciclo 61 (previo a D.2) el
resolutor contó `terna: 28` y `grupo: 4`.

## `/salud` después de D.2

El bloque `harvest_destino` de `/salud` sale de las notas del **último
ciclo**, y el último ciclo (61) corrió antes de D.2. El «después» en
`/salud` (`resueltos.grupo` incluyendo al grupo 1 y `saltos_grupo` sin
`destino_inconsistente` para sus campañas) se lee en el ciclo de las 08:41
UTC del 2026-09-16; el readback de la base ya cuenta como verificación de
la fila. Si en ese ciclo apareciera `destino_inconsistente` o
`sin_destino_de_harvest` para el grupo 1, se reabre D.2.

## Residuales

- La huella del dry-run y el `go` literal del dueño quedaron en su
  terminal, no anexados aquí; `--limpiar-terna` no escribe ledger (solo
  `edita_goal`), así que el readback de arriba es la evidencia de la base.
- Si se vuelve a correr el dry-run, debe decir «ya limpia» para los cinco
  goals; no se corrió para no repetir una lectura que no cambia nada.
