# ruff: noqa
"""30 requests intercalados base/head a /, p95 nearest-rank y payload exacto.

El umbral se evalua con los p95 SIN redondear. Cada respuesta se identifica
por SHA-256, no por longitud: dos payloads distintos pueden medir lo mismo.
"""

import hashlib
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
    return (time.perf_counter() - inicio) * 1000, hashlib.sha256(cuerpo).hexdigest(), len(cuerpo)


for url in (BASE, HEAD):
    for _ in range(5):
        mide(url)


def stats(latencias):
    ordenadas = sorted(latencias)
    p95 = ordenadas[math.ceil(0.95 * len(ordenadas)) - 1]
    return {
        "median_ms": statistics.median(latencias),
        "p95_ms": p95,
    }


rondas = []
for _ in range(5):
    latencias_base, latencias_head = [], []
    hashes_base, hashes_head = [], []
    bytes_base = bytes_head = 0
    for i in range(30):
        if i % 2 == 0:
            ms, sha, bytes_base = mide(BASE)
            latencias_base.append(ms)
            hashes_base.append(sha)
            ms, sha, bytes_head = mide(HEAD)
            latencias_head.append(ms)
            hashes_head.append(sha)
        else:
            ms, sha, bytes_head = mide(HEAD)
            latencias_head.append(ms)
            hashes_head.append(sha)
            ms, sha, bytes_base = mide(BASE)
            latencias_base.append(ms)
            hashes_base.append(sha)
    assert hashes_base == hashes_head, (
        "el payload de / cambio entre base y head: "
        f"base={sorted(set(hashes_base))} head={sorted(set(hashes_head))}"
    )
    base_stats = stats(latencias_base)
    head_stats = stats(latencias_head)
    ronda = {
        "base": {**base_stats, "bytes": bytes_base, "sha256": sorted(set(hashes_base))},
        "head": {**head_stats, "bytes": bytes_head, "sha256": sorted(set(hashes_head))},
        "head_sobre_base_pct": round((head_stats["p95_ms"] / base_stats["p95_ms"] - 1) * 100, 2),
        "muestras_ms": {"base": latencias_base, "head": latencias_head},
    }
    rondas.append(ronda)
    print(json.dumps(ronda))

base_p95 = statistics.median(r["base"]["p95_ms"] for r in rondas)
head_p95 = statistics.median(r["head"]["p95_ms"] for r in rondas)
resumen = {
    "rondas": len(rondas),
    "base_p95_mediana_ms": base_p95,
    "head_p95_mediana_ms": head_p95,
    "head_sobre_base_pct": (head_p95 / base_p95 - 1) * 100,
    "regla": "head_p95 <= base_p95 * 1.10 con valores sin redondear",
}
print(json.dumps(resumen))
assert head_p95 <= base_p95 * 1.10
