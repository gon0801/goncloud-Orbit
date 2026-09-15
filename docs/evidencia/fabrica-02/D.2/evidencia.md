# E/D.2 — Terna del grupo 1 limpiada (2026-09-15)

**Qué es esto.** Evidencia (parcial, fila en `WIP`) de la fila D.2 de `plans/fabrica-02.md`. El
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
cinco viene de que el tool fija `ahora` una sola vez antes del bucle y se lo
pasa a cada `edita_goal`; cada goal confirma su propia llamada (no hay
transacción global: una corrida parcial se reanuda y lo limpio queda
limpio).

## Migración a `harvest_excepcion`

`SELECT count(*) FROM harvest_excepcion` → **0**. El dueño no migró ninguna
campaña: las 4 candidatas naturales y el resto de las 241 siguen
resolviendo por la terna del goal de plataforma con `migracion_pendiente`,
estado legítimo y declarado en la fila. En el ciclo 61 (previo a D.2) el
resolutor contó `terna: 28` y `grupo: 4`.

## Lo que falta para cerrar la fila: `/salud` después de D.2

El criterio de salida de D.2 pide **dos** cosas: terna NULL (arriba, ya
verificada) y `/salud` mostrando al grupo 1 resuelto por grupo. El bloque
`harvest_destino` de `/salud` sale de las notas del **último ciclo**, y el
último ciclo (61) corrió antes de D.2, así que al 2026-09-15 esa segunda
lectura **no existe todavía**. Por eso la fila queda `WIP` (ejecutada, no
cerrada) hasta leer el ciclo de las 08:41 UTC del 2026-09-16:
`resueltos.grupo` debe incluir al grupo 1 y `saltos_grupo` no debe traer
`destino_inconsistente` ni `sin_destino_de_harvest` para sus campañas. Con
esa lectura se cierra en un PR de docs; si aparece cualquiera de los dos
motivos, se reabre D.2 con hallazgo.

Lectura adelantada (dueño, con `!`, cero escrituras), dry-run del propio
tool el 2026-09-15 después de las 18:00 UTC:

```
grupo: id=1 platform=amazon_mx tipo_producto=kit_arras
exacta: campaign_external=145787501515469 ad_group_external=182421284463033
[ya limpia] goal=11 rol=product_targeting terna=NULL/NULL bid=11.6200
[ya limpia] goal=9 rol=category_phrase terna=NULL/NULL bid=11.6200
[ya limpia] goal=10 rol=category_broad terna=NULL/NULL bid=11.6200
[ya limpia] goal=8 rol=category_exact terna=NULL/NULL bid=11.6200
[ya limpia] goal=12 rol=auto_discovery terna=NULL/NULL bid=11.6200
candidatas: 0
huella: e3b0c44298fc1c14
```

El resolutor ya ve la exacta del grupo como destino (`exacta:` arriba) y
no queda nada por limpiar; la huella es la del conjunto vacío. Sigue
faltando la lectura de `/salud` del ciclo del 16-sep para cerrar la fila.

## Residuales

- La huella del dry-run y el `go` literal del dueño quedaron en su
  terminal, no anexados aquí; `--limpiar-terna` no escribe ledger (solo
  `edita_goal`), así que el readback de arriba es la evidencia de la base.
- Si se vuelve a correr el dry-run, debe decir «ya limpia» para los cinco
  goals; no se corrió para no repetir una lectura que no cambia nada.

## Cierre anticipado (2026-09-15 19:40 UTC): ciclo 62 corrido, D.2 verificada

David autorizó cerrar D.2 hoy en vez de esperar el ciclo del 16-sep
08:41 UTC. El mismo readback independiente del lead corrió UN ciclo del
optimizador para `amazon_mx` (no es harvest forzado; dentro de caps y
cooldowns), luego leyó `/salud` y la base. Prueba de comando:

```
$ docker exec orbit-app-1 python -m app.cli cycle --platform amazon_mx
== Ciclo del optimizador (ads_optimizer:amazon_mx) ==
cycle_id=62 status=done decisions_count=0
```

`/salud` (HTML `http://127.0.0.1:8010/salud`, tarjeta amazon_mx) después
del ciclo:

```
Ultimo ciclo: #62 · 2026-09-15 19:40:44 UTC · decisiones: 0 · applies: 0
Destino de harvest: por grupo 4 · por excepcion 0 · por terna 28
(Destino por terna del goal (migracion a grupo o excepcion pendiente))
```

`por grupo 4` significa que el resolutor sigue dejando notas de destino
por grupo en sus 4 entidades fuente; los 28 restantes siguen con la terna
del goal de plataforma con `migracion_pendiente` (estado legítimo).

### Checks de /salud (los tres, verificados en base tras el ciclo)

1. **Grupo 1 resuelto por grupo con la exacta como destino.** En
   `campana_grupo_rol` el rol `category_exact` del grupo 1 apunta a
   campaign `145787501515469` (id interno 415287) y ad group
   `182421284463033` (id interno 415462) — exactamente el par que el
   dry-run del dueño reportó como `exacta:`. El resolutor cuenta los
   destinos por grupo (`por grupo 4` en /salud) y las consultas de
   destino del grupo 1 no traen terna.
2. **Contadores del grupo 1 en cero.** `harvest_job` tiene 2 filas en
   total (set de arras, arras matrimoniales de oro; ambas fase `done`,
   previas a D.2) y **ninguna** apunta a entidades fuente del grupo 1:
   join con `campana_grupo_rol grupo_id=1` → 0 filas. Las campañas del
   grupo 1 no han producido harvest jobs en ningún ciclo.
3. **Sin motivos `destino inconsistente`.** El JSON de skips del ciclo 62
   en /salud (entidad y termino, amazon_mx) no contiene ningún motivo
   `destino_inconsistente`; tampoco `sin_destino_de_harvest`. Los skips
   del ciclo 62 son los declarados (campana_no_enabled, cooldown,
   entidad_inerte, etc.).

### Confirmación base de los goals (readback directo, 19:5x UTC)

```sql
select id, mode, harvest_campaign_id, harvest_ad_group_id, harvest_default_bid,
       updated_at from ads_optimizer_goal where id between 8 and 12;
```

Los cinco goals del grupo 1 siguen `shadow` con terna NULL/NULL y bid
11.6200 intacto — nada de este ciclo los tocó (ni fue a tocarlos).

## Veredicto

**D.2 cerrable: SÍ.** Ambos criterios de salida están verificados hoy:
terna NULL (16:57 UTC, dry-run del dueño + readback de base) y /salud
mostrando el grupo 1 resuelto por grupo con la exacta como destino
(ciclo 62, 19:40 UTC), sin motivos de inconsistencia y con contadores
del grupo 1 en cero. El PR #285 pasa de WIP a cerrado.
