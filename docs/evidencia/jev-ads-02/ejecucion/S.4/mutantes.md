# S.4 — mutantes (uno por prueba, 2026-10-06)

Cada mutante cambia una línea, pone su prueba en rojo y se revierte.
Comandos: `python3` (aplicar) + `pytest <prueba> -q` (rojo) + `cp` del
respaldo (revertir) + `pytest` (verde). Sin remanentes (`grep MUTANTE`
limpio en los archivos de S.4).

| # | Prueba | Mutante (1 línea) | Rojo |
|---|--------|-------------------|------|
| 1 | `test_no_se_paga_dos_veces` | `app/jev_libro.py` `juicio`: `if exito is not None:` → `if False:` | `assert 4 == 3` (la ficha compartida se paga 2 veces) |
| 2 | `test_tope_aguanta_una_caida` | `app/jev_libro.py` `juicio`: `if self.llamadas_de_hoy() >= tope_diario:` → `if False:` | `assert 'completa' == 'tope'` (paga 4 con tope 3) |
| 3 | `test_apagado_es_cero` | `app/jev_senales.py` `correr` (aplicar): `if isinstance(ajustes, Apagado):` → `if False:` | `AttributeError: 'Apagado' object has no attribute 'min_clics'` |
| 4 | `test_sin_clave_sella_ventas_sin_intenciones` | `app/jev_senales.py` `_aplicar`: `motivo_base = None if api_key else "sin_api_key"` → `motivo_base = None` | `assert 'completa' == 'sin_api_key'` (abre intenciones sin clave) |
| 5 | `test_universo_de_punta_a_punta` | `app/jev_senales.py` `_roster`: `recortado = anunciados_hoy(con_fichas)` → `recortado = con_fichas` | `At index 0 diff: 'sin_lectura' != 'ajena'` |
| 6 | `test_madurez_otro_grupo_vs_historial` | `app/jev_senales.py` `_economia_de`: `historial, obs_historial = _historial(...)` → `= None, None` | `assert 'sin_lectura' == 'vendio_aqui'` |
| 7 | `test_harvest_sin_destino_legible` | `app/jev_senales.py` `_propuestas`: `ilegibles.append(cola)` → `pass` | `assert 1 in []` |
| 8 | `test_seco_por_omision` | `app/jev_senales.py` `_seco`: `if api_key and restantes:` → `if False:` | `assert 0 == 2` en `cierre.llamadas` |
| 9 | `test_dos_procesos_a_la_vez` | `app/jev_senales.py` `correr`: `if not tomado:` → `if False:` | `assert 'completa' == 'ocupado'` |
| 10 | `test_subquery_compartido_*` | `app/optimizer/windows.py`: `where="WHERE ad_entity_id = %s AND metric_date BETWEEN %s AND %s"` → `where="WHERE ad_entity_id = %s"` | texto normalizado difiere (`Skipping 634 identical leading characters…`) |
| 11 | `test_jev_solo_importa_lo_declarado` | `app/cycle.py`: +`from app import jev_ads` (temporal, revertido) | `assert ['app/cycle.py: app.jev_ads'] == []` |
| 12 | `test_cli_jev_senales_despacha_con_sus_args` | `app/cli.py`: `return jev_senales.main(rest)` → `return 0` | `assert 0 == 7` |
| 13 | `test_deploy_documenta_la_linea_de_jev_senales` + `test_instalador_no_borra…` | `docs/DEPLOY.md`: `30 9,21` → `31 9,21` en la línea | `assert '<línea 30 …>' in '<DEPLOY…>'` (2 failed) |
| 14 | `test_cupo_agotado_reutiliza_juicios_guardados` (r2 B1) | `_pares_de`: `in ("sin_cupo", "proveedor_caido")` → `== "proveedor_caido"` | 1 failed (su prueba), la otra en verde |
| 15 | `test_proveedor_caido_reutiliza_juicios_guardados` (r2 B1) | `_pares_de`: `in ("sin_cupo", "proveedor_caido")` → `== "sin_cupo"` | 1 failed (su prueba), la otra en verde |
| 16 | `test_presupuesto_de_tamano_por_modulo` (r3 B2) | árbol pre-partición (`jev_senales.py` 905 líneas, sin `jev_salud.py`) | 1 failed (`{'app/jev_senales.py': 905}`); partido (785 + 136): verde |

Notas:

- M3 se intentó primero en la rama seca (`if False` donde el test no
  pasa) y dio verde: el mutante válido es el de la rama `--aplicar`.
- M11 toca `app/cycle.py` solo durante el mutante; `git diff` queda
  limpio después.
