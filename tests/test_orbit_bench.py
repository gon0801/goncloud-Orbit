from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.orbit_bench import report, runs

CASE = {
    "title": "Caso ejecutable",
    "category": "codigo",
    "prompt": "Implementa la solucion.",
    "files": {"input.json": "{}\n"},
    "artifacts": ["solution.py"],
    "dimensions": ["razonamiento", "correccion"],
    "automatic": True,
    "extension_of": None,
}
EXTENSION = {
    "title": "Mantenimiento",
    "category": "mantenibilidad",
    "prompt": "Mantiene la entrega congelada.",
    "files": {"CHANGE.md": "Describe el cambio.\n"},
    "artifacts": ["solution.py", "CHANGE.md"],
    "dimensions": ["mantenibilidad"],
    "automatic": True,
    "extension_of": "codigo",
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def fake_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs.catalog, "VERSION", "test-v1")
    monkeypatch.setattr(
        runs.catalog,
        "CASES",
        {"codigo": CASE, "mantenibilidad": EXTENSION},
    )


def _prepare(tmp_path: Path, *, name: str = "run", attempt: int = 1) -> Path:
    return runs.prepare_run(
        case_id="codigo",
        model="modelo-a",
        harness="codex",
        config={"effort": "high", "temperature": 0},
        attempt=attempt,
        output=tmp_path / name,
        time_limit=120,
        budget_usd=3.5,
    )


def _deliver_and_grade(monkeypatch: pytest.MonkeyPatch, run: Path) -> dict:
    (run / "candidate" / "solution.py").write_text("print('{}')\n", encoding="utf-8")

    def passing_grade(case_id: str, candidate: Path, timeout: float = 5.0) -> dict:
        assert case_id in {"codigo", "mantenibilidad"}
        assert candidate.is_dir()
        assert timeout == 2.0
        return {
            "checks": [{"name": "salida", "passed": True, "detail": "correcta"}],
            "automatic_pass": True,
        }

    monkeypatch.setattr(runs.graders, "grade", passing_grade)
    return runs.grade_run(run, timeout=2.0)


def _complete_review(run: Path, *, critical_failures: list[str] | None = None) -> None:
    review = _json(run / "review.json")
    review.update(
        {
            "reviewer": "persona-1",
            "accepted": True,
            "scores": {
                "razonamiento": {"score": 4, "evidence": "Explica cada decision."},
                "correccion": {"score": 3, "evidence": "Resuelve los bordes."},
            },
            "critical_failures": critical_failures or [],
        }
    )
    _write_json(run / "review.json", review)


def test_prepare_creates_auditable_attempt_without_overwriting(
    fake_catalog: None, tmp_path: Path
) -> None:
    run = _prepare(tmp_path)

    metadata = _json(run / "run.json")
    assert metadata["case_id"] == "codigo"
    assert metadata["model"] == "modelo-a"
    assert metadata["harness"] == "codex"
    assert metadata["config"] == {"effort": "high", "temperature": 0}
    assert metadata["benchmark_version"] == "test-v1"
    assert metadata["attempt"] == 1
    assert metadata["limits"] == {"time_seconds": 120.0, "budget_usd": 3.5}
    assert len(metadata["run_id"]) == 32
    assert len(metadata["initial_fingerprint"]) == 64
    assert (run / "candidate" / "TASK.md").read_text(encoding="utf-8").endswith("\n")
    assert _json(run / "metrics.json") == {
        "duration_seconds": None,
        "cost_usd": None,
        "interventions": None,
        "rework_minutes": None,
        "input_tokens": None,
        "output_tokens": None,
    }
    assert _json(run / "review.json") == {
        "reviewer": None,
        "accepted": None,
        "scores": {
            "razonamiento": {"score": None, "evidence": ""},
            "correccion": {"score": None, "evidence": ""},
        },
        "critical_failures": [],
        "grade_fingerprint": None,
    }

    with pytest.raises(FileExistsError):
        _prepare(tmp_path)
    with pytest.raises(runs.BenchError, match="budget_usd"):
        runs.prepare_run(
            case_id="codigo",
            model="modelo-a",
            harness="codex",
            config={},
            attempt=1,
            output=tmp_path / "nan-limit",
            budget_usd=float("nan"),
        )


def test_grade_freezes_submission_and_completed_review_becomes_stale_after_change(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _prepare(tmp_path)
    first_grade = _deliver_and_grade(monkeypatch, run)
    review = _json(run / "review.json")
    assert review["grade_fingerprint"] == runs.grade_fingerprint(first_grade)
    _complete_review(run)
    assert "aceptado" in report.build_report(tmp_path)

    (run / "candidate" / "solution.py").write_text("print('cambio')\n", encoding="utf-8")
    second_grade = runs.grade_run(run, timeout=2.0)

    assert second_grade["submission_fingerprint"] != first_grade["submission_fingerprint"]
    assert _json(run / "review.json")["reviewer"] == "persona-1"
    stale_report = report.build_report(tmp_path)
    assert "incompleto" in stale_report
    assert "revision obsoleta" in stale_report
    assert "razonamiento: sin datos, n=0/1" in stale_report


def test_grade_is_idempotent_for_same_delivery_and_preserves_completed_review(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _prepare(tmp_path)
    first = _deliver_and_grade(monkeypatch, run)
    _complete_review(run)

    second = runs.grade_run(run, timeout=2.0)

    assert second == first
    assert _json(run / "review.json")["accepted"] is True
    assert "aceptado" in report.build_report(tmp_path)
    with pytest.raises(runs.BenchError, match="timeout"):
        runs.grade_run(run, timeout=float("inf"))


def test_grade_rejects_tampered_task_and_catalog_version_mismatch(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _prepare(tmp_path)
    metadata = _json(run / "run.json")
    assert metadata["case"]["task_text"] == (run / "candidate" / "TASK.md").read_text(
        encoding="utf-8"
    )
    (run / "candidate" / "TASK.md").write_text("Tarea reemplazada\n", encoding="utf-8")
    with pytest.raises(runs.BenchError, match="TASK.md"):
        runs.grade_run(run)

    (run / "candidate" / "TASK.md").write_text(metadata["case"]["task_text"], encoding="utf-8")
    monkeypatch.setattr(runs.catalog, "VERSION", "otra-version")
    with pytest.raises(runs.BenchError, match="version"):
        runs.grade_run(run)


def test_report_rejects_critical_failure_and_keeps_unknown_metrics_unknown(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _prepare(tmp_path)
    _deliver_and_grade(monkeypatch, run)
    _complete_review(run, critical_failures=["Escribe fuera del paquete"])

    rendered = report.build_report(tmp_path)

    assert "rechazado" in rendered
    assert "Escribe fuera del paquete" in rendered
    assert "desconocido (0/1)" in rendered
    assert "$0" not in rendered


def test_report_rejects_malformed_automatic_grade_even_if_review_accepted(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _prepare(tmp_path)
    grade = _deliver_and_grade(monkeypatch, run)
    _complete_review(run)
    grade["automatic_pass"] = None
    _write_json(run / "grade.json", grade)
    review = _json(run / "review.json")
    review["grade_fingerprint"] = runs.grade_fingerprint(grade)
    _write_json(run / "review.json", review)

    with pytest.raises(runs.BenchError, match="automatic_pass"):
        report.build_report(tmp_path)


def test_report_does_not_average_score_without_evidence(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _prepare(tmp_path)
    _deliver_and_grade(monkeypatch, run)
    _complete_review(run)
    review = _json(run / "review.json")
    review["scores"]["razonamiento"]["evidence"] = ""
    _write_json(run / "review.json", review)

    rendered = report.build_report(tmp_path)

    assert "razonamiento: sin datos, n=0/1" in rendered
    assert "correccion: media 3.00, rango 3-3, n=1/1" in rendered


def test_report_groups_only_equivalent_attempts_and_shows_score_denominators(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = _prepare(tmp_path, name="attempt-1", attempt=1)
    second = _prepare(tmp_path, name="attempt-2", attempt=2)
    _deliver_and_grade(monkeypatch, first)
    _deliver_and_grade(monkeypatch, second)
    _complete_review(first)

    rendered = report.build_report(tmp_path)

    assert rendered.count("modelo-a / codex / codigo") == 1
    assert "razonamiento: media 4.00, rango 4-4, n=1/2" in rendered
    assert "correccion: media 3.00, rango 3-3, n=1/2" in rendered

    third = runs.prepare_run(
        case_id="codigo",
        model="modelo-a",
        harness="codex",
        config={"effort": "low", "temperature": 0},
        attempt=1,
        output=tmp_path / "different-config",
        time_limit=120,
        budget_usd=3.5,
    )
    _deliver_and_grade(monkeypatch, third)
    assert report.build_report(tmp_path).count("modelo-a / codex / codigo") == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("cost_usd", -1),
        ("cost_usd", float("nan")),
        ("duration_seconds", "rapido"),
        ("interventions", 1.5),
        ("input_tokens", -2),
    ],
)
def test_report_rejects_invalid_metrics(
    fake_catalog: None, tmp_path: Path, field: str, value: object
) -> None:
    run = _prepare(tmp_path)
    metrics = _json(run / "metrics.json")
    metrics[field] = value
    _write_json(run / "metrics.json", metrics)

    with pytest.raises(runs.BenchError, match=field):
        report.build_report(tmp_path)


@pytest.mark.parametrize("score", [-1, 4.1, 5, True, "4"])
def test_report_rejects_invalid_human_scores(
    fake_catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    score: object,
) -> None:
    run = _prepare(tmp_path)
    _deliver_and_grade(monkeypatch, run)
    _complete_review(run)
    review = _json(run / "review.json")
    review["scores"]["correccion"]["score"] = score
    _write_json(run / "review.json", review)

    with pytest.raises(runs.BenchError, match="correccion"):
        report.build_report(tmp_path)


def test_text_case_is_never_accepted_from_artifact_existence_alone(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text_case = dict(CASE, automatic=False, category="diseno")
    monkeypatch.setitem(runs.catalog.CASES, "diseno", text_case)
    run = runs.prepare_run(
        case_id="diseno",
        model="modelo-a",
        harness="codex",
        config={},
        attempt=1,
        output=tmp_path / "text",
    )
    (run / "candidate" / "solution.py").write_text("contenido\n", encoding="utf-8")
    monkeypatch.setattr(
        runs.graders,
        "grade",
        lambda *args, **kwargs: {"checks": [], "automatic_pass": None},
    )
    runs.grade_run(run)

    assert "pendiente" in report.build_report(tmp_path)


def test_failed_manual_evaluation_cannot_be_accepted_by_human_review(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text_case = dict(CASE, automatic=False, category="diseno")
    monkeypatch.setitem(runs.catalog.CASES, "diseno", text_case)
    run = runs.prepare_run(
        case_id="diseno",
        model="modelo-a",
        harness="codex",
        config={},
        attempt=1,
        output=tmp_path / "failed-text",
    )
    (run / "candidate" / "solution.py").write_text("contenido\n", encoding="utf-8")
    monkeypatch.setattr(
        runs.graders,
        "grade",
        lambda *args, **kwargs: {
            "checks": [{"name": "evaluacion", "passed": False, "detail": "fallo"}],
            "automatic_pass": None,
        },
    )
    runs.grade_run(run)
    _complete_review(run)

    rendered = report.build_report(tmp_path)

    assert "rechazado" in rendered
    assert "fallo de evaluacion" in rendered


def test_extension_freezes_parent_delivery_and_provenance_without_mutating_parent(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = _prepare(tmp_path, name="parent")
    (parent / "candidate" / "reglas.py").write_text("VALOR = 1\n", encoding="utf-8")
    parent_grade = _deliver_and_grade(monkeypatch, parent)
    parent_before = runs.tree_fingerprint(parent)

    extension = runs.extend_run(
        parent,
        tmp_path / "extension",
        model="modelo-b",
        harness="terminal",
        config={"effort": "medium"},
    )

    assert runs.tree_fingerprint(parent) == parent_before
    metadata = _json(extension / "run.json")
    assert metadata["case_id"] == "mantenibilidad"
    assert metadata["model"] == "modelo-b"
    assert metadata["extension"]["parent_run_id"] == _json(parent / "run.json")["run_id"]
    assert metadata["extension"]["parent_config"] == {
        "effort": "high",
        "temperature": 0,
    }
    assert (
        metadata["extension"]["parent_submission_fingerprint"]
        == parent_grade["submission_fingerprint"]
    )
    assert metadata["extension"]["parent_grade_fingerprint"] == runs.grade_fingerprint(parent_grade)
    assert (extension / "candidate" / "solution.py").is_file()
    assert (extension / "candidate" / "CHANGE.md").is_file()
    assert (extension / "candidate" / "ORIGINAL_TASK.md").read_text(encoding="utf-8") == _json(
        parent / "run.json"
    )["case"]["task_text"]
    assert (extension / "baseline" / "solution.py").read_text(encoding="utf-8") == (
        parent / "candidate" / "solution.py"
    ).read_text(encoding="utf-8")
    assert (extension / "baseline" / "reglas.py").read_text(encoding="utf-8") == "VALOR = 1\n"
    assert metadata["extension"]["baseline_fingerprint"] == runs.tree_fingerprint(
        extension / "baseline"
    )


def test_report_keeps_parent_configuration_in_extension_comparison_identity(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first_parent = _prepare(tmp_path, name="parent-a")
    second_parent = runs.prepare_run(
        case_id="codigo",
        model="modelo-a",
        harness="codex",
        config={"effort": "low"},
        attempt=1,
        output=tmp_path / "parent-b",
        time_limit=120,
        budget_usd=3.5,
    )
    _deliver_and_grade(monkeypatch, first_parent)
    _deliver_and_grade(monkeypatch, second_parent)
    first_extension = runs.extend_run(
        first_parent,
        tmp_path / "extension-a",
        config={"effort": "medium"},
    )
    second_extension = runs.extend_run(
        second_parent,
        tmp_path / "extension-b",
        config={"effort": "medium"},
    )
    assert (
        _json(first_extension / "run.json")["initial_fingerprint"]
        == _json(second_extension / "run.json")["initial_fingerprint"]
    )

    rendered = report.build_report(tmp_path)

    assert rendered.count("modelo-a / codex / mantenibilidad") == 2
    assert "Configuracion padre:" in rendered


def test_blind_packet_excludes_identity_and_keeps_mapping_outside_packet(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _prepare(tmp_path)
    (run / "candidate" / "input.json").write_text('{"manipulado": true}\n', encoding="utf-8")
    (run / "candidate" / "reglas.py").write_text("VALOR = 1\n", encoding="utf-8")
    _deliver_and_grade(monkeypatch, run)

    exported = runs.blind_run(run, tmp_path / "blind")

    packet = exported / "packet"
    assert (exported / "operator-map.json").is_file()
    assert (packet / "TASK.md").is_file()
    assert (packet / "submission" / "solution.py").is_file()
    assert (packet / "submission" / "reglas.py").read_text(encoding="utf-8") == "VALOR = 1\n"
    assert (packet / "RUBRIC.md").is_file()
    assert (packet / "review.json").is_file()
    assert (packet / "context" / "input.json").read_text(encoding="utf-8") == "{}\n"
    assert not (packet / "run.json").exists()
    assert not (packet / "grade.json").exists()
    assert not (packet / "metrics.json").exists()
    identity = _json(run / "run.json")
    packet_text = "\n".join(
        path.read_text(encoding="utf-8") for path in packet.rglob("*") if path.is_file()
    )
    assert identity["model"] not in packet_text
    assert identity["harness"] not in packet_text
    assert identity["run_id"] not in packet_text
    mapping = _json(exported / "operator-map.json")
    assert mapping["blind_id"] in packet_text
    assert mapping["run_id"] == identity["run_id"]

    with pytest.raises(FileExistsError):
        runs.blind_run(run, exported)


def test_blind_extension_exports_verified_parent_baseline(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = _prepare(tmp_path, name="parent")
    _deliver_and_grade(monkeypatch, parent)
    extension = runs.extend_run(parent, tmp_path / "extension")
    _deliver_and_grade(monkeypatch, extension)

    exported = runs.blind_run(extension, tmp_path / "blind-extension")

    assert (exported / "packet" / "baseline" / "solution.py").read_text(encoding="utf-8") == (
        parent / "candidate" / "solution.py"
    ).read_text(encoding="utf-8")
    assert (exported / "packet" / "submission" / "solution.py").is_file()

    (extension / "baseline" / "solution.py").write_text("alterado\n", encoding="utf-8")
    with pytest.raises(runs.BenchError, match="baseline"):
        runs.blind_run(extension, tmp_path / "blind-stale-baseline")


def test_report_rejects_duplicate_run_ids_and_ignores_nested_candidate_metadata(
    fake_catalog: None, tmp_path: Path
) -> None:
    run = _prepare(tmp_path, name="original")
    nested = run / "candidate" / "fixture"
    nested.mkdir()
    _write_json(nested / "run.json", {"esto": "no es otro intento"})

    rendered = report.build_report(tmp_path)

    assert rendered.count("intento 1") == 1

    shutil.copytree(run, tmp_path / "backup")
    with pytest.raises(runs.BenchError, match="run_id duplicado"):
        report.build_report(tmp_path)


def test_rejects_catalog_traversal_and_submission_symlinks(
    fake_catalog: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(
        runs.catalog.CASES,
        "hostil",
        dict(CASE, files={"../escape.txt": "no\n"}),
    )
    with pytest.raises(runs.BenchError, match="ruta"):
        runs.prepare_run(
            case_id="hostil",
            model="modelo-a",
            harness="codex",
            config={},
            attempt=1,
            output=tmp_path / "hostil",
        )
    assert not (tmp_path / "escape.txt").exists()

    run = _prepare(tmp_path, name="symlink")
    (run / "candidate" / "solution.py").symlink_to(tmp_path / "outside.py")
    with pytest.raises(runs.BenchError, match="enlace simbolico"):
        runs.grade_run(run)


def test_cli_list_and_prepare_use_json_configuration(
    fake_catalog: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert runs.cli(["list"]) == 0
    assert "codigo" in capsys.readouterr().out
    output = tmp_path / "cli-run"
    assert (
        runs.cli(
            [
                "prepare",
                "--case",
                "codigo",
                "--model",
                "modelo-cli",
                "--harness",
                "terminal",
                "--config",
                '{"b": 2, "a": 1}',
                "--attempt",
                "2",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert _json(output / "run.json")["config"] == {"a": 1, "b": 2}


def test_real_module_cli_wires_catalog_prepare_and_grade(tmp_path: Path) -> None:
    command = [sys.executable, "-m", "tools.orbit_bench"]
    listed = subprocess.run(
        [*command, "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "codigo" in listed.stdout
    assert "mantenibilidad" not in listed.stdout

    run = tmp_path / "real-run"
    prepared = subprocess.run(
        [
            *command,
            "prepare",
            "--case",
            "codigo",
            "--model",
            "modelo-real",
            "--harness",
            "terminal",
            "--config",
            "{}",
            "--attempt",
            "1",
            "--output",
            str(run),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert prepared.returncode == 0, prepared.stderr
    cannot_prepare_extension = subprocess.run(
        [
            *command,
            "prepare",
            "--case",
            "mantenibilidad",
            "--model",
            "modelo-real",
            "--harness",
            "terminal",
            "--config",
            "{}",
            "--attempt",
            "1",
            "--output",
            str(tmp_path / "forbidden"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert cannot_prepare_extension.returncode == 2

    graded = subprocess.run(
        [*command, "grade", "--run", str(run)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert graded.returncode == 0, graded.stderr
    assert _json(run / "grade.json")["automatic_pass"] is False
