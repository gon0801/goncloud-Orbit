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

__all__ = ["ConfigPrecio", "leer_config"]


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


def leer_config(settings: Mapping) -> ConfigPrecio:
    """Valida las claves `precio_*` de las reglas contra sus cotas del plan."""
    return ConfigPrecio(
        caida_ventas_pct=_fraccion(
            settings, "precio_caida_ventas_pct", minima="0.10", maxima="0.90"
        ),
        senal_dias=_entero(settings, "precio_senal_dias", minimo=1, maximo=7),
        u60_min=_entero(settings, "precio_u60_min", minimo=1, maximo=1000),
        fechas_excluidas=_fechas_excluidas(settings, "precio_fechas_excluidas"),
        escalon_max_pct=_fraccion(settings, "precio_escalon_max_pct", minima="0.01", maxima="0.25"),
        movimiento_min_pct=_fraccion(
            settings, "precio_movimiento_min_pct", minima="0", maxima="0.10"
        ),
        movimiento_min_abs_mxn=_positivo(settings, "precio_movimiento_min_abs_mxn"),
        movimiento_min_abs_usd=_positivo(settings, "precio_movimiento_min_abs_usd"),
        tolerancia=_fraccion(settings, "precio_tolerancia", minima="0", maxima="0.05"),
        dias_entre_cambios=_entero(settings, "precio_dias_entre_cambios", minimo=1, maximo=30),
        freno_cambios=_entero(settings, "precio_freno_cambios", minimo=2, maximo=10),
        divergencia_max_pct=_fraccion(
            settings, "precio_divergencia_max_pct", minima="0", maxima="0.10"
        ),
    )
