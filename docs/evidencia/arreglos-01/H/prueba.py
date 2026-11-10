"""H (E-H2): el codigo desplegado no deja un piso aprendido al revertir una
subida de bid. Funcion pura: sin base ni red.
Se corre con `bash docs/evidencia/arreglos-01/en_prod.sh .../H/prueba.py`.
"""

import datetime as dt
from decimal import Decimal

from app.optimizer.caso import CambioBid, EfectoCambio, Trayectoria


def _cambio(fecha, antes, despues, origen):
    return CambioBid(
        fecha=fecha,
        bid_antes=Decimal(antes),
        bid_despues=Decimal(despues),
        origen=origen,
    )


def _efecto():
    return EfectoCambio(
        dias_post=10,
        impresiones_pre7=5000,
        clics_pre7=50,
        impresiones_post=100,
        dias_post_trafico=7,
        clics_post=25,
        gasto_post=Decimal("100"),
        clics_pre=40,
        gasto_pre=Decimal("120"),
        vendia=True,
    )


subida_revertida = Trayectoria(
    cambios=(
        _cambio(dt.date(2026, 9, 1), "1.00", "1.15", "motor"),
        _cambio(dt.date(2026, 9, 5), "1.15", "1.00", "regreso_del_dueno"),
    ),
    efecto=_efecto(),
)
piso = subida_revertida.piso_aprendido
assert piso is None, f"revertir una subida no ensena piso, dio {piso}"
print(f"subida revertida: piso_aprendido={piso}")

regreso_de_recorte = Trayectoria(
    cambios=(
        _cambio(dt.date(2026, 9, 1), "9.74", "3.73", "motor"),
        _cambio(dt.date(2026, 9, 5), "3.73", "5.00", "regreso_del_dueno"),
    ),
    efecto=_efecto(),
)
piso = regreso_de_recorte.piso_aprendido
assert piso == Decimal("3.73"), f"un regreso de recorte si ensena piso, dio {piso}"
print(f"regreso de recorte: piso_aprendido={piso}")
print("H OK")
