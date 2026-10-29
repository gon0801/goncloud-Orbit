# ruff: noqa
"""30 requests intercalados base/head a /, con p95 nearest-rank."""

import json
import math
import statistics
import sys
import time
import urllib.request

BASE = sys.argv[1]
HEAD = sys.argv[2]


def mide(url):
    inicio = time.perf_counter()
    with urllib.request.urlopen(url, timeout=10) as respuesta:
        cuerpo = respuesta.read()
        assert respuesta.status == 200
    return (time.perf_counter() - inicio) * 1000, len(cuerpo)


for url in (BASE, HEAD):
    for _ in range(5):
        mide(url)


def stats(latencias):
    ordenadas = sorted(latencias)
    p95 = ordenadas[math.ceil(0.95 * len(ordenadas)) - 1]
    return {
        "median_ms": round(statistics.median(latencias), 2),
        "p95_ms": round(p95, 2),
    }


rondas = []
for _ in range(5):
    latencias_base, latencias_head = [], []
    bytes_base = bytes_head = 0
    for i in range(30):
        if i % 2 == 0:
            ms, bytes_base = mide(BASE)
            latencias_base.append(ms)
            ms, bytes_head = mide(HEAD)
            latencias_head.append(ms)
        else:
            ms, bytes_head = mide(HEAD)
            latencias_head.append(ms)
            ms, bytes_base = mide(BASE)
            latencias_base.append(ms)
    ronda = {
        "base": {**stats(latencias_base), "bytes": bytes_base},
        "head": {**stats(latencias_head), "bytes": bytes_head},
    }
    ronda["head_sobre_base_pct"] = round(
        (ronda["head"]["p95_ms"] / ronda["base"]["p95_ms"] - 1) * 100, 2
    )
    rondas.append(ronda)
    print(json.dumps(ronda))

base_p95 = statistics.median(r["base"]["p95_ms"] for r in rondas)
head_p95 = statistics.median(r["head"]["p95_ms"] for r in rondas)
resumen = {
    "rondas": len(rondas),
    "base_p95_mediana_ms": round(base_p95, 2),
    "head_p95_mediana_ms": round(head_p95, 2),
    "head_sobre_base_pct": round((head_p95 / base_p95 - 1) * 100, 2),
}
print(json.dumps(resumen))
assert head_p95 <= base_p95 * 1.10
