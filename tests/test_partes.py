"""La bateria repartida en partes no pierde ni repite pruebas."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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
            "tests/test_arq_bids_p.py",
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
