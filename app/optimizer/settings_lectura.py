"""Lectores fail-closed de settings del optimizador (extraido de app/optimizer/goals.py, Fase 3.5).

Modulo puro dentro de `app/optimizer/` (sin IO): lee claves selladas de
`config_version.settings` (target por plataforma, escalera/flags, fraccion de
margen, confianzas de recorte/subida, gasto para concluir, politica de bids y
version de economia de cambio). `app/optimizer/goals.py` re-exporta este bloque.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.formato import dinero
from app.optimizer.caso import VERSIONES_ECONOMIA_CAMBIO


@dataclass(frozen=True)
class TechoHumano:
    """Tope de lo que TECLEA una persona (no del motor): decision del dueno
    del 2026-11-09. Se aplica al construir o cambiar un valor, jamas al
    releer lo guardado."""

    bid: Decimal
    presupuesto_diario: Decimal


TECHOS_HUMANOS_POR_MONEDA: dict[str, TechoHumano] = {
    "USD": TechoHumano(bid=Decimal("10"), presupuesto_diario=Decimal("53")),
    "MXN": TechoHumano(bid=Decimal("180"), presupuesto_diario=Decimal("10708")),
}


def exige_bajo_techo(
    clase: Literal["bid", "presupuesto_diario"], monto: Decimal, moneda: str
) -> None:
    """ValueError en espanol si `monto` supera el tope humano de su moneda."""
    techo = TECHOS_HUMANOS_POR_MONEDA.get(moneda)
    if techo is None:
        raise ValueError(f"moneda {moneda!r} sin tope humano: TECHOS_HUMANOS_POR_MONEDA")
    if clase == "bid":
        maximo, nombre = techo.bid, "la puja"
    elif clase == "presupuesto_diario":
        maximo, nombre = techo.presupuesto_diario, "el presupuesto diario"
    else:
        raise ValueError(f"clase de techo desconocida: {clase!r}")
    if not monto.is_finite():
        raise ValueError(f"{nombre} debe ser un numero finito")
    if monto > maximo:
        raise ValueError(f"{nombre} de {monto} {moneda} supera el maximo de {maximo} {moneda}")


VECES_PRESUPUESTO_ALTO = 2
MONTO_MAXIMO = Decimal("9999999999.99")
TEXTO_FALTA_CONFIRMAR_PRESUPUESTO_ALTO = (
    "Falta confirmar el presupuesto alto: recarga la página, vuelve a ver el plan"
    " y marca «Sí, quiero ese presupuesto»"
)


@dataclass(frozen=True)
class PresupuestoAlto:
    """Aviso de un presupuesto diario mas del doble de su referencia."""

    monto: Decimal  # por campana y por dia
    moneda: str
    referencia: Decimal | None
    contra: Literal["esta_campana", "tus_campanas"]

    def texto(self) -> str:
        """La frase que ve la persona (cifras con miles y dos decimales)."""
        monto = f"{dinero(self.monto)} {self.moneda}"
        if self.contra == "esta_campana":
            if self.referencia is None:
                return (
                    f"Vas a poner {monto} diarios."
                    " Esta campaña no tiene un presupuesto anterior para comparar."
                )
            return (
                f"Vas a poner {monto} diarios. Es más del doble de lo que esta campaña"
                f" tiene hoy ({dinero(self.referencia)} {self.moneda})."
            )
        if self.referencia is None:
            return (
                f"Vas a poner {monto} diarios."
                f" Hoy no tienes campañas activas en {self.moneda} para comparar."
            )
        return (
            f"Vas a poner {monto} diarios. 9 de cada 10 de tus campañas activas"
            f" tienen {dinero(self.referencia)} {self.moneda} o menos."
        )


def presupuesto_alto(
    monto: Decimal,
    moneda: str,
    referencia: Decimal | None,
    contra: Literal["esta_campana", "tus_campanas"],
) -> PresupuestoAlto | None:
    """Aviso si `monto` pasa del doble de `referencia` (o del numero fijo sin referencia)."""
    techo = TECHOS_HUMANOS_POR_MONEDA.get(moneda)
    if techo is None:
        raise ValueError(f"moneda {moneda!r} sin tope humano: TECHOS_HUMANOS_POR_MONEDA")
    umbral = techo.presupuesto_diario if referencia is None else VECES_PRESUPUESTO_ALTO * referencia
    return PresupuestoAlto(monto, moneda, referencia, contra) if monto > umbral else None


CLAVE_SETTING_MODO = "ads_optimizer_mode"
CLAVE_SETTING_PROPUESTAS_CAMPANA = "ads_propuestas_campana"

# Reticulo de modos: off < shadow < live.
MODOS = ("off", "shadow", "live")
_ORDEN_MODO: dict[str, int] = {modo: indice for indice, modo in enumerate(MODOS)}

CONFIANZA_RECORTE_DEFAULT = Decimal("0.80")
CONFIANZA_SUBIDA_DEFAULT = Decimal("0.70")
CONFIANZA_MIN = Decimal("0.50")
CONFIANZA_MAX = Decimal("0.99")

# Defaults POR PLATAFORMA (dueno, 2026-10-09): con la clave ausente el motor
# concluye con 350 MXN / 36 USD. UNICA fuente del numero.
GASTO_PARA_CONCLUIR_DEFAULT: dict[str, Decimal] = {
    "amazon_mx": Decimal("350"),
    "amazon_us": Decimal("36"),
}

# Politica de bids vigente (BIDS 02 M.3): el UNICO valor que enciende el motor.
POLITICA_BID_VIGENTE = "niveles_v3"


def clave_target_plataforma(platform: str) -> str:
    """Clave sellada del setting de target por plataforma
    (ads_target_acos_pct_<platform>, docs/DATABASE.md)."""
    return f"ads_target_acos_pct_{platform}"


def target_desde_settings(settings: Mapping, platform: str) -> Decimal | None:
    """Target ACoS de config_version.settings para la plataforma: None si la
    clave no esta. Presente pero no numerico, NaN/Inf, 0 o negativo = config
    CORRUPTA -> ValueError ruidoso (regla 3)."""
    clave = clave_target_plataforma(platform)
    valor = settings.get(clave)
    if valor is None:
        return None
    try:
        target = Decimal(str(valor))
    except ArithmeticError as exc:  # InvalidOperation: basura no numerica
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}") from exc
    if not target.is_finite() or target <= 0:
        raise ValueError(f"setting {clave}: target debe ser > 0 y finito, llego {target}")
    return target


def modo_desde_settings(settings: Mapping) -> str:
    """Escalera global desde config_version.settings (ads_optimizer_mode).
    Fail-closed: sin clave, valor invalido o NULL -> 'off'."""
    valor = settings.get(CLAVE_SETTING_MODO)
    if isinstance(valor, str) and valor in _ORDEN_MODO:
        return valor
    return "off"


def propuestas_campana_desde_settings(settings: Mapping) -> bool:
    """C.4 B3: True solo si config_version.settings trae JSON true bajo
    ads_propuestas_campana (fail-closed)."""
    return settings.get(CLAVE_SETTING_PROPUESTAS_CAMPANA) is True


def fraccion_desde_settings(settings: Mapping, platform: str) -> Decimal | None:
    """Fraccion del target de margen (ads_target_fraccion_margen_<plat>).
    Ausente/null -> None; presente invalida -> ValueError ruidoso."""
    clave = f"ads_target_fraccion_margen_{platform}"
    valor = settings.get(clave)
    if valor is None:
        return None
    try:
        fraccion = Decimal(str(valor).strip())
    except InvalidOperation as exc:
        raise ValueError(f"setting {clave}: fraccion no numerica: {valor!r}") from exc
    if not fraccion.is_finite() or not Decimal(0) < fraccion <= Decimal(1):
        raise ValueError(f"setting {clave}: fraccion debe estar en (0, 1], llego {valor!r}")
    return fraccion


def _valida_fraccion(fraccion: Decimal | None) -> Decimal | None:
    """Fraccion YA parseada: None pasa; presente pero no finita, <= 0 o > 1
    revienta (config corrupta, no dato faltante)."""
    if fraccion is None:
        return None
    if not fraccion.is_finite() or not Decimal(0) < fraccion <= Decimal(1):
        raise ValueError(f"fraccion de margen invalida: {fraccion!r} (debe estar en (0, 1])")
    return fraccion


def clave_confianza_recorte(platform: str) -> str:
    """Clave sellada de la confianza de recorte (ads_confianza_recorte_<platform>)."""
    return f"ads_confianza_recorte_{platform}"


def clave_confianza_subida(platform: str) -> str:
    """Clave sellada de la confianza de subida (ads_confianza_subida_<platform>)."""
    return f"ads_confianza_subida_{platform}"


def _confianza_desde_settings(
    settings: Mapping, clave: str, default: Decimal, nombre: str
) -> Decimal:
    """Confianza resuelta: ausente = default; presente fuera de [0.50, 0.99]
    o no numerica = ValueError ruidoso."""
    valor = settings.get(clave)
    if valor is None:
        return default
    try:
        confianza = Decimal(str(valor).strip())
    except InvalidOperation as exc:
        raise ValueError(f"setting {clave}: confianza no numerica: {valor!r}") from exc
    if not confianza.is_finite() or not CONFIANZA_MIN <= confianza <= CONFIANZA_MAX:
        raise ValueError(
            f"setting {clave}: confianza de {nombre} debe estar en [0.50, 0.99], llego {valor!r}"
        )
    return confianza


def confianza_recorte_desde_settings(settings: Mapping, platform: str) -> Decimal:
    """Confianza de recorte. Ausente = 0.80."""
    return _confianza_desde_settings(
        settings, clave_confianza_recorte(platform), CONFIANZA_RECORTE_DEFAULT, "recorte"
    )


def confianza_subida_desde_settings(settings: Mapping, platform: str) -> Decimal:
    """Confianza de subida. Ausente = 0.70."""
    return _confianza_desde_settings(
        settings, clave_confianza_subida(platform), CONFIANZA_SUBIDA_DEFAULT, "subida"
    )


def clave_gasto_para_concluir(platform: str) -> str:
    """Clave de config_version.settings del gasto para concluir."""
    return f"ads_gasto_para_concluir_{platform}"


def gasto_para_concluir_desde_settings(settings: Mapping, platform: str) -> Decimal:
    """Lector fail-closed de `ads_gasto_para_concluir_<platform>`: ausente =
    350 MXN / 36 USD; presente invalido o plataforma desconocida -> ValueError."""
    clave = clave_gasto_para_concluir(platform)
    valor = settings.get(clave)
    if valor is None:
        try:
            return GASTO_PARA_CONCLUIR_DEFAULT[platform]
        except KeyError as exc:
            raise ValueError(
                f"sin default inventado para {platform!r}; agregalo a "
                "GASTO_PARA_CONCLUIR_DEFAULT con decision del dueno"
            ) from exc
    try:
        gasto = Decimal(str(valor).strip())
    except InvalidOperation as exc:
        raise ValueError(f"setting {clave}: gasto no numerico: {valor!r}") from exc
    if not gasto.is_finite() or gasto <= 0:
        raise ValueError(
            f"setting {clave}: gasto para concluir debe ser > 0 y finito, llego {gasto}"
        )
    return gasto


def clave_bid_politica(platform: str) -> str:
    """Clave sellada del interruptor del motor de bids."""
    return f"ads_bid_politica_{platform}"


def politica_bid_desde_settings(settings: Mapping, plataforma: str) -> Literal["niveles_v3"] | None:
    """Interruptor fail-closed de `ads_bid_politica_<platform>`: ausente ->
    None; 'niveles_v3' -> activa; cualquier otro valor -> ValueError."""
    clave = clave_bid_politica(plataforma)
    valor = settings.get(clave)
    if valor is None:
        return None
    if valor == POLITICA_BID_VIGENTE:
        return POLITICA_BID_VIGENTE
    raise ValueError(
        f"setting {clave}: politica de bids debe ser {POLITICA_BID_VIGENTE!r}, llego {valor!r}"
    )


def clave_economia_cambio(plataforma: str) -> str:
    """Clave de la seleccion de la regla economica de repeticion (M6)."""
    return f"ads_bid_economia_{plataforma}"


def version_economia_cambio_desde_settings(settings: Mapping, plataforma: str) -> str | None:
    """Interruptor fail-closed de la regla M5/M6 y su version."""
    clave = clave_economia_cambio(plataforma)
    valor = settings.get(clave)
    if valor is None:
        return None
    if valor in VERSIONES_ECONOMIA_CAMBIO:
        return valor
    raise ValueError(f"setting {clave}: debe ser {VERSIONES_ECONOMIA_CAMBIO}, llego {valor!r}")
