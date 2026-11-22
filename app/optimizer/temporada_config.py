"""Configuracion, validacion, lectores de settings y bloques circulares de Temporada.

Extraido de `app/optimizer/temporada.py` (Ola D2). PURO: sin IO, sin reloj, Decimal.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

VERSION_TEMPORADA = "temporada_v1"
CLAVE_TEMPORADA = "ads_temporada"  # global, como precio_modo_global
CLAVE_CAMPANA_TOPADA = "ads_campana_topada"  # global; tambien la leen avisos y dinero

# Prefill del formulario (la tarjeta y la ficha); nunca decide sin guardarse.
FORMULARIO_POR_DEFECTO = {
    "reduccion_baja_pct": "30",
    "dias_preparacion": 21,
    "mult_cortes_baja": "2",
    "aumento_presupuesto_alta_pct": "25",
    "campana_topada_pct": "90",
    "campana_topada_dias": 5,
    "meta_alta_pct": "25",
    "memoria_dias": 365,
}

TipoMes = Literal["baja", "normal", "alta"]
Fase = Literal["baja", "normal", "alta"]  # la preparacion es "normal"
EventoTipo = Literal["entra_baja", "prepara_alta", "restaura", "entra_alta", "sale_alta"]

REDUCCION_MIN, REDUCCION_MAX = Decimal("0.05"), Decimal("0.30")
DIAS_PREPARACION_MIN, DIAS_PREPARACION_MAX = 7, 42
BAJA_EFECTIVA_MIN_DIAS = 7
MULT_CORTES_MIN, MULT_CORTES_MAX = Decimal("1"), Decimal("3")
AUMENTO_PRESUPUESTO_MIN, AUMENTO_PRESUPUESTO_MAX = Decimal("0"), Decimal("1")
META_ALTA_MIN, META_ALTA_MAX = Decimal("10"), Decimal("45")
MANTENER_PAUSADAS_MAX = 500
TOPADA_PCT_MIN, TOPADA_PCT_MAX = Decimal("50"), Decimal("100")
TOPADA_DIAS_MIN, TOPADA_DIAS_MAX = 1, 7
MEMORIA_DIAS_MIN, MEMORIA_DIAS_MAX = 90, 730

PLATAFORMAS_TEMPORADA = ("amazon_mx", "amazon_us")
ANIO_REFERENCIA = 2027

MESES_POR_DEFECTO: tuple[TipoMes, ...] = (
    "baja",
    "baja",
    "alta",
    "alta",
    "baja",
    "baja",
    "normal",
    "normal",
    "normal",
    "alta",
    "alta",
    "normal",
)

MESES_ANIO = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

MSG_MESES = "Marca cada uno de los 12 meses como baja, normal o alta."
MSG_TODOS_BAJA = (
    "Si todos los meses son baja, Orbit nunca regresaría los bids."
    " Marca al menos un mes como normal o alta."
)
MSG_REDUC = (
    "La reducción puede ser de 5 % a 30 %: es lo más que Orbit mueve un bid de una sola vez."
)
MSG_PREPARACION = "Los días de preparación van de 7 a 42."
MSG_MULT = (
    "El multiplicador de la temporada baja va de 1 a 3."
    " Con 1, Orbit pausa igual que en un mes normal."
)
MSG_AUMENTO = "El aumento sugerido de presupuesto va de 0 % a 100 %."
MSG_CAMPANA_TOPADA = "El porcentaje va de 50 a 100 y los días de 1 a 7."
MSG_META_ALTA = "La meta de ACoS de temporada alta va de 10 % a 45 %."
MSG_FIN_BAJA = "Solo puedes terminar la baja mientras estás en temporada baja."
MSG_ENCIENDE_SIN_MESES = "Para encender la temporada primero guarda los meses."
MSG_NUMERO = "Escribe solo el número, por ejemplo 30."
MSG_BOOL = "Escribe un número, no sí o no."

MODOS_MOTOR = ("shadow", "live")


@dataclass(frozen=True)
class ConfigTemporada:
    """Lo que fijo David. Solo `valida` la construye: si existe, es valida."""

    version: str
    meses: tuple[TipoMes, ...]
    reduccion_baja: Decimal
    dias_preparacion: int
    mult_cortes_baja: Decimal
    aumento_presupuesto_alta: Decimal
    fin_anticipado: dt.date | None
    mantener_pausadas: frozenset[int]


@dataclass(frozen=True)
class CampanaTopada:
    """Una sola definicion para aviso, propuesta y freno de subidas."""

    pct: Decimal
    dias: int


@dataclass(frozen=True)
class Estacion:
    """Lo que el calendario dice del dia D para TODA la plataforma."""

    version: str
    dia: dt.date
    fase: Fase
    previa_alta: bool
    reduccion_baja: Decimal
    mult_cortes_baja: Decimal
    inicio_bloque: dt.date


@dataclass(frozen=True)
class Evento:
    fecha: dt.date
    tipo: EventoTipo
    frase: str


@dataclass(frozen=True)
class ResumenTemporada:
    hoy: dt.date
    fase_hoy: Fase
    frase_hoy: str
    proximos: tuple[Evento, ...]
    aviso_cambio: str | None

    def como_dict(self) -> dict:
        return {
            "hoy": self.hoy.isoformat(),
            "fase_hoy": self.fase_hoy,
            "frase_hoy": self.frase_hoy,
            "proximos": [
                {"fecha": e.fecha.isoformat(), "tipo": e.tipo, "frase": e.frase}
                for e in self.proximos
            ],
            "aviso_cambio": self.aviso_cambio,
        }


@dataclass(frozen=True)
class EpisodioVigente:
    """El episodio abierto de la plataforma (lo lee temporada_io.lee_estado)."""

    id: int
    abierto_el: dt.datetime


@dataclass(frozen=True)
class FinReduccion:
    """La reduccion vieja de una hoja termina en este ciclo, sin enviar nada."""

    episodio_id: int
    motivo: Literal["ya_arriba", "mano_del_dueno"]
    excluye_abierto: bool
    bid_visto: Decimal | None = None


@dataclass(frozen=True)
class FronteraHoja:
    """Lo que la temporada dice de UNA keyword para cortes y juicio."""

    fase_hoja: Fase
    mult_cortes: Decimal
    cerca_hoja: dt.date | None
    cerca_plataforma: dt.date | None
    episodio_id: int | None
    fin: FinReduccion | None = None
    exclusion_hoy: Decimal | None = None


@dataclass(frozen=True)
class EpisodioPresupuesto:
    """Hechos de un episodio para propuestas de presupuesto (bloque G)."""

    id: int
    abierto_el: dt.date
    cerrado_el: dt.date | None


@dataclass(frozen=True)
class _Bloque:
    """Bloque circular instanciado en fechas reales de un ano."""

    tipo: TipoMes
    inicio: dt.date
    fin: dt.date
    restaura: dt.date | None = None


def _numero(crudo) -> Decimal:
    """Decimal desde un valor JSON crudo. bool -> MSG_BOOL; texto con coma,
    NaN, infinito o lo demas -> MSG_NUMERO."""
    if isinstance(crudo, bool):
        raise ValueError(MSG_BOOL)
    if isinstance(crudo, str):
        texto = crudo.strip()
        if not texto or "," in texto:
            raise ValueError(MSG_NUMERO)
        try:
            valor = Decimal(texto)
        except InvalidOperation:
            raise ValueError(MSG_NUMERO) from None
    elif isinstance(crudo, (int, float)):
        valor = Decimal(str(crudo))
    else:
        raise ValueError(MSG_NUMERO)
    if not valor.is_finite():
        raise ValueError(MSG_NUMERO)
    return valor


def _entero(crudo) -> int:
    valor = _numero(crudo)
    if valor != valor.to_integral_value():
        raise ValueError(MSG_NUMERO)
    return int(valor)


def _fecha(crudo) -> dt.date | None:
    if crudo is None:
        return None
    if isinstance(crudo, str):
        try:
            return dt.date.fromisoformat(crudo.strip())
        except ValueError:
            pass
    raise ValueError(f"fin_anticipado debe ser una fecha AAAA-MM-DD o null, llego {crudo!r}")


def _identificadores(crudo) -> frozenset[int]:
    if crudo is None:
        return frozenset()
    if not isinstance(crudo, list):
        raise ValueError("mantener_pausadas debe ser una lista de ids de keywords")
    if len(crudo) > MANTENER_PAUSADAS_MAX:
        raise ValueError(f"mantener_pausadas admite hasta {MANTENER_PAUSADAS_MAX} keywords")
    ids = []
    for item in crudo:
        n = _entero(item)
        if n < 1:
            raise ValueError("mantener_pausadas debe ser una lista de ids de keywords")
        ids.append(n)
    return frozenset(ids)


def _meses(crudo) -> tuple[TipoMes, ...]:
    if not isinstance(crudo, list) or len(crudo) != 12:
        raise ValueError(MSG_MESES)
    if any(m not in ("baja", "normal", "alta") for m in crudo):
        raise ValueError(MSG_MESES)
    if all(m == "baja" for m in crudo):
        raise ValueError(MSG_TODOS_BAJA)
    return tuple(crudo)


def _texto(dec: Decimal) -> str:
    """Decimal -> string sin notacion cientifica, la forma de las configs."""
    return format(dec.normalize(), "f")


def _rachas(meses: tuple[TipoMes, ...]) -> list[tuple[TipoMes, int, int]]:
    """(tipo, mes inicial 0-11, largo) de cada racha circular de 12 meses."""
    if all(m == meses[0] for m in meses):
        return [(meses[0], 0, 12)]
    cortes = [i for i in range(12) if meses[i] != meses[i - 1]]
    rachas = []
    for k, inicio in enumerate(cortes):
        fin = cortes[(k + 1) % len(cortes)]
        largo = (fin - inicio) % 12
        rachas.append((meses[inicio], inicio, largo))
    return rachas


def _fecha_mes(anio: int, mes: int) -> dt.date:
    """Dia 1 del mes `mes` (1-12), rodando al ano siguiente si hace falta."""
    return dt.date(anio + (mes - 1) // 12, (mes - 1) % 12 + 1, 1)


def _instancias(
    meses: tuple[TipoMes, ...],
    dias_preparacion: int,
    fin_anticipado: dt.date | None,
    anio: int,
) -> list[_Bloque]:
    """Bloques del ano `anio` en fechas reales con `restaura` para bloques de baja."""
    rachas = _rachas(meses)
    altas_vecinas = [
        _fecha_mes(vecino, m0 + 1)
        for vecino in (anio - 1, anio, anio + 1)
        for tipo, m0, _largo in rachas
        if tipo == "alta"
    ]
    bloques: list[_Bloque] = []
    for tipo, m0, largo in rachas:
        inicio = _fecha_mes(anio, m0 + 1)
        fin_natural = _fecha_mes(anio, m0 + 1 + largo)
        restaura: dt.date | None = None
        if tipo == "baja":
            r = fin_natural
            siguientes = [a for a in altas_vecinas if a > inicio]
            if siguientes:
                r = min(r, min(siguientes) - dt.timedelta(days=dias_preparacion))
            if fin_anticipado is not None and inicio <= fin_anticipado < r:
                r = fin_anticipado
            restaura = r
        bloques.append(_Bloque(tipo=tipo, inicio=inicio, fin=fin_natural, restaura=restaura))
    return bloques


def _nombre_bloque(m0: int, largo: int) -> str:
    if largo == 1:
        return f"de {MESES_ANIO[m0]}"
    return f"de {MESES_ANIO[m0]} a {MESES_ANIO[(m0 + largo - 1) % 12]}"


def _chequea_bloques_de_baja(meses: tuple[TipoMes, ...], prep: int) -> None:
    bloques = _instancias(meses, prep, None, ANIO_REFERENCIA)
    for tipo, m0, largo in _rachas(meses):
        if tipo != "baja":
            continue
        propio = next(b for b in bloques if b.inicio == _fecha_mes(ANIO_REFERENCIA, m0 + 1))
        assert propio.restaura is not None
        dias = max(0, (propio.restaura - propio.inicio).days)
        if dias < BAJA_EFECTIVA_MIN_DIAS:
            duracion = (propio.fin - propio.inicio).days
            raise ValueError(
                f"La temporada baja {_nombre_bloque(m0, largo)} dura {duracion} días y una"
                f" preparación de {prep} la deja en {dias}. Necesitas al menos"
                f" {BAJA_EFECTIVA_MIN_DIAS} días de baja: acorta la preparación o marca otro"
                " mes como baja."
            )


def _fin_dentro_de_baja(meses: tuple[TipoMes, ...], prep: int, fin: dt.date) -> bool:
    for anio in (fin.year - 1, fin.year):
        for bloque in _instancias(meses, prep, None, anio):
            if (
                bloque.tipo == "baja"
                and bloque.restaura is not None
                and bloque.inicio <= fin < bloque.restaura
            ):
                return True
    return False


def valida(crudo: Mapping) -> ConfigTemporada:
    """Construye la config desde el JSON crudo o revienta con el mensaje exacto."""
    if not isinstance(crudo, Mapping):
        raise ValueError(MSG_MESES)
    meses = _meses(crudo.get("meses"))
    reduccion_pct = _numero(crudo.get("reduccion_baja_pct"))
    if not REDUCCION_MIN * 100 <= reduccion_pct <= REDUCCION_MAX * 100:
        raise ValueError(MSG_REDUC)
    dias_preparacion = _entero(crudo.get("dias_preparacion"))
    if not DIAS_PREPARACION_MIN <= dias_preparacion <= DIAS_PREPARACION_MAX:
        raise ValueError(MSG_PREPARACION)
    mult = _numero(crudo.get("mult_cortes_baja"))
    if not MULT_CORTES_MIN <= mult <= MULT_CORTES_MAX:
        raise ValueError(MSG_MULT)
    aumento_pct = _numero(crudo.get("aumento_presupuesto_alta_pct"))
    if not AUMENTO_PRESUPUESTO_MIN * 100 <= aumento_pct <= AUMENTO_PRESUPUESTO_MAX * 100:
        raise ValueError(MSG_AUMENTO)
    fin = _fecha(crudo.get("fin_anticipado"))
    pausadas = _identificadores(crudo.get("mantener_pausadas"))

    _chequea_bloques_de_baja(meses, dias_preparacion)
    if fin is not None and not _fin_dentro_de_baja(meses, dias_preparacion, fin):
        fin = None

    return ConfigTemporada(
        version=VERSION_TEMPORADA,
        meses=meses,
        reduccion_baja=reduccion_pct / 100,
        dias_preparacion=dias_preparacion,
        mult_cortes_baja=mult,
        aumento_presupuesto_alta=aumento_pct / 100,
        fin_anticipado=fin,
        mantener_pausadas=pausadas,
    )


def valida_campana_topada(crudo: Mapping) -> CampanaTopada:
    if not isinstance(crudo, Mapping):
        raise ValueError(MSG_CAMPANA_TOPADA)
    pct = _numero(crudo.get("pct"))
    if not TOPADA_PCT_MIN <= pct <= TOPADA_PCT_MAX:
        raise ValueError(MSG_CAMPANA_TOPADA)
    dias = _entero(crudo.get("dias"))
    if not TOPADA_DIAS_MIN <= dias <= TOPADA_DIAS_MAX:
        raise ValueError(MSG_CAMPANA_TOPADA)
    return CampanaTopada(pct=pct / 100, dias=dias)


def valida_meta_alta(crudo) -> Decimal:
    valor = _numero(crudo)
    if not META_ALTA_MIN <= valor <= META_ALTA_MAX:
        raise ValueError(MSG_META_ALTA)
    return valor


def desde_settings(settings: Mapping) -> ConfigTemporada | None:
    crudo = settings.get(CLAVE_TEMPORADA)
    if crudo is None:
        return None
    return valida(crudo)


def modo_desde_settings(settings: Mapping, plataforma: str) -> Literal["shadow", "live"] | None:
    clave = f"ads_temporada_{plataforma}"
    valor = settings.get(clave)
    if valor is None:
        return None
    if valor not in MODOS_MOTOR:
        raise ValueError(f"setting {clave}: debe ser shadow o live, llego {valor!r}")
    if settings.get(CLAVE_TEMPORADA) is None:
        raise ValueError(MSG_ENCIENDE_SIN_MESES)
    return valor


def meta_alta_desde_settings(settings: Mapping, plataforma: str) -> Decimal:
    valor = settings.get(f"ads_temporada_meta_alta_{plataforma}")
    if valor is None:
        return Decimal("25")
    return valida_meta_alta(valor)


def campana_topada_desde_settings(settings: Mapping) -> CampanaTopada:
    crudo = settings.get(CLAVE_CAMPANA_TOPADA)
    if crudo is None:
        return CampanaTopada(Decimal("0.90"), 5)
    return valida_campana_topada(crudo)


def modo_recuperacion_desde_settings(
    settings: Mapping, plataforma: str
) -> Literal["shadow", "live"] | None:
    valor = settings.get(f"ads_bid_recuperacion_{plataforma}")
    if valor is None:
        return None
    if valor not in MODOS_MOTOR:
        raise ValueError(
            f"setting ads_bid_recuperacion_{plataforma}: debe ser shadow o live, llego {valor!r}"
        )
    return valor


def memoria_recuperacion_desde_settings(settings: Mapping, plataforma: str) -> int:
    clave = f"ads_bid_recuperacion_memoria_dias_{plataforma}"
    valor = settings.get(clave)
    if valor is None:
        return 365
    dias = _entero(valor)
    if not MEMORIA_DIAS_MIN <= dias <= MEMORIA_DIAS_MAX:
        raise ValueError(
            f"setting {clave}: los dias de memoria van de {MEMORIA_DIAS_MIN} a {MEMORIA_DIAS_MAX}"
        )
    return dias


def como_guardada(config: ConfigTemporada) -> dict:
    return {
        "version": VERSION_TEMPORADA,
        "meses": list(config.meses),
        "reduccion_baja_pct": _texto(config.reduccion_baja * 100),
        "dias_preparacion": config.dias_preparacion,
        "mult_cortes_baja": _texto(config.mult_cortes_baja),
        "aumento_presupuesto_alta_pct": _texto(config.aumento_presupuesto_alta * 100),
        "fin_anticipado": (
            None if config.fin_anticipado is None else config.fin_anticipado.isoformat()
        ),
        "mantener_pausadas": sorted(config.mantener_pausadas),
    }


def campana_topada_guardada(topada: CampanaTopada) -> dict:
    return {"pct": _texto(topada.pct * 100), "dias": topada.dias}
