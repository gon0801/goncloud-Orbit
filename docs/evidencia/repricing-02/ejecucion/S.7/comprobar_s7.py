#!/usr/bin/env python3
"""Comprueba S.7 sin base: senal por unidad, trafico, cohorte y lineal."""

import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))

import test_precio_reglas as fx  # noqa: E402

from app.precio.reglas import decidir  # noqa: E402
from app.precio.ventas import evaluar_cohorte, evaluar_senal  # noqa: E402

HOY = fx.HOY

s = evaluar_senal(
    fx.historia_sana(HOY, qty15=6),
    hoy=HOY,
    config=fx.cfg(),
    racha_previa=0,
    ultima_subida=None,
    trafico_min=100,
)
print(f"unidades: estado={s.estado} evidencia={s.evidencia} u15={s.u15} u60={s.u60}")
assert (s.estado, s.evidencia, s.u15, s.u60) == ("no_perdiendo", "unidades", 90, 600)

trafico = fx.trafico_ventana(HOY, 11, 38, 200, 20)
trafico.update(fx.trafico_ventana(HOY, 1, 9, 200, 8))
t = evaluar_senal(
    fx.historia_sin_unidades(HOY, trafico=trafico),
    hoy=HOY,
    config=fx.cfg(),
    racha_previa=1,
    ultima_subida=HOY - timedelta(days=10),
    trafico_min=100,
)
print(f"trafico: estado={t.estado} evidencia={t.evidencia} racha={t.racha}")
assert (t.estado, t.evidencia) == ("perdiendo", "trafico")

h = fx.historia_sana(HOY, sin_atribuir=3)
v = evaluar_senal(h, hoy=HOY, config=fx.cfg(), racha_previa=0, ultima_subida=None, trafico_min=100)
print(f"sin atribuir: estado={v.estado} submotivo={v.submotivo}")
assert (v.estado, v.submotivo) == ("sin_dato", "venta_sin_publicacion")

lec = evaluar_cohorte(
    (fx.historia_cohorte(HOY, 1, 10),),
    (fx.historia_cohorte(HOY, 10, 10),),
    hoy=HOY,
    min_unidades=20,
    caida_pct=fx.Decimal("0.25"),
)
print(f"cohorte: subidos={lec.subidos} control={lec.control} alarma={lec.alarma}")
assert lec.alarma is True

ent = fx.entrada(costo="60")
d = decidir(
    ent,
    hoy=HOY,
    config=fx.cfg(),
    cotizaciones=(),
    fees_escenario=fx.fees_escenario_de(ent),
    lineal=True,
)
print(f"lineal: resultado={d.resultado} verificacion={d.verificacion} p={d.p_objetivo.valor}")
assert (d.resultado, d.verificacion) == ("subir", "lineal")
print("OK: senal por unidad, trafico, cohorte y lineal")
