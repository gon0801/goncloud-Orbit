"""Pruebas del verificador read-only `tools/pausa_economica_verifica.py`.

Cubren el replay COMPLETO paginado (sin tope implicito), la marca de parcial
con `--limit` y la compuerta de madurez `+10d` del desenlace real.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

from test_pausa_economica_io import _entidad, _evaluacion, _pendiente, _siembra_tx3

from app import pausa_economica_io as pio
from app.cycle_grupos import _Contadores


def _tool():
    ruta = Path(__file__).resolve().parents[1] / "tools" / "pausa_economica_verifica.py"
    spec = importlib.util.spec_from_file_location("pausa_economica_verifica", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_madurez_disponible_exige_diez_dias_sobre_la_ultima_fecha():
    """Contar 10 filas no es madurar: la ultima fecha del seguimiento (+10d)
    necesita `DIAS_MADUREZ_CORTES` dias antes de juzgar el ACoS."""
    mod = _tool()
    hasta = dt.date(2026, 11, 10)
    madura_el = dt.date(2026, 11, 30)  # fin 11-20 + 10 dias de madurez
    assert mod._madurez_disponible(hasta, madura_el - dt.timedelta(days=1)) is False
    assert mod._madurez_disponible(hasta, madura_el) is True
    assert mod._madurez_disponible(hasta, madura_el + dt.timedelta(days=1)) is True


def test_todas_recorre_todas_las_filas_y_marca_el_limit_como_parcial(db_template):
    """El replay por defecto NO se corta en 1000: recorre todo por keyset y
    solo `--limit` marca la salida como parcial."""
    mod = _tool()
    conn, _dsn = db_template
    conn.autocommit = True
    config, ciclo, hoja, decision = _siembra_tx3(conn)
    for indice in range(3):
        entidad = hoja if indice == 0 else _entidad(conn, f"p03-tool-{indice}")
        contadores = _Contadores()
        contadores.pausa_economica_evals.append(_evaluacion(entidad))
        if indice == 0:
            pio.tx3_pausa_economica(
                conn, ciclo, config, contadores, [_pendiente(hoja)], [decision], {}
            )
        else:
            pio.tx3_pausa_economica(conn, ciclo, config, contadores, [], [], {})

    filas, parcial = mod._todas(conn, mod._SQL_EVALUACIONES, "amazon_us", None)
    assert len(filas) == 3
    assert parcial is False

    filas, parcial = mod._todas(conn, mod._SQL_EVALUACIONES, "amazon_us", 2)
    assert len(filas) == 2
    assert parcial is True

    # Un limite que cubre exactamente lo que hay no es parcial.
    filas, parcial = mod._todas(conn, mod._SQL_EVALUACIONES, "amazon_us", 3)
    assert len(filas) == 3
    assert parcial is False
