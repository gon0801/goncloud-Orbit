"""Clasifica elegibilidad y concilia el reporte externo guardado, sin red."""

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

base = Path(__file__).parent
r = json.loads((base / "replay.json").read_text())
e = json.loads((base / "elegibilidad.json").read_text())
c = json.loads((base / "cobertura.json").read_text())
a = json.loads((base / "reporte-existente.json").read_text())
decisiones = {
    (x["cycle_id"], x["ad_entity_id"]): x for x in e["queries"]["decisiones_candidatos"]["rows"]
}
ciclos = {x["id"]: x for x in e["queries"]["ciclos"]["rows"]}
configs = {x["cycle_id"]: x for x in e["queries"]["config_por_ciclo"]["rows"]}
cohortes = []
for fila in r["rows"]:
    if fila["candidate"] is not True:
        continue
    clave = (fila["cycle"], fila["entity"])
    decision = decisiones.get(clave)
    assert ciclos[fila["cycle"]]["status"] == "done"
    assert configs[fila["cycle"]]["pause_economica"] is None
    cohortes.append(
        {
            "cycle": fila["cycle"],
            "entity": fila["entity"],
            "kind": fila["kind"],
            "moneda": fila["moneda"],
            "numeric_candidate": True,
            "platform_guard": "paso_segun_ciclo_done_con_decisiones",
            "minimum_dates_and_maturity": "cumple",
            "window_contract": "ancla_por_entidad_conforme_windows",
            "economic_enabled": False,
            "config_version_id": configs[fila["cycle"]]["config_version_id"],
            "historical_entity_goal_ancestors": (
                "pasaron_camino_decision_historica"
                if decision
                else "unknown_sin_decision_individual_ni_historia_state_goal"
            ),
            "historical_decision_id": decision["id"] if decision else None,
            "historical_reason": decision["inputs"].get("motivo") if decision else None,
            "historical_goal_freeze": decision["inputs"].get("goal") if decision else None,
            "campaign_state_freshness_48h": (
                "unknown_sin_historia_state_campana"
                if fila["kind"] == "campaign"
                else "no_aplica_guard_individual_de_campana"
            ),
            "full_eligibility": "bloqueado_flag_economico_ausente",
            "counterfactual_eligibility_if_enabled": "unknown_gates_historicos_no_reconstruibles",
        }
    )
externos = {"110131350133029": 3911, "114399001706085": 3920}
db = {
    (int(i), fecha): (Decimal(cost), Decimal(revenue), moneda)
    for i, fecha, cost, revenue, moneda in a["db_rows"]
}
for fila in a["selected_rows"]:
    clave = (externos[fila["campaignId"]], fila["date"])
    assert clave in db
    assert (Decimal(fila["cost"]), Decimal(fila["sales30d"])) == db[clave][:2]
    assert db[clave][2] == "USD"
assert len(a["selected_rows"]) == len(db) == 62
parcial = [
    x for x in a["selected_rows"] if externos[x["campaignId"]] == 3920 and x["date"] <= "2026-10-24"
]
assert len(parcial) == 22
out = {
    "cohorts": cohortes,
    "eligibility_counts": dict(Counter(x["full_eligibility"] for x in cohortes)),
    "historical_individual_gate_counts": dict(
        Counter(x["historical_entity_goal_ancestors"] for x in cohortes)
    ),
    "coverage": {
        "mature_candidates": sum(x["status"] == "medida" for x in c["cohorts"]),
        "future_candidates": sum(x["status"] == "horizonte_futuro" for x in c["cohorts"]),
        "all_dates_revised_candidates": sum(
            x.get("all_dates_revised", False) for x in c["cohorts"]
        ),
        "revised_date_cohort_pairs": sum(x.get("revised_dates", 0) for x in c["cohorts"]),
        "total_mature_date_cohort_pairs": sum(x.get("dates_asof", 0) for x in c["cohorts"]),
        "warning": "pares fecha/cohorte repetidos; revision parcial no prueba maduracion completa",
    },
    "external": {
        "report_id": a["report_id"],
        "http_status": a["http_status"],
        "metadata": a["metadata"],
        "matching_entity_date_rows": len(db),
        "currency": "USD",
        "money_mismatches": 0,
        "candidate_3920_cycle_164_partial_dates": 22,
        "candidate_3920_cycle_164_partial_cost": str(
            sum((Decimal(x["cost"]) for x in parcial), Decimal(0))
        ),
        "candidate_3920_cycle_164_partial_revenue": str(
            sum((Decimal(x["sales30d"]) for x in parcial), Decimal(0))
        ),
        "missing_window": "2026-09-25..2026-10-02 (8 fechas)",
        "vintage": "reporte_3nov_leido_5nov; no es vintage +10d de cohortes anteriores",
    },
}
(base / "clasificacion.json").write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps({k: v for k, v in out.items() if k != "cohorts"}, indent=2))
