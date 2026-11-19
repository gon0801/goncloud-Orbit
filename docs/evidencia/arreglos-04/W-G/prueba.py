"""Prueba real del bloque W-G, para en_prod.sh.

Funciones puras: sin base, sin red, sin escritura.
"""

from __future__ import annotations

import datetime as dt

from app import notifica

assert not hasattr(notifica, "aviso_corte_encolado")
assert not hasattr(notifica, "notifica_encola")

vence = dt.datetime(2026, 11, 14, 17, 3, tzinfo=dt.UTC)

practica_31 = [
    notifica.CorteEncolado(
        platform="amazon_us",
        kind="negative" if i % 2 == 0 else "pause",
        search_term=f"funda iphone {i}" if i % 2 == 0 else None,
        vence_el=vence + dt.timedelta(minutes=i),
        modo="shadow",
    )
    for i in range(31)
]
mensajes_practica = notifica.avisos_cortes_del_ciclo(practica_31)
assert len(mensajes_practica) == 1, mensajes_practica
msg_practica = mensajes_practica[0]
assert "48h" not in msg_practica, msg_practica
assert "no se aplicarán" in msg_practica, msg_practica
assert "y 26 más" in msg_practica, msg_practica

mixtos = [
    notifica.CorteEncolado(
        platform="amazon_us",
        kind="negative",
        search_term="funda iphone",
        vence_el=vence + dt.timedelta(hours=1),
        modo="live",
    ),
    notifica.CorteEncolado(
        platform="amazon_us",
        kind="harvest",
        search_term="cargador usb c",
        vence_el=vence,
        modo="live",
    ),
    notifica.CorteEncolado(
        platform="amazon_us",
        kind="pause",
        search_term=None,
        vence_el=vence,
        modo="shadow",
    ),
]
mensajes_mixtos = notifica.avisos_cortes_del_ciclo(mixtos)
assert len(mensajes_mixtos) == 1, mensajes_mixtos
msg_mixto = mensajes_mixtos[0]
assert "En vivo: 2" in msg_mixto, msg_mixto
assert "14 nov, 11:03" in msg_mixto, msg_mixto
assert "En práctica: 1" in msg_mixto, msg_mixto
assert msg_mixto.index("En vivo: 2") < msg_mixto.index("En práctica: 1"), msg_mixto

print("W-G OK")
