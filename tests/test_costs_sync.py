"""Tests de la ingesta de productos y costos desde contabilidad (ORBIT 06 0.1).

Fuente: snapshot de la SQLite de accounting (`sku_costs` + `bom_headers`), con
el esquema y las formas de dato medidas en vivo el 2026-08-29 (ver las
Decisiones de la 0.1 en plans/orbit-06.md: colapso a una vigencia por dia con
el ULTIMO valor, valid_from NULL arranca en created_at, includes_tax=false).

(a) UNITARIOS: `colapsar` pura y `leer_origen` contra una SQLite temporal con
    el esquema real de accounting. Corren siempre.
(b) INTEGRACION: `sync_costos` contra un Postgres temporal con la migracion
    aplicada: corrida completa, doble corrida no-op REAL (base identica), la
    vigencia nueva CIERRA la anterior, divergencia retroactiva rechazada sin
    escribir nada del SKU. Skip automatico sin Postgres utilizable.

El trigger `sku_cost_solo_cierra_vigencia` (UPDATE de importe / DELETE /
reabrir) ya lo prueba tests/test_schema.py contra la migracion con
superusuario; aqui no se duplica: lo que se prueba es que el PIPELINE jamas
intenta esas mutaciones (diverge por SKU y cuenta el skip).

Regla 9: los tests de skip AFIRMAN el skip y su motivo.
"""

from __future__ import annotations

import os
import socket
import sqlite3
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from test_schema import SQL, _hay_postgres_local, _test_dsn

from app.costs import (
    NOMBRE_SIN_ODOO,
    FilaCosto,
    Vigencia,
    _plan_sku,
    colapsar,
    leer_origen,
    main,
    sync_costos,
)

# ---------------------------------------------------------------------------
# Helpers: snapshot de accounting con el esquema REAL (medido en vivo)
# ---------------------------------------------------------------------------

_DDL_ACCOUNTING = """
CREATE TABLE sku_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    cost REAL NOT NULL,
    currency TEXT DEFAULT 'MXN',
    valid_from TEXT DEFAULT (datetime('now')),
    valid_to TEXT,
    source TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE bom_headers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    odoo_bom_id INTEGER,
    product_sku TEXT,
    product_name TEXT,
    updated_at TEXT,
    UNIQUE(odoo_bom_id, product_sku)
);
"""


def _snapshot(ruta: Path, filas: list[tuple], nombres: list[tuple]) -> Path:
    """Crea una SQLite con el esquema de accounting y las filas dadas.

    filas: (sku, cost, currency, valid_from, valid_to, source, created_at)
    nombres: (odoo_bom_id, product_sku, product_name)
    """
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_ACCOUNTING)
    con.executemany(
        "INSERT INTO sku_costs (sku, cost, currency, valid_from, valid_to, source,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        filas,
    )
    con.executemany(
        "INSERT INTO bom_headers (odoo_bom_id, product_sku, product_name) VALUES (?, ?, ?)",
        nombres,
    )
    con.commit()
    con.close()
    return ruta


def _fila(
    sku: str,
    costo: float | None,
    inicio: str | None,
    fin: str | None,
    moneda: str = "MXN",
    creado: str = "2026-02-20 05:00:02",
) -> FilaCosto:
    return FilaCosto(sku=sku, costo=costo, moneda=moneda, inicio=inicio, fin=fin, creado=creado)


# Muestras REALES medidas en contabilidad (2026-08-29): la rotacion intradia
# identica del Y4-FB35-N645 y los tres costos distintos en un dia del
# NH-CAR-AZU-CEN-DOR. Los tests usan exactamente esas formas.
_FILAS_Y4 = [
    (
        "Y4-FB35-N645",
        301.5,
        "MXN",
        "2026-02-07 12:25:00",
        "2026-02-07 12:47:06",
        "manual",
        "2026-02-07 12:25:00",
    ),
    (
        "Y4-FB35-N645",
        301.5,
        "MXN",
        "2026-02-07 12:47:06",
        "2026-02-07 12:47:07",
        "manual",
        "2026-02-07 12:25:00",
    ),
    (
        "Y4-FB35-N645",
        301.5,
        "MXN",
        "2026-02-07 12:47:07",
        "2026-02-07 12:47:08",
        "manual",
        "2026-02-07 12:25:00",
    ),
    (
        "Y4-FB35-N645",
        301.5,
        "MXN",
        "2026-02-07 12:47:08",
        None,
        "manual",
        "2026-02-07 12:25:00",
    ),
]

_FILAS_NH = [
    (
        "NH-CAR-AZU-CEN-DOR",
        301.5,
        "MXN",
        None,
        "2026-08-18 05:00:07",
        "odoo_sync",
        "2026-08-18 05:00:07",
    ),
    (
        "NH-CAR-AZU-CEN-DOR",
        291.63,
        "MXN",
        "2026-08-18 05:00:07",
        "2026-08-18 06:00:07",
        "odoo_sync",
        "2026-08-18 05:00:07",
    ),
    (
        "NH-CAR-AZU-CEN-DOR",
        306.63,
        "MXN",
        "2026-08-18 06:00:07",
        "2026-08-18 07:00:06",
        "odoo_sync",
        "2026-08-18 05:00:07",
    ),
    (
        "NH-CAR-AZU-CEN-DOR",
        304.65,
        "MXN",
        "2026-08-18 07:00:06",
        None,
        "odoo_sync",
        "2026-08-18 05:00:07",
    ),
]


# ---------------------------------------------------------------------------
# (a) colapsar: pura
# ---------------------------------------------------------------------------


def test_rotacion_intradia_identica_colapsa_a_una_vigencia():
    """Las 753 filas que abren y cierran el mismo dia no pueden ir fila por
    fila: darian valid_to = valid_from (viola sku_cost_rango). El colapso deja
    UNA vigencia por (sku, dia)."""
    vigencias, skips, stats = colapsar(
        [_fila(f[0], f[1], f[3], f[4], moneda=f[2]) for f in _FILAS_Y4]
    )
    assert skips == Counter()
    assert vigencias == {
        "Y4-FB35-N645": [("Y4-FB35-N645", Decimal("301.5"), "MXN", date(2026, 2, 7), None)]
    }
    assert stats["filas"] == 4
    assert stats["vigencias"] == 1


def test_ultimo_valor_del_dia_gana():
    """Tres costos DISTINTOS dentro del mismo dia (medido: 603 grupos asi):
    el costo del dia es el de la ultima fila (sellado: una venta del dia D
    uso UN costo). Ademas la fila con valid_from NULL cae dentro del mismo
    dia y se absorbe."""
    filas = [
        _fila("NH", 301.5, None, "2026-08-18 05:00:07", creado="2026-08-18 05:00:07"),
        _fila("NH", 291.63, "2026-08-18 05:00:07", "2026-08-18 06:00:07"),
        _fila("NH", 306.63, "2026-08-18 06:00:07", "2026-08-18 07:00:06"),
        _fila("NH", 304.65, "2026-08-18 07:00:06", None),
    ]
    vigencias, skips, _ = colapsar(filas)
    assert skips == Counter()
    assert vigencias == {"NH": [("NH", Decimal("304.65"), "MXN", date(2026, 8, 18), None)]}


def test_dias_consecutivos_de_igual_costo_se_funden():
    filas = [
        _fila("X", 100.0, "2026-03-01 10:00:00", "2026-03-03 09:00:00"),
        _fila("X", 100.0, "2026-03-03 09:00:00", "2026-03-05 09:00:00"),
        _fila("X", 120.0, "2026-03-05 09:00:00", None),
    ]
    vigencias, _, stats = colapsar(filas)
    assert vigencias == {
        "X": [
            ("X", Decimal("100"), "MXN", date(2026, 3, 1), date(2026, 3, 5)),
            ("X", Decimal("120"), "MXN", date(2026, 3, 5), None),
        ]
    }
    assert stats["fusiones"] == 1


def test_valid_from_null_arranca_en_created_at():
    """974 filas del backfill con valid_from NULL: contabilidad las trata como
    "desde siempre"; Orbit solo reclama desde created_at (regla 3: la fila no
    puede probar cobertura anterior a su creacion)."""
    filas = [
        _fila("4207", 81.2, None, "2026-05-04 05:40:54", creado="2026-02-20 05:00:02"),
        _fila("4207", 84.0, "2026-05-04 05:40:54", None, creado="2026-02-20 05:00:02"),
    ]
    vigencias, skips, _ = colapsar(filas)
    assert skips == Counter()
    assert vigencias == {
        "4207": [
            ("4207", Decimal("81.2"), "MXN", date(2026, 2, 20), date(2026, 5, 4)),
            ("4207", Decimal("84"), "MXN", date(2026, 5, 4), None),
        ]
    }


def test_costo_cero_no_se_escribe_y_corta_la_cadena():
    """Sellado 1: costo 0 o NULL es dato faltante -> la fila NO se escribe y
    queda CONTADA. Ademas no puede dejar que la vigencia anterior se estire
    sobre sus dias: el cero corta (los dias del cero quedan sin costo)."""
    filas = [
        _fila("Z", 100.0, "2026-04-01 10:00:00", "2026-04-03 09:00:00"),
        _fila("Z", 0.0, "2026-04-03 09:00:00", "2026-04-05 09:00:00"),
        _fila("Z", 110.0, "2026-04-05 09:00:00", None),
    ]
    vigencias, skips, _ = colapsar(filas)
    assert skips == Counter({"costo cero o nulo (dato faltante)": 1})
    assert vigencias == {
        "Z": [
            ("Z", Decimal("100"), "MXN", date(2026, 4, 1), date(2026, 4, 3)),
            ("Z", Decimal("110"), "MXN", date(2026, 4, 5), None),
        ]
    }


def test_costo_nulo_rechazado():
    _, skips, _ = colapsar([_fila("N", None, "2026-04-01 10:00:00", None)])
    assert skips == Counter({"costo cero o nulo (dato faltante)": 1})


def test_moneda_fuera_de_dominio_rechazada():
    _, skips, _ = colapsar([_fila("E", 100.0, "2026-04-01 10:00:00", None, moneda="EUR")])
    assert skips == Counter({"moneda fuera de dominio (MXN/USD): EUR": 1})


def test_sku_vacio_rechazado():
    _, skips, _ = colapsar([_fila("  ", 100.0, "2026-04-01 10:00:00", None)])
    assert skips == Counter({"sku vacio": 1})


def test_vigencia_invertida_rechazada():
    _, skips, _ = colapsar([_fila("I", 100.0, "2026-04-05 10:00:00", "2026-04-01 09:00:00")])
    assert skips == Counter({"vigencia invertida (valid_to < valid_from)": 1})


def test_timestamp_corrupto_rechazado():
    """Hallazgo 4 de codex (baja): '2026-04-01basura' pasaba como fecha valida
    porque _dia solo miraba los primeros 10 caracteres — una fecha plausible
    publicada sobre un dato corrupto. Ahora se parsea el timestamp COMPLETO."""
    _, skips, _ = colapsar([_fila("C", 100.0, "2026-04-01basura", None, creado="tambien-basura")])
    assert skips == Counter({"fecha de inicio ilegible (valid_from/created_at)": 1})
    _, skips_fin, _ = colapsar([_fila("C", 100.0, "2026-04-01 10:00:00", "2026-05-01xy")])
    assert skips_fin == Counter({"fecha de cierre ilegible (valid_to)": 1})


def test_costo_con_mas_de_4_decimales_rechazado():
    # str(301.50001) preserva los decimales: NUMERIC(14,4) no puede guardarlos
    # sin redondear, y redondear dinero en silencio es lo que la regla 4 mata.
    _, skips, _ = colapsar([_fila("D", 301.50001, "2026-04-01 10:00:00", None)])
    assert skips == Counter({"costo con mas de 4 decimales": 1})


def test_ruido_binario_del_float_se_cuantiza():
    """Hallazgo de la corrida real (2026-08-29): 506 de 2,708 filas llegan como
    554.1800000000001 (ruido binario del REAL de SQLite, residuo <= 1e-13). El
    valor ES dinero de 2 decimales: se cuantiza a 4, no se rechaza. Precision
    GENUINA de mas de 4 decimales (>= 1e-5) si se rechaza."""
    vigencias, skips, _ = colapsar([_fila("R", 554.1800000000001, "2026-04-01 10:00:00", None)])
    assert skips == Counter()
    assert vigencias == {"R": [("R", Decimal("554.18"), "MXN", date(2026, 4, 1), None)]}


# --- hallazgos del adversario (0 altos, 3 medios, 3 bajos), 2026-08-30 ------


def test_costo_subcentavo_cuantiza_a_cero_y_se_rechaza():
    """Hallazgo 1 (medio): un costo en (0, 1e-5) pasa el chequeo > 0 y cuantiza
    a 0.0000: sin este rechazo el INSERT revienta sku_cost_positivo y aborta
    la transaccion ENTERA en vez del skip contado que promete el sellado 1."""
    _, skips, _ = colapsar([_fila("S", 1e-06, "2026-04-01 10:00:00", None)])
    assert skips == Counter({"costo cero o nulo (dato faltante)": 1})


def test_solape_en_el_origen_corta_el_sku_entero():
    """Hallazgo 2 (medio): si el origen solapa sus propias vigencias, no se
    publica NI la vieja abierta (divergiria del costo vigente de la fuente):
    el SKU completo queda sin escribir y cada fila afectada cuenta su skip."""
    filas = [
        _fila("O", 100.0, "2026-04-01 10:00:00", None),
        _fila("O", 120.0, "2026-04-03 09:00:00", "2026-04-10 09:00:00"),
        _fila("O", 130.0, "2026-04-10 09:00:00", None),
    ]
    vigencias, skips, _ = colapsar(filas)
    assert vigencias == {}
    assert skips == Counter({"vigencia solapada en el origen (sku completo sin escribir)": 3})


def test_intradia_en_el_borde_de_la_serie_pierde_el_dia_y_queda_contado():
    """Hallazgo 3 (medio): un tramo que abre y cierra el mismo dia en el BORDE
    de la serie (sin fila que continue el dia) no puede reclamar el dia bajo
    la granularidad DATE: es dato faltante, contado por separado del ruido
    intradario con sucesor (declarado en D2 del plan)."""
    filas = [
        _fila("B", 100.0, "2026-04-01 10:00:00", "2026-04-05 09:00:00"),
        _fila("B", 120.0, "2026-04-05 10:00:00", "2026-04-05 15:00:00"),
    ]
    vigencias, skips, stats = colapsar(filas)
    # el 2026-04-05 queda SIN costo: la fila que lo cubria vivio menos de un
    # dia y nada la continua (sellado 1: no se reclama lo que no se puede probar)
    assert vigencias == {"B": [("B", Decimal("100"), "MXN", date(2026, 4, 1), date(2026, 4, 5))]}
    assert skips == Counter()
    assert stats["segmentos_intradia_en_borde"] == 1
    assert stats["segmentos_intradia_colapsados"] == 0


def test_plan_sku_rechaza_moneda_o_includes_tax_distinto_de_lo_publicado():
    """Hallazgo 4 (bajo): lo publicado con MISMO importe pero otra moneda (o
    includes_tax distinto) NO es un no-op: es divergencia y el SKU completo
    queda sin escribir (regla 4: un numero sin su moneda es otro numero)."""
    serie = [Vigencia("M", Decimal("100"), "USD", date(2026, 4, 1), None)]
    _, _, motivo = _plan_sku(serie, {date(2026, 4, 1): (Decimal("100"), None, "MXN", False)})
    assert motivo == "moneda distinta para vigencia ya publicada"
    serie_mx = [Vigencia("M", Decimal("100"), "MXN", date(2026, 4, 1), None)]
    _, _, motivo = _plan_sku(serie_mx, {date(2026, 4, 1): (Decimal("100"), None, "MXN", True)})
    assert motivo == "includes_tax distinto en vigencia ya publicada"


def test_plan_sku_acepta_prefijo_identico_con_moneda_y_tax():
    """Cara complementaria del hallazgo 4: moneda e includes_tax identicos a
    lo publicado siguen siendo no-op (el caso comun, 100% MXN / false)."""
    serie = [Vigencia("M", Decimal("100"), "MXN", date(2026, 4, 1), None)]
    cierres, inserciones, motivo = _plan_sku(
        serie, {date(2026, 4, 1): (Decimal("100"), None, "MXN", False)}
    )
    assert motivo is None
    assert cierres == [] and inserciones == []


# ---------------------------------------------------------------------------
# (a) leer_origen: snapshot real de SQLite
# ---------------------------------------------------------------------------


def test_leer_origen_lee_filas_y_nombres(tmp_path):
    snap = _snapshot(
        tmp_path / "snap.db",
        _FILAS_Y4,
        nombres=[(1, "Y4-FB35-N645", "Arra Fija 35mm")],
    )
    origen = leer_origen(snap)
    assert len(origen.filas) == 4
    assert origen.filas[0].sku == "Y4-FB35-N645"
    assert origen.filas[0].costo == 301.5
    assert origen.filas[0].inicio == "2026-02-07 12:25:00"
    assert origen.nombres == {"Y4-FB35-N645": "Arra Fija 35mm"}


def test_leer_origen_ignora_nombres_vacios(tmp_path):
    snap = _snapshot(tmp_path / "snap.db", _FILAS_Y4, nombres=[(1, "Y4-FB35-N645", "  ")])
    assert leer_origen(snap).nombres == {}


# ---------------------------------------------------------------------------
# (a) main: fail-closed de config
# ---------------------------------------------------------------------------


def test_main_sin_dsn_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    snap = _snapshot(tmp_path / "snap.db", _FILAS_Y4, nombres=[])
    assert main(["--sqlite", str(snap)]) == 2
    assert "ORBIT_DSN_INGEST" in capsys.readouterr().err


def test_main_sin_snapshot_falla_cerrado(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ORBIT_DSN_INGEST", "postgresql://x/y")
    assert main(["--sqlite", str(tmp_path / "no-existe.db")]) == 2
    err = capsys.readouterr().err
    assert "no-existe.db" in err


def test_main_exige_flag_sqlite(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2  # argparse: flag requerido


# ---------------------------------------------------------------------------
# (b) integracion: sync_costos contra la migracion real
# ---------------------------------------------------------------------------

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


def _filas_v1() -> list[tuple]:
    """Escenario completo: ruido intradia identico + cambio real de costo,
    fila eterna del backfill (ambos NULL), y un costo cero intermedio."""
    return [
        # ARR-16-001: rotacion intradia identica (Y4-FB35 real) + cambio real
        (
            "ARR-16-001",
            301.5,
            "MXN",
            "2026-02-07 12:25:00",
            "2026-02-07 12:47:06",
            "manual",
            "2026-02-07 12:25:00",
        ),
        (
            "ARR-16-001",
            301.5,
            "MXN",
            "2026-02-07 12:47:06",
            "2026-02-07 12:47:07",
            "manual",
            "2026-02-07 12:25:00",
        ),
        (
            "ARR-16-001",
            301.5,
            "MXN",
            "2026-02-07 12:47:07",
            "2026-05-10 05:00:00",
            "odoo_sync",
            "2026-02-07 12:25:00",
        ),
        (
            "ARR-16-001",
            305.0,
            "MXN",
            "2026-05-10 05:00:00",
            None,
            "odoo_sync",
            "2026-02-07 12:25:00",
        ),
        # SKU-B-SIN-NOMBRE: fila eterna del backfill (valid_from y valid_to NULL)
        (
            "SKU-B-SIN-NOMBRE",
            84.0,
            "MXN",
            None,
            None,
            "odoo_sync",
            "2026-02-20 05:00:02",
        ),
        # SKU-C-CERO: costo cero intermedio (sellado 1: rechazado y contado)
        (
            "SKU-C-CERO",
            100.0,
            "MXN",
            "2026-04-01 10:00:00",
            "2026-04-03 09:00:00",
            "odoo_sync",
            "2026-04-01 10:00:00",
        ),
        (
            "SKU-C-CERO",
            0.0,
            "MXN",
            "2026-04-03 09:00:00",
            "2026-04-05 09:00:00",
            "odoo_sync",
            "2026-04-01 10:00:00",
        ),
        (
            "SKU-C-CERO",
            110.0,
            "MXN",
            "2026-04-05 09:00:00",
            None,
            "odoo_sync",
            "2026-04-01 10:00:00",
        ),
    ]


_NOMBRES_V1 = [
    (1, "ARR-16-001", "Arra 16mm"),
    (2, "SKU-C-CERO", "Producto C"),
]


def _estado(conn) -> list[tuple]:
    """Vuelca product+sku_cost completo para comparacion exacta de igualdad."""
    return sorted(
        conn.execute(
            "SELECT p.odoo_sku, p.name, p.active, c.cost_amount, c.cost_currency,"
            " c.includes_tax, c.valid_from, c.valid_to"
            " FROM sku_cost c JOIN product p ON p.id = c.product_id"
        ).fetchall()
    )


def _abiertas_por_sku(conn) -> dict[str, int]:
    return dict(
        conn.execute(
            "SELECT p.odoo_sku, count(*) FROM sku_cost c"
            " JOIN product p ON p.id = c.product_id"
            " WHERE c.valid_to IS NULL GROUP BY 1"
        ).fetchall()
    )


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sync_costos_ciclo_completo_en_vivo(tmp_path):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_costs_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # la migracion entera

        # ---------------- corrida 1: colapso + escritura ----------------
        snap1 = _snapshot(tmp_path / "v1.db", _filas_v1(), _NOMBRES_V1)
        r1 = sync_costos(conn, snap1)

        assert r1.ok is True
        assert r1.rows_written == 5  # 5 vigencias insertadas, 0 cierres
        assert r1.vigencias_insertadas == 5
        assert r1.vigencias_cerradas == 0
        assert r1.rows_skipped == 1
        assert "1x costo cero o nulo (dato faltante)" in r1.skip_reason
        assert r1.productos_nuevos == 3
        # contadores de colapso visibles (hallazgo de revision): los dos
        # intradia del fixture tienen sucesor -> ruido; ninguno en el borde
        assert r1.segmentos_intradia_colapsados == 2
        assert r1.segmentos_intradia_en_borde == 0
        assert r1.fusiones == 0
        assert r1.nombres_derivados == 1  # solo SKU-B-SIN-NOMBRE
        # colapso declarado: 8 filas origen -> 5 vigencias
        assert r1.filas_origen == 8
        assert r1.vigencias_finales == 5

        run = conn.execute(
            "SELECT source, ok, rows_written, rows_skipped, skip_reason, finished_at"
            " FROM ingest_run WHERE id = %s",
            (r1.run_id,),
        ).fetchone()
        assert run[0] == "accounting_sku_costs"
        assert run[1] is True and run[2] == 5 and run[3] == 1
        assert run[4] is not None and run[5] is not None

        # productos: nombre real, derivado con la marca sellada, activos
        productos = dict(conn.execute("SELECT odoo_sku, name FROM product").fetchall())
        assert productos == {
            "ARR-16-001": "Arra 16mm",
            "SKU-B-SIN-NOMBRE": f"{NOMBRE_SIN_ODOO} SKU-B-SIN-NOMBRE",
            "SKU-C-CERO": "Producto C",
        }
        assert conn.execute("SELECT count(*) FROM product WHERE NOT active").fetchone()[0] == 0

        # vigencias exactas (colapso intradia + fusion + created_at)
        estado1 = _estado(conn)
        assert estado1 == [
            (
                "ARR-16-001",
                "Arra 16mm",
                True,
                Decimal("301.5"),
                "MXN",
                False,
                date(2026, 2, 7),
                date(2026, 5, 10),
            ),
            (
                "ARR-16-001",
                "Arra 16mm",
                True,
                Decimal("305"),
                "MXN",
                False,
                date(2026, 5, 10),
                None,
            ),
            (
                "SKU-B-SIN-NOMBRE",
                f"{NOMBRE_SIN_ODOO} SKU-B-SIN-NOMBRE",
                True,
                Decimal("84"),
                "MXN",
                False,
                date(2026, 2, 20),
                None,
            ),
            (
                "SKU-C-CERO",
                "Producto C",
                True,
                Decimal("100"),
                "MXN",
                False,
                date(2026, 4, 1),
                date(2026, 4, 3),
            ),
            (
                "SKU-C-CERO",
                "Producto C",
                True,
                Decimal("110"),
                "MXN",
                False,
                date(2026, 4, 5),
                None,
            ),
        ]
        # una sola vigencia abierta por producto
        assert _abiertas_por_sku(conn) == {
            "ARR-16-001": 1,
            "SKU-B-SIN-NOMBRE": 1,
            "SKU-C-CERO": 1,
        }

        # ---------------- corrida 2: no-op REAL ----------------
        antes = _estado(conn)
        r2 = sync_costos(conn, snap1)
        assert r2.ok is True
        assert r2.rows_written == 0
        assert r2.rows_skipped == 1  # el cero sigue contado en cada corrida
        assert _estado(conn) == antes  # la base quedo IDENTICA
        # y el no-op no es "mismo ingest_run": hay DOS corridas selladas
        assert (
            conn.execute(
                "SELECT count(*) FROM ingest_run WHERE source = 'accounting_sku_costs'"
            ).fetchone()[0]
            == 2
        )

        # ---------------- corrida 3: vigencia nueva cierra la anterior ----------------
        filas_v3 = list(_filas_v1())
        # la vigencia abierta de ARR cierra y entra la nueva (rotacion de Odoo)
        filas_v3[3] = (
            "ARR-16-001",
            305.0,
            "MXN",
            "2026-05-10 05:00:00",
            "2026-06-01 05:00:00",
            "odoo_sync",
            "2026-02-07 12:25:00",
        )
        filas_v3.append(
            (
                "ARR-16-001",
                310.0,
                "MXN",
                "2026-06-01 05:00:00",
                None,
                "odoo_sync",
                "2026-02-07 12:25:00",
            )
        )
        snap3 = _snapshot(tmp_path / "v3.db", filas_v3, _NOMBRES_V1)
        r3 = sync_costos(conn, snap3)
        assert r3.ok is True
        assert r3.vigencias_cerradas == 1
        assert r3.vigencias_insertadas == 1
        assert r3.rows_written == 2
        assert _abiertas_por_sku(conn) == {
            "ARR-16-001": 1,
            "SKU-B-SIN-NOMBRE": 1,
            "SKU-C-CERO": 1,
        }
        fila_abierta = conn.execute(
            "SELECT c.valid_from, c.cost_amount FROM sku_cost c"
            " JOIN product p ON p.id = c.product_id"
            " WHERE p.odoo_sku = 'ARR-16-001' AND c.valid_to IS NULL"
        ).fetchone()
        assert fila_abierta == (date(2026, 6, 1), Decimal("310"))
        cerrada = conn.execute(
            "SELECT valid_to FROM sku_cost c JOIN product p ON p.id = c.product_id"
            " WHERE p.odoo_sku = 'ARR-16-001' AND c.valid_from = '2026-05-10'"
        ).fetchone()
        assert cerrada == (date(2026, 6, 1),)

        # -------- corrida 4: divergencia retroactiva NO escribe el SKU --------
        filas_v4 = list(filas_v3)
        # el origen "corrige" el importe de una vigencia YA publicada
        filas_v4[2] = (
            "ARR-16-001",
            400.0,
            "MXN",
            "2026-02-07 12:47:07",
            "2026-05-10 05:00:00",
            "odoo_sync",
            "2026-02-07 12:25:00",
        )
        snap4 = _snapshot(tmp_path / "v4.db", filas_v4, _NOMBRES_V1)
        antes4 = _estado(conn)
        r4 = sync_costos(conn, snap4)
        assert r4.ok is True  # la corrida no revienta: el SKU se rechaza completo
        assert r4.rows_written == 0
        assert "costo distinto para vigencia ya publicada" in r4.skip_reason
        assert _estado(conn) == antes4  # ni cierres ni insert del SKU divergente

        # -------- corrida 5: reabrir vigencia publicada tampoco se escribe --------
        # El origen REABRE: la rotacion nueva desaparece y la fila publicada
        # como cerrada vuelve con valid_to NULL. (Dejar AMBIAS filas abiertas
        # seria un solape del origen, que el colapso caza ANTES de _plan_sku.)
        filas_v5 = list(filas_v3)[:-1]
        filas_v5[3] = (
            "ARR-16-001",
            305.0,
            "MXN",
            "2026-05-10 05:00:00",
            None,
            "odoo_sync",
            "2026-02-07 12:25:00",
        )
        snap5 = _snapshot(tmp_path / "v5.db", filas_v5, _NOMBRES_V1)
        antes5 = _estado(conn)
        r5 = sync_costos(conn, snap5)
        assert r5.ok is True
        assert r5.rows_written == 0
        assert "origen reabre vigencia ya publicada" in r5.skip_reason
        assert _estado(conn) == antes5

        # -------- corrida 6: SKU ausente del origen queda CONTADO --------
        # (hallazgo 5 del adversario, bajo): un SKU que desaparece entero del
        # origen no puede quedar en silencio con su vigencia abierta huerfana.
        filas_v6 = [f for f in filas_v3 if f[0] != "SKU-C-CERO"]
        snap6 = _snapshot(tmp_path / "v6.db", filas_v6, _NOMBRES_V1)
        antes6 = _estado(conn)
        r6 = sync_costos(conn, snap6)
        assert r6.ok is True
        assert r6.rows_written == 0
        assert "1x sku ausente del origen (su vigencia abierta queda huerfana)" in r6.skip_reason
        # no se ESCRIBE nada del ausente (ni cerrar su vigencia: eso seria
        # inventar que dejo de aplicar), y la base queda identica
        assert _estado(conn) == antes6

        # -------- corrida 7: el nombre real NUNCA degrada a derivado --------
        # (hallazgo 2 de codex, media): si bom_headers pierde el nombre de un
        # SKU cuyo nombre real YA esta publicado (sync de Odoo incompleto), el
        # upsert no puede pisarlo con "[sin nombre en Odoo] SKU": solo se
        # actualiza cuando el nombre MEJORA (derivado -> real, o real -> real).
        snap7 = _snapshot(tmp_path / "v7.db", filas_v3, [(2, "SKU-C-CERO", "Producto C")])
        antes7 = _estado(conn)
        r7 = sync_costos(conn, snap7)
        assert r7.ok is True
        # ARR-16-001 sin nombre en el origen: conserva el nombre publicado
        nombre_arr = conn.execute(
            "SELECT name FROM product WHERE odoo_sku = 'ARR-16-001'"
        ).fetchone()[0]
        assert nombre_arr == "Arra 16mm"
        assert _estado(conn) == antes7  # nada cambio: ni nombre ni vigencias
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()
