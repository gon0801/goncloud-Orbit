# C.5 — cierre humano: descarte en Orbit + pausa manual en Amazon (PR, sin merge)

Fecha: 2026-09-25 UTC. Rama: `feat/ads-proteccion-c5` (base `origin/master`).
Alcance: codigo + tests + documento del PR. PROHIBIDO unir a main.
La pausa en Amazon la ejecuta David a mano; Orbit solo verifica (read-only).

## Contrato implementado (DoD fila 103 del plan)

| DoD | Donde | Test |
| --- | --- | --- |
| Canal visible + descarte autenticado con rol minimo | `POST /api/ads-optimizer/propuestas-campana/<id>/descartar` (`app/api_write.py`, token `x-orbit-token` + DSN admin); boton Descartar en `/propuestas` (`cortes.html` + `cortes.js`) | `test_endpoint_descartar_auth_ciclo_y_errores`, `test_cortes_muestra_descartar_solo_en_open`, `test_grant_admin_minimo_solo_cierre` |
| Rol minimo, sin GRANTs nuevos salvo revision | `migrations/0043_ads_propuesta_descarte_admin.sql`: `app_admin` UPDATE solo en 5 columnas de cierre; sin INSERT ni dinero | `test_grant_admin_minimo_solo_cierre`, `test_migracion_0043_parsea` |
| Propuesta revisada con campana ENABLED sigue pendiente | `transicion open + ENABLED + riesgo = actualizar` (sin cambios) | `test_revisada_con_enabled_sigue_pendiente` |
| Descarte no muta Amazon | `descartar_propuesta` no construye cliente HTTP; cero `/sp/campaigns` en el camino de escritura | `test_descartar_no_muta_amazon`, `test_modulo_propuestas_sin_cliente_amazon_en_runtime`, `test_cero_ruta_sp_campaigns_en_camino_de_escritura` |
| PAUSED externo cierra con snapshot/fecha, sin autor | open + PAUSED -> `paused_external` (`estado_pausado_externo`), `close_evidence.cierre` + refresh de `campaign_status`/`status_synced_at`; `profile_id` del ultimo `written` de `campanas` (0040) o NULL | `test_paused_externo_cierra_con_snapshot_sin_autor`, `test_profile_id_se_resuelve_de_ads_report_result`, `test_profile_id_null_sin_fuente` |
| Cola descendiente no aplica tras el readback | `apply.gate_ancestros` bloquea la hoja con `campana_no_enabled` (cache del sync; sin cambios) | `test_cola_descendiente_no_aplica_tras_paused` |
| Cron repetido no duplica ni reabre | `dismissed`/`paused_external` + PAUSED = mantener; reabre solo con ventana limpia (reset) + riesgo nuevo | `test_cron_repetido_no_duplica_ni_reabre`, `test_descartar_repetido_no_duplica_y_avisa_cierre`, `test_descartada_requiere_ventana_limpia_para_episodio_nuevo` |
| Cero llamadas de mutacion de campana | Estatico (arriba) | `test_cero_ruta_sp_campaigns_en_camino_de_escritura` |

## Decisiones de implementacion

1. Dos carriles convergieron en este worktree (rama compartida): se adopto
   UN contrato (nombres `descartar_propuesta`/`PropuestaYaCerrada`, ruta
   `/propuestas-campana/<id>/descartar`, `test_cierre_campana.py` referenciado
   por 0043) y se porto lo util del otro (UI, bordes de auth, candados
   estaticos, ciclo dismissed->reset->nuevo episodio). Sin duplicados.
2. `report_name` del perfil = `'campanas'` (`cfg["nombre"]` de REPORTES_CFG,
   lo que prod guarda en `ads_report_result`): los seeds que usaban el
   `reportTypeId` (`spCampaigns`) se corrigieron, porque con el filtro
   correcto no matcheaban.
3. El cierre readback refresca `campaign_status`/`status_synced_at` de la fila
   (la pantalla muestra la PAUSED leida) y anida la evidencia bajo `cierre`.
   La cola de `test_propuestas_campana.py` que esperaba `paused_observed` para
   ese caso se actualizo a `paused_external` (cambio de comportamiento C.5,
   no regresion).
4. El harness `_db_temporal` (test_cycle, test_cycle_apply) aplica 0040: la
   DB de prueba es la de produccion y `_persiste` lee `ads_report_result`.

## Paso manual exacto para David (DETENERSE AQUI — no tocar produccion)

Sobre una propuesta `open` concreta (ejemplo: campana A1U):

1. Abre la pantalla `/propuestas` de Orbit y anota de la fila open:
   `campaign_external_id` (= campaignId en Amazon), `platform`
   (`amazon_us` = cuenta US, `amazon_mx` = cuenta MX) y `profile_id`
   (cuenta exacta para el readback) — o `GET
   /api/ads-optimizer/campaign-proposals?status=open`.
2. En tu navegador ve a `advertising.amazon.com`, elige el perfil/cuenta que
   corresponde a `platform` (+ `profile_id` si hay varias), entra a
   **Campanas**, busca la campana por su ID (`campaign_external_id`),
   abrela y pulsa **Pausar**.
3. Verifica ahi mismo que su estado quedo **Paused** (chip gris). Si sigue
   Enabled/Delivering, NO sigas: la pausa no se aplico.
4. Listo del lado Amazon. Del lado Orbit NO hagas nada: el proximo sync de
   estructura + ciclo cierra la propuesta solo con readback `PAUSED` del
   mismo campaignId/profile (`status = paused_external`,
   `close_reason = estado_pausado_externo`).
5. Readback esperado en Orbit (verificar, no tocar): la fila sale de open en
   `/propuestas` (o `GET
   /api/ads-optimizer/campaign-proposals?status=paused_external`) con
   `campaign_status = PAUSED` y `closed_at` de hoy. Si tras 24h sigue open
   con campana Paused, reportarlo como incidencia (no re-pausar).
6. Alternativa sin Amazon: **Descartar** en la misma fila de `/propuestas`
   (boton Descartar -> actor + token `x-orbit-token`): cierra el episodio en
   Orbit (`dismissed`) y la campana sigue ENABLED. Solo una ventana sin
   riesgo observada habilita un episodio nuevo.

## Estado de gates (2026-09-25)

C.4 mergeado en `787869a` (PR #342). El dueno decidio propiedad C.5 opcion
(b) "si funciona hay que dejarlo": SE QUEDA TODO el paquete convergido.
Gate C.2b/C.2 LEVANTADO por decision expresa del dueno 2026-09-25; este PR
pasa a ready y se une.

## Condiciones del veredicto forense (C1-C4, aplicadas 2026-09-25)

- **C1 — 0040 ya en prod (solo lectura).** Confirmado sin escribir prod:
  `migrations/0040_ads_report_result.sql` existe en el arbol, `docs/DATABASE.md`
  documenta `ads_report_result` (0040) como fuente del `profile_id`, y
  `docs/DEPLOY.md` lo referencia como base del pipeline vigente. Sigue ahi.
- **C2 — 0043 JUNTO con el despliegue del codigo, nunca sola.** La 0043 viaja
  en este mismo PR que el codigo que la exige (endpoint + tests la referencian).
  Orden de deploy: aplicar `migrations/0043_ads_propuesta_descarte_admin.sql`
  (patron «Aplicar migraciones» de `docs/DEPLOY.md`: `psql -U orbit -d orbit
  -v ON_ERROR_STOP=1 -1`) inmediatamente antes del rebuild de `orbit-app-1`,
  en la misma ventana. Sin 0043 el descarte da 500 (grant ausente); 0043 sola
  sin codigo es un grant sin consumidor: por eso van juntas.
- **C3 — ValueError de actor vacio -> 422.** `"   "` pasa el `min_length=1` de
  pydantic y lo caza `descartar_propuesta` (`app/propuestas_campana.py:375`):
  el endpoint (`app/api_write.py`, `descartar_propuesta`) lo mapea a 422
  (mismo codigo que cuerpo invalido), nunca 500. Test:
  `test_endpoint_descartar_auth_ciclo_y_errores` (caso actor en blanco).
- **C4 — conteo real: 19 tests.** `tests/test_cierre_campana.py` trae 19
  `def test` (verificado con `grep -c`). El PR decia 24: corregido a 19.
