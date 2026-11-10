"""Sonda B (angulo M2): meta directa de producto, entradas extremas y caida del respaldo.

Pura: sin base. Correr con
  cd <orbit07-wt> && PYTHONPATH=. /Users/dn/dev/goncloud-Orbit/.venv/bin/python <esta sonda>
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from app.optimizer import goals as g
from app.optimizer import politica as pol
from app.optimizer import productos as p
from app.optimizer.caso import (
    BidVigente,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EvidenciaNivel,
    InsumosPausa,
    PrecioVentana,
    Tramo,
    Trayectoria,
)

AHORA = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.UTC)
HOY = AHORA.date()


def medicion(margen, *, cobertura=D("1"), dias=60, fresco=AHORA, cubierta=D("1000")):
    return g.MedicionMargen(
        margen_neto_pct=margen,
        cobertura=cobertura,
        dias_con_venta=dias,
        venta_cubierta=cubierta,
        ledger_fresco_at=fresco,
        moneda="MXN",
        ventana_desde=dt.date(2026, 2, 20),
        ventana_hasta=HOY - dt.timedelta(days=15),
    )


GRUPO = p.GrupoAptoParaMeta(
    grupo_id=6,
    campana_id=5,
    hojas=(7,),
    ventana_evidencia_hojas=p.Ventana(HOY - dt.timedelta(days=90), HOY - dt.timedelta(days=3)),
    referencias_identidad_y_halo=(),
    huella_alcance="sha256:x",
)


def entrada(margen, fraccion=D("0.5")):
    return p.EntradaProducto(
        producto=p.Producto("amazon_mx", 4),
        decidido_el=AHORA,
        margen=margen,
        fraccion=fraccion,
        ventana_halo=p.Ventana(HOY - dt.timedelta(days=90), HOY - dt.timedelta(days=60)),
        dias_halo=(),
        actividad_pausa=p.SinDato(("sin_filas_en_ventana",)),
        anuncios=(),
        grupos_aptos_para_meta=(GRUPO,),
        impedimentos=(),
        limites_pausa=p.LimitesPausa(100, p.Dinero(D("500"), "MXN")),
    )


print("== 1. Entradas extremas -> meta -> peldano de la cascada (M2, sin rampa) ==")
casos = {
    "margen 0.0001 %": medicion(D("0.0001")),
    "margen 0": medicion(D("0")),
    "margen -5": medicion(D("-5")),
    "margen 150 %": medicion(D("150")),
    "margen None (vista NULL)": medicion(None),
    "cobertura 0.90": medicion(D("8"), cobertura=D("0.90")),
    "dias con venta 10": medicion(D("8"), dias=10),
    "ledger de hace 4 dias": medicion(D("8"), fresco=AHORA - dt.timedelta(days=4)),
    "venta_cubierta 0": medicion(D("8"), cubierta=D("0")),
    "producto sin fila (SinDato)": p.SinDato(("sin_margen",)),
}
for nombre, m in casos.items():
    ev = p.resolver_producto(entrada(m))
    res = p.meta_de_hoja(
        ev.meta, ultimo=D("25"), destino=D("25"), tiene_previo=True, version=p.POLITICA_ECONOMIA
    )
    try:
        t = g.cascada_target_acos(
            None, D("30"), D("25"), "platform", None, target_margen_producto=res.aplicado
        )
        pel = g.peldano_target_acos(
            None, "platform", D("25"), D("30"), None, target_margen_producto=res.aplicado
        )
        salida = f"target={t} peldano={pel}"
    except Exception as exc:  # noqa: BLE001
        salida = f"EXCEPCION {type(exc).__name__}: {exc}"
    meta = ev.meta.porcentaje if isinstance(ev.meta, p.MetaReferencia) else ev.meta
    print(f"  {nombre:28s} meta={meta} aplicado={res.aplicado} -> {salida}")


print()
print("== 2. Bajada directa vs rampa: previo 25 %, meta nueva 4 % ==")
ev = p.resolver_producto(entrada(medicion(D("8"))))
for version in (p.ECONOMIA_AUTO03, p.POLITICA_ECONOMIA):
    r = p.meta_de_hoja(ev.meta, ultimo=D("25"), destino=D("25"), tiene_previo=True, version=version)
    print(f"  {version:20s} aplicado={r.aplicado}")


print()
print("== 3. Ledger sin refrescar 4 dias: el producto (meta 4 %) pierde elegibilidad ==")
rancio = p.resolver_producto(entrada(medicion(D("8"), fresco=AHORA - dt.timedelta(days=4))))
print(f"  motivos de la evaluacion: {rancio.motivos}")
# Peldano de plataforma ese mismo ciclo: el mismo ledger esta rancio y converge
# 0.5 desde su ultimo aplicado (25) hacia el setting (30), como _resuelve_target_ciclo.
plat = g.resuelve_target_margen(
    medicion(D("50"), fresco=AHORA - dt.timedelta(days=4)), D("0.5"), HOY, D("25"), D("30")
)
print(f"  plataforma: aplicado={plat.aplicado} motivo={plat.motivo} (margen_neto_pct al caso = None)")
for version in (p.ECONOMIA_AUTO03, p.POLITICA_ECONOMIA):
    r = p.meta_de_hoja(
        rancio.meta, ultimo=D("4"), destino=plat.aplicado, tiene_previo=True, version=version
    )
    t = g.cascada_target_acos(
        None, D("30"), plat.aplicado, "platform", None, target_margen_producto=r.aplicado
    )
    pel = g.peldano_target_acos(
        None, "platform", plat.aplicado, D("30"), None, target_margen_producto=r.aplicado
    )
    print(f"  {version:20s} peldano producto={r.aplicado} -> target de la hoja={t} ({pel})")


print()
print("== 4. Misma keyword vendedora (ACoS 10 %), veredicto segun el target que gane ==")


def caso(target: str, equilibrio: str | None, bid="1.00") -> CasoHoja:
    propia = EvidenciaNivel(
        reciente=Tramo(clics=400, pedidos=20, venta=D("2000"), gasto=D("200"), impresiones=20000),
        antiguo=Tramo(clics=0, pedidos=0, venta=D("0"), gasto=D("0"), impresiones=0),
    )
    return CasoHoja(
        plataforma="amazon_mx",
        hoja_id=7,
        ad_group_id=6,
        bid=BidVigente(valor=D(bid), moneda="MXN", piso=D("1.00"), techo=D("45.00")),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda="MXN",
                equilibrio_acos_pct=D(equilibrio) if equilibrio is not None else None,
                gasto_para_concluir=D("500"),
                confianza_recorte=D("0.80"),
                confianza_subida=D("0.70"),
            ),
            target_acos_pct=D(target),
        ),
        propia=propia,
        pedidos_inmaduros=0,
        grupo=propia,
        cuenta=propia,
        precio=PrecioVentana(gasto=D("200"), clics=400),
        trayectoria=Trayectoria(cambios=(), efecto=None),
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=100,
            gasto_minimo=D("500"),
            expected_clicks=None,
            politica_economica=None,
        ),
        ventana_desde=HOY - dt.timedelta(days=90),
        ventana_hasta=HOY - dt.timedelta(days=10),
        observado_al=AHORA - dt.timedelta(days=3),
    )


print(f"  M2 elegible (target 4, equilibrio 8):        {pol.decide(caso('4', '8', bid='2.00'))}")
print(f"  M2 ledger rancio (target 25.5, equilibrio -): {pol.decide(caso('25.5', None, bid='2.00'))}")
print(f"  AUTO-03 ledger rancio (target 4.5, eq. -):   {pol.decide(caso('4.5', None, bid='2.00'))}")

print()
print("== 5. Topes despues de la meta directa (target 0.00005 %) ==")
print(f"  bid 2.00, piso 1.00: {pol.decide(caso('0.00005', '0.0001', bid='2.00'))}")
print(f"  bid 1.20, piso 1.00: {pol.decide(caso('0.00005', '0.0001', bid='1.20'))}")
print(f"  bid 1.00 (en piso):  {pol.decide(caso('0.00005', '0.0001', bid='1.00'))}")
