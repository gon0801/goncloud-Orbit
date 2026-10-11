"""Configuracion de campana (BIDS 02, V.1). Frontera del payload de
`/sp/campaigns/list`: lo que hoy se tira (presupuesto, estrategia,
ajustes por placement, gasto fuera de Amazon) se vuelve dominio.

`config_de_payload` valida forma y tipos; item ilegible = None y se
cuenta como skip, jamas ceros. El presupuesto llega sin moneda y se
guarda con la del perfil, igual que los bids (`_bid_decimal`).
`guarda_config` es append-only y solo inserta cuando algo cambio
respecto de la ultima fila. Todo corre dentro del sync de estructura.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.ads.structure_plan import _bid_decimal

Moneda = Literal["MXN", "USD"]
Plataforma = Literal["amazon_mx", "amazon_us"]
_MONEDAS = ("MXN", "USD")

_AJUSTE_POR_PLACEMENT = {
    "PLACEMENT_TOP": 0,
    "PLACEMENT_REST_OF_SEARCH": 1,
    "PLACEMENT_PRODUCT_PAGE": 2,
}
_PCT_MAX = 900


@dataclass(frozen=True)
class ConfigCampana:
    """Dominio, no payload: el parseo del wire queda detras de
    `config_de_payload`. Moneda None solo sin presupuesto (espejo del
    CHECK de nulidad de la 0061: la fila sin presupuesto no trae moneda
    y al releerla la moneda es desconocida)."""

    campana_externa: str
    presupuesto_diario: Decimal | None
    moneda: Moneda | None
    estrategia_puja: str | None
    ajuste_top_pct: int | None
    ajuste_resto_pct: int | None
    ajuste_producto_pct: int | None
    fuera_de_amazon: str | None = None

    def __post_init__(self) -> None:
        if (self.presupuesto_diario is None) != (self.moneda is None):
            raise ValueError("presupuesto y moneda van juntos o ausentes juntos")

    def como_json(self) -> dict:
        return {
            "campana_externa": self.campana_externa,
            "presupuesto_diario": (
                None if self.presupuesto_diario is None else str(self.presupuesto_diario)
            ),
            "moneda": self.moneda,
            "estrategia_puja": self.estrategia_puja,
            "ajuste_top_pct": self.ajuste_top_pct,
            "ajuste_resto_pct": self.ajuste_resto_pct,
            "ajuste_producto_pct": self.ajuste_producto_pct,
            "fuera_de_amazon": self.fuera_de_amazon,
        }

    @classmethod
    def desde_json(cls, doc: dict) -> ConfigCampana:
        """Falla fuerte con forma corrupta (ValueError/TypeError)."""
        if not isinstance(doc, dict):
            raise TypeError("config de campana no es objeto")
        try:
            campana = doc["campana_externa"]
            presupuesto = doc["presupuesto_diario"]
            moneda = doc["moneda"]
            estrategia = doc["estrategia_puja"]
            top = doc["ajuste_top_pct"]
            resto = doc["ajuste_resto_pct"]
            producto = doc["ajuste_producto_pct"]
            fuera = doc["fuera_de_amazon"]
        except KeyError as exc:
            raise ValueError(f"config de campana sin {exc}") from None
        if not isinstance(campana, str) or not campana:
            raise ValueError("campana_externa no es texto")
        if presupuesto is not None:
            try:
                presupuesto = Decimal(str(presupuesto))
            except (InvalidOperation, ValueError):
                raise ValueError("presupuesto_diario no numerico") from None
        if moneda is not None and moneda not in _MONEDAS:
            raise ValueError("moneda desconocida")
        if estrategia is not None and not isinstance(estrategia, str):
            raise ValueError("estrategia_puja no es texto")
        for nombre, valor in (("top", top), ("resto", resto), ("producto", producto)):
            if valor is not None and (isinstance(valor, bool) or not isinstance(valor, int)):
                raise ValueError(f"ajuste_{nombre}_pct no es entero")
        if fuera is not None and not isinstance(fuera, str):
            raise ValueError("fuera_de_amazon no es texto")
        return cls(
            campana_externa=campana,
            presupuesto_diario=presupuesto,
            moneda=moneda,
            estrategia_puja=estrategia,
            ajuste_top_pct=top,
            ajuste_resto_pct=resto,
            ajuste_producto_pct=producto,
            fuera_de_amazon=fuera,
        )


def _presupuesto(budget: object) -> Decimal | None:
    """Ausente = None. Presente exige dict con numero positivo y tipo
    DAILY explicito (sin tipo no se sabe la frecuencia: asumir diario
    guardaria mentira)."""
    if budget is None:
        return None
    if not isinstance(budget, dict):
        raise ValueError("budget con mala forma")
    if budget.get("budgetType") != "DAILY":
        raise ValueError("budgetType no diario")
    valor = budget.get("budget")
    if valor is None:
        raise ValueError("budget presente sin numero")
    return _bid_decimal(valor, "presupuesto")


def _ajustes(placements: object) -> tuple[int, int, int]:
    """Sin lista = ceros (sin ajustes configurados). Cada placement
    conocido cae en su ajuste; lo demas es forma corrupta."""
    if placements is None:
        return (0, 0, 0)
    if not isinstance(placements, list):
        raise ValueError("placementBidding no es lista")
    valores = [0, 0, 0]
    vistos: set[str] = set()
    for entrada in placements:
        if not isinstance(entrada, dict):
            raise ValueError("entrada de placement con mala forma")
        ubicacion = entrada.get("placement")
        if (
            not isinstance(ubicacion, str)
            or ubicacion not in _AJUSTE_POR_PLACEMENT
            or ubicacion in vistos
        ):
            raise ValueError("placement desconocido o repetido")
        pct = entrada.get("percentage")
        if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= _PCT_MAX:
            raise ValueError("percentage fuera de 0 a 900")
        vistos.add(ubicacion)
        valores[_AJUSTE_POR_PLACEMENT[ubicacion]] = pct
    return (valores[0], valores[1], valores[2])


def _dinamica(bidding: object) -> tuple[str | None, tuple[int | None, int | None, int | None]]:
    """Sin dynamicBidding, estrategia y ajustes son None."""
    if bidding is None:
        return None, (None, None, None)
    if not isinstance(bidding, dict):
        raise ValueError("dynamicBidding con mala forma")
    estrategia = bidding.get("strategy")
    if estrategia is not None and not isinstance(estrategia, str):
        raise ValueError("strategy no es texto")
    if isinstance(estrategia, str) and not estrategia.strip():
        raise ValueError("strategy vacia")
    return estrategia, _ajustes(bidding.get("placementBidding"))


def _fuera(off: object) -> str | None:
    """Vacio = None. Lo demas, texto JSON con llaves ordenadas (V.3
    sellara el vocabulario con la sonda 4). No serializable = corrupto
    y anula la campana (visible en skips, jamas NULL silencioso)."""
    if not off:
        return None
    try:
        return json.dumps(off, sort_keys=True)
    except TypeError:
        raise ValueError("offAmazonSettings no serializable") from None


def config_de_payload(item: dict, moneda: Moneda) -> ConfigCampana | None:
    """Frontera: valida forma y tipos; item ilegible = None y se cuenta
    como skip (jamas ceros)."""
    if not isinstance(item, dict):
        return None
    externa = item.get("campaignId")
    if externa is None:
        return None
    try:
        presupuesto = _presupuesto(item.get("budget"))
        estrategia, (top, resto, producto) = _dinamica(item.get("dynamicBidding"))
        fuera = _fuera(item.get("offAmazonSettings"))
    except ValueError:
        return None
    return ConfigCampana(
        campana_externa=str(externa),
        presupuesto_diario=presupuesto,
        moneda=moneda if presupuesto is not None else None,
        estrategia_puja=estrategia,
        ajuste_top_pct=top,
        ajuste_resto_pct=resto,
        ajuste_producto_pct=producto,
        fuera_de_amazon=fuera,
    )


_SQL_ENTIDADES = """
SELECT id, external_id FROM ad_entity
 WHERE platform = %s AND kind = 'campaign' AND external_id = ANY(%s)
"""

_SQL_VIGENTES = """
SELECT DISTINCT ON (ad_entity_id) ad_entity_id, presupuesto_diario, presupuesto_moneda,
       estrategia_puja, ajuste_top_pct, ajuste_resto_pct, ajuste_producto_pct, fuera_de_amazon
  FROM ads_campana_config_observation
 WHERE ad_entity_id = ANY(%s)
 ORDER BY ad_entity_id, observed_at DESC, id DESC
"""

_SQL_INSERTA = """
INSERT INTO ads_campana_config_observation
    (ad_entity_id, observed_at, presupuesto_diario, presupuesto_moneda, estrategia_puja,
     ajuste_top_pct, ajuste_resto_pct, ajuste_producto_pct, fuera_de_amazon)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_SQL_CONFIG_VIGENTE = """
SELECT o.presupuesto_diario, o.presupuesto_moneda, o.estrategia_puja, o.ajuste_top_pct,
       o.ajuste_resto_pct, o.ajuste_producto_pct, o.fuera_de_amazon, e.external_id
  FROM ads_campana_config_observation o
  JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE o.ad_entity_id = %s
 ORDER BY o.observed_at DESC, o.id DESC
 LIMIT 1
"""


def _huella(config: ConfigCampana) -> tuple:
    """Lo que se compara contra la ultima fila: moneda solo con
    presupuesto (espejo del CHECK de nulidad)."""
    return (
        config.presupuesto_diario,
        config.moneda if config.presupuesto_diario is not None else None,
        config.estrategia_puja,
        config.ajuste_top_pct,
        config.ajuste_resto_pct,
        config.ajuste_producto_pct,
        config.fuera_de_amazon,
    )


def guarda_config(
    conn, plataforma: Plataforma, configs: list[ConfigCampana], observado_el: dt.datetime
) -> int:
    """Append-only y solo cuando algo cambio respecto de la ultima fila
    de esa campana. Corre dentro del sync (misma transaccion). Sin
    ad_entity (no pasa desde el sync) la config se salta. Devuelve filas
    insertadas."""
    if not configs:
        return 0
    unicas: dict[str, ConfigCampana] = {}
    for config in configs:
        unicas[config.campana_externa] = config
    externas = sorted(unicas)
    entidades = {
        fila[1]: fila[0] for fila in conn.execute(_SQL_ENTIDADES, (plataforma, externas)).fetchall()
    }
    vigentes = {
        fila[0]: tuple(fila[1:])
        for fila in conn.execute(
            _SQL_VIGENTES, ([entidades[e] for e in externas if e in entidades],)
        ).fetchall()
    }
    filas = []
    for externa in externas:
        entidad = entidades.get(externa)
        if entidad is None:
            continue
        huella = _huella(unicas[externa])
        if vigentes.get(entidad) == huella:
            continue
        filas.append((entidad, observado_el, *huella))
    conn.cursor().executemany(_SQL_INSERTA, filas)
    return len(filas)


def config_vigente(conn, ad_entity_id: int) -> ConfigCampana | None:
    """Ultima configuracion de la campana, o None sin filas. Sin
    presupuesto la moneda es desconocida (la fila no la trae)."""
    fila = conn.execute(_SQL_CONFIG_VIGENTE, (ad_entity_id,)).fetchone()
    if fila is None:
        return None
    return ConfigCampana(
        campana_externa=fila[7],
        presupuesto_diario=fila[0],
        moneda=fila[1],
        estrategia_puja=fila[2],
        ajuste_top_pct=fila[3],
        ajuste_resto_pct=fila[4],
        ajuste_producto_pct=fila[5],
        fuera_de_amazon=fila[6],
    )
