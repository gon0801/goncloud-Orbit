# 0.b: mutantes (obligatorio + uno por prueba nueva)

Procedimiento por mutante: aplicar el cambio, correr la prueba nombrada
(rojo), revertir, correr de nuevo (verde). DSN de superusuario para
Comprueba: `ORBIT_TEST_DSN="postgresql://orbit:***@127.0.0.1:5433/postgres"`.

| # | Prueba | Mutante | Rojo observado | Revertido |
|---|--------|---------|----------------|-----------|
| M0 | `test_v_cambio_bid_regreso_del_dueno` | Obligatorio de la guia: en `0060_bids02_base_lectura.sql`, borrar la segunda rama de `v_cambio_bid` (el `UNION ALL` + el `SELECT` de reversas) | `1 failed`: `AssertionError: assert [] == [(3, datetime..._dueno', ...)]`, `Right contains one more item: (3, datetime.datetime(2026, 9, 22, 12, 0, ...), None, Decimal('10'), 'MXN', 'regreso_del_dueno', ...)` | si, `1 passed` |
| M1 | `test_v_hoja_activa_exige_triple_enabled` | `sg.status = 'ENABLED'` → `'PAUSED'` en `v_hoja_activa` | `1 failed`: `At index 0 diff: (9, 'amazon_mx', 'keyword', 8, 7, 'exact', Decimal('10.0000'), 'MXN') != (3, 'amazon_mx', 'keyword', 2, 1, 'exact', Decimal('10'), 'MXN')` (entra la de grupo pausado, sale la viva) | si, `1 passed` |
| M2 | `test_v_hoja_activa_tipo_campana_cinco_casos` | `WHEN k.match_type = 'EXACT' THEN 'exact'` → `THEN 'broad'` | `1 failed`: `Differing items: {9: 'broad'} != {9: 'exact'}` | si, `1 passed` |
| M3 | `test_v_cambio_bid_motor_live_y_sus_exclusiones` | `oc.mode = 'live'` → `'shadow'` en la rama motor | `1 failed`: `At index 0 diff: (..., 'motor', 3) != (..., 'motor', 1)`, `Right contains one more item: (..., 'regreso_por_desplome', ...)` (entra la shadow, salen las live) | si, `1 passed` |
| M4 | `test_v_cambio_bid_regreso_del_dueno` | `d.old_value` → `d.new_value` como `bid_despues` de la rama `regreso_del_dueno` | `1 failed`: `At index 0 diff: (..., None, Decimal('8'), 'MXN', 'regreso_del_dueno', 1) != (..., None, Decimal('10'), 'MXN', 'regreso_del_dueno', 1)` | si, `1 passed` |
| M5 | `test_vistas_legibles_para_decide_read_admin` | Agregar `REVOKE SELECT ON v_cambio_bid FROM app_read;` tras los GRANT | `1 failed`: `psycopg.errors.RaiseException: 0060: privilegios de v_hoja_activa/v_cambio_bid invalidos` (el bloque DO aborta el apply) | si, `1 passed` |
| M6 | `test_gasto_para_concluir_defaults_por_plataforma` | `"amazon_mx": Decimal("350")` → `Decimal("351")` | `1 failed`: `AssertionError: assert Decimal('351') == Decimal('350')` | si, `1 passed` |
| M7 | `test_gasto_para_concluir_invalido_falla_cerrado` | El `except InvalidOperation` devuelve el default en vez de `raise ValueError` (fail-open) | `1 failed`: `Failed: DID NOT RAISE ValueError` | si, `1 passed` |
| M8 | `test_paso_guardas_nombra_test_arq_bids` | Quitar ` tests/test_arq_bids_*.py` de `quality.yml:78` | `1 failed`: `AssertionError: el paso de guardas debe correr tests/test_arq_bids_*.py ...`, `assert 'tests/test_arq_bids_*.py' in 'pip install ...'` | si, `1 passed` |

Notas:

- M5 cae en el bloque DO del apply, no en los SELECT con login (mismo
  caso que S.3 M1). La pata viva (login real) quedo probada por el verde:
  cada rol lee `count(*) = 1` en las dos vistas.
- M5 bis (mudo, descartado): quitar `app_read` del GRANT NO pone la prueba
  en rojo, porque el `ALTER DEFAULT PRIVILEGES` de 0001 le otorga SELECT
  de todos modos (verificado: `has_table_privilege('app_ingest', ...)` da
  true sin GRANT explicito). Por eso el mutante es un REVOKE de una linea.
- M1 muestra `Decimal('10.0000')` contra `Decimal('10')`: la vista publica
  `money_amount` (escala 4) y el fixture inserta `Decimal("10")`; en Python
  son iguales y el verde lo confirma.
- Tras los 9: `grep` de restos limpio y los 4 archivos de Comprueba en
  `62 passed`.
