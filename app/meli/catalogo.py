"""Ingesta diaria del catalogo de MeLi (REPRICING 02, M.1)."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

import psycopg
from psycopg.types.json import Json

from app.db import connect
from app.redaction import scrub
from app.reputacion_clientes import ClienteMeli, MeliCredentials, ReputacionError

SOURCE = "meli_catalogo"
MONEDAS = ("MXN", "USD")
MAX_FALLOS_SEGUIDOS = 5

SKIP_ITEM = "meli: item fallo (se reintenta manana)"
SKIP_SCAN = "meli: scan truncado"
SKIP_VISITAS = "meli: visitas sin dato"

_ERRORES_ITEM = (ReputacionError, ValueError, TypeError, KeyError)

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id"
_SQL_SELLAR_RUN = (
    "UPDATE ingest_run SET finished_at = now(), rows_written = %s,"
    " rows_skipped = %s, skip_reason = %s, ok = %s WHERE id = %s"
)
_SQL_PRODUCTOS = "SELECT odoo_sku, id FROM product ORDER BY odoo_sku"
_SQL_EXISTENTES = (
    "SELECT external_id, id, product_id, seller_sku FROM listing WHERE platform = 'meli'"
)
_SQL_VIVOS = "SELECT id, external_id FROM listing WHERE platform = 'meli'"
_SQL_INSERT_LISTING = """
INSERT INTO listing (product_id, platform, external_id, seller_sku)
VALUES (%s, 'meli', %s, %s)
ON CONFLICT (platform, external_id) DO UPDATE SET
    product_id = EXCLUDED.product_id,
    seller_sku = EXCLUDED.seller_sku
RETURNING id
"""
_SQL_CAMBIA_LISTING = "UPDATE listing SET product_id = %s, seller_sku = %s WHERE id = %s"
_SQL_MIEMBRO = """
INSERT INTO listing_miembro
    (listing_id, platform, variante_ext, seller_sku, product_id, activo, visto_at)
VALUES (%s, 'meli', %s, %s, %s, true, %s)
ON CONFLICT (listing_id, variante_ext) DO UPDATE SET
    seller_sku = EXCLUDED.seller_sku,
    product_id = EXCLUDED.product_id,
    activo = true,
    visto_at = EXCLUDED.visto_at
"""
_SQL_BAJA_MIEMBROS = (
    "UPDATE listing_miembro SET activo = false"
    " WHERE listing_id = %s AND activo = true AND NOT (variante_ext = ANY(%s))"
)
_SQL_BAJA_TODOS = (
    "UPDATE listing_miembro SET activo = false WHERE listing_id = %s AND activo = true"
)
_SQL_OBSERVA = """
INSERT INTO meli_publicacion_observation
    (item_id, metric_date, observed_at, estado, precio, moneda, stock,
     tipo_publicacion, categoria_id, logistica, visitas, variantes, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class ClienteCatalogo(Protocol):
    def get(self, path: str, params: dict | None = None) -> tuple[dict, datetime]: ...
    def items_seller(self, seller_id: int) -> tuple[list[str], bool]: ...
    def close(self) -> None: ...


@dataclass
class PlanMiembro:
    variante_ext: str
    sku: str


@dataclass
class PlanItem:
    item_id: str
    estado: str
    precio: Decimal | None
    moneda: str | None
    stock: int | None
    tipo: str | None
    categoria: str | None
    logistica: str | None
    visitas: int | None
    variantes: list
    miembros: list
    sin_sku: int
    visitas_ok: bool


@dataclass
class ResultadoCatalogo:
    observadas: int = 0
    listings_nuevos: int = 0
    listings_actualizados: int = 0
    miembros_escritos: int = 0
    con_todos: int = 0
    con_alguno: int = 0
    sin_ninguno: int = 0
    reconciliados: int = 0
    ok: bool = True
    run_id: int | None = None
    skips: Counter = field(default_factory=Counter)


def _sku_de_atributos(atributos: object) -> str | None:
    if not isinstance(atributos, list):
        return None
    for atributo in atributos:
        if not isinstance(atributo, dict):
            continue
        if atributo.get("id") != "SELLER_SKU":
            continue
        valor = atributo.get("value_name")
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None


def _texto_o_none(valor: object) -> str | None:
    return valor if isinstance(valor, str) else None


def _entero_o_none(valor: object) -> int | None:
    if isinstance(valor, bool):
        return None
    return valor if isinstance(valor, int) else None


def _precio_moneda(datos: dict) -> tuple[Decimal | None, str | None]:
    moneda = datos.get("currency_id")
    if moneda not in MONEDAS:
        return None, None
    try:
        precio = Decimal(str(datos.get("price")))
    except InvalidOperation:
        return None, None
    if not precio.is_finite() or precio <= 0:
        return None, None
    return precio, moneda


def _visitas(cliente: ClienteCatalogo, item_id: str) -> int | None:
    try:
        respuesta, _ = cliente.get(
            f"/items/{item_id}/visits/time_window", {"last": 1, "unit": "day"}
        )
    except _ERRORES_ITEM:
        return None
    total = respuesta.get("total_visits")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total


def _fetch_item(cliente: ClienteCatalogo, item_id: str) -> PlanItem:
    """Trae y parsea una publicacion. Puro-red, sin DB."""
    datos, _ = cliente.get(f"/items/{item_id}", {"include_attributes": "all"})
    estado = datos["status"]
    variaciones = datos.get("variations") or []
    miembros: list = []
    variantes: list = []
    sin_sku = 0
    for variacion in variaciones:
        ext = str(variacion["id"])
        sku = _sku_de_atributos(variacion.get("attributes"))
        stock = _entero_o_none(variacion.get("available_quantity"))
        variantes.append({"variante_ext": ext, "seller_sku": sku, "stock": stock})
        if sku is None:
            sin_sku += 1
        else:
            miembros.append(PlanMiembro(ext, sku))
    if not variaciones:
        sku_raiz = _sku_de_atributos(datos.get("attributes"))
        stock_raiz = _entero_o_none(datos.get("available_quantity"))
        variantes.append({"variante_ext": "", "seller_sku": sku_raiz, "stock": stock_raiz})
        if sku_raiz is None:
            sin_sku += 1
        else:
            miembros.append(PlanMiembro("", sku_raiz))
    if variaciones:
        stocks = [_entero_o_none(v.get("available_quantity")) for v in variaciones]
        stock = sum(stocks) if all(s is not None for s in stocks) else None
    else:
        stock = _entero_o_none(datos.get("available_quantity"))
    precio, moneda = _precio_moneda(datos)
    envio = datos.get("shipping")
    logistica = _texto_o_none(envio.get("mode")) if isinstance(envio, dict) else None
    visitas = _visitas(cliente, item_id) if estado == "active" else None
    return PlanItem(
        item_id=item_id,
        estado=estado,
        precio=precio,
        moneda=moneda,
        stock=stock,
        tipo=_texto_o_none(datos.get("listing_type_id")),
        categoria=_texto_o_none(datos.get("category_id")),
        logistica=logistica,
        visitas=visitas,
        variantes=variantes,
        miembros=miembros,
        sin_sku=sin_sku,
        visitas_ok=estado != "active" or visitas is not None,
    )


def _formato_skips(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


def _sella(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    observadas: int,
    skips: Counter,
) -> None:
    conn.execute(
        _SQL_SELLAR_RUN,
        (observadas, sum(skips.values()), _formato_skips(skips), ok, run_id),
    )


def _observa(
    conn: psycopg.Connection,
    plan: PlanItem,
    *,
    hoy: date,
    ahora: datetime,
    run_id: int,
) -> None:
    conn.execute(
        _SQL_OBSERVA,
        (
            plan.item_id,
            hoy,
            ahora,
            plan.estado,
            plan.precio,
            plan.moneda,
            plan.stock,
            plan.tipo,
            plan.categoria,
            plan.logistica,
            plan.visitas,
            Json(plan.variantes),
            run_id,
        ),
    )


def _cuenta_mapeo(resultado: ResultadoCatalogo, mapeados: int, total: int) -> None:
    if mapeados == 0:
        resultado.sin_ninguno += 1
    elif mapeados == total:
        resultado.con_todos += 1
    else:
        resultado.con_alguno += 1


def _aplica_plan(
    conn: psycopg.Connection,
    plan: PlanItem,
    *,
    mapa: dict,
    existentes: dict,
    resultado: ResultadoCatalogo,
    hoy: date,
    ahora: datetime,
    run_id: int,
) -> None:
    _observa(conn, plan, hoy=hoy, ahora=ahora, run_id=run_id)
    resultado.observadas += 1
    if not plan.visitas_ok:
        resultado.skips[SKIP_VISITAS] += 1
    mapeados = [m for m in plan.miembros if m.sku in mapa]
    _cuenta_mapeo(resultado, len(mapeados), len(plan.miembros) + plan.sin_sku)
    previo = existentes.get(plan.item_id)
    if plan.estado != "active":
        if previo is not None:
            conn.execute(_SQL_BAJA_TODOS, (previo[0],))
        return
    vistos = [m.variante_ext for m in plan.miembros]
    if not mapeados:
        if previo is not None:
            for miembro in plan.miembros:
                conn.execute(
                    _SQL_MIEMBRO, (previo[0], miembro.variante_ext, miembro.sku, None, ahora)
                )
            if vistos:
                conn.execute(_SQL_BAJA_MIEMBROS, (previo[0], vistos))
            else:
                conn.execute(_SQL_BAJA_TODOS, (previo[0],))
        return
    ancla_sku = min(m.sku for m in mapeados)
    nuevo = (mapa[ancla_sku], ancla_sku)
    if previo is None:
        params = (nuevo[0], plan.item_id, nuevo[1])
        listing_id = conn.execute(_SQL_INSERT_LISTING, params).fetchone()[0]
        resultado.listings_nuevos += 1
    elif previo[1:] != nuevo:
        listing_id = previo[0]
        conn.execute(_SQL_CAMBIA_LISTING, (*nuevo, listing_id))
        resultado.listings_actualizados += 1
    else:
        listing_id = previo[0]
    for miembro in plan.miembros:
        conn.execute(
            _SQL_MIEMBRO,
            (listing_id, miembro.variante_ext, miembro.sku, mapa.get(miembro.sku), ahora),
        )
    conn.execute(_SQL_BAJA_MIEMBROS, (listing_id, vistos))
    resultado.miembros_escritos += len(vistos)


def _reconcilia_ausentes(
    conn: psycopg.Connection,
    vistos: set,
    *,
    resultado: ResultadoCatalogo,
) -> None:
    for listing_id, externo in conn.execute(_SQL_VIVOS).fetchall():
        if externo in vistos:
            continue
        conn.execute(_SQL_BAJA_TODOS, (listing_id,))
        resultado.reconciliados += 1


def sincronizar(
    conn: psycopg.Connection,
    cliente: ClienteCatalogo,
    *,
    hoy: date,
    ahora: datetime,
    max_fallos_seguidos: int = MAX_FALLOS_SEGUIDOS,
) -> ResultadoCatalogo:
    """Baja el scan, trae cada publicacion y escribe identidad + observacion.

    Red primero (sin tocar la base), escritura despues en una transaccion
    corta con savepoint por publicacion: un item que falla a mitad no tira
    lo ya escrito. Falla honesto tras `max_fallos_seguidos` seguidos.
    """
    resultado = ResultadoCatalogo()
    datos_me, _ = cliente.get("/users/me")
    seller = datos_me.get("id")
    if isinstance(seller, bool) or not isinstance(seller, int):
        raise ReputacionError("meli: /users/me sin id entero")
    ids, truncado = cliente.items_seller(seller)
    ids = list(dict.fromkeys(str(i) for i in ids))
    if truncado:
        resultado.skips[SKIP_SCAN] += 1
    planes: list = []
    fallos = 0
    for item_id in ids:
        try:
            planes.append(_fetch_item(cliente, item_id))
        except _ERRORES_ITEM:
            resultado.skips[SKIP_ITEM] += 1
            fallos += 1
            if fallos >= max_fallos_seguidos:
                raise ReputacionError(
                    f"meli: {fallos} fallos seguidos, se aborta la corrida"
                ) from None
            continue
        fallos = 0
    with conn.transaction():
        resultado.run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]
    with conn.transaction():
        mapa: dict = {}
        for sku, pid in conn.execute(_SQL_PRODUCTOS).fetchall():
            if isinstance(sku, str) and sku.strip():
                mapa.setdefault(sku.strip(), pid)
        existentes = {ext: (lid, pid, sku) for ext, lid, pid, sku in conn.execute(_SQL_EXISTENTES)}
        for plan in planes:
            try:
                with conn.transaction():
                    _aplica_plan(
                        conn,
                        plan,
                        mapa=mapa,
                        existentes=existentes,
                        resultado=resultado,
                        hoy=hoy,
                        ahora=ahora,
                        run_id=resultado.run_id,
                    )
            except psycopg.Error:
                resultado.skips[SKIP_ITEM] += 1
        if not truncado:
            _reconcilia_ausentes(conn, set(ids), resultado=resultado)
        resultado.ok = not ids or resultado.observadas > 0
        _sella(
            conn,
            resultado.run_id,
            ok=resultado.ok,
            observadas=resultado.observadas,
            skips=resultado.skips,
        )
    return resultado


def main(argv: list[str] | None = None, *, cliente: ClienteCatalogo | None = None) -> int:
    parser = argparse.ArgumentParser(prog="catalogo", description=__doc__)
    args, extras = parser.parse_known_args(argv)
    if extras:
        parser.print_usage(sys.stderr)
        return 2
    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir catalogo (fail-closed)",
            file=sys.stderr,
        )
        return 2
    propio = False
    if cliente is None:
        try:
            creds = MeliCredentials.from_secrets_dir()
            cliente = ClienteMeli(creds)
        except ReputacionError as exc:
            print(f"no se pudo crear el cliente MeLi: {scrub(str(exc))}", file=sys.stderr)
            return 1
        propio = True
    try:
        with connect(dsn) as conn:
            ahora = datetime.now(UTC)
            try:
                resultado = sincronizar(conn, cliente, hoy=ahora.date(), ahora=ahora)
            except ReputacionError as exc:
                print(f"catalogo abortado: {scrub(str(exc))}", file=sys.stderr)
                return 1
            print(
                f"catalogo meli: observadas={resultado.observadas}"
                f" nuevos={resultado.listings_nuevos}"
                f" actualizados={resultado.listings_actualizados}"
                f" miembros={resultado.miembros_escritos}"
                f" todos={resultado.con_todos} alguno={resultado.con_alguno}"
                f" ninguno={resultado.sin_ninguno} reconciliados={resultado.reconciliados}"
                f" run={resultado.run_id} skips={dict(resultado.skips)}"
            )
            if not resultado.ok:
                print("catalogo meli: sin observaciones con items en el scan", file=sys.stderr)
                return 1
            return 0
    except Exception as exc:
        print(f"catalogo meli fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    finally:
        if propio:
            cliente.close()


if __name__ == "__main__":
    raise SystemExit(main())
