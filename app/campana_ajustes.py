"""Ajustes de campana que aprueba el dueno (BIDS 02, V.3). PURO: planea el
cambio desde la configuracion vigente, sin base ni Amazon (candado en
`tests/test_arq_bids_v.py`). La escritura vive en `app/apply.py`.

Solo existen las clases cuya sonda sello V.0 (`CLASES_SELLADAS`):
presupuesto (sonda 2), ajuste por placement (sonda 3) y gasto fuera de
Amazon (sonda 4, con desviacion autorizada: mandar {} no regresa, el
readback lo detecta).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from app.ads.campana_config import ConfigCampana
from app.optimizer.bid import PLATAFORMAS_MONEDA

CLASES_SELLADAS = frozenset({"fuera_de_amazon", "ajuste_ubicacion", "presupuesto"})

# Orden fijo de los botones de la pantalla (P.2b): el dict filtra por
# selladas, asi una clase sin sonda no trae boton.
ORDEN_CLASES = ("presupuesto", "ajuste_ubicacion", "fuera_de_amazon")

_PCT_MAX = 900
_FUERA_LIMITADO = '{"offAmazonBudgetControlStrategy": "MINIMIZE_SPEND"}'
_CAMPO_POR_UBICACION = {
    "arriba_de_busqueda": "ajuste_top_pct",
    "resto_de_busqueda": "ajuste_resto_pct",
    "paginas_de_producto": "ajuste_producto_pct",
}
_NOMBRE_UBICACION = {
    "arriba_de_busqueda": "arriba de búsqueda",
    "resto_de_busqueda": "resto de búsqueda",
    "paginas_de_producto": "páginas de producto",
}


@dataclass(frozen=True)
class LimitarFueraDeAmazon:
    """Limita el gasto fuera de Amazon (sonda 4: MINIMIZE_SPEND)."""


@dataclass(frozen=True)
class CambiarAjusteUbicacion:
    ubicacion: str  # una de las tres con ajuste; 0 quita el ajuste
    porcentaje: int  # 0 a 900 (tope documentado por Amazon)


@dataclass(frozen=True)
class CambiarPresupuesto:
    presupuesto_diario: Decimal  # en la moneda del perfil; > 0


AjusteCampana = LimitarFueraDeAmazon | CambiarAjusteUbicacion | CambiarPresupuesto


class AjusteSinCambio(ValueError):
    """El ajuste pedido no cambia nada contra el vigente. Subclase de
    ValueError (los callers que mapean ValueError a 422 no cambian); la
    ruta la distingue para reconciliar replays (B4)."""


_CLASE_POR_TIPO = {
    LimitarFueraDeAmazon: "fuera_de_amazon",
    CambiarAjusteUbicacion: "ajuste_ubicacion",
    CambiarPresupuesto: "presupuesto",
}


@dataclass(frozen=True)
class PlanAjuste:
    """Lo que el dueno ve antes de confirmar. `antes` y `despues` son la
    configuracion COMPLETA (no un parche); el PUT (aplicar y regresar)
    filtra a la llave de la clase."""

    campana_id: int
    plataforma: str
    clase: str
    ajuste: AjusteCampana
    antes: ConfigCampana
    despues: ConfigCampana
    frase: str

    def huella(self) -> str:
        """Huella del plan (como la de la fabrica)."""
        canonico = json.dumps(
            {
                "campana_id": self.campana_id,
                "plataforma": self.plataforma,
                "clase": self.clase,
                "antes": self.antes.como_json(),
                "despues": self.despues.como_json(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonico.encode("utf-8")).hexdigest()

    def como_dict(self) -> dict:
        return {
            "campana_id": self.campana_id,
            "plataforma": self.plataforma,
            "clase": self.clase,
            "antes": self.antes.como_json(),
            "despues": self.despues.como_json(),
            "huella": self.huella(),
            "frase": self.frase,
        }


def _dinero_txt(valor: Decimal) -> str:
    """Dinero para la frase del dueno: sin ceros de mas ("100.0000" ->
    "100"). Solo presentacion; el valor viaja intacto."""
    texto = format(valor, "f")
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


def _ya_limitado(fuera: str | None) -> bool:
    if not fuera:
        return False
    try:
        return json.loads(fuera).get("offAmazonBudgetControlStrategy") == "MINIMIZE_SPEND"
    except (ValueError, AttributeError):
        return False


def planea_ajuste(
    vigente: ConfigCampana, ajuste: AjusteCampana, *, campana_id: int, plataforma: str
) -> PlanAjuste:
    """Pura. Rechaza con ValueError la clase sin sonda sellada, un ajuste
    que no cambia nada, un porcentaje fuera de 0..900, un presupuesto
    que no es > 0 finito y un ajuste de ubicacion sobre un vigente sin
    `dynamicBidding` (forma que V.0 no sondo: el PUT mezclaria y el
    readback divergiria siempre). El presupuesto se cuantiza a centavos
    (lo que Amazon ecoa) y estrena la moneda del perfil si el vigente no
    trae (si no, el readback trae moneda y el destino no: divergencia)."""
    clase = _CLASE_POR_TIPO.get(type(ajuste))
    if clase is None or clase not in CLASES_SELLADAS:
        raise ValueError(f"clase sin sonda sellada: {type(ajuste).__name__}")
    if isinstance(ajuste, CambiarPresupuesto):
        if not ajuste.presupuesto_diario.is_finite():
            raise ValueError(f"presupuesto debe ser finito, llego {ajuste.presupuesto_diario}")
        try:
            monto = ajuste.presupuesto_diario.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        except InvalidOperation:
            raise ValueError(
                f"presupuesto fuera de rango, llego {ajuste.presupuesto_diario}"
            ) from None
        if monto <= 0:
            raise ValueError(f"presupuesto debe ser > 0, llego {ajuste.presupuesto_diario}")
        if vigente.presupuesto_diario == monto:
            raise AjusteSinCambio("el ajuste no cambia nada")
        despues = replace(
            vigente,
            presupuesto_diario=monto,
            moneda=vigente.moneda or PLATAFORMAS_MONEDA[plataforma],
        )
        if vigente.presupuesto_diario is None:
            frase = (
                f"Orbit pone el presupuesto diario en {_dinero_txt(monto)}"
                f" {despues.moneda} en campaña {vigente.campana_externa} (antes sin presupuesto)"
            )
        else:
            frase = (
                f"Orbit cambia el presupuesto diario de {_dinero_txt(vigente.presupuesto_diario)}"
                f" {vigente.moneda} a {_dinero_txt(monto)} {vigente.moneda}"
                f" en campaña {vigente.campana_externa}"
            )
    elif isinstance(ajuste, CambiarAjusteUbicacion):
        campo = _CAMPO_POR_UBICACION.get(ajuste.ubicacion)
        if campo is None:
            raise ValueError(f"ubicacion sin ajuste: {ajuste.ubicacion}")
        if vigente.estrategia_puja is None:
            raise ValueError(
                "campana sin dynamicBidding: V.0 no sello esa forma,"
                " el ajuste de ubicacion divergiria"
            )
        if not 0 <= ajuste.porcentaje <= _PCT_MAX:
            raise ValueError(f"porcentaje fuera de 0..{_PCT_MAX}: {ajuste.porcentaje}")
        actual = getattr(vigente, campo) or 0
        if actual == ajuste.porcentaje:
            raise AjusteSinCambio("el ajuste no cambia nada")
        despues = replace(vigente, **{campo: ajuste.porcentaje})
        frase = (
            f"Orbit cambia el ajuste de {_NOMBRE_UBICACION[ajuste.ubicacion]}"
            f" de +{actual} % a +{ajuste.porcentaje} %"
            f" en campaña {vigente.campana_externa}"
        )
    else:
        if _ya_limitado(vigente.fuera_de_amazon):
            raise AjusteSinCambio("el ajuste no cambia nada")
        despues = replace(vigente, fuera_de_amazon=_FUERA_LIMITADO)
        frase = (
            "Orbit limita el gasto fuera de Amazon (MINIMIZE_SPEND)"
            f" en campaña {vigente.campana_externa}"
        )
    return PlanAjuste(
        campana_id=campana_id,
        plataforma=plataforma,
        clase=clase,
        ajuste=ajuste,
        antes=vigente,
        despues=despues,
        frase=frase,
    )


def campo_del_ajuste(ajuste: AjusteCampana) -> str:
    """Campo de ConfigCampana que el ajuste cambia (B11/B12):
    presupuesto_diario, el campo del placement o fuera_de_amazon."""
    if isinstance(ajuste, CambiarPresupuesto):
        return "presupuesto_diario"
    if isinstance(ajuste, CambiarAjusteUbicacion):
        return _CAMPO_POR_UBICACION[ajuste.ubicacion]
    return "fuera_de_amazon"


def coincide_campo(ajuste: AjusteCampana, leida: ConfigCampana, destino: ConfigCampana) -> bool:
    """El campo que el ajuste cambia coincide entre lo leido y el
    destino (B11: el readback compara ese campo; B12: la reconciliacion
    tambien). El presupuesto compara moneda ademas del monto."""
    if isinstance(ajuste, CambiarPresupuesto):
        return (
            leida.presupuesto_diario == destino.presupuesto_diario
            and leida.moneda == destino.moneda
        )
    if isinstance(ajuste, CambiarAjusteUbicacion):
        campo = _CAMPO_POR_UBICACION[ajuste.ubicacion]
        return getattr(leida, campo) == getattr(destino, campo)
    return leida.fuera_de_amazon == destino.fuera_de_amazon


def coincide_clase(clase: str, leida: ConfigCampana, destino: ConfigCampana) -> bool:
    """El readback confirma el ajuste si los campos que la clase mueve son
    los del destino (comparar el registro completo diverge ante cualquier
    normalizacion de Amazon en campos ajenos: estrategia por default,
    ceros, moneda estrenada)."""
    if leida.campana_externa != destino.campana_externa:
        return False
    if clase == "presupuesto":
        return (
            leida.presupuesto_diario == destino.presupuesto_diario
            and leida.moneda == destino.moneda
        )
    if clase == "ajuste_ubicacion":
        return (
            leida.estrategia_puja == destino.estrategia_puja
            and leida.ajuste_top_pct == destino.ajuste_top_pct
            and leida.ajuste_resto_pct == destino.ajuste_resto_pct
            and leida.ajuste_producto_pct == destino.ajuste_producto_pct
        )
    return leida.fuera_de_amazon == destino.fuera_de_amazon
