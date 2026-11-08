"""M3: con cero pedidos propios en la ventana madura, decide() jamas emite una subida."""
import itertools
from decimal import Decimal
from test_optimizer_caso import fabrica_caso, nivel, tramo, cambio, efecto, trayectoria, economia_mx
from app.optimizer.politica import decide, Mover, Regresar, Mantener, Pausar

veredictos = {}
subidas = []
grupos = [None,
          nivel(tramo(clics=300, pedidos=4, venta="2000", gasto="300")),   # cumple
          nivel(tramo(clics=300, pedidos=4, venta="2000", gasto="900")),   # sangra
          nivel(tramo(clics=300, pedidos=0, venta="0", gasto="900"))]      # sangra sin pedidos
cuentas = [None, nivel(tramo(clics=10000, pedidos=100, venta="100000", gasto="9000"))]
gastos = ["0", "50", "200", "349", "350", "700", "5000"]
clics = [0, 10, 60, 300]
trays = [trayectoria(),
         trayectoria([cambio(antes="8.00", despues="10.00")], efecto(dias_post=20, clics_post=40, post_impr=6000)),
         trayectoria([cambio(antes="12.00", despues="10.00")], efecto(dias_post=20, clics_post=40, post_impr=6000))]
inmaduros = [0, None]
n = 0
for g, c, ga, cl, tr, inm in itertools.product(grupos, cuentas, gastos, clics, trays, inmaduros):
    caso = fabrica_caso(
        propia=nivel(tramo(clics=cl, pedidos=0, venta="0", gasto=ga, impresiones=cl * 50)),
        pedidos_inmaduros=inm, grupo=g, cuenta=c, trayectoria=tr,
    )
    v = decide(caso)
    n += 1
    clave = type(v).__name__ + ":" + v.motivo
    veredictos[clave] = veredictos.get(clave, 0) + 1
    if isinstance(v, Mover) and v.factor > 0:
        subidas.append((ga, cl, inm, v))
print("casos evaluados:", n)
for k in sorted(veredictos):
    print(f"  {k}: {veredictos[k]}")
print("subidas (Mover con factor > 0) con pedidos propios = 0:", len(subidas))
