from app.ads import reports
from decimal import Decimal
from collections import Counter
import datetime as dt

filas = [
    {"date": "2026-09-03", "advertisedAsin": "B0AAAAAAA1", "advertisedSku": "SKU-A",
     "campaignId": 100, "adGroupId": 1001, "adId": 11, "impressions": 50, "clicks": 5,
     "cost": 10.0, "purchases30d": 0, "sales30d": 0.0, "purchasesSameSku30d": 0, "attributedSalesSameSku30d": 0.0},
    {"date": "2026-09-03", "advertisedAsin": "B0AAAAAAA1", "advertisedSku": "SKU-A",
     "campaignId": 100, "adGroupId": 1002, "adId": 12, "impressions": 50, "clicks": 5,
     "cost": 10.0, "purchases30d": 1, "sales30d": 200.0, "purchasesSameSku30d": 1, "attributedSalesSameSku30d": 200.0},
]
plan, skips = reports._planea_filas_productos(
    reports.PRODUCTOS_CFG, filas, hoy=dt.date(2026, 9, 4),
    fecha_ini=dt.date(2026, 9, 1), fecha_fin=dt.date(2026, 9, 3)
)
print("Fila fusionada en ads_product_metric_observation:", plan[0])
