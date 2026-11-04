from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.orbit_bench.catalog import CASES

_MAX_STDOUT = 1_000_000
_CANDIDATE_ENV = {
    "PATH": os.defpath,
    "PYTHONHASHSEED": "0",
    "PYTHONIOENCODING": "utf-8",
}

_CODIGO_VECTORS = [
    (
        {
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": "40.0000",
            "currency": "USD",
            "metric_date": "2026-08-20",
            "decided_at": "2026-08-30",
        },
        {"action": "pause", "reason": "corte"},
    ),
    (
        {
            "platform": "amazon_us",
            "clicks": 99,
            "orders": 0,
            "cost": "40.0000",
            "currency": "USD",
            "metric_date": "2026-08-20",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 1,
            "cost": "40.0000",
            "currency": "USD",
            "metric_date": "2026-08-20",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": "39.9999",
            "currency": "USD",
            "metric_date": "2026-08-20",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": "40.0000",
            "currency": "USD",
            "metric_date": "2026-08-21",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": "40.0000",
            "currency": "USD",
            "metric_date": "2026-08-30",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "platform": "amazon_mx",
            "clicks": 100,
            "orders": 0,
            "cost": "500.0000",
            "currency": "MXN",
            "metric_date": "2026-07-01",
            "decided_at": "2026-08-30",
        },
        {"action": "pause", "reason": "corte"},
    ),
    (
        {
            "platform": "amazon_mx",
            "clicks": 100,
            "orders": 0,
            "cost": "499.9999",
            "currency": "MXN",
            "metric_date": "2026-07-01",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": "800.0000",
            "currency": "MXN",
            "metric_date": "2026-07-01",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "moneda_invalida"},
    ),
    (
        {
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": None,
            "currency": "USD",
            "metric_date": "2026-07-01",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "dato_faltante"},
    ),
    (
        {
            "platform": "amazon_us",
            "clicks": 160,
            "orders": 1,
            "cost": "90.0000",
            "currency": "USD",
            "metric_date": "2026-07-01",
            "decided_at": "2026-08-30",
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
]

_DEPURACION_VECTORS = [
    (
        {
            "mode": "current",
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": "40.0000",
            "currency": "USD",
        },
        {"action": "pause", "reason": "corte"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_us",
            "clicks": 24,
            "orders": 0,
            "cost": "12.0000",
            "currency": "USD",
            "frozen_policy": {"min_clicks": 25, "min_cost": "12.0000", "currency": "USD"},
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_us",
            "clicks": 25,
            "orders": 1,
            "cost": "12.0000",
            "currency": "USD",
            "frozen_policy": {"min_clicks": 25, "min_cost": "12.0000", "currency": "USD"},
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_us",
            "clicks": 25,
            "orders": 0,
            "cost": "11.9999",
            "currency": "USD",
            "frozen_policy": {"min_clicks": 25, "min_cost": "12.0000", "currency": "USD"},
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_us",
            "clicks": 25,
            "orders": 0,
            "cost": "12.0000",
            "currency": "MXN",
            "frozen_policy": {"min_clicks": 25, "min_cost": "12.0000", "currency": "USD"},
        },
        {"action": "none", "reason": "moneda_invalida"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_us",
            "clicks": 25,
            "orders": 0,
            "cost": "12.0000",
            "currency": "USD",
            "frozen_policy": {"min_clicks": 25, "min_cost": "12.0000", "currency": "USD"},
        },
        {"action": "pause", "reason": "corte"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": "40.0000",
            "currency": "USD",
            "frozen_policy": {"min_clicks": 120, "min_cost": "50.0000", "currency": "USD"},
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_mx",
            "clicks": 100,
            "orders": 0,
            "cost": "500.0000",
            "currency": "MXN",
        },
        {"action": "none", "reason": "politica_historica_ausente"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_mx",
            "clicks": 25,
            "orders": 0,
            "cost": "200.0000",
            "currency": "MXN",
            "frozen_policy": {"min_clicks": 25, "min_cost": "200.0000", "currency": "MXN"},
        },
        {"action": "pause", "reason": "corte"},
    ),
    # Costo suficiente: aislar la frontera de clicks de la politica congelada.
    (
        {
            "mode": "replay",
            "platform": "amazon_us",
            "clicks": 119,
            "orders": 0,
            "cost": "50.0000",
            "currency": "USD",
            "frozen_policy": {"min_clicks": 120, "min_cost": "50.0000", "currency": "USD"},
        },
        {"action": "none", "reason": "evidencia_insuficiente"},
    ),
    (
        {
            "mode": "replay",
            "platform": "amazon_us",
            "clicks": 120,
            "orders": 0,
            "cost": "50.0000",
            "currency": "USD",
            "frozen_policy": {"min_clicks": 120, "min_cost": "50.0000", "currency": "USD"},
        },
        {"action": "pause", "reason": "corte"},
    ),
]

_MANTENIBILIDAD_VECTORS = [
    *_CODIGO_VECTORS,
    (
        {
            "platform": "amazon_us",
            "clicks": 100,
            "orders": 0,
            "cost": "40.0000",
            "currency": "USD",
            "metric_date": "2026-08-20",
            "decided_at": "2026-08-30",
            "goal_enabled": False,
        },
        {"action": "none", "reason": "goal_deshabilitado"},
    ),
    (
        {
            "platform": "amazon_mx",
            "clicks": 100,
            "orders": 0,
            "cost": "500.0000",
            "currency": "MXN",
            "metric_date": "2026-07-01",
            "decided_at": "2026-08-30",
            "goal_enabled": True,
        },
        {"action": "pause", "reason": "corte"},
    ),
]

_VECTORS = {
    "codigo": _CODIGO_VECTORS,
    "depuracion": _DEPURACION_VECTORS,
    "mantenibilidad": _MANTENIBILIDAD_VECTORS,
}


def _artifact_check(candidate: Path, artifact: str) -> dict:
    path = candidate / artifact
    if path.is_symlink() or not path.is_file():
        return {"name": f"artefacto {artifact}", "passed": False, "detail": "archivo ausente"}
    if not path.read_bytes().strip():
        return {"name": f"artefacto {artifact}", "passed": False, "detail": "archivo vacio"}
    return {"name": f"artefacto {artifact}", "passed": True, "detail": "archivo presente"}


def _execute(solution: Path, payload: dict, timeout: float) -> tuple[bool, Any, str]:
    solution = solution.resolve()
    try:
        process = subprocess.run(
            [sys.executable, "-B", str(solution)],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            cwd=solution.parent,
            env=_CANDIDATE_ENV,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, None, "tiempo agotado"
    except OSError:
        return False, None, "no se pudo ejecutar solution.py"
    if process.returncode != 0:
        return False, None, f"proceso termino con codigo {process.returncode}"
    if len(process.stdout) > _MAX_STDOUT:
        return False, None, "stdout excede el limite permitido"
    try:
        stdout = process.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return False, None, "stdout no contiene UTF-8 valido"
    try:
        output = json.loads(stdout)
    except json.JSONDecodeError:
        return False, None, "stdout no contiene JSON valido"
    if not isinstance(output, dict):
        return False, None, "stdout debe contener un objeto JSON"
    return True, output, "salida valida"


def _automatic_checks(case_id: str, solution: Path, timeout: float) -> list[dict]:
    checks = []
    for index, (payload, expected) in enumerate(_VECTORS[case_id], start=1):
        ran, output, detail = _execute(solution, payload, timeout)
        passed = ran and output == expected
        if ran and not passed:
            detail = "resultado incorrecto"
        checks.append({"name": f"evaluacion privada {index}", "passed": passed, "detail": detail})
        if not passed:
            break
    return checks


def grade(case_id: str, candidate: Path, timeout: float = 5.0) -> dict:
    valid_timeout = (
        not isinstance(timeout, bool)
        and isinstance(timeout, (int, float))
        and math.isfinite(timeout)
        and timeout > 0
    )
    if not valid_timeout:
        raise ValueError("timeout debe ser finito y positivo")
    case = CASES[case_id]
    candidate = Path(candidate)
    checks = [_artifact_check(candidate, artifact) for artifact in case["artifacts"]]
    if not case["automatic"]:
        return {"checks": checks, "automatic_pass": None}
    solution_check = next(
        (check for check in checks if check["name"] == "artefacto solution.py"), None
    )
    if solution_check is None or not solution_check["passed"]:
        return {"checks": checks, "automatic_pass": False}
    checks.extend(_automatic_checks(case_id, candidate / "solution.py", timeout))
    return {"checks": checks, "automatic_pass": all(check["passed"] for check in checks)}
