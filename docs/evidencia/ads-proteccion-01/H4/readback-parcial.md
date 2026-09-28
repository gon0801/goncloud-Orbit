# H4 / A.4 — readback parcial de la ingesta vigilada (días 1-2 de 7)

Leído 2026-09-28 ~03:00 UTC por el lead, solo lectura (`/api/dashboard/salud` y
`BEGIN READ ONLY` … `ROLLBACK`). A.3 se desplegó con C.5 el 2026-09-25 19:50 UTC;
la observación de 7 días termina el 2026-10-02.

## Éxito principal por día (`amazon_ads_reports_v3`)

| Día UTC | Corrida | ok | Filas escritas | Perfiles `written` |
| --- | --- | --- | --- | --- |
| 2026-09-26 | 460 (07:10-07:22) | t | 9903 | MX y US, 4 reportes cada uno |
| 2026-09-27 | 479 (07:10-07:51) | t | 9987 | MX y US, 4 reportes cada uno |

`/salud` muestra para los dos perfiles `ultimo_estado = written` y
`metric_date = 2026-09-26`. Criterio (i) de H4 (éxito ≥ 3 de 7 días): 2 de 2 hasta hoy.

## Hallazgo: fallo global falso que nunca se recupera

`ads_ingest_incident` id 1: `tipo = fallo`, global (sin perfil ni plataforma),
abierto por la corrida 460 (26-sep 07:22 UTC), aviso enviado por Telegram
(`alert_sent_at` 07:22:05), `recovered_at` vacío. `/salud` lo muestra abierto.

Causa: cada corrida registra el perfil 1104602105442735 como `rejected` con
motivo `pais no soportado: CA` y plataforma vacía (rechazo intencional de
`evaluar_perfiles`, `app/ads/structure_api.py:223-226`). `incidentes_de_run`
(`app/ads/salud.py:34`) trata `rejected` como fallo y, sin plataforma, lo manda
al alcance global `(None, None, "fallo")`. `procesar_run` solo recupera el global
si ese alcance no está entre los fallos de la corrida, así que ninguna corrida
lo cierra.

Efectos:

1. Un aviso falso de "fallo de ingesta principal: global" el 26-sep.
2. El episodio global queda abierto para siempre. Como `_SQL_ABRIR` hace
   `ON CONFLICT DO NOTHING` sobre el episodio abierto, **un fallo global real
   no abriría episodio ni mandaría aviso**: el aviso global está ciego desde
   el 26-sep.
3. Si se arregla solo el código, la siguiente corrida "recupera" el global y
   manda un aviso de recuperación falso.

Arreglo propuesto (brief aparte): un perfil `rejected` sin plataforma de Orbit
no abre fallo; cerrar el episodio 1 a mano después del deploy del arreglo y
antes de la siguiente corrida, sin aviso de recuperación.

## Pendiente de A.4

- Días 3 a 7 (28-sep a 2-oct).
- El arreglo del fallo global falso, su deploy y el cierre del episodio 1.
- Criterio (ii): sin fallo real hasta hoy; si no hay ninguno en 7 días, A.4
  cierra como "desplegado, recovery pendiente de fallo real" (H4).
