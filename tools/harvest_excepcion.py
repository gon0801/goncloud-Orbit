"""Migracion a `harvest_excepcion` y limpieza de terna (FABRICA 02, A.5).

Solo Postgres y solo `app_admin` (unico DSN: `ORBIT_DSN_ADMIN`), cero
Amazon: dos modos excluyentes, dry-run por defecto y la ceremonia sellada
(`--acepto-mutacion-real --esperado N --huella H --go "<literal>"`).

- `--migrar`: mete UNA campana sin grupo en `harvest_excepcion` con un
  destino resuelto contra `ad_entity` (kind, `parent_id`, plataforma);
  texto libre rechazado, idempotente, jamas pisa una excepcion existente.
- `--limpiar-terna`: pone a NULL la terna de los goals de campana de UN
  grupo, solo por `goals_write.edita_goal` (este tool jamas trae SQL de
  escritura de goals: despacha, no duplica). El go es POR GOAL,
  REANUDABLE: cada `edita_goal` confirma su llamada (sin transaccion
  global a proposito); un fallo a mitad aborta con el `goal_id`, lo
  limpio queda limpio (bid-solo en grupo es valido y resuelve por
  grupo) y re-correr con ceremonia nueva termina el resto.

Una campana o un grupo por corrida y por `go`. Todo lo que cambia queda
legible en el dry-run antes, y leido de vuelta despues.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sys

from app.db import OrbitDbError, connect
from app.goals_write import GoalInexistente, GoalInvalido, edita_goal
from app.optimizer.harvest_destino import (
    RESUELTO_EXCEPCION,
    RESUELTO_GRUPO,
    DestinoHarvest,
    resolver_destino,
    simula_excepcion,
)


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_admin() -> str:
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        raise Abortar("ORBIT_DSN_ADMIN no esta en el entorno (unico DSN del tool)")
    return dsn


_PLATAFORMAS = ("amazon_us", "amazon_mx")

_SQL_CAMPANA = """
SELECT id, external_id FROM ad_entity
 WHERE platform = %s::platform AND kind = 'campaign' AND external_id = %s
"""

_SQL_ROL = """
SELECT grupo_id, rol::text FROM campana_grupo_rol WHERE ad_entity_id = %s
"""

_SQL_AD_GROUP = """
SELECT id, external_id, parent_id FROM ad_entity
 WHERE platform = %s::platform AND kind = 'ad_group' AND external_id = %s
"""

_SQL_PADRE_EXT = "SELECT external_id FROM ad_entity WHERE id = %s"

_SQL_EXCEPCION = """
SELECT destino_campaign_external, destino_ad_group_external
  FROM harvest_excepcion WHERE ad_entity_id = %s
"""

_SQL_INSERTA_EXCEPCION = """
INSERT INTO harvest_excepcion
  (ad_entity_id, destino_campaign_external, destino_ad_group_external, go_literal)
  VALUES (%s, %s, %s, %s)
"""


def _linea_resolucion(cual: str, res) -> str:
    """`resolucion_hoy/despues`: resuelto_por + motivo (+ bid), o
    `skip motivo=...` (el plan que ve el dueno antes del go)."""
    if isinstance(res, DestinoHarvest):
        if res.bid is None:
            return f"resolucion_{cual}: {res.resuelto_por} motivo={res.motivo} sin bid"
        return (
            f"resolucion_{cual}: {res.resuelto_por} motivo={res.motivo} bid={res.bid} {res.moneda}"
        )
    return f"resolucion_{cual}: skip motivo={res.motivo}"


def _huella_migrar(platform: str, origen_ext: str, camp_ext: str, ag_ext: str) -> str:
    base = f"{platform}:{origen_ext}>{camp_ext}/{ag_ext}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _cmd_migrar(conn, args) -> int:
    """Valida (fail-closed, cero escritura) e imprime el plan; sin
    `--acepto-mutacion-real` ahi termina (dry-run)."""
    if (
        not args.plataforma
        or not args.campana
        or not args.destino_campana
        or not args.destino_ad_group
    ):
        raise Abortar(
            "--migrar exige --plataforma, --campana, --destino-campana y --destino-ad-group"
        )
    platform = args.plataforma
    if platform not in _PLATAFORMAS:
        raise Abortar(f"plataforma {platform!r}: solo amazon_us o amazon_mx")
    origen = conn.execute(_SQL_CAMPANA, (platform, args.campana)).fetchone()
    if origen is None:
        raise Abortar(
            f"campana origen {platform}:{args.campana} no existe en ad_entity (kind campaign)"
        )
    origen_id, origen_ext = origen
    rol = conn.execute(_SQL_ROL, (origen_id,)).fetchone()
    if rol is not None:
        grupo_id, rol_nombre = rol
        raise Abortar(
            f"campana {origen_ext} esta en el grupo {grupo_id} con rol {rol_nombre}:"
            " resuelve por grupo, no por excepcion"
        )
    camp_dest = conn.execute(_SQL_CAMPANA, (platform, args.destino_campana)).fetchone()
    if camp_dest is None:
        raise Abortar(f"campana destino {platform}:{args.destino_campana} no existe en ad_entity")
    camp_dest_id, camp_dest_ext = camp_dest
    ag_dest = conn.execute(_SQL_AD_GROUP, (platform, args.destino_ad_group)).fetchone()
    if ag_dest is None:
        raise Abortar(f"ad group destino {platform}:{args.destino_ad_group} no existe en ad_entity")
    ag_dest_id, ag_dest_ext, padre_id = ag_dest
    if padre_id != camp_dest_id:
        padre_ext = conn.execute(_SQL_PADRE_EXT, (padre_id,)).fetchone()[0]
        raise Abortar(
            f"ad group destino {ag_dest_ext} es hijo de {padre_ext}, no de {camp_dest_ext}"
        )
    previa = conn.execute(_SQL_EXCEPCION, (origen_id,)).fetchone()
    if previa is not None:
        if tuple(previa) == (camp_dest_ext, ag_dest_ext):
            print("ya migrada, nada que hacer")
            return 0
        raise Abortar(
            f"excepcion congelada para {origen_ext}: ya apunta a"
            f" {previa[0]}/{previa[1]} («que queden asi ya»); cambiarla no es tarea de este tool"
        )
    hoy = resolver_destino(conn, platform, origen_id)
    despues = simula_excepcion(conn, platform, origen_id, (camp_dest_ext, ag_dest_ext))
    huella = _huella_migrar(platform, origen_ext, camp_dest_ext, ag_dest_ext)
    print(f"origen: platform={platform} external_id={origen_ext} ad_entity_id={origen_id}")
    print(
        f"destino: campaign_external={camp_dest_ext} ad_entity_id={camp_dest_id}"
        f" ad_group_external={ag_dest_ext} ad_entity_id={ag_dest_id}"
    )
    print(_linea_resolucion("hoy", hoy))
    print(_linea_resolucion("despues", despues))
    if isinstance(despues, DestinoHarvest) and despues.bid is None:
        print(
            "aviso: la campana no tiene harvest_default_bid (ni propio ni de plataforma);"
            " el motor saltara con harvest_sin_config hasta que exista bid"
        )
    print(f"huella: {huella}")
    if not args.acepto_mutacion_real:
        return 0
    # Ceremonia sellada (orden de reversa_harvest: esperado -> go ->
    # huella; `--go` solo-espacios cuenta como ausente, mas estricto que
    # reversa). `--esperado` es 1: UNA campana por corrida y por go.
    if args.esperado is None:
        raise Abortar("mutacion real exige --esperado N (anti-typo del plan)")
    if not args.go or not args.go.strip():
        raise Abortar("mutacion real exige --go con el literal del dueno (no vacio)")
    if args.esperado != 1:
        raise Abortar(
            f"--esperado {args.esperado} != 1 candidata:"
            " --migrar mueve UNA campana por go, se re-autoriza con el dueno"
        )
    if not args.huella:
        raise Abortar("mutacion real exige --huella del dry-run (autorizacion por conjunto)")
    if args.huella != huella:
        raise Abortar(f"--huella {args.huella} != huella {huella}: el conjunto cambio")
    conn.execute(_SQL_INSERTA_EXCEPCION, (origen_id, camp_dest_ext, ag_dest_ext, args.go))
    conn.commit()
    leida = conn.execute(_SQL_EXCEPCION, (origen_id,)).fetchone()
    res = resolver_destino(conn, platform, origen_id)
    if (
        leida is None
        or tuple(leida) != (camp_dest_ext, ag_dest_ext)
        or not isinstance(res, DestinoHarvest)
        or res.resuelto_por != RESUELTO_EXCEPCION
        or (res.campaign_external, res.ad_group_external) != (camp_dest_ext, ag_dest_ext)
    ):
        raise Abortar(
            f"readback tras el go no da excepcion con {camp_dest_ext}/{ag_dest_ext}:"
            f" fila={leida} resolucion={res} (commit hecho, no se revierte: se declara)"
        )
    print(f"migrada: origen={origen_ext} destino={camp_dest_ext}/{ag_dest_ext}")
    print(
        f"readback: excepcion {res.campaign_external}/{res.ad_group_external}"
        f" resuelto_por={res.resuelto_por}"
    )
    return 0


_SQL_GRUPO = """
SELECT id, platform::text, tipo_producto FROM campana_grupo WHERE id = %s
"""

_SQL_EXACTA = """
SELECT ce.external_id, ae.external_id
  FROM campana_grupo_rol re
  JOIN ad_entity ce ON ce.id = re.ad_entity_id
  JOIN ad_entity ae ON ae.id = re.ad_group_ad_entity_id
 WHERE re.grupo_id = %s AND re.rol = 'category_exact'
"""

_SQL_GOALS_GRUPO = """
SELECT r.ad_entity_id, r.rol::text, g.id,
       g.harvest_campaign_id, g.harvest_ad_group_id, g.harvest_default_bid
  FROM campana_grupo_rol r
  LEFT JOIN ads_optimizer_goal g
    ON g.scope = 'campaign' AND g.ad_entity_id = r.ad_entity_id
 WHERE r.grupo_id = %s
 ORDER BY r.ad_entity_id
"""


def _huella_limpieza(candidatas) -> str:
    """Huella del conjunto: sha256 (unidas con `\n`) de las lineas
    ordenadas `{goal_id}:{camp}/{ag}/{bid}` de las candidatas, [:16].
    Conjunto vacio = sha256 del vacio (`e3b0c44298fc1c14`)."""
    lineas = sorted(f"{gid}:{camp}/{ag}/{bid}" for gid, camp, ag, bid in candidatas)
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()[:16]


def _cmd_limpiar(conn, args) -> int:
    """Valida el grupo y clasifica sus goals (fail-closed, cero
    escritura) e imprime el plan; sin `--acepto-mutacion-real` ahi
    termina (dry-run)."""
    if args.grupo is None:
        raise Abortar("--limpiar-terna exige --grupo <campana_grupo.id>")
    grupo = conn.execute(_SQL_GRUPO, (args.grupo,)).fetchone()
    if grupo is None:
        raise Abortar(f"grupo {args.grupo} no existe en campana_grupo")
    gid, platform, tipo = grupo
    exacta = conn.execute(_SQL_EXACTA, (gid,)).fetchone()
    if exacta is None:
        raise Abortar(f"grupo {gid} sin rol category_exact: no hay contra que comparar")
    exacta_camp, exacta_ag = exacta
    filas = conn.execute(_SQL_GOALS_GRUPO, (gid,)).fetchall()
    discovery = next((ent for ent, rol, *_resto in filas if rol == "auto_discovery"), None)
    if discovery is None:
        raise Abortar(f"grupo {gid} sin campana auto_discovery: el readback del go la exige")
    print(f"grupo: id={gid} platform={platform} tipo_producto={tipo}")
    print(f"exacta: campaign_external={exacta_camp} ad_group_external={exacta_ag}")
    candidatas: list[tuple[int, str, str, object]] = []
    for _entidad_id, rol, goal_id, camp, ag, bid in filas:
        if goal_id is None:
            print(f"[sin goal] rol={rol} campana_ad_entity_id={_entidad_id}")
            continue
        if camp is None and ag is None:
            print(f"[ya limpia] goal={goal_id} rol={rol} terna=NULL/NULL bid={bid}")
            continue
        if (camp, ag) != (exacta_camp, exacta_ag):
            raise Abortar(
                f"destino_inconsistente en goal {goal_id} (rol {rol}):"
                f" terna {camp}/{ag} != exacta del grupo {exacta_camp}/{exacta_ag};"
                " lo decide el dueno (goals_write o la UI)"
            )
        if bid is None:
            raise Abortar(
                f"goal {goal_id} (rol {rol}) con terna presente y harvest_default_bid NULL:"
                " el trigger lo prohibe; se declara, no se limpia"
            )
        print(f"[candidata] goal={goal_id} rol={rol} terna={camp}/{ag} bid={bid}")
        candidatas.append((goal_id, camp, ag, bid))
    huella = _huella_limpieza(candidatas)
    print(f"candidatas: {len(candidatas)} huella: {huella}")
    print(f"huella: {huella}")
    if not args.acepto_mutacion_real:
        return 0
    if args.esperado is None:
        raise Abortar("mutacion real exige --esperado N (anti-typo del plan)")
    if not args.go or not args.go.strip():
        raise Abortar("mutacion real exige --go con el literal del dueno (no vacio)")
    if len(candidatas) != args.esperado:
        raise Abortar(
            f"--esperado {args.esperado} != {len(candidatas)} candidatas:"
            " el plan cambio, se re-autoriza con el dueno"
        )
    if not args.huella:
        raise Abortar("mutacion real exige --huella del dry-run (autorizacion por conjunto)")
    if args.huella != huella:
        raise Abortar(f"--huella {args.huella} != huella {huella}: el conjunto cambio")
    # Por goal, reanudable: cada `edita_goal` confirma su propia llamada
    # (no hay transaccion global a proposito); un fallo a mitad aborta con
    # el `goal_id` y lo limpio queda limpio (bid-solo en grupo es valido y
    # resuelve por grupo), y re-correr salta las ya limpias.
    ahora = dt.datetime.now(dt.UTC)
    for limpias, (goal_id, _camp, _ag, _bid) in enumerate(candidatas):
        try:
            edita_goal(conn, goal_id, harvest_limpia_destino=True, updated_at=ahora)
        except (GoalInvalido, GoalInexistente) as exc:
            raise Abortar(
                f"goal {goal_id}: {exc} (limpias {limpias} de {len(candidatas)};"
                " lo limpio queda limpio, re-correr reanuda)"
            ) from None
        print(f"limpio: goal={goal_id}")
    releidas = {
        goal_id: (camp, ag, bid)
        for _ent, _rol, goal_id, camp, ag, bid in conn.execute(_SQL_GOALS_GRUPO, (gid,)).fetchall()
        if goal_id is not None
    }
    for goal_id, _camp, _ag, bid in candidatas:
        estado = releidas.get(goal_id)
        if estado is None or estado[0] is not None or estado[1] is not None or estado[2] != bid:
            raise Abortar(
                f"readback tras el go: goal {goal_id} quedo {estado},"
                f" se esperaba (None, None, {bid}) (commits hechos, se declara)"
            )
    print(f"readback: {len(candidatas)} goals en bid-solo, bid intacto")
    res = resolver_destino(conn, platform, discovery)
    if (
        not isinstance(res, DestinoHarvest)
        or res.resuelto_por != RESUELTO_GRUPO
        or res.motivo is not None
    ):
        raise Abortar(
            f"readback tras el go: la discovery {discovery} no resuelve grupo: {res}"
            " (commits hechos, se declara)"
        )
    print(f"readback: discovery {discovery} resuelto_por={res.resuelto_por} motivo=None")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    modos = ap.add_mutually_exclusive_group(required=True)
    modos.add_argument("--migrar", action="store_true", help="migrar UNA campana sin grupo")
    modos.add_argument("--limpiar-terna", action="store_true", help="limpiar la terna de UN grupo")
    ap.add_argument("--plataforma", default=None, help="amazon_us | amazon_mx (con --migrar)")
    ap.add_argument("--campana", default=None, help="external_id de la campana origen")
    ap.add_argument("--destino-campana", default=None, help="external_id de la campana destino")
    ap.add_argument("--destino-ad-group", default=None, help="external_id del ad group destino")
    ap.add_argument("--grupo", type=int, default=None, help="id de campana_grupo")
    ap.add_argument(
        "--acepto-mutacion-real",
        action="store_true",
        help="obligatorio para escribir; sin el = dry-run",
    )
    ap.add_argument("--esperado", type=int, default=None, help="candidatas autorizadas")
    ap.add_argument("--huella", default=None, help="huella del dry-run")
    ap.add_argument("--go", default=None, help="literal del dueno (no vacio)")
    args = ap.parse_args(argv)

    try:
        conn = connect(_dsn_admin())
    except OrbitDbError as exc:
        raise Abortar(str(exc)) from None
    if args.migrar:
        return _cmd_migrar(conn, args)
    return _cmd_limpiar(conn, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        print(f"ABORTAR: {exc}")
        sys.exit(2)
