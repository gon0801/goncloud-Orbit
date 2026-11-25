# Recorrido de Settings con orbit-verify (ARREGLOS 05 I)

SHA: `eb8817077ae7ecd25f3ca706b6aba1cdda8e0417`.

## Recorrido formal (helper sin adaptar, WSL Linux)

El helper de `.cursor/skills/verify-orbit/helpers/orbit-verify` hace
introspeccion POSIX del proceso (`/proc/<pid>/cmdline`, `os.kill(pid, 0)`), asi
que en esta PC Windows `doctor` no puede reconocer el uvicorn local. Para no
adaptar el helper dentro del PR, el recorrido formal se ejecuto en WSL con el
mismo SHA, un venv Python 3.12 propio en `/tmp/orbit-i/.venv` y el PG del
laboratorio (`ORBIT_TEST_DSN=postgresql://orbit@127.0.0.1:55449/postgres`).
`drive-settings` no necesita navegador.

```text
$ ./.venv/bin/python .cursor/skills/verify-orbit/helpers/orbit-verify launch --run-id i-cuotas-linux
{"run_id": "i-cuotas-linux", "pid": 498184, "port": 18010,
 "base_url": "http://127.0.0.1:18010", "db": "orbit_verify_i_cuotas_linux",
 "evidence_dir": "/tmp/orbit-i/.cursor/skills/verify-orbit/evidence/i-cuotas-linux"}

$ ./.venv/bin/python .cursor/skills/verify-orbit/helpers/orbit-verify doctor
{"run_id": "i-cuotas-linux", "ok": true, "pid": 498184, "port": 18010,
 "base_url": "http://127.0.0.1:18010",
 "cmdline": "/tmp/orbit-i/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18010 ",
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
 "evidence_dir": "/tmp/orbit-i/.cursor/skills/verify-orbit/evidence/i-cuotas-linux/settings"}

$ ./.venv/bin/python .cursor/skills/verify-orbit/helpers/orbit-verify cleanup
{"cleaned_run_id": "i-cuotas-linux", "killed_pid": 498184,
 "dropped_db": "orbit_verify_i_cuotas_linux",
 "evidence_still_at": "/tmp/orbit-i/.cursor/skills/verify-orbit/evidence/i-cuotas-linux"}
```

Los archivos del drive se copiaron a `I/ui/`: `launch-ready.txt`, `doctor.json`,
`PROOF.json` (todas las comprobaciones en `true`) y `03-settings.json`.

## Caso adverso del formulario (Edge real en Windows)

El navegador de esta PC es Edge en Windows, asi que el caso adverso corrio
contra una segunda instancia local del MISMO SHA (`i-cuotas-win`, misma base
desechable del laboratorio) con `docs/evidencia/arreglos-05/I/adverso.py`. El
script usa CDP (el mismo enfoque del helper), teclea `1,000` en la cuota de bids
y mide el DOM, la red y la base.

```text
$ ./.venv/Scripts/python.exe docs/evidencia/arreglos-05/I/adverso.py \
    --base http://127.0.0.1:18010 --db orbit_verify_i_cuotas_win \
    --salida docs/evidencia/arreglos-05/I/adverso.txt
{"ok": true, ..., "checks": {
  "pagina_settings": true,
  "cap_es_texto": true,
  "cap_conserva_texto": true,
  "error_visible": true,
  "cero_post": true,
  "sin_version_nueva": true,
  "sin_recarga": true,
  "texto_error": "Error: la cuota de bid debe ser un numero entero sin comas ni decimales (llego 1,000).",
  "cuota_valida_intenta_post": true,
  "cuota_valida_sin_error_del_navegador": true,
  "estado_cuota_valida": "Error: escrituras no disponibles: configuracion de token incompleta"
}}
```

El control con `7` demuestra que la validacion no bloquea un entero valido: el
formulario intenta el POST y el unico error es del servidor de verificacion (sin
token ni DSN admin), no del navegador.

Limpieza: el `cleanup` del helper en Windows reporto `killed_pid` y
`dropped_db`, pero el proceso siguio vivo por la misma comprobacion POSIX; se
cerro con `Stop-Process` y se confirmo que la base desaparecio. En la base del
laboratorio no quedo ninguna `orbit_verify_i_cuotas%` y no quedo ningun uvicorn
de estas corridas.
