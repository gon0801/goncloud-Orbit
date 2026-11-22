"""Informes comparables sin fabricar datos faltantes ni una nota global."""

from __future__ import annotations

import json
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
from .schemas import GradeFileModel, MetricsModel, ReviewModel, validate_model


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


def _validate_metrics(path: Path) -> dict[str, int | float | None]:
    return validate_model(MetricsModel, _read_json(path), error_cls=BenchError, context=path)


def _validate_review(path: Path, dimensions: list[str]) -> dict[str, Any]:
    review = validate_model(ReviewModel, _read_json(path), error_cls=BenchError, context=path)
    if set(review["scores"]) != set(dimensions):
        raise BenchError(f"dimensiones invalidas en {path}")
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
    grade = validate_model(GradeFileModel, _read_json(path), error_cls=BenchError, context=path)
    if grade["case_id"] != case_id:
        raise BenchError(f"case_id invalido en {path}")
    _validate_grade_result(
        {"checks": grade["checks"], "automatic_pass": grade["automatic_pass"]},
        automatic,
    )
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
            lines.append(f"- Autor padre: {parent_identity[0]} / {parent_identity[1]}")
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
            if isinstance(metadata.get("extension"), dict):
                lines.append(f"  - intento padre: `{metadata['extension']['parent_run_id']}`")
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
