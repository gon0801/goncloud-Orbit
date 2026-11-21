"""Re-ancla goals activos de Mexico (S3 -> S4) preservando el P* vigente.

Herramienta operativa para acompaniar la migracion 0093 (S4 caja real MX):
los goals activos en `amazon_mx` y `meli` fueron sembrados sobre la escala S3
(ingreso sin IVA y ReferralFee sin IVA en Amazon MX). Cambiar la politica sin
re-expresar esos goals haria que el motor en `live` viera saltos artificiales
de margen (~-2 pts en Amazon MX, ~+0.7 pts en MeLi) y disparara cambios de
precio sin cambio real de costo.

Por cada goal vigente en Mexico:
- Si `goal_s3` coincide con `m_actual_s3` (siembra "margen de hoy"), el goal
  equivalente en S4 es exactamente `m_actual_s4` (P* sigue siendo `P_actual`).
- Si `goal_s3` difiere de `m_actual_s3`, calcula `P*_s3` y evalua el margen S4
  a ese mismo precio `P*_s3`, preservando el precio objetivo al centavo.

Ceremonia (mismo patron que `tools/precio_goal.py`):
- Dry-run por omision: imprime tabla completa y huella (`--platform`).
- Escritura real: exige `--acepto-mutacion-real --huella H --go "<literal>"`,
  corre TODO O NADA en una transaccion y escribe SOLO via `app.precio.goals_write`
  (jamas SQL crudo sobre `precio_goal`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.db import OrbitDbError, connect
from app.precio.goals_write import (
    PrecioGoalInvalido,
    banda_desde_settings,
    cerrar_goal,
    config_vigente_settings,
    sembrar_goal,
)
from app.precio.objetivo import ErrorObjetivo, margen_a_precio, precio_estrella, techo_centavo

_IVA_DIVISOR_MX = Decimal("1.16")
_ISR_TASA_MX = Decimal("0.025")
_RET_IVA_TASA_MX = Decimal("0.08")
_MULT_REF_AMAZON_MX = Decimal("1.16")
_MULT_REF_MELI = Decimal("1")
_CUATRO_DECIMALES = Decimal("0.0001")
_DOS_DECIMALES = Decimal("0.01")
_LOTE_DEFAULT = "reancle_s4_2026_11"


@dataclass(frozen=True)
class FilaReancle:
    goal_id: int
    listing_id: int
    platform: str
    canal: str
    seller_sku: str
    mode: str
    goal_s3: Decimal
    m_s3: Decimal
    m_s4: Decimal
    goal_s4: Decimal
    p_actual: Decimal
    p_star_s3: Decimal
    p_star_s4: Decimal
    fuera_de_banda: bool


def _comp(componentes: list[dict[str, Any]], nombre: str) -> Decimal:
    for item in componentes:
        if item.get("nombre") == nombre and item.get("importe_normalizado") is not None:
            return Decimal(str(item["importe_normalizado"]))
    raise ValueError(f"escenario sin componente {nombre}")


def calcular_goal_equivalente_s4(
    *,
    platform: str,
    goal_s3: Decimal,
    p_actual: Decimal,
    costo: Decimal,
    fee_raw_total: Decimal,
    fee_var_raw: Decimal,
    fee_fijo: Decimal,
    envio: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Devuelve `(m_s3, m_s4, goal_s4, p_star_s3, p_star_s4)` en Decimal.

    Preserva el mismo precio objetivo `P*` al pasar de S3 a S4:
    - S3: `I = P / 1.16`, `R = 0.025 * I`, `F = ref_raw * P + fijo`
    - S4: `I = P`, `R = (0.025 + 0.08) / 1.16 * P`, `F = ref_raw * mult * P + fijo`
    """
    mult_ref = _MULT_REF_AMAZON_MX if platform == "amazon_mx" else _MULT_REF_MELI
    ref_raw = fee_var_raw / p_actual
    ref_s4 = ref_raw * mult_ref
    tasa_ret_s4 = (_ISR_TASA_MX + _RET_IVA_TASA_MX) / _IVA_DIVISOR_MX

    m_s3 = margen_a_precio(
        p_actual, costo, ref_raw, fee_fijo, envio, _ISR_TASA_MX, _IVA_DIVISOR_MX, True
    )
    m_s4 = margen_a_precio(
        p_actual, costo, ref_s4, fee_fijo, envio, tasa_ret_s4, Decimal("1"), False
    )

    if abs(goal_s3 - m_s3) <= Decimal("0.0005"):
        p_star_s3 = p_actual
        goal_s4 = m_s4.quantize(_CUATRO_DECIMALES, rounding=ROUND_HALF_UP)
    else:
        p_star_s3 = techo_centavo(
            precio_estrella(
                costo, fee_fijo, envio, _ISR_TASA_MX, goal_s3, _IVA_DIVISOR_MX, True, ref_raw
            )
        )
        goal_s4_exacto = margen_a_precio(
            p_star_s3, costo, ref_s4, fee_fijo, envio, tasa_ret_s4, Decimal("1"), False
        )
        goal_s4 = goal_s4_exacto.quantize(_CUATRO_DECIMALES, rounding=ROUND_HALF_UP)

    p_star_s4 = techo_centavo(
        precio_estrella(costo, fee_fijo, envio, tasa_ret_s4, goal_s4, Decimal("1"), False, ref_s4)
    )
    return (
        m_s3.quantize(_CUATRO_DECIMALES, rounding=ROUND_HALF_UP),
        m_s4.quantize(_CUATRO_DECIMALES, rounding=ROUND_HALF_UP),
        goal_s4,
        p_star_s3,
        p_star_s4,
    )


def construir_plan_reancle(conn, *, plataformas: tuple[str, ...]) -> tuple[list[FilaReancle], str]:
    """Lee goals activos MX y su ultimo escenario S3 disponible; calcula plan y huella."""
    minimo, maximo = banda_desde_settings(config_vigente_settings(conn))
    filas_db = conn.execute(
        """
        WITH ult_esc AS (
            SELECT DISTINCT ON (e.listing_id)
                   e.listing_id, e.platform, e.canal, e.seller_sku,
                   e.formula_version, e.fee_variable, e.fee_fijo, e.componentes
              FROM estimacion_escenario e
             WHERE e.estado = 'disponible'
               AND e.platform = ANY(%s)
               AND e.formula_version = 'S3'
             ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
        )
        SELECT g.id, g.listing_id, g.platform::text, g.mode::text, g.margen_goal_pct,
               u.canal::text, u.seller_sku, u.fee_variable, u.fee_fijo, u.componentes
          FROM precio_goal g
          JOIN ult_esc u ON u.listing_id = g.listing_id AND u.platform = g.platform
         WHERE g.valid_to IS NULL
           AND g.platform = ANY(%s)
         ORDER BY g.platform, g.listing_id
        """,
        (list(plataformas), list(plataformas)),
    ).fetchall()

    plan: list[FilaReancle] = []
    partes_huella: list[str] = []
    for (
        goal_id,
        listing_id,
        platform,
        mode,
        goal_s3,
        canal,
        seller_sku,
        fee_var_raw,
        fee_fijo,
        componentes,
    ) in filas_db:
        if fee_var_raw is None or fee_fijo is None:
            raise ValueError(f"listing {listing_id} ({platform}) sin fee_variable/fee_fijo")
        p_actual = _comp(componentes, "precio_bruto")
        costo = _comp(componentes, "costo_normalizado")
        fee_raw = _comp(componentes, "fee_total")
        envio = _comp(componentes, "logistica")
        m_s3, m_s4, goal_s4, p_star_s3, p_star_s4 = calcular_goal_equivalente_s4(
            platform=platform,
            goal_s3=Decimal(goal_s3),
            p_actual=p_actual,
            costo=costo,
            fee_raw_total=fee_raw,
            fee_var_raw=Decimal(fee_var_raw),
            fee_fijo=Decimal(fee_fijo),
            envio=envio,
        )
        fuera = not (minimo <= goal_s4 <= maximo)
        plan.append(
            FilaReancle(
                goal_id=goal_id,
                listing_id=listing_id,
                platform=platform,
                canal=canal,
                seller_sku=seller_sku or "",
                mode=mode,
                goal_s3=Decimal(goal_s3),
                m_s3=m_s3,
                m_s4=m_s4,
                goal_s4=goal_s4,
                p_actual=p_actual,
                p_star_s3=p_star_s3,
                p_star_s4=p_star_s4,
                fuera_de_banda=fuera,
            )
        )
        partes_huella.append(f"{goal_id}:{listing_id}:{platform}:{mode}:{goal_s3}->{goal_s4}")

    huella = hashlib.sha256("|".join(partes_huella).encode()).hexdigest()[:16]
    return plan, huella


def _fmt_pct(fraccion: Decimal) -> str:
    return str((fraccion * Decimal("100")).quantize(_DOS_DECIMALES, rounding=ROUND_HALF_UP))


def aplicar_reancle(
    conn,
    *,
    plan: list[FilaReancle],
    go_literal: str,
    lote: str = _LOTE_DEFAULT,
) -> int:
    """Cierra goals S3 y siembra goals S4 equivalentes via `goals_write`, todo o nada."""
    fuera = [f.listing_id for f in plan if f.fuera_de_banda]
    if fuera:
        raise PrecioGoalInvalido(f"goals S4 fuera de banda, abortando: {fuera}")
    escritos = 0
    with conn.transaction():
        for fila in plan:
            cerrar_goal(conn, listing_id=fila.listing_id, platform=fila.platform)
            sembrar_goal(
                conn,
                listing_id=fila.listing_id,
                platform=fila.platform,
                goal_pct=_fmt_pct(fila.goal_s4),
                mode=fila.mode,
                go_literal=go_literal if fila.mode == "live" else None,
                creado_por="reancle_s4",
                origen="cambio_de_modo",
                lote=lote,
                m_referencia=fila.m_s4,
            )
            escritos += 1
    return escritos


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-ancla goals activos de Mexico (S3 -> S4) preservando P*."
    )
    parser.add_argument(
        "--platform",
        choices=("amazon_mx", "meli", "todas"),
        default="todas",
        help="Plataforma MX a evaluar (default: todas).",
    )
    parser.add_argument(
        "--acepto-mutacion-real",
        action="store_true",
        help="Aplica los cambios reales en precio_goal (requiere --huella y --go).",
    )
    parser.add_argument("--huella", help="Huella exacta obtenida en el dry-run.")
    parser.add_argument("--go", dest="go_literal", help="Literal de aprobacion para goals live.")
    parser.add_argument("--lote", default=_LOTE_DEFAULT, help="Etiqueta de lote de siembra.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.acepto_mutacion_real and (args.huella or args.go_literal):
        print("ERROR: --huella y --go requieren --acepto-mutacion-real", file=sys.stderr)
        return 2
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        print("ERROR: ORBIT_DSN_ADMIN no esta en el entorno", file=sys.stderr)
        return 2
    plataformas = ("amazon_mx", "meli") if args.platform == "todas" else (args.platform,)
    try:
        with connect(dsn) as conn:
            plan, huella = construir_plan_reancle(conn, plataformas=plataformas)
            print(
                "goal_id|listing_id|platform|canal|sku|mode|goal_s3%|goal_s4%|delta_pts|"
                "p_actual|p_star_s3|p_star_s4|banda"
            )
            for f in plan:
                delta = (f.goal_s4 - f.goal_s3) * Decimal("100")
                print(
                    f"{f.goal_id}|{f.listing_id}|{f.platform}|{f.canal}|{f.seller_sku}|"
                    f"{f.mode}|{_fmt_pct(f.goal_s3)}|{_fmt_pct(f.goal_s4)}|{delta:+.2f}|"
                    f"{f.p_actual:.2f}|{f.p_star_s3:.2f}|{f.p_star_s4:.2f}|"
                    f"{'FUERA' if f.fuera_de_banda else 'ok'}"
                )
            print(f"RESUMEN total={len(plan)} huella={huella}")
            if not args.acepto_mutacion_real:
                return 0
            if args.huella != huella:
                print(
                    f"ERROR: huella distinta (esperaba {args.huella!r}, actual {huella!r})",
                    file=sys.stderr,
                )
                return 2
            hay_live = any(f.mode == "live" for f in plan)
            if hay_live and not (args.go_literal and args.go_literal.strip()):
                print("ERROR: hay goals live; exige --go <literal>", file=sys.stderr)
                return 2
            n = aplicar_reancle(conn, plan=plan, go_literal=args.go_literal or "", lote=args.lote)
            print(f"APLICADO goals_reanclados={n} lote={args.lote}")
            return 0
    except (OrbitDbError, PrecioGoalInvalido, ErrorObjetivo, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
