"""Lector unico de envios (ENVIOS 01 §4): pantalla, API y contador leen de aqui.

Solo SELECT. Cada envio se clasifica al vuelo y se juzga al instante de la
ultima lectura correcta de su plataforma: una lectura atrasada no inventa demoras.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Literal, get_args

import psycopg

from app.db import cursor_dict
from app.envios.estado import (
    ESTADOS_CERRADOS,
    Clasificacion,
    Estado,
    Tipo,
    clasificar,
    fecha_limite,
)
from app.envios.modelo import (
    DIAS_SEGUIMIENTO,
    Articulo,
    EnvioObservado,
    PaqueteObservado,
    Plataforma,
)

# Dos ciclos de ingesta de 4 h.
HORAS_LECTURA_ATRASADA = 8

Vista = Literal["atencion", "en_curso", "cerrados", "todos"]


@dataclass(frozen=True)
class EnvioVista:
    envio: EnvioObservado
    clasificacion: Clasificacion
    juzgado_al: datetime


@dataclass(frozen=True)
class Filtros:
    vista: Vista = "en_curso"
    plataforma: Plataforma | None = None
    tipo: Tipo | None = None
    estado: Estado | None = None
    q: str | None = None


_EN_VISTA: dict[Vista, Callable[[Clasificacion], bool]] = {
    "atencion": lambda c: c.motivo is not None,
    "en_curso": lambda c: c.estado not in ESTADOS_CERRADOS,
    "cerrados": lambda c: c.estado in ESTADOS_CERRADOS,
    "todos": lambda c: True,
}

# `started_at` es el instante conservador del dato: nada leido es anterior a el.
_SQL_ULTIMAS_LECTURAS = """
SELECT platform::text, max(started_at) FROM ingest_run
 WHERE ok
   AND ((source = 'spapi_orders' AND platform IN ('amazon_mx', 'amazon_us'))
        OR (source = 'envios_meli' AND platform = 'meli'))
 GROUP BY platform
"""

# El orden de aqui desempata el de pantalla: el pedido mas reciente primero.
_SQL_VENTANA = """
SELECT * FROM v_envio_actual
 WHERE COALESCE(pedido_creado_el, observed_at) >= %s
 ORDER BY pedido_creado_el DESC NULLS LAST, platform, envio_ref
"""

_SQL_PAQUETES = "SELECT * FROM envio_paquete_observation WHERE envio_obs_id = ANY(%s) ORDER BY id"


def ultimas_lecturas(conn: psycopg.Connection) -> dict[str, datetime | None]:
    """Inicio de la ultima corrida correcta por plataforma; None si nunca hubo."""
    leidas = dict(conn.execute(_SQL_ULTIMAS_LECTURAS).fetchall())
    return {plataforma: leidas.get(plataforma) for plataforma in get_args(Plataforma)}


def lectura_atrasada(ultima_ok: datetime | None, *, ahora: datetime) -> bool:
    return ultima_ok is None or ahora - ultima_ok > timedelta(hours=HORAS_LECTURA_ATRASADA)


def _armar(clase: type, fila: dict, **ya_armados: object):
    nombres = (campo.name for campo in fields(clase))
    return clase(**{n: ya_armados[n] if n in ya_armados else fila[n] for n in nombres})


def _orden(vista: EnvioVista) -> tuple:
    """Atencion primero, luego el plazo mas proximo; sin plazo al final."""
    limite = fecha_limite(vista.envio, vista.clasificacion.estado)
    return (vista.clasificacion.motivo is None, limite is None, limite)


def envios_actuales(conn: psycopg.Connection, *, ahora: datetime) -> list[EnvioVista]:
    """Ultima observacion de cada envio de la ventana, clasificada y ordenada."""
    lecturas = ultimas_lecturas(conn)
    with cursor_dict(conn) as cur:
        desde = ahora - timedelta(days=DIAS_SEGUIMIENTO)
        filas = cur.execute(_SQL_VENTANA, (desde,)).fetchall()
        paquetes: dict[int, list[PaqueteObservado]] = {}
        for fila in cur.execute(_SQL_PAQUETES, ([f["id"] for f in filas],)).fetchall():
            paquetes.setdefault(fila["envio_obs_id"], []).append(_armar(PaqueteObservado, fila))

    vistas: list[EnvioVista] = []
    for fila in filas:
        envio = _armar(
            EnvioObservado,
            fila,
            pedidos=tuple(fila["pedidos"]),
            articulos=tuple(Articulo(**articulo) for articulo in fila["articulos"]),
            paquetes=tuple(paquetes.get(fila["id"], ())),
        )
        ultima_ok = lecturas[envio.platform]
        juzgado_al = fila["observed_at"] if ultima_ok is None else min(ahora, ultima_ok)
        vistas.append(EnvioVista(envio, clasificar(envio, juzgado_al=juzgado_al), juzgado_al))
    return sorted(vistas, key=_orden)


def _buscables(envio: EnvioObservado) -> Iterator[str]:
    """Pedidos, guias y SKU: lo que se puede buscar con `q`."""
    yield from envio.pedidos
    yield from (p.guia for p in envio.paquetes if p.guia)
    yield from (a.sku for a in envio.articulos if a.sku)


def filtra(envios: Sequence[EnvioVista], filtros: Filtros) -> list[EnvioVista]:
    """Los filtros presentes se suman (AND); `q` busca un fragmento sin mayusculas."""
    buscado = (filtros.q or "").strip().casefold()
    return [
        v
        for v in envios
        if _EN_VISTA[filtros.vista](v.clasificacion)
        and filtros.plataforma in (None, v.envio.platform)
        and filtros.tipo in (None, v.clasificacion.tipo)
        and filtros.estado in (None, v.clasificacion.estado)
        and (not buscado or any(buscado in texto.casefold() for texto in _buscables(v.envio)))
    ]


def cuenta_atencion(conn: psycopg.Connection, *, ahora: datetime) -> int:
    return len(filtra(envios_actuales(conn, ahora=ahora), Filtros(vista="atencion")))
