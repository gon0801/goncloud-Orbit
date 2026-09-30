# H6/H7 — estado 2026-09-25 ~20:05 UTC, actualizado 2026-09-30 UTC

La sombra H5 tiene cinco fechas de ciclo observadas (25-29 sep, ciclos
82-91, evidencia en `H5/ciclo-1..5.txt`); no son 120 horas completas desde
el flip del 25-sep. H5.4 se ejecuto con go separado el 30-sep 03:04 UTC:
goals 4-12 `live`, readback 9/9 (`H5/live-h5-4.md`). Los primeros ciclos
live 92/93 terminaron `done`, con 69 bids confirmados y tres bids
`fuera_de_cap` (`H5/primer-live-2026-09-30.md`); B.4 sigue WIP hasta
integrar #373. A.4 sigue abierta: el cron real `ads-salud` corrio desde
10:30 UTC del 30-sep; observacion 1/7, faltan seis dias (PR #374).

## H6 (proteccion economica)

| pieza | estado |
| --- | --- |
| C.2b riesgo aceptado | LISTO: aceptacion del dueno 2026-09-25 (literal Aceptado, `evidencia/ads-proteccion-h5:28d34d8`) |
| C.3/C.4 merge | LISTO: #342 en `787869a` |
| C.5 merge | LISTO: #344 en `ad79eeb` |
| C.5 deploy (0041+0042+0043 + codigo) | LISTO hoy: `H6/deploy-c5.md` (tablas activas, endpoint 200/401, sombra intacta) |
| C.5 pausa manual en Amazon | BLOQUEADO (la hace David; ver paso exacto abajo). Hoy `proposals open = []`: aun no hay propuesta open sobre la que actuar. |
| C.6 shadow economico 5 ciclos | BLOQUEADO hasta cerrar A.4 y B.4 (primer live H5.4 verificado; B.4 espera integrar #373; A.4 observa el cron, 1/7; C.6 exige su propio INICIO_SHADOW, runbook H6.4). Sin `H6/inicio.txt` en ningun lado: el arranque lo crea con el paso H6.4 del runbook. |
| `ads_pause_economica` | ausente en `config_version` 21 (fail-closed False): el vivo economico sigue apagado. |

## H7 (cierre)

- Ledger: este archivo + `H6/deploy-c5.md` + `C.5/cierre.md` en master;
  `H5/ciclo-4..5.txt` y `H5/live-h5-4.md` estan en el PR #373 sin merge.
- Rama de cierre: `docs/c5-deploy-cierre-h6` (desde `ad79eeb`), pusheada,
  SIN merge (prohibido sin orden).
- Pendiente para el PR de cierre: cross-review opus del diff (solo docs de
  evidencia, sin codigo) y el `APROBADO` explicito; luego go de merge del dueno.
- AppFlowy H6/H7: no tocado (sin skill a la mano en este turno; lo mueve el lead).

## PASO EXACTO PARA DAVID 1/2 — pausa manual Amazon (C.5, cuando haya propuesta open)

Hoy no hay propuestas open (`GET campaign-proposals?status=open` -> `[]`).
Cuando el motor abra una (el sync + ciclo las crean solas), sobre ESA propuesta:

1. Abre `/propuestas` y anota: `campaign_external_id`, `platform`
   (`amazon_us` = US, `amazon_mx` = MX) y `profile_id`.
2. En `advertising.amazon.com`, perfil = `platform`/`profile_id`, Campanas,
   busca por ID, abrela, pulsa **Pausar**. Verifica chip **Paused**.
3. Del lado Orbit NO hagas nada: el proximo sync + ciclo la cierra sola con
   readback PAUSED (`status = paused_external`). Verificala en
   `/propuestas` o `GET .../campaign-proposals?status=paused_external`
   con `campaign_status = PAUSED`.
4. Si tras 24h sigue open con campana Paused: incidencia (no re-pausar).
5. Alternativa sin Amazon: boton **Descartar** en la fila (cierra en Orbit
   como `dismissed`, la campana sigue ENABLED).

## PASO EXACTO PARA DAVID 2/2 — permiso vivo (H5.4 + C.6)

1. H5.4 (flip de vuelta a live de B.4): EJECUTADO con el go separado del
   dueno el 30-sep 03:04 UTC para `$IDS_LIVE = 6,7,4,5,11,9,10,8,12`.
   Transaccion y antes/despues en `H5/live-h5-4.md`. Falta verificar el
   primer ciclo live esta en `H5/primer-live-2026-09-30.md`; no repetir el UPDATE.
2. C.6 (shadow economico 5 ciclos + su propio live): PRIMERO tienen que
   estar cerrados A.4 (observacion 7 dias + readbacks H4) y B.4 (5/5 ciclos
   sombra + live H5.4), cada uno con su evidencia; el plan declara
   `C.6 Depends: C.3, C.5, A.4, B.4`. Despues de eso, requiere tu go de
   deploy (efecto cero-applies + acuerdo D.3) y luego go de live con el
   riesgo C.2b citado. Sin A.4+B.4 cerrados y sin tus dos literales no arranca.
3. Para C.6 siguen haciendo falta sus dos gos separados en orden:
   `C.6 deploy GO ...` con efecto cero-applies + acuerdo D.3 y, tras su
   propia sombra, `C.6 live GO ...` citando el riesgo C.2b. El go H5.4
   ejecutado solo cubre B.4, no C.6.
