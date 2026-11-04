# E/B3-r3 — correccion revisiones automaticas post-push PR 392 (F1/F2 + Lows)

Fecha UTC: 2026-10-04T03:36:24Z (ACK) a cierre. Rama `docs/jev-ads-b3`,
partiendo limpia de `1b4318cd5247f4fe98dada061eec125aa7ff7c33`
(VEREDICTO-B3-r2 APROBADO; PR 392 OPEN ready, no mergeado). Un solo
escritor; SIN push (lo hace Claw tras VEREDICTO: APROBADO).

## SHA

| Commit | Contenido |
| --- | --- |
| `e8b16d8bf4becf87c79bb5912710cd4c61556100` | B3-r3: --decision-id rechazo explicito, ruta canonica de secretos, pureza ampliada, guardas de entorno |

HEAD final de CODIGO (40): `e8b16d8bf4becf87c79bb5912710cd4c61556100`
(commits posteriores, si los hay, SOLO evidencia documental).

## B1 (High): --decision-id se parseaba y se IGNORABA

- Confirmado: `rg decision_id tools/jev_ads.py` solo docstring/add_argument;
  `DecisionARevisar` cero usos.
- DECISION (sin anticipar 2.1): RECHAZO EXPLICITO. `--decision-id` con
  valor → mensaje "configuracion rechazada: --decision-id todavia no esta
  soportado (la revision atada a decision llega con la fila 2.1)..." y
  exit 2, ANTES de abrir base: cero escritura, cero HTTP. Docstring y
  --help alineados.
- ROJO: `test_cli_decision_id_se_rechaza_hasta_2_1` (exit 0 y escritura
  semillas antes del arreglo; `jev_b3r3_rojo.txt`).
- VERDE: exit 2, mensaje con "decision", cero llamadas, cero filas en
  jev_revision.
- MUTANTE: quitar el rechazo (`if args.decision_id` → `if False`) →
  1 failed; restaurado → verde.

## B2 (Medium): leer_api_key leia el cwd sin ORBIT_SECRETS_DIR

- Causa: `Path(os.environ.get("ORBIT_SECRETS_DIR", ""))` → `Path(".")`.
- ARREGLO: ruta canonica `DEFAULT_SECRETS_DIR` de `app.ads.config` (mismo
  patron que notifica.py:174), leida por atributo del modulo
  (`config_ads.DEFAULT_SECRETS_DIR`) para que sea la fuente viva.
- ROJO: `test_leer_api_key_sin_variable_usa_la_ruta_canonica` — cwd
  trampa con SU propio typesafe.json ("de-cwd") y canonico con
  "canonica": la implementacion vieja devolvia "de-cwd" (lee el cwd).
- VERDE: devuelve "canonica" sin tocar el cwd.
- MUTANTE: revertir a `Path(os.environ.get(..., ""))` → 1 failed;
  restaurado → verde.

## Lows (empaquetados en esta ronda)

- F3: docstring de `app/jev_ads.py` actualizado (el NUCLEO es puro; la IO
  vive SOLO en AsesorAds). Guarda de pureza ampliada (CodeRabbit): TODO
  nodo top-level excepto `AsesorAds` se recorre con `ast.walk` completo:
  imports prohibidos anidados a cualquier profundidad y `ast.Name`
  psycopg/httpx/requests/socket/ssl/conn rechazados; ademas top-level
  sigue acotado a biblioteca estandar pura. MUTANTE: `import httpx`
  anidado en `_solo_no_activo` → 1 failed (la guarda vieja por substring
  lo dejaba pasar).
- F4: `pytestmark = pytest.mark.skipif(_postgres_obligatorio_ausente())`
  en `tests/test_jev_cli.py` (patron del resto del repo).
- F5: `test_cli_sin_dsn_da_error_config` aislado con
  `monkeypatch.delenv("ORBIT_DSN_ADMIN")` (antes, dsn="" caeria a la
  variable del entorno y el test dependia del ambiente).

## Comandos y salida real

```
pytest -q tests/test_jev_cli.py::test_cli_decision_id_se_rechaza_hasta_2_1
  tests/test_jev_cli.py::test_cli_sin_dsn_da_error_config
  tests/test_jev_juicios.py::test_leer_api_key_sin_variable_usa_la_ruta_canonica
  -> 2 failed, 1 passed (rojo B1+B2; F5 ya aislado)
mutantes in vivo: 3 aplicados, 3 matados (1 failed cada uno; restaurados
  con reemplazo exacto)
pytest focalizadas Jev (4 archivos) -> 87 passed
pytest -q -> 3,834 passed, 1 warning in 274.01s (bateria una vez sobre
  e8b16d8)
uv run ruff check . -> All checks passed!
pre-commit run --all-files -> 9 hooks Passed
git commit sin --no-verify
```

Evidencia cruda: `jev_b3r3_rojo.txt`, `jev_b3r3_verde.txt` (87 passed),
`jev_b3r3_bateria.txt`.

## Limites respetados

- Archivos: `tools/jev_ads.py`, `app/jev_juicios.py`, `app/jev_ads.py`
  (solo docstring), `tests/test_jev_cli.py`, `tests/test_jev_juicios.py`,
  `tests/test_jev_ads.py` (solo la guarda de pureza del delta) y esta
  evidencia. cycle.py, apply_cola.py, apply_harvest.py, migrations/*,
  api_dashboard, fabrica_web, api_fabrica INTACTOS. Sin migracion nueva.
- Cero llamadas a TypeSafe/Jev real; cero claves; sin secretos.
- Sin push: commit local `e8b16d8`; arbol limpio.

## Skills/agentes/modelos

- Sesion principal opencode: `zai-coding-plan/glm-5.3-flash`, un solo
  escritor, dueno del diff.
- Skills activas de la sesion: poteto-mode (playbook Feature, skips
  razonados), unslop, tdd (rojo -> verde + mutantes por hallazgo),
  typesafe-ai + docs vivas (sin novedad: jev-1.13.0 sigue vigente).
- Subagentes: NINGUNO; paneles: NINGUNO (arreglos pautados).
