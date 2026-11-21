"""Operaciones sobre lotes existentes de la fabrica de campanas.

Separa las operaciones post-creacion (`pausar_lote`, `reanudar_lote`,
`reactiva_lote`, `desarmar_lote`, `reconciliar_lote`, `registrar_lote`) del
motor de creacion en `app/fabrica_motor.py` (DAG: `fabrica_lotes ->
fabrica_motor -> fabrica_registro -> fabrica_datos`).
"""

from __future__ import annotations

import datetime
import json

from psycopg.rows import tuple_row

from app import fabrica_motor as fm
from app import fabrica_plan as fp
from app import fabrica_registro, goals_write
from app.ads.config import AdsCredentials
from app.fabrica_datos import Abortar, _roles_del_plan
from app.fabrica_registro import _SQL_CAMPANAS_APPLIED, _cierra, _dsn_admin, _log

_SQL_CAMPANAS_DEL_LOTE = """
SELECT t.rol, t.external_id, t.goal_id
  FROM (
    SELECT DISTINCT ON (p.rol) p.rol::text AS rol, p.external_id, g.id AS goal_id, p.orden
      FROM fabrica_lote_paso p
      LEFT JOIN campana_grupo cg ON cg.lote = p.lote
      LEFT JOIN campana_grupo_rol r ON r.grupo_id = cg.id AND r.rol = p.rol
      LEFT JOIN ads_optimizer_goal g ON g.ad_entity_id = r.ad_entity_id AND g.scope = 'campaign'
     WHERE p.lote = %s AND p.recurso = 'campaign' AND p.external_id IS NOT NULL
     ORDER BY p.rol, p.orden DESC
  ) t
 ORDER BY t.orden
"""
_SQL_PENDIENTES = """
SELECT p.id, p.lote, p.rol::text, l.platform::text, p.recurso, CASE p.recurso
         WHEN 'campaign' THEN '/sp/campaigns' WHEN 'ad_group' THEN '/sp/adGroups'
         WHEN 'product_ad' THEN '/sp/productAds' WHEN 'keyword' THEN '/sp/keywords'
         WHEN 'target' THEN '/sp/targets' ELSE '/sp/negativeKeywords' END,
       p.external_id, p.request_payload
  FROM fabrica_lote_paso p
  JOIN fabrica_lote l ON l.lote = p.lote
 WHERE p.estado IN ('planeado', 'failed')
   AND (%s::text IS NULL OR p.lote = %s)
   AND (%s::platform IS NULL OR l.platform = %s::platform)
 ORDER BY p.id
"""
_SQL_PROMUEVE_APPLIED = """
UPDATE fabrica_lote_paso
   SET estado = 'applied', ack = %s::jsonb, readback_estado = %s
 WHERE id = %s AND estado IN ('planeado', 'failed')
"""


def registrar_lote(lote: str) -> int:
    conn_admin = fm.connect(_dsn_admin())
    try:
        conn_admin.row_factory = tuple_row
        fila = conn_admin.execute(
            "SELECT plan, estado FROM fabrica_lote WHERE lote = %s", (lote,)
        ).fetchone()
        if fila is None:
            raise Abortar(f"lote {lote} no existe")
        plan_json, estado = fila
        if estado == "applied":
            raise Abortar(f"lote {lote} ya esta applied: nada que registrar")
        if estado == "desarmado":
            raise Abortar(
                f"lote {lote} esta desarmado (campanas PAUSED): registrarlo "
                "lo resucitaria a medias; si se quiere vivo, es decision nueva del dueno"
            )
        plan = fp.plan_v2_desde_json(plan_json)
        fabrica_registro._exige_ledger_completo(conn_admin, plan, lote)
        pasos = conn_admin.execute(_SQL_CAMPANAS_APPLIED, (lote,)).fetchall()
        conn_admin.commit()
        creadas: dict[str, dict] = {}
        for rol, recurso, external in pasos:
            creadas.setdefault(rol, {"rol": rol, "product_ads": [], "semillas": []})[recurso] = (
                external
            )
        faltan = [r for r in _roles_del_plan(plan) if "ad_group" not in creadas.get(r, {})]
        if faltan:
            raise Abortar(
                f"lote {lote} sin campana+ad group applied para {faltan}: --reconciliar primero"
            )
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = fm.AdsClient(cred)
        perfiles = fm._perfiles(cliente_lectura)
        if plan.platform not in perfiles:
            raise Abortar(f"sin perfil aceptado para {plan.platform}")
        ctx = fm._Ctx(None, "", cred, cliente_lectura, perfiles[plan.platform], conn_admin, lote)
        fabrica_registro._registrar(ctx, plan, [creadas[r] for r in _roles_del_plan(plan)])
        fm._sella_lote(conn_admin, lote, "applied", "registro reintentado")
        return 0
    finally:
        _cierra(conn_admin)


def _registrar_cmd(args) -> int:
    return registrar_lote(args.registrar)


def _pausa_una(ctx: fm._Ctx, rol: str, external: str, goal_id) -> None:
    fm._aplica_estado_campana_con_ledger(
        ctx,
        rol,
        external,
        fm._PAUSADA,
        descripcion=f"desarmar {rol}",
        evento_log="desarmar",
        sella_put_abortado=True,
        log_ack=True,
    )
    if goal_id is not None:
        goals_write.edita_goal(
            ctx.conn_admin,
            goal_id,
            enabled=False,
            updated_at=datetime.datetime.now(datetime.UTC),
        )


def _cambia_estado_lote(ctx: fm._Ctx, lote: str, estado: str, actor: str) -> None:
    """Pausa o reanuda las campanas de un lote sin desarmarlo: fila en
    el ledger antes de cada PUT, _put_estado_campana, readback y sello.
    No toca los goals (a diferencia de _pausa_una): el motor sigue
    decidiendo y el vigia gradua despues."""
    actual = fm._estado_lote(ctx.conn_admin, lote)
    if actual is None:
        raise Abortar(f"lote {lote} no existe")
    if actual == "desarmado":
        raise Abortar(f"lote {lote} desarmado: nunca revive")
    if estado == "pausado" and actual != "applied":
        raise Abortar(f"lote {lote} en {actual}: solo se pausa desde applied")
    if estado == "applied" and actual != "pausado":
        raise Abortar(f"lote {lote} en {actual}: solo se reanuda desde pausado")
    filas = ctx.conn_admin.execute(_SQL_CAMPANAS_DEL_LOTE, (lote,)).fetchall()
    ctx.conn_admin.commit()
    if not filas:
        raise Abortar(f"lote {lote} sin campanas en fabrica_lote_paso: nada que {estado}")
    ctx.orden = ctx.conn_admin.execute(
        "SELECT coalesce(max(orden), 0) FROM fabrica_lote_paso WHERE lote = %s", (lote,)
    ).fetchone()[0]
    if estado == "pausado":
        # G9: el actor queda grabado antes del primer PUT, con COMMIT.
        ctx.conn_admin.execute(
            "UPDATE fabrica_lote SET pausado_por = %s WHERE lote = %s", (actor, lote)
        )
        ctx.conn_admin.commit()
    nuevo = fm._PAUSADA if estado == "pausado" else fp.ESTADO_NUEVO
    for rol, external, _goal in filas:
        fm._aplica_estado_campana_con_ledger(
            ctx, rol, external, nuevo, descripcion=f"{estado} {rol}", evento_log="cambio_estado"
        )
    # G1: estado y pausado_por en un solo UPDATE atomico.
    fm._sella_lote(
        ctx.conn_admin,
        lote,
        estado,
        f"go: {actor}",
        pausado_por=actor if estado == "pausado" else None,
    )


def pausar_lote(ctx: fm._Ctx, lote: str, actor: str) -> None:
    """Pausa un lote applied (reversible)."""
    _cambia_estado_lote(ctx, lote, "pausado", actor)


def reanudar_lote(ctx: fm._Ctx, lote: str, actor: str) -> None:
    """Reanuda un lote pausado."""
    _cambia_estado_lote(ctx, lote, "applied", actor)


def reactiva_lote(ctx: fm._Ctx, lote: str, actor: str) -> None:
    """Graduar casa con Amazon: PUT ENABLED (ledger + readback) a las
    campanas del lote que no esten ENABLED, y sello applied si estaba
    pausado. Nunca revive desarmados."""
    actual = fm._estado_lote(ctx.conn_admin, lote)
    if actual is None:
        raise Abortar(f"lote {lote} no existe")
    if actual == "desarmado":
        raise Abortar(f"lote {lote} desarmado: nunca revive")
    ctx.orden = ctx.conn_admin.execute(
        "SELECT coalesce(max(orden), 0) FROM fabrica_lote_paso WHERE lote = %s", (lote,)
    ).fetchone()[0]
    for rol, external, _goal in ctx.conn_admin.execute(_SQL_CAMPANAS_DEL_LOTE, (lote,)).fetchall():
        leido = fm._readback(ctx.cliente_lectura, ctx.profile, "/sp/campaigns", external)
        visto = leido.get("state") if isinstance(leido, dict) else None
        if visto == fp.ESTADO_NUEVO:
            continue
        fm._aplica_estado_campana_con_ledger(
            ctx,
            rol,
            external,
            fp.ESTADO_NUEVO,
            descripcion=f"reactivar {rol}",
            evento_log="reactivar",
        )
    if actual == "pausado":
        fm._sella_lote(ctx.conn_admin, lote, "applied", f"go: {actor}", pausado_por=None)


def desarmar_lote(lote: str, *, acepto_mutacion_real: bool, go: str | None) -> int:
    conn_admin = None
    http = None
    try:
        conn_admin = fm.connect(_dsn_admin())
        if fm._estado_lote(conn_admin, lote) == "desarmado":
            raise Abortar(f"lote {lote} desarmado: nunca revive")
        filas = conn_admin.execute(_SQL_CAMPANAS_DEL_LOTE, (lote,)).fetchall()
        conn_admin.commit()
        if not filas:
            raise Abortar(
                f"lote {lote} sin campanas que pausar en fabrica_lote_paso: nada que desarmar"
            )
        for rol, external, goal_id in filas:
            print(f"{rol} | {external} | goal={goal_id} -> PAUSED + enabled=false", flush=True)
        _log("plan_desarmar", lote=lote, campanas=len(filas))
        if not acepto_mutacion_real:
            _log(
                "dry_run",
                modo="desarmar",
                lote=lote,
                nota="sin --acepto-mutacion-real no se pausa nada",
            )
            return 0
        if not go or not go.strip():
            raise Abortar("desarmar real exige --go con el literal del dueno")
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = fm.AdsClient(cred)
        perfiles = fm._perfiles(cliente_lectura)
        platform = conn_admin.execute(
            "SELECT platform::text FROM fabrica_lote WHERE lote = %s", (lote,)
        ).fetchone()
        if platform is None or platform[0] not in perfiles:
            raise Abortar(f"sin perfil aceptado para el lote {lote}")
        http = fm.httpx.Client(
            timeout=fm.httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
        )
        ctx = fm._Ctx(
            http,
            fm._token_lwa(cred, http),
            cred,
            cliente_lectura,
            perfiles[platform[0]],
            conn_admin,
            lote,
        )
        # R09-I: el ledger de _pausa_una continua en max(orden), como _cambia_estado_lote.
        ctx.orden = conn_admin.execute(
            "SELECT COALESCE(MAX(orden), 0) FROM fabrica_lote_paso WHERE lote = %s",
            (lote,),
        ).fetchone()[0]
        conn_admin.commit()
        pausadas = 0
        for rol, external, goal_id in filas:
            _pausa_una(ctx, rol, external, goal_id)
            pausadas += 1
        fm._sella_lote(conn_admin, lote, "desarmado", f"go: {go}")
        _log("reconciliacion_final", lote=lote, pausadas=pausadas, ok=True)
        return 0
    finally:
        _cierra(http, conn_admin)


def _desarmar(args) -> int:
    return desarmar_lote(
        args.desarmar, acepto_mutacion_real=bool(args.acepto_mutacion_real), go=args.go
    )


def _reconciliar(
    conn_admin, cliente_lectura, perfiles: dict, lote: str | None, plataforma: str | None
) -> dict:
    filas = conn_admin.execute(_SQL_PENDIENTES, (lote, lote, plataforma, plataforma)).fetchall()
    conn_admin.commit()
    resumen = {"pendientes": len(filas), "recuperadas": 0, "sin_verificar": 0, "ausentes": 0}
    por_plataforma: dict[str, list] = {}
    for fila in filas:
        por_plataforma.setdefault(fila[3], []).append(fila)
    for plataforma_fila, pasos in por_plataforma.items():
        profile = perfiles.get(plataforma_fila)
        if profile is None:
            _log(
                "reconciliar_sin_perfil",
                plataforma=plataforma_fila,
                pasos=len(pasos),
                nota="sin perfil aceptado: quedan sin verificar",
            )
            resumen["sin_verificar"] += len(pasos)
            continue
        for fid, lote_fila, rol, _plat, recurso, path, external, payload in pasos:
            if external is None:
                _log(
                    "reconciliar_sin_external",
                    paso=fid,
                    lote=lote_fila,
                    rol=rol,
                    recurso=recurso,
                    nota="el POST no dejo id: verificar a mano por nombre en la consola",
                )
                resumen["sin_verificar"] += 1
                continue
            leido = fm._readback(cliente_lectura, profile, path, external)
            if leido is None:
                resumen["sin_verificar"] += 1
                _log(
                    "reconciliar_sin_verificar",
                    paso=fid,
                    external=external,
                    nota="el LIST no respondio o no lo trae",
                )
                continue
            if not fm._readback_cuadra(leido, payload if isinstance(payload, dict) else {}):
                resumen["ausentes"] += 1
                _log(
                    "reconciliar_no_cuadra", paso=fid, external=external, estado=leido.get("state")
                )
                continue
            ack = {"fuente": "reconciliar", "external": external, "lote": lote_fila}
            conn_admin.execute(_SQL_PROMUEVE_APPLIED, (json.dumps(ack), leido.get("state"), fid))
            conn_admin.commit()
            resumen["recuperadas"] += 1
    _log("reconciliacion", lote=lote, **resumen)
    if resumen["ausentes"]:
        raise Abortar(
            f"reconciliacion con {resumen['ausentes']} paso(s) que viven pero no cuadran"
            " con el payload del ledger: verificacion manual (no se promueve a ciegas)"
        )
    if resumen["sin_verificar"]:
        raise Abortar(
            f"reconciliacion con {resumen['sin_verificar']} paso(s) sin verificar:"
            " nada se promovio a ciegas"
        )
    return resumen


def reconciliar_lote(*, lote: str | None = None, plataforma: str | None = None) -> int:
    conn_admin = None
    try:
        if plataforma is None and lote is None:
            raise Abortar("--reconciliar exige --lote X o --plataforma (para elegir el perfil)")
        conn_admin = fm.connect(_dsn_admin())
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = fm.AdsClient(cred)
        perfiles = fm._perfiles(cliente_lectura)
        platform = plataforma
        if lote is not None:
            fila = conn_admin.execute(
                "SELECT platform::text FROM fabrica_lote WHERE lote = %s", (lote,)
            ).fetchone()
            if fila is None:
                raise Abortar(f"lote {lote} no existe")
            lote_platform = fila[0]
            if platform is None:
                platform = lote_platform
            elif platform != lote_platform:
                raise Abortar(
                    f"--lote {lote} es {lote_platform} pero --plataforma"
                    f" {platform}: filtros contradictorios"
                )
        if platform not in perfiles:
            raise Abortar(f"sin perfil aceptado para {platform}")
        _reconciliar(conn_admin, cliente_lectura, perfiles, lote, platform)
        return 0
    finally:
        _cierra(conn_admin)


def _reconciliar_cmd(args) -> int:
    return reconciliar_lote(lote=args.lote, plataforma=args.plataforma)
