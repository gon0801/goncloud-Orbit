"""Formato de cifras y horas para las pantallas (CLARIDAD 01, H.3).

Un solo lugar decide como se ve un numero: dinero con miles y 2 decimales,
porcentajes con 2 decimales, enteros con miles y horas del centro de Mexico.
Nada aqui calcula; solo presenta. None queda en raya y lo que no parsea se
deja igual (la evidencia no se pierde). Nunca se usa dentro de `value=`,
`data-` ni campos de formulario: el dato que se guarda viaja crudo.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

ZONA_PANTALLA = ZoneInfo("America/Mexico_City")
LEYENDA_ZONA = "Horas del centro de México"
_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_RAYA = "—"


def _decimal(valor) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        dec = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return dec if dec.is_finite() else None


def dinero(valor) -> str:
    """«1234.5» -> «1,234.50»."""
    if valor is None:
        return _RAYA
    dec = _decimal(valor)
    return str(valor) if dec is None else f"{dec:,.2f}"


def pct(valor) -> str:
    """Valor que ya viene en porcentaje: «19.3661» -> «19.37 %»."""
    if valor is None:
        return _RAYA
    dec = _decimal(valor)
    return str(valor) if dec is None else f"{dec:,.2f} %"


def numero(valor, decimales: int = 0) -> str:
    """Cifra con miles: «424436» -> «424,436»; con decimales, «1234.5» -> «1,234.50»."""
    if valor is None:
        return _RAYA
    dec = _decimal(valor)
    if dec is None:
        return str(valor)
    if decimales == 0:
        return f"{round(dec):,}"
    return f"{dec:,.{decimales}f}"


def momento_local(valor) -> dt.datetime | None:
    """Instante (datetime o ISO) en la hora del centro de Mexico. Un instante
    sin zona viene de la base en UTC. Una fecha sola no es un instante: None."""
    if isinstance(valor, str):
        if len(valor.strip()) <= 10:
            return None
        try:
            valor = dt.datetime.fromisoformat(valor)
        except ValueError:
            return None
    if not isinstance(valor, dt.datetime):
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=dt.UTC)
    return valor.astimezone(ZONA_PANTALLA)


def fecha_hora(valor) -> str:
    """«2026-11-08T17:58:03+00:00» -> «8 nov, 11:58» (centro de Mexico)."""
    if valor is None:
        return _RAYA
    momento = momento_local(valor)
    if momento is None:
        return str(valor)
    return f"{momento.day} {_MESES[momento.month - 1]}, {momento.hour:02d}:{momento.minute:02d}"


def fecha_dia(valor) -> str:
    """Dia visible de un instante: «2026-11-08T17:58:03+00:00» -> «8 nov»."""
    if valor is None:
        return _RAYA
    momento = momento_local(valor)
    if momento is None:
        return str(valor)
    return f"{momento.day} {_MESES[momento.month - 1]}"


def hora(valor) -> str:
    """Hora visible de un instante: «2026-11-08T17:58:03+00:00» -> «11:58»."""
    if valor is None:
        return _RAYA
    momento = momento_local(valor)
    if momento is None:
        return str(valor)
    return f"{momento.hour:02d}:{momento.minute:02d}"


FILTROS = {
    "dinero": dinero,
    "pct": pct,
    "numero": numero,
    "fecha_hora": fecha_hora,
    "fecha_dia": fecha_dia,
    "hora": hora,
}
