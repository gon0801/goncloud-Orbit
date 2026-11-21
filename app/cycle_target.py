"""Resolucion de targets por margen de plataforma y por familia del ciclo (Fase 3.4).

Agrupa los SQL de medicion de margen (plataforma y familia), los previos de
hoja/grupo y los tipos `_TargetCiclo`, `_PrevioHoja`, `_FamiliasCiclo`. Sin
imports de `app.cycle` (DAG: `cycle -> cycle_target -> cycle_serializacion`).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

import psycopg
from psycopg.rows import dict_row

from app.cycle_serializacion import _dec_str, _fecha_iso, _ts
from app.optimizer import goals as g
from app.optimizer import productos

# ORBIT 06 2.3: medicion del margen, UNA fila por plataforma (la vista SOLO
# MIDE; la resolucion es goals.resuelve_target_margen en Python).
_SQL_TARGET_MARGEN = """
SELECT platform, ventana_desde, ventana_hasta, venta_total, venta_cubierta,
       cargos_con_orden, cargos_sin_orden, cogs, cobertura, dias_con_venta,
       fees_sin_tipo, margen_neto_pct, ledger_fresco_at, moneda,
       cogs_vigente, cobertura_vigente, margen_vigente_pct
  FROM v_target_margen_plataforma
 WHERE platform = %s::platform
"""

# A5: medicion del margen POR FAMILIA (la vista SOLO MIDE; la resolucion es
# goals.resuelve_target_margen_familia en Python, por hoja).
_SQL_MARGENES_FAMILIA = """
SELECT familia_id, padre_id, ventana_desde, ventana_hasta, venta_cubierta,
       cobertura, dias_con_venta, margen_neto_pct, ledger_fresco_at, moneda,
       cogs_vigente, cobertura_vigente, margen_vigente_pct, venta_total
  FROM v_margen_familia
 WHERE platform = %s::platform
"""

# A5: parejas (campana, familia | None) por producto anunciado, via product ads.
_SQL_PAREJAS_CAMPANA_FAMILIA = """
SELECT camp.id AS campana_id, pf.familia_id, f.padre_id
  FROM ad_entity camp
  JOIN ad_entity ag ON ag.parent_id = camp.id AND ag.kind = 'ad_group'
  JOIN ad_entity pa ON pa.parent_id = ag.id AND pa.kind = 'product_ad'
  JOIN ad_entity_state es ON es.ad_entity_id = pa.id AND es.status IN ('ENABLED', 'PAUSED')
  LEFT JOIN listing li ON li.id = pa.listing_id
  LEFT JOIN producto_familia pf
    ON pf.product_id = li.product_id AND pf.platform = camp.platform
  LEFT JOIN familia f ON f.id = pf.familia_id
 WHERE camp.platform = %s::platform AND camp.kind = 'campaign'
"""

# A5/A7: ultimo target aplicado POR HOJA (ancla del paso familiar y de grupo).
_SQL_TARGETS_PREVIOS = """
SELECT DISTINCT ON (t.ad_entity_id) t.ad_entity_id, t.target_acos_pct, t.procedencia,
       t.cycle_id, k.parent_id AS ad_group_id
  FROM target_acos_ciclo t
  JOIN optimizer_cycle c ON c.id = t.cycle_id
  JOIN ad_entity k ON k.id = t.ad_entity_id
 WHERE c.platform = %s::platform AND c.mode = 'live' AND c.status = 'done'
   AND c.id < %s
 ORDER BY t.ad_entity_id, c.id DESC
"""

# Ad revenue de la ventana del margen al grano keyword+product_target.
_SQL_AD_REVENUE_VENTANA = """
SELECT SUM(m.ad_revenue), COUNT(DISTINCT m.metric_currency), MAX(m.metric_currency::text)
  FROM v_metric_latest m
  JOIN ad_entity e ON e.id = m.ad_entity_id
 WHERE e.platform = %s::platform
   AND e.kind IN ('keyword', 'product_target')
   AND m.metric_date >= %s AND m.metric_date < %s
"""

# Notas de ciclos previos (misma plataforma+motor, anteriores al actual).
_SQL_NOTAS_PREVIAS = """
SELECT mode, status, notes
  FROM optimizer_cycle
 WHERE platform = %s::platform AND motor = 'ads_optimizer' AND id < %s
   AND status = 'done'
 ORDER BY id DESC
 LIMIT 200
"""


@dataclass(frozen=True)
class _TargetCiclo:
    """El peldano a nivel ciclo (ORBIT 06 2.3, calculado UNA vez en TX2):
    valor que entra a las cascadas + snapshot para el freeze y notes.target
    + ancla del ultimo aviso del digest (ultimo_avisado, D-2.3.14)."""

    margen: Decimal | None
    snapshot: dict
    ancla: str | None
    margen_neto_pct: Decimal | None = None


def _target_de_notas(notes) -> dict | None:
    """notes.target de un ciclo previo, o None (TEXT no-JSON, no-dict o sin
    la clave: regla 3, lo ausente no se menciona ni rompe el scan)."""
    if not isinstance(notes, str):
        return None
    try:
        cuerpo = json.loads(notes)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(cuerpo, dict):
        return None
    target = cuerpo.get("target")
    return target if isinstance(target, dict) else None


def _resuelve_target_ciclo(
    conn: psycopg.Connection,
    *,
    platform: str,
    settings: dict,
    setting_target: Decimal | None,
    cycle_id: int,
    decided_at: dt.datetime,
) -> _TargetCiclo:
    """Lee la vista + notes previas (mismo snapshot TX2) y resuelve el
    peldano con goals.resuelve_target_margen (D-2.3.2: UNA vez por ciclo)."""
    with conn.cursor(row_factory=dict_row) as cur:
        fila = cur.execute(_SQL_TARGET_MARGEN, (platform,)).fetchone()
    previas = conn.execute(_SQL_NOTAS_PREVIAS, (platform, cycle_id)).fetchall()
    ultimo: Decimal | None = None
    ancla: str | None = None
    for mode, status, notes in previas:
        target = _target_de_notas(notes)
        if target is None:
            continue
        if ancla is None and target.get("ultimo_avisado") is not None:
            ancla = target["ultimo_avisado"]
        if (
            ultimo is None
            and mode == "live"
            and status == "done"
            and target.get("procedencia") == "margen_plataforma"
            and target.get("target_aplicado") is not None
        ):
            ultimo = Decimal(target["target_aplicado"])
    if ultimo is None:
        ultimo = setting_target
    fraccion = g.fraccion_desde_settings(settings, platform)
    if fila is None:
        hoy = decided_at.astimezone(dt.UTC).date()
        medicion = g.MedicionMargen(
            margen_neto_pct=None,
            cobertura=None,
            dias_con_venta=None,
            venta_cubierta=None,
            ledger_fresco_at=None,
            moneda=None,
            ventana_desde=None,
            ventana_hasta=None,
        )
        venta_total = None
        ad_revenue_ventana = None
    else:
        hoy = g.hoy_de_ventana(fila["ventana_hasta"])
        medicion = g.medicion_desde_fila(fila)
        venta_total = fila["venta_total"]
        suma_ads, n_monedas_ads, moneda_ads = conn.execute(
            _SQL_AD_REVENUE_VENTANA,
            (platform, fila["ventana_desde"], fila["ventana_hasta"]),
        ).fetchone()
        ad_revenue_ventana = g.ratio_ads_publicable(
            suma_ads, n_monedas_ads, moneda_ads, medicion.moneda
        )
    res = g.resuelve_target_margen(medicion, fraccion, hoy, ultimo, setting_target)
    procedencia = "margen_plataforma" if res.motivo is None else None
    snapshot = {
        "procedencia": procedencia,
        "paso_politica": g.PASO_POLITICA,
        "motivo_abstencion": res.motivo,
        "convergiendo_a_setting": res.convergiendo,
        "margen_neto_pct": _dec_str(medicion.margen_neto_pct),
        "margen_historico_pct": _dec_str(medicion.margen_historico_pct),
        "fraccion": _dec_str(fraccion),
        "cobertura": _dec_str(medicion.cobertura),
        "ventana_desde": _fecha_iso(medicion.ventana_desde),
        "ventana_hasta": _fecha_iso(medicion.ventana_hasta),
        "target_derivado": _dec_str(res.derivado),
        "target_aplicado": _dec_str(res.aplicado),
        "setting": _dec_str(setting_target),
        "venta_total": _dec_str(venta_total),
        "ad_revenue_ventana": _dec_str(ad_revenue_ventana),
        "ultimo_avisado": ancla,
        "ledger_fresco_at": _ts(medicion.ledger_fresco_at),
        "moneda": medicion.moneda,
    }
    return _TargetCiclo(
        res.aplicado,
        snapshot,
        ancla,
        margen_neto_pct=medicion.margen_neto_pct if procedencia == "margen_plataforma" else None,
    )


class _PrevioHoja(NamedTuple):
    """Ultimo target live+done de UNA hoja (A7: cycle_id + ad_group_id para
    el ancla del grupo, que colapsa previos margen_familia por grupo)."""

    target: Decimal
    procedencia: str
    cycle_id: int
    ad_group_id: int | None


@dataclass
class _FamiliasCiclo:
    """Insumos A5 del peldano margen_familia, leidos UNA vez por ciclo en TX2."""

    mediciones: dict[int, g.MedicionMargen]
    padres: dict[int, int | None]
    fam_por_campana: dict[int, int | None]
    previos: dict[int, _PrevioHoja]
    anclas_grupo: dict[int, Decimal]
    fraccion: Decimal | None
    hoy: dt.date
    destino: Decimal | None
    notas: dict[int, dict]
    economia: str = productos.ECONOMIA_AUTO03


def _lee_familias_ciclo(
    conn: psycopg.Connection,
    *,
    platform: str,
    cycle_id: int,
    hoy: dt.date,
    fraccion: Decimal | None,
    destino: Decimal | None,
    economia: str = productos.ECONOMIA_AUTO03,
) -> _FamiliasCiclo:
    """Tres lecturas (margenes, parejas, previos) + colapso puro."""
    mediciones: dict[int, g.MedicionMargen] = {}
    padres: dict[int, int | None] = {}
    for fila in conn.execute(_SQL_MARGENES_FAMILIA, (platform,)).fetchall():
        fid = fila[0]
        padres[fid] = fila[1]
        mediciones[fid] = g.MedicionMargen(
            margen_neto_pct=fila[12],
            cobertura=fila[11],
            dias_con_venta=fila[6],
            venta_cubierta=g.importe_cubierto_vigente(fila[13], fila[11]),
            ledger_fresco_at=fila[8],
            moneda=str(fila[9]) if fila[9] is not None else None,
            ventana_desde=fila[2],
            ventana_hasta=fila[3],
            margen_historico_pct=fila[7],
        )
    pares: list[tuple[int, int | None]] = []
    for fila in conn.execute(_SQL_PAREJAS_CAMPANA_FAMILIA, (platform,)).fetchall():
        pares.append((fila[0], fila[1]))
        if fila[1] is not None and fila[1] not in padres:
            padres[fila[1]] = fila[2]
    fam_por_campana = g.familia_efectiva_por_campana(pares)
    previos = {
        fila[0]: _PrevioHoja(
            target=fila[1], procedencia=fila[2], cycle_id=fila[3], ad_group_id=fila[4]
        )
        for fila in conn.execute(_SQL_TARGETS_PREVIOS, (platform, cycle_id)).fetchall()
    }
    anclas_grupo = _anclas_grupo(previos)
    notas: dict[int, dict] = {}
    for fid, med in mediciones.items():
        res = g.resuelve_target_margen(med, fraccion, hoy, None, destino, g.MARGEN_DIAS_MIN_FAMILIA)
        notas[fid] = {
            "margen_neto_pct": _dec_str(med.margen_neto_pct),
            "margen_historico_pct": _dec_str(med.margen_historico_pct),
            "cobertura": _dec_str(med.cobertura),
            "dias_con_venta": med.dias_con_venta,
            "motivo": res.motivo,
            "derivado": _dec_str(res.derivado),
            "aplicado": _dec_str(res.aplicado),
        }
    return _FamiliasCiclo(
        mediciones,
        padres,
        fam_por_campana,
        previos,
        anclas_grupo,
        fraccion,
        hoy,
        destino,
        notas,
        economia,
    )


def _anclas_grupo(previos: dict[int, _PrevioHoja]) -> dict[int, Decimal]:
    """Ancla familiar por grupo (A7, puro): entre los previos margen_familia
    de SUS hojas, el maximo (cycle_id, hoja_id)."""
    mejor: dict[int, tuple[int, int, Decimal]] = {}
    for hoja_id, previo in previos.items():
        if previo.procedencia != "margen_familia" or previo.ad_group_id is None:
            continue
        candidato = (previo.cycle_id, hoja_id, previo.target)
        actual = mejor.get(previo.ad_group_id)
        if actual is None or (candidato[0], candidato[1]) > (actual[0], actual[1]):
            mejor[previo.ad_group_id] = candidato
    return {grupo_id: target for grupo_id, (_, _, target) in mejor.items()}


def _margen_familia_de_hoja(
    familias: _FamiliasCiclo,
    campaign_id: int,
    hoja_id: int | None,
    *,
    ancla_grupo: Decimal | None = None,
) -> tuple[Decimal | None, dict | None]:
    """Peldano margen_familia para UNA hoja (Python puro, sin queries)."""
    fam_id = familias.fam_por_campana.get(campaign_id)
    etiqueta = familias.mediciones.get(fam_id) if fam_id is not None else None
    if fam_id is not None and etiqueta is None:
        etiqueta = g.MEDICION_NULA
    padre_id = familias.padres.get(fam_id) if fam_id is not None else None
    padre = familias.mediciones.get(padre_id) if padre_id is not None else None
    previo = familias.previos.get(hoja_id) if hoja_id is not None else None
    ancla = None
    if hoja_id is None and ancla_grupo is not None:
        ultimo, tiene_previo, ancla = ancla_grupo, True, "grupo"
    elif hoja_id is None:
        ultimo, tiene_previo, ancla = familias.destino, False, "destino"
    else:
        ultimo, tiene_previo = productos.ancla_familia(
            (previo.target, previo.procedencia) if previo is not None else None,
            familias.destino,
            version=familias.economia,
        )
    res, ganadora = g.resuelve_target_margen_familia(
        etiqueta,
        padre,
        familias.fraccion,
        familias.hoy,
        ultimo,
        familias.destino,
        tiene_previo=tiene_previo,
    )
    if res.aplicado is None:
        return (None, None)
    usada = {"etiqueta": fam_id, "padre": padre_id}.get(ganadora if ganadora else "")
    if ganadora is None and etiqueta is None:
        med_ref = g.MEDICION_NULA
    else:
        med_ref = {"etiqueta": etiqueta, "padre": padre}.get(ganadora if ganadora else "", etiqueta)
        assert med_ref is not None
    snapshot = {
        "margen_neto_pct": _dec_str(med_ref.margen_neto_pct),
        "cobertura": _dec_str(med_ref.cobertura),
        "dias_con_venta": med_ref.dias_con_venta,
        "motivo": res.motivo,
        "target_aplicado": _dec_str(res.aplicado),
        "familia_etiqueta": fam_id,
        "familia_usada": usada,
        **({"ancla": ancla} if hoja_id is None else {}),
    }
    return (res.aplicado, snapshot)
