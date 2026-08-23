"""CLI de operacion de Orbit (ORBIT 03, task 3.3): `python -m app.cli`.

Envoltorio DELGADO: cada subcomando invoca EXACTAMENTE el mismo camino que ya
existe — `cycle` llama al orquestador de `app.cycle.py` (`corre_ciclo`, con su
MISMO claim/job_key/envelope; el job_key del lock es COMPARTIDO entre cron y
CLI via `app.cycle.job_key_de`, una sola fuente) y `ingest` delega a los mains
de los pipelines de `app/ads/` (structure/reports, con su ORBIT_DSN_INGEST y
su contabilidad de ingest_run). PROHIBIDO duplicar logica: aqui no vive
NINGUNA regla de decision ni de ingesta, solo el despacho a los caminos
existentes. El disparo manual del ciclo en PR1 ES este CLI via ssh (no hay
`/run` en la API; hallazgo Security del plan).

Exit codes: 0 exito — `cycle` con CicloOcupado TAMBIEN sale 0: el claim del
lock lo garantiza, el trabajo ya esta en curso (cron + manual coincidiendo),
no es un fallo de esta corrida; 1 fallo (el error va scrubbado: jamas un
secreto en la salida); 2 config ausente o uso invalido (patron de los mains
de app.ads: DSN sin definir -> mensaje claro y fail-closed).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import sys

from app import cycle as ciclo
from app.ads import reports, structure
from app.db import connect
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import scrub


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
    raise AssertionError(f"pipeline inalcanzable: {args.pipeline!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Operacion de Orbit: `ingest` corre los pipelines de app/ads "
            "(estructura o metricas+search terms) y `cycle` corre UN ciclo del "
            "optimizador por el MISMO camino que el cron (mismo claim/job_key/"
            "envelope)."
        ),
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_ingest = sub.add_parser("ingest", help="pipelines de ingesta de Amazon Ads (app/ads)")
    p_ingest.add_argument(
        "pipeline",
        choices=("structure", "metrics"),
        help="structure: sync de estructura; metrics: metricas + search terms",
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

    args, rest = parser.parse_known_args(argv)
    if args.comando == "cycle":
        # El ciclo ESCRIBE decisiones: un flag mal tipeado que se ignorara en
        # silencio correria con el reloj equivocado (hallazgo reviewer 3.2,
        # baja). Los tokens extra de `cycle` son SIEMPRE un error del operador.
        if rest:
            print(f"argumentos desconocidos para 'cycle': {rest}", file=sys.stderr)
            return 2
        return _cycle(args)
    return _ingest(args, rest)


if __name__ == "__main__":
    raise SystemExit(main())
