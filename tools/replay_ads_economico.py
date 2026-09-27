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


_FUENTES_COMPARTIDAS = frozenset(
    {"goal_campana", "goal_plataforma", "margen_plataforma", "setting_plataforma"}
)


def _congela_targets(conn, inicio_utc, fin_utc, campana_de, targets_entidad, targets_compartidos):
    """Colecciona el target por (ciclo, entidad) en los diccionarios dados.

    Fuente PRIMARIA: target_acos_ciclo (fila por hoja y ciclo, decision o
    no). Los ciclos SIN NINGUNA fila (anteriores a 0046) caen al freeze que
    sus decisiones ya traen en inputs: sin ese fallback, toda la ventana
    historica con decisiones perdria su target (bloqueante del review r2).
    El goal vigente NO es fuente en ningun camino (se edita).
    """
    ciclos_con_freeze: set[int] = set()
    for ciclo, entidad, valor, fuente in conn.execute(
        "SELECT t.cycle_id,t.ad_entity_id,t.target_acos_pct,t.procedencia "
        "FROM target_acos_ciclo t "
        "JOIN optimizer_cycle c ON c.id=t.cycle_id "
        "WHERE c.started_at >= %s AND c.started_at < %s",
        (inicio_utc, fin_utc),
    ):
        ciclos_con_freeze.add(ciclo)
        campana = campana_de.get(entidad)
        if campana is not None and valor is not None:
            par = (valor, fuente)
            targets_entidad[ciclo][entidad].add(par)
            if fuente in _FUENTES_COMPARTIDAS:
                targets_compartidos[ciclo][campana].add(par)
    for ciclo, entidad, valor, fuente in conn.execute(
        "SELECT d.cycle_id,d.ad_entity_id,d.inputs->>'target_acos_pct_usado',"
        "d.inputs->>'target_procedencia' FROM decision d "
        "JOIN optimizer_cycle c ON c.id=d.cycle_id "
        "WHERE c.started_at >= %s AND c.started_at < %s",
        (inicio_utc, fin_utc),
    ):
        if ciclo in ciclos_con_freeze:
            continue  # el ciclo tiene tabla: la tabla manda, no se mezclan fuentes
        campana = campana_de.get(entidad)
        if campana is not None and valor is not None:
            par = (Decimal(valor), fuente)
            targets_entidad[ciclo][entidad].add(par)
            if fuente in _FUENTES_COMPARTIDAS:
                targets_compartidos[ciclo][campana].add(par)


def medir(conn, desde: dt.date, hasta: dt.date):
    inicio_utc = dt.datetime.combine(desde, dt.time(), dt.UTC)
    fin_utc = dt.datetime.combine(hasta + dt.timedelta(days=1), dt.time(), dt.UTC)
    ciclos = list(
        conn.execute(
            "SELECT c.id,c.platform,c.started_at,min(d.decided_at),max(d.decided_at) "
            "FROM optimizer_cycle c LEFT JOIN decision d ON d.cycle_id=c.id "
            "WHERE c.started_at >= %s AND c.started_at < %s AND c.platform IS NOT NULL "
            "GROUP BY c.id,c.platform,c.started_at ORDER BY c.started_at",
            (inicio_utc, fin_utc),
        )
    )
    limite_observacion = max(
        [fin_utc] + [decidido_max for *_, decidido_max in ciclos if decidido_max is not None]
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
    metricas = list(
        conn.execute(
            "SELECT ad_entity_id,metric_date,observed_at,metric_currency::text,cost,ad_revenue "
            "FROM ads_metric_observation WHERE observed_at <= %s ORDER BY observed_at",
            (limite_observacion,),
        )
    )
    targets_entidad = defaultdict(lambda: defaultdict(set))
    targets_compartidos = defaultdict(lambda: defaultdict(set))
    _congela_targets(conn, inicio_utc, fin_utc, campana_de, targets_entidad, targets_compartidos)
    salida = []
    sin_reloj = []
    for ciclo, platform, _started_at, decidido_min, decidido_max in ciclos:
        if decidido_min is None or decidido_min != decidido_max:
            sin_reloj.append({"cycle": ciclo, "platform": platform, "reason": "sin_reloj_unico"})
            continue
        instante = decidido_min
        vintage = ventana_vintage(metricas, instante)
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
