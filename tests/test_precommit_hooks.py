"""Candados sobre `.pre-commit-config.yaml`.

El hook de pre-push es la ultima red antes de que algo salga del repo. Si su
`entry` no se puede ejecutar, el push muere con un error que parece del hook y
no del codigo -- y la salida facil es `--no-verify`, que este repo prohibe.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / ".pre-commit-config.yaml"


def _hook(hook_id: str) -> dict:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for repo in cfg["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == hook_id:
                return hook
    raise AssertionError(f"hook {hook_id!r} ausente de {CONFIG.name}")


def test_pytest_corre_en_pre_push():
    """La suite se cobra en pre-push; degradarlo a manual seria perder la red."""
    assert _hook("pytest-pre-push")["stages"] == ["pre-push"]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="el entry apunta a .venv/Scripts/*.exe; el fallo de CreateProcess es de Windows",
)
def test_entry_de_pre_push_es_ejecutable(monkeypatch: pytest.MonkeyPatch):
    """El `entry` tiene que poder EJECUTARSE, no solo existir como archivo.

    Regresion: el entry era `.venv/Scripts/python.exe -m pytest -x -q`, ruta
    relativa con barras normales. El archivo existe --`Path.exists()` devuelve
    True-- pero CreateProcess la rechaza y el push se cae con
    `[WinError 2] The system cannot find the file specified`. Un test que solo
    comprobara la existencia del archivo habria pasado con el bug puesto; por
    eso aca se lanza el binario de verdad, y desde la raiz del repo, que es
    donde pre-commit se para para invocarlo.
    """
    exe = shlex.split(_hook("pytest-pre-push")["entry"])[0]
    assert (RAIZ / exe).exists(), f"{exe} ni siquiera existe como archivo"

    monkeypatch.chdir(RAIZ)
    try:
        proc = subprocess.run([exe, "-c", "pass"], capture_output=True, timeout=60)
    except OSError as e:
        pytest.fail(f"pre-commit no podra lanzar {exe!r}: {type(e).__name__}: {e}")
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
