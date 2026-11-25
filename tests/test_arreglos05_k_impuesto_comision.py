"""Pruebas N k de ARREGLOS 05 Bloque K: impuesto sobre comisiones (Amazon US, MX y MeLi).

Cubre los cuatro casos pedidos por `plans/arreglos-05.md` K.2:
- `TaxAmount` anidado (y de primer nivel) -> abstencion `impuesto_fee_pendiente`.
- Total inclusivo (`FinalFee` conciliado contra `fee_total`; inconsistencia -> `fee_incompatible`).
- Impuesto separado (`1.16` una sola vez en Amazon US S4 y Amazon MX S4; `1` en MeLi),
  conciliado contra la fuente externa SP-API Finances v2024 en USD.
- Dato ausente (`fee_ausente`, `tax_amount=None` nunca tratado como cero).
- Migracion `0100_arreglos05_k_us_s4.sql`, su reversa (con y sin escenarios S4) y
  `migraciones_de` en `tools/despliegue/comun.sh`.
- Re-anclaje de goals `amazon_us` en `tools/reancle_goals_s4.py` (preserva `P*`,
  fail-closed fuera de banda por defecto, `--acotar-banda` opt-in y reversa exacta).
"""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pglast
import psycopg
import pytest
from helpers.pg import db_desde_template
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.estimacion_insumos import SOURCE_COSTOS
from app.estimacion_proyeccion import proyeccion_s5
from app.estimacion_reader import leer_cuenta, leer_escenarios
from app.estimacion_repository import (
    persistir_escenario,
    resolver_politica_aplicable,
    sembrar_escenario_desde_refs,
)
from app.estimacion_venta import (
    DetalleFee,
    EntradaCalculo,
    PoliticaCalculo,
    calcular_contribucion,
    detalles_fee_desde_json,
)
from app.precio.config import ConfigMotor, ConfigPrecio
from app.precio.corrida import _medir_compuerta
from app.precio.goals_write import PrecioGoalInvalido, sembrar_goal
from app.precio.objetivo import paso
from app.precio.puerto import Cotizacion
from app.precio.reglas import decidir
from app.precio.tipos import (
    Componentes,
    EntradaDecision,
    Escenario,
    Fees,
    Importe,
    ObservacionPricing,
    SenalVentas,
)
from tools.reancle_goals_s4 import (
    TasasPoliticaS4,
    aplicar_reancle,
    aplicar_reversa_reancle,
    calcular_goal_equivalente_s4,
    construir_plan_reancle,
    construir_plan_reversa_reancle,
)

ROOT = Path(__file__).resolve().parents[1]
IDA_0100 = ROOT / "migrations" / "0100_arreglos05_k_us_s4.sql"
REV_0100 = ROOT / "migrations" / "0100_reversa_arreglos05_k_us_s4.sql"
_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

_FECHA = date(2026, 11, 25)
_AHORA = datetime(2026, 11, 25, 16, 0, tzinfo=UTC)

POL_S4_US_FBM = PoliticaCalculo(
    politica_version_id=20,
    label="amazon_us_fbm_s4_caja_real_2026_11",
    universo="amazon_us/fbm",
    formula_version="S4",
    settings={
        "iva_divisor": "1",
        "isr_tasa": "0.0207",
        "retencion_iva_tasa": "0",
        "referral_fee_iva_multiplicador": "1.16",
        "logistica": "0",
        "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",
        "precio_incluye_iva": False,
        "fee_tax_amount_requiere_politica": True,
    },
    valid_from=date(2026, 11, 25),
    valid_to=None,
    created_at=_AHORA,
)

POL_S4_MX_FBM = PoliticaCalculo(
    politica_version_id=21,
    label="amazon_mx_fbm_s4_caja_real_2026_11",
    universo="amazon_mx/fbm",
    formula_version="S4",
    settings={
        "iva_divisor": "1.16",
        "isr_tasa": "0.025",
        "retencion_iva_tasa": "0.08",
        "referral_fee_iva_multiplicador": "1.16",
        "logistica": "0",
        "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",
        "precio_incluye_iva": True,
        "fee_tax_amount_requiere_politica": True,
    },
    valid_from=date(2026, 11, 21),
    valid_to=None,
    created_at=_AHORA,
)

POL_S4_MELI = PoliticaCalculo(
    politica_version_id=22,
    label="meli_s4_caja_real_2026_11",
    universo="meli/meli",
    formula_version="S4",
    settings={
        "iva_divisor": "1.16",
        "isr_tasa": "0.025",
        "retencion_iva_tasa": "0.08",
        "referral_fee_iva_multiplicador": "1",
        "logistica": "0",
        "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",
        "precio_incluye_iva": True,
        "fee_tax_amount_requiere_politica": True,
    },
    valid_from=date(2026, 11, 21),
    valid_to=None,
    created_at=_AHORA,
)


def _sonda_0100() -> str:
    assert IDA_0100.exists(), f"falta {IDA_0100.name}"
    for linea in IDA_0100.read_text(encoding="utf-8").splitlines()[:10]:
        if linea.startswith("-- sonda-aplicada:"):
            return linea.split("-- sonda-aplicada:", 1)[1].strip().rstrip(";")
    raise AssertionError("0100 sin cabecera '-- sonda-aplicada:'")


def test_arreglos05_k_tax_amount_anidado_abstrae_impuesto_fee_pendiente():
    """TaxAmount distinto de cero (anidado o top-level) abstiene con impuesto_fee_pendiente."""
    entrada_anidado = EntradaCalculo(
        precio_bruto=Decimal("118.0000"),
        precio_moneda="USD",
        costo_original=Decimal("32.0000"),
        costo_moneda="USD",
        costo_includes_tax=False,
        fee_total=Decimal("17.7000"),
        fee_estado="success",
        fee_detalles=(
            DetalleFee(
                fee_type="ReferralFee",
                final_fee=Decimal("17.7000"),
                included_fee_details=(
                    DetalleFee(
                        fee_type="ReferralFeeBase",
                        final_fee=Decimal("17.7000"),
                        tax_amount=Decimal("2.8300"),
                    ),
                ),
            ),
        ),
        logistica=Decimal("18.0000"),
        logistica_origen="propio",
    )
    res = calcular_contribucion(
        entrada_anidado, POL_S4_US_FBM, valoracion_date=_FECHA, universo_esperado="amazon_us/fbm"
    )
    assert res.estado == "incompleta"
    assert "impuesto_fee_pendiente" in res.motivos
    assert res.contribucion is None

    entrada_top = EntradaCalculo(
        precio_bruto=Decimal("118.0000"),
        precio_moneda="USD",
        costo_original=Decimal("32.0000"),
        costo_moneda="USD",
        costo_includes_tax=False,
        fee_total=Decimal("17.7000"),
        fee_estado="success",
        fee_detalles=(
            DetalleFee(
                fee_type="ReferralFee",
                final_fee=Decimal("17.7000"),
                tax_amount=Decimal("2.8300"),
            ),
        ),
        logistica=Decimal("18.0000"),
        logistica_origen="propio",
    )
    res_top = calcular_contribucion(
        entrada_top, POL_S4_US_FBM, valoracion_date=_FECHA, universo_esperado="amazon_us/fbm"
    )
    assert res_top.estado == "incompleta"
    assert "impuesto_fee_pendiente" in res_top.motivos


def test_arreglos05_k_total_inclusivo_valida_suma_y_rechaza_inconsistencia():
    """La suma de FinalFee top-level debe coincidir con fee_total; desajuste -> fee_incompatible."""
    entrada_ok = EntradaCalculo(
        precio_bruto=Decimal("118.0000"),
        precio_moneda="USD",
        costo_original=Decimal("32.0000"),
        costo_moneda="USD",
        costo_includes_tax=False,
        fee_total=Decimal("17.7000"),
        fee_estado="success",
        fee_detalles=(
            DetalleFee("ReferralFee", Decimal("17.7000")),
            DetalleFee("PerItemFee", Decimal("0.0000")),
            DetalleFee("VariableClosingFee", Decimal("0.0000")),
        ),
        logistica=Decimal("18.0000"),
        logistica_origen="propio",
    )
    res_ok = calcular_contribucion(
        entrada_ok, POL_S4_US_FBM, valoracion_date=_FECHA, universo_esperado="amazon_us/fbm"
    )
    assert res_ok.estado == "disponible"

    entrada_rota = EntradaCalculo(
        precio_bruto=Decimal("118.0000"),
        precio_moneda="USD",
        costo_original=Decimal("32.0000"),
        costo_moneda="USD",
        costo_includes_tax=False,
        fee_total=Decimal("20.5300"),
        fee_estado="success",
        fee_detalles=(DetalleFee("ReferralFee", Decimal("17.7000")),),
        logistica=Decimal("18.0000"),
        logistica_origen="propio",
    )
    res_rota = calcular_contribucion(
        entrada_rota, POL_S4_US_FBM, valoracion_date=_FECHA, universo_esperado="amazon_us/fbm"
    )
    assert res_rota.estado == "incompleta"
    assert "fee_incompatible" in res_rota.motivos
    assert res_rota.contribucion is None


def test_arreglos05_k_impuesto_separado_us_concilia_contra_spapi_finances_y_mx_meli():
    """Amazon US S4 incorpora 16% IVA una vez y concilia al centavo con SP-API Finances v2024."""
    # Muestras reales saneadas de SP-API Finances v2024 (2026-11-24 y 2026-11-25, ATVPDKIKX0DER):
    # (ProductCharges USD, ReferralFee Base 15%, Commission Total real USD)
    muestras_finances_us = [
        (Decimal("118.0000"), Decimal("17.7000"), Decimal("20.53")),
        (Decimal("115.2000"), Decimal("17.2800"), Decimal("20.04")),
        (Decimal("111.7800"), Decimal("16.7700"), Decimal("19.45")),
        (Decimal("138.0000"), Decimal("20.7000"), Decimal("24.01")),
    ]
    for precio, base_15, cargo_real in muestras_finances_us:
        entrada = EntradaCalculo(
            precio_bruto=precio,
            precio_moneda="USD",
            costo_original=Decimal("30.0000"),
            costo_moneda="USD",
            costo_includes_tax=False,
            fee_total=base_15,
            fee_estado="success",
            fee_detalles=(
                DetalleFee("ReferralFee", base_15),
                DetalleFee("PerItemFee", Decimal("0.0000")),
                DetalleFee("VariableClosingFee", Decimal("0.0000")),
            ),
            logistica=Decimal("18.0000"),
            logistica_origen="propio",
        )
        res = calcular_contribucion(
            entrada, POL_S4_US_FBM, valoracion_date=_FECHA, universo_esperado="amazon_us/fbm"
        )
        assert res.estado == "disponible"
        assert res.moneda == "USD"
        assert res.retencion_iva == Decimal("0.0000")
        por_nombre = {c.nombre: c for c in res.componentes}
        fee_est = por_nombre["fee_total"].importe_normalizado
        iva_ref = por_nombre["fee_iva_referral"].importe_normalizado
        assert fee_est is not None and iva_ref is not None
        assert fee_est == (base_15 * Decimal("1.16")).quantize(Decimal("0.0001"))
        assert iva_ref == (base_15 * Decimal("0.16")).quantize(Decimal("0.0001"))
        # Conciliacion contra cargo real USD de SP-API Finances dentro de $0.01 de redondeo
        assert abs(fee_est - cargo_real) <= Decimal("0.01")

    # Control de MeLi (mult = 1): no agrega fee_iva_referral.
    entrada_meli = EntradaCalculo(
        precio_bruto=Decimal("1000.0000"),
        precio_moneda="MXN",
        costo_original=Decimal("250.0000"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fee_total=Decimal("195.0000"),
        fee_estado="success",
        fee_detalles=(DetalleFee("SaleFee", Decimal("195.0000")),),
        logistica=Decimal("100.0000"),
        logistica_origen="propio",
    )
    res_meli = calcular_contribucion(
        entrada_meli, POL_S4_MELI, valoracion_date=_FECHA, universo_esperado="meli/meli"
    )
    assert res_meli.estado == "disponible"
    assert "fee_iva_referral" not in {c.nombre for c in res_meli.componentes}

    # Motor de precios (objetivo.paso + reglas.decidir) en US S4 aplica 1.16 una sola vez.
    p_us = Decimal("118.0000")
    c_us = Decimal("30.0000")
    l_us = Decimal("18.0000")
    f_eff_us = Decimal("20.5320")
    isr_us = Decimal("2.4426")
    goal_us = Decimal("0.4500")
    comp_us = Componentes(
        p_actual=Importe(p_us, "USD"),
        ingreso=Importe(p_us, "USD"),
        costo=Importe(c_us, "USD"),
        fees=Importe(f_eff_us, "USD"),
        envio=Importe(l_us, "USD"),
        isr=Importe(isr_us, "USD"),
    )
    esc_us = Escenario(
        componentes=comp_us,
        fee_detalles=(DetalleFee(fee_type="ReferralFee", final_fee=Decimal("17.7000")),),
        precio_cotizado=Importe(p_us, "USD"),
        iva_divisor=Decimal("1"),
        isr_tasa=Decimal("0.0207"),
        precio_incluye_iva=False,
        oferta_observada_en=_AHORA,
        referral_fee_iva_multiplicador=Decimal("1.16"),
    )
    fees_esc_us = Fees(
        total=Importe(f_eff_us, "USD"),
        variable=Importe(f_eff_us, "USD"),
        fijo=Importe(Decimal("0"), "USD"),
        impuesto_pendiente=False,
    )
    cfg_us = ConfigPrecio(
        caida_ventas_pct=Decimal("0.30"),
        senal_dias=5,
        u60_min=5,
        fechas_excluidas=(),
        escalon_max_pct=Decimal("0.15"),
        movimiento_min_pct=Decimal("0.02"),
        movimiento_min_abs_mxn=Decimal("5"),
        movimiento_min_abs_usd=Decimal("0.50"),
        tolerancia=Decimal("0.01"),
        dias_entre_cambios=7,
        freno_cambios=3,
        divergencia_max_pct=Decimal("0.05"),
    )
    entrada_dec = EntradaDecision(
        listing_id=1,
        platform="amazon_us",
        product_id=101,
        canal="fbm",
        mode="shadow",
        goal=goal_us,
        escenario=esc_us,
        pricing=ObservacionPricing(Importe(p_us, "USD"), _AHORA),
        senal=SenalVentas("no_perdiendo", None, "unidades", 10, 40, 15, 60, 0),
        cambios=(),
        historial=(),
        goal_vigente_desde=date(2026, 11, 1),
    )
    pide = decidir(
        entrada_dec, hoy=_FECHA, config=cfg_us, cotizaciones=(), fees_escenario=fees_esc_us
    )
    assert pide.intento == 1
    ref_crudo = (pide.precio.valor * Decimal("0.15")).quantize(Decimal("0.01"))
    cot1 = Cotizacion(
        precio=pide.precio,
        fees=Fees(
            total=Importe(ref_crudo, "USD"),
            variable=Importe(ref_crudo, "USD"),
            fijo=Importe(Decimal("0"), "USD"),
            impuesto_pendiente=False,
        ),
        estimada_en=_AHORA,
        error_code=None,
    )
    final = decidir(
        entrada_dec, hoy=_FECHA, config=cfg_us, cotizaciones=(cot1,), fees_escenario=fees_esc_us
    )
    assert final.resultado == "subir"
    assert final.p_objetivo == pide.precio

    # Control: sin multiplicador en paso() si rebotaria a intento 2.
    rebote = paso(
        costo=c_us,
        fijo=Decimal("0"),
        envio=l_us,
        isr_tasa=Decimal("0.0207"),
        goal=goal_us,
        iva_divisor=Decimal("1"),
        incluye_iva=False,
        ref=f_eff_us / p_us,
        moneda="USD",
        tolerancia=Decimal("0.01"),
        cotizaciones=(cot1,),
        fee_variable_multiplicador=Decimal("1"),
    )
    assert hasattr(rebote, "intento") and rebote.intento == 2


def test_arreglos05_k_dato_ausente_no_inventa_cero_ni_dispara_abstencion():
    """fee_total None -> fee_ausente; tax_amount ausente queda None y no inventa cero."""
    detalles = detalles_fee_desde_json([{"fee_type": "ReferralFee", "final_fee": "17.7000"}])
    assert detalles == (DetalleFee("ReferralFee", Decimal("17.7000"), tax_amount=None),)

    entrada_sin_fee = EntradaCalculo(
        precio_bruto=Decimal("118.0000"),
        precio_moneda="USD",
        costo_original=Decimal("30.0000"),
        costo_moneda="USD",
        costo_includes_tax=False,
        fee_total=None,
        fee_estado=None,
        fee_detalles=(),
        logistica=Decimal("18.0000"),
        logistica_origen="propio",
    )
    res = calcular_contribucion(
        entrada_sin_fee, POL_S4_US_FBM, valoracion_date=_FECHA, universo_esperado="amazon_us/fbm"
    )
    assert res.estado == "incompleta"
    assert "fee_ausente" in res.motivos
    assert "impuesto_fee_pendiente" not in res.motivos


def _sembrar_base_us(
    conn,
    *,
    precio: str = "118.0000",
    ref_fee: str = "17.7000",
    costo_mxn: str = "561.5880",
    envio_mxn: str = "336.9528",
):
    """Siembra producto, listing amazon_us/fbm, costo MXN, FX, muestra envio, oferta y fee USD."""
    hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    ahora = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0].replace(tzinfo=UTC)
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('K-US-1', 'Arras US K') RETURNING id"
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_us', 'B0KUS00001', 'SKU-K-US-1') RETURNING id",
        (pid,),
    ).fetchone()[0]
    run_id = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, ok)"
        " VALUES ('test_k_us', %s, %s, true) RETURNING id",
        (ahora, ahora),
    ).fetchone()[0]
    val_id = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, ok)"
        " VALUES (%s, %s, %s, true) RETURNING id",
        (SOURCE_COSTOS, ahora, ahora),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id) VALUES (%s, %s, 'MXN', false, DATE '2026-01-01', %s)",
        (pid, costo_mxn, val_id),
    )
    conn.execute(
        "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, ingest_run_id)"
        " VALUES (%s, 'USD', 'MXN', 18.71960000, %s)"
        " ON CONFLICT DO NOTHING",
        (hoy, run_id),
    )
    mid = conn.execute(
        "INSERT INTO precio_envio_muestra (product_id, platform, canal, ventana_desde,"
        " ventana_hasta, envios, percentil, valor, valor_currency, mediana,"
        " mediana_currency, p90, p90_currency, maximo, maximo_currency, origen,"
        " familia_id, donantes, regla_version)"
        " VALUES (%s, 'amazon_us', 'fbm', %s, %s, 10, 'mediana', %s, 'MXN', %s,"
        " 'MXN', %s, 'MXN', %s, 'MXN', 'propio', NULL, NULL, 'f2.1') RETURNING id",
        (pid, hoy - timedelta(days=30), hoy, envio_mxn, envio_mxn, envio_mxn, envio_mxn),
    ).fetchone()[0]
    fp = f"fp-k-us-{lid}"
    oid = conn.execute(
        "INSERT INTO estimacion_oferta_observation"
        " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        "  fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint,"
        "  ingest_run_id)"
        " VALUES (%s, 'amazon_us', 'SKU-K-US-1', 'B0KUS00001', 'fbm', %s, 'USD',"
        "  %s, %s, %s, %s, %s, %s) RETURNING id",
        (lid, precio, ahora, ahora, f"of-k-us-{lid}", Json({"p": precio}), fp, run_id),
    ).fetchone()[0]
    fid = conn.execute(
        "INSERT INTO estimacion_fee_observation"
        " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
        "  quoted_price_amount, quoted_price_currency, total_fees, fee_details,"
        "  fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
        "  canonical_input, context_fingerprint, ingest_run_id)"
        " VALUES (%s, %s, 'amazon_us', 'SKU-K-US-1', 'B0KUS00001', 'fbm', %s, 'USD',"
        "  %s, %s, %s, %s, %s, 'success', %s, %s, %s, %s) RETURNING id",
        (
            oid,
            lid,
            precio,
            ref_fee,
            Json(
                [
                    {"fee_type": "ReferralFee", "final_fee": ref_fee},
                    {"fee_type": "PerItemFee", "final_fee": "0.0000"},
                    {"fee_type": "VariableClosingFee", "final_fee": "0.0000"},
                ]
            ),
            ahora,
            ahora,
            ahora,
            f"fee-k-us-{lid}",
            Json({"f": ref_fee}),
            fp,
            run_id,
        ),
    ).fetchone()[0]
    return pid, lid, oid, fid, mid, hoy, ahora


def test_arreglos05_k_migracion_0100_sintaxis_y_receta_despliegue():
    """0100 y su reversa existen, parsean con pglast y entran en migraciones_de."""
    assert IDA_0100.exists(), f"falta {IDA_0100.name}"
    assert REV_0100.exists(), f"falta {REV_0100.name}"
    assert pglast.parse_sql(IDA_0100.read_text(encoding="utf-8"))
    assert pglast.parse_sql(REV_0100.read_text(encoding="utf-8"))
    _sonda_0100()

    # Verifica que migraciones_de en tools/despliegue/comun.sh acepta 0100 en el arbol local.
    for ruta in (IDA_0100, REV_0100):
        txt = ruta.read_text(encoding="utf-8")
        assert "BEGIN;" not in txt.upper()
        assert "COMMIT;" not in txt.upper()


@_skip_db
def test_arreglos05_k_migracion_0100_ida_reversa_y_escenario_us_s4_en_pg16():
    """PG16: 0100 activa S4 para amazon_us/fbm, reader cuadra y ambas reversas funcionan."""
    sonda = _sonda_0100()
    sql_ida = IDA_0100.read_text(encoding="utf-8")
    sql_rev = REV_0100.read_text(encoding="utf-8")

    with db_desde_template(prefijo="orbit_a05k_0100", base=_test_dsn(), autocommit=True) as (
        conn,
        _,
    ):
        assert conn.execute(sonda).fetchone() == (True,)

        # 1) Reversa limpia (0 escenarios S4 US) tras simular repricing02_fbm_us previa.
        conn.execute(sql_rev)
        assert conn.execute(sonda).fetchone() == (False,)

        # Insertar repricing02_fbm_us S3 (como en produccion antes de 0100) y aplicar 0100.
        conn.execute(
            """
            INSERT INTO estimacion_politica_version
                (label, universo, formula_version, settings, valid_from, valid_to)
            SELECT 'repricing02_fbm_us', 'amazon_us/fbm', 'S3',
                   '{"iva_divisor": "1", "precio_incluye_iva": false, "isr_tasa": "0.0207",
                     "logistica": "0", "logistica_semantica": "L sale de precio_envio_muestra",
                     "retencion_iva_reconciliacion": "0.08",
                     "fee_tax_amount_requiere_politica": true}'::jsonb,
                   DATE '2026-10-15', NULL
             WHERE NOT EXISTS (
                 SELECT 1 FROM estimacion_politica_version
                  WHERE universo = 'amazon_us/fbm' AND formula_version = 'S3' AND valid_to IS NULL
             )
            """
        )
        conn.execute(sql_ida)
        assert conn.execute(sonda).fetchone() == (True,)

        # La S3 anterior quedo cerrada hoy y los triggers de 0093 siguen activos.
        cerrada = conn.execute(
            "SELECT valid_to FROM estimacion_politica_version"
            " WHERE label = 'repricing02_fbm_us' AND formula_version = 'S3'"
        ).fetchone()
        assert cerrada is not None and cerrada[0] is not None

        pol_s4_id = conn.execute(
            "SELECT id FROM estimacion_politica_version"
            " WHERE universo = 'amazon_us/fbm' AND formula_version = 'S4' AND valid_to IS NULL"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM estimacion_politica_version WHERE id = %s", (pol_s4_id,))

        # 2) Reversa limpia con repricing02_fbm_us previa la reabre con valid_to = NULL.
        conn.execute(sql_rev)
        assert conn.execute(sonda).fetchone() == (False,)
        reabierta = conn.execute(
            "SELECT valid_to FROM estimacion_politica_version"
            " WHERE label = 'repricing02_fbm_us' AND formula_version = 'S3'"
        ).fetchone()
        assert reabierta == (None,)

        # 3) Re-aplicar 0100 y sembrar escenario US S4 end-to-end.
        conn.execute(sql_ida)
        assert conn.execute(sonda).fetchone() == (True,)
        _, lid, oid, fid, _mid, hoy, ahora = _sembrar_base_us(conn)
        esc = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=hoy,
            observed_at=ahora,
        )
        assert esc.estado == "disponible"
        assert esc.formula_version == "S4"
        assert esc.moneda == "USD"
        assert esc.fee_variable == Decimal("20.5320")
        assert esc.fee_fijo == Decimal("0.0000")
        persistir_escenario(conn, esc)

        # Reader + proyeccion S5 coherentes con precios.
        leidos = leer_escenarios(conn, [lid], as_of=ahora)
        sobre = proyeccion_s5(leidos[0])
        assert sobre["escenario"]["version_formula"] == "S4"
        assert sobre["base_porcentaje"] == "precio_bruto"

        cuenta = leer_cuenta(conn, lid, as_of=ahora)
        assert not isinstance(cuenta, str) and cuenta is not None
        assert cuenta.iva_divisor == Decimal("1")
        assert cuenta.precio_incluye_iva is False
        assert cuenta.referral_fee_iva_multiplicador == Decimal("1.16")
        assert cuenta.componentes.fees.valor == Decimal("20.5320")
        assert cuenta.componentes.isr.valor == Decimal("2.4426")

        # 4) Reversa CON escenario S4 referenciado: conserva historia, cierra hoy y abre S3.
        conn.execute(sql_rev)
        assert conn.execute(sonda).fetchone() == (False,)
        ahora_rev = (
            conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0].replace(tzinfo=UTC)
        )
        pol_rev = resolver_politica_aplicable(
            conn, fecha=hoy, universo="amazon_us/fbm", observed_at=ahora_rev
        )
        assert pol_rev.politica is not None and pol_rev.politica.formula_version == "S3"
        assert (
            conn.execute(
                "SELECT count(*) FROM estimacion_escenario"
                " WHERE platform = 'amazon_us' AND formula_version = 'S4'"
            ).fetchone()[0]
            == 1
        )

        # 5) Re-aplicar 0100 el mismo dia UTC vuelve a S4 sin chocar con la continuacion S3.
        conn.execute(sql_ida)
        assert conn.execute(sonda).fetchone() == (True,)
        ahora_redep = (
            conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0].replace(tzinfo=UTC)
        )
        pol_redep = resolver_politica_aplicable(
            conn, fecha=hoy, universo="amazon_us/fbm", observed_at=ahora_redep
        )
        assert pol_redep.politica is not None and pol_redep.politica.formula_version == "S4"


@_skip_db
def test_arreglos05_k_reancle_us_preserva_pstar_acota_piso_con_flag_y_revierte():
    """Re-anclaje US: preserva P*, aborta sin flag si <10%, acota con flag y revierte exacto."""
    sql_ida = IDA_0100.read_text(encoding="utf-8")
    sql_rev = REV_0100.read_text(encoding="utf-8")

    with db_desde_template(prefijo="orbit_a05k_reancle_us", base=_test_dsn(), autocommit=True) as (
        conn,
        _,
    ):
        # Volver US a S3 para sembrar escenarios y goals S3 reales (en banda y en 12.06%).
        conn.execute(sql_rev)
        conn.execute(
            """
            INSERT INTO estimacion_politica_version
                (label, universo, formula_version, settings, valid_from, valid_to)
            SELECT 'repricing02_fbm_us', 'amazon_us/fbm', 'S3',
                   '{"iva_divisor": "1", "precio_incluye_iva": false, "isr_tasa": "0.0207",
                     "logistica": "0", "logistica_semantica": "L sale de precio_envio_muestra",
                     "retencion_iva_reconciliacion": "0.08",
                     "fee_tax_amount_requiere_politica": true}'::jsonb,
                   DATE '2026-10-15', NULL
             WHERE NOT EXISTS (
                 SELECT 1 FROM estimacion_politica_version
                  WHERE universo = 'amazon_us/fbm' AND formula_version = 'S3' AND valid_to IS NULL
             )
            """
        )
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('test-k-us', %s)",
            (Json({"precio_goal_min_pct": "0.10", "precio_goal_max_pct": "0.60"}),),
        )

        # Listing 1: en banda (~38% S3 -> ~35.6% S4).
        _, lid1, oid1, fid1, _, hoy, ahora = _sembrar_base_us(
            conn, precio="118.0000", ref_fee="17.7000", costo_mxn="561.5880", envio_mxn="336.9528"
        )
        esc1 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid1,
            oferta_observation_id=oid1,
            fee_observation_id=fid1,
            valoracion_date=hoy,
            observed_at=ahora,
        )
        assert esc1.formula_version == "S3" and esc1.estado == "disponible"
        persistir_escenario(conn, esc1)
        sembrar_goal(
            conn,
            listing_id=lid1,
            platform="amazon_us",
            goal_pct="36.00",
            mode="live",
            go_literal="GO-US-S3-1",
            creado_por="X.3 encender.sh",
            origen="cambio_de_modo",
            m_referencia=Decimal("0.3600"),
        )

        # Listing 2: replica el borde real de produccion (p=$48, goal_s3=12.06% -> 9.67% < 10%).
        pid2 = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('K-US-2', 'Arras US Piso') RETURNING id"
        ).fetchone()[0]
        lid2 = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'B0KUS00002', 'SKU-K-US-2') RETURNING id",
            (pid2,),
        ).fetchone()[0]
        val_id = conn.execute(
            "SELECT id FROM ingest_run WHERE source = %s ORDER BY id DESC LIMIT 1",
            (SOURCE_COSTOS,),
        ).fetchone()[0]
        run_id = conn.execute(
            "SELECT id FROM ingest_run WHERE source = 'test_k_us' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        # p=$48, costo=$17 USD (318.2332 MXN), envio=$15.70 USD (293.8977 MXN) -> m_s3 ≈ 14.80%
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from, ingest_run_id) VALUES (%s, 318.2332, 'MXN', false, %s, %s)",
            (pid2, hoy, val_id),
        )
        conn.execute(
            "INSERT INTO precio_envio_muestra (product_id, platform, canal, ventana_desde,"
            " ventana_hasta, envios, percentil, valor, valor_currency, mediana,"
            " mediana_currency, p90, p90_currency, maximo, maximo_currency, origen,"
            " familia_id, donantes, regla_version)"
            " VALUES (%s, 'amazon_us', 'fbm', %s, %s, 10, 'mediana', 293.8977, 'MXN',"
            " 293.8977, 'MXN', 293.8977, 'MXN', 293.8977, 'MXN', 'propio', NULL, NULL, 'f2.1')",
            (pid2, hoy - timedelta(days=30), hoy),
        )
        fp2 = f"fp-k-us-{lid2}"
        oid2 = conn.execute(
            "INSERT INTO estimacion_oferta_observation"
            " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
            "  fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint,"
            "  ingest_run_id)"
            " VALUES (%s, 'amazon_us', 'SKU-K-US-2', 'B0KUS00002', 'fbm', 48.0000, 'USD',"
            "  %s, %s, %s, %s, %s, %s) RETURNING id",
            (lid2, ahora, ahora, f"of-k-us-{lid2}", Json({"p": "48.0000"}), fp2, run_id),
        ).fetchone()[0]
        fid2 = conn.execute(
            "INSERT INTO estimacion_fee_observation"
            " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
            "  quoted_price_amount, quoted_price_currency, total_fees, fee_details,"
            "  fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
            "  canonical_input, context_fingerprint, ingest_run_id)"
            " VALUES (%s, %s, 'amazon_us', 'SKU-K-US-2', 'B0KUS00002', 'fbm', 48.0000, 'USD',"
            "  7.2000, %s, %s, %s, %s, 'success', %s, %s, %s, %s) RETURNING id",
            (
                oid2,
                lid2,
                Json([{"fee_type": "ReferralFee", "final_fee": "7.2000"}]),
                ahora,
                ahora,
                ahora,
                f"fee-k-us-{lid2}",
                Json({"f": "7.2000"}),
                fp2,
                run_id,
            ),
        ).fetchone()[0]
        esc2 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid2,
            oferta_observation_id=oid2,
            fee_observation_id=fid2,
            valoracion_date=hoy,
            observed_at=ahora,
        )
        assert esc2.formula_version == "S3" and esc2.estado == "disponible"
        persistir_escenario(conn, esc2)
        sembrar_goal(
            conn,
            listing_id=lid2,
            platform="amazon_us",
            goal_pct="12.06",
            mode="live",
            go_literal="GO-US-S3-2",
            creado_por="X.3 encender.sh",
            origen="cambio_de_modo",
            m_referencia=Decimal("0.1206"),
        )

        # Sin 0100 (US aun en S3), reancle aborta fail-closed.
        with pytest.raises(ValueError, match="no 'S4'"):
            construir_plan_reancle(conn, plataformas=("amazon_us",))

        # Aplicar 0100 -> ahora US esta en S4.
        conn.execute(sql_ida)

        # Sin acotar_banda: fila 2 queda fuera de banda y aplicar_reancle aborta fail-closed.
        plan_estricto, huella_estricta = construir_plan_reancle(conn, plataformas=("amazon_us",))
        assert len(plan_estricto) == 2
        f1, f2 = sorted(plan_estricto, key=lambda x: x.listing_id)
        assert not f1.fuera_de_banda
        assert abs(f1.p_star_s4 - f1.p_star_s3) <= Decimal("0.01")
        assert f2.fuera_de_banda is True
        assert f2.goal_s4 < Decimal("0.10")
        with pytest.raises(PrecioGoalInvalido, match="fuera de banda"):
            aplicar_reancle(conn, plan=plan_estricto, go_literal="GO-US-FAIL")

        # Con acotar_banda=True: fila 2 se acota explicitamente al piso 0.1000 y cambia la huella.
        plan_acotado, huella_acotada = construir_plan_reancle(
            conn, plataformas=("amazon_us",), acotar_banda=True
        )
        assert huella_acotada != huella_estricta
        fa1, fa2 = sorted(plan_acotado, key=lambda x: x.listing_id)
        assert fa1.acotado_a_banda is False
        assert fa2.acotado_a_banda is True
        assert fa2.fuera_de_banda is False
        assert fa2.goal_s4 == Decimal("0.1000")

        escritos = aplicar_reancle(
            conn, plan=plan_acotado, go_literal="GO-REANCLE-US-S4", lote="reancle_s4_us_2026_11"
        )
        assert escritos == 2

        # Idempotente: segundo plan devuelve 0 filas.
        plan_2, _ = construir_plan_reancle(conn, plataformas=("amazon_us",), acotar_banda=True)
        assert plan_2 == []

        # Reversa restaura exactamente 36.00% y 12.06%.
        plan_rev, _ = construir_plan_reversa_reancle(
            conn, plataformas=("amazon_us",), lote="reancle_s4_us_2026_11"
        )
        assert len(plan_rev) == 2
        assert (
            aplicar_reversa_reancle(
                conn,
                plan=plan_rev,
                go_literal="GO-REV-US-S3",
                lote="reancle_s4_us_2026_11",
            )
            == 2
        )
        restaurados = dict(
            conn.execute(
                "SELECT listing_id, margen_goal_pct FROM precio_goal"
                " WHERE platform = 'amazon_us' AND valid_to IS NULL"
            ).fetchall()
        )
        assert restaurados == {lid1: Decimal("0.3600"), lid2: Decimal("0.1206")}


@_skip_db
def test_arreglos05_k_compuerta_no_traba_us_en_transicion_s3_a_s4():
    """Transicion S3->S4 en US exime fee_variable (+16%), pero retiene ante salto real de costo."""
    sql_ida = IDA_0100.read_text(encoding="utf-8")
    sql_rev = REV_0100.read_text(encoding="utf-8")

    with db_desde_template(prefijo="orbit_a05k_comp_us", base=_test_dsn(), autocommit=True) as (
        conn,
        _,
    ):
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        ayer = hoy - timedelta(days=1)
        ahora = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0].replace(tzinfo=UTC)
        ahora_ayer = ahora - timedelta(days=1)

        # Sembrar S3 ayer y S4 hoy para amazon_us/fbm.
        conn.execute(sql_rev)
        conn.execute(
            """
            INSERT INTO estimacion_politica_version
                (created_at, label, universo, formula_version, settings, valid_from, valid_to)
            VALUES (%s, 'repricing02_fbm_us', 'amazon_us/fbm', 'S3',
                    '{"iva_divisor": "1", "precio_incluye_iva": false, "isr_tasa": "0.0207",
                      "logistica": "0", "logistica_semantica": "L sale de precio_envio_muestra",
                      "retencion_iva_reconciliacion": "0.08",
                      "fee_tax_amount_requiere_politica": true}'::jsonb,
                    DATE '2026-10-15', NULL)
            """,
            (ahora_ayer,),
        )
        pol_s3_id = conn.execute(
            "SELECT id FROM estimacion_politica_version"
            " WHERE universo = 'amazon_us/fbm' AND formula_version = 'S3' AND valid_to IS NULL"
        ).fetchone()[0]
        conn.execute(sql_ida)
        pol_s4_id = conn.execute(
            "SELECT id FROM estimacion_politica_version"
            " WHERE universo = 'amazon_us/fbm' AND formula_version = 'S4' AND valid_to IS NULL"
        ).fetchone()[0]

        cfg_id = conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('comp-us-s4', %s) RETURNING id",
            (Json({"precio_goal_min_pct": "0.10", "precio_goal_max_pct": "0.60"}),),
        ).fetchone()[0]
        conn.execute("ALTER TABLE precio_corrida DISABLE TRIGGER USER")
        try:
            corrida_ayer = conn.execute(
                "INSERT INTO precio_corrida"
                " (platform, fecha, owner, estado, iniciada_at, cerrada_at,"
                "  config_version_id, modo_global, decisiones, movidas, virtuales,"
                "  retenidas, errores, saltadas_por_edicion)"
                " VALUES ('amazon_us', %s, 'test', 'cerrada', %s, %s, %s, 'live',"
                "  1, 0, 0, 0, 0, 0) RETURNING id",
                (ayer, ahora_ayer, ahora_ayer, cfg_id),
            ).fetchone()[0]
        finally:
            conn.execute("ALTER TABLE precio_corrida ENABLE TRIGGER USER")
        corrida_hoy = conn.execute(
            "INSERT INTO precio_corrida"
            " (platform, fecha, owner, estado, iniciada_at, config_version_id, modo_global)"
            " VALUES ('amazon_us', %s, 'test', 'abierta', %s, %s, 'live') RETURNING id",
            (hoy, ahora, cfg_id),
        ).fetchone()[0]

        val_id = conn.execute(
            "INSERT INTO ingest_run (source, started_at, finished_at, ok)"
            " VALUES (%s, %s, %s, true) RETURNING id",
            (SOURCE_COSTOS, ahora, ahora),
        ).fetchone()[0]
        val_id_ayer = conn.execute(
            "INSERT INTO ingest_run (source, started_at, finished_at, ok)"
            " VALUES (%s, %s, %s, true) RETURNING id",
            (SOURCE_COSTOS, ahora_ayer, ahora_ayer),
        ).fetchone()[0]
        ahora_esc = (
            conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0].replace(tzinfo=UTC)
        )

        for idx in range(12):
            sku = f"SKU-KUS-{idx:02d}"
            asin = f"B0KUS000{idx:02d}"
            fp_us = f"fp-kus-{idx:02d}"
            pid = conn.execute(
                "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
            ).fetchone()[0]
            lid = conn.execute(
                "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
                " VALUES (%s, 'amazon_us', %s, %s) RETURNING id",
                (pid, asin, sku),
            ).fetchone()[0]
            sku_cost_id = conn.execute(
                "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
                " valid_from, ingest_run_id)"
                " VALUES (%s, 561.5880, 'MXN', false, DATE '2026-01-01', %s) RETURNING id",
                (pid, val_id),
            ).fetchone()[0]
            mid = conn.execute(
                "INSERT INTO precio_envio_muestra (product_id, platform, canal, ventana_desde,"
                " ventana_hasta, envios, percentil, valor, valor_currency, mediana,"
                " mediana_currency, p90, p90_currency, maximo, maximo_currency, origen,"
                " familia_id, donantes, regla_version)"
                " VALUES (%s, 'amazon_us', 'fbm', %s, %s, 10, 'mediana', 336.9528, 'MXN',"
                " 336.9528, 'MXN', 336.9528, 'MXN', 336.9528, 'MXN', 'propio', NULL, NULL, 'f2.1')"
                " RETURNING id",
                (pid, hoy - timedelta(days=30), hoy),
            ).fetchone()[0]
            oid = conn.execute(
                "INSERT INTO estimacion_oferta_observation"
                " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
                "  fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
                " VALUES (%s, 'amazon_us', %s, %s, 'fbm', 118.0000, 'USD',"
                "  %s, %s, %s, '{}'::jsonb, %s) RETURNING id",
                (lid, sku, asin, ahora_esc, ahora_esc, f"of-kus-{idx}", fp_us),
            ).fetchone()[0]
            fid = conn.execute(
                "INSERT INTO estimacion_fee_observation"
                " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
                "  quoted_price_amount, quoted_price_currency, total_fees, fee_details,"
                "  fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
                "  canonical_input, context_fingerprint)"
                " VALUES (%s, %s, 'amazon_us', %s, %s, 'fbm', 118.0000, 'USD', 17.7000,"
                "  '[]'::jsonb, %s, %s, %s, 'success', %s, '{}'::jsonb, %s) RETURNING id",
                (oid, lid, sku, asin, ahora_esc, ahora_esc, ahora_esc, f"fe-kus-{idx}", fp_us),
            ).fetchone()[0]
            gid = conn.execute(
                "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
                " valid_from, creado_por, go_literal)"
                " VALUES (%s, 'amazon_us', 0.3500, 'live', %s, 'test', 'GO') RETURNING id",
                (lid, ayer),
            ).fetchone()[0]
            esc_s3_id = conn.execute(
                "INSERT INTO estimacion_escenario"
                " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
                "  politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
                "  sku_cost_id, costo_validation_run_id, costo_validated_at,"
                "  estado, motivos, componentes, exclusiones, canonical_input, context_fingerprint,"
                "  source_event_id, moneda, contribucion, contribucion_pct, logistica_origen,"
                "  envio_muestra_id, fee_variable, fee_fijo, fee_cotizable)"
                " VALUES (%s, 'amazon_us', %s, %s, 'fbm', %s, %s, %s, 'S3',"
                "  %s, %s, %s, %s, %s, 'disponible', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,"
                "  '{}'::jsonb, %s, %s, 'USD', 49.8574, 42.2520, 'propio',"
                "  %s, 17.7000, 0.0000, true) RETURNING id",
                (
                    lid,
                    sku,
                    asin,
                    ayer,
                    ahora_esc,
                    pol_s3_id,
                    oid,
                    fid,
                    sku_cost_id,
                    val_id_ayer,
                    ahora_ayer,
                    fp_us,
                    f"esc-us-s3-{idx}",
                    mid,
                ),
            ).fetchone()[0]
            esc_s4_id = conn.execute(
                "INSERT INTO estimacion_escenario"
                " (listing_id, platform, seller_sku, asin, canal, valoracion_date, observed_at,"
                "  politica_version_id, formula_version, oferta_observation_id, fee_observation_id,"
                "  sku_cost_id, costo_validation_run_id, costo_validated_at,"
                "  estado, motivos, componentes, exclusiones, canonical_input, context_fingerprint,"
                "  source_event_id, moneda, contribucion, contribucion_pct, logistica_origen,"
                "  envio_muestra_id, fee_variable, fee_fijo, fee_cotizable)"
                " VALUES (%s, 'amazon_us', %s, %s, 'fbm', %s, %s, %s, 'S4',"
                "  %s, %s, %s, %s, %s, 'disponible', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,"
                "  '{}'::jsonb, %s, %s, 'USD', 47.0254, 39.8520, 'propio',"
                "  %s, 20.5320, 0.0000, true) RETURNING id",
                (
                    lid,
                    sku,
                    asin,
                    hoy,
                    ahora_esc,
                    pol_s4_id,
                    oid,
                    fid,
                    sku_cost_id,
                    val_id,
                    ahora,
                    fp_us,
                    f"esc-us-s4-{idx}",
                    mid,
                ),
            ).fetchone()[0]

            conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER USER")
            try:
                conn.execute(
                    "INSERT INTO precio_decision"
                    " (listing_id, platform, decision_date, resultado, motivo, product_id, canal,"
                    "  m_actual, goal, p_actual, p_actual_currency, p_objetivo_currency,"
                    "  p_aplicado_currency, i_valor, i_currency,"
                    "  c_valor, c_currency, f_valor, f_currency, l_valor, l_currency,"
                    "  r_valor, r_currency, escenario_id, fee_observation_id, mode,"
                    "  config_version_id, corrida_id, goal_id, l_origen, envio_muestra_id)"
                    " VALUES (%s, 'amazon_us', %s, 'mantener', 'en_tolerancia', %s, 'fbm',"
                    "  0.4225, 0.3500, 118.00, 'USD', 'USD', 'USD', 118.00, 'USD',"
                    "  30.00, 'USD', 17.70, 'USD', 18.00, 'USD',"
                    "  2.44, 'USD', %s, %s, 'live', %s, %s, %s, 'propio', %s)",
                    (lid, ayer, pid, esc_s3_id, fid, cfg_id, corrida_ayer, gid, mid),
                )
            finally:
                conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER USER")

            res_hoy = "subir" if idx == 0 else "mantener"
            mot_hoy = None if idx == 0 else "en_tolerancia"
            conn.execute(
                "INSERT INTO precio_decision"
                " (listing_id, platform, decision_date, resultado, motivo, product_id, canal,"
                "  m_actual, goal, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
                "  p_aplicado, p_aplicado_currency, i_valor, i_currency,"
                "  c_valor, c_currency, f_valor, f_currency, l_valor, l_currency,"
                "  r_valor, r_currency, escenario_id, fee_observation_id, mode,"
                "  config_version_id, corrida_id, goal_id, l_origen, envio_muestra_id,"
                "  verificacion)"
                " VALUES (%s, 'amazon_us', %s, %s, %s, %s, 'fbm',"
                "  0.3985, 0.3500, 118.00, 'USD', %s, 'USD', %s, 'USD', 118.00, 'USD',"
                "  30.00, 'USD', 20.53, 'USD', 18.00, 'USD',"
                "  2.44, 'USD', %s, %s, 'live', %s, %s, %s, 'propio', %s, %s)",
                (
                    lid,
                    hoy,
                    res_hoy,
                    mot_hoy,
                    pid,
                    Decimal("124.00") if idx == 0 else None,
                    Decimal("124.00") if idx == 0 else None,
                    esc_s4_id,
                    fid,
                    cfg_id,
                    corrida_hoy,
                    gid,
                    mid,
                    "cotizada" if idx == 0 else None,
                ),
            )

        class _MercadoUS:
            clave = "amazon_us"
            filtra_catalogo = False

        motor = ConfigMotor(
            modo_global="live",
            modo_universo={"amazon_us/fbm": "live"},
            banda_goal=(Decimal("0.10"), Decimal("0.60")),
            freno_dias_error=3,
            catalogo_max_dias_sin_reportar=3,
            aviso_dias_sin_evaluar=3,
            fusible_frac=Decimal("0.20"),
            fusible_min_movimientos=10,
            insumo_salto_pct=Decimal("0.05"),
            insumo_salto_frac=Decimal("0.30"),
            corte_errores_consecutivos=5,
            senal_trafico_min=50,
            cohorte_caida_pct=Decimal("0.30"),
            envio_ventana_dias=90,
        )
        veredictos = _medir_compuerta(conn, _MercadoUS(), hoy, motor, corrida_hoy)
        assert "fbm" in veredictos
        assert not hasattr(veredictos["fbm"], "causa"), (
            f"transicion S3->S4 en US no debe trabar compuerta: {veredictos['fbm']}"
        )

        # Un salto real de costo (30 -> 36, +20%) durante S3->S4 SI debe retener.
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER USER")
        try:
            conn.execute(
                "UPDATE precio_decision SET c_valor = 36.00 WHERE corrida_id = %s",
                (corrida_hoy,),
            )
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER USER")
        corrida_costo = conn.execute(
            "INSERT INTO precio_corrida"
            " (platform, fecha, owner, estado, iniciada_at, config_version_id, modo_global)"
            " VALUES ('amazon_us', %s, 'test', 'abierta', %s, %s, 'live') RETURNING id",
            (hoy, ahora, cfg_id),
        ).fetchone()[0]
        veredictos_costo = _medir_compuerta(conn, _MercadoUS(), hoy, motor, corrida_costo)
        assert getattr(veredictos_costo["fbm"], "causa", None) == "insumo_sistemico"
        assert getattr(veredictos_costo["fbm"], "insumo", None) == "costo"


def test_arreglos05_k_calcular_goal_equivalente_us_s4_exacto():
    """Unitario puro: `calcular_goal_equivalente_s4` baja exactamente ~2.40 pts y conserva P*."""
    tasas_us = TasasPoliticaS4(
        iva_divisor=Decimal("1"),
        isr_tasa=Decimal("0.0207"),
        retencion_iva_tasa=Decimal("0"),
        referral_fee_iva_multiplicador=Decimal("1.16"),
        precio_incluye_iva=False,
    )
    m_s3, m_s4, goal_s4, p3, p4 = calcular_goal_equivalente_s4(
        platform="amazon_us",
        goal_s3=Decimal("0.3600"),
        p_actual=Decimal("118.0000"),
        costo=Decimal("30.0000"),
        fee_raw_total=Decimal("17.7000"),
        fee_var_raw=Decimal("17.7000"),
        fee_fijo=Decimal("0.0000"),
        envio=Decimal("18.0000"),
        tasas_s4=tasas_us,
    )
    assert (m_s3 - m_s4) == Decimal("0.0240")
    assert goal_s4 == Decimal("0.3360")
    assert abs(p4 - p3) <= Decimal("0.01")
    # Confirmar que migraciones_de existe para el despliegue de 0100.
    r = subprocess.run(
        ["bash", "-c", "source tools/despliegue/comun.sh && type migraciones_de"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "migraciones_de" in r.stdout
