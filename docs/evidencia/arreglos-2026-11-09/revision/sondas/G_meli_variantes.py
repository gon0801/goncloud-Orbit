"""Sonda G-2: la escritura de MeLi desde `cambios.aplicar` nunca lleva variantes.

`cambios.aplicar` (y `_revertir_fila`) arman la unidad con
`_unidad_de_listing`, que pone `Miembro(product_id, sku, None)`: variante
siempre `None`. `MercadoMeli.escribir` compara las variaciones VIVAS del item
contra las variantes de la unidad: con un item con variaciones, todas salen
"huerfanas" y levanta ValueError sin PUT. Ademas se muestra el corte: cinco de
esas seguidas abren el cortacircuito de la corrida.

Sin red ni base: lector y escritor falsos.
"""

from decimal import Decimal as D

from app.meli.precio_mercado import MercadoMeli
from app.precio.cambios import _unidad_de_listing
from app.precio.compuerta import Cortacircuito
from app.precio.puerto import Acuse, Importe, Miembro, UnidadPrecio


class LectorFalso:
    def __init__(self, variaciones):
        self.variaciones = variaciones

    def get(self, ruta):
        return ({"id": "MLM123", "price": 500, "currency_id": "MXN",
                 "variations": self.variaciones}, None)


class EscritorFalso:
    puts = []

    def exigir_sello(self):
        return None

    def poner_precio(self, item, variantes, precio, moneda, sonda=False):
        EscritorFalso.puts.append((item, list(variantes), precio))
        return Acuse(aceptada=True, codigo=None, cuerpo={})


def mercado(variaciones):
    return MercadoMeli("meli", lector=LectorFalso(variaciones), escritor=EscritorFalso(),
                       fees=object())


# 1) Unidad tal como la arma cambios.aplicar (l.368): variante None.
unidad_aplicar = _unidad_de_listing(10, "meli", 55, "SKU-A", "MLM123")
print("unidad de aplicar:", unidad_aplicar.miembros)

# 1a) item SIN variaciones: el PUT sale (precio en la raiz)
EscritorFalso.puts.clear()
mercado([]).escribir(unidad_aplicar, Importe(D("550.00"), "MXN"))
print("sin variaciones -> PUTs:", EscritorFalso.puts)

# 1b) item CON 2 variaciones vivas, ambas mapeadas en listing_miembro
EscritorFalso.puts.clear()
try:
    mercado([{"id": 111}, {"id": 222}]).escribir(unidad_aplicar, Importe(D("550.00"), "MXN"))
except ValueError as exc:
    print("con variaciones (unidad de aplicar) -> ValueError:", exc)
print("PUTs:", EscritorFalso.puts)

# 2) La misma unidad con sus miembros reales (como la arma el catalogo/insumos)
EscritorFalso.puts.clear()
unidad_catalogo = UnidadPrecio(10, "meli", "MLM123", None, 55,
                               (Miembro(55, "SKU-A", "111"), Miembro(56, "SKU-B", "222")))
mercado([{"id": 111}, {"id": 222}]).escribir(unidad_catalogo, Importe(D("550.00"), "MXN"))
print("con variaciones (unidad con miembros) -> PUTs:", EscritorFalso.puts)

# 3) cortacircuito: cada ValueError cuenta como 'error' (corrida.py:1300)
corte = Cortacircuito(seguidos=0, limite=5)
for _ in range(5):
    corte = corte.tras("error")
print("tras 5 items con variaciones seguidos: corte.abierto =", corte.abierto)
