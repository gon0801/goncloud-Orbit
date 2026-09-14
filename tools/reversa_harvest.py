"""Reversa manual de un harvest con hermanas (FABRICA 02, A.3).

Deriva todo de la base para `--job <id>` y ejecuta en orden canonico
(keyword -> hermanas propias -> origen), con readback entre deletes, stop
al primer fallo y reanudacion sin repetir (lo confirmado se salta por
ledger, sin guard global).

Dry-run por defecto (imprime plan + huella, cero HTTP). La mutacion real
exige juntos `--acepto-mutacion-real`, `--esperado N`, `--huella H` (del
dry-run, sobre los pasos PENDIENTES) y `--go` no vacio. Usa
`ORBIT_DSN_DECIDE` (jamas DSN admin) y el cliente solo via
`apply._cliente_reversa`: no construye `AdsWriteClient`, no acepta
profile/IDs arbitrarios.

La ejecucion real se ensaya en D.3 con un go nuevo del dueno; en A.3 solo
se prueba con MockTransport (la logica vive en `app.apply_harvest`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys

from app import apply
from app.ads.client import AdsApiError
from app.apply import SinPerfilReversa
from app.apply_harvest import ejecuta_reversa_harvest, plan_reversa_harvest
from app.db import connect


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_decide() -> str:
    dsn = os.environ.get("ORBIT_DSN_DECIDE")
    if not dsn:
        raise Abortar("ORBIT_DSN_DECIDE no esta en el entorno (jamas DSN admin)")
    return dsn


def _huella_pasos(pasos) -> str:
    """Huella del conjunto pendiente: clase|rol|id en orden canonico."""
    canon = "|".join(f"{p.clase}:{p.rol or ''}:{p.objeto_id}" for p in pasos)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def _pendientes(conn, decision_id: int, pasos):
    from app.apply_harvest import _reversa_confirmada

    return [p for p in pasos if not _reversa_confirmada(conn, decision_id, p.clase, p.objeto_id)]


def _linea_paso(paso, hecho: bool) -> str:
    marca = "hecho" if hecho else "pendiente"
    quien = paso.rol or paso.clase
    return f"[{marca}] {paso.clase} {quien} ad_group={paso.ad_group_ext} id={paso.objeto_id}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", type=int, required=True, help="id de harvest_job done a revertir")
    ap.add_argument(
        "--acepto-mutacion-real",
        action="store_true",
        help="obligatorio para tocar Amazon; sin el = dry-run",
    )
    ap.add_argument("--esperado", type=int, default=None, help="pasos pendientes autorizados")
    ap.add_argument("--huella", default=None, help="huella del dry-run sobre los pendientes")
    ap.add_argument("--go", default=None, help="literal del dueno (no vacio)")
    args = ap.parse_args(argv)

    conn = connect(_dsn_decide())
    try:
        platform, term, decision_id, pasos = plan_reversa_harvest(conn, args.job)
    except ValueError as exc:
        raise Abortar(str(exc)) from None
    pendientes = _pendientes(conn, decision_id, pasos)
    huella = _huella_pasos(pendientes)
    print(f"job: {args.job} platform: {platform} termino: {term} decision: {decision_id}")
    for paso in pasos:
        print(_linea_paso(paso, paso not in pendientes))
    print(f"pendientes: {len(pendientes)} huella: {huella}")

    if not args.acepto_mutacion_real:
        print("dry-run: sin --acepto-mutacion-real no se toca Amazon")
        return 0
    if args.esperado is None:
        raise Abortar("mutacion real exige --esperado N (anti-typo del plan)")
    if not args.go:
        raise Abortar("mutacion real exige --go con el literal del dueno (no vacio)")
    if len(pendientes) != args.esperado:
        raise Abortar(
            f"--esperado {args.esperado} != pasos pendientes {len(pendientes)}: "
            "el plan cambio, se re-autoriza con el dueno"
        )
    if not args.huella:
        raise Abortar("mutacion real exige --huella del dry-run (autorizacion por conjunto)")
    if args.huella != huella:
        raise Abortar(f"--huella {args.huella} != huella {huella}: el conjunto cambio")
    if not pendientes:
        print("nada que revertir: todo confirmado")
        return 0

    try:
        cliente = apply._cliente_reversa(platform, transport=None)
    except (AdsApiError, SinPerfilReversa) as exc:
        raise Abortar(f"sin cliente de reversa: {exc} (nada se toco)") from None
    try:
        ok, detalle = ejecuta_reversa_harvest(conn, cliente, decision_id, term, pasos)
    except AdsApiError as exc:
        # Readback ambiguo (5xx/red/ilegible) a mitad de reversa: stop
        # limpio en vez de traceback (r4). Lo ya confirmado queda sellado
        # ok; lo abierto se reanuda con el mismo plan (mismo --go NO sirve:
        # el conjunto cambio -> nuevo dry-run y nuevo go del dueno).
        raise Abortar(
            f"lectura ambigua a mitad de reversa: {exc} (ver ledger y reanudar)"
        ) from None
    print(detalle)
    conn.commit()
    if not ok:
        raise Abortar(detalle)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        print(f"ABORTAR: {exc}")
        sys.exit(2)
