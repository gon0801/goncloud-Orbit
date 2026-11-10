"""Sonda A (solo lectura, sin base): proteccion por venta reciente (M1) y
espera por venta reciente del producto (M4) en ORBIT 07.

Corre el codigo REAL de la copia de trabajo. La unica pieza simulada es la
conexion de `proteccion_venta.lee_cache`: un objeto que devuelve las filas que
`_SQL_RECIENTES` y `_SQL_EPISODIOS` producirian para los datos de cada caso
(mismo agregado: bool_and(orders no nulo y >= 0) -> sum, si no NULL;
min(fecha) con orders > 0; max(observed_at); array_agg de fechas).

Uso:
  PYTHONPATH=<orbit07-wt> /Users/dn/dev/goncloud-Orbit/.venv/bin/python -I \
      sondas/A_venta_reciente.py
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import proteccion_venta as pv
from app import productos_io
from app.evaluacion_catalogo import ObservacionAds
from app.optimizer import bid, productos
from app.optimizer import proteccion as sp
from app.optimizer.windows import AgregadoMetricas, fin_ventana_cortes, inicio_ventana

D = dt.date(2026, 11, 9)
DECIDIDO = dt.datetime(2026, 11, 9, 8, 40, tzinfo=dt.UTC)
PLAT = "amazon_mx"


class _Res:
    def __init__(self, filas):
        self._f = filas

    def fetchall(self):
        return self._f


class ConnFalsa:
    """Devuelve lo que darian las dos consultas de lee_cache.

    filas_hoja: {hoja: [(fecha, orders|None, observed_at)]}
    episodios: [(hoja, ancla, vence_el)]
    dias_plataforma: fechas con ingesta de cualquier hoja (cobertura)."""

    def __init__(self, filas_hoja, episodios=(), dias_plataforma=None):
        self.filas_hoja = filas_hoja
        self.episodios = list(episodios)
        self.dias_plataforma = dias_plataforma

    def execute(self, sql, params):
        if "FROM proteccion_venta_reciente" in sql:
            return _Res(sorted(self.episodios, key=lambda e: (e[0], -e[1].toordinal())))
        desde, hasta = params[0], params[1]
        salida = []
        for hoja, filas in self.filas_hoja.items():
            en = [f for f in filas if desde <= f[0] <= hasta]
            if not en:
                continue
            ok = all(o is not None and o >= 0 for _, o, _ in en)
            pedidos = sum(o for _, o, _ in en) if ok else None
            con = [d for d, o, _ in en if o is not None and o > 0]
            ancla = min(con) if con else None
            obs = max(t for _, _, t in en)
            salida.append((hoja, pedidos, ancla, obs, sorted({d for d, _, _ in en})))
        # dias de otras hojas (cobertura de plataforma): hoja ficticia 0 con ceros
        if self.dias_plataforma:
            relleno = [d for d in self.dias_plataforma if desde <= d <= hasta]
            if relleno:
                salida.append((0, 0, None, DECIDIDO - dt.timedelta(hours=1), relleno))
        return _Res(salida)


def todos_los_dias(desde, hasta):
    return [desde + dt.timedelta(days=i) for i in range((hasta - desde).days + 1)]


def cortes(window_end, orders, clicks=120, cost="500"):
    ini = inicio_ventana(window_end)
    return AgregadoMetricas(
        window_start=ini,
        window_end=window_end,
        fechas=tuple(todos_los_dias(ini, window_end)),
        metric_currency="MXN",
        cost=Decimal(cost),
        ad_revenue=Decimal(0),
        revenue_same_sku=Decimal(0),
        impressions=5000,
        clicks=clicks,
        orders=orders,
        observed_at_max=DECIDIDO - dt.timedelta(hours=2),
    )


def decide(cortes_hoja, insumos):
    return bid.decide_pause(
        platform=PLAT,
        bids=None,
        cortes=cortes_hoja,
        target_acos_pct=Decimal("20"),
        umbral_pause=60,
        cost_min=None,
        policy_version=None,  # produccion: ads_pause_economica ausente
        proteccion=insumos,
    )


OBS = DECIDIDO - dt.timedelta(hours=1)
plat_dias = todos_los_dias(D - dt.timedelta(days=9), D - dt.timedelta(days=1))

print("=== P1. M1 metrica venenosa en D-9..D-1 con un pedido CONOCIDO ===")
filas = {
    7: [
        (D - dt.timedelta(days=7), None, OBS),  # orders NULL ese dia
        (D - dt.timedelta(days=3), 1, OBS),  # pedido conocido
    ]
}
cache = pv.lee_cache(ConnFalsa(filas, dias_plataforma=plat_dias), PLAT, DECIDIDO)
c = cortes(D - dt.timedelta(days=10), orders=0)
ins = cache.insumos(7, ventana_desde=c.window_start, ventana_hasta=c.window_end)
print("lectura:", ins.reciente)
print("hay_venta:", cache.hay_venta(7), "| tiene_proteccion:", cache.tiene_proteccion(7))
r = sp.evalua(ins)
print("evalua ->", r.accion, r.motivo)
res = decide(c, ins)
print("decide_pause ->", res.kind, res.motivo)
print("misma hoja SIN el NULL (control):")
filas_ok = {7: [(D - dt.timedelta(days=7), 0, OBS), (D - dt.timedelta(days=3), 1, OBS)]}
cache_ok = pv.lee_cache(ConnFalsa(filas_ok, dias_plataforma=plat_dias), PLAT, DECIDIDO)
ins_ok = cache_ok.insumos(7, ventana_desde=c.window_start, ventana_hasta=c.window_end)
res_ok = decide(c, ins_ok)
print("decide_pause ->", res_ok.kind, res_ok.motivo)
print("comparacion: el mismo NULL en la ventana MADURA:")
c_null = cortes(D - dt.timedelta(days=10), orders=None)
print("decide_pause ->", decide(c_null, ins_ok).kind, decide(c_null, ins_ok).motivo)

print()
print("=== P2. M1 fuente incompleta (falta D-1 en la plataforma) con pedido conocido ===")
filas = {7: [(D - dt.timedelta(days=3), 1, OBS)]}
cache = pv.lee_cache(
    ConnFalsa(filas, dias_plataforma=plat_dias[:-1]), PLAT, DECIDIDO
)  # sin D-1
ins = cache.insumos(7, ventana_desde=c.window_start, ventana_hasta=c.window_end)
print("cubierta:", cache.cubierta, "| lectura:", ins.reciente)
res = decide(c, ins)
print("decide_pause ->", res.kind, res.motivo)

print()
print("=== P3. Hueco entre ventanas cuando la hoja deja de tener filas (agotado) ===")
for ultima in (D - dt.timedelta(days=10), D - dt.timedelta(days=8)):
    venta = ultima if ultima == D - dt.timedelta(days=10) else D - dt.timedelta(days=10)
    fin = fin_ventana_cortes(ultima, DECIDIDO)
    print(
        f"ultima fila {ultima} venta {venta}: ventana madura {inicio_ventana(fin)}..{fin},"
        f" reciente {D - dt.timedelta(days=9)}..{D - dt.timedelta(days=1)}"
    )
    print("  venta dentro de la madura:", inicio_ventana(fin) <= venta <= fin)
    filas_h = {9: [(d, 0, OBS) for d in todos_los_dias(venta + dt.timedelta(days=1), ultima)]}
    cache = pv.lee_cache(ConnFalsa(filas_h, dias_plataforma=plat_dias), PLAT, DECIDIDO)
    c3 = cortes(fin, orders=0)
    ins = cache.insumos(9, ventana_desde=c3.window_start, ventana_hasta=c3.window_end)
    res = decide(c3, ins)
    print("  sin episodio previo -> decide_pause:", res.kind, res.motivo)
    con_ep = pv.lee_cache(
        ConnFalsa(filas_h, episodios=[(9, venta, venta + dt.timedelta(days=10))],
                  dias_plataforma=plat_dias),
        PLAT,
        DECIDIDO,
    )
    ins_ep = con_ep.insumos(9, ventana_desde=c3.window_start, ventana_hasta=c3.window_end)
    res_ep = decide(c3, ins_ep)
    print("  con episodio abierto antes -> decide_pause:", res_ep.kind, res_ep.motivo)

print()
print("=== P4. M1 limites de fecha del episodio (ancla + 10, ventana D-10) ===")
ancla = dt.date(2026, 10, 30)
for hoy in (ancla + dt.timedelta(days=9), ancla + dt.timedelta(days=10)):
    dec = dt.datetime.combine(hoy, dt.time(8, 40), tzinfo=dt.UTC)
    fin = fin_ventana_cortes(hoy - dt.timedelta(days=1), dec)
    ins = pv.CacheProteccion(
        plataforma=PLAT,
        evaluado_el=hoy,
        cubierta=True,
        _recientes={},
        _episodios={1: pv.EpisodioProteccion(ancla, ancla + dt.timedelta(days=10))},
    ).insumos(1, ventana_desde=inicio_ventana(fin), ventana_hasta=fin)
    r = sp.evalua(ins)
    print(f"hoy {hoy} ventana_hasta {fin}: {r.accion} {r.motivo}")

print()
print("=== P5. M4: dia de venta reciente con una metrica ausente ===")
moneda = "MXN"
fecha_venta = D - dt.timedelta(days=5)
por_fecha = {
    fecha_venta: [
        ObservacionAds(
            metric_date=fecha_venta,
            observed_at=OBS,
            clicks=4,
            cost=Decimal("12"),
            purchases30d=Decimal(1),
            sales30d=Decimal("399"),
            attributed_sales_same_sku30d=None,  # ausente
        )
    ]
}
ventana_rec = productos.ventana_venta_reciente(DECIDIDO)
dias_rec, faltantes = productos_io._dias_halo(ventana_rec, por_fecha, moneda)
print("dias_recientes:", dias_rec, "| faltantes (descartado en productos_io.py:310):", faltantes)
vp = productos.ventana_pausa(DECIDIDO)
entrada = productos.EntradaProducto(
    producto=productos.Producto(PLAT, 1),
    decidido_el=DECIDIDO,
    margen=productos.SinDato(("sin_margen",)),
    fraccion=productos.SinDato(("sin_fraccion",)),
    ventana_halo=productos.Ventana(D - dt.timedelta(days=90), D - dt.timedelta(days=3)),
    dias_halo=(),
    actividad_pausa=productos.AdsCompletos(
        ventana=vp,
        gasto=productos.Dinero(Decimal("800"), moneda),
        clics=200,
        compras_total=Decimal(0),
        ventas_total=productos.Dinero(Decimal(0), moneda),
        ventas_mismo_sku=productos.Dinero(Decimal(0), moneda),
        referencias_por_fecha=(),
    ),
    anuncios=(),
    grupos_aptos_para_meta=(),
    impedimentos=(),
    limites_pausa=productos.LimitesPausa(60, productos.Dinero(Decimal("200"), moneda)),
    ventana_venta_reciente=ventana_rec,
    dias_recientes=dias_rec,
)
print("venta_reciente_producto:", productos.venta_reciente_producto(entrada))
print("motivos_economicos_pausa (vacio = la pausa procede):",
      productos.motivos_economicos_pausa(entrada))
print("cumple_limites_pausa:", productos.cumple_limites_pausa(entrada))
