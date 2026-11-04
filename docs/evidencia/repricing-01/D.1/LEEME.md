# REPRICING 01 D.1 — despliegue en sombra MX

Estado: **cerrado por lectura de producción el 2026-09-23 UTC**,
tras la corrida automática del 22-sep. El despliegue
y la automatización quedaron instalados el 2026-09-20 UTC. Las corridas
automáticas del 21 y 22-sep cumplieron el criterio aprobado por el dueño.

## Despliegue

- Autorización del dueño: literal `!` en la sesión.
- SHA desplegado: `f301cf442a2a4edf90564c61c41faa39c4b91eb7`, igual a
  `origin/master` y sin commits adicionales.
- Respaldo reversible:
  `/mnt/data/appdata/orbit/predeploy-D1-20260920-035304`.
- La migración 0039 ya estaba aplicada y `apply_cap_de_config('precio:amazon_mx')`
  devolvió `5`; no se volvió a ejecutar.
- `git archive` copió `app`, `Dockerfile`, `.dockerignore`, `pyproject.toml`,
  `uv.lock` y `tools/fabrica_campanas.py`. La comparación SHA-256 archivo por
  archivo terminó con todos los archivos en `OK`.
- Imagen resultante:
  `sha256:4e885e3cfef5f6f536964d87ffa8f4a4980264946cbbfe98cd2a328bf2b2f468`.
- Readback: contenedor `running`; `/health` devolvió `{"status":"ok"}`;
  `/precios`, `/salud` y `/api/dashboard/precios` devolvieron HTTP 200; bind en
  `127.0.0.1:8010`; los tres DSN estaban presentes y los secretos conservaron
  permisos `600`.

## Cron

Se guardó el crontab previo en:

`/mnt/data/appdata/orbit/archive/crontab-gon.20260920-035601.repricing-d1`

La línea quedó instalada exactamente una vez y el resto del crontab quedó
idéntico:

```cron
10 13 * * * /usr/bin/flock -n /tmp/precio-corrida.lock docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1
```

El orden diario relevante es costos/FX/ledger a las 08:15 UTC y precios a las
13:10 UTC.

## Goals shadow

El dueño eligió los mismos tres productos de A.4. Los dry-runs y huellas fueron:

| listing | goal | margen visto | P actual | P estrella | huella | goal id |
|---:|---:|---:|---:|---:|---|---:|
| 1213 | 35.00% | 34.95% | 988.00 MXN | 989.06 MXN | `a3698bd398d97c67` | 6 |
| 1284 | 53.00% | 52.89% | 1288.00 MXN | 1292.99 MXN | `fe33016d3715ed74` | 7 |
| 1295 | 59.50% | 59.19% | 699.00 MXN | 708.47 MXN | `6b3350821fb5ab69` | 8 |

Readback: tres goals vigentes `shadow`, `go_literal IS NULL` y cero goals
vigentes `live`.

## Preflight temprano del 2026-09-20

La corrida manual de las 03:xx UTC terminó así:

```text
listing=1213 amazon_mx shadow no_evaluado costo_desactualizado
listing=1284 amazon_mx shadow no_evaluado costo_desactualizado
listing=1295 amazon_mx shadow no_evaluado costo_desactualizado
decisiones=3 escritas=0
```

Esto fue antes del refresco diario de costos de las 08:15 UTC. La base confirmó
seis decisiones totales después de la corrida, los mismos seis cambios de A.4
que había antes, cero cambios shadow del día y cero cambios shadow aplicados.
Por ser append-only y existir el único `(listing, platform, decision_date)`, no
se borra ni se reescribe: esta corrida es preflight y no cuenta entre las cinco
corridas válidas.

## Cobertura observada

El recuadro cuadró el 2026-09-20 como `260 = 0 + 105 + 155 + 0`. Los 105 no
evaluados fueron 102 `canal_sin_dato` y los tres goals con
`costo_desactualizado`. `listing` mostró 342 identidades, que no equivalen a
342 publicaciones activas del bridge.

Las cifras 264/284 escritas originalmente en D.1 son una foto histórica. La
fuente canónica vigente tiene 260 activas MX (`BUYABLE` o
`BUYABLE,DISCOVERABLE`) y `listing` tiene 342 identidades. Orbit no guarda la
cuenta de activas del bridge; se informa como desconocida, sin convertir la
identidad en ese número. No se fuerzan los valores viejos.

## Criterio de cierre aprobado

- Leer una corrida automática válida a partir del 2026-09-21 UTC, después del
  refresco diario de costos.
- Exigir los tres goals evaluados, es decir, 3/3 en vez del umbral anterior de
  ≥80%.
- Reproducir manualmente la cuenta de los tres productos sobre esa corrida.
- Comprobar la trayectoria y el cooldown con las pruebas automatizadas ya
  existentes.
- Confirmar que no apareció ningún PATCH fuera de A.4.
- Completar revisión, PR y CI después de esa corrida.

El dueño aprobó este criterio en la sesión. Las cinco corridas quedan como
seguimiento operativo no bloqueante y no condicionan el merge.

## Cierre observado el 2026-09-23

Lectura como `orbit_read` dentro de `orbit-app-1`, sin escritura:

```sql
SELECT decision_date, listing_id, resultado, motivo, mode, m_actual, goal,
       p_actual, created_at
FROM precio_decision
WHERE decision_date IN (DATE '2026-09-21', DATE '2026-09-22')
ORDER BY decision_date, listing_id;

SELECT count(*) FILTER (WHERE mode = 'live') AS live,
       count(*) FILTER (WHERE mode = 'shadow') AS shadow
FROM precio_goal WHERE valid_to IS NULL;

SELECT id, listing_id, estado, es_reversa, enviado_at
FROM precio_cambio ORDER BY id;
```

| Día UTC | Decisiones automáticas | Goals evaluados | Resultado | Cambios aplicados |
|---|---:|---:|---|---:|
| 2026-09-21 13:10:01 | 3 | 3/3 | `mantener(cooldown)` ×3 | 0 |
| 2026-09-22 13:10:02 | 3 | 3/3 | `mantener(cooldown)` ×3 | 0 |

Los tres listings fueron 1213, 1284 y 1295. Sus márgenes observados fueron
34.95%, 52.89% y 59.19% frente a goals de 35.00%, 53.00% y 59.50%. La
configuración vigente fija `precio_dias_entre_cambios = 7`; los cambios
confirmados de A.4 salieron el 19-sep. El 21-sep habían pasado 2 días y el
22-sep, 3. En ambos días el resultado `mantener(cooldown)` coincide con la
regla `días < 7`. La reversa del 20-sep no se contó como cambio nuevo.

El recuadro canónico cuadró ambos días: `260 = 3 evaluadas + 102
canal_sin_dato + 155 sin_goal + 0 fuera_de_alcance`. La lectura actual mostró
cero goals `live`, tres `shadow` y seis filas en `precio_cambio`, exactamente
los tres cambios y sus tres reversas de A.4. Orbit no registró otro
intento de cambio.

Readback operativo del 23-sep: respaldo
`predeploy-D1-20260920-035304` presente; imagen
`sha256:4e885e3cfef5f6f536964d87ffa8f4a4980264946cbbfe98cd2a328bf2b2f468`
en estado `running`; `/health` y `/precios` HTTP 200; línea de precio una sola
vez en el crontab de `gon`. El 23-sep aún no tenía decisión a las 01:42 UTC,
antes del cron de las 13:10 UTC; ese estado no altera el cierre del 21-sep.
