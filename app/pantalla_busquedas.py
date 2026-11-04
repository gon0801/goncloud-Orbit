"""Contrato de la pantalla de busquedas que gastan sin vender (BIDS 02, P.6).

Solo lectura (decision D11): paginas de producto ajenas y palabras
que ya gastaron el gasto para concluir sin un pedido, junto a la
referencia de lo que daria el azar con la conversion de la cuenta.
No escribe nada en Amazon y no propone nada a la cola de veto: el
dueno niega a mano. Puro contrato: dataclasses + `como_dict()` +
una funcion pura de resumen + lecturas SOLO SELECT.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.goals import gasto_para_concluir_desde_settings

PALABRAS_VACIAS = frozenset(
    [
        "de",
        "la",
        "el",
        "los",
        "las",
        "para",
        "y",
        "con",
        "en",
        "un",
        "una",
        "por",
        "del",
        "a",
        "al",
        "que",
        "the",
        "of",
        "for",
        "and",
        "to",
        "in",
        "on",
        "with",
    ]
)

_RE_PALABRA = re.compile(r"[a-záéíóúñü0-9]+")


@dataclass(frozen=True)
class FilaBusqueda:
    """Una busqueda dentro de su ad group, ya colapsada a la ultima
    observacion por fecha y sumada en la ventana."""

    ad_group_id: int
    campana: str
    termino: str
    es_asin: bool
    clics: int | None
    gasto: Decimal | None
    pedidos: int | None


@dataclass(frozen=True)
class FilaAsinAjeno:
    asin: str
    clics: int
    gasto: Decimal
    campanas: tuple[str, ...]
    es_propio: bool  # el ASIN es de un producto del dueno: se marca, no se esconde

    def como_dict(self) -> dict:
        return {
            "asin": self.asin,
            "clics": self.clics,
            "gasto": str(self.gasto),
            "campanas": list(self.campanas),
            "es_propio": self.es_propio,
        }


@dataclass(frozen=True)
class FilaPalabra:
    palabra: str  # una o dos palabras
    busquedas: int
    clics: int
    gasto: Decimal
    ejemplos: tuple[str, ...]  # hasta tres busquedas, las de mas gasto

    def como_dict(self) -> dict:
        return {
            "palabra": self.palabra,
            "busquedas": self.busquedas,
            "clics": self.clics,
            "gasto": str(self.gasto),
            "ejemplos": list(self.ejemplos),
        }


@dataclass(frozen=True)
class ReferenciaAzar:
    """Parte del gasto en busquedas sin pedido: lo visto contra lo que
    daria el azar con la conversion de la cuenta. None si la cuenta no
    tiene pedidos o clics en la ventana (regla 3: no se inventa la
    conversion)."""

    visto_pct: Decimal | None
    azar_pct: Decimal | None

    def como_dict(self) -> dict:
        return {
            "visto_pct": None if self.visto_pct is None else str(self.visto_pct),
            "azar_pct": None if self.azar_pct is None else str(self.azar_pct),
        }


@dataclass(frozen=True)
class PantallaBusquedas:
    plataforma: str
    moneda: str
    desde: dt.date
    hasta: dt.date
    referencia: ReferenciaAzar
    asins: tuple[FilaAsinAjeno, ...]
    palabras: tuple[FilaPalabra, ...]
    asins_a_medio_camino: int
    palabras_a_medio_camino: int

    def como_dict(self) -> dict:
        return {
            "plataforma": self.plataforma,
            "moneda": self.moneda,
            "desde": self.desde.isoformat(),
            "hasta": self.hasta.isoformat(),
            "referencia": self.referencia.como_dict(),
            "asins": [a.como_dict() for a in self.asins],
            "palabras": [p.como_dict() for p in self.palabras],
            "asins_a_medio_camino": self.asins_a_medio_camino,
            "palabras_a_medio_camino": self.palabras_a_medio_camino,
        }


def _pct(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _referencia(filas: tuple[FilaBusqueda, ...]) -> ReferenciaAzar:
    gasto = sum(f.gasto for f in filas)
    clics = sum(f.clics for f in filas)
    pedidos = sum(f.pedidos for f in filas)
    if pedidos == 0 or clics == 0 or gasto == 0:
        return ReferenciaAzar(None, None)
    p = Decimal(pedidos) / Decimal(clics)
    visto = Decimal(100) * sum(f.gasto for f in filas if f.pedidos == 0) / gasto
    azar = Decimal(100) * sum(f.gasto * (1 - p) ** f.clics for f in filas) / gasto
    return ReferenciaAzar(_pct(visto), _pct(azar))


def _resumenes_asin(filas, minimo, propios):
    por_asin: dict[str, dict] = {}
    for f in filas:
        if not f.es_asin:
            continue
        clave = f.termino.strip().upper()
        aqui = por_asin.setdefault(
            clave, {"clics": 0, "gasto": Decimal(0), "pedidos": 0, "campanas": set()}
        )
        aqui["clics"] += f.clics
        aqui["gasto"] += f.gasto
        aqui["pedidos"] += f.pedidos
        aqui["campanas"].add(f.campana)
    lista = []
    medio = 0
    for asin, v in por_asin.items():
        if v["pedidos"] != 0:
            continue
        if v["gasto"] >= minimo:
            lista.append(
                FilaAsinAjeno(
                    asin,
                    v["clics"],
                    v["gasto"],
                    tuple(sorted(v["campanas"])),
                    asin in propios,
                )
            )
        elif v["gasto"] >= minimo / 2:
            medio += 1
    lista.sort(key=lambda a: (-a.gasto, a.asin))
    return tuple(lista), medio


def _entradas(fila) -> set[str]:
    toks = _RE_PALABRA.findall(fila.termino.lower())
    unas = {t for t in toks if t not in PALABRAS_VACIAS and len(t) > 2}
    dos = {" ".join(toks[i : i + 2]) for i in range(len(toks) - 1)}
    return unas | dos


def _resumenes_palabra(filas, minimo):
    por_entrada: dict[str, dict] = {}
    for f in filas:
        if f.es_asin:
            continue
        for entrada in _entradas(f):
            aqui = por_entrada.setdefault(
                entrada,
                {"clics": 0, "gasto": Decimal(0), "pedidos": 0, "por_texto": {}},
            )
            aqui["clics"] += f.clics
            aqui["gasto"] += f.gasto
            aqui["pedidos"] += f.pedidos
            aqui["por_texto"][f.termino] = aqui["por_texto"].get(f.termino, Decimal(0)) + f.gasto
    lista = []
    medio = 0
    for palabra, v in por_entrada.items():
        if v["pedidos"] != 0:
            continue
        if v["gasto"] >= minimo:
            ejemplos = tuple(
                texto
                for texto, _g in sorted(v["por_texto"].items(), key=lambda par: (-par[1], par[0]))[
                    :3
                ]
            )
            lista.append(
                FilaPalabra(palabra, len(v["por_texto"]), v["clics"], v["gasto"], ejemplos)
            )
        elif v["gasto"] >= minimo / 2:
            medio += 1
    lista.sort(key=lambda p: (-p.gasto, p.palabra))
    return tuple(lista), medio


def resume_busquedas(
    filas: tuple[FilaBusqueda, ...], *, gasto_para_concluir: Decimal, asins_propios: frozenset[str]
) -> tuple[ReferenciaAzar, tuple[FilaAsinAjeno, ...], tuple[FilaPalabra, ...], int, int]:
    """Es pura: una fila con clics, gasto o pedidos None no suma y no
    cuenta como cero (regla 3)."""
    completas = tuple(
        f for f in filas if f.clics is not None and f.gasto is not None and f.pedidos is not None
    )
    referencia = _referencia(completas)
    asins, asins_medio = _resumenes_asin(completas, gasto_para_concluir, asins_propios)
    palabras, palabras_medio = _resumenes_palabra(completas, gasto_para_concluir)
    return referencia, asins, palabras, asins_medio, palabras_medio


_SQL_SETTINGS = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""

_SQL_BUSQUEDAS = """
WITH ultima AS (
    SELECT DISTINCT ON (o.ad_entity_id, o.search_term, o.metric_date)
           o.ad_entity_id, o.search_term, o.is_asin_like, o.cost, o.clicks, o.orders,
           coalesce(c.name, c.external_id) AS campana
      FROM search_term_observation o
      JOIN ad_entity ag ON ag.id = o.ad_entity_id
      JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
      JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
     WHERE o.platform = %s::platform AND o.metric_date BETWEEN %s AND %s
     ORDER BY o.ad_entity_id, o.search_term, o.metric_date,
              o.observed_at DESC, o.source_report_id DESC NULLS LAST)
SELECT ad_entity_id, campana, search_term, bool_or(is_asin_like),
       CASE WHEN bool_and(clicks IS NOT NULL) THEN sum(clicks)::bigint END,
       CASE WHEN bool_and(cost IS NOT NULL) THEN sum(cost) END,
       CASE WHEN bool_and(orders IS NOT NULL) THEN sum(orders)::bigint END
  FROM ultima
 GROUP BY 1, 2, 3
 ORDER BY 3
"""

_SQL_PROPIOS = """
SELECT upper(external_id) FROM listing WHERE platform IN ('amazon_mx', 'amazon_us')
"""


def lee_busquedas(conn, *, plataforma: str, hoy: dt.date) -> PantallaBusquedas:
    """Solo SELECT: ultima observacion por ad group, busqueda y fecha
    en los 90 dias que terminan en hoy - 3; solo campanas encendidas."""
    fila_settings = conn.execute(_SQL_SETTINGS).fetchone()
    settings = fila_settings[0] if fila_settings is not None else {}
    hasta = hoy - dt.timedelta(days=3)
    desde = hasta - dt.timedelta(days=90)
    filas = tuple(
        FilaBusqueda(
            ad_group_id=f[0],
            campana=f[1],
            termino=f[2],
            es_asin=bool(f[3]),
            clics=f[4],
            gasto=f[5],
            pedidos=f[6],
        )
        for f in conn.execute(_SQL_BUSQUEDAS, (plataforma, desde, hasta)).fetchall()
    )
    propios = frozenset(f[0] for f in conn.execute(_SQL_PROPIOS).fetchall())
    referencia, asins, palabras, asins_medio, palabras_medio = resume_busquedas(
        filas,
        gasto_para_concluir=gasto_para_concluir_desde_settings(settings, plataforma),
        asins_propios=propios,
    )
    return PantallaBusquedas(
        plataforma=plataforma,
        moneda=PLATAFORMAS_MONEDA[plataforma],
        desde=desde,
        hasta=hasta,
        referencia=referencia,
        asins=asins,
        palabras=palabras,
        asins_a_medio_camino=asins_medio,
        palabras_a_medio_camino=palabras_medio,
    )
