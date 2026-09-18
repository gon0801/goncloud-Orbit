"""Unico escritor de `precio_goal` (REPRICING 01 A.1, fila A.1 del plan).

Un camino (regla 1): sembrar (`sembrar_goal`, INSERT) y cerrar
(`cerrar_goal`, UPDATE de `valid_to` y nada mas) viven SOLO aqui;
`tools/precio_goal.py` despacha, jamas duplica SQL (candado
`_IDENT_PRECIO_GOAL` en `tests/test_architecture.py`). Solo `app_admin`
(GRANTs de la 0039); cero Amazon: este modulo no cotiza ni escribe precios.

El goal se recibe en POR CIENTO con dos decimales (`30.00`) y se guarda
como fraccion (`0.3000`); un valor en fraccion (`0.30`) se rechaza con
mensaje que dice que el argumento va en por ciento (Reject del plan:
goal como fraccion, defaults de goal).

La banda se lee de la `config_version` vigente
(`precio_goal_min_pct`/`precio_goal_max_pct`) con la FORMA de `_fraccion`
de `app/precio/config.py` (copiada aqui, no importada: `leer_config` no
conoce esas claves y A.1 no puede editar `config.py`); sin las claves se
aborta con `config sin precio_goal_min_pct`, nunca con un default. La
coherencia `0 < min < max < 1` replica el CHECK de la base.

Todo dinero y fracciones en `Decimal`, cero `float` (regla 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
import psycopg.errors

__all__ = [
    "PrecioGoalInvalido",
    "PrecioGoalAusente",
    "MODOS_PRECIO_GOAL",
    "PLATAFORMAS_PRECIO_GOAL",
    "fraccion_desde_porcentaje",
    "banda_desde_settings",
    "config_vigente_settings",
    "sembrar_goal",
    "cerrar_goal",
]


class PrecioGoalInvalido(ValueError):
    """Uso invalido del escritor de goals (exit 2 en el tool)."""


class PrecioGoalAusente(LookupError):
    """La fila que se pide no existe (exit 1 en el tool)."""


MODOS_PRECIO_GOAL = ("shadow", "live")
PLATAFORMAS_PRECIO_GOAL = ("amazon_mx", "amazon_us", "meli")


def fraccion_desde_porcentaje(texto: str) -> Decimal:
    """`30.00` -> `Decimal("0.3000")`; `0.30` se rechaza (va en por ciento)."""
    try:
        valor = Decimal(str(texto).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise PrecioGoalInvalido(
            f"--goal-pct no numerico: {texto!r} (el argumento va en por ciento, ej. 30.00)"
        ) from exc
    if not valor.is_finite():
        raise PrecioGoalInvalido(
            f"--goal-pct no finito: {texto!r} (el argumento va en por ciento, ej. 30.00)"
        )
    if valor <= 0:
        raise PrecioGoalInvalido(
            f"--goal-pct no positivo: {texto!r} (el argumento va en por ciento, ej. 30.00)"
        )
    if valor < 1:
        raise PrecioGoalInvalido(
            f"--goal-pct {texto!r} parece fraccion: el argumento va en por ciento (30.00 para 30 %)"
        )
    if valor.as_tuple().exponent < -2:
        raise PrecioGoalInvalido(f"--goal-pct {texto!r}: maximo dos decimales")
    return (valor / 100).quantize(Decimal("0.0001"))


def _numero(settings: Mapping, clave: str) -> Decimal:
    """Copia de `app/precio/config.py::_numero` (A.1 no edita `config.py`)."""
    if clave not in settings or settings[clave] is None:
        raise PrecioGoalInvalido(f"config sin {clave}")
    valor = settings[clave]
    if isinstance(valor, bool):
        raise PrecioGoalInvalido(f"setting {clave}: valor no numerico: {valor!r}")
    try:
        numero = Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))
    except (InvalidOperation, ValueError) as exc:
        raise PrecioGoalInvalido(f"setting {clave}: valor no numerico: {valor!r}") from exc
    if not numero.is_finite():
        raise PrecioGoalInvalido(f"setting {clave}: valor no finito: {valor!r}")
    return numero


def _fraccion(settings: Mapping, clave: str, *, minima: str, maxima: str) -> Decimal:
    """Copia de la forma de `app/precio/config.py::_fraccion` (l.57-61)."""
    numero = _numero(settings, clave)
    if not Decimal(minima) <= numero <= Decimal(maxima):
        raise PrecioGoalInvalido(f"setting {clave}: fuera de cota [{minima}, {maxima}]: {numero}")
    return numero


def banda_desde_settings(settings: Mapping) -> tuple[Decimal, Decimal]:
    """`(min, max)` de la config vigente; sin claves o incoherente = aborta.

    Replica el CHECK de la base (`0 < min < max < 1`, cota del plan
    `precio_goal_min/max_pct`): ningun default en codigo (Reject del plan).
    """
    minimo = _fraccion(settings, "precio_goal_min_pct", minima="0", maxima="1")
    maximo = _fraccion(settings, "precio_goal_max_pct", minima="0", maxima="1")
    if not Decimal(0) < minimo < maximo < Decimal(1):
        raise PrecioGoalInvalido(
            f"config precio_goal incoherente: exige 0 < min < max < 1 (min={minimo}, max={maximo})"
        )
    return (minimo, maximo)


def config_vigente_settings(conn: psycopg.Connection) -> dict[str, Any]:
    """Settings de la `config_version` vigente; sin fila o sin claves = aborta."""
    fila = conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()
    if fila is None:
        raise PrecioGoalInvalido("config sin precio_goal_min_pct: no hay config_version vigente")
    return fila[0]


def _validar_fila(
    *,
    goal_pct: str,
    mode: str,
    go_literal: str | None,
    settings: Mapping,
) -> Decimal:
    if mode not in MODOS_PRECIO_GOAL:
        raise PrecioGoalInvalido(f"mode invalido: {mode!r} (vocabulario: shadow|live)")
    fraccion = fraccion_desde_porcentaje(goal_pct)
    minimo, maximo = banda_desde_settings(settings)
    if not minimo <= fraccion <= maximo:
        raise PrecioGoalInvalido(
            f"goal {goal_pct} % (= {fraccion}) fuera de banda [{minimo}, {maximo}]"
        )
    if mode == "live":
        if go_literal is None or not go_literal.strip():
            raise PrecioGoalInvalido(
                "live sin go: subir a live exige go literal no vacio"
                " (CHECK precio_goal_live_exige_go)"
            )
    elif go_literal is not None:
        raise PrecioGoalInvalido(
            "shadow es sin go literal: el go solo aplica a live (CHECK precio_goal_live_exige_go)"
        )
    return fraccion


def sembrar_goal(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    platform: str,
    goal_pct: str,
    mode: str,
    go_literal: str | None,
    creado_por: str = "precio_goal",
) -> int:
    """INSERTa el goal vigente; devuelve su id. Falla cerrado y en voz alta."""
    if platform not in PLATAFORMAS_PRECIO_GOAL:
        raise PrecioGoalInvalido(
            f"platform invalida: {platform!r} (vocabulario: amazon_mx|amazon_us|meli)"
        )
    fraccion = _validar_fila(
        goal_pct=goal_pct,
        mode=mode,
        go_literal=go_literal,
        settings=config_vigente_settings(conn),
    )
    vigente = conn.execute(
        "SELECT id FROM precio_goal WHERE listing_id = %s AND platform = %s AND valid_to IS NULL",
        (listing_id, platform),
    ).fetchone()
    if vigente is not None:
        raise PrecioGoalInvalido(
            f"ya hay goal vigente para listing {listing_id} en {platform}:"
            " cierralo con --cerrar antes de sembrar otro"
        )
    try:
        fila = conn.execute(
            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
            " valid_from, creado_por, go_literal)"
            " VALUES (%s, %s, %s, %s, (now() AT TIME ZONE 'UTC')::date, %s, %s)"
            " RETURNING id",
            (listing_id, platform, fraccion, mode, creado_por, go_literal),
        ).fetchone()
    except (
        psycopg.errors.UniqueViolation,
        psycopg.errors.ExclusionViolation,
    ) as exc:
        nombre = exc.diag.constraint_name if exc.diag else ""
        if nombre == "precio_goal_unico_por_fecha":
            raise PrecioGoalInvalido(
                f"ese listing ya tuvo un goal que empezo hoy (UTC): el siguiente"
                f" entra al dia siguiente UTC (listing {listing_id} en {platform})"
            ) from exc
        raise PrecioGoalInvalido(
            f"ya hay goal vigente para listing {listing_id} en {platform}:"
            " cierralo con --cerrar antes de sembrar otro"
        ) from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        raise PrecioGoalAusente(
            f"listing {listing_id} inexistente en {platform}: sin listing no hay goal"
        ) from exc
    except psycopg.errors.CheckViolation as exc:
        nombre = exc.diag.constraint_name if exc.diag else "?"
        raise PrecioGoalInvalido(
            f"la base rechazo el goal ({nombre}): revisa banda y go literal"
        ) from exc
    assert fila is not None
    return int(fila[0])


def cerrar_goal(conn: psycopg.Connection, *, listing_id: int, platform: str) -> int:
    """Fija `valid_to` del goal vigente (la unica mutacion que el trigger
    admite); devuelve su id. Sin vigente = `PrecioGoalAusente`."""
    if platform not in PLATAFORMAS_PRECIO_GOAL:
        raise PrecioGoalInvalido(
            f"platform invalida: {platform!r} (vocabulario: amazon_mx|amazon_us|meli)"
        )
    fila = conn.execute(
        "SELECT id FROM precio_goal WHERE listing_id = %s AND platform = %s AND valid_to IS NULL",
        (listing_id, platform),
    ).fetchone()
    if fila is None:
        raise PrecioGoalAusente(
            f"sin goal vigente para listing {listing_id} en {platform}: nada que cerrar"
        )
    conn.execute(
        "UPDATE precio_goal SET valid_to = (now() AT TIME ZONE 'UTC')::date WHERE id = %s",
        (fila[0],),
    )
    return int(fila[0])
