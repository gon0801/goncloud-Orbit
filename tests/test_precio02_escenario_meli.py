"""Escenarios `meli/meli` (REPRICING 02 M.3).

Una publicacion con todo produce escenario `disponible` reproducible a
mano; cada componente ausente da su motivo; con varios miembros la
cuenta de la fila es la del miembro que manda (P* mas alto = margen mas
bajo con goal unico; empate -> seller_sku ascendente).
"""

from __future__ import annotations

import itertools
import os
import socket
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente

from app.estimacion_fees import ResultadoCotizacion
from app.estimacion_repository import persistir_escenario
from app.precio.tipos import Importe

RAIZ = Path(__file__).resolve().parents[1]

ORDEN_M3 = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
    "0055_precio02_escenario_exige.sql",
    "0073_precio02_meli_miembro_costo.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
FETCH = NOW - timedelta(hours=1)
FEE_TIME = NOW - timedelta(minutes=30)
VALORACION = date(2026, 9, 8)

_CONTADOR = itertools.count()


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db():
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"precio02m3_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_M3:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


POLITICA_MELI = {
    "universo": "meli/meli",
    "formula_version": "S3",
    "iva_divisor": "1.16",
    "isr_tasa": "0.025",
    "logistica": "0",
    "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",
    "retencion_iva_reconciliacion": "0.08",
    "precio_incluye_iva": True,
    "fee_tax_amount_requiere_politica": True,
}


def _producto(conn, sku: str) -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (sku, sku),
    ).fetchone()[0]


def _publicacion(
    conn,
    item: str,
    miembros: list[tuple[str, int | None]],
    *,
    precio: str | None = "979.11",
    moneda: str | None = None,
    estado: str = "active",
    observado: datetime = FETCH,
) -> int:
    ancla = min(sku for sku, _ in miembros)
    pid_ancla = next(pid for sku, pid in miembros if sku == ancla)
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'meli', %s, %s) RETURNING id",
        (pid_ancla, item, ancla),
    ).fetchone()[0]
    for sku, pid in miembros:
        conn.execute(
            "INSERT INTO listing_miembro"
            " (listing_id, platform, variante_ext, seller_sku, product_id, activo, visto_at)"
            " VALUES (%s, 'meli', %s, %s, %s, true, %s)",
            (lid, f"v-{sku}", sku, pid, FETCH),
        )
    _observacion(
        conn,
        item,
        observado=observado,
        precio=precio,
        moneda=moneda if moneda is not None else ("MXN" if precio else None),
        estado=estado,
    )
    return lid


def _observacion(
    conn,
    item: str,
    *,
    observado: datetime,
    precio: str | None = "979.11",
    moneda: str | None = "MXN",
    estado: str = "active",
    visitas: int | None = None,
    stock: int | None = 3,
) -> None:
    """Agrega una observacion (append-only: nunca UPDATE)."""
    conn.execute(
        "INSERT INTO meli_publicacion_observation"
        " (item_id, metric_date, observed_at, estado, precio, moneda, stock,"
        " tipo_publicacion, categoria_id, logistica, visitas, variantes)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 'gold_pro', 'MLM1440', NULL, %s, '[]')",
        (item, observado.date(), observado, estado, precio, moneda, stock, visitas),
    )


def _costo(conn, pid: int, *, monto: str = "400.0000", moneda: str = "MXN") -> int:
    run = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written,"
        " rows_skipped, ok) VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE)"
        " RETURNING id",
        (FETCH, FETCH),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id) VALUES (%s, %s, %s, FALSE, '2026-01-01', %s)"
        " RETURNING id",
        (pid, monto, moneda, run),
    ).fetchone()[0]


def _fx(conn, *, dia, tasa: str = "17.00000000") -> None:
    run = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written,"
        " rows_skipped, ok) VALUES ('accounting_currency_rates', %s, %s, 1, 0, TRUE)"
        " RETURNING id",
        (datetime(dia.year, dia.month, dia.day, 8, 15, tzinfo=UTC),) * 2,
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, ingest_run_id)"
        " VALUES (%s, 'USD', 'MXN', %s, %s)",
        (dia, tasa, run),
    )


def _politica(conn) -> int:
    return conn.execute(
        "INSERT INTO estimacion_politica_version"
        " (created_at, label, universo, formula_version, settings, valid_from)"
        " VALUES (%s, 'm3-test', 'meli/meli', 'S3', %s, '2026-01-01') RETURNING id",
        (FETCH, Json(POLITICA_MELI)),
    ).fetchone()[0]


def _costo_id(conn, pid: int) -> int:
    return conn.execute("SELECT id FROM sku_cost WHERE product_id = %s", (pid,)).fetchone()[0]


def _muestra(conn, pid: int, *, valor: str = "30.0000", moneda: str = "MXN") -> int:
    return conn.execute(
        "INSERT INTO precio_envio_muestra (product_id, platform, canal, ventana_desde,"
        " ventana_hasta, envios, percentil, valor, valor_currency, mediana,"
        " mediana_currency, p90, p90_currency, maximo, maximo_currency, origen,"
        " familia_id, donantes, regla_version)"
        " VALUES (%s, 'meli', 'meli', '2026-03-12', '2026-09-07', 4, 'mediana', %s,"
        " %s, %s, %s, %s, %s, %s, %s, 'propio', NULL, NULL, 'f2.1')"
        " RETURNING id",
        (pid, valor, moneda, valor, moneda, valor, moneda, valor, moneda),
    ).fetchone()[0]


def _siembra(
    conn,
    lid: int,
    *,
    fee_total: str = "190.93",
    observed_at=NOW,
    ahora=NOW,
    valoracion=VALORACION,
):
    """Cadena oferta -> fee -> escenario con funciones publicas."""
    from app.estimacion_fees import persistir_fee_observation
    from app.estimacion_insumos import persistir_oferta_observation
    from app.estimacion_repository import sembrar_escenario_desde_refs
    from app.meli.estimacion import (
        construir_fee_canonical_input_meli,
        construir_fee_outcome_canonical_meli,
        construir_fee_source_event_id_meli,
        resolver_oferta_meli,
    )

    fee_time = ahora - timedelta(minutes=30)
    res = resolver_oferta_meli(conn, lid, ahora=ahora)
    assert res.motivo is None, res.motivo
    oferta = res.oferta
    assert oferta is not None
    oid = persistir_oferta_observation(conn, oferta, observed_at=fee_time).id
    canon_in = construir_fee_canonical_input_meli(oferta)
    cot = ResultadoCotizacion(
        estado="success",
        total_fees=Decimal(fee_total),
        fee_details=[
            {"fee_type": "SaleFee", "final_fee": fee_total, "currency": "MXN"},
            {"fee_type": "SaleFeeFixed", "final_fee": "0", "currency": "MXN"},
            {"fee_type": "ListingFee", "final_fee": "0", "currency": "MXN"},
        ],
        fees_estimated_at=fee_time,
        error_code=None,
        fetched_at=fee_time,
    )
    canon_out = construir_fee_outcome_canonical_meli(
        oferta, cot, attempted_at=fee_time, canonica_entrada=canon_in
    )
    fid = persistir_fee_observation(
        conn,
        oferta_observation_id=oid,
        oferta=oferta,
        resultado=cot,
        observed_at=fee_time,
        source_event_id=construir_fee_source_event_id_meli(canon_out),
        canonical_input=canon_out,
    ).id
    esc = sembrar_escenario_desde_refs(
        conn,
        listing_id=lid,
        oferta_observation_id=oid,
        fee_observation_id=fid,
        valoracion_date=valoracion,
        observed_at=observed_at,
    )
    return persistir_escenario(conn, esc).id


@_skip_sin_pg
def test_disponible_reproducible_a_mano():
    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        costo_id = _costo(conn, pid)
        _muestra(conn, pid)
        _politica(conn)
        persistido = _siembra(conn, lid)
        fila = conn.execute(
            "SELECT estado, motivos, contribucion, contribucion_pct, moneda,"
            " fee_variable, fee_fijo, fee_cotizable, logistica_origen,"
            " miembro_product_id, seller_sku, componentes, sku_cost_id"
            " FROM estimacion_escenario WHERE id = %s",
            (persistido,),
        ).fetchone()
        # A mano: P=979.11, I=979.11/1.16=844.0603448, C=400, F=190.93,
        # L=30, R=844.0603448x0.025=21.1015086.
        # contrib=844.0603448-400-190.93-30-21.1015086=202.0288 (4dp).
        # pct=202.0288362x100/844.0603448=23.9354.
        assert fila[0] == "disponible"
        assert list(fila[1]) == []
        assert fila[2] == Decimal("202.0288")
        assert fila[3] == Decimal("23.9354")
        assert fila[4] == "MXN"
        assert fila[5] == Decimal("190.93")
        assert fila[6] == Decimal("0")
        assert fila[7] is True
        assert fila[8] == "propio"
        assert fila[9] == pid
        assert fila[10] == "SKU-M3-A"
        nombres = [c["nombre"] for c in fila[11]]
        assert "miembro:SKU-M3-A" not in nombres
        assert fila[12] == costo_id


@_skip_sin_pg
def test_oferta_sin_observacion_da_oferta_ausente():
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-Z")
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'meli', 'MLM-Z', 'SKU-M3-Z') RETURNING id",
            (pid,),
        ).fetchone()[0]
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.motivo == "oferta_ausente"
        assert res.oferta is None


@_skip_sin_pg
def test_oferta_trae_identidad_meli():
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.motivo is None
        assert res.oferta is not None
        assert res.oferta.source_event_id.startswith("meli-oferta:")
        assert res.oferta.canonical_input["categoria_id"] == "MLM1440"
        assert res.oferta.canonical_input["listing_type_id"] == "gold_pro"
        assert res.oferta.seller_sku == "SKU-M3-A"


@_skip_sin_pg
def test_oferta_sin_precio_da_oferta_ausente():
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)], precio=None)
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.motivo == "oferta_ausente"
        assert res.oferta is None


@_skip_sin_pg
def test_fila_desactualizada_por_oferta_vieja_al_sembrar():
    from app.estimacion_insumos import persistir_oferta_observation
    from app.estimacion_repository import sembrar_escenario_desde_refs
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _muestra(conn, pid)
        _politica(conn)
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        oid = persistir_oferta_observation(conn, res.oferta, observed_at=FEE_TIME).id
        tarde = NOW + timedelta(hours=7)
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=None,
            valoracion_date=VALORACION,
            observed_at=tarde,
        )
        pid_row = persistir_escenario(conn, esc).id
        fila = conn.execute(
            "SELECT estado, motivos FROM estimacion_escenario WHERE id = %s", (pid_row,)
        ).fetchone()
        assert fila[0] == "desactualizada"
        assert "oferta_desactualizada" in fila[1]


@_skip_sin_pg
def test_cambio_en_miembro_no_mandante_resiembra():
    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        pc = _producto(conn, "SKU-M3-C")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-C", pc)])
        _costo(conn, pa, monto="400.0000")
        _costo(conn, pc, monto="500.0000")
        _muestra(conn, pa, valor="30.0000")
        _muestra(conn, pc, valor="20.0000")
        _politica(conn)
        _siembra(conn, lid)
        # Nuevo costo de A (no manda): la canonica cambia y se escribe otra fila.
        conn.execute("UPDATE sku_cost SET valid_to = '2026-09-01' WHERE product_id = %s", (pa,))
        run = conn.execute(
            "INSERT INTO ingest_run (source, started_at, finished_at, rows_written,"
            " rows_skipped, ok) VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE)"
            " RETURNING id",
            (FETCH, FETCH),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id) VALUES (%s, '410.0000', 'MXN', FALSE,"
            " '2026-09-01', %s)",
            (pa, run),
        )
        _siembra(conn, lid, observed_at=NOW + timedelta(hours=1))
        n = conn.execute("SELECT count(*) FROM estimacion_escenario").fetchone()[0]
        assert n == 2


@_skip_sin_pg
def test_oferta_futura_via_resolver():
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)], observado=NOW + timedelta(hours=1))
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.motivo == "oferta_futura"
        assert res.oferta is None


@_skip_sin_pg
def test_empate_cercano_manda_carga_exacta():
    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        pb = _producto(conn, "SKU-M3-B")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-B", pb)])
        _costo(conn, pa, monto="400.0000")
        _costo(conn, pb, monto="400.0001")
        _muestra(conn, pa, valor="30.0000")
        _muestra(conn, pb, valor="30.0000")
        _politica(conn)
        # Δ(C+L) = 0.0001: empataria a pct 4dp, pero manda B por carga exacta.
        persistido = _siembra(conn, lid)
        fila = conn.execute(
            "SELECT seller_sku, miembro_product_id FROM estimacion_escenario WHERE id = %s",
            (persistido,),
        ).fetchone()
        assert tuple(fila) == ("SKU-M3-A", pb)


@_skip_sin_pg
def test_miembro_con_producto_sin_muestra_falla_con_sku():
    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        pb = _producto(conn, "SKU-M3-B")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-B", pb)])
        _costo(conn, pa)
        _costo(conn, pb)
        _muestra(conn, pa)
        _politica(conn)
        persistido = _siembra(conn, lid)
        fila = conn.execute(
            "SELECT estado, motivos, componentes FROM estimacion_escenario WHERE id = %s",
            (persistido,),
        ).fetchone()
        assert fila[0] == "incompleta"
        assert "envio_sin_historia" in fila[1]
        otros = [c for c in fila[2] if c["nombre"].startswith("miembro:")]
        assert [(c["nombre"], c.get("fuente")) for c in otros] == [
            ("miembro:SKU-M3-B", "envio_sin_historia")
        ]


@_skip_sin_pg
def test_miembro_usd_hacia_venta_mxn_da_fx_ausente():
    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        pb = _producto(conn, "SKU-M3-B")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-B", pb)])
        _costo(conn, pa)
        _muestra(conn, pa)
        _costo(conn, pb, monto="25.0000", moneda="USD")
        _muestra(conn, pb, valor="2.0000", moneda="USD")
        _fx(conn, dia=VALORACION, tasa="17.00000000")
        _politica(conn)
        persistido = _siembra(conn, lid)
        fila = conn.execute(
            "SELECT estado, motivos FROM estimacion_escenario WHERE id = %s",
            (persistido,),
        ).fetchone()
        # El job fx solo publica (USD, MXN): costo USD hacia venta MXN
        # no tiene fila inversa y es `fx_ausente` honesto (igual que en
        # Amazon MX; la direccion inversa es tema del job, no de M.3).
        assert fila[0] == "incompleta"
        assert "fx_ausente" in fila[1]


@_skip_sin_pg
def test_oferta_usd_da_universo_no_soportado():
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)], moneda="USD")
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.motivo == "universo_no_soportado"
        assert res.oferta is None


@_skip_sin_pg
def test_miembro_sin_costo_ni_muestra_aporta_ambos_motivos():
    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        pc = _producto(conn, "SKU-M3-C")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-C", pc)])
        _costo(conn, pa)
        _muestra(conn, pa)
        _politica(conn)
        persistido = _siembra(conn, lid)
        fila = conn.execute(
            "SELECT estado, motivos, componentes FROM estimacion_escenario WHERE id = %s",
            (persistido,),
        ).fetchone()
        assert fila[0] == "incompleta"
        assert "costo_ausente" in fila[1]
        assert "envio_sin_historia" in fila[1]
        otros = [c for c in fila[2] if c["nombre"].startswith("miembro:")]
        assert [c["nombre"] for c in otros] == ["miembro:SKU-M3-C"]


@_skip_sin_pg
def test_fee_error_colapsa_a_vocabulario_cerrado():
    from app.estimacion_fees import persistir_fee_observation
    from app.estimacion_insumos import persistir_oferta_observation
    from app.estimacion_repository import sembrar_escenario_desde_refs
    from app.estimacion_venta import MOTIVOS_ESTIMACION
    from app.meli.estimacion import (
        construir_fee_canonical_input_meli,
        construir_fee_outcome_canonical_meli,
        construir_fee_source_event_id_meli,
        resolver_oferta_meli,
    )

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _muestra(conn, pid)
        _politica(conn)
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        oid = persistir_oferta_observation(conn, res.oferta, observed_at=FEE_TIME).id
        for i, codigo in enumerate(("fee_contrato_incompatible", "fee_error")):
            intento = FEE_TIME + timedelta(minutes=i)
            cot = ResultadoCotizacion(
                estado="error",
                total_fees=None,
                fee_details=[],
                fees_estimated_at=None,
                error_code=codigo,
                fetched_at=intento,
            )
            canon_in = construir_fee_canonical_input_meli(res.oferta)
            canon_out = construir_fee_outcome_canonical_meli(
                res.oferta, cot, attempted_at=intento, canonica_entrada=canon_in
            )
            fid = persistir_fee_observation(
                conn,
                oferta_observation_id=oid,
                oferta=res.oferta,
                resultado=cot,
                observed_at=intento,
                source_event_id=construir_fee_source_event_id_meli(canon_out),
                canonical_input=canon_out,
            ).id
            esc = sembrar_escenario_desde_refs(
                conn,
                listing_id=lid,
                oferta_observation_id=oid,
                fee_observation_id=fid,
                valoracion_date=VALORACION,
                observed_at=NOW,
            )
            assert "fee_incompatible" in esc.motivos
            assert all(m in MOTIVOS_ESTIMACION for m in esc.motivos)


@_skip_sin_pg
def test_oferta_cerrada_o_vieja_no_resuelve():
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)], estado="closed")
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.motivo == "oferta_ausente"
        lid2 = _publicacion(conn, "MLM2", [("SKU-M3-A", pid)], observado=NOW - timedelta(hours=31))
        res2 = resolver_oferta_meli(conn, lid2, ahora=NOW)
        assert res2.motivo == "oferta_desactualizada"


@_skip_sin_pg
def test_tres_miembros_la_fila_es_del_que_manda():
    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        pb = _producto(conn, "SKU-M3-B")
        pc = _producto(conn, "SKU-M3-C")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-B", pb), ("SKU-M3-C", pc)])
        _costo(conn, pa, monto="400.0000")
        _costo(conn, pb, monto="350.0000")
        _costo(conn, pc, monto="500.0000")
        _muestra(conn, pa, valor="30.0000")
        _muestra(conn, pb, valor="60.0000")
        mc = _muestra(conn, pc, valor="20.0000")
        _politica(conn)
        # C+L: A=430, B=410, C=520 -> manda C (margen mas bajo). La fila
        # lleva el ancla en seller_sku (trigger 0028) y a C en miembro.
        persistido = _siembra(conn, lid)
        fila = conn.execute(
            "SELECT estado, seller_sku, miembro_product_id, contribucion,"
            " contribucion_pct, envio_muestra_id, logistica_origen, componentes"
            " FROM estimacion_escenario WHERE id = %s",
            (persistido,),
        ).fetchone()
        assert fila[:3] == ("disponible", "SKU-M3-A", pc)
        # Manda C != ancla A: refs de costo de C (0073 lo permite).
        cc = _costo_id(conn, pc)
        sku_cost = conn.execute(
            "SELECT sku_cost_id FROM estimacion_escenario WHERE id = %s", (persistido,)
        ).fetchone()[0]
        assert sku_cost == cc
        assert fila[3] == Decimal("112.0288")
        assert fila[4] == Decimal("13.2726")
        assert fila[5:7] == (mc, "propio")
        otros = {c["nombre"]: c for c in fila[7] if c["nombre"].startswith("miembro:")}
        assert set(otros) == {"miembro:SKU-M3-A", "miembro:SKU-M3-B"}
        assert otros["miembro:SKU-M3-A"]["importe_normalizado"] == "202.0288"
        assert otros["miembro:SKU-M3-B"]["importe_normalizado"] == "222.0288"
        assert all(c["pertenece_a_total"] is False for c in otros.values())


@_skip_sin_pg
def test_empate_de_margen_gana_sku_ascendente():
    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        pb = _producto(conn, "SKU-M3-B")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-B", pb)])
        _costo(conn, pa, monto="400.0000")
        _costo(conn, pb, monto="390.0000")
        _muestra(conn, pa, valor="30.0000")
        _muestra(conn, pb, valor="40.0000")
        _politica(conn)
        # C+L iguales (430): manda A por sku ascendente.
        persistido = _siembra(conn, lid)
        fila = conn.execute(
            "SELECT seller_sku, miembro_product_id FROM estimacion_escenario WHERE id = %s",
            (persistido,),
        ).fetchone()
        assert tuple(fila) == ("SKU-M3-A", pa)


@_skip_sin_pg
def test_miembro_sin_producto_frena_la_fila_con_sku_a_la_vista():
    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-B", None)])
        _costo(conn, pa)
        _muestra(conn, pa)
        _politica(conn)
        persistido = _siembra(conn, lid)
        fila = conn.execute(
            "SELECT estado, motivos, componentes FROM estimacion_escenario WHERE id = %s",
            (persistido,),
        ).fetchone()
        assert fila[0] == "incompleta"
        assert "costo_ausente" in fila[1]
        otros = [c for c in fila[2] if c["nombre"].startswith("miembro:")]
        assert [(c["nombre"], c.get("fuente")) for c in otros] == [
            ("miembro:SKU-M3-B", "costo_ausente")
        ]


def _motivos_de(conn, lid: int) -> list:
    persistido = _siembra(conn, lid)
    fila = conn.execute(
        "SELECT estado, motivos FROM estimacion_escenario WHERE id = %s", (persistido,)
    ).fetchone()
    assert fila[0] == "incompleta"
    return list(fila[1])


@_skip_sin_pg
def test_sin_politica_da_politica_ausente():
    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _muestra(conn, pid)
        assert _motivos_de(conn, lid) == ["politica_ausente"]


@_skip_sin_pg
def test_sin_fee_da_fee_ausente():
    from app.estimacion_insumos import persistir_oferta_observation
    from app.estimacion_repository import sembrar_escenario_desde_refs
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _muestra(conn, pid)
        _politica(conn)
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        oid = persistir_oferta_observation(conn, res.oferta, observed_at=FEE_TIME).id
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=None,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        pid_row = persistir_escenario(conn, esc).id
        fila = conn.execute(
            "SELECT estado, motivos FROM estimacion_escenario WHERE id = %s", (pid_row,)
        ).fetchone()
        assert fila[0] == "incompleta"
        assert "fee_ausente" in fila[1]


@_skip_sin_pg
def test_sin_costo_da_costo_ausente():
    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _muestra(conn, pid)
        _politica(conn)
        assert "costo_ausente" in _motivos_de(conn, lid)


@_skip_sin_pg
def test_sin_muestra_da_envio_sin_historia():
    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _politica(conn)
        assert "envio_sin_historia" in _motivos_de(conn, lid)


@_skip_sin_pg
def test_fee_que_no_concilia_da_fee_incompatible():
    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _muestra(conn, pid)
        _politica(conn)
        assert "fee_incompatible" in _motivos_de_detalles(
            conn, lid, [("SaleFee", "100.00")], total="190.93"
        )


def _motivos_de_detalles(conn, lid, detalles, *, total):
    from app.estimacion_fees import persistir_fee_observation
    from app.estimacion_insumos import persistir_oferta_observation
    from app.estimacion_repository import sembrar_escenario_desde_refs
    from app.meli.estimacion import (
        construir_fee_canonical_input_meli,
        construir_fee_outcome_canonical_meli,
        construir_fee_source_event_id_meli,
        resolver_oferta_meli,
    )

    res = resolver_oferta_meli(conn, lid, ahora=NOW)
    assert res.motivo is None
    assert res.oferta is not None
    oid = persistir_oferta_observation(conn, res.oferta, observed_at=FEE_TIME).id
    canon_in = construir_fee_canonical_input_meli(res.oferta)
    cot = ResultadoCotizacion(
        estado="success",
        total_fees=Decimal(total),
        fee_details=[{"fee_type": t, "final_fee": m, "currency": "MXN"} for t, m in detalles],
        fees_estimated_at=FEE_TIME,
        error_code=None,
        fetched_at=FEE_TIME,
    )
    canon_out = construir_fee_outcome_canonical_meli(
        res.oferta, cot, attempted_at=FEE_TIME, canonica_entrada=canon_in
    )
    fid = persistir_fee_observation(
        conn,
        oferta_observation_id=oid,
        oferta=res.oferta,
        resultado=cot,
        observed_at=FEE_TIME,
        source_event_id=construir_fee_source_event_id_meli(canon_out),
        canonical_input=canon_out,
    ).id
    esc = sembrar_escenario_desde_refs(
        conn,
        listing_id=lid,
        oferta_observation_id=oid,
        fee_observation_id=fid,
        valoracion_date=VALORACION,
        observed_at=NOW,
    )
    pid_row = persistir_escenario(conn, esc).id
    fila = conn.execute(
        "SELECT estado, motivos FROM estimacion_escenario WHERE id = %s", (pid_row,)
    ).fetchone()
    assert fila[0] == "incompleta"
    return list(fila[1])


@_skip_sin_pg
def test_leer_cuenta_devuelve_la_del_que_manda():
    from app.estimacion_reader import leer_cuenta

    with _db() as conn:
        pa = _producto(conn, "SKU-M3-A")
        pc = _producto(conn, "SKU-M3-C")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pa), ("SKU-M3-C", pc)])
        _costo(conn, pa, monto="400.0000")
        _costo(conn, pc, monto="500.0000")
        _muestra(conn, pa, valor="30.0000")
        _muestra(conn, pc, valor="20.0000")
        _politica(conn)
        _siembra(conn, lid)
        leida = leer_cuenta(conn, lid, as_of=NOW + timedelta(minutes=1))
        assert not isinstance(leida, str)
        assert leida is not None
        assert leida.componentes.costo == Importe(Decimal("500.0000"), "MXN")
        assert leida.componentes.envio == Importe(Decimal("20.0000"), "MXN")
        assert leida.componentes.p_actual.moneda == "MXN"
        assert leida.refs.product_id == pc


@_skip_sin_pg
def test_ingesta_meli_punta_a_punta(tmp_path):
    import sqlite3

    import httpx

    from app.estimacion_ingest import ejecutar_ingesta
    from app.reputacion_clientes import ClienteMeli, MeliCredentials

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _muestra(conn, pid)
        _politica(conn)
        snap = tmp_path / "bridge.db"
        con = sqlite3.connect(snap)
        con.execute(
            "CREATE TABLE amazon_listing_prices (seller_sku TEXT, asin TEXT,"
            " marketplace_id TEXT, marketplace_name TEXT, price REAL,"
            " fulfillment_channel TEXT, fetched_at TEXT)"
        )
        con.commit()
        con.close()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "currency_id": "MXN",
                    "sale_fee_amount": 190.93,
                    "sale_fee_details": {"percentage_fee": 19.5, "fixed_fee": 0},
                    "listing_fee_amount": 0,
                },
            )

        creds = MeliCredentials(
            access_token="t",
            refresh_token="r",
            client_id="c",
            client_secret="s",
            ruta=tmp_path / "tok.json",
        )
        cliente = ClienteMeli(creds, transport=httpx.MockTransport(handler), backoff_base=0.0)
        res = ejecutar_ingesta(
            conn,
            ruta_sqlite=snap,
            now_utc=NOW,
            fee_now_utc=lambda: NOW,
            meli_client=cliente,
        )
        assert (res.ofertas_nuevas, res.fees_nuevos, res.escenarios_nuevos) == (1, 1, 1)
        assert res.fees_error == 0
        fila = conn.execute("SELECT estado, contribucion FROM estimacion_escenario").fetchone()
        assert fila[0] == "disponible"
        assert fila[1] == Decimal("202.0288")
        cliente.close()


@_skip_sin_pg
def test_ingesta_meli_sin_cliente_falla_cerrado(tmp_path):
    import sqlite3

    from app.estimacion_ingest import ejecutar_ingesta

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        snap = tmp_path / "bridge.db"
        con = sqlite3.connect(snap)
        con.execute(
            "CREATE TABLE amazon_listing_prices (seller_sku TEXT, asin TEXT,"
            " marketplace_id TEXT, marketplace_name TEXT, price REAL,"
            " fulfillment_channel TEXT, fetched_at TEXT)"
        )
        con.commit()
        con.close()
        with pytest.raises(ValueError, match="meli_client"):
            ejecutar_ingesta(conn, ruta_sqlite=snap, now_utc=NOW)
        assert conn.execute("SELECT count(*) FROM ingest_run").fetchone()[0] == 0


@_skip_sin_pg
def test_ctor_amazon_falla_sella_run_sin_huerfano(monkeypatch, tmp_path):
    import sqlite3

    from app.estimacion_ingest import ejecutar_ingesta

    def _explota(*args, **kwargs):
        raise RuntimeError("sin credenciales amazon")

    monkeypatch.setattr("app.estimacion_ingest.ProductFeesClient", _explota)
    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0X', 'SKU-M3-A')",
            (pid,),
        )
        snap = tmp_path / "bridge.db"
        con = sqlite3.connect(snap)
        con.execute(
            "CREATE TABLE amazon_listing_prices (seller_sku TEXT, asin TEXT,"
            " marketplace_id TEXT, marketplace_name TEXT, price REAL,"
            " fulfillment_channel TEXT, fetched_at TEXT)"
        )
        con.commit()
        con.close()
        with pytest.raises(RuntimeError, match="sin credenciales"):
            ejecutar_ingesta(conn, ruta_sqlite=snap, now_utc=NOW)
        fila = conn.execute(
            "SELECT ok, finished_at IS NOT NULL, rows_written, rows_skipped"
            " FROM ingest_run WHERE source = 'bridge_estimacion_fees'"
        ).fetchone()
        assert fila == (False, True, 0, 0)


@_skip_sin_pg
def test_ingesta_meli_no_toca_secretos_amazon(monkeypatch, tmp_path):
    import sqlite3

    import httpx

    from app.estimacion_ingest import ejecutar_ingesta
    from app.reputacion_clientes import ClienteMeli, MeliCredentials

    def _explota(*args, **kwargs):
        raise AssertionError("no debe construir ProductFeesClient")

    monkeypatch.setattr("app.estimacion_ingest.ProductFeesClient", _explota)
    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _muestra(conn, pid)
        _politica(conn)
        snap = tmp_path / "bridge.db"
        con = sqlite3.connect(snap)
        con.execute(
            "CREATE TABLE amazon_listing_prices (seller_sku TEXT, asin TEXT,"
            " marketplace_id TEXT, marketplace_name TEXT, price REAL,"
            " fulfillment_channel TEXT, fetched_at TEXT)"
        )
        con.commit()
        con.close()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "currency_id": "MXN",
                    "sale_fee_amount": 190.93,
                    "sale_fee_details": {"percentage_fee": 19.5, "fixed_fee": 0},
                    "listing_fee_amount": 0,
                },
            )

        creds = MeliCredentials(
            access_token="t",
            refresh_token="r",
            client_id="c",
            client_secret="s",
            ruta=tmp_path / "tok.json",
        )
        cliente = ClienteMeli(creds, transport=httpx.MockTransport(handler), backoff_base=0.0)
        res = ejecutar_ingesta(
            conn,
            ruta_sqlite=snap,
            now_utc=NOW,
            fee_now_utc=lambda: NOW,
            meli_client=cliente,
        )
        assert res.escenarios_nuevos == 1
        cliente.close()


@_skip_sin_pg
def test_negativa_meli_reutiliza_en_segunda_corrida(tmp_path):
    import sqlite3

    from app.estimacion_ingest import ejecutar_ingesta

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        _costo(conn, pid)
        _muestra(conn, pid)
        _politica(conn)
        _siembra(conn, lid)
        snap = tmp_path / "bridge.db"
        con = sqlite3.connect(snap)
        con.execute(
            "CREATE TABLE amazon_listing_prices (seller_sku TEXT, asin TEXT,"
            " marketplace_id TEXT, marketplace_name TEXT, price REAL,"
            " fulfillment_channel TEXT, fetched_at TEXT)"
        )
        con.commit()
        con.close()
        _observacion(conn, "MLM1", observado=NOW + timedelta(hours=1))
        r1 = ejecutar_ingesta(
            conn,
            ruta_sqlite=snap,
            now_utc=NOW,
            fee_now_utc=lambda: NOW,
            meli_client=object(),
        )
        r2 = ejecutar_ingesta(
            conn,
            ruta_sqlite=snap,
            now_utc=NOW,
            fee_now_utc=lambda: NOW,
            meli_client=object(),
        )
        assert (r1.escenarios_nuevos, r1.escenarios_reutilizados) == (1, 0)
        assert (r2.escenarios_nuevos, r2.escenarios_reutilizados) == (0, 1)
        fila = conn.execute(
            "SELECT estado, motivos FROM estimacion_escenario"
            " WHERE motivos @> '[\"oferta_futura\"]'"
        ).fetchone()
        assert fila[0] == "desactualizada"
        assert fila[1] == ["oferta_futura"]


@_skip_sin_pg
def test_fee_malformado_se_cuenta_no_tumba():
    from app.estimacion_ingest import _persistir_fee_item
    from app.meli.estimacion import resolver_oferta_meli

    with _db() as conn:
        pid = _producto(conn, "SKU-M3-A")
        lid = _publicacion(conn, "MLM1", [("SKU-M3-A", pid)])
        res = resolver_oferta_meli(conn, lid, ahora=NOW)
        assert res.oferta is not None
        # Success incompleto: la canonica levanta; el item lo cuenta.
        cot = ResultadoCotizacion(
            estado="success",
            total_fees=None,
            fee_details=[],
            fees_estimated_at=None,
            error_code=None,
            fetched_at=FEE_TIME,
        )
        assert (
            _persistir_fee_item(
                conn,
                oferta=res.oferta,
                cotizacion=cot,
                oferta_id=1,
                fee_observed_at=FEE_TIME,
                run_id=1,
            )
            is None
        )


@_skip_sin_pg
def test_politica_meli_sql_inserta_idempotente():
    with _db() as conn:
        conn.execute(
            "INSERT INTO estimacion_politica_version"
            " (created_at, label, universo, formula_version, settings, valid_from)"
            " VALUES (%s, 'fba-test', 'amazon_mx/fba', 'S3', %s, '2026-01-01')",
            (FETCH, Json(POLITICA_MELI)),
        )
        sql = (RAIZ / "docs/evidencia/repricing-02/ejecucion/M.3/politica_meli.sql").read_text(
            encoding="utf-8"
        )
        conn.execute(sql)
        conn.execute(sql)
        filas = conn.execute(
            "SELECT label, universo, formula_version, settings, valid_to"
            " FROM estimacion_politica_version WHERE universo = 'meli/meli'"
        ).fetchall()
        assert len(filas) == 1
        label, universo, formula, settings, valid_to = filas[0]
        assert (label, universo, formula, valid_to) == (
            "repricing02_meli",
            "meli/meli",
            "S3",
            None,
        )
        assert settings["isr_tasa"] == "0.025"
        assert settings["iva_divisor"] == "1.16"
        assert settings["precio_incluye_iva"] is True
        assert settings["logistica"] == "0"
        assert settings["fee_tax_amount_requiere_politica"] is True
