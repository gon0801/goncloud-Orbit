from pathlib import Path
from test_architecture import PERMITIDOS_IMPORTAR_ADS_WRITE

for mig in ("0065_bids02_impulso.sql", "0066_bids02_retiro_anuncios.sql", "0069_bids02_retiro_intencion.sql"):
    lineas = [l.strip() for l in Path("migrations", mig).read_text().splitlines() if "app_decide" in l]
    print(mig, "->", lineas)
print("PERMITIDOS_IMPORTAR_ADS_WRITE ->", sorted(PERMITIDOS_IMPORTAR_ADS_WRITE))
