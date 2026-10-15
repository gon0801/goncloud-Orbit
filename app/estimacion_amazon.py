"""Cotizador del universo Amazon (REPRICING 02 F.3, seccion Q).

`CotizadorAmazon` es el `cotizador` de los tres universos Amazon del
registro: cotiza con el marketplace y fulfillment del universo de la oferta
y rechaza lo que el registro no trae sin tocar red. `partir_fees` parte el
arbol con el MISMO predicado que `derivar_ref_fijo`: el unico ReferralFee
con monto positivo es la parte variable y el resto es fijo. La decision de
F.0 (los dos universos FBM cotizan de verdad) vive aqui.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal

from app.estimacion_fees import (
    ProductFeesClient,
    ResultadoCotizacion,
    cotizar_a_precio,
)
from app.estimacion_insumos import OfertaResuelta
from app.estimacion_venta import DetalleFee

__all__ = ["CotizadorAmazon", "partir_fees"]


class CotizadorAmazon:
    """Fees reales de una oferta a un precio, con el cliente adentro.

    Lo usan la ingesta de estimacion (para armar el escenario) y el
    `Mercado.cotizar` del mismo universo (F.4).
    """

    def __init__(self, client: ProductFeesClient) -> None:
        self._client = client

    def cotizar(
        self,
        oferta: OfertaResuelta,
        precio: Decimal,
        *,
        ahora: Callable[[], datetime],
    ) -> ResultadoCotizacion:
        # Local: universo -> amazon -> fees -> insumos; arriba seria ciclo.
        from app.estimacion_universo import universo_de

        universo = universo_de(oferta.platform, oferta.canal)
        if universo is None or universo.cotizador is None:
            return ResultadoCotizacion(
                estado="error",
                total_fees=None,
                fee_details=[],
                fees_estimated_at=None,
                error_code="fee_universo_no_soportado",
                fetched_at=ahora(),
            )
        return cotizar_a_precio(self._client, oferta, precio, now_utc=ahora)


def partir_fees(
    detalles: Sequence[DetalleFee], fee_total: Decimal
) -> tuple[Decimal, Decimal] | str:
    """`(variable, fijo)` desde el arbol; sin referral unico, `fee_incompatible`.

    Variable es el unico `ReferralFee` con monto positivo (top-level, igual
    que `derivar_ref_fijo`); fijo es `total - variable`. Un candado fija que
    los dos predicados coinciden.
    """
    referrals = [det for det in detalles if det.fee_type == "ReferralFee" and det.final_fee > 0]
    if len(referrals) != 1:
        return "fee_incompatible"
    return (referrals[0].final_fee, fee_total - referrals[0].final_fee)
