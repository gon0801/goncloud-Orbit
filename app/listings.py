"""Ingesta de listings desde la SQLite del bridge (ORBIT 06 0.2).

Fuente: snapshot READ-ONLY de la base del bridge (`amazon_listing_prices` +
`amazon_sku_mapping`). Destino: `listing` de Orbit (rol de ingesta,
ORBIT_DSN_INGEST). Este modulo NUNCA escribe en el bridge y no toca
`decision`, `apply_queue`, `ads_optimizer_goal` ni `ad_entity` — el
`ad_entity.listing_id` es trabajo de la 0.4; aqui solo se construye el MAPA.

Decisiones selladas (plans/orbit-06.md, "Decisiones de la 0.2", con su
evidencia medida; este docstring es el resumen operativo):

1. LA TRAMPA: los SKU de Amazon NO son los de Odoo (autogenerados tipo
   `01-5LZU-V9KZ` vs codigos de negocio). Unir por texto da 1% de cobertura y
   esta PROHIBIDO. El puente OBLIGATORIO es `amazon_sku_mapping` por
   `seller_sku` (su PK; cubre 450/735 — unir por ASIN perderia 12 filas del
   mapeo sin ASIN). Sin mapeo = fila NO escrita y contada (285 hoy): jamas se
   inventa un producto para un seller_sku.
2. `external_id` = el ASIN DEL LISTING, siempre (0 nulos, 0 duplicados
   (plataforma, asin), 0 ASIN con dos SKUs de Odoo — medido). La unica
   divergencia listing-vs-mapeo (XM-20QN-2YJR) resuelve al ASIN del listing:
   es el namespace de Amazon, el mismo que traeran los product ads de la 0.4.
   `platform` = `marketplace_name` verificado literal (amazon_mx / amazon_us).
3. Precio: el reporte origen se pide UNO POR marketplace y su price viene en
   moneda local — la moneda se DERIVA de la plataforma (mx->MXN, us->USD,
   mismo mapa que app/ads/structure.py). Precio NULL => ambos NULL (el
   listing se escribe igual: el MAPA manda). Precio <= 0 o con precision
   genuina > 4 decimales => tratado como dato faltante (NULL) y CONTADO;
   ruido binario <= 1e-13 se cuantiza (misma regla de la 0.1). `status` NO
   filtra: el listing de Orbit es identidad, no ciclo de vida.
4. Re-corrida: upsert sobre (platform, external_id). Nueva => INSERT;
   existente => UPDATE de product_id/seller_sku/precio cuando difieran (el
   re-mapeo del bridge es una correccion legitima del catalogo y se cuenta
   aparte); sin cambios => no-op REAL. Un listing de Orbit ausente del origen
   se CONSERVA y queda contado (nada se borra).

Contabilidad (ingest_run): la unidad es el LISTING final; rows_written =
inserciones + actualizaciones; todo rechazo y toda ausencia quedan en
rows_skipped con su motivo. Patron de transacciones de app/ads/structure.py
(la corrida nace abierta en su propia transaccion, se sella al final, y en
fallo queda ok=false con rollback del trabajo).
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import psycopg

from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "bridge_listings"

# platform -> moneda del marketplace. El reporte origen (GET_MERCHANT_
# LISTINGS_ALL_DATA) se pide uno por marketplace y su precio viene en moneda
# local; el bridge no guarda la columna. Mismo mapa que _PAIS_PLATAFORMA_
# MONEDA de app/ads/structure.py (D3 de la 0.2).
MONEDA_POR_PLATAFORMA = {"amazon_mx": "MXN", "amazon_us": "USD"}

_MAX_DECIMALES = Decimal("0.0001")  # money_amount = NUMERIC(14,4)
_RUIDO_FLOAT = Decimal("0.00001")  # umbral ruido binario vs precision real
_MAX_PRECIO = Decimal(10) ** 10  # 10 enteros + 4 decimales = 14 digitos


class ListingsError(Exception):
    """Error de la ingesta de listings (snapshot inexistente o ilegible)."""


@dataclass(frozen=True)
class FilaListing:
    """Una fila de amazon_listing_prices tal cual viene del bridge."""

    seller_sku: str
    asin: str | None
    plataforma: str | None  # marketplace_name
    precio: float | None


@dataclass(frozen=True)
class OrigenListings:
    listings: tuple[FilaListing, ...]
    mapeo: dict[str, str]  # seller_sku -> odoo_default_code (ambos stripped)
    mapeo_ambiguo: frozenset[str] = frozenset()  # claves colisionantes tras strip


@dataclass(frozen=True)
class PlanListing:
    """Un listing listo para escribir: el mapa SKU <-> plataforma <-> ASIN."""

    producto: str  # odoo_sku del producto de Orbit
    plataforma: str
    external_id: str  # ASIN del listing
    seller_sku: str
    precio: Decimal | None  # ambos None = sin precio (el mapa manda)
    moneda: str | None


@dataclass(frozen=True)
class ResultadoSync:
    """Outcome contable de la corrida (espejo de la ingest_run sellada)."""

    run_id: int
    ok: bool
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    listings_insertadas: int
    listings_actualizadas: int
    remapeos: int
    precios_actualizados: int
    filas_origen: int
    listings_finales: int
    conteo_por_plataforma: dict[str, int]
    colapsados_por_asin: int


# ---------------------------------------------------------------------------
# Lectura del snapshot (read-only por construccion: mode=ro + solo SELECT)
# ---------------------------------------------------------------------------


def leer_origen(ruta: Path | str) -> OrigenListings:
    """Lee amazon_listing_prices + amazon_sku_mapping del snapshot bridge."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ListingsError(f"snapshot inexistente: {ruta}")
    con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    try:
        listings = tuple(
            FilaListing(
                seller_sku=fila[0] or "",
                asin=fila[1],
                plataforma=fila[2],
                precio=fila[3],
            )
            for fila in con.execute(
                "SELECT seller_sku, asin, marketplace_name, price"
                " FROM amazon_listing_prices ORDER BY marketplace_name, seller_sku"
            )
        )
        # Hallazgo 2 del adversario: la llave y el codigo se NORMALIZAN igual
        # que el lado del listing (strip simetrico). Una colision de claves
        # tras el strip con codigos DISTINTOS ('XM-1' y ' XM-1' apuntando a
        # dos productos) es ambigua: no se elige arbitrario — la clave queda
        # senalada y plan_listings cuenta el skip por fila.
        mapeo: dict[str, str] = {}
        ambiguo: set[str] = set()
        for seller_sku, odoo_code in con.execute(
            "SELECT seller_sku, odoo_default_code FROM amazon_sku_mapping"
            " WHERE odoo_default_code IS NOT NULL AND TRIM(odoo_default_code) != ''"
            " ORDER BY seller_sku"
        ):
            clave = (seller_sku or "").strip()
            codigo = (odoo_code or "").strip()
            if not clave or not codigo:
                continue
            if clave in ambiguo:
                continue
            previo = mapeo.get(clave)
            if previo is None:
                mapeo[clave] = codigo
            elif previo != codigo:
                del mapeo[clave]
                ambiguo.add(clave)
    finally:
        con.close()
    return OrigenListings(listings=listings, mapeo=mapeo, mapeo_ambiguo=frozenset(ambiguo))


# ---------------------------------------------------------------------------
# Planificacion (pura): filas crudas -> listings por (plataforma, asin)
# ---------------------------------------------------------------------------


def _precio_decimal(precio: float | None) -> tuple[Decimal | None, str | None]:
    """Precio del REAL de origen -> (Decimal cuantizado, motivo | None).

    Devuelve motivo cuando el precio es dato faltante (NULL en el plan): <= 0,
    no finito, o precision genuina > 4 decimales. El ruido binario (<= 1e-13,
    la misma firma de la 0.1) se cuantiza.
    """
    if precio is None:
        return None, None
    if not math.isfinite(precio) or precio <= 0:
        return None, "precio no positivo (dato faltante)"
    valor = Decimal(str(precio))
    # Guarda de rango ANTES de cuantizar (codex#2/grok#3 de la cross-review):
    # un REAL finito enorme (~1e25+) lanza InvalidOperation en quantize con la
    # precision por defecto y ABORTABA la corrida entera — justo lo que esta
    # guarda pretendia evitar. La comparacion Decimal no necesita precision.
    if abs(valor) >= _MAX_PRECIO:
        return None, "precio fuera de rango NUMERIC(14,4)"
    if abs(valor - valor.quantize(_MAX_DECIMALES)) >= _RUIDO_FLOAT:
        return None, "precio con mas de 4 decimales (dato faltante)"
    # El sub-centavo cuantiza a 0.0000 y violaria listing_precio_positivo
    # ABORTANDO la corrida entera (hallazgo 1 del adversario): dato faltante.
    cuantizado = valor.quantize(_MAX_DECIMALES)
    if cuantizado <= 0:
        return None, "precio no positivo (dato faltante)"
    return cuantizado, None


def plan_listings(
    filas: list[FilaListing] | tuple[FilaListing, ...],
    mapeo: dict[str, str],
    productos: set[str] | None = None,
    mapeo_ambiguo: frozenset[str] = frozenset(),
) -> tuple[dict[tuple[str, str], PlanListing], Counter, Counter]:
    """Convierte filas del bridge en listings por (plataforma, ASIN).

    La union a Odoo es SOLO por mapeo[seller_sku] (D2: unir por texto de SKU
    esta PROHIBIDO — 1% de cobertura y pareceria funcionar). Si `productos`
    viene, un mapeo a un SKU sin producto en Orbit es rechazo (defensa: hoy
    los 450 del mapeo existen). Un (plataforma, asin) con DOS productos
    distintos es conflicto: no se elige uno arbitrario (mismo criterio que la
    0.4 para multi-ASIN). Y dos filas del mismo (plataforma, asin) y mismo
    producto con precios DISTINTOS descartan el precio (hallazgo 3 del
    adversario): elegir la primera alfabeticamente seria elegir dinero en
    silencio; la fuente que se contradice no trae un precio confiable.
    """
    skips: Counter = Counter()
    stats: Counter = Counter()
    stats["filas"] = len(filas)
    planes: dict[tuple[str, str], PlanListing] = {}
    en_conflicto: set[tuple[str, str]] = set()

    for fila in filas:
        seller_sku = fila.seller_sku.strip()
        plataforma = (fila.plataforma or "").strip()
        asin = (fila.asin or "").strip()
        if not asin:
            skips["listing sin ASIN"] += 1
            continue
        if plataforma not in MONEDA_POR_PLATAFORMA:
            dominios = "/".join(sorted(MONEDA_POR_PLATAFORMA))
            skips[f"plataforma fuera de dominio ({dominios}): {plataforma}"] += 1
            continue
        if seller_sku in mapeo_ambiguo:
            skips["seller_sku con mapeo ambiguo tras normalizar"] += 1
            continue
        odoo_sku = mapeo.get(seller_sku)
        if odoo_sku is None:
            skips["seller_sku sin mapeo a SKU de Odoo"] += 1
            continue
        if productos is not None and odoo_sku not in productos:
            skips["mapeo a SKU sin producto en Orbit"] += 1
            continue

        precio, motivo_precio = _precio_decimal(fila.precio)
        if motivo_precio is not None:
            skips[motivo_precio] += 1

        clave = (plataforma, asin)
        if clave in en_conflicto:
            continue
        previo = planes.get(clave)
        if previo is not None:
            if previo.producto != odoo_sku:
                # Mismo ASIN en la misma plataforma apuntando a dos productos:
                # conflicto del origen, no se elige uno arbitrario.
                del planes[clave]
                en_conflicto.add(clave)
                skips["ASIN con conflicto de producto"] += 1
            elif previo.precio != precio:
                # Mismo listing con precios divergentes (incluye uno con
                # precio y el otro no): el precio se descarta (dato faltante),
                # no se elige ninguno de los dos.
                skips["ASIN con precios divergentes en el origen (precio descartado)"] += 1
                planes[clave] = replace(previo, precio=None, moneda=None)
            elif previo.seller_sku != seller_sku:
                # Mismo ASIN publicado bajo dos seller_sku (legal en el
                # bridge: su unique es (seller_sku, marketplace)). El ASIN y
                # el producto NO ambiguan, asi que el mapa se conserva con el
                # primero por orden determinista — pero la divergencia deja
                # de ser silenciosa (grok#1 de la cross-review).
                skips["ASIN con seller_sku divergentes (se conserva el primero)"] += 1
            else:
                stats["colapsados_por_asin"] += 1
            continue
        planes[clave] = PlanListing(
            producto=odoo_sku,
            plataforma=plataforma,
            external_id=asin,
            seller_sku=seller_sku,
            precio=precio,
            moneda=MONEDA_POR_PLATAFORMA[plataforma] if precio is not None else None,
        )

    stats["listings"] = len(planes)
    return planes, skips, stats


# ---------------------------------------------------------------------------
# Escritura en Orbit (listing, rol de ingesta)
# ---------------------------------------------------------------------------

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id"

_SQL_SELLAR_RUN = """
UPDATE ingest_run
   SET finished_at = now(),
       rows_written = %s,
       rows_skipped = %s,
       skip_reason = %s,
       ok = %s
 WHERE id = %s
"""

# Upsert del catalogo (D4): nueva => INSERT; existente => UPDATE solo cuando
# algo difiere (el WHERE hace el no-op REAL barato); el re-mapeo del bridge
# (product_id) es una correccion legitima del mapa.
_SQL_UPSERT_LISTING = """
INSERT INTO listing (product_id, platform, external_id, seller_sku,
                     listing_price, price_currency)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (platform, external_id) DO UPDATE SET
    product_id = EXCLUDED.product_id,
    seller_sku = EXCLUDED.seller_sku,
    listing_price = EXCLUDED.listing_price,
    price_currency = EXCLUDED.price_currency
WHERE (listing.product_id, listing.seller_sku, listing.listing_price,
       listing.price_currency)
   IS DISTINCT FROM
      (EXCLUDED.product_id, EXCLUDED.seller_sku, EXCLUDED.listing_price,
       EXCLUDED.price_currency)
"""


def _fila_upsert(product_id: int, plan: PlanListing) -> tuple:
    return (
        product_id,
        plan.plataforma,
        plan.external_id,
        plan.seller_sku,
        plan.precio,
        plan.moneda,
    )


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


def sync_listings(conn: psycopg.Connection, ruta_sqlite: Path | str) -> ResultadoSync:
    """Corre la ingesta completa y sella su ingest_run (patron structure.sync).

    OJO transacciones: NADA se ejecuta antes del primer bloque `with
    conn.transaction()` (regla pagada por las corridas 35/36 del 2026-08-30):
    con la conexion del CLI (sin autocommit), un SELECT suelto abre una
    transaccion implicita y conn.close() la revierte ENTERA — la corrida
    imprimia contadores y la base quedaba vacia. Los SELECT de estado viven
    DENTRO de la transaccion de trabajo, que commitea de verdad.
    """
    origen = leer_origen(ruta_sqlite)

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    insertadas = 0
    actualizadas = 0
    remapeos = 0
    precios_actualizados = 0
    por_plataforma: Counter = Counter()
    try:
        with conn.transaction():
            # Un SELECT por tabla: los ids de producto y el estado actual del
            # catalogo, en bloque y DENTRO de la transaccion (ver docstring).
            productos = dict(conn.execute("SELECT odoo_sku, id FROM product").fetchall())
            existentes = {
                (plataforma, external_id): (product_id, seller_sku, precio, moneda)
                for plataforma, external_id, product_id, seller_sku, precio, moneda in conn.execute(
                    "SELECT platform, external_id, product_id, seller_sku,"
                    " listing_price, price_currency FROM listing"
                )
            }

            planes, skips, stats = plan_listings(
                origen.listings,
                origen.mapeo,
                productos=set(productos),
                mapeo_ambiguo=origen.mapeo_ambiguo,
            )
            mutaciones: list[tuple] = []
            for clave in sorted(planes):
                plan = planes[clave]
                por_plataforma[plan.plataforma] += 1
                product_id = productos[plan.producto]
                previo = existentes.get(clave)
                if previo is None:
                    insertadas += 1
                    mutaciones.append(_fila_upsert(product_id, plan))
                    continue
                previo_producto, previo_sku, previo_precio, previo_moneda = previo
                if (previo_producto, previo_sku, previo_precio, previo_moneda) == (
                    product_id,
                    plan.seller_sku,
                    plan.precio,
                    plan.moneda,
                ):
                    continue  # no-op REAL: nada que escribir
                actualizadas += 1
                # Sin elif (hallazgo 4 del adversario): un re-mapeo que ademas
                # cambia el precio cuenta en AMBOS contadores.
                if previo_producto != product_id:
                    remapeos += 1
                if (previo_precio, previo_moneda) != (plan.precio, plan.moneda):
                    precios_actualizados += 1
                mutaciones.append(_fila_upsert(product_id, plan))

            # Listings de Orbit que el ARCHIVO de origen ya no trae: se
            # CONSERVAN (nada se borra) y quedan contados — la ausencia puede
            # ser un hueco del snapshot, no una baja real. OJO (hallazgo 4 del
            # adversario): solo cuentan los ausentes del ARCHIVO; una fila que
            # SIGUE en el origen pero perdio el mapeo ya cuenta arriba como
            # "sin mapeo" y no es "ausente". Y solo de plataformas PROPIAS de
            # este pipeline (codex#1/grok#2): un listing meli no es ausente
            # del snapshot Amazon — no es su jurisdiccion.
            claves_origen = {
                ((fila.plataforma or "").strip(), (fila.asin or "").strip())
                for fila in origen.listings
            }
            for clave in existentes:
                if (
                    clave not in planes
                    and clave not in claves_origen
                    and clave[0] in MONEDA_POR_PLATAFORMA
                ):
                    skips["listing de Orbit ausente en el origen (se conserva)"] += 1

            if mutaciones:
                with conn.cursor() as cur:
                    cur.executemany(_SQL_UPSERT_LISTING, mutaciones)

            skip_reason = _formato_skip_reason(skips)
            _sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=insertadas + actualizadas,
                rows_skipped=sum(skips.values()),
                skip_reason=skip_reason,
            )
    except BaseException as exc:
        # Patron de structure.sync: rollback del trabajo (el with de arriba) y
        # sello best-effort ok=false; BaseException para que un Ctrl-C tambien
        # deje la run sellada, no abierta.
        try:
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    rows_written=0,
                    rows_skipped=0,
                    skip_reason=scrub(str(exc)) or type(exc).__name__,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo; "
                "el error original de la corrida era: %s",
                run_id,
                scrub(str(exc)),
            )
        raise

    return ResultadoSync(
        run_id=run_id,
        ok=True,
        rows_written=insertadas + actualizadas,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        listings_insertadas=insertadas,
        listings_actualizadas=actualizadas,
        remapeos=remapeos,
        precios_actualizados=precios_actualizados,
        filas_origen=stats["filas"],
        listings_finales=stats["listings"],
        conteo_por_plataforma=dict(por_plataforma),
        colapsados_por_asin=stats["colapsados_por_asin"],
    )


def _sellar_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    rows_written: int,
    rows_skipped: int,
    skip_reason: str | None,
) -> None:
    conn.execute(_SQL_SELLAR_RUN, (rows_written, rows_skipped, skip_reason, ok, run_id))


# ---------------------------------------------------------------------------
# main del pipeline (el CLI despacha aqui; cero logica de argparse en el CLI)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest listings",
        description=(
            "Ingresa el mapa de listings desde un snapshot read-only de la SQLite"
            " del bridge (runbook: docs/DEPLOY.md, 'Ingesta de listings')."
        ),
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        help="ruta del snapshot del bridge (producido con la API .backup(); ver runbook)",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir listings (fail-closed)",
            file=sys.stderr,
        )
        return 2
    ruta = Path(args.sqlite)
    if not ruta.is_file():
        print(
            f"el snapshot no existe: {ruta} (runbook: docs/DEPLOY.md, ingesta de listings)",
            file=sys.stderr,
        )
        return 2
    try:
        conn = connect(dsn)
        try:
            resultado = sync_listings(conn, ruta)
        finally:
            conn.close()
    except Exception as exc:
        print(
            "ingesta de listings fallo (la ingest_run quedo sellada ok=false cuando"
            f" fue posible): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1

    por_plataforma = " ".join(
        f"{plataforma}={n}" for plataforma, n in sorted(resultado.conteo_por_plataforma.items())
    )
    print(f"== Ingesta de listings desde el bridge ({SOURCE}) ==")
    print(
        f"run_id={resultado.run_id} ok={resultado.ok}"
        f" rows_written={resultado.rows_written} rows_skipped={resultado.rows_skipped}"
    )
    print(
        f"listings: insertadas={resultado.listings_insertadas}"
        f" actualizadas={resultado.listings_actualizadas}"
        f" remapeos={resultado.remapeos} precios_actualizados={resultado.precios_actualizados}"
        f" finales={resultado.listings_finales} (filas origen: {resultado.filas_origen})"
    )
    print(f"por plataforma: {por_plataforma or '(nada escrito)'}")
    print(f"colapso: filas_mismo_asin={resultado.colapsados_por_asin}")
    if resultado.skip_reason:
        print(f"skips: {resultado.skip_reason}")
    return 0
