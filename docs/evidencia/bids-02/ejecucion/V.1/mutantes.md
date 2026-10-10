# Mutantes de V.1 (migracion 0061)

Un mutante por prueba nueva de `tests/test_migracion_0061.py`: cada cambio es
de una linea y pone su prueba en rojo. Verificados el 2026-10-10 corriendo
cada prueba contra su mutante (los cinco dieron `1 failed`) y restaurando
despues (la suite quedo en `5 passed`).

| Prueba | Mutante (1 linea) | Efecto |
| --- | --- | --- |
| `test_0061_rechaza_presupuesto_sin_moneda` | En `migrations/0061_bids02_campana_config.sql`: `CHECK ((presupuesto_diario IS NULL) = (presupuesto_moneda IS NULL)),` por `CHECK ((presupuesto_diario IS NULL) = (presupuesto_diario IS NULL)),` | El CHECK se vuelve tautologia: el presupuesto sin moneda entra y la prueba falla porque no hay `CheckViolation`. |
| `test_0061_rechaza_ajuste_901` | En `migrations/0061_bids02_campana_config.sql`: `COALESCE(ajuste_top_pct, 0) BETWEEN 0 AND 900` por `... BETWEEN 0 AND 901` | El ajuste de 901 entra y la prueba falla porque no hay `CheckViolation`. |
| `test_0061_rechaza_entidad_que_no_es_campana` | En `migrations/0061_bids02_campana_config.sql`: `PERFORM 1 FROM ad_entity WHERE id = NEW.ad_entity_id AND kind = 'campaign';` por `PERFORM 1;` | El trigger siempre encuentra fila: la observacion sobre un ad group entra y la prueba falla. |
| `test_0061_reversa_deja_esquema_como_estaba` | En `migrations/0061_reversa_bids02_campana_config.sql`: `DROP FUNCTION ads_campana_config_0061_kind();` por `-- DROP FUNCTION ads_campana_config_0061_kind();` | La funcion sobrevive a la reversa: la foto del catalogo difiere y la prueba falla. |
| `test_v_campana_config_vigente_trae_ultima_por_campana` | En `migrations/0061_bids02_campana_config.sql`: `ORDER BY ad_entity_id, observed_at DESC;` por `ORDER BY ad_entity_id, observed_at ASC;` | La vista trae la observacion mas vieja y la prueba falla contra el literal esperado. |

## Modulo y wiring (verificados 2026-10-10, todos MUEREN)
- `guarda_config` inserta siempre (sin el `continue` de huella igual) ->
  test_guarda_config_inserta_solo_con_cambio en rojo. (Mutante nombrado por la guia.)
- `_ajustes` escribe 0 en vez del porcentaje -> test_los_tres_placements_mapean_cada_uno_a_su_ajuste en rojo.
- `_fuera` devuelve None siempre -> test_offAmazon_vacio_none_y_lleno_json_ordenado en rojo.
- `config_de_payload` deja moneda del perfil sin presupuesto -> test_sin_budget_presupuesto_y_moneda_none en rojo.
- `config_vigente` devuelve None siempre -> test_config_vigente_trae_ultima_y_none_sin_filas en rojo.
- sync sin el hook (`written += _guardar_configs_sync` borrado) ->
  test_sync_guarda_config_y_vista_trae_cada_campana y test_sync_y_resync_estructura_en_vivo en rojo.
- sync sin el skip ("config de campana ilegible" borrado) ->
  test_sync_config_ilegible_cuenta_skip_y_escribe_entidad en rojo.
- `placementBidding` nombrado en otro archivo de app/ ->
  test_solo_campana_config_nombra_llaves_de_payload en rojo (fuga temporal, revertida).

## Panel V.1 (verificados 2026-10-10, todos MUEREN)
- `_fuera` permisivo (None en vez de ValueError) -> test_offAmazon_no_serializable_anula_la_campana en rojo.
- `budgetType` permisivo (ausente aceptado) -> test_budgetType_no_diario_o_ausente_anula_la_campana en rojo.
- Hook cuenta sin campaignId (sin el `continue`) -> test_sync_campana_sin_id_cuenta_un_solo_skip_del_plan en rojo.
- `guarda_config` sin dedup (lista en vez de dict) -> test_guarda_config_duplicada_en_una_llamada_inserta_una en rojo (UNIQUE).
- `config_de_payload` sin guardia dict -> test_item_no_dict_anula en rojo.
