"""Temporadas de venta (TEMPORADA 01, bloque A). PURO: sin IO, sin reloj,
Decimal.

Lo que fijo David — el calendario de 12 meses y sus parametros — vive aqui
junto a la unica resolucion de «que dia es que fase» (`resuelve`, sobre
fechas reales, con bloques circulares que cruzan el ano). Los lectores
(`desde_settings` y compania) son la UNICA puerta entre el JSONB de
config_version y este modulo: el ciclo, el guardado, la pantalla y la vista
previa leen todos por aqui.

Los valores por defecto (MESES_POR_DEFECTO y los rangos) SOLO prellenan el
formulario: nada decide hasta que David guarda (regla 3: sin config no hay
fase). Los mensajes de `valida` son los que ve David en el 422; los escribe
este modulo y el endpoint los pega tal cual despues de «Error: ».

Bloque A: config, valida, lectores, resuelve, eventos y resumen. Lo que
decide bids (pendiente, frontera, destino_entrada) llega con los bloques
B y D y no vive aqui todavia.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal

from app.optimizer import recuperacion
from app.optimizer.centavos import CENTAVO, centavo_hacia

if TYPE_CHECKING:
    from app.optimizer.caso import CasoHoja
    from app.optimizer.recuperacion import CadenaBid, ReduccionVigente

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

REDUCCION_MIN, REDUCCION_MAX = Decimal("0.05"), Decimal("0.30")  # MAX = -politica.CLAMP_FACTOR_MIN
DIAS_PREPARACION_MIN, DIAS_PREPARACION_MAX = 7, 42
BAJA_EFECTIVA_MIN_DIAS = 7
MULT_CORTES_MIN, MULT_CORTES_MAX = Decimal("1"), Decimal("3")
AUMENTO_PRESUPUESTO_MIN, AUMENTO_PRESUPUESTO_MAX = Decimal("0"), Decimal("1")
META_ALTA_MIN, META_ALTA_MAX = Decimal("10"), Decimal("45")
MANTENER_PAUSADAS_MAX = 500
TOPADA_PCT_MIN, TOPADA_PCT_MAX = Decimal("50"), Decimal("100")
TOPADA_DIAS_MIN, TOPADA_DIAS_MAX = 1, 7
MEMORIA_DIAS_MIN, MEMORIA_DIAS_MAX = 90, 730

# Plataformas con tarjeta de temporada (el par encendido siempre junto, decision 9).
PLATAFORMAS_TEMPORADA = ("amazon_mx", "amazon_us")

# Ano de referencia NO bisiesto para el chequeo de dias efectivos de baja:
# con febrero de 28 la validacion no cambia entre anos (diseno, «valida»).
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
)  # solo prellena el formulario; nunca decide sin guardarse

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

# Mensajes EXACTOS de la tabla «Mensajes de validacion» del diseno; el 422
# los pega tal cual (settings.js: «Error: » + detail).
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
    meses: tuple[TipoMes, ...]  # 12; indice = mes - 1
    reduccion_baja: Decimal  # fraccion en [0.05, 0.30]
    dias_preparacion: int
    mult_cortes_baja: Decimal  # en [1, 3]
    aumento_presupuesto_alta: Decimal  # fraccion en [0, 1]
    fin_anticipado: dt.date | None  # «Terminar la baja hoy»; solo vale dentro de su bloque de baja
    mantener_pausadas: frozenset[int]  # ad_entity_id que David no quiere reactivar


@dataclass(frozen=True)
class CampanaTopada:
    """Una sola definicion para aviso, propuesta y freno de subidas."""

    pct: Decimal  # 0.90
    dias: int  # de los ultimos 7 con ingesta


@dataclass(frozen=True)
class Estacion:
    """Lo que el calendario dice del dia D para TODA la plataforma."""

    version: str
    dia: dt.date
    fase: Fase
    previa_alta: bool  # dentro de los dias de preparacion de un bloque alto
    reduccion_baja: Decimal  # parametros vigentes ese dia (el replay no relee settings)
    mult_cortes_baja: Decimal
    inicio_bloque: dt.date  # primer dia del bloque de fase vigente


EventoTipo = Literal["entra_baja", "prepara_alta", "restaura", "entra_alta", "sale_alta"]


@dataclass(frozen=True)
class Evento:
    fecha: dt.date
    tipo: EventoTipo
    frase: str  # espanol llano, sin jerga


@dataclass(frozen=True)
class ResumenTemporada:
    hoy: dt.date
    fase_hoy: Fase
    frase_hoy: str
    proximos: tuple[Evento, ...]  # proximos 12 meses, en orden
    aviso_cambio: str | None  # solo si la edicion cambia la fase de hoy

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


# ---------------------------------------------------------------------------
# Tipos crudos: la frontera ANTES de cualquier rango. Pydantic no convierte
# (CuerpoSettings usa Any), asi que aqui llega el JSON tal cual.
# ---------------------------------------------------------------------------


def _numero(crudo) -> Decimal:
    """Decimal desde un valor JSON crudo. bool -> MSG_BOOL; texto con coma,
    NaN, infinito o lo demas -> MSG_NUMERO (mensajes exactos de la tabla)."""
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
    """Entero crudo (dias): bool y no-enteros -> los mensajes de numero."""
    valor = _numero(crudo)
    if valor != valor.to_integral_value():
        raise ValueError(MSG_NUMERO)
    return int(valor)


def _fecha(crudo) -> dt.date | None:
    """«AAAA-MM-DD» o null (fin_anticipado). Lo demas es config corrupta."""
    if crudo is None:
        return None
    if isinstance(crudo, str):
        try:
            return dt.date.fromisoformat(crudo.strip())
        except ValueError:
            pass
    raise ValueError(f"fin_anticipado debe ser una fecha AAAA-MM-DD o null, llego {crudo!r}")


def _identificadores(crudo) -> frozenset[int]:
    """mantener_pausadas: lista de enteros >= 1, <= MANTENER_PAUSADAS_MAX."""
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


# ---------------------------------------------------------------------------
# valida: la frontera del guardado y de la lectura. ValueError con el
# mensaje de la tabla; el endpoint responde 422 con detail = mensaje.
# ---------------------------------------------------------------------------


def valida(crudo: Mapping) -> ConfigTemporada:
    """Construye la config desde el JSON crudo o revienta con el mensaje
    exacto de la tabla «Mensajes de validacion».

    # 1. tipos crudos: bool en campo numerico -> «Escribe un numero, no si
    #    o no.»; string decimal o numero JSON finito; dias_* enteros;
    #    fin_anticipado «AAAA-MM-DD» o null; mantener_pausadas enteros.
    # 2. meses: 12 en {baja, normal, alta}; no todos baja.
    # 3. rangos de la tabla.
    # 4. por cada bloque de baja: dias(inicio, R) >= BAJA_EFECTIVA_MIN_DIAS,
    #    con febrero de 28 (el peor ano; la validacion no cambia entre anos).
    #    Un fin_anticipado que quedo fuera del calendario editado se DESCARTA
    #    (hallazgo de revision: ya no gobierna nada y no debe bloquear el
    #    guardado; el mensaje «Solo puedes terminar la baja...» vive en el
    #    endpoint del boton, que escribe la fecha de hoy tras comprobar la
    #    fase)."""
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
    """Criterio de campana topada (pct 50..100 como porcentaje, dias 1..7)."""
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
    """Meta de alta de UNA plataforma, en puntos de ACoS (10 a 45)."""
    valor = _numero(crudo)
    if not META_ALTA_MIN <= valor <= META_ALTA_MAX:
        raise ValueError(MSG_META_ALTA)
    return valor


# ---------------------------------------------------------------------------
# Bloques circulares: rachas de meses del mismo tipo, instanciadas en fechas
# reales del ano pedido (una racha dic-feb es un solo bloque).
# ---------------------------------------------------------------------------


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


def _instancias(config_like, anio: int) -> list[dict]:
    """Bloques del ano `anio` en fechas reales: alta con su A y su prep;
    baja con su B, su fin natural F y su R = min(F, prep del siguiente
    bloque alto), acortado por fin_anticipado si cae dentro.

    El «siguiente bloque alto» se busca entre los anos vecinos: una racha
    dic-feb cerrada por la preparacion de marzo del ano siguiente debe ver
    esa preparacion (el bloque alto vive en otra instancia anual).

    `config_like` es ConfigTemporada o la pareja (meses, dias_preparacion)
    que usa `valida` para el ano de referencia."""
    if isinstance(config_like, ConfigTemporada):
        meses, prep, fin = (
            config_like.meses,
            config_like.dias_preparacion,
            config_like.fin_anticipado,
        )
    else:
        meses, prep, fin = config_like[0], config_like[1], None
    bloques = []
    for tipo, m0, largo in _rachas(meses):
        bloques.append(
            {"tipo": tipo, "b": _fecha_mes(anio, m0 + 1), "f": _fecha_mes(anio, m0 + 1 + largo)}
        )
    # El limite de cada baja lo pone la preparacion del SIGUIENTE bloque alto
    # elegido por su fecha de inicio, aunque esa preparacion EMPIECE antes que
    # la baja (feb baja + mar alta con 42 dias: la preparacion arranca el 18 de
    # enero y se come febrero entero; hallazgo de revision). Los vecinos
    # anio-1..anio+1 cubren los bloques que cruzan de un ano al otro.
    altas_vecinas = [
        _fecha_mes(vecino, m0 + 1)
        for vecino in (anio - 1, anio, anio + 1)
        for tipo, m0, _largo in _rachas(meses)
        if tipo == "alta"
    ]
    for b in bloques:
        if b["tipo"] == "baja":
            r = b["f"]
            siguientes = [a for a in altas_vecinas if a > b["b"]]
            if siguientes:
                r = min(r, min(siguientes) - dt.timedelta(days=prep))
            if fin is not None and b["b"] <= fin < r:
                r = fin
            b["r"] = r
    return bloques


def _nombre_bloque(m0: int, largo: int) -> str:
    """«de febrero» o «de diciembre a febrero», para el mensaje de valida."""
    if largo == 1:
        return f"de {MESES_ANIO[m0]}"
    return f"de {MESES_ANIO[m0]} a {MESES_ANIO[(m0 + largo - 1) % 12]}"


def _chequea_bloques_de_baja(meses: tuple[TipoMes, ...], prep: int) -> None:
    """Regla 4 de valida: cada bloque de baja deja >= 7 dias efectivos,
    con febrero de 28 (ano de referencia fijo). Una preparacion que empieza
    antes del bloque la deja en 0 y tambien se rechaza (hallazgo de
    revision: la baja que no llega a existir no se anuncia)."""
    bloques = _instancias((meses, prep), ANIO_REFERENCIA)
    for tipo, m0, largo in _rachas(meses):
        if tipo != "baja":
            continue
        propio = next(b for b in bloques if b["b"] == _fecha_mes(ANIO_REFERENCIA, m0 + 1))
        dias = max(0, (propio["r"] - propio["b"]).days)
        if dias < BAJA_EFECTIVA_MIN_DIAS:
            duracion = (propio["f"] - propio["b"]).days
            raise ValueError(
                f"La temporada baja {_nombre_bloque(m0, largo)} dura {duracion} días y una"
                f" preparación de {prep} la deja en {dias}. Necesitas al menos"
                f" {BAJA_EFECTIVA_MIN_DIAS} días de baja: acorta la preparación o marca otro"
                " mes como baja."
            )


def _fin_dentro_de_baja(meses: tuple[TipoMes, ...], prep: int, fin: dt.date) -> bool:
    """«Terminar la baja hoy» solo vale mientras el dia cae dentro de un
    bloque de baja, sobre fechas reales; los bloques que abrieron el año
    anterior (dic-feb) viven en la instancia del año previo (hallazgo de
    revision). Los limites son naturales, sin fin_anticipado aplicado."""
    for anio in (fin.year - 1, fin.year):
        for bloque in _instancias((meses, prep), anio):
            if bloque["tipo"] == "baja" and bloque["b"] <= fin < bloque["r"]:
                return True
    return False


# ---------------------------------------------------------------------------
# Lectores: la unica puerta entre settings y este modulo.
# ---------------------------------------------------------------------------


def desde_settings(settings: Mapping) -> ConfigTemporada | None:
    """ConfigTemporada de `ads_temporada`; ausente -> None; corrupta ->
    ValueError (el ciclo falla cerrado como con los demas lectores)."""
    crudo = settings.get(CLAVE_TEMPORADA)
    if crudo is None:
        return None
    return valida(crudo)


def modo_desde_settings(settings: Mapping, plataforma: str) -> Literal["shadow", "live"] | None:
    """ads_temporada_<plat>: ausente -> None; shadow|live; otro -> ValueError
    (config corrupta). Presente sin meses guardados -> el mensaje de la
    tabla: encender sin calendario no puede pasar."""
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
    """ads_temporada_meta_alta_<plat>; ausente -> 25 (el prellenado del
    formulario; sin config guardada no hay decision que lo use)."""
    valor = settings.get(f"ads_temporada_meta_alta_{plataforma}")
    if valor is None:
        return Decimal("25")
    return valida_meta_alta(valor)


def campana_topada_desde_settings(settings: Mapping) -> CampanaTopada:
    """ads_campana_topada; ausente -> la definicion de hoy (90 %, 5 de 7).
    La leen el aviso de Telegram, «Donde poner el dinero» y (en los bloques
    que siguen) la propuesta y el freno de subidas: una sola definicion."""
    crudo = settings.get(CLAVE_CAMPANA_TOPADA)
    if crudo is None:
        return CampanaTopada(Decimal("0.90"), 5)
    return valida_campana_topada(crudo)


def modo_recuperacion_desde_settings(
    settings: Mapping, plataforma: str
) -> Literal["shadow", "live"] | None:
    """ads_bid_recuperacion_<plat> (bloque A: la tarjeta lo guarda; el motor
    llega con el bloque C). Ausente -> None; otro valor -> ValueError."""
    valor = settings.get(f"ads_bid_recuperacion_{plataforma}")
    if valor is None:
        return None
    if valor not in MODOS_MOTOR:
        raise ValueError(
            f"setting ads_bid_recuperacion_{plataforma}: debe ser shadow o live, llego {valor!r}"
        )
    return valor


def memoria_recuperacion_desde_settings(settings: Mapping, plataforma: str) -> int:
    """ads_bid_recuperacion_memoria_dias_<plat>; ausente -> 365."""
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


# ---------------------------------------------------------------------------
# resuelve: la fase del dia sobre fechas reales. Prioridad alta >
# preparacion > baja > normal.
# ---------------------------------------------------------------------------


def resuelve(config: ConfigTemporada, dia: dt.date) -> Estacion:
    """La fase de `dia` para TODA la plataforma, con el bloque que gobierna.

    # bloques = rachas circulares de meses del mismo tipo, instanciadas en
    # fechas reales (anos y-1, y, y+1; una racha dic-feb es un solo bloque).
    # para cada bloque alto: A = dia 1 de su primer mes; prep = A - dias.
    #   (un bloque alto de varios meses se prepara una vez)
    # para cada bloque de baja: R = min(F, prep del siguiente bloque alto),
    #   acortado por fin_anticipado si cae en [B, R).
    # sin meses alta nunca hay preparacion; sin meses baja nunca hay baja."""
    for bloque in _instancias(config, dia.year - 1) + _instancias(config, dia.year):
        if bloque["tipo"] == "alta" and bloque["b"] <= dia < bloque["f"]:
            return Estacion(
                version=VERSION_TEMPORADA,
                dia=dia,
                fase="alta",
                previa_alta=False,
                reduccion_baja=config.reduccion_baja,
                mult_cortes_baja=config.mult_cortes_baja,
                inicio_bloque=bloque["b"],
            )
    bloques = [
        b for anio in (dia.year - 1, dia.year, dia.year + 1) for b in _instancias(config, anio)
    ]
    for bloque in bloques:
        if bloque["tipo"] != "alta":
            continue
        prep = bloque["b"] - dt.timedelta(days=config.dias_preparacion)
        if prep <= dia < bloque["b"]:
            return Estacion(
                version=VERSION_TEMPORADA,
                dia=dia,
                fase="normal",
                previa_alta=True,
                reduccion_baja=config.reduccion_baja,
                mult_cortes_baja=config.mult_cortes_baja,
                inicio_bloque=prep,
            )
    for bloque in bloques:
        if bloque["tipo"] == "baja" and bloque["b"] <= dia < bloque["r"]:
            return Estacion(
                version=VERSION_TEMPORADA,
                dia=dia,
                fase="baja",
                previa_alta=False,
                reduccion_baja=config.reduccion_baja,
                mult_cortes_baja=config.mult_cortes_baja,
                inicio_bloque=bloque["b"],
            )
    # Cola de una baja ya restaurada (fin_anticipado antes de la preparacion
    # real): mes de baja, fase normal, sin previa.
    for bloque in bloques:
        if bloque["tipo"] == "baja" and bloque["b"] <= dia < bloque["f"] and dia >= bloque["r"]:
            return Estacion(
                version=VERSION_TEMPORADA,
                dia=dia,
                fase="normal",
                previa_alta=False,
                reduccion_baja=config.reduccion_baja,
                mult_cortes_baja=config.mult_cortes_baja,
                inicio_bloque=bloque["r"],
            )
    return Estacion(
        version=VERSION_TEMPORADA,
        dia=dia,
        fase="normal",
        previa_alta=False,
        reduccion_baja=config.reduccion_baja,
        mult_cortes_baja=config.mult_cortes_baja,
        inicio_bloque=_inicio_racha_normal(config.meses, dia),
    )


def _inicio_racha_normal(meses: tuple[TipoMes, ...], dia: dt.date) -> dt.date:
    """Dia 1 de la racha normal que contiene el mes de `dia` (una racha que
    cruza el ano abre en el diciembre anterior)."""
    for tipo, m0, largo in _rachas(meses):
        if tipo != "normal":
            continue
        indices = {(m0 + k) % 12 for k in range(largo)}
        if (dia.month - 1) in indices:
            anio = dia.year if m0 <= dia.month - 1 else dia.year - 1
            return _fecha_mes(anio, m0 + 1)
    raise AssertionError("fase normal sin racha normal que contenga el mes")


# ---------------------------------------------------------------------------
# eventos y resumen: lo que ve David en «Así queda tu año».
# ---------------------------------------------------------------------------

FRASE_ENTRA_BAJA = "Empieza la temporada baja: Orbit baja todos los bids {pct} %."
FRASE_RESTAURA_PREPARA = (
    "Empieza la preparación: los bids regresan a como estaban antes de la baja"
    " y Orbit reactiva las keywords que pausó durante la baja."
)
FRASE_RESTAURA = "Termina la baja: los bids regresan. Sigue un mes normal."
FRASE_PREPARA_ALTA = "Empieza la previa de la temporada alta; todavía no cambia nada."
FRASE_ENTRA_ALTA = "Empieza la temporada alta."
FRASE_SALE_ALTA = "La meta de ACoS regresa a la normal."


def eventos(config: ConfigTemporada, desde: dt.date, hasta: dt.date) -> tuple[Evento, ...]:
    """Los cambios del calendario en [desde, hasta), en orden. restaura
    lleva la frase de preparacion cuando detras sigue un bloque alto;
    prepara_alta es la previa que no viene de una baja (solo aviso).
    Un calendario TODO-ALTA es continuo (hallazgo de la revision): no hay
    mes normal al que volver, asi que no se anuncian entra/sale ni previa."""
    if all(m == "alta" for m in config.meses):
        return ()
    anios = range(desde.year - 1, hasta.year + 2)
    bloques = [b for anio in anios for b in _instancias(config, anio)]
    preps = {
        b["b"] - dt.timedelta(days=config.dias_preparacion) for b in bloques if b["tipo"] == "alta"
    }
    restaura_en: set[dt.date] = {b["r"] for b in bloques if b["tipo"] == "baja" and b["b"] < b["r"]}
    sueltos: list[Evento] = []
    for bloque in bloques:
        if bloque["tipo"] == "baja":
            if bloque["b"] < bloque["r"] and desde <= bloque["b"] < hasta:
                sueltos.append(
                    Evento(
                        bloque["b"],
                        "entra_baja",
                        FRASE_ENTRA_BAJA.format(pct=_texto(config.reduccion_baja * 100)),
                    )
                )
            if bloque["b"] < bloque["r"] and desde <= bloque["r"] < hasta:
                frase = FRASE_RESTAURA_PREPARA if bloque["r"] in preps else FRASE_RESTAURA
                sueltos.append(Evento(bloque["r"], "restaura", frase))
        elif bloque["tipo"] == "alta":
            if desde <= bloque["b"] < hasta:
                sueltos.append(Evento(bloque["b"], "entra_alta", FRASE_ENTRA_ALTA))
            if desde <= bloque["f"] < hasta:
                sueltos.append(Evento(bloque["f"], "sale_alta", FRASE_SALE_ALTA))
            prep = bloque["b"] - dt.timedelta(days=config.dias_preparacion)
            if prep not in restaura_en and desde <= prep < hasta:
                sueltos.append(Evento(prep, "prepara_alta", FRASE_PREPARA_ALTA))
    return tuple(sorted(sueltos, key=lambda e: (e.fecha, e.tipo)))


NOMBRES_PLATAFORMA = {"amazon_mx": "MX", "amazon_us": "US"}


def _texto_metas(metas: Mapping[str, Decimal]) -> str:
    """{plat: meta} -> «25 % en MX y US» o «25 % en MX y 24 % en US»."""
    por_valor: dict[Decimal, list[str]] = {}
    for plataforma, meta in metas.items():
        nombre = NOMBRES_PLATAFORMA.get(plataforma, plataforma)
        por_valor.setdefault(meta, []).append(nombre)
    partes = [f"{_texto(v)} % en {' y '.join(nombres)}" for v, nombres in por_valor.items()]
    return " y ".join(partes)


def _frase_hoy(
    estacion: Estacion,
    config: ConfigTemporada,
    metas_hoy: Mapping[str, Decimal],
    meta_alta: Mapping[str, Decimal],
) -> str:
    """La primera linea de «Así queda tu año» (la fecha la agrega la vista)."""
    if estacion.fase == "baja":
        return f"Hoy: temporada baja. Orbit baja los bids {_texto(config.reduccion_baja * 100)} %."
    if estacion.fase == "alta":
        texto = _texto_metas(meta_alta)
        return "Hoy: temporada alta." + (f" Meta de ACoS {texto}." if texto else "")
    if estacion.previa_alta:
        return "Hoy: preparación de la temporada alta. Los bids ya regresaron."
    texto = _texto_metas(metas_hoy)
    return "Hoy: mes normal." + (f" Meta de ACoS {texto}." if texto else "")


def _aviso_cambio(
    nueva: Estacion,
    config: ConfigTemporada,
    fase_antes: Fase,
    meta_alta: Mapping[str, Decimal],
) -> str:
    """Aviso amarillo de la vista previa: la edicion cambia la fase de hoy y
    el siguiente ciclo lo ejecuta."""
    if nueva.fase == "baja":
        return (
            "Ojo: con este cambio hoy pasa a temporada baja. En el siguiente ciclo"
            f" Orbit baja los bids {_texto(config.reduccion_baja * 100)} %."
        )
    if nueva.fase == "alta":
        texto = _texto_metas(meta_alta)
        cola = f" la meta de ACoS pasa a {texto}" if texto else " empieza la meta de temporada alta"
        return f"Ojo: con este cambio hoy pasa a temporada alta. En el siguiente ciclo{cola}."
    if fase_antes == "baja":
        return (
            "Ojo: con este cambio hoy sale de la temporada baja. En el siguiente ciclo"
            " los bids regresan a como estaban."
        )
    return (
        "Ojo: con este cambio hoy deja la temporada alta. En el siguiente ciclo"
        " la meta de ACoS vuelve a la normal."
    )


def resumen(
    config: ConfigTemporada,
    hoy: dt.date,
    *,
    antes: ConfigTemporada | None,
    metas_hoy: Mapping[str, Decimal],
    meta_alta: Mapping[str, Decimal],
) -> ResumenTemporada:
    """eventos de los proximos 12 meses + la frase de hoy. Si `antes` da
    otra fase para hoy, aviso_cambio dice que pasa en el siguiente ciclo.
    Lo llaman la pagina, la vista previa y el guardado."""
    hasta = hoy + dt.timedelta(days=365)
    proximos = list(eventos(config, hoy, hasta))
    texto = _texto_metas(meta_alta)
    if texto:
        proximos = [
            replace(e, frase=f"{e.frase} Meta de ACoS {texto}.") if e.tipo == "entra_alta" else e
            for e in proximos
        ]
    estacion = resuelve(config, hoy)
    aviso = None
    if antes is not None:
        fase_antes = resuelve(antes, hoy).fase
        if fase_antes != estacion.fase:
            aviso = _aviso_cambio(estacion, config, fase_antes, meta_alta)
    return ResumenTemporada(
        hoy=hoy,
        fase_hoy=estacion.fase,
        frase_hoy=_frase_hoy(estacion, config, metas_hoy, meta_alta),
        proximos=tuple(proximos),
        aviso_cambio=aviso,
    )


MESES_LARGOS = MESES_ANIO


def fecha_larga(dia: dt.date) -> str:
    """2027-02-08 -> «8 de febrero» (el texto del mensaje de guardado)."""
    return f"{dia.day} de {MESES_LARGOS[dia.month - 1]}"


def mensaje_guardado(resumen_temporada: ResumenTemporada) -> str:
    """El mensaje verde despues de guardar: «Guardado. Próximo cambio: el 1
    de diciembre la meta de ACoS regresa a la normal. La próxima preparación
    empieza el 8 de febrero.»"""
    partes = ["Guardado."]
    if resumen_temporada.proximos:
        primero = resumen_temporada.proximos[0]
        frase = primero.frase[0].lower() + primero.frase[1:]
        partes.append(f"Próximo cambio: el {fecha_larga(primero.fecha)} {frase}")
        prepara = next(
            (e for e in resumen_temporada.proximos[1:] if e.tipo in ("restaura", "prepara_alta")),
            None,
        )
        if prepara is not None:
            partes.append(f"La próxima preparación empieza el {fecha_larga(prepara.fecha)}.")
    return " ".join(partes)


# ---------------------------------------------------------------------------
# Forma guardada: el blob canonico que vive en config_version.settings.
# ---------------------------------------------------------------------------


def como_guardada(config: ConfigTemporada) -> dict:
    """El objeto ads_temporada que se guarda: porcentajes como string
    (regla 4), dias como entero, listas ordenadas."""
    return {
        "version": VERSION_TEMPORADA,
        "meses": list(config.meses),
        "reduccion_baja_pct": _texto(config.reduccion_baja * 100),
        "dias_preparacion": config.dias_preparacion,
        "mult_cortes_baja": _texto(config.mult_cortes_baja),
        "aumento_presupuesto_alta_pct": _texto(config.aumento_presupuesto_alta * 100),
        "fin_anticipado": None
        if config.fin_anticipado is None
        else config.fin_anticipado.isoformat(),
        "mantener_pausadas": sorted(config.mantener_pausadas),
    }


def campana_topada_guardada(topada: CampanaTopada) -> dict:
    """El objeto ads_campana_topada que se guarda (pct como string)."""
    return {"pct": _texto(topada.pct * 100), "dias": topada.dias}


# ---------------------------------------------------------------------------
# TEMPORADA 01 B: lo que decide una keyword. PURO. `destino_entrada`,
# `reduccion_vigente` (la UNICA resolucion de «¿esta hoja sigue reducida?»),
# `nivel_de_regreso`, `inicio_episodio`, `frontera` y `pendiente`. La usan el
# ciclo (decidir), la cola (revalidar al liberar) y TX3 (estampa y fines).
# ---------------------------------------------------------------------------

DIAS_CERCA = 90  # la cerca caduca a los 90 dias (caso.DIAS_LOOKBACK)


@dataclass(frozen=True)
class EpisodioVigente:
    """El episodio abierto de la plataforma (lo lee temporada_io.lee_estado)."""

    id: int
    abierto_el: dt.datetime


@dataclass(frozen=True)
class FinReduccion:
    """La reduccion vieja de una hoja termina en este ciclo, sin enviar nada.
    TX3 lo guarda en temporada_fin_reduccion; si la mano se vio con esta baja
    abierta, `excluye_abierto` la excluye del episodio con `bid_visto`."""

    episodio_id: int
    motivo: Literal["ya_arriba", "mano_del_dueno"]
    excluye_abierto: bool
    bid_visto: Decimal | None = None


@dataclass(frozen=True)
class FronteraHoja:
    """Lo que la temporada dice de UNA keyword para cortes y juicio. Sale de
    `frontera`."""

    fase_hoja: Fase  # "baja" si el calendario dice baja o la hoja sigue reducida
    mult_cortes: Decimal  # 1 salvo fase_hoja == "baja"
    cerca_hoja: dt.date | None  # dia UTC siguiente a su ultimo movimiento confirmado
    cerca_plataforma: dt.date | None  # para grupo, cuenta y terminos
    episodio_id: int | None  # el de su reduccion vigente, o el abierto
    fin: FinReduccion | None = None  # TX3 lo guarda con temporada_io.registra_fines
    exclusion_hoy: Decimal | None = None  # mano sin fecha vista hoy en baja


@dataclass(frozen=True)
class EntradaPendiente:
    """La bajada planeada de la entrada a la baja (destino ya en centavos)."""

    destino: Decimal


@dataclass(frozen=True)
class AjustePendiente:
    """David edito la reduccion a media baja: destino = base x (1 - r nueva)."""

    destino: Decimal


@dataclass(frozen=True)
class RestauracionPendiente:
    """El regreso al salir de la baja (o al terminar la baja hoy)."""

    nivel: Decimal  # base del episodio, acotada por piso y techo
    episodio_id: int


SinPendienteMotivo = Literal[
    "fuera_de_temporada",
    "ya_entro",
    "ya_arriba",
    "dueno_decidio",
    "bid_heredado",
    "sin_centavo_factible",
    "dato_faltante",
]


@dataclass(frozen=True)
class SinPendiente:
    motivo: SinPendienteMotivo


Pendiente = EntradaPendiente | AjustePendiente | RestauracionPendiente | SinPendiente


def _centavos_del_rango(piso: Decimal, techo: Decimal) -> tuple[Decimal, Decimal] | None:
    """El intervalo de CENTAVOS validos dentro de [piso, techo]: el minimo
    redondeado hacia arriba y el maximo hacia abajo (un goal puede llevar
    limites fraccionarios; Amazon recibe centavos y un clamp sin redondeo
    enviaria una puja fuera del rango). None sin ningun centavo factible."""
    piso_c = piso.quantize(CENTAVO, rounding=ROUND_CEILING)
    techo_c = techo.quantize(CENTAVO, rounding=ROUND_FLOOR)
    if piso_c > techo_c:
        return None
    return piso_c, techo_c


def destino_entrada(
    bid: Decimal, reduccion: Decimal, piso: Decimal, techo: Decimal
) -> Decimal | None:
    """El centavo mas bajo que no baja mas que `reduccion`, dentro del
    intervalo de centavos de [piso, techo]. None sin centavo factible (el bid
    ya vive en el piso).
    # 0.15 USD, 30 % -> 0.11 (26.7 %); 1.55 MXN -> 1.09 (29.7 %); 8.00 -> 5.60;
    # 0.10 USD con piso 0.10 -> None; piso fraccionario 5.6049 -> 5.61."""
    rango = _centavos_del_rango(piso, techo)
    if rango is None:
        return None
    piso_c, techo_c = rango
    d = centavo_hacia(bid * (Decimal(1) - reduccion), desde=bid)
    d = min(max(d, piso_c), techo_c)
    if bid - d < CENTAVO:
        return None
    return d


def inicio_episodio(episodio: EpisodioVigente | None, dia: dt.date) -> dt.datetime:
    """Desde cuando una mano del dueno cuenta como de esta baja: el inicio del dia
    UTC en que se abrio el episodio. El dia en que TX3 lo abre, TX2 todavia no lo
    ve y usa `dia`. Una mano de esa manana protege la keyword ese dia y los
    siguientes, aunque `abierto_el` sea posterior."""
    base = episodio.abierto_el.astimezone(dt.UTC).date() if episodio is not None else dia
    return dt.datetime.combine(base, dt.time(0), tzinfo=dt.UTC)


def nivel_de_regreso(
    red: ReduccionVigente, bid: Decimal, piso: Decimal, techo: Decimal
) -> Decimal | None:
    """A donde regresa una hoja reducida. Sin recortes despues de la entrada es la
    base. Si Orbit recorto despues de la entrada, el regreso conserva esos recortes
    en proporcion; nunca baja y siempre queda en centavos del intervalo de
    [piso, techo]. None si el rango del goal no tiene ningun centavo factible
    (piso arriba del techo al redondear): la hoja que lo necesite deja SU
    regreso pendiente, jamas tumba el ciclo.
    # 8 -> 5.60 (entrada) -> 3.15 (recortes con la temporada apagada): 8 x
    # 3.15 / 5.60 = 4.50, no 8.00. Techo fraccionario 15.999 -> 15.99."""
    rango = _centavos_del_rango(piso, techo)
    if rango is None:
        return None
    piso_c, techo_c = rango
    if red.solo_temporada_despues or bid is None or bid >= red.ultimo_bid:
        factor = Decimal(1)
    else:
        factor = bid / red.ultimo_bid
    nivel = (red.base * factor).quantize(CENTAVO, rounding=ROUND_FLOOR)
    return min(max(nivel, piso_c), techo_c)


def reduccion_vigente(
    cadena: CadenaBid,
    *,
    en_baja: bool,
    dia: dt.date,
    episodio: EpisodioVigente | None,
    bid: Decimal,
    piso: Decimal,
    techo: Decimal,
) -> tuple[ReduccionVigente | None, FinReduccion | None]:
    """La unica resolucion de «¿esta hoja sigue reducida?». La usan pendiente,
    frontera (y por ella estampa y la revalidacion de la cola) y TX3.
    cadena.reduccion ya omite las reducciones con fila en temporada_fin_reduccion
    (HistoriaBid.fines), asi que un fin guardado no revive."""
    red = cadena.reduccion
    if red is None or (episodio is not None and red.episodio_id == episodio.id):
        return red, None
    manos = [m for m in cadena.manos_del_dueno if m > red.ultimo_el]
    if manos or cadena.dueno_al_final:
        ahora = cadena.dueno_al_final or (
            en_baja and bool(manos) and max(manos) >= inicio_episodio(episodio, dia)
        )
        return None, FinReduccion(
            red.episodio_id,
            "mano_del_dueno",
            excluye_abierto=bool(en_baja and ahora),
            bid_visto=bid,
        )
    nivel = nivel_de_regreso(red, bid, piso, techo)
    if bid is not None and nivel is not None and bid >= nivel:
        return None, FinReduccion(red.episodio_id, "ya_arriba", excluye_abierto=False)
    return red, None  # su regreso sigue pendiente (retenido o sin centavo factible en el rango)


def _pendiente_desde(
    estacion: Estacion,
    cadena: CadenaBid | None,
    *,
    episodio: EpisodioVigente | None,
    excluida_en: frozenset[int],
    bid: Decimal | None,
    piso: Decimal,
    techo: Decimal,
) -> Pendiente:
    """Lo pendiente de UNA keyword. Decisiones 4 y 5 del diseno. `cadena` None =
    sin cambios conocidos (la entrada baja el bid que tenga; riesgo 2 del
    diseno: dueno_al_final no es determinable)."""
    if bid is None:
        return SinPendiente("bid_heredado")
    manos = cadena.manos_del_dueno if cadena is not None else ()
    dueno_al_final = cadena.dueno_al_final if cadena is not None else False
    en_baja = estacion.fase == "baja"
    if cadena is None:
        red: ReduccionVigente | None = None
        fin: FinReduccion | None = None
    else:
        red, fin = reduccion_vigente(
            cadena,
            en_baja=en_baja,
            dia=estacion.dia,
            episodio=episodio,
            bid=bid,
            piso=piso,
            techo=techo,
        )
    if fin is not None and fin.excluye_abierto:
        return SinPendiente("dueno_decidio")
    if red is not None:
        if red.episodio_id in excluida_en:
            return SinPendiente("dueno_decidio")
        if any(m > red.ultimo_el for m in manos) or dueno_al_final:
            return SinPendiente("dueno_decidio")
        if en_baja:
            if (
                episodio is not None
                and red.episodio_id == episodio.id
                and red.solo_temporada_despues
            ):
                d = destino_entrada(red.base, estacion.reduccion_baja, piso, techo)
                if d is not None and abs(d - bid) >= CENTAVO:
                    return AjustePendiente(d)
            return SinPendiente("ya_entro")  # nunca otra entrada
        nivel = nivel_de_regreso(red, bid, piso, techo)
        if nivel is None:
            # El rango del goal no tiene ningun centavo factible para su
            # regreso: la reduccion queda pendiente (sin fin) y la hoja no
            # recibe veredicto de temporada hasta que el dueno arregle el
            # rango. Jamas tumba el ciclo.
            return SinPendiente("dato_faltante")
        if bid >= nivel:
            return SinPendiente("ya_arriba")
        return RestauracionPendiente(nivel, red.episodio_id)
    if estacion.fase != "baja":
        return SinPendiente("fuera_de_temporada")
    if episodio is not None and episodio.id in excluida_en:
        return SinPendiente("dueno_decidio")
    if any(m >= inicio_episodio(episodio, estacion.dia) for m in manos):
        return SinPendiente("dueno_decidio")
    if dueno_al_final:
        # Cambio sin registro: Amazon no da su fecha, cuenta como mano de esta
        # baja; frontera la deja en exclusion_hoy y TX3 la guarda fechada con
        # bid_visto, asi en la siguiente baja ya es vieja.
        return SinPendiente("dueno_decidio")
    d = destino_entrada(bid, estacion.reduccion_baja, piso, techo)
    if d is None:
        return SinPendiente("sin_centavo_factible")
    return EntradaPendiente(d)


def pendiente(caso: CasoHoja) -> Pendiente:
    """Decisiones 4 y 5. Requiere caso.temporada; la cadena sale de
    recuperacion.cadena_bid (None = dato faltante)."""
    t = caso.temporada
    if t is None:
        raise ValueError("pendiente exige caso.temporada (decide solo la llama con el marcador)")
    bid = caso.bid.valor
    if bid is None:
        return SinPendiente("bid_heredado")
    cad = recuperacion.cadena_bid(caso)
    if cad is None:
        return SinPendiente("dato_faltante")
    return _pendiente_desde(
        t.estacion,
        cad,
        episodio=t.episodio,
        excluida_en=t.excluida_en,
        bid=bid,
        piso=caso.bid.piso,
        techo=caso.bid.techo,
    )


def frontera(
    estacion: Estacion,
    cadena: CadenaBid | None,
    *,
    episodio: EpisodioVigente | None,
    excluida_en: frozenset[int],
    cerca_plataforma: dt.date | None,
    bid: Decimal | None,
    piso: Decimal,
    techo: Decimal,
) -> FronteraHoja:
    """Una sola funcion para el ciclo (decidir) y la cola (liberar)."""
    p = _pendiente_desde(
        estacion,
        cadena,
        episodio=episodio,
        excluida_en=excluida_en,
        bid=bid,
        piso=piso,
        techo=techo,
    )
    dueno_al_final = cadena.dueno_al_final if cadena is not None else False
    en_baja = estacion.fase == "baja"
    if cadena is None:
        red: ReduccionVigente | None = None
        fin: FinReduccion | None = None
    else:
        red, fin = reduccion_vigente(
            cadena,
            en_baja=en_baja,
            dia=estacion.dia,
            episodio=episodio,
            bid=bid,
            piso=piso,
            techo=techo,
        )
    terreno_de_david = en_baja and (
        (episodio is not None and episodio.id in excluida_en) or dueno_al_final
    )
    # Hoja con reduccion viva y regreso IMPOSIBLE (rango del goal sin centavo
    # factible): sigue en baja (conservadora, como un regreso retenido) y el
    # ciclo corre: jamas una excepcion de plataforma.
    sin_nivel = red is not None and _centavos_del_rango(piso, techo) is None
    if terreno_de_david:
        fase_hoja: Fase = "normal"  # decision 7 del plan: sin reglas de la baja
    elif en_baja or isinstance(p, RestauracionPendiente) or sin_nivel:
        fase_hoja = "baja"
    else:
        fase_hoja = estacion.fase
    mult = estacion.mult_cortes_baja if fase_hoja == "baja" else Decimal(1)
    cerca_hoja = None
    if cadena is not None:
        temporadas = [c for c in cadena.cambios if c.origen == "temporada"]
        if temporadas:
            ultimo = temporadas[-1]
            cerca = ultimo.confirmado_el.astimezone(dt.UTC).date() + dt.timedelta(days=1)
            if cerca >= estacion.dia - dt.timedelta(days=DIAS_CERCA):
                cerca_hoja = cerca
    episodio_id = None
    if red is not None:
        episodio_id = red.episodio_id
    elif episodio is not None:
        episodio_id = episodio.id
    exclusion_hoy = None
    if (
        en_baja
        and dueno_al_final
        and bid is not None
        and (episodio is None or episodio.id not in excluida_en)
    ):
        exclusion_hoy = bid
    return FronteraHoja(
        fase_hoja=fase_hoja,
        mult_cortes=mult,
        cerca_hoja=cerca_hoja,
        cerca_plataforma=cerca_plataforma,
        episodio_id=episodio_id,
        fin=fin,
        exclusion_hoy=exclusion_hoy,
    )
