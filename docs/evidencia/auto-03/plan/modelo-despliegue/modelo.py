"""Modelo de referencia de la receta de despliegue de B.1/B.2 frente al ciclo del motor.

Reproduce lo que hace el motor (`goncloud-motor/motor/effects.py`, deploy): corre desplegar y
checklist; si alguno falla corre rollback; si el proceso muere a medio desplegar, al reanudar
corre primero el checklist y, si falla, repite desplegar con el mismo MOTOR_SHA.

El servidor es un directorio con `sha`, `ultimo.env`, el codigo (`app.txt`), la config y los
respaldos. Los commits son un repo git falso E -> Gm -> Fm -> F2 donde Fm enciende la meta.
Sale 0 si todos los escenarios cumplen sus aserciones. Sin dependencias: `python3 modelo.py`.
"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="modelo-despliegue-"))
REPO, SRV = T / "repo", T / "srv"


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, check=False)


def crea_repo():
    REPO.mkdir()
    git("init", "-q"); git("config", "user.email", "m@x"); git("config", "user.name", "m")
    for tag, meta in [("E", "shadow"), ("Gm", "shadow"), ("Fm", "live"), ("F2", "live")]:
        (REPO / "interruptores.env").write_text(
            f"ads_producto_meta_amazon_mx={meta}\nads_producto_meta_amazon_us={meta}\n")
        (REPO / "app.txt").write_text(tag + "\n")
        git("add", "-A"); git("commit", "-q", "-m", tag); git("tag", tag)


def rev(t): return git("rev-parse", t).stdout.strip()
def contiene(desc, anc): return bool(desc) and git("merge-base", "--is-ancestor", anc, desc).returncode == 0
def inter(sha):
    if not sha: return {}
    return dict(l.split("=", 1) for l in git("show", f"{sha}:interruptores.env").stdout.splitlines())
def cfg(): return json.loads((SRV / "config.json").read_text())
def set_cfg(k, v):
    c = cfg()
    if c.get(k, "") == v: return            # C.6: el valor vigente no se reescribe
    c.pop(k, None) if v == "" else c.__setitem__(k, v)
    (SRV / "config.json").write_text(json.dumps(c))
def ult(): p = SRV / "ultimo.env"; return p.read_text().splitlines() if p.exists() else []
def primero(key, L):
    v = [l.split("=", 1)[1] for l in L if l.startswith(key + "=")]; return v[0] if v else None
def ultimo(key, L):
    v = [l.split("=", 1)[1] for l in L if l.startswith(key + "=")]; return v[-1] if v else None
def anota(l): open(SRV / "ultimo.env", "a").write(l + "\n")
def sha_srv(): p = SRV / "sha"; return p.read_text().strip() if p.exists() else ""


class Corte(Exception): pass
TERMINALES = ("verificado", "sin_cambios", "abortado_sin_cambios", "revertido")
CORTE, FALLA_HEALTH, OCUPADO = {}, set(), set()


def corta(sha, paso):
    if CORTE.get(sha) == paso: CORTE.pop(sha); raise Corte(paso)


def desplegar(sha):
    """B.1. (0) flock, ultimo.env y SHA_PREVIO; (1) guardas; (2) monotonia; (3) respaldo;
    (5) codigo y sha; (6) claves frente a SHA_PREVIO."""
    L = ult()
    cambios = any(l.startswith(("CODIGO=", "MIGRACION=", "CLAVE=")) for l in L)
    if ultimo("ESTADO", L) == "revirtiendo" or (
            cambios and ultimo("MOTOR_SHA", L) != sha and ultimo("ESTADO", L) not in TERMINALES):
        return 1                             # reversa pendiente: no toca nada, el rollback la termina
    reanuda = ultimo("MOTOR_SHA", L) == sha and ultimo("ESTADO", L) not in TERMINALES
    if sha in OCUPADO:                       # (1) guardas agotadas
        if not reanuda:
            (SRV / "ultimo.env").write_text(f"MOTOR_SHA={sha}\nESTADO=guardas\nSHA_PREVIO={sha_srv()}\n")
        if any(l.startswith(("CODIGO=", "MIGRACION=", "CLAVE=")) for l in ult()):
            anota("ESTADO=guardas_con_cambios"); return 75   # el rollback deshace lo anotado
        anota("ESTADO=abortado_sin_cambios"); return 75
    if not reanuda:
        (SRV / "ultimo.env").write_text(f"MOTOR_SHA={sha}\nESTADO=guardas\nSHA_PREVIO={sha_srv()}\n")
    L = ult(); prev = primero("SHA_PREVIO", L)          # vale la de la primera corrida
    if prev and contiene(prev, sha):
        anota("ESTADO=sin_cambios"); return 0
    if primero("CODIGO", L) is None:                     # (3) una sola vez, completo
        n = len(list(SRV.glob("predeploy-*"))) + 1
        tmp = SRV / f"predeploy-{n}.parcial"; tmp.mkdir()
        corta(sha, "3a")                                 # muere a media copia
        shutil.copy(SRV / "app.txt", tmp / "app.txt")
        tmp.rename(SRV / f"predeploy-{n}"); anota(f"CODIGO=predeploy-{n}")
    corta(sha, 3)
    (SRV / "app.txt").write_text(git("show", f"{sha}:app.txt").stdout); (SRV / "sha").write_text(sha)
    corta(sha, 5)
    a, b = inter(prev), inter(sha)
    ya = {l.split("=", 1)[1].split(":")[0] for l in ult() if l.startswith("CLAVE=")}
    for i, k in enumerate(sorted(set(a) | set(b))):
        if a.get(k, "") != b.get(k, ""):
            if k not in ya: anota(f"CLAVE={k}:{cfg().get(k, '')}")   # aunque ya traiga el valor
            set_cfg(k, b.get(k, ""))
            if i == 0: corta(sha, 6)
    anota("ESTADO=desplegado"); return 0


def checklist(sha):
    """B.2 checklist."""
    L = ult(); prev = primero("SHA_PREVIO", L)
    if not contiene(sha_srv(), sha) or sha in FALLA_HEALTH: return 1
    if not (prev and contiene(prev, sha)):                # monotonia: no se exigen claves
        claves = {l.split("=", 1)[1].split(":")[0] for l in L if l.startswith("CLAVE=")}
        a, b = inter(prev), inter(sha)
        if any(a.get(k, "") != b.get(k, "") and k not in claves for k in set(a) | set(b)): return 1
        if any(cfg().get(k, "") != b.get(k, "") for k in claves): return 1
    if ultimo("MOTOR_SHA", L) == sha and ultimo("ESTADO", L) != "verificado": anota("ESTADO=verificado")
    return 0


def rollback(sha):
    """B.2 rollback: solo lo anotado por este MOTOR_SHA; primera CODIGO y primera CLAVE por clave."""
    L = ult()
    if (ultimo("MOTOR_SHA", L) != sha or ultimo("ESTADO", L) in ("abortado_sin_cambios", "revertido")
            or not any(l.startswith(("CLAVE=", "CODIGO=", "MIGRACION=")) for l in L)):
        return 0
    if ultimo("ESTADO", L) != "revirtiendo": anota("ESTADO=revirtiendo")
    vistos = set()
    for k, previo in (l.split("=", 1)[1].split(":", 1) for l in L if l.startswith("CLAVE=")):
        if k in vistos: continue
        vistos.add(k)
        if cfg().get(k, "") != previo: set_cfg(k, previo)
        corta(sha, "rb")                                 # muere a media reversa
    st = primero("CODIGO", L)
    if st: shutil.copy(SRV / st / "app.txt", SRV / "app.txt")
    prev = primero("SHA_PREVIO", L)
    (SRV / "sha").write_text(prev) if prev else (SRV / "sha").unlink(missing_ok=True)
    anota("ESTADO=revertido"); return 1


INICIADOS = set()
def motor(tag):
    sha = rev(tag)
    if sha in INICIADOS and checklist(sha) == 0: return "ya_estaba"
    INICIADOS.add(sha)
    try:
        rc = desplegar(sha)
    except Corte:
        return "cortado"
    if rc == 0 and checklist(sha) == 0: return "ok"
    try:
        corta(sha, "pre_rb"); rollback(sha)               # el motor puede morir antes o durante
    except Corte:
        return "cortado"
    return f"fallo_{rc}"


def reset(config=None):
    shutil.rmtree(SRV, ignore_errors=True); SRV.mkdir()
    (SRV / "app.txt").write_text("E\n"); (SRV / "sha").write_text(rev("E"))
    (SRV / "config.json").write_text(json.dumps(config if config is not None else inter(rev("E"))))
    for s in (CORTE, FALLA_HEALTH, OCUPADO, INICIADOS): s.clear()


def estado(): return (sha_srv(), (SRV / "app.txt").read_text().strip(), cfg())
LIVE = {"ads_producto_meta_amazon_mx": "live", "ads_producto_meta_amazon_us": "live"}
SHADOW = {"ads_producto_meta_amazon_mx": "shadow", "ads_producto_meta_amazon_us": "shadow"}


def escenarios():
    r = {}
    reset(); inicial = estado()
    FALLA_HEALTH.add(rev("Fm")); motor("Fm")
    r["rollback deja todo como antes"] = estado() == inicial
    FALLA_HEALTH.clear(); motor("F2")
    r["tras rollback, el SHA siguiente vuelve a aplicar las claves"] = estado() == (rev("F2"), "F2", LIVE)

    reset(); motor("Fm"); res = motor("Gm")
    r["fuera de orden: no toca claves y el checklist pasa"] = res == "ok" and estado() == (rev("Fm"), "Fm", LIVE)

    reset(); CORTE[rev("Fm")] = 5; motor("Fm"); res = motor("Fm")
    r["corte tras (5) y reanudacion: aplica las claves"] = res == "ok" and estado() == (rev("Fm"), "Fm", LIVE)

    reset(); inicial = estado(); CORTE[rev("Fm")] = 5; motor("Fm"); FALLA_HEALTH.add(rev("Fm")); motor("Fm")
    r["corte tras (5) y rollback: codigo, sha y config como antes"] = estado() == inicial

    reset(); inicial = estado(); CORTE[rev("Fm")] = 6; motor("Fm"); FALLA_HEALTH.add(rev("Fm")); motor("Fm")
    r["corte tras la primera clave y rollback: ninguna meta queda en vivo"] = estado() == inicial

    reset(config=LIVE); res = motor("Fm")
    r["meta encendida a mano antes: el checklist pasa"] = res == "ok" and estado() == (rev("Fm"), "Fm", LIVE)

    reset(); motor("Fm"); set_cfg("ads_producto_meta_amazon_mx", "off"); res = motor("F2")
    r["apagado manual: se respeta"] = res == "ok" and cfg()["ads_producto_meta_amazon_mx"] == "off"

    reset(); inicial = estado(); OCUPADO.add(rev("Fm")); res = motor("Fm")
    r["guarda ocupada: sale 75 y no cambia nada"] = res == "fallo_75" and estado() == inicial

    reset(); inicial = estado(); CORTE[rev("Fm")] = 6; motor("Fm"); OCUPADO.add(rev("Fm")); res = motor("Fm")
    r["corte tras la primera clave, reanudacion con guarda ocupada: rollback restaura todo"] = (
        res == "fallo_75" and estado() == inicial)

    reset(); inicial = estado(); CORTE[rev("Fm")] = "3a"; motor("Fm"); FALLA_HEALTH.add(rev("Fm"))
    try:
        motor("Fm"); ok = estado() == inicial
    except (FileNotFoundError, OSError):
        ok = False
    r["corte a media copia del respaldo y rollback: restaura el codigo completo"] = ok

    reset(); inicial = estado(); FALLA_HEALTH.add(rev("Fm")); CORTE[rev("Fm")] = "pre_rb"; motor("Fm")
    motor("Fm")
    r["corte entre checklist rojo y rollback: la reanudacion conserva el diario y revierte"] = estado() == inicial

    reset(); inicial = estado(); FALLA_HEALTH.add(rev("Fm")); CORTE[rev("Fm")] = "rb"; motor("Fm")
    medio = estado(); res = motor("Fm")
    r["rollback cortado a la mitad: desplegar no toca nada y el rollback termina"] = (
        medio != inicial and res == "fallo_1" and estado() == inicial)
    return r


if __name__ == "__main__":
    crea_repo()
    r = escenarios()
    for k, ok in r.items(): print(("ok    " if ok else "FALLA ") + k)
    shutil.rmtree(T, ignore_errors=True)
    sys.exit(0 if all(r.values()) else 1)
