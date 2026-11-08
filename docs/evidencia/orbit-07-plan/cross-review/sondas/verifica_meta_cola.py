import datetime as dt
import inspect
from decimal import Decimal

from app import apply_cola
from app.optimizer import goals
from app.optimizer.bid import POLITICA_PAUSE_ECONOMICA, decide_pause
from app.optimizer.windows import AgregadoMetricas

meta_general, procedencia = goals.cascada_target_acos_con_procedencia(
    None, None, {}, None, "amazon_mx", Decimal("20")
)
meta_producto = min(Decimal("8") * Decimal("0.5"), goals.MARGEN_BANDA_MAX)
print(f"meta_general={meta_general} procedencia={procedencia}")
print(f"meta_producto={meta_producto}")
fuente = inspect.getsource(apply_cola._target_pause_vigente)
print("revalidacion_lee_snapshot_general=", 'notas["target"]' in fuente)
print("revalidacion_resuelve_cascada_general=", "g.cascada_target_acos_con_procedencia" in fuente)
cortes = AgregadoMetricas(
    window_start=dt.date(2026, 9, 1),
    window_end=dt.date(2026, 9, 10),
    fechas=tuple(dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(10)),
    metric_currency="MXN", cost=Decimal("5000"), ad_revenue=Decimal("10000"),
    revenue_same_sku=Decimal("10000"), impressions=1000, clicks=120, orders=1,
    observed_at_max=dt.datetime(2026, 9, 11, tzinfo=dt.UTC),
)
for nombre, meta in (("producto", meta_producto), ("general", meta_general)):
    resultado = decide_pause(
        platform="amazon_mx", bids=None, cortes=cortes,
        target_acos_pct=meta, umbral_pause=100,
        policy_version=POLITICA_PAUSE_ECONOMICA,
    )
    print(f"{nombre}: target={meta}% kind={resultado.kind} motivo={resultado.motivo}")
