"""Sonda C (M5/M6): juicio_repeticion y decide() con entradas extremas.

Pura: sin base. Correr con
  PYTHONPATH=<copia> /Users/dn/dev/goncloud-Orbit/.venv/bin/python -I <este archivo>
(-I ignora PYTHONPATH, asi que se inserta la copia a mano abajo).
"""

import datetime as dt
import sys
from decimal import Decimal as D

sys.path.insert(
    0,
    "/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt",
)

from app.optimizer.caso import (  # noqa: E402
    VERSION_ECONOMIA_CAMBIO,
    BidVigente,
    CambioBid,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    EpisodioEconomico,
    EvidenciaNivel,
    InsumosPausa,
    PrecioVentana,
    Tramo,
    Trayectoria,
    VentanaEconomica,
)
from app.optimizer.economia_cambio import juicio_repeticion  # noqa: E402
from app.optimizer.politica import decide  # noqa: E402

X = dt.date(2026, 10, 1)


def ven(desde, gasto, venta, pedidos, clics):
    return VentanaEconomica(
        desde=desde,
        hasta=desde + dt.timedelta(days=6),
        gasto=None if gasto is None else D(gasto),
        venta=None if venta is None else D(venta),
        venta_mismo_sku=None if venta is None else D(venta),
        pedidos=pedidos,
        clics=clics,
    )


def epi(pre, post, margen=None, estado="maduro", aplicable=True, motivo=None):
    def contrib(v):
        if margen is None or v.venta is None or v.gasto is None:
            return None
        return v.venta * D(margen) / 100 - v.gasto

    return EpisodioEconomico(
        version=VERSION_ECONOMIA_CAMBIO,
        fecha_cambio=X,
        moneda="USD",
        pre=pre,
        post=post,
        estado=estado,
        motivo=motivo,
        aplicable=aplicable,
        motivo_aplicabilidad=None if aplicable else "cambio_posterior",
        margen_pct=None if margen is None else D(margen),
        contribucion_pre=contrib(pre),
        contribucion_post=contrib(post),
    )


PRE = X - dt.timedelta(days=7)
POST = X + dt.timedelta(days=1)

print("== 1. Aritmetica extrema de juicio_repeticion (target 25) ==")
casos = {
    "pre gasto 0": (ven(PRE, "0", "100", 2, 10), ven(POST, "5", "100", 2, 10)),
    "pre venta 0": (ven(PRE, "10", "0", 0, 10), ven(POST, "5", "0", 0, 10)),
    "post todo 0": (ven(PRE, "10", "30", 1, 4), ven(POST, "0", "0", 0, 0)),
    "post gasto 0 venta igual": (ven(PRE, "10", "30", 1, 4), ven(POST, "0", "30", 1, 0)),
    "metrica None": (ven(PRE, "10", None, 1, 4), ven(POST, "5", "30", 1, 3)),
    "venta 1e-4": (ven(PRE, "0.0001", "0.0001", 1, 1), ven(POST, "0.0001", "0.0001", 1, 1)),
    "montos enormes": (
        ven(PRE, "9999999999.9999", "9999999999.9999", 10**9, 10**12),
        ven(POST, "1", "9999999999.9999", 10**9, 10**12),
    ),
}
for nombre, (pre, post) in casos.items():
    for factor in (D("-0.12"), D("0"), D("0.15")):
        for margen in (None, "8", "-5", "0"):
            try:
                r = juicio_repeticion(epi(pre, post, margen), target_acos_pct=D("25"), factor=factor)
                out = (r.veredicto, r.motivo)
            except Exception as exc:  # noqa: BLE001
                out = f"EXCEPCION {type(exc).__name__}: {exc}"
            if margen in (None, "8"):
                print(f"  {nombre:28s} factor={factor!s:6s} margen={margen!s:5s} -> {out}")
            elif isinstance(out, str):
                print(f"  {nombre:28s} factor={factor!s:6s} margen={margen!s:5s} -> {out}")


def vendedora():
    # 40 clics, 2 pedidos, venta 200, gasto 20: ACoS 10 % con target 25 -> R6 sube.
    return EvidenciaNivel(
        reciente=Tramo(clics=40, pedidos=2, venta=D("200"), gasto=D("20"), impresiones=1000),
        antiguo=Tramo(clics=0, pedidos=0, venta=D("0"), gasto=D("0"), impresiones=0),
    )


def caso(episodio, *, cambio, efecto, propia=None, bid="1.00"):
    propia = propia or vendedora()
    return CasoHoja(
        plataforma="amazon_us",
        hoja_id=1,
        ad_group_id=2,
        bid=BidVigente(valor=D(bid), moneda="USD", piso=D("0.20"), techo=D("3.00")),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda="USD",
                equilibrio_acos_pct=None,
                gasto_para_concluir=D("36"),
                confianza_recorte=D("0.80"),
                confianza_subida=D("0.70"),
            ),
            target_acos_pct=D("25"),
        ),
        propia=propia,
        pedidos_inmaduros=0,
        grupo=vendedora(),
        cuenta=vendedora(),
        precio=PrecioVentana(gasto=D("5"), clics=10),
        trayectoria=Trayectoria(cambios=(cambio,), efecto=efecto, episodio=episodio),
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=100,
            gasto_minimo=D("40"),
            expected_clicks=None,
            politica_economica=None,
        ),
        ventana_desde=dt.date(2026, 8, 1),
        ventana_hasta=dt.date(2026, 10, 30),
        observado_al=None,
    )


SUBIDA = CambioBid(fecha=X, bid_antes=D("0.87"), bid_despues=D("1.00"), origen="motor")
# Trafico 1.20 tras la subida, 40 clics al bid nuevo en [x+1, D-3]: R15 y R16 dejan pasar.
EFECTO_SUBIDA = EfectoCambio(
    dias_post=40,
    impresiones_pre7=700,
    clics_pre7=20,
    impresiones_post=840,
    dias_post_trafico=7,
    clics_post=40,
    gasto_post=D("20"),
    clics_pre=20,
    gasto_pre=D("17"),
    vendia=True,
)

print()
print("== 2. Hoja rentable (ACoS 10 % vs target 25), R6 pide +15 % ==")
print("  sin episodio (R14-R17 como antes):", decide(caso(None, cambio=SUBIDA, efecto=EFECTO_SUBIDA)))
# Semana posterior con 3 clics y 0 ventas; la anterior con 1 venta de 30.
ruido = epi(ven(PRE, "4", "30", 1, 4), ven(POST, "5", "0", 0, 3))
print("  episodio post 3 clics / 0 ventas:", decide(caso(ruido, cambio=SUBIDA, efecto=EFECTO_SUBIDA)))
# Misma venta (1 pedido de 30 en cada semana) y ACoS 16.7 % < target 25.
igual = epi(ven(PRE, "4", "30", 1, 4), ven(POST, "5", "30", 1, 5))
print("  episodio venta igual, ACoS post 16.7 %:", decide(caso(igual, cambio=SUBIDA, efecto=EFECTO_SUBIDA)))
print("  efecto.clics_post usado por R15 =", EFECTO_SUBIDA.clics_post,
      "| clics de la ventana post que juzga M6 =", ruido.post.clics)

print()
print("== 3. Recorte: M6 solo PERMITE, nunca sube ==")
RECORTE = CambioBid(fecha=X, bid_antes=D("1.00"), bid_despues=D("0.88"), origen="motor")
for f in (D("-0.25"), D("-0.12"), D("0.15"), D("0.20")):
    for pre, post in casos.values():
        r = juicio_repeticion(epi(pre, post, "8"), target_acos_pct=D("25"), factor=f)
        if f > 0 and r.veredicto == "permite":
            print("  PERMITE una subida: ", pre, post)
print("  ninguna combinacion devolvio 'permite' con factor > 0 (si no hay linea arriba)")
