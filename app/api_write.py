"""Router de ESCRITURA del optimizador (ORBIT 04, task 3.1).

La superficie de escritura bajo el MISMO prefijo `/api/ads-optimizer` que la
lectura (app/api.py): veto de cortes, reversas manuales (regla 7) y edicion
de goals (3.2, sellado 26 — despacha a app/goals_write.edita_goal, un solo
camino con el CLI). `/run` = Reject formal PERMANENTE: el disparo del ciclo
es el CLI por ssh, jamas un endpoint HTTP del ciclo.

AUTH (sellado 18, docs/APPLY.md §10.1): token estatico en el archivo
`api_write_token` dentro de `ORBIT_SECRETS_DIR` (0600 en el server), leido con
`register_secret` y comparado con `hmac.compare_digest`. SOLO header
`x-orbit-token`: la query string JAMAS autentica (con test). Sin secrets dir, sin
archivo, archivo ilegible o vacio -> 503 FAIL-CLOSED con detalle GENERICO
(ADV-4: la razon especifica va solo al logger; un caller sin autenticar no
recibe un oraculo del estado interno; jamas fail-open).

CONEXION: `ConexionEscritura` abre `ORBIT_DSN_ADMIN` (usuario LOGIN
`orbit_admin` -> rol `app_admin`; docs/DEPLOY.md) SIN autocommit — las
reversas gestionan sus transacciones con conn.commit()/conn.transaction().
Sin DSN -> 503 fail-closed con mensaje claro (jamas el DSN crudo: ya redactado
por app.db.connect). En cada endpoint la dependencia de token va PRIMERO que
la de conexion: el guard de auth corre antes de abrir el DSN (sin token Y sin
DSN -> 401).

VETO (sellados 3/4): UPDATE atomico `pending_veto|released -> vetoed` con
actor, rastro y vence_el editable (default 30d). Corre como admin porque el
trigger de 0002 exige pg_has_role(current_user, 'app_admin'): el rol del motor
JAMAS veta. applying es punto de no retorno (409 "en vuelo"); terminales 409;
inexistente 404.

REVERSAS: despachan a `apply.reversa_manual` (el UNICO punto fuera del ciclo
que construye el write client, candado de test_architecture intacto). Mapeo
de errores: ReversaNoAplicada -> 409 (precondicion), ReversaYaHecha -> 409
"ya revertida" (ADV-3: una reversa es UNA por decision, regla 7), ReversaInexistente ->
404, NegativeIdNoResoluble -> 422, SinPerfilReversa -> 503 fail-closed. El
negative_id del DELETE sale SIEMPRE del ack del ledger de ESA fila (ADV-2: el
body JAMAS lo acepta, regla 1 — borrar el negativo de OTRO con un id a mano
es imposible). La respuesta lleva `confirmada: bool` (false = la reversa quedo
sellada como fallo en el ledger; el detalle vive ahi, regla 10).
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app import apply, goals_write
from app.db import OrbitDbError, connect
from app.redaction import install_scrub_filter, register_secret

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

router = APIRouter(prefix="/api/ads-optimizer", tags=["ads-optimizer-write"])

# Token estatico de escritura: solo header, archivo plano en el secrets dir
# (rotacion en docs/DEPLOY.md "Rotacion del token de escritura", APPLY.md §11b).
HEADER_TOKEN = "x-orbit-token"
ARCHIVO_TOKEN = "api_write_token"

# Veto DURABLE por defecto: 30 dias, editable al vetar (sellado 3).
VENCIMIENTO_VETO_DEFAULT_DIAS = 30


# ADV-4: TODOS los 503 de configuracion del token comparten UN detalle
# generico — la razon especifica va SOLO al logger.warning (scrubbed): un
# caller sin autenticar no recibe un oraculo del estado interno del server.
# El 503 de ORBIT_DSN_ADMIN ausente NO se toca (mensaje estandar del repo).
DETAIL_TOKEN_503 = "escrituras no disponibles: configuracion de token incompleta"


def _lee_token_escritura() -> str:
    """El token del archivo `<ORBIT_SECRETS_DIR>/api_write_token`.

    Fail-closed en TODA forma de fallo (jamas fail-open): sin dir, sin
    archivo, ilegible o vacio -> 503 con detalle GENERICO (ADV-4); la razon
    especifica solo va al logger (scrubbed por el filtro del modulo). El
    valor se registra en redaction para que jamas aparezca en logs ni
    errores."""
    secrets_dir = os.environ.get("ORBIT_SECRETS_DIR")
    if not secrets_dir:
        logger.warning("token de escritura ilegible: ORBIT_SECRETS_DIR no esta definido")
        raise HTTPException(status_code=503, detail=DETAIL_TOKEN_503)
    ruta = Path(secrets_dir) / ARCHIVO_TOKEN
    try:
        contenido = ruta.read_text(encoding="utf-8")
    except OSError:
        logger.warning("token de escritura ilegible: %s", ruta)
        raise HTTPException(status_code=503, detail=DETAIL_TOKEN_503) from None
    token = contenido.strip()
    if not token:
        logger.warning("token de escritura ilegible: %s esta vacio", ruta)
        raise HTTPException(status_code=503, detail=DETAIL_TOKEN_503)
    register_secret(token)
    return token


def exige_token(request: Request) -> str:
    """Dependencia de auth: SOLO el header `x-orbit-token`.

    La query string JAMAS autentica (sellado 18, con test). Comparacion con
    `hmac.compare_digest` (sin timing oracles). Sin header o distinto -> 401."""
    token_esperado = _lee_token_escritura()
    recibido = request.headers.get(HEADER_TOKEN)
    if recibido is None:
        raise HTTPException(status_code=401, detail=f"falta el header {HEADER_TOKEN}")
    if not hmac.compare_digest(recibido.encode(), token_esperado.encode()):
        raise HTTPException(status_code=401, detail=f"{HEADER_TOKEN} invalido")
    return recibido


def _conexion_escritura():
    """Dependency: conexion como rol admin (ORBIT_DSN_ADMIN).

    Fail-closed: sin DSN -> 503 con mensaje claro; fallo de conexion -> 503
    con el DSN ya redactado por `app.db.connect` (jamas el DSN crudo). SIN
    autocommit: el veto hace commit explicito y las reversas gestionan sus
    transacciones (ledger pre-HTTP + sellos)."""
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        raise HTTPException(
            status_code=503,
            detail="ORBIT_DSN_ADMIN no esta definido: la API de escritura no puede conectar",
        )
    try:
        conn = connect(dsn)
    except OrbitDbError as exc:
        logger.warning("no se pudo conectar la API de escritura: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from None
    try:
        yield conn
    finally:
        conn.close()


ConexionEscritura = Annotated[psycopg.Connection, Depends(_conexion_escritura)]


class CuerpoVeto(BaseModel):
    queue_id: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=200)
    dias: int = Field(default=VENCIMIENTO_VETO_DEFAULT_DIAS, ge=1, le=365)


class CuerpoReversaBid(BaseModel):
    decision_id: int = Field(ge=1)


class CuerpoReversaPause(BaseModel):
    queue_id: int = Field(ge=1)


class CuerpoReversaNegative(BaseModel):
    """ADV-2: el body JAMAS acepta negative_id — el id del DELETE sale del
    ack del ledger de ESA fila (regla 1, una fuente); borrar el negativo de
    OTRO con un id a mano es imposible. Pydantic ignora extras: un
    negative_id en el body no viaja a ningun lado."""

    queue_id: int = Field(ge=1)


class CuerpoGoal(BaseModel):
    """Edicion de goals (3.2, sellado 26): TODOS los campos opcionales — None
    = no cambiar. Montos gt=0 y strings no vacios (min_length=1) validados
    aqui (el resto de la pre-validacion, que combina con los valores
    EXISTENTES de la fila y caza whitespace-only, vive en
    goals_write.edita_goal: una sola implementacion CLI/endpoint). Extras
    ignorados (pydantic default)."""

    target_acos_pct: Decimal | None = Field(default=None, gt=0)
    enabled: bool | None = None
    bid_floor: Decimal | None = Field(default=None, gt=0)
    bid_ceiling: Decimal | None = Field(default=None, gt=0)
    harvest_campaign_id: str | None = Field(default=None, min_length=1)
    harvest_ad_group_id: str | None = Field(default=None, min_length=1)
    harvest_default_bid: Decimal | None = Field(default=None, gt=0)
    harvest_limpia: bool = False


# Transicion atomica del veto: el WHERE de estados ES la carrera contra el
# claim del motor (released -> applying) y contra un segundo veto; rowcount 0
# = alguien mas movio la fila y se relee para responder 404/409 honesto.
# make_interval fija el vencimiento al reloj de la BASE (vence_el TSTZ).
_SQL_VETO = """
UPDATE apply_queue
   SET estado = 'vetoed', vence_el = now() + make_interval(days => %s),
       vetoed_at = now(), vetoed_by = %s
 WHERE id = %s AND estado IN ('pending_veto', 'released')
RETURNING id, estado, vence_el, vetoed_at, vetoed_by
"""

# dict_row activo cuando se lee: la columna llega por nombre ("estado").
_SQL_ESTADO_FILA = "SELECT estado FROM apply_queue WHERE id = %s"


@router.post("/veto")
def veto(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoVeto,
) -> dict:
    """Veta un corte de la cola: `vetoed` con actor, rastro y vence_el editable.

    Corre con el DSN admin (el trigger de 0002 exige pg_has_role(current_user,
    'app_admin')). pending_veto y released son vetables (released sigue
    vetable mientras espera quota, r2 grok); applying es punto de no retorno
    (409 en vuelo); los terminales son inmutables (409); inexistente 404."""
    conn.row_factory = dict_row
    fila = conn.execute(_SQL_VETO, (cuerpo.dias, cuerpo.actor, cuerpo.queue_id)).fetchone()
    if fila is None:
        estado = conn.execute(_SQL_ESTADO_FILA, (cuerpo.queue_id,)).fetchone()
        if estado is None:
            raise HTTPException(status_code=404, detail=f"queue_id {cuerpo.queue_id} no existe")
        # dict_row activo: la columna se lee por nombre (AS estado), jamas por indice
        estado_fila = estado["estado"]
        if estado_fila == "applying":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"fila {cuerpo.queue_id} en vuelo (applying), no vetable: SOLO"
                    " applying es punto de no retorno (sellado 4)"
                ),
            )
        raise HTTPException(
            status_code=409,
            detail=f"fila {cuerpo.queue_id} en estado terminal {estado_fila}: no vetable",
        )
    conn.commit()
    return {
        "id": fila["id"],
        "estado": fila["estado"],
        "vence_el": fila["vence_el"].isoformat(),
        "vetoed_at": fila["vetoed_at"].isoformat(),
        "vetoed_by": fila["vetoed_by"],
    }


# Mapeo sellado de errores de reversa_manual -> HTTP (el endpoint no inventa
# semantica: cada excepcion de apply tiene su codigo). ADV-3: ReversaYaHecha
# -> 409 "ya revertida" (una reversa es UNA por decision, regla 7).
_ERRORES_REVERSA: dict[type[Exception], int] = {
    apply.ReversaNoAplicada: 409,
    apply.ReversaYaHecha: 409,
    apply.ReversaInexistente: 404,
    apply.NegativeIdNoResoluble: 422,
    apply.SinPerfilReversa: 503,
}


def _error_reversa(exc: Exception) -> HTTPException:
    return HTTPException(status_code=_ERRORES_REVERSA[type(exc)], detail=str(exc))


@router.post("/reversa/bid")
def reversa_bid(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoReversaBid,
) -> dict:
    """Reversa manual del bid de una decision confirmada (PUT con old_value)."""
    try:
        return apply.reversa_manual(conn, tipo="bid", decision_id=cuerpo.decision_id)
    except tuple(_ERRORES_REVERSA) as exc:
        raise _error_reversa(exc) from None


@router.post("/reversa/pause")
def reversa_pause(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoReversaPause,
) -> dict:
    """Reversa manual del pause aplicado de una fila de la cola (resume)."""
    try:
        return apply.reversa_manual(conn, tipo="pause", queue_id=cuerpo.queue_id)
    except tuple(_ERRORES_REVERSA) as exc:
        raise _error_reversa(exc) from None


@router.post("/reversa/negative")
def reversa_negative(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    cuerpo: CuerpoReversaNegative,
) -> dict:
    """Reversa manual del negative aplicado (DELETE del id creado). El id sale
    SIEMPRE del ack del ledger de ESA fila (ADV-2: el body no lo acepta; 422 si
    el ledger no lo resuelve)."""
    try:
        return apply.reversa_manual(conn, tipo="negative", queue_id=cuerpo.queue_id)
    except tuple(_ERRORES_REVERSA) as exc:
        raise _error_reversa(exc) from None


# Mapeo sellado de errores de edita_goal -> HTTP (mismo principio que las
# reversas: el endpoint no inventa semantica). GoalInvalido -> 422 con el
# MOTIVO como detail (la pre-validacion combina nuevo+existente); 404 para el
# goal inexistente.
_ERRORES_GOAL: dict[type[Exception], int] = {
    goals_write.GoalInvalido: 422,
    goals_write.GoalInexistente: 404,
}


@router.post("/goals/{goal_id}")
def editar_goal(
    _token: Annotated[str, Depends(exige_token)],
    conn: ConexionEscritura,
    goal_id: int,
    cuerpo: CuerpoGoal,
) -> dict:
    """Edita un goal del optimizador (3.2, sellado 26): target/enabled/
    floor/ceiling y los campos harvest_*, con la auth de 3.1 (token solo
    header, DSN admin). DESPACHA a `goals_write.edita_goal` — el UNICO camino
    de escritura de ads_optimizer_goal (regla 1; el CLI `goals set` llama a la
    misma funcion). El `updated_at` explicito lo pone AQUI: now UTC del
    servidor (no hay trigger que lo mantenga; dashboard-01 r2). Respuesta: la
    fila actualizada, mismo shape que GET /goals."""
    try:
        return goals_write.edita_goal(
            conn,
            goal_id,
            target_acos_pct=cuerpo.target_acos_pct,
            enabled=cuerpo.enabled,
            bid_floor=cuerpo.bid_floor,
            bid_ceiling=cuerpo.bid_ceiling,
            harvest_campaign_id=cuerpo.harvest_campaign_id,
            harvest_ad_group_id=cuerpo.harvest_ad_group_id,
            harvest_default_bid=cuerpo.harvest_default_bid,
            harvest_limpia=cuerpo.harvest_limpia,
            updated_at=dt.datetime.now(dt.UTC),
        )
    except tuple(_ERRORES_GOAL) as exc:
        raise HTTPException(status_code=_ERRORES_GOAL[type(exc)], detail=str(exc)) from None
