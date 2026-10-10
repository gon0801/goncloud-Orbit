"""Guardas de arquitectura de la letra M (BIDS 02 M.1; las demas tareas M agregan aqui).

caso.py y politica.py son puros: sin reloj, entorno ni azar. Los numeros que
espejan a otro modulo llevan su pin de igualdad. Sin acentos en el codigo.
"""

from __future__ import annotations

import ast
from pathlib import Path

from test_architecture import _imports_runtime, _usos_reloj

RAIZ = Path(__file__).resolve().parents[1]
MODULOS = [RAIZ / "app" / "optimizer" / "caso.py", RAIZ / "app" / "optimizer" / "politica.py"]


def _fugas_reloj(modulos):
    return {
        p.name: fugas
        for p in modulos
        if (fugas := _usos_reloj(ast.parse(p.read_text(encoding="utf-8"))))
    }


def test_caso_y_politica_sin_reloj_ni_entorno():
    assert not _fugas_reloj(MODULOS)


def test_detector_caza_datetime_now_sembrado():
    assert _usos_reloj(ast.parse("import datetime as dt\nx = dt.datetime.now()\n"))
    assert _usos_reloj(ast.parse("import datetime\nx = datetime.date.today()\n"))
    assert not _usos_reloj(
        ast.parse(
            "import datetime as dt\n"
            "x = dt.date(2026, 10, 9)\n"
            "y = dt.date.fromisoformat('2026-10-09')\n"
        )
    )


def test_caso_y_politica_sin_azar_ni_entorno_en_imports():
    prohibidos = ("random", "time", "os", "os.path")
    for modulo in MODULOS:
        fugas = [i for i in _imports_runtime(modulo) if i in prohibidos]
        assert not fugas, f"{modulo.name} importa {fugas}"


def test_espejos_de_bid_pineados():
    from app.optimizer import bid as b
    from app.optimizer import politica as p

    assert p.MULT_BAJA_FUERTE == b.MULT_BAJA_FUERTE
    assert p.MULT_BAJA_SUAVE == b.MULT_BAJA_SUAVE
    assert p.MULT_SUBIDA == b.MULT_SUBIDA
    assert p.PASO_BAJA_FUERTE == b.FACTOR_BAJA_FUERTE
    assert p.PASO_BAJA_SUAVE == b.FACTOR_BAJA_SUAVE
    assert p.PASO_SUBIDA == b.FACTOR_SUBIDA
    assert p.CLAMP_FACTOR_MIN == b.CLAMP_FACTOR_MIN
    assert p.CLAMP_FACTOR_MAX == b.CLAMP_FACTOR_MAX
    assert p.MIN_DELTA_ABSOLUTO == b.MIN_DELTA_ABSOLUTO
    assert p.MOTIVO_RANGO_BLOQUEA_AJUSTE == b.MOTIVO_RANGO_BLOQUEA_AJUSTE
    assert p.MOTIVO_DELTA_BAJO_UMBRAL == b.MOTIVO_DELTA_BAJO_UMBRAL


def test_espejos_de_windows_goals_evidencia_pineados():
    from app.optimizer import caso as c
    from app.optimizer import evidencia as ev
    from app.optimizer import goals as g
    from app.optimizer import politica as p
    from app.optimizer import windows as w

    assert c.DIAS_MADUREZ == w.DIAS_MADUREZ_CORTES
    assert c.DIAS_LOOKBACK == w.LOOKBACK_EVIDENCIA
    assert g.COOLDOWN.days == p.DIAS_EFECTO
    assert p.CLICS_NUEVOS == ev.MIN_CLICS_CPC
    assert p.PRECISION == ev._PRECISION


def test_politica_importa_gamma_y_pausa_en_vez_de_copiar():
    import app.optimizer.politica as p
    from app.optimizer import bid as b
    from app.optimizer import evidencia as ev

    assert p.gamma_p is ev.gamma_p
    assert p.gamma_q is ev.gamma_q
    assert p._decide_pause is b._decide_pause
