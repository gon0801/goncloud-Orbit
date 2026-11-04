"""Estimacion del universo `meli/meli` (REPRICING 02 M.3).

La oferta sale de `meli_publicacion_observation` (la escribe M.1 a
diario); los fees se cotizan por GET a `/sites/MLM/listing_prices` con
`price + category_id + listing_type_id` (conclusion 2 de M.0: separa
porcentaje y fijo). El escenario por publicacion lo siembra
`sembrar_escenario_desde_refs`, que para `meli` corre una cuenta por
miembro y deja en la fila la del que manda (mayor carga costo+envio
exacta — con goal unico equivale al margen mas bajo y al P* mas alto;
empate -> `seller_sku` ascendente).

Vocabulario de detalles de fee MeLi (lo que persiste `fee_details`):

- `SaleFee`: la parte porcentual (monto que escala con el precio).
- `SaleFeeFixed`: el fijo de la venta.
- `ListingFee`: el cargo por publicar.

Los tres suman `total_fees`; `partir_fees_meli` exige un unico `SaleFee`
positivo (espejo de `partir_fees`, que exige el unico `ReferralFee`).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from app.estimacion_fees import FeesClientError, ResultadoCotizacion
from app.estimacion_insumos import (
    OfertaResuelta,
    ResultadoResolucionOferta,
    construir_context_fingerprint,
    evaluar_frescura,
)
from app.estimacion_venta import DetalleFee
from app.redaction import install_scrub_filter
from app.reputacion_clientes import ClienteMeli, ReputacionError

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

__all__ = [
    "CotizadorMeli",
    "construir_fee_canonical_input_meli",
    "construir_fee_outcome_canonical_meli",
    "construir_fee_source_event_id_meli",
    "construir_huella_meli",
    "construir_oferta_canonical_input_meli",
    "construir_oferta_source_event_id_meli",
    "partir_fees_meli",
    "resolver_oferta_meli",
]

SITIO_MLM = "MLM"
_PATH_PRECIOS = f"/sites/{SITIO_MLM}/listing_prices"
SOURCE_MELI = "meli_publicacion_observation"

_MAX_DECIMALES = Decimal("0.0001")
_MAX_DINERO = Decimal(10) ** 10  # espejo de app.estimacion_fees

_SQL_LISTING = "SELECT id, platform, external_id, seller_sku FROM listing WHERE id = %s"
_SQL_PUB = (
    "SELECT id, item_id, metric_date, observed_at, estado, precio, moneda,"
    " tipo_publicacion, categoria_id, variantes FROM meli_publicacion_observation"
    " WHERE item_id = %s ORDER BY observed_at DESC, id DESC LIMIT 1"
)

_SQL_MIEMBRO_EXTS = (
    "SELECT variante_ext FROM listing_miembro"
    " WHERE listing_id = %s AND platform = 'meli' AND activo"
)


def partir_fees_meli(
    detalles: Sequence[DetalleFee], fee_total: Decimal
) -> tuple[Decimal, Decimal] | str:
    """`(variable, fijo)` desde el arbol; sin `SaleFee` unico, `fee_incompatible`.

    Variable es el unico `SaleFee` con monto positivo; fijo es
    `total - variable` (el fijo de venta mas el de publicacion).
    Restriccion de diseno (espejo de Amazon): un fee puramente fijo
    (variable 0) no parte y la unidad queda `incompleta` ruidosa.
    """
    variables = [det for det in detalles if det.fee_type == "SaleFee" and det.final_fee > 0]
    if len(variables) != 1:
        return "fee_incompatible"
    return (variables[0].final_fee, fee_total - variables[0].final_fee)


def construir_oferta_canonical_input_meli(
    *,
    listing_id: int,
    seller_sku: str,
    item_id: str,
    pub_obs_id: int,
    precio: Decimal,
    moneda: str,
    categoria_id: str | None,
    listing_type_id: str | None,
    estado: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """Identidad de la oferta: observacion PG + entradas de la cotizacion."""
    return {
        "fuente": SOURCE_MELI,
        "listing_id": listing_id,
        "seller_sku": seller_sku,
        "item_id": item_id,
        "pub_obs_id": pub_obs_id,
        "platform": "meli",
        "canal": "meli",
        "price": str(precio),
        "price_currency": moneda,
        "categoria_id": categoria_id,
        "listing_type_id": listing_type_id,
        "estado": estado,
        "fetched_at": fetched_at.isoformat(),
    }


def construir_oferta_source_event_id_meli(canonical_input: dict[str, Any]) -> str:
    payload = json.dumps(canonical_input, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"meli-oferta:{digest}"


def construir_fee_canonical_input_meli(oferta: OfertaResuelta) -> dict[str, Any]:
    """Entrada de la cotizacion; sin `marketplace_id` (no es SP-API)."""
    vista = oferta.canonical_input if isinstance(oferta.canonical_input, dict) else {}
    return {
        "oferta_source_event_id": oferta.source_event_id,
        "seller_sku": oferta.seller_sku,
        "item_id": oferta.asin,
        "platform": oferta.platform,
        "canal": oferta.canal,
        "price_amount": str(oferta.price_amount),
        "price_currency": oferta.price_currency,
        "categoria_id": vista.get("categoria_id"),
        "listing_type_id": vista.get("listing_type_id"),
        "fetched_at": oferta.fetched_at.isoformat(),
    }


def construir_fee_outcome_canonical_meli(
    oferta: OfertaResuelta,
    resultado: ResultadoCotizacion,
    *,
    attempted_at: datetime,
    canonica_entrada: dict[str, Any],
) -> dict[str, Any]:
    """Identidad de persistencia: request + outcome normalizado."""
    obs = attempted_at if attempted_at.tzinfo else attempted_at.replace(tzinfo=UTC)
    obs_utc = obs.astimezone(UTC)
    if resultado.estado == "success":
        if resultado.fees_estimated_at is None or resultado.total_fees is None:
            raise FeesClientError("outcome success incompleto")
        fee_at = resultado.fees_estimated_at
        if fee_at.tzinfo is None:
            raise FeesClientError("fees_estimated_at debe ser tz-aware")
        return {
            **canonica_entrada,
            "outcome": "success",
            "fees_estimated_at": fee_at.astimezone(UTC).isoformat(),
            "total_fees": str(resultado.total_fees),
            "fee_details": resultado.fee_details,
        }
    return {
        **canonica_entrada,
        "outcome": "error",
        "error_code": resultado.error_code or "fee_error",
        "attempted_at": obs_utc.isoformat(),
    }


def construir_fee_source_event_id_meli(canonical_outcome: dict[str, Any]) -> str:
    payload = json.dumps(canonical_outcome, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"meli-fee:{digest}"


def construir_huella_meli(item_id: str, obs_id: int | None, motivo: str) -> str:
    """Huella estable de la transicion negativa (misma obs -> reutilizada)."""
    payload = json.dumps(
        {"item_id": item_id, "obs_id": obs_id, "motivo": motivo},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _variantes_sin_mapear(conn: psycopg.Connection, listing_id: int, variantes_obs: Any) -> bool:
    """La ultima obs trae variantes fuera de los miembros activos.

    CR-2 (CodeRabbit): el PUT manda solo los miembros y MeLi BORRA las
    omitidas; una huerfana frena la oferta (sin escenario, sin goal,
    sin escritura). Obs corrupta (no-lista, nodos rotos) tambien
    frena: no se puede probar completo. Arreglo vacio = sin info
    (semillas viejas): nada que comparar, no frena.
    """
    if not isinstance(variantes_obs, list):
        return True
    exts_obs: set[str] = set()
    for nodo in variantes_obs:
        if not isinstance(nodo, dict):
            return True
        ext = nodo.get("variante_ext")
        if not isinstance(ext, str):
            return True
        exts_obs.add(ext)
    if not exts_obs:
        return False
    exts_miembros = {ext for (ext,) in conn.execute(_SQL_MIEMBRO_EXTS, (listing_id,)).fetchall()}
    return bool(exts_obs - exts_miembros)


def resolver_oferta_meli(
    conn: psycopg.Connection,
    listing_id: int,
    *,
    ahora: datetime,
) -> ResultadoResolucionOferta:
    """Oferta comprable de la publicacion desde su ultima observacion.

    `seller_sku` es el ancla (`listing.seller_sku`: los triggers 0028
    exigen que oferta, fee y escenario casen con el listing). El que manda
    se conoce al sembrar y va en `miembro_product_id` de la fila.
    Sin observacion, cerrada, sin precio o sin moneda -> `oferta_ausente`
    (igual que Amazon colapsa precio nulo a ausencia); vieja (> 6 h, lo
    que tambien exige el trigger a todo disponible) -> `oferta_desactualizada`.
    Obs con variantes fuera de los miembros activos ->
    `variantes_sin_mapear` (CR-2: el PUT las borraria).
    """
    # Local: universo -> meli.estimacion; arriba seria ciclo.
    from app.estimacion_universo import universo_de

    fila = conn.execute(_SQL_LISTING, (listing_id,)).fetchone()
    if fila is None or fila[1] != "meli":
        return ResultadoResolucionOferta(motivo="oferta_ausente", oferta=None)
    _lid, _plat, item_id, ancla = fila
    pub = conn.execute(_SQL_PUB, (item_id,)).fetchone()
    if pub is None:
        return ResultadoResolucionOferta(motivo="oferta_ausente", oferta=None)
    (obs_id, _item, _metric, observado, estado, precio, moneda, tipo, categoria, variantes) = pub
    if estado != "active" or precio is None:
        return ResultadoResolucionOferta(motivo="oferta_ausente", oferta=None)
    if _variantes_sin_mapear(conn, listing_id, variantes):
        return ResultadoResolucionOferta(motivo="variantes_sin_mapear", oferta=None)
    uni = universo_de("meli", "meli")
    if uni is None:
        return ResultadoResolucionOferta(motivo="universo_no_soportado", oferta=None)
    if moneda != uni.moneda_venta:
        # Universo MXN-only (el sitio MLM cotiza MXN y el costo USD hacia
        # MXN no tiene tasa inversa): la muestra tomaria el monto en otra
        # moneda como si fuera venta. Rechazo limpio, no mezcla.
        return ResultadoResolucionOferta(motivo="universo_no_soportado", oferta=None)
    fetched = observado if observado.tzinfo else observado.replace(tzinfo=UTC)
    frescura = evaluar_frescura(fetched, ahora)
    if frescura == "futura":
        return ResultadoResolucionOferta(motivo="oferta_futura", oferta=None)
    if frescura == "desactualizada":
        return ResultadoResolucionOferta(motivo="oferta_desactualizada", oferta=None)

    canonica = construir_oferta_canonical_input_meli(
        listing_id=listing_id,
        seller_sku=ancla,
        item_id=item_id,
        pub_obs_id=obs_id,
        precio=precio,
        moneda=moneda,
        categoria_id=categoria,
        listing_type_id=tipo,
        estado=estado,
        fetched_at=fetched,
    )
    return ResultadoResolucionOferta(
        motivo=None,
        oferta=OfertaResuelta(
            listing_id=listing_id,
            platform="meli",
            seller_sku=ancla,
            asin=item_id,
            canal="meli",
            price_amount=precio,
            price_currency=moneda,
            fetched_at=fetched,
            canonical_input=canonica,
            context_fingerprint=construir_context_fingerprint("meli", precio, moneda, fetched),
            source_event_id=construir_oferta_source_event_id_meli(canonica),
        ),
    )


def _decimal(raw: Any, *, campo: str) -> Decimal:
    try:
        valor = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise FeesClientError(f"{campo} invalido") from exc
    if not valor.is_finite() or abs(valor) >= _MAX_DINERO:
        raise FeesClientError(f"{campo} fuera de rango")
    try:
        cuantizado = valor.quantize(_MAX_DECIMALES)
    except InvalidOperation as exc:
        raise FeesClientError(f"{campo} fuera de rango") from exc
    if valor != cuantizado:
        raise FeesClientError(f"{campo} excede 4 decimales")
    return cuantizado


class CotizadorMeli:
    """Fees reales de una oferta `meli/meli` a un precio."""

    def __init__(self, client: ClienteMeli) -> None:
        self._client = client

    def cotizar(
        self,
        oferta: OfertaResuelta,
        precio: Decimal,
        *,
        ahora: Callable[[], datetime],
    ) -> ResultadoCotizacion:
        # Local: universo -> meli.estimacion; arriba seria ciclo.
        from app.estimacion_universo import universo_de

        universo = universo_de(oferta.platform, oferta.canal)
        if universo is None or universo.cotizador is None or oferta.platform != "meli":
            return ResultadoCotizacion(
                estado="error",
                total_fees=None,
                fee_details=[],
                fees_estimated_at=None,
                error_code="fee_universo_no_soportado",
                fetched_at=ahora(),
            )
        try:
            if not isinstance(precio, Decimal) or not precio.is_finite() or precio <= 0:
                raise ValueError(f"precio a cotizar invalido: {precio!r}")
            if precio >= _MAX_DINERO:
                raise ValueError(f"precio a cotizar invalido: {precio!r}")
            centavos = precio.quantize(Decimal("0.01"))
            if precio != centavos:
                raise ValueError(f"precio a cotizar sin centavos exactos: {precio!r}")
        except InvalidOperation:
            raise ValueError(f"precio a cotizar invalido: {precio!r}") from None
        vista = oferta.canonical_input if isinstance(oferta.canonical_input, dict) else {}
        categoria = vista.get("categoria_id")
        tipo = vista.get("listing_type_id")
        if not categoria or not tipo:
            return ResultadoCotizacion(
                estado="error",
                total_fees=None,
                fee_details=[],
                fees_estimated_at=None,
                error_code="fee_incompatible",
                fetched_at=ahora(),
            )
        try:
            cuerpo, _fetched = self._client.get(
                _PATH_PRECIOS,
                {
                    "price": str(centavos),
                    "category_id": categoria,
                    "listing_type_id": tipo,
                },
            )
        except ReputacionError as exc:
            logger.warning("cotizacion meli fallo: %s", exc)
            return ResultadoCotizacion(
                estado="error",
                total_fees=None,
                fee_details=[],
                fees_estimated_at=None,
                error_code="fee_error",
                fetched_at=ahora(),
            )
        capturada = ahora()
        try:
            # Forma real (sonda M.0): `sale_fee_amount` trae venta+fijo
            # (1989 x 19.5 % = 387.855 -> 387.86 con fijo 0), el cargo de
            # publicar es `listing_fee_amount` (NO `listing_fee`), y todo
            # viene en `currency_id`. Clave ausente o moneda distinta es
            # contrato incompatible, nunca default silencioso.
            venta = _decimal(cuerpo.get("sale_fee_amount"), campo="sale_fee_amount")
            detalles = cuerpo.get("sale_fee_details")
            if not isinstance(detalles, dict):
                raise FeesClientError("sale_fee_details ausente")
            fijo = _decimal(detalles.get("fixed_fee"), campo="fixed_fee")
            if "listing_fee_amount" not in cuerpo:
                raise FeesClientError("listing_fee_amount ausente")
            publicacion = _decimal(cuerpo.get("listing_fee_amount"), campo="listing_fee_amount")
            moneda = cuerpo.get("currency_id")
            if moneda != oferta.price_currency:
                raise FeesClientError("currency_id distinta")
            # Supuesto: `sale_fee_amount` INCLUYE el fijo (M.0 sonda-meli.txt
            # :258-261 solo trae fijo 0 y no lo discrimina; el total es
            # robusto en ambos casos, el split no — verificar contra una
            # categoria con fijo > 0 antes de operar P* en firme).
            variable = (venta - fijo).quantize(_MAX_DECIMALES)
            if variable < 0 or fijo < 0 or publicacion < 0:
                raise FeesClientError("fee negativo")
            total = (variable + fijo + publicacion).quantize(_MAX_DECIMALES)
        except (FeesClientError, AttributeError, TypeError) as exc:
            if not isinstance(exc, FeesClientError):
                logger.warning("cotizacion meli ilegible: %s", type(exc).__name__)
            return ResultadoCotizacion(
                estado="error",
                total_fees=None,
                fee_details=[],
                fees_estimated_at=None,
                error_code="fee_contrato_incompatible",
                fetched_at=capturada,
            )
        return ResultadoCotizacion(
            estado="success",
            total_fees=total,
            fee_details=[
                {
                    "fee_type": "SaleFee",
                    "final_fee": str(variable),
                    "currency": moneda,
                },
                {
                    "fee_type": "SaleFeeFixed",
                    "final_fee": str(fijo),
                    "currency": moneda,
                },
                {
                    "fee_type": "ListingFee",
                    "final_fee": str(publicacion),
                    "currency": moneda,
                },
            ],
            fees_estimated_at=capturada,
            error_code=None,
            fetched_at=capturada,
        )
