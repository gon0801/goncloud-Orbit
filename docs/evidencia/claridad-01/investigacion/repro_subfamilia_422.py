import sys; sys.path.insert(0, sys.argv[1])
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import api_fabrica as af
from app.api import _conexion_lectura as dep
app = FastAPI(); app.include_router(af.router)
app.dependency_overrides[dep] = lambda: None
c = TestClient(app)
r = c.post("/api/fabrica/impulso/plan", json={"plataforma":"amazon_mx","listing_ids":[1],"subfamilia_por_listing":{"1":"Arras para boda"}})
print(r.status_code, r.json())
