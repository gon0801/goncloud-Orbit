from decimal import Decimal
from test_optimizer_caso import fabrica_caso, nivel, tramo
from test_optimizer_politica import _cuenta_llena
from app.optimizer.caso import PrecioVentana
from app.optimizer.politica import decide

m3 = open('plans/orbit-07-motor-decisiones.md', encoding='utf-8').read().split(
    '## Compartir el aprendizaje'
)[1].split('## Retirar anuncios')[0]
print('M3 asigna app/optimizer/politica.py:', 'app/optimizer/politica.py' in m3)
propia = nivel(tramo(
    clics=50, pedidos=5, venta='5000', gasto='200', impresiones=2000
))
caso = fabrica_caso(
    propia=propia, grupo=propia, cuenta=_cuenta_llena(),
    precio=PrecioVentana(Decimal('100'), 50),
)
print('veredicto actual sin restriccion de aprendizaje:', decide(caso))
print('CasoHoja tiene campo de aprendizaje:',
      any('aprend' in campo for campo in caso.__dataclass_fields__))
