"""Sonda D (motor): validadores de settings ORBIT 07, reversa de M1 sobre filas
en espera, puerta de pujas con episodio viejo y escritor de config.

Pura: sin base de datos. Correr con
  PYTHONPATH=<orbit07-wt> /Users/dn/dev/goncloud-Orbit/.venv/bin/python -I <este archivo>
(-I ignora PYTHONPATH, asi que se agrega a sys.path a mano desde argv[1]).
"""

import datetime as dt
import sys
from decimal import Decimal

sys.path.insert(0, sys.argv[1])

from app import config_write  # noqa: E402
from app.optimizer import bid  # noqa: E402
from app.optimizer import goals as g  # noqa: E402
from app.optimizer import productos  # noqa: E402
from app.optimizer import proteccion as subp  # noqa: E402
from app.optimizer.caso import (  # noqa: E402
    VERSION_PROTECCION_VENTA,
    EpisodioProteccion,
    InsumosProteccion,
    LecturaReciente,
)
from app.optimizer.windows import AgregadoMetricas  # noqa: E402
from app.proteccion_venta import CacheProteccion  # noqa: E402


def prueba(nombre, fn):
    try:
        print(f"{nombre}: OK -> {fn()!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"{nombre}: {type(exc).__name__}: {exc}")


print("== 1. validadores de settings (TX2 los llama sin try)")
for valor in (None, "off", "", True, "proteccion_venta_v1"):
    prueba(f"activa_desde_settings({valor!r})", lambda v=valor: subp.activa_desde_settings({subp.CLAVE_ACTIVA: v}))
for valor in (None, "off", "auto03", "producto_directo_v1"):
    prueba(
        f"economia_desde_settings({valor!r})",
        lambda v=valor: productos.economia_desde_settings({"ads_producto_economia_amazon_us": v}, "amazon_us"),
    )
for valor in (None, "off", "economia_cambio_v1"):
    prueba(
        f"economia_cambio_desde_settings({valor!r})",
        lambda v=valor: g.economia_cambio_desde_settings({"ads_bid_economia_amazon_mx": v}, "amazon_mx"),
    )

print("\n== 2. misma hoja, misma ventana madura: M1 encendida vs apagada (reversa)")
hoy = dt.date(2026, 11, 9)
cortes = AgregadoMetricas(
    window_start=hoy - dt.timedelta(days=40),
    window_end=hoy - dt.timedelta(days=10),
    fechas=tuple(hoy - dt.timedelta(days=d) for d in range(10, 41)),
    metric_currency="USD",
    cost=Decimal("55"),
    ad_revenue=Decimal("0"),
    revenue_same_sku=Decimal("0"),
    impressions=4000,
    clicks=120,
    orders=0,
    observed_at_max=dt.datetime(2026, 11, 8, tzinfo=dt.UTC),
)
venta_reciente = LecturaReciente(
    pedidos=2, ancla=hoy - dt.timedelta(days=4), cubierta=True, observado_al=None
)
episodio = EpisodioProteccion(ancla=hoy - dt.timedelta(days=4), vence_el=hoy + dt.timedelta(days=6))
insumos = InsumosProteccion(
    version=VERSION_PROTECCION_VENTA,
    evaluado_el=hoy,
    reciente=venta_reciente,
    ventana_desde=cortes.window_start,
    ventana_hasta=cortes.window_end,
    episodio=episodio,
)
kw = dict(
    platform="amazon_us",
    bids=None,
    cortes=cortes,
    target_acos_pct=Decimal("30"),
    umbral_pause=100,
    cost_min=Decimal("40"),
    policy_version=None,
)
con_m1 = bid.decide_pause(**kw, proteccion=insumos)
sin_m1 = bid.decide_pause(**kw, proteccion=None)
print(f"M1 encendida (cola con proteccion_activa=True): kind={con_m1.kind} motivo={con_m1.motivo}")
print(f"M1 apagada / codigo anterior (proteccion=None):   kind={sin_m1.kind} motivo={sin_m1.motivo}")
print(f"orders en la ventana fresca de la revalidacion = {cortes.orders} -> no aplica vendio_en_ventana")

print("\n== 3. puerta de pujas del ciclo: episodio viejo y vencido, sin venta reciente")
cache = CacheProteccion(
    plataforma="amazon_us",
    evaluado_el=hoy,
    cubierta=True,
    _recientes={},
    _episodios={7: EpisodioProteccion(ancla=dt.date(2026, 11, 8) - dt.timedelta(days=200),
                                      vence_el=dt.date(2026, 11, 8) - dt.timedelta(days=190))},
)
print(f"hay_venta(7)={cache.hay_venta(7)} tiene_proteccion(7)={cache.tiene_proteccion(7)}")

print("\n== 4. escritor de config: off quita la clave, el resto se conserva")
actual = {
    "ads_optimizer_mode": "live",
    "ads_producto_meta_amazon_us": "live",
    "ads_producto_pausa_amazon_us": "shadow",
    "ads_producto_pausa_amazon_mx": "shadow",
    "ads_proteccion_venta": "proteccion_venta_v1",
    "ads_producto_economia_amazon_us": "producto_directo_v1",
    "ads_bid_economia_amazon_us": "economia_cambio_v1",
    "ads_bid_economia_amazon_mx": "economia_cambio_v1",
}
prueba(
    "proxima_config(--proteccion off --bid-economia off)",
    lambda: config_write.proxima_config(
        actual, "amazon_us", proteccion_venta="off", economia_cambio="off"
    ),
)
prueba(
    "proxima_config(edicion de pantalla: caps)",
    lambda: sorted(
        k
        for k in config_write.proxima_config(actual, "amazon_us", caps={"bid": 50})[0]
        if "proteccion" in k or "economia" in k
    ),
)
prueba(
    "proxima_config sobre config con 'off' literal (otra pantalla guardando)",
    lambda: config_write.proxima_config(
        {**actual, "ads_proteccion_venta": "off"}, "amazon_mx", caps={"bid": 50}
    ),
)
