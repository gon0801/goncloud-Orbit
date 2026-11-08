import datetime as dt
from decimal import Decimal
from app.optimizer import politica, caso, goals
from test_optimizer_caso import fabrica_caso, nivel, tramo

print("1) Convergencia desde ultimo=4.0:",
      goals._converge_al_destino(ultimo=Decimal("4.0"), destino=Decimal("20.0"), motivo="sin_margen"))
med_6 = goals.MedicionMargen(Decimal("6.0"), Decimal("0.98"), 45, Decimal("10000"),
                             dt.datetime(2026, 11, 5, tzinfo=dt.UTC), "MXN",
                             dt.date(2026, 2, 20), dt.date(2026, 10, 22))
print("2) Paso cuando margen baja de 8% a 6% (ultimo=4.0):",
      goals.resuelve_target_margen(med_6, Decimal("0.5"), dt.date(2026, 11, 6), ultimo=Decimal("4.0"), setting=Decimal("20.0"), dias_min=30))

propia = nivel(tramo(clics=200, pedidos=10, venta="10000", gasto="2500", impresiones=9000),
               tramo(clics=100, pedidos=5, venta="5000", gasto="1250", impresiones=4500))
precio = caso.PrecioVentana(Decimal("1500"), 120) # ACoS = 25%
for etiqueta, eq in (("eq_plataforma=41.43", Decimal("41.43")), ("eq_none=None", None), ("eq_producto=8.00", Decimal("8.00"))):
    c = fabrica_caso(propia=propia, precio=precio, economia=caso.Economia(
        plataforma=caso.EconomiaPlataforma("MXN", eq, Decimal("350"), Decimal("0.80"), Decimal("0.70")),
        target_acos_pct=Decimal("4.00"),
    ))
    print(f"3) R3 con target=4.00 y {etiqueta}:", politica.decide(c))
