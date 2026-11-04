# LEEME — verify/ de Orbit

generado: 2026-09-15 · sha del HEAD del worktree al generar: b46beac1770229e2a271777d2268d996e0ce648d

## Qué es

Flota de verificación independiente de Orbit para el equipo claw (OpenClaw/
gateway). La crea `bash ~/.claude/skills/saikit-verificar-app/verificar.sh
generar .` desde el worktree; los test de e2e viven en `test_drive.py` y se
corren con el COMANDO_DRIVE de `Drive.md` (pytest verify/). No son la suite
unitaria del repo: solo comprueban la superficie de usuario (HTTP) de la app
completa, como la ve quien la llama.

## Mapa de funciones (3–5, en español)

1. **Entrar a la app.** Con FastAPI TestClient (en proceso, sin uvicorn ni puerto HTTP) se pide
   `GET /health`. Devuelve JSON con `status: ok`.
2. **Ver el estado del optimizador.** `GET /api/ads-optimizer/status` devuelve
   el último ciclo por plataforma, con watermarks y notas, en JSON; dinero
   como string.
3. **Ver los goals.** `GET /api/ads-optimizer/goals` lista los objetivos
   configurados; filtros opcionales por plataforma / scope / enabled.
4. **Auditar decisiones.** `GET /api/ads-optimizer/audit`, paginado con
   `limit`/`offset` y filtros opcionales; lista las decisiones del motor.

## Convivencia con la flota DG

`verify/` es de la flota claw (agentes Orbit en el gateway y Mac via
OpenClaw). Otra flota, `.cursor/skills/verify-orbit` en el mismo repo, sigue
siendo de la flota DG (Cursor en la Mac de David): mantienen sus invariantes
por separado. Este `verify/` no toca esa skill ni la reemplaza: pueden
convivir, una por cada agente dueño, mientras mantengan sus cheat sheets al
día (el Doctor.md de la flota DG no entra en este carril).

## Cómo correr

```bash
# Postgres 16 desechable, mismas credenciales que quality.yml (orbit/orbit)
# Procedimiento completo (initdb, migrations, variables): ver Launch.md.
PGDATA=$(mktemp -d /tmp/orbit-pgdata.XXXXXX)
SOCK=$(mktemp -d /tmp/orbit-pgsock.XXXXXX)
initdb -D "$PGDATA" -U orbit --auth=trust -E UTF8
pg_ctl -D "$PGDATA" -o "-p 5433 -k $SOCK -c listen_addresses=127.0.0.1" start
export ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres
export ORBIT_DSN_READ=$ORBIT_TEST_DSN
export ORBIT_SECRETS_DIR=$(mktemp -d /tmp/orbit-secrets.XXXXXX)  # vacio: canal notifica apagado
pytest verify/
# Cleanup con guards: ver Cleanup.md (refusa borrar paths que no eran mktemp propios)
```
