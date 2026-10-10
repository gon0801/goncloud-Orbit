"""Tests de app/optimizer/politica.py (BIDS 02 M.1): un caso por regla.

Cada regla R0 a R19 y R11b tiene un caso que la dispara con el motivo de
tabla-decision.md, y cada caso mata a su mutante (ver mutantes.md).
Sin acentos en el codigo, por regla del repo.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from test_optimizer_caso import (
    cambio,
    cortes,
    economia_mx,
    economia_us,
    efecto,
    fabrica_caso,
    nivel,
    tramo,
    trayectoria,
)

from app.optimizer.caso import (
    BidVigente,
    InsumosPausa,
    PrecioVentana,
)
from app.optimizer.politica import (
    Mantener,
    Mover,
    Pausar,
    Regresar,
    decide,
    estado_grupo,
    estima,
)

RAIZ = Path(__file__).resolve().parents[1]
DORADA = RAIZ / "tests" / "fixtures" / "politica_dorada_a1.json"
HOJA_2963 = RAIZ / "tests" / "fixtures" / "politica_2963_a2.json"


def _vendedora_fuerte():
    """Pierde dinero con certeza tambien a 1.35 x target: p 0.85 y 0.98."""
    return nivel(
        tramo(clics=200, pedidos=2, venta="1000", gasto="800", impresiones=9000),
        tramo(clics=100, pedidos=1, venta="500", gasto="400", impresiones=4500),
    )


def _vendedora_suave():
    """Pierde dinero con certeza (p 0.85) pero no a 1.35 x target (p 0.75)."""
    return nivel(tramo(clics=100, pedidos=2, venta="400", gasto="300", impresiones=4000))


def _cuenta_llena():
    return nivel(
        tramo(
            clics=10000,
            pedidos=100,
            venta="100000",
            gasto="9000",
            impresiones=400000,
        )
    )


def _grupo_sangra():
    return nivel(tramo(clics=300, pedidos=4, venta="2000", gasto="900", impresiones=9000))


def _grupo_cumple():
    return nivel(tramo(clics=500, pedidos=5, venta="8000", gasto="800", impresiones=15000))


# ---------------------------------------------------------------------------
# R0: PAUSE identico a hoy
# ---------------------------------------------------------------------------


def test_r0_pausa_por_umbral():
    caso = fabrica_caso(pausa=InsumosPausa(cortes(), 100, Decimal("350"), None, None))
    veredicto = decide(caso)
    assert veredicto == Pausar("pause_umbral")


# ---------------------------------------------------------------------------
# R1 y R2: efecto del ultimo cambio
# ---------------------------------------------------------------------------


def test_r1_regresa_tras_desplome():
    caso = fabrica_caso(
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "12.00", "10.00", "motor")],
            efecto(dias_post=10, pre_impr=5000, post_impr=100, vendia=True),
        ),
    )
    assert decide(caso) == Regresar(Decimal("12.00"), "regreso_por_desplome")


def test_r1_no_regresa_sin_filtro_de_volumen():
    caso = fabrica_caso(
        propia=nivel(tramo(gasto="800")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "12.00", "10.00", "motor")],
            efecto(
                dias_post=10,
                pre_impr=500,
                pre_clics=10,
                post_impr=10,
                clics_post=25,
                vendia=True,
            ),
        ),
    )
    assert decide(caso) == Mantener("recorte_costo_trafico")


def test_r1_no_regresa_con_razon_sobre_el_umbral():
    caso = fabrica_caso(
        propia=nivel(tramo(gasto="800")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "12.00", "10.00", "motor")],
            efecto(dias_post=10, pre_impr=7000, post_impr=2100, clics_post=25, vendia=True),
        ),
    )
    assert decide(caso) == Mantener("recorte_costo_trafico")


def test_r2_espera_antes_de_7_dias():
    caso = fabrica_caso(
        propia=nivel(tramo(gasto="800")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 10, 6), "12.00", "10.00", "motor")],
            efecto(dias_post=3, pre_impr=5000, post_impr=100, vendia=True),
        ),
    )
    assert decide(caso) == Mantener("esperando_efecto")


# ---------------------------------------------------------------------------
# R3 a R7: vendedoras
# ---------------------------------------------------------------------------


def test_r3_fuerte_recorta_25():
    caso = fabrica_caso(propia=_vendedora_fuerte(), precio=PrecioVentana(Decimal("300"), 60))
    assert decide(caso) == Mover(
        Decimal("-0.25"), Decimal("7.5000"), "pierde_dinero_fuerte", "hoja"
    )


def test_r3_suave_recorta_12_en_us():
    caso = fabrica_caso(
        plataforma="amazon_us",
        hoja_id=4924,
        bid=BidVigente(Decimal("0.50"), "USD", Decimal("0.10"), Decimal("2.00")),
        economia=economia_us(),
        propia=_vendedora_suave(),
        precio=PrecioVentana(Decimal("150"), 50),
        pausa=InsumosPausa(None, 100, Decimal("36"), None, None),
    )
    assert decide(caso) == Mover(Decimal("-0.12"), Decimal("0.4400"), "pierde_dinero", "hoja")


def test_r4_hereda_12_y_nunca_25():
    caso = fabrica_caso(
        propia=_vendedora_suave(),
        grupo=_grupo_sangra(),
        precio=PrecioVentana(Decimal("150"), 50),
    )
    assert decide(caso) == Mover(
        Decimal("-0.12"), Decimal("8.8000"), "grupo_sangra_vendedora", "ad_group"
    )


def test_r5_vende_dentro_del_margen():
    caso = fabrica_caso(
        propia=_vendedora_suave(),
        grupo=_grupo_cumple(),
        precio=PrecioVentana(Decimal("150"), 50),
    )
    assert decide(caso) == Mantener("vende_dentro_del_margen")


def test_r6_sube_con_evidencia():
    propia = nivel(tramo(clics=50, pedidos=5, venta="5000", gasto="200", impresiones=2000))
    caso = fabrica_caso(
        propia=propia,
        grupo=propia,
        cuenta=_cuenta_llena(),
        precio=PrecioVentana(Decimal("100"), 50),
    )
    assert decide(caso) == Mover(Decimal("0.15"), Decimal("11.5000"), "bajo_target", "hoja")


def test_r6_sube_con_previa_plana_si_la_cuenta_esta_vacia():
    propia = nivel(tramo(clics=50, pedidos=5, venta="5000", gasto="200", impresiones=2000))
    caso = fabrica_caso(
        propia=propia,
        grupo=propia,
        cuenta=nivel(),
        precio=PrecioVentana(Decimal("100"), 50),
    )
    assert decide(caso) == Mover(Decimal("0.15"), Decimal("11.5000"), "bajo_target", "hoja")


def test_r7_un_pedido_no_concluye():
    propia = nivel(tramo(clics=10, pedidos=1, venta="800", gasto="30", impresiones=400))
    caso = fabrica_caso(
        propia=propia,
        grupo=propia,
        cuenta=_cuenta_llena(),
        precio=PrecioVentana(Decimal("20"), 10),
    )
    assert decide(caso) == Mantener("azar_lo_explica")


# ---------------------------------------------------------------------------
# R8 a R13: sin pedidos
# ---------------------------------------------------------------------------


def test_r8_venta_reciente_gana_al_gasto():
    caso = fabrica_caso(propia=nivel(tramo(gasto="800")), pedidos_inmaduros=1)
    assert decide(caso) == Mantener("venta_reciente")


def test_r9_simple_y_doble():
    assert decide(fabrica_caso(propia=nivel(tramo(gasto="400")))) == Mover(
        Decimal("-0.12"), Decimal("8.8000"), "gasto_sin_venta", "hoja"
    )
    assert decide(fabrica_caso(propia=nivel(tramo(gasto="800")))) == Mover(
        Decimal("-0.25"), Decimal("7.5000"), "gasto_sin_venta_doble", "hoja"
    )
    assert decide(fabrica_caso(propia=nivel(tramo(gasto="350")))) == Mover(
        Decimal("-0.12"), Decimal("8.8000"), "gasto_sin_venta", "hoja"
    )
    assert decide(fabrica_caso(propia=nivel(tramo(gasto="700")))) == Mover(
        Decimal("-0.25"), Decimal("7.5000"), "gasto_sin_venta_doble", "hoja"
    )
    assert decide(fabrica_caso(propia=nivel(tramo(gasto="349.99")))) == Mantener("sin_evidencia")


def test_r10_sin_gasto():
    assert decide(fabrica_caso()) == Mantener("sin_gasto")


def test_r11_hereda_del_grupo_que_sangra():
    caso = fabrica_caso(propia=nivel(tramo(gasto="130")), grupo=_grupo_sangra())
    assert decide(caso) == Mover(Decimal("-0.12"), Decimal("8.8000"), "grupo_sangra", "ad_group")
    justo = fabrica_caso(propia=nivel(tramo(gasto="87.50")), grupo=_grupo_sangra())
    assert decide(justo) == Mover(Decimal("-0.12"), Decimal("8.8000"), "grupo_sangra", "ad_group")


def test_r11b_hoja_delgada_no_mueve_dinero():
    caso = fabrica_caso(propia=nivel(tramo(gasto="50")), grupo=_grupo_sangra())
    assert decide(caso) == Mantener("hoja_delgada_sin_dinero_que_mover")


def test_r12_grupo_cumple():
    caso = fabrica_caso(propia=nivel(tramo(gasto="130")), grupo=_grupo_cumple())
    assert decide(caso) == Mantener("grupo_cumple")


def test_r13_sin_veredicto_del_grupo():
    delgado = nivel(tramo(clics=30, pedidos=1, venta="300", gasto="40"))
    caso = fabrica_caso(propia=nivel(tramo(gasto="130")), grupo=delgado)
    assert decide(caso) == Mantener("sin_evidencia")


# ---------------------------------------------------------------------------
# R14 a R17: repetir direccion
# ---------------------------------------------------------------------------


def test_r14_invierte_solo_con_precio_medido():
    caso = fabrica_caso(
        propia=nivel(tramo(gasto="400")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "8.00", "10.00", "motor")],
            efecto(dias_post=10, clics_post=10),
        ),
    )
    assert decide(caso) == Mantener("esperando_precio_medido")


def test_r15_repite_solo_con_20_clics():
    caso = fabrica_caso(
        propia=nivel(tramo(gasto="400")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "12.00", "10.00", "motor")],
            efecto(dias_post=10, clics_post=10, vendia=False),
        ),
    )
    assert decide(caso) == Mantener("sin_clics_nuevos")


def test_r16_recorte_que_costo_trafico():
    caso = fabrica_caso(
        propia=nivel(tramo(gasto="400")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "12.00", "10.00", "motor")],
            efecto(
                dias_post=10,
                pre_impr=7000,
                post_impr=3500,
                clics_post=25,
                vendia=True,
            ),
        ),
    )
    assert decide(caso) == Mantener("recorte_costo_trafico")


def test_r16_subida_sin_trafico():
    propia = nivel(tramo(clics=50, pedidos=5, venta="5000", gasto="200", impresiones=2000))
    caso = fabrica_caso(
        propia=propia,
        grupo=propia,
        cuenta=_cuenta_llena(),
        precio=PrecioVentana(Decimal("100"), 50),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "8.00", "10.00", "motor")],
            efecto(
                dias_post=10,
                pre_impr=7000,
                post_impr=7350,
                clics_post=25,
                vendia=True,
            ),
        ),
    )
    assert decide(caso) == Mantener("subida_sin_trafico")


def test_r17_no_baja_del_piso_aprendido():
    cambios = [
        cambio(dt.date(2026, 9, 9), "5.00", "3.52", "motor"),
        cambio(dt.date(2026, 9, 19), "3.52", "4.00", "regreso_del_dueno"),
    ]
    caso = fabrica_caso(
        bid=BidVigente(Decimal("4.00"), "MXN", Decimal("0.50"), Decimal("50.00")),
        propia=nivel(tramo(gasto="400")),
        trayectoria=trayectoria(cambios, efecto(dias_post=20, clics_post=30)),
    )
    assert decide(caso) == Mantener("piso_aprendido")


# ---------------------------------------------------------------------------
# R18 y R19: dato faltante y clamps
# ---------------------------------------------------------------------------


def test_r18_dato_faltante():
    propia = nivel(
        tramo(clics=200, pedidos=2, venta="1000", gasto="800"),
        tramo(clics=100, pedidos=1, venta=None, gasto="400"),
    )
    caso = fabrica_caso(propia=propia, precio=PrecioVentana(Decimal("300"), 60))
    assert decide(caso) == Mantener("dato_faltante")
    assert decide(fabrica_caso(pedidos_inmaduros=None)) == Mantener("dato_faltante")


def test_r18_sin_precio():
    propia = nivel(tramo(pedidos=2, venta="1000", gasto="50"))
    caso = fabrica_caso(propia=propia)
    assert decide(caso) == Mantener("sin_precio")


def test_r19_rango_bloquea_ajuste():
    caso = fabrica_caso(
        bid=BidVigente(Decimal("100"), "MXN", Decimal("0.50"), Decimal("50.00")),
        propia=nivel(tramo(gasto="400")),
    )
    assert decide(caso) == Mantener("rango_bloquea_ajuste")


def test_r19_delta_bajo_umbral():
    caso = fabrica_caso(
        bid=BidVigente(Decimal("0.05"), "MXN", Decimal("0.01"), Decimal("50.00")),
        propia=nivel(tramo(gasto="400")),
    )
    assert decide(caso) == Mantener("delta_bajo_umbral")


def test_r19_bid_ausente_o_invalido_no_mueve():
    for valor in (None, Decimal("0"), Decimal("-1")):
        caso = fabrica_caso(
            bid=BidVigente(valor, "MXN", Decimal("0.50"), Decimal("50.00")),
            propia=nivel(tramo(gasto="400")),
        )
        assert decide(caso) == Mantener("dato_faltante")


# ---------------------------------------------------------------------------
# Regreso del dueno: R2, R14, recorte con 20 clics, recorte al dia 14, R17
# ---------------------------------------------------------------------------


def _caso_dueno(dias_post, clics_post):
    return fabrica_caso(
        bid=BidVigente(Decimal("5.00"), "MXN", Decimal("0.50"), Decimal("50.00")),
        propia=nivel(tramo(gasto="400")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "3.50", "5.00", "regreso_del_dueno")],
            efecto(dias_post=dias_post, clics_post=clics_post),
        ),
    )


def test_dueno_r2_antes_de_7_dias():
    assert decide(_caso_dueno(3, 5)) == Mantener("esperando_efecto")


def test_dueno_r14_al_dia_8_con_19_clics():
    assert decide(_caso_dueno(8, 19)) == Mantener("esperando_precio_medido")


def test_dueno_recorte_con_20_clics():
    assert decide(_caso_dueno(10, 20)) == Mover(
        Decimal("-0.12"), Decimal("4.4000"), "gasto_sin_venta", "hoja"
    )


def test_dueno_recorte_al_dia_14_sin_20_clics():
    assert decide(_caso_dueno(14, 5)) == Mover(
        Decimal("-0.12"), Decimal("4.4000"), "gasto_sin_venta", "hoja"
    )


def test_dueno_nunca_da_sin_clics_nuevos():
    for dias_post, clics_post in ((3, 5), (8, 19), (10, 20), (14, 5), (20, 30)):
        assert decide(_caso_dueno(dias_post, clics_post)) != Mantener("sin_clics_nuevos")


# ---------------------------------------------------------------------------
# Ajuste de campana: R2 espera y la escalera no proyecta
# ---------------------------------------------------------------------------


def _caso_ajuste(dias_post):
    propia = nivel(
        tramo(clics=40, venta="0", gasto="250", impresiones=1500),
        tramo(clics=20, venta="0", gasto="150", impresiones=800),
    )
    return fabrica_caso(
        propia=propia,
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "10.00", "10.00", "ajuste_de_campana")],
            efecto(dias_post=dias_post, clics_post=5, clics_pre=30, gasto_pre="90"),
        ),
    )


def test_ajuste_r2_espera():
    assert decide(_caso_ajuste(3)) == Mantener("esperando_efecto")


def test_ajuste_escalera_no_proyecta():
    caso = _caso_ajuste(10)
    assert estima(caso).fuente_precio == "ventana_madura"
    assert decide(caso) == Mantener("esperando_precio_medido")


# ---------------------------------------------------------------------------
# Prueba CodeRabbit: ni el regreso del dueno ni el ajuste disparan R1
# ---------------------------------------------------------------------------


def test_dueno_que_recorta_no_dispara_r1():
    caso = fabrica_caso(
        bid=BidVigente(Decimal("8.00"), "MXN", Decimal("0.50"), Decimal("50.00")),
        propia=nivel(tramo(gasto="800")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 29), "10.00", "8.00", "regreso_del_dueno")],
            efecto(
                dias_post=10,
                pre_impr=5000,
                post_impr=500,
                clics_post=25,
                vendia=True,
            ),
        ),
    )
    assert decide(caso) == Mantener("recorte_costo_trafico")


def test_ajuste_no_dispara_r1():
    caso = _caso_ajuste(10)
    veredicto = decide(caso)
    assert not isinstance(veredicto, Regresar)
    assert veredicto == Mantener("esperando_precio_medido")


# ---------------------------------------------------------------------------
# estado_grupo y estima
# ---------------------------------------------------------------------------


def test_estado_grupo_sangra_cumple_y_sin_veredicto():
    economia = economia_mx()
    assert estado_grupo(_grupo_sangra(), economia) == "sangra"
    assert estado_grupo(_grupo_cumple(), economia) == "cumple"
    assert estado_grupo(None, economia) == "sin_veredicto"
    delgado = nivel(tramo(clics=30, pedidos=1, venta="300", gasto="40"))
    assert estado_grupo(delgado, economia) == "sin_veredicto"
    intermedio = nivel(tramo(clics=300, pedidos=4, venta="2000", gasto="450"))
    assert estado_grupo(intermedio, economia) == "sin_veredicto"
    cero_pedidos = nivel(tramo(gasto="400"))
    assert estado_grupo(cero_pedidos, economia) == "sangra"
    assert estado_grupo(nivel(tramo(gasto="100")), economia) == "sin_veredicto"


def test_estado_grupo_sin_veredicto_con_dato_none():
    economia = economia_mx()
    base = tramo(clics=300, pedidos=4, venta="2000", gasto="900")
    for campo in ("pedidos", "venta", "gasto"):
        for lado in ("reciente", "antiguo"):
            envenenado = dict(
                clics=base.clics,
                pedidos=base.pedidos,
                venta=base.venta,
                gasto=base.gasto,
                impresiones=base.impresiones,
            )
            envenenado[campo] = None
            tramos = {"reciente": base, "antiguo": base}
            tramos[lado] = tramo(**envenenado)
            assert estado_grupo(nivel(**tramos), economia) == "sin_veredicto"
    assert estado_grupo(nivel(tramo(pedidos=None)), economia) == "sin_veredicto"
    assert estado_grupo(nivel(tramo(gasto=None)), economia) == "sin_veredicto"


def test_estima_no_levanta_sin_datos():
    estimacion = estima(fabrica_caso(propia=None, pedidos_inmaduros=None))
    assert estimacion.cpc is None
    assert estimacion.estado_grupo == "sin_veredicto"
    assert estimacion.p_sobre_equilibrio is None
    assert estimacion.acos_grupo_pct is None


def test_estima_expone_los_numeros_del_veredicto():
    caso = fabrica_caso(propia=_vendedora_fuerte(), precio=PrecioVentana(Decimal("300"), 60))
    estimacion = estima(caso)
    assert estimacion.cpc == Decimal("5")
    assert estimacion.fuente_precio == "ventana"
    assert estimacion.p_sobre_equilibrio >= Decimal("0.80")
    assert estimacion.estado_grupo == "sin_veredicto"


# ---------------------------------------------------------------------------
# decide lee el caso, no un global
# ---------------------------------------------------------------------------


def test_decide_usa_el_concluir_del_caso():
    gasto = nivel(tramo(gasto="150"))
    assert decide(fabrica_caso(propia=gasto)) == Mantener("sin_evidencia")
    chico = fabrica_caso(propia=gasto, economia=economia_mx(concluir="100"))
    assert decide(chico) == Mover(Decimal("-0.12"), Decimal("8.8000"), "gasto_sin_venta", "hoja")


def test_decide_usa_el_target_del_caso():
    propia = nivel(tramo(clics=50, pedidos=5, venta="5000", gasto="200", impresiones=2000))
    base = dict(propia=propia, grupo=propia, cuenta=_cuenta_llena())
    assert decide(fabrica_caso(precio=PrecioVentana(Decimal("100"), 50), **base)) == Mover(
        Decimal("0.15"), Decimal("11.5000"), "bajo_target", "hoja"
    )
    bajo = fabrica_caso(
        precio=PrecioVentana(Decimal("100"), 50),
        economia=economia_mx(target="5.00"),
        **base,
    )
    assert decide(bajo) == Mantener("azar_lo_explica")


def test_decide_usa_la_confianza_del_caso():
    caso = fabrica_caso(
        propia=_vendedora_fuerte(),
        precio=PrecioVentana(Decimal("300"), 60),
        economia=economia_mx(conf_rec="0.90"),
    )
    assert decide(caso) == Mantener("vende_dentro_del_margen")


# ---------------------------------------------------------------------------
# Prueba dorada y hoja 2963
# ---------------------------------------------------------------------------


def _esperado(entrada):
    return entrada["prototipo"]


def _revisa_veredicto(caso, esperado):
    from app.optimizer.caso import CasoHoja

    veredicto = decide(CasoHoja.desde_json(caso))
    accion = esperado["accion"]
    if accion == "mantener":
        assert veredicto == Mantener(esperado["motivo"])
    elif accion == "regresar":
        assert veredicto == Regresar(veredicto.bid_nuevo, esperado["motivo"])
        assert veredicto.bid_nuevo == CasoHoja.desde_json(caso).trayectoria.ultimo.bid_antes
    else:
        assert isinstance(veredicto, Mover), veredicto
        assert veredicto.motivo == esperado["motivo"]
        assert veredicto.factor == Decimal(str(esperado["factor"]))
        if accion == "recortar":
            assert veredicto.factor < 0
        else:
            assert veredicto.factor > 0


def test_dorada_da_el_veredicto_del_prototipo():
    bruto = json.loads(DORADA.read_text(encoding="utf-8"))
    assert bruto["casos"], "fixture dorado vacio"
    assert len(bruto["casos"]) <= 300
    for entrada in bruto["casos"]:
        _revisa_veredicto(entrada["caso"], _esperado(entrada))


def test_hoja_2963_no_recibe_cambios():
    bruto = json.loads(HOJA_2963.read_text(encoding="utf-8"))
    assert len(bruto["casos"]) == 38
    for entrada in bruto["casos"]:
        _revisa_veredicto(entrada["caso"], _esperado(entrada))
