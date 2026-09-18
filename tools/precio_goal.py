"""Siembra y cierra goals de margen del motor de precios (REPRICING 01, A.1).

Solo Postgres y solo `app_admin` (unico DSN: `ORBIT_DSN_ADMIN`), cero
Amazon: la referencia del dry-run (`m_actual`, `P*`) sale del escenario
`disponible` mas reciente del listing y de su `fee_observation`
(`derivar_ref_fijo` + `precio_estrella` de `app/precio/objetivo.py`), sin
cotizar. La escritura vive en `app.precio.goals_write` (este tool jamas
trae SQL de escritura de goals: despacha, no duplica).

Ceremonia (la de `tools/precio_reversa.py` con UNA diferencia declarada):
dry-run por omision (imprime el plan y su huella, cero escritura); la
mutacion real exige `--acepto-mutacion-real --huella H`, y ADEMAS `--go
<literal>` en `live` (`--acepto-mutacion-real --huella H --go <literal>`).
En `shadow` (y en `--cerrar`) NO hay go literal: `shadow` = dry-run +
huella + `--acepto-mutacion-real` sin `--go`, y `--go` en shadow se
rechaza (el CHECK `precio_goal_live_exige_go` exige `go_literal` vacio
fuera de `live`; "shadow sin ceremonia" del plan = sin go literal, no
sin huella). `--go`/`--huella` sin `--acepto-mutacion-real` aborta
(dry-run enganoso). `--cerrar` jamas lleva `--go` (no enciende nada).

`--mode live` sin escenario `disponible` aborta con `sin_escenario` (un
goal encendido sobre un producto que el motor no puede evaluar no se
siembra); `--mode shadow` sigue con aviso. Sin escenario, el dry-run
imprime `sin_escenario` en `m_actual` y `P*` y el chequeo del 25 % no
corre. Si hay escenario pero la fee no deriva referencia (`fee_error` o
`margen_imposible`), se imprime `m_actual` real con `P*=sin_escenario`,
se avisa y se sigue (residual declarado: el motor si puede evaluar ese
caso; solo la falta de escenario aborta `live`).

El go es POR FILA via `goals_write` (cada llamada confirma: sin
transaccion global a proposito, igual que `goals_modo_grupo.py`); un
fallo a mitad aborta con la fila, lo sembrado queda sembrado y re-correr
con ceremonia nueva termina el resto.

Conexion en autocommit; cada fila confirma al salir.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.db import OrbitDbError, connect
from app.estimacion_venta import DetalleFee
from app.precio.goals_write import (
    PrecioGoalAusente,
    PrecioGoalInvalido,
    banda_desde_settings,
    cerrar_goal,
    config_vigente_settings,
    fraccion_desde_porcentaje,
    sembrar_goal,
)
from app.precio.objetivo import (
    ErrorObjetivo,
    derivar_ref_fijo,
    precio_estrella,
    techo_centavo,
)

UMBRAL_SALTO = Decimal("0.25")


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_admin() -> str:
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        raise Abortar("ORBIT_DSN_ADMIN no esta en el entorno (unico DSN del tool)")
    return dsn


_SQL_ESCENARIO = (
    "SELECT id, contribucion_pct, componentes, fee_observation_id,"
    " oferta_observation_id, moneda FROM estimacion_escenario"
    " WHERE listing_id = %s AND platform = %s AND estado = 'disponible'"
    " ORDER BY observed_at DESC LIMIT 1"
)
_SQL_OFERTA = "SELECT price_amount, price_currency FROM estimacion_oferta_observation WHERE id = %s"
_SQL_FEE = (
    "SELECT total_fees, fee_details, quoted_price_amount, estado"
    " FROM estimacion_fee_observation WHERE id = %s"
)
_SQL_VIGENTE = (
    "SELECT id, margen_goal_pct, mode::text FROM precio_goal"
    " WHERE listing_id = %s AND platform = %s AND valid_to IS NULL"
)


def _detalle_fee(nodo: dict) -> DetalleFee:
    tasa = nodo.get("tax_amount")
    return DetalleFee(
        fee_type=nodo["fee_type"],
        final_fee=Decimal(str(nodo["final_fee"])),
        tax_amount=None if tasa is None else Decimal(str(tasa)),
        included_fee_details=tuple(
            _detalle_fee(hijo) for hijo in nodo.get("included_fee_details") or []
        ),
    )


def _componente(componentes: list, nombre: str) -> Decimal:
    for item in componentes:
        if item.get("nombre") == nombre:
            return Decimal(str(item["importe_normalizado"]))
    raise PrecioGoalInvalido(f"escenario sin componente {nombre}")


def _referencia(conn, listing_id: int, platform: str, goal: Decimal):
    """`(m_actual, p_estrella, p_actual, moneda)`; `None` donde no hay dato.

    `m_actual` sale del escenario; `P*` de `derivar_ref_fijo` sobre su
    `fee_observation` + `precio_estrella` con el goal a sembrar. `r`, el
    divisor de IVA y el flag de IVA se reconstruyen de los componentes
    congelados (`isr/ingreso`, `precio_bruto/ingreso`): sin fuentes nuevas.
    """
    esc = conn.execute(_SQL_ESCENARIO, (listing_id, platform)).fetchone()
    if esc is None:
        return (None, None, None, None)
    m_actual = esc[1]
    oferta = conn.execute(_SQL_OFERTA, (esc[4],)).fetchone()
    if oferta is None:
        return (m_actual, None, None, None)
    p_actual, moneda = oferta
    fee = conn.execute(_SQL_FEE, (esc[3],)).fetchone()
    if fee is None or fee[3] != "success" or fee[0] is None:
        return (m_actual, None, p_actual, moneda)
    try:
        detalles = tuple(_detalle_fee(nodo) for nodo in fee[1])
        ref, fijo = derivar_ref_fijo(detalles, Decimal(str(fee[0])), Decimal(str(fee[2])))
        componentes = esc[2]
        costo = _componente(componentes, "costo_normalizado")
        ingreso = _componente(componentes, "ingreso_normalizado")
        envio = _componente(componentes, "logistica")
        isr_importe = _componente(componentes, "isr")
        precio_bruto = _componente(componentes, "precio_bruto")
        if ingreso <= 0:
            raise PrecioGoalInvalido("escenario con ingreso no positivo")
        tasa_isr = isr_importe / ingreso
        divisor_iva = precio_bruto / ingreso
        estrella = precio_estrella(
            costo, fijo, envio, tasa_isr, goal, divisor_iva, divisor_iva != 1, ref
        )
    except (
        ErrorObjetivo,
        PrecioGoalInvalido,
        InvalidOperation,
        ArithmeticError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return (m_actual, None, p_actual, moneda)
    return (m_actual, techo_centavo(estrella), p_actual, moneda)


@dataclass
class FilaPlan:
    listing_id: int
    platform: str
    cerrar: bool
    goal_texto: str | None
    fraccion: Decimal | None
    goal_id: int | None
    m_actual: Decimal | None
    p_estrella: Decimal | None
    p_actual: Decimal | None
    moneda: str | None
    linea: int


def _dinero(valor: Decimal | None, moneda: str | None) -> str:
    if valor is None:
        return "sin_escenario"
    return f"{valor:.2f} {moneda}"


def _linea(fila: FilaPlan) -> str:
    if fila.cerrar:
        return (
            f"[plan] cerrar listing={fila.listing_id} platform={fila.platform}"
            f" goal_id={fila.goal_id}"
        )
    m = "sin_escenario" if fila.m_actual is None else f"{fila.m_actual:.4f}"
    return (
        f"[plan] siembra listing={fila.listing_id} platform={fila.platform}"
        f" goal={fila.fraccion} m_actual={m}"
        f" P*={_dinero(fila.p_estrella, fila.moneda)}"
        f" P_actual={_dinero(fila.p_actual, fila.moneda)}"
    )


def _huella(plan: list[FilaPlan], mode: str) -> str:
    partes = []
    for fila in plan:
        if fila.cerrar:
            partes.append(f"cerrar:{fila.listing_id}:{fila.platform}:{fila.goal_id}")
        else:
            partes.append(f"siembra:{fila.listing_id}:{fila.platform}:{fila.fraccion}:{mode}")
    return hashlib.sha256("|".join(sorted(partes)).encode()).hexdigest()[:16]


def _filas_csv(ruta: str, *, cerrar: bool) -> list[tuple[int, str, str | None, int]]:
    try:
        texto = Path(ruta).read_text(encoding="utf-8")
    except OSError as exc:
        raise Abortar(f"no se pudo leer --csv {ruta}: {exc}") from exc
    lector = csv.DictReader(texto.splitlines())
    campos = set(lector.fieldnames or [])
    if "listing_id" not in campos or "platform" not in campos:
        raise Abortar("--csv exige cabecera listing_id,platform[,goal_pct]")
    if not cerrar and "goal_pct" not in campos:
        raise Abortar("--csv para sembrar exige columna goal_pct")
    filas: list[tuple[int, str, str | None, int]] = []
    for numero, cruda in enumerate(lector, start=2):
        if all((v is None or not str(v).strip()) for v in cruda.values()):
            continue
        try:
            listing_id = int(str(cruda["listing_id"]).strip())
        except (ValueError, TypeError) as exc:
            raise Abortar(f"linea {numero}: listing_id invalido") from exc
        platform = str(cruda.get("platform") or "").strip()
        if platform not in ("amazon_mx", "amazon_us", "meli"):
            raise Abortar(f"linea {numero}: platform invalida: {platform!r}")
        goal_texto = None
        if not cerrar:
            goal_texto = str(cruda.get("goal_pct") or "").strip()
            if not goal_texto:
                raise Abortar(f"linea {numero}: goal_pct vacio")
        filas.append((listing_id, platform, goal_texto, numero))
    if not filas:
        raise Abortar("--csv sin filas de datos")
    vistos: dict[tuple[int, str], int] = {}
    for listing_id, platform, _goal, numero in filas:
        clave = (listing_id, platform)
        if clave in vistos:
            raise Abortar(
                f"fila repetida (listing_id, platform) = {clave} en linea {numero}"
                f" (primera en linea {vistos[clave]}):"
                " el lote se aborta antes de la huella"
            )
        vistos[clave] = numero
    return filas


def _construir_plan(conn, entradas, *, mode: str, cerrar: bool) -> list[FilaPlan]:
    settings = config_vigente_settings(conn)
    minimo, maximo = banda_desde_settings(settings)
    plan: list[FilaPlan] = []
    for listing_id, platform, goal_texto, numero in entradas:
        prefijo = f"linea {numero}: " if numero else ""
        if cerrar:
            vigente = conn.execute(_SQL_VIGENTE, (listing_id, platform)).fetchone()
            if vigente is None:
                raise Abortar(
                    f"{prefijo}sin goal vigente para listing {listing_id} en {platform}:"
                    " nada que cerrar"
                )
            plan.append(
                FilaPlan(
                    listing_id,
                    platform,
                    True,
                    None,
                    None,
                    int(vigente[0]),
                    None,
                    None,
                    None,
                    None,
                    numero,
                )
            )
            continue
        try:
            fraccion = fraccion_desde_porcentaje(goal_texto or "")
        except PrecioGoalInvalido as exc:
            raise Abortar(f"{prefijo}{exc}") from exc
        if not minimo <= fraccion <= maximo:
            raise Abortar(
                f"{prefijo}goal {goal_texto} % (= {fraccion}) fuera de banda [{minimo}, {maximo}]"
            )
        m, estrella, p_actual, moneda = _referencia(conn, listing_id, platform, fraccion)
        plan.append(
            FilaPlan(
                listing_id,
                platform,
                False,
                goal_texto,
                fraccion,
                None,
                m,
                estrella,
                p_actual,
                moneda,
                numero,
            )
        )
    return plan


def _entradas_desde_args(args) -> list[tuple[int, str, str | None, int]]:
    """Objetivo del plan: una fila o el lote CSV (deduplicado, con linea)."""
    if args.csv is not None and args.listing_id is not None:
        raise Abortar("pasa --listing-id o --csv, no los dos")
    if args.csv is None and args.listing_id is None:
        raise Abortar("falta el objetivo: pasa --listing-id o --csv")
    if args.csv is None and args.platform is None:
        raise Abortar("falta --platform (amazon_mx|amazon_us|meli)")
    if args.cerrar and args.goal_pct is not None:
        raise Abortar("--cerrar no siembra: --goal-pct sobra")
    if not args.cerrar and args.csv is None and args.goal_pct is None:
        raise Abortar("falta --goal-pct (en por ciento, ej. 30.00)")
    if args.csv is not None:
        return _filas_csv(args.csv, cerrar=args.cerrar)
    assert args.listing_id is not None and args.platform is not None
    return [(args.listing_id, args.platform, args.goal_pct, 0)]


def _guardas_del_plan(plan: list[FilaPlan], args) -> None:
    """Salto del 25 % (siembra) y `live` sin escenario: abortan sin huella."""
    if not args.cerrar:
        for fila in plan:
            if (
                fila.p_estrella is not None
                and fila.p_actual is not None
                and fila.p_actual > 0
                and abs(fila.p_estrella - fila.p_actual) > UMBRAL_SALTO * fila.p_actual
                and not args.confirmar_salto
            ):
                salto = abs(fila.p_estrella - fila.p_actual) / fila.p_actual * 100
                raise Abortar(
                    f"salto {salto:.1f} % > 25 % en listing {fila.listing_id}"
                    f" (P*={fila.p_estrella:.2f} P_actual={fila.p_actual:.2f}):"
                    " pasa --confirmar-salto para sembrarlo"
                )
        if args.mode == "live" and any(fila.m_actual is None for fila in plan):
            raise Abortar(
                "sin_escenario: --mode live no siembra goals sobre"
                " productos que el motor no puede evaluar"
                " (usa --mode shadow o espera al escenario disponible)"
            )


def _go_con_ceremonia(conn, plan: list[FilaPlan], args, huella: str) -> int:
    """Ceremonia y mutacion: shadow/cerrar sin go, live con go literal."""
    if not args.acepto_mutacion_real:
        if args.go is not None or args.huella is not None:
            raise Abortar(
                "falto --acepto-mutacion-real: --go/--huella sin"
                " --acepto-mutacion-real no muta (dry-run enganoso)"
            )
        return 0
    if not args.huella:
        raise Abortar("falta --huella: el go exige la huella del dry-run")
    if args.huella != huella:
        raise Abortar(f"--huella {args.huella} != huella del plan {huella}: re-corre el dry-run")
    if args.cerrar or args.mode == "shadow":
        if args.go is not None:
            donde = "--cerrar" if args.cerrar else "shadow"
            raise Abortar(f"{donde} es sin go literal: --go no aplica (el go solo enciende live)")
        go_literal = None
    else:
        if not args.go or not args.go.strip():
            raise Abortar("falta --go: el go en live exige el literal del dueno")
        go_literal = args.go
    for fila in plan:
        if fila.cerrar:
            gid = cerrar_goal(conn, listing_id=fila.listing_id, platform=fila.platform)
            print(f"[hecho] cerrar goal_id={gid} listing={fila.listing_id}")
        else:
            assert fila.goal_texto is not None
            gid = sembrar_goal(
                conn,
                listing_id=fila.listing_id,
                platform=fila.platform,
                goal_pct=fila.goal_texto,
                mode=args.mode,
                go_literal=go_literal,
            )
            print(
                f"[hecho] siembra goal_id={gid} listing={fila.listing_id}"
                f" platform={fila.platform} goal={fila.fraccion} mode={args.mode}"
            )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--listing-id", type=int, default=None)
    ap.add_argument("--platform", default=None, choices=("amazon_mx", "amazon_us", "meli"))
    ap.add_argument("--goal-pct", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--mode", default="shadow", choices=("shadow", "live"))
    ap.add_argument("--cerrar", action="store_true")
    ap.add_argument("--confirmar-salto", action="store_true")
    ap.add_argument("--acepto-mutacion-real", action="store_true")
    ap.add_argument("--huella", default=None)
    ap.add_argument("--go", default=None)
    args = ap.parse_args(argv)

    try:
        entradas = _entradas_desde_args(args)
        conn = connect(_dsn_admin(), autocommit=True)
        try:
            plan = _construir_plan(conn, entradas, mode=args.mode, cerrar=args.cerrar)
            for fila in plan:
                print(_linea(fila))
                if not fila.cerrar and fila.m_actual is None and fila.p_estrella is None:
                    print(
                        f"aviso: sin_escenario para listing {fila.listing_id}"
                        f" en {fila.platform}: el dry-run no evalua salto"
                    )
            _guardas_del_plan(plan, args)
            huella = _huella(plan, args.mode)
            print(f"huella: {huella}")
            return _go_con_ceremonia(conn, plan, args, huella)
        finally:
            conn.close()
    except (Abortar, PrecioGoalInvalido, PrecioGoalAusente) as exc:
        print(f"precio_goal: {exc}", file=sys.stderr)
        return 2
    except OrbitDbError as exc:
        print(f"precio_goal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
