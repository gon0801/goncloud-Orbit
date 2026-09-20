# REPRICING 01 D.1 — despliegue en sombra MX

Estado: **en curso**. El despliegue y la automatización quedaron instalados el
2026-09-20 UTC. Falta una corrida automática válida para cerrar D.1.

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
`costo_desactualizado`. La identidad del puente mostró 342 filas.

Las cifras 264/284 escritas originalmente en D.1 son una foto histórica. La
fuente canónica vigente tiene 260 activas MX (`BUYABLE` o
`BUYABLE,DISCOVERABLE`) y el puente de identidad tiene 342; no se fuerzan los
valores viejos.

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
