"""AUTO-03 C.5 y C.7 (dinero): la meta por producto y los limites de su pausa.

La meta decide el target de las pujas de grupos exclusivos (bloque F) y los
limites deciden cuando se pausa un anuncio (bloque H): cada caso fija el
numero que llega a la puja o al corte.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from app import config_write
from app.optimizer import goals as g
from app.optimizer import productos as p

DECIDIDO = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.UTC)
HOY = DECIDIDO.date()
GRUPO = p.GrupoAptoParaMeta(
    grupo_id=501,
    ventana_evidencia_hojas=p.Ventana(dt.date(2026, 8, 8), dt.date(2026, 11, 4)),
    referencias_identidad_y_halo=("ad_entity:501",),
    huella_alcance="sha256:abc",
)


def _medicion(margen: str | None) -> g.MedicionMargen:
    return g.MedicionMargen(
        margen_neto_pct=None if margen is None else Decimal(margen),
        cobertura=Decimal("0.98"),
        dias_con_venta=40,
        venta_cubierta=Decimal("12000"),
        ledger_fresco_at=DECIDIDO - dt.timedelta(days=1),
        moneda="MXN",
        ventana_desde=HOY - dt.timedelta(days=105),
        ventana_hasta=HOY - dt.timedelta(days=15),
    )


def _mxn(valor: str) -> p.Dinero:
    return p.Dinero(Decimal(valor), "MXN")


def _dia(fecha: dt.date, ventas: str, mismo_sku: str) -> p.DiaProducto:
    return p.DiaProducto(fecha, _mxn("80"), 40, Decimal("1"), _mxn(ventas), _mxn(mismo_sku))


def _entrada(
    margen: str | None = "40",
    fraccion: str = "0.5",
    *,
    grupos: tuple[p.GrupoAptoParaMeta, ...] = (GRUPO,),
    dias: tuple[p.DiaProducto, ...] = (),
    impedimentos: tuple[str, ...] = (),
) -> p.EntradaProducto:
    return p.EntradaProducto(
        producto=p.Producto("amazon_mx", 77),
        decidido_el=DECIDIDO,
        margen=_medicion(margen),
        fraccion=Decimal(fraccion),
        ventana_halo=p.Ventana(dt.date(2026, 8, 8), dt.date(2026, 11, 4)),
        dias_halo=dias,
        grupos_aptos_para_meta=grupos,
        impedimentos=impedimentos,
        limites_pausa=p.limites_pausa_desde_settings({}, "amazon_mx"),
    )


def test_margen_40_con_fraccion_05_da_meta_20():
    ev = p.resolver_producto(_entrada("40", "0.5"))
    assert ev.meta == p.MetaReferencia(Decimal("20.0"), (GRUPO,))
    assert ev.motivos == ("halo_sin_madurar",)
    assert ev.politica == "v1"


def test_margen_8_con_fraccion_05_da_meta_4_sin_piso_de_10():
    ev = p.resolver_producto(_entrada("8", "0.5"))
    assert ev.meta.porcentaje == Decimal("4.0")
    plataforma = g.resuelve_target_margen(_medicion("8"), Decimal("0.5"), HOY, None, None, 30)
    assert plataforma.aplicado == Decimal("10")


def test_meta_topada_en_45():
    ev = p.resolver_producto(_entrada("80", "0.8"))
    assert ev.meta.porcentaje == Decimal("45")


def test_goal_con_target_gana_a_la_meta_del_producto():
    assert g.cascada_target_acos(
        Decimal("18"),
        Decimal("30"),
        Decimal("12"),
        "campaign",
        Decimal("20"),
        target_margen_producto=Decimal("4"),
    ) == Decimal("18")
    assert (
        g.peldano_target_acos(
            Decimal("18"),
            "campaign",
            Decimal("12"),
            Decimal("30"),
            Decimal("20"),
            target_margen_producto=Decimal("4"),
        )
        == "goal_campana"
    )
    sin_goal = (None, Decimal("30"), Decimal("12"), None, Decimal("20"))
    assert g.cascada_target_acos(*sin_goal, target_margen_producto=Decimal("4")) == Decimal("4")
    assert (
        g.peldano_target_acos(
            None,
            None,
            Decimal("12"),
            Decimal("30"),
            Decimal("20"),
            target_margen_producto=Decimal("4"),
        )
        == "margen_producto"
    )
    assert g.cascada_target_acos_con_procedencia(
        None,
        None,
        {},
        None,
        "amazon_mx",
        Decimal("12"),
        Decimal("20"),
        target_margen_producto=Decimal("4"),
    ) == (Decimal("4"), "margen_producto")


def test_grupo_no_apto_queda_sin_meta():
    evs = (p.resolver_producto(_entrada("8")),)
    assert p.meta_para_grupo(evs, 501).porcentaje == Decimal("4.0")
    assert p.meta_para_grupo(evs, 999) == p.SinDato(("grupo_no_apto",))
    sin_aptos = p.resolver_producto(_entrada("8", grupos=()))
    assert sin_aptos.meta == p.MetaReferencia(Decimal("4.0"), ())
    assert "sin_grupo_apto" in sin_aptos.motivos
    assert p.meta_para_grupo((sin_aptos,), 501) == p.SinDato(("grupo_no_apto",))


def test_con_ancla_sube_de_una_vez_y_baja_medio_punto():
    meta_20 = p.MetaReferencia(Decimal("20"), (GRUPO,))
    meta_4 = p.MetaReferencia(Decimal("4"), (GRUPO,))
    sube = p.meta_de_hoja(meta_20, ultimo=Decimal("10"), destino=None, tiene_previo=True)
    assert (sube.aplicado, sube.motivo) == (Decimal("20"), None)
    baja = p.meta_de_hoja(meta_4, ultimo=Decimal("6"), destino=None, tiene_previo=True)
    assert baja.aplicado == Decimal("5.5")
    llega = p.meta_de_hoja(meta_4, ultimo=Decimal("4.2"), destino=None, tiene_previo=True)
    assert llega.aplicado == Decimal("4")
    sin_ancla = p.meta_de_hoja(meta_4, ultimo=None, destino=None, tiene_previo=False)
    assert sin_ancla.aplicado == Decimal("4")


def test_previo_de_otro_peldano_no_es_ancla():
    """S2: entrar desde familia, plataforma o setting en 20 aplica la meta 4 de
    una vez; solo un previo margen_producto frena la bajada."""
    meta_4 = p.resolver_producto(_entrada("8")).meta
    entra = p.meta_de_hoja(meta_4, ultimo=Decimal("20"), destino=Decimal("20"), tiene_previo=False)
    assert (entra.aplicado, entra.derivado, entra.motivo) == (Decimal("4.0"), Decimal("4.0"), None)
    sigue = p.meta_de_hoja(meta_4, ultimo=Decimal("20"), destino=Decimal("20"), tiene_previo=True)
    assert sigue.aplicado == Decimal("19.5")


def test_dato_invalido_con_ancla_converge_al_destino():
    abstiene = p.SinDato(("ledger_rancio",))
    res = p.meta_de_hoja(abstiene, ultimo=Decimal("4"), destino=Decimal("20"), tiene_previo=True)
    assert (res.aplicado, res.motivo, res.convergiendo) == (Decimal("4.5"), "ledger_rancio", True)
    cae = p.meta_de_hoja(abstiene, ultimo=Decimal("4"), destino=Decimal("20"), tiene_previo=False)
    assert (cae.aplicado, cae.motivo) == (None, "ledger_rancio")


@pytest.mark.parametrize(
    ("margen", "motivo"),
    [("0", "margen_no_positivo"), ("-5", "margen_no_positivo"), (None, "sin_margen")],
)
def test_margen_cero_negativo_o_ausente_deja_motivo_y_nunca_meta_cero(margen, motivo):
    ev = p.resolver_producto(_entrada(margen))
    assert ev.meta == p.SinDato((motivo,))
    res = p.meta_de_hoja(ev.meta, ultimo=None, destino=Decimal("20"), tiene_previo=False)
    assert res.aplicado is None


def test_halo_de_otro_sku_e_identidad_impiden_la_meta():
    dias = (_dia(dt.date(2026, 10, 1), "300", "300"), _dia(dt.date(2026, 10, 2), "250", "200"))
    assert p.resolver_producto(_entrada("40", dias=dias)).meta == p.SinDato(("halo_sin_margen",))
    compensa = (_dia(dt.date(2026, 10, 2), "250", "200"), _dia(dt.date(2026, 10, 3), "100", "150"))
    assert p.resolver_producto(_entrada("40", dias=compensa)).meta == p.SinDato(
        ("halo_sin_margen",)
    )
    fuera = (_dia(dt.date(2026, 8, 1), "250", "0"),)
    assert isinstance(p.resolver_producto(_entrada("40", dias=fuera)).meta, p.MetaReferencia)
    ev = p.resolver_producto(_entrada("40", impedimentos=("sku_distinto_en_asin",)))
    assert ev.meta == p.SinDato(("sku_distinto_en_asin",))


def test_evaluacion_ida_y_vuelta_por_json():
    dias = (_dia(dt.date(2026, 10, 1), "300", "300"),)
    for entrada in (_entrada("8", dias=dias), _entrada(None, grupos=())):
        ev = p.resolver_producto(entrada)
        vuelta = p.EvaluacionProducto.desde_json(json.loads(json.dumps(ev.como_json())))
        assert vuelta == ev
        assert p.resolver_producto(vuelta.entrada) == ev


def test_limites_sin_claves_dan_100_clics_y_500_mxn():
    assert p.limites_pausa_desde_settings({}, "amazon_mx") == p.LimitesPausa(100, _mxn("500"))
    assert p.limites_pausa_desde_settings({}, "amazon_us") == p.LimitesPausa(
        100, p.Dinero(Decimal("40"), "USD")
    )


def test_limites_50_clics_y_250_mxn_se_aceptan():
    nuevo, _ = config_write.proxima_config(
        {}, "amazon_mx", limites_pausa=p.LimitesPausa(50, _mxn("250"))
    )
    assert nuevo["ads_producto_pausa_clics_amazon_mx"] == "50"
    assert nuevo["ads_producto_pausa_gasto_amazon_mx"] == "250"
    assert p.limites_pausa_desde_settings(nuevo, "amazon_mx") == p.LimitesPausa(50, _mxn("250"))
    with pytest.raises(config_write.EdicionVacia):
        config_write.proxima_config(
            nuevo, "amazon_mx", limites_pausa=p.LimitesPausa(50, _mxn("250.00"))
        )


@pytest.mark.parametrize(
    "limites",
    [
        p.LimitesPausa(5, _mxn("250")),
        p.LimitesPausa(50, _mxn("50")),
        p.LimitesPausa(50, p.Dinero(Decimal("250"), "USD")),
    ],
)
def test_limites_bajo_el_piso_o_en_otra_moneda_se_rechazan(limites):
    with pytest.raises(config_write.SettingsInvalido):
        config_write.proxima_config({}, "amazon_mx", limites_pausa=limites)
