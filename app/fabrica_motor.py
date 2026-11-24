"""Motor de ejecucion, ledger, readback y reversa de la fabrica de campanas.

Extraido de `tools/fabrica_campanas.py` en Fase 3.2: elimina la inversion de
dependencias `app/ -> tools/` y sustituye adaptadores `SimpleNamespace` por
entradas tipadas (`mutar`, `registrar_lote`, `desarmar_lote`, `reconciliar_lote`).

ARREGLOS 05 D: toda mutacion sale por `app.ads.write.AdsWriteClient` (ruta
sellada, vendor de la allowlist y perfil de la instancia). Este modulo ya no
trae token LWA, headers ni `httpx.Client` propios: conserva su ceremonia, su
ledger `fabrica_lote_paso` (payload e ID+ACK antes del LIST) y su readback.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import psycopg

if TYPE_CHECKING:  # solo anotaciones: el transporte HTTP lo pone el cliente
    import httpx

from app import fabrica_plan as fp
from app import fabrica_registro
from app.ads import sp_wire
from app.ads.client import DEFAULT_BASE_URL, AdsApiError, AdsAuthError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import evaluar_perfiles
from app.ads.write import MODO_CONFIRMADO_LIVE, AdsWriteClient
from app.db import connect
from app.fabrica_datos import Abortar, _roles_del_plan
from app.fabrica_registro import (
    _cierra,
    _dsn_admin,
    _dsn_ingest,
    _expresion_normalizada,
    _log,
    _plan_como_json,
)
from app.redaction import scrub

API = DEFAULT_BASE_URL
_PAUSADA = "PAUSED"
# Ante cualquier 5xx, Amazon Ads pudo haber creado el recurso antes de responder.
_HTTP_INCERTO = range(500, 600)

_SQL_INSERTA_LOTE = """
INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal, huella,
                          plan, modo_goal, estado)
VALUES (%s, %s::platform, %s, %s, %s, %s, %s::jsonb, %s, 'planeado')
"""
_SQL_BLOQUEA_LOTE = "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))"
_SQL_DESBLOQUEA_LOTE = "SELECT pg_advisory_unlock(hashtextextended(%s, 0))"
_SQL_ESTADO_LOTE = "SELECT estado FROM fabrica_lote WHERE lote = %s"
_SQL_SELLA_LOTE = """
UPDATE fabrica_lote SET estado = %s, detalle = %s, finished_at = now() WHERE lote = %s
"""
_SQL_SELLA_LOTE_CON_PAUSA = """
UPDATE fabrica_lote
   SET estado = %s, detalle = %s, pausado_por = %s, finished_at = now()
 WHERE lote = %s
"""
_SIN_CAMBIO_PAUSA = object()
_SQL_EXTERNALS_CAMPANA_DEL_LOTE = """
SELECT DISTINCT external_id FROM fabrica_lote_paso
 WHERE lote = %s AND recurso = 'campaign' AND external_id IS NOT NULL
"""
_SQL_INSERTA_PASO = """
INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload, estado)
VALUES (%s, %s, %s::campana_rol, %s, %s::jsonb, 'planeado')
RETURNING id
"""
_SQL_SELLA_PASO_APPLIED = """
UPDATE fabrica_lote_paso
   SET estado = 'applied', external_id = %s, ack = %s::jsonb, readback_estado = %s
 WHERE id = %s
"""
_SQL_SELLA_PASO_FAILED = """
UPDATE fabrica_lote_paso
   SET estado = 'failed', external_id = %s, ack = %s::jsonb, readback_estado = %s
 WHERE id = %s
"""
# D-CURSOR-177-F2: ID+ACK durables ANTES del LIST; el paso sigue planeado
# hasta que el readback verifique (applied exige readback_estado en el CHECK).
_SQL_SELLA_PASO_ACK = """
UPDATE fabrica_lote_paso
   SET external_id = %s, ack = %s::jsonb
 WHERE id = %s AND estado = 'planeado'
"""


@dataclass
class _Ctx:
    escritor: Any
    cliente_lectura: Any
    profile: int
    conn_admin: psycopg.Connection
    lote: str
    orden: int = 0
    conocidos: list = field(default_factory=list)
    crear_pausado: bool = False


def _lote_nuevo(huella: str) -> str:
    """Identidad durable de la corrida CLI autorizada por una huella."""
    return f"cli-{huella}"


def _escritor(cred: AdsCredentials, platform: str, profile: int) -> AdsWriteClient:
    """UN escritor sellado por plataforma para este lote.

    Solo se construye DESPUES de la ceremonia (`--acepto-mutacion-real`,
    `--go`, `--huella`, `--esperado`), que es la autorizacion real del dueno:
    el cliente vuelve a exigir el modo live antes de cualquier HTTP y sella el
    perfil, asi que ningun paso posterior elige ruta ni cuenta.
    """
    return AdsWriteClient(
        cred,
        platform=platform,
        profile_id=profile,
        modo_confirmado=MODO_CONFIRMADO_LIVE,
    )


def _perfiles(cliente_lectura) -> dict[str, int]:
    return {
        p.platform: p.profile_id
        for p in evaluar_perfiles(cliente_lectura)
        if p.aceptado and p.platform and p.profile_id is not None
    }


def _bootstrap_amazon(
    plataforma: str | None = None,
    *,
    error_sin_perfil: str | None = None,
) -> tuple[AdsCredentials, AdsClient, dict[str, int]]:
    """Arranque unico de credenciales + cliente de lectura + perfiles aceptados."""
    cred = AdsCredentials.from_secrets_dir()
    cliente_lectura = AdsClient(cred)
    perfiles = _perfiles(cliente_lectura)
    if plataforma is not None and plataforma not in perfiles:
        raise Abortar(error_sin_perfil or f"sin perfil aceptado para {plataforma}")
    return cred, cliente_lectura, perfiles


def errores_207(ack: dict) -> list:
    """Errores por item de un ACK 207, con la gramatica unica de `sp_wire`.

    Un cuerpo que no es objeto no es un exito: se reporta como error."""
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return [{"no_json": ack.get("texto")}]
    return sp_wire.errores_de_ack(cuerpo)


def ack_ok(ack: dict) -> bool:
    """2xx sin errores por item. El juicio es del dueno del ledger."""
    return ack.get("status") in (200, 207) and not errores_207(ack)


def id_creado(ack: dict, clave: str) -> str | None:
    """Id del objeto creado segun el ack (mismo lector que el resto)."""
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return None
    return sp_wire.id_de_ack(cuerpo, clave)


def _decimal_wire(valor: object) -> Decimal:
    """El payload del ledger ya trae el numero cuantizado (`fp.monto_wire`);
    volver a Decimal no inventa precision y deja que el cliente lo revalide."""
    return Decimal(str(valor))


def _crea_paso(escritor: AdsWriteClient, paso: fp.Paso, payload: dict) -> httpx.Response:
    """Manda UN paso de creacion por la operacion especifica del cliente.

    El payload es el que el ledger guardo antes del HTTP (`fabrica_plan`); aqui
    solo se traduce a la firma tipada, que revalida montos, enums y la moneda
    de la plataforma ANTES de la red. Devuelve la respuesta cruda (el ACK).
    """
    recurso = paso.recurso
    if recurso == "campaign":
        return escritor.crear_campana(
            nombre=str(payload["name"]),
            targeting_type=str(payload["targetingType"]),
            estado=str(payload["state"]),
            presupuesto=_decimal_wire(payload["budget"]["budget"]),
            moneda=escritor.moneda,
            fecha_inicio=dt.date.fromisoformat(str(payload["startDate"])),
        )
    if recurso == "ad_group":
        return escritor.crear_ad_group(
            payload["campaignId"],
            nombre=str(payload["name"]),
            estado=str(payload["state"]),
            default_bid=_decimal_wire(payload["defaultBid"]),
            moneda=escritor.moneda,
        )
    if recurso == "product_ad":
        return escritor.crear_product_ad(
            payload["adGroupId"], payload["campaignId"], str(payload["sku"]), str(payload["state"])
        )
    if recurso == "keyword":
        return escritor.crear_keyword(
            payload["adGroupId"],
            payload["campaignId"],
            str(payload["keywordText"]),
            _decimal_wire(payload["bid"]),
            escritor.moneda,
            match_type=str(payload["matchType"]),
            estado=str(payload["state"]),
        )
    if recurso == "target":
        (expresion,) = payload["expression"]
        return escritor.crear_target_asin(
            payload["adGroupId"],
            payload["campaignId"],
            str(expresion["value"]),
            _decimal_wire(payload["bid"]),
            escritor.moneda,
            estado=str(payload["state"]),
        )
    if recurso == "negative_keyword":
        return escritor.crear_negative_exacto(
            payload["adGroupId"], payload["campaignId"], str(payload["keywordText"])
        )
    raise Abortar(f"recurso sin operacion en el cliente protegido: {recurso}")


def _mutacion_de_paso(escritor: AdsWriteClient, paso: fp.Paso, payload: dict) -> dict:
    """ACK canonico de una creacion; un fallo del cliente tambien es un ACK."""
    try:
        return sp_wire.ack_de(_crea_paso(escritor, paso, payload))
    except AdsApiError as exc:
        return sp_wire.ack_de_error(exc)
    except Exception as exc:  # noqa: BLE001 - pudo salir: se sella incierto
        return {
            "status": sp_wire.ACK_SIN_RESPUESTA,
            "cuerpo": {},
            "texto": scrub(str(exc))[:300],
        }


def _readback(cliente_lectura, profile: int, path_create: str, external: str) -> dict | None:
    path_list = fp.LIST_POR_PATH[path_create]
    clave = fp.CLAVE_ID_POR_PATH[path_create]
    try:
        resp = cliente_lectura.list_objects(
            path_list,
            {fp.FILTRO_ID_POR_LIST[path_list]: {"include": [str(external)]}},
            profile_id=profile,
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        cuerpo = resp.json()
    except Exception:
        return None
    if not isinstance(cuerpo, dict):
        return None
    contenedor = cuerpo.get(fp.CONTENEDOR_POR_LIST[path_list])
    if not isinstance(contenedor, list):
        return None
    for fila in contenedor:
        if isinstance(fila, dict) and str(fila.get(clave)) == str(external):
            return fila
    return None


def _monto_wire_cuadra(crudo_leido: Any, crudo_pedido: Any) -> bool:
    if crudo_leido is None:
        return False
    try:
        pedido = Decimal(str(crudo_pedido)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        leido = Decimal(str(crudo_leido)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError):
        return False
    return leido == pedido


def _readback_cuadra(leido: dict | None, payload: dict) -> bool:
    esperado = payload.get("state", fp.ESTADO_NUEVO)
    if not isinstance(leido, dict) or leido.get("state") != esperado:
        return False
    for clave in (
        "keywordText",
        "matchType",
        "sku",
        "name",
        "targetingType",
        "campaignId",
        "adGroupId",
    ):
        if clave in payload and str(leido.get(clave)) != str(payload[clave]):
            return False
    if "expression" in payload and (
        _expresion_normalizada(leido.get("expression"))
        != _expresion_normalizada(payload["expression"])
    ):
        return False
    for clave in ("bid", "defaultBid"):
        if clave in payload and not _monto_wire_cuadra(leido.get(clave), payload[clave]):
            return False
    pedido_budget = payload.get("budget")
    if isinstance(pedido_budget, dict) and "budget" in pedido_budget:
        leido_budget = leido.get("budget")
        if not isinstance(leido_budget, dict) or not _monto_wire_cuadra(
            leido_budget.get("budget"), pedido_budget["budget"]
        ):
            return False
        if "budgetType" in pedido_budget and str(leido_budget.get("budgetType")) != str(
            pedido_budget["budgetType"]
        ):
            return False
    return True


def _inserta_lote(conn, lote: str, plan: fp.PlanGrupoV2, huella: str, go: str) -> None:
    conn.execute(
        _SQL_INSERTA_LOTE,
        (
            lote,
            plan.platform,
            plan.tipo_producto,
            plan.nombre_base,
            go,
            huella,
            json.dumps(_plan_como_json(plan)),
            plan.modo,
        ),
    )
    conn.commit()


def _estado_lote(conn, lote: str) -> str | None:
    fila = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
    if fila is None:
        return None
    return fila[0] if not isinstance(fila, dict) else fila["estado"]


def _bloquea_lote(conn, lote: str) -> None:
    """Lock de SESION compartido con fabrica_web sobre hashtextextended(lote, 0)."""
    fila = conn.execute(_SQL_BLOQUEA_LOTE, (lote,)).fetchone()
    conn.commit()
    if fila is not None and not fila[0]:
        raise Abortar(f"lote {lote} ocupado por otra operacion en curso: reintenta")


def _desbloquea_lote(conn, lote: str) -> None:
    try:
        conn.execute(_SQL_DESBLOQUEA_LOTE, (lote,))
        conn.commit()
    except Exception:
        pass


def _sella_lote(
    conn,
    lote: str,
    estado: str,
    detalle: str | None,
    *,
    pausado_por: Any = _SIN_CAMBIO_PAUSA,
) -> None:
    if pausado_por is _SIN_CAMBIO_PAUSA:
        conn.execute(_SQL_SELLA_LOTE, (estado, detalle, lote))
    else:
        conn.execute(_SQL_SELLA_LOTE_CON_PAUSA, (estado, detalle, pausado_por, lote))
    conn.commit()


def _inserta_paso(ctx: _Ctx, paso: fp.Paso, payload: dict) -> int:
    ctx.orden += 1
    fila = ctx.conn_admin.execute(
        _SQL_INSERTA_PASO, (ctx.lote, ctx.orden, paso.rol, paso.recurso, json.dumps(payload))
    ).fetchone()
    ctx.conn_admin.commit()
    return fila[0]


def _sella_paso(ctx: _Ctx, paso_id: int, ok: bool, external, ack: dict, readback) -> None:
    sql = _SQL_SELLA_PASO_APPLIED if ok else _SQL_SELLA_PASO_FAILED
    ctx.conn_admin.execute(sql, (external, json.dumps(ack, default=str), readback, paso_id))
    ctx.conn_admin.commit()


def _guarda_ack(ctx: _Ctx, paso_id: int, external: str, ack: dict) -> None:
    """D-CURSOR-177-F2: persiste ID+ACK con COMMIT antes del sleep/LIST."""
    ctx.conn_admin.execute(_SQL_SELLA_PASO_ACK, (external, json.dumps(ack, default=str), paso_id))
    ctx.conn_admin.commit()


def _falla_paso(ctx: _Ctx, paso: fp.Paso, paso_id: int, ack: dict, external, motivo: str) -> None:
    _sella_paso(ctx, paso_id, False, external, ack, None)
    raise Abortar(motivo)


def _motivo_post_fallido(ctx: _Ctx, paso: fp.Paso, ack: dict) -> str:
    status = ack.get("status")
    if status == "excepcion" or status in _HTTP_INCERTO:
        nacido = (
            "un PAUSED que no gasta"
            if ctx.crear_pausado and paso.recurso == "campaign"
            else "un ENABLED"
        )
        return (
            f"{paso.descripcion} ({paso.rol}) INCERTO (status {status}):"
            f" Amazon pudo crear {nacido}; no reintentar este POST;"
            " --desarmar solo pausa filas con external_id"
        )
    return f"{paso.descripcion} ({paso.rol}) rechazado (status {status})"


def _ejecuta_paso(ctx: _Ctx, paso: fp.Paso, padres: dict) -> str:
    payload = {**padres, **paso.payload}
    if ctx.crear_pausado and paso.recurso == "campaign":
        # Camino impulso: nacen PAUSED y se activan al final, con el
        # lote completo y verificado. Un 500 incierto deja una fantasma
        # que no gasta (sin vigia no se sirve nada).
        payload["state"] = _PAUSADA
    paso_id = _inserta_paso(ctx, paso, payload)
    ack = _mutacion_de_paso(ctx.escritor, paso, payload)
    if not ack_ok(ack):
        _falla_paso(ctx, paso, paso_id, ack, None, _motivo_post_fallido(ctx, paso, ack))
    external = id_creado(ack, fp.CLAVE_ID_POR_PATH[paso.path])
    if external is None:
        _falla_paso(
            ctx,
            paso,
            paso_id,
            ack,
            None,
            f"{paso.descripcion} ({paso.rol}) INCERTO: ack sin"
            f" {fp.CLAVE_ID_POR_PATH[paso.path]}; Amazon pudo crear;"
            " no reintentar este POST; --desarmar solo pausa filas con external_id",
        )
    # ID confirmado: durable ANTES del LIST (sigue planeado hasta verificar).
    _guarda_ack(ctx, paso_id, str(external), ack)
    ctx.conocidos.append({"rol": paso.rol, "recurso": paso.recurso, "external": str(external)})
    time.sleep(0.3)
    try:
        leido = _readback(ctx.cliente_lectura, ctx.profile, paso.path, external)
    except Exception as exc:
        leido = None
        _log(
            "readback_excepcion",
            lote=ctx.lote,
            rol=paso.rol,
            recurso=paso.recurso,
            external=external,
            error=scrub(str(exc))[:200],
        )
    readback = leido.get("state") if isinstance(leido, dict) else None
    cuadra = _readback_cuadra(leido, payload)
    _log(
        "paso",
        lote=ctx.lote,
        rol=paso.rol,
        recurso=paso.recurso,
        external=external,
        ack=ack["cuerpo"],
        readback=readback,
        ok=cuadra,
    )
    _sella_paso(ctx, paso_id, cuadra, external, ack, readback)
    if not cuadra:
        raise Abortar(
            f"readback de {paso.descripcion} ({paso.rol}) no cuadra ({readback});"
            f" external_id={external} queda en el ledger (failed) y en --desarmar;"
            " no reintentar este POST"
        )
    return external


def _ejecuta_rol(ctx: _Ctx, plan: fp.PlanGrupoV2, rol: str) -> dict:
    pasos = fp.pasos_del_rol(plan, rol)
    externos: dict = {"rol": rol, "product_ads": [], "semillas": []}
    externos["campaign"] = _ejecuta_paso(ctx, pasos[0], {})
    padres = {"campaignId": externos["campaign"]}
    externos["ad_group"] = _ejecuta_paso(ctx, pasos[1], padres)
    padres["adGroupId"] = externos["ad_group"]
    for paso in pasos[2:]:
        ext = _ejecuta_paso(ctx, paso, padres)
        destino = externos["product_ads"] if paso.recurso == "product_ad" else externos["semillas"]
        destino.append({"recurso": paso.recurso, "external": ext})
    return externos


def _valida_go_valores(
    esperado: int | None, huella_dada: str | None, go: str | None, huella: str, n_roles: int
) -> None:
    if esperado is None:
        raise Abortar(
            f"mutacion real exige --esperado {n_roles} (las {n_roles} campanas del grupo)"
        )
    if esperado != n_roles:
        raise Abortar(f"--esperado {esperado} != {n_roles} campanas del grupo")
    if not huella_dada:
        raise Abortar("mutacion real exige --huella del dry-run (autorizacion por conjunto)")
    if huella_dada != huella:
        raise Abortar(
            f"--huella {huella_dada} != huella del plan {huella}: el plan cambio, se re-autoriza"
        )
    if not go or not go.strip():
        raise Abortar("mutacion real exige --go con el literal del dueno (no vacio)")


def _valida_go(args, huella: str, n_roles: int) -> None:
    _valida_go_valores(args.esperado, args.huella, args.go, huella, n_roles)


def mutar(
    plan: fp.PlanGrupoV2,
    huella: str,
    *,
    esperado: int | None,
    go: str | None,
    huella_autorizada: str | None = None,
    lote: str | None = None,
    revierte_al_fallar: bool = False,
    crear_pausado: bool = False,
    lock_ya_tomado: bool = False,
) -> int:
    huella_dada = huella if huella_autorizada is None else huella_autorizada
    _valida_go_valores(esperado, huella_dada, go, huella, len(_roles_del_plan(plan)))
    # D-CURSOR-177-F3: falla cerrado antes de LWA/lote/POST (spec §5.1).
    _dsn_ingest()
    if lote is None:
        lote = _lote_nuevo(huella)
    conn_admin = None
    escritor = None
    lock_propio = False
    try:
        conn_admin = connect(_dsn_admin())
        if not lock_ya_tomado:
            _bloquea_lote(conn_admin, lote)
            lock_propio = True
        estado = _estado_lote(conn_admin, lote)
        conn_admin.commit()
        if estado is not None:
            raise Abortar(
                f"lote {lote} ya existe ({estado}): usa --reconciliar, --registrar o --desarmar"
            )
        cred, cliente_lectura, perfiles = _bootstrap_amazon(
            plan.platform,
            error_sin_perfil=f"sin perfil aceptado para {plan.platform}: no se muta",
        )
        _log("perfiles", perfiles=perfiles)
        escritor = _escritor(cred, plan.platform, perfiles[plan.platform])
        # El token se pide ANTES de registrar el lote: si LWA rechaza, no queda
        # un lote que impida repetir la misma huella (revision P1 de #759).
        try:
            escritor.autenticar()
        except AdsAuthError as exc:
            raise Abortar(f"LWA rechazo el token del escritor: {exc}") from exc
        assert go is not None
        _inserta_lote(conn_admin, lote, plan, huella, go)
        ctx = _Ctx(
            escritor,
            cliente_lectura,
            perfiles[plan.platform],
            conn_admin,
            lote,
            crear_pausado=crear_pausado,
        )
        creadas: list[dict] = []
        try:
            for rol in _roles_del_plan(plan):
                creadas.append(_ejecuta_rol(ctx, plan, rol))
                _log("campana_creada", lote=lote, rol=rol, external=creadas[-1]["campaign"])
            if crear_pausado:
                _activa_campanas(ctx, lote)
        except Exception as exc:
            # Abortar, red, timeout o muerte-viva: mismo cierre (failed +
            # rollback best-effort). Lo incierto lo reconcilia el vigia.
            detalle = f"{exc} | creadas: {[c['rol'] for c in creadas]} | conocidos: {ctx.conocidos}"
            _sella_lote(conn_admin, lote, "failed", detalle)
            rollback_incompleto = ""
            if revierte_al_fallar:
                resumen = _revierte_parcial(ctx, lote)
                nota = (
                    f"rollback impulso: pausadas {resumen['pausadas']}"
                    + (f"; errores {resumen['errores']}" if resumen["errores"] else "; sin errores")
                    + "; el reintento se bloquea hasta --desarmar y nueva vista"
                )
                if resumen["errores"]:
                    rollback_incompleto = "; ".join(resumen["errores"])
            else:
                nota = (
                    "las creadas quedan ENABLED en Amazon; --desarmar <lote> las pausa"
                    if "INCERTO" not in str(exc)
                    else (
                        "resultado INCERTO: no reintentar POST; --desarmar solo pausa"
                        " filas con external_id; verificar consola Amazon por nombre"
                    )
                )
            _log(
                "lote_detenido",
                lote=lote,
                motivo=str(exc),
                creadas=[c["rol"] for c in creadas],
                conocidos=ctx.conocidos,
                nota=nota,
            )
            if rollback_incompleto:
                raise Abortar(
                    f"{exc} || rollback incompleto ({rollback_incompleto}): puede quedar"
                    " ENABLED en Amazon; el vigia la pausa al reconciliar"
                ) from exc
            raise
        try:
            fabrica_registro._registrar(ctx, plan, creadas)
        except Exception as exc:
            conn_admin.rollback()
            _sella_lote(
                conn_admin,
                lote,
                "failed",
                f"campanas creadas en Amazon, registro interno incompleto: {exc}",
            )
            rollback_txt = ""
            if revierte_al_fallar:
                # L8: sin grupo no hay impulso; las activadas se pausan
                # (best effort) para no gastar sin tope.
                resumen = _revierte_parcial(ctx, lote)
                if resumen["errores"]:
                    rollback_txt = f" || rollback incompleto ({'; '.join(resumen['errores'])})"
            _log(
                "lote_detenido",
                lote=lote,
                motivo=f"registro: {exc}",
                creadas=[c["rol"] for c in creadas],
                nota="--registrar <lote> reintenta SOLO el registro (no toca Amazon)",
            )
            raise Abortar(f"registro interno incompleto: {exc}{rollback_txt}") from exc
        _sella_lote(conn_admin, lote, "applied", None)
        _log("reconciliacion_final", lote=lote, campanas=[c["campaign"] for c in creadas], ok=True)
        return 0
    finally:
        if lock_propio and conn_admin is not None:
            _desbloquea_lote(conn_admin, lote)
        _cierra(escritor, conn_admin)


def _mutar(
    args,
    plan: fp.PlanGrupoV2,
    huella: str,
    *,
    lote: str | None = None,
    revierte_al_fallar: bool = False,
    crear_pausado: bool = False,
) -> int:
    return mutar(
        plan,
        huella,
        esperado=args.esperado,
        go=args.go,
        huella_autorizada=args.huella,
        lote=lote,
        revierte_al_fallar=revierte_al_fallar,
        crear_pausado=crear_pausado,
    )


def _put_estado_campana(ctx: _Ctx, external: str, estado: str) -> dict:
    """PUT de estado por el cliente protegido; SIEMPRE vuelve un ACK canonico.

    Un 5xx o una respuesta perdida pudieron APLICAR: no se sigue como si no
    (mismo criterio que el POST de creacion, sellado 8), pero el ACK incierto
    viaja al llamador para que lo selle con el `external_id` que ya conoce y la
    conciliacion del lote lo pueda leer. Nunca levanta: un fallo de una campana
    no frena las demas pausas del mismo lote (revision P1 de #759).
    """
    try:
        resp = ctx.escritor.cambiar_estado_campana(external, estado)
    except AdsApiError as exc:
        return sp_wire.ack_de_error(exc)
    return sp_wire.ack_de(resp)


def _motivo_put_fallido(ack: dict, external: str, nuevo: str) -> str:
    """Motivo del sello: INCERTO cuando el PUT pudo aplicarse, rechazo si no."""
    status = ack.get("status")
    if status == sp_wire.ACK_SIN_RESPUESTA or status in _HTTP_INCERTO:
        return (
            f"PUT {nuevo} de {external} INCERTO (status {status}): pudo aplicarse;"
            " el paso queda failed con su ID y su ack"
        )
    return f"PUT {nuevo} de {external} rechazado (status {status})"


def _put_y_readback_estado(ctx: _Ctx, external: str, nuevo: str) -> tuple[dict, str | None, bool]:
    """Ejecuta PUT estado + sleep + readback; devuelve (ack, estado_visto, ack_ok)."""
    ack = _put_estado_campana(ctx, external, nuevo)
    if not ack_ok(ack):
        return ack, None, False
    time.sleep(0.3)
    leido = _readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
    visto = leido.get("state") if isinstance(leido, dict) else None
    return ack, visto, True


def _aplica_estado_campana_con_ledger(
    ctx: _Ctx,
    rol: str,
    external: str,
    nuevo: str,
    *,
    descripcion: str,
    evento_log: str,
    log_ack: bool = False,
) -> None:
    """Ciclo comun ledger -> PUT -> sleep -> readback -> sello -> Abortar.

    Siempre sella el paso como `failed` ante Abortar o excepcion en readback
    (no deja filas `planeado` huerfanas).
    """
    paso = fp.Paso(rol, "campaign", "/sp/campaigns", {}, descripcion)
    paso_id = _inserta_paso(ctx, paso, {"campaignId": str(external), "state": nuevo})
    ack = _put_estado_campana(ctx, external, nuevo)
    if not ack_ok(ack):
        _sella_paso(ctx, paso_id, False, external, ack, None)
        motivo = _motivo_put_fallido(ack, external, nuevo)
        if log_ack:
            _log("lote_detenido", lote=ctx.lote, motivo=motivo)
        raise Abortar(motivo)
    try:
        time.sleep(0.3)
        leido = _readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
        visto = leido.get("state") if isinstance(leido, dict) else None
    except Exception as exc:
        _sella_paso(ctx, paso_id, False, external, ack, None)
        raise Abortar(f"readback de {external} ({rol}) levanto ({exc}): se detiene") from exc
    campos_log: dict[str, Any] = {
        "lote": ctx.lote,
        "rol": rol,
        "external": external,
        "readback": visto,
    }
    if log_ack:
        campos_log["ack"] = ack["cuerpo"]
        campos_log["ok"] = visto == nuevo
    elif evento_log == "cambio_estado":
        campos_log["ok"] = visto == nuevo
    _log(evento_log, **campos_log)
    _sella_paso(ctx, paso_id, visto == nuevo, external, ack, visto)
    if visto != nuevo:
        raise Abortar(f"readback de {external} ({rol}) != {nuevo} ({visto}): se detiene")


def _revierte_parcial(ctx: _Ctx, lote: str) -> dict:
    """Rollback del camino impulso: pausa en Amazon las campanas del lote
    con external_id (sin vigia no gastan) y lo anota en detalle. Nunca
    levanta: el lote ya va failed y el reintento se bloquea igual."""
    resumen: dict = {"pausadas": [], "errores": []}
    filas = ctx.conn_admin.execute(_SQL_EXTERNALS_CAMPANA_DEL_LOTE, (lote,)).fetchall()
    for (external,) in filas:
        try:
            ack, estado, ok_ack = _put_y_readback_estado(ctx, external, _PAUSADA)
            if not ok_ack:
                raise Abortar(f"PUT PAUSED rechazado (status {ack.get('status')})")
            if estado != _PAUSADA:
                raise Abortar(f"readback tras PUT: {estado}")
            resumen["pausadas"].append(external)
        except Exception as exc:
            resumen["errores"].append(f"{external}: {scrub(str(exc))[:160]}")
    _log("lote_revertido", lote=lote, **resumen)
    try:
        ctx.conn_admin.execute(
            "UPDATE fabrica_lote SET detalle = COALESCE(detalle, '') || %s WHERE lote = %s",
            (f" | revertidas: {resumen['pausadas']} errores: {resumen['errores']}", lote),
        )
        ctx.conn_admin.commit()
    except Exception as exc:
        _log("lote_revertido_sin_anotar", lote=lote, error=scrub(str(exc))[:160])
    return resumen


def _activa_campanas(ctx: _Ctx, lote: str) -> None:
    """Camino impulso: tras crear el lote completo PAUSED, lo activa
    (PUT ENABLED + readback por campana). Falla cerrado: el rollback
    del llamador vuelve a pausar lo activado."""
    filas = ctx.conn_admin.execute(_SQL_EXTERNALS_CAMPANA_DEL_LOTE, (lote,)).fetchall()
    if not filas:
        raise Abortar(f"lote {lote} sin campanas con external: nada que activar")
    for (external,) in filas:
        ack, estado, ok_ack = _put_y_readback_estado(ctx, external, fp.ESTADO_NUEVO)
        if not ok_ack:
            raise Abortar(f"PUT ENABLED de {external} rechazado (status {ack.get('status')})")
        _log("campana_activada", lote=lote, external=external, readback=estado)
        if estado != fp.ESTADO_NUEVO:
            raise Abortar(f"readback de {external} tras activar != ENABLED ({estado})")
