"""Siembra y cierra goals de margen del motor de precios (REPRICING 01, A.1).

Despachador delgado (REPRICING 02 G.1): argparse/CSV -> `Pedido` ->
`siembra.planear` / `goals_write.aplicar_plan`. Solo Postgres y solo
`app_admin` (unico DSN: `ORBIT_DSN_ADMIN`), cero Amazon: la referencia del
dry-run (`m_actual`, `P*`) sale del escenario `disponible` mas reciente
del listing y de su `fee_observation`, sin cotizar. La escritura vive en
`app.precio.goals_write` (este tool jamas trae SQL de escritura de goals:
despacha, no duplica).

El objetivo es `--listing-id`, `--sku` (se resuelve en dry-run contra
`seller_sku` + `--platform`: 0 o 2+ publicaciones abortan) o `--csv`;
los tres excluyentes. Si el listing ya tiene goal vigente, el plan
propone reemplazarlo (G.1: editar es cerrar y sembrar). Goal y `m_actual`
se imprimen en por ciento (`contribucion_pct` esta en por ciento); el
salto se confirma por listing (`--confirmar-salto <listing_id>`,
repetible).

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

El go es TODO O NADA via `goals_write.aplicar_plan` (una transaccion):
un fallo a mitad no deja ninguna fila. Resembrar el mismo dia se permite
(la fila anterior queda ANULADA).

Conexion en autocommit; el lote confirma al salir.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from app.db import OrbitDbError, connect
from app.precio.goals_write import (
    PLATAFORMAS_PRECIO_GOAL,
    BloqueoSinConfirmar,
    HuellaDistinta,
    PorIds,
    PrecioGoalAusente,
    PrecioGoalInvalido,
    aplicar_plan,
    banda_desde_settings,
    config_vigente_settings,
    fraccion_desde_porcentaje,
    leer_candidatos,
)
from app.precio.siembra import (
    FilaPlan,
    PedidoCierre,
    PedidoInvalido,
    PedidoValores,
    PlanGoals,
    ValorPorUnidad,
    planear,
)


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_admin() -> str:
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        raise Abortar("ORBIT_DSN_ADMIN no esta en el entorno (unico DSN del tool)")
    return dsn


def _dinero(importe) -> str:
    if importe is None:
        return "sin_escenario"
    return f"{importe.valor:.2f} {importe.moneda}"


def _linea(fila: FilaPlan) -> str:
    # G1: `contribucion_pct` esta en POR CIENTO (39.0000 = 39 %): goal y
    # `m_actual` se imprimen en la misma unidad (por ciento, dos decimales).
    if fila.accion == "cerrar":
        return (
            f"[plan] cerrar listing={fila.listing_id} platform={fila.plataforma}"
            f" goal_id={fila.goal_id}"
        )
    if fila.accion == "nada":
        return (
            f"[plan] nada listing={fila.listing_id} platform={fila.plataforma} motivo={fila.motivo}"
        )
    assert fila.goal_propuesto is not None
    m = "sin_escenario" if fila.m_hoy is None else f"{fila.m_hoy * 100:.2f}%"
    base = (
        f" goal={fila.goal_propuesto * 100:.2f}% m_actual={m}"
        f" P*={_dinero(fila.p_estrella)}"
        f" P_actual={_dinero(fila.p_actual)}"
    )
    if fila.accion == "reemplazar":
        assert fila.goal_actual is not None
        return (
            f"[plan] reemplaza listing={fila.listing_id} platform={fila.plataforma}"
            f" goal_actual={fila.goal_actual * 100:.2f}%{base}"
        )
    return f"[plan] siembra listing={fila.listing_id} platform={fila.plataforma}{base}"


def _filas_csv(ruta: str, *, cerrar: bool) -> list[tuple[int, str, str | None, int]]:
    try:
        # AC3: `utf-8-sig` traga el BOM del «CSV UTF-8» de Excel (si no,
        # la cabecera llega con `\ufeff` y el aborto «exige cabecera» miente).
        texto = Path(ruta).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise Abortar(f"no se pudo leer --csv {ruta}: {exc}") from exc
    lector = csv.DictReader(texto.splitlines())
    campos = set(lector.fieldnames or [])
    if "listing_id" not in campos or "platform" not in campos:
        raise Abortar("--csv exige cabecera listing_id,platform[,goal_pct]")
    if not cerrar and "goal_pct" not in campos:
        raise Abortar("--csv para sembrar exige columna goal_pct")
    filas: list[tuple[int, str, str | None, int]] = []
    # AC3: `lector.line_num` es la linea del archivo (el `DictReader` se
    # salta las vacias y `enumerate(start=2)` las descontaba del reporte).
    for cruda in lector:
        numero = lector.line_num
        if all((v is None or not str(v).strip()) for v in cruda.values()):
            continue
        try:
            listing_id = int(str(cruda["listing_id"]).strip())
        except (ValueError, TypeError) as exc:
            raise Abortar(f"linea {numero}: listing_id invalido") from exc
        platform = str(cruda.get("platform") or "").strip()
        if platform not in PLATAFORMAS_PRECIO_GOAL:
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


def _resolver_sku(conn, sku: str, platform: str) -> int:
    """S1: `seller_sku` + platform a un listing; 0 o 2+ abortan en el dry-run."""
    candidatos = conn.execute(
        "SELECT id FROM listing WHERE seller_sku = %s AND platform = %s ORDER BY id",
        (sku, platform),
    ).fetchall()
    if not candidatos:
        raise Abortar(
            f"sin publicacion para esa SKU ({sku!r} en {platform}):"
            " revisa el seller_sku y --platform"
        )
    if len(candidatos) > 1:
        ids = [c[0] for c in candidatos]
        raise Abortar(
            f"--sku {sku!r} mapea a mas de una publicacion en {platform}"
            f" (listing_id {ids}): usa --listing-id"
        )
    return int(candidatos[0][0])


def _entradas_desde_args(args) -> list[tuple[int, str, str | None, int]]:
    """Objetivo del plan: una fila o el lote CSV (deduplicado, con linea)."""
    dados = [
        nombre
        for nombre, presente in (
            ("--listing-id", args.listing_id is not None),
            ("--sku", args.sku is not None),
            ("--csv", args.csv is not None),
        )
        if presente
    ]
    if len(dados) > 1:
        raise Abortar(f"pasa solo uno: {' '.join(dados)} son excluyentes")
    if not dados:
        raise Abortar("falta el objetivo: pasa --listing-id, --sku o --csv")
    if args.csv is None and args.platform is None:
        raise Abortar(f"falta --platform ({'|'.join(PLATAFORMAS_PRECIO_GOAL)})")
    if args.cerrar and args.goal_pct is not None:
        raise Abortar("--cerrar no siembra: --goal-pct sobra")
    if args.cerrar and args.confirmar_salto:
        raise Abortar("--cerrar no siembra: --confirmar-salto sobra")
    if not args.cerrar and args.csv is None and args.goal_pct is None:
        raise Abortar("falta --goal-pct (en por ciento, ej. 30.00)")
    if args.csv is not None:
        return _filas_csv(args.csv, cerrar=args.cerrar)
    if args.sku is not None:
        return []  # se resuelve contra la base en main (necesita conn)
    assert args.listing_id is not None and args.platform is not None
    return [(args.listing_id, args.platform, args.goal_pct, 0)]


def _planear_tool(conn, entradas, args):
    """Despacho delgado: entradas -> `Pedido` -> `leer_candidatos` + `planear`.

    Devuelve `(plan, textos, banda, lineas)`: los textos (`goal_pct` por
    fila) y la banda formulan los abortos legados; `lineas` mapea cada par
    a su linea del CSV (0 = una fila).
    """
    settings = config_vigente_settings(conn)
    banda = banda_desde_settings(settings)
    lineas = {(lid, plat): numero for lid, plat, _goal, numero in entradas}
    if args.cerrar:
        pedido: PedidoCierre | PedidoValores = PedidoCierre()
        textos: dict[tuple[int, str], tuple[str, int]] = {}
    else:
        valores = []
        textos = {}
        for lid, plat, goal_texto, numero in entradas:
            prefijo = f"linea {numero}: " if numero else ""
            try:
                fraccion = fraccion_desde_porcentaje(goal_texto or "")
            except PrecioGoalInvalido as exc:
                raise Abortar(f"{prefijo}{exc}") from exc
            assert goal_texto is not None
            textos[(lid, plat)] = (goal_texto, numero)
            valores.append(ValorPorUnidad(lid, plat, fraccion))
        pedido = PedidoValores(goals=tuple(valores), mode=args.mode)
    candidatos = []
    for lid, plat, _goal, numero in entradas:
        prefijo = f"linea {numero}: " if numero else ""
        try:
            (cand,) = leer_candidatos(conn, filtro=PorIds(pares=((lid, plat),)))
        except (PrecioGoalAusente, PrecioGoalInvalido) as exc:
            raise Abortar(f"{prefijo}{exc}") from exc
        candidatos.append(cand)
    try:
        plan = planear(tuple(candidatos), pedido=pedido, banda=banda)
    except PedidoInvalido as exc:
        numero = lineas.get((exc.listing_id, exc.platform), 0)
        prefijo = f"linea {numero}: " if numero else ""
        raise Abortar(f"{prefijo}{exc}") from exc
    return (plan, textos, banda, lineas)


def _exigir_sembrable(plan: PlanGoals, args, textos, banda, lineas) -> None:
    """El tool es estricto: cualquier bloqueo aborta (legado), salvo el
    salto confirmado por listing (`--confirmar-salto`).

    G3: `--confirmar-salto <listing_id>` es por listing (repetible): solo
    los listings nombrados se saltan el chequeo; nombrar uno fuera del
    plan aborta.
    """
    if isinstance(plan.pedido, PedidoCierre):
        return
    confirmados = set(args.confirmar_salto or [])
    ajenos = sorted(lid for lid in confirmados if lid not in {f.listing_id for f in plan.filas})
    if ajenos:
        raise Abortar(f"--confirmar-salto para listings fuera del plan: {ajenos}")
    minimo, maximo = banda
    for fila in plan.filas:
        if fila.bloqueo is None:
            continue
        if fila.bloqueo == "salto_mayor_25" and fila.listing_id in confirmados:
            continue
        numero = lineas.get((fila.listing_id, fila.plataforma), 0)
        prefijo = f"linea {numero}: " if numero else ""
        if fila.bloqueo == "salto_mayor_25":
            assert fila.p_estrella is not None and fila.p_actual is not None
            estrella = fila.p_estrella.valor
            p_actual = fila.p_actual.valor
            salto = abs(estrella - p_actual) / p_actual * 100
            raise Abortar(
                f"{prefijo}salto {salto:.1f} % > 25 % en listing {fila.listing_id}"
                f" (P*={estrella:.2f} P_actual={p_actual:.2f}):"
                " pasa --confirmar-salto <listing_id> para sembrarlo"
            )
        if fila.bloqueo == "fuera_de_banda":
            texto, _numero = textos[(fila.listing_id, fila.plataforma)]
            raise Abortar(
                f"{prefijo}goal {texto} % (= {fila.goal_propuesto})"
                f" fuera de banda [{minimo}, {maximo}]"
            )
        if fila.bloqueo == "live_sin_escenario":
            raise Abortar(
                f"{prefijo}sin_escenario: --mode live no siembra goals sobre"
                " productos que el motor no puede evaluar"
                " (usa --mode shadow o espera al escenario disponible)"
            )
        if fila.bloqueo == "sin_escenario":
            raise Abortar(
                f"{prefijo}sin_escenario para listing {fila.listing_id}"
                f" en {fila.plataforma}: sin margen de hoy que copiar (no se puede sembrar)"
            )
        if fila.bloqueo == "sin_cambio":
            raise Abortar(
                f"{prefijo}sin cambios para listing {fila.listing_id}"
                f" en {fila.plataforma}: ya tiene ese goal y ese modo"
            )
        raise AssertionError(f"bloqueo desconocido: {fila.bloqueo}")


def _go_con_ceremonia(conn, plan: PlanGoals, args, textos, banda, lineas) -> int:
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
    if args.huella != plan.huella:
        raise Abortar(
            f"--huella {args.huella} != huella del plan {plan.huella}: re-corre el dry-run"
        )
    es_cierre = isinstance(plan.pedido, PedidoCierre)
    if es_cierre or args.mode == "shadow":
        if args.go is not None:
            donde = "--cerrar" if es_cierre else "shadow"
            raise Abortar(f"{donde} es sin go literal: --go no aplica (el go solo enciende live)")
        go_literal = None
    else:
        if not args.go or not args.go.strip():
            raise Abortar("falta --go: el go en live exige el literal del dueno")
        go_literal = args.go
    try:
        res = aplicar_plan(
            conn,
            plan,
            huella=args.huella,
            confirmados=frozenset(args.confirmar_salto or ()),
            go_literal=go_literal,
            actor="precio_goal",
            estricto=True,
        )
    except HuellaDistinta as exc:
        raise Abortar(
            f"--huella {args.huella} != huella del plan {exc.plan.huella}: re-corre el dry-run"
        ) from exc
    except BloqueoSinConfirmar as exc:
        # El dry-run ya exigio; si el recalculado se bloqueo entre mirar y
        # aceptar, se informa con los mismos textos legados.
        _exigir_sembrable(exc.plan, args, textos, banda, lineas)
        raise AssertionError("el plan fresco debio abortar arriba") from exc
    filas = {(f.listing_id, f.plataforma): f for f in plan.filas}
    for hecho in res.hechos:
        if hecho.accion == "cerrar":
            print(f"[hecho] cerrar goal_id={hecho.goal_id} listing={hecho.listing_id}")
        else:
            fila = filas[(hecho.listing_id, hecho.platform)]
            assert fila.goal_propuesto is not None
            pct = f"{fila.goal_propuesto * 100:.2f}%"
            if hecho.accion == "reemplazar":
                print(
                    f"[hecho] reemplaza goal_id={hecho.goal_id} cerro={hecho.cerro_id}"
                    f" listing={hecho.listing_id} platform={hecho.platform}"
                    f" goal={pct} mode={args.mode}"
                )
            else:
                print(
                    f"[hecho] siembra goal_id={hecho.goal_id} listing={hecho.listing_id}"
                    f" platform={hecho.platform} goal={pct} mode={args.mode}"
                )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--listing-id", type=int, default=None)
    ap.add_argument("--sku", default=None)
    ap.add_argument("--platform", default=None, choices=PLATAFORMAS_PRECIO_GOAL)
    ap.add_argument("--goal-pct", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--mode", default="shadow", choices=("shadow", "live"))
    ap.add_argument("--cerrar", action="store_true")
    ap.add_argument("--confirmar-salto", action="append", type=int, default=None)
    ap.add_argument("--acepto-mutacion-real", action="store_true")
    ap.add_argument("--huella", default=None)
    ap.add_argument("--go", default=None)
    args = ap.parse_args(argv)

    try:
        entradas = _entradas_desde_args(args)
        conn = connect(_dsn_admin(), autocommit=True)
        try:
            if args.sku is not None:
                assert args.platform is not None
                entradas = [
                    (
                        _resolver_sku(conn, args.sku, args.platform),
                        args.platform,
                        args.goal_pct,
                        0,
                    )
                ]
            plan, textos, banda, lineas = _planear_tool(conn, entradas, args)
            for fila in plan.filas:
                print(_linea(fila))
                if fila.accion != "cerrar" and fila.p_estrella is None and fila.motivo:
                    print(
                        f"aviso: {fila.motivo} para listing {fila.listing_id}"
                        f" en {fila.plataforma}: el salto no se evalua"
                    )
            _exigir_sembrable(plan, args, textos, banda, lineas)
            print(f"huella: {plan.huella}")
            return _go_con_ceremonia(conn, plan, args, textos, banda, lineas)
        finally:
            conn.close()
    except (Abortar, PrecioGoalInvalido, PrecioGoalAusente, PedidoInvalido) as exc:
        print(f"precio_goal: {exc}", file=sys.stderr)
        return 2
    except OrbitDbError as exc:
        print(f"precio_goal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
