"""Revision termonuclear de la politica de pujas (solo lectura).

Cada prueba FALLA por la asercion que describe la falla. Los casos reales
salen de decision.inputs de produccion (tests/_revision_politica/datos/,
congelados con la sonda de solo lectura el 2026-11-12).
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "tests"))

from test_optimizer_caso import (  # noqa: E402
    cambio,
    economia_mx,
    economia_us,
    efecto,
    fabrica_caso,
    nivel,
    tramo,
    trayectoria,
)

from app.ads.write import _bid_payload  # noqa: E402
from app.optimizer.caso import BidVigente, CasoHoja, PrecioVentana  # noqa: E402
from app.optimizer.politica import Mantener, Mover, decide  # noqa: E402
from app.optimizer.replay import reproduce  # noqa: E402

DATOS = Path(__file__).resolve().parent / "datos"


def _real(decision_id: int) -> tuple[dict, CasoHoja]:
    inputs = json.loads((DATOS / f"decision_{decision_id}.json").read_text())
    return inputs, CasoHoja.desde_json(inputs["caso"])


def _lo_que_recibe_amazon(bid: Decimal) -> Decimal:
    """El apply manda el bid con la MISMA funcion que usa app/apply.py."""
    return Decimal(_bid_payload(bid))


# ---------------------------------------------------------------------------
# F1: el piso aprendido se cruza por el redondeo a centavos del apply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision_id", [2865, 2866, 2904])
def test_f1_piso_aprendido_se_pisa_al_redondear_caso_real(decision_id):
    """Hojas 4934, 5336 y 4924: el motor recorta a 0.2904 / 0.4224 / 0.2625,
    apenas encima del piso 0.29 / 0.42 / 0.26 donde la hoja ya perdio su
    trafico; Amazon recibe el bid redondeado y queda EXACTAMENTE en el piso."""
    inputs, caso = _real(decision_id)
    kind, nuevo, _ = reproduce(inputs)
    piso = caso.trayectoria.piso_aprendido
    assert kind == "bid" and nuevo < caso.bid.valor
    assert _lo_que_recibe_amazon(nuevo) > piso, (
        f"decision {decision_id}: bid enviado {_lo_que_recibe_amazon(nuevo)} "
        f"cae en el piso aprendido {piso}"
    )


def test_f1_piso_aprendido_sintetico():
    """Recorte por gasto sin venta tras un regreso por desplome de 0.29 a 0.33."""
    caso = fabrica_caso(
        plataforma="amazon_us",
        bid=BidVigente(Decimal("0.33"), "USD", Decimal("0.10"), Decimal("2.50")),
        economia=economia_us(),
        propia=nivel(tramo(clics=60, pedidos=0, venta="0", gasto="40", impresiones=3000)),
        trayectoria=trayectoria(
            [
                cambio(dt.date(2026, 10, 25), "0.33", "0.2904", "motor"),
                cambio(dt.date(2026, 10, 30), "0.29", "0.33", "regreso_por_desplome"),
            ],
            efecto(dias_post=8, clics_post=25, gasto_post="9", vendia=False),
        ),
    )
    veredicto = decide(caso)
    assert isinstance(veredicto, Mover)
    assert _lo_que_recibe_amazon(veredicto.bid_nuevo) > caso.trayectoria.piso_aprendido


# ---------------------------------------------------------------------------
# F2: la anti-inversion de 10 dias (D.2) que CONTEXTO declara ya no existe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision_id", [2865, 2904])
def test_f2_inversion_antes_de_10_dias_caso_real(decision_id):
    """CONTEXTO.md (ADS PROTECCION 01, D.2): tras un BID aplicado, la direccion
    contraria exige >= 10 dias de evidencia posterior. Estas hojas invirtieron
    a los 7 dias del regreso."""
    inputs, caso = _real(decision_id)
    ultimo = caso.trayectoria.ultimo
    veredicto = decide(caso)
    assert ultimo.direccion == 1 and isinstance(veredicto, Mover) and veredicto.factor < 0
    assert caso.trayectoria.efecto.dias_post >= 10, (
        f"decision {decision_id}: invierte con {caso.trayectoria.efecto.dias_post} dias"
    )


# ---------------------------------------------------------------------------
# F3: "heredar el veredicto del ad group" cuando el grupo es la misma hoja
# ---------------------------------------------------------------------------


def test_f3_grupo_de_una_hoja_recorta_por_acos_crudo():
    """La posterior dice que la hoja NO pierde dinero con certeza (R3 no
    dispara, R5 tampoco); el 'grupo' es la propia hoja y su ACoS crudo (30 %)
    la recorta -12 % con nivel ad_group. Caso real 4934: 98 % del gasto del
    grupo es de la hoja (337.43 de 344.19, mismos 13 pedidos)."""
    hoja = nivel(tramo(clics=300, pedidos=3, venta="1500", gasto="450", impresiones=9000))
    caso = fabrica_caso(
        economia=economia_mx(),
        propia=hoja,
        grupo=hoja,
        cuenta=nivel(tramo(clics=10000, pedidos=100, venta="100000", gasto="9000")),
        precio=PrecioVentana(Decimal("450"), 300),
    )
    con_grupo = decide(caso)
    assert not isinstance(con_grupo, Mover), f"el grupo-hoja recorta: {con_grupo}"


def test_f3_caso_real_4934_grupo_es_la_hoja():
    _, caso = _real(2865)
    gasto_hoja = caso.propia.gasto_pesado()
    gasto_grupo = caso.grupo.gasto_pesado()
    veredicto = decide(caso)
    assert veredicto.nivel == "ad_group"
    assert gasto_hoja / gasto_grupo < Decimal("0.5"), (
        f"el 'veredicto del grupo' es la propia hoja: {gasto_hoja}/{gasto_grupo}"
    )


# ---------------------------------------------------------------------------
# F4: un ajuste de campana (el bid NO cambio) cuenta como subida previa
# ---------------------------------------------------------------------------


def test_f4_ajuste_de_campana_se_juzga_como_subida_repetida():
    """CambioBid.direccion da +1 con bid_antes == bid_despues. Tras un ajuste
    de ubicacion, una subida se trata como 'repetir subida' y se frena por
    'subida_sin_trafico' aunque el bid nunca subio."""
    vendedora_barata = nivel(
        tramo(clics=200, pedidos=8, venta="4000", gasto="300", impresiones=9000)
    )
    caso = fabrica_caso(
        economia=economia_mx(),
        propia=vendedora_barata,
        grupo=vendedora_barata,
        cuenta=nivel(tramo(clics=10000, pedidos=100, venta="100000", gasto="9000")),
        trayectoria=trayectoria(
            [cambio(dt.date(2026, 9, 15), "10.00", "10.00", "ajuste_de_campana")],
            efecto(
                dias_post=10,
                pre_impr=7000,
                post_impr=6300,
                clics_post=30,
                gasto_post="45",
                clics_pre=60,
                gasto_pre="90",
            ),
        ),
    )
    veredicto = decide(caso)
    assert veredicto != Mantener("subida_sin_trafico"), "un ajuste de campana no es una subida"
