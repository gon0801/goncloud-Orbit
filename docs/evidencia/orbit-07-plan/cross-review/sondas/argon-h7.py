from pathlib import Path
from app import lecturas_caso

print("Rama 3 de v_cambio_bid en 0067:")
for l in Path("migrations/0067_bids02_campana_ajuste.sql").read_text().splitlines()[90:101]:
    print(" ", l)
print("Columnas leidas hoy en _SQL_EFECTO:")
for l in lecturas_caso._SQL_EFECTO.splitlines():
    if "sum(" in l:
        print(" ", l.strip())
