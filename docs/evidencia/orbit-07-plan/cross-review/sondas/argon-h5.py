from app import apply
from pathlib import Path

print("Motivos permitidos en decision_sin_aplicar:", apply.MOTIVOS_SIN_APLICAR)
lineas = Path("app/cycle.py").read_text().splitlines()[1673:1683]
print("Comportamiento de cycle.py ante fila en apply_queue (pending_veto o released):")
for l in lineas:
    print(" ", l)
