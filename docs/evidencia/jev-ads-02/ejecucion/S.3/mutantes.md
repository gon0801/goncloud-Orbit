# S.3: mutantes (uno por prueba nombrada)

Procedimiento por mutante: aplicar el cambio de una línea, correr la prueba
nombrada (rojo), revertir, correr de nuevo (verde). Sin claves `jev.*`.
DSN de superusuario para Comprueba: `ORBIT_TEST_DSN="postgresql://orbit:***@localhost:5432/postgres"`.

| # | Prueba | Mutante (una línea) | Rojo observado | Revertido |
|---|--------|---------------------|----------------|-----------|
| M1 | `test_perimetro_jev_con_login_real` | En `0052_jev_senales.sql`: comentar `REVOKE ALL ON TABLE jev_senal FROM app_decide, app_ingest;` | `1 failed`: `RaiseException: 0052: app_decide/app_ingest no deben leer jev_senal` (el bloque DO aborta el apply) | sí, `1 passed` |
| M2 | `test_senal_ajena_exige_roster_probado_y_ordenes_otros` | `roster_probado AND miembros` → `roster_probado OR miembros` en `jev_senal_ajena_exige_roster` | `1 failed`: `DID NOT RAISE CheckViolation` (el INSERT con `roster_probado=false` entra) | sí, `1 passed` |
| M3 | `test_senal_moneda_de_plataforma_rechaza_mx_con_usd` | `amazon_mx AND moneda = 'MXN'` → `amazon_mx AND moneda = 'USD'` | `1 failed`: `DID NOT RAISE CheckViolation` (MX con USD entra) | sí, `1 passed` |
| M4 | `test_lote_acepta_intencion_y_resultado_encadenados` | Quitar `'lote'` del `IN` de `jev_revision_sujeto_tipo_check` | `1 failed`: `CheckViolation ... violates check constraint "jev_revision_sujeto_tipo_check"` | sí, `1 passed` |
| M5 | `test_tablas_nuevas_rechazan_update_delete_y_truncate` | `BEFORE UPDATE OR DELETE ON jev_corrida` → `BEFORE UPDATE ON jev_corrida` | `1 failed`: `DID NOT RAISE RestrictViolation` (el DELETE entra) | sí, `1 passed` |
| M6 | `test_vista_senal_vigente_marca_vencida_y_roster_revocado` | `valida_hasta > now() AND NOT EXISTS` → `... OR NOT EXISTS` en `jev_senal_vigente` | `1 failed`: vencida y revocada salen `True` en vez de `False` | sí, `1 passed` |
| M7 | `test_compose_db_no_recibe_ningun_dsn` | Borrar la línea `ORBIT_DSN_JEV: ${ORBIT_DSN_JEV}` de `docker-compose.yml` | `1 failed`: `app recibe DSNs distintos de los cinco de servicio: ['ADMIN', 'DECIDE', 'INGEST', 'READ']` | sí, `1 passed` |

Notas:

- M1 cae en el bloque DO del apply, no en los SELECT con login: el DO
  espeja el perímetro y aborta antes. La pata viva (login real) quedó
  probada aparte: pre-B `app_decide` leía `jev_revision` (`count 0` sin
  error); con B, `InsufficientPrivilege`.
- M5: el TRUNCATE por tabla usa `CASCADE` porque sin él Postgres rechaza
  por la FK antes de disparar el trigger (`FeatureNotSupported`, no el
  candado). La presencia por tabla del trigger STATEMENT la fija además
  `test_migracion_b_trae_append_only_por_tabla` (estática, 10 triggers).
- Tras los 7: `diff` contra respaldos limpio (sin restos de mutantes).
