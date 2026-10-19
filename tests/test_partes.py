"""La bateria repartida en partes no pierde ni repite pruebas."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml
from conftest import parte_de

RAIZ = Path(__file__).resolve().parents[1]


def test_cada_prueba_cae_en_una_sola_parte():
    ids = [f"tests/test_x.py::test_{k}" for k in range(500)]
    partes = [parte_de(i, 6) for i in ids]
    assert set(partes) == {1, 2, 3, 4, 5, 6}
    assert all(parte_de(i, 6) == p for i, p in zip(ids, partes, strict=True))


def _recolecta(parte: str | None) -> set[str]:
    env = dict(os.environ, PYTHONPATH=".")
    env.pop("ORBIT_PARTE", None)
    if parte:
        env["ORBIT_PARTE"] = parte
    salida = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--collect-only",
            "-p",
            "no:cacheprovider",
            "tests/test_partes.py",
            "tests/test_precommit_hooks.py",
        ],
        cwd=RAIZ,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {linea for linea in salida.splitlines() if "::" in linea}


def test_la_union_de_las_partes_es_la_bateria():
    todas = _recolecta(None)
    partes = [_recolecta(f"{i}/3") for i in (1, 2, 3)]
    assert set().union(*partes) == todas
    assert sum(len(p) for p in partes) == len(todas)


def test_ci_corre_todas_las_partes():
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    job = workflow["jobs"]["completa"]
    total = job["strategy"]["matrix"]["total"]
    assert job["strategy"]["matrix"]["parte"] == list(range(1, total[0] + 1))
    assert job["strategy"]["fail-fast"] is False
    pasos = [p for p in job["steps"] if "pytest" in str(p.get("run", ""))]
    assert any(
        p.get("env", {}).get("ORBIT_PARTE") == "${{ matrix.parte }}/${{ matrix.total }}"
        for p in pasos
    )


def test_precommit_va_solo_en_la_parte_1():
    """Las partes 2 a 6 solo corren pytest: pre-commit vive en la parte 1 (#481).

    Si el hook de candados rapidos se colara en todas las partes, el ahorro
    de la matriz se lo comeria pre-commit repetido 6 veces.
    """
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    job = workflow["jobs"]["completa"]
    con_precommit = [p for p in job["steps"] if str(p.get("uses", "")).startswith("pre-commit/")]
    assert len(con_precommit) == 1, "solo la parte 1 debe correr pre-commit"
    condicion = str(con_precommit[0].get("if", ""))
    assert "matrix.parte == 1" in condicion, (
        f"pre-commit debe quedar acotado a la parte 1 (condicion: {condicion!r})"
    )


def test_un_pr_de_solo_docs_no_corre_la_bateria():
    """El job `cambios` (#481) decide: docs-only => `completa` no entra.

    Un PR que solo toca docs/, plans/ y *.md no paga ~6 jobs de bateria:
    `cambios` mira el diff contra la base y `completa` le pregunta.
    """
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    cambios = workflow["jobs"]["cambios"]
    assert cambios["if"] == "github.event_name == 'pull_request'"
    assert cambios["outputs"]["codigo"] == "${{ steps.mira.outputs.codigo }}"
    mira = next(p for p in cambios["steps"] if p.get("id") == "mira")
    assert "docs/" in str(mira["run"]), "el diff debe ignorar docs/ al decidir si toca codigo"
    condicion = str(workflow["jobs"]["completa"]["if"])
    assert "needs.cambios.outputs.codigo == 'true'" in condicion, (
        f"completa debe preguntarle a `cambios` (condicion: {condicion!r})"
    )
