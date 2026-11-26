"""API GET de envios (ENVIOS 01 §4). Solo lectura (ConexionLectura).

`GET /api/envios`: estado de las lecturas, conteo por vista y la pagina de
envios filtrada. `GET /api/envios/contador`: el mismo `resumen.atencion`, para
el menu. `ui.py` reusa `carga_envios`; las consultas viven en
`app/envios/lectura.py`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from math import ceil
from typing import Annotated, get_args

from fastapi import APIRouter, Query
from psycopg import Connection

from app.api import ConexionLectura
from app.envios import lectura
from app.envios.estado import Estado, Tipo
from app.envios.modelo import DIAS_SEGUIMIENTO, Plataforma

router = APIRouter(prefix="/api/envios", tags=["envios"])

POR_PAGINA = 50


def ahora_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _fila(vista: lectura.EnvioVista) -> dict:
    return asdict(vista.envio) | asdict(vista.clasificacion) | {"juzgado_al": vista.juzgado_al}


def carga_envios(conn: Connection, filtros: lectura.Filtros, page: int) -> dict:
    """Todo lo que muestran la API y `/envios`. El resumen ignora los filtros:
    es la fuente del contador del menu."""
    ahora = ahora_utc()
    lecturas = lectura.ultimas_lecturas(conn)
    envios = lectura.envios_actuales(conn, ahora=ahora)
    visibles = lectura.filtra(envios, filtros)
    inicio = (page - 1) * POR_PAGINA
    return {
        "generado_el": ahora,
        "ventana_dias": DIAS_SEGUIMIENTO,
        "lecturas": {
            plataforma: {
                "ultima_ok": ultima_ok,
                "atrasada": lectura.lectura_atrasada(ultima_ok, ahora=ahora),
            }
            for plataforma, ultima_ok in lecturas.items()
        },
        "resumen": {
            vista: len(lectura.filtra(envios, lectura.Filtros(vista=vista)))
            for vista in get_args(lectura.Vista)
        },
        "total": len(visibles),
        "page": page,
        "paginas": max(1, ceil(len(visibles) / POR_PAGINA)),
        "envios": [_fila(v) for v in visibles[inicio : inicio + POR_PAGINA]],
    }


@router.get("")
def listado(
    conn: ConexionLectura,
    vista: lectura.Vista = lectura.Filtros.vista,
    plataforma: Plataforma | None = None,
    tipo: Tipo | None = None,
    estado: Estado | None = None,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    """Envios de los ultimos 30 dias, 50 por pagina."""
    return carga_envios(conn, lectura.Filtros(vista, plataforma, tipo, estado, q), page)


@router.get("/contador")
def contador(conn: ConexionLectura) -> dict:
    """Envios que requieren atencion, para el menu."""
    return {"atencion": lectura.cuenta_atencion(conn, ahora=ahora_utc())}
