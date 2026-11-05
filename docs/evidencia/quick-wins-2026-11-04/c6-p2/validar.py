"""Comprueba la formula monetaria y la madurez de la evidencia guardada."""

import datetime as dt
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

base = Path(__file__).parent
r = json.loads((base / "replay.json").read_text())
limites = {"USD": Decimal("80"), "MXN": Decimal("1000")}
for fila in r["rows"]:
    assert fila["fechas"] >= 7
    assert dt.date.fromisoformat(fila["hasta"]) <= (
        dt.datetime.fromisoformat(fila["as_of"]).date() - dt.timedelta(days=10)
    )
    if fila["target"] is not None:
        esperado = Decimal(fila["target"]) * Decimal(fila["revenue"]) / 100
        exceso = Decimal(fila["cost"]) - esperado
        assert fila["candidate"] == (
            Decimal(fila["cost"]) > 3 * esperado and exceso >= limites[fila["moneda"]]
        )
        assert Decimal(fila["excess"]) == exceso
    else:
        assert fila["candidate"] is None
counts = Counter((x["status"], str(x["candidate_asof"])) for x in r["maturity_10d"])
resumen = {
    "metadata": r["metadata"],
    "rows": len(r["rows"]),
    "cycles_without_clock": len(r["cycles_without_decision_clock"]),
    "candidate_rows": sum(x["candidate"] is True for x in r["rows"]),
    "candidate_entities": sorted({x["entity"] for x in r["rows"] if x["candidate"]}),
    "maturity_counts": [
        {"status": k[0], "candidate_asof": k[1], "rows": v} for k, v in sorted(counts.items())
    ],
    "validation": {
        "formula_decimal_all_rows": True,
        "minimum_7_dates_and_maturity_10d_all_rows": True,
    },
}
(base / "resumen.json").write_text(json.dumps(resumen, indent=2) + "\n")
print(json.dumps(resumen, indent=2))
