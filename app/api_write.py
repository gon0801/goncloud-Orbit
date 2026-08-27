"""Router de ESCRITURA del optimizador (ORBIT 04, task 3.1).

La superficie de escritura bajo el MISMO prefijo `/api/ads-optimizer` que la
lectura (app/api.py): veto de cortes + reversas manuales (regla 7). Los goals
write llegan en 3.2 sobre esta misma auth. `/run` = Reject formal PERMANENTE:
el disparo del ciclo es el CLI por ssh, jamas un endpoint HTTP del ciclo.

AUTH (sellado 18, docs/APPLY.md §10.1): token estatico en el archivo
`api_write_token` dentro de `ORBIT_SECRETS_DIR` (0600 en el server), leido con
`register_secret` y comparado con `hmac.compare_digest`. SOLO header
`x-orbit-token`: la query string JAMAS autentica (test). Sin secrets dir, sin
archivo, archivo ilegible o vacio -> 503 FAIL-CLOSED (jamas fail-open: un
endpoint de escritura sin token conocido no existe).

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
de errores: ReversaNoAplicada -> 409 (precondicion), ReversaInexistente ->
404, NegativeIdNoResoluble -> 422, SinPerfilReversa -> 503 fail-closed. La
respuesta lleva `confirmada: bool` (false = la reversa quedo sellada como
fallo en el ledger; el detalle vive ahi, regla 10).
"""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app import apply
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


def _lee_token_escritura() -> str:
    """El token del archivo `<ORBIT_SECRETS_DIR>/api_write_token`.

    Fail-closed en TODA forma de fallo (jamas fail-open): sin dir, sin
    archivo, ilegible o vacio -> 503. El valor se registra en redaction para
    que jamas aparezca en logs ni errores."""
    secrets_dir = os.environ.get("ORBIT_SECRETS_DIR")
    if not secrets_dir:
        raise HTTPException(
            status_code=503,
            detail=(
                "ORBIT_SECRETS_DIR no esta definido: no se puede leer el token de"
                f" escritura ({ARCHIVO_TOKEN}); los endpoints de escritura quedan"
                " cerrados (fail-closed)"
            ),
        )
    ruta = Path(secrets_dir) / ARCHIVO_TOKEN
    try:
        contenido = ruta.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"no se pudo leer {ARCHIVO_TOKEN} dentro de ORBIT_SECRETS_DIR:"
                " los endpoints de escritura quedan cerrados (fail-closed)"
            ),
        ) from None
    token = contenido.strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{ARCHIVO_TOKEN} esta vacio: los endpoints de escritura quedan"
                " cerrados (fail-closed)"
            ),
        )
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
    queue_id: int = Field(ge=1)
    negative_id: str | int | None = None


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
# semantica: cada excepcion de apply tiene su codigo).
_ERRORES_REVERSA: dict[type[Exception], int] = {
    apply.ReversaNoAplicada: 409,
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
    """Reversa manual del negative aplicado (DELETE); negative_id opcional: sin
    el se resuelve del ultimo intento ok del ledger (422 si no es resoluble)."""
    try:
        return apply.reversa_manual(
            conn, tipo="negative", queue_id=cuerpo.queue_id, negative_id=cuerpo.negative_id
        )
    except tuple(_ERRORES_REVERSA) as exc:
        raise _error_reversa(exc) from None
