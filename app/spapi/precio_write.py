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

Conexión en autocommit; cada bloque confirma al salir: `cambiar_precio`,
`revertir` y `cerrar_por_observacion` exigen `conn.autocommit` antes de
hacer nada, así la fila `pendiente` ya es durable cuando sale el PATCH.
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
    SpapiAuthError,
    SpapiClient,
    SpapiError,
    construir_ruta_listings,
    construir_ruta_ofertas,
)
from app.spapi.pricing import RUTA_COMPETITIVO, PrecioOmitido, parsear_precios
from app.spapi.write_client import SpapiWriteClient, validar_patch_listings

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


class DecisionSinAccion(ValueError):
    """`cambiar_precio` mal usado: la decision no mueve precio."""


class PublicacionSinSku(ValueError):
    """La publicacion no tiene SKU con que armar la ruta: nada que escribir."""


@dataclass(frozen=True)
class PrecioVivo:
    precio: Decimal
    moneda: str


@dataclass(frozen=True)
class ResultadoReversion:
    id_reversa: int | None
    estado: str  # "enviado" | "error" | "saltado"
    motivo: str | None


@dataclass(frozen=True)
class ResultadoCambio:
    id_cambio: int | None
    estado: str  # "enviado" | "error"
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


def _normalizar_ahora(ahora: datetime | None) -> datetime:
    """`ahora` siempre en UTC (r1-A7d): naive se asume UTC, aware se convierte."""
    if ahora is None:
        return datetime.now(UTC)
    if ahora.tzinfo is None:
        return ahora.replace(tzinfo=UTC)
    return ahora.astimezone(UTC)


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


def _validar_destino(escritor: SpapiWriteClient, sku: str | None) -> None:
    """Todo lo que puede fallar sin red, ANTES del INSERT (r1-A2): SKU
    nulo o vacío (`listing_sin_sku`) y la ruta del PATCH."""
    if not sku:
        raise PublicacionSinSku("listing_sin_sku: sin seller_sku no hay ruta que escribir")
    validar_patch_listings(
        construir_ruta_listings(escritor.seller_id, sku), escritor.seller_id, sku
    )


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
        and cuerpo.get("status") == "ACCEPTED"
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
    # El readback nunca tumba una operacion ya sellada (r1-A6): LWA caido
    # tambien es `fallido`, no una excepcion hacia quien llama.
    try:
        return leer_precio_vivo(lector, platform=platform, asin=asin)
    except (PrecioVivoAusente, SpapiError, httpx.HTTPError):
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
    es `CambioNoReversible` (funcion mal usada). Si el original sigue
    abierto (`pendiente` o `enviado`) se salta con `original_abierto`:
    el indice unico de cambio abierto impide la reversa, que solo es
    posible cuando el original ya cerro (al dia siguiente, con
    `cerrar_por_observacion`); NO se puede revertir el mismo dia del
    cambio. Si el precio vivo ya no coincide con el `precio_despues` del
    cambio, se salta con su razon (otro proceso ya movio el precio): sin
    fila y sin PATCH. El cuerpo se arma ANTES de insertar la fila
    `pendiente`: con la forma sin sellar no queda fila ni hay red. Orden
    S5: INSERT + COMMIT -> PATCH -> sello.

    Ventana COMMIT-PATCH (r1-A8): si el proceso muere entre el COMMIT
    del INSERT y el PATCH (o el PATCH nunca vuelve), queda una fila
    pendiente huerfana: nunca se envio a Amazon pero el indice de
    abierto unico la vuelve visible para siempre, porque todo reintento
    la ve abierta y salta con `original_abierto` (y el cierre por
    observacion solo confirma lo que si se envio). No se reintenta
    sola: el dueno la detecta con
    `SELECT id FROM precio_cambio WHERE estado = 'pendiente' AND es_reversa`
    y la cierra a mano tras verificar en Seller Central que el precio
    no se movio.
    """
    if not conn.autocommit:
        raise ValueError(
            "revertir exige conexión en autocommit: el INSERT tiene que"
            " confirmar antes del PATCH (S5)"
        )
    momento = _normalizar_ahora(ahora)
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
    if es_reversa or not aplicado:
        raise CambioNoReversible(
            f"cambio {cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    if _estado in ("pendiente", "enviado"):
        logger.info("revertir cambio=%s saltado=original_abierto", cambio_id)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="original_abierto")
    if enviado_at is None:
        raise CambioNoReversible(
            f"cambio {cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    _validar_destino(escritor, sku)
    otro_abierto = conn.execute(
        "SELECT count(*) FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s"
        " AND estado IN ('pendiente', 'enviado')",
        (listing_id, platform),
    ).fetchone()[0]
    if otro_abierto:
        logger.info("revertir cambio=%s saltado=listing_con_cambio_abierto", cambio_id)
        return ResultadoReversion(
            id_reversa=None, estado="saltado", motivo="listing_con_cambio_abierto"
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
    cuerpo = arma(platform=platform, sku=sku, precio=_antes, moneda=_antes_moneda)
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
    return _escribir_y_sellar(
        conn,
        id_reversa,
        lector=lector,
        escritor=escritor,
        cuerpo=cuerpo,
        sku=sku,
        platform=platform,
        asin=asin,
        momento=momento,
        origen="revertir",
        exito=lambda: ResultadoReversion(id_reversa=id_reversa, estado="enviado", motivo=None),
        fallo=lambda codigo: ResultadoReversion(
            id_reversa=id_reversa, estado="error", motivo=codigo
        ),
    )


def _escribir_y_sellar(
    conn: psycopg.Connection,
    cambio_id: int,
    *,
    lector: SpapiClient,
    escritor: SpapiWriteClient,
    cuerpo: dict,
    sku: str,
    platform: str,
    asin: str,
    momento: datetime,
    origen: str,
    exito,
    fallo,
):
    """PATCH -> sello por ack -> readback informativo. Compartido por
    `cambiar_precio` y `revertir` (mismo camino S5).

    HTTP 2xx con estado aceptado -> `enviado` con `ack` literal (saneado)
    y `enviado_at`; 4xx/5xx, red o estado distinto -> `error` con
    `error_code = "<METODO> <ruta sin SKU> <status>"`, aunque una lectura
    posterior muestre el precio nuevo. El readback no decide: `ok` si se
    pudo leer (sea cual sea el precio), `fallido` si no.
    """
    ruta = _ruta_sin_sku(escritor.seller_id)
    try:
        resp = escritor.patch_listing(sku, cuerpo)
    except SpapiAuthError as exc:
        codigo = f"PATCH {ruta} lwa"
        logger.info("%s cambio=%s error=%s (%s)", origen, cambio_id, codigo, type(exc).__name__)
        _sellar(conn, cambio_id, estado="error", error_code=codigo)
        vivo_rb = _leer_vivo_suave(lector, platform=platform, asin=asin)
        _readback(conn, cambio_id, vivo_rb, momento, origen=origen)
        return fallo(codigo)
    except httpx.HTTPError as exc:
        codigo = f"PATCH {ruta} red"
        logger.info("%s cambio=%s error=%s (%s)", origen, cambio_id, codigo, type(exc).__name__)
        _sellar(conn, cambio_id, estado="error", error_code=codigo)
        vivo_rb = _leer_vivo_suave(lector, platform=platform, asin=asin)
        _readback(conn, cambio_id, vivo_rb, momento, origen=origen)
        return fallo(codigo)
    except Exception as exc:
        codigo = f"PATCH {ruta} excepcion:{type(exc).__name__}"
        logger.info("%s cambio=%s error=%s", origen, cambio_id, codigo)
        _sellar(conn, cambio_id, estado="error", error_code=codigo)
        vivo_rb = _leer_vivo_suave(lector, platform=platform, asin=asin)
        _readback(conn, cambio_id, vivo_rb, momento, origen=origen)
        raise
    aceptado, _cuerpo = _estado_aceptado(resp)
    if aceptado:
        _sellar(conn, cambio_id, estado="enviado", ack=_ack_saneado(resp))
        logger.info("%s cambio=%s estado=enviado", origen, cambio_id)
        vivo_rb = _leer_vivo_suave(lector, platform=platform, asin=asin)
        _readback(conn, cambio_id, vivo_rb, momento, origen=origen)
        return exito()
    codigo = f"PATCH {ruta} {resp.status_code}"
    logger.info("%s cambio=%s error=%s", origen, cambio_id, codigo)
    _sellar(conn, cambio_id, estado="error", error_code=codigo)
    vivo_rb = _leer_vivo_suave(lector, platform=platform, asin=asin)
    _readback(conn, cambio_id, vivo_rb, momento, origen=origen)
    return fallo(codigo)


def cambiar_precio(
    conn: psycopg.Connection,
    decision_id: int,
    *,
    lector: SpapiClient,
    escritor: SpapiWriteClient,
    construir_cuerpo: Callable[..., dict] | None = None,
    ahora: datetime | None = None,
) -> ResultadoCambio:
    """Aplica el `p_aplicado` de una decision `subir`/`bajar` en Amazon.

    Cualquier otro resultado o modo es `DecisionSinAccion` (funcion mal
    usada; la base tambien lo rechazaria). Sin precio vivo no hay fila
    que insertar (`precio_antes` es NOT NULL): `error` sin fila y sin
    PATCH. Orden S5: INSERT + COMMIT -> PATCH -> sello.

    Ventana COMMIT-PATCH (r1-A8): si el proceso muere entre el COMMIT
    del INSERT y el PATCH (o el PATCH nunca vuelve), queda una fila
    pendiente huerfana: nunca se envio a Amazon pero el indice de
    abierto unico la vuelve visible para siempre, porque todo reintento
    la ve abierta y salta con `original_abierto`. No se reintenta sola:
    el dueno la detecta con
    `SELECT id FROM precio_cambio WHERE estado = 'pendiente' AND NOT es_reversa`
    y la cierra a mano tras verificar en Seller Central que el precio
    no se movio.
    """
    if not conn.autocommit:
        raise ValueError(
            "cambiar_precio exige conexión en autocommit: el INSERT tiene que"
            " confirmar antes del PATCH (S5)"
        )
    momento = _normalizar_ahora(ahora)
    dec = conn.execute(
        "SELECT d.listing_id, d.platform, d.resultado, d.mode,"
        " d.p_actual, d.p_actual_currency, d.p_aplicado, d.p_aplicado_currency,"
        " l.seller_sku, l.external_id"
        " FROM precio_decision d JOIN listing l"
        " ON l.id = d.listing_id AND l.platform = d.platform"
        " WHERE d.id = %s",
        (decision_id,),
    ).fetchone()
    if dec is None:
        raise DecisionSinAccion(f"decision {decision_id} inexistente")
    (
        listing_id,
        platform,
        resultado,
        mode,
        p_actual,
        p_actual_moneda,
        p_aplicado,
        p_moneda,
        sku,
        asin,
    ) = dec
    if resultado not in ("subir", "bajar") or mode != "live":
        raise DecisionSinAccion(
            f"decision {decision_id}: solo subir/bajar en live mueven precio,"
            f" llego {resultado}/{mode}"
        )
    _validar_destino(escritor, sku)
    try:
        vivo = leer_precio_vivo(lector, platform=platform, asin=asin)
    except (PrecioVivoAusente, httpx.HTTPError):
        logger.info("cambiar decision=%s error=sin_precio_vivo", decision_id)
        return ResultadoCambio(id_cambio=None, estado="error", motivo="sin_precio_vivo")
    if (vivo.precio, vivo.moneda) != (p_actual, p_actual_moneda):
        motivo = (
            f"precio_vivo_distinto: vivo={vivo.precio}/{vivo.moneda}"
            f" vs p_actual={p_actual}/{p_actual_moneda}"
        )
        logger.info("cambiar decision=%s saltado=%s", decision_id, motivo)
        return ResultadoCambio(id_cambio=None, estado="saltado", motivo=motivo)
    arma = construir_cuerpo or construir_cuerpo_parche
    cuerpo = arma(platform=platform, sku=sku, precio=p_aplicado, moneda=p_moneda)
    obs_precio, obs_moneda = _observada_del_dia(
        conn, asin=asin, platform=platform, dia=momento.date()
    )
    with conn.transaction():
        id_cambio = conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_observado_antes,"
            " precio_observado_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, enviado_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, 'pendiente', %s)"
            " RETURNING id",
            (
                decision_id,
                listing_id,
                platform,
                vivo.precio,
                vivo.moneda,
                obs_precio,
                obs_moneda,
                p_aplicado,
                p_moneda,
                momento,
            ),
        ).fetchone()[0]
    return _escribir_y_sellar(
        conn,
        id_cambio,
        lector=lector,
        escritor=escritor,
        cuerpo=cuerpo,
        sku=sku,
        platform=platform,
        asin=asin,
        momento=momento,
        origen="cambiar",
        exito=lambda: ResultadoCambio(id_cambio=id_cambio, estado="enviado", motivo=None),
        fallo=lambda codigo: ResultadoCambio(id_cambio=id_cambio, estado="error", motivo=codigo),
    )


def cerrar_por_observacion(conn: psycopg.Connection, hoy) -> dict:
    """Cierra por la observacion propia del dia posterior al envio.

    Para cada cambio `enviado` con `enviado_at` de un dia UTC anterior a
    `hoy`: la observacion propia mas reciente de un dia posterior al del
    envio (join `listing.external_id = asin` + `platform`) igual a
    `precio_despues` (importe Y moneda) -> `confirmado` /
    `confirmado_por = 'observacion'`; distinta -> `no_confirmado`; sin
    observacion -> no se toca (no se adivina). Vale igual para reversas.
    """
    if not conn.autocommit:
        raise ValueError(
            "cerrar_por_observacion exige conexión en autocommit: el INSERT tiene que"
            " confirmar antes del PATCH (S5)"
        )
    abiertos = conn.execute(
        "SELECT c.id, c.listing_id, c.platform, c.precio_despues,"
        " c.precio_despues_currency, (c.enviado_at AT TIME ZONE 'UTC')::date,"
        " l.external_id"
        " FROM precio_cambio c JOIN listing l"
        " ON l.id = c.listing_id AND l.platform = c.platform"
        " WHERE c.estado = 'enviado' AND (c.enviado_at AT TIME ZONE 'UTC')::date < %s"
        " ORDER BY c.id",
        (hoy,),
    ).fetchall()
    cuenta = {"confirmado": 0, "no_confirmado": 0, "intactos": 0}
    for cid, _lid, platform, despues, despues_moneda, dia_envio, asin in abiertos:
        obs = conn.execute(
            "SELECT own_listing_price, own_listing_currency FROM spapi_price_observation"
            " WHERE asin = %s AND platform = %s AND metric_date > %s AND metric_date <= %s"
            " AND own_listing_price IS NOT NULL"
            " ORDER BY metric_date DESC, observed_at DESC LIMIT 1",
            (asin, platform, dia_envio, hoy),
        ).fetchone()
        if obs is None:
            cuenta["intactos"] += 1
            continue
        estado = "confirmado" if (obs[0], obs[1]) == (despues, despues_moneda) else "no_confirmado"
        with conn.transaction():
            conn.execute(
                "UPDATE precio_cambio SET estado = %s, confirmado_por = 'observacion'"
                " WHERE id = %s",
                (estado, cid),
            )
        cuenta[estado] += 1
        logger.info("cerrar cambio=%s estado=%s", cid, estado)
    return cuenta
