"""B (E-S1): el codigo desplegado no negativiza ni cosecha un termino que es
keyword EXACT propia del mismo ad group. Funcion pura: sin base ni red.
Se corre con `bash docs/evidencia/arreglos-01/en_prod.sh .../B/prueba.py`.
"""

import datetime as dt
from decimal import Decimal

from app.optimizer import hygiene as h
from app.optimizer import windows as w

INICIO = dt.date(2026, 7, 14)
FIN = dt.date(2026, 8, 12)
OBS = dt.datetime(2026, 8, 18, 4, 0, tzinfo=dt.UTC)
CONFIG = h.ConfigHarvest(
    campaign_id="9002",
    ad_group_id="9102",
    default_bid=Decimal("0.75"),
    moneda="USD",
)


def _terminos(cost: str, ad_revenue: str, clicks: int, orders: int) -> w.TerminosCortes:
    termino = w.AgregadoTermino(
        ad_entity_id=1,
        search_term="Arras de Boda ",
        metric_currency="USD",
        cost=Decimal(cost),
        ad_revenue=Decimal(ad_revenue),
        clicks=clicks,
        orders=orders,
        fechas_distintas=3,
        is_asin_like=False,
        observed_at_max=OBS,
    )
    return w.TerminosCortes(
        ad_entity_id=1,
        window_start=INICIO,
        window_end=FIN,
        fechas_entidad=tuple(INICIO + dt.timedelta(days=i) for i in range(7)),
        terminos=(termino,),
    )


casos = (
    ("negative", _terminos("10", "0", 25, 0)),
    ("harvest", _terminos("10", "100", 5, 2)),
)
for etiqueta, terminos in casos:
    (resultado,) = h.decide_hygiene(
        platform="amazon_us",
        terminos=terminos,
        target_acos_pct=Decimal("25"),
        config_harvest=CONFIG,
        keywords_existentes=frozenset(),
        keywords_propias=frozenset({"arras de boda"}),
    )
    assert resultado.kind is None, (etiqueta, resultado)
    assert resultado.motivo == "termino_es_keyword_propia", (etiqueta, resultado)
    print(f"{etiqueta}: kind={resultado.kind} motivo={resultado.motivo}")
print("B OK")
