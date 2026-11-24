"""Persistencia de ledger, readback paginado y utilidades del aplicador de bids.

Extraido de `app/apply.py` (Ola D3a) como modulo hoja sin importar
`app.ads.write`: el cliente de escritura vive solo en `app/apply.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import psycopg
from psycopg.types.json import Json

from app import temporada_io
from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import PerfilAds, evaluar_perfiles
from app.apply_estados import TOPE_INTENTOS_CAMBIO as TOPE_INTENTOS
from app.redaction import scrub

if TYPE_CHECKING:
    from app.ads.write import AdsWriteClient

# Tope de paginas para readback por /list (page size v3 ~1000, cubre ~20k
# filas): evita colgar el ciclo ante un nextToken infinito. Si la cuenta supera
# este tope, una entidad mas alla aparece como ausente.
TOPE_PAGINAS_READBACK = 20

# Estados del WIRE en el readback por LIST (sellado por probe 2.5, 2026-08-26).
# ARCHIVED = operativamente muerto: NO confirma entidad viva ni pause verificado.
ESTADO_WIRE_ENABLED = "ENABLED"
ESTADO_WIRE_PAUSED = "PAUSED"
ESTADO_WIRE_ARCHIVED = "ARCHIVED"

# (kind de la entidad) -> (metodo del write client, path del LIST de
# readback, contenedor de la respuesta, campo del id para el cruce).
_KINDS_DECISORAS = {
    "keyword": ("bid_keyword", "/sp/keywords/list", "keywords", "keywordId"),
    "product_target": ("bid_target", "/sp/targets/list", "targetingClauses", "targetId"),
}

_SQL_IDENTIDAD = """
SELECT kind, external_id FROM ad_entity WHERE id = %s
"""

# El tope cuenta solo intentos 'normal': las reversas nunca consumen
# presupuesto de reintentos.
_SQL_COUNT_INTENTOS = """
SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'
"""

# F2 (A.3): la secuencia es GLOBAL por decision (max+1 sobre TODOS los tipos).
_SQL_MAX_SEQ = """
SELECT coalesce(max(seq), 0) FROM apply_attempt WHERE decision_id = %s
"""

_SQL_INSERT_LEDGER = """
INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)
VALUES (%s, %s, %s, %s, %s)
RETURNING id
"""

_SQL_SELLA_LEDGER = """
UPDATE apply_attempt SET ack = %s, resultado = %s, finished_at = now() WHERE id = %s
"""

_SQL_YA_APLICADA = """
SELECT EXISTS (
    SELECT 1 FROM decision_application WHERE decision_id = %s AND verify_ok IS TRUE
)
"""

_SQL_CONFIRMAR_RESUMEN = """
INSERT INTO decision_application (decision_id, confirmed_at, platform_ack, verify_ok)
VALUES (%s, now(), %s, %s)
ON CONFLICT (decision_id) DO UPDATE
   SET confirmed_at = EXCLUDED.confirmed_at,
       platform_ack = EXCLUDED.platform_ack,
       verify_ok = EXCLUDED.verify_ok
"""

_SQL_SELLA_CICLO_EJECUTOR = """
UPDATE decision_application SET applied_cycle_id = %s WHERE decision_id = %s
"""

_SQL_APPLIED_COUNT = """
UPDATE optimizer_cycle SET applied_count = coalesce(applied_count, 0) + 1 WHERE id = %s
"""

_SQL_ACTUALIZA_CACHE = """
UPDATE ad_entity_state SET current_bid = %s, synced_at = now() WHERE ad_entity_id = %s
"""

_SQL_HERMANA_POR_ADGROUP = """
SELECT count(*) FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'hermana' AND request_payload->>'adGroupId' = %s
"""

_SQL_INTENTOS_HERMANA_DETALLE = """
SELECT id, ack, finished_at IS NULL AS abierta FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'hermana' AND request_payload->>'adGroupId' = %s
 ORDER BY id
"""

_SQL_GUARDA_ACK = """
UPDATE apply_attempt SET ack = %s WHERE id = %s AND finished_at IS NULL
"""

_SQL_SELLA_RESULTADO = """
UPDATE apply_attempt SET resultado = %s, finished_at = now() WHERE id = %s
"""


def perfil_aceptado_de(
    credentials: AdsCredentials, platform: str, *, transport: httpx.BaseTransport | None = None
) -> PerfilAds | None:
    """Perfil ACEPTADO de la plataforma desde GET /v2/profiles + evaluar_perfiles.

    UNA fuente de la resolucion (regla 2): la fabrica del ciclo y la reversa
    manual comparten este camino — el profile_id JAMAS se inventa.
    """
    for perfil in evaluar_perfiles(AdsClient(credentials, transport=transport)):
        if perfil.aceptado and perfil.platform == platform:
            return perfil
    return None


def _ledger(
    conn: psycopg.Connection,
    decision_id: int,
    tipo: str,
    payload: dict,
    *,
    quota_cobrada: bool,
) -> int | None:
    """Nace la fila del ledger PRE-HTTP: seq = max(seq)+1 GLOBAL por
    decision_id. El TOPE 3 (sellado 10) cuenta SOLO intentos 'normal'.
    """
    if tipo == "normal":
        count = conn.execute(_SQL_COUNT_INTENTOS, (decision_id,)).fetchone()[0]
        if count >= TOPE_INTENTOS:
            return None
    seq = conn.execute(_SQL_MAX_SEQ, (decision_id,)).fetchone()[0] + 1
    return conn.execute(
        _SQL_INSERT_LEDGER,
        (decision_id, seq, tipo, Json(payload), quota_cobrada),
    ).fetchone()[0]


def _intentos_hermana(conn: psycopg.Connection, decision_id: int, ad_group_id: str) -> int:
    """Intentos POST de hermana acumulados para (decision, adGroupId)."""
    return conn.execute(_SQL_HERMANA_POR_ADGROUP, (decision_id, str(ad_group_id))).fetchone()[0]


def _intentos_hermana_detalle(
    conn: psycopg.Connection, decision_id: int, ad_group_id: str
) -> list[tuple[int, dict | None, bool]]:
    """Filas de hermana de (decision, adGroupId) con su ack y si sigue abierta."""
    return [
        (f[0], f[1], bool(f[2]))
        for f in conn.execute(
            _SQL_INTENTOS_HERMANA_DETALLE, (decision_id, str(ad_group_id))
        ).fetchall()
    ]


def _sella_ledger(
    conn: psycopg.Connection, id_attempt: int, ack: dict | None, resultado: str
) -> None:
    """Sella UNA vez (el trigger de 0002 lo hace cumplir)."""
    conn.execute(_SQL_SELLA_LEDGER, (Json(ack) if ack is not None else None, resultado, id_attempt))


def _guarda_ack(conn: psycopg.Connection, id_attempt: int, ack: dict) -> None:
    """Guarda el ack SIN sellar el resultado (F2, A.3 r3)."""
    conn.execute(_SQL_GUARDA_ACK, (Json(ack), id_attempt))


def _sella_resultado(conn: psycopg.Connection, id_attempt: int, resultado: str) -> None:
    """Sella resultado + finished_at de una fila cuyo ack ya puede estar guardado."""
    conn.execute(_SQL_SELLA_RESULTADO, (resultado, id_attempt))


def _identidad(conn: psycopg.Connection, ad_entity_id: int) -> tuple[str, str] | None:
    """(kind, external_id) de la entidad — el id EXTERNO es lo que viaja al payload."""
    fila = conn.execute(_SQL_IDENTIDAD, (ad_entity_id,)).fetchone()
    return (fila[0], fila[1]) if fila is not None else None


def _json_seguro(resp: httpx.Response) -> dict:
    """El JSON del ack SANEADO (GK9: el body pasa SIEMPRE por scrub)."""
    try:
        data = resp.json()
    except ValueError:
        return {"status": resp.status_code, "body": scrub(resp.text[:200])}
    if not isinstance(data, dict):
        return {"status": resp.status_code, "body": scrub(str(data)[:200])}
    return json.loads(scrub(json.dumps(data)))


def _fila_de_filas(filas, id_campo: str, external_id: str) -> dict:
    """El cruce de id PURO: la primera fila de `filas` cuyo id CRUZA con el pedido."""
    for fila in filas or []:
        if isinstance(fila, dict) and str(fila.get(id_campo)) == str(external_id):
            return fila
    return {}


def _fila_de_lista(resp: httpx.Response, contenedor: str, id_campo: str, external_id: str) -> dict:
    """La fila de ESTA entidad en UNA pagina de la respuesta del LIST."""
    try:
        cuerpo = resp.json()
    except ValueError:
        raise AdsApiError("readback malformado: 2xx sin JSON en el LIST") from None
    if not isinstance(cuerpo, dict):
        raise AdsApiError("readback malformado: el JSON del LIST no es un objeto")
    filas = cuerpo.get(contenedor)
    if not isinstance(filas, list):
        raise AdsApiError(f"readback malformado: el LIST no trae '{contenedor}' como lista")
    return _fila_de_filas(filas, id_campo, external_id)


def _fila_de_readback(
    cliente: AdsWriteClient, path: str, contenedor: str, id_campo: str, external_id: str
) -> dict:
    """La fila de ESTA entidad recorriendo TODAS las paginas del LIST de readback."""
    token: str | None = None
    for _ in range(TOPE_PAGINAS_READBACK):
        body = {"nextToken": token} if token else {}
        try:
            data = cliente.list_sellado(path, body).json()
        except ValueError:
            raise AdsApiError(f"readback malformado: 2xx sin JSON en POST {path}") from None
        if not isinstance(data, dict):
            raise AdsApiError(f"readback malformado: el JSON de POST {path} no es un objeto")
        filas = data.get(contenedor)
        if not isinstance(filas, list):
            raise AdsApiError(f"readback malformado: POST {path} no trae '{contenedor}' como lista")
        fila = _fila_de_filas(filas, id_campo, external_id)
        if fila:
            return fila
        token = data.get("nextToken")
        if not token:
            return {}
    return {}


def _bid_de_fila(fila: dict) -> Decimal | None:
    """El bid de la fila del LIST ya cruzada. None = sin bid legible."""
    bid = fila.get("bid")
    if bid is None:
        return None
    try:
        return Decimal(str(bid))
    except ArithmeticError:
        return None


def _estado_de_fila(fila: dict) -> str | None:
    """El estado de la fila del LIST ya cruzada, vocabulario UPPER del wire."""
    estado = fila.get("state")
    return estado if isinstance(estado, str) else None


def _bid_de_readback(
    cliente: AdsWriteClient, path: str, contenedor: str, id_campo: str, external_id: str
) -> Decimal | None:
    """El bid del READBACK paginado por LIST. None = sin bid legible."""
    return _bid_de_fila(_fila_de_readback(cliente, path, contenedor, id_campo, external_id))


def _estado_de_readback(
    cliente: AdsWriteClient, path: str, contenedor: str, id_campo: str, external_id: str
) -> str | None:
    """El estado del READBACK paginado por LIST."""
    return _estado_de_fila(_fila_de_readback(cliente, path, contenedor, id_campo, external_id))


def _bid_leido(
    resp: httpx.Response, contenedor: str, id_campo: str, external_id: str
) -> Decimal | None:
    """El bid del READBACK por LIST de UNA pagina, SOLO de la fila cuyo id CRUZA."""
    return _bid_de_fila(_fila_de_lista(resp, contenedor, id_campo, external_id))


def _estado_leido(
    resp: httpx.Response, contenedor: str, id_campo: str, external_id: str
) -> str | None:
    """El estado del READBACK por LIST de UNA pagina, SOLO de la fila cuyo id CRUZA."""
    return _estado_de_fila(_fila_de_lista(resp, contenedor, id_campo, external_id))


def _ya_aplicada(conn: psycopg.Connection, decision_id: int) -> bool:
    return conn.execute(_SQL_YA_APLICADA, (decision_id,)).fetchone()[0]


def _confirma_resumen(
    conn: psycopg.Connection,
    decision_id: int,
    ack: dict,
    verify_ok: bool,
    cycle_id_ejecutor: int,
    *,
    leido: Decimal | None = None,
) -> None:
    """UPSERT del resumen y cierre atomico del efecto de temporada (TEMPORADA 01 B)."""
    conn.execute(_SQL_CONFIRMAR_RESUMEN, (decision_id, Json(ack), verify_ok))
    if verify_ok:
        conn.execute(_SQL_SELLA_CICLO_EJECUTOR, (cycle_id_ejecutor, decision_id))
        conn.execute(_SQL_APPLIED_COUNT, (cycle_id_ejecutor,))
    temporada_io.cierra_efecto_resumen(
        conn, decision_id=decision_id, verify_ok=verify_ok, leido=leido
    )


def _actualiza_cache(conn: psycopg.Connection, ad_entity_id: int, bid_leido: Decimal) -> None:
    """El cache queda con LO LEIDO del readback (sellado 16)."""
    conn.execute(_SQL_ACTUALIZA_CACHE, (bid_leido, ad_entity_id))


def _tick(tick: Callable[[], None] | None) -> None:
    if tick is not None:
        tick()
