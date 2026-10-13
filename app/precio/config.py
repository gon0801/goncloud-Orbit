"""Config del motor de precios (REPRICING 01 A.2).

Lee un `Mapping` (los `settings` de `config_version`): valores que llegan
como string o numero JSON, `ValueError` ruidoso que nombra la clave si
falta o sale de cota. Patron copiado de `app/optimizer/goals.py`
(`target_desde_settings`, `fraccion_desde_settings`); NO importa
`app.optimizer`.

Sin defaults en codigo: el valor inicial del plan es dato de la config,
no una constante. Cotas literales del plan (l.50-66).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["ConfigPrecio", "ConfigMotor", "leer_config", "leer_config_motor"]

MODOS = ("off", "shadow", "live")
_MONEDA_ORDEN = {"off": 0, "shadow": 1, "live": 2}


@dataclass(frozen=True)
class ConfigPrecio:
    """Claves de `precio_*` que usan las reglas, ya validadas."""

    caida_ventas_pct: Decimal
    senal_dias: int
    u60_min: int
    fechas_excluidas: tuple[tuple[date, date], ...]
    escalon_max_pct: Decimal
    movimiento_min_pct: Decimal
    movimiento_min_abs_mxn: Decimal
    movimiento_min_abs_usd: Decimal
    tolerancia: Decimal
    dias_entre_cambios: int
    freno_cambios: int
    divergencia_max_pct: Decimal


def _numero(settings: Mapping, clave: str) -> Decimal:
    if clave not in settings or settings[clave] is None:
        raise ValueError(f"config sin {clave}")
    valor = settings[clave]
    if isinstance(valor, bool):
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}")
    try:
        numero = Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}") from exc
    if not numero.is_finite():
        raise ValueError(f"setting {clave}: valor no finito: {valor!r}")
    return numero


def _fraccion(settings: Mapping, clave: str, *, minima: str, maxima: str) -> Decimal:
    numero = _numero(settings, clave)
    if not Decimal(minima) <= numero <= Decimal(maxima):
        raise ValueError(f"setting {clave}: fuera de cota [{minima}, {maxima}]: {numero}")
    return numero


def _entero(settings: Mapping, clave: str, *, minimo: int, maximo: int) -> int:
    numero = _numero(settings, clave)
    if numero != numero.to_integral_value():
        raise ValueError(f"setting {clave}: debe ser entero: {numero}")
    entero = int(numero)
    if not minimo <= entero <= maximo:
        raise ValueError(f"setting {clave}: fuera de cota [{minimo}, {maximo}]: {entero}")
    return entero


def _positivo(settings: Mapping, clave: str) -> Decimal:
    numero = _numero(settings, clave)
    if numero <= 0:
        raise ValueError(f"setting {clave}: debe ser > 0: {numero}")
    return numero


def _rango_fechas(valor: Any, *, clave: str) -> tuple[date, date]:
    if isinstance(valor, Mapping):
        desde_raw, hasta_raw = valor.get("desde"), valor.get("hasta")
    elif isinstance(valor, (list, tuple)) and len(valor) == 2:
        desde_raw, hasta_raw = valor
    else:
        raise ValueError(f"setting {clave}: rango invalido: {valor!r}")
    try:
        desde = date.fromisoformat(desde_raw) if isinstance(desde_raw, str) else None
        hasta = date.fromisoformat(hasta_raw) if isinstance(hasta_raw, str) else None
    except ValueError as exc:
        raise ValueError(f"setting {clave}: fecha ISO invalida: {valor!r}") from exc
    if desde is None or hasta is None or desde > hasta:
        raise ValueError(f"setting {clave}: rango invalido: {valor!r}")
    return (desde, hasta)


def _fechas_excluidas(settings: Mapping, clave: str) -> tuple[tuple[date, date], ...]:
    if clave not in settings or settings[clave] is None:
        raise ValueError(f"config sin {clave}")
    valor = settings[clave]
    if not isinstance(valor, (list, tuple)):
        raise ValueError(f"setting {clave}: debe ser lista de rangos: {valor!r}")
    return tuple(_rango_fechas(rango, clave=clave) for rango in valor)


@dataclass(frozen=True)
class ConfigMotor:
    """Claves `precio_*` que no son de las reglas (REPRICING 02, seccion H).

    Apagador (modos), compuerta (fusible, insumo sistemico, corte), senal de
    trafico, cohorte, ventana de envio y las que hoy leen `banda_desde_settings`,
    `max_dias_desde_settings`, `validar_freno_dias_error` y
    `validar_precio_aviso_dias`. Sin defaults: clave ausente o fuera de cota
    -> `ValueError` que la nombra. Las reglas puras siguen en `ConfigPrecio`
    para no exigirle estas claves al codigo que corre hoy.
    """

    modo_global: str
    modo_universo: Mapping[str, str]
    banda_goal: tuple[Decimal, Decimal]
    freno_dias_error: int
    catalogo_max_dias_sin_reportar: int
    aviso_dias_sin_evaluar: int
    fusible_frac: Decimal
    fusible_min_movimientos: int
    insumo_salto_pct: Decimal
    insumo_salto_frac: Decimal
    corte_errores_consecutivos: int
    senal_trafico_min: int
    cohorte_caida_pct: Decimal
    envio_ventana_dias: int

    def modo_de(self, plataforma: str, canal: str | None) -> str:
        """`min(modo_global, modo_universo[platform/canal])`. Universo sin
        entrada = `off` (la config es valida; el universo esta apagado)."""
        universo = self.modo_universo.get(f"{plataforma}/{canal}", "off")
        return min(self.modo_global, universo, key=lambda modo: _MONEDA_ORDEN[modo])


def _modo(settings: Mapping, clave: str) -> str:
    if clave not in settings or settings[clave] is None:
        raise ValueError(f"config sin {clave}")
    valor = settings[clave]
    if valor not in MODOS:
        raise ValueError(f"setting {clave}: modo invalido (off|shadow|live): {valor!r}")
    return valor


def _modo_universo(settings: Mapping, clave: str) -> dict[str, str]:
    if clave not in settings or settings[clave] is None:
        raise ValueError(f"config sin {clave}")
    valor = settings[clave]
    if not isinstance(valor, Mapping):
        raise ValueError(f"setting {clave}: debe ser objeto plataforma/canal -> modo: {valor!r}")
    resultado: dict[str, str] = {}
    for universo, modo in valor.items():
        if not isinstance(universo, str) or "/" not in universo:
            raise ValueError(f"setting {clave}: universo invalido: {universo!r}")
        if modo not in MODOS:
            raise ValueError(f"setting {clave}: modo invalido para {universo}: {modo!r}")
        resultado[universo] = modo
    return resultado


def leer_config_motor(settings: Mapping) -> ConfigMotor:
    """Valida las claves `precio_*` de la seccion H contra sus cotas."""
    banda = (
        _fraccion(settings, "precio_goal_min_pct", minima="0", maxima="1"),
        _fraccion(settings, "precio_goal_max_pct", minima="0", maxima="1"),
    )
    if not Decimal(0) < banda[0] < banda[1] < Decimal(1):
        raise ValueError(f"config precio_goal incoherente: exige 0 < min < max < 1: {banda}")
    return ConfigMotor(
        modo_global=_modo(settings, "precio_modo_global"),
        modo_universo=_modo_universo(settings, "precio_modo_universo"),
        banda_goal=banda,
        freno_dias_error=_entero(settings, "precio_freno_dias_error", minimo=1, maximo=14),
        catalogo_max_dias_sin_reportar=_entero(
            settings, "precio_catalogo_max_dias_sin_reportar", minimo=1, maximo=14
        ),
        aviso_dias_sin_evaluar=_entero(
            settings, "precio_aviso_dias_sin_evaluar", minimo=1, maximo=14
        ),
        fusible_frac=_fraccion(settings, "precio_fusible_frac", minima="0.10", maxima="0.90"),
        fusible_min_movimientos=_entero(
            settings, "precio_fusible_min_movimientos", minimo=1, maximo=100
        ),
        insumo_salto_pct=_fraccion(
            settings, "precio_insumo_salto_pct", minima="0.01", maxima="0.50"
        ),
        insumo_salto_frac=_fraccion(
            settings, "precio_insumo_salto_frac", minima="0.05", maxima="0.90"
        ),
        corte_errores_consecutivos=_entero(
            settings, "precio_corte_errores_consecutivos", minimo=2, maximo=20
        ),
        senal_trafico_min=_entero(settings, "precio_senal_trafico_min", minimo=20, maximo=5000),
        cohorte_caida_pct=_fraccion(
            settings, "precio_cohorte_caida_pct", minima="0.05", maxima="0.90"
        ),
        envio_ventana_dias=_entero(settings, "precio_envio_ventana_dias", minimo=30, maximo=365),
    )


def leer_config(settings: Mapping) -> ConfigPrecio:
    """Valida las claves `precio_*` de las reglas contra sus cotas del plan."""
    escalon = _fraccion(settings, "precio_escalon_max_pct", minima="0.01", maxima="0.25")
    minimo = _fraccion(settings, "precio_movimiento_min_pct", minima="0", maxima="0.10")
    if escalon < minimo:
        raise ValueError(
            "setting precio_escalon_max_pct por debajo de precio_movimiento_min_pct: "
            f"{escalon} < {minimo} (el escalón aplicado quedaría bajo el mínimo y "
            "consumiría cooldown y cuota)"
        )
    return ConfigPrecio(
        caida_ventas_pct=_fraccion(
            settings, "precio_caida_ventas_pct", minima="0.10", maxima="0.90"
        ),
        senal_dias=_entero(settings, "precio_senal_dias", minimo=1, maximo=7),
        u60_min=_entero(settings, "precio_u60_min", minimo=1, maximo=1000),
        fechas_excluidas=_fechas_excluidas(settings, "precio_fechas_excluidas"),
        escalon_max_pct=escalon,
        movimiento_min_pct=minimo,
        movimiento_min_abs_mxn=_positivo(settings, "precio_movimiento_min_abs_mxn"),
        movimiento_min_abs_usd=_positivo(settings, "precio_movimiento_min_abs_usd"),
        tolerancia=_fraccion(settings, "precio_tolerancia", minima="0", maxima="0.05"),
        dias_entre_cambios=_entero(settings, "precio_dias_entre_cambios", minimo=1, maximo=30),
        freno_cambios=_entero(settings, "precio_freno_cambios", minimo=2, maximo=10),
        divergencia_max_pct=_fraccion(
            settings, "precio_divergencia_max_pct", minima="0", maxima="0.10"
        ),
    )
