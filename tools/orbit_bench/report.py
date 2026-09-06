"""Informes comparables sin fabricar datos faltantes ni una nota global."""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .runs import (
    METRIC_FIELDS,
    BenchError,
    _read_json,
    _safe_relative,
    _validate_grade_result,
    grade_fingerprint,
    load_run,
    tree_fingerprint,
)


def _run_directories(root: Path) -> list[Path]:
    if root.is_file():
        raise BenchError("--runs debe apuntar a un directorio")
    found: list[Path] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        if "run.json" in files:
            found.append(Path(current))
            directories.clear()
    return found


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_metrics(path: Path) -> dict[str, int | float | None]:
    metrics = _read_json(path)
    if set(metrics) != set(METRIC_FIELDS):
        raise BenchError(f"campos invalidos en {path}")
    for field in ("duration_seconds", "cost_usd", "rework_minutes"):
        value = metrics[field]
        if value is not None and (not _number(value) or not math.isfinite(value) or value < 0):
            raise BenchError(f"metrica invalida {field} en {path}")
    for field in ("interventions", "input_tokens", "output_tokens"):
        value = metrics[field]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise BenchError(f"metrica invalida {field} en {path}")
    return metrics


def _validate_review(path: Path, dimensions: list[str]) -> dict[str, Any]:
    review = _read_json(path)
    required = {"reviewer", "accepted", "scores", "critical_failures", "grade_fingerprint"}
    if set(review) != required:
        raise BenchError(f"campos invalidos en {path}")
    if review["reviewer"] is not None and (
        not isinstance(review["reviewer"], str) or not review["reviewer"].strip()
    ):
        raise BenchError(f"reviewer invalido en {path}")
    if review["accepted"] is not None and not isinstance(review["accepted"], bool):
        raise BenchError(f"accepted invalido en {path}")
    failures = review["critical_failures"]
    if not isinstance(failures, list) or not all(
        isinstance(item, str) and item.strip() for item in failures
    ):
        raise BenchError(f"critical_failures invalido en {path}")
    if review["grade_fingerprint"] is not None and not isinstance(review["grade_fingerprint"], str):
        raise BenchError(f"grade_fingerprint invalido en {path}")
    scores = review["scores"]
    if not isinstance(scores, dict) or set(scores) != set(dimensions):
        raise BenchError(f"dimensiones invalidas en {path}")
    for dimension, item in scores.items():
        if not isinstance(item, dict) or set(item) != {"score", "evidence"}:
            raise BenchError(f"nota invalida para {dimension} en {path}")
        score = item["score"]
        if score is not None and (
            isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 4
        ):
            raise BenchError(f"nota invalida para {dimension} en {path}")
        if not isinstance(item["evidence"], str):
            raise BenchError(f"evidence invalida para {dimension} en {path}")
    return review


def _artifacts_present(run: Path, case: dict[str, Any]) -> bool:
    for raw in case["artifacts"]:
        path = run / "candidate" / _safe_relative(raw)
        if not path.is_file() or path.is_symlink():
            return False
    return True


def _human_complete(review: dict[str, Any], dimensions: list[str]) -> bool:
    return (
        isinstance(review["reviewer"], str)
        and bool(review["reviewer"].strip())
        and isinstance(review["accepted"], bool)
        and all(
            review["scores"][dimension]["score"] is not None
            and bool(review["scores"][dimension]["evidence"].strip())
            for dimension in dimensions
        )
    )


def _validate_grade(
    path: Path,
    *,
    case_id: str,
    automatic: bool,
) -> dict[str, Any]:
    grade = _read_json(path)
    required = {
        "case_id",
        "submission_fingerprint",
        "checks",
        "automatic_pass",
        "artifacts_present",
        "graded_at",
    }
    if set(grade) != required:
        raise BenchError(f"campos invalidos en {path}")
    if grade["case_id"] != case_id:
        raise BenchError(f"case_id invalido en {path}")
    fingerprint = grade["submission_fingerprint"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
    ):
        raise BenchError(f"submission_fingerprint invalido en {path}")
    if not isinstance(grade["artifacts_present"], bool):
        raise BenchError(f"artifacts_present invalido en {path}")
    if not isinstance(grade["graded_at"], str) or not grade["graded_at"]:
        raise BenchError(f"graded_at invalido en {path}")
    _validate_grade_result(grade, automatic)
    return grade


def _analyze(run: Path) -> dict[str, Any]:
    run, metadata = load_run(run)
    case = metadata["case"]
    dimensions = case["dimensions"]
    metrics = _validate_metrics(run / "metrics.json")
    review = _validate_review(run / "review.json", dimensions)
    grade_path = run / "grade.json"
    grade = (
        _validate_grade(
            grade_path,
            case_id=metadata["case_id"],
            automatic=case["automatic"],
        )
        if grade_path.is_file()
        else None
    )
    reasons: list[str] = []

    grade_current = grade is not None and grade.get("submission_fingerprint") == tree_fingerprint(
        run / "candidate"
    )
    review_current = grade_current and review["grade_fingerprint"] == grade_fingerprint(grade)

    if not _artifacts_present(run, case):
        status = "incompleto"
        reasons.append("faltan entregables")
    elif grade is None:
        status = "pendiente"
        reasons.append("falta calificacion")
    else:
        current_submission = tree_fingerprint(run / "candidate")
        if grade.get("submission_fingerprint") != current_submission:
            status = "incompleto"
            reasons.append("calificacion obsoleta")
        elif review["grade_fingerprint"] not in {None, grade_fingerprint(grade)}:
            status = "incompleto"
            reasons.append("revision obsoleta")
        elif any(not check["passed"] for check in grade["checks"]):
            status = "rechazado"
            reasons.append("fallo de evaluacion")
        elif grade.get("automatic_pass") is False:
            status = "rechazado"
            reasons.append("fallo automatico")
        elif review["critical_failures"]:
            status = "rechazado"
            reasons.append("fallo critico")
        elif not _human_complete(review, dimensions):
            status = "pendiente"
            reasons.append("falta revision humana completa")
        elif review["grade_fingerprint"] != grade_fingerprint(grade):
            status = "incompleto"
            reasons.append("revision sin huella vigente")
        elif review["accepted"] is False:
            status = "rechazado"
            reasons.append("revision humana rechazada")
        else:
            status = "aceptado"

    return {
        "path": run,
        "metadata": metadata,
        "metrics": metrics,
        "review": review,
        "grade": grade,
        "status": status,
        "reasons": reasons,
        "scores_current": review_current,
    }


def _group_key(item: dict[str, Any]) -> tuple[Any, ...]:
    metadata = item["metadata"]
    limits = metadata["limits"]
    extension = metadata.get("extension")
    parent_identity = None
    if isinstance(extension, dict):
        parent_identity = (
            extension.get("parent_model"),
            extension.get("parent_harness"),
            extension.get("parent_config_fingerprint"),
            extension.get("parent_benchmark_version"),
            extension.get("parent_initial_fingerprint"),
        )
    return (
        metadata["model"],
        metadata["harness"],
        json.dumps(metadata["config"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        metadata["benchmark_version"],
        metadata["initial_fingerprint"],
        metadata["case_id"],
        limits.get("time_seconds"),
        limits.get("budget_usd"),
        parent_identity,
    )


def _format_number(value: int | float) -> str:
    return f"{value:g}"


def _metric_summary(items: list[dict[str, Any]], field: str) -> str:
    known = [item["metrics"][field] for item in items if item["metrics"][field] is not None]
    total = len(items)
    if not known:
        return f"desconocido (0/{total})"
    summed = sum(known)
    return (
        f"total {_format_number(summed)}, media {summed / len(known):.2f}, n={len(known)}/{total}"
    )


def build_report(runs_root: Path | str) -> str:
    """Agrega intentos equivalentes y conserva el denominador de cada dato."""

    root = Path(runs_root)
    analyzed = [_analyze(path) for path in _run_directories(root)]
    seen: dict[str, Path] = {}
    for item in analyzed:
        run_id = item["metadata"]["run_id"]
        if run_id in seen:
            raise BenchError(f"run_id duplicado {run_id}: {seen[run_id]} y {item['path']}")
        seen[run_id] = item["path"]
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in analyzed:
        grouped[_group_key(item)].append(item)

    lines = ["# Orbit Bench", ""]
    if not grouped:
        return "# Orbit Bench\n\nNo hay intentos.\n"
    for key in sorted(grouped, key=lambda value: tuple(str(item) for item in value)):
        model, harness, config, version, initial, case_id, time_limit, budget, parent_identity = key
        items = grouped[key]
        lines.extend(
            [
                f"## {model} / {harness} / {case_id}",
                "",
                f"- Configuracion: `{config}`",
                f"- Benchmark: `{version}`",
                f"- Huella inicial: `{initial}`",
                f"- Limites: tiempo={time_limit}, presupuesto_usd={budget}",
                f"- Intentos: {len(items)}",
            ]
        )
        if parent_identity is not None:
            parent_config = items[0]["metadata"]["extension"]["parent_config"]
            lines.append(
                "- Configuracion padre: `"
                + json.dumps(
                    parent_config,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "`"
            )
        counts = Counter(item["status"] for item in items)
        coverage = ", ".join(
            f"{status}={counts.get(status, 0)}"
            for status in ("aceptado", "rechazado", "pendiente", "incompleto")
        )
        lines.extend([f"- Estados: {coverage}", "", "### Notas", ""])
        dimensions = items[0]["metadata"]["case"]["dimensions"]
        for dimension in dimensions:
            values = [
                item["review"]["scores"][dimension]["score"]
                for item in items
                if item["scores_current"]
                and item["review"]["scores"][dimension]["score"] is not None
                and item["review"]["scores"][dimension]["evidence"].strip()
            ]
            if values:
                lines.append(
                    f"- {dimension}: media {sum(values) / len(values):.2f}, "
                    f"rango {min(values)}-{max(values)}, n={len(values)}/{len(items)}"
                )
            else:
                lines.append(f"- {dimension}: sin datos, n=0/{len(items)}")
        lines.extend(["", "### Metricas", ""])
        for field in METRIC_FIELDS:
            lines.append(f"- {field}: {_metric_summary(items, field)}")
        lines.extend(["", "### Intentos", ""])
        for item in sorted(items, key=lambda value: value["metadata"]["attempt"]):
            metadata = item["metadata"]
            reason = f" ({'; '.join(item['reasons'])})" if item["reasons"] else ""
            lines.append(
                f"- intento {metadata['attempt']} `{metadata['run_id']}`: {item['status']}{reason}"
            )
            for failure in item["review"]["critical_failures"]:
                lines.append(f"  - fallo critico: {failure}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(
    runs_root: Path | str,
    output: Path | str,
    *,
    rendered: str | None = None,
) -> Path:
    """Escribe el informe solicitado por el operador."""

    output = Path(output)
    content = build_report(runs_root) if rendered is None else rendered
    output.write_text(content, encoding="utf-8")
    return output
