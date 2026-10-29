# ruff: noqa
import json
import os
import statistics
import sys
import tempfile
import time

dsn = sys.argv[1]
os.environ["ORBIT_DSN_READ"] = dsn
os.environ["ORBIT_DSN_ADMIN"] = dsn
os.environ["ORBIT_SECRETS_DIR"] = tempfile.mkdtemp(prefix="orbit-secrets-")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

with TestClient(app) as cliente:
    for _ in range(3):
        assert cliente.get("/").status_code == 200
    latencias = []
    ultima = None
    for _ in range(30):
        inicio = time.perf_counter()
        respuesta = cliente.get("/")
        latencias.append((time.perf_counter() - inicio) * 1000)
        assert respuesta.status_code == 200, respuesta.text
        ultima = respuesta
ordenadas = sorted(latencias)
p95 = ordenadas[math.ceil(0.95 * len(ordenadas)) - 1]
print(
    json.dumps(
        {
            "n": len(latencias),
            "median_ms": round(statistics.median(latencias), 2),
            "p95_ms": round(p95, 2),
            "bytes": len(ultima.content),
        }
    )
)
