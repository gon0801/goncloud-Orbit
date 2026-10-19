"""Candados sobre `.pre-commit-config.yaml`.

El hook de pre-push es la ultima red antes de que algo salga del repo. Si su
`entry` no se puede ejecutar, el push muere con un error que parece del hook y
no del codigo -- y la salida facil es `--no-verify`, que este repo prohibe.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import textwrap
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


def test_push_creds_hook_existe_y_es_pre_push():
    """Sin credencial el push se cuelga (incidente 2026-10-16): el fail-fast
    va primero en pre-push para frenar antes de pagar la bateria."""
    hook = _hook("push-creds-failfast")
    assert hook["stages"] == ["pre-push"]
    assert hook["language"] == "python"
    assert "check_push_creds" in hook["entry"]


def test_check_push_creds_sin_git_falla_rapido(monkeypatch):
    """Sin binario de git no hay credential helper: 1 sin colgarse."""
    import subprocess as sp

    from tools.check_push_creds import main

    def roto(*a, **k):
        raise FileNotFoundError("sin git")

    monkeypatch.setattr(sp, "run", roto)
    assert main() == 1


def test_check_push_creds_timeout_falla(monkeypatch):
    """Un credential helper colgado no detiene el push eternamente."""
    import subprocess as sp

    from tools.check_push_creds import main

    def colgado(*a, **k):
        raise sp.TimeoutExpired(cmd="git", timeout=15)

    monkeypatch.setattr(sp, "run", colgado)
    assert main() == 1


def test_check_push_creds_con_credencial_pasa(monkeypatch):
    """Usuario+password del fill = via libre."""
    import subprocess as sp

    from tools.check_push_creds import main

    def con_cred(*a, **k):
        return sp.CompletedProcess(
            args="git", returncode=0, stdout="username=x\npassword=y\n", stderr=""
        )

    monkeypatch.setattr(sp, "run", con_cred)
    assert main() == 0


def test_completa_corre_en_pr_ready_y_no_en_draft():
    """`completa` entra al PR solo cuando deja de ser draft (una vez por bloque).

    Flujo del dueno (orden 2026-10-18): PR en draft durante la implementacion
    y la revision (solo corre `rapido`); tras el APROBADO el PR pasa a ready
    y `completa` corre UNA vez sobre el SHA final. Sin estos tipos el job
    ignoraba los PR y cada plan lanzaba la bateria a mano con
    `gh workflow run` (3+ corridas de ~15 min por bloque).
    """
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    disparador = workflow.get(True, workflow.get("on"))  # PyYAML parsea `on` como True
    assert isinstance(disparador, dict), "el workflow debe declarar sus disparadores"
    tipos = set(disparador["pull_request"]["types"])
    assert tipos == {"opened", "synchronize", "reopened", "ready_for_review"}, (
        "pull_request debe dispararse tambien en ready_for_review: es el paso "
        f"donde `completa` corre una sola vez (tipos hallados: {sorted(tipos)})"
    )
    condicion = str(workflow["jobs"]["completa"]["if"])
    assert "github.event_name == 'pull_request'" in condicion, (
        f"completa debe atender pull_request (condicion: {condicion!r})"
    )
    assert "!github.event.pull_request.draft" in condicion, (
        f"completa debe exigir PR NO draft; en draft solo corre `rapido` (condicion: {condicion!r})"
    )


def test_gate_exige_completa_success_en_pr_no_draft():
    """El gate cierra el ciclo: en PR no draft, `completa=skipped` ya no vale.

    Antes el gate aceptaba `skipped` de `completa` en cualquier PR y uno
    listo podia unirse sin bateria. Ahora: draft -> `completa` skipped sigue
    siendo valido (no aplica); ready -> la bateria corrio y paso sobre el
    SHA final o el gate falla. En push y dispatch el veredicto no cambia.
    """
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    gate = workflow["jobs"]["gate"]
    assert gate["if"] == "always()", "el gate corre siempre para dar veredicto unico"
    paso = gate["steps"][0]
    env = paso.get("env", {})
    assert "github.event_name" in env.get("EVENTO", ""), "el gate necesita saber el evento"
    assert "pull_request.draft" in env.get("DRAFT", ""), "el gate necesita saber si el PR es draft"
    script = str(paso["run"])
    inicio = script.find('if [ "$EVENTO" = "pull_request" ]')
    assert inicio != -1, "el gate debe tener la exigencia extra de `completa` solo para PRs"
    fin = script.find("\nfi", inicio)
    assert fin != -1, "la exigencia extra debe ser un if acotado"
    bloque = script[inicio:fin]
    assert '"$DRAFT" != "true"' in bloque, "la exigencia aplica a PR que NO es draft"
    assert '"$R_COMPLETA" != "success"' in bloque, "el gate exige completa=success en PR no draft"
    assert "exit 1" in bloque, "sin completa=success el gate falla con exit 1"


def test_reuso_por_arbol_solo_en_push_y_falla_cerrado_a_correr():
    """Push a master no repite la bateria si el MISMO arbol ya paso en el PR.

    El reuso compara `git rev-parse HEAD^{tree}` contra el arbol de la
    cabeza del PR asociado (API commits/{sha}/pulls -> head.sha): sirve para
    merge commit y para squash, porque ambos producen un arbol identico al
    de la cabeza. Fail-closed hacia CORRER, nunca hacia saltar: arbol
    distinto (la base avanzo y no se mezclo), sin PR o consulta fallida =>
    se corre la bateria completa.
    """
    workflow = yaml.safe_load((RAIZ / ".github" / "workflows" / "quality.yml").read_text("utf-8"))
    completa = workflow["jobs"]["completa"]
    reuso = next((p for p in completa["steps"] if p.get("id") == "reuso"), None)
    assert reuso is not None, "completa debe tener el paso de reuso por arbol (id: reuso)"
    assert reuso["if"] == "github.event_name == 'push'", (
        f"el reuso SOLO aplica en push a main/master (if: {reuso.get('if')!r})"
    )
    assert "github.token" in str(reuso.get("env", {}).get("GH_TOKEN", "")), (
        "el reuso usa el token del propio workflow, sin tokens nuevos"
    )
    pytest_paso = next(p for p in completa["steps"] if "pytest -q" in str(p.get("run", "")))
    assert "steps.reuso.outputs.reusar != 'true'" in str(pytest_paso.get("if", "")), (
        "el pytest de la bateria debe quedar detras del veredicto del reuso"
    )
    script_path = RAIZ / ".github" / "scripts" / "reusar_completa.sh"
    texto = script_path.read_text(encoding="utf-8")
    assert "reusar=false" in texto, (
        "el default del reuso es NO reusar: sin veredicto confirmado se corre la bateria"
    )
    assert "se corre la bateria completa" in texto, (
        "el texto del fallback (correr, no saltar) debe estar fijado en el script"
    )


def _env_sin_git() -> dict[str, str]:
    """Entorno del proceso SIN variables GIT_*.

    El hook pre-push corre pytest con GIT_DIR, GIT_INDEX_FILE, etc. puestos
    por git; heredadas en un subprocess, `git init/add/commit` pegarian
    contra el repo apuntado por el entorno y no contra el repo temporal
    (incidente 2026-10-18: goncloud-Orbit reinicializado, core.bare=true).
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _escenario_reuso(raiz: Path) -> str:
    """Monta el triple del reuso en `raiz` (repo tmp + doble de gh sin red).

    Devuelve el sha del commit del repo temporal. Los subprocess de git
    corren con un entorno SIN variables GIT_*: dentro del hook pre-push git
    exporta GIT_DIR/GIT_INDEX_FILE/etc. y, heredadas, `git init/add/commit`
    del repo temporal pegan contra el repo REAL (incidente 2026-10-18: el
    `git init` reinicializo goncloud-Orbit/.git y le puso core.bare=true).
    """
    raiz.mkdir(parents=True, exist_ok=True)
    doble = raiz / "doble"
    doble.mkdir()
    (doble / "gh").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys

            args = sys.argv[1:]
            assert args[0] == "api", args
            url = args[1]
            escena = json.loads(os.environ["ESCENA"])
            if url.endswith("/pulls"):
                clave = "pulls"
            elif url.endswith("/check-runs"):
                clave = "check-runs"
            else:
                clave = "arbol"
            accion = escena[clave]
            if accion == "FALLO":
                sys.exit(1)
            sys.stdout.write(accion)
            """
        )
    )
    (doble / "gh").chmod(0o755)

    def git(*args: str) -> str:
        res = subprocess.run(
            ["git", "-C", str(raiz), *args], check=True, capture_output=True, env=_env_sin_git()
        )
        return res.stdout.decode().strip()

    subprocess.run(["git", "init", "-q", "-b", "master", str(raiz)], check=True, env=_env_sin_git())
    (raiz / "archivo.txt").write_text("contenido\n")
    git("add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "uno")
    sha = git("rev-parse", "HEAD")
    arbol = git("rev-parse", "HEAD^{tree}")
    cabeza_json = json.dumps({"commit": {"tree": {"sha": arbol}}})
    check_success = json.dumps(
        {
            "check_runs": [
                {"name": "completa", "conclusion": "success", "html_url": "https://ci/run/1"}
            ]
        }
    )

    def correr(escena: dict) -> tuple[subprocess.CompletedProcess, str]:
        salida = raiz / f"salida-{len(list(raiz.glob('salida-*')))}.txt"
        env = _env_sin_git() | {
            "PATH": f"{doble}:{os.environ['PATH']}",
            "ESCENA": json.dumps(escena),
            "GITHUB_OUTPUT": str(salida),
            "GH_REPO": "dueno/orbit",
        }
        proc = subprocess.run(
            ["bash", str(RAIZ / ".github" / "scripts" / "reusar_completa.sh")],
            capture_output=True,
            cwd=str(raiz),
            env=env,
            text=True,
            timeout=60,
        )
        return proc, salida.read_text()

    # 1) Arbol identico + `completa` en success en la cabeza -> REUSA.
    proc, salida = correr(
        {
            "pulls": json.dumps([{"number": 7, "head": {"sha": sha}}]),
            "arbol": cabeza_json,
            "check-runs": check_success,
        }
    )
    assert proc.returncode == 0, proc.stderr
    assert "reusar=true" in salida, f"mismo arbol ya validado debe reusarse: {proc.stderr}"
    assert sha in proc.stdout, "debe imprimir la cabeza reusada"

    # 2) Arbol distinto (la base avanzo) -> CORRE la bateria completa.
    proc, salida = correr(
        {
            "pulls": json.dumps([{"number": 7, "head": {"sha": sha}}]),
            "arbol": json.dumps({"commit": {"tree": {"sha": "0" * 40}}}),
            "check-runs": check_success,
        }
    )
    assert proc.returncode == 0, proc.stderr
    assert "reusar=false" in salida, f"arbol distinto nunca se reusa: {proc.stderr}"

    # 3) gh falla (consulta rota) -> CORRE: fail-closed hacia correr.
    proc, salida = correr({"pulls": "FALLO", "arbol": "FALLO", "check-runs": "FALLO"})
    assert proc.returncode == 0, proc.stderr
    assert "reusar=false" in salida, f"consulta fallida cae a correr, jamas a saltar: {proc.stderr}"

    # 4) Sin PR asociado (push directo a master) -> CORRE.
    proc, salida = correr({"pulls": "[]", "arbol": cabeza_json, "check-runs": check_success})
    assert proc.returncode == 0, proc.stderr
    assert "reusar=false" in salida, f"sin PR no hay veredicto que reusar: {proc.stderr}"
    return sha


def test_script_reuso_con_doble_de_gh(tmp_path):
    """Triple del reuso con un doble de `gh` (sin red): reusa o cae a correr.

    - arbol identico + `completa` en success en la cabeza del PR -> reusar=true
    - arbol distinto (la base avanzo y no se mezclo) -> reusar=false (corre)
    - gh falla (red, rate limit, sin gh) -> reusar=false (corre)
    El script jamas termina en error: su unica salida es reusar=true (salta
    la bateria) o reusar=false (la corre completa).
    """
    _escenario_reuso(tmp_path)


def test_reuso_con_git_env_del_hook_deja_intacto_el_repo_ajeno(tmp_path, monkeypatch):
    """Regresion del incidente 2026-10-18: GIT_* del hook no pegan al repo real.

    El hook pre-push corre pytest con GIT_DIR, GIT_INDEX_FILE, etc. en el
    entorno. Si los subprocess de git del repo temporal heredan esas
    variables, `git init/add/commit` pegarian contra el repo apuntado
    (en el incidente: goncloud-Orbit reinicializado y core.bare=true).
    Centinela: un repo temporal distinto recibe el GIT_DIR/GIT_INDEX_FILE
    hostil; tras correr TODO el escenario del reuso, el centinela debe
    quedar exactamente igual (core.bare, HEAD, indice). Si el filtro de
    GIT_* falta, la mutacion del centinela vuelve la prueba roja.
    """
    centinela = tmp_path / "centinela"
    _escenario_reuso(centinela)  # repo centinela real, con commit

    def leer(*args: str) -> str:
        res = subprocess.run(
            ["git", "-C", str(centinela), *args],
            check=True,
            capture_output=True,
            env=_env_sin_git(),
        )
        return res.stdout.decode().strip()

    bare_antes = leer("config", "core.bare")
    head_antes = leer("rev-parse", "HEAD")
    indice_antes = leer("ls-files", "-s")

    # Entorno hostil del hook, apuntando al CENTINELA (jamás al repo real):
    # la prueba debe ignorar estas variables por completo.
    monkeypatch.setenv("GIT_DIR", str(centinela / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(centinela / ".git" / "index"))
    _escenario_reuso(tmp_path / "escenario")

    assert leer("config", "core.bare") == bare_antes, (
        "el repo centinela cambio core.bare: el GIT_* del hook se colo en los subprocess"
    )
    assert leer("rev-parse", "HEAD") == head_antes, (
        "el repo centinela cambio de HEAD: el GIT_* del hook se colo en los subprocess"
    )
    assert leer("ls-files", "-s") == indice_antes, (
        "el indice del centinela cambio: el GIT_INDEX_FILE del hook se colo en los subprocess"
    )
