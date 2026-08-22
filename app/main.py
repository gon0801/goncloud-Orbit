"""Orbit — punto de entrada de la API.

Sistema nuevo desde cero (ver docs/CONTEXTO.md). Las reglas de negocio y sus
umbrales viven en docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md; este modulo solo
expone la app y el healthcheck por ahora.
"""

from fastapi import FastAPI

app = FastAPI(title="Orbit", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
