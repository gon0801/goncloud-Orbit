# A.3d — caso reproducible del bloqueo (rama `feat/ads-proteccion-a3`)

Base: `app/ads/salud.py` (`b40c215`), migracion `0041_ads_ingest_alert.sql`.
La tabla `ads_ingest_incident` admite UN episodio abierto por
(scope, tipo) via indice unico parcial `WHERE closed_at IS NULL`; abrir
duplicado hace `ON CONFLICT DO NOTHING` (el evento se pierde en silencio).

## Secuencia 1 — fallo nuevo tras recovery pendiente: CUBIERTA

1. Run principal falla -> episodio (fallo) abierto, Telegram ok
   (`alert_sent_at` set).
2. Run ok -> `recovered_at` set; como el aviso salio, sigue abierto con
   recovery pendiente.
3. Telegram caido -> `recovery_sent_at` NULL, reintentos acotados, sigue
   abierto.
4. Otro run falla -> `procesar_run` ejecuta `_SQL_CANCELAR_RECUPERACION`
   (cierra el viejo con `recovery_cancelled_at`) y `_SQL_ABRIR` abre el
   nuevo. El fallo nuevo NO se pierde.

## Secuencia 2 — atraso nuevo tras recovery pendiente: HUECO

1. Chequeo 10:30 sin exito de hoy -> episodio (atraso) abierto, Telegram ok.
2. Exito posterior -> `recovered_at` set, sigue abierto, recovery pendiente.
3. Siguiente 10:30 sigue sin exito de hoy -> `comprobar_atraso` ejecuta
   `_SQL_ABRIR` directo, SIN cancelar primero -> `ON CONFLICT DO NOTHING`
   por el episodio abierto del paso 1 -> **el atraso nuevo se pierde**: ni
   episodio nuevo ni aviso nuevo; `/salud` solo muestra el recovery
   pendiente del incidente viejo.

## Opciones (decide el dueno)

- (a) Autorizar correccion: cancelar simetrico en el path de atraso
  (`_SQL_CANCELAR_RECUPERACION` antes de `_SQL_ABRIR` en `comprobar_atraso`)
  + tests rojos previos de ambas secuencias. Efecto: el recovery pendiente
  del incidente viejo se cancela (queda `recovery_cancelled_at`) y se avisa
  el atraso nuevo.
- (b) No autorizar: A.3/A.4/C.6 siguen pausadas.

Decision literal 25-sep-2026: "Corregir simetrico (Recomendado)" — autorizada
la correccion (a). A.3 se reanuda tras el merge de A.2.
