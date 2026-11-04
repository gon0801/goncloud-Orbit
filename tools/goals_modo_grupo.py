"""Modo de los goals de un grupo con ceremonia (precondicion de D.3).

Solo Postgres y solo `app_admin` (unico DSN: `ORBIT_DSN_ADMIN`), cero
Amazon, cero apply: `--grupo N --mode shadow|live` mueve el `mode` de
los goals `scope='campaign'` de UN grupo, dry-run por defecto y la
ceremonia sellada (`--acepto-mutacion-real --esperado N --huella H --go
"<literal>"`, patron `tools/harvest_excepcion.py`).

Las candidatas se resuelven por IDENTIDAD (`ad_entity_id` en
`campana_grupo_rol`, jamas por nombre); un goal ya en el modo pedido
cuenta como «ya esta» y no entra a `--esperado`. El go es POR GOAL via
`goals_write.edita_goal` (este tool jamas trae SQL de escritura de
goals: despacha, no duplica), REANUDABLE: cada `edita_goal` confirma su
llamada (sin transaccion global a proposito); un fallo a mitad aborta
con el `goal_id`, lo cambiado queda cambiado y re-correr con ceremonia
nueva termina el resto.

La envolvente (`ads_optimizer_mode` de la config vigente) solo se
MUESTRA, no se valida: el readback trae el modo efectivo (meet) por
goal. Encender un grupo con la envolvente en `shadow` es legal y no
hace nada hasta que la envolvente suba.

Un grupo y un modo por corrida y por `go`. Todo lo que cambia queda
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
from app.optimizer.goals import (
    CLAVE_SETTING_MODO,
    modo_desde_settings,
    modo_efectivo,
)


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_admin() -> str:
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        raise Abortar("ORBIT_DSN_ADMIN no esta en el entorno (unico DSN del tool)")
    return dsn


_SQL_GRUPO = """
SELECT id, platform::text, tipo_producto FROM campana_grupo WHERE id = %s
"""

_SQL_CONFIG_VIGENTE = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_GOALS_GRUPO = """
SELECT r.ad_entity_id, r.rol::text, g.id, g.mode,
       g.harvest_campaign_id, g.harvest_ad_group_id, g.harvest_default_bid
  FROM campana_grupo_rol r
  LEFT JOIN ads_optimizer_goal g
    ON g.scope = 'campaign' AND g.ad_entity_id = r.ad_entity_id
 WHERE r.grupo_id = %s
 ORDER BY r.ad_entity_id
"""


def _huella(grupo_id: int, pedido: str, goal_ids) -> str:
    """Huella del conjunto: sha256 de `{grupo}:{pedido}:{ids ordenados
    con coma}` [:16]. Autorizacion por conjunto: si el plan cambia, la
    huella ya no coincide y el go aborta."""
    base = f"{grupo_id}:{pedido}:{','.join(str(i) for i in sorted(goal_ids))}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _etiqueta_destino(camp, ag, bid) -> str:
    """Terna del goal en una palabra: `terna` (completa), `bid-solo`
    (destino por grupo, A.2) o `sin-terna`."""
    if camp is not None and ag is not None and bid is not None:
        return "terna"
    if camp is None and ag is None and bid is not None:
        return "bid-solo"
    return "sin-terna"


def _envolvente(conn) -> tuple[str, str]:
    """`(valor_mostrado, modo_para_meet)` de la envolvente: el valor CRUDO
    de `ads_optimizer_mode` en la config vigente (o `ausente`/`invalido`)
    para mostrar, y el fail-closed que el motor usa
    (`modo_desde_settings`) para el meet. El tool NO valida la
    envolvente, solo la muestra."""
    vig = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    settings = vig[1] if vig is not None else {}
    crudo = settings.get(CLAVE_SETTING_MODO)
    meet = modo_desde_settings(settings)
    if crudo is None:
        return ("ausente (el motor corre off)", meet)
    if meet != crudo:
        return (f"{crudo!r} invalido (el motor corre off)", meet)
    return (crudo, meet)


def _cmd(conn, args) -> int:
    """Valida el grupo y clasifica sus goals (fail-closed, cero
    escritura) e imprime el plan; sin `--acepto-mutacion-real` ahi
    termina (dry-run)."""
    pedido = args.mode
    grupo = conn.execute(_SQL_GRUPO, (args.grupo,)).fetchone()
    if grupo is None:
        raise Abortar(f"grupo {args.grupo} no existe en campana_grupo")
    gid, platform, tipo = grupo
    print(f"grupo: id={gid} platform={platform} tipo_producto={tipo}")
    mostrado, meet_env = _envolvente(conn)
    print(f"envolvente: ads_optimizer_mode={mostrado} (modo efectivo = meet)")
    filas = conn.execute(_SQL_GOALS_GRUPO, (gid,)).fetchall()
    candidatas: list[int] = []
    for entidad_id, rol, goal_id, actual, camp, ag, bid in filas:
        if goal_id is None:
            print(f"[sin goal] rol={rol} campana_ad_entity_id={entidad_id}")
            continue
        if actual == pedido:
            print(f"[ya esta] goal={goal_id} rol={rol} mode={actual}")
            continue
        print(f"goal={goal_id} rol={rol} {actual} → {pedido} {_etiqueta_destino(camp, ag, bid)}")
        candidatas.append(goal_id)
    huella = _huella(gid, pedido, candidatas)
    print(f"candidatas: {len(candidatas)}")
    print(f"huella: {huella}")
    if not args.acepto_mutacion_real:
        return 0
    # Ceremonia sellada (orden de reversa_harvest: esperado -> go ->
    # huella; `--go` solo-espacios cuenta como ausente).
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
    # el `goal_id` y lo cambiado queda cambiado, y re-correr con ceremonia
    # nueva termina el resto (las ya cambiadas salen como «ya esta»).
    ahora = dt.datetime.now(dt.UTC)
    for cambiados, goal_id in enumerate(candidatas):
        try:
            edita_goal(conn, goal_id, mode=pedido, updated_at=ahora)
        except (GoalInvalido, GoalInexistente) as exc:
            raise Abortar(
                f"goal {goal_id}: {exc} (cambiados {cambiados} de {len(candidatas)};"
                " lo cambiado queda cambiado, re-correr reanuda)"
            ) from None
        print(f"cambiado: goal={goal_id}")
    if not candidatas:
        print("readback: sin candidatas, nada cambio")
        return 0
    releidos = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT g.id, g.mode FROM ads_optimizer_goal g"
            " JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id"
            " WHERE g.scope = 'campaign' AND r.grupo_id = %s",
            (gid,),
        ).fetchall()
    }
    # El readback imprime lo LEIDO (jamas lo pedido) + el meet; si algo
    # no quedo, se declara (commits hechos, no se revierten). Un goal
    # desaparecido a mitad aborta limpio (sin esto, `modo_efectivo`
    # reventaria con ValueError crudo).
    for goal_id in candidatas:
        leido = releidos.get(goal_id)
        if leido is None:
            raise Abortar(
                f"readback tras el go: goal {goal_id} desaparecio a mitad"
                " (commits hechos, se declara)"
            )
        print(f"readback: goal={goal_id} mode={leido} efectivo={modo_efectivo(meet_env, leido)}")
    for goal_id in candidatas:
        leido = releidos.get(goal_id)
        if leido != pedido:
            raise Abortar(
                f"readback tras el go: goal {goal_id} quedo {leido},"
                f" se esperaba {pedido} (commits hechos, se declara)"
            )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grupo", type=int, default=None, help="id de campana_grupo")
    ap.add_argument("--mode", default=None, help="shadow | live (off no entra al tool)")
    ap.add_argument(
        "--acepto-mutacion-real",
        action="store_true",
        help="obligatorio para escribir; sin el = dry-run",
    )
    ap.add_argument("--esperado", type=int, default=None, help="candidatas autorizadas")
    ap.add_argument("--huella", default=None, help="huella del dry-run")
    ap.add_argument("--go", default=None, help="literal del dueno (no vacio)")
    args = ap.parse_args(argv)

    # Uso antes de I/O: un flag mal puesto no abre la base.
    if args.grupo is None:
        raise Abortar("--grupo <campana_grupo.id> es obligatorio")
    if args.mode not in ("shadow", "live"):
        raise Abortar(
            "--mode exige shadow o live (off no entra al tool:"
            " el apagado es goal por goal con goals set)"
        )

    try:
        conn = connect(_dsn_admin())
    except OrbitDbError as exc:
        raise Abortar(str(exc)) from None
    return _cmd(conn, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        print(f"ABORTAR: {exc}")
        sys.exit(2)
