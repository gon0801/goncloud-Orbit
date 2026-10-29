# ruff: noqa
import json
import math
import statistics
import sys
import time
import urllib.request

url = sys.argv[1]
for _ in range(3):
    assert urllib.request.urlopen(url, timeout=5).status == 200
lat = []
for _ in range(30):
    inicio = time.perf_counter()
    with urllib.request.urlopen(url, timeout=10) as r:
        cuerpo = r.read()
    lat.append((time.perf_counter() - inicio) * 1000)
    assert r.status == 200
ordenadas = sorted(lat)
p95 = ordenadas[math.ceil(0.95 * len(ordenadas)) - 1]
print(json.dumps({"n": len(lat), "median_ms": round(statistics.median(lat), 2), "p95_ms": round(p95, 2), "bytes": len(cuerpo)}))
