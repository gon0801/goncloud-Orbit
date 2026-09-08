"""Tests MARGEN ESTIMADO 01 A.4 — calculador puro y lectura de escenario.

(a) UNITARIOS: formula S3, validaciones, FX, TaxAmount, estados (AC1-AC9).
(b) INTEGRACION: persistencia idempotente, reader batch as-of, pipeline (AC11).
(c) REGRESION: sin imports Ads, politica cero/multiple, no HTTP en TX.

Regla 9: cada caso distingue el codigo previo (ModuleNotFoundError al coleccionar).
"""

from __future__ import annotations

import importlib
import os
import socket
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.estimacion_venta import (
    DetalleFee,
    EntradaCalculo,
    PoliticaCalculo,
    calcular_contribucion,
    validar_dinero,
)

ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
FETCH = NOW - timedelta(hours=1)
FEE_TIME = NOW - timedelta(minutes=30)
VALORACION = date(2026, 9, 8)

ORDEN = ("0001_initial.sql", "0028_estimacion_venta.sql")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

POLITICA_AC1 = {
    "universo": "amazon_mx/fba",
    "formula_version": "S3",
    "iva_divisor": "1.16",
    "isr_tasa": "0.01",
    "logistica": "5",
    "retencion_iva_reconciliacion": "0.08",
    "precio_incluye_iva": True,
    "fee_tax_amount_requiere_politica": True,
}

POLITICA_FBA_MX_SELLADA = {
    "universo": "amazon_mx/fba",
    "formula_version": "S3",
    "iva_divisor": "1.16",
    "isr_tasa": "0.025",
    "logistica": "0",
    "logistica_semantica": "L=0 solo porque logistica FBA esta incluida en fees Amazon",
    "retencion_iva_reconciliacion": "0.08",
    "precio_incluye_iva": True,
    "fee_tax_amount_requiere_politica": True,
}

POLITICA_USD_AC5 = {
    "universo": "amazon_us/fba",
    "formula_version": "S3",
    "iva_divisor": "1",
    "isr_tasa": "0.01",
    "logistica": "5",
    "retencion_iva_reconciliacion": "0",
    "precio_incluye_iva": False,
    "fee_tax_amount_requiere_politica": True,
}

EXCLUSIONES_ESPERADAS = frozenset(
    {
        "ads",
        "reembolsos",
        "almacenamiento",
        "devoluciones",
        "costos_periodicos",
        "gastos_generales",
    }
)

FEE_DETALLES_15 = (
    DetalleFee(fee_type="ReferralFee", final_fee=Decimal("10.0000")),
    DetalleFee(fee_type="FBAFees", final_fee=Decimal("5.0000")),
)

FEE_DETALLES_15_JSON = [
    {"fee_type": "ReferralFee", "final_fee": "10.0000"},
    {"fee_type": "FBAFees", "final_fee": "5.0000"},
]

FEE_DETALLES_16_JSON = [
    {"fee_type": "ReferralFee", "final_fee": "11.0000"},
    {"fee_type": "FBAFees", "final_fee": "5.0000"},
]

FEE_DETALLES_20_JSON = [
    {"fee_type": "ReferralFee", "final_fee": "14.0000"},
    {"fee_type": "FBAFees", "final_fee": "6.0000"},
]

FEE_DETALLE_CERO = (DetalleFee(fee_type="TotalFees", final_fee=Decimal("0.0000")),)

FEE_DETALLE_CERO_JSON = [{"fee_type": "TotalFees", "final_fee": "0.0000"}]


def _importar_modulos():
    return {
        "DetalleFee": DetalleFee,
        "EntradaCalculo": EntradaCalculo,
        "PoliticaCalculo": PoliticaCalculo,
        "calcular_contribucion": calcular_contribucion,
        "validar_dinero": validar_dinero,
    }


def _politica_ac1(**overrides) -> PoliticaCalculo:
    settings = {**POLITICA_AC1, **overrides}
    return PoliticaCalculo(
        politica_version_id=1,
        label="fixture-ac1",
        universo=settings["universo"],
        formula_version=settings["formula_version"],
        settings=settings,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        created_at=FETCH,
    )


def _entrada_completa_ac1(**kwargs) -> EntradaCalculo:
    base = dict(
        precio_bruto=Decimal("116.0000"),
        precio_moneda="MXN",
        costo_original=Decimal("40.0000"),
        costo_moneda="MXN",
        fee_total=Decimal("15.0000"),
        costo_includes_tax=False,
        fee_detalles=FEE_DETALLES_15,
        fee_estado="success",
        moneda_base="MXN",
    )
    base.update(kwargs)
    return EntradaCalculo(**base)


def _insertar_fee(
    conn,
    *,
    oid,
    lid,
    total,
    detalles_json,
    observed_at=NOW,
    evento="evt-fee",
    huella=None,
    oferta_id=None,
    fees_estimated_at=FEE_TIME,
):
    huella = huella or f"fba:116.0000:MXN:{FETCH.isoformat()}"
    return conn.execute(
        "INSERT INTO estimacion_fee_observation"
        " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
        " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
        " fetched_at, observed_at, estado, source_event_id, canonical_input,"
        " context_fingerprint, fee_details)"
        " VALUES (%s, %s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 116.0000, 'MXN',"
        " %s, %s, %s, %s, 'success', %s, '{}'::jsonb, %s, %s) RETURNING id",
        (
            oid,
            lid,
            total,
            fees_estimated_at,
            FETCH,
            observed_at,
            evento,
            huella,
            Json(detalles_json),
        ),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# (a) UNITARIOS — AC1-AC9
# ---------------------------------------------------------------------------


def test_modulo_estimacion_venta_existe():
    mod = importlib.import_module("app.estimacion_venta")
    assert hasattr(mod, "calcular_contribucion")


def test_sin_imports_motor_ads():
    fuente = (ROOT / "app" / "estimacion_venta.py").read_text(encoding="utf-8")
    assert "app.ads" not in fuente
    assert "from app.ads" not in fuente
    mod_repo = (ROOT / "app" / "estimacion_repository.py").read_text(encoding="utf-8")
    assert "app.ads" not in mod_repo
    assert "httpx" not in mod_repo


def test_ac1_inputs_completos_contribucion_39():
    m = _importar_modulos()
    resultado = m["calcular_contribucion"](
        _entrada_completa_ac1(), _politica_ac1(), valoracion_date=VALORACION
    )
    assert resultado.estado == "disponible"
    assert resultado.contribucion == Decimal("39.0000")
    assert resultado.contribucion_pct == Decimal("39.0000")
    assert resultado.moneda == "MXN"
    assert resultado.motivos == ()


def test_ac3_faltante_obligatorio_null_con_motivo():
    m = _importar_modulos()
    politica = _politica_ac1()
    for campo, kwargs in (
        ("costo_ausente", {"costo_original": None, "costo_moneda": None}),
        ("fee_ausente", {"fee_total": None, "fee_estado": None}),
        ("precio_ausente", {"precio_bruto": None, "precio_moneda": None}),
    ):
        entrada = _entrada_completa_ac1(**kwargs)
        resultado = m["calcular_contribucion"](entrada, politica, valoracion_date=VALORACION)
        assert resultado.estado == "incompleta", campo
        assert resultado.contribucion is None
        assert resultado.contribucion_pct is None
        assert campo in resultado.motivos


def test_ac3_subtotal_conocido_no_se_presenta_completo():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(fee_total=None, fee_estado=None)
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert resultado.contribucion is None
    nombres = {c.nombre for c in resultado.componentes if c.importe_normalizado is not None}
    assert "ingreso_normalizado" in nombres
    assert "costo_normalizado" in nombres


def test_ac4_oferta_vieja_estado_desactualizada():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(motivos_entrada=("oferta_desactualizada",))
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "desactualizada"
    assert resultado.contribucion is None
    assert "oferta_desactualizada" in resultado.motivos


def test_ac4_fee_incompatible_no_reutilizado():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(motivos_entrada=("fee_incompatible",))
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert resultado.contribucion is None
    assert "fee_incompatible" in resultado.motivos


def test_ac5_fx_usd_mxn_contribucion_39():
    m = _importar_modulos()
    politica = PoliticaCalculo(
        politica_version_id=2,
        label="fixture-usd",
        universo=POLITICA_USD_AC5["universo"],
        formula_version="S3",
        settings=POLITICA_USD_AC5,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        created_at=FETCH,
    )
    entrada = m["EntradaCalculo"](
        precio_bruto=Decimal("100.0000"),
        precio_moneda="USD",
        costo_original=Decimal("680.0000"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fx_rate=Decimal("17.00000000"),
        fx_rate_date=VALORACION,
        fx_base="USD",
        fx_quote="MXN",
        fx_source="test",
        fee_total=Decimal("15.0000"),
        fee_detalles=FEE_DETALLES_15,
        fee_estado="success",
        moneda_base="USD",
    )
    resultado = m["calcular_contribucion"](entrada, politica, valoracion_date=VALORACION)
    assert resultado.estado == "disponible"
    assert resultado.contribucion == Decimal("39.0000")
    assert resultado.contribucion_pct == Decimal("39.0000")
    assert resultado.moneda == "USD"
    costo_comp = next(c for c in resultado.componentes if c.nombre == "costo_normalizado")
    assert costo_comp.importe_normalizado == Decimal("40.0000")
    fx_comp = next(c for c in resultado.componentes if c.nombre == "fx")
    assert fx_comp.tasa == Decimal("17.00000000")
    assert fx_comp.fecha == VALORACION


def test_ac5_sin_fx_null_con_motivo():
    m = _importar_modulos()
    politica = PoliticaCalculo(
        politica_version_id=2,
        label="fixture-usd",
        universo=POLITICA_USD_AC5["universo"],
        formula_version="S3",
        settings=POLITICA_USD_AC5,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        created_at=FETCH,
    )
    entrada = m["EntradaCalculo"](
        precio_bruto=Decimal("100.0000"),
        precio_moneda="USD",
        costo_original=Decimal("680.0000"),
        costo_moneda="MXN",
        fee_total=Decimal("15.0000"),
        fee_detalles=FEE_DETALLES_15,
        fee_estado="success",
        moneda_base="USD",
        motivos_entrada=("fx_ausente",),
    )
    resultado = m["calcular_contribucion"](entrada, politica, valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert resultado.contribucion is None
    assert "fx_ausente" in resultado.motivos


def test_ac6_identidad_ambigua():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(motivos_entrada=("identidad_ambigua",))
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "identidad_ambigua"
    assert resultado.contribucion is None
    assert "identidad_ambigua" in resultado.motivos


def test_ac7_contribucion_cero_sigue_disponible():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(
        costo_original=Decimal("79.0000"),
        fee_total=Decimal("15.0000"),
    )
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "disponible"
    assert resultado.contribucion == Decimal("0.0000")
    assert resultado.contribucion_pct == Decimal("0.0000")


def test_ac7_contribucion_negativa_sigue_disponible():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(costo_original=Decimal("89.0000"))
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "disponible"
    assert resultado.contribucion == Decimal("-10.0000")
    assert resultado.contribucion_pct == Decimal("-10.0000")


def test_ac8_total_fee_se_resta_una_sola_vez():
    m = _importar_modulos()
    detalles = (
        m["DetalleFee"](fee_type="ReferralFee", final_fee=Decimal("10.0000")),
        m["DetalleFee"](fee_type="FBAFees", final_fee=Decimal("5.0000")),
    )
    entrada = _entrada_completa_ac1(fee_total=Decimal("15.0000"), fee_detalles=detalles)
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "disponible"
    assert resultado.contribucion == Decimal("39.0000")
    fee_comp = next(c for c in resultado.componentes if c.nombre == "fee_total")
    assert fee_comp.importe_normalizado == Decimal("15.0000")


def test_ac9_costo_cero_rechazado():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(costo_original=Decimal("0"))
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert resultado.contribucion is None
    assert any("costo" in mot for mot in resultado.motivos)


def test_ac9_nan_infinity_rechazados():
    m = _importar_modulos()
    with pytest.raises(ValueError):
        m["validar_dinero"](Decimal("NaN"), campo="costo")
    with pytest.raises(ValueError):
        m["validar_dinero"](Decimal("Infinity"), campo="costo")


def test_ac9_fuera_de_rango_rechazado():
    m = _importar_modulos()
    with pytest.raises(ValueError):
        m["validar_dinero"](Decimal("10000000000.0000"), campo="costo")


def test_ac9_dinero_con_fraccion_subcentavo_rechazado_sin_redondear():
    m = _importar_modulos()
    with pytest.raises(ValueError):
        m["validar_dinero"](Decimal("1.000001"), campo="costo")


def test_ac5_fx_acepta_precision_de_ocho_decimales():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(
        costo_original=Decimal("684.9383"),
        costo_moneda="MXN",
        precio_moneda="USD",
        moneda_base="USD",
        fx_rate=Decimal("17.12345678"),
        fx_rate_date=VALORACION,
        fx_base="USD",
        fx_quote="MXN",
        fx_source="banxico",
    )
    resultado = m["calcular_contribucion"](
        entrada,
        _politica_ac1(universo="amazon_us/fba", iva_divisor="1"),
        valoracion_date=VALORACION,
        universo_esperado="amazon_us/fba",
    )
    assert resultado.estado == "disponible"
    assert "fx_tasa_invalida" not in resultado.motivos


def test_ac9_fee_incluido_profundo_invalido_bloquea_total():
    m = _importar_modulos()
    profundo = m["DetalleFee"](fee_type="Nivel3", final_fee=Decimal("1.000001"))
    nivel_dos = m["DetalleFee"](
        fee_type="Nivel2",
        final_fee=Decimal("1.0000"),
        included_fee_details=(profundo,),
    )
    detalle = m["DetalleFee"](
        fee_type="ReferralFee",
        final_fee=Decimal("10.0000"),
        included_fee_details=(nivel_dos,),
    )
    entrada = _entrada_completa_ac1(
        fee_total=Decimal("15.0000"),
        fee_detalles=(detalle, FEE_DETALLES_15[1]),
    )
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert "fee_incompatible" in resultado.motivos


def test_ac9_fee_cero_solo_con_success_y_evidencia():
    m = _importar_modulos()
    entrada_ok = _entrada_completa_ac1(
        fee_total=Decimal("0.0000"),
        fee_detalles=FEE_DETALLE_CERO,
        fee_estado="success",
    )
    assert (
        m["calcular_contribucion"](entrada_ok, _politica_ac1(), valoracion_date=VALORACION).estado
        == "disponible"
    )
    entrada_sin_detalle = _entrada_completa_ac1(
        fee_total=Decimal("0.0000"),
        fee_detalles=(),
        fee_estado="success",
    )
    assert (
        m["calcular_contribucion"](
            entrada_sin_detalle, _politica_ac1(), valoracion_date=VALORACION
        ).estado
        == "incompleta"
    )
    entrada_mala = _entrada_completa_ac1(fee_total=Decimal("0.0000"), fee_estado=None)
    assert (
        m["calcular_contribucion"](entrada_mala, _politica_ac1(), valoracion_date=VALORACION).estado
        == "incompleta"
    )


def test_tax_amount_anidado_impuesto_fee_pendiente():
    m = _importar_modulos()
    detalle = m["DetalleFee"](
        fee_type="ReferralFee",
        final_fee=Decimal("10.0000"),
        tax_amount=Decimal("1.6000"),
    )
    entrada = _entrada_completa_ac1(
        fee_total=Decimal("15.0000"), fee_detalles=(detalle, *FEE_DETALLES_15[1:])
    )
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert resultado.contribucion is None
    assert "impuesto_fee_pendiente" in resultado.motivos


def test_tax_amount_anidado_en_included_fee_detail():
    m = _importar_modulos()
    incluido = m["DetalleFee"](
        fee_type="SubFee",
        final_fee=Decimal("5.0000"),
        tax_amount=Decimal("0.5000"),
    )
    detalle = m["DetalleFee"](
        fee_type="ReferralFee",
        final_fee=Decimal("10.0000"),
        included_fee_details=(incluido,),
    )
    entrada = _entrada_completa_ac1(
        fee_total=Decimal("15.0000"),
        fee_detalles=(detalle, FEE_DETALLES_15[1]),
    )
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert "impuesto_fee_pendiente" in resultado.motivos


def test_retencion_iva_solo_conciliacion_no_resta():
    m = _importar_modulos()
    resultado = m["calcular_contribucion"](
        _entrada_completa_ac1(), _politica_ac1(), valoracion_date=VALORACION
    )
    ret_iva = next(c for c in resultado.componentes if c.nombre == "retencion_iva_conciliacion")
    assert ret_iva.importe_normalizado == Decimal("8.0000")
    assert ret_iva.pertenece_a_total is False
    assert resultado.contribucion == Decimal("39.0000")


def test_exclusiones_auditables():
    m = _importar_modulos()
    resultado = m["calcular_contribucion"](
        _entrada_completa_ac1(), _politica_ac1(), valoracion_date=VALORACION
    )
    assert EXCLUSIONES_ESPERADAS.issubset(set(resultado.exclusiones))


def test_sin_redondeo_intermedio_divisor_irracional():
    """Detecta redondeo prematuro de I, ISR o costo FX antes del resultado."""
    m = _importar_modulos()
    politica = _politica_ac1(iva_divisor="3", isr_tasa="0.3333")
    entrada = m["EntradaCalculo"](
        precio_bruto=Decimal("100.0000"),
        precio_moneda="MXN",
        costo_original=Decimal("40.0000"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fee_total=Decimal("10.0000"),
        fee_detalles=(m["DetalleFee"](fee_type="ReferralFee", final_fee=Decimal("10.0000")),),
        fee_estado="success",
        moneda_base="MXN",
    )
    resultado = m["calcular_contribucion"](entrada, politica, valoracion_date=VALORACION)
    assert resultado.estado == "disponible"
    ingreso_exacto = Decimal("100.0000") / Decimal("3")
    isr_exacto = ingreso_exacto * Decimal("0.3333")
    contrib_exacto = (
        ingreso_exacto - Decimal("40.0000") - Decimal("10.0000") - Decimal("5") - isr_exacto
    )
    assert resultado.contribucion == contrib_exacto.quantize(Decimal("0.0001"))
    assert resultado.contribucion != Decimal("38.3333")


def test_fee_detalle_suma_distinta_incompatible():
    m = _importar_modulos()
    entrada = _entrada_completa_ac1(
        fee_detalles=(
            DetalleFee(fee_type="ReferralFee", final_fee=Decimal("10.0000")),
            DetalleFee(fee_type="FBAFees", final_fee=Decimal("4.0000")),
        )
    )
    resultado = m["calcular_contribucion"](entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert "fee_incompatible" in resultado.motivos


def test_politica_cero_o_multiple_motivo_import():
    from app.estimacion_repository import resolver_politica_aplicable

    assert callable(resolver_politica_aplicable)


def test_politica_invalida_devuelve_null_sin_usar_settings_faltantes():
    politica = PoliticaCalculo(
        politica_version_id=1,
        label="invalida",
        universo="amazon_mx/fba",
        formula_version="S3",
        settings={},
        valid_from=date(2026, 1, 1),
        valid_to=None,
        created_at=FETCH,
    )
    resultado = calcular_contribucion(_entrada_completa_ac1(), politica, valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert resultado.contribucion is None
    assert resultado.motivos == ("politica_invalida",)


def test_politica_con_divisor_infinito_devuelve_null():
    resultado = calcular_contribucion(
        _entrada_completa_ac1(),
        _politica_ac1(iva_divisor="Infinity"),
        valoracion_date=VALORACION,
    )
    assert resultado.estado == "incompleta"
    assert resultado.contribucion is None
    assert resultado.motivos == ("politica_invalida",)


def test_costo_includes_tax_bloquea_disponible():
    entrada = _entrada_completa_ac1(costo_includes_tax=True)
    resultado = calcular_contribucion(entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert resultado.contribucion is None
    assert "costo_impuesto_incompatible" in resultado.motivos


def test_costo_base_fiscal_ausente_bloquea_disponible():
    entrada = _entrada_completa_ac1(costo_includes_tax=None)
    resultado = calcular_contribucion(entrada, _politica_ac1(), valoracion_date=VALORACION)
    assert resultado.estado == "incompleta"
    assert "costo_base_fiscal_ausente" in resultado.motivos


# ---------------------------------------------------------------------------
# (b) INTEGRACION — AC11, reader, persistencia
# ---------------------------------------------------------------------------


@contextmanager
def db_estimacion(prefijo: str = "orbit_margen_a4", *, incluir_politica_seed: bool = True):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=False)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        if incluir_politica_seed:
            conn.execute(
                "INSERT INTO estimacion_politica_version"
                " (created_at, label, universo, formula_version, settings, valid_from)"
                " VALUES (%s, 'fixture-fba-mx', 'amazon_mx/fba', 'S3', %s, '2026-01-01')",
                (FETCH, Json(POLITICA_FBA_MX_SELLADA)),
            )
        conn.commit()
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sembrar_listing(conn, *, odoo_sku="P-MX", asin="B0EST01", seller_sku="SS-MX-1"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (odoo_sku, odoo_sku),
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_mx', %s, %s) RETURNING id",
        (pid, asin, seller_sku),
    ).fetchone()[0]
    return pid, lid


def _sembrar_costo(
    conn,
    product_id,
    *,
    amount="40.0000",
    run_finished=FETCH,
    includes_tax=False,
):
    run_id = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written, rows_skipped, ok)"
        " VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE) RETURNING id",
        (run_finished, run_finished),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id) VALUES (%s, %s, 'MXN', %s, '2026-01-01', %s)"
        " RETURNING id",
        (product_id, amount, includes_tax, run_id),
    ).fetchone()[0]


def _sembrar_oferta(conn, lid, *, evento="evt-oferta", observed_at=NOW, huella=None):
    huella = huella or f"fba:116.0000:MXN:{FETCH.isoformat()}"
    return conn.execute(
        "INSERT INTO estimacion_oferta_observation"
        " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
        " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 116.0000, 'MXN',"
        " %s, %s, %s, '{}'::jsonb, %s) RETURNING id",
        (lid, FETCH, observed_at, evento, huella),
    ).fetchone()[0]


@_skip_db
def test_ac11_idempotencia_escenario():
    from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento="evt-oferta-1")
        fid = _insertar_fee(
            conn, oid=oid, lid=lid, total="15.0000", detalles_json=FEE_DETALLES_15_JSON
        )
        conn.commit()

        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        conn.commit()
        r1 = persistir_escenario(conn, esc)
        conn.commit()
        r2 = persistir_escenario(conn, esc)
        conn.commit()
        assert r1.reutilizada is False
        assert r2.reutilizada is True
        n = conn.execute("SELECT count(*) FROM estimacion_escenario").fetchone()[0]
        assert n == 1


@_skip_db
def test_ac11_correccion_posterior_nueva_fila():
    from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento="evt-oferta-v1")
        fid = _insertar_fee(
            conn,
            oid=oid,
            lid=lid,
            total="15.0000",
            detalles_json=FEE_DETALLES_15_JSON,
            evento="evt-fee-v1",
        )
        conn.commit()
        esc1 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        persistir_escenario(conn, esc1)
        conn.commit()

        huella2 = f"fba:120.0000:MXN:{FETCH.isoformat()}"
        oid2 = conn.execute(
            "INSERT INTO estimacion_oferta_observation"
            " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
            " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
            " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 120.0000, 'MXN',"
            " %s, %s, 'evt-oferta-v2', '{}'::jsonb, %s) RETURNING id",
            (lid, FETCH, NOW + timedelta(hours=1), huella2),
        ).fetchone()[0]
        fid2 = conn.execute(
            "INSERT INTO estimacion_fee_observation"
            " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
            " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
            " fetched_at, observed_at, estado, source_event_id, canonical_input,"
            " context_fingerprint, fee_details)"
            " VALUES (%s, %s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 120.0000, 'MXN',"
            " 16.0000, %s, %s, %s, 'success', 'evt-fee-v2', '{}'::jsonb, %s, %s) RETURNING id",
            (
                oid2,
                lid,
                FEE_TIME,
                FETCH,
                NOW + timedelta(hours=1),
                huella2,
                Json(FEE_DETALLES_16_JSON),
            ),
        ).fetchone()[0]
        conn.commit()
        esc2 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid2,
            fee_observation_id=fid2,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(hours=1, seconds=1),
        )
        persistir_escenario(conn, esc2)
        conn.commit()
        n = conn.execute("SELECT count(*) FROM estimacion_escenario").fetchone()[0]
        assert n == 2


@_skip_db
def test_ac11_reader_as_of_sin_datos_futuros():
    from app.estimacion_repository import (
        leer_escenarios,
        persistir_escenario,
        sembrar_escenario_desde_refs,
    )

    corte = NOW + timedelta(minutes=30)
    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento="evt-oferta-asof")
        fid = _insertar_fee(
            conn,
            oid=oid,
            lid=lid,
            total="15.0000",
            detalles_json=FEE_DETALLES_15_JSON,
            evento="evt-fee-asof",
        )
        conn.commit()
        esc_temprano = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        persistir_escenario(conn, esc_temprano)
        conn.commit()

        huella_f = f"fba:200.0000:MXN:{FETCH.isoformat()}"
        oid2 = conn.execute(
            "INSERT INTO estimacion_oferta_observation"
            " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
            " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
            " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 200.0000, 'MXN',"
            " %s, %s, 'evt-oferta-futuro', '{}'::jsonb, %s) RETURNING id",
            (lid, FETCH, NOW + timedelta(hours=2), huella_f),
        ).fetchone()[0]
        fid2 = conn.execute(
            "INSERT INTO estimacion_fee_observation"
            " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
            " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
            " fetched_at, observed_at, estado, source_event_id, canonical_input,"
            " context_fingerprint, fee_details)"
            " VALUES (%s, %s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 200.0000, 'MXN',"
            " 20.0000, %s, %s, %s, 'success', 'evt-fee-futuro', '{}'::jsonb, %s, %s) RETURNING id",
            (
                oid2,
                lid,
                FEE_TIME,
                FETCH,
                NOW + timedelta(hours=2),
                huella_f,
                Json(FEE_DETALLES_20_JSON),
            ),
        ).fetchone()[0]
        conn.commit()
        esc_futuro = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid2,
            fee_observation_id=fid2,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(hours=2, seconds=1),
        )
        persistir_escenario(conn, esc_futuro)
        conn.commit()

        filas = leer_escenarios(conn, [lid], as_of=corte)
        assert len(filas) == 1
        pk = conn.execute(
            "SELECT id FROM estimacion_escenario WHERE listing_id = %s"
            " AND observed_at <= %s ORDER BY observed_at DESC LIMIT 1",
            (lid, corte),
        ).fetchone()[0]
        assert filas[0].id == pk
        assert filas[0].observed_at <= corte
        assert Decimal(str(filas[0].contribucion)) == Decimal("42.5000")

        vencidas = leer_escenarios(conn, [lid], as_of=NOW + timedelta(hours=7))
        assert len(vencidas) == 1
        assert vencidas[0].estado == "desactualizada"
        assert vencidas[0].contribucion is None
        assert vencidas[0].contribucion_pct is None
        assert vencidas[0].moneda is None
        assert "oferta_desactualizada" in vencidas[0].motivos


@_skip_db
def test_politica_cero_o_multiple_sin_elegir():
    from app.estimacion_repository import resolver_politica_aplicable

    with db_estimacion() as conn:
        conn.execute(
            "INSERT INTO estimacion_politica_version"
            " (created_at, label, universo, formula_version, settings, valid_from, valid_to)"
            " VALUES (%s, 'dup-a', 'amazon_mx/fba', 'S3', %s, '2026-01-01', NULL)",
            (FETCH, Json(POLITICA_FBA_MX_SELLADA)),
        )
        conn.execute(
            "INSERT INTO estimacion_politica_version"
            " (created_at, label, universo, formula_version, settings, valid_from, valid_to)"
            " VALUES (%s, 'dup-b', 'amazon_mx/fba', 'S3', %s, '2026-01-01', NULL)",
            (FETCH, Json(POLITICA_FBA_MX_SELLADA)),
        )
        conn.commit()
        r = resolver_politica_aplicable(
            conn,
            fecha=VALORACION,
            universo="amazon_mx/fba",
            observed_at=NOW,
        )
        assert r.politica is None
        assert r.motivo == "politica_ambigua"


@_skip_db
def test_politica_no_vigente():
    from app.estimacion_repository import resolver_politica_aplicable

    with db_estimacion() as conn:
        r = resolver_politica_aplicable(
            conn,
            fecha=date(2025, 1, 1),
            universo="amazon_mx/fba",
            observed_at=NOW,
        )
        assert r.politica is None
        assert r.motivo == "politica_no_vigente"


@_skip_db
def test_politica_ausente():
    from app.estimacion_repository import resolver_politica_aplicable

    with db_estimacion() as conn:
        r = resolver_politica_aplicable(
            conn,
            fecha=VALORACION,
            universo="amazon_us/fba",
            observed_at=NOW,
        )
        assert r.politica is None
        assert r.motivo == "politica_ausente"


@_skip_db
def test_politica_created_at_futura_no_aplica():
    from app.estimacion_repository import resolver_politica_aplicable

    with db_estimacion(incluir_politica_seed=False) as conn:
        conn.execute(
            "INSERT INTO estimacion_politica_version"
            " (created_at, label, universo, formula_version, settings, valid_from, valid_to)"
            " VALUES (%s, 'futura', 'amazon_mx/fba', 'S3', %s, '2026-01-01', NULL)",
            (NOW + timedelta(days=1), Json(POLITICA_FBA_MX_SELLADA)),
        )
        conn.commit()
        r = resolver_politica_aplicable(
            conn,
            fecha=VALORACION,
            universo="amazon_mx/fba",
            observed_at=NOW,
        )
        assert r.politica is None
        assert r.motivo == "politica_no_vigente"


@_skip_db
def test_sembrar_sin_politica_no_fallback():
    from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P-US', 'P-US') RETURNING id"
        ).fetchone()[0]
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'B0US01', 'SS-US-1') RETURNING id",
            (pid,),
        ).fetchone()[0]
        _sembrar_costo(conn, pid)
        huella = f"fba:116.0000:USD:{FETCH.isoformat()}"
        oid = conn.execute(
            "INSERT INTO estimacion_oferta_observation"
            " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
            " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
            " VALUES (%s, 'amazon_us', 'SS-US-1', 'B0US01', 'fba', 116.0000, 'USD',"
            " %s, %s, 'evt-sin-pol', '{}'::jsonb, %s) RETURNING id",
            (lid, FETCH, NOW, huella),
        ).fetchone()[0]
        fid = conn.execute(
            "INSERT INTO estimacion_fee_observation"
            " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
            " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
            " fetched_at, observed_at, estado, source_event_id, canonical_input,"
            " context_fingerprint, fee_details)"
            " VALUES (%s, %s, 'amazon_us', 'SS-US-1', 'B0US01', 'fba', 116.0000, 'USD',"
            " 15.0000, %s, %s, %s, 'success', 'evt-fee-sin-pol', '{}'::jsonb, %s, %s) RETURNING id",
            (oid, lid, FEE_TIME, FETCH, NOW, huella, Json(FEE_DETALLES_15_JSON)),
        ).fetchone()[0]
        conn.commit()
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        assert esc.politica_version_id is None
        assert esc.estado == "incompleta"
        assert "politica_ausente" in esc.motivos
        persistir_escenario(conn, esc)
        conn.commit()
        fila = conn.execute(
            "SELECT politica_version_id, estado FROM estimacion_escenario WHERE listing_id = %s",
            (lid,),
        ).fetchone()
        assert fila == (None, "incompleta")


@_skip_db
def test_fee_asociado_a_otra_oferta():
    from app.estimacion_repository import sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid1 = _sembrar_oferta(conn, lid, evento="evt-oferta-1")
        oid2 = _sembrar_oferta(
            conn, lid, evento="evt-oferta-2", observed_at=NOW + timedelta(minutes=1)
        )
        fid = _insertar_fee(
            conn,
            oid=oid1,
            lid=lid,
            total="15.0000",
            detalles_json=FEE_DETALLES_15_JSON,
            evento="evt-fee-1",
        )
        conn.commit()
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid2,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(minutes=2),
        )
        assert esc.estado == "incompleta"
        assert "fee_incompatible" in esc.motivos


@_skip_db
def test_fee_detalle_corrupto():
    from app.estimacion_repository import sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid)
        fid = _insertar_fee(
            conn,
            oid=oid,
            lid=lid,
            total="15.0000",
            detalles_json=[{"fee_type": "ReferralFee", "final_fee": "no-es-decimal"}],
            evento="evt-fee-corrupto",
        )
        conn.commit()
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        assert esc.estado == "incompleta"
        assert "fee_incompatible" in esc.motivos


@_skip_db
def test_contribucion_cero_preservada_canonical_y_db():
    from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid, amount="82.5000")
        oid = _sembrar_oferta(conn, lid, evento="evt-cero")
        fid = _insertar_fee(
            conn,
            oid=oid,
            lid=lid,
            total="15.0000",
            detalles_json=FEE_DETALLES_15_JSON,
            evento="evt-fee-cero",
        )
        conn.commit()
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        assert esc.estado == "disponible"
        assert esc.contribucion == Decimal("0.0000")
        assert esc.canonical_input["resultado"]["contribucion"] == "0.0000"
        persistir_escenario(conn, esc)
        conn.commit()
        fila = conn.execute(
            "SELECT contribucion, contribucion_pct FROM estimacion_escenario WHERE listing_id = %s",
            (lid,),
        ).fetchone()
        assert Decimal(str(fila[0])) == Decimal("0.0000")
        assert Decimal(str(fila[1])) == Decimal("0.0000")


@_skip_db
def test_fx_corregido_crea_evento_nuevo():
    from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P-USD', 'P-USD') RETURNING id"
        ).fetchone()[0]
        lid = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'B0USD01', 'SS-USD-1') RETURNING id",
            (pid,),
        ).fetchone()[0]
        run_id = conn.execute(
            "INSERT INTO ingest_run "
            "(source, started_at, finished_at, rows_written, rows_skipped, ok)"
            " VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE) RETURNING id",
            (FETCH, FETCH),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id) VALUES (%s, 680.0000, 'MXN', FALSE, '2026-01-01', %s)",
            (pid, run_id),
        )
        conn.execute(
            "INSERT INTO estimacion_politica_version"
            " (created_at, label, universo, formula_version, settings, valid_from, valid_to)"
            " VALUES (%s, 'usd-fba', 'amazon_us/fba', 'S3', %s, '2026-01-01', NULL)",
            (FETCH, Json(POLITICA_USD_AC5)),
        )
        conn.execute(
            "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, ingest_run_id)"
            " VALUES ('2026-09-07', 'USD', 'MXN', 17.00000000, %s)",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, ingest_run_id)"
            " VALUES ('2026-09-08', 'USD', 'MXN', 18.00000000, %s)",
            (run_id,),
        )
        huella = f"fba:100.0000:USD:{FETCH.isoformat()}"
        oid = conn.execute(
            "INSERT INTO estimacion_oferta_observation"
            " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
            " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
            " VALUES (%s, 'amazon_us', 'SS-USD-1', 'B0USD01', 'fba', 100.0000, 'USD',"
            " %s, %s, 'evt-usd', '{}'::jsonb, %s) RETURNING id",
            (lid, FETCH, NOW, huella),
        ).fetchone()[0]
        fid = conn.execute(
            "INSERT INTO estimacion_fee_observation"
            " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
            " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
            " fetched_at, observed_at, estado, source_event_id, canonical_input,"
            " context_fingerprint, fee_details)"
            " VALUES (%s, %s, 'amazon_us', 'SS-USD-1', 'B0USD01', 'fba', 100.0000, 'USD',"
            " 15.0000, %s, %s, %s, 'success', 'evt-fee-usd', '{}'::jsonb, %s, %s) RETURNING id",
            (oid, lid, FEE_TIME, FETCH, NOW, huella, Json(FEE_DETALLES_15_JSON)),
        ).fetchone()[0]
        conn.commit()

        esc1 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=date(2026, 9, 7),
            observed_at=NOW + timedelta(seconds=1),
        )
        persistir_escenario(conn, esc1)
        conn.commit()

        esc2 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=date(2026, 9, 8),
            observed_at=NOW + timedelta(seconds=2),
        )
        assert esc1.source_event_id != esc2.source_event_id
        persistir_escenario(conn, esc2)
        conn.commit()
        n = conn.execute(
            "SELECT count(*) FROM estimacion_escenario WHERE listing_id = %s", (lid,)
        ).fetchone()[0]
        assert n == 2


@_skip_db
def test_migracion_escenario_source_event_id():
    assert "source_event_id" in (ROOT / "migrations" / "0028_estimacion_venta.sql").read_text()


@_skip_db
def test_canonical_incluye_validacion_costo():
    from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento="evt-canonical-costo")
        fid = _insertar_fee(
            conn, oid=oid, lid=lid, total="15.0000", detalles_json=FEE_DETALLES_15_JSON
        )
        conn.commit()
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        assert esc.costo_validation_run_id is not None
        assert esc.costo_validated_at is not None
        assert esc.canonical_input["costo_validation_run_id"] == esc.costo_validation_run_id
        assert esc.canonical_input["costo_includes_tax"] is False
        persistir_escenario(conn, esc)
        conn.commit()


@_skip_db
def test_ac6_dos_listings_mismo_producto_precios_distintos():
    from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('P-SHARED', 'P-SHARED') RETURNING id"
        ).fetchone()[0]
        lid1 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST01', 'SS-MX-1') RETURNING id",
            (pid,),
        ).fetchone()[0]
        lid2 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0EST02', 'SS-MX-2') RETURNING id",
            (pid,),
        ).fetchone()[0]
        _sembrar_costo(conn, pid)
        oid1 = _sembrar_oferta(conn, lid1, evento="evt-ac6-a")
        oid2 = conn.execute(
            "INSERT INTO estimacion_oferta_observation"
            " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
            " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
            " VALUES (%s, 'amazon_mx', 'SS-MX-2', 'B0EST02', 'fba', 120.0000, 'MXN',"
            " %s, %s, 'evt-ac6-b', '{}'::jsonb, %s) RETURNING id",
            (lid2, FETCH, NOW, f"fba:120.0000:MXN:{FETCH.isoformat()}"),
        ).fetchone()[0]
        fid1 = _insertar_fee(
            conn, oid=oid1, lid=lid1, total="15.0000", detalles_json=FEE_DETALLES_15_JSON
        )
        fid2 = conn.execute(
            "INSERT INTO estimacion_fee_observation"
            " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
            " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
            " fetched_at, observed_at, estado, source_event_id, canonical_input,"
            " context_fingerprint, fee_details)"
            " VALUES (%s, %s, 'amazon_mx', 'SS-MX-2', 'B0EST02', 'fba', 120.0000, 'MXN',"
            " 16.0000, %s, %s, %s, 'success', 'evt-fee-ac6-b', '{}'::jsonb, %s, %s) RETURNING id",
            (
                oid2,
                lid2,
                FEE_TIME,
                FETCH,
                NOW,
                f"fba:120.0000:MXN:{FETCH.isoformat()}",
                Json(FEE_DETALLES_16_JSON),
            ),
        ).fetchone()[0]
        conn.commit()
        esc1 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid1,
            oferta_observation_id=oid1,
            fee_observation_id=fid1,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        esc2 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid2,
            oferta_observation_id=oid2,
            fee_observation_id=fid2,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=2),
        )
        assert esc1.source_event_id != esc2.source_event_id
        assert esc1.contribucion != esc2.contribucion
        persistir_escenario(conn, esc1)
        persistir_escenario(conn, esc2)
        conn.commit()
        filas = conn.execute(
            "SELECT listing_id, contribucion FROM estimacion_escenario ORDER BY listing_id"
        ).fetchall()
        assert len(filas) == 2
        assert filas[0][0] != filas[1][0]
        assert Decimal(str(filas[0][1])) != Decimal(str(filas[1][1]))


def _entrada_desde_canonical(canonical: dict) -> EntradaCalculo:
    entrada = canonical.get("entrada") or {}
    detalles_raw = entrada.get("fee_detalles") or []

    def detalle_desde_json(raw: dict) -> DetalleFee:
        return DetalleFee(
            fee_type=raw["fee_type"],
            final_fee=Decimal(raw["final_fee"]),
            tax_amount=Decimal(raw["tax_amount"]) if raw.get("tax_amount") else None,
            included_fee_details=tuple(
                detalle_desde_json(hijo) for hijo in raw.get("included_fee_details") or []
            ),
        )

    detalles = tuple(detalle_desde_json(raw) for raw in detalles_raw)
    return EntradaCalculo(
        precio_bruto=Decimal(entrada["precio_bruto"]) if entrada.get("precio_bruto") else None,
        precio_moneda=entrada.get("precio_moneda"),
        costo_original=Decimal(entrada["costo_original"])
        if entrada.get("costo_original")
        else None,
        costo_moneda=entrada.get("costo_moneda"),
        costo_includes_tax=entrada.get("costo_includes_tax"),
        fee_total=Decimal(entrada["fee_total"]) if entrada.get("fee_total") else None,
        fee_detalles=detalles,
        fee_estado=entrada.get("fee_estado"),
        fx_rate=Decimal(entrada["fx_rate"]) if entrada.get("fx_rate") else None,
        fx_rate_date=(
            date.fromisoformat(entrada["fx_rate_date"]) if entrada.get("fx_rate_date") else None
        ),
        fx_base=entrada.get("fx_base"),
        fx_quote=entrada.get("fx_quote"),
        fx_source=entrada.get("fx_source"),
        moneda_base=entrada.get("precio_moneda"),
        motivos_entrada=tuple(entrada.get("motivos_entrada") or ()),
    )


@_skip_db
def test_ac11_reproducible_tras_cambio_fuentes():
    from app.estimacion_repository import (
        leer_escenarios,
        persistir_escenario,
        sembrar_escenario_desde_refs,
    )

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento="evt-ac11-base")
        fid = _insertar_fee(
            conn, oid=oid, lid=lid, total="15.0000", detalles_json=FEE_DETALLES_15_JSON
        )
        conn.commit()
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(seconds=1),
        )
        persistir_escenario(conn, esc)
        conn.commit()
        canonical_antes = dict(esc.canonical_input)
        contrib_antes = esc.contribucion

        conn.execute(
            "INSERT INTO ingest_run "
            "(source, started_at, finished_at, rows_written, rows_skipped, ok)"
            " VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE)",
            (NOW, NOW),
        )
        conn.execute(
            "UPDATE sku_cost SET valid_to = '2026-09-09' WHERE product_id = %s",
            (pid,),
        )
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id) VALUES (%s, 99.0000, 'MXN', FALSE, '2026-09-09',"
            " (SELECT id FROM ingest_run ORDER BY id DESC LIMIT 1))",
            (pid,),
        )
        conn.execute(
            "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, ingest_run_id)"
            " VALUES ('2026-09-08', 'USD', 'MXN', 99.00000000,"
            " (SELECT id FROM ingest_run ORDER BY id DESC LIMIT 1))"
        )
        conn.commit()

        fila_db = conn.execute(
            "SELECT canonical_input, contribucion FROM estimacion_escenario WHERE listing_id = %s",
            (lid,),
        ).fetchone()
        assert dict(fila_db[0]) == canonical_antes
        assert Decimal(str(fila_db[1])) == contrib_antes

        leido = leer_escenarios(conn, [lid], as_of=NOW + timedelta(hours=1))[0]
        assert leido.contribucion == contrib_antes
        assert leido.canonical_input == canonical_antes

        politica = conn.execute(
            "SELECT id, label, universo, formula_version, settings,"
            " valid_from, valid_to, created_at"
            " FROM estimacion_politica_version LIMIT 1"
        ).fetchone()
        pol = PoliticaCalculo(
            politica_version_id=politica[0],
            label=politica[1],
            universo=politica[2],
            formula_version=politica[3],
            settings=dict(politica[4]),
            valid_from=politica[5],
            valid_to=politica[6],
            created_at=politica[7],
        )
        recalc = calcular_contribucion(
            _entrada_desde_canonical(canonical_antes),
            pol,
            valoracion_date=VALORACION,
        )
        assert recalc.contribucion == contrib_antes


@_skip_db
def test_transicion_disponible_a_ausente_reader_null():
    from app.estimacion_repository import (
        leer_escenarios,
        persistir_escenario,
        sembrar_escenario_desde_refs,
        sembrar_escenario_negativo_transicion,
    )

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento="evt-previo-ok")
        fid = _insertar_fee(
            conn, oid=oid, lid=lid, total="15.0000", detalles_json=FEE_DETALLES_15_JSON
        )
        conn.commit()
        esc_ok = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        persistir_escenario(conn, esc_ok)
        conn.commit()

        huella_vacia = "abc123" * 5 + "ab"
        esc_neg = sembrar_escenario_negativo_transicion(
            conn,
            listing_id=lid,
            motivo="oferta_ausente",
            snapshot_huella=huella_vacia[:32],
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(minutes=5),
        )
        assert esc_neg is not None
        assert esc_neg.oferta_observation_id is None
        persistir_escenario(conn, esc_neg)
        conn.commit()

        leido = leer_escenarios(conn, [lid], as_of=NOW + timedelta(hours=1))[0]
        assert leido.estado == "incompleta"
        assert leido.contribucion is None
        assert "oferta_ausente" in leido.motivos


@_skip_db
def test_transicion_historica_no_usa_contexto_futuro():
    from app.estimacion_repository import (
        persistir_escenario,
        sembrar_escenario_desde_refs,
        sembrar_escenario_negativo_transicion,
    )

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento="evt-contexto-futuro")
        fid = _insertar_fee(
            conn, oid=oid, lid=lid, total="15.0000", detalles_json=FEE_DETALLES_15_JSON
        )
        conn.commit()
        futuro = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(hours=1),
        )
        persistir_escenario(conn, futuro)
        conn.commit()

        negativa = sembrar_escenario_negativo_transicion(
            conn,
            listing_id=lid,
            motivo="oferta_ausente",
            snapshot_huella="snapshot-historico",
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(minutes=30),
        )

        assert negativa is None


@_skip_db
def test_transicion_mismo_snapshot_no_rejuvenece():
    from app.estimacion_repository import persistir_escenario, sembrar_escenario_negativo_transicion

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento="evt-contexto-previo")
        fid = _insertar_fee(
            conn, oid=oid, lid=lid, total="15.0000", detalles_json=FEE_DETALLES_15_JSON
        )
        conn.commit()
        from app.estimacion_repository import sembrar_escenario_desde_refs

        esc_ok = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        persistir_escenario(conn, esc_ok)
        conn.commit()

        huella = "deadbeef" * 4
        params = dict(
            listing_id=lid,
            motivo="oferta_ausente",
            snapshot_huella=huella,
            valoracion_date=VALORACION,
        )
        esc1 = sembrar_escenario_negativo_transicion(
            conn, observed_at=NOW + timedelta(minutes=1), **params
        )
        esc2 = sembrar_escenario_negativo_transicion(
            conn, observed_at=NOW + timedelta(hours=2), **params
        )
        assert esc1 is not None and esc2 is not None
        assert esc1.source_event_id == esc2.source_event_id
        r1 = persistir_escenario(conn, esc1)
        r2 = persistir_escenario(conn, esc2)
        conn.commit()
        assert r1.reutilizada is False
        assert r2.reutilizada is True

        fid_recuperado = _insertar_fee(
            conn,
            oid=oid,
            lid=lid,
            total="16.0000",
            detalles_json=FEE_DETALLES_16_JSON,
            observed_at=NOW + timedelta(minutes=30),
            fees_estimated_at=NOW + timedelta(minutes=20),
            evento="evt-fee-recuperado",
        )
        conn.commit()
        recuperado = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid_recuperado,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(minutes=31),
        )
        persistir_escenario(conn, recuperado)
        conn.commit()

        esc3 = sembrar_escenario_negativo_transicion(
            conn, observed_at=NOW + timedelta(minutes=40), **params
        )
        esc4 = sembrar_escenario_negativo_transicion(
            conn, observed_at=NOW + timedelta(minutes=50), **params
        )
        assert esc3 is not None and esc4 is not None
        assert esc3.source_event_id != esc1.source_event_id
        assert esc4.source_event_id == esc3.source_event_id
        r3 = persistir_escenario(conn, esc3)
        r4 = persistir_escenario(conn, esc4)
        conn.commit()
        assert r3.reutilizada is False
        assert r4.reutilizada is True
        n = conn.execute(
            "SELECT count(*) FROM estimacion_escenario WHERE listing_id = %s", (lid,)
        ).fetchone()[0]
        assert n == 4


@_skip_db
@pytest.mark.parametrize(
    ("motivo", "estado"),
    (
        ("oferta_futura", "desactualizada"),
        ("oferta_desactualizada", "desactualizada"),
        ("logistica_fbm_pendiente", "incompleta"),
    ),
)
def test_transiciones_de_oferta_invalidan_contexto_fba_previo(motivo: str, estado: str):
    from app.estimacion_repository import (
        persistir_escenario,
        sembrar_escenario_desde_refs,
        sembrar_escenario_negativo_transicion,
    )

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        oid = _sembrar_oferta(conn, lid, evento=f"evt-contexto-{motivo}")
        fid = _insertar_fee(
            conn,
            oid=oid,
            lid=lid,
            total="15.0000",
            detalles_json=FEE_DETALLES_15_JSON,
            evento=f"evt-fee-{motivo}",
        )
        conn.commit()
        base = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW,
        )
        persistir_escenario(conn, base)
        conn.commit()
        negativa = sembrar_escenario_negativo_transicion(
            conn,
            listing_id=lid,
            motivo=motivo,
            snapshot_huella=f"snapshot-{motivo}",
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(minutes=1),
        )
        assert negativa is not None
        assert negativa.estado == estado
        assert negativa.contribucion is None


@_skip_db
def test_leer_escenarios_rechaza_as_of_naive():
    from app.estimacion_repository import leer_escenarios

    with db_estimacion() as conn, pytest.raises(ValueError, match="zona horaria"):
        leer_escenarios(conn, [1], as_of=datetime(2026, 9, 8, 12, 0, 0))


@_skip_db
def test_sembrar_oferta_futura_desactualizada_sin_total():
    from app.estimacion_repository import sembrar_escenario_desde_refs

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        fetched_futuro = NOW + timedelta(hours=1)
        huella = f"fba:116.0000:MXN:{fetched_futuro.isoformat()}"
        oid = conn.execute(
            "INSERT INTO estimacion_oferta_observation"
            " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
            " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
            " VALUES (%s, 'amazon_mx', 'SS-MX-1', 'B0EST01', 'fba', 116.0000, 'MXN',"
            " %s, %s, 'evt-futura', '{}'::jsonb, %s) RETURNING id",
            (lid, fetched_futuro, fetched_futuro, huella),
        ).fetchone()[0]
        fid = _insertar_fee(
            conn,
            oid=oid,
            lid=lid,
            total="15.0000",
            detalles_json=FEE_DETALLES_15_JSON,
            observed_at=fetched_futuro,
            huella=huella,
            fees_estimated_at=fetched_futuro,
        )
        conn.commit()
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=NOW + timedelta(minutes=30),
        )
        assert esc.estado == "desactualizada"
        assert esc.contribucion is None
        assert "oferta_futura" in esc.motivos


# ---------------------------------------------------------------------------
# (c) ORQUESTACION — pipeline crea escenario tras fee
# ---------------------------------------------------------------------------

_DDL_BRIDGE = """
CREATE TABLE amazon_listing_prices (
    id INTEGER PRIMARY KEY,
    seller_sku TEXT NOT NULL,
    asin TEXT,
    marketplace_id TEXT NOT NULL,
    marketplace_name TEXT,
    price REAL,
    fulfillment_channel TEXT,
    fetched_at TEXT,
    UNIQUE(seller_sku, marketplace_id)
);
"""


def _snapshot_bridge(ruta: Path, filas: list[tuple]) -> Path:
    con = sqlite3.connect(ruta)
    con.executescript(_DDL_BRIDGE)
    con.executemany(
        "INSERT INTO amazon_listing_prices"
        " (seller_sku, asin, marketplace_id, marketplace_name, price,"
        " fulfillment_channel, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        filas,
    )
    con.commit()
    con.close()
    return ruta


@_skip_db
def test_pipeline_crea_escenario_tras_fee(tmp_path):
    from app.estimacion_fees import ProductFeesClient
    from app.estimacion_ingest import ejecutar_ingesta
    from app.estimacion_insumos import (
        FilaOfertaBridge,
        OfertaResuelta,
        construir_canonical_input,
        construir_context_fingerprint,
        construir_source_event_id,
    )

    snap = _snapshot_bridge(
        tmp_path / "bridge.db",
        [
            (
                "SS-MX-1",
                "B0EST01",
                "A1AM78C64UM0Y8",
                "amazon_mx",
                116.0,
                "AMAZON_NA",
                FETCH.isoformat(),
            ),
        ],
    )

    def _respuesta_exito(oferta):
        return {
            "payload": {
                "FeesEstimateResult": {
                    "Status": "Success",
                    "FeesEstimateIdentifier": {
                        "IdType": "SellerSKU",
                        "IdValue": oferta.seller_sku,
                        "SellerInputIdentifier": oferta.source_event_id,
                        "MarketplaceId": "A1AM78C64UM0Y8",
                        "IsAmazonFulfilled": True,
                        "PriceToEstimateFees": {
                            "ListingPrice": {
                                "CurrencyCode": "MXN",
                                "Amount": str(oferta.price_amount),
                            }
                        },
                    },
                    "FeesEstimate": {
                        "TimeOfFeesEstimation": FEE_TIME.isoformat(),
                        "TotalFeesEstimate": {"CurrencyCode": "MXN", "Amount": "15.00"},
                        "FeeDetailList": [
                            {
                                "FeeType": "ReferralFee",
                                "FeeAmount": {"CurrencyCode": "MXN", "Amount": "10.00"},
                                "FinalFee": {"CurrencyCode": "MXN", "Amount": "10.00"},
                            },
                            {
                                "FeeType": "FBAFees",
                                "FeeAmount": {"CurrencyCode": "MXN", "Amount": "5.00"},
                                "FinalFee": {"CurrencyCode": "MXN", "Amount": "5.00"},
                            },
                        ],
                    },
                }
            }
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(
                200,
                json={"access_token": "tok", "expires_in": 3600},
            )
        return httpx.Response(200, json=_respuesta_exito(_oferta_fixture()))

    def _oferta_fixture():
        fila = FilaOfertaBridge(
            seller_sku="SS-MX-1",
            asin="B0EST01",
            marketplace_id="A1AM78C64UM0Y8",
            marketplace_name="amazon_mx",
            price=116.0,
            fulfillment_channel="AMAZON_NA",
            fetched_at=FETCH.isoformat(),
        )
        canon = construir_canonical_input(fila, "amazon_mx", "fba", Decimal("116.0000"), "MXN")
        return OfertaResuelta(
            listing_id=1,
            platform="amazon_mx",
            seller_sku="SS-MX-1",
            asin="B0EST01",
            canal="fba",
            price_amount=Decimal("116.0000"),
            price_currency="MXN",
            fetched_at=FETCH,
            canonical_input=canon,
            context_fingerprint=construir_context_fingerprint(
                "fba", Decimal("116.0000"), "MXN", FETCH
            ),
            source_event_id=construir_source_event_id(canon),
        )

    client = ProductFeesClient(
        credentials={
            "lwa_app_id": "id",
            "lwa_client_secret": "sec",
            "refresh_token": "ref",
        },
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        clock=lambda: 0.0,
    )

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        conn.commit()

        resultado = ejecutar_ingesta(conn, ruta_sqlite=snap, now_utc=NOW, client=client)
        conn.commit()
        assert resultado.escenarios_nuevos >= 1
        n_esc = conn.execute("SELECT count(*) FROM estimacion_escenario").fetchone()[0]
        assert n_esc >= 1
        esc = conn.execute(
            "SELECT estado, contribucion, contribucion_pct FROM estimacion_escenario"
            " WHERE listing_id = %s ORDER BY observed_at DESC LIMIT 1",
            (lid,),
        ).fetchone()
        assert esc[0] == "disponible"
        assert esc[1] is not None
        assert Decimal(str(esc[1])) == Decimal("42.5000")


@_skip_db
def test_pipeline_no_http_dentro_de_transaccion(tmp_path):
    import psycopg

    from app.estimacion_fees import ProductFeesClient
    from app.estimacion_ingest import ejecutar_ingesta
    from app.estimacion_insumos import (
        FilaOfertaBridge,
        OfertaResuelta,
        construir_canonical_input,
        construir_context_fingerprint,
        construir_source_event_id,
    )

    snap = _snapshot_bridge(
        tmp_path / "bridge.db",
        [
            (
                "SS-MX-1",
                "B0EST01",
                "A1AM78C64UM0Y8",
                "amazon_mx",
                116.0,
                "AMAZON_NA",
                FETCH.isoformat(),
            ),
        ],
    )

    def _oferta_fixture():
        fila = FilaOfertaBridge(
            seller_sku="SS-MX-1",
            asin="B0EST01",
            marketplace_id="A1AM78C64UM0Y8",
            marketplace_name="amazon_mx",
            price=116.0,
            fulfillment_channel="AMAZON_NA",
            fetched_at=FETCH.isoformat(),
        )
        canon = construir_canonical_input(fila, "amazon_mx", "fba", Decimal("116.0000"), "MXN")
        return OfertaResuelta(
            listing_id=1,
            platform="amazon_mx",
            seller_sku="SS-MX-1",
            asin="B0EST01",
            canal="fba",
            price_amount=Decimal("116.0000"),
            price_currency="MXN",
            fetched_at=FETCH,
            canonical_input=canon,
            context_fingerprint=construir_context_fingerprint(
                "fba", Decimal("116.0000"), "MXN", FETCH
            ),
            source_event_id=construir_source_event_id(canon),
        )

    def _respuesta_exito(oferta):
        return {
            "payload": {
                "FeesEstimateResult": {
                    "Status": "Success",
                    "FeesEstimateIdentifier": {
                        "IdType": "SellerSKU",
                        "IdValue": oferta.seller_sku,
                        "SellerInputIdentifier": oferta.source_event_id,
                        "MarketplaceId": "A1AM78C64UM0Y8",
                        "IsAmazonFulfilled": True,
                        "PriceToEstimateFees": {
                            "ListingPrice": {
                                "CurrencyCode": "MXN",
                                "Amount": str(oferta.price_amount),
                            }
                        },
                    },
                    "FeesEstimate": {
                        "TimeOfFeesEstimation": FEE_TIME.isoformat(),
                        "TotalFeesEstimate": {"CurrencyCode": "MXN", "Amount": "15.00"},
                        "FeeDetailList": [
                            {
                                "FeeType": "ReferralFee",
                                "FeeAmount": {"CurrencyCode": "MXN", "Amount": "10.00"},
                                "FinalFee": {"CurrencyCode": "MXN", "Amount": "10.00"},
                            },
                            {
                                "FeeType": "FBAFees",
                                "FeeAmount": {"CurrencyCode": "MXN", "Amount": "5.00"},
                                "FinalFee": {"CurrencyCode": "MXN", "Amount": "5.00"},
                            },
                        ],
                    },
                }
            }
        }

    tx_durante_http: list[bool] = []

    client = ProductFeesClient(
        credentials={
            "lwa_app_id": "id",
            "lwa_client_secret": "sec",
            "refresh_token": "ref",
        },
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        ),
        sleep=lambda _: None,
        clock=lambda: 0.0,
    )

    with db_estimacion() as conn:
        pid, lid = _sembrar_listing(conn)
        _sembrar_costo(conn, pid)
        conn.commit()

        class _ConnProxy:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def transaction(self, *args, **kwargs):
                tx_durante_http.append(
                    self._inner.info.transaction_status != psycopg.pq.TransactionStatus.IDLE
                )
                return self._inner.transaction(*args, **kwargs)

        proxy = _ConnProxy(conn)
        payload = _respuesta_exito(_oferta_fixture())

        def handler_full(request: httpx.Request) -> httpx.Response:
            tx_durante_http.append(
                conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE
            )
            if request.url.host == "api.amazon.com":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
            return httpx.Response(200, json=payload)

        client._transport = httpx.MockTransport(handler_full)  # type: ignore[attr-defined]
        ejecutar_ingesta(proxy, ruta_sqlite=snap, now_utc=NOW, client=client)
        conn.commit()
        assert not any(tx_durante_http), f"TX abierta durante HTTP: {tx_durante_http}"


@_skip_db
def test_b3_recorrido_persistido_proyeccion_s5_ac14():
    """B.3: siembra→persist→reader→proyeccion S5 en DB aislada (0028+politica).

    Numeros del caso vivo listing 1213 (P=988, C=341, F=191.76). Demuestra
    snapshot_id y procedencia real sin desplegar produccion.
    """
    import json

    from app.estimacion_proyeccion import proyeccion_s5
    from app.estimacion_repository import (
        leer_escenarios,
        persistir_escenario,
        sembrar_escenario_desde_refs,
    )

    p = Decimal("988.0000")
    c = Decimal("341.0000")
    f_total = Decimal("191.7600")
    fees_json = [
        {"fee_type": "ReferralFee", "final_fee": "127.7600", "tax_amount": None},
        {"fee_type": "FBAFees", "final_fee": "64.0000", "tax_amount": None},
    ]
    fetch_oferta = datetime(2026, 9, 8, 18, 35, 40, tzinfo=UTC)
    fee_time = datetime(2026, 9, 8, 19, 10, 55, tzinfo=UTC)
    obs_oferta = datetime(2026, 9, 8, 18, 40, 0, tzinfo=UTC)
    obs_fee = datetime(2026, 9, 8, 19, 11, 0, tzinfo=UTC)
    obs_esc = obs_fee + timedelta(microseconds=1)

    with db_estimacion(prefijo="orbit_margen_b3") as conn:
        pid, lid = _sembrar_listing(
            conn, odoo_sku="SK-YBQX", asin="B0C8RVWG4F", seller_sku="SK-YBQX-XQWV"
        )
        run_id = conn.execute(
            "INSERT INTO ingest_run (source, started_at, finished_at, rows_written,"
            " rows_skipped, ok) VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE)"
            " RETURNING id",
            (FETCH, FETCH),
        ).fetchone()[0]
        cost_id = conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id) VALUES (%s, %s, 'MXN', FALSE, '2026-08-18', %s)"
            " RETURNING id",
            (pid, c, run_id),
        ).fetchone()[0]
        huella = f"fba:{p}:MXN:{fetch_oferta.isoformat()}"
        oid = conn.execute(
            "INSERT INTO estimacion_oferta_observation"
            " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
            " fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
            " VALUES (%s, 'amazon_mx', 'SK-YBQX-XQWV', 'B0C8RVWG4F', 'fba', %s, 'MXN',"
            " %s, %s, 'evt-b3-oferta', '{}'::jsonb, %s) RETURNING id",
            (lid, p, fetch_oferta, obs_oferta, huella),
        ).fetchone()[0]
        fid = conn.execute(
            "INSERT INTO estimacion_fee_observation"
            " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
            " quoted_price_amount, quoted_price_currency, total_fees, fees_estimated_at,"
            " fetched_at, observed_at, estado, source_event_id, canonical_input,"
            " context_fingerprint, fee_details)"
            " VALUES (%s, %s, 'amazon_mx', 'SK-YBQX-XQWV', 'B0C8RVWG4F', 'fba', %s, 'MXN',"
            " %s, %s, %s, %s, 'success', 'evt-b3-fee', '{}'::jsonb, %s, %s) RETURNING id",
            (oid, lid, p, f_total, fee_time, obs_fee, obs_fee, huella, Json(fees_json)),
        ).fetchone()[0]
        conn.commit()
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=VALORACION,
            observed_at=obs_esc,
        )
        pers = persistir_escenario(conn, esc)
        conn.commit()
        leidos = leer_escenarios(conn, [lid], as_of=obs_esc + timedelta(minutes=1))
        assert len(leidos) == 1
        leido = leidos[0]
        assert leido.id == pers.id
        assert leido.procedencia is not None
        assert leido.procedencia.oferta_fetched_at == fetch_oferta
        assert leido.procedencia.fee_fees_estimated_at == fee_time
        assert leido.procedencia.costo_valid_from == date(2026, 8, 18)
        assert leido.procedencia.politica_valid_from == date(2026, 1, 1)
        sobre = proyeccion_s5(leido)
        assert sobre["snapshot_id"] == pers.id
        assert sobre["estado"] == "disponible"
        assert Decimal(sobre["contribucion"]) == Decimal("297.6710")
        assert Decimal(sobre["contribucion_pct"]) == Decimal("34.9492")
        por_nombre = {c["nombre"]: c for c in sobre["componentes"]}
        assert por_nombre["precio_bruto"]["fecha_fuente"] == fetch_oferta.isoformat()
        assert por_nombre["precio_bruto"]["observed_at"] == obs_oferta.isoformat()
        assert por_nombre["fee_total"]["fecha_fuente"] == fee_time.isoformat()
        assert por_nombre["costo_original"]["vigencia"] == "2026-08-18"
        assert por_nombre["isr"]["vigencia"] == "2026-01-01"
        assert por_nombre["isr"]["estado"] is None
        evidencia = {
            "modo": "db_aislada_0028",
            "listing_id_simulado": lid,
            "asin": "B0C8RVWG4F",
            "seller_sku": "SK-YBQX-XQWV",
            "snapshot_id": pers.id,
            "sku_cost_id": cost_id,
            "oferta_observation_id": oid,
            "fee_observation_id": fid,
            "contribucion": sobre["contribucion"],
            "contribucion_pct": sobre["contribucion_pct"],
            "procedencia": {
                "oferta_fetched_at": fetch_oferta.isoformat(),
                "fee_fees_estimated_at": fee_time.isoformat(),
                "costo_valid_from": "2026-08-18",
                "politica_valid_from": "2026-01-01",
            },
            "match_hoja_ac14": True,
        }
        dest = ROOT / "docs/evidencia/margen-estimado-01/B.3/recorrido-persistido-1213.json"
        dest.write_text(json.dumps(evidencia, indent=2) + "\n", encoding="utf-8")
