"""Reversa manual de cambios de precio de Amazon (REPRICING 01, A.3).

Nunca automatica: el dueno corre dry-run, revisa el plan y su huella, y
solo entonces el go con la misma huella. Lee el precio vivo antes de
escribir y salta lo que ya no coincide (un saltado no aborta el lote).
Limitación visible: el cambio del mismo día sigue abierto y el índice
único impide su reversa; el plan lo muestra como saltado
`original_abierto` hasta que cierre por observación. Un cambio en
`error` (nace con `enviado_at` y no pasa por el cierre) sí es
reversible el mismo día cuando el vivo coincide.

Dry-run por omision (imprime plan + huella, cero PATCH). La mutacion
real exige juntos `--acepto-mutacion-real`, `--huella H` (la del
dry-run) y `--go` no vacio. Usa `ORBIT_DSN_DECIDE` (jamas DSN admin) y
el escritor solo via `app.spapi.precio_write.construir_escritor`: este
tool no importa `app.spapi.write_client` directo.

La ejecucion real se ensaya en A.4 con ids reales del dueno; hasta que
A.4 selle la forma del parche, el go levanta `FormaParcheSinSellar`
(sin fila y sin red).

Conexión en autocommit; cada bloque confirma al salir: el tool conecta
con `autocommit=True` para que la fila `pendiente` ya sea durable
cuando sale el PATCH (S5).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

import httpx

from app.db import OrbitDbError, connect
from app.spapi import precio_write
from app.spapi.client import SpapiClient
from app.spapi.precio_write import FormaParcheSinSellar


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_decide() -> str:
    dsn = os.environ.get("ORBIT_DSN_DECIDE")
    if not dsn:
        raise Abortar("ORBIT_DSN_DECIDE no esta en el entorno (jamas DSN admin)")
    return dsn


def _cambios(conn, ids: list[int]) -> list:
    filas = conn.execute(
        "SELECT c.id, c.listing_id, c.platform, c.precio_antes, c.precio_antes_currency,"
        " c.precio_despues, c.precio_despues_currency, c.aplicado, c.estado,"
        " c.enviado_at, c.es_reversa, l.seller_sku, l.external_id"
        " FROM precio_cambio c JOIN listing l"
        " ON l.id = c.listing_id AND l.platform = c.platform"
        " WHERE c.id = ANY(%s) ORDER BY c.id",
        (ids,),
    ).fetchall()
    vistos = {f[0] for f in filas}
    for pedido in ids:
        if pedido not in vistos:
            raise Abortar(f"cambio {pedido} inexistente")
    return filas


def _plan(conn, lector: SpapiClient, filas) -> list[tuple]:
    """Plan fresco: (id, accion, detalle) con accion en
    {'revertir', 'saltar'}; saltar no aborta, se imprime con su razon."""
    plan = []
    for f in filas:
        cid = f[0]
        if f[10] or not f[7] or f[9] is None:
            plan.append(
                (
                    cid,
                    "saltar",
                    "no_reversible: cambio real, no-reversa, con enviado_at",
                )
            )
            continue
        if f[8] in ("pendiente", "enviado"):
            plan.append(
                (
                    cid,
                    "saltar",
                    "original_abierto (cierra con la observación del día siguiente)",
                )
            )
            continue
        otro_abierto = conn.execute(
            "SELECT count(*) FROM precio_cambio"
            " WHERE listing_id = %s AND platform = %s"
            " AND estado IN ('pendiente', 'enviado')",
            (f[1], f[2]),
        ).fetchone()[0]
        if otro_abierto:
            plan.append((cid, "saltar", "listing_con_cambio_abierto"))
            continue
        try:
            vivo = precio_write.leer_precio_vivo(lector, platform=f[2], asin=f[12])
        except Exception:
            plan.append((cid, "saltar", "sin_precio_vivo"))
            continue
        if (vivo.precio, vivo.moneda) != (f[5], f[6]):
            plan.append((cid, "saltar", f"precio_vivo_distinto: vivo={vivo.precio}/{vivo.moneda}"))
            continue
        plan.append((cid, "revertir", f"{f[3]}/{f[4]} -> {f[5]}/{f[6]}"))
    return plan


def _huella(plan, filas_por_id) -> str:
    partes = []
    for cid, accion, detalle in plan:
        if accion == "revertir":
            f = filas_por_id[cid]
            partes.append(f"revertir:{cid}:{f[1]}:{f[2]}:{f[5]}:{f[6]}")
        else:
            partes.append(f"saltar:{cid}:{detalle.split(':')[0]}")
    return hashlib.sha256("|".join(sorted(partes)).encode()).hexdigest()[:16]


def _linea(plan_uno, filas_por_id) -> str:
    cid, accion, detalle = plan_uno
    f = filas_por_id[cid]
    if accion == "revertir":
        return (
            f"[revertir] cambio={cid} {f[11]} {f[2]}"
            f" {f[5]:.2f} {f[6]} -> {f[3]:.2f} {f[4]} (vivo coincide)"
        )
    return f"[saltar] cambio={cid} {f[11]} {f[2]} motivo={detalle}"


def main(
    argv=None,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep=time.sleep,
    credentials: dict | None = None,
) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cambio-id", dest="cambios", type=int, action="append", required=True)
    ap.add_argument("--acepto-mutacion-real", action="store_true")
    ap.add_argument("--huella", default=None)
    ap.add_argument("--go", default=None)
    args = ap.parse_args(argv)

    try:
        conn = connect(_dsn_decide(), autocommit=True)
    except OrbitDbError as exc:
        raise Abortar(str(exc)) from None
    try:
        filas = _cambios(conn, args.cambios)
        lector = SpapiClient(credentials=credentials, transport=transport, sleep=sleep)
        plan = _plan(conn, lector, filas)
        por_id = {f[0]: f for f in filas}
        huella = _huella(plan, por_id)
        for uno in plan:
            print(_linea(uno, por_id))
        print(f"huella: {huella}")
        if not args.acepto_mutacion_real:
            return 0
        if not args.huella or not args.go:
            raise Abortar("el go exige --huella <la del dry-run> y --go no vacio")
        if args.huella != huella:
            raise Abortar(
                f"--huella {args.huella} != huella del plan {huella}: re-corre el dry-run"
            )
        for cid, accion, _detalle in plan:
            if accion != "revertir":
                continue
            f = por_id[cid]
            escritor = precio_write.construir_escritor(
                lector, f[2], transport=transport, sleep=sleep
            )
            try:
                res = precio_write.revertir(
                    conn,
                    cid,
                    lector=lector,
                    escritor=escritor,
                )
            except FormaParcheSinSellar as exc:
                raise Abortar(f"forma del parche sin sellar (A.4 la sella): {exc}") from None
            print(f"[hecho] cambio={cid} estado={res.estado} motivo={res.motivo}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        print(f"precio_reversa: {exc}", file=sys.stderr)
        sys.exit(2)
