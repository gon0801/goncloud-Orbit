"""Candados sobre `.pre-commit-config.yaml`.

El hook de pre-push es la ultima red antes de que algo salga del repo. Si su
`entry` no se puede ejecutar, el push muere con un error que parece del hook y
no del codigo -- y la salida facil es `--no-verify`, que este repo prohibe.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

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


def test_entry_de_pre_push_es_portable():
    """El YAML no conserva la ruta Python de la maquina que lo genero."""
    hook = _hook("pytest-pre-push")
    tokens = shlex.split(hook["entry"])
    assert tokens[:3] == ["python", "tools/quality_run_python_tests.py", "pytest"]
    assert hook["language"] == "python"
    runner = RAIZ / "tools" / "quality_run_python_tests.py"
    assert "QUALITY-KIT PYTHON RUNNER" in runner.read_text(encoding="utf-8")


def test_repo_hygiene_recibe_python_de_pre_commit():
    """El candado de contexto no depende del alias `python` del host."""
    assert _hook("context-docs-budget")["language"] == "python"


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


def test_dsn_ci_coincide_con_el_postgres_del_job():
    """La bateria con DB no puede usar una clave distinta a su servicio."""
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    for nombre in ("completa", "pesada"):
        job = workflow["jobs"][nombre]
        servicio = job["services"]["postgres"]["env"]
        esperado = (
            f"postgresql://{servicio['POSTGRES_USER']}:{servicio['POSTGRES_PASSWORD']}"
            f"@localhost:5432/{servicio['POSTGRES_DB']}"
        )
        pasos = [paso for paso in job["steps"] if "ORBIT_TEST_DSN" in paso.get("env", {})]
        assert pasos, f"{nombre}: falta ORBIT_TEST_DSN"
        for paso in pasos:
            assert paso["env"]["ORBIT_TEST_DSN"] == esperado, (
                f"{nombre}/{paso['name']}: DSN distinto del servicio postgres"
            )


def test_harness_ci_instala_dependencias_del_proyecto():
    """El harness pesado necesita psycopg y pytest antes de arrancar."""
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    pasos = workflow["jobs"]["pesada"]["steps"]
    harness = next(
        paso for paso in pasos if paso.get("name") == "Verificar dashboard con el harness real"
    )
    comandos = harness["run"].splitlines()
    assert "uv sync --frozen" in comandos, "el harness debe sincronizar las dependencias"
    assert comandos.index("uv sync --frozen") < next(
        i
        for i, comando in enumerate(comandos)
        if "orbit-verify maintain-verification-skill" in comando
    )


def test_drive_ci_migra_su_base_antes_de_probar_rutas():
    """El drive consulta tablas reales y necesita el esquema en su DB desechable."""
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    pasos = workflow["jobs"]["pesada"]["steps"]
    drive = next(paso for paso in pasos if paso.get("name") == "Drive de superficie (verify/)")
    script = drive["run"]
    assert "_aplicar_migraciones" in script, "el drive necesita migrar su Postgres"
    llamada = re.search(r"(?m)^[ \t]*aplicar\(conn\)[ \t]*$", script)
    assert llamada, "el drive debe ejecutar la migracion, no solo mencionarla"
    assert llamada.start() < script.index("-m pytest verify/")


def test_concurrencia_de_ci_distingue_evento_y_gate_sigue_fail_closed():
    """El grupo de concurrencia separa schedule de push y el gate sigue cerrado.

    Con `quality-<PR||ref>` el schedule nocturno y el push a master comparten
    grupo (`quality-refs/heads/master`) y se cancelan entre si: en el run
    37195080800 (squash de B5) la corrida schedule dejo `completa=cancelled`
    y `gate=failure`. Al anteponer `github.event_name` al grupo, dos pushes
    de la misma rama o PR siguen cancelandose entre si, pero schedule y push
    ya no comparten grupo. El gate se pinea fail-closed para que una
    cancelacion jamas deje un verde falso.
    """
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))

    concurrencia = workflow["concurrency"]
    grupo = str(concurrencia["group"])
    assert "github.event_name" in grupo, (
        "el grupo debe distinguir github.event_name: si no, el schedule nocturno "
        f"comparte grupo con el push a master y cancela su bateria completa ({grupo!r})"
    )
    assert concurrencia["cancel-in-progress"] is True, (
        "la cancelacion entre pushes de la misma rama o PR se mantiene"
    )

    gate = workflow["jobs"]["gate"]
    assert gate["if"] == "always()", "el gate corre siempre para dar veredicto unico"
    veredicto = "\n".join(str(paso.get("run", "")) for paso in gate["steps"])
    caso = re.search(r'case "\$par" in ([^)]*)\)', veredicto)
    assert caso, "el gate decide con el case sobre el resultado de cada job"
    assert set(caso.group(1).split("|")) == {"*=success", "*=skipped"}, (
        "el gate es fail-closed: solo success o skipped pasan; cancelled o "
        "failure dejan rc=1 y exit 1"
    )
    assert "exit 1" in veredicto, "un job aplicable no verde mata el gate con exit 1"


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
