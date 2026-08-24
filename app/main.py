"""Orbit — punto de entrada de la API.

Sistema nuevo desde cero (ver docs/CONTEXTO.md). Las reglas de negocio y sus
umbrales viven en docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md; este modulo solo
expone la app, el healthcheck, el router de SOLO LECTURA del optimizador
(3.2) y el router del dashboard (ORBIT 16; el bind real 127.0.0.1:<puerto> lo
hace el servicio de deploy en 4.1, jamas 0.0.0.0).
"""

from fastapi import FastAPI

from app.api import router as ads_optimizer_router
from app.api_dashboard import router as dashboard_router

app = FastAPI(title="Orbit", version="0.1.0")
app.include_router(ads_optimizer_router)
app.include_router(dashboard_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
