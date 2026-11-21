"""Despacho IO de los avisos diarios de campana (BIDS 02, V.4).

Extraido de `app/notifica.py` en Fase 3.1 para romper el ciclo runtime
`notifica -> pantalla_dinero / avisos_campana`.
"""

from __future__ import annotations

import datetime as dt
import logging

from app import notifica
from app import pantalla_dinero as pd
from app.avisos_campana import CLASES, avisos_del_dia, mensaje_clase
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


def avisar_campanas(conn, hoy=None, *, solo=None):
    """Avisos diarios de campana (BIDS 02, V.4): lee los lectores de P.2a
    por mercado (ventana de 30 dias hasta ayer), arma con `avisos_del_dia`
    y envia un mensaje por plataforma y clase con `notifica._envia_texto`.
    `solo` restringe a {(plataforma, clase)} (el comando salta lo ya enviado).
    Una clase fallida no tumba las demas; una plataforma con lectura
    fallida no tumba la otra; sin casos no envia nada.
    Devuelve {(plataforma, clase): salio} de lo intentado."""
    dia = hoy if hoy is not None else dt.datetime.now(dt.UTC).date()
    hasta = dia - dt.timedelta(days=1)
    desde = hasta - dt.timedelta(days=pd.DIAS_UBICACION - 1)
    if solo is None:
        pedidos = [(p, c) for p in PLATAFORMAS_MONEDA for c in CLASES]
    else:
        pedidos = [(p, c) for p in PLATAFORMAS_MONEDA for c in CLASES if (p, c) in solo]
    salieron = {}
    for plataforma in PLATAFORMAS_MONEDA:
        clases = [c for p, c in pedidos if p == plataforma]
        if not clases:
            continue
        try:
            avisos = avisos_del_dia(
                pd.lee_ubicaciones(conn, plataforma=plataforma, desde=desde, hasta=hasta),
                pd.lee_campanas(conn, plataforma=plataforma, desde=desde, hasta=hasta),
                plataforma=plataforma,
                gasto_para_concluir=pd.umbral_concluir(conn, plataforma),
            )
        except Exception as exc:
            logger.warning("campanas: lectura de %s fallida: %s", plataforma, scrub(str(exc)))
            conn.rollback()
            continue
        for clase in clases:
            texto = mensaje_clase(plataforma, clase, avisos)
            if not texto:
                continue
            salieron[(plataforma, clase)] = notifica._envia_texto(texto)
    return salieron
