"""Pruebas de formula S4 (caja real Mexico), migracion 0093 y re-anclaje de goals.

Verifica:
- Conciliacion al centavo de S4 contra pedidos reales de produccion (Amazon MX FBM,
  Amazon MX FBA y Mercado Libre).
- Guardas fail-closed de S4 (politica invalida, TaxAmount no cero, referral ausente).
- Coherencia completa del motor de precios (`objetivo.paso`, `reglas.decidir`,
  `siembra._referencia`) con `referral_fee_iva_multiplicador = 1.16` y cotizacion cruda.
- Equivalencia de `P*` en `tools/reancle_goals_s4.py`.
- Migracion `0093_estimacion_s4_caja_real_mx.sql` y su reversa (con y sin escenarios S4)
  en PostgreSQL 16 real.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
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
from app.estimacion_repository import persistir_escenario, sembrar_escenario_desde_refs
from app.estimacion_venta import (
    DetalleFee,
    EntradaCalculo,
    PoliticaCalculo,
    calcular_contribucion,
)
from app.precio.config import ConfigPrecio
from app.precio.objetivo import paso
from app.precio.puerto import Cotizacion
from app.precio.reglas import decidir
from app.precio.siembra import LecturaHoy, _referencia
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
    aplicar_reancle,
    calcular_goal_equivalente_s4,
    construir_plan_reancle,
)

ROOT = Path(__file__).resolve().parents[1]
SQL_0093 = (ROOT / "migrations" / "0093_estimacion_s4_caja_real_mx.sql").read_text(encoding="utf-8")
SQL_0093_REV = (ROOT / "migrations" / "0093_reversa_estimacion_s4_caja_real_mx.sql").read_text(
    encoding="utf-8"
)
_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")

_FECHA = date(2026, 11, 21)
_AHORA = datetime(2026, 11, 21, 13, 0, tzinfo=UTC)

POL_S4_AMZ_FBM = PoliticaCalculo(
    politica_version_id=10,
    label="amazon_mx_fbm_s4_caja_real_2026_11",
    universo="amazon_mx/fbm",
    formula_version="S4",
    settings={
        "iva_divisor": "1.16",
        "isr_tasa": "0.025",
        "retencion_iva_tasa": "0.08",
        "referral_fee_iva_multiplicador": "1.16",
        "logistica": "0",
        "logistica_semantica": "L sale de precio_envio_muestra",
        "precio_incluye_iva": True,
        "fee_tax_amount_requiere_politica": True,
    },
    valid_from=date(2026, 11, 21),
    valid_to=None,
    created_at=_AHORA,
)

POL_S4_AMZ_FBA = PoliticaCalculo(
    politica_version_id=11,
    label="amazon_mx_fba_s4_caja_real_2026_11",
    universo="amazon_mx/fba",
    formula_version="S4",
    settings={
        **POL_S4_AMZ_FBM.settings,
        "logistica_semantica": "L=0 porque FBA esta en fees",
    },
    valid_from=date(2026, 11, 21),
    valid_to=None,
    created_at=_AHORA,
)

POL_S4_MELI = PoliticaCalculo(
    politica_version_id=12,
    label="meli_s4_caja_real_2026_11",
    universo="meli/meli",
    formula_version="S4",
    settings={
        **POL_S4_AMZ_FBM.settings,
        "referral_fee_iva_multiplicador": "1",
    },
    valid_from=date(2026, 11, 21),
    valid_to=None,
    created_at=_AHORA,
)


def test_migraciones_0093_parsean_y_traen_sonda():
    assert len(tuple(pglast.parse_sql(SQL_0093))) > 0
    assert len(tuple(pglast.parse_sql(SQL_0093_REV))) > 0
    primeras = SQL_0093.splitlines()[:10]
    assert any(line.startswith("-- sonda-aplicada: SELECT ") for line in primeras)
    assert "BEGIN;" not in SQL_0093
    assert "COMMIT;" not in SQL_0093


def test_s4_amazon_mx_fbm_concilia_al_centavo_con_ledger_real():
    """Pedido real 701-1258100-4790641 (DC-H8LG-NATY): P=989, C=242.47, F_raw=127.89, L=91."""
    entrada = EntradaCalculo(
        precio_bruto=Decimal("989.0000"),
        precio_moneda="MXN",
        costo_original=Decimal("242.4700"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fee_total=Decimal("127.8900"),
        fee_estado="success",
        fee_detalles=(DetalleFee(fee_type="ReferralFee", final_fee=Decimal("127.8900")),),
        logistica=Decimal("91.0000"),
    )
    res = calcular_contribucion(
        entrada, POL_S4_AMZ_FBM, valoracion_date=_FECHA, universo_esperado="amazon_mx/fbm"
    )
    assert res.estado == "disponible"
    assert res.ingreso_normalizado == Decimal("989.0000")
    assert res.isr == Decimal("21.3147")
    assert res.retencion_iva == Decimal("68.2069")
    # 989 - 242.47 - (127.89*1.16=148.3524) - 91 - 21.3147 - 68.2069 = 417.6560
    assert res.contribucion == Decimal("417.6560")
    assert res.contribucion_pct == Decimal("42.2301")
    por_nombre = {c.nombre: c for c in res.componentes}
    assert por_nombre["fee_total"].importe_original == Decimal("127.8900")
    assert por_nombre["fee_total"].importe_normalizado == Decimal("148.3524")
    assert por_nombre["fee_iva_referral"].importe_normalizado == Decimal("20.4624")
    assert por_nombre["fee_iva_referral"].pertenece_a_total is False
    assert por_nombre["retencion_iva"].importe_normalizado == Decimal("68.2069")
    assert por_nombre["retencion_iva"].pertenece_a_total is True


def test_s4_amazon_mx_fba_solo_escala_referral_y_no_fba_fees():
    """Pedido real 701-0082430-3849834 (87-RIK9-VTJQ): ReferralFee*1.16, FBAFees intacto."""
    entrada = EntradaCalculo(
        precio_bruto=Decimal("1488.0000"),
        precio_moneda="MXN",
        costo_original=Decimal("309.8200"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fee_total=Decimal("256.4100"),
        fee_estado="success",
        fee_detalles=(
            DetalleFee(fee_type="ReferralFee", final_fee=Decimal("192.4100")),
            DetalleFee(fee_type="FBAFees", final_fee=Decimal("64.0000")),
        ),
        logistica=Decimal("0.0000"),
    )
    res = calcular_contribucion(
        entrada, POL_S4_AMZ_FBA, valoracion_date=_FECHA, universo_esperado="amazon_mx/fba"
    )
    assert res.estado == "disponible"
    por_nombre = {c.nombre: c for c in res.componentes}
    # 192.41 * 1.16 = 223.1956; + 64.00 = 287.1956 (FBAFees no lleva x1.16)
    assert por_nombre["fee_total"].importe_normalizado == Decimal("287.1956")
    assert por_nombre["fee_iva_referral"].importe_normalizado == Decimal("30.7856")
    # 1488 - 309.82 - 287.1956 - 0 - (1488/1.16 * 0.105) = 756.294744...
    assert res.contribucion == Decimal("756.2947")
    assert res.contribucion_pct == Decimal("50.8263")


def test_s4_meli_concilia_al_centavo_sin_escalar_comision():
    """Pedido real 2000019303321078 (SET-CAR-AZU-PEZ-DOR): mult=1, caja real exacta."""
    entrada = EntradaCalculo(
        precio_bruto=Decimal("979.1100"),
        precio_moneda="MXN",
        costo_original=Decimal("126.2100"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fee_total=Decimal("190.9300"),
        fee_estado="success",
        fee_detalles=(DetalleFee(fee_type="SaleFee", final_fee=Decimal("190.9300")),),
        logistica=Decimal("84.5000"),
    )
    res = calcular_contribucion(
        entrada, POL_S4_MELI, valoracion_date=_FECHA, universo_esperado="meli/meli"
    )
    assert res.estado == "disponible"
    assert res.contribucion == Decimal("488.8437")
    assert res.contribucion_pct == Decimal("49.9273")
    nombres = {c.nombre for c in res.componentes}
    assert "fee_iva_referral" not in nombres
    assert "retencion_iva" in nombres


def test_s4_guardas_fail_closed():
    """Politica invalida, TaxAmount no cero y referral ausente con mult>1 bloquean."""
    pol_mala = PoliticaCalculo(
        politica_version_id=99,
        label="mala",
        universo="amazon_mx/fbm",
        formula_version="S4",
        settings={**POL_S4_AMZ_FBM.settings, "referral_fee_iva_multiplicador": "0.90"},
        valid_from=date(2026, 11, 21),
        valid_to=None,
        created_at=_AHORA,
    )
    entrada_ok = EntradaCalculo(
        precio_bruto=Decimal("989.0000"),
        precio_moneda="MXN",
        costo_original=Decimal("242.4700"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fee_total=Decimal("127.8900"),
        fee_estado="success",
        fee_detalles=(DetalleFee(fee_type="ReferralFee", final_fee=Decimal("127.8900")),),
        logistica=Decimal("91.0000"),
    )
    assert calcular_contribucion(entrada_ok, pol_mala, valoracion_date=_FECHA).motivos == (
        "politica_invalida",
    )

    entrada_tax = EntradaCalculo(
        precio_bruto=Decimal("989.0000"),
        precio_moneda="MXN",
        costo_original=Decimal("242.4700"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fee_total=Decimal("127.8900"),
        fee_estado="success",
        fee_detalles=(
            DetalleFee(
                fee_type="ReferralFee",
                final_fee=Decimal("127.8900"),
                tax_amount=Decimal("20.4600"),
            ),
        ),
        logistica=Decimal("91.0000"),
    )
    res_tax = calcular_contribucion(entrada_tax, POL_S4_AMZ_FBM, valoracion_date=_FECHA)
    assert res_tax.estado == "incompleta"
    assert "impuesto_fee_pendiente" in res_tax.motivos

    entrada_sin_ref = EntradaCalculo(
        precio_bruto=Decimal("989.0000"),
        precio_moneda="MXN",
        costo_original=Decimal("242.4700"),
        costo_moneda="MXN",
        costo_includes_tax=False,
        fee_total=Decimal("64.0000"),
        fee_estado="success",
        fee_detalles=(DetalleFee(fee_type="FBAFees", final_fee=Decimal("64.0000")),),
        logistica=Decimal("0.0000"),
    )
    res_sin_ref = calcular_contribucion(entrada_sin_ref, POL_S4_AMZ_FBA, valoracion_date=_FECHA)
    assert res_sin_ref.estado == "incompleta"
    assert "fee_incompatible" in res_sin_ref.motivos


def test_objetivo_y_reglas_s4_verifican_cotizacion_cruda_sin_rebotar():
    """Con mult=1.16, una cotizacion cruda de SP-API (12.93%) verifica en el intento 1."""
    p = Decimal("989.00")
    c = Decimal("242.47")
    l_env = Decimal("91.00")
    r_tot = Decimal("89.5216")  # 21.3147 + 68.2069
    tasa_r = r_tot / p
    f_eff = Decimal("148.3524")
    goal = Decimal("0.50")

    comp = Componentes(
        p_actual=Importe(p, "MXN"),
        ingreso=Importe(p, "MXN"),
        costo=Importe(c, "MXN"),
        fees=Importe(f_eff, "MXN"),
        envio=Importe(l_env, "MXN"),
        isr=Importe(r_tot, "MXN"),
    )
    esc = Escenario(
        componentes=comp,
        fee_detalles=(DetalleFee(fee_type="ReferralFee", final_fee=Decimal("127.8900")),),
        precio_cotizado=Importe(p, "MXN"),
        iva_divisor=Decimal("1"),
        isr_tasa=tasa_r,
        precio_incluye_iva=False,
        oferta_observada_en=_AHORA,
        referral_fee_iva_multiplicador=Decimal("1.16"),
    )
    fees_esc = Fees(
        total=Importe(f_eff, "MXN"),
        variable=Importe(f_eff, "MXN"),
        fijo=Importe(Decimal("0"), "MXN"),
        impuesto_pendiente=False,
    )
    cfg = ConfigPrecio(
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
    entrada = EntradaDecision(
        listing_id=1,
        platform="amazon_mx",
        product_id=207,
        canal="fbm",
        mode="shadow",
        goal=goal,
        escenario=esc,
        pricing=ObservacionPricing(Importe(p, "MXN"), _AHORA),
        senal=SenalVentas("no_perdiendo", None, "unidades", 10, 40, 15, 60, 0),
        cambios=(),
        historial=(),
        goal_vigente_desde=date(2026, 11, 1),
    )

    pide = decidir(entrada, hoy=_FECHA, config=cfg, cotizaciones=(), fees_escenario=fees_esc)
    assert pide.intento == 1
    p_cot = pide.precio.valor
    # SP-API responde 15%/1.16 (sin IVA) sobre p_cot
    ref_crudo = (p_cot * Decimal("0.15") / Decimal("1.16")).quantize(Decimal("0.01"))
    cot1 = Cotizacion(
        precio=pide.precio,
        fees=Fees(
            total=Importe(ref_crudo, "MXN"),
            variable=Importe(ref_crudo, "MXN"),
            fijo=Importe(Decimal("0"), "MXN"),
            impuesto_pendiente=False,
        ),
        estimada_en=_AHORA,
        error_code=None,
    )
    final = decidir(entrada, hoy=_FECHA, config=cfg, cotizaciones=(cot1,), fees_escenario=fees_esc)
    assert final.resultado == "subir"
    assert final.p_objetivo == pide.precio
    assert final.p_objetivo.valor > Decimal("1280.00")

    # Control: sin multiplicador en paso() si rebotaria
    rebote = paso(
        costo=c,
        fijo=Decimal("0"),
        envio=l_env,
        isr_tasa=tasa_r,
        goal=goal,
        iva_divisor=Decimal("1"),
        incluye_iva=False,
        ref=f_eff / p,
        moneda="MXN",
        tolerancia=Decimal("0.01"),
        cotizaciones=(cot1,),
        fee_variable_multiplicador=Decimal("1"),
    )
    assert hasattr(rebote, "intento") and rebote.intento == 2


def test_siembra_referencia_usa_retencion_iva_s4():
    """siembra._referencia incluye retencion_iva y fee_variable efectiva en S4."""
    lectura = LecturaHoy(
        m_pct=Decimal("42.2301"),
        p_actual=Decimal("989.0000"),
        moneda="MXN",
        componentes=[
            {
                "nombre": "precio_bruto",
                "importe_normalizado": "989.0000",
                "pertenece_a_total": True,
            },
            {
                "nombre": "ingreso_normalizado",
                "importe_normalizado": "989.0000",
                "pertenece_a_total": True,
            },
            {
                "nombre": "costo_normalizado",
                "importe_normalizado": "242.4700",
                "pertenece_a_total": True,
            },
            {"nombre": "logistica", "importe_normalizado": "91.0000", "pertenece_a_total": True},
            {"nombre": "isr", "importe_normalizado": "21.3147", "pertenece_a_total": True},
            {
                "nombre": "retencion_iva",
                "importe_normalizado": "68.2069",
                "pertenece_a_total": True,
            },
        ],
        fee_total=Decimal("127.8900"),
        fee_quoted=Decimal("989.0000"),
        fee_variable=Decimal("148.3524"),
        fee_fijo=Decimal("0.0000"),
        fee_estado="success",
    )
    p_est, _p_act, motivo = _referencia(lectura, Decimal("0.4223"))
    assert motivo is None
    assert p_est is not None
    assert abs(p_est.valor - Decimal("989.00")) <= Decimal("0.05")


def test_reancle_preserva_precio_objetivo_exacto():
    """Re-anclar un goal S3 a S4 mantiene P* intacto en Amazon MX y MeLi."""
    # Caso 1: goal sembrado con margen de hoy (Amazon MX FBM)
    m_s3, m_s4, goal_s4, p3, p4 = calcular_goal_equivalente_s4(
        platform="amazon_mx",
        goal_s3=Decimal("0.4339"),
        p_actual=Decimal("989.00"),
        costo=Decimal("242.47"),
        fee_raw_total=Decimal("127.89"),
        fee_var_raw=Decimal("127.89"),
        fee_fijo=Decimal("0.00"),
        envio=Decimal("91.00"),
    )
    assert m_s3 == Decimal("0.4339")
    assert m_s4 == Decimal("0.4223")
    assert goal_s4 == Decimal("0.4223")
    assert p3 == Decimal("989.00")
    assert abs(p4 - Decimal("989.00")) <= Decimal("0.05")

    # Caso 2: goal distinto al margen actual (Amazon MX FBA con meta manual 48%)
    _, _, _, p3_fba, p4_fba = calcular_goal_equivalente_s4(
        platform="amazon_mx",
        goal_s3=Decimal("0.4800"),
        p_actual=Decimal("1488.00"),
        costo=Decimal("309.82"),
        fee_raw_total=Decimal("256.41"),
        fee_var_raw=Decimal("192.41"),
        fee_fijo=Decimal("64.00"),
        envio=Decimal("0.00"),
    )
    assert abs(p4_fba - p3_fba) <= Decimal("0.50")

    # Caso 3: MeLi con meta 35%
    _, _, _, p3_meli, p4_meli = calcular_goal_equivalente_s4(
        platform="meli",
        goal_s3=Decimal("0.3500"),
        p_actual=Decimal("1488.00"),
        costo=Decimal("372.00"),
        fee_raw_total=Decimal("290.16"),
        fee_var_raw=Decimal("290.16"),
        fee_fijo=Decimal("0.00"),
        envio=Decimal("84.50"),
    )
    assert abs(p4_meli - p3_meli) <= Decimal("0.50")


def _sembrar_base_s4(conn: psycopg.Connection) -> tuple[int, int, int, int, date, datetime]:
    """Siembra producto, listing FBA MX, costo validado, oferta y fee crudo."""
    hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    ahora = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0].replace(tzinfo=UTC)
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-S4-1', 'Prod S4') RETURNING id"
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_mx', 'B0S4TEST01', 'SKU-S4-1') RETURNING id",
        (pid,),
    ).fetchone()[0]
    run_id = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, ok)"
        " VALUES (%s, %s, %s, true) RETURNING id",
        (SOURCE_COSTOS, ahora, ahora),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, valid_from,"
        " includes_tax, ingest_run_id)"
        " VALUES (%s, 309.8200, 'MXN', '2026-01-01', false, %s)",
        (pid, run_id),
    )
    oid = conn.execute(
        "INSERT INTO estimacion_oferta_observation"
        " (listing_id, platform, seller_sku, asin, canal, price_amount, price_currency,"
        "  fetched_at, observed_at, source_event_id, canonical_input, context_fingerprint)"
        " VALUES (%s, 'amazon_mx', 'SKU-S4-1', 'B0S4TEST01', 'fba', 1488.0000, 'MXN',"
        "  %s, %s, 'evt-of-s4', '{}'::jsonb, 'fp-s4') RETURNING id",
        (lid, ahora, ahora),
    ).fetchone()[0]
    fid = conn.execute(
        "INSERT INTO estimacion_fee_observation"
        " (oferta_observation_id, listing_id, platform, seller_sku, asin, canal,"
        "  quoted_price_amount, quoted_price_currency, total_fees, fee_details,"
        "  fees_estimated_at, fetched_at, observed_at, estado, source_event_id,"
        "  canonical_input, context_fingerprint)"
        " VALUES (%s, %s, 'amazon_mx', 'SKU-S4-1', 'B0S4TEST01', 'fba',"
        "  1488.0000, 'MXN', 256.4100, %s, %s, %s, %s, 'success', 'evt-fee-s4',"
        "  '{}'::jsonb, 'fp-s4') RETURNING id",
        (
            oid,
            lid,
            Json(
                [
                    {"fee_type": "ReferralFee", "final_fee": "192.4100"},
                    {"fee_type": "FBAFees", "final_fee": "64.0000"},
                ]
            ),
            ahora,
            ahora,
            ahora,
        ),
    ).fetchone()[0]
    return pid, lid, oid, fid, hoy, ahora


@_skip_db
def test_migracion_0093_ida_reversa_y_escenario_s4_en_pg16():
    """Prueba real en PG16: 0093, trigger cierre unico, escenario S4, reader y ambas reversas."""
    sonda = next(
        line.removeprefix("-- sonda-aplicada: ").strip()
        for line in SQL_0093.splitlines()[:10]
        if line.startswith("-- sonda-aplicada:")
    )
    with db_desde_template(prefijo="orbit_s4_0093", base=_test_dsn(), autocommit=True) as (
        conn,
        _,
    ):
        assert conn.execute(sonda).fetchone()[0] is True

        # Trigger: prohibe mutar settings o borrar; solo permite cerrar valid_to una vez.
        pol_s4_id = conn.execute(
            "SELECT id FROM estimacion_politica_version"
            " WHERE label = 'amazon_mx_fba_s4_caja_real_2026_11'"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE estimacion_politica_version SET label = 'hack' WHERE id = %s",
                (pol_s4_id,),
            )
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM estimacion_politica_version WHERE id = %s", (pol_s4_id,))

        # Reversa sin escenarios referenciados -> restaura limpio y sonda vuelve a False.
        conn.execute(SQL_0093_REV)
        assert conn.execute(sonda).fetchone()[0] is False
        abiertas_s3 = conn.execute(
            "SELECT count(*) FROM estimacion_politica_version"
            " WHERE universo = 'amazon_mx/fba' AND formula_version = 'S3' AND valid_to IS NULL"
        ).fetchone()[0]
        assert abiertas_s3 == 1

        # Re-aplicar 0093 y sembrar escenario S4 real end-to-end.
        conn.execute(SQL_0093)
        assert conn.execute(sonda).fetchone()[0] is True
        _, lid, oid, fid, hoy, ahora = _sembrar_base_s4(conn)
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
        assert esc.contribucion == Decimal("756.2947")
        assert esc.contribucion_pct == Decimal("50.8263")
        assert esc.fee_variable == Decimal("223.1956")
        assert esc.fee_fijo == Decimal("64.0000")
        persistir_escenario(conn, esc)

        # Proyeccion S5 informa base precio_bruto en S4.
        leidos = leer_escenarios(conn, [lid], as_of=ahora)
        sobre = proyeccion_s5(leidos[0])
        assert sobre["escenario"]["version_formula"] == "S4"
        assert sobre["base_porcentaje"] == "precio_bruto"

        # Reader construye CuentaLeida S4 coherente con motor de precios.
        cuenta = leer_cuenta(conn, lid, as_of=ahora)
        assert not isinstance(cuenta, str) and cuenta is not None
        assert cuenta.iva_divisor == Decimal("1")
        assert cuenta.precio_incluye_iva is False
        assert cuenta.referral_fee_iva_multiplicador == Decimal("1.16")
        assert cuenta.componentes.fees.valor == Decimal("287.1956")
        assert cuenta.componentes.isr.valor == Decimal("134.6897")

        # Reversa CON escenario S4 existente: conserva historial y abre continuacion S3.
        conn.execute(SQL_0093_REV)
        assert conn.execute(sonda).fetchone()[0] is False
        assert (
            conn.execute(
                "SELECT count(*) FROM estimacion_escenario WHERE formula_version = 'S4'"
            ).fetchone()[0]
            == 1
        )


@_skip_db
def test_reancle_goals_s4_en_pg16():
    """Construye y aplica el re-anclaje S3 -> S4 via goals_write sin SQL crudo."""
    with db_desde_template(prefijo="orbit_reancle_s4", base=_test_dsn(), autocommit=True) as (
        conn,
        _,
    ):
        # Volver temporalmente a S3 para sembrar escenario S3 y goal S3.
        conn.execute(SQL_0093_REV)
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('test', %s)",
            (Json({"precio_goal_min_pct": "0.10", "precio_goal_max_pct": "0.60"}),),
        )
        _, lid, oid, fid, hoy, ahora = _sembrar_base_s4(conn)
        esc_s3 = sembrar_escenario_desde_refs(
            conn,
            listing_id=lid,
            oferta_observation_id=oid,
            fee_observation_id=fid,
            valoracion_date=hoy,
            observed_at=ahora,
        )
        assert esc_s3.formula_version == "S3"
        assert esc_s3.estado == "disponible"
        persistir_escenario(conn, esc_s3)

        # Sembrar goal vigente en base S3 (~53.36%).
        from app.precio.goals_write import sembrar_goal

        gid_s3 = sembrar_goal(
            conn,
            listing_id=lid,
            platform="amazon_mx",
            goal_pct="53.36",
            mode="live",
            go_literal="GO-TEST-S3",
            creado_por="test",
            origen="cambio_de_modo",
            m_referencia=Decimal("0.5336"),
        )

        plan, huella = construir_plan_reancle(conn, plataformas=("amazon_mx",))
        assert len(plan) == 1
        assert len(huella) == 16
        assert plan[0].goal_id == gid_s3
        assert plan[0].goal_s4 == Decimal("0.5083")
        assert not plan[0].fuera_de_banda

        escritos = aplicar_reancle(conn, plan=plan, go_literal="GO-REANCLE-S4")
        assert escritos == 1
        viejo = conn.execute(
            "SELECT valid_to FROM precio_goal WHERE id = %s", (gid_s3,)
        ).fetchone()[0]
        assert viejo is not None
        nuevo = conn.execute(
            "SELECT margen_goal_pct, mode, creado_por, m_referencia FROM precio_goal"
            " WHERE listing_id = %s AND valid_to IS NULL",
            (lid,),
        ).fetchone()
        assert nuevo == (Decimal("0.5083"), "live", "reancle_s4", Decimal("0.5083"))
