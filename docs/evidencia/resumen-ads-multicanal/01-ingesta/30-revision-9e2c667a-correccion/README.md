# Correccion sobre 9e2c667a: conciliacion T+0 y CI fail closed

Evidencia de la ronda. El veredicto de la revision pidio dos correcciones:

1. Conciliacion T+0: la ventana cerrada termina AYER. El control diario de
   HOY posterior a `--hasta` se excluye como T+0 (accounting publica el total
   antes que el desglose de campanas) y se reporta; un NULL de HOY sigue
   fallando; un control historico posterior a `--hasta` sigue fallando; una
   campana fuera de la ventana sigue fallando; con `--hasta` incluyendo a HOY
   el control de hoy no tiene exencion.
2. Skip silencioso de CI: el fixture `pg` de `tests/test_meli_ads_ingest.py`
   ahora usa `_postgres_obligatorio_ausente()` (RuntimeError en CI, skip solo
   fuera de CI) e importa psycopg normal, sin `importorskip`.

## Contenido

| Archivo | Que prueba |
|---|---|
| `regresiones-contra-9e2c667a.txt` | Las regresiones nuevas corriendo contra `9e2c667a` (HEAD + solo las pruebas): `test_control_t0_de_hoy_se_excluye_con_ventana_que_termina_ayer` FALLA (el control de hoy rechazado) y `test_pg_guard_falla_cerrado_en_ci_sin_postgres` FALLA (NameError: no existia el guard). Las 3 de conservacion pasan. |
| `regresiones-en-head.txt` | Las mismas 5 pruebas en el arbol corregido: 5 passed. |
| `focal.txt` | `uv run --frozen python -m pytest -q tests/test_meli_ads_ingest.py tests/test_conciliar_meli_ads.py tests/test_cli.py tests/test_architecture.py` → 272 passed. |
| `calidad.txt` | ruff check + ruff format --check sobre los archivos modificados, `bash -n tools/refresh_costos.sh`, `git diff --check`, `pre-commit run --all-files`. |
| `copias-identicas.txt` | SHA-256 de las tres copias de `conciliar.py`: `726c73a94561bddc26b519cac18ffe728263221bad25c1f0109c5975d6573711` (byte-identicas). |
| `crossreview-kimi.txt` | Cross review con Kimi (`kimi-code-plan-cn/k3.1-pro`, 3 revisores): A y B aprueban; C rechazo por `date -d` GNU en la maquina del lead -> corregido con la forma portable de repricing-02. Incluye las notas de los otros dos revisores. |
| `suite-completa.txt` | Bateria completa con `ORBIT_TEST_DSN` resuelto via `test_schema._test_dsn`. |

## Pendiente (NO ejecutado aqui)

Diez lanes live con PNG, regresion Amazon base/HEAD y perfil de `/`: siguen
pendientes, igual que marco el veredicto anterior.
