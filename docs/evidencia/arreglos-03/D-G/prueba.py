import datetime as dt

from app.ads.reports import SEARCH_TERMS_CFG, _planea_filas_terminos

DIA = dt.date(2026, 11, 1)


def fila(**m):
    return {"date": DIA.isoformat(), "adGroupId": 111, "searchTerm": "arras boda", **m}


def plan(filas):
    p, _ = _planea_filas_terminos(SEARCH_TERMS_CFG, filas, ultimo_cerrado=DIA, fecha_ini=DIA, fecha_fin=DIA)
    return [(x.search_term, x.clicks, x.orders) for x in p]


assert plan([fila(clicks=12, cost=9.5, purchases7d="2?", sales7d=40.0),
             fila(clicks=30, cost=21.0, purchases7d=0, sales7d=0.0)]) == [], "fila ilegible no invalida la clave"
assert plan([fila(clicks=5, cost=1.0, purchases7d=None, sales7d=None),
             fila(clicks=None, cost=None, purchases7d=2, sales7d=40.0),
             fila(clicks=30, cost=20.0, purchases7d=0, sales7d=0.0)]) == [], "tercera fila recrea la clave"
assert plan([fila(clicks=12, cost=9.5, purchases7d=None, sales7d=40.0),
             fila(clicks=30, cost=21.0, purchases7d=0, sales7d=0.0)]) == [], "clics sin pedidos no invalida la clave"
print("D-G desplegado")
