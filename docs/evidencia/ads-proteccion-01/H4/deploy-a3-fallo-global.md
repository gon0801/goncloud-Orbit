# A.3 — deploy del arreglo del fallo global falso (#369)

2026-09-28 03:51 UTC, por el dueño con `!`, con
[`deploy-codigo-2d29ef2.sh`](deploy-codigo-2d29ef2.sh). Solo código, sin
migraciones: `app/ads/salud.py` y `app/ads/structure_api.py` (un perfil de país
no soportado ya no abre fallo global). El bug y su causa están en
[`readback-parcial.md`](readback-parcial.md).

```text
APROBADO=2d29ef2ee1dad3158fcabd5186bed82e2ba55233 (CI success)
preflight: ok|0|0|0|0|1   (sin ciclo running, sin ingesta a medias, cero harvest en vuelo, episodio 1 abierto)
prod = 649d1044593885066c0121e374f463432a78761d (md5 salud.py, structure_api.py)
respaldo: predeploy-20260928-0351/
md5 OK app/ads/salud.py, app/ads/structure_api.py, app/api_dashboard.py, app/cycle.py, app/optimizer/goals.py
DIGEST antes=sha256:d6fd2f5f277bc8a9d3d14a6c82b37489fd1735742c6bfa89eb291d1aae5a2f7e
DIGEST despues=sha256:f84b0a350b89a7e64c97a3fdc943b44d6f7c2dea372b502184d8f5b1d01ba122
COPY app ./app  DONE (no CACHED); orbit-app-1 Recreated
{"status":"ok"}   /salud HTTP 200
episodio 1 cerrado (UPDATE ... RETURNING id = 1)
abiertos=0
1|fallo|460|2026-09-26 07:22:06||2026-09-28 03:52:08
```

El episodio 1 quedó cerrado sin `recovered_at` ni aviso de recuperación: con el
código nuevo, la ingesta de las 07:10 UTC lo habría "recuperado" y mandado un
aviso falso. El primer intento (03:42 UTC) se detuvo en el paso 1 porque la CI
de `master` seguía corriendo; no tocó el server.

Reversa del código: `predeploy-20260928-0351/` + rebuild. El cierre del
episodio 1 no se revierte (no hay nada que recuperar: el fallo no era real).

Qué verificar en la ingesta del 28-sep (07:10 UTC): el perfil CA sigue
`rejected` y `ads_ingest_incident` no gana filas nuevas.
