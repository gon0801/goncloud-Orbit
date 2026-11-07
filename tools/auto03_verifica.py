"""Verificacion de las decisiones por producto (AUTO-03) contra los datos reales.

Corre el MISMO codigo del ciclo en solo lectura (ORBIT_DSN_READ, transaccion
READ ONLY) para que la evidencia previa a cada `live` no espere un ciclo.

- evalua --platform P [--json ruta]: app/productos_io.py y el resolutor sobre
  los datos de hoy; imprime resumen_cobertura y, con --json, guarda
  {resumen, entradas, evaluaciones}.
- replay [--json ruta | --cycle-id N|ultimo]: vuelve a correr
  resolver_producto sobre cada entrada congelada e imprime
  `replay <plataforma>: diferencias: N`. Sin argumentos toma el ultimo ciclo
  done de cada plataforma. Sale 1 con alguna diferencia, con un ciclo cuya
  meta no estaba en off y trae notes.productos.error, con menos o mas filas
  que notes.productos.productos_evaluados, o sin evaluaciones que revisar.
- concilia --platform P --n N [--json ruta]: pide a Amazon el reporte de
  productos (reports.PRODUCTOS_CFG) con un sondeo HTTP puro, sin ingest_run ni
  ads_report_result, y compara por producto gasto, clics, compras, ventas y
  ventas del mismo SKU de N productos de la ultima evaluacion (o del --json):
  los candidatos a pausa en [D-60, D-30) y los productos con meta en el tramo
  maduro del halo [D-90, D-31], con los reportes pedidos en paralelo. Sale 1
  con una diferencia de dinero mayor a 0.01 o un conteo distinto en esos
  productos, y 2 si un reporte no queda COMPLETED en 1500 s. Si no alcanzan
  N, completa con los de mas gasto en ese tramo como relleno informativo: sus
  diferencias se imprimen con sus fechas y no cambian la salida.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from itertools import zip_longest

from app import productos_io
from app.ads import descarga, reports
from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure_api import perfiles_aceptados
from app.cycle import lee_goals
from app.db import connect
from app.optimizer import productos as p

_SQL_CONFIG = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"

_SQL_CAMPANAS = """
SELECT id FROM ad_entity WHERE platform = %s::platform AND kind = 'campaign'
"""

_SQL_CICLO = """
SELECT id, platform::text, notes FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND id = %s
"""

_SQL_ULTIMO_CICLO = """
SELECT id, platform::text, notes FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND platform = %s::platform AND status = 'done'
 ORDER BY id DESC LIMIT 1
"""

_SQL_EVALUACIONES = """
SELECT entrada, resultado FROM evaluacion_producto_ciclo
 WHERE cycle_id = %s ORDER BY product_id
"""


def _conexion():
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        raise SystemExit("ORBIT_DSN_READ no esta definido")
    conn = connect(dsn, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def evalua(plataforma: str) -> dict:
    with _conexion() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        fila = conn.execute(_SQL_CONFIG).fetchone()
        settings = fila[0] if fila else {}
        campanas = [f[0] for f in conn.execute(_SQL_CAMPANAS, (plataforma,))]
        goals = lee_goals(conn, plataforma, campanas)
        entradas = productos_io.lee_entradas_productos(
            conn, plataforma, decidido_el=dt.datetime.now(dt.UTC), settings=settings, goals=goals
        )
    evaluaciones = [p.resolver_producto(e) for e in entradas]
    return {
        "resumen": productos_io.resumen_cobertura(plataforma, evaluaciones, goals),
        "entradas": [e.como_json() for e in entradas],
        "evaluaciones": [productos_io.resultado_json(ev, ()) for ev in evaluaciones],
    }


def diferencias(filas: Sequence[tuple[Mapping, Mapping]]) -> int:
    """Cuantas evaluaciones congeladas no reproduce el resolutor vigente."""
    n = 0
    for entrada, resultado in filas:
        congelada = productos_io.evaluacion_de_fila(entrada, resultado)
        n += p.resolver_producto(congelada.entrada) != congelada
    return n


def _replay_ciclo(conn, ciclo: tuple | None, plataforma: str) -> bool:
    if ciclo is None:
        print(f"replay {plataforma}: sin evaluaciones (sin ciclo done)")
        return False
    cycle_id, plataforma, notes = ciclo
    notas = (json.loads(notes) if isinstance(notes, str) else notes or {}).get("productos")
    filas = conn.execute(_SQL_EVALUACIONES, (cycle_id,)).fetchall()
    ok = True
    if notas is not None and notas.get("error"):
        print(f"replay {plataforma}: ciclo {cycle_id} con error: {notas['error']}")
        ok = False
    if notas is not None and len(filas) != notas.get("productos_evaluados"):
        print(
            f"replay {plataforma}: ciclo {cycle_id} guardo {len(filas)} evaluaciones y "
            f"evaluo {notas.get('productos_evaluados')}"
        )
        ok = False
    if not filas:
        print(f"replay {plataforma}: sin evaluaciones (ciclo {cycle_id})")
        return False
    n = diferencias(filas)
    print(f"replay {plataforma}: diferencias: {n} (ciclo {cycle_id}, {len(filas)} evaluaciones)")
    return ok and n == 0


def replay(ruta: str | None, cycle_id: str | None) -> bool:
    if ruta is not None:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        plataforma = datos["resumen"]["plataforma"]
        filas = list(zip(datos["entradas"], datos["evaluaciones"], strict=True))
        if not filas:
            print(f"replay {plataforma}: sin evaluaciones")
            return False
        n = diferencias(filas)
        print(f"replay {plataforma}: diferencias: {n}")
        return n == 0
    with _conexion() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        if cycle_id in (None, "ultimo"):
            resultados = [
                _replay_ciclo(conn, conn.execute(_SQL_ULTIMO_CICLO, (plat,)).fetchone(), plat)
                for plat in p.PLATAFORMAS
            ]
            return all(resultados)
        ciclo = conn.execute(_SQL_CICLO, (int(cycle_id),)).fetchone()
        return _replay_ciclo(conn, ciclo, f"ciclo {cycle_id}")


PLAZO_CONCILIA_S = 1500.0
TOLERANCIA_DINERO = Decimal("0.01")


@dataclass(frozen=True)
class Totales:
    gasto: Decimal
    clics: int
    compras: Decimal
    ventas: Decimal
    ventas_mismo_sku: Decimal

    def __add__(self, otro: Totales) -> Totales:
        return Totales(*(getattr(self, c.name) + getattr(otro, c.name) for c in fields(self)))

    def __str__(self) -> str:
        return " ".join(f"{c.name}={getattr(self, c.name)}" for c in fields(self))


CERO = Totales(Decimal(0), 0, Decimal(0), Decimal(0), Decimal(0))


@dataclass(frozen=True)
class Comparacion:
    product_id: int
    categoria: str
    ventana: p.Ventana
    orbit: Totales
    dias: Mapping[dt.date, Totales]


def campos_distintos(orbit: Totales, amazon: Totales) -> list[str]:
    dinero = ("gasto", "ventas", "ventas_mismo_sku")
    distintos = [
        c for c in dinero if abs(getattr(orbit, c) - getattr(amazon, c)) > TOLERANCIA_DINERO
    ]
    return distintos + [c for c in ("clics", "compras") if getattr(orbit, c) != getattr(amazon, c)]


def tramo_halo_maduro(decidido_el: dt.datetime) -> p.Ventana:
    """[D-90, D-31]: el tramo del halo que Amazon ya no reatribuye."""
    hoy = decidido_el.astimezone(dt.UTC).date()
    return p.Ventana(hoy - dt.timedelta(days=90), hoy - dt.timedelta(days=p.DIAS_PAUSA_HASTA))


def comparaciones(evaluaciones: Sequence[p.EvaluacionProducto], n: int) -> list[Comparacion]:
    """Hasta N productos: con meta y candidatos a pausa intercalados y, si no
    alcanzan, los de mas gasto en el tramo maduro del halo (relleno
    informativo). La meta y el relleno se comparan en ese tramo; un
    candidato, en la ventana de la pausa."""

    def dias(ev: p.EvaluacionProducto, ventana: p.Ventana) -> dict[dt.date, Totales]:
        return {
            d.fecha: Totales(
                d.gasto.valor, d.clics, d.compras, d.ventas.valor, d.ventas_mismo_sku.valor
            )
            for d in ev.entrada.dias_halo
            if ventana.contiene(d.fecha)
        }

    def halo(ev: p.EvaluacionProducto, categoria: str) -> Comparacion:
        tramo = tramo_halo_maduro(ev.entrada.decidido_el)
        por_fecha = dias(ev, tramo)
        orbit = sum(por_fecha.values(), CERO)
        return Comparacion(ev.producto.product_id, categoria, tramo, orbit, por_fecha)

    def pausa(ev: p.EvaluacionProducto) -> Comparacion:
        a = ev.entrada.actividad_pausa
        assert isinstance(a, p.AdsCompletos)
        orbit = Totales(
            a.gasto.valor, a.clics, a.compras_total, a.ventas_total.valor, a.ventas_mismo_sku.valor
        )
        return Comparacion(
            ev.producto.product_id, "candidato_pausa", a.ventana, orbit, dias(ev, a.ventana)
        )

    con_meta = [halo(ev, "meta") for ev in evaluaciones if isinstance(ev.meta, p.MetaReferencia)]
    candidatos = [pausa(ev) for ev in evaluaciones if p.cumple_limites_pausa(ev.entrada)]
    intercalados = [c for par in zip_longest(con_meta, candidatos) for c in par if c is not None]
    elegidos = {c.product_id for c in intercalados}
    relleno = sorted(
        (halo(ev, "relleno") for ev in evaluaciones if ev.producto.product_id not in elegidos),
        key=lambda c: (-c.orbit.gasto, c.product_id),
    )
    return [*intercalados, *(c for c in relleno if c.orbit.gasto > 0)][:n]


def amazon_por_fecha(
    filas: Iterable[Mapping], claves: Iterable[tuple[str, str | None]], ventana: p.Ventana
) -> dict[dt.date, Totales]:
    """Suma por fecha las filas del reporte con la vinculacion S5 del producto
    (ASIN y seller_sku exactos) y fecha en la ventana, con los parsers de la
    ingesta. Una metrica ausente o corrupta lanza ValueError."""
    claves = set(claves)
    por_fecha: dict[dt.date, Totales] = {}
    for fila in filas:
        clave = tuple(str(fila.get(c) or "").strip() for c in ("advertisedAsin", "advertisedSku"))
        if clave not in claves:
            continue
        fecha = dt.date.fromisoformat(str(fila.get("date")))
        if not ventana.contiene(fecha):
            continue
        valores = (
            reports._decimal_reporte(fila.get("cost"), "cost"),
            reports._entero_reporte(fila.get("clicks"), "clicks"),
            reports._decimal_reporte(fila.get("purchases30d"), "purchases30d"),
            reports._decimal_reporte(fila.get("sales30d"), "sales30d"),
            reports._decimal_reporte(
                fila.get("attributedSalesSameSku30d"), "attributedSalesSameSku30d"
            ),
        )
        if None in valores:
            raise ValueError(f"fila de Amazon con metrica ausente el {fecha}")
        por_fecha[fecha] = por_fecha.get(fecha, CERO) + Totales(*valores)
    return por_fecha


def _pide(client: AdsClient, perfil, rango: tuple[dt.date, dt.date]) -> str:
    try:
        return reports.solicitar_reporte(
            client, perfil, reports.PRODUCTOS_CFG, *rango, sufijo="-concilia"
        )
    except AdsApiError as exc:
        duplicado = descarga._id_de_duplicado(exc)
        if duplicado is None:
            raise
        return duplicado


def descarga_reportes(
    client: AdsClient,
    perfil,
    rangos: Sequence[tuple[dt.date, dt.date]],
    *,
    plazo_s: float = PLAZO_CONCILIA_S,
    reloj=time.monotonic,
    dormir=time.sleep,
) -> dict[tuple[dt.date, dt.date], list[dict]] | None:
    """Pide todos los reportes antes de sondear (en paralelo) y los descarga
    con HTTP puro. None si alguno falla o no queda COMPLETED en el plazo."""
    inicio = reloj()
    pendientes = {rango: _pide(client, perfil, rango) for rango in rangos}
    filas: dict[tuple[dt.date, dt.date], list[dict]] = {}
    while pendientes:
        for rango, report_id in list(pendientes.items()):
            try:
                datos = client.get_report(report_id, profile_id=perfil.profile_id).json()
            except (AdsApiError, ValueError):
                continue
            estado = datos.get("status") if isinstance(datos, dict) else None
            if estado == "COMPLETED":
                url = descarga._url_de_completado(datos, report_id)
                filas[rango] = reports.descargar_filas(client, url)
                del pendientes[rango]
            elif estado == "FAILED":
                return None
        transcurrido = reloj() - inicio
        if not pendientes:
            break
        if transcurrido >= plazo_s:
            return None
        rapido = transcurrido < descarga.RAPIDO_HASTA_SEGUNDOS
        dormir(descarga.ESPERA_RAPIDA_SEGUNDOS if rapido else descarga.ESPERA_LENTA_SEGUNDOS)
    return filas


def _evaluaciones(conn, plataforma: str, ruta: str | None) -> list[p.EvaluacionProducto]:
    if ruta is not None:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        if datos["resumen"]["plataforma"] != plataforma:
            raise SystemExit(f"{ruta} es de {datos['resumen']['plataforma']}, no de {plataforma}")
        pares = list(zip(datos["entradas"], datos["evaluaciones"], strict=True))
    else:
        ciclo = conn.execute(_SQL_ULTIMO_CICLO, (plataforma,)).fetchone()
        pares = conn.execute(_SQL_EVALUACIONES, (ciclo[0],)).fetchall() if ciclo else []
    return [productos_io.evaluacion_de_fila(entrada, resultado) for entrada, resultado in pares]


def concilia(plataforma: str, n: int, ruta: str | None) -> int:
    with _conexion() as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        evaluaciones = _evaluaciones(conn, plataforma, ruta)
        muestra = comparaciones(evaluaciones, n)
        claves = productos_io.claves_por_producto(
            conn, plataforma, sorted({c.product_id for c in muestra})
        )
    if not evaluaciones:
        print(f"concilia {plataforma}: sin evaluaciones")
        return 1
    decidido_el = evaluaciones[0].entrada.decidido_el
    tramo = tramo_halo_maduro(decidido_el)
    pausa = p.ventana_pausa(decidido_el)
    un_dia = dt.timedelta(days=1)
    if not muestra:
        print(f"concilia {plataforma}: sin productos con meta ni candidatos ni gasto")
        print(f"concilia {plataforma}: diferencias: 0")
        return 0
    rangos = [(pausa.desde, pausa.hasta_exclusivo - un_dia)]
    if any(c.ventana == tramo for c in muestra):
        rangos.append((tramo.desde, pausa.desde - un_dia))
    client = AdsClient(AdsCredentials.from_secrets_dir())
    perfil = next((x for x in perfiles_aceptados(client) if x.platform == plataforma), None)
    if perfil is None:
        print(f"concilia {plataforma}: Amazon no devolvio un perfil aceptado de {plataforma}")
        return 1
    print(
        f"concilia {plataforma}: D={decidido_el.date()} reportes "
        + ", ".join(f"[{a}, {b}]" for a, b in rangos)
    )
    filas = descarga_reportes(client, perfil, rangos)
    if filas is None:
        print(f"concilia {plataforma}: sin reporte")
        return 2
    todas = [fila for lote in filas.values() for fila in lote]
    distintos = {"cuenta": 0, "relleno": 0}
    for c in muestra:
        clase = "relleno" if c.categoria == "relleno" else "cuenta"
        try:
            por_fecha = amazon_por_fecha(todas, claves.get(c.product_id, ()), c.ventana)
        except ValueError as exc:
            distintos[clase] += 1
            print(f"concilia {plataforma}: producto {c.product_id} {c.categoria}: DISTINTO {exc}")
            continue
        amazon = sum(por_fecha.values(), CERO)
        campos = campos_distintos(c.orbit, amazon)
        distintos[clase] += bool(campos)
        print(
            f"concilia {plataforma}: producto {c.product_id} {c.categoria} "
            f"[{c.ventana.desde}, {c.ventana.hasta_exclusivo}): orbit {c.orbit} | amazon {amazon}"
            + (f" | DISTINTO {','.join(campos)}" if campos else " | igual")
        )
        for fecha in sorted(c.dias.keys() | por_fecha.keys()) if campos else ():
            de_orbit, de_amazon = c.dias.get(fecha, CERO), por_fecha.get(fecha, CERO)
            if campos_distintos(de_orbit, de_amazon):
                print(f"    {fecha}: orbit {de_orbit} | amazon {de_amazon}")
    fin_halo = evaluaciones[0].entrada.ventana_halo.hasta_exclusivo - un_dia
    print(
        f"concilia {plataforma}: sin conciliar [{pausa.hasta_exclusivo}, {fin_halo}]: "
        "Amazon todavia reatribuye las ventas de ese tramo"
    )
    if any(c.categoria == "relleno" for c in muestra):
        print(f"concilia {plataforma}: relleno informativo: diferencias: {distintos['relleno']}")
    print(f"concilia {plataforma}: diferencias: {distintos['cuenta']}")
    return 1 if distintos["cuenta"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.auto03_verifica", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    p_evalua = sub.add_parser("evalua", help="evalua los productos de hoy en solo lectura")
    p_evalua.add_argument("--platform", required=True, choices=p.PLATAFORMAS)
    p_evalua.add_argument("--json", help="guarda {resumen, entradas, evaluaciones} en esta ruta")
    p_replay = sub.add_parser("replay", help="vuelve a correr el resolutor sobre lo congelado")
    origen = p_replay.add_mutually_exclusive_group()
    origen.add_argument("--json", help="salida de evalua --json")
    origen.add_argument("--cycle-id", help="id de optimizer_cycle o 'ultimo' (por omision)")
    p_concilia = sub.add_parser("concilia", help="compara la evaluacion con el reporte de Amazon")
    p_concilia.add_argument("--platform", required=True, choices=p.PLATAFORMAS)
    p_concilia.add_argument("--n", type=int, required=True, help="cuantos productos comparar")
    p_concilia.add_argument("--json", help="salida de evalua --json (por omision, el ultimo ciclo)")
    args = parser.parse_args(argv)
    if args.comando == "concilia":
        return concilia(args.platform, args.n, args.json)
    if args.comando == "evalua":
        datos = evalua(args.platform)
        print(json.dumps(datos["resumen"], ensure_ascii=False, sort_keys=True))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False)
        return 0
    return 0 if replay(args.json, args.cycle_id) else 1


if __name__ == "__main__":
    sys.exit(main())
