"""Orbit — punto de entrada de la API.

Sistema nuevo desde cero (ver docs/CONTEXTO.md). Este modulo expone la app,
el healthcheck, el router de SOLO LECTURA del optimizador (3.2), el router de
ESCRITURA del optimizador (ORBIT 04 3.1: veto + reversas, token solo-header
via ORBIT_DSN_ADMIN), el router del dashboard (ORBIT 16: API + UI
server-rendered) y los headers de la UI (CSP default-src 'self' y
Cache-Control: no-store en el HTML — decision 12 del header de
plans/dashboard-01.md). El bind real 127.0.0.1:<puerto> lo hace el servicio
de deploy en 4.1, jamas 0.0.0.0.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import router as ads_optimizer_router
from app.api_dashboard import router as dashboard_router
from app.api_write import router as ads_optimizer_write_router
from app.ui import router as dashboard_ui_router


class _HeadersDashboard(BaseHTTPMiddleware):
    """CSP `default-src 'self'` + `Cache-Control: no-store` en las respuestas
    HTML del dashboard (decision 12 del header: XSS de dos contextos + cero
    assets externos). La API JSON no los necesita y no los recibe. /docs y
    /redoc quedan FUERA de la CSP (grok r2): cargan swagger/redoc desde CDN
    y la politica los dejaria en blanco — son superficie dev por tunel, no
    parte del contrato cero-CDN del dashboard."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if (response.headers.get("content-type") or "").startswith("text/html"):
            if not request.url.path.startswith(("/docs", "/redoc")):
                response.headers["Content-Security-Policy"] = "default-src 'self'"
            response.headers["Cache-Control"] = "no-store"
        return response


app = FastAPI(title="Orbit", version="0.1.0")
app.add_middleware(_HeadersDashboard)
# Lib de graficas VENDOREADA (Chart.js 4.4.9, brief §6): cero CDN, el asset
# se sirve localmente (los templates jamas referencian hosts externos).
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(ads_optimizer_router)
app.include_router(ads_optimizer_write_router)
app.include_router(dashboard_router)
app.include_router(dashboard_ui_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
