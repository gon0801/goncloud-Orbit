"""D.0 (REPRICING 01): la `config_version` vigente trae las 20 claves bien.

Lee de stdin el JSON de `settings` de la vigente (el que `correr.sh` saca como
lector) y comprueba, con el código de `origin/master` (el que se va a
desplegar), que:

- las 20 claves `precio_*` están con el valor inicial de la tabla de umbrales
  del plan v1.3, y ninguna `precio_envio_*` (esas las siembra E.3);
- `leer_config` (reglas), `banda_desde_settings` (goals, A.1) y
  `max_dias_desde_settings` (cobertura, A.7) no levantan `ValueError`;
- tampoco lo que la corrida (A.5) y la pantalla (A.6) validan al arrancar:
  `validar_cap` de las plataformas de `PLATAFORMAS_MONEDA` (`/precios` las
  lee todas y es fail-closed global) y de las tres de los caps sembrados (la
  corrida acepta `--platform` de cualquiera), `validar_freno_dias_error` y
  `validar_precio_aviso_dias`.

Imprime `CONFIG-OK` y sale 0, o una línea por falla y sale 1. `CLAVES` es la
lista literal de la fila D.0; `tests/test_precio_d0.py` comprueba que
`siembra.sql` siembra exactamente esto.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[4]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.notifica import validar_precio_aviso_dias  # noqa: E402
from app.optimizer.bid import PLATAFORMAS_MONEDA  # noqa: E402
from app.precio.config import leer_config  # noqa: E402
from app.precio.corrida import validar_freno_dias_error  # noqa: E402
from app.precio.cuota import validar_cap  # noqa: E402
from app.precio.fuentes import max_dias_desde_settings  # noqa: E402
from app.precio.goals_write import banda_desde_settings  # noqa: E402

CLAVES: dict[str, object] = {
    "precio_caida_ventas_pct": Decimal("0.40"),
    "precio_senal_dias": 3,
    "precio_u60_min": 20,
    "precio_fechas_excluidas": [],
    "precio_escalon_max_pct": Decimal("0.10"),
    "precio_movimiento_min_pct": Decimal("0.01"),
    "precio_movimiento_min_abs_mxn": Decimal("1.00"),
    "precio_movimiento_min_abs_usd": Decimal("0.10"),
    "precio_tolerancia": Decimal("0.005"),
    "precio_dias_entre_cambios": 7,
    "precio_cap_amazon_mx": 5,
    "precio_cap_amazon_us": 5,
    "precio_cap_meli": 5,
    "precio_goal_min_pct": Decimal("0.10"),
    "precio_goal_max_pct": Decimal("0.60"),
    "precio_freno_cambios": 3,
    "precio_aviso_dias_sin_evaluar": 3,
    "precio_freno_dias_error": 3,
    "precio_divergencia_max_pct": Decimal("0.01"),
    "precio_catalogo_max_dias_sin_reportar": 3,
}


def _igual(esperado: object, valor: object) -> bool:
    if isinstance(esperado, list):
        return valor == esperado
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str, Decimal)):
        return False
    try:
        return Decimal(str(valor)) == Decimal(str(esperado))
    except InvalidOperation:  # GLM r1 (#317): «tres» es una línea de falla, no un traceback
        return False


def _plataformas_con_cap() -> list[str]:
    """Las que `/precios` lee (`PLATAFORMAS_MONEDA`) más las de los caps que
    se siembran (la corrida acepta `--platform` de las tres)."""
    sembradas = {c.removeprefix("precio_cap_") for c in CLAVES if c.startswith("precio_cap_")}
    return sorted(set(PLATAFORMAS_MONEDA) | sembradas)


def fallas(settings: dict) -> list[str]:
    """Una línea por cada cosa que no cuadra; lista vacía = todo bien."""
    errores: list[str] = []
    for clave, esperado in CLAVES.items():
        if clave not in settings:
            errores.append(f"falta {clave}")
        elif not _igual(esperado, settings[clave]):
            errores.append(f"{clave} = {settings[clave]!r}, esperado {esperado}")
    envio = sorted(k for k in settings if k.startswith("precio_envio_"))
    if envio:
        errores.append(f"claves de envío sembradas antes de E.3: {envio}")
    sobrantes = sorted(k for k in settings if k.startswith("precio_") and k not in CLAVES)
    if sobrantes:
        errores.append(f"claves precio_* fuera de la tabla del plan: {sobrantes}")
    for nombre, lector in (
        ("leer_config", leer_config),
        ("banda_desde_settings", banda_desde_settings),
        ("max_dias_desde_settings", max_dias_desde_settings),
        *((f"validar_cap({p})", lambda s, p=p: validar_cap(s, p)) for p in _plataformas_con_cap()),
        ("validar_freno_dias_error", validar_freno_dias_error),
        ("validar_precio_aviso_dias", validar_precio_aviso_dias),
    ):
        try:
            lector(settings)
        except ValueError as exc:
            errores.append(f"{nombre}: {exc}")
    return errores


def main() -> int:
    settings = json.loads(sys.stdin.read())
    errores = fallas(settings)
    for linea in errores:
        print(f"FALLA {linea}")
    if errores:
        return 1
    print(f"CONFIG-OK {len(CLAVES)} claves precio_*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
