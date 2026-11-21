"""Pruebas de regresion de Fase 2.5: precio, estimacion, costs, evaluacion y productos."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import costs, evaluacion_catalogo
from app.estimacion_universo import Universo, logistica_del_universo
from app.optimizer import productos
from app.precio import fuentes as precio_fuentes
from app.precio.objetivo import precio_estrella
from app.precio.puerto import Miembro, miembro_que_manda
from app.precio.tipos import Cuenta, EnvioIncluido, Fees, Importe, TasaFx
from tests.helpers.pg import db_desde_template, pg_disponible

_skip_pg = pytest.mark.skipif(not pg_disponible(), reason="sin Postgres local")


def test_miembro_que_manda_usa_ref_sobre_precio_bruto_con_iva():
    """A11: con IVA 1.16, ref = variable / P (no variable / I) para precio_estrella."""
    # P = 116, I = 100 (iva_divisor=1.16), fee variable = 17.40 (15% de P, no 17.4% de I)
    m1 = Miembro(product_id=1, seller_sku="SKU-A", variante=None)
    m2 = Miembro(product_id=2, seller_sku="SKU-B", variante=None)

    def _cuenta(costo: Decimal, fee_var: Decimal) -> Cuenta:
        precio = Importe(Decimal("116.0000"), "MXN")
        ingreso = Importe(Decimal("100.0000"), "MXN")
        return Cuenta(
            precio=precio,
            ingreso=ingreso,
            costo=Importe(costo, "MXN"),
            fees=Fees(
                total=Importe(fee_var, "MXN"),
                variable=Importe(fee_var, "MXN"),
                fijo=Importe(Decimal("0.0000"), "MXN"),
                impuesto_pendiente=False,
            ),
            envio=EnvioIncluido(Importe(Decimal("0.0000"), "MXN")),
            retencion=Importe(Decimal("2.5000"), "MXN"),
        )

    c1 = _cuenta(Decimal("50.0000"), Decimal("17.4000"))
    c2 = _cuenta(Decimal("45.0000"), Decimal("17.4000"))
    ganador, _ = miembro_que_manda([(m1, c1), (m2, c2)], lambda _m: Decimal("0.20"))
    assert ganador == m1

    # Verifica que con ref alto donde ref_sobre_I haria denominador <= 0, ref_sobre_P si resuelve.
    c_alto = _cuenta(Decimal("20.0000"), Decimal("69.6000"))  # 60% de P, pero 69.6% de I
    # Con goal=0.20, r=0.025, d=1.16: (1 - 0.025 - 0.20)/1.16 = 0.6681
    # ref sobre P = 0.60 (< 0.6681 -> valido); ref sobre I = 0.696 (> 0.6681 -> explotaba!)
    est = precio_estrella(
        c_alto.costo.valor,
        Decimal("0"),
        Decimal("0"),
        Decimal("0.025"),
        Decimal("0.20"),
        Decimal("1.16"),
        True,
        Decimal("0.60"),
    )
    assert est > 0
    ganador_alto, _ = miembro_que_manda([(m1, c_alto)], lambda _m: Decimal("0.20"))
    assert ganador_alto == m1


def test_costs_motivo_rechazo_no_crashea_con_float_gigante():
    """costs.py: validar _MAX_COSTO antes de quantize evita InvalidOperation con floats enormes."""
    fila = costs.FilaCosto(
        sku="SKU-GIGANTE",
        costo=1e30,
        moneda="MXN",
        inicio="2026-01-01",
        fin=None,
        creado="2026-01-01",
    )
    motivo = costs._motivo_rechazo(fila)
    assert motivo == "costo fuera de rango NUMERIC(14,4)"


def test_evaluacion_catalogo_clicks_es_int():
    """evaluacion_catalogo: clicks debe ser int, no Decimal."""
    obs = [
        evaluacion_catalogo.ObservacionAds(
            metric_date=dt.date(2026, 8, 1),
            observed_at=dt.datetime(2026, 9, 5, tzinfo=dt.UTC),
            cost=Decimal("10.00"),
            clicks=7,
            sales30d=Decimal("50.00"),
            purchases30d=2,
            attributed_sales_same_sku30d=Decimal("50.00"),
        )
    ]
    res = evaluacion_catalogo.evaluar_ads(
        obs, (dt.date(2026, 8, 1), dt.date(2026, 8, 30)), moneda="MXN"
    )
    assert res.clicks == 7
    assert type(res.clicks) is int


def test_productos_venta_reciente_vacia_no_cae_a_dias_halo():
    """productos.py: con ventana_venta_reciente presente y dias_recientes=(), no usa dias_halo."""
    hoy = dt.datetime(2026, 9, 15, tzinfo=dt.UTC)
    v_rec = productos.Ventana(dt.date(2026, 8, 16), dt.date(2026, 9, 15))
    dia_halo = productos.DiaProducto(
        fecha=dt.date(2026, 9, 1),
        gasto=productos.Dinero(Decimal("10"), "MXN"),
        clics=5,
        compras=Decimal("1"),
        ventas=productos.Dinero(Decimal("100"), "MXN"),
        ventas_mismo_sku=productos.Dinero(Decimal("100"), "MXN"),
    )
    entrada = SimpleNamespace(
        producto=productos.Producto(plataforma="amazon_mx", product_id=1),
        decidido_el=hoy,
        dias_halo=(dia_halo,),
        dias_recientes=(),
        ventana_venta_reciente=v_rec,
    )
    assert productos.venta_reciente_producto(entrada) is None


@_skip_pg
def test_logistica_del_universo_resuelve_fx_propio_cuando_costo_no_tuvo_fx():
    """A11: envio en MXN para amazon_us/fbm con costo USD (fx=None) usa resolver_fx_envio."""
    with db_desde_template("orbit_f2_fx_envio") as (conn, _dsn):
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('SKU-US-FBM', 'Prod US') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO precio_envio_muestra (product_id, platform, canal, origen, valor,"
            " valor_currency, envios, donantes, ventana_desde, ventana_hasta, regla_version)"
            " VALUES (%s, 'amazon_us', 'fbm', 'propio', 200.0000, 'MXN', 5, NULL,"
            " '2026-08-01', '2026-08-31', 'v1')",
            (pid,),
        )
        conn.commit()

        uni = Universo(
            plataforma="amazon_us",
            canal="fbm",
            moneda_venta="USD",
            cotizador="amazon",
            envio="muestra",
            fx_max_dias=7,
        )
        tasa = TasaFx(
            base="USD",
            quote="MXN",
            tasa=Decimal("20.0000"),
            fecha=dt.date(2026, 9, 1),
            fuente="test",
        )
        res = logistica_del_universo(
            conn,
            uni,
            product_id=pid,
            politica_settings={},
            fx=None,
            resolver_fx_envio=lambda b, q: tasa if (b, q) == ("USD", "MXN") else None,
        )
        assert not isinstance(res, str)
        assert res.importe == Importe(Decimal("10.0000"), "USD")


@_skip_pg
def test_historial_margenes_excluye_cambios_sin_efecto():
    """A11: precio_cambio con estado='error' y confirmado_por sin_efecto se excluye."""
    # 1) Verifica que el SQL real de historial_margenes corre limpio sobre el esquema Postgres 16.
    with db_desde_template("orbit_f2_hist") as (conn, _dsn):
        assert precio_fuentes.historial_margenes(conn, 999999, "amazon_mx") == ()

    # 2) Verifica que las filas `sin_efecto` se filtran y las confirmadas se conservan.
    class _FakeConn:
        def execute(self, _sql, _params):
            filas = [
                (
                    Decimal("0.15"),
                    Decimal("0.25"),
                    dt.date(2026, 9, 1),
                    Decimal("100"),
                    Decimal("110"),
                    True,
                    "error",
                    "observacion",
                ),
                (
                    Decimal("0.18"),
                    Decimal("0.25"),
                    dt.date(2026, 9, 2),
                    Decimal("100"),
                    Decimal("108"),
                    True,
                    "confirmado",
                    "observacion",
                ),
            ]
            return type("_C", (), {"fetchall": lambda _s: filas})()

    puntos = precio_fuentes.historial_margenes(_FakeConn(), 1, "amazon_mx")
    assert len(puntos) == 1
    assert puntos[0].fecha == dt.date(2026, 9, 2)
    assert puntos[0].distancia == Decimal("0.07")
