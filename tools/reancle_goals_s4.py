"""Re-ancla y revierte goals activos de Mexico (S3 <-> S4) preservando P*.

Herramienta operativa para acompaniar la migracion 0093 (S4 caja real MX):
los goals activos en `amazon_mx` y `meli` fueron sembrados sobre la escala S3
(ingreso sin IVA y ReferralFee sin IVA en Amazon MX). Cambiar la politica sin
re-expresar esos goals haria que el motor en `live` viera saltos artificiales
de margen (~-2 pts en Amazon MX, ~+0.7 pts en MeLi) y disparara cambios de
precio sin cambio real de costo.

Reglas de seguridad:
- Lee las tasas S4 desde `estimacion_politica_version` via
  `resolver_politica_aplicable` (Regla 2, cero tasas hardcodeadas) y exige que
  `S4` este vigente antes de construir o aplicar el re-anclaje.
- Idempotente: excluye goals ya escritos por `reancle_s4` y goals creados
  cuando ya existia un escenario S4 para esa publicacion (nunca convierte dos
  veces la misma meta).
- Reversible (`--revertir`): restaura cada meta activa de un lote `reancle_s4`
  a la meta S3 inmediatamente anterior cerrada el mismo dia o antes, via
  `app.precio.goals_write` (nunca SQL crudo sobre `precio_goal`).
- Conexion `autocommit=True` (espejo de `tools/precio_goal.py`): cada
  `with conn.transaction():` es una transaccion raiz durable, no un SAVEPOINT.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import UTC
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.db import OrbitDbError, connect
from app.estimacion_repository import resolver_politica_aplicable
from app.estimacion_venta import PoliticaCalculo
from app.precio.goals_write import (
    PrecioGoalInvalido,
    banda_desde_settings,
    cerrar_goal,
    config_vigente_settings,
    sembrar_goal,
)
from app.precio.objetivo import ErrorObjetivo, margen_a_precio, precio_estrella, techo_centavo

_CUATRO_DECIMALES = Decimal("0.0001")
_DOS_DECIMALES = Decimal("0.01")
_LOTE_DEFAULT = "reancle_s4_2026_11"
_CREADO_POR_REANCLE = "reancle_s4"
_CREADO_POR_REVERSA = "reancle_s4_reversa"
_UNIVERSOS_POR_PLATAFORMA: dict[str, tuple[str, ...]] = {
    "amazon_mx": ("amazon_mx/fba", "amazon_mx/fbm"),
    "meli": ("meli/meli",),
}


@dataclass(frozen=True)
class TasasPoliticaS4:
    iva_divisor: Decimal
    isr_tasa: Decimal
    retencion_iva_tasa: Decimal
    referral_fee_iva_multiplicador: Decimal
    precio_incluye_iva: bool


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


@dataclass(frozen=True)
class FilaReversaReancle:
    goal_s4_id: int
    goal_s3_id: int
    listing_id: int
    platform: str
    mode: str
    goal_s4: Decimal
    goal_s3_restaurar: Decimal
    m_referencia_s3: Decimal | None


def _comp(componentes: list[dict[str, Any]], nombre: str) -> Decimal:
    for item in componentes:
        if item.get("nombre") == nombre and item.get("importe_normalizado") is not None:
            return Decimal(str(item["importe_normalizado"]))
    raise ValueError(f"escenario sin componente {nombre}")


def _tasas_de_politica_s4(politica: PoliticaCalculo) -> TasasPoliticaS4:
    if politica.formula_version != "S4":
        raise ValueError(
            f"politica vigente de {politica.universo} es {politica.formula_version!r}, no 'S4'"
        )
    s = politica.settings
    return TasasPoliticaS4(
        iva_divisor=Decimal(str(s["iva_divisor"])),
        isr_tasa=Decimal(str(s["isr_tasa"])),
        retencion_iva_tasa=Decimal(str(s["retencion_iva_tasa"])),
        referral_fee_iva_multiplicador=Decimal(str(s["referral_fee_iva_multiplicador"])),
        precio_incluye_iva=bool(s["precio_incluye_iva"]),
    )


def _cargar_politicas_s4(
    conn,
    *,
    plataformas: tuple[str, ...],
) -> dict[str, TasasPoliticaS4]:
    """Lee politicas vigentes en BD; aborta si algun universo pedido no esta en S4."""
    ahora = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0].replace(tzinfo=UTC)
    hoy = ahora.date()
    tasas_por_universo: dict[str, TasasPoliticaS4] = {}
    for plat in plataformas:
        for universo in _UNIVERSOS_POR_PLATAFORMA[plat]:
            res = resolver_politica_aplicable(conn, fecha=hoy, universo=universo, observed_at=ahora)
            if res.politica is None:
                raise ValueError(
                    f"sin politica S4 vigente para {universo}: {res.motivo} (aplica 0093 primero)"
                )
            tasas_por_universo[universo] = _tasas_de_politica_s4(res.politica)
    return tasas_por_universo


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
    tasas_s4: TasasPoliticaS4 | None = None,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Devuelve `(m_s3, m_s4, goal_s4, p_star_s3, p_star_s4)` en Decimal.

    Preserva el mismo precio objetivo `P*` al pasar de S3 a S4 usando las tasas
    de la politica S4 vigente.
    """
    _ = fee_raw_total
    if tasas_s4 is None:
        tasas_s4 = TasasPoliticaS4(
            iva_divisor=Decimal("1.16"),
            isr_tasa=Decimal("0.025"),
            retencion_iva_tasa=Decimal("0.08"),
            referral_fee_iva_multiplicador=(
                Decimal("1.16") if platform == "amazon_mx" else Decimal("1")
            ),
            precio_incluye_iva=True,
        )
    divisor_fiscal = tasas_s4.iva_divisor if tasas_s4.precio_incluye_iva else Decimal("1")
    ref_raw = fee_var_raw / p_actual
    ref_s4 = ref_raw * tasas_s4.referral_fee_iva_multiplicador
    tasa_ret_s4 = (tasas_s4.isr_tasa + tasas_s4.retencion_iva_tasa) / divisor_fiscal

    m_s3 = margen_a_precio(
        p_actual,
        costo,
        ref_raw,
        fee_fijo,
        envio,
        tasas_s4.isr_tasa,
        tasas_s4.iva_divisor,
        tasas_s4.precio_incluye_iva,
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
                costo,
                fee_fijo,
                envio,
                tasas_s4.isr_tasa,
                goal_s3,
                tasas_s4.iva_divisor,
                tasas_s4.precio_incluye_iva,
                ref_raw,
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
    """Lee goals activos S3 de MX y su ultimo escenario S3; exige politica S4 activa."""
    tasas_por_universo = _cargar_politicas_s4(conn, plataformas=plataformas)
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
           AND g.creado_por <> %s
           AND NOT EXISTS (
               SELECT 1
                 FROM estimacion_escenario e4
                WHERE e4.listing_id = g.listing_id
                  AND e4.platform = g.platform
                  AND e4.formula_version = 'S4'
                  AND e4.estado = 'disponible'
                  AND e4.observed_at <= g.created_at
           )
         ORDER BY g.platform, g.listing_id
        """,
        (list(plataformas), list(plataformas), _CREADO_POR_REANCLE),
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
        universo = f"{platform}/{canal}"
        tasas_s4 = tasas_por_universo.get(universo)
        if tasas_s4 is None:
            raise ValueError(f"sin politica S4 cargada para universo {universo}")
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
            tasas_s4=tasas_s4,
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


def construir_plan_reversa_reancle(
    conn,
    *,
    plataformas: tuple[str, ...],
    lote: str = _LOTE_DEFAULT,
) -> tuple[list[FilaReversaReancle], str]:
    """Planea la restauracion de goals activos escritos por `reancle_s4` al S3 previo."""
    filas_db = conn.execute(
        """
        SELECT g.id, g.listing_id, g.platform::text, g.mode::text, g.margen_goal_pct,
               prev.id, prev.margen_goal_pct, prev.m_referencia
          FROM precio_goal g
          JOIN LATERAL (
              SELECT p.id, p.margen_goal_pct, p.m_referencia
                FROM precio_goal p
               WHERE p.listing_id = g.listing_id
                 AND p.platform = g.platform
                 AND p.id < g.id
                 AND p.creado_por <> %s
               ORDER BY p.id DESC
               LIMIT 1
          ) prev ON true
         WHERE g.valid_to IS NULL
           AND g.platform = ANY(%s)
           AND g.creado_por = %s
           AND g.lote = %s
         ORDER BY g.platform, g.listing_id
        """,
        (_CREADO_POR_REANCLE, list(plataformas), _CREADO_POR_REANCLE, lote),
    ).fetchall()
    plan: list[FilaReversaReancle] = []
    partes: list[str] = []
    for g4_id, lid, plat, mode, g4_pct, g3_id, g3_pct, m_ref_s3 in filas_db:
        plan.append(
            FilaReversaReancle(
                goal_s4_id=g4_id,
                goal_s3_id=g3_id,
                listing_id=lid,
                platform=plat,
                mode=mode,
                goal_s4=Decimal(g4_pct),
                goal_s3_restaurar=Decimal(g3_pct),
                m_referencia_s3=Decimal(m_ref_s3) if m_ref_s3 is not None else None,
            )
        )
        partes.append(f"rev:{g4_id}->{g3_id}:{lid}:{plat}:{mode}:{g4_pct}->{g3_pct}")
    huella = hashlib.sha256("|".join(partes).encode()).hexdigest()[:16]
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
                creado_por=_CREADO_POR_REANCLE,
                origen="cambio_de_modo",
                lote=lote,
                m_referencia=fila.m_s4,
            )
            escritos += 1
    return escritos


def aplicar_reversa_reancle(
    conn,
    *,
    plan: list[FilaReversaReancle],
    go_literal: str,
    lote: str = _LOTE_DEFAULT,
) -> int:
    """Revierte un lote `reancle_s4` restaurando la meta S3 previa via `goals_write`."""
    escritos = 0
    lote_rev = f"{lote}_reversa"
    with conn.transaction():
        for fila in plan:
            cerrar_goal(conn, listing_id=fila.listing_id, platform=fila.platform)
            sembrar_goal(
                conn,
                listing_id=fila.listing_id,
                platform=fila.platform,
                goal_pct=_fmt_pct(fila.goal_s3_restaurar),
                mode=fila.mode,
                go_literal=go_literal if fila.mode == "live" else None,
                creado_por=_CREADO_POR_REVERSA,
                origen="cambio_de_modo",
                lote=lote_rev,
                m_referencia=fila.m_referencia_s3,
            )
            escritos += 1
    return escritos


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-ancla o revierte goals activos de Mexico (S3 <-> S4) preservando P*."
    )
    parser.add_argument(
        "--platform",
        choices=("amazon_mx", "meli", "todas"),
        default="todas",
        help="Plataforma MX a evaluar (default: todas).",
    )
    parser.add_argument(
        "--revertir",
        action="store_true",
        help="Revierte un lote previo de reancle_s4 a las metas S3 anteriores.",
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
        with connect(dsn, autocommit=True) as conn:
            if args.revertir:
                plan_rev, huella = construir_plan_reversa_reancle(
                    conn, plataformas=plataformas, lote=args.lote
                )
                print("goal_s4_id|goal_s3_id|listing_id|platform|mode|goal_s4%|goal_s3_restaurar%")
                for f in plan_rev:
                    print(
                        f"{f.goal_s4_id}|{f.goal_s3_id}|{f.listing_id}|{f.platform}|{f.mode}|"
                        f"{_fmt_pct(f.goal_s4)}|{_fmt_pct(f.goal_s3_restaurar)}"
                    )
                print(f"RESUMEN_REVERSA total={len(plan_rev)} huella={huella}")
                if not args.acepto_mutacion_real:
                    return 0
                if args.huella != huella:
                    print(
                        f"ERROR: huella distinta (esperaba {args.huella!r}, actual {huella!r})",
                        file=sys.stderr,
                    )
                    return 2
                if any(f.mode == "live" for f in plan_rev) and not (
                    args.go_literal and args.go_literal.strip()
                ):
                    print("ERROR: hay goals live; exige --go <literal>", file=sys.stderr)
                    return 2
                n = aplicar_reversa_reancle(
                    conn, plan=plan_rev, go_literal=args.go_literal or "", lote=args.lote
                )
                print(f"REVERTIDO goals_restaurados={n} lote={args.lote}_reversa")
                return 0

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
            if any(f.mode == "live" for f in plan) and not (
                args.go_literal and args.go_literal.strip()
            ):
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
