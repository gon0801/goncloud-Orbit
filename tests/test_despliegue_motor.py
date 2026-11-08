"""B.5 de AUTO-03: instalar y deshacer en maquinas desechables con los scripts reales.

Corre `tools/despliegue/{desplegar,checklist,rollback}.sh` y `tools/auto03/sonda.sh` con
ORBIT_SIMULACION=1 contra una base desechable con la cadena 0001..0075 (sin la 0011 ni las
reversas, como tests/test_schema_docs.py) y un servidor que es un directorio temporal. `ssh`
falso corre el comando remoto en local; `docker` falso registra sus argumentos y emula el CLI
`productos-modo` escribiendo `config_version`; `curl` falso contesta /health. Los cortes son
`kill -9` de la corrida en el paso nombrado por ORBIT_SIM_CORTE, despues de cambiar estado.

Los escenarios (a)-(n) son los de docs/evidencia/auto-03/plan/modelo-despliegue/modelo.py con
el mismo repo E -> Gm -> Fm -> F2 (Fm enciende la meta y trae las migraciones de la receta
(*_auto03_* de AUTO-03 y *_orbit07_* de ORBIT 07): la de prueba y las reales del repo, asi
cada bloque nuevo queda cubierto). (o)-(v) son de esta prueba.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
HERRAMIENTAS = ("bash", "git", "psql", "pg_dump", "flock", "md5sum", "tar")


def _falta_herramienta() -> str | None:
    falta = next((h for h in HERRAMIENTAS if shutil.which(h) is None), None)
    if falta and "CI" in os.environ:
        raise RuntimeError(f"CI definida pero sin {falta}: B.5 desapareceria en silencio")
    return falta


pytestmark = pytest.mark.skipif(
    _postgres_obligatorio_ausente() or _falta_herramienta() is not None,
    reason="sin Postgres utilizable o sin bash/git/psql/pg_dump/flock en el PATH",
)

LIVE = {"ads_producto_meta_amazon_mx": "live", "ads_producto_meta_amazon_us": "live"}
SHADOW = {"ads_producto_meta_amazon_mx": "shadow", "ads_producto_meta_amazon_us": "shadow"}
CORTADO = -9

MIGRACION_PRUEBA = """-- sonda-aplicada: SELECT to_regclass('public.auto03_prueba') IS NOT NULL
CREATE TABLE public.auto03_prueba (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nota TEXT NOT NULL CHECK (nota <> '')
);
ALTER TABLE public.config_version ADD COLUMN auto03_prueba_col TEXT;
CREATE TRIGGER auto03_prueba_inmutable BEFORE UPDATE OR DELETE ON public.auto03_prueba
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
GRANT SELECT ON public.auto03_prueba TO app_read;
"""
REVERSA_PRUEBA = """DROP TABLE public.auto03_prueba;
ALTER TABLE public.config_version DROP COLUMN auto03_prueba_col;
"""

SSH = """#!/usr/bin/env bash
[ -f "$ORBIT_FAKE_CTL/ssh_falla" ] && { echo "ssh falso: falla" >&2; exit 255; }
while [ $# -gt 0 ]; do case "$1" in -n | -T) shift ;; -o) shift 2 ;; *) break ;; esac; done
shift
case "$*" in
  *"echo CANDADO"*)
    if [ -f "$ORBIT_FAKE_CTL/candado_setsid" ]; then
      echo $$ > "$ORBIT_FAKE_CTL/candado.pid"
      exec setsid bash -c "$*"
    fi ;;
esac
exec bash -c "$*"
"""

DOCKER = """#!/usr/bin/env bash
C=$ORBIT_FAKE_CTL
echo "docker $*" >> "$C/docker.log"
case "$1" in
  top)
    if [ -f "$C/top" ]; then cat "$C/top"
    else printf 'UID PID CMD\\n10001 1 uvicorn app.main:app\\n'; fi ;;
  compose)
    dir=""
    while [ $# -gt 0 ]; do [ "$1" = --project-directory ] && dir=$2; shift; done
    (cd "$dir" && find app -type f | LC_ALL=C sort | xargs md5sum) > "$C/compose-md5.txt"
    touch "$C/arranque" ;;
  exec)
    case "$*" in
      *productos-modo*)
        if [ -f "$C/barrera" ]; then
          touch "$C/barrera.llego"
          while [ -f "$C/barrera" ]; do sleep 0.2; done
        fi
        set -- $*
        while [ $# -gt 0 ]; do
          case "$1" in
            --platform) plat=$2 ;;
            --meta | --pausa) tipo=${1#--}; val=$2 ;;
          esac
          shift
        done
        k="ads_producto_${tipo}_${plat}"
        expr="s || jsonb_build_object('$k', '$val')"
        [ "$val" = off ] && expr="s - '$k'"
        psql "$ORBIT_SIM_DSN" -X -q -v ON_ERROR_STOP=1 -c "
          INSERT INTO config_version (label, settings)
          SELECT 'productos-modo falso', $expr
            FROM (SELECT coalesce((SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
                                  '{}'::jsonb) AS s) v
           WHERE ($expr) IS DISTINCT FROM s" ;;
      *auto03_verifica*) ;;
      *) echo "docker falso: exec inesperado: $*" >&2; exit 99 ;;
    esac ;;
  *) echo "docker falso: inesperado: $*" >&2; exit 99 ;;
esac
"""

CURL = """#!/usr/bin/env bash
C=$ORBIT_FAKE_CTL
etiqueta=$(cat "$ORBIT_SIM_SRV/app/etiqueta.txt")
if [ -f "$C/inestable" ] && [ "$(cat "$C/inestable")" = "$etiqueta" ]; then
  if [ -f "$C/arranque" ]; then rm -f "$C/arranque"; printf 200; else printf 500; fi
else
  printf 200
fi
"""

PSQL = """#!/usr/bin/env bash
case " $* " in *" -c "*) exec "$ORBIT_REAL_PSQL" "$@" ;; esac
sql=$(cat)
if [ -f "$ORBIT_FAKE_CTL/psql_falla_vigente" ]; then
  case "$sql" in *"settings ->>"*) echo "psql falso: falla la lectura vigente" >&2; exit 2 ;; esac
fi
printf '%s\\n' "$sql" | "$ORBIT_REAL_PSQL" "$@"
"""


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "b5",
        "GIT_AUTHOR_EMAIL": "b5@x",
        "GIT_COMMITTER_NAME": "b5",
        "GIT_COMMITTER_EMAIL": "b5@x",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _commit(repo: Path, etiqueta: str, archivos: dict[str, str]) -> None:
    for ruta, texto in archivos.items():
        (repo / ruta).parent.mkdir(parents=True, exist_ok=True)
        (repo / ruta).write_text(texto, encoding="utf-8", newline="\n")
    (repo / "app/etiqueta.txt").write_text(etiqueta + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", etiqueta)
    _git(repo, "tag", etiqueta)


def _interruptores(valores: dict[str, str]) -> str:
    return "".join(f"{k}={v}\n" for k, v in sorted(valores.items()))


def _crea_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "core.autocrlf", "false")
    base = [
        "Dockerfile",
        ".dockerignore",
        "pyproject.toml",
        "uv.lock",
        "tools/auto03/sonda.sh",
        "tools/auto03/sondas/base.sql",
        *(
            f"tools/despliegue/{n}"
            for n in ("comun.sh", "desplegar.sh", "checklist.sh", "rollback.sh")
        ),
    ]
    base += [
        ln.split()[1]
        for ln in (ROOT / "Dockerfile").read_text().splitlines()
        if ln.startswith("COPY tools/")
    ]
    for ruta in base:
        (repo / ruta).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / ruta, repo / ruta)
    (repo / "app/__init__.py").parent.mkdir(parents=True)
    (repo / "app/__init__.py").write_text("")
    inter = "tools/despliegue/interruptores.env"
    _commit(repo, "E", {inter: _interruptores(SHADOW)})
    _commit(repo, "Gm", {})
    reales = {
        f"migrations/{p.name}": p.read_text(encoding="utf-8")
        for patron in ("*_auto03_*.sql", "*_orbit07_*.sql")
        for p in (ROOT / "migrations").glob(patron)
    }
    _commit(
        repo,
        "Fm",
        {
            inter: _interruptores(LIVE),
            **reales,
            "migrations/9001_auto03_prueba.sql": MIGRACION_PRUEBA,
            "migrations/9001_reversa_auto03_prueba.sql": REVERSA_PRUEBA,
        },
    )
    _commit(repo, "F2", {})
    _commit(repo, "F3", {inter: ""})
    ramas = {
        "SinCabecera": {
            "migrations/9002_auto03_sin.sql": "SELECT 1;\n",
            "migrations/9002_reversa_auto03_sin.sql": "SELECT 1;\n",
        },
        "SondaVacia": {
            "migrations/9003_auto03_vacia.sql": "-- sonda-aplicada: SELECT true WHERE false\n",
            "migrations/9003_reversa_auto03_vacia.sql": "SELECT 1;\n",
        },
        "Revienta": {
            "migrations/9004_auto03_revienta.sql": "-- sonda-aplicada: "
            "SELECT to_regclass('public.auto03_revienta') IS NOT NULL\n"
            "CREATE TABLE public.auto03_revienta (id INT PRIMARY KEY);\n"
            "INSERT INTO public.auto03_revienta VALUES (1), (1);\n",
            "migrations/9004_reversa_auto03_revienta.sql": "DROP TABLE public.auto03_revienta;\n",
        },
        "PausaLive": {inter: "ads_producto_pausa_amazon_mx=live\n"},
        "ConTransaccion": {
            "migrations/9005_auto03_tx.sql": "-- sonda-aplicada: "
            "SELECT to_regclass('public.auto03_tx') IS NOT NULL\n"
            "BEGIN;\nCREATE TABLE public.auto03_tx (id INT PRIMARY KEY);\nCOMMIT;\n"
            "INSERT INTO public.auto03_tx VALUES (1), (1);\n",
            "migrations/9005_reversa_auto03_tx.sql": "DROP TABLE public.auto03_tx;\n",
        },
    }
    for nombre, archivos in ramas.items():
        _git(repo, "checkout", "-q", "-b", nombre.lower(), "E")
        _commit(repo, nombre, archivos)
    _git(repo, "checkout", "-q", "master")


@pytest.fixture(scope="module")
def banco():
    raiz = Path(tempfile.mkdtemp(prefix="auto03-b5-"))
    dsn = _test_dsn()
    plantilla = f"auto03_b5_{socket.gethostname().lower().replace('-', '_')}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        _crea_repo(raiz / "repo")
        bin_ = raiz / "bin"
        bin_.mkdir()
        for nombre, texto in {"ssh": SSH, "docker": DOCKER, "curl": CURL, "psql": PSQL}.items():
            (bin_ / nombre).write_text(texto, encoding="utf-8", newline="\n")
            (bin_ / nombre).chmod(0o755)
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(plantilla)))
        with psycopg.connect(dsn, dbname=plantilla, autocommit=True) as conn:
            conn.execute("SET TIME ZONE 'UTC'")
            for ruta in sorted((ROOT / "migrations").glob("*.sql")):
                if any(x in ruta.name for x in ("0011_", "_reversa_", "_auto03_", "_orbit07_")):
                    continue
                conn.execute(ruta.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO config_version (label, settings) VALUES ('b5', %s)",
                (json.dumps(SHADOW),),
            )
        yield {
            "raiz": raiz,
            "admin": admin,
            "plantilla": plantilla,
            "dsn": dsn,
            "real_psql": shutil.which("psql"),
        }
    finally:
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(plantilla))
        )
        admin.close()
        shutil.rmtree(raiz, ignore_errors=True)


class Servidor:
    """Un servidor desechable (directorio + base) con el codigo de E desplegado."""

    def __init__(self, banco, nombre: str):
        self.b = banco
        self.repo = banco["raiz"] / "repo"
        self.srv = banco["raiz"] / nombre / "srv"
        self.ctl = banco["raiz"] / nombre / "ctl"
        self.ctl.mkdir(parents=True)
        (self.srv / "despliegue").mkdir(parents=True)
        self.db = f"{banco['plantilla']}_{nombre}"
        banco["admin"].execute(
            pgsql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                pgsql.Identifier(self.db), pgsql.Identifier(banco["plantilla"])
            )
        )
        self.dsn = make_conninfo(banco["dsn"], dbname=self.db)
        archivo = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "archive",
                "E",
                "app",
                "Dockerfile",
                ".dockerignore",
                "pyproject.toml",
                "uv.lock",
                "tools",
            ],
            check=True,
            capture_output=True,
        ).stdout
        subprocess.run(["tar", "-xf", "-", "-C", str(self.srv)], input=archivo, check=True)
        (self.srv / "tools/solo_servidor.sh").write_text("echo servidor\n")
        (self.srv / "docker-compose.yml").write_text("services: {}\n")
        (self.srv / "despliegue/sha").write_text(self.rev("E") + "\n")
        self.iniciados: set[str] = set()

    def cierra(self):
        self.b["admin"].execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(self.db))
        )

    def rev(self, tag: str) -> str:
        return _git(self.repo, "rev-parse", tag)

    def entorno(self, **extra: str) -> dict:
        env = {
            **os.environ,
            "PATH": f"{self.b['raiz'] / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "ORBIT_SIMULACION": "1",
            "ORBIT_SIM_SRV": str(self.srv),
            "ORBIT_SIM_DSN": self.dsn,
            "ORBIT_FAKE_CTL": str(self.ctl),
            "ORBIT_REAL_PSQL": self.b["real_psql"],
            "AUTO03_ESPERA_S": "20",
        }
        env.pop("ORBIT_SIM_CORTE", None)
        env.update(extra)
        return env

    def corre(self, script: str, tag: str, *args: str, **extra: str) -> subprocess.CompletedProcess:
        r = subprocess.run(
            ["bash", str(self.repo / script), *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            timeout=300,
            env=self.entorno(MOTOR_SHA=self.rev(tag), **extra),
        )
        print(f"### {script} {tag} {args} {extra} -> {r.returncode}\n{r.stdout}{r.stderr}")
        return r

    def desplegar(self, tag, *args, **extra):
        return self.corre("tools/despliegue/desplegar.sh", tag, *args, **extra)

    def checklist(self, tag, **extra):
        return self.corre("tools/despliegue/checklist.sh", tag, **extra)

    def rollback(self, tag, **extra):
        return self.corre("tools/despliegue/rollback.sh", tag, **extra)

    def motor(self, tag, *, corte=None, corte_rb=None, muere_antes_rb=False, **extra) -> str:
        """El ciclo del motor (motor/effects.py, Deploy): desplegar y checklist; con 75 no corre
        rollback ("reintenta luego"); con otra falla, rollback."""
        sha = self.rev(tag)
        if sha in self.iniciados and self.checklist(tag).returncode == 0:
            return "ya_estaba"
        self.iniciados.add(sha)
        d = self.desplegar(tag, **({"ORBIT_SIM_CORTE": corte} if corte else {}), **extra)
        if d.returncode == CORTADO:
            return "cortado"
        if d.returncode == 75:
            return "reintenta"
        if d.returncode == 0 and self.checklist(tag).returncode == 0:
            return "ok"
        if muere_antes_rb:
            return "cortado"
        r = self.rollback(tag, **({"ORBIT_SIM_CORTE": corte_rb} if corte_rb else {}))
        if r.returncode == CORTADO:
            return "cortado"
        return f"fallo_{d.returncode}"

    def sql(self, consulta: str, *params):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            cur = conn.execute(consulta, params or None)
            return cur.fetchall() if cur.description else []

    def config(self) -> dict:
        return self.sql("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1")[0][0]

    def pon_config(self, settings: dict) -> None:
        self.sql(
            "INSERT INTO config_version (label, settings) VALUES ('b5 a mano', %s)",
            json.dumps(settings),
        )

    def sha_srv(self) -> str:
        p = self.srv / "despliegue/sha"
        return p.read_text().strip() if p.exists() else ""

    def ultimo(self) -> list[str]:
        p = self.srv / "despliegue/ultimo.env"
        return p.read_text().splitlines() if p.exists() else []

    def estado_diario(self) -> str:
        return [ln for ln in self.ultimo() if ln.startswith("ESTADO=")][-1].split("=", 1)[1]

    def estado(self):
        return (self.sha_srv(), (self.srv / "app/etiqueta.txt").read_text().strip(), self.config())

    def huella_esquema(self):
        return self.sql("""
            SELECT 'col', table_name || '.' || column_name || ':' || data_type
              FROM information_schema.columns WHERE table_schema = 'public'
            UNION ALL SELECT 'con', conrelid::regclass::text || '.' || conname || ':'
                   || pg_get_constraintdef(oid)
              FROM pg_constraint WHERE connamespace = 'public'::regnamespace
            UNION ALL SELECT 'trg', tgrelid::regclass::text || '.' || tgname
              FROM pg_trigger WHERE NOT tgisinternal
            UNION ALL SELECT 'acl', table_name || ':' || grantee || ':' || privilege_type
              FROM information_schema.role_table_grants WHERE table_schema = 'public'
            ORDER BY 1, 2""")

    def huella_srv(self) -> str:
        h = hashlib.md5()
        for p in sorted(self.srv.rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(self.srv)).encode() + b"\0" + p.read_bytes())
        return h.hexdigest()

    def md5_git(self, tag: str, ruta: str) -> str:
        blob = subprocess.run(
            ["git", "-C", str(self.repo), "show", f"{tag}:{ruta}"], check=True, capture_output=True
        ).stdout
        return hashlib.md5(blob).hexdigest()


_NUMERO = itertools.count()


@pytest.fixture
def s(banco):
    servidor = Servidor(banco, f"s{next(_NUMERO)}")
    yield servidor
    servidor.cierra()


def _inicial(s: Servidor):
    return s.estado(), s.huella_esquema()


def test_a_despliegue_completo_y_checklist_verde(s):
    assert s.motor("Fm") == "ok"
    assert s.sql("SELECT to_regclass('public.auto03_prueba') IS NOT NULL")[0][0] is True
    assert "MIGRACION=0081_orbit07_proteccion_venta.sql" in s.ultimo()
    assert s.sql("SELECT to_regclass('public.proteccion_venta_reciente') IS NOT NULL")[0][0] is True
    assert (
        s.sql(
            "SELECT pg_get_constraintdef(oid) LIKE '%espera_proteccion%'"
            " FROM pg_constraint WHERE conname = 'decision_sin_aplicar_motivo_check'"
        )[0][0]
        is True
    )
    assert s.estado() == (s.rev("Fm"), "Fm", LIVE)
    assert s.estado_diario() == "verificado"
    compose = (s.ctl / "docker.log").read_text()
    proyecto = f"-p orbit --project-directory {s.srv} -f {s.srv}/docker-compose.yml"
    assert f"compose {proyecto} up -d --no-deps --build app" in compose
    assert (
        f"{s.md5_git('Fm', 'app/etiqueta.txt')}  app/etiqueta.txt"
        in (s.ctl / "compose-md5.txt").read_text()
    )
    assert (s.srv / "tools/solo_servidor.sh").exists()


def test_b_checklist_rojo_con_migracion_pendiente_o_clave_distinta(s):
    assert s.motor("Fm") == "ok"
    s.sql(REVERSA_PRUEBA)
    assert s.checklist("Fm").returncode != 0
    s.sql(MIGRACION_PRUEBA)
    assert s.checklist("Fm").returncode == 0
    s.pon_config({**LIVE, "ads_producto_meta_amazon_us": "shadow"})
    assert s.checklist("Fm").returncode != 0


def test_c_h_rollback_deja_todo_igual_y_el_sha_siguiente_reaplica(s):
    inicial = _inicial(s)
    (s.ctl / "inestable").write_text("Fm")
    assert s.motor("Fm") == "fallo_0"
    assert _inicial(s) == inicial
    assert s.estado_diario() == "revertido"
    assert s.sha_srv() == s.rev("E")
    assert s.motor("F2") == "ok"
    assert s.estado() == (s.rev("F2"), "F2", LIVE)


def test_d_rollback_no_toca_otro_sha_ni_abortado(s):
    inicial = _inicial(s)
    for diario in (
        f"MOTOR_SHA={s.rev('F2')}\nESTADO=desplegado\nSHA_PREVIO={s.rev('E')}\n"
        "CODIGO=predeploy-x\nCLAVE=ads_producto_meta_amazon_mx:\n",
        f"MOTOR_SHA={s.rev('Fm')}\nESTADO=guardas\nSHA_PREVIO={s.rev('E')}\n"
        "ESTADO=abortado_sin_cambios\n",
    ):
        (s.srv / "despliegue/ultimo.env").write_text(diario)
        r = s.rollback("Fm")
        assert r.returncode == 0, r.stdout + r.stderr
        assert (s.srv / "despliegue/ultimo.env").read_text() == diario
        assert _inicial(s) == inicial


def test_e_guarda_ocupada_sale_75_sin_cambiar_nada(s):
    inicial = _inicial(s)
    (s.ctl / "top").write_text(
        "UID PID CMD\n10001 9 python -m app.cli cycle --platform amazon_mx\n"
    )
    r = s.desplegar("Fm", AUTO03_ESPERA_S="1")
    assert r.returncode == 75, r.stdout + r.stderr
    assert s.estado_diario() == "abortado_sin_cambios"
    assert _inicial(s) == inicial
    assert not (s.ctl / "compose-md5.txt").exists()


def test_f_sonda(s):
    sondas = s.repo / "tools/auto03/sondas"
    casos = {
        "b5_ok": ("SELECT 't|uno'; SELECT 't|dos';", 0),
        "b5_f": ("SELECT 't|uno'; SELECT 'f|dos';", 1),
        "b5_vacia": ("SELECT 't|x' WHERE false;", 1),
        "b5_error": ("SELECT 1/0;", 1),
    }
    try:
        for nombre, (texto, esperado) in casos.items():
            (sondas / f"{nombre}.sql").write_text(texto)
            r = s.corre("tools/auto03/sonda.sh", "E", nombre)
            assert (r.returncode != 0) == bool(esperado), (nombre, r.stdout, r.stderr)
        assert s.corre("tools/auto03/sonda.sh", "E", "base").returncode == 1
        s.pon_config({})
        assert s.corre("tools/auto03/sonda.sh", "E", "base").returncode == 0
        (s.ctl / "ssh_falla").touch()
        assert s.corre("tools/auto03/sonda.sh", "E", "b5_ok", ORBIT_SIMULACION="0").returncode != 0
    finally:
        for nombre in casos:
            (sondas / f"{nombre}.sql").unlink(missing_ok=True)


def test_g_migracion_sin_cabecera_hace_fallar_desplegar(s):
    inicial = _inicial(s)
    r = s.desplegar("SinCabecera")
    assert r.returncode == 1 and "sonda-aplicada" in r.stderr
    assert _inicial(s) == inicial and s.ultimo() == []


def test_i_sha_contenido_no_cambia_claves(s):
    assert s.motor("Fm") == "ok"
    s.pon_config(SHADOW)
    r = s.desplegar("Gm")
    assert r.returncode == 0 and s.estado_diario() == "sin_cambios"
    assert s.checklist("Gm").returncode == 0
    assert s.estado() == (s.rev("Fm"), "Fm", SHADOW)


@pytest.mark.parametrize("corte", ["5", "6"])
def test_j_corte_y_reanudacion_aplica_todas_las_claves(s, corte):
    assert s.motor("Fm", corte=corte) == "cortado"
    assert s.sha_srv() == s.rev("Fm")
    assert s.config() == SHADOW if corte == "5" else s.config() not in (SHADOW, LIVE)
    assert s.motor("Fm") == "ok"
    assert s.estado() == (s.rev("Fm"), "Fm", LIVE)


@pytest.mark.parametrize("corte", ["5", "6"])
def test_j_corte_y_checklist_rojo_el_rollback_deja_todo_igual(s, corte):
    inicial = _inicial(s)
    assert s.motor("Fm", corte=corte) == "cortado"
    (s.ctl / "inestable").write_text("Fm")
    assert s.motor("Fm") == "fallo_0"
    assert _inicial(s) == inicial


def test_k_corte_tras_la_primera_clave_y_guarda_ocupada(s):
    inicial = _inicial(s)
    assert s.motor("Fm", corte="6") == "cortado"
    (s.ctl / "top").write_text("UID PID CMD\n10001 9 python -m app.cli ingest\n")
    assert s.motor("Fm", AUTO03_ESPERA_S="1") == "reintenta"
    estados = [ln for ln in s.ultimo() if ln.startswith("ESTADO=")]
    assert estados[-3:] == ["ESTADO=guardas_con_cambios", "ESTADO=revirtiendo", "ESTADO=revertido"]
    assert _inicial(s) == inicial
    (s.ctl / "top").unlink()
    assert s.motor("F2") == "ok"
    assert s.estado() == (s.rev("F2"), "F2", LIVE)


def test_k_guarda_ocupada_y_reversa_incompleta_sale_1_y_el_motor_la_termina(s):
    inicial = _inicial(s)
    assert s.motor("Fm", corte="6") == "cortado"
    (s.ctl / "top").write_text("UID PID CMD\n10001 9 python -m app.cli ingest\n")
    (s.ctl / "psql_falla_vigente").touch()
    assert s.motor("Fm", AUTO03_ESPERA_S="1") == "fallo_1"
    assert s.estado_diario() == "rollback_incompleto"
    assert s.desplegar("F2").returncode == 1
    (s.ctl / "psql_falla_vigente").unlink()
    assert s.rollback("Fm").returncode == 1
    assert _inicial(s) == inicial and s.estado_diario() == "revertido"


def test_l_muerte_a_media_copia_del_respaldo(s):
    inicial = _inicial(s)
    assert s.motor("Fm", corte="3a") == "cortado"
    assert not any(ln.startswith("CODIGO=") for ln in s.ultimo())
    assert list((s.srv / "despliegue").glob("predeploy-*.parcial"))
    (s.ctl / "inestable").write_text("Fm")
    assert s.motor("Fm") == "fallo_0"
    assert not list((s.srv / "despliegue").glob("predeploy-*.parcial"))
    assert _inicial(s) == inicial
    assert (s.srv / "tools/solo_servidor.sh").exists()


def test_m_corte_entre_checklist_rojo_y_rollback(s):
    inicial = _inicial(s)
    (s.ctl / "inestable").write_text("Fm")
    assert s.motor("Fm", muere_antes_rb=True) == "cortado"
    antes = s.ultimo()
    assert s.estado_diario() == "desplegado"
    assert s.motor("Fm") == "fallo_0"
    despues = s.ultimo()
    for clave in ("SHA_PREVIO=", "CODIGO=", "CLAVE="):
        assert [ln for ln in despues if ln.startswith(clave)] == [
            ln for ln in antes if ln.startswith(clave)
        ]
    assert _inicial(s) == inicial


def test_n_rollback_cortado_a_la_mitad(s):
    inicial = _inicial(s)
    (s.ctl / "inestable").write_text("Fm")
    assert s.motor("Fm", corte_rb="rb") == "cortado"
    assert s.estado() != inicial[0] and s.estado_diario() == "revirtiendo"
    diario = s.ultimo()
    r = s.desplegar("F2")
    assert r.returncode == 1 and s.ultimo() == diario
    assert s.motor("Fm") == "fallo_1"
    assert _inicial(s) == inicial and s.estado_diario() == "revertido"


def test_o_ensayo_no_escribe_y_falla_con_ssh_psql_o_sonda_vacia(s):
    inicial, huella = _inicial(s), s.huella_srv()
    r = s.desplegar("Fm", "--ensayo")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "migracion pendiente: 9001_auto03_prueba.sql" in r.stdout
    assert "clave que cambiaria: ads_producto_meta_amazon_mx" in r.stdout
    assert _inicial(s) == inicial and s.huella_srv() == huella
    assert not (s.ctl / "compose-md5.txt").exists()
    otro = make_conninfo(s.dsn, dbname="no_existe_b5")
    assert s.desplegar("Fm", "--ensayo", ORBIT_SIM_DSN=otro).returncode != 0
    assert s.desplegar("SondaVacia", "--ensayo").returncode != 0
    (s.ctl / "ssh_falla").touch()
    assert s.desplegar("Fm", "--ensayo").returncode != 0
    assert s.huella_srv() == huella


def test_p_candado_ocupado(s):
    (s.ctl / "inestable").write_text("Fm")
    assert s.desplegar("Fm").returncode == 0
    assert s.checklist("Fm").returncode == 1
    diario = (s.srv / "despliegue/ultimo.env").read_text()
    rival = subprocess.Popen(
        ["flock", str(s.srv / "despliegue/.lock"), "cat"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        t0 = time.monotonic()
        r = s.desplegar("F2", AUTO03_ESPERA_S="1")
        assert r.returncode == 75 and time.monotonic() - t0 < 20
        assert s.rollback("Fm", AUTO03_ESPERA_S="1").returncode == 75
        assert (s.srv / "despliegue/ultimo.env").read_text() == diario
        espera = subprocess.Popen(
            ["bash", str(s.repo / "tools/despliegue/rollback.sh")],
            cwd=s.repo,
            env=s.entorno(MOTOR_SHA=s.rev("Fm"), AUTO03_ESPERA_S="60"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(4)
        assert espera.poll() is None
        assert (s.srv / "despliegue/ultimo.env").read_text() == diario
    finally:
        rival.stdin.close()
        rival.wait(timeout=10)
    assert espera.wait(timeout=120) == 1
    assert s.estado_diario() == "revertido" and s.estado() == (s.rev("E"), "E", SHADOW)


def test_w_rollback_que_pierde_el_candado_no_sigue_cambiando(s):
    inicial = _inicial(s)
    assert s.motor("Fm") == "ok"
    (s.ctl / "candado_setsid").touch()
    (s.ctl / "barrera").touch()
    rb = subprocess.Popen(
        ["bash", str(s.repo / "tools/despliegue/rollback.sh")],
        cwd=s.repo,
        env=s.entorno(MOTOR_SHA=s.rev("Fm")),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    fin = time.monotonic() + 60
    while not (s.ctl / "barrera.llego").exists():
        assert rb.poll() is None and time.monotonic() < fin
        time.sleep(0.2)
    os.killpg(int((s.ctl / "candado.pid").read_text()), signal.SIGTERM)
    candado = str(s.srv / "despliegue/.lock")
    while subprocess.run(["flock", "-n", candado, "true"]).returncode != 0:
        assert time.monotonic() < fin
        time.sleep(0.2)
    (s.ctl / "barrera").unlink()
    salida = rb.communicate(timeout=120)[0]
    print(salida)
    assert rb.returncode == 1 and "se perdio la sesion ssh" in salida
    assert s.estado_diario() == "revirtiendo"
    assert s.estado() == (s.rev("Fm"), "Fm", {**LIVE, "ads_producto_meta_amazon_mx": "shadow"})
    assert s.sql("SELECT to_regclass('public.auto03_prueba') IS NOT NULL")[0][0] is True
    (s.ctl / "candado_setsid").unlink()
    assert s.rollback("Fm").returncode == 1
    assert _inicial(s) == inicial and s.estado_diario() == "revertido"


def test_q_vaciar_el_archivo_apaga_las_claves(s):
    assert s.motor("Fm") == "ok"
    assert s.motor("F3") == "ok"
    assert s.config() == {}
    s.pon_config({"ads_producto_meta_amazon_mx": "live"})
    assert s.checklist("F3").returncode == 1


def test_r_sin_sha_previo_converge_todo_el_archivo(s):
    (s.srv / "despliegue/sha").unlink()
    s.pon_config({})
    assert s.motor("Gm") == "ok"
    assert s.config() == SHADOW
    assert sorted(ln for ln in s.ultimo() if ln.startswith("CLAVE=")) == [
        "CLAVE=ads_producto_meta_amazon_mx:",
        "CLAVE=ads_producto_meta_amazon_us:",
    ]


def test_s_subir_a_live_verifica_cada_plataforma_completa(s):
    assert s.motor("Fm") == "ok"
    log = (s.ctl / "docker.log").read_text()
    for plat in ("amazon_mx", "amazon_us"):
        assert f"auto03_verifica evalua --platform {plat} --json /tmp/auto03-{plat}.json" in log
        assert f"productos-modo --platform {plat} --meta live" in log


def test_t_migracion_que_revienta_no_deja_nada(s):
    r = s.desplegar("Revienta")
    assert r.returncode not in (0, 75) and "duplicate key" in r.stderr
    assert s.sql("SELECT to_regclass('public.auto03_revienta')")[0][0] is None
    assert s.rollback("Revienta").returncode == 1
    assert s.estado() == (s.rev("E"), "E", SHADOW)


def test_t_migracion_con_transaccion_propia_no_entra(s):
    inicial = _inicial(s)
    r = s.desplegar("ConTransaccion")
    assert r.returncode == 1 and "control de transaccion" in r.stderr
    assert s.sql("SELECT to_regclass('public.auto03_tx')")[0][0] is None
    assert _inicial(s) == inicial and s.ultimo() == []


def test_u_lectura_vigente_fallida_no_anota_ni_muta(s):
    (s.ctl / "psql_falla_vigente").touch()
    r = s.desplegar("Fm")
    assert r.returncode == 1
    assert not any(ln.startswith("CLAVE=") for ln in s.ultimo())
    assert "productos-modo" not in (s.ctl / "docker.log").read_text()
    (s.ctl / "psql_falla_vigente").unlink()
    assert s.rollback("Fm").returncode == 1
    assert s.estado() == (s.rev("E"), "E", SHADOW)


def test_v_la_pausa_en_live_no_entra_por_el_archivo(s):
    inicial = _inicial(s)
    r = s.desplegar("PausaLive")
    assert r.returncode == 1 and "pausa" in r.stderr
    assert _inicial(s) == inicial and s.ultimo() == []


def test_meta_encendida_a_mano_antes_el_checklist_pasa(s):
    s.pon_config(LIVE)
    assert s.motor("Fm") == "ok"
    assert s.estado() == (s.rev("Fm"), "Fm", LIVE)


def test_apagado_manual_se_respeta(s):
    assert s.motor("Fm") == "ok"
    s.pon_config({"ads_producto_meta_amazon_us": "live"})
    assert s.motor("F2") == "ok"
    assert s.config() == {"ads_producto_meta_amazon_us": "live"}


def test_interruptores_del_repo_son_los_del_lector():
    """D.5: cada clave del estado deseado es un interruptor que lee
    productos.modos_desde_settings, con un valor que acepta."""
    from app.optimizer import productos

    claves = {
        productos.clave_modo(tipo, plataforma)
        for tipo in ("meta", "pausa")
        for plataforma in productos.PLATAFORMAS
    }
    texto = (ROOT / "tools/despliegue/interruptores.env").read_text(encoding="utf-8")
    deseado = dict(linea.split("=", 1) for linea in texto.splitlines() if linea.strip())
    assert deseado and set(deseado) <= claves
    for plataforma in productos.PLATAFORMAS:
        productos.modos_desde_settings(deseado, plataforma)
