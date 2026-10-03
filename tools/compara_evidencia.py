#!/usr/bin/env python3
"""Compara sombra v1 vs contrafactual v2 (A4): cuantas decisiones de bid la
politica por evidencia mantendria, quitaria o agregaria.

Lee decision.inputs (SOLO SELECT) y clasifica cada decision del motor de
bids por su veredicto v2 CONGELADO (inputs.evidencia_v2.veredicto): el tool
CONFIA en lo congelado, no re-decide (eso lo hace --verificar). Buckets:
mantiene (mismo kind+factor), quita (bid vivo -> v2 no actua), cambia_banda
(bid vivo -> bid v2 con OTRO factor).

A6-live: las filas via=decide (el vivo DECIDIO con evidencia) van al bucket
vive_v2 SIN clasificar en la direccion sombra (ahi no hay sombra v2 que
comparar). A6-r1 F1: via=decide se PARTE por abstencion_v2 en vive_v2
(decidio v2) y vive_v2_fallback (decidio el fallback v1). A6-r2 B3: las
filas decide llevan ADEMAS el contrafactual v1 (inputs.bandas_v1) y se
clasifican en la direccion INVERTIDA (vivo v2 vs v1 congelado): compara
cuenta en los dos sentidos, sin mezclarlos.

LIMITACION ESTRUCTURAL DECLARADA (no escondida como cero): el "agrega puro"
es INVISIBLE en ambas direcciones porque los no-op no tienen fila donde
congelar (ni silencio v1 -> bid v2 en A4, ni silencio v2 -> bid v1 en
A6-live). Este tool reporta agrega_puro como no-medible, jamas como 0.

Cero mutaciones. DSN: ORBIT_DSN_READ via app.db.connect. SOLO SELECT.

USO:
  python tools/compara_evidencia.py --platform amazon_mx [--cycle 12]
    [--since 2026-10-03] [--verificar] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect  # noqa: E402
from app.optimizer import bid as b  # noqa: E402
from app.optimizer.replay import reproduce_bandas_v1, reproduce_evidencia_v2  # noqa: E402
from app.redaction import scrub  # noqa: E402

# Vocabulario cerrado de motivos v2 (res B12): bandas v1 (las que v2 puede
# emitir) + abstenciones v2 + no-ops de la cola compartida que un veredicto
# v2 puede traer (mismo tail que v1). Motivo fuera de aqui = drift: el tool
# falla RUIDOSO (exit 1), jamas lo cuenta en silencio.
_MOTIVOS_V2 = frozenset(
    {
        b._MOTIVO_BANDA[b.FACTOR_BAJA_FUERTE],
        b._MOTIVO_BANDA[b.FACTOR_BAJA_SUAVE],
        b._MOTIVO_BANDA[b.FACTOR_SUBIDA],
        b.MOTIVO_EVIDENCIA_INSUFICIENTE,
        b.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE,
        b.MOTIVO_PAUSE,
        # Pause BLOQUEADO (no-op final cuando v2 abstiene y la eval de pause
        # estaba bloqueada: el no-op reporta pause->bids por contrato v1).
        b.MOTIVO_PAUSE_CORTES_INCOMPLETO,
        b.MOTIVO_PAUSE_MONEDA_INVALIDA,
        b.MOTIVO_PAUSE_ORDERS_DESCONOCIDO,
        b.MOTIVO_PAUSE_CLICKS_COST_DESCONOCIDOS,
        # Dato faltante economico (r3, F1): cortes completa pero revenue
        # None/negativo + policy economica + live-bid + v2-abstiene -> el
        # no-op reporta esta guarda pause. pause_economica AUSENTE a
        # proposito: v2 no pausa en fila bid (mismos cortes que live).
        b.MOTIVO_PAUSE_ECONOMICA_DATO_FALTANTE,
        b.MOTIVO_BIDS_SIN_OBSERVACIONES,
        b.MOTIVO_BIDS_MONEDA_INVALIDA,
        b.MOTIVO_BID_ACTUAL_AUSENTE,
        b.MOTIVO_BID_ACTUAL_INVALIDO,
        b.MOTIVO_BID_MONEDA_INVALIDA,
        b.MOTIVO_DELTA_BAJO_UMBRAL,
        b.MOTIVO_RANGO_BLOQUEA_AJUSTE,
    }
)

# Fold de presentacion (res B12): cero_ventas es un -25 con motivo propio.
_FOLD_MOTIVO = {b.MOTIVO_BANDA_MENOS_25_CERO_VENTAS: b._MOTIVO_BANDA[b.FACTOR_BAJA_FUERTE]}

# Vocabulario cerrado de motivos v1 (A6-r2 B3, direccion invertida): bandas
# v1 (las 3 + cero_ventas, que v2 nunca emite) + no-ops v1 que un veredicto
# puede traer (el no-op reporta la primera guarda que bloqueo, pause antes
# que bids). Las abstenciones v2 y pause_economica quedan FUERA a proposito
# (v1 no abstiene; un pause en fila bid es invariante roto, no vocabulario).
_MOTIVOS_V1 = frozenset(
    {
        b._MOTIVO_BANDA[b.FACTOR_BAJA_FUERTE],
        b._MOTIVO_BANDA[b.FACTOR_BAJA_SUAVE],
        b._MOTIVO_BANDA[b.FACTOR_SUBIDA],
        b.MOTIVO_BANDA_MENOS_25_CERO_VENTAS,
        b.MOTIVO_PAUSE_CORTES_INCOMPLETO,
        b.MOTIVO_PAUSE_MONEDA_INVALIDA,
        b.MOTIVO_PAUSE_ORDERS_DESCONOCIDO,
        b.MOTIVO_PAUSE_CLICKS_COST_DESCONOCIDOS,
        b.MOTIVO_PAUSE_ECONOMICA_DATO_FALTANTE,
        b.MOTIVO_BIDS_SIN_OBSERVACIONES,
        b.MOTIVO_BIDS_INCOMPLETO,
        b.MOTIVO_BIDS_MONEDA_INVALIDA,
        b.MOTIVO_ACOS_DESCONOCIDO,
        b.MOTIVO_SIN_BANDA,
        b.MOTIVO_DELTA_BAJO_UMBRAL,
        b.MOTIVO_RANGO_BLOQUEA_AJUSTE,
        b.MOTIVO_BID_ACTUAL_INVALIDO,
        b.MOTIVO_BID_ACTUAL_AUSENTE,
        b.MOTIVO_BID_MONEDA_INVALIDA,
    }
)

_SQL_DECISIONES = """
SELECT d.id, d.cycle_id, d.kind, d.inputs
  FROM decision d JOIN optimizer_cycle c ON c.id = d.cycle_id
 WHERE c.platform = %s AND c.motor = 'ads_optimizer'
   AND d.inputs->>'motor' = 'bid' AND d.kind IN ('bid', 'pause')
"""


def clasifica(
    live_kind: str,
    live_motivo: str,
    live_factor: str | None,
    veredicto: dict,
    *,
    motivos: frozenset = _MOTIVOS_V2,
    etiqueta: str = "v2",
) -> str:
    """Bucket de UNA decision (pura, testeable): mantiene | quita |
    cambia_banda. Invariantes estructurales (res A14, ambas direcciones):
    pause->bid y bid->pause son IMPOSIBLES (pause_intacto + mismo bloque
    pause): si aparecen, es un bug y se levanta, no se clasifica. El
    vocabulario cerrado aplica SOLO a filas bid (veredicto DECIDIDO): en
    filas pause el motivo congelado es eco del vivo (cualquiera de los
    pause, ej pause_economica) y no se valida (r3, F1). `motivos` + la
    `etiqueta` del contrafactual eligen la direccion (sombra v2 por
    default; v1 en la invertida de A6-r2 B3)."""
    kind_cf = veredicto.get("kind")
    if live_kind == "pause" and kind_cf != "pause":
        raise ValueError(f"invariante roto: pause viva con {etiqueta} kind={kind_cf!r}")
    if live_kind == "bid" and kind_cf == "pause":
        raise ValueError(f"invariante roto: bid viva con {etiqueta} pause (mismo bloque)")
    if live_kind == "pause":
        return "mantiene"
    motivo_cf = veredicto.get("motivo")
    if motivo_cf not in motivos:
        raise ValueError(f"motivo {etiqueta} fuera del vocabulario cerrado: {motivo_cf!r}")
    if kind_cf != "bid":
        return "quita"
    if veredicto.get("factor") == live_factor:
        return "mantiene"
    return "cambia_banda"


def resume(filas: list[dict]) -> dict:
    """Resume puro sobre filas {id, kind, motivo, factor, evidencia_v2,
    bandas_v1}: buckets + detalle por motivo (foldeado) + nota de agrega.
    Sin clave evidencia_v2 (fila pre-A4) => se cuenta aparte (pre_a4), no
    se inventa. A6-live: fila via=decide => bucket vive_v2 SIN clasificar
    en la direccion sombra (ni vocabulario ni invariantes: el veredicto
    ES el vivo, no una sombra). A6-r1 F1: via=decide se PARTE por
    abstencion_v2 (decidido-por-v2 vs decidido-por-fallback-v1:
    mezclarlos mentiria el rollout). A6-r2 B3: la fila decide CON
    contrafactual v1 se clasifica ADEMAS en la direccion invertida
    (seccion propia `invertida`, sin mezclar sentidos)."""
    buckets: dict[str, int] = {
        "mantiene": 0,
        "quita": 0,
        "cambia_banda": 0,
        "pre_a4": 0,
        "vive_v2": 0,
        "vive_v2_fallback": 0,
    }
    por_motivo: dict[str, dict[str, int]] = {}
    inv_buckets: dict[str, int] = {"mantiene": 0, "quita": 0, "cambia_banda": 0}
    inv_motivo: dict[str, dict[str, int]] = {}
    inv_n = 0
    for fila in filas:
        frozen = fila.get("evidencia_v2")
        if not isinstance(frozen, dict) or "veredicto" not in frozen:
            buckets["pre_a4"] += 1
            continue
        if frozen.get("via") == "decide":
            if frozen.get("abstencion_v2") is not None:
                buckets["vive_v2_fallback"] += 1
            else:
                buckets["vive_v2"] += 1
            cf_v1 = fila.get("bandas_v1")
            if isinstance(cf_v1, dict) and "veredicto" in cf_v1:
                inv_n += 1
                inv = clasifica(
                    fila["kind"],
                    fila["motivo"],
                    fila.get("factor"),
                    cf_v1["veredicto"],
                    motivos=_MOTIVOS_V1,
                    etiqueta="v1",
                )
                inv_buckets[inv] += 1
                motivo = _FOLD_MOTIVO.get(fila["motivo"], fila["motivo"])
                celda = inv_motivo.setdefault(
                    motivo, {"mantiene": 0, "quita": 0, "cambia_banda": 0}
                )
                celda[inv] += 1
            continue
        bucket = clasifica(fila["kind"], fila["motivo"], fila.get("factor"), frozen["veredicto"])
        buckets[bucket] += 1
        motivo = _FOLD_MOTIVO.get(fila["motivo"], fila["motivo"])
        celda = por_motivo.setdefault(motivo, {"mantiene": 0, "quita": 0, "cambia_banda": 0})
        celda[bucket] += 1
    return {
        "decisiones": len(filas),
        "buckets": buckets,
        "por_motivo_v1": por_motivo,
        "invertida": {
            "decisiones": inv_n,
            "buckets": inv_buckets,
            "por_motivo_v2": inv_motivo,
        },
        "agrega_puro": (
            "NO MEDIBLE: los no-op no tienen fila donde congelar el "
            "contrafactual, en ninguna direccion (estructural, no cero)."
        ),
    }


def _filas(conn, platform: str, cycle: int | None, since: str | None) -> list[dict]:
    sql = _SQL_DECISIONES
    params: list = [platform]
    if cycle is not None:
        sql += " AND d.cycle_id = %s"
        params.append(cycle)
    if since is not None:
        sql += " AND d.decided_at >= %s::timestamptz"
        params.append(since)
    sql += " ORDER BY d.id"
    filas = []
    for _id, _ciclo, kind, inputs in conn.execute(sql, params).fetchall():
        if not isinstance(inputs, dict):
            continue
        filas.append(
            {
                "id": _id,
                "kind": kind,
                "motivo": inputs.get("motivo"),
                "factor": inputs.get("factor"),
                "evidencia_v2": inputs.get("evidencia_v2"),
                "bandas_v1": inputs.get("bandas_v1"),
                "inputs": inputs,
            }
        )
    return filas


def _verifica_bloque(fila: dict, clave: str, reproduce) -> str | None:
    """Re-decide UN bloque contrafactual y exige igualdad con lo congelado.
    Devuelve el mensaje de falla o None (lane-8-continuo, ambas direcciones)."""
    frozen = fila.get(clave)
    if not isinstance(frozen, dict):
        return None
    try:
        rejugado = reproduce(fila["inputs"])
    except Exception as exc:
        return f"decision {fila['id']}: replay {clave} fallo: {scrub(str(exc))}"
    if rejugado != frozen["veredicto"]:
        return (
            f"decision {fila['id']}: veredicto {clave} congelado != rejugado"
            f" (frozen={frozen['veredicto']} replay={rejugado})"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compara sombra v1 vs v2 (A4).")
    parser.add_argument("--platform", required=True, choices=["amazon_us", "amazon_mx"])
    parser.add_argument("--cycle", type=int, default=None, help="solo este ciclo")
    parser.add_argument("--since", default=None, help="decided_at >= fecha (ISO)")
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="re-decide cada bloque evidencia_v2 con reproduce_evidencia_v2"
        " (contrafactual, decide y pause_intacto) y cada bloque bandas_v1"
        " con reproduce_bandas_v1, y exige igualdad con lo congelado"
        " (lane-8-continuo, ambas direcciones)",
    )
    parser.add_argument("--json", action="store_true", help="salida JSON")
    args = parser.parse_args(argv)
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        print("ORBIT_DSN_READ no esta definido: fail-closed, cero lecturas", file=sys.stderr)
        return 2
    try:
        conn = connect(dsn)
    except Exception as exc:  # noqa: BLE001 - connect ya redacta el DSN
        print(f"no se pudo conectar: {scrub(str(exc))}", file=sys.stderr)
        return 1
    try:
        filas = _filas(conn, args.platform, args.cycle, args.since)
    except Exception as exc:
        print(f"lectura fallida: {scrub(str(exc))}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    if args.verificar:
        for fila in filas:
            for clave, reproduce in (
                ("evidencia_v2", reproduce_evidencia_v2),
                ("bandas_v1", reproduce_bandas_v1),
            ):
                if (falla := _verifica_bloque(fila, clave, reproduce)) is not None:
                    print(falla, file=sys.stderr)
                    return 1
    try:
        resumen = resume(filas)
    except ValueError as exc:
        print(f"vocabulario/invariante: {scrub(str(exc))}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(resumen, indent=2, ensure_ascii=False))
        return 0
    print(f"decisiones: {resumen['decisiones']}")
    for bucket, n in resumen["buckets"].items():
        print(f"  {bucket}: {n}")
    print("por motivo v1:")
    for motivo, celdas in sorted(resumen["por_motivo_v1"].items()):
        print(f"  {motivo}: {celdas}")
    inv = resumen["invertida"]
    print(f"invertida (vivo v2 vs v1, decisiones: {inv['decisiones']}):")
    for bucket, n in inv["buckets"].items():
        print(f"  {bucket}: {n}")
    print("por motivo v2:")
    for motivo, celdas in sorted(inv["por_motivo_v2"].items()):
        print(f"  {motivo}: {celdas}")
    print(f"agrega_puro: {resumen['agrega_puro']}")
    if args.verificar:
        print("verificar: OK (ambos bloques reproducen exacto)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
