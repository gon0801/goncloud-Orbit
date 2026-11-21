"""Registro interno de grupos/goals y verificacion de ledger de la fabrica.

Extraido de `tools/fabrica_campanas.py` en Fase 3.2 para mantener el motor
de fabrica dentro del presupuesto de tamano y en un DAG aciclico:
`fabrica_motor -> fabrica_registro -> fabrica_datos -> fabrica_plan`.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
from decimal import Decimal
from typing import Any

from app import fabrica_plan as fp
from app import goals_write
from app.ads.structure import fetch_structure, sync_structure
from app.db import connect
from app.fabrica_datos import Abortar, _roles_del_plan
from app.redaction import scrub

_SQL_ID_ENTIDAD = """
SELECT id FROM ad_entity WHERE platform = %s::platform AND kind = %s AND external_id = %s
"""
_SQL_INSERTA_GRUPO = """
INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote, target_acos_pct,
                           target_derivado_pct, fraccion, target_origen, target_procedencia,
                           go_literal)
SELECT %s::platform, %s, %s, %s, %s, %s, %s, %s, %s, go_literal
  FROM fabrica_lote WHERE lote = %s
ON CONFLICT (lote) DO UPDATE SET lote = EXCLUDED.lote
RETURNING id
"""
_SQL_INSERTA_GRUPO_IMPULSO = """
INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote, target_acos_pct,
                           target_derivado_pct, fraccion, target_origen, target_procedencia,
                           go_literal, tipo)
SELECT %s::platform, %s, %s, %s, %s, %s, %s, %s, %s, go_literal, 'impulso'
  FROM fabrica_lote WHERE lote = %s
ON CONFLICT (lote) DO UPDATE SET lote = EXCLUDED.lote
RETURNING id
"""
_SQL_INSERTA_ROL = """
INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)
VALUES (%s, %s::campana_rol, %s, %s)
ON CONFLICT DO NOTHING
"""
_SQL_INSERTA_PRODUCTO = """
INSERT INTO campana_grupo_producto (grupo_id, product_id, listing_id, seller_sku, margen_neto_pct)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""
_SQL_GOAL_EXISTENTE = """
SELECT id, enabled, mode, harvest_campaign_id, harvest_ad_group_id, harvest_default_bid
  FROM ads_optimizer_goal WHERE ad_entity_id = %s AND scope = 'campaign'
"""
_SQL_CAMPANAS_APPLIED = """
SELECT rol::text, recurso, external_id
  FROM fabrica_lote_paso
 WHERE lote = %s AND estado = 'applied' AND recurso IN ('campaign', 'ad_group')
 ORDER BY orden
"""
# D-CURSOR-177-F1: ledger completo (no solo campaign/ad_group) para --registrar.
_SQL_PASOS_LOTE = """
SELECT orden, rol::text, recurso, estado, external_id, request_payload, ack, readback_estado
  FROM fabrica_lote_paso
 WHERE lote = %s
 ORDER BY orden
"""


def _log(evento: str, **campos: Any) -> None:
    print(scrub(json.dumps({"evento": evento, **campos}, default=str)), flush=True)


def _dsn(nombre: str) -> str:
    dsn = os.environ.get(nombre)
    if not dsn:
        raise Abortar(f"{nombre} no esta en el entorno (corre dentro del contenedor app)")
    return dsn


def _dsn_read() -> str:
    return _dsn("ORBIT_DSN_READ")


def _dsn_admin() -> str:
    return _dsn("ORBIT_DSN_ADMIN")


def _dsn_ingest() -> str:
    return _dsn("ORBIT_DSN_INGEST")


def _cierra(*recursos: Any) -> None:
    """Cierra httpx/psycopg si tienen close. No inventa AdsClient.close."""
    for recurso in recursos:
        closer = getattr(recurso, "close", None)
        if closer is None:
            continue
        with contextlib.suppress(Exception):
            closer()


def _expresion_normalizada(exp: Any) -> tuple:
    if not isinstance(exp, list):
        return ()
    return tuple(
        sorted((str(e.get("type")), str(e.get("value"))) for e in exp if isinstance(e, dict))
    )


def _plan_como_json(plan: fp.PlanGrupoV2) -> dict:
    return fp.plan_v2_como_json(plan)


def _datos_objetivo(
    plan: fp.PlanGrupoV2,
) -> tuple[Decimal, Decimal | None, Decimal | None, str, str]:
    objetivo = plan.objetivo
    return (
        objetivo.acos_pct,
        objetivo.derivado,
        objetivo.fraccion,
        objetivo.origen,
        objetivo.procedencia,
    )


def _publicaciones_del_plan(plan: fp.PlanGrupoV2):
    return tuple(sorted(plan.publicaciones, key=lambda publicacion: publicacion.listing_id))


def _sync(cliente_lectura) -> None:
    conn_ingest = connect(_dsn_ingest())
    try:
        sync_structure(conn_ingest, fetch_structure(cliente_lectura))
    finally:
        _cierra(conn_ingest)


def _id_entidad(conn, platform: str, kind: str, external: str) -> int:
    fila = conn.execute(_SQL_ID_ENTIDAD, (platform, kind, external)).fetchone()
    if fila is None:
        raise Abortar(f"{kind} {external} no esta en ad_entity tras el sync: registro detenido")
    return fila[0] if not isinstance(fila, dict) else fila["id"]


def _registrar(ctx, plan: fp.PlanGrupoV2, creadas: list[dict]) -> int:
    _sync(ctx.cliente_lectura)
    conn = ctx.conn_admin
    exact = next(c for c in creadas if c["rol"] == "category_exact")
    ids = {
        c["rol"]: (
            _id_entidad(conn, plan.platform, "campaign", c["campaign"]),
            _id_entidad(conn, plan.platform, "ad_group", c["ad_group"]),
        )
        for c in creadas
    }
    target, derivado, fraccion, origen, procedencia = _datos_objetivo(plan)
    # A1: el grupo conserva el snapshot derivado (auditoria de con que nacio);
    # los goals solo fijan target con manual confirmado, si no nacen NULL y
    # resuelven por margen_plataforma cada ciclo.
    goal_target = fp.target_para_goals(plan)
    inserta_grupo = (
        _SQL_INSERTA_GRUPO_IMPULSO if plan.roles == fp.ROLES_IMPULSO else _SQL_INSERTA_GRUPO
    )
    grupo = conn.execute(
        inserta_grupo,
        (
            plan.platform,
            plan.tipo_producto,
            plan.nombre_base,
            ctx.lote,
            target,
            derivado,
            fraccion,
            origen,
            procedencia,
            ctx.lote,
        ),
    ).fetchone()[0]
    if isinstance(grupo, dict):
        grupo = grupo["id"]
    for rol, (camp_id, ag_id) in ids.items():
        conn.execute(_SQL_INSERTA_ROL, (grupo, rol, camp_id, ag_id))
    for p in _publicaciones_del_plan(plan):
        conn.execute(
            _SQL_INSERTA_PRODUCTO,
            (grupo, p.product_id, p.listing_id, p.seller_sku, p.margen_neto_pct),
        )
    conn.commit()
    ahora = datetime.datetime.now(datetime.UTC)
    bid_exact = plan.parametros["category_exact"].bid
    for rol, (camp_id, _ag_id) in ids.items():
        previo = conn.execute(_SQL_GOAL_EXISTENTE, (camp_id,)).fetchone()
        if previo is not None:
            if isinstance(previo, dict):
                goal_previo = previo["id"]
                enabled = previo["enabled"]
                mode = previo["mode"]
                h_camp = previo["harvest_campaign_id"]
                h_ag = previo["harvest_ad_group_id"]
                h_bid = previo["harvest_default_bid"]
            else:
                goal_previo, enabled, mode, h_camp, h_ag, h_bid = previo
            if (
                not enabled
                or mode != plan.modo
                or str(h_camp) != str(exact["campaign"])
                or str(h_ag) != str(exact["ad_group"])
                or h_bid is None
            ):
                raise Abortar(
                    f"goal existente de {rol} (id={goal_previo}) no esta listo"
                    f" (enabled={enabled}, mode={mode}): no se sella applied;"
                    " no se reactiva por --registrar"
                )
            _log("goal_ya_existe", lote=ctx.lote, rol=rol, goal_id=goal_previo)
            continue
        goal = goals_write.crea_goal(
            conn,
            ad_entity_id=camp_id,
            target_acos_pct=goal_target,
            bid_currency=plan.moneda,
            mode=plan.modo,
            harvest_campaign_id=exact["campaign"],
            harvest_ad_group_id=exact["ad_group"],
            harvest_default_bid=bid_exact,
            created_at=ahora,
        )
        _log(
            "goal_creado",
            lote=ctx.lote,
            rol=rol,
            goal_id=goal["id"],
            mode=plan.modo,
            target=str(goal_target),
        )
    _log("grupo_registrado", lote=ctx.lote, grupo_id=grupo, target=str(target))
    return grupo


def _payload_como_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    if raw is None:
        return {}
    return dict(raw)


def _fila_paso(fila) -> dict:
    if isinstance(fila, dict):
        return fila
    claves = (
        "orden",
        "rol",
        "recurso",
        "estado",
        "external_id",
        "request_payload",
        "ack",
        "readback_estado",
    )
    return dict(zip(claves, fila, strict=True))


def _pedido_con_padres(paso: fp.Paso, camp: str | None, ag: str | None) -> dict:
    if paso.recurso == "campaign":
        return dict(paso.payload)
    if paso.recurso == "ad_group":
        return {**paso.payload, "campaignId": camp}
    return {**paso.payload, "campaignId": camp, "adGroupId": ag}


def _identidad_coincide(recurso: str, pedido: dict, guardado: Any) -> bool:
    """Compara identidad del paso (campos), no el JSON serializado completo."""
    g = _payload_como_dict(guardado)
    claves = {
        "campaign": ("name", "targetingType"),
        "ad_group": ("name", "campaignId"),
        "product_ad": ("sku", "campaignId", "adGroupId"),
        "keyword": ("keywordText", "matchType", "campaignId", "adGroupId"),
        "target": ("campaignId", "adGroupId"),
        "negative_keyword": ("keywordText", "matchType", "campaignId", "adGroupId"),
    }
    for clave in claves.get(recurso, ()):
        if clave in pedido and str(g.get(clave)) != str(pedido[clave]):
            return False
    if recurso == "target" and "expression" in pedido:
        return _expresion_normalizada(g.get("expression")) == _expresion_normalizada(
            pedido["expression"]
        )
    return True


def _exige_ledger_completo(conn, plan: fp.PlanGrupoV2, lote: str) -> None:
    """D-CURSOR-177-F1: todos los pasos de pasos_del_rol deben estar applied."""
    filas = [_fila_paso(f) for f in conn.execute(_SQL_PASOS_LOTE, (lote,)).fetchall()]
    camp_ag: dict[str, dict[str, str]] = {}
    for f in filas:
        if (
            f["estado"] == "applied"
            and f["external_id"]
            and f["recurso"] in ("campaign", "ad_group")
        ):
            camp_ag.setdefault(f["rol"], {})[f["recurso"]] = str(f["external_id"])
    usados: set[int] = set()
    faltan: list[str] = []
    for rol in _roles_del_plan(plan):
        ids = camp_ag.get(rol, {})
        if "campaign" not in ids or "ad_group" not in ids:
            faltan.append(f"{rol}: falta campaign/ad_group applied")
            continue
        camp, ag = ids["campaign"], ids["ad_group"]
        for paso in fp.pasos_del_rol(plan, rol):
            pedido = _pedido_con_padres(paso, camp, ag)
            match_i = None
            for i, f in enumerate(filas):
                if i in usados or f["rol"] != rol or f["recurso"] != paso.recurso:
                    continue
                if not _identidad_coincide(paso.recurso, pedido, f["request_payload"]):
                    continue
                match_i = i
                break
            if match_i is None:
                faltan.append(f"{rol}/{paso.recurso}: ausente en ledger")
                continue
            usados.add(match_i)
            f = filas[match_i]
            if (
                f["estado"] != "applied"
                or not f["external_id"]
                or f["ack"] is None
                or not f["readback_estado"]
            ):
                faltan.append(
                    f"{rol}/{paso.recurso}: estado={f['estado']} (exige applied con evidencia)"
                )
    if faltan:
        raise Abortar(f"lote {lote} incompleto para registrar: {'; '.join(faltan)}")
