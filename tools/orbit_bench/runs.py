"""Ciclo de vida auditable de los intentos de Orbit Bench."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import stat
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from . import catalog, graders

METRIC_FIELDS = (
    "duration_seconds",
    "cost_usd",
    "interventions",
    "rework_minutes",
    "input_tokens",
    "output_tokens",
)


class BenchError(ValueError):
    """Error de contrato o integridad del benchmark."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BenchError(f"valor no representable como JSON: {exc}") from exc


def json_fingerprint(value: Any) -> str:
    """Calcula una huella estable de un valor JSON."""

    return hashlib.sha256(_canonical_json(value)).hexdigest()


def grade_fingerprint(grade: dict[str, Any]) -> str:
    """Liga una revision al resultado semantico, no a su fecha de escritura."""

    semantic = {key: value for key, value in grade.items() if key != "graded_at"}
    return json_fingerprint(semantic)


def _safe_relative(raw: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise BenchError(f"ruta insegura: {raw!r}")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise BenchError(f"ruta insegura: {raw!r}")
    return Path(*pure.parts)


def _assert_plain_path(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise BenchError(f"falta la ruta: {path}") from exc
    if stat.S_ISLNK(mode):
        raise BenchError(f"enlace simbolico prohibido: {path}")
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise BenchError(f"tipo de archivo prohibido: {path}")


def tree_fingerprint(root: Path | str) -> str:
    """Calcula SHA256 de nombres y contenidos, rechazando enlaces y tipos especiales."""

    root = Path(root)
    _assert_plain_path(root)
    digest = hashlib.sha256()
    entries = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in entries:
        _assert_plain_path(path)
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        else:
            digest.update(b"F\0" + relative + b"\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    _assert_plain_path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchError(f"JSON invalido en {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BenchError(f"se esperaba un objeto JSON en {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


def _validate_config(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise BenchError("config debe ser un objeto JSON")
    canonical = _canonical_json(config)
    return json.loads(canonical.decode("utf-8"))


def _validate_identity(model: str, harness: str, attempt: int) -> None:
    if not isinstance(model, str) or not model.strip():
        raise BenchError("model no puede estar vacio")
    if not isinstance(harness, str) or not harness.strip():
        raise BenchError("harness no puede estar vacio")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise BenchError("attempt debe ser un entero positivo")


def _validate_limit(name: str, value: int | float | None) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise BenchError(f"{name} debe ser un numero no negativo o null")
    return float(value)


def _case_snapshot(case_id: str, case: dict[str, Any]) -> dict[str, Any]:
    files = case.get("files")
    artifacts = case.get("artifacts")
    dimensions = case.get("dimensions")
    if not isinstance(files, dict) or not all(
        isinstance(path, str) and isinstance(content, str) for path, content in files.items()
    ):
        raise BenchError("files del caso debe mapear rutas a texto")
    if not isinstance(artifacts, list) or not all(isinstance(item, str) for item in artifacts):
        raise BenchError("artifacts del caso debe ser una lista de rutas")
    if not isinstance(dimensions, list) or not all(isinstance(item, str) for item in dimensions):
        raise BenchError("dimensions del caso debe ser una lista")
    for relative in [*files, *artifacts]:
        _safe_relative(relative)
    required = ("title", "category", "prompt", "automatic", "extension_of")
    if any(key not in case for key in required):
        raise BenchError("caso incompleto")
    return {
        "title": case["title"],
        "category": case["category"],
        "artifacts": list(artifacts),
        "dimensions": list(dimensions),
        "automatic": case["automatic"],
        "extension_of": case["extension_of"],
        "task_text": _task_text(case_id, case),
        "initial_files": dict(files),
    }


def _task_text(case_id: str, case: dict[str, Any]) -> str:
    artifacts = "\n".join(f"- `{item}`" for item in case["artifacts"])
    return (
        f"# {case['title']}\n\n"
        f"Caso: `{case_id}`\n\n"
        f"{case['prompt'].rstrip()}\n\n"
        f"## Entregables\n\n{artifacts}\n"
    )


def _review_template(case: dict[str, Any], fingerprint: str | None = None) -> dict[str, Any]:
    return {
        "reviewer": None,
        "accepted": None,
        "scores": {dimension: {"score": None, "evidence": ""} for dimension in case["dimensions"]},
        "critical_failures": [],
        "grade_fingerprint": fingerprint,
    }


def _metrics_template() -> dict[str, None]:
    return {field: None for field in METRIC_FIELDS}


def _write_candidate_seed(candidate: Path, case_id: str, case: dict[str, Any]) -> None:
    candidate.mkdir()
    (candidate / "TASK.md").write_text(_task_text(case_id, case), encoding="utf-8")
    for raw, content in case["files"].items():
        destination = candidate / _safe_relative(raw)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise BenchError(f"archivo inicial duplicado: {raw}")
        destination.write_text(content, encoding="utf-8")


def _base_metadata(
    *,
    case_id: str,
    case: dict[str, Any],
    model: str,
    harness: str,
    config: dict[str, Any],
    attempt: int,
    limits: dict[str, float | None],
    initial_fingerprint: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex,
        "case_id": case_id,
        "case": _case_snapshot(case_id, case),
        "model": model.strip(),
        "harness": harness.strip(),
        "config": config,
        "benchmark_version": catalog.VERSION,
        "attempt": attempt,
        "limits": limits,
        "initial_fingerprint": initial_fingerprint,
        "created_at": _now(),
        "extension": None,
    }


def prepare_run(
    *,
    case_id: str,
    model: str,
    harness: str,
    config: dict[str, Any],
    attempt: int,
    output: Path | str,
    time_limit: int | float | None = 1800,
    budget_usd: int | float | None = 5,
) -> Path:
    """Prepara un intento nuevo sin ejecutar ningun modelo."""

    if case_id not in catalog.CASES:
        raise BenchError(f"caso desconocido: {case_id}")
    case = catalog.CASES[case_id]
    if case.get("extension_of") is not None:
        raise BenchError("los casos de mantenimiento se crean con extend")
    _validate_identity(model, harness, attempt)
    normalized_config = _validate_config(config)
    _case_snapshot(case_id, case)
    limits = {
        "time_seconds": _validate_limit("time_limit", time_limit),
        "budget_usd": _validate_limit("budget_usd", budget_usd),
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    candidate = output / "candidate"
    _write_candidate_seed(candidate, case_id, case)
    metadata = _base_metadata(
        case_id=case_id,
        case=case,
        model=model,
        harness=harness,
        config=normalized_config,
        attempt=attempt,
        limits=limits,
        initial_fingerprint=tree_fingerprint(candidate),
    )
    _write_json(output / "run.json", metadata)
    _write_json(output / "review.json", _review_template(case))
    _write_json(output / "metrics.json", _metrics_template())
    return output


def load_run(run: Path | str) -> tuple[Path, dict[str, Any]]:
    """Carga los metadatos minimos y confirma la estructura del intento."""

    run = Path(run)
    _assert_plain_path(run)
    metadata = _read_json(run / "run.json")
    required = {
        "run_id",
        "case_id",
        "case",
        "model",
        "harness",
        "config",
        "benchmark_version",
        "attempt",
        "limits",
        "initial_fingerprint",
    }
    missing = required - metadata.keys()
    if missing:
        raise BenchError(f"run.json incompleto: {', '.join(sorted(missing))}")
    if not isinstance(metadata["case"], dict):
        raise BenchError("case debe ser un objeto en run.json")
    _assert_plain_path(run / "candidate")
    extension = metadata.get("extension")
    if isinstance(extension, dict):
        expected_baseline = extension.get("baseline_fingerprint")
        if not isinstance(expected_baseline, str):
            raise BenchError("falta la huella del baseline de la extension")
        if tree_fingerprint(run / "baseline") != expected_baseline:
            raise BenchError("el baseline de la extension fue alterado")
    return run, metadata


def _validate_grade_result(result: object, expected_automatic: bool) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise BenchError("el calificador no devolvio un objeto")
    checks = result.get("checks")
    automatic_pass = result.get("automatic_pass")
    if not isinstance(checks, list):
        raise BenchError("checks del calificador debe ser una lista")
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"name", "passed", "detail"}:
            raise BenchError("check del calificador invalido")
        if (
            not isinstance(check["name"], str)
            or not isinstance(check["passed"], bool)
            or not isinstance(check["detail"], str)
        ):
            raise BenchError("tipos invalidos en check del calificador")
    expected = {True, False} if expected_automatic else {None}
    if (
        automatic_pass not in expected
        or isinstance(automatic_pass, int)
        and not isinstance(automatic_pass, bool)
    ):
        raise BenchError("automatic_pass incompatible con el caso")
    if expected_automatic and automatic_pass is not all(check["passed"] for check in checks):
        raise BenchError("automatic_pass no coincide con los checks")
    return {"checks": checks, "automatic_pass": automatic_pass}


def _review_has_human_content(review: dict[str, Any]) -> bool:
    if review.get("reviewer") is not None or review.get("accepted") is not None:
        return True
    if review.get("critical_failures"):
        return True
    scores = review.get("scores")
    if not isinstance(scores, dict):
        return True
    return any(
        not isinstance(value, dict)
        or value.get("score") is not None
        or value.get("evidence") not in {None, ""}
        for value in scores.values()
    )


def _artifacts_present(run: Path, case: dict[str, Any]) -> bool:
    candidate = run / "candidate"
    for raw in case["artifacts"]:
        artifact = candidate / _safe_relative(raw)
        try:
            _assert_plain_path(artifact)
        except BenchError:
            return False
        if not artifact.is_file():
            return False
    return True


def grade_run(run: Path | str, timeout: float = 5.0) -> dict[str, Any]:
    """Califica una entrega y liga una plantilla humana aun vacia a ese resultado."""

    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise BenchError("timeout debe ser positivo")
    run, metadata = load_run(run)
    if metadata["benchmark_version"] != catalog.VERSION:
        raise BenchError("la version del catalogo no coincide con el intento")
    candidate = run / "candidate"
    submission_fingerprint = tree_fingerprint(candidate)
    try:
        task_text = (candidate / "TASK.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BenchError(f"TASK.md invalido: {exc}") from exc
    if task_text != metadata["case"].get("task_text"):
        raise BenchError("TASK.md fue alterado; restaura la tarea sellada")
    try:
        raw_result = graders.grade(metadata["case_id"], candidate, timeout=float(timeout))
        result = _validate_grade_result(raw_result, metadata["case"]["automatic"])
    except BenchError:
        raise
    except Exception as exc:  # El fallo de evaluacion se conserva como evidencia negativa.
        result = {
            "checks": [
                {
                    "name": "evaluacion",
                    "passed": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            ],
            "automatic_pass": False if metadata["case"]["automatic"] else None,
        }
    fresh = {
        "case_id": metadata["case_id"],
        "submission_fingerprint": submission_fingerprint,
        **result,
        "artifacts_present": _artifacts_present(run, metadata["case"]),
        "graded_at": _now(),
    }
    grade_path = run / "grade.json"
    if grade_path.exists():
        existing = _read_json(grade_path)
        if grade_fingerprint(existing) == grade_fingerprint(fresh):
            fresh = existing
    _write_json(grade_path, fresh)

    review_path = run / "review.json"
    review = _read_json(review_path)
    if not _review_has_human_content(review):
        review = _review_template(metadata["case"], grade_fingerprint(fresh))
        _write_json(review_path, review)
    return fresh


def _find_extension(parent_case_id: str) -> tuple[str, dict[str, Any]]:
    matches = [
        (case_id, case)
        for case_id, case in catalog.CASES.items()
        if case.get("extension_of") == parent_case_id
    ]
    if len(matches) != 1:
        raise BenchError(f"se esperaba una extension unica para {parent_case_id}")
    return matches[0]


def extend_run(
    run: Path | str,
    output: Path | str,
    *,
    model: str | None = None,
    harness: str | None = None,
    config: dict[str, Any] | None = None,
) -> Path:
    """Congela una entrega calificada como entrada de mantenibilidad."""

    parent, parent_metadata = load_run(run)
    if parent_metadata["benchmark_version"] != catalog.VERSION:
        raise BenchError("la version del catalogo no coincide con el intento padre")
    parent_grade_path = parent / "grade.json"
    if not parent_grade_path.is_file():
        raise BenchError("el intento padre debe calificarse antes de extenderlo")
    parent_grade = _read_json(parent_grade_path)
    current_submission = tree_fingerprint(parent / "candidate")
    if parent_grade.get("submission_fingerprint") != current_submission:
        raise BenchError("la calificacion del intento padre esta obsoleta")
    extension_id, extension_case = _find_extension(parent_metadata["case_id"])
    _case_snapshot(extension_id, extension_case)
    selected_model = parent_metadata["model"] if model is None else model
    selected_harness = parent_metadata["harness"] if harness is None else harness
    selected_config = parent_metadata["config"] if config is None else config
    _validate_identity(selected_model, selected_harness, 1)
    normalized_config = _validate_config(selected_config)

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    baseline = output / "baseline"
    shutil.copytree(parent / "candidate", baseline)
    candidate = output / "candidate"
    shutil.copytree(baseline, candidate)
    (candidate / "TASK.md").write_text(_task_text(extension_id, extension_case), encoding="utf-8")
    for raw, content in extension_case["files"].items():
        destination = candidate / _safe_relative(raw)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    original_task = parent_metadata["case"]["task_text"]
    (candidate / "ORIGINAL_TASK.md").write_text(original_task, encoding="utf-8")
    initial_fingerprint = tree_fingerprint(candidate)
    metadata = _base_metadata(
        case_id=extension_id,
        case=extension_case,
        model=selected_model,
        harness=selected_harness,
        config=normalized_config,
        attempt=1,
        limits=parent_metadata["limits"],
        initial_fingerprint=initial_fingerprint,
    )
    metadata["extension"] = {
        "parent_run_id": parent_metadata["run_id"],
        "parent_case_id": parent_metadata["case_id"],
        "parent_model": parent_metadata["model"],
        "parent_harness": parent_metadata["harness"],
        "parent_config": parent_metadata["config"],
        "parent_config_fingerprint": json_fingerprint(parent_metadata["config"]),
        "parent_benchmark_version": parent_metadata["benchmark_version"],
        "parent_initial_fingerprint": parent_metadata["initial_fingerprint"],
        "parent_submission_fingerprint": current_submission,
        "parent_grade_fingerprint": grade_fingerprint(parent_grade),
        "parent_automatic_pass": parent_grade.get("automatic_pass"),
        "baseline_fingerprint": tree_fingerprint(baseline),
    }
    metadata["case"]["initial_files"]["ORIGINAL_TASK.md"] = original_task
    _write_json(output / "run.json", metadata)
    _write_json(output / "review.json", _review_template(extension_case))
    _write_json(output / "metrics.json", _metrics_template())
    return output


_DIMENSION_ANCHORS = {
    "razonamiento": "Restricciones, consecuencias, contraejemplos y decisiones verificables.",
    "correccion": "Contrato, invariantes y fronteras; cero y desconocido siguen distintos.",
    "limpieza": "Nombres y responsabilidades claros, poca duplicacion y estilo adecuado.",
    "arquitectura": (
        "Alternativas, duenos, interfaces, fallos y reversa con complejidad proporcional."
    ),
    "diseno": "Jerarquia, legibilidad, estados, accesibilidad, efecto y evidencia comprensibles.",
    "revision": "Hallazgos reproducibles y priorizados, impacto, arreglo y falsas alarmas.",
    "mantenibilidad": "El cambio encaja sin regresiones ni excepciones dispersas.",
    "autonomia": "Entrega dentro de limites, decisiones razonables y bloqueos comunicados.",
}


def _rubric_text(case: dict[str, Any], blind_id: str) -> str:
    dimensions = "\n".join(
        f"- {dimension}: {_DIMENSION_ANCHORS[dimension]}" for dimension in case["dimensions"]
    )
    return (
        f"# Rubrica de revision {blind_id}\n\n"
        "Califica solo la evidencia del paquete. Escala: 0 ausente o inutil; "
        "1 con errores graves; 2 util con correcciones importantes; "
        "3 aceptable con ajustes menores; 4 listo y justificado.\n\n"
        f"## Dimensiones\n\n{dimensions}\n\n"
        "Un fallo critico impide aceptar la entrega aunque las notas sean altas.\n"
    )


def blind_run(run: Path | str, output: Path | str) -> Path:
    """Exporta un paquete sin los metadatos de identidad del intento."""

    run, metadata = load_run(run)
    grade_path = run / "grade.json"
    if not grade_path.is_file():
        raise BenchError("califica la entrega antes de exportarla para revision")
    grade = _read_json(grade_path)
    current_submission = tree_fingerprint(run / "candidate")
    if grade.get("submission_fingerprint") != current_submission:
        raise BenchError("la calificacion esta obsoleta")
    if not _artifacts_present(run, metadata["case"]):
        raise BenchError("faltan entregables para exportar")

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    packet = output / "packet"
    packet.mkdir()
    blind_id = f"OB-{uuid.uuid4().hex[:12]}"
    candidate = run / "candidate"
    (packet / "TASK.md").write_text(metadata["case"]["task_text"], encoding="utf-8")
    shutil.copytree(candidate, packet / "submission")
    if isinstance(metadata.get("extension"), dict):
        shutil.copytree(run / "baseline", packet / "baseline")
    context = packet / "context"
    for raw, content in metadata["case"]["initial_files"].items():
        destination = context / _safe_relative(raw)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    (packet / "RUBRIC.md").write_text(_rubric_text(metadata["case"], blind_id), encoding="utf-8")
    blind_review = _review_template(metadata["case"], grade_fingerprint(grade))
    _write_json(packet / "review.json", blind_review)
    _write_json(
        output / "operator-map.json",
        {
            "blind_id": blind_id,
            "run_id": metadata["run_id"],
            "run_path": str(run.resolve()),
            "model": metadata["model"],
            "harness": metadata["harness"],
            "config": metadata["config"],
            "grade_fingerprint": grade_fingerprint(grade),
            "created_at": _now(),
        },
    )
    return output


def _parse_config(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BenchError(f"config no es JSON valido: {exc}") from exc
    return _validate_config(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.orbit_bench")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="lista los casos")

    prepare = commands.add_parser("prepare", help="prepara un intento")
    prepare.add_argument("--case", required=True, dest="case_id")
    prepare.add_argument("--model", required=True)
    prepare.add_argument("--harness", required=True)
    prepare.add_argument("--config", required=True)
    prepare.add_argument("--attempt", required=True, type=int)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--time-limit", type=float, default=1800)
    prepare.add_argument("--budget-usd", type=float, default=5)

    grade = commands.add_parser("grade", help="califica una entrega")
    grade.add_argument("--run", required=True, type=Path)
    grade.add_argument("--timeout", type=float, default=5)

    extend = commands.add_parser("extend", help="crea la fase de mantenibilidad")
    extend.add_argument("--run", required=True, type=Path)
    extend.add_argument("--output", required=True, type=Path)
    extend.add_argument("--model")
    extend.add_argument("--harness")
    extend.add_argument("--config")

    blind = commands.add_parser("blind", help="exporta un paquete ciego")
    blind.add_argument("--run", required=True, type=Path)
    blind.add_argument("--output", required=True, type=Path)

    report_parser = commands.add_parser("report", help="genera un informe comparativo")
    report_parser.add_argument("--runs", required=True, type=Path)
    report_parser.add_argument("--output", type=Path)
    return parser


def cli(argv: list[str] | None = None) -> int:
    """Ejecuta el CLI; devuelve 0 al completar y 2 ante datos invalidos."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "list":
            for case_id, case in catalog.CASES.items():
                if case["extension_of"] is None:
                    print(f"{case_id}\t{case['category']}\t{case['title']}")
        elif arguments.command == "prepare":
            destination = prepare_run(
                case_id=arguments.case_id,
                model=arguments.model,
                harness=arguments.harness,
                config=_parse_config(arguments.config),
                attempt=arguments.attempt,
                output=arguments.output,
                time_limit=arguments.time_limit,
                budget_usd=arguments.budget_usd,
            )
            print(destination)
        elif arguments.command == "grade":
            print(json.dumps(grade_run(arguments.run, arguments.timeout), ensure_ascii=False))
        elif arguments.command == "extend":
            destination = extend_run(
                arguments.run,
                arguments.output,
                model=arguments.model,
                harness=arguments.harness,
                config=None if arguments.config is None else _parse_config(arguments.config),
            )
            print(destination)
        elif arguments.command == "blind":
            print(blind_run(arguments.run, arguments.output))
        elif arguments.command == "report":
            from .report import build_report, write_report

            rendered = build_report(arguments.runs)
            if arguments.output is None:
                print(rendered, end="")
            else:
                write_report(arguments.runs, arguments.output, rendered=rendered)
                print(arguments.output)
    except (BenchError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
