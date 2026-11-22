#!/usr/bin/env python3
"""TEMPORADA 01 H.1: rejuega el `caso_sombra` de un ciclo con el codigo del repo.

Uso (sobre el `notes.temporada` del ciclo, sin tocar produccion):

    echo "select notes::jsonb->'temporada' from optimizer_cycle where id = 207" \\
      | bash docs/evidencia/arreglos-01/lee_prod.sh > /tmp/temporada-207.json
    python docs/evidencia/temporada-01/H/replay_sombra.py /tmp/temporada-207.json

Para cada diferencia imprime: veredicto real, veredicto de la sombra, el replay
del caso_sombra con `decide`, el m* de R6r y el gasto extra proyectado.

m* se captura del propio `multiplicador_sostenible` de R6r, envolviendolo: no se
reimplementa la cadena de R6r ni se reescribe la formula. Si R6r no llega a
llamarlo (el techo no deja hueco para saltar), la columna sale vacia y el
documento lo dice.

`m_estrella_sin_tope` es el mismo m* cuando el techo del caso no deja hueco: se
pide con las mismas funciones privadas que usa `_juzga_vendedora`, en el mismo
orden (`_precio_de_hoy`, `_fondo_juicio`, `_previa`, `_proyeccion_subida`), y la
misma `multiplicador_sostenible`. Es informativo: dice cuanto sostenia la
evidencia, no cuanto autorizaba el tope.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from decimal import Decimal, localcontext

from app.optimizer import recuperacion, temporada
from app.optimizer.caso import CasoHoja
from app.optimizer.evidencia import gamma_q
from app.optimizer.politica import (
    _CIEN,
    MULT_SUBIDA,
    _fondo_juicio,
    _precio_de_hoy,
    _previa,
    _proyeccion_subida,
    decide,
)
from app.optimizer.recuperacion import multiplicador_sostenible as _sostenible


def _caso_del_juicio(caso: CasoHoja) -> CasoHoja:
    """La misma sustitucion de meta que `decide` hace antes de juzgar."""
    return replace(
        caso,
        economia=replace(
            caso.economia,
            target_acos_pct=temporada.meta_del_juicio(caso),
            plataforma=replace(
                caso.economia.plataforma,
                equilibrio_acos_pct=temporada.equilibrio_del_juicio(caso),
            ),
        ),
    )


def m_sin_tope(caso: CasoHoja) -> Decimal | None:
    """El m* de R6r sin la compuerta del techo, con la cadena de `_juzga_vendedora`."""
    cpc, _fuente = _precio_de_hoy(caso)
    propia = caso.propia
    if cpc is None or propia is None:
        return None
    fondo = _fondo_juicio(propia, cpc)
    if fondo is None:
        return None
    previa = _previa(caso.cuenta, caso.grupo, propia)
    if previa is None:
        return None
    clics_sub, ticket_sub = _proyeccion_subida(fondo, previa)
    margen = MULT_SUBIDA * caso.economia.target_acos_pct / _CIEN
    arg_subida = clics_sub * cpc / (margen * ticket_sub)
    return _sostenible(fondo.a_subida, arg_subida, caso.economia.plataforma.confianza_subida)


def gamma_q_subida(caso_base: CasoHoja, caso_juicio: CasoHoja) -> tuple[Decimal, Decimal] | None:
    """`gamma_q` de R6 con la meta base y con la meta del juicio: lo que hace R6 para
    pasar de `Mantener` a `Mover` cuando la temporada cambia la meta."""
    cpc, _fuente = _precio_de_hoy(caso_base)
    propia = caso_base.propia
    if cpc is None or propia is None:
        return None
    fondo = _fondo_juicio(propia, cpc)
    if fondo is None:
        return None
    previa = _previa(caso_base.cuenta, caso_base.grupo, propia)
    if previa is None:
        return None
    clics_sub, ticket_sub = _proyeccion_subida(fondo, previa)

    def _g(target: Decimal) -> Decimal:
        arg = clics_sub * cpc / ((MULT_SUBIDA * target / _CIEN) * ticket_sub)
        return gamma_q(fondo.a_subida, arg)

    return _g(caso_base.economia.target_acos_pct), _g(caso_juicio.economia.target_acos_pct)


_real = recuperacion.multiplicador_sostenible
_capturado: list[Decimal] = []


def _espia(a_subida, arg_subida, confianza):
    m = _real(a_subida, arg_subida, confianza)
    _capturado.append(m)
    return m


recuperacion.multiplicador_sostenible = _espia


def veredicto(v) -> str:
    return f"{type(v).__name__}({getattr(v, 'motivo', '')})"


def main(ruta: str) -> int:
    with open(ruta, encoding="utf-8") as f:
        crudo = json.load(f)
    temporada = crudo.get("temporada", crudo)
    diferencias = temporada.get("diferencias") or []
    print(
        "hoja|real|sombra|destino_sombra|replay_sombra|m_estrella|m_estrella_sin_tope|"
        "gamma_q_base|gamma_q_juicio|confianza|techo_efectivo|gasto_ventana|gasto_extra"
    )
    total_extra = Decimal(0)
    for d in diferencias:
        sombra_json = d.get("caso_sombra")
        if sombra_json is None:
            print(f"{d['hoja_id']}|sin caso_sombra: no replayable")
            continue
        caso = CasoHoja.desde_json(sombra_json)
        juicio = _caso_del_juicio(caso)
        _capturado.clear()
        replay = decide(caso)
        m = _capturado[0] if _capturado else None
        m_libre = m_sin_tope(juicio)
        gq = gamma_q_subida(caso, juicio)
        confianza = caso.economia.plataforma.confianza_subida
        techo = recuperacion.techo_conocido(caso)
        gasto = caso.propia.reciente.gasto
        bid = caso.bid.valor
        destino = Decimal(str(d["destino_sombra"]))
        with localcontext() as ctx:
            ctx.prec = 30
            extra = gasto * (destino / bid - Decimal(1))
        total_extra += extra
        print(
            f"{d['hoja_id']}|{d['real']}({d['motivo_real']})|"
            f"{d['sombra']}({d['motivo_sombra']})|{destino}|"
            f"{veredicto(replay)}|{m if m is not None else 'R6r no llego a m*'}|"
            f"{m_libre if m_libre is not None else 'sin insumos'}|"
            f"{gq[0] if gq is not None else 'sin insumos'}|"
            f"{gq[1] if gq is not None else 'sin insumos'}|{confianza}|"
            f"{techo}|{gasto}|{extra:.2f}"
        )
    print(f"total|{'|' * 12}{total_extra:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"))
