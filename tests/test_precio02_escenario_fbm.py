"""Escenarios FBM de MX y US (REPRICING 02 F.3).

Una oferta FBM produce escenario disponible con `logistica_origen`, muestra
de envio y fees partidos; sin muestra, sin tasa o con tasa vieja el motivo
manda (`envio_sin_historia`, `fx_ausente`, `fx_desactualizado`). La 0055
exige origen y partidos a todo disponible nuevo.
"""

from __future__ import annotations

import itertools
import os
import re
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

from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs

RAIZ = Path(__file__).resolve().parents[1]

ORDEN_F3 = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
    "0055_precio02_escenario_exige.sql",
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
    db = f"precio02f3_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_F3:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _listing(conn, *, plataforma: str, sku: str, asin: str) -> tuple[int, int]:
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (f"P-{sku}", sku),
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pid, plataforma, asin, sku),
    ).fetchone()[0]
    return pid, lid


def _costo(conn, pid: int, *, monto: str = "40.0000") -> int:
    run = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written,"
        " rows_skipped, ok) VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE)"
        " RETURNING id",
        (FETCH, FETCH),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id) VALUES (%s, %s, 'MXN', FALSE, '2026-01-01', %s)"
        " RETURNING id",
        (pid, monto, run),
    ).fetchone()[0]


def _oferta(
    conn,
    lid: int,
    *,
    plataforma: str,
    canal: str,
    sku: str,
    asin: str,
    precio: str,
    moneda: str,
    evento: str,
) -> int:
    return conn.execute(
        "INSERT INTO estimacion_oferta_observation"
        " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, %s) RETURNING id",
        (
            lid,
            plataforma,
            sku,
            asin,
            canal,
            precio,
            moneda,
            FETCH,
            NOW,
            evento,
            f"{canal}:{precio}:{moneda}:{FETCH.isoformat()}",
        ),
    ).fetchone()[0]


def _fee(
    conn,
    oid: int,
    lid: int,
    *,
    plataforma: str,
    canal: str,
    sku: str,
    asin: str,
    precio: str,
    moneda: str,
    total: str,
    detalles: list,
    evento: str,
) -> int:
    return conn.execute(
        "INSERT INTO estimacion_fee_observation"
        " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
        " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
        " fetched_at, observed_at, estado, source_event_id, canonical_input,"
        " context_fingerprint, fee_details)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'success', %s,"
        " '{}'::jsonb, %s, %s) RETURNING id",
        (
            oid,
            lid,
            plataforma,
            sku,
            asin,
            canal,
            precio,
            moneda,
            total,
            FEE_TIME,
            FETCH,
            NOW,
            evento,
            f"{canal}:{precio}:{moneda}:{FETCH.isoformat()}",
            Json(detalles),
        ),
    ).fetchone()[0]


def _politica(conn, *, universo: str, settings: dict, label: str) -> int:
    return conn.execute(
        "INSERT INTO estimacion_politica_version"
        " (created_at, label, universo, formula_version, settings, valid_from)"
        " VALUES (%s, %s, %s, 'S3', %s, '2026-01-01') RETURNING id",
        (FETCH, label, universo, Json(settings)),
    ).fetchone()[0]


POLITICA_FBM_MX = {
    "universo": "amazon_mx/fbm",
    "formula_version": "S3",
    "iva_divisor": "1.16",
    "isr_tasa": "0.025",
    "logistica": "0",
    "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",
    "retencion_iva_reconciliacion": "0.08",
    "precio_incluye_iva": True,
    "fee_tax_amount_requiere_politica": True,
}

POLITICA_FBM_US = {
    "universo": "amazon_us/fbm",
    "formula_version": "S3",
    "iva_divisor": "1",
    "isr_tasa": "0.0207",
    "logistica": "0",
    "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",
    "retencion_iva_reconciliacion": "0.08",
    "precio_incluye_iva": False,
    "fee_tax_amount_requiere_politica": True,
}


def _muestra(
    conn,
    pid: int,
    *,
    plataforma: str,
    canal: str = "fbm",
    origen: str = "propio",
    valor: str = "30.0000",
    moneda: str = "MXN",
) -> int:
    return conn.execute(
        "INSERT INTO precio_envio_muestra (product_id, platform, canal, ventana_desde,"
        " ventana_hasta, envios, percentil, valor, valor_currency, mediana,"
        " mediana_currency, p90, p90_currency, maximo, maximo_currency, origen,"
        " familia_id, donantes, regla_version)"
        " VALUES (%s, %s, %s, '2026-03-12', '2026-09-07', 4, 'mediana', %s, %s, %s,"
        " %s, %s, %s, %s, %s, %s, NULL, NULL, 'f2.1') RETURNING id",
        (
            pid,
            plataforma,
            canal,
            valor,
            moneda,
            valor,
            moneda,
            valor,
            moneda,
            valor,
            moneda,
            origen,
        ),
    ).fetchone()[0]


def _fx(conn, *, dia: date, tasa: str = "17.00000000") -> None:
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


def _sembrar_fbm_mx(conn, *, muestra: bool = True):
    pid, lid = _listing(conn, plataforma="amazon_mx", sku="SS-F3-FBM", asin="B0F3FBM01")
    _costo(conn, pid)
    oid = _oferta(
        conn,
        lid,
        plataforma="amazon_mx",
        canal="fbm",
        sku="SS-F3-FBM",
        asin="B0F3FBM01",
        precio="116.0000",
        moneda="MXN",
        evento="evt-oferta-fbm",
    )
    fid = _fee(
        conn,
        oid,
        lid,
        plataforma="amazon_mx",
        canal="fbm",
        sku="SS-F3-FBM",
        asin="B0F3FBM01",
        precio="116.0000",
        moneda="MXN",
        total="5.0000",
        detalles=[{"fee_type": "ReferralFee", "final_fee": "5.0000"}],
        evento="evt-fee-fbm",
    )
    _politica(conn, universo="amazon_mx/fbm", settings=POLITICA_FBM_MX, label="fbm-mx-f3")
    mid = _muestra(conn, pid, plataforma="amazon_mx") if muestra else None
    return pid, lid, oid, fid, mid


def _sembrar_fbm_us(conn):
    pid, lid = _listing(conn, plataforma="amazon_us", sku="SS-F3-US", asin="B0F3US001")
    _costo(conn, pid)
    oid = _oferta(
        conn,
        lid,
        plataforma="amazon_us",
        canal="fbm",
        sku="SS-F3-US",
        asin="B0F3US001",
        precio="29.9900",
        moneda="USD",
        evento="evt-oferta-us",
    )
    fid = _fee(
        conn,
        oid,
        lid,
        plataforma="amazon_us",
        canal="fbm",
        sku="SS-F3-US",
        asin="B0F3US001",
        precio="29.9900",
        moneda="USD",
        total="6.6000",
        detalles=[
            {"fee_type": "ReferralFee", "final_fee": "4.5000"},
            {"fee_type": "VariableClosingFee", "final_fee": "0.3000"},
            {"fee_type": "PerItemFee", "final_fee": "1.8000"},
        ],
        evento="evt-fee-us",
    )
    _politica(conn, universo="amazon_us/fbm", settings=POLITICA_FBM_US, label="fbm-us-f3")
    mid = _muestra(conn, pid, plataforma="amazon_us", valor="170.0000")
    return pid, lid, oid, fid, mid


@_skip_sin_pg
def test_fbm_mx_disponible_con_origen_y_fees_partidos():
    with _db() as conn:
        pid, lid, oid, fid, mid = _sembrar_fbm_mx(conn)
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "disponible"
        assert esc.logistica_origen == "propio"
        assert esc.envio_muestra_id == mid
        assert (esc.fee_variable, esc.fee_fijo) == (Decimal("5.0000"), Decimal("0.0000"))
        assert esc.fee_cotizable is True
        assert esc.miembro_product_id == pid
        assert esc.contribucion == Decimal("22.5000")
        persistir_escenario(conn, esc)
        fila = conn.execute(
            "SELECT logistica_origen, envio_muestra_id, fee_variable, fee_fijo,"
            " fee_cotizable, miembro_product_id FROM estimacion_escenario"
            " WHERE listing_id = %s",
            (lid,),
        ).fetchone()
        assert fila == ("propio", mid, Decimal("5.0000"), Decimal("0.0000"), True, pid)


@_skip_sin_pg
def test_fbm_mx_sin_muestra_da_envio_sin_historia():
    with _db() as conn:
        _pid, lid, oid, fid, _mid = _sembrar_fbm_mx(conn, muestra=False)
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "incompleta"
        assert "envio_sin_historia" in esc.motivos
        assert esc.logistica_origen is None
        persistir_escenario(conn, esc)


@_skip_sin_pg
def test_us_sin_tasa_da_fx_ausente():
    with _db() as conn:
        _pid, lid, oid, fid, _mid = _sembrar_fbm_us(conn)
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "incompleta"
        assert "fx_ausente" in esc.motivos


@_skip_sin_pg
def test_us_tasa_vieja_da_fx_desactualizado():
    with _db() as conn:
        _pid, lid, oid, fid, _mid = _sembrar_fbm_us(conn)
        _fx(conn, dia=VALORACION - timedelta(days=4))
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "incompleta"
        assert "fx_desactualizado" in esc.motivos


@_skip_sin_pg
def test_us_disponible_convierte_muestra_y_parte_fees():
    with _db() as conn:
        pid, lid, oid, fid, mid = _sembrar_fbm_us(conn)
        _fx(conn, dia=VALORACION)
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "disponible"
        comp_l = next(c for c in esc.componentes if c["nombre"] == "logistica")
        assert Decimal(comp_l["importe_normalizado"]) == Decimal("10.0000")
        assert esc.logistica_origen == "propio"
        assert esc.envio_muestra_id == mid
        assert (esc.fee_variable, esc.fee_fijo) == (Decimal("4.5000"), Decimal("2.1000"))
        assert esc.fee_cotizable is True
        assert esc.miembro_product_id == pid
        comp = next(c for c in esc.componentes if c["nombre"] == "logistica")
        assert (Decimal(comp["importe_original"]), comp["moneda_original"]) == (
            Decimal("170.0000"),
            "MXN",
        )
        assert Decimal(comp["tasa"]) == Decimal("17.00000000")
        assert comp["fuente"] == "propio"
        persistir_escenario(conn, esc)


@_skip_sin_pg
def test_us_conversion_inexacta_cuantiza_a_4():
    with _db() as conn:
        _pid, lid, oid, fid, _mid = _sembrar_fbm_us(conn)
        _fx(conn, dia=VALORACION, tasa="17.50000000")
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "disponible"
        comp_l = next(c for c in esc.componentes if c["nombre"] == "logistica")
        assert Decimal(comp_l["importe_normalizado"]) == Decimal("9.7143")


@_skip_sin_pg
def test_sin_referral_unico_da_fee_incompatible():
    with _db() as conn:
        pid, lid = _listing(conn, plataforma="amazon_mx", sku="SS-F3-NR", asin="B0F3NR001")
        _costo(conn, pid)
        oid = _oferta(
            conn,
            lid,
            plataforma="amazon_mx",
            canal="fbm",
            sku="SS-F3-NR",
            asin="B0F3NR001",
            precio="116.0000",
            moneda="MXN",
            evento="evt-oferta-nr",
        )
        fid = _fee(
            conn,
            oid,
            lid,
            plataforma="amazon_mx",
            canal="fbm",
            sku="SS-F3-NR",
            asin="B0F3NR001",
            precio="116.0000",
            moneda="MXN",
            total="10.0000",
            detalles=[{"fee_type": "FBAFees", "final_fee": "10.0000"}],
            evento="evt-fee-nr",
        )
        _politica(conn, universo="amazon_mx/fbm", settings=POLITICA_FBM_MX, label="fbm-mx-nr")
        _muestra(conn, pid, plataforma="amazon_mx")
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "incompleta"
        assert "fee_incompatible" in esc.motivos


@_skip_sin_pg
def test_fba_sigue_con_origen_politica_y_partidos():
    with _db() as conn:
        pid, lid = _listing(conn, plataforma="amazon_mx", sku="SS-F3-FBA", asin="B0F3FBA01")
        _costo(conn, pid)
        oid = _oferta(
            conn,
            lid,
            plataforma="amazon_mx",
            canal="fba",
            sku="SS-F3-FBA",
            asin="B0F3FBA01",
            precio="116.0000",
            moneda="MXN",
            evento="evt-oferta-fba",
        )
        fid = _fee(
            conn,
            oid,
            lid,
            plataforma="amazon_mx",
            canal="fba",
            sku="SS-F3-FBA",
            asin="B0F3FBA01",
            precio="116.0000",
            moneda="MXN",
            total="15.0000",
            detalles=[
                {"fee_type": "ReferralFee", "final_fee": "10.0000"},
                {"fee_type": "FBAFees", "final_fee": "5.0000"},
            ],
            evento="evt-fee-fba",
        )
        _politica(
            conn,
            universo="amazon_mx/fba",
            settings={**POLITICA_FBM_MX, "universo": "amazon_mx/fba"},
            label="fba-mx-f3",
        )
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "disponible"
        assert esc.logistica_origen == "politica"
        assert esc.envio_muestra_id is None
        assert (esc.fee_variable, esc.fee_fijo) == (Decimal("10.0000"), Decimal("5.0000"))
        assert esc.fee_cotizable is True
        persistir_escenario(conn, esc)


@_skip_sin_pg
def test_politica_sin_logistica_da_motivo_no_excepcion():
    with _db() as conn:
        pid, lid = _listing(conn, plataforma="amazon_mx", sku="SS-F3-PL", asin="B0F3PL001")
        _costo(conn, pid)
        oid = _oferta(
            conn,
            lid,
            plataforma="amazon_mx",
            canal="fba",
            sku="SS-F3-PL",
            asin="B0F3PL001",
            precio="116.0000",
            moneda="MXN",
            evento="evt-oferta-pl",
        )
        fid = _fee(
            conn,
            oid,
            lid,
            plataforma="amazon_mx",
            canal="fba",
            sku="SS-F3-PL",
            asin="B0F3PL001",
            precio="116.0000",
            moneda="MXN",
            total="15.0000",
            detalles=[{"fee_type": "ReferralFee", "final_fee": "10.0000"}],
            evento="evt-fee-pl",
        )
        rotos = {**POLITICA_FBM_MX, "universo": "amazon_mx/fba"}
        del rotos["logistica"]
        _politica(conn, universo="amazon_mx/fba", settings=rotos, label="fba-rota")
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        assert esc.estado == "incompleta"
        assert "politica_invalida" in esc.motivos


def _fila_que_llega_al_check(
    conn, *, lid, pol, oid, fid, costo_id, run_id, sku, asin, evento, columnas, valores
):
    """INSERT `disponible` valido salvo lo que cada test omite.

    La huella es la de oferta/fee para que el trigger de coherencia deje
    pasar la fila hasta el CHECK de la 0055.
    """
    huella = f"fbm:116.0000:MXN:{FETCH.isoformat()}"
    conn.execute(
        "INSERT INTO estimacion_escenario (listing_id, platform, asin, seller_sku,"
        " canal, observed_at, valoracion_date, estado, formula_version,"
        " componentes, contribucion, contribucion_pct, moneda,"
        " politica_version_id, oferta_observation_id, fee_observation_id,"
        " sku_cost_id, costo_validation_run_id, costo_validated_at,"
        + columnas
        + " canonical_input, context_fingerprint, source_event_id)"
        " VALUES (%s, 'amazon_mx', %s, %s, 'fbm', %s, %s,"
        " 'disponible', 'S3', '[]'::jsonb, 22.5000, 22.5000,"
        " 'MXN', %s, %s, %s, %s, %s, %s," + valores + " '{}'::jsonb, %s, %s)",
        (lid, asin, sku, NOW, VALORACION, pol, oid, fid, costo_id, run_id, FETCH, huella, evento),
    )


@_skip_sin_pg
def test_0055_rechaza_disponible_sin_fees_partidos():
    import psycopg.errors

    with _db() as conn:
        pid, lid = _listing(conn, plataforma="amazon_mx", sku="SS-F3-56", asin="B0F356001")
        costo_id = _costo(conn, pid)
        run_id = conn.execute(
            "SELECT ingest_run_id FROM sku_cost WHERE id = %s", (costo_id,)
        ).fetchone()[0]
        oid = _oferta(
            conn,
            lid,
            plataforma="amazon_mx",
            canal="fbm",
            sku="SS-F3-56",
            asin="B0F356001",
            precio="116.0000",
            moneda="MXN",
            evento="evt-oferta-56",
        )
        fid = _fee(
            conn,
            oid,
            lid,
            plataforma="amazon_mx",
            canal="fbm",
            sku="SS-F3-56",
            asin="B0F356001",
            precio="116.0000",
            moneda="MXN",
            total="5.0000",
            detalles=[{"fee_type": "ReferralFee", "final_fee": "5.0000"}],
            evento="evt-fee-56",
        )
        pol = _politica(conn, universo="amazon_mx/fbm", settings=POLITICA_FBM_MX, label="fbm-mx-56")
        # Origen politica (sin muestra): aisla el CHECK de partidos sin
        # violar la coherencia 0054.
        with pytest.raises(psycopg.errors.CheckViolation, match="disponible_exige_fees_partidos"):
            _fila_que_llega_al_check(
                conn,
                lid=lid,
                pol=pol,
                oid=oid,
                fid=fid,
                costo_id=costo_id,
                run_id=run_id,
                sku="SS-F3-56",
                asin="B0F356001",
                evento="evt-0056-ko",
                columnas=" logistica_origen,",
                valores=" 'politica',",
            )


@_skip_sin_pg
def test_0055_rechaza_disponible_sin_origen():
    import psycopg.errors

    with _db() as conn:
        pid, lid = _listing(conn, plataforma="amazon_mx", sku="SS-F3-55", asin="B0F355001")
        costo_id = _costo(conn, pid)
        run_id = conn.execute(
            "SELECT ingest_run_id FROM sku_cost WHERE id = %s", (costo_id,)
        ).fetchone()[0]
        oid = _oferta(
            conn,
            lid,
            plataforma="amazon_mx",
            canal="fbm",
            sku="SS-F3-55",
            asin="B0F355001",
            precio="116.0000",
            moneda="MXN",
            evento="evt-oferta-55",
        )
        fid = _fee(
            conn,
            oid,
            lid,
            plataforma="amazon_mx",
            canal="fbm",
            sku="SS-F3-55",
            asin="B0F355001",
            precio="116.0000",
            moneda="MXN",
            total="5.0000",
            detalles=[{"fee_type": "ReferralFee", "final_fee": "5.0000"}],
            evento="evt-fee-55",
        )
        pol = _politica(conn, universo="amazon_mx/fbm", settings=POLITICA_FBM_MX, label="fbm-mx-55")
        # Partidos puestos: aisla el CHECK de origen.
        with pytest.raises(psycopg.errors.CheckViolation, match="disponible_exige_origen"):
            _fila_que_llega_al_check(
                conn,
                lid=lid,
                pol=pol,
                oid=oid,
                fid=fid,
                costo_id=costo_id,
                run_id=run_id,
                sku="SS-F3-55",
                asin="B0F355001",
                evento="evt-0055-ko",
                columnas=" fee_variable, fee_fijo, fee_cotizable,",
                valores=" 5.0000, 0.0000, TRUE,",
            )
        conn.execute(
            "INSERT INTO estimacion_escenario (listing_id, platform, asin, seller_sku,"
            " canal, observed_at, valoracion_date, estado, formula_version,"
            " componentes, motivos, canonical_input, context_fingerprint,"
            " source_event_id) VALUES (%s, 'amazon_mx', 'B0F355001', 'SS-F3-55', 'fbm',"
            " %s, %s, 'incompleta', 'S3', '[]'::jsonb,"
            " '[\"envio_sin_historia\"]'::jsonb, '{}'::jsonb, 'huella-55-ok',"
            " 'evt-0055-ok')",
            (lid, NOW, VALORACION),
        )


# ---------------------------------------------------------------------------
# R05d-r1: filtro "sin oferta viva" de los scripts X.* (VEREDICTO-R05d-r1,
# AVISO PARA EL OPERADOR). En FBM (X.2/X.3) basta SIN PRECIO PROPIO
# OBSERVADO para excluir (el inventario FBA no existe para FBM y no se
# pide); en FBA (X.1) y MeLi (X.4) la regla sigue siendo "inventario 0 Y
# sin precio". Las pruebas corren el SQL LITERAL de cada script contra
# una base temporal: con el SQL anterior a r1 el caso FBM sin fila de
# inventario NO se excluia (el `total_quantity = 0` era NULL).
# ---------------------------------------------------------------------------

ORDEN_FILTRO = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0032_spapi_pricing.sql",
    "0035_spapi_listings_inventario.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
    "0055_precio02_escenario_exige.sql",
)

EJEC = RAIZ / "docs" / "evidencia" / "repricing-02" / "ejecucion"


@contextmanager
def _db_filtro():
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"precio02r5_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN_FILTRO:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sql_filtro(archivo: Path, marca: str) -> str:
    """El SQL que el script pasa a `lee_sql` en la asignacion de `marca`.

    Sustituye las variables de shell que el script define fuera del SQL
    (`CANAL_SQL`, `ESC_LATERAL`); `$IDS_TODOS`, `$LOTE` y `$FECHA` las
    pone cada prueba.
    """
    texto = archivo.read_text(encoding="utf-8")
    hallado = re.search(marca + r'=\$\(lee_sql "([^"]+)"\)', texto)
    assert hallado, f"{archivo.name}: no se encontro el SQL de {marca}"
    sql = hallado.group(1)
    for var in ("CANAL_SQL", "ESC_LATERAL"):
        definicion = re.search(rf'^{var}="([^"]*)"$', texto, re.MULTILINE)
        if definicion:
            sql = sql.replace(f"${var}", definicion.group(1))
    return sql


def _precio_obs(
    conn, *, asin: str, plataforma: str, propio: Decimal | None, dia: date, hora: int = 10
) -> None:
    moneda = None if propio is None else ("USD" if plataforma == "amazon_us" else "MXN")
    conn.execute(
        "INSERT INTO spapi_price_observation (asin, platform, metric_date, observed_at,"
        " own_listing_price, own_listing_currency) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            asin,
            plataforma,
            dia,
            datetime(dia.year, dia.month, dia.day, hora, tzinfo=UTC),
            propio,
            moneda,
        ),
    )


def _inventario_obs(conn, *, sku: str, plataforma: str, total: int, dia: date) -> None:
    conn.execute(
        "INSERT INTO spapi_inventario_observation (seller_sku, platform, metric_date,"
        " observed_at, total_quantity, api_version) VALUES (%s, %s, %s, %s, %s, 'v1')",
        (sku, plataforma, dia, datetime(dia.year, dia.month, dia.day, 10, tzinfo=UTC), total),
    )


def _meli_obs(conn, *, item: str, precio: Decimal | None, stock: int) -> None:
    conn.execute(
        "INSERT INTO meli_publicacion_observation (item_id, metric_date, observed_at,"
        " estado, precio, moneda, stock) VALUES (%s, %s, %s, 'active', %s, %s, %s)",
        (item, VALORACION, NOW, precio, None if precio is None else "MXN", stock),
    )


def _goal_lote(conn, lid: int, *, plataforma: str, lote: str) -> None:
    conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode, valid_from,"
        " creado_por, go_literal, origen, lote) VALUES (%s, %s, 0.3000, 'shadow', %s, 't',"
        " NULL, 'margen_de_hoy', %s)",
        (lid, plataforma, VALORACION - timedelta(days=1), lote),
    )


def _excluidas(conn, sql: str) -> set[str]:
    crudo = conn.execute(sql).fetchone()[0]
    return set(crudo.split(",")) if crudo else set()


@_skip_sin_pg
def test_r05d_r1_sembrar_fbm_sin_precio_excluye_sin_pedir_inventario():
    """X.2/X.3 (FBM): sin precio propio observado basta para EXCLUIDA.

    El caso discriminante es A: sin fila en spapi_inventario_observation
    (en FBM no existen) la regla vieja NO la excluia; la r1 si.
    """
    sql_x2 = _sql_filtro(EJEC / "X.2" / "sembrar.sh", "EXCLUIDAS")
    sql_x3 = _sql_filtro(EJEC / "X.3" / "sembrar.sh", "EXCLUIDAS")
    with _db_filtro() as conn:
        _, a = _listing(conn, plataforma="amazon_mx", sku="SS-R5-A", asin="B0R5A00001")
        _, b = _listing(conn, plataforma="amazon_mx", sku="SS-R5-B", asin="B0R5B00001")
        _, c = _listing(conn, plataforma="amazon_mx", sku="SS-R5-C", asin="B0R5C00001")
        _, d = _listing(conn, plataforma="amazon_mx", sku="SS-R5-D", asin="B0R5D00001")
        _, e = _listing(conn, plataforma="amazon_mx", sku="SS-R5-E", asin="B0R5E00001")
        # A: sin observacion de precio y SIN fila de inventario -> EXCLUIDA.
        # B: ultima observacion sin precio propio -> EXCLUIDA.
        _precio_obs(conn, asin="B0R5B00001", plataforma="amazon_mx", propio=None, dia=VALORACION)
        # C: con precio propio -> entra.
        _precio_obs(
            conn,
            asin="B0R5C00001",
            plataforma="amazon_mx",
            propio=Decimal("116.0000"),
            dia=VALORACION,
        )
        # D: sin precio propio con inventario 7 (pasado FBA): en FBM el
        # inventario no se consulta -> EXCLUIDA.
        _precio_obs(conn, asin="B0R5D00001", plataforma="amazon_mx", propio=None, dia=VALORACION)
        _inventario_obs(conn, sku="SS-R5-D", plataforma="amazon_mx", total=7, dia=VALORACION)
        # E: manda la ultima observacion (ayer con precio, hoy sin).
        _precio_obs(
            conn,
            asin="B0R5E00001",
            plataforma="amazon_mx",
            propio=Decimal("116.0000"),
            dia=VALORACION,
            hora=8,
        )
        _precio_obs(
            conn, asin="B0R5E00001", plataforma="amazon_mx", propio=None, dia=VALORACION, hora=9
        )
        excl = _excluidas(conn, sql_x2.replace("$IDS_TODOS", f"{a},{b},{c},{d},{e}"))
        assert excl == {str(a), str(b), str(d), str(e)}
        # X.3 (US FBM): la misma regla.
        _, u1 = _listing(conn, plataforma="amazon_us", sku="SS-R5-U1", asin="B0R5U00001")
        _, u2 = _listing(conn, plataforma="amazon_us", sku="SS-R5-U2", asin="B0R5U00002")
        _precio_obs(
            conn,
            asin="B0R5U00002",
            plataforma="amazon_us",
            propio=Decimal("29.9900"),
            dia=VALORACION,
        )
        assert _excluidas(conn, sql_x3.replace("$IDS_TODOS", f"{u1},{u2}")) == {str(u1)}


@_skip_sin_pg
def test_r05d_r1_leer_fbm_excluye_del_denominador_sin_inventario():
    """leer.sh de X.2/X.3: sin precio propio el dia D basta para salir
    del denominador; ya no se pide inventario 0."""
    lote = "a05d11"
    sql_x2 = _sql_filtro(EJEC / "X.2" / "leer.sh", "EXC_CSV")
    sql_x3 = _sql_filtro(EJEC / "X.3" / "leer.sh", "EXC_CSV")
    with _db_filtro() as conn:
        _, g1 = _listing(conn, plataforma="amazon_mx", sku="SS-R5-G1", asin="B0R5G00001")
        _, g2 = _listing(conn, plataforma="amazon_mx", sku="SS-R5-G2", asin="B0R5G00002")
        _, g3 = _listing(conn, plataforma="amazon_mx", sku="SS-R5-G3", asin="B0R5G00003")
        for lid in (g1, g2, g3):
            _goal_lote(conn, lid, plataforma="amazon_mx", lote=lote)
        # G1: sin precio propio el dia D y SIN inventario -> fuera del
        # denominador (la regla vieja la dejaba dentro).
        # G2: con precio propio el dia D -> dentro.
        _precio_obs(
            conn,
            asin="B0R5G00002",
            plataforma="amazon_mx",
            propio=Decimal("116.0000"),
            dia=VALORACION,
        )
        # G3: sin precio el dia D con inventario 0 -> tambien fuera.
        _inventario_obs(conn, sku="SS-R5-G3", plataforma="amazon_mx", total=0, dia=VALORACION)
        sql = sql_x2.replace("'$LOTE'", f"'{lote}'").replace("'$FECHA'", f"'{VALORACION}'")
        assert _excluidas(conn, sql) == {str(g1), str(g3)}
        # X.3 (US): igual.
        _, h1 = _listing(conn, plataforma="amazon_us", sku="SS-R5-H1", asin="B0R5H00001")
        _, h2 = _listing(conn, plataforma="amazon_us", sku="SS-R5-H2", asin="B0R5H00002")
        for lid in (h1, h2):
            _goal_lote(conn, lid, plataforma="amazon_us", lote=lote)
        _precio_obs(
            conn,
            asin="B0R5H00002",
            plataforma="amazon_us",
            propio=Decimal("29.9900"),
            dia=VALORACION,
        )
        sql_us = sql_x3.replace("'$LOTE'", f"'{lote}'").replace("'$FECHA'", f"'{VALORACION}'")
        assert _excluidas(conn, sql_us) == {str(h1)}


@_skip_sin_pg
def test_r05d_r1_fba_y_meli_exigen_inventario_cero_y_sin_precio():
    """La regla de X.1 (FBA) y X.4 (MeLi) NO cambio: inventario 0 Y sin
    precio. Sus scripts no se tocaron; aqui se blinda el contraste."""
    sql_x1 = _sql_filtro(EJEC / "X.1" / "sembrar.sh", "EXCLUIDAS")
    sql_x4 = _sql_filtro(EJEC / "X.4" / "sembrar.sh", "EXCLUIDAS")
    with _db_filtro() as conn:
        _, f1 = _listing(conn, plataforma="amazon_mx", sku="SS-R5-F1", asin="B0R5F00001")
        _, f2 = _listing(conn, plataforma="amazon_mx", sku="SS-R5-F2", asin="B0R5F00002")
        _, f3 = _listing(conn, plataforma="amazon_mx", sku="SS-R5-F3", asin="B0R5F00003")
        _, f4 = _listing(conn, plataforma="amazon_mx", sku="SS-R5-F4", asin="B0R5F00004")
        # F1: sin precio y SIN fila de inventario -> entra (sin dato no es 0).
        # F2: sin precio con inventario 0 -> EXCLUIDA.
        _inventario_obs(conn, sku="SS-R5-F2", plataforma="amazon_mx", total=0, dia=VALORACION)
        # F3: sin precio con inventario 4 -> entra.
        _inventario_obs(conn, sku="SS-R5-F3", plataforma="amazon_mx", total=4, dia=VALORACION)
        # F4: con precio propio e inventario 0 -> entra.
        _precio_obs(
            conn,
            asin="B0R5F00004",
            plataforma="amazon_mx",
            propio=Decimal("116.0000"),
            dia=VALORACION,
        )
        _inventario_obs(conn, sku="SS-R5-F4", plataforma="amazon_mx", total=0, dia=VALORACION)
        excl = _excluidas(conn, sql_x1.replace("$IDS_TODOS", f"{f1},{f2},{f3},{f4}"))
        assert excl == {str(f2)}
        # MeLi (X.4): precio NULL y stock 0, las dos cosas.
        _, m1 = _listing(conn, plataforma="meli", sku="SS-R5-M1", asin="MLMR500001")
        _, m2 = _listing(conn, plataforma="meli", sku="SS-R5-M2", asin="MLMR500002")
        _, m3 = _listing(conn, plataforma="meli", sku="SS-R5-M3", asin="MLMR500003")
        _, m4 = _listing(conn, plataforma="meli", sku="SS-R5-M4", asin="MLMR500004")
        # M1: sin observacion -> entra.
        _meli_obs(conn, item="MLMR500002", precio=None, stock=0)
        _meli_obs(conn, item="MLMR500003", precio=None, stock=5)
        _meli_obs(conn, item="MLMR500004", precio=Decimal("100.0000"), stock=0)
        excl_m = _excluidas(conn, sql_x4.replace("$IDS_TODOS", f"{m1},{m2},{m3},{m4}"))
        assert excl_m == {str(m2)}
