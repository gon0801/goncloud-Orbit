"""Medicion contrafactual de la regla economica, solo SELECT con ORBIT_DSN_READ.

Ejecutar dentro de orbit-app-1: python tools/replay_ads_economico.py. El resultado
JSON contiene senales economicas, no permisos de mutacion ni ahorro estimado.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from decimal import Decimal

from app.db import connect
from app.optimizer.windows import fin_ventana_cortes, inicio_ventana

LIMITES = {"USD": Decimal("80"), "MXN": Decimal("1000")}


def riesgo(cost: Decimal | None, revenue: Decimal | None, moneda: str, target: Decimal | None):
    """Devuelve (califica, exceso); faltantes y moneda ajena se abstienen."""
    if (
        cost is None
        or revenue is None
        or target is None
        or moneda not in LIMITES
        or cost < 0
        or revenue < 0
        or target <= 0
    ):
        return None, None
    esperado = target * revenue / Decimal("100")
    exceso = cost - esperado
    return cost > Decimal("3") * esperado and exceso >= LIMITES[moneda], exceso


def ventana_vintage(filas, instante: dt.datetime):
    """Ultima observacion por entidad/fecha que el ciclo ya podia conocer."""
    actuales = {}
    for entidad, fecha, observado, moneda, cost, revenue in filas:
        if observado > instante:
            continue
        clave = (entidad, fecha)
        previo = actuales.get(clave)
        if previo is None or observado > previo[0]:
            actuales[clave] = (observado, moneda, cost, revenue)
    por_entidad = defaultdict(dict)
    for (entidad, fecha), valor in actuales.items():
        por_entidad[entidad][fecha] = valor
    return por_entidad


def agregado(fechas, instante):
    if not fechas:
        return None
    fin = fin_ventana_cortes(max(fechas), instante)
    inicio = inicio_ventana(fin)
    valores = [v for d, v in fechas.items() if inicio <= d <= fin]
    if len(valores) < 7:
        return None
    monedas = {v[1] for v in valores}
    if len(monedas) != 1 or any(v[2] is None or v[3] is None for v in valores):
        return None
    moneda = monedas.pop()
    return {
        "desde": inicio.isoformat(),
        "hasta": fin.isoformat(),
        "fechas": len(valores),
        "moneda": moneda,
        "cost": sum((v[2] for v in valores), Decimal("0")),
        "revenue": sum((v[3] for v in valores), Decimal("0")),
    }


def medir(conn, desde: dt.date, hasta: dt.date):
    ciclos = list(
        conn.execute(
            "SELECT c.id,c.platform,c.started_at,min(d.decided_at),max(d.decided_at) "
            "FROM optimizer_cycle c LEFT JOIN decision d ON d.cycle_id=c.id "
            "WHERE c.started_at::date BETWEEN %s AND %s AND c.platform IS NOT NULL "
            "GROUP BY c.id,c.platform,c.started_at ORDER BY c.started_at",
            (desde, hasta),
        )
    )
    entidades = {}
    for ident, kind, platform, padre, nombre in conn.execute(
        "SELECT id,kind::text,platform::text,parent_id,name FROM ad_entity "
        "WHERE kind IN ('campaign','ad_group','keyword','product_target')"
    ):
        entidades[ident] = (kind, platform, padre, nombre)
    campana_de = {}
    for ident, (kind, _platform, padre, _) in entidades.items():
        if kind == "campaign":
            campana_de[ident] = ident
        elif kind in ("keyword", "product_target") and padre in entidades:
            abuelo = entidades[padre][2]
            if abuelo in entidades and entidades[abuelo][0] == "campaign":
                campana_de[ident] = abuelo
    limite_observacion = dt.datetime.combine(hasta + dt.timedelta(days=1), dt.time(), dt.UTC)
    metricas = list(
        conn.execute(
            "SELECT ad_entity_id,metric_date,observed_at,metric_currency::text,cost,ad_revenue "
            "FROM ads_metric_observation WHERE observed_at <= %s ORDER BY observed_at",
            (limite_observacion,),
        )
    )
    targets_entidad = defaultdict(lambda: defaultdict(set))
    targets_compartidos = defaultdict(lambda: defaultdict(set))
    targets_plataforma = defaultdict(lambda: defaultdict(set))
    fuentes_compartidas = frozenset(
        {"goal_campana", "goal_plataforma", "margen_plataforma", "setting_plataforma"}
    )
    for ciclo, entidad, valor, fuente in conn.execute(
        "SELECT d.cycle_id,d.ad_entity_id,d.inputs->>'target_acos_pct_usado',"
        "d.inputs->>'target_procedencia' FROM decision d "
        "JOIN optimizer_cycle c ON c.id=d.cycle_id "
        "WHERE c.started_at::date BETWEEN %s AND %s",
        (desde, hasta),
    ):
        campana = campana_de.get(entidad)
        if campana is not None and valor is not None:
            par = (Decimal(valor), fuente)
            targets_entidad[ciclo][entidad].add(par)
            if fuente in fuentes_compartidas:
                targets_compartidos[ciclo][campana].add(par)
            if fuente == "margen_plataforma":
                targets_plataforma[ciclo][entidades[entidad][1]].add(par)
    goals = {}
    for campana, target, updated in conn.execute(
        "SELECT ad_entity_id,target_acos_pct,updated_at FROM ads_optimizer_goal "
        "WHERE scope='campaign'"
    ):
        goals[campana] = (target, updated)
    salida = []
    sin_reloj = []
    for ciclo, platform, _started_at, decidido_min, decidido_max in ciclos:
        if decidido_min is None or decidido_min != decidido_max:
            sin_reloj.append({"cycle": ciclo, "platform": platform, "reason": "sin_reloj_unico"})
            continue
        instante = decidido_min
        vintage = ventana_vintage(metricas, instante)
        plataforma = targets_plataforma[ciclo][platform]
        for entidad, fechas in vintage.items():
            meta = entidades.get(entidad)
            if (
                meta is None
                or meta[1] != platform
                or meta[0] not in ("campaign", "keyword", "product_target")
            ):
                continue
            resumen = agregado(fechas, instante)
            if resumen is None:
                continue
            campana = campana_de.get(entidad)
            if campana is None:
                continue
            propios = targets_entidad[ciclo].get(entidad, set())
            if len(propios) == 1:
                target, fuente = next(iter(propios))
                procedencia = "freeze_entidad"
            elif len(propios) > 1:
                target = fuente = None
                procedencia = "freeze_inconsistente"
            elif len(targets_compartidos[ciclo][campana]) == 1:
                target, fuente = next(iter(targets_compartidos[ciclo][campana]))
                procedencia = "freeze_compartido_campana"
            elif len(targets_compartidos[ciclo][campana]) > 1:
                target = fuente = None
                procedencia = "freeze_compartido_inconsistente"
            elif (
                campana in goals
                and goals[campana][1] <= instante
                and goals[campana][0] is None
                and len(plataforma) == 1
            ):
                target, fuente = next(iter(plataforma))
                procedencia = "goal_estable_y_freeze_plataforma"
            else:
                target = fuente = None
                procedencia = "sin_target_historico"
            califica, exceso = riesgo(
                resumen["cost"], resumen["revenue"], resumen["moneda"], target
            )
            salida.append(
                {
                    "cycle": ciclo,
                    "as_of": instante,
                    "platform": platform,
                    "entity": entidad,
                    "kind": meta[0],
                    "campaign": campana,
                    "name": meta[3],
                    **resumen,
                    "target": target,
                    "target_source": fuente,
                    "target_evidence": procedencia,
                    "candidate": califica,
                    "excess": exceso,
                }
            )
    return {"rows": salida, "cycles_without_decision_clock": sin_reloj}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desde", type=dt.date.fromisoformat, default=dt.date(2026, 9, 11))
    parser.add_argument("--hasta", type=dt.date.fromisoformat, default=dt.date(2026, 9, 24))
    args = parser.parse_args()
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        parser.error("ORBIT_DSN_READ no configurado")
    with connect(dsn) as conn:
        filas = medir(conn, args.desde, args.hasta)
    print(json.dumps(filas, default=str, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
