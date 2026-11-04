#!/usr/bin/env python3
"""Compara sombra manual vs derivado del target de margen (ORBIT 06 2.3, D-2.3.8).

Reejecuta el motor de bids sobre las decisiones bid de UN ciclo con sus
inputs CONGELADOS, una vez con el target manual y otra con el derivado, y
devuelve la tabla por entidad (kind/new/factor bajo cada target +
cambia_banda) y el resumen (cuantas cambian de banda). Insumo del ciclo
sombra comparado del spec §10 (la 2.4 lo corre; el go del dueno sale de ahi).

Cero mutaciones. DSN: ORBIT_DSN_READ via app.db.connect. SOLO SELECT.

La reconstruccion de inputs NO se duplica aqui: `compara()` llama a la API
publica `replay.replay_bid_con_target` (el mismo codigo del spot-check con
el target inyectado) y es por esa via que `bid.decide_bid` se reejecuta.
Matiz declarado: la cascada del ciclo resuelve el target POR ENTIDAD (un
goal de campana puede ganarle al peldano de plataforma); este tool compara
los dos valores DEL PELDANO sobre las mismas entradas, no re-resuelve la
cascada (inputs.target_acos_pct_usado muestra lo que el ciclo uso de verdad).

USO:
  python tools/compara_target_margen.py --platform amazon_mx
    [--cycle 12] [--manual 20 --derivado 17.5]

Defaults (D-2.3.8): derivado = notes.target.target_aplicado del ciclo,
manual = notes.target.setting del ciclo (el setting que la cascada usa
cuando el peldano no gana). Sin setting y sin --manual el tool falla en
voz alta (regla 3: el manual no se inventa).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect  # noqa: E402
from app.optimizer.replay import replay_bid_con_target  # noqa: E402
from app.redaction import scrub  # noqa: E402

_SQL_CICLO = """
SELECT id, platform, notes FROM optimizer_cycle WHERE id = %s
"""

_SQL_ULTIMO_CICLO = """
SELECT id FROM optimizer_cycle
 WHERE platform = %s AND motor = 'ads_optimizer'
 ORDER BY id DESC LIMIT 1
"""

_SQL_BIDS_CICLO = """
SELECT d.id, d.ad_entity_id, d.inputs,
       e.kind AS entidad_kind, e.keyword_text, e.name, e.external_id
  FROM decision d JOIN ad_entity e ON e.id = d.ad_entity_id
 WHERE d.cycle_id = %s AND d.kind = 'bid'
 ORDER BY d.id
"""


def _a_decimal(valor, nombre: str) -> Decimal:
    """Decimal estricto desde CLI o notes (regla 4: jamas float)."""
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{nombre} ilegible como numero: {valor!r}") from None
    if not numero.is_finite() or numero <= 0:
        raise ValueError(f"{nombre} fuera de rango (debe ser > 0): {valor!r}")
    return numero


def _notas_target(notes) -> dict | None:
    """notes.target del ciclo (formato mixto tolerado, como _parse_notes)."""
    if isinstance(notes, dict):
        datos = notes
    else:
        try:
            datos = json.loads(notes)
        except (ValueError, TypeError):
            return None
    if not isinstance(datos, dict):
        return None
    bloque = datos.get("target")
    return bloque if isinstance(bloque, dict) else None


def resuelve_targets(
    notas_target: dict | None,
    manual_arg: str | None,
    derivado_arg: str | None,
) -> tuple[Decimal, Decimal]:
    """Targets a comparar, puros y testeables (D-2.3.8). Falla en voz alta
    cuando alguno no se puede derivar (ValueError con el motivo)."""
    notas_target = notas_target if isinstance(notas_target, dict) else {}
    if derivado_arg is not None:
        derivado = _a_decimal(derivado_arg, "derivado")
    else:
        aplicado = notas_target.get("target_aplicado")
        if aplicado is None:
            raise ValueError(
                "sin derivado: el ciclo no trae target_aplicado en "
                "notes.target y no se paso --derivado"
            )
        derivado = _a_decimal(aplicado, "derivado")
    if manual_arg is not None:
        manual = _a_decimal(manual_arg, "manual")
    else:
        setting = notas_target.get("setting")
        if setting is None:
            raise ValueError(
                "sin manual: el ciclo no trae setting en notes.target y no "
                "se paso --manual; el setting no se inventa"
            )
        manual = _a_decimal(setting, "manual")
    return manual, derivado


def etiqueta_entidad(fila: dict) -> str:
    """Identidad legible de la entidad para la tabla (cuales cambian)."""
    for clave in ("keyword_text", "name", "external_id"):
        valor = fila.get(clave)
        if valor:
            return f"{fila.get('entidad_kind')}:{valor}"
    return f"{fila.get('entidad_kind')}:{fila.get('ad_entity_id')}"


def _celda(valor) -> str:
    """Celda de la tabla: ausente = '-' (regla 3: jamas un 0 inventado)."""
    return str(valor) if valor is not None else "-"


def _fila_dic(cur, fila) -> dict:
    cols = [c.name for c in cur.description]
    return dict(zip(cols, fila, strict=True))


def renglones(
    entradas: list[dict], manual: Decimal, derivado: Decimal
) -> tuple[list[dict], int, int]:
    """Nucleo puro de la tabla (testeable sin base): por entrada con inputs
    congelados, kind/new/factor bajo cada target + cambia_banda (difieren
    kind o factor = nivel banda, literal del spec §10; ambos new quedan en
    la fila para que el clamp absorbido sea auditable). Devuelve
    (filas, cambian, errores). Una entrada con inputs corruptos no tumba
    la tabla: queda con error visible y cuenta en errores (ruidoso, jamas
    silencioso)."""
    filas: list[dict] = []
    cambian = 0
    errores = 0
    for entrada in entradas:
        fila: dict = {
            "decision": entrada.get("id"),
            "entidad": etiqueta_entidad(entrada),
            "error": None,
        }
        try:
            inputs = entrada.get("inputs")
            if not isinstance(inputs, dict):
                raise ValueError("inputs ausentes o no-dict")
            res_m = replay_bid_con_target(inputs, manual)
            res_d = replay_bid_con_target(inputs, derivado)
        except Exception as exc:  # noqa: BLE001 - ruidoso en la fila (docstring)
            fila.update(
                {
                    "kind_manual": None,
                    "new_manual": None,
                    "factor_manual": None,
                    "kind_derivado": None,
                    "new_derivado": None,
                    "factor_derivado": None,
                    "cambia_banda": False,
                    "error": scrub(str(exc)) or "error",
                }
            )
            errores += 1
        else:
            cambia = (res_m.kind, res_m.factor) != (res_d.kind, res_d.factor)
            cambian += int(cambia)
            fila.update(
                {
                    "kind_manual": res_m.kind,
                    "new_manual": res_m.new_value,
                    "factor_manual": res_m.factor,
                    "kind_derivado": res_d.kind,
                    "new_derivado": res_d.new_value,
                    "factor_derivado": res_d.factor,
                    "cambia_banda": cambia,
                }
            )
        filas.append(fila)
    return filas, cambian, errores


def compara(
    conn,
    cycle_id: int,
    *,
    manual: str | None = None,
    derivado: str | None = None,
) -> tuple[list[dict], dict]:
    """Tabla sombra del ciclo (D-2.3.8, la fija el rojo (g)): lee el ciclo
    (plataforma + notes.target para los defaults) y sus decisiones bid con
    inputs congelados; reejecuta cada una bajo manual y derivado con
    `renglones()`. Devuelve (filas, resumen con decisiones/cambian_banda/
    errores). Ciclo inexistente o targets sin Default -> ValueError en voz
    alta (regla 3)."""
    cur = conn.execute(_SQL_CICLO, (cycle_id,))
    cols = [c.name for c in cur.description]
    cruda = cur.fetchone()
    if cruda is None:
        raise ValueError(f"ciclo inexistente: {cycle_id}")
    ciclo = dict(zip(cols, cruda, strict=True))
    manual_dec, derivado_dec = resuelve_targets(_notas_target(ciclo["notes"]), manual, derivado)
    cur = conn.execute(_SQL_BIDS_CICLO, (cycle_id,))
    cols = [c.name for c in cur.description]
    entradas = [dict(zip(cols, f, strict=True)) for f in cur.fetchall()]
    filas, cambian, errores = renglones(entradas, manual_dec, derivado_dec)
    resumen = {
        "ciclo": cycle_id,
        "plataforma": ciclo["platform"],
        "manual": manual_dec,
        "derivado": derivado_dec,
        "decisiones": len(filas),
        "cambian_banda": cambian,
        "errores": errores,
    }
    return filas, resumen


def _imprime(filas: list[dict], resumen: dict) -> None:
    print(f"# compara target margen: ciclo {resumen['ciclo']} {resumen['plataforma']}")
    print(f"# manual={resumen['manual']} derivado={resumen['derivado']}")
    print()
    print("| decision | entidad | kind_m | new_m | factor_m | kind_d | new_d | factor_d | cambia |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in filas:
        print(
            f"| {r['decision']} | {r['entidad']} | {_celda(r['kind_manual'])} | "
            f"{_celda(r['new_manual'])} | {_celda(r['factor_manual'])} | "
            f"{_celda(r['kind_derivado'])} | {_celda(r['new_derivado'])} | "
            f"{_celda(r['factor_derivado'])} | {_celda(r['cambia_banda'])} |"
            + (f" <!-- error: {r['error']} -->" if r["error"] else "")
        )
    print()
    print(
        f"cambian de banda: {resumen['cambian_banda']} de {resumen['decisiones']} "
        f"decisiones bid (errores: {resumen['errores']})"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tabla sombra manual vs derivado (ORBIT 06 2.3, solo lectura).",
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=("amazon_mx", "amazon_us"),
        help="plataforma (para resolver el ultimo ciclo cuando falta --cycle)",
    )
    parser.add_argument(
        "--cycle", type=int, default=None, help="ciclo a comparar (default: ultimo)"
    )
    parser.add_argument("--manual", default=None, help="target manual (default: setting del ciclo)")
    parser.add_argument(
        "--derivado", default=None, help="target derivado (default: aplicado del ciclo)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
        with conn:
            if args.cycle is not None:
                cycle_id = args.cycle
            else:
                cur = conn.execute(_SQL_ULTIMO_CICLO, (args.platform,))
                ultima = cur.fetchone()
                if ultima is None:
                    print(f"sin ciclos ads_optimizer para {args.platform}", file=sys.stderr)
                    return 2
                cycle_id = ultima[0]
            try:
                filas, resumen = compara(conn, cycle_id, manual=args.manual, derivado=args.derivado)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
    except Exception as exc:  # noqa: BLE001 - lectura fallida, ruidosa
        print(f"lectura fallida: {scrub(str(exc))}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    _imprime(filas, resumen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
