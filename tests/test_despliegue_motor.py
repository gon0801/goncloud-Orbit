"""Prueba B.5 de AUTO-03: instalar y deshacer en maquinas desechables.

Corre los scripts REALES `tools/despliegue/{desplegar,checklist,rollback}.sh`
con ORBIT_SIMULACION=1 sobre una base desechable con la cadena completa
0001..0075 SIN la 0011 y sin reversas (patron de tests/test_schema_docs.py y
test_bids02_vistas.py: la 0011 exige el estado medido de produccion), con un
`docker` y un `ssh` falsos en el PATH que registran sus argumentos y emulan
`productos-modo` contra la base desechable, y con $SRV apuntado a un
directorio temporal (ORBIT_SRV). El md5 del codigo desplegado se compara
contra el SHA real del clon de pruebas.

(a) a (p) son los escenarios de docs/evidencia/auto-03/plan/modelo-despliegue/
modelo.py, el modelo de referencia de la receta, reproducidos contra los
scripts reales (los cortes de corrida se provocan con el docker y el pg_dump
fallos que crea esta prueba).

Skip fail-closed: sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432,
o sin psql/pg_dump/flock en el PATH, se skipea; corre en CI (quality.yml,
que trae postgresql-client y flock) y en dev con ORBIT_TEST_DSN.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
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


def _falta_herramienta() -> str | None:
    # El chequeo se hace como lo ven los scripts (bash command -v): en Windows
    # un shim sin extension existe para bash pero no para shutil.which.
    for herramienta in ("psql", "pg_dump", "flock", "md5sum", "git", "bash"):
        if shutil.which(herramienta) is None:
            vista = subprocess.run(
                ["bash", "-c", f"command -v {herramienta} >/dev/null 2>&1"],
                capture_output=True,
            )
            if vista.returncode != 0:
                return herramienta
    return None


_FALTA = _falta_herramienta()
skip = pytest.mark.skipif(
    _postgres_obligatorio_ausente() or _FALTA is not None,
    reason="sin Postgres utilizable o sin psql/pg_dump/flock en el PATH (corre en CI)",
)

PLATAFORMAS = ("amazon_mx", "amazon_us")
CLAVES_SHADOW = "".join(f"ads_producto_meta_{p}=shadow\n" for p in PLATAFORMAS)

SQL_0076 = (
    "-- AUTO-03: migracion de prueba del banco (solo existe en el clon temporal"
    " de esta prueba).\n"
    "-- sonda-aplicada: SELECT to_regclass('public.auto03_prueba_despliegue')"
    " IS NOT NULL;\n"
    "CREATE TABLE public.auto03_prueba_despliegue (\n"
    "    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY\n"
    ");\n"
    "GRANT SELECT ON public.auto03_prueba_despliegue TO app_read;\n"
)
REVERSA_0076 = "DROP TABLE IF EXISTS public.auto03_prueba_despliegue;\n"

SQL_0077_DOS = (
    "-- Segunda migracion de prueba (desplegable).\n"
    "-- sonda-aplicada: SELECT to_regclass('public.auto03_prueba_dos')"
    " IS NOT NULL;\n"
    "CREATE TABLE public.auto03_prueba_dos (id BIGINT PRIMARY KEY);\n"
    "GRANT SELECT ON public.auto03_prueba_dos TO app_read;\n"
)
REVERSA_0077_DOS = "DROP TABLE IF EXISTS public.auto03_prueba_dos;\n"

SQL_0077_VACIA = (
    "-- Sonda que no devuelve ninguna fila (el desplegador debe abortar).\n"
    "-- sonda-aplicada: SELECT NULL;\n"
    "SELECT 1;\n"
)
REVERSA_0077_VACIA = "SELECT 1;\n"

SQL_0077_SIN_CABECERA = (
    "-- Sin cabecera -- sonda-aplicada: el desplegador debe abortar.\nSELECT 1;\n"
)
REVERSA_0077_SIN_CABECERA = "SELECT 1;\n"

SQL_0077_REVIENTA = (
    "-- Aplica 0076 y revienta a proposito en el INSERT duplicado.\n"
    "-- sonda-aplicada: SELECT to_regclass('public.auto03_prueba_revienta')"
    " IS NOT NULL;\n"
    "CREATE TABLE public.auto03_prueba_revienta (id BIGINT PRIMARY KEY);\n"
    "INSERT INTO public.auto03_prueba_revienta VALUES (1), (1);\n"
)
REVERSA_0077_REVIENTA = "DROP TABLE IF EXISTS public.auto03_prueba_revienta;\n"

TABLAS_PRUEBA = (
    "auto03_prueba_despliegue",
    "auto03_prueba_dos",
    "auto03_prueba_revienta",
)

SSH_FALSO = """#!/usr/bin/env bash
set -u
printf 'ssh %s\\n' "$*" >> "${ORBIT_FAKE_LOG:?}"
if [ -f "${ORBIT_FAKE_DIR}/ssh-rc" ]; then exit "$(cat "${ORBIT_FAKE_DIR}/ssh-rc")"; fi
case "$*" in
  *"docker top orbit-app-1"*)
    [ -f "${ORBIT_FAKE_DIR}/top.txt" ] && cat "${ORBIT_FAKE_DIR}/top.txt"
    ;;
  *"curl"*)
    if [ -f "${ORBIT_FAKE_DIR}/health.txt" ]; then
      cat "${ORBIT_FAKE_DIR}/health.txt"
    else
      printf '200\\n'
    fi
    ;;
esac
exit 0
"""

DOCKER_FALSO = """#!/usr/bin/env bash
set -u
printf 'docker %s\\n' "$*" >> "${ORBIT_FAKE_LOG:?}"
case "$*" in
  *"productos-modo"*)
    if [ -f "${ORBIT_FAKE_DIR}/docker-lento" ]; then sleep 10; fi
    if [ -f "${ORBIT_FAKE_DIR}/docker-rc-una" ]; then
      rm -f "${ORBIT_FAKE_DIR}/docker-rc-una"
      exit 1
    fi
    if [ -f "${ORBIT_FAKE_DIR}/docker-rc" ]; then exit "$(cat "${ORBIT_FAKE_DIR}/docker-rc")"; fi
    plat="" tipo="" val=""; prev=""
    for a in "$@"; do
      case "$prev" in
        --platform) plat="$a" ;;
        --meta) tipo="meta"; val="$a" ;;
        --pausa) tipo="pausa"; val="$a" ;;
        --clics) tipo="clics"; val="$a" ;;
        --gasto) tipo="gasto"; val="$a" ;;
      esac
      prev="$a"
    done
    case "$tipo" in
      meta) clave="ads_producto_meta_$plat" ;;
      pausa) clave="ads_producto_pausa_$plat" ;;
      clics) clave="ads_producto_pausa_clics_$plat" ;;
      gasto) clave="ads_producto_pausa_gasto_$plat" ;;
      *) exit 0 ;;
    esac
    if [ -f "${ORBIT_FAKE_DIR}/override-meta" ] && [ "$tipo" = meta ]; then
      val="$(cat "${ORBIT_FAKE_DIR}/override-meta")"
    fi
    sql_vig="SELECT COALESCE(settings->>'$clave','') FROM config_version ORDER BY id DESC LIMIT 1"
    vig="$(psql "$ORBIT_SIM_DSN" -X -q -tA -v ON_ERROR_STOP=1 -c "$sql_vig")" || exit 1
    [ "$vig" = "$val" ] && exit 0
    sql_pre="INSERT INTO config_version (label, settings) SELECT 'productos-modo (docker falso)',"
    if [ "$val" = off ]; then
      sql_set=" COALESCE((SELECT settings - '$clave' FROM config_version"
      sql_set="$sql_set ORDER BY id DESC LIMIT 1), '{}')"
    else
      sql_set=" jsonb_set(COALESCE((SELECT settings FROM config_version"
      sql_set="$sql_set ORDER BY id DESC LIMIT 1), '{}'), '{$clave}', to_jsonb('$val'::text))"
    fi
    psql "$ORBIT_SIM_DSN" -X -q -v ON_ERROR_STOP=1 -c "$sql_pre$sql_set"
    ;;
esac
exit 0
"""

PGDUMP_FALSO = """#!/usr/bin/env bash
set -u
DIR="${ORBIT_FAKE_DIR:?}"
if [ -f "$DIR/pgdump-una" ]; then
  rm -f "$DIR/pgdump-una"
  echo "-- dump truncado a proposito (prueba B.5 l)"
  exit 1
fi
exec {REAL_PG_DUMP} "$@"
"""


def _commit(clon: Path, mensaje: str, archivos: dict[str, str | None]) -> str:
    for relativa, contenido in archivos.items():
        destino = clon / relativa
        if contenido is None:
            destino.unlink(missing_ok=True)
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido, encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "-A"], cwd=clon, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=banco@prueba",
            "-c",
            "user.name=banco",
            "commit",
            "-q",
            "-m",
            mensaje,
        ],
        cwd=clon,
        check=True,
        capture_output=True,
    )
    return _git(clon, "rev-parse", "HEAD")


def _git(clon: Path, *args: str) -> str:
    salida = subprocess.run(["git", *args], cwd=clon, check=True, capture_output=True, text=True)
    return salida.stdout.strip()


def _montar_banco():
    """Monta banco de pruebas (base desechable + clon con commits + falsos).

    Rinde el dict del banco; el llamador limpia con _limpiar_banco()."""
    dsn = _test_dsn()
    nombre_db = f"despliegue_motor_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(
        pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nombre_db))
    )
    admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(nombre_db)))
    conn = psycopg.connect(dsn, dbname=nombre_db, autocommit=True)
    # Un Postgres local sin tzdata (pgserver) corre en GMT, el mismo offset
    # que UTC; los escenarios de despliegue no comparan horas.
    with contextlib.suppress(psycopg.Error):
        conn.execute("SET TIME ZONE 'UTC'")
    for ruta in sorted((ROOT / "migrations").glob("*.sql")):
        if "0011_" in ruta.name or "_reversa_" in ruta.name:
            continue
        conn.execute(ruta.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO config_version (label, settings) VALUES ('banco de pruebas', '{}')")
    dsn_db = make_conninfo(dsn, dbname=nombre_db)

    base = Path(tempfile.mkdtemp(prefix="auto03-banco-"))
    clon = base / "clon"
    subprocess.run(
        ["git", "clone", "-q", "--shared", "--no-hardlinks", str(ROOT), str(clon)],
        check=True,
        capture_output=True,
    )
    base_sha = _git(clon, "rev-parse", "HEAD")
    c2 = _commit(
        clon,
        "banco: migracion de prueba e interruptores en shadow",
        {
            "tools/despliegue/interruptores.env": CLAVES_SHADOW,
            "migrations/0076_auto03_prueba_despliegue.sql": SQL_0076,
            "migrations/0076_reversa_auto03_prueba_despliegue.sql": REVERSA_0076,
        },
    )
    c6 = _commit(
        clon,
        "banco: segunda migracion desplegable",
        {
            "migrations/0077_auto03_prueba_dos.sql": SQL_0077_DOS,
            "migrations/0077_reversa_auto03_prueba_dos.sql": REVERSA_0077_DOS,
        },
    )
    c3 = _commit(
        clon,
        "banco: migracion con sonda vacia",
        {
            "migrations/0077_auto03_prueba_dos.sql": None,
            "migrations/0077_reversa_auto03_prueba_dos.sql": None,
            "migrations/0077_auto03_sonda_vacia.sql": SQL_0077_VACIA,
            "migrations/0077_reversa_auto03_sonda_vacia.sql": REVERSA_0077_VACIA,
        },
    )
    c4 = _commit(
        clon,
        "banco: migracion sin cabecera",
        {
            "migrations/0077_auto03_sonda_vacia.sql": None,
            "migrations/0077_reversa_auto03_sonda_vacia.sql": None,
            "migrations/0077_auto03_sin_cabecera.sql": SQL_0077_SIN_CABECERA,
            "migrations/0077_reversa_auto03_sin_cabecera.sql": REVERSA_0077_SIN_CABECERA,
        },
    )
    c5 = _commit(
        clon,
        "banco: migracion que revienta al aplicar",
        {
            "migrations/0077_auto03_sin_cabecera.sql": None,
            "migrations/0077_reversa_auto03_sin_cabecera.sql": None,
            "migrations/0077_auto03_revienta_al_aplicar.sql": SQL_0077_REVIENTA,
            "migrations/0077_reversa_auto03_revienta_al_aplicar.sql": REVERSA_0077_REVIENTA,
        },
    )

    falsos = base / "falsos"
    falsos.mkdir()
    for nombre, contenido in (("ssh", SSH_FALSO), ("docker", DOCKER_FALSO)):
        ruta = falsos / nombre
        ruta.write_text(contenido, encoding="utf-8", newline="\n")
        ruta.chmod(0o755)
    log = base / "falsos.log"
    log.write_text("", encoding="utf-8")

    datos = {
        "base": base,
        "clon": clon,
        "falsos": falsos,
        "log": log,
        "srv": base / "srv",
        "conn": conn,
        "admin": admin,
        "dsn_db": dsn_db,
        "nombre_db": nombre_db,
        "base_sha": base_sha,
        "c2": c2,
        "c3": c3,
        "c4": c4,
        "c5": c5,
        "c6": c6,
    }
    return datos


def _limpiar_banco(banco) -> None:
    banco["conn"].close()
    banco["admin"].execute(
        pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
            pgsql.Identifier(banco["nombre_db"])
        )
    )
    banco["admin"].close()
    shutil.rmtree(banco["base"], ignore_errors=True)


@pytest.fixture(scope="module")
def banco():
    datos = _montar_banco()
    yield datos
    _limpiar_banco(datos)


# ---------------------------------------------------------------------------
# Helpers de escenario
# ---------------------------------------------------------------------------


def _siembra_srv(banco, sha: str) -> None:
    for tabla in TABLAS_PRUEBA:
        banco["conn"].execute(f"DROP TABLE IF EXISTS public.{tabla}")
    srv = banco["srv"]
    shutil.rmtree(srv, ignore_errors=True)
    (srv / "despliegue").mkdir(parents=True)
    (srv / "app").mkdir()
    (srv / "app" / "viejo.py").write_text("codigo viejo\n", encoding="utf-8", newline="\n")
    (srv / "tools").mkdir()
    (srv / "tools" / "viejo.sh").write_text("viejo\n", encoding="utf-8", newline="\n")
    for nombre, contenido in {
        "Dockerfile": "FROM nada\n",
        ".dockerignore": "x\n",
        "pyproject.toml": "[project]\n",
        "uv.lock": "lock\n",
    }.items():
        (srv / nombre).write_text(contenido, encoding="utf-8", newline="\n")
    (srv / "despliegue" / "sha").write_text(f"{sha}\n", encoding="utf-8", newline="\n")


def _limpia_falsos(banco) -> None:
    banco["log"].write_text("", encoding="utf-8")
    for nombre in (
        "top.txt",
        "docker-rc",
        "docker-rc-una",
        "override-meta",
        "ssh-rc",
        "health.txt",
        "pgdump-una",
        "docker-lento",
    ):
        (banco["falsos"] / nombre).unlink(missing_ok=True)


def _ruta_bash(ruta: Path) -> str:
    # Los scripts corren en bash: en Windows, cygpath les da la forma POSIX
    # (tar de msys rechaza C:\\... y tar la lee como host remoto).
    if os.name != "nt":
        return str(ruta)
    salida = subprocess.run(
        ["cygpath", "-u", str(ruta)], capture_output=True, text=True
    )
    if salida.returncode == 0 and salida.stdout.strip():
        return salida.stdout.strip()
    return str(ruta)


def _entorno(banco, *, espera: str = "600", extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env["ORBIT_SIMULACION"] = "1"
    env["ORBIT_SIM_DSN"] = banco["dsn_db"]
    env["ORBIT_SRV"] = _ruta_bash(banco["srv"])
    env["ORBIT_FAKE_LOG"] = _ruta_bash(banco["log"])
    env["ORBIT_FAKE_DIR"] = _ruta_bash(banco["falsos"])
    env["AUTO03_ESPERA_S"] = espera
    env["PATH"] = f"{banco['falsos']}{os.pathsep}{env['PATH']}"
    env.pop("MOTOR_SHA", None)
    if extra:
        env.update(extra)
    return env


def _corre(
    banco,
    ruta: str,
    *,
    sha: str | None = None,
    espera: str = "600",
    extra: dict | None = None,
    tope: int = 240,
    path_frente: Path | None = None,
):
    env = _entorno(banco, espera=espera, extra=extra)
    if sha is not None:
        env["MOTOR_SHA"] = sha
    if path_frente is not None:
        env["PATH"] = f"{path_frente}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", ruta],
        cwd=str(banco["clon"]),
        env=env,
        capture_output=True,
        text=True,
        timeout=tope,
    )


def _despliega(banco, sha, **kwargs):
    return _corre(banco, "tools/despliegue/desplegar.sh", sha=sha, **kwargs)


def _checklist(banco, sha, **kwargs):
    return _corre(banco, "tools/despliegue/checklist.sh", sha=sha, **kwargs)


def _rollback(banco, sha, **kwargs):
    return _corre(banco, "tools/despliegue/rollback.sh", sha=sha, **kwargs)


def _ultimo(banco) -> list[str]:
    ruta = banco["srv"] / "despliegue" / "ultimo.env"
    if not ruta.exists():
        return []
    return ruta.read_text(encoding="utf-8").splitlines()


def _cuenta_lineas(banco, clave: str) -> int:
    prefijo = f"{clave}="
    return len([linea for linea in _ultimo(banco) if linea.startswith(prefijo)])


def _valor_ultimo(banco, clave: str) -> str:
    valor = ""
    for linea in _ultimo(banco):
        if linea.startswith(f"{clave}="):
            valor = linea.split("=", 1)[1]
    return valor


def _sha_srv(banco) -> str:
    ruta = banco["srv"] / "despliegue" / "sha"
    return ruta.read_text(encoding="utf-8").strip() if ruta.exists() else ""


def _huella_esquema(banco) -> str:
    consultas = (
        "SELECT 'columna', table_name, column_name, coalesce(data_type,''),"
        " coalesce(is_nullable,''), coalesce(column_default,'')"
        " FROM information_schema.columns WHERE table_schema='public'",
        "SELECT 'constraint', conrelid::regclass::text, conname,"
        " pg_get_constraintdef(oid), '', '' FROM pg_constraint",
        "SELECT 'trigger', tgrelid::regclass::text, tgname, pg_get_triggerdef(oid), '', ''"
        " FROM pg_trigger WHERE NOT tgisinternal",
        "SELECT 'grant', table_name, grantee, privilege_type, '', ''"
        " FROM information_schema.role_table_grants WHERE table_schema='public'",
    )
    filas: list[tuple] = []
    for consulta in consultas:
        filas.extend(banco["conn"].execute(consulta).fetchall())
    return hashlib.sha256(repr(sorted(filas)).encode("utf-8")).hexdigest()


def _config(banco) -> dict:
    fila = (
        banco["conn"]
        .execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1")
        .fetchone()
    )
    return dict(fila[0]) if fila is not None else {}


def _huella_srv(banco) -> str:
    huellas = []
    for ruta in sorted(banco["srv"].rglob("*")):
        if ruta.is_file():
            huellas.append(
                f"{ruta.relative_to(banco['srv']).as_posix()}="
                f"{hashlib.md5(ruta.read_bytes()).hexdigest()}"
            )
    return "\n".join(huellas)


def _toque_config(banco, clave: str, valor: str) -> None:
    if valor == "off":
        banco["conn"].execute(
            "INSERT INTO config_version (label, settings)"
            " SELECT 'prueba (toque off)',"
            " COALESCE((SELECT settings - %s FROM config_version ORDER BY id DESC LIMIT 1),"
            " '{}'::jsonb)",
            (clave,),
        )
    else:
        banco["conn"].execute(
            "INSERT INTO config_version (label, settings)"
            " SELECT 'prueba (toque)',"
            " jsonb_set((SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),"
            " ARRAY[%s], to_jsonb(%s::text))",
            (clave, valor),
        )


def _log_tiene(banco, fragmento: str) -> bool:
    return fragmento in banco["log"].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) desplegar aplica la migracion, su sonda da t, registra compose up con el
#     md5 correcto y el checklist sale 0
# ---------------------------------------------------------------------------


@skip
def test_a_despliegue_completo_checklist_verde(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    corrida = _despliega(banco, banco["c2"])
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    assert "compose up -d --no-deps --build app" in banco["log"].read_text(encoding="utf-8")
    fila = (
        banco["conn"]
        .execute("SELECT to_regclass('public.auto03_prueba_despliegue') IS NOT NULL")
        .fetchone()
    )
    assert fila[0] is True, "la 0076 de prueba no quedo aplicada"
    assert "MIGRACION=migrations/0076_auto03_prueba_despliegue.sql" in _ultimo(banco)
    assert _cuenta_lineas(banco, "CODIGO") == 1
    assert _valor_ultimo(banco, "ESTADO") == "desplegado"
    assert _sha_srv(banco) == banco["c2"]
    config = _config(banco)
    for plataforma in PLATAFORMAS:
        assert config[f"ads_producto_meta_{plataforma}"] == "shadow"
        assert f"CLAVE=ads_producto_meta_{plataforma}:" in "\n".join(_ultimo(banco))
    archivo = subprocess.run(
        ["git", "show", f"{banco['c2']}:app/main.py"],
        cwd=str(banco["clon"]),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert (banco["srv"] / "app" / "main.py").read_text(encoding="utf-8") == archivo, (
        "el codigo desplegado no es el del SHA (md5)"
    )
    checklist = _checklist(banco, banco["c2"])
    assert checklist.returncode == 0, checklist.stdout + checklist.stderr
    assert _valor_ultimo(banco, "ESTADO") == "verificado"


# ---------------------------------------------------------------------------
# (b) con una migracion pendiente o una clave convergida distinta del archivo,
#     el checklist sale distinto de cero
# ---------------------------------------------------------------------------


@skip
def test_b_checklist_falla_migracion_pendiente_y_clave_divergente(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    corrida = _despliega(banco, banco["c5"])
    assert corrida.returncode != 0, "la 0077 de prueba debio revientar al aplicar"
    ultimo = "\n".join(_ultimo(banco))
    assert "MIGRACION=migrations/0076_auto03_prueba_despliegue.sql" in ultimo
    assert "MIGRACION=migrations/0077_auto03_revienta_al_aplicar.sql" in ultimo
    checklist = _checklist(banco, banco["c5"])
    assert checklist.returncode != 0, "el checklist debio fallar con la 0077 pendiente"
    assert "0077_auto03_revienta_al_aplicar" in checklist.stdout
    reversa = _rollback(banco, banco["c5"])
    assert reversa.returncode != 0
    assert _valor_ultimo(banco, "ESTADO") == "revertido"

    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    (banco["falsos"] / "override-meta").write_text("live", encoding="utf-8")
    corrida = _despliega(banco, banco["c2"])
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    assert _config(banco)["ads_producto_meta_amazon_mx"] == "live"
    checklist = _checklist(banco, banco["c2"])
    assert checklist.returncode != 0, "el checklist debio fallar con la clave divergente"
    (banco["falsos"] / "override-meta").unlink()
    reversa = _rollback(banco, banco["c2"])
    assert _valor_ultimo(banco, "ESTADO") == "revertido"
    assert _config(banco) == {}


# ---------------------------------------------------------------------------
# (c) rollback deja la huella del esquema (columnas, constraints, triggers,
#     grants) y la config iguales a las de antes de desplegar
# ---------------------------------------------------------------------------


@skip
def test_c_rollback_restaura_esquema_y_config(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    esquema_antes = _huella_esquema(banco)
    config_antes = _config(banco)
    (banco["falsos"] / "override-meta").write_text("live", encoding="utf-8")
    corrida = _despliega(banco, banco["c2"])
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    assert _huella_esquema(banco) != esquema_antes, "el despliegue no cambio el esquema"
    checklist = _checklist(banco, banco["c2"])
    assert checklist.returncode != 0
    (banco["falsos"] / "override-meta").unlink()
    assert _rollback(banco, banco["c2"]).returncode != 0
    assert _valor_ultimo(banco, "ESTADO") == "revertido"
    assert _huella_esquema(banco) == esquema_antes, "el rollback no restauro el esquema"
    assert _config(banco) == config_antes, "el rollback no restauro la config"


# ---------------------------------------------------------------------------
# (d) con un ultimo.env de otro MOTOR_SHA o con ESTADO=abortado_sin_cambios,
#     rollback no toca nada
# ---------------------------------------------------------------------------


@skip
def test_d_rollback_no_toca_con_otro_sha_ni_con_abortado(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    corrida = _despliega(banco, banco["c2"])
    assert corrida.returncode == 0
    antes_sha = _sha_srv(banco)
    antes_config = _config(banco)
    otro = _rollback(banco, banco["c6"])
    assert otro.returncode == 0, otro.stdout + otro.stderr
    assert "nada que revertir" in otro.stdout
    assert _sha_srv(banco) == antes_sha
    assert _config(banco) == antes_config

    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    (banco["falsos"] / "top.txt").write_text("python app.cli cycle --platform amazon_us\n")
    corrida = _despliega(banco, banco["c2"], espera="1")
    assert corrida.returncode == 75
    assert _valor_ultimo(banco, "ESTADO") == "abortado_sin_cambios"
    antes_sha = _sha_srv(banco)
    otro = _rollback(banco, banco["c2"])
    assert otro.returncode == 0
    assert _valor_ultimo(banco, "ESTADO") == "abortado_sin_cambios"
    assert _sha_srv(banco) == antes_sha


# ---------------------------------------------------------------------------
# (e) con la guarda ocupada y AUTO03_ESPERA_S=1, desplegar sale 75 sin cambiar
#     nada
# ---------------------------------------------------------------------------


@skip
def test_e_guarda_ocupada_sale_75_sin_cambios(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    esquema_antes = _huella_esquema(banco)
    srv_antes = _huella_srv(banco)
    (banco["falsos"] / "top.txt").write_text("python app.cli cycle --platform amazon_us\n")
    inicio = time.monotonic()
    corrida = _despliega(banco, banco["c2"], espera="1")
    transcurrido = time.monotonic() - inicio
    assert corrida.returncode == 75, corrida.stdout + corrida.stderr
    assert transcurrido < 60, f"el plazo de 1s se disparo a {transcurrido:.0f}s"
    ultimo = "\n".join(_ultimo(banco))
    assert "ESTADO=abortado_sin_cambios" in ultimo
    for clave in ("CODIGO", "MIGRACION", "CLAVE"):
        assert f"\n{clave}=" not in f"\n{ultimo}"
    assert _sha_srv(banco) == banco["base_sha"]
    assert _huella_esquema(banco) == esquema_antes
    assert _huella_srv(banco) == srv_antes


# ---------------------------------------------------------------------------
# (f) sonda.sh sale distinto de cero con un error de psql, con salida vacia o
#     con una fila f|; y 0 con todas t|
# ---------------------------------------------------------------------------


@skip
def test_f_sonda_semantica(banco):
    sondas = banco["clon"] / "tools" / "auto03" / "sondas"
    sondas.mkdir(parents=True, exist_ok=True)
    (sondas / "prueba_bien.sql").write_text("SELECT 't' || '|ok';\n", encoding="utf-8")
    (sondas / "prueba_mal.sql").write_text("SELECT 'f' || '|mal';\n", encoding="utf-8")
    (sondas / "prueba_vacia.sql").write_text("SELECT 1 WHERE false;\n", encoding="utf-8")
    for nombre, espera_rc in (
        ("base", 0),
        ("prueba_bien", 0),
        ("prueba_mal", 1),
        ("prueba_vacia", 1),
    ):
        salida = subprocess.run(
            ["bash", "tools/auto03/sonda.sh", nombre],
            cwd=str(banco["clon"]),
            env=_entorno(banco),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert salida.returncode == espera_rc, (
            f"sonda {nombre}: rc={salida.returncode} salida={salida.stdout}{salida.stderr}"
        )
    rota = subprocess.run(
        ["bash", "tools/auto03/sonda.sh", "base"],
        cwd=str(banco["clon"]),
        env=_entorno(
            banco, extra={"ORBIT_SIM_DSN": "postgresql://orbit:orbit@127.0.0.1:54399/nada"}
        ),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert rota.returncode != 0, "la sonda debio fallar con psql inalcanzable"


# ---------------------------------------------------------------------------
# (g) una migracion *_auto03_* sin cabecera hace fallar desplegar
# ---------------------------------------------------------------------------


@skip
def test_g_migracion_sin_cabecera_falla_desplegar(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    esquema_antes = _huella_esquema(banco)
    corrida = _despliega(banco, banco["c4"])
    assert corrida.returncode != 0, corrida.stdout + corrida.stderr
    assert "no trae cabecera" in corrida.stderr
    ultimo = "\n".join(_ultimo(banco))
    assert "MIGRACION=" not in ultimo
    assert _huella_esquema(banco) == esquema_antes
    reversa = _rollback(banco, banco["c4"])
    assert reversa.returncode == 0
    assert _sha_srv(banco) == banco["base_sha"] or _valor_ultimo(banco, "ESTADO") == "revertido"


# ---------------------------------------------------------------------------
# (h) tras un checklist que falla y su rollback, $SRV/despliegue/sha vuelve a
#     SHA_PREVIO y desplegar un SHA posterior con el mismo interruptores.env
#     vuelve a aplicar las claves
# ---------------------------------------------------------------------------


@skip
def test_h_tras_rollback_sha_posterior_reaplica_claves(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    (banco["falsos"] / "override-meta").write_text("live", encoding="utf-8")
    assert _despliega(banco, banco["c2"]).returncode == 0
    assert _checklist(banco, banco["c2"]).returncode != 0
    (banco["falsos"] / "override-meta").unlink()
    assert _rollback(banco, banco["c2"]).returncode != 0
    assert _valor_ultimo(banco, "ESTADO") == "revertido"
    assert _sha_srv(banco) == banco["base_sha"], "el sha no volvio al SHA_PREVIO"
    assert _config(banco) == {}

    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    corrida = _despliega(banco, banco["c6"])
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    ultimo = "\n".join(_ultimo(banco))
    for plataforma in PLATAFORMAS:
        assert f"CLAVE=ads_producto_meta_{plataforma}:" in ultimo
        assert _config(banco)[f"ads_producto_meta_{plataforma}"] == "shadow"
    assert _checklist(banco, banco["c6"]).returncode == 0


# ---------------------------------------------------------------------------
# (i) desplegar un SHA ya contenido en el desplegado no cambia claves y el
#     checklist sale 0
# ---------------------------------------------------------------------------


@skip
def test_i_sha_contenido_sin_cambios(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    assert _despliega(banco, banco["c2"]).returncode == 0
    config_antes = _config(banco)
    claves_antes = _cuenta_lineas(banco, "CLAVE")
    corrida = _despliega(banco, banco["base_sha"])
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    assert _valor_ultimo(banco, "ESTADO") == "sin_cambios"
    assert _config(banco) == config_antes, "la monotonia no debe tocar claves"
    assert _cuenta_lineas(banco, "CLAVE") == claves_antes
    assert _sha_srv(banco) == banco["c2"]
    checklist = _checklist(banco, banco["base_sha"])
    assert checklist.returncode == 0, checklist.stdout + checklist.stderr


# ---------------------------------------------------------------------------
# (j) con una corrida cortada despues de (5) y otra despues de la primera clave
#     de (6), repetir desplegar con el mismo MOTOR_SHA aplica todas las claves
#     y el checklist sale 0; si despues el checklist falla, el rollback deja
#     codigo, sha y config iguales a antes de la primera corrida
# ---------------------------------------------------------------------------


@skip
def test_j_cortes_en_5_y_en_6_se_reanudan_y_rollback_deja_igual(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    srv_antes = _huella_srv(banco)
    esquema_antes = _huella_esquema(banco)
    config_antes = _config(banco)

    (banco["falsos"] / "docker-rc").write_text("1", encoding="utf-8")
    corrida = _despliega(banco, banco["c2"])
    assert corrida.returncode != 0, "el corte en (5) debio abortar el despliegue"
    assert _cuenta_lineas(banco, "CODIGO") == 1
    assert _sha_srv(banco) == banco["base_sha"], "el sha no debio escribirse antes del compose"
    (banco["falsos"] / "docker-rc").unlink()
    reanuda = _despliega(banco, banco["c2"])
    assert reanuda.returncode == 0, reanuda.stdout + reanuda.stderr
    assert _cuenta_lineas(banco, "CODIGO") == 1, "la reanudacion no debe repetir el respaldo"
    assert _cuenta_lineas(banco, "CLAVE") == 2
    assert _valor_ultimo(banco, "ESTADO") == "desplegado"
    assert _checklist(banco, banco["c2"]).returncode == 0

    _toque_config(banco, "ads_producto_meta_amazon_mx", "live")
    assert _checklist(banco, banco["c2"]).returncode != 0
    assert _rollback(banco, banco["c2"]).returncode != 0
    assert _valor_ultimo(banco, "ESTADO") == "revertido"
    assert _huella_srv(banco) == srv_antes, "el rollback no restauro el codigo"
    assert _huella_esquema(banco) == esquema_antes
    assert _config(banco) == config_antes
    assert _sha_srv(banco) == banco["base_sha"]

    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    (banco["falsos"] / "docker-rc-una").write_text("1", encoding="utf-8")
    corrida = _despliega(banco, banco["c2"])
    assert corrida.returncode != 0, "el corte en la primera clave debio abortar"
    assert _cuenta_lineas(banco, "CLAVE") == 1, "solo la primera clave debio anotarse"
    assert _valor_ultimo(banco, "ESTADO") == "guardas"
    (banco["falsos"] / "docker-rc-una").unlink()
    reanuda = _despliega(banco, banco["c2"])
    assert reanuda.returncode == 0, reanuda.stdout + reanuda.stderr
    assert _cuenta_lineas(banco, "CLAVE") == 2
    assert _config(banco)["ads_producto_meta_amazon_us"] == "shadow"
    assert _checklist(banco, banco["c2"]).returncode == 0


# ---------------------------------------------------------------------------
# (k) con una corrida cortada despues de la primera clave de (6), repetir
#     desplegar con la guarda ocupada sale 75 con ESTADO=guardas_con_cambios,
#     y el rollback deja codigo, sha y config como antes
# ---------------------------------------------------------------------------


@skip
def test_k_reanudacion_con_guarda_ocupada_y_rollback(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    srv_antes = _huella_srv(banco)
    esquema_antes = _huella_esquema(banco)
    config_antes = _config(banco)
    (banco["falsos"] / "docker-rc-una").write_text("1", encoding="utf-8")
    assert _despliega(banco, banco["c2"]).returncode != 0
    assert _cuenta_lineas(banco, "CLAVE") == 1
    _limpia_falsos(banco)
    (banco["falsos"] / "top.txt").write_text("python app.cli cycle --platform amazon_mx\n")
    corrida = _despliega(banco, banco["c2"], espera="1")
    assert corrida.returncode == 75, corrida.stdout + corrida.stderr
    assert _valor_ultimo(banco, "ESTADO") == "guardas_con_cambios"
    reversa = _rollback(banco, banco["c2"])
    assert _valor_ultimo(banco, "ESTADO") == "revertido"
    assert reversa.returncode != 0
    assert _huella_srv(banco) == srv_antes
    assert _huella_esquema(banco) == esquema_antes
    assert _config(banco) == config_antes
    assert _sha_srv(banco) == banco["base_sha"]


# ---------------------------------------------------------------------------
# (l) una corrida que muere a media copia del respaldo (3) no deja CODIGO=; la
#     reanudacion respalda completo y, si despues el checklist falla, el
#     rollback restaura todos los archivos
# ---------------------------------------------------------------------------


@skip
def test_l_muerte_a_media_copia_del_respaldo(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    srv_antes = _huella_srv(banco)
    real_pg_dump = shutil.which("pg_dump")
    assert real_pg_dump, "pg_dump debe estar en el PATH para esta prueba"
    pgdump_falso = banco["base"] / "pgdump-falso"
    pgdump_falso.mkdir(exist_ok=True)
    script = pgdump_falso / "pg_dump"
    script.write_text(
        PGDUMP_FALSO.replace("{REAL_PG_DUMP}", real_pg_dump),
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    (banco["falsos"] / "pgdump-una").write_text("1", encoding="utf-8")
    corrida = _despliega(banco, banco["c2"], path_frente=pgdump_falso)
    assert corrida.returncode != 0, "la muerte a media copia debio abortar"
    assert _cuenta_lineas(banco, "CODIGO") == 0, "no debe quedar CODIGO= a media copia"
    parciales = list((banco["srv"] / "despliegue").glob("predeploy-*.parcial"))
    assert parciales, "el respaldo debio quedar en .parcial"

    _limpia_falsos(banco)
    reanuda = _despliega(banco, banco["c2"])
    assert reanuda.returncode == 0, reanuda.stdout + reanuda.stderr
    assert _cuenta_lineas(banco, "CODIGO") == 1
    parciales = list((banco["srv"] / "despliegue").glob("predeploy-*"))
    assert len(parciales) == 1, f"la reanudacion debio dejar un respaldo completo: {parciales}"
    assert not list((banco["srv"] / "despliegue").glob("predeploy-*.parcial"))

    _toque_config(banco, "ads_producto_meta_amazon_mx", "live")
    assert _checklist(banco, banco["c2"]).returncode != 0
    assert _rollback(banco, banco["c2"]).returncode != 0
    assert _huella_srv(banco) == srv_antes, "el rollback no restauro todos los archivos"
    assert _config(banco) == {}


# ---------------------------------------------------------------------------
# (m) con una corrida que termina desplegado, checklist rojo y muerte antes del
#     rollback, repetir desplegar con el mismo MOTOR_SHA conserva SHA_PREVIO,
#     CODIGO y CLAVE, y el rollback siguiente deja todo como antes
# ---------------------------------------------------------------------------


@skip
def test_m_desplegado_checklist_rojo_reanuda_y_revierte(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    srv_antes = _huella_srv(banco)
    config_antes = _config(banco)
    esquema_antes = _huella_esquema(banco)
    assert _despliega(banco, banco["c2"]).returncode == 0
    _toque_config(banco, "ads_producto_meta_amazon_us", "live")
    assert _checklist(banco, banco["c2"]).returncode != 0
    antes_sha_previo = _cuenta_lineas(banco, "SHA_PREVIO")
    antes_codigo = _cuenta_lineas(banco, "CODIGO")
    antes_claves = _cuenta_lineas(banco, "CLAVE")
    assert antes_claves == 2

    reanuda = _despliega(banco, banco["c2"])
    assert reanuda.returncode == 0, reanuda.stdout + reanuda.stderr
    assert _cuenta_lineas(banco, "SHA_PREVIO") == antes_sha_previo, (
        "no debe agregar otra SHA_PREVIO"
    )
    assert _cuenta_lineas(banco, "CODIGO") == antes_codigo
    assert _cuenta_lineas(banco, "CLAVE") == antes_claves
    assert _valor_ultimo(banco, "ESTADO") == "desplegado"

    assert _rollback(banco, banco["c2"]).returncode != 0
    assert _valor_ultimo(banco, "ESTADO") == "revertido"
    assert _huella_srv(banco) == srv_antes
    assert _config(banco) == config_antes
    assert _huella_esquema(banco) == esquema_antes
    assert _sha_srv(banco) == banco["base_sha"]


# ---------------------------------------------------------------------------
# (n) con un rollback cortado a la mitad, desplegar sale 1 sin tocar nada y
#     otro rollback termina la reversa
# ---------------------------------------------------------------------------


@skip
def test_n_rollback_cortado_se_reanuda(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    srv_antes = _huella_srv(banco)
    config_antes = _config(banco)
    esquema_antes = _huella_esquema(banco)
    assert _despliega(banco, banco["c2"]).returncode == 0
    _toque_config(banco, "ads_producto_meta_amazon_mx", "live")
    assert _checklist(banco, banco["c2"]).returncode != 0

    (banco["falsos"] / "docker-lento").write_text("1", encoding="utf-8")
    env = _entorno(banco)
    env["MOTOR_SHA"] = banco["c2"]
    proceso = subprocess.Popen(
        ["bash", "tools/despliegue/rollback.sh"],
        cwd=str(banco["clon"]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    fin = time.monotonic() + 90
    while time.monotonic() < fin:
        log = banco["log"].read_text(encoding="utf-8")
        if "revirtiendo" in "\n".join(_ultimo(banco)) and "productos-modo" in log:
            break
        if proceso.poll() is not None:
            break
        time.sleep(0.5)
    proceso.kill()
    proceso.wait(timeout=30)
    (banco["falsos"] / "docker-lento").unlink()
    assert _valor_ultimo(banco, "ESTADO") == "revirtiendo", "\n".join(_ultimo(banco))
    cortado = _despliega(banco, banco["c2"])
    assert cortado.returncode == 1, "desplegar debe negarse mientras hay una reversa pendiente"
    completa = _rollback(banco, banco["c2"])
    assert _valor_ultimo(banco, "ESTADO") == "revertido", "\n".join(_ultimo(banco))
    assert _huella_srv(banco) == srv_antes
    assert _config(banco) == config_antes
    assert _huella_esquema(banco) == esquema_antes
    assert _sha_srv(banco) == banco["base_sha"]
    assert completa.returncode != 0


# ---------------------------------------------------------------------------
# (o) --ensayo con ORBIT_SIMULACION=1 deja iguales la huella de $SRV, del
#     esquema y de la config; no escribe ultimo.env ni sha; no registra
#     compose up ni sustituye archivos; y sale distinto de cero con ssh o psql
#     fallando o con una sonda vacia
# ---------------------------------------------------------------------------


@skip
def test_o_ensayo_simulado_no_escribe_nada(banco):
    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    srv_antes = _huella_srv(banco)
    esquema_antes = _huella_esquema(banco)
    config_antes = _config(banco)
    ensayo = subprocess.run(
        ["bash", "tools/despliegue/desplegar.sh", "--ensayo"],
        cwd=str(banco["clon"]),
        env=_entorno(banco, extra={"MOTOR_SHA": banco["c2"]}),
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert ensayo.returncode == 0, ensayo.stdout + ensayo.stderr
    assert not (banco["srv"] / "despliegue" / "ultimo.env").exists()
    assert _sha_srv(banco) == banco["base_sha"]
    assert _huella_srv(banco) == srv_antes
    assert _huella_esquema(banco) == esquema_antes
    assert _config(banco) == config_antes
    assert not _log_tiene(banco, "compose up")
    assert "aplicaria: migrations/0076_auto03_prueba_despliegue.sql" in ensayo.stdout

    (banco["falsos"] / "ssh-rc").write_text("1", encoding="utf-8")
    con_ssh_roto = subprocess.run(
        ["bash", "tools/despliegue/desplegar.sh", "--ensayo"],
        cwd=str(banco["clon"]),
        env=_entorno(banco, extra={"MOTOR_SHA": banco["c2"]}),
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert con_ssh_roto.returncode != 0, "el ensayo debio fallar con ssh roto"
    (banco["falsos"] / "ssh-rc").unlink()

    con_psql_roto = subprocess.run(
        ["bash", "tools/despliegue/desplegar.sh", "--ensayo"],
        cwd=str(banco["clon"]),
        env=_entorno(
            banco,
            extra={
                "MOTOR_SHA": banco["c2"],
                "ORBIT_SIM_DSN": "postgresql://orbit:orbit@127.0.0.1:54399/nada",
            },
        ),
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert con_psql_roto.returncode != 0, "el ensayo debio fallar con psql roto"

    con_sonda_vacia = subprocess.run(
        ["bash", "tools/despliegue/desplegar.sh", "--ensayo"],
        cwd=str(banco["clon"]),
        env=_entorno(banco, extra={"MOTOR_SHA": banco["c3"]}),
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert con_sonda_vacia.returncode != 0, "el ensayo debio fallar con la sonda vacia"
    assert "salio vacia" in con_sonda_vacia.stderr


# ---------------------------------------------------------------------------
# (p) con $SRV/despliegue/.lock tomado por otro proceso y AUTO03_ESPERA_S=1,
#     desplegar sale 75 dentro del plazo sin crear ni modificar ultimo.env, y
#     rollback.sh con el candado tomado no lee ni revierte hasta obtenerlo
# ---------------------------------------------------------------------------


@skip
def test_p_candado_ocupado_espera_y_sale_75(banco):
    import fcntl

    _limpia_falsos(banco)
    _siembra_srv(banco, banco["base_sha"])
    ruta_lock = banco["srv"] / "despliegue" / ".lock"
    ruta_lock.write_text("", encoding="utf-8")
    manejador = open(ruta_lock, "a+")  # noqa: SIM115 (el candado vive todo el escenario)
    try:
        fcntl.flock(manejador.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        inicio = time.monotonic()
        corrida = _despliega(banco, banco["c2"], espera="1")
        transcurrido = time.monotonic() - inicio
        assert corrida.returncode == 75, corrida.stdout + corrida.stderr
        assert transcurrido < 60, f"el plazo de 1s se disparo a {transcurrido:.0f}s"
        assert not (banco["srv"] / "despliegue" / "ultimo.env").exists(), (
            "con el candado ocupado no debe escribirse ultimo.env"
        )

        fcntl.flock(manejador.fileno(), fcntl.LOCK_UN)
        assert _despliega(banco, banco["c2"]).returncode == 0
        _toque_config(banco, "ads_producto_meta_amazon_mx", "live")
        assert _checklist(banco, banco["c2"]).returncode != 0
        antes = "\n".join(_ultimo(banco))

        fcntl.flock(manejador.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        inicio = time.monotonic()
        rollback = _rollback(banco, banco["c2"], espera="1")
        transcurrido = time.monotonic() - inicio
        assert rollback.returncode == 75, rollback.stdout + rollback.stderr
        assert transcurrido < 60
        assert "\n".join(_ultimo(banco)) == antes, (
            "el rollback no debe leer ni revertir sin el candado"
        )

        fcntl.flock(manejador.fileno(), fcntl.LOCK_UN)
        completa = _rollback(banco, banco["c2"])
        assert _valor_ultimo(banco, "ESTADO") == "revertido"
        assert completa.returncode != 0
        assert _config(banco) == {}
        assert _sha_srv(banco) == banco["base_sha"]
    finally:
        manejador.close()
