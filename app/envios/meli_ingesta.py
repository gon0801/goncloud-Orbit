"""Ingesta solo-GET de pedidos y envios de Mercado Libre (ENVIOS 01).

Descubre las ordenes de los ultimos 30 dias, agrupa por `shipping.id` y lee
`/shipments/{id}` de los envios nuevos y de los abiertos, aunque estos ya hayan
salido de la busqueda. Un envio cerrado (estado terminal o vencido sin
confirmacion, `app.envios.estado`) no se relee salvo que se pida con `--releer`.
Escribe por `app.envios.store` y sella su ingest_run en la misma transaccion.
Runbook: `python -m app.cli ingest envios_meli`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass

import httpx
import psycopg

from app.db import connect
from app.envios import lectura
from app.envios import store as envios_store
from app.envios.estado import ESTADOS_CERRADOS, DiasCierreInvalidos, clasificar
from app.envios.meli import (
    EnvioIlegible,
    creada_el,
    envio_actualizado_desde_meli,
    envio_desde_meli,
    envio_ref_de_orden,
    pedido_ref_de_orden,
)
from app.envios.modelo import DIAS_SEGUIMIENTO, EnvioObservado
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


@dataclass(frozen=True)
class Descubrimiento:
    """Ordenes por envio (`shipping.id`), lo que se salto y si la lectura quedo corta."""

    envios: dict[str, list[dict]]
    saltos: Counter
    aviso: str | None


class ReferenciaDesconocida(ValueError):
    """`--releer` pide envios que no estan en la base."""


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


def _leer_envios(
    cliente: ClienteMeli,
    refs: list[str],
    traducir: Callable[[str, dict], EnvioObservado],
    saltos: Counter,
    *,
    viejos: Set[str],
) -> list[EnvioObservado]:
    """Un envio que falla se cuenta y no tumba a los demas; un 401 aborta todo.

    Los fallos de `viejos` se cuentan aparte (`envio_viejo_*`): quedan en el
    sello sin bajar `ok`.
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
            saltos["envio_viejo_ilegible" if ref in viejos else "envio_ilegible"] += 1
            continue
        leidos.append(envio)
    return leidos


def _causa(exc: BaseException) -> str:
    """El cliente MeLi y la config arman mensajes sin datos; del resto va la clase."""
    con_mensaje = (ReputacionError, DiasCierreInvalidos)
    return scrub(str(exc)) if isinstance(exc, con_mensaje) else type(exc).__name__


def _exige_conocidos(conn: psycopg.Connection, refs: Sequence[str]) -> None:
    """Antes de abrir corrida y de llamar a Mercado Libre."""
    with conn.transaction():
        conocidos = lectura.envios_meli(conn)
    desconocidos = [ref for ref in dict.fromkeys(refs) if ref not in conocidos]
    if desconocidos:
        raise ReferenciaDesconocida(
            f"No hay envíos de Mercado Libre con estas referencias: {', '.join(desconocidos)}."
            " No se consultó nada."
        )


def ejecutar_ingesta(
    conn: psycopg.Connection,
    cliente: ClienteMeli,
    *,
    ahora: dt.datetime | None = None,
    max_paginas: int = MAX_PAGINAS_DEFAULT,
    releer: Sequence[str] = (),
) -> ResultadoIngestaMeli:
    """Una corrida completa. `ok=False` si quedo algun envio sin leer o ilegible,
    o la busqueda incompleta; lo que si se leyo queda escrito. `releer` suma
    envios conocidos aunque esten cerrados.

    No baja `ok` el fallo de un envio que ya no vino en la busqueda y cuya compra
    es anterior a su ventana (la pantalla no lo muestra), salvo que este en
    `releer`.
    """
    momento = ahora or dt.datetime.now(dt.UTC)
    desde_ventana = momento - dt.timedelta(days=DIAS_SEGUIMIENTO)
    if releer:
        _exige_conocidos(conn, releer)
    with conn.transaction():
        run_id = abrir_run(conn, SOURCE, platform=PLATAFORMA)
    saltos: Counter = Counter()
    try:
        with conn.transaction():
            dias = lectura.dias_cierre(conn)
            conocidos = lectura.envios_meli(conn)
        cerrados = {
            ref
            for ref, envio in conocidos.items()
            if clasificar(envio, juzgado_al=momento, ahora=momento, dias_cierre=dias).estado
            in ESTADOS_CERRADOS
        }
        hallado = descubrir_ordenes(
            cliente,
            desde=desde_ventana,
            max_paginas=max_paginas,
        )
        saltos.update(hallado.saltos)
        pendientes = (hallado.envios.keys() | conocidos.keys()) - cerrados
        por_leer = sorted(pendientes | set(releer))
        viejos = {
            ref
            for ref in pendientes - hallado.envios.keys() - set(releer)
            if (creado := conocidos[ref].pedido_creado_el) is not None and creado < desde_ventana
        }

        def traducir(ref: str, crudo: dict) -> EnvioObservado:
            if ref in hallado.envios:
                return envio_desde_meli(hallado.envios[ref], crudo)
            return envio_actualizado_desde_meli(conocidos[ref], crudo)

        envios = _leer_envios(cliente, por_leer, traducir, saltos, viejos=viejos)
        with conn.transaction():
            escritos, sin_cambio = envios_store.guardar(
                conn, envios, observed_at=momento, run_id=run_id
            )
            saltos[SALTO_ENVIO_SIN_CAMBIO] += sin_cambio
            sin_certificar = saltos["envio_no_leido"] + saltos["envio_ilegible"]
            ok = hallado.aviso is None and sin_certificar == 0
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
    """Exit 0 completa, 1 fallida o incompleta, 2 sin DSN o con un `--releer`
    desconocido. `transport` es para tests."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest envios_meli",
        description="Ingiere envios de Mercado Libre. Solo lectura contra Mercado Libre.",
    )
    parser.add_argument(
        "--max-paginas", type=int, default=MAX_PAGINAS_DEFAULT, help="tope de paginas de ordenes"
    )
    parser.add_argument(
        "--releer",
        action="append",
        default=[],
        metavar="REF",
        help="relee ese envio aunque este cerrado o vencido (repetible)",
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
                resultado = ejecutar_ingesta(
                    conn, cliente, max_paginas=args.max_paginas, releer=args.releer
                )
            finally:
                cliente.close()
        finally:
            conn.close()
    except ReferenciaDesconocida as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 2
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
