# Recorrido de Settings de la fase 2 (SHA desplegado 0244dc38)

SHA integrado y desplegado: `0244dc381c18b59b76112c68317738114d0b6ff8`.

## Recorrido formal (helper sin adaptar, WSL Linux)

El helper es POSIX en la introspeccion del proceso; el recorrido formal se
ejecuto en WSL con el arbol del SHA integrado, un venv Python 3.12 en
`/tmp/orbit-i2/.venv` y el PG del laboratorio
(`ORBIT_TEST_DSN=postgresql://orbit@127.0.0.1:55449/postgres`). Es el mismo
codigo que quedo en el contenedor `orbit-app-1` (huella en `huella-codigo.txt`).

```text
$ ./.venv/bin/python .cursor/skills/verify-orbit/helpers/orbit-verify launch --run-id i-cuotas-fase2
{"run_id": "i-cuotas-fase2", "pid": 546294, "port": 18010,
 "base_url": "http://127.0.0.1:18010", "db": "orbit_verify_i_cuotas_fase2",
 "evidence_dir": "/tmp/orbit-i2/.cursor/skills/verify-orbit/evidence/i-cuotas-fase2"}

$ ./.venv/bin/python .cursor/skills/verify-orbit/helpers/orbit-verify doctor
{"run_id": "i-cuotas-fase2", "ok": true, "pid": 546294, "port": 18010,
 "base_url": "http://127.0.0.1:18010",
 "cmdline": "/tmp/orbit-i2/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18010 ",
 "health": "{\"status\":\"ok\"}", "fallos": []}

$ ./.venv/bin/python .cursor/skills/verify-orbit/helpers/orbit-verify drive-settings
{"feature": "settings", "entry_point": "nav /settings",
 "base_url": "http://127.0.0.1:18010",
 "action": "GET /settings y GET /api/dashboard/settings",
 "checks": {"http_200": true, "data-pantalla=settings": true, "h1_settings": true,
   "h2_settings": true, "nav_aria_current": true, "token_input": true,
   "plataforma_us": true, "peldano_goal_plataforma": true, "target_25": true,
   "sin_goals_campana": true, "api_http_200": true, "api_modo_shadow": true,
   "api_us_target_25": true, "api_us_peldano": true, "api_goal_plataforma_us": true,
   "api_sin_goal_campana": true},
 "ok": true,
 "evidence_dir": "/tmp/orbit-i2/.cursor/skills/verify-orbit/evidence/i-cuotas-fase2/settings"}

$ ./.venv/bin/python .cursor/skills/verify-orbit/helpers/orbit-verify cleanup
{"cleaned_run_id": "i-cuotas-fase2", "killed_pid": 546294,
 "dropped_db": "orbit_verify_i_cuotas_fase2",
 "evidence_still_at": "/tmp/orbit-i2/.cursor/skills/verify-orbit/evidence/i-cuotas-fase2"}
```

Los archivos del drive quedan en `I/ui-fase2/`: `launch-ready.txt`, `doctor.json`,
`PROOF.json` (todo en `true`) y `03-settings.json`.

## Caso adverso con Edge real (Windows)

Segunda instancia local del MISMO SHA integrado (`i-cuotas-fase2win`), con
`I/adverso.py`; el resultado queda en `I/adverso-fase2.txt`:

```json
{"ok": true, ...
 "checks": {"pagina_settings": true, "cap_es_texto": true, "cap_conserva_texto": true,
   "error_visible": true, "cero_post": true, "sin_version_nueva": true,
   "sin_recarga": true,
   "texto_error": "Error: la cuota de bid debe ser un numero entero sin comas ni decimales (llego 1,000).",
   "cuota_valida_intenta_post": true, "cuota_valida_sin_error_del_navegador": true,
   "estado_cuota_valida": "Error: escrituras no disponibles: configuracion de token incompleta"}}
```

Limpieza: el `cleanup` en Windows reporto `killed_pid` y `dropped_db`; el
proceso se cerro ademas con `Stop-Process` por la comprobacion POSIX del helper.
No quedo ninguna base `orbit_verify_i_cuotas%` ni uvicorn de estas corridas.
