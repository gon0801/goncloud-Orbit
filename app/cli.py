"""CLI de operacion de Orbit (ORBIT 03, task 3.3): `python -m app.cli`.

Envoltorio DELGADO: cada subcomando invoca EXACTAMENTE el mismo camino que ya
existe — `cycle` llama al orquestador de `app.cycle.py` (`corre_ciclo`, con su
MISMO claim/job_key/envelope; el job_key del lock es COMPARTIDO entre cron y
CLI via `app.cycle.job_key_de`, una sola fuente), `ingest` delega a los mains
de los pipelines de `app/ads/` (structure/reports, con su ORBIT_DSN_INGEST y
su contabilidad de ingest_run) y de `app/costs.py` (ORBIT 06 0.1: costos de
contabilidad desde un snapshot SQLite), y `goals set` despacha a
`app.goals_write.edita_goal` (ORBIT 04 3.2: el UNICO camino de escritura de
ads_optimizer_goal, con su ORBIT_DSN_ADMIN). PROHIBIDO duplicar logica: aqui
no vive NINGUNA regla de decision ni de ingesta, solo el despacho a los
caminos existentes. El disparo manual del ciclo en PR1 ES este CLI via ssh
(no hay `/run` en la API; hallazgo Security del plan).

Exit codes: 0 exito — `cycle` con CicloOcupado TAMBIEN sale 0: el claim del
lock lo garantiza, el trabajo ya esta en curso (cron + manual coincidiendo),
no es un fallo de esta corrida; 1 fallo (el error va scrubbado: jamas un
secreto en la salida; para `goals set`, goal inexistente); 2 config ausente o
uso invalido (patron de los mains de app.ads: DSN sin definir -> mensaje
claro y fail-closed; edicion de goal invalida).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import sys
from decimal import Decimal
from pathlib import Path

from app import costs, goals_write, listings
from app import cycle as ciclo
from app.ads import archivar, reports, structure
from app.db import connect
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import scrub

# El unico valor de --confirmar que ARCHIVA. Cualquier otra cosa es ensayo.
MODO_ARCHIVADO_LIVE = "live"


def _parse_decided_at(texto: str | None) -> dt.datetime:
    """Reloj de las decisiones: `--decided-at` ISO 8601 tz-aware, o ahora UTC.

    El contrato de `corre_ciclo` exige tz-aware (un naive evaluaria segun la
    TZ local; windows.goals lo rechazan ruidosamente): el CLI lo valida ANTES
    con mensaje claro, y normaliza a UTC (mismo criterio que
    `windows._fecha_utc`: determinismo, un solo reloj).
    """
    if texto is None:
        return dt.datetime.now(dt.UTC)
    try:
        momento = dt.datetime.fromisoformat(texto)
    except ValueError:
        raise ValueError(f"--decided-at no es una fecha ISO 8601 valida: {texto!r}") from None
    if momento.tzinfo is None:
        raise ValueError(
            "--decided-at debe ser tz-aware (UTC): un naive evaluaria segun la TZ local"
        )
    return momento.astimezone(dt.UTC)


def _cycle(args) -> int:
    """`cycle`: el MISMO camino del orquestador (mismo claim/job_key/envelope)."""
    dsn = os.environ.get("ORBIT_DSN_DECIDE")
    if not dsn:
        print(
            "ORBIT_DSN_DECIDE no esta definido: no se puede decidir (fail-closed)",
            file=sys.stderr,
        )
        return 2
    try:
        decided_at = _parse_decided_at(args.decided_at)
    except ValueError as exc:
        # Error de USO del operador (config), no un fallo del ciclo: exit 2
        # igual que un argumento invalido de argparse.
        print(f"{exc}", file=sys.stderr)
        return 2
    try:
        owner = args.owner or f"{socket.gethostname()}:{os.getpid()}"
        conn = connect(dsn)
        try:
            resultado = ciclo.corre_ciclo(
                conn, platform=args.platform, owner=owner, decided_at=decided_at
            )
        finally:
            conn.close()
    except ciclo.CicloOcupado as exc:
        # Condicion esperada de concurrencia, no un fallo (ver docstring).
        print(f"ciclo ya en curso (exit 0): {scrub(str(exc))}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(
            "ciclo fallo (el envelope quedo sellado 'failed' cuando fue posible): "
            f"{scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1
    print(f"== Ciclo del optimizador ({ciclo.job_key_de(args.platform)}) ==")
    print(
        f"cycle_id={resultado.cycle_id} status={resultado.status} "
        f"decisions_count={resultado.decisions_count}"
    )
    if resultado.status != "done":
        # El operador manual del ciclo necesita el PORQUE de un skipped/
        # degraded sin tragar el JSON entero (observacion reviewer 3.2).
        try:
            notas = json.loads(resultado.notes)
        except (ValueError, TypeError):
            print(f"notas: {resultado.notes[:200]}")
        else:
            motivo = notas.get("motivo_skip") or notas.get("error")
            print(f"motivo: {motivo or 'ver notes del ciclo'}")
    return 0


def _ingest(args, rest: list[str]) -> int:
    """`ingest`: delega a los mains de los pipelines (el mismo camino, cero
    logica duplicada; cada pipeline exige su propio ORBIT_DSN_INGEST y sella
    su ingest_run). argparse ya valido `pipeline` contra las choices.
    `metrics` acepta los args del pipeline (--fecha/--fecha-fin); `structure`
    no define ninguna opcion: tokens extra ahi son un error del operador."""
    if args.pipeline == "structure":
        if rest:
            print(f"argumentos desconocidos para 'ingest structure': {rest}", file=sys.stderr)
            return 2
        return structure.main()
    if args.pipeline == "metrics":
        return reports.main(rest)
    if args.pipeline == "costs":
        # ORBIT 06 0.1: costos de contabilidad (snapshot SQLite via --sqlite;
        # runbook en docs/DEPLOY.md). Mismo patron que metrics: args al main.
        return costs.main(rest)
    if args.pipeline == "listings":
        # ORBIT 06 0.2: mapa de listings desde el bridge (snapshot SQLite del
        # bridge via --sqlite; runbook en docs/DEPLOY.md). Mismo patron.
        return listings.main(rest)
    raise AssertionError(f"pipeline inalcanzable: {args.pipeline!r}")


def _archivar_anuncios(args) -> int:
    """`archivar-anuncios`: archiva product ads MUERTOS de una lista EXPLICITA.

    Envoltorio delgado sobre `app.ads.archivar`: aqui no vive ninguna regla
    de cuales estan muertos — esa evidencia la trae el operador en el
    archivo de ids. ENSAYO por defecto: sin `--confirmar live` no sale ni
    una mutacion, y el ensayo imprime exactamente lo que haria.
    """
    try:
        contenido = Path(args.ids_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"no se pudo leer --ids-file: {scrub(str(exc))}", file=sys.stderr)
        return 2
    ad_ids = [linea.strip() for linea in contenido.splitlines() if linea.strip()]
    if not ad_ids:
        print(f"--ids-file sin ningun adId: {args.ids_file}", file=sys.stderr)
        return 2

    ejecutar = args.confirmar == MODO_ARCHIVADO_LIVE
    try:
        escritor = archivar.preparar_escritor(args.platform)
    except ValueError as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 2

    print(f"plataforma: {args.platform}  adIds en la lista: {len(ad_ids)}")
    print(
        "MODO: LIVE — esto ARCHIVA en la cuenta y NO tiene reversa"
        if ejecutar
        else "MODO: ENSAYO — no sale ninguna mutacion"
    )

    try:
        resultados = archivar.archivar_anuncios(escritor, ad_ids, ejecutar=ejecutar)
    except ValueError as exc:
        # p.ej. adIds repetidos: el modulo aborta ANTES de mutar. El
        # envoltorio tiene que contarlo como error de uso, no escupir un
        # traceback (hallazgo cross-review codex/grok 2026-08-30).
        print(scrub(str(exc)), file=sys.stderr)
        return 2

    for r in resultados:
        print(f"  {r.ad_id}	{r.estado_previo or '-'}	{r.resultado}	{r.detalle}")
    cuenta = archivar.resumen(resultados)
    print(f"RESUMEN: {cuenta}")

    if not ejecutar:
        return 0
    return 1 if (cuenta["fallo"] or cuenta["sin_confirmar"]) else 0


def _decimal_arg(flag: str):
    """type de argparse para montos: Decimal exacto (regla 4, jamas float) con
    mensaje claro de USO (exit 2). Decimal lanza ArithmeticError, que argparse
    NO convierte en error de uso: hay que traducirla a ArgumentTypeError."""

    def _parse(texto: str) -> Decimal:
        try:
            return Decimal(texto)
        except ArithmeticError:
            raise argparse.ArgumentTypeError(
                f"--{flag} no es un numero valido: {texto!r}"
            ) from None

    return _parse


def _bool_arg(flag: str):
    """type de argparse para --enabled: true|false (case-insensitive)."""

    def _parse(texto: str) -> bool:
        t = texto.strip().lower()
        if t == "true":
            return True
        if t == "false":
            return False
        raise argparse.ArgumentTypeError(f"--{flag} acepta solo true|false, llego {texto!r}")

    return _parse


# Orden de impresion de la fila editada (una linea por campo, updated_at
# visible: el sello de la edicion es parte del rastro).
_CAMPOS_GOAL = (
    "scope",
    "platform",
    "ad_entity_id",
    "target_acos_pct",
    "enabled",
    "bid_floor",
    "bid_ceiling",
    "bid_currency",
    "harvest_campaign_id",
    "harvest_ad_group_id",
    "harvest_default_bid",
    "mode",
    "created_at",
    "updated_at",
)


def _goals_set(args) -> int:
    """`goals set`: DESPACHA a goals_write.edita_goal (el UNICO camino de
    escritura de ads_optimizer_goal, regla 1; cero SQL aqui). Requiere
    ORBIT_DSN_ADMIN (los goals los escribe app_admin): sin DSN -> exit 2
    fail-closed (patron _cycle). Exit codes sellados en goals_write:
    GoalInvalido = 2 (uso invalido), GoalInexistente = 1; la conexion se abre
    SOLO despues de validar los argumentos."""
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        print(
            "ORBIT_DSN_ADMIN no esta definido: no se puede editar el goal (fail-closed)",
            file=sys.stderr,
        )
        return 2
    campos = {
        "target_acos_pct": args.target,
        "enabled": args.enabled,
        "bid_floor": args.floor,
        "bid_ceiling": args.ceiling,
        "harvest_campaign_id": args.harvest_campaign,
        "harvest_ad_group_id": args.harvest_ad_group,
        "harvest_default_bid": args.harvest_bid,
    }
    if not any(v is not None for v in campos.values()) and not args.harvest_limpia:
        print(
            "goals set necesita al menos un campo a editar "
            "(--target/--enabled/--floor/--ceiling/--harvest-*)",
            file=sys.stderr,
        )
        return 2
    try:
        conn = connect(dsn)
        try:
            fila = goals_write.edita_goal(
                conn,
                args.goal_id,
                harvest_limpia=args.harvest_limpia,
                updated_at=dt.datetime.now(dt.UTC),
                **campos,
            )
        finally:
            conn.close()
    except goals_write.GoalInvalido as exc:
        print(f"edicion invalida (exit 2): {exc}", file=sys.stderr)
        return 2
    except goals_write.GoalInexistente as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"edicion del goal fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    print(f"== Goal {fila['id']} actualizado ==")
    for campo in _CAMPOS_GOAL:
        valor = fila[campo]
        if hasattr(valor, "isoformat"):
            valor = valor.isoformat()
        print(f"{campo}={valor}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Operacion de Orbit: `ingest` corre los pipelines de app/ads "
            "(estructura o metricas+search terms), `cycle` corre UN ciclo del "
            "optimizador por el MISMO camino que el cron (mismo claim/job_key/"
            "envelope) y `goals set` edita un goal del optimizador por el unico "
            "camino de escritura (app/goals_write)."
        ),
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_ingest = sub.add_parser(
        "ingest", help="pipelines de ingesta (app/ads, app/costs y app/listings)"
    )
    p_ingest.add_argument(
        "pipeline",
        choices=("structure", "metrics", "costs", "listings"),
        help=(
            "structure: sync de estructura; metrics: metricas + search terms;"
            " costs: productos+costos desde contabilidad (--sqlite);"
            " listings: mapa de listings desde el bridge (--sqlite)"
        ),
    )

    p_cycle = sub.add_parser(
        "cycle",
        help="corre UN ciclo del optimizador (el mismo camino que el cron de 4.2)",
    )
    p_cycle.add_argument(
        "--platform", required=True, choices=sorted(PLATAFORMAS_MONEDA), help="plataforma del ciclo"
    )
    p_cycle.add_argument(
        "--owner", default=None, help="identidad del proceso (default: hostname:pid)"
    )
    p_cycle.add_argument(
        "--decided-at",
        default=None,
        help="ISO 8601 tz-aware para el reloj de las decisiones (default: ahora UTC)",
    )

    p_goals = sub.add_parser(
        "goals",
        help="edicion amigable de goals del optimizador (ORBIT 04 3.2; requiere ORBIT_DSN_ADMIN)",
        # Sin abreviaturas: --targe NO puede interpretarse como --target y
        # editar con un valor mal tipeado (la edicion ESCRIBE config viva).
        allow_abbrev=False,
    )
    goals_sub = p_goals.add_subparsers(dest="subcomando", required=True)
    p_set = goals_sub.add_parser(
        "set", help="edita un goal por id (caminos no pasados quedan igual)", allow_abbrev=False
    )
    p_set.add_argument(
        "goal_id",
        type=int,
        help="id numerico del goal (el que lista GET /api/ads-optimizer/goals)",
    )
    p_set.add_argument(
        "--target", type=_decimal_arg("target"), default=None, help="target_acos_pct"
    )
    p_set.add_argument("--enabled", type=_bool_arg("enabled"), default=None, help="true|false")
    p_set.add_argument("--floor", type=_decimal_arg("floor"), default=None, help="bid_floor")
    p_set.add_argument("--ceiling", type=_decimal_arg("ceiling"), default=None, help="bid_ceiling")
    p_set.add_argument(
        "--harvest-campaign", default=None, help="harvest_campaign_id (terna harvest completa)"
    )
    p_set.add_argument(
        "--harvest-ad-group", default=None, help="harvest_ad_group_id (terna harvest completa)"
    )
    p_set.add_argument(
        "--harvest-bid", type=_decimal_arg("harvest-bid"), default=None, help="harvest_default_bid"
    )
    p_set.add_argument(
        "--harvest-limpia",
        action="store_true",
        help="pone los TRES campos de harvest en NULL (no combina con --harvest-*)",
    )

    p_arch = sub.add_parser(
        "archivar-anuncios",
        help=(
            "archiva product ads MUERTOS de una lista explicita de adIds "
            "(ENSAYO salvo --confirmar live; el archivado NO tiene reversa)"
        ),
        # Mismo candado que goals/cycle: esto MUTA la cuenta del dueno, un
        # --confirma mal tipeado no puede colarse como abreviatura.
        allow_abbrev=False,
    )
    p_arch.add_argument(
        "--platform", required=True, choices=sorted(PLATAFORMAS_MONEDA), help="plataforma"
    )
    p_arch.add_argument(
        "--ids-file", required=True, help="archivo con UN adId por linea (evidencia del operador)"
    )
    p_arch.add_argument(
        "--confirmar",
        default=None,
        help=f"'{MODO_ARCHIVADO_LIVE}' para ARCHIVAR de verdad; sin esto es ensayo",
    )

    args, rest = parser.parse_known_args(argv)
    if args.comando == "cycle":
        # El ciclo ESCRIBE decisiones: un flag mal tipeado que se ignorara en
        # silencio correria con el reloj equivocado (hallazgo reviewer 3.2,
        # baja). Los tokens extra de `cycle` son SIEMPRE un error del operador.
        if rest:
            print(f"argumentos desconocidos para 'cycle': {rest}", file=sys.stderr)
            return 2
        return _cycle(args)
    if args.comando == "goals":
        # Mismo candado que cycle: un --targe mal tipeado NO puede ignorarse y
        # "editar" nada (la edicion ESCRIBE configuracion viva del optimizador).
        if rest:
            print(f"argumentos desconocidos para 'goals set': {rest}", file=sys.stderr)
            return 2
        return _goals_set(args)
    if args.comando == "archivar-anuncios":
        if rest:
            print(f"argumentos desconocidos para 'archivar-anuncios': {rest}", file=sys.stderr)
            return 2
        return _archivar_anuncios(args)
    return _ingest(args, rest)


if __name__ == "__main__":
    raise SystemExit(main())
