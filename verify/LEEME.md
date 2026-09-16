# LEEME — verify/ de Orbit

generado: 2026-09-15 · sha del HEAD del worktree al generar: 0617328c11c04e44f63635e612a87dff41942f79

## Qué es

Flota de verificación independiente de Orbit para el equipo claw (OpenClaw/
gateway). La crea `bash ~/.claude/skills/saikit-verificar-app/verificar.sh
generar .` desde el worktree; los test de e2e viven en `test_drive.py` y se
corren con el COMANDO_DRIVE de `Drive.md` (pytest verify/). No son la suite
unitaria del repo: solo comprueban la superficie de usuario (HTTP) de la app
completa, como la ve quien la llama.

## Mapa de funciones (3–5, en español)

1. **Entrar a la app.** Se levanta uvicorn (o FastAPI TestClient) y se pide
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
# Postgres 16 desechable, misma credenciales que quality.yml (orbit/orbit)
pg_ctl -D $PGDATA start -p 5433   # o docker run para ci
export ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres
export ORBIT_DSN_READ=$ORBIT_TEST_DSN
export ORBIT_SECRETS_DIR=$(mktemp -d)  # vacio: canal notifica apagado
pytest verify/
```
