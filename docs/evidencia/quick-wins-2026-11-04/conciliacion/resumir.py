"""Recalcula totales y versiones de la evidencia saneada, sin red ni secretos."""

import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RAIZ))
from app.meli.ads_source import version_canonica  # noqa: E402 - ruta de evidencia

p = Path(__file__).resolve().parent
d = json.loads((p / "datos.json").read_text())
versiones = {(r[0], r[1]): r[2].split("|")[3] for r in d["versiones_orbit"]}
resumen = {}
for nombre in ("filas_origen", "filas_orbit"):
    filas = d[nombre]
    resumen[nombre] = {
        "filas": len(filas),
        "dias": len({r[1] for r in filas}),
        "cost_mxn": str(sum((Decimal(r[2]) for r in filas), Decimal(0))),
        "revenue_mxn": str(sum((Decimal(r[3]) for r in filas), Decimal(0))),
        "impressions": sum(r[4] for r in filas),
        "clicks": sum(r[5] for r in filas),
        "nulos_metricas_conciliables": sum(v is None for r in filas for v in r[2:]),
    }
    hashes = []
    for campana, fecha, costo, ingreso, impresiones, clicks in filas:
        version = version_canonica(
            campana,
            dt.date.fromisoformat(fecha),
            "MXN",
            Decimal(costo),
            Decimal(ingreso),
            impresiones,
            clicks,
            None,
        )
        if version != versiones[(campana, fecha)]:
            hashes.append([campana, fecha])
    resumen[nombre]["versiones_distintas_de_orbit"] = hashes
origen = {(r[0], r[1]): r[2:] for r in d["filas_origen"]}
orbit = {(r[0], r[1]): r[2:] for r in d["filas_orbit"]}
resumen["hasta_2026_11_03"] = {
    "filas": sum(k[1] <= "2026-11-03" for k in origen),
    "diferencias": [k for k, v in origen.items() if k[1] <= "2026-11-03" and orbit.get(k) != v],
}
resumen["orders_null_orbit_esperados_por_contrato"] = sum(
    r[3] is None for r in d["versiones_orbit"]
)
fechas = {r[1] for r in d["filas_origen"]}
inicio, fin = map(dt.date.fromisoformat, d["ventana_utc"])
resumen["huecos_calendario"] = [
    (inicio + dt.timedelta(days=i)).isoformat()
    for i in range((fin - inicio).days + 1)
    if (inicio + dt.timedelta(days=i)).isoformat() not in fechas
]
(p / "resumen.json").write_text(json.dumps(resumen, indent=2) + "\n")
print(json.dumps(resumen, indent=2))
