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


def test_bateria_completa_corre_en_ci():
    """La bateria COMPLETA se cobra en CI, no en la maquina del lead.

    Decision del dueno 2026-08-29: el pre-push local quedo con el subconjunto
    de GUARDAS (arquitectura + esta config, ~1.5 s) porque la bateria entera
    costaba ~6 min POR PUSH en Windows y CI ya la corre en ~1.5-2 min. El
    candado no se debilita, se MUEVE — y este test lo pinea: si alguien saca
    el `pytest` de CI, el push falla aqui (que es el unico lugar donde la
    ausencia se puede detectar sin red).
    """
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    pasos = [
        paso
        for job in workflow["jobs"].values()
        for paso in job.get("steps", [])
        if "pytest" in str(paso.get("run", ""))
    ]
    assert pasos, "CI debe correr pytest: la bateria completa vive ahi (no en pre-push)"
    # Un `in` sobre el texto aceptaria un pytest ACOTADO (`pytest tests/x.py`) o
    # una mera mencion en un echo (hallazgo Greptile PR #50): hay que mirar la
    # invocacion REAL y exigir que no lleve rutas de test.
    # `pytest` tiene que ser el COMANDO EJECUTADO, no una palabra en la linea:
    # un `pip install ... pytest ...` (que el workflow ya tiene) colaba como si
    # fuera una corrida, y borrar el pytest real dejaba el candado verde
    # (hallazgo Greptile PR #50, 2a pasada).
    NO_EJECUTAN = {"echo", "printf", "pip", "pip3", "uv", "poetry", "apt", "apt-get", "npm", "#"}
    completas = []
    for paso in pasos:
        for linea in str(paso["run"]).splitlines():
            tokens = shlex.split(linea, posix=True) if linea.strip() else []
            # Prefijos de entorno tipo `PYTHONPATH=. pytest -q` no son el comando.
            resto = list(tokens)
            while resto and "=" in resto[0] and not resto[0].startswith("-"):
                resto.pop(0)
            if not resto or resto[0] in NO_EJECUTAN:
                continue
            comando = resto[0]
            es_pytest_directo = comando == "pytest" or comando.endswith("/pytest")
            es_modulo = comando.startswith("python") and resto[1:3] == ["-m", "pytest"]
            if not (es_pytest_directo or es_modulo):
                continue
            args = resto[1:] if es_pytest_directo else resto[3:]
            rutas = [
                a for a in args if not a.startswith("-") and (a.endswith(".py") or "tests" in a)
            ]
            if not rutas:
                completas.append(linea.strip())
    assert completas, (
        "CI debe correr la bateria COMPLETA (pytest SIN rutas de test); "
        f"invocaciones halladas: {[str(p['run']).strip() for p in pasos]!r}"
    )


def test_pre_push_es_rapido_y_declara_donde_vive_la_bateria():
    """El entry de pre-push acota a las guardas y el archivo declara POR QUE.

    Sin esta asercion, un `pytest -x -q` pelado vuelve a colarse en el hook
    (paso 5 veces) y cada push del lead vuelve a costar ~6 min.
    """
    entry = _hook("pytest-pre-push")["entry"]
    assert "tests/test_architecture.py" in entry and "tests/test_precommit_hooks.py" in entry, (
        f"el pre-push local corre SOLO las guardas; la bateria va en CI: {entry!r}"
    )
    texto = CONFIG.read_text(encoding="utf-8")
    assert "quality.yml" in texto and "--no-verify" in texto, (
        "la config debe declarar donde corre la bateria completa y que jamas se usa --no-verify"
    )
