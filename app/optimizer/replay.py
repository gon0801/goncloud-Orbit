"""Replay puro de decisiones desde inputs congelados (corazon de la
auditabilidad; spot-check humano de ORBIT 03 4.4).

Despacho por era (BIDS 02 M.3): motor bid con inputs.politica ==
"niveles_v3" se re-decide con decide(caso); motor bid sin esa clave es la
era bandas (decide_bid_era_bandas); hygiene como siempre. Sin marcador de
era en el esquema: el vocabulario de inputs decide (bosquejo replay.py).

Vivio en app/cycle.py hasta ORBIT 05 2.1a: se movio aqui (motor puro, sin
psycopg ni app.ads) para que tools/dossier_adversarial.py pueda replayear sin
cargar app.apply/app.ads. app.cycle lo reexporta (API publica sellada). El
FREEZE (serializacion congelada de inputs) sigue en app/cycle.py: el par
freeze<->replay queda partido a proposito y DECLARADO (pinneado con asserts
por clave + golden en tests/test_cycle.py; no existe allowlist en
tests/test_architecture.py); cualquier clave nueva en inputs se agrega en
los dos.

REPLAY FIEL POR CONSTRUCCION (decision del lead 2026-08-28, cierre CORTES
03): el replay LEE lo congelado, JAMAS recalcula evidencia (el snapshot de la
decision ya no existe) y JAMAS usa un valor vigente. Fila historica sin la
clave rejuega con la HISTORIA de su era (constantes REPLAY_*_PRE_* y
LEGACY_*), nunca con el umbral vigente.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.optimizer import bid, cortes, hygiene, windows
from app.optimizer.caso import POLITICA_BID, CasoHoja
from app.optimizer.eras import _agregado_sintetico as _agregado_sintetico
from app.optimizer.eras import _args_replay_bid as _args_replay_bid
from app.optimizer.eras import _replay_bid as _replay_bid
from app.optimizer.eras import decide_bid_era_bandas
from app.optimizer.politica import Mantener, Mover, Pausar, Regresar, decide


def _dec_de_json(valor) -> Decimal | None:
    """Decimal de vuelta desde el string congelado (regla 4; nunca float)."""
    return Decimal(str(valor)) if valor is not None else None


def _fechas_sinteticas(window_end: dt.date, n: int) -> tuple[dt.date, ...]:
    """n fechas dentro de la ventana terminando en window_end: el CONTEO es lo
    que replayea `completa` (>= 7 fechas); el replay sintetiza las fechas."""
    return tuple(window_end - dt.timedelta(days=n - 1 - i) for i in range(n))


def _replay_hygiene(inputs: dict) -> hygiene.ResultadoTermino:
    vt = inputs["ventana_terminos"]
    td = inputs["termino"]
    fin = dt.date.fromisoformat(vt["window_end"])
    observed = td["observed_at_max"]
    termino = windows.AgregadoTermino(
        ad_entity_id=0,  # no consumido por el motor; identidad no congelada
        search_term=td["search_term"],
        metric_currency=td["moneda"],
        cost=_dec_de_json(td["cost"]),
        ad_revenue=_dec_de_json(td["ad_revenue"]),
        clicks=td["clicks"],
        orders=td["orders"],
        fechas_distintas=td["fechas_distintas"],
        is_asin_like=False,  # un termino ASIN-like JAMAS genera decision (2.3)
        observed_at_max=dt.datetime.fromisoformat(observed) if observed else None,
    )
    terminos = windows.TerminosCortes(
        ad_entity_id=0,
        window_start=dt.date.fromisoformat(vt["window_start"]),
        window_end=fin,
        fechas_entidad=_fechas_sinteticas(fin, vt["fechas"]),
        terminos=(termino,),
    )
    harvest = inputs["goal"]["harvest"]
    config = (
        hygiene.ConfigHarvest(
            campaign_id=harvest["campaign_id"],
            ad_group_id=harvest["ad_group_id"],
            default_bid=Decimal(harvest["default_bid"]),
            moneda=harvest["moneda"],
        )
        if harvest
        else None
    )
    # CORTES 01 (spec): el replay LEE inputs.corte.umbral_clicks_usado y
    # piso_cost_usado, JAMAS recalcula evidencia ni AOV (el snapshot de la
    # decision ya no existe). Fila historica sin la clave (pre-CORTES, o
    # congelada en 1.2/1.3 sin piso) -> legacy 20 y 8/130, replay exacto.
    corte = inputs.get("corte")
    umbral_negative = corte["umbral_clicks_usado"] if corte is not None else cortes.LEGACY_NEGATIVE
    piso = (
        Decimal(corte["piso_cost_usado"])
        if corte is not None and "piso_cost_usado" in corte
        else None
    )
    (resultado,) = hygiene.decide_hygiene(
        platform=inputs["platform"],
        terminos=terminos,
        target_acos_pct=Decimal(inputs["target_acos_pct_usado"]),
        config_harvest=config,
        # keywords_existentes vacio: una decision de harvest solo existe si el
        # termino NO estaba duplicado al decidir (replay contra nada bloquea).
        keywords_existentes=frozenset(),
        umbral_negative=umbral_negative,
        piso_negative=piso,
    )
    return resultado


def replay_bid_con_target(inputs: dict, target_acos_pct: Decimal) -> bid.ResultadoBid:
    """Re-decide UNA bid de la era bandas desde sus inputs congelados bajo
    un target DADO (ORBIT 06 2.3, D-2.3.8: el tool compara_target_margen
    compara el target manual vs el derivado sobre las MISMAS entradas).
    Misma reconstruccion que _replay_bid (cero copias: es el mismo codigo
    con el target inyectado); devuelve el ResultadoBid COMPLETO
    (kind/new/factor). El target se valida en el camino v1 (> 0, como el
    spot-check). Solo aplica a filas viejas (sin inputs.politica)."""
    assert inputs.get("politica") != POLITICA_BID, "fila nueva por el camino viejo"
    return _replay_bid(inputs, target_acos_pct)


def reproduce(inputs: dict) -> tuple[str | None, Decimal | None, str | None]:
    """Re-decide UNA decision desde sus inputs congelados y devuelve
    (kind, new_value, value_currency). Es la funcion del spot-check humano
    (4.4): reproduce(inputs) debe igualar la decision persistida.

    Despacho por era (BIDS 02 M.3, bosquejo replay.py):
      inputs["politica"] == "niveles_v3" -> decide(CasoHoja.desde_json(inputs["caso"]))
      inputs["motor"] == "bid" sin esa clave -> decide_bid_era_bandas(inputs)
      inputs["motor"] == "hygiene" -> _replay_hygiene, como hoy.
    """
    if inputs.get("politica") == POLITICA_BID:
        caso = CasoHoja.desde_json(inputs["caso"])
        veredicto = decide(caso)
        if isinstance(veredicto, Pausar):
            return ("pause", None, None)
        if isinstance(veredicto, (Mover, Regresar)):
            return ("bid", veredicto.bid_nuevo, caso.bid.moneda)
        assert isinstance(veredicto, Mantener)
        return (None, None, None)
    motor = inputs.get("motor")
    if motor == "bid":
        return decide_bid_era_bandas(inputs)
    elif motor == "hygiene":
        resultado = _replay_hygiene(inputs)
    else:
        raise ValueError(f"inputs.motor fuera del vocabulario {{bid, hygiene}}: {motor!r}")
    return (resultado.kind, resultado.new_value, resultado.value_currency)
