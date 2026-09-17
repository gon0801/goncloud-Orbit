"""Escritura y reversa de precios en Amazon (REPRICING 01, A.3).

Orden inmutable de S5: `INSERT` de la fila `pendiente` + `COMMIT` ->
escritura (PATCH) -> sello por ack -> readback informativo -> cierre por
observacion del dia siguiente. Todo con el cliente falso en tests
(`httpx.MockTransport`): cero red, cero Amazon.

La forma del cuerpo del PATCH la sella la fila A.4 con ids reales: hasta
entonces `FORMA_PARCHE = "pendiente_sonda"` y `construir_cuerpo_parche`
levanta `FormaParcheSinSellar` ANTES de insertar la fila (no queda fila
ni hay red). Los tests inyectan `construir_cuerpo=`.

`leer_precio_vivo` usa el GET de ofertas de Pricing con `parsear_precios`
(parser ya probado en produccion), no un GET de Listings Items cuyo
formato de `offers` nadie ha probado aqui (diferencia con el «GET del
item» de S5 que A.4 confirma o cambia). Sin oferta propia o sin moneda
-> `PrecioVivoAusente`: nunca un precio supuesto.

El cuerpo nunca se loguea: ni el del PATCH ni la respuesta cruda; el
`ack` que se guarda pasa por `scrub()` y los errores llevan metodo +
ruta (sin SKU) + status.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app.redaction import scrub
from app.spapi.client import (
    MERCADOS,
    VENDEDORES_PROPIOS,
    SpapiClient,
    construir_ruta_ofertas,
)
from app.spapi.pricing import RUTA_COMPETITIVO, PrecioOmitido, parsear_precios
from app.spapi.write_client import SpapiWriteClient

logger = logging.getLogger(__name__)

# La forma del cuerpo del PATCH la sella A.4 con ids reales (el lead corre
# `git grep -n pendiente_sonda -- app/spapi/precio_write.py` y exige linea
# de codigo dentro de `construir_cuerpo_parche`, no un comentario).
FORMA_PARCHE = "pendiente_sonda"


class FormaParcheSinSellar(Exception):
    """El cuerpo del PATCH aun no tiene forma sellada (A.4 la sella)."""


class PrecioVivoAusente(Exception):
    """No hay precio propio legible: no se escribe nada."""


class CambioNoReversible(ValueError):
    """`revertir` mal usado: el cambio no existe o no es revertible."""


@dataclass(frozen=True)
class PrecioVivo:
    precio: Decimal
    moneda: str


@dataclass(frozen=True)
class ResultadoReversion:
    id_reversa: int | None
    estado: str  # "enviado" | "error" | "saltado"
    motivo: str | None


def construir_escritor(
    lector: SpapiClient,
    platform: str,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 30.0,
) -> SpapiWriteClient:
    """Un escritor por operacion (sin instancia compartida ni cache)."""
    return SpapiWriteClient(
        platform=platform,
        modo_confirmado="live",
        lector=lector,
        transport=transport,
        sleep=sleep,
        timeout=timeout,
    )


def construir_cuerpo_parche(*, platform: str, sku: str, precio: Decimal, moneda: str) -> dict:
    """Cuerpo del PATCH de precio. Hoy: sin forma sellada (A.4)."""
    if FORMA_PARCHE != "pendiente_sonda":
        raise FormaParcheSinSellar(f"forma desconocida: {FORMA_PARCHE}")
    raise FormaParcheSinSellar(FORMA_PARCHE)


def leer_precio_vivo(lector: SpapiClient, *, platform: str, asin: str) -> PrecioVivo:
    """Precio propio vivo por el GET de ofertas de Pricing + `parsear_precios`.

    Non-200, respuesta no JSON, sin oferta propia o sin moneda ->
    `PrecioVivoAusente`: un dato malo de la fuente no tumba la operacion.
    """
    if platform not in MERCADOS:
        raise ValueError(f"platform invalida: {platform!r}")
    marketplace = MERCADOS[platform]
    propio = VENDEDORES_PROPIOS[marketplace]
    ruta = construir_ruta_ofertas(asin)
    resp = lector.get(ruta, params={"MarketplaceId": marketplace, "ItemCondition": "New"})
    comp = lector.get(
        RUTA_COMPETITIVO,
        params={"MarketplaceId": marketplace, "ItemType": "Asin", "Asins": asin},
    )
    if resp.status_code != 200 or comp.status_code != 200:
        raise PrecioVivoAusente(f"pricing {asin} status={resp.status_code}/{comp.status_code}")
    try:
        ofertas = resp.json()
        competitivas = comp.json()
    except ValueError:
        raise PrecioVivoAusente(f"pricing {asin} respuesta no JSON") from None
    try:
        parsed = parsear_precios(asin, ofertas, competitivas, vendedor_propio=propio)
    except PrecioOmitido as exc:
        raise PrecioVivoAusente(f"pricing {asin} omitido: {exc.motivo}") from None
    if parsed.own_price is None or parsed.own_currency is None:
        raise PrecioVivoAusente(f"pricing {asin} sin oferta propia")
    return PrecioVivo(precio=parsed.own_price, moneda=parsed.own_currency)


def _fila_cambio(conn: psycopg.Connection, cambio_id: int) -> Any:
    fila = conn.execute(
        "SELECT c.id, c.listing_id, c.platform, c.precio_antes, c.precio_antes_currency,"
        " c.precio_despues, c.precio_despues_currency, c.aplicado, c.estado,"
        " c.enviado_at, c.es_reversa, l.seller_sku, l.external_id"
        " FROM precio_cambio c JOIN listing l"
        " ON l.id = c.listing_id AND l.platform = c.platform"
        " WHERE c.id = %s",
        (cambio_id,),
    ).fetchone()
    if fila is None:
        raise CambioNoReversible(f"cambio {cambio_id} inexistente")
    return fila


def _observada_del_dia(
    conn: psycopg.Connection, *, asin: str, platform: str, dia
) -> tuple[Decimal | None, str | None]:
    fila = conn.execute(
        "SELECT own_listing_price, own_listing_currency FROM spapi_price_observation"
        " WHERE asin = %s AND platform = %s AND metric_date = %s"
        " AND own_listing_price IS NOT NULL"
        " ORDER BY observed_at DESC LIMIT 1",
        (asin, platform, dia),
    ).fetchone()
    if fila is None:
        return None, None
    return fila[0], fila[1]


def _ack_saneado(resp: httpx.Response) -> dict:
    try:
        texto = resp.text
    except Exception:
        texto = ""
    return {"http_status": resp.status_code, "cuerpo": scrub(texto)[:2000]}


def _ruta_sin_sku(seller_id: str) -> str:
    return f"/listings/2021-08-01/items/{seller_id}"


def _estado_aceptado(resp: httpx.Response) -> tuple[bool, Any]:
    try:
        cuerpo = resp.json()
    except ValueError:
        return False, None
    if not isinstance(cuerpo, dict):
        return False, cuerpo
    aceptado = (
        200 <= resp.status_code < 300
        and cuerpo.get("submissionId") is not None
        and cuerpo.get("status", "ACCEPTED") == "ACCEPTED"
    )
    return aceptado, cuerpo


def _sellar(
    conn: psycopg.Connection, cambio_id: int, *, estado: str, ack=None, error_code=None
) -> None:
    with conn.transaction():
        if estado == "enviado":
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado', ack = %s WHERE id = %s",
                (Jsonb(ack), cambio_id),
            )
        else:
            conn.execute(
                "UPDATE precio_cambio SET estado = 'error', error_code = %s WHERE id = %s",
                (error_code, cambio_id),
            )


def _readback(
    conn: psycopg.Connection,
    cambio_id: int,
    vivo: PrecioVivo | None,
    ahora: datetime,
    *,
    origen: str,
) -> None:
    with conn.transaction():
        if vivo is None:
            conn.execute(
                "UPDATE precio_cambio SET readback_estado = 'fallido', readback_at = %s"
                " WHERE id = %s",
                (ahora, cambio_id),
            )
        else:
            conn.execute(
                "UPDATE precio_cambio SET readback_precio = %s, readback_precio_currency = %s,"
                " readback_estado = 'ok', readback_at = %s WHERE id = %s",
                (vivo.precio, vivo.moneda, ahora, cambio_id),
            )
    estado_rb = "ok" if vivo is not None else "fallido"
    logger.info("%s cambio=%s readback=%s", origen, cambio_id, estado_rb)


def _leer_vivo_suave(lector: SpapiClient, *, platform: str, asin: str) -> PrecioVivo | None:
    try:
        return leer_precio_vivo(lector, platform=platform, asin=asin)
    except (PrecioVivoAusente, httpx.HTTPError):
        return None


def revertir(
    conn: psycopg.Connection,
    cambio_id: int,
    *,
    lector: SpapiClient,
    escritor: SpapiWriteClient,
    construir_cuerpo: Callable[..., dict] | None = None,
    ahora: datetime | None = None,
) -> ResultadoReversion:
    """Reversa manual de un cambio real: vuelve a su `precio_antes`.

    Solo un cambio real, no-reversa, con `enviado_at`; cualquier otro uso
    es `CambioNoReversible` (funcion mal usada). Si el precio vivo ya no
    coincide con el `precio_despues` del cambio, se salta con su razon
    (otro proceso ya movio el precio): sin fila y sin PATCH. El cuerpo se
    arma ANTES de insertar la fila `pendiente`: con la forma sin sellar
    no queda fila ni hay red. Orden S5: INSERT + COMMIT -> PATCH -> sello.
    """
    momento = ahora or datetime.now(UTC)
    fila = _fila_cambio(conn, cambio_id)
    (
        _id,
        listing_id,
        platform,
        _antes,
        _antes_moneda,
        despues,
        despues_moneda,
        aplicado,
        _estado,
        enviado_at,
        es_reversa,
        sku,
        asin,
    ) = fila
    if es_reversa or not aplicado or enviado_at is None:
        raise CambioNoReversible(
            f"cambio {cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    try:
        vivo = leer_precio_vivo(lector, platform=platform, asin=asin)
    except (PrecioVivoAusente, httpx.HTTPError):
        logger.info("revertir cambio=%s saltado=sin_precio_vivo", cambio_id)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="sin_precio_vivo")
    if (vivo.precio, vivo.moneda) != (despues, despues_moneda):
        motivo = (
            f"precio_vivo_distinto: vivo={vivo.precio}/{vivo.moneda}"
            f" vs despues={despues}/{despues_moneda}"
        )
        logger.info("revertir cambio=%s saltado=%s", cambio_id, motivo)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo=motivo)
    arma = construir_cuerpo or construir_cuerpo_parche
    cuerpo = arma(platform=platform, sku=sku, precio=vivo.precio, moneda=vivo.moneda)
    obs_precio, obs_moneda = _observada_del_dia(
        conn, asin=asin, platform=platform, dia=momento.date()
    )
    with conn.transaction():
        id_reversa = conn.execute(
            "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_observado_antes,"
            " precio_observado_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, enviado_at, es_reversa, reversa_de)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, 'pendiente', %s, true, %s)"
            " RETURNING id",
            (
                listing_id,
                platform,
                vivo.precio,
                vivo.moneda,
                obs_precio,
                obs_moneda,
                _antes,
                _antes_moneda,
                momento,
                cambio_id,
            ),
        ).fetchone()[0]
    try:
        resp = escritor.patch_listing(sku, cuerpo)
    except httpx.HTTPError as exc:
        codigo = f"PATCH {_ruta_sin_sku(escritor._seller_id)} red"
        logger.info("revertir cambio=%s error=%s (%s)", id_reversa, codigo, type(exc).__name__)
        _sellar(conn, id_reversa, estado="error", error_code=codigo)
        _readback(conn, id_reversa, None, momento, origen="revertir")
        return ResultadoReversion(id_reversa=id_reversa, estado="error", motivo=codigo)
    aceptado, _cuerpo = _estado_aceptado(resp)
    if aceptado:
        _sellar(conn, id_reversa, estado="enviado", ack=_ack_saneado(resp))
        logger.info("revertir cambio=%s estado=enviado", id_reversa)
        vivo_rb = _leer_vivo_suave(lector, platform=platform, asin=asin)
        _readback(conn, id_reversa, vivo_rb, momento, origen="revertir")
        return ResultadoReversion(id_reversa=id_reversa, estado="enviado", motivo=None)
    codigo = f"PATCH {_ruta_sin_sku(escritor._seller_id)} {resp.status_code}"
    logger.info("revertir cambio=%s error=%s", id_reversa, codigo)
    _sellar(conn, id_reversa, estado="error", error_code=codigo)
    vivo_rb = _leer_vivo_suave(lector, platform=platform, asin=asin)
    _readback(conn, id_reversa, vivo_rb, momento, origen="revertir")
    return ResultadoReversion(id_reversa=id_reversa, estado="error", motivo=codigo)
