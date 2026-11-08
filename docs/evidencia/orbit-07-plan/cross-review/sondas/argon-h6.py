from pathlib import Path
from app import retiro_anuncios, apply

print("MotivoRetiro en retiro_anuncios.py:", retiro_anuncios.MotivoRetiro)
print("repone recrea ARCHIVED en retiro_anuncios.py:514-518:")
for l in Path("app/retiro_anuncios.py").read_text().splitlines()[513:519]:
    print(" ", l)
print("KINDS_QUOTA en apply.py:", apply.KINDS_QUOTA)
