"""Sonda pura W-N: aviso de presupuesto alto y campos de confirmacion.

Corre sin base ni red (`en_prod.sh` la ejecuta en el contenedor). Los casos
son los de la fila W-N.1 de `plans/arreglos-04.md`; los modelos deben traer
`acepto_presupuesto_alto`. Debe fallar contra 976df4dc (ahi no existen).
"""

from __future__ import annotations

from decimal import Decimal

from app.api_fabrica import CuerpoLanzarImpulso, SolicitudCrear
from app.api_write import CuerpoAplicaAjuste
from app.optimizer.goals import presupuesto_alto


def main() -> None:
    esta, tus = "esta_campana", "tus_campanas"

    aviso = presupuesto_alto(Decimal("125"), "USD", Decimal("26.22"), esta)
    assert "125.00 USD" in aviso.texto()
    assert "26.22 USD" in aviso.texto()
    assert "esta campaña tiene hoy" in aviso.texto()
    assert presupuesto_alto(Decimal("600"), "MXN", Decimal("300"), esta) is None
    assert presupuesto_alto(Decimal("600.01"), "MXN", Decimal("300"), esta) is not None
    aviso = presupuesto_alto(Decimal("25000"), "MXN", Decimal("5353.7800"), esta)
    assert "5,353.78 MXN" in aviso.texto()

    aviso = presupuesto_alto(Decimal("1500"), "MXN", Decimal("312.08"), tus)
    assert "9 de cada 10" in aviso.texto() and "312.08 MXN" in aviso.texto()
    assert presupuesto_alto(Decimal("624.16"), "MXN", Decimal("312.08"), tus) is None
    assert presupuesto_alto(Decimal("70"), "USD", Decimal("24.57"), tus) is not None
    assert presupuesto_alto(Decimal("50"), "USD", None, tus) is None
    assert (
        "no tienes campañas activas en USD"
        in presupuesto_alto(Decimal("60"), "USD", None, tus).texto()
    )
    assert (
        "no tiene un presupuesto anterior"
        in presupuesto_alto(Decimal("60"), "USD", None, esta).texto()
    )

    for modelo in (SolicitudCrear, CuerpoLanzarImpulso, CuerpoAplicaAjuste):
        assert "acepto_presupuesto_alto" in modelo.model_fields, modelo.__name__
    print("W-N OK")


if __name__ == "__main__":
    main()
