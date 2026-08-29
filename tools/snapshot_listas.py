#!/usr/bin/env python3
"""Snapshot READ-ONLY de las listas v3 de Amazon Ads (ORBIT 05 preflight 1.3).

Decision sellada 3 del preflight (regla 10: conciliar contra Amazon, no
contra consistencia interna): el snapshot de listas del backup pre-cutover
se produce con ESTE tool del repo, no con codigo inline (4.4 lo corrio inline
y quedo declarado como hueco del runbook; DEPLOY.md §"Backup pre-cutover"
cita este tool desde ahora). Cero mutaciones: las unicas llamadas son POST
de LIST v3 por el cliente de LECTURA allowlist (app.ads.client.list_objects);
jamas importa app.ads.write ni toca la base (cero conexiones).

QUE PRODUCE: por cada perfil aceptado (app.ads.structure.perfiles_aceptados,
la MISMA fuente del gate seller/pais/moneda), las TRES listas read-only:
/sp/keywords/list, /sp/negativeKeywords/list y /sp/targets/list, con la
paginacion nextToken completa y su guard de totalResults (listar_todo).
Items VERBATIM de Amazon agrupados por str(campaignId); un item sin
campaignId va a la clave declarada "sin_campaignId" (jamas un id inventado,
regla 3). Salida:

  {"generado_utc": <ISO UTC>,
   "plataformas": {<platform>: {"keywords": {campaignId: [items]},
                                "negativeKeywords": {...},
                                "targetingClauses": {...}}},
   "resumen": {<platform>: {"keywords": N, "negativeKeywords": N,
                            "targetingClauses": N}}}

OJO: negativeKeywords NO tiene espejo en ad_entity (el cache no lo guarda):
al comparar contra cache la fila queda con cache=None — declarado, no drop
silencioso (ver comparar_con_cache).

USO:
  python tools/snapshot_listas.py --out <dir>
      # escribe <dir>/listas_por_plataforma.json (umask 077 -> dir 700, archivo 600)
  python tools/snapshot_listas.py --solo-conteos
      # imprime el resumen por stdout, NO escribe archivo
  [--platform amazon_us|amazon_mx]  # filtra a ese perfil (default: todos los aceptados)

Sin --out y sin --solo-conteos -> error de uso (exit != 0): el tool siempre
o imprime o escribe, explicito. Ambos flags a la vez TAMBIEN es error de uso
(excluyentes, hallazgo grok r1: jamas un exito sin archivo que parezca
backup). Sin ORBIT_SECRETS_DIR valido -> fail-closed
sin abrir red. Errores de API/estructura/escritura -> mensaje por stderr
(scrub) y exit != 0. --platform que no deja ningun perfil aceptado ->
exit != 0 (jamas un exito vacio).

RUNBOOK DEL CONTENEDOR (el tool no va en la imagen y el contenedor corre
non-root con /app no escribible — patron DEPLOY.md §11d / smoke_apply; todo
desde el host del repo, donde viven los secrets que ya usa orbit-app-1):
  cat tools/snapshot_listas.py | ssh goncloud 'docker exec -i orbit-app-1 \
    sh -c "cat > /tmp/snapshot_listas.py"'
  set -o pipefail   # LOCAL: el | tee corre aqui, no dentro del ssh
  ssh goncloud 'docker exec orbit-app-1 sh -c \
    "PYTHONPATH=/app python /tmp/snapshot_listas.py --out /tmp/listas"' \
    2>&1 | tee out/snapshot-listas-<fecha>.log
  rc=$?
  if [ "$rc" -eq 0 ]; then   # cp SOLO con snapshot completo (grok r1)
    # El rc del cp MANDA (hallazgo Greptile PR #48): sin esto, un mkdir o un
    # docker cp fallido dejaba rc=0 y el runbook borraba la copia del
    # contenedor declarando que el artefacto estaba en $D cuando no estaba.
    ssh goncloud "mkdir -p '$D/listas_amazon'" || rc=$?
    if [ "$rc" -eq 0 ]; then
      ssh goncloud "docker cp orbit-app-1:/tmp/listas/listas_por_plataforma.json \
        '$D/listas_amazon/'" || rc=$?   # cp crea un ARCHIVO llamado listas_amazon
    fi
  fi   # $D = backups/precutover_<tag>/ del host
  ssh goncloud 'docker exec orbit-app-1 sh -c "rm -f /tmp/snapshot_listas.py \
    && rm -rf /tmp/listas"'   # limpieza SIEMPRE: /tmp no es el backup
  echo "snapshot rc=$rc"   # 0 = snapshot completo y copiado a $D; otro = NO seguir
(--solo-conteos antes de la corrida completa para ver los totales sin
escribir nada.) Verificacion del JSON: DEPLOY.md §"Backup pre-cutover"
(totales por plataforma/recurso = ad_entity, incl. ARCHIVED;
negativeKeywords solo conteo, sin espejo en cache).

PREREQUISITO DE IMAGEN: la receta de arriba (solo el tool en /tmp con
PYTHONPATH=/app) exige que la imagen incluya ESTE commit (app.ads.structure
con listar_todo publica y PATH_NEGATIVE_KEYWORDS). Con una imagen anterior,
monta el ARBOL del commit en /tmp y corralo desde ahi — el bootstrap del
tool pone su propio arbol primero en sys.path, sin mezclar modulos:
  git archive HEAD app tools/snapshot_listas.py | ssh goncloud \
    'docker exec -i orbit-app-1 sh -c "rm -rf /tmp/snapshot_src && \
     mkdir -p /tmp/snapshot_src && tar -x -C /tmp/snapshot_src"'
  ssh goncloud 'docker exec orbit-app-1 python \
    /tmp/snapshot_src/tools/snapshot_listas.py --solo-conteos'
(variante usada en la corrida real del 2026-08-28 contra la imagen vigente,
anterior al commit; evidencia out/orbit-05-preflight-1-3-2026-08-28.md).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

# Bootstrap del repo: el tool corre como script (python tools/snapshot_listas.py)
# y app/ no esta en sys.path desde tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ads.client import AdsClient  # noqa: E402
from app.ads.config import AdsCredentials  # noqa: E402
from app.ads.structure import (  # noqa: E402
    PATH_KEYWORDS,
    PATH_NEGATIVE_KEYWORDS,
    PATH_TARGETS,
    listar_todo,
    perfiles_aceptados,
)
from app.redaction import scrub  # noqa: E402

if TYPE_CHECKING:
    from app.ads.structure import PerfilAds

ARCHIVO = "listas_por_plataforma.json"

# Clave declarada para items sin campaignId (regla 3: jamas un id inventado).
SIN_CAMPAIGN_ID = "sin_campaignId"

# (path, contenedor) en el ORDEN de la corrida. Los contenedores son los
# REALES verificados: structure.py para keywords/targetingClauses y
# negativeKeywords por regla 8 en vivo (2026-08-25,
# out/regla8-negkeywords.log).
_RECURSOS: tuple[tuple[str, str], ...] = (
    (PATH_KEYWORDS, "keywords"),
    (PATH_NEGATIVE_KEYWORDS, "negativeKeywords"),
    (PATH_TARGETS, "targetingClauses"),
)


# ---------------------------------------------------------------------------
# Partes puras (sin red, sin base): agrupado, conteos, comparacion
# ---------------------------------------------------------------------------


def agrupa_por_campana(items: list[dict]) -> dict[str, list[dict]]:
    """Agrupa items VERBATIM por str(campaignId), conservando el orden.

    Un item sin campaignId va a la clave declarada SIN_CAMPAIGN_ID (regla 3:
    jamas un id inventado para que el conteo no pierda filas).
    """
    grupos: dict[str, list[dict]] = {}
    for item in items:
        campaign_id = item.get("campaignId")
        clave = SIN_CAMPAIGN_ID if campaign_id is None else str(campaign_id)
        grupos.setdefault(clave, []).append(item)
    return grupos


def conteos(
    plataformas: dict[str, dict[str, dict[str, list[dict]]]],
) -> dict[tuple[str, str], int]:
    """Total de items por (plataforma, recurso) — la forma que consume
    comparar_con_cache por AMBOS lados (snapshot y cache)."""
    return {
        (plataforma, recurso): sum(len(grupo) for grupo in grupos.values())
        for plataforma, recursos in plataformas.items()
        for recurso, grupos in recursos.items()
    }


def comparar_con_cache(
    conteos_snapshot: dict[tuple[str, str], int],
    conteos_cache: dict[tuple[str, str], int],
) -> list[dict]:
    """Diferencia CON SIGNO (snapshot - cache), una FILA por clave de la
    UNION de ambas dicts — no un bool: la fila dice cuanto y hacia donde.

    Clave solo en snapshot -> cache=None y diferencia=snapshot; solo en
    cache -> snapshot=None y diferencia=-cache. negativeKeywords NO tiene
    espejo en ad_entity: el caller no la pasa en conteos_cache y la fila
    queda con cache=None (declarado, no drop silencioso). El mapeo
    contenedor->kind de ad_entity lo aplica el caller: keywords->keyword,
    targetingClauses->product_target (asi lo hizo la conciliacion real del
    2026-08-28, evidencia out/concilia-cache-20260828.log). Orden
    determinista por (plataforma, recurso)."""
    filas: list[dict] = []
    for plataforma, recurso in sorted(set(conteos_snapshot) | set(conteos_cache)):
        n_snapshot = conteos_snapshot.get((plataforma, recurso))
        n_cache = conteos_cache.get((plataforma, recurso))
        filas.append(
            {
                "plataforma": plataforma,
                "recurso": recurso,
                "snapshot": n_snapshot,
                "cache": n_cache,
                "diferencia": (n_snapshot or 0) - (n_cache or 0),
            }
        )
    return filas


def snapshot(perfiles: list[PerfilAds], client: AdsClient) -> dict:
    """Corre las TRES listas read-only por perfil aceptado y arma el
    snapshot (items verbatim agrupados por campana + resumen de conteos)."""
    plataformas: dict[str, dict[str, dict[str, list[dict]]]] = {}
    resumen: dict[str, dict[str, int]] = {}
    for perfil in perfiles:
        if not perfil.aceptado:
            continue
        # Perfil aceptado implica profile_id/platform fijados (structure.py).
        recursos = {
            recurso: agrupa_por_campana(listar_todo(client, path, profile_id=perfil.profile_id))
            for path, recurso in _RECURSOS
        }
        plataformas[perfil.platform] = recursos
        resumen[perfil.platform] = {
            recurso: sum(len(grupo) for grupo in grupos.values())
            for recurso, grupos in recursos.items()
        }
    return {
        "generado_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "plataformas": plataformas,
        "resumen": resumen,
    }


# ---------------------------------------------------------------------------
# CLI (patron smoke_apply.py: cero side effects al import)
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python tools/snapshot_listas.py",
        description=(
            "Snapshot READ-ONLY de las listas v3 de Amazon Ads (keywords/"
            "negativeKeywords/targetingClauses) agrupadas por campana, para "
            "el backup pre-cutover (ORBIT 05 preflight 1.3). Cero mutaciones."
        ),
    )
    # Excluyentes (hallazgo grok r1): ambos a la vez produce un "exito" sin
    # archivo que el operador del backup podria tomar por backup completo.
    grupo_destino = parser.add_mutually_exclusive_group()
    grupo_destino.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="DIR",
        help=(f"directorio de salida (lo crea si falta, umask 077); escribe {ARCHIVO}"),
    )
    grupo_destino.add_argument(
        "--solo-conteos",
        dest="solo_conteos",
        action="store_true",
        help="imprime el resumen de conteos por stdout y NO escribe archivo",
    )
    parser.add_argument(
        "--platform",
        choices=("amazon_us", "amazon_mx"),
        default=None,
        help="filtra a esa plataforma (default: todos los perfiles aceptados)",
    )
    return parser.parse_args(argv)


def _imprimir_resumen(snap: dict) -> None:
    print(f"snapshot de listas amazon ads (generado_utc={snap['generado_utc']})")
    for plataforma in sorted(snap["resumen"]):
        for recurso in sorted(snap["resumen"][plataforma]):
            print(f"  {plataforma}/{recurso}: {snap['resumen'][plataforma][recurso]}")


def _escribir_snapshot(snap: dict, out_dir: Path) -> int:
    # umask 077 SOLO durante la creacion y RESTAURADA al salir (hallazgo qwen
    # r2): 700/600 sin importar la umask del caller (el backup pre-cutover
    # exige 600; DEPLOY.md), sin contagiar la umask del proceso (tests
    # in-process).
    vieja = os.umask(0o077)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        # Un out_dir PREEXISTENTE conserva sus permisos: `exist_ok=True` no
        # aplica la umask (hallazgo CodeRabbit PR #48). El tool promete 700,
        # asi que lo IMPONE — y si el dir es de otro dueno, fail-closed en vez
        # de escribir el backup en un directorio ajeno.
        # Un out_dir que es SYMLINK se rechaza antes de mirar modos: `stat` y
        # `chmod` seguirian el enlace y le cambiarian los permisos al
        # directorio APUNTADO (hallazgo Greptile PR #48 sobre este mismo
        # endurecimiento). El backup no toca directorios ajenos: fail-closed.
        if out_dir.is_symlink():
            raise PermissionError(f"{out_dir} es un symlink: el snapshot exige un directorio real")
        info = out_dir.stat()
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PermissionError(f"{out_dir} no es del usuario del proceso (uid {info.st_uid})")
        if stat.S_IMODE(info.st_mode) != 0o700:
            out_dir.chmod(0o700)
        destino = out_dir / ARCHIVO
        # Escritura ATOMICA (qwen r2) con temporal EXCLUSIVO y no predecible
        # (hallazgos Greptile + CodeRabbit PR #48): `mkstemp` crea con
        # O_CREAT|O_EXCL y modo 600, asi que no sigue un symlink plantado ni
        # trunca un archivo ajeno, y dos corridas concurrentes no comparten
        # temporal. `os.replace` publica en un solo paso: una re-corrida
        # jamas deja un JSON a medias en el destino del backup. La
        # re-escritura PISA el snapshot previo (la ultima corrida gana;
        # declarado: la receta apunta cada flip a un $D fresco).
        fd, temporal = tempfile.mkstemp(dir=out_dir, prefix=".listas-", suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(snap, ensure_ascii=False, indent=2) + "\n")
            os.replace(temporal, destino)
        except BaseException:
            # El temporal jamas queda huerfano si algo revienta a mitad.
            with contextlib.suppress(OSError):
                os.unlink(temporal)
            raise
    finally:
        os.umask(vieja)
    print(f"snapshot escrito: {destino}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.out is None and not args.solo_conteos:
        print(
            "error de uso: falta el destino — pasa --out <dir> (escribe "
            f"{ARCHIVO}) o --solo-conteos (imprime el resumen por stdout); "
            "el tool siempre o imprime o escribe, explicito (fail-closed)",
            file=sys.stderr,
        )
        return 2
    try:
        credentials = AdsCredentials.from_secrets_dir()
    except Exception as exc:
        print(
            "credenciales no disponibles (ORBIT_SECRETS_DIR ausente/invalido): "
            f"{scrub(str(exc))} — fail-closed, cero llamadas de red",
            file=sys.stderr,
        )
        return 2
    try:
        cliente = AdsClient(credentials)
        perfiles = perfiles_aceptados(cliente)
        if args.platform is not None:
            perfiles = [p for p in perfiles if p.platform == args.platform]
        if not perfiles:
            print(
                "sin perfiles aceptados para "
                f"{args.platform or 'ninguna plataforma'}: no se produce un "
                "snapshot vacio (fail-closed)",
                file=sys.stderr,
            )
            return 2
        snap = snapshot(perfiles, cliente)
    except Exception as exc:
        print(f"snapshot de listas fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    if args.solo_conteos:
        _imprimir_resumen(snap)
        return 0
    try:
        return _escribir_snapshot(snap, args.out)
    except OSError as exc:
        # Mismo trato que el resto del tool (hallazgo reviewer 1.3): mensaje
        # a stderr con scrub y exit controlado, jamas un traceback crudo.
        print(f"no se pudo escribir el snapshot: {scrub(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
