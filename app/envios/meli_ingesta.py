"""Ingesta solo-GET de pedidos y envios de Mercado Libre (ENVIOS 01).

Descubre las ordenes de los ultimos 30 dias, agrupa por `shipping.id` y lee
`/shipments/{id}` de los envios nuevos y de los abiertos, aunque estos ya hayan
salido de la busqueda. Escribe por `app.envios.store` y sella su ingest_run en
la misma transaccion. Runbook: `python -m app.cli ingest envios_meli`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, fields

import httpx
import psycopg

from app.db import connect, cursor_dict
from app.envios import store as envios_store
from app.envios.meli import (
    EnvioIlegible,
    creada_el,
    envio_actualizado_desde_meli,
    envio_desde_meli,
    envio_ref_de_orden,
    pedido_ref_de_orden,
)
from app.envios.modelo import DIAS_SEGUIMIENTO, Articulo, EnvioObservado, PaqueteObservado
from app.ingest_run import (
    SALTO_ENVIO_SIN_CAMBIO,
    abrir_run,
    formato_skip_reason,
    sellar_fallo_seguro,
    sellar_run,
)
from app.meli.client import ClienteMeli, MeliCredentials, PaseVencidoError, ReputacionError
from app.redaction import install_scrub_filter, redact_dsn, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "envios_meli"
PLATAFORMA = "meli"
LIMITE_PAGINA = 50
MAX_PAGINAS_DEFAULT = 100
DIAS_TOPE_RELECTURA = 90

# Regla unica de envio terminal (no se relee). Un `not_delivered` sin
# devolucion, un estado nulo o uno desconocido siguen abiertos.
_SQL_TERMINAL = """coalesce(
    estado_fuente IN ('delivered', 'cancelled')
    OR (estado_fuente = 'not_delivered' AND devuelto_el IS NOT NULL),
    false)"""
_SQL_ABIERTOS = f"""
SELECT * FROM v_envio_actual
 WHERE platform = 'meli' AND NOT {_SQL_TERMINAL}
"""
_SQL_ABIERTOS_DESDE = f"""
SELECT * FROM v_envio_actual
 WHERE platform = 'meli'
   AND NOT {_SQL_TERMINAL}
   AND COALESCE(pedido_creado_el, observed_at) >= %s
"""
_SQL_CERRADOS = f"""
SELECT envio_ref FROM v_envio_actual
 WHERE platform = 'meli' AND envio_ref = ANY(%s) AND {_SQL_TERMINAL}
"""
_SQL_PAQUETES = "SELECT * FROM envio_paquete_observation WHERE envio_obs_id = ANY(%s) ORDER BY id"


@dataclass(frozen=True)
class Descubrimiento:
    """Ordenes por envio (`shipping.id`), lo que se salto y si la lectura quedo corta."""

    envios: dict[str, list[dict]]
    saltos: Counter
    aviso: str | None


@dataclass(frozen=True)
class ResultadoIngestaMeli:
    run_id: int
    ok: bool
    escritos: int
    sin_cambio: int
    omitidos: int
    skip_reason: str | None
    aviso: str | None


def _recorrer_busqueda(
    cliente: ClienteMeli, *, desde: dt.datetime, max_paginas: int
) -> tuple[list[object], str | None]:
    """`/orders/search` de lo mas nuevo a lo mas viejo, hasta pasar de `desde`."""
    yo, _ = cliente.get("/users/me")
    vendedor = yo.get("id")
    if not isinstance(vendedor, int):
        raise ReputacionError("MeLi /users/me sin id entero")
    leidas: list[object] = []
    for _pagina in range(max_paginas):
        datos, _ = cliente.get(
            "/orders/search",
            {
                "seller": vendedor,
                "sort": "date_desc",
                "limit": LIMITE_PAGINA,
                "offset": len(leidas),
            },
        )
        pagina = datos.get("results")
        if not isinstance(pagina, list):
            raise ReputacionError("MeLi /orders/search sin lista results")
        leidas.extend(pagina)
        fechas = (creada_el(o) for o in pagina if isinstance(o, dict))
        if any(fecha is not None and fecha < desde for fecha in fechas):
            return leidas, None
        paginado = datos.get("paging")
        total = paginado.get("total") if isinstance(paginado, dict) else None
        if isinstance(total, int) and len(leidas) >= total:
            return leidas, None
        if not pagina:
            return leidas, "pagina_vacia_antes_del_total" if isinstance(total, int) else None
    return leidas, "limite_max_paginas"


def descubrir_ordenes(
    cliente: ClienteMeli, *, desde: dt.datetime, max_paginas: int = MAX_PAGINAS_DEFAULT
) -> Descubrimiento:
    """Envios con alguna orden dentro de la ventana, con todas sus ordenes leidas.

    Una orden sin fecha legible cuenta como dentro: no se puede probar que sea vieja.
    """
    leidas, aviso = _recorrer_busqueda(cliente, desde=desde, max_paginas=max_paginas)
    grupos: dict[str, dict[str, dict]] = {}
    en_ventana: set[str] = set()
    saltos: Counter = Counter()
    for orden in leidas:
        if not isinstance(orden, dict):
            saltos["orden_ilegible"] += 1
            continue
        fecha = creada_el(orden)
        reciente = fecha is None or fecha >= desde
        pedido = pedido_ref_de_orden(orden)
        ref = envio_ref_de_orden(orden)
        if pedido is None or ref is None:
            if reciente:
                saltos["orden_ilegible" if pedido is None else "orden_sin_envio"] += 1
            continue
        # Por id: una venta nueva a media lectura repite ordenes entre paginas.
        grupos.setdefault(ref, {})[pedido] = orden
        if reciente:
            en_ventana.add(ref)
    envios = {ref: list(ordenes.values()) for ref, ordenes in grupos.items() if ref in en_ventana}
    return Descubrimiento(envios, saltos, aviso)


def _armar(clase: type, fila: dict, **ya_armados: object):
    nombres = (campo.name for campo in fields(clase))
    return clase(**{n: ya_armados[n] if n in ya_armados else fila[n] for n in nombres})


def envios_abiertos(
    conn: psycopg.Connection, *, desde: dt.datetime | None = None
) -> dict[str, EnvioObservado]:
    """Ultima observacion de cada envio de Mercado Libre que aun se relee.

    Solo la fuente mueve un envio a terminal: al reportar en una lectura que se
    guarda `delivered`, `cancelled`, o `not_delivered` con fecha de devolucion.
    Con `desde`, corta relecturas de envios abiertos mas viejos que el tope
    (`COALESCE(pedido_creado_el, observed_at) >= desde`).
    """
    with conn.transaction(), cursor_dict(conn) as cur:
        if desde is None:
            filas = cur.execute(_SQL_ABIERTOS).fetchall()
        else:
            filas = cur.execute(_SQL_ABIERTOS_DESDE, (desde,)).fetchall()
        paquetes: dict[int, list[PaqueteObservado]] = {}
        for fila in cur.execute(_SQL_PAQUETES, ([f["id"] for f in filas],)).fetchall():
            paquetes.setdefault(fila["envio_obs_id"], []).append(_armar(PaqueteObservado, fila))
    return {
        fila["envio_ref"]: _armar(
            EnvioObservado,
            fila,
            pedidos=tuple(fila["pedidos"]),
            articulos=tuple(Articulo(**articulo) for articulo in fila["articulos"]),
            paquetes=tuple(paquetes.get(fila["id"], ())),
        )
        for fila in filas
    }


def _envios_cerrados(conn: psycopg.Connection, refs: list[str]) -> set[str]:
    with conn.transaction():
        return {fila[0] for fila in conn.execute(_SQL_CERRADOS, (refs,)).fetchall()}


def _leer_envios(
    cliente: ClienteMeli,
    refs: list[str],
    traducir: Callable[[str, dict], EnvioObservado],
    saltos: Counter,
    *,
    viejos: frozenset[str] | set[str] = frozenset(),
) -> list[EnvioObservado]:
    """Un envio que falla se cuenta y no tumba a los demas; un 401 aborta todo.

    Los envios abiertos con mas de 30 dias (`viejos`) cuentan bajo
    `envio_viejo_no_leido` para quedar registrados sin tumbar `ok`.
    """
    leidos: list[EnvioObservado] = []
    for ref in refs:
        try:
            crudo, _ = cliente.get(f"/shipments/{ref}")
        except PaseVencidoError:
            raise
        except ReputacionError as exc:
            clave = "envio_viejo_no_leido" if ref in viejos else "envio_no_leido"
            saltos[clave] += 1
            logger.warning("envios_meli: envio %s no leido (%s): %s", ref, clave, exc)
            continue
        try:
            envio = traducir(ref, crudo)
        except EnvioIlegible:
            envio = None
        if envio is None or envio.envio_ref != ref:
            saltos["envio_ilegible"] += 1
            continue
        leidos.append(envio)
    return leidos


def _causa(exc: BaseException) -> str:
    """Solo el cliente MeLi arma mensajes sin datos; del resto va la clase."""
    return scrub(str(exc)) if isinstance(exc, ReputacionError) else type(exc).__name__


def ejecutar_ingesta(
    conn: psycopg.Connection,
    cliente: ClienteMeli,
    *,
    ahora: dt.datetime | None = None,
    max_paginas: int = MAX_PAGINAS_DEFAULT,
) -> ResultadoIngestaMeli:
    """Una corrida completa. `ok=False` si quedo algun envio reciente sin leer o
    la busqueda incompleta; lo que si se leyo queda escrito.

    Los envios abiertos de mas de `DIAS_TOPE_RELECTURA` dias dejan de releerse,
    y un fallo de lectura sobre un envio abierto de mas de `DIAS_SEGUIMIENTO`
    dias queda en `skip_reason` (`envio_viejo_no_leido`) sin bajar `ok`.
    """
    momento = ahora or dt.datetime.now(dt.UTC)
    desde_ventana = momento - dt.timedelta(days=DIAS_SEGUIMIENTO)
    desde_relectura = momento - dt.timedelta(days=DIAS_TOPE_RELECTURA)
    with conn.transaction():
        run_id = abrir_run(conn, SOURCE, platform=PLATAFORMA)
    saltos: Counter = Counter()
    try:
        hallado = descubrir_ordenes(
            cliente,
            desde=desde_ventana,
            max_paginas=max_paginas,
        )
        saltos.update(hallado.saltos)
        abiertos = envios_abiertos(conn, desde=desde_relectura)
        cerrados = _envios_cerrados(conn, list(hallado.envios))
        por_leer = sorted((hallado.envios.keys() - cerrados) | abiertos.keys())
        viejos = {
            ref
            for ref, envio in abiertos.items()
            if ref not in hallado.envios
            and envio.pedido_creado_el is not None
            and envio.pedido_creado_el < desde_ventana
        }

        def traducir(ref: str, crudo: dict) -> EnvioObservado:
            if ref in hallado.envios:
                return envio_desde_meli(hallado.envios[ref], crudo)
            return envio_actualizado_desde_meli(abiertos[ref], crudo)

        envios = _leer_envios(cliente, por_leer, traducir, saltos, viejos=viejos)
        with conn.transaction():
            escritos, sin_cambio = envios_store.guardar(
                conn, envios, observed_at=momento, run_id=run_id
            )
            saltos[SALTO_ENVIO_SIN_CAMBIO] += sin_cambio
            ok = hallado.aviso is None and saltos["envio_no_leido"] == 0
            omitidos = sum(saltos.values())
            motivo = formato_skip_reason(saltos)
            if hallado.aviso is not None:
                prefijo = f"paginacion_incompleta:{hallado.aviso}"
                motivo = f"{prefijo}; {motivo}" if motivo else prefijo
            sellar_run(
                conn,
                run_id,
                ok=ok,
                rows_written=escritos,
                rows_skipped=omitidos,
                skip_reason=motivo,
            )
    except BaseException as exc:
        resumen = formato_skip_reason(saltos)
        causa = f"fallo la corrida: {_causa(exc)}"
        sellar_fallo_seguro(
            conn,
            run_id,
            exc,
            rows_skipped=sum(saltos.values()),
            motivo=f"{causa}; {resumen}" if resumen else causa,
        )
        raise
    return ResultadoIngestaMeli(
        run_id=run_id,
        ok=ok,
        escritos=escritos,
        sin_cambio=sin_cambio,
        omitidos=omitidos,
        skip_reason=motivo,
        aviso=hallado.aviso,
    )


def _sella_caida_temprana(conn: psycopg.Connection, exc: Exception) -> None:
    """Sin llave o sin cliente tambien queda corrida fallida: que la fuente se
    cayo se distingue de que el cron no corrio."""
    with conn.transaction():
        run_id = abrir_run(conn, SOURCE, platform=PLATAFORMA)
    sellar_fallo_seguro(conn, run_id, exc, motivo=f"fallo antes de empezar: {type(exc).__name__}")


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """Exit 0 completa, 1 fallida o incompleta, 2 sin DSN. `transport` es para tests."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest envios_meli",
        description="Ingiere envios de Mercado Libre. Solo lectura contra Mercado Libre.",
    )
    parser.add_argument(
        "--max-paginas", type=int, default=MAX_PAGINAS_DEFAULT, help="tope de paginas de ordenes"
    )
    args = parser.parse_args(argv)
    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir (fail-closed)",
            file=sys.stderr,
        )
        return 2
    redact_dsn(dsn)
    try:
        conn = connect(dsn)
        try:
            try:
                cliente = ClienteMeli(MeliCredentials.from_secrets_dir(), transport=transport)
            except Exception as exc:
                _sella_caida_temprana(conn, exc)
                raise
            try:
                resultado = ejecutar_ingesta(conn, cliente, max_paginas=args.max_paginas)
            finally:
                cliente.close()
        finally:
            conn.close()
    except Exception as exc:
        print(f"ingesta {SOURCE} fallo: {_causa(exc)}", file=sys.stderr)
        return 1
    print(
        f"{SOURCE}: run={resultado.run_id} escritos={resultado.escritos} "
        f"sin_cambio={resultado.sin_cambio} omitidos={resultado.omitidos} "
        f"aviso={resultado.aviso}"
    )
    if not resultado.ok:
        print(f"ingesta {SOURCE} incompleta: {resultado.skip_reason}", file=sys.stderr)
        return 1
    return 0
