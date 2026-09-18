"""Cupo diario del motor de precios (REPRICING 01 A.5, S4 #12).

El motor es `precio:<platform>` en `apply_quota_state` (la 0039 lo mapea
en `apply_cap_de_config`; las cuotas de Ads no se tocan). Consume cada
intento reservado (K1, r4): `reservar` cobra la unidad ANTES de
`cambiar_precio`, y un `saltado` sin fila (`precio_vivo_distinto`,
`sin_precio_vivo`, `listing_con_cambio_abierto`) no la devuelve. **No se
devuelve**: el trigger `apply_quota_used_creciente` de la 0002 sella que
`used` jamas decrece (descontar un consumo reescribiria la historia del
dia y el cap dejaria de ser un tope). `used` es monotono por la 0002, y
por eso `used` puede ser mayor que las filas reales de `precio_cambio`
del dia. Las reversas del dia las corre el dueno fuera de la corrida y se
descuentan por consulta. El virtual de sombra (`aplicado = false`) no
consume.

La reserva es atomica con el patron copiado de `app/apply.py` (NO se
importa: `app.precio` no puede importar `app.apply`): INSERT + ON
CONFLICT con la cota en el WHERE, jamas SELECT-luego-UPDATE. El `cap` de
la fila lo fija el trigger desde la config (`precio_cap_<platform>`);
aqui se valida igual (entero 0-20, `ValueError` que nombra la clave) para
fallar en voz alta al arrancar, antes del lock.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import psycopg

__all__ = [
    "motor_cuota",
    "validar_cap",
    "reversas_hoy",
    "reservar",
]


def motor_cuota(platform: str) -> str:
    """Clave del motor en `apply_quota_state`."""
    return f"precio:{platform}"


def _numero(settings: Mapping, clave: str) -> Decimal:
    """Replica de `app/precio/config.py::_numero` (duplicacion deliberada:
    `config.py` no se toca y `_numero` es privado)."""
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


def validar_cap(settings: Mapping, platform: str) -> int:
    """`precio_cap_<platform>` entero 0-20; falta o fuera de cota = `ValueError`."""
    clave = f"precio_cap_{platform}"
    numero = _numero(settings, clave)
    if numero != numero.to_integral_value():
        raise ValueError(f"setting {clave}: debe ser entero: {numero}")
    cap = int(numero)
    if not 0 <= cap <= 20:
        raise ValueError(f"setting {clave}: fuera de cota [0, 20]: {cap}")
    return cap


def reversas_hoy(conn: psycopg.Connection, platform: str, hoy) -> int:
    """Reversas aplicadas del dia (S4 #12 tambien consumen; las corre el
    dueno, asi que no pasan por la reserva atomica: se descuentan)."""
    return conn.execute(
        "SELECT count(*) FROM precio_cambio"
        " WHERE platform = %s AND es_reversa"
        " AND (enviado_at AT TIME ZONE 'UTC')::date = %s",
        (platform, hoy),
    ).fetchone()[0]


def reservar(conn: psycopg.Connection, *, platform: str, cap: int, extra: int = 0) -> bool:
    """Cobra UNA unidad del cupo del dia, atomicamente.

    `extra` = unidades consumidas fuera de la reserva (reversas del dia):
    la guarda es `used + extra < cap`. El trigger fija el `cap` de la fila
    desde la config; el `cap` que se pasa debe ser el validado (si difiere,
    el trigger revienta: el tope se sube con config, nunca aqui).
    """
    if cap - extra <= 0:
        # El INSERT nace con used=1 sin WHERE: con cupo agotado no se
        # inserta (el ON CONFLICT si trae guarda, el INSERT no).
        return False
    fila = conn.execute(
        "INSERT INTO apply_quota_state (motor, quota_date, cap, used)"
        " VALUES (%s, (now() AT TIME ZONE 'UTC')::date, %s, 1)"
        " ON CONFLICT (motor, quota_date) DO UPDATE"
        " SET used = apply_quota_state.used + 1"
        " WHERE apply_quota_state.used + %s < apply_quota_state.cap"
        " RETURNING used, cap",
        (motor_cuota(platform), cap, extra),
    ).fetchone()
    return fila is not None
