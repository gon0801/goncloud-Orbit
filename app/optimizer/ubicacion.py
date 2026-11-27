"""Politica pura de ajustes automaticos por ubicacion (AUTO-04, `ubicacion_v1`).

Sin IO, sin reloj, sin float: toda la aritmetica corre en `Decimal` bajo
`localcontext(prec=PRECISION)` y la comparacion se hace por multiplicacion.
`decide_ubicacion` no lee nada fuera del `CasoCampana` que recibe, de modo que
el replay determinista (`caso_a_json` / `caso_desde_json`) reproduce el mismo
veredicto sin tocar la base.

Las tres ubicaciones ajustables son las que llevan multiplicador de puja en
Amazon: `arriba_de_busqueda` (top of search), `resto_de_busqueda` (rest of
search) y `paginas_de_producto` (product pages). El cuarto bloque
(`fuera_de_amazon`) queda fuera de alcance.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

from app.optimizer.caso import PRECISION, EconomiaPlataforma
from app.optimizer.evidencia import gamma_p, gamma_q
from app.optimizer.politica import CLICS_NUEVOS, PEDIDOS_GRUPO
from app.optimizer.temporada_config import CampanaTopada

POLITICA = "ubicacion_v1"

Ubicacion = Literal["arriba_de_busqueda", "resto_de_busqueda", "paginas_de_producto"]

UBICACIONES: tuple[Ubicacion, ...] = (
    "arriba_de_busqueda",
    "resto_de_busqueda",
    "paginas_de_producto",
)

# ---------------------------------------------------------------------------
# Constantes de la politica (seccion 6 de la especificacion)
# ---------------------------------------------------------------------------

DIAS_ENFRIAMIENTO = 14
DIAS_VENTANA_MAX = 30
DIAS_REZAGO = 3  # ventana_hasta = decided_at.date() - 3d
DIAS_VENTANA_MIN = 10
PEDIDOS_MIN_SUBIDA = PEDIDOS_GRUPO  # 3
CLICS_MIN_SUBIDA = CLICS_NUEVOS  # 20
CLICS_MIN_RESTO = 20
MULT_SUBIDA = Decimal("0.85")
MULT_BAJA_SUAVE = Decimal("1.15")
MULT_BAJA_FUERTE = Decimal("1.35")
RAZON_RPC_MIN_SUBIDA = Decimal("1.15")
PASO_SUBIDA_PTS = 15
PASO_BAJA_SUAVE_PTS = 15
PASO_BAJA_FUERTE_PTS = 25
TOLERANCIA_CONCILIACION_PCT = Decimal("0.01")
TOLERANCIA_CONCILIACION_ABS = Decimal("0.05")

_CIEN = Decimal(100)
_ORDEN_RECORTE: dict[str, int] = {
    "paginas_de_producto": 0,
    "resto_de_busqueda": 1,
    "arriba_de_busqueda": 2,
}
_ORDEN_SUBIDA: dict[str, int] = {
    "arriba_de_busqueda": 0,
    "resto_de_busqueda": 1,
    "paginas_de_producto": 2,
}

# Techos por estrategia y ubicacion (dynamicBidding de Amazon).
TECHOS_POR_ESTRATEGIA: dict[str, dict[str, int]] = {
    "LEGACY_FOR_SALES": {
        "arriba_de_busqueda": 100,
        "resto_de_busqueda": 50,
        "paginas_de_producto": 50,
    },
    "MANUAL": {
        "arriba_de_busqueda": 100,
        "resto_de_busqueda": 50,
        "paginas_de_producto": 50,
    },
    "AUTO_FOR_SALES": {
        "arriba_de_busqueda": 50,
        "resto_de_busqueda": 25,
        "paginas_de_producto": 25,
    },
}

MODOS_UBICACION = ("off", "shadow", "live")
_ORDEN_MODO = {"off": 0, "shadow": 1, "live": 2}

# ---------------------------------------------------------------------------
# Motivos (vocabulario cerrado, espanol sin acentos)
# ---------------------------------------------------------------------------

MOTIVO_CAMPANA_NO_ELEGIBLE = "campana_no_elegible"
MOTIVO_CAMPANA_EN_IMPULSO = "campana_en_impulso"
MOTIVO_SIN_DYNAMIC_BIDDING = "sin_dynamic_bidding"
MOTIVO_ESTRATEGIA_DESCONOCIDA = "estrategia_desconocida"
MOTIVO_AJUSTE_EN_VUELO = "ajuste_en_vuelo"
MOTIVO_ENFRIAMIENTO = "en_enfriamiento_14d"
MOTIVO_DATOS_INCOMPLETOS = "datos_placements_incompletos"
MOTIVO_DESCUADRE = "descuadre_placements_campana"
MOTIVO_SIN_META = "sin_meta_campana"
MOTIVO_ACOS_FUERTE = "acos_sobre_meta_fuerte"
MOTIVO_ACOS = "acos_sobre_meta"
MOTIVO_GASTO_SIN_VENTA_DOBLE = "gasto_sin_venta_doble"
MOTIVO_GASTO_SIN_VENTA = "gasto_sin_venta"
MOTIVO_SOLO_BAJADAS = "solo_bajadas_activo"
MOTIVO_TEMPORADA_BAJA = "temporada_baja_sin_subida"
MOTIVO_PRESUPUESTO_SIN_MEDIR = "presupuesto_sin_medir"
MOTIVO_CAMPANA_TOPADA = "campana_topada"
MOTIVO_SUBIDA = "bajo_meta_con_ventaja"
MOTIVO_SIN_EVIDENCIA = "sin_evidencia_ubicacion"
MOTIVO_PRODUCTO_SIN_EVIDENCIA = "producto_sin_evidencia"

# Texto para la pantalla. Una sola fuente del vocabulario en espanol.
MOTIVOS_UBICACION_ES: dict[str, str] = {
    MOTIVO_CAMPANA_NO_ELEGIBLE: "la campaña no está habilitada para ajustes automáticos",
    MOTIVO_CAMPANA_EN_IMPULSO: "un producto de la campaña está en impulso",
    MOTIVO_SIN_DYNAMIC_BIDDING: "Amazon no reporta la estrategia de puja de la campaña",
    MOTIVO_ESTRATEGIA_DESCONOCIDA: "la estrategia de puja no es compatible con ajustes automáticos",
    MOTIVO_AJUSTE_EN_VUELO: "hay un cambio de ubicación pendiente de confirmar",
    MOTIVO_ENFRIAMIENTO: "en espera de 14 días tras el último cambio",
    MOTIVO_DATOS_INCOMPLETOS: "faltan datos recientes de ubicaciones",
    MOTIVO_DESCUADRE: "el gasto por ubicación no coincide con el total de la campaña",
    MOTIVO_SIN_META: "la campaña no tiene meta de ACoS definida",
    MOTIVO_ACOS_FUERTE: "ACoS muy por encima de la meta",
    MOTIVO_ACOS: "ACoS por encima de la meta",
    MOTIVO_GASTO_SIN_VENTA_DOBLE: "gasto muy alto sin ventas",
    MOTIVO_GASTO_SIN_VENTA: "gasto alto sin ventas",
    MOTIVO_SOLO_BAJADAS: "solo se permiten bajadas en este mercado",
    MOTIVO_TEMPORADA_BAJA: "temporada baja, no se proponen subidas",
    MOTIVO_PRESUPUESTO_SIN_MEDIR: "sin datos suficientes del uso de presupuesto",
    MOTIVO_CAMPANA_TOPADA: "la campaña agota su presupuesto diario",
    MOTIVO_SUBIDA: "bajo la meta y rinde más que el resto de la campaña",
    MOTIVO_SIN_EVIDENCIA: "sin datos suficientes en ninguna ubicación",
    MOTIVO_PRODUCTO_SIN_EVIDENCIA: (
        "faltó evidencia para calcular la meta de un producto de la campaña"
    ),
}

# ---------------------------------------------------------------------------
# Tipos del dominio
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenciaUbicacion:
    """Metricas maduras de UNA ubicacion en la ventana congelada.

    `ajuste_vigente_pct` es el multiplicador vigente; el lector normaliza
    `NULL` a 0. El resto de campos es `None` cuando la fuente no lo trae: un
    dato faltante nunca se convierte en cero (regla 3).
    """

    ubicacion: Ubicacion
    ajuste_vigente_pct: int
    clics: int | None
    gasto: Decimal | None
    pedidos: int | None
    venta: Decimal | None
    pedidos_inmaduros: int | None  # ultimos dias D-2..D-1; None abstiene recortes


@dataclass(frozen=True)
class MetaCampana:
    """Banda de metas congeladas de las hojas activas de la campana."""

    exigente_pct: Decimal  # menor meta entre las hojas activas
    laxa_pct: Decimal  # mayor meta entre las hojas activas
    procedencias: tuple[str, ...]


@dataclass(frozen=True)
class CasoCampana:
    campana_id: int
    plataforma: Literal["amazon_mx", "amazon_us"]
    config_id: int
    estrategia: str | None
    ventana_desde: dt.date
    ventana_hasta: dt.date
    ubicaciones: tuple[EvidenciaUbicacion, ...]  # exactamente las 3 ajustables
    meta: MetaCampana | None
    economia: EconomiaPlataforma
    gasto_campana: Decimal | None
    gasto_placements_total: Decimal | None  # suma de las 4 ubicaciones
    placements_frescos: bool
    dias_desde_ultimo_cambio: int | None
    ajuste_en_vuelo: bool
    elegible: bool
    en_impulso: bool
    solo_bajadas: bool
    dias_topados_7d: int | None
    topada: CampanaTopada
    fase_temporada: Literal["baja", "normal", "alta"] | None


@dataclass(frozen=True)
class MantenerUbicacion:
    motivo: str


@dataclass(frozen=True)
class MoverUbicacion:
    ubicacion: Ubicacion
    de_pct: int
    a_pct: int
    motivo: str


VeredictoUbicacion = MantenerUbicacion | MoverUbicacion


# ---------------------------------------------------------------------------
# Lectores de settings (fail-closed)
# ---------------------------------------------------------------------------


def modo_ubicacion_desde_settings(settings, platform: str) -> str:
    """`ads_ubicacion_<platform>` en {off, shadow, live}; ausente = off."""
    clave = f"ads_ubicacion_{platform}"
    valor = settings.get(clave)
    if valor is None:
        return "off"
    if not isinstance(valor, str) or valor not in MODOS_UBICACION:
        raise ValueError(f"{clave}: valor fuera de {MODOS_UBICACION}: {valor!r}")
    return valor


def solo_bajadas_desde_settings(settings, platform: str) -> bool:
    """`ads_ubicacion_solo_bajadas_<platform>`; ausente = True en US, False en MX."""
    clave = f"ads_ubicacion_solo_bajadas_{platform}"
    valor = settings.get(clave)
    if valor is None:
        return platform == "amazon_us"
    if isinstance(valor, bool):
        return valor
    if valor == "true":
        return True
    if valor == "false":
        return False
    raise ValueError(f"{clave}: se esperaba true/false, llego {valor!r}")


def max_en_espera_desde_settings(settings, platform: str) -> int:
    """`ads_ubicacion_max_en_espera_<platform>`; ausente = 6 MX / 2 US."""
    clave = f"ads_ubicacion_max_en_espera_{platform}"
    valor = settings.get(clave)
    if valor is None:
        return 6 if platform == "amazon_mx" else 2
    if isinstance(valor, bool):
        raise ValueError(f"{clave}: entero esperado, llego booleano {valor!r}")
    if isinstance(valor, int):
        n = valor
    elif isinstance(valor, str) and valor.isascii() and valor.isdigit():
        n = int(valor)
    else:
        raise ValueError(f"{clave}: entero esperado, llego {valor!r}")
    if n < 0:
        raise ValueError(f"{clave}: no admite negativos, llego {n}")
    return n


def techo_ubicacion(estrategia: str, ubicacion: str) -> int:
    """Techo del multiplicador de una ubicacion segun la estrategia de puja."""
    try:
        return TECHOS_POR_ESTRATEGIA[estrategia][ubicacion]
    except KeyError as exc:
        raise ValueError(
            f"sin techo para estrategia={estrategia!r} ubicacion={ubicacion!r}"
        ) from exc


def modo_efectivo(*modos: str) -> str:
    """El modo mas restrictivo de la cadena `off < shadow < live`."""
    for modo in modos:
        if modo not in _ORDEN_MODO:
            raise ValueError(f"modo fuera de {MODOS_UBICACION}: {modo!r}")
    return min(modos, key=_ORDEN_MODO.__getitem__)


# ---------------------------------------------------------------------------
# Politica
# ---------------------------------------------------------------------------


def decide_ubicacion(caso: CasoCampana) -> VeredictoUbicacion:
    """Devuelve el movimiento (o la abstencion) de la campana para este ciclo."""
    motivo = _compuerta_de_campana(caso)
    if motivo is not None:
        return MantenerUbicacion(motivo)

    recorte = _mejor_recorte(caso)
    if recorte is not None:
        return recorte

    motivo = _compuerta_de_subida(caso)
    if motivo is not None:
        return MantenerUbicacion(motivo)

    subida = _mejor_subida(caso)
    if subida is not None:
        return subida
    return MantenerUbicacion(MOTIVO_SIN_EVIDENCIA)


def _compuerta_de_campana(caso: CasoCampana) -> str | None:
    if not caso.elegible:
        return MOTIVO_CAMPANA_NO_ELEGIBLE
    if caso.en_impulso:
        return MOTIVO_CAMPANA_EN_IMPULSO
    if caso.estrategia is None:
        return MOTIVO_SIN_DYNAMIC_BIDDING
    if caso.estrategia not in TECHOS_POR_ESTRATEGIA:
        return MOTIVO_ESTRATEGIA_DESCONOCIDA
    if caso.ajuste_en_vuelo:
        return MOTIVO_AJUSTE_EN_VUELO
    if (
        caso.dias_desde_ultimo_cambio is not None
        and caso.dias_desde_ultimo_cambio < DIAS_ENFRIAMIENTO
    ):
        return MOTIVO_ENFRIAMIENTO
    dias_ventana = (caso.ventana_hasta - caso.ventana_desde).days + 1
    if not caso.placements_frescos or dias_ventana < DIAS_VENTANA_MIN:
        return MOTIVO_DATOS_INCOMPLETOS
    if caso.gasto_campana is None or caso.gasto_placements_total is None:
        return MOTIVO_DATOS_INCOMPLETOS
    tolerancia = max(TOLERANCIA_CONCILIACION_ABS, TOLERANCIA_CONCILIACION_PCT * caso.gasto_campana)
    if abs(caso.gasto_placements_total - caso.gasto_campana) > tolerancia:
        return MOTIVO_DESCUADRE
    if caso.meta is None:
        return MOTIVO_SIN_META
    return None


@dataclass(frozen=True)
class _Candidato:
    ubicacion: Ubicacion
    de_pct: int
    a_pct: int
    motivo: str
    margen: Decimal  # exceso (recorte) u holgura (subida)
    orden: int


def _mejor_recorte(caso: CasoCampana) -> MoverUbicacion | None:
    candidatos = [
        c
        for ev in caso.ubicaciones
        if ev.ajuste_vigente_pct > 0
        for c in (_candidato_recorte(ev, caso.meta, caso.economia),)
        if c is not None
    ]
    if not candidatos:
        return None
    elegido = min(candidatos, key=lambda c: (-c.margen, c.orden))
    return MoverUbicacion(
        ubicacion=elegido.ubicacion,
        de_pct=elegido.de_pct,
        a_pct=elegido.a_pct,
        motivo=elegido.motivo,
    )


def _candidato_recorte(
    ev: EvidenciaUbicacion, meta: MetaCampana | None, economia: EconomiaPlataforma
) -> _Candidato | None:
    if meta is None or meta.laxa_pct <= 0:
        return None
    if ev.pedidos is None or ev.gasto is None or ev.venta is None:
        return None
    paso: int
    motivo: str
    if ev.pedidos >= 1:
        if not (ev.venta > 0 and ev.gasto > 0):
            return None
        with localcontext(prec=PRECISION):
            base_mu = Decimal(ev.pedidos) * ev.gasto / ev.venta
            a = Decimal(ev.pedidos + 1)
            if (
                gamma_p(a, base_mu / (MULT_BAJA_FUERTE * meta.laxa_pct / _CIEN))
                >= economia.confianza_recorte
            ):
                paso, motivo = PASO_BAJA_FUERTE_PTS, MOTIVO_ACOS_FUERTE
            elif (
                gamma_p(a, base_mu / (MULT_BAJA_SUAVE * meta.laxa_pct / _CIEN))
                >= economia.confianza_recorte
            ):
                paso, motivo = PASO_BAJA_SUAVE_PTS, MOTIVO_ACOS
            else:
                return None
    else:
        if ev.pedidos_inmaduros is None or ev.pedidos_inmaduros >= 1:
            return None
        if ev.gasto >= 2 * economia.gasto_para_concluir:
            paso, motivo = PASO_BAJA_FUERTE_PTS, MOTIVO_GASTO_SIN_VENTA_DOBLE
        elif ev.gasto >= economia.gasto_para_concluir:
            paso, motivo = PASO_BAJA_SUAVE_PTS, MOTIVO_GASTO_SIN_VENTA
        else:
            return None
    a_pct = max(0, ev.ajuste_vigente_pct - paso)
    margen = ev.gasto - (meta.laxa_pct / _CIEN) * ev.venta
    return _Candidato(
        ubicacion=ev.ubicacion,
        de_pct=ev.ajuste_vigente_pct,
        a_pct=a_pct,
        motivo=motivo,
        margen=margen,
        orden=_ORDEN_RECORTE[ev.ubicacion],
    )


def _compuerta_de_subida(caso: CasoCampana) -> str | None:
    if caso.solo_bajadas:
        return MOTIVO_SOLO_BAJADAS
    if caso.fase_temporada == "baja":
        return MOTIVO_TEMPORADA_BAJA
    if caso.dias_topados_7d is None:
        return MOTIVO_PRESUPUESTO_SIN_MEDIR
    if caso.dias_topados_7d >= caso.topada.dias:
        return MOTIVO_CAMPANA_TOPADA
    return None


def _mejor_subida(caso: CasoCampana) -> MoverUbicacion | None:
    candidatos = [
        c for ev in caso.ubicaciones for c in (_candidato_subida(ev, caso),) if c is not None
    ]
    if not candidatos:
        return None
    elegido = min(candidatos, key=lambda c: (-c.margen, c.orden))
    return MoverUbicacion(
        ubicacion=elegido.ubicacion,
        de_pct=elegido.de_pct,
        a_pct=elegido.a_pct,
        motivo=MOTIVO_SUBIDA,
    )


def _candidato_subida(ev: EvidenciaUbicacion, caso: CasoCampana) -> _Candidato | None:
    meta = caso.meta
    if meta is None or meta.exigente_pct <= 0 or caso.estrategia is None:
        return None
    techo = TECHOS_POR_ESTRATEGIA[caso.estrategia][ev.ubicacion]
    if ev.ajuste_vigente_pct >= techo:
        return None
    if (
        ev.pedidos is None
        or ev.clics is None
        or ev.gasto is None
        or ev.venta is None
        or ev.pedidos < PEDIDOS_MIN_SUBIDA
        or ev.clics < CLICS_MIN_SUBIDA
        or not (ev.venta > 0 and ev.gasto > 0)
    ):
        return None
    with localcontext(prec=PRECISION):
        acos = Decimal(ev.pedidos) * ev.gasto / ev.venta
        if (
            gamma_q(
                Decimal(ev.pedidos + 1),
                acos / (MULT_SUBIDA * meta.exigente_pct / _CIEN),
            )
            < caso.economia.confianza_subida
        ):
            return None
        if not _tiene_ventaja_relativa(ev, caso.ubicaciones):
            return None
        a_pct = min(ev.ajuste_vigente_pct + PASO_SUBIDA_PTS, techo)
        holgura = (meta.exigente_pct / _CIEN) * ev.venta - ev.gasto
    return _Candidato(
        ubicacion=ev.ubicacion,
        de_pct=ev.ajuste_vigente_pct,
        a_pct=a_pct,
        motivo=MOTIVO_SUBIDA,
        margen=holgura,
        orden=_ORDEN_SUBIDA[ev.ubicacion],
    )


def _tiene_ventaja_relativa(
    ev: EvidenciaUbicacion, ubicaciones: tuple[EvidenciaUbicacion, ...]
) -> bool:
    """La ubicacion debe rendir mejor que las otras dos juntas de su campana."""
    if ev.clics is None or ev.venta is None or ev.gasto is None:
        return False
    resto = [o for o in ubicaciones if o.ubicacion != ev.ubicacion]
    if any(o.clics is None for o in resto):
        return False
    clics_resto = sum(o.clics for o in resto)
    if clics_resto < CLICS_MIN_RESTO:
        return False
    if any(o.venta is None or o.gasto is None for o in resto):
        return False
    venta_resto = sum((o.venta for o in resto), Decimal(0))
    gasto_resto = sum((o.gasto for o in resto), Decimal(0))
    if ev.venta * clics_resto < RAZON_RPC_MIN_SUBIDA * venta_resto * ev.clics:
        return False
    return venta_resto <= 0 or ev.gasto * venta_resto < gasto_resto * ev.venta


# ---------------------------------------------------------------------------
# Serializacion de replay
# ---------------------------------------------------------------------------


def _dec_a_texto(valor: Decimal | None) -> str | None:
    return None if valor is None else str(valor)


def _dec_desde_texto(valor) -> Decimal | None:
    if valor is None:
        return None
    return Decimal(str(valor))


def _evidencia_a_json(ev: EvidenciaUbicacion) -> dict:
    return {
        "ubicacion": ev.ubicacion,
        "ajuste_vigente_pct": ev.ajuste_vigente_pct,
        "clics": ev.clics,
        "gasto": _dec_a_texto(ev.gasto),
        "pedidos": ev.pedidos,
        "venta": _dec_a_texto(ev.venta),
        "pedidos_inmaduros": ev.pedidos_inmaduros,
    }


def _evidencia_desde_json(doc: dict) -> EvidenciaUbicacion:
    return EvidenciaUbicacion(
        ubicacion=doc["ubicacion"],
        ajuste_vigente_pct=doc["ajuste_vigente_pct"],
        clics=doc["clics"],
        gasto=_dec_desde_texto(doc["gasto"]),
        pedidos=doc["pedidos"],
        venta=_dec_desde_texto(doc["venta"]),
        pedidos_inmaduros=doc["pedidos_inmaduros"],
    )


def _meta_a_json(meta: MetaCampana) -> dict:
    return {
        "exigente_pct": str(meta.exigente_pct),
        "laxa_pct": str(meta.laxa_pct),
        "procedencias": list(meta.procedencias),
    }


def _meta_desde_json(doc: dict) -> MetaCampana:
    return MetaCampana(
        exigente_pct=Decimal(str(doc["exigente_pct"])),
        laxa_pct=Decimal(str(doc["laxa_pct"])),
        procedencias=tuple(doc["procedencias"]),
    )


def _economia_a_json(eco: EconomiaPlataforma) -> dict:
    return {
        "moneda": eco.moneda,
        "equilibrio_acos_pct": _dec_a_texto(eco.equilibrio_acos_pct),
        "gasto_para_concluir": str(eco.gasto_para_concluir),
        "confianza_recorte": str(eco.confianza_recorte),
        "confianza_subida": str(eco.confianza_subida),
    }


def _economia_desde_json(doc: dict) -> EconomiaPlataforma:
    return EconomiaPlataforma(
        moneda=doc["moneda"],
        equilibrio_acos_pct=_dec_desde_texto(doc["equilibrio_acos_pct"]),
        gasto_para_concluir=Decimal(str(doc["gasto_para_concluir"])),
        confianza_recorte=Decimal(str(doc["confianza_recorte"])),
        confianza_subida=Decimal(str(doc["confianza_subida"])),
    )


def caso_a_json(caso: CasoCampana) -> dict:
    """Congela el caso completo en tipos JSON deterministas (Decimales como texto)."""
    return {
        "campana_id": caso.campana_id,
        "plataforma": caso.plataforma,
        "config_id": caso.config_id,
        "estrategia": caso.estrategia,
        "ventana_desde": caso.ventana_desde.isoformat(),
        "ventana_hasta": caso.ventana_hasta.isoformat(),
        "ubicaciones": [_evidencia_a_json(ev) for ev in caso.ubicaciones],
        "meta": None if caso.meta is None else _meta_a_json(caso.meta),
        "economia": _economia_a_json(caso.economia),
        "gasto_campana": _dec_a_texto(caso.gasto_campana),
        "gasto_placements_total": _dec_a_texto(caso.gasto_placements_total),
        "placements_frescos": caso.placements_frescos,
        "dias_desde_ultimo_cambio": caso.dias_desde_ultimo_cambio,
        "ajuste_en_vuelo": caso.ajuste_en_vuelo,
        "elegible": caso.elegible,
        "en_impulso": caso.en_impulso,
        "solo_bajadas": caso.solo_bajadas,
        "dias_topados_7d": caso.dias_topados_7d,
        "topada": {"pct": str(caso.topada.pct), "dias": caso.topada.dias},
        "fase_temporada": caso.fase_temporada,
    }


def caso_desde_json(doc: dict) -> CasoCampana:
    """Reconstruye el `CasoCampana` congelado. Los Decimales vuelven exactos."""
    return CasoCampana(
        campana_id=doc["campana_id"],
        plataforma=doc["plataforma"],
        config_id=doc["config_id"],
        estrategia=doc["estrategia"],
        ventana_desde=dt.date.fromisoformat(doc["ventana_desde"]),
        ventana_hasta=dt.date.fromisoformat(doc["ventana_hasta"]),
        ubicaciones=tuple(_evidencia_desde_json(x) for x in doc["ubicaciones"]),
        meta=None if doc["meta"] is None else _meta_desde_json(doc["meta"]),
        economia=_economia_desde_json(doc["economia"]),
        gasto_campana=_dec_desde_texto(doc["gasto_campana"]),
        gasto_placements_total=_dec_desde_texto(doc["gasto_placements_total"]),
        placements_frescos=doc["placements_frescos"],
        dias_desde_ultimo_cambio=doc["dias_desde_ultimo_cambio"],
        ajuste_en_vuelo=doc["ajuste_en_vuelo"],
        elegible=doc["elegible"],
        en_impulso=doc["en_impulso"],
        solo_bajadas=doc["solo_bajadas"],
        dias_topados_7d=doc["dias_topados_7d"],
        topada=CampanaTopada(pct=Decimal(str(doc["topada"]["pct"])), dias=doc["topada"]["dias"]),
        fase_temporada=doc["fase_temporada"],
    )
