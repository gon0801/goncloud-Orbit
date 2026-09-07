"""Disponibilidad comercial desde el snapshot SQLite del bridge (ORBIT 19 B.3).

Fuente VERIFICADA en 0.3 (docs/evidencia/orbit-19/0.3/reporte.md, tabla (c)):
snapshot read-only de la SQLite del bridge (el lead lo copia por SSH con
`mode=ro` + `.backup()`, ver tools/disponibilidad_snapshot.py). Este modulo
NUNCA toca el servidor bridge/accounting: solo lee un archivo local.

Mapa sellado (0.3, bridge-observado.txt):

1. FBA = `amazon_fba_inventory.quantity_available` por (seller_sku,
   marketplace_id). Fresco el dia de la sonda (fetched_at por fila).
2. FBM = `amazon_listing_prices.quantity` SOLO si
   `fulfillment_channel='DEFAULT'`. En `AMAZON_NA` quantity es SIEMPRE NULL
   (359/359): ese NULL no es stock desconocido, es que la fila no es FBM —
   se omite y se cuenta, jamas se convierte en 0 ni en desconocido FBM.
3. `amazon_inventory_cache` esta PROHIBIDO (stale ~27d, 806/806 filas en
   cero): este modulo no lo lee (candado en tests/test_disponibilidad.py).
4. FBA y FBM NO se suman: canales distintos, una fila append-only por fuente.
5. Tres estados distinguibles (AC8): cero OBSERVADO (quantity=0 escrito),
   positivo (quantity>0) y desconocido (quantity NULL o fila ausente).
   NULL jamas se rellena con 0 (regla 3).
6. Conciliacion: el (platform, seller_sku) debe existir en `listing` de
   Orbit. SKU sin match = fila no mapeada, contada y reportada; no se
   inventa listing (mismo criterio que app/listings.py).

Featured Offer: fuente no verificada, ampliacion abierta ORBIT19 B.3.
Sin columna `featured`/`buybox`/`eligibility` en el bridge (HITS=[] en la
sonda 0.3, tabla (d)). `estado_disponibilidad(..., aspecto="featured_offer")`
declara 'sin_verificar' como constante: NO es integracion terminada y NO
bloquea la seleccion de publicaciones (disponibilidad es Recommended).
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import psycopg

from app.redaction import install_scrub_filter

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "bridge_disponibilidad"

# marketplace_id del bridge -> platform de Orbit (medido en 0.3: A1AM78C64UM0Y8
# = amazon_mx, ATVPDKIKX0DER = amazon_us). Un marketplace fuera de este mapa
# es rechazo contado, nunca una plataforma inventada.
MARKETPLACES: dict[str, str] = {
    "A1AM78C64UM0Y8": "amazon_mx",
    "ATVPDKIKX0DER": "amazon_us",
}

# Estados del contrato B.3 / AC8. 'sin_verificar' SOLO para Featured Offer.
ESTADO_CERO = "cero"
ESTADO_POSITIVO = "positivo"
ESTADO_DESCONOCIDO = "desconocido"
ESTADO_SIN_VERIFICAR = "sin_verificar"

ASPECTO_STOCK = "stock"
ASPECTO_FEATURED_OFFER = "featured_offer"


class DisponibilidadError(Exception):
    """Error del adaptador (snapshot inexistente, ilegible o aspecto invalido)."""


@dataclass(frozen=True)
class FilaFBA:
    """Una fila de amazon_fba_inventory tal cual viene del bridge."""

    seller_sku: str
    marketplace_id: str
    quantity_available: int | None
    fetched_at: str


@dataclass(frozen=True)
class FilaFBM:
    """Una fila de amazon_listing_prices (canal DEFAULT) tal cual viene."""

    seller_sku: str
    marketplace_id: str
    quantity: int | None
    fetched_at: str


@dataclass(frozen=True)
class SnapshotDisponibilidad:
    fba: tuple[FilaFBA, ...]
    fbm: tuple[FilaFBM, ...]


@dataclass(frozen=True)
class ObservacionDisponibilidad:
    """Una fila lista para escribir en disponibilidad_observation."""

    plataforma: str
    seller_sku: str
    fuente: str  # 'fba' | 'fbm'
    quantity: int | None  # None = desconocido, JAMAS 0 inventado
    fetched_at: dt.datetime  # UTC del bridge


@dataclass(frozen=True)
class ResultadoDisponibilidad:
    """Outcome contable de la corrida (espejo de la ingest_run)."""

    run_id: int
    ok: bool
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    filas_insertadas: int
    filas_idempotentes: int  # conflictos anti-duplicado (re-corrida)


# ---------------------------------------------------------------------------
# Lectura del snapshot (read-only por construccion: mode=ro + solo SELECT)
# ---------------------------------------------------------------------------


def _parsear_fetched_at(valor: str | None) -> dt.datetime | None:
    """fetched_at del bridge (texto, UTC) -> datetime consciente UTC.

    Acepta la forma observada 'YYYY-MM-DD HH:MM:SS' y la ISO con 'T'.
    Ilegible -> None (el llamador cuenta el skip; jamas se inventa frescura).
    """
    if not valor:
        return None
    texto = valor.strip()
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(texto, formato).replace(tzinfo=dt.UTC)
        except ValueError:
            continue
    return None


def leer_snapshot(ruta: Path | str) -> SnapshotDisponibilidad:
    """Lee FBA y FBM del snapshot bridge. NO toca amazon_inventory_cache."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise DisponibilidadError(f"snapshot inexistente: {ruta}")
    con = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    try:
        fba = tuple(
            FilaFBA(
                seller_sku=fila[0] or "",
                marketplace_id=fila[1] or "",
                quantity_available=fila[2],
                fetched_at=fila[3] or "",
            )
            for fila in con.execute(
                "SELECT seller_sku, marketplace_id, quantity_available, fetched_at"
                " FROM amazon_fba_inventory"
                " ORDER BY marketplace_id, seller_sku"
            )
        )
        # FBM: SOLO canal DEFAULT. Las filas AMAZON_NA quedan fuera de la
        # lectura misma (mapa 2): su quantity NULL no es observacion FBM.
        fbm = tuple(
            FilaFBM(
                seller_sku=fila[0] or "",
                marketplace_id=fila[1] or "",
                quantity=fila[2],
                fetched_at=fila[3] or "",
            )
            for fila in con.execute(
                "SELECT seller_sku, marketplace_id, quantity, fetched_at"
                " FROM amazon_listing_prices"
                " WHERE fulfillment_channel = 'DEFAULT'"
                " ORDER BY marketplace_id, seller_sku"
            )
        )
    finally:
        con.close()
    return SnapshotDisponibilidad(fba=fba, fbm=fbm)


# ---------------------------------------------------------------------------
# Planificacion (pura): filas crudas -> observaciones por (sku, fuente)
# ---------------------------------------------------------------------------


def _fila_a_observacion(
    seller_sku: str,
    marketplace_id: str,
    fuente: str,
    quantity: int | None,
    fetched_at_texto: str,
    skus_orbit: set[tuple[str, str]],
    skips: Counter,
) -> ObservacionDisponibilidad | None:
    """Normaliza UNA fila origen; None cuando la fila no es observable.

    Cada rechazo es contado con motivo: nada se descarta en silencio y nada
    se rellena (regla 3).
    """
    seller_sku = seller_sku.strip()
    plataforma = MARKETPLACES.get(marketplace_id.strip())
    if plataforma is None:
        skips[f"marketplace fuera de dominio ({marketplace_id.strip()!r})"] += 1
        return None
    if not seller_sku:
        skips["fila sin seller_sku"] += 1
        return None
    if quantity is not None and quantity < 0:
        skips[f"{fuente}: quantity negativo (dato invalido)"] += 1
        return None
    fetched_at = _parsear_fetched_at(fetched_at_texto)
    if fetched_at is None:
        skips[f"{fuente}: fetched_at ilegible o ausente"] += 1
        return None
    if (plataforma, seller_sku) not in skus_orbit:
        skips[f"{fuente}: seller_sku sin listing en Orbit"] += 1
        return None
    return ObservacionDisponibilidad(
        plataforma=plataforma,
        seller_sku=seller_sku,
        fuente=fuente,
        quantity=quantity,
        fetched_at=fetched_at,
    )


def plan_disponibilidad(
    snapshot: SnapshotDisponibilidad,
    skus_orbit: set[tuple[str, str]],
) -> tuple[dict[tuple[str, str, str], ObservacionDisponibilidad], Counter]:
    """Filas del bridge -> observaciones por (plataforma, seller_sku, fuente).

    FBA y FBM producen filas SEPARADAS (nunca sumadas). Un (sku, fuente) con
    dos filas divergentes en el origen es conflicto: no se elige arbitrario
    (mismo criterio que plan_listings con precios divergentes).
    """
    skips: Counter = Counter()
    planes: dict[tuple[str, str, str], ObservacionDisponibilidad] = {}
    en_conflicto: set[tuple[str, str, str]] = set()

    crudos: list[tuple[str, str, str, int | None, str]] = [
        *(
            ("fba", f.seller_sku, f.marketplace_id, f.quantity_available, f.fetched_at)
            for f in snapshot.fba
        ),
        *(("fbm", f.seller_sku, f.marketplace_id, f.quantity, f.fetched_at) for f in snapshot.fbm),
    ]
    for fuente, seller_sku, marketplace_id, quantity, fetched_at in crudos:
        obs = _fila_a_observacion(
            seller_sku, marketplace_id, fuente, quantity, fetched_at, skus_orbit, skips
        )
        if obs is None:
            continue
        clave = (obs.plataforma, obs.seller_sku, obs.fuente)
        if clave in en_conflicto:
            continue
        previo = planes.get(clave)
        if previo is not None and (previo.quantity, previo.fetched_at) != (
            obs.quantity,
            obs.fetched_at,
        ):
            del planes[clave]
            en_conflicto.add(clave)
            skips[f"{fuente}: filas divergentes en el origen (se descarta)"] += 1
            continue
        planes[clave] = obs
    return planes, skips


# ---------------------------------------------------------------------------
# Escritura en Orbit (rol de ingesta), patron de app/listings.py
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

# Append-only + idempotente por (platform, seller_sku, metric_date, fuente,
# observed_at): la re-corrida con el MISMO observed_at no duplica filas.
_SQL_INSERT = """
INSERT INTO disponibilidad_observation
    (platform, seller_sku, metric_date, fuente, quantity, fetched_at, observed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT disponibilidad_observation_anti_duplicado DO NOTHING
"""


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


def sync_disponibilidad(
    conn: psycopg.Connection,
    ruta_sqlite: Path | str,
    observed_at: dt.datetime | None = None,
    metric_date: dt.date | None = None,
) -> ResultadoDisponibilidad:
    """Carga el snapshot a disponibilidad_observation y sella su ingest_run.

    `observed_at` (UTC ahora por defecto) y `metric_date` (su fecha UTC) son
    parte de la llave anti-duplicado: pasarlos explicitos hace la corrida
    reproducible en tests. Cero UPDATE/DELETE: append-only (Regla 5).
    """
    snapshot = leer_snapshot(ruta_sqlite)
    if observed_at is None:
        observed_at = dt.datetime.now(dt.UTC)
    if observed_at.tzinfo is None:
        raise DisponibilidadError("observed_at debe traer zona horaria (UTC)")
    if metric_date is None:
        metric_date = observed_at.date()

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    insertadas = 0
    idempotentes = 0
    try:
        with conn.transaction():
            # Conciliacion contra el catalogo de Orbit (mapa 6): solo se
            # escribe disponibilidad de un (platform, seller_sku) publicado.
            skus_orbit = {
                (plataforma, seller_sku)
                for plataforma, seller_sku in conn.execute(
                    "SELECT platform, seller_sku FROM listing WHERE seller_sku IS NOT NULL"
                )
            }
            planes, skips = plan_disponibilidad(snapshot, skus_orbit)
            for clave in sorted(planes):
                obs = planes[clave]
                fila = (
                    obs.plataforma,
                    obs.seller_sku,
                    metric_date,
                    obs.fuente,
                    obs.quantity,
                    obs.fetched_at,
                    observed_at,
                )
                cur = conn.execute(_SQL_INSERT, fila)
                if cur.rowcount == 0:
                    idempotentes += 1
                else:
                    insertadas += 1
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN,
                (insertadas, sum(skips.values()), _formato_skip_reason(skips), True, run_id),
            )
    except Exception:
        with conn.transaction():
            conn.execute(
                _SQL_SELLAR_RUN,
                (insertadas, sum(skips.values()), "fallo la corrida", False, run_id),
            )
        raise
    return ResultadoDisponibilidad(
        run_id=run_id,
        ok=True,
        rows_written=insertadas,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        filas_insertadas=insertadas,
        filas_idempotentes=idempotentes,
    )


# ---------------------------------------------------------------------------
# Etiqueta: tres estados + Sin verificar (Featured Offer)
# ---------------------------------------------------------------------------

# Featured Offer: fuente no verificada, ampliacion abierta ORBIT19 B.3.
# Constante declarada, NO integracion: sin columna en el bridge (0.3, (d)).
_FEATURED_OFFER_SIN_VERIFICAR: dict = {
    "estado": ESTADO_SIN_VERIFICAR,
    "cantidad": None,
    "fuente": None,
    "freshness": None,
    "nota": "Featured Offer: fuente no verificada, ampliacion abierta ORBIT19 B.3",
}


def estado_desde_observaciones(
    observaciones: list[tuple[str, int | None, dt.datetime]],
) -> dict:
    """Etiqueta pura a partir de (fuente, quantity, fetched_at) observados.

    Contrato (AC8): 'positivo' si alguna fuente observo >0; 'cero' si toda
    fuente observada trae 0; 'desconocido' sin filas o con solo NULL.
    `cantidad` y `freshness` van por fuente: FBA y FBM no se suman.
    Ningun estado bloquea la seleccion de publicaciones.
    """
    if not observaciones:
        return {
            "estado": ESTADO_DESCONOCIDO,
            "cantidad": None,
            "fuente": None,
            "freshness": None,
        }
    cantidad = {fuente: qty for fuente, qty, _ in observaciones}
    freshness = {fuente: fetched.isoformat() for fuente, _, fetched in observaciones}
    cantidades = [qty for qty in cantidad.values() if qty is not None]
    if any(qty is not None and qty > 0 for qty in cantidad.values()):
        estado = ESTADO_POSITIVO
    elif cantidades:  # observadas y todas en 0
        estado = ESTADO_CERO
    else:  # filas presentes pero solo NULL: desconocido, no cero
        estado = ESTADO_DESCONOCIDO
    return {
        "estado": estado,
        "cantidad": cantidad,
        "fuente": sorted(cantidad),
        "freshness": freshness,
    }


def estado_disponibilidad(
    conn: psycopg.Connection,
    platform: str,
    seller_sku: str,
    aspecto: str = ASPECTO_STOCK,
) -> dict:
    """Etiqueta de disponibilidad de un (platform, seller_sku).

    aspecto='stock': ultima observacion append-only por fuente (DISTINCT ON
    por observed_at). aspecto='featured_offer': 'sin_verificar' constante
    (ampliacion abierta, no integracion declarada).
    """
    if aspecto == ASPECTO_FEATURED_OFFER:
        return dict(_FEATURED_OFFER_SIN_VERIFICAR)
    if aspecto != ASPECTO_STOCK:
        raise DisponibilidadError(f"aspecto invalido: {aspecto!r}")
    filas = conn.execute(
        "SELECT DISTINCT ON (fuente) fuente, quantity, fetched_at"
        " FROM disponibilidad_observation"
        " WHERE platform = %s AND seller_sku = %s"
        " ORDER BY fuente, observed_at DESC, fetched_at DESC",
        (platform, seller_sku),
    ).fetchall()
    return estado_desde_observaciones(
        [(fuente, quantity, fetched_at) for fuente, quantity, fetched_at in filas]
    )
