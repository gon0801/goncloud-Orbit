"""Tests de disponibilidad comercial (ORBIT 19 B.3, app/disponibilidad.py).

Fuente verificada en 0.3 (docs/evidencia/orbit-19/0.3/reporte.md, tabla
(c)/(d)): FBA = amazon_fba_inventory.quantity_available; FBM =
amazon_listing_prices.quantity SOLO con fulfillment_channel=DEFAULT
(AMAZON_NA siempre NULL). amazon_inventory_cache PROHIBIDO. FBA y FBM no se
suman. Featured Offer = Sin verificar (ampliacion abierta).

(a) UNITARIOS: plan_disponibilidad pura contra un fixture SQLite con el
    esquema REAL del bridge (medido en 0.3) y estado_desde_observaciones.
(b) INTEGRACION: migracion 0022 + sync idempotente + estado_disponibilidad
    contra Postgres real. Skip fail-closed sin Postgres (misma condicion que
    test_schema).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pglast
import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.disponibilidad import (
    ASPECTO_FEATURED_OFFER,
    ESTADO_CERO,
    ESTADO_DESCONOCIDO,
    ESTADO_POSITIVO,
    ESTADO_SIN_VERIFICAR,
    DisponibilidadError,
    estado_desde_observaciones,
    estado_disponibilidad,
    estados_disponibilidad_por_sku,
    leer_snapshot,
    plan_disponibilidad,
    sync_disponibilidad,
)

ROOT = Path(__file__).resolve().parents[1]
SQL22 = (ROOT / "migrations" / "0022_disponibilidad_snapshot.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

UTC = dt.UTC
FRESKO = "2026-09-06 18:36:21"  # frescura observada en la sonda 0.3
VIEJO = "2026-04-13 04:01:06"  # fetched_at min observado en listing_prices

# ---------------------------------------------------------------------------
# Fixture: snapshot del bridge con el esquema REAL (medido en 0.3)
# ---------------------------------------------------------------------------

_DDL_BRIDGE = """
CREATE TABLE amazon_fba_inventory (
    id INTEGER PRIMARY KEY, seller_sku TEXT NOT NULL, fnsku TEXT, asin TEXT,
    marketplace_id TEXT NOT NULL, marketplace_name TEXT, condition_type TEXT,
    quantity_available INTEGER, fetched_at TEXT
);
CREATE TABLE amazon_listing_prices (
    id INTEGER PRIMARY KEY, seller_sku TEXT NOT NULL, asin TEXT, listing_id TEXT,
    marketplace_id TEXT NOT NULL, marketplace_name TEXT, price REAL,
    quantity INTEGER, fulfillment_channel TEXT, item_name TEXT,
    status TEXT, fetched_at TEXT
);
-- PROHIBIDO leer esta tabla: existe en el bridge real y aqui para probar que
-- el adaptador JAMAS la usa (806/806 filas en cero, stale ~27d).
CREATE TABLE amazon_inventory_cache (
    seller_sku TEXT PRIMARY KEY, asin TEXT, product_name TEXT,
    qty INTEGER DEFAULT 0, marketplace TEXT DEFAULT 'MX', updated_at TEXT
);
"""

_MX = "A1AM78C64UM0Y8"  # amazon_mx
_US = "ATVPDKIKX0DER"  # amazon_us


def _snapshot_bridge(tmp_path: Path, fba: list[tuple], listings: list[tuple]) -> Path:
    """Crea la SQLite del bridge.

    fba: (seller_sku, marketplace_id, quantity_available, fetched_at)
    listings: (seller_sku, marketplace_id, quantity, fulfillment_channel,
               fetched_at)
    """
    ruta = tmp_path / "bridge.db"
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_BRIDGE)
    con.executemany(
        "INSERT INTO amazon_fba_inventory"
        " (seller_sku, marketplace_id, quantity_available, fetched_at)"
        " VALUES (?, ?, ?, ?)",
        fba,
    )
    con.executemany(
        "INSERT INTO amazon_listing_prices"
        " (seller_sku, marketplace_id, quantity, fulfillment_channel, fetched_at)"
        " VALUES (?, ?, ?, ?, ?)",
        listings,
    )
    # Cebado de la trampa: cache "lleno" de ceros. Si el adaptador lo leyera,
    # todo SKU tendria cantidad 0 y los tests de estados positivos fallarian.
    con.executemany(
        "INSERT INTO amazon_inventory_cache (seller_sku, qty, marketplace, updated_at)"
        " VALUES (?, 0, 'MX', '2026-08-10 06:14:34')",
        [(sku,) for sku in dict.fromkeys(sku for sku, *_ in fba)],
    )
    con.commit()
    con.close()
    return ruta


# ---------------------------------------------------------------------------
# (a) UNITARIOS: lectura y planificacion puras
# ---------------------------------------------------------------------------


def test_fba_cero_observado_no_es_null(tmp_path):
    """quantity_available=0 es un cero OBSERVADO: se escribe 0, no NULL."""
    ruta = _snapshot_bridge(tmp_path, fba=[("SS-CERO", _MX, 0, FRESKO)], listings=[])
    snapshot = leer_snapshot(ruta)
    planes, _ = plan_disponibilidad(snapshot, {("amazon_mx", "SS-CERO")})
    assert planes[("amazon_mx", "SS-CERO", "fba")].quantity == 0


def test_fba_null_o_fila_ausente_es_desconocido_jamas_cero(tmp_path):
    """quantity_available NULL (o fila ausente) = desconocido; JAMAS 0."""
    ruta = _snapshot_bridge(
        tmp_path,
        fba=[("SS-NULL", _MX, None, FRESKO)],  # SS-AUSENTE no tiene fila FBA
        listings=[],
    )
    snapshot = leer_snapshot(ruta)
    planes, _ = plan_disponibilidad(
        snapshot, {("amazon_mx", "SS-NULL"), ("amazon_mx", "SS-AUSENTE")}
    )
    assert planes[("amazon_mx", "SS-NULL", "fba")].quantity is None
    assert ("amazon_mx", "SS-AUSENTE", "fba") not in planes


def test_fbm_solo_canal_default_amazon_na_no_es_fbm(tmp_path):
    """AMAZON_NA.quantity siempre NULL: la fila NO es observacion FBM."""
    ruta = _snapshot_bridge(
        tmp_path,
        fba=[],
        listings=[
            ("SS-NA", _MX, None, "AMAZON_NA", FRESKO),  # FBA listing: se omite
            ("SS-DEFAULT", _MX, 9, "DEFAULT", FRESKO),  # FBM real
        ],
    )
    snapshot = leer_snapshot(ruta)
    assert [f.seller_sku for f in snapshot.fbm] == ["SS-DEFAULT"]  # la lectura excluye NA
    planes, _ = plan_disponibilidad(snapshot, {("amazon_mx", "SS-NA"), ("amazon_mx", "SS-DEFAULT")})
    assert ("amazon_mx", "SS-NA", "fbm") not in planes
    assert planes[("amazon_mx", "SS-DEFAULT", "fbm")].quantity == 9


def test_fba_fbm_no_se_suman(tmp_path):
    """FBA=3 y FBM=5 del mismo SKU producen DOS filas; nada vale 8."""
    ruta = _snapshot_bridge(
        tmp_path,
        fba=[("SS-MIX", _MX, 3, FRESKO)],
        listings=[("SS-MIX", _MX, 5, "DEFAULT", FRESKO)],
    )
    snapshot = leer_snapshot(ruta)
    planes, _ = plan_disponibilidad(snapshot, {("amazon_mx", "SS-MIX")})
    assert planes[("amazon_mx", "SS-MIX", "fba")].quantity == 3
    assert planes[("amazon_mx", "SS-MIX", "fbm")].quantity == 5


def test_frescura_vieja_se_propaga_no_se_descarta(tmp_path):
    """fetched_at antiguo pasa igual (append-only): la ingesta no la maquilla
    ni la descarta; la capa de estado la expone."""
    ruta = _snapshot_bridge(tmp_path, fba=[("SS-VIEJO", _MX, 7, VIEJO)], listings=[])
    snapshot = leer_snapshot(ruta)
    planes, _ = plan_disponibilidad(snapshot, {("amazon_mx", "SS-VIEJO")})
    assert planes[("amazon_mx", "SS-VIEJO", "fba")].fetched_at == dt.datetime(
        2026, 4, 13, 4, 1, 6, tzinfo=UTC
    )


def test_cache_prohibido_nunca_se_lee(tmp_path):
    """El cache cebado con ceros no produce observaciones."""
    ruta = _snapshot_bridge(tmp_path, fba=[], listings=[])
    snapshot = leer_snapshot(ruta)
    planes, _ = plan_disponibilidad(snapshot, {("amazon_mx", "SS-CUALQUIERA")})
    assert planes == {}
    # Candado de arquitectura: el adaptador no consulta la tabla.
    fuente = (ROOT / "app" / "disponibilidad.py").read_text(encoding="utf-8")
    assert "FROM amazon_inventory_cache" not in fuente


def test_sku_sin_match_en_orbit_se_reporta(tmp_path):
    """SKU del bridge sin listing en Orbit: fila NO escrita, skip contado."""
    ruta = _snapshot_bridge(tmp_path, fba=[("SS-FANTASMA", _MX, 4, FRESKO)], listings=[])
    snapshot = leer_snapshot(ruta)
    planes, skips = plan_disponibilidad(snapshot, {("amazon_mx", "SS-OTRO")})
    assert ("amazon_mx", "SS-FANTASMA", "fba") not in planes
    assert skips["fba: seller_sku sin listing en Orbit"] == 1


def test_marketplace_fuera_de_dominio_rechazado(tmp_path):
    ruta = _snapshot_bridge(tmp_path, fba=[("SS-RARO", "OtroMarket", 4, FRESKO)], listings=[])
    snapshot = leer_snapshot(ruta)
    planes, skips = plan_disponibilidad(snapshot, {("amazon_mx", "SS-RARO")})
    assert planes == {}
    assert any("marketplace fuera de dominio" in motivo for motivo in skips)


def test_filas_divergentes_se_descartan(tmp_path):
    """Mismo (sku, fuente) con valores distintos: no se elige arbitrario."""
    ruta = _snapshot_bridge(
        tmp_path,
        fba=[("SS-DUP", _MX, 1, FRESKO), ("SS-DUP", _MX, 2, FRESKO)],
        listings=[],
    )
    snapshot = leer_snapshot(ruta)
    planes, skips = plan_disponibilidad(snapshot, {("amazon_mx", "SS-DUP")})
    assert ("amazon_mx", "SS-DUP", "fba") not in planes
    assert skips["fba: filas divergentes en el origen (se descarta)"] == 1


def test_snapshot_inexistente_falla_claro(tmp_path):
    with pytest.raises(DisponibilidadError, match="snapshot inexistente"):
        leer_snapshot(tmp_path / "no-existe.db")


# ---------------------------------------------------------------------------
# (a) UNITARIOS: etiqueta pura (tres estados + Sin verificar)
# ---------------------------------------------------------------------------


def _t(fuente: str, qty: int | None, cuando: str = FRESKO) -> tuple[str, int | None, dt.datetime]:
    return fuente, qty, dt.datetime.strptime(cuando, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def test_estado_cero_observado():
    res = estado_desde_observaciones([_t("fba", 0), _t("fbm", 0)])
    assert res["estado"] == "cero"
    assert res["cantidad"] == {"fba": 0, "fbm": 0}


def test_estado_positivo_no_suma_canales():
    res = estado_desde_observaciones([_t("fba", 0), _t("fbm", 5)])
    assert res["estado"] == ESTADO_POSITIVO
    assert res["cantidad"] == {"fba": 0, "fbm": 5}  # nunca 5+0


def test_estado_desconocido_sin_filas_o_solo_null():
    assert estado_desde_observaciones([])["estado"] == ESTADO_DESCONOCIDO
    res = estado_desde_observaciones([_t("fba", None)])
    assert res["estado"] == ESTADO_DESCONOCIDO
    assert res["cantidad"] == {"fba": None}  # NULL no se convierte en 0


def test_estado_cero_exige_todas_las_fuentes_con_cantidad():
    """Hallazgo cross-review codex 2026-09-07: FBA=0 y FBM=NULL NO es cero --
    'cero' exige que TODAS las fuentes observadas trajeron cantidad no-NULL y
    todas en 0; la incertidumbre de una fuente se preserva (regla 3: NULL
    jamas se convierte en 0)."""
    mixto = estado_desde_observaciones([_t("fba", 0), _t("fbm", None)])
    assert mixto["estado"] == ESTADO_DESCONOCIDO
    assert mixto["cantidad"] == {"fba": 0, "fbm": None}
    assert estado_desde_observaciones([_t("fba", 0)])["estado"] == ESTADO_CERO
    assert estado_desde_observaciones([_t("fba", None)])["estado"] == ESTADO_DESCONOCIDO
    assert estado_desde_observaciones([_t("fba", 3)])["estado"] == ESTADO_POSITIVO


def test_estado_tres_estados_distinguibles():
    """AC8: cero observado / positivo / desconocido son tres estados distintos."""
    cero = estado_desde_observaciones([_t("fba", 0)])
    positivo = estado_desde_observaciones([_t("fba", 2)])
    desconocido = estado_desde_observaciones([])
    assert len({cero["estado"], positivo["estado"], desconocido["estado"]}) == 3


def test_estado_frescura_por_fuente():
    res = estado_desde_observaciones([_t("fba", 1, FRESKO), _t("fbm", 2, VIEJO)])
    assert res["freshness"]["fba"].startswith("2026-09-06T18:36:21")
    assert res["freshness"]["fbm"].startswith("2026-04-13T04:01:06")


# ---------------------------------------------------------------------------
# (b) ESTATICO de la migracion 0022
# ---------------------------------------------------------------------------


def test_migracion_parsea_y_trae_invariantes():
    stmts = tuple(pglast.parse_sql(SQL22))
    assert len(stmts) > 0
    cuerpo = SQL22.split("CREATE TABLE disponibilidad_observation")[1].split("COMMENT ON TYPE")[0]
    assert "UNIQUE (platform, seller_sku, metric_date, fuente, observed_at)" in cuerpo
    assert "CHECK (quantity IS NULL OR quantity >= 0)" in cuerpo
    # Append-only: sin UPDATE ni DELETE de datos en el cuerpo de la tabla.
    assert "UPDATE" not in cuerpo and "DELETE" not in cuerpo


def test_migracion_declara_featured_offer_como_ampliacion():
    """La migracion declara Featured Offer Sin verificar como ampliacion, no
    integracion terminada (B.3: sin fuente no se declara cierre)."""
    assert "ampliacion abierta" in SQL22


# ---------------------------------------------------------------------------
# (b) INTEGRACION: Postgres real con 0001 + 0022
# ---------------------------------------------------------------------------

ORDEN = ("0001_initial.sql", "0022_disponibilidad_snapshot.sql")


@contextmanager
def db_disponibilidad(prefijo: str = "orbit_b3"):
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sembrar_catalogo(conn, odoo_sku: str, plataforma: str, asin: str, seller_sku: str) -> None:
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (odoo_sku, odoo_sku),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s)",
        (pid, plataforma, asin, seller_sku),
    )


@_skip_db
def test_sync_idempotente_y_tres_estados_en_db(tmp_path):
    with db_disponibilidad() as conn:
        _sembrar_catalogo(conn, "P-1", "amazon_mx", "B0CERO", "SS-CERO")
        _sembrar_catalogo(conn, "P-2", "amazon_mx", "B0NULO", "SS-NULL")
        _sembrar_catalogo(conn, "P-3", "amazon_mx", "B0MIX", "SS-MIX")
        _sembrar_catalogo(conn, "P-4", "amazon_us", "B0NA", "SS-NA-US")
        ruta = _snapshot_bridge(
            tmp_path,
            fba=[
                ("SS-CERO", _MX, 0, FRESKO),
                ("SS-NULL", _MX, None, FRESKO),
                ("SS-MIX", _MX, 3, FRESKO),
            ],
            listings=[
                ("SS-MIX", _MX, 5, "DEFAULT", FRESKO),
                ("SS-NA-US", _US, None, "AMAZON_NA", FRESKO),
            ],
        )
        observed = dt.datetime(2026, 9, 6, 22, 0, 0, tzinfo=UTC)
        r1 = sync_disponibilidad(conn, ruta, observed_at=observed)
        assert r1.ok
        assert r1.filas_insertadas == 4  # cero fba, null fba, mix fba, mix fbm
        # SS-NA-US AMAZON_NA no produce fila; el cache tampoco
        filas = conn.execute(
            "SELECT platform, seller_sku, fuente FROM disponibilidad_observation"
        ).fetchall()
        assert ("amazon_us", "SS-NA-US", "fbm") not in filas
        assert len(filas) == 4

        # Idempotencia: MISMO observed_at -> cero inserciones nuevas
        r2 = sync_disponibilidad(conn, ruta, observed_at=observed)
        assert r2.filas_insertadas == 0
        assert r2.filas_idempotentes == 4
        assert conn.execute("SELECT count(*) FROM disponibilidad_observation").fetchone()[0] == 4

        # Append-only: una re-observacion posterior ANADE, no pisa
        r3 = sync_disponibilidad(conn, ruta, observed_at=observed + dt.timedelta(hours=1))
        assert r3.filas_insertadas == 4
        assert conn.execute("SELECT count(*) FROM disponibilidad_observation").fetchone()[0] == 8

        # Tres estados distinguibles desde la base
        assert estado_disponibilidad(conn, "amazon_mx", "SS-CERO")["estado"] == "cero"
        mix = estado_disponibilidad(conn, "amazon_mx", "SS-MIX")
        assert mix["estado"] == ESTADO_POSITIVO
        assert mix["cantidad"] == {"fba": 3, "fbm": 5}  # no sumados
        nulo = estado_disponibilidad(conn, "amazon_mx", "SS-NULL")
        assert nulo["estado"] == ESTADO_DESCONOCIDO
        assert nulo["cantidad"] == {"fba": None}
        assert estado_disponibilidad(conn, "amazon_us", "SS-NA-US")["estado"] == (
            ESTADO_DESCONOCIDO
        )


@_skip_db
def test_check_rechaza_negativo_y_unique_muerde():
    # Timestamps FIJOS: con now() cada INSERT es su propia transaccion
    # (autocommit) y el reloj avanzaria entre ambas, esquivando el UNIQUE.
    with db_disponibilidad() as conn:
        _sembrar_catalogo(conn, "P-1", "amazon_mx", "B0X", "SS-X")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO disponibilidad_observation"
                " (platform, seller_sku, metric_date, fuente, quantity, fetched_at,"
                "  observed_at) VALUES ('amazon_mx', 'SS-X', '2026-09-06', 'fba', -1,"
                " '2026-09-06 18:36:21+00', '2026-09-06 22:00:00+00')"
            )
        conn.execute(
            "INSERT INTO disponibilidad_observation"
            " (platform, seller_sku, metric_date, fuente, quantity, fetched_at,"
            "  observed_at) VALUES ('amazon_mx', 'SS-X', '2026-09-06', 'fba', 0,"
            " '2026-09-06 18:36:21+00', '2026-09-06 22:00:00+00')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO disponibilidad_observation"
                " (platform, seller_sku, metric_date, fuente, quantity, fetched_at,"
                "  observed_at) VALUES ('amazon_mx', 'SS-X', '2026-09-06', 'fba', 0,"
                " '2026-09-06 18:36:21+00', '2026-09-06 22:00:00+00')"
            )


@_skip_db
def test_featured_offer_sin_verificar_no_toca_la_base():
    with db_disponibilidad() as conn:
        res = estado_disponibilidad(conn, "amazon_mx", "SS-X", aspecto=ASPECTO_FEATURED_OFFER)
        assert res["estado"] == ESTADO_SIN_VERIFICAR
        assert res["cantidad"] is None
        assert "ampliacion abierta ORBIT19 B.3" in res["nota"]
        with pytest.raises(DisponibilidadError):
            estado_disponibilidad(conn, "amazon_mx", "SS-X", aspecto="inexistente")


@_skip_db
def test_estados_disponibilidad_por_sku_coincide_con_unitario_y_respeta_reciente():
    """F2 (Ola F2): consulta por lote con varios SKUs (FBA+FBM, 0/NULL/positivo,
    observacion superada y SKU sin filas) coincide 1:1 con estado_disponibilidad."""
    with db_disponibilidad() as conn:
        for idx, sku in enumerate(("SS-A", "SS-B", "SS-C", "SS-VACIO"), start=1):
            _sembrar_catalogo(conn, f"P-{idx}", "amazon_mx", f"B0L{idx:07d}", sku)

        # Observaciones viejas (deben quedar superadas por las mas recientes).
        filas_sql = [
            # SS-A: FBA viejo=10 -> nuevo=0; FBM viejo=0 -> nuevo=4 => positivo {fba: 0, fbm: 4}
            ("SS-A", "fba", 10, "2026-09-05 10:00:00+00", "2026-09-05 12:00:00+00"),
            ("SS-A", "fba", 0, "2026-09-06 10:00:00+00", "2026-09-06 12:00:00+00"),
            ("SS-A", "fbm", 0, "2026-09-05 10:00:00+00", "2026-09-05 12:00:00+00"),
            ("SS-A", "fbm", 4, "2026-09-06 10:00:00+00", "2026-09-06 12:00:00+00"),
            # SS-B: FBA viejo=7 -> nuevo=0; FBM=0 => cero {fba: 0, fbm: 0}
            ("SS-B", "fba", 7, "2026-09-05 10:00:00+00", "2026-09-05 12:00:00+00"),
            ("SS-B", "fba", 0, "2026-09-06 10:00:00+00", "2026-09-06 12:00:00+00"),
            ("SS-B", "fbm", 0, "2026-09-06 10:00:00+00", "2026-09-06 12:00:00+00"),
            # SS-C: FBA=NULL => desconocido {fba: None}
            ("SS-C", "fba", None, "2026-09-06 10:00:00+00", "2026-09-06 12:00:00+00"),
        ]
        for sku, fuente, qty, fetched_at, observed_at in filas_sql:
            conn.execute(
                "INSERT INTO disponibilidad_observation"
                " (platform, seller_sku, metric_date, fuente, quantity, fetched_at, observed_at)"
                " VALUES ('amazon_mx', %s, '2026-09-06', %s, %s, %s, %s)",
                (sku, fuente, qty, fetched_at, observed_at),
            )

        skus = ["SS-A", "SS-B", "SS-C", "SS-VACIO"]
        lote = estados_disponibilidad_por_sku(conn, "amazon_mx", skus)
        assert set(lote) == set(skus)
        for sku in skus:
            assert lote[sku] == estado_disponibilidad(conn, "amazon_mx", sku), sku
        assert lote["SS-A"]["estado"] == ESTADO_POSITIVO
        assert lote["SS-A"]["cantidad"] == {"fba": 0, "fbm": 4}
        assert lote["SS-B"]["estado"] == ESTADO_CERO
        assert lote["SS-B"]["cantidad"] == {"fba": 0, "fbm": 0}
        assert lote["SS-C"]["estado"] == ESTADO_DESCONOCIDO
        assert lote["SS-C"]["cantidad"] == {"fba": None}
        assert lote["SS-VACIO"]["estado"] == ESTADO_DESCONOCIDO


# ---------------------------------------------------------------------------
# ORBIT 19 / P09: Oferta destacada (SP-API pricing) y stock FBA fresco SP-API
# ---------------------------------------------------------------------------


def test_oferta_destacada_desde_observacion_todos_los_estados():
    from app.disponibilidad import (
        OFERTA_AJENA,
        OFERTA_PROPIA,
        OFERTA_SIN_GANADOR,
        OFERTA_SIN_OFERTAS,
        oferta_destacada_desde_observacion,
    )

    obs_ts = dt.datetime(2026, 11, 27, 5, 2, 44, tzinfo=UTC)
    assert oferta_destacada_desde_observacion(None) == {
        "estado": ESTADO_SIN_VERIFICAR,
        "fuente": None,
        "freshness": None,
    }
    assert oferta_destacada_desde_observacion((True, 1, obs_ts)) == {
        "estado": OFERTA_PROPIA,
        "fuente": "spapi_pricing",
        "freshness": obs_ts.isoformat(),
    }
    assert oferta_destacada_desde_observacion((False, 2, obs_ts)) == {
        "estado": OFERTA_AJENA,
        "fuente": "spapi_pricing",
        "freshness": obs_ts.isoformat(),
    }
    assert oferta_destacada_desde_observacion((None, 0, obs_ts)) == {
        "estado": OFERTA_SIN_OFERTAS,
        "fuente": "spapi_pricing",
        "freshness": obs_ts.isoformat(),
    }
    assert oferta_destacada_desde_observacion((None, 2, obs_ts)) == {
        "estado": OFERTA_SIN_GANADOR,
        "fuente": "spapi_pricing",
        "freshness": obs_ts.isoformat(),
    }


def test_combina_fba_spapi_fresco_supera_bridge_y_conserva_fbm():
    from app.disponibilidad import combina_observaciones_stock

    viejo = dt.datetime(2026, 9, 7, 17, 39, 33, tzinfo=UTC)
    fresco = dt.datetime(2026, 11, 27, 5, 2, 9, tzinfo=UTC)
    # Bridge viejo FBA=0 + FBM=3, SP-API fresco FBA=12 -> FBA toma 12 (fresco) y FBM sigue en 3.
    combinadas = combina_observaciones_stock(
        [("fba", 0, viejo), ("fbm", 3, viejo)],
        spapi_fba=(12, fresco),
    )
    res = estado_desde_observaciones(combinadas)
    assert res["estado"] == ESTADO_POSITIVO
    assert res["cantidad"] == {"fba": 12, "fbm": 3}
    assert res["freshness"]["fba"] == fresco.isoformat()
    assert res["freshness"]["fbm"] == viejo.isoformat()

    # Si SP-API es mas viejo que bridge, bridge gana en FBA.
    mas_viejo = dt.datetime(2026, 9, 1, 5, 0, 0, tzinfo=UTC)
    combinadas_v = combina_observaciones_stock(
        [("fba", 5, viejo)],
        spapi_fba=(0, mas_viejo),
    )
    res_v = estado_desde_observaciones(combinadas_v)
    assert res_v["cantidad"] == {"fba": 5}
    assert res_v["freshness"]["fba"] == viejo.isoformat()


def test_ofertas_destacadas_por_asin_y_stock_spapi_con_conn_simulada():
    from app.disponibilidad import (
        OFERTA_AJENA,
        OFERTA_PROPIA,
        OFERTA_SIN_OFERTAS,
        ofertas_destacadas_por_asin,
    )

    obs_ts = dt.datetime(2026, 11, 27, 5, 2, 44, tzinfo=UTC)

    class _Cur:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class _Conn:
        def __init__(self, responses):
            self._responses = list(responses)

        def execute(self, _sql, _params=None):
            return _Cur(self._responses.pop(0))

    conn = _Conn(
        [
            [("spapi_price_observation",)],
            [
                ("B0OWN", True, 1, obs_ts),
                ("B0OTHER", False, 3, obs_ts),
                ("B0NONE", None, 0, obs_ts),
            ],
        ]
    )
    res = ofertas_destacadas_por_asin(conn, "amazon_mx", ["B0OWN", "B0OTHER", "B0NONE", "B0MISS"])
    assert res["B0OWN"]["estado"] == OFERTA_PROPIA
    assert res["B0OTHER"]["estado"] == OFERTA_AJENA
    assert res["B0NONE"]["estado"] == OFERTA_SIN_OFERTAS
    assert res["B0MISS"]["estado"] == ESTADO_SIN_VERIFICAR
    assert res["B0OWN"]["freshness"] == obs_ts.isoformat()


@_skip_db
def test_postgres_spapi_oferta_destacada_y_fba_fresco():
    """Integracion real sobre 0001 + 0022 + 0032 + 0035: oferta destacada y FBA fresco."""
    from app.disponibilidad import (
        OFERTA_AJENA,
        OFERTA_PROPIA,
        OFERTA_SIN_OFERTAS,
        ofertas_destacadas_por_asin,
    )

    with db_disponibilidad("orbit_p09") as conn:
        for mig in ("0032_spapi_pricing.sql", "0035_spapi_listings_inventario.sql"):
            conn.execute((ROOT / "migrations" / mig).read_text(encoding="utf-8"))
        _sembrar_catalogo(conn, "P-1", "amazon_mx", "B0OWN11111", "SKU-OWN")
        _sembrar_catalogo(conn, "P-2", "amazon_mx", "B0OTH22222", "SKU-OTH")
        _sembrar_catalogo(conn, "P-3", "amazon_mx", "B0NOO33333", "SKU-NOO")
        # Bridge viejo FBA=0 + FBM=2
        conn.execute(
            "INSERT INTO disponibilidad_observation"
            " (platform, seller_sku, metric_date, fuente, quantity, fetched_at, observed_at)"
            " VALUES"
            " ('amazon_mx', 'SKU-OWN', '2026-09-07', 'fba', 0,"
            "  '2026-09-07 17:00:00+00', '2026-09-07 17:39:00+00'),"
            " ('amazon_mx', 'SKU-OWN', '2026-09-07', 'fbm', 2,"
            "  '2026-09-07 17:00:00+00', '2026-09-07 17:39:00+00')"
        )
        # SP-API fresco FBA vendible (fulfillable=9) supera al bridge viejo sin tocar FBM;
        # SKU-OTH tiene total=50 pero fulfillable=0 (en camino/reservado) -> Stock en 0;
        # SKU-NOO tiene total=50 pero fulfillable=NULL -> conserva bridge FBA=0, jamas 50.
        conn.execute(
            "INSERT INTO disponibilidad_observation"
            " (platform, seller_sku, metric_date, fuente, quantity, fetched_at, observed_at)"
            " VALUES"
            " ('amazon_mx', 'SKU-NOO', '2026-09-07', 'fba', 0,"
            "  '2026-09-07 17:00:00+00', '2026-09-07 17:39:00+00')"
        )
        conn.execute(
            "INSERT INTO spapi_inventario_observation"
            " (seller_sku, asin, platform, metric_date, observed_at,"
            "  total_quantity, fulfillable_quantity, api_version)"
            " VALUES"
            " ('SKU-OWN', 'B0OWN11111', 'amazon_mx', '2026-11-27',"
            "  '2026-11-27 05:02:00+00', 15, 9, 'v1'),"
            " ('SKU-OTH', 'B0OTH22222', 'amazon_mx', '2026-11-27',"
            "  '2026-11-27 05:02:00+00', 50, 0, 'v1'),"
            " ('SKU-NOO', 'B0NOO33333', 'amazon_mx', '2026-11-27',"
            "  '2026-11-27 05:02:00+00', 50, NULL, 'v1')"
        )
        # SP-API pricing (dos observaciones: gana la mas reciente)
        conn.execute(
            "INSERT INTO spapi_price_observation"
            " (asin, platform, metric_date, observed_at, buy_box_is_own, offers_count)"
            " VALUES"
            " ('B0OWN11111', 'amazon_mx', '2026-11-26', '2026-11-26 05:00:00+00', false, 2),"
            " ('B0OWN11111', 'amazon_mx', '2026-11-27', '2026-11-27 05:02:00+00', true, 1),"
            " ('B0OTH22222', 'amazon_mx', '2026-11-27', '2026-11-27 05:02:00+00', false, 3),"
            " ('B0NOO33333', 'amazon_mx', '2026-11-27', '2026-11-27 05:02:00+00', NULL, 0)"
        )

        stock = estados_disponibilidad_por_sku(conn, "amazon_mx", ["SKU-OWN", "SKU-OTH", "SKU-NOO"])
        assert stock["SKU-OWN"]["estado"] == ESTADO_POSITIVO
        assert stock["SKU-OWN"]["cantidad"] == {"fba": 9, "fbm": 2}
        assert stock["SKU-OWN"]["freshness"]["fba"].startswith("2026-11-27T05:02:00")
        assert stock["SKU-OTH"]["estado"] == ESTADO_CERO
        assert stock["SKU-OTH"]["cantidad"] == {"fba": 0}
        assert stock["SKU-NOO"]["estado"] == ESTADO_CERO
        assert stock["SKU-NOO"]["cantidad"] == {"fba": 0}
        assert stock["SKU-NOO"]["freshness"]["fba"].startswith("2026-09-07T17:00:00")

        ofertas = ofertas_destacadas_por_asin(
            conn, "amazon_mx", ["B0OWN11111", "B0OTH22222", "B0NOO33333", "B0SIN44444"]
        )
        assert ofertas["B0OWN11111"]["estado"] == OFERTA_PROPIA
        assert ofertas["B0OTH22222"]["estado"] == OFERTA_AJENA
        assert ofertas["B0NOO33333"]["estado"] == OFERTA_SIN_OFERTAS
        assert ofertas["B0SIN44444"]["estado"] == ESTADO_SIN_VERIFICAR
        assert (
            estado_disponibilidad(conn, "amazon_mx", "SKU-OWN", aspecto=ASPECTO_FEATURED_OFFER)[
                "estado"
            ]
            == OFERTA_PROPIA
        )
