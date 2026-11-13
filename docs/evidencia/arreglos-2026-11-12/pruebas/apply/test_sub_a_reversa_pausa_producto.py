"""Revision sub-A (comprobacion, regla 7): la pausa por producto del ciclo
tiene reversa. Ejecuta la pausa real (libera_vencidos -> ejecuta_pause_producto)
y luego `retiro_anuncios.repone` con autorizacion humana sobre ese retiro.
"""

from __future__ import annotations

import test_apply_producto as t

from app import retiro_anuncios as ra

plantilla = t.plantilla


def test_repone_regresa_la_pausa_de_un_producto_sin_ventas(plantilla):
    with t._db(plantilla, "orbit_suba_reversa") as conn:
        t._mundo_pausa(conn)
        t._config(conn)
        _dec, _q = t._propuesta(conn)
        amazon = t._Amazon({"pa1": "ENABLED"})
        res = t._libera(conn, amazon)
        assert res.aplicadas == 1
        [(rid, motivo, _autoria, _actor)] = t._retiros(conn)
        assert motivo == "producto_sin_ventas"
        escritor = t._aplicador(conn, amazon, t._ciclo_nuevo(conn))._cliente()
        (rep,) = ra.repone(
            conn,
            retiro_ids=(rid,),
            autorizacion=ra.AutorizacionHumana("REGRESAR 1 ANUNCIOS", "dueno"),
            escritor=escritor,
        )
        assert rep.resultado == "repuesto"
        assert amazon.estado["pa1"] == "ENABLED"
        assert amazon.puts == [("pa1", "PAUSED"), ("pa1", "ENABLED")]
