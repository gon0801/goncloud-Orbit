import datetime as dt
import hashlib
import json
import os
from decimal import Decimal

from app.db import connect
from app.redaction import register_secret, scrub

SOURCE = (
    '"""Medicion contrafactual de la regla economica, solo S'
    "ELECT con ORBIT_DSN_READ.\n\nEjecutar dentro de orbit-app"
    "-1: python tools/replay_ads_economico.py. El resultado\n"
    "JSON contiene senales economicas, no permisos de mutaci"
    'on ni ahorro estimado.\n"""\n\nfrom __future__ import anno'
    "tations\n\nimport argparse\nimport datetime as dt\nimport j"
    "son\nimport os\nfrom collections import defaultdict\nfrom "
    "decimal import Decimal\n\nfrom app.db import connect\nfrom"
    " app.optimizer.windows import fin_ventana_cortes, inici"
    'o_ventana\n\nLIMITES = {"USD": Decimal("80"), "MXN": Deci'
    'mal("1000")}\n\n\ndef riesgo(cost: Decimal | None, revenue'
    ": Decimal | None, moneda: str, target: Decimal | None):"
    '\n    """Devuelve (califica, exceso); faltantes y moneda'
    ' ajena se abstienen."""\n    if (\n        cost is None\n '
    "       or revenue is None\n        or target is None\n   "
    "     or moneda not in LIMITES\n        or cost < 0\n     "
    "   or revenue < 0\n        or target <= 0\n    ):\n       "
    " return None, None\n    esperado = target * revenue / De"
    'cimal("100")\n    exceso = cost - esperado\n    return co'
    'st > Decimal("3") * esperado and exceso >= LIMITES[mone'
    "da], exceso\n\n\ndef ventana_vintage(filas, instante: dt.d"
    'atetime):\n    """Ultima observacion por entidad/fecha q'
    'ue el ciclo ya podia conocer."""\n    actuales = {}\n    '
    "for entidad, fecha, observado, moneda, cost, revenue in"
    " filas:\n        if observado > instante:\n            co"
    "ntinue\n        clave = (entidad, fecha)\n        previo "
    "= actuales.get(clave)\n        if previo is None or obse"
    "rvado > previo[0]:\n            actuales[clave] = (obser"
    "vado, moneda, cost, revenue)\n    por_entidad = defaultd"
    "ict(dict)\n    for (entidad, fecha), valor in actuales.i"
    "tems():\n        por_entidad[entidad][fecha] = valor\n   "
    " return por_entidad\n\n\ndef agregado(fechas, instante):\n "
    "   if not fechas:\n        return None\n    fin = fin_ven"
    "tana_cortes(max(fechas), instante)\n    inicio = inicio_"
    "ventana(fin)\n    valores = [v for d, v in fechas.items("
    ") if inicio <= d <= fin]\n    if len(valores) < 7:\n     "
    "   return None\n    monedas = {v[1] for v in valores}\n  "
    "  if len(monedas) != 1 or any(v[2] is None or v[3] is N"
    "one for v in valores):\n        return None\n    moneda ="
    ' monedas.pop()\n    return {\n        "desde": inicio.iso'
    'format(),\n        "hasta": fin.isoformat(),\n        "fe'
    'chas": len(valores),\n        "moneda": moneda,\n        '
    '"cost": sum((v[2] for v in valores), Decimal("0")),\n   '
    '     "revenue": sum((v[3] for v in valores), Decimal("0'
    '")),\n    }\n\n\n_FUENTES_COMPARTIDAS = frozenset(\n    {"go'
    'al_campana", "goal_plataforma", "margen_plataforma", "s'
    'etting_plataforma"}\n)\n\n\ndef _congela_targets(conn, inic'
    "io_utc, fin_utc, campana_de, targets_entidad, targets_c"
    'ompartidos):\n    """Colecciona el target por (ciclo, en'
    "tidad) en los diccionarios dados.\n\n    Fuente PRIMARIA:"
    " target_acos_ciclo (fila por hoja y ciclo, decision o\n "
    "   no). Los ciclos SIN NINGUNA fila (anteriores a 0046)"
    " caen al freeze que\n    sus decisiones ya traen en inpu"
    "ts: sin ese fallback, toda la ventana\n    historica con"
    " decisiones perdria su target (bloqueante del review r2"
    ").\n    El goal vigente NO es fuente en ningun camino (s"
    'e edita).\n    """\n    ciclos_con_freeze: set[int] = set'
    "()\n    for ciclo, entidad, valor, fuente in conn.execut"
    'e(\n        "SELECT t.cycle_id,t.ad_entity_id,t.target_a'
    'cos_pct,t.procedencia "\n        "FROM target_acos_ciclo'
    ' t "\n        "JOIN optimizer_cycle c ON c.id=t.cycle_id'
    ' "\n        "WHERE c.started_at >= %s AND c.started_at <'
    ' %s",\n        (inicio_utc, fin_utc),\n    ):\n        cic'
    "los_con_freeze.add(ciclo)\n        campana = campana_de."
    "get(entidad)\n        if campana is not None and valor i"
    "s not None:\n            par = (valor, fuente)\n         "
    "   targets_entidad[ciclo][entidad].add(par)\n           "
    " if fuente in _FUENTES_COMPARTIDAS:\n                tar"
    "gets_compartidos[ciclo][campana].add(par)\n    for ciclo"
    ', entidad, valor, fuente in conn.execute(\n        "SELE'
    "CT d.cycle_id,d.ad_entity_id,d.inputs->>'target_acos_pc"
    "t_usado',\"\n        \"d.inputs->>'target_procedencia' FRO"
    'M decision d "\n        "JOIN optimizer_cycle c ON c.id='
    'd.cycle_id "\n        "WHERE c.started_at >= %s AND c.st'
    'arted_at < %s",\n        (inicio_utc, fin_utc),\n    ):\n '
    "       if ciclo in ciclos_con_freeze:\n            conti"
    "nue  # el ciclo tiene tabla: la tabla manda, no se mezc"
    "lan fuentes\n        campana = campana_de.get(entidad)\n "
    "       if campana is not None and valor is not None:\n  "
    "          par = (Decimal(valor), fuente)\n            ta"
    "rgets_entidad[ciclo][entidad].add(par)\n            if f"
    "uente in _FUENTES_COMPARTIDAS:\n                targets_"
    "compartidos[ciclo][campana].add(par)\n\n\ndef medir(conn, "
    "desde: dt.date, hasta: dt.date):\n    inicio_utc = dt.da"
    "tetime.combine(desde, dt.time(), dt.UTC)\n    fin_utc = "
    "dt.datetime.combine(hasta + dt.timedelta(days=1), dt.ti"
    "me(), dt.UTC)\n    ciclos = list(\n        conn.execute(\n"
    '            "SELECT c.id,c.platform,c.started_at,min(d.'
    'decided_at),max(d.decided_at) "\n            "FROM optim'
    'izer_cycle c LEFT JOIN decision d ON d.cycle_id=c.id "\n'
    '            "WHERE c.started_at >= %s AND c.started_at '
    '< %s AND c.platform IS NOT NULL "\n            "GROUP BY'
    ' c.id,c.platform,c.started_at ORDER BY c.started_at",\n '
    "           (inicio_utc, fin_utc),\n        )\n    )\n    l"
    "imite_observacion = max(\n        [fin_utc] + [decidido_"
    "max for *_, decidido_max in ciclos if decidido_max is n"
    "ot None]\n    )\n    entidades = {}\n    for ident, kind, "
    'platform, padre, nombre in conn.execute(\n        "SELEC'
    "T id,kind::text,platform::text,parent_id,name FROM ad_e"
    "ntity \"\n        \"WHERE kind IN ('campaign','ad_group','"
    "keyword','product_target')\"\n    ):\n        entidades[id"
    "ent] = (kind, platform, padre, nombre)\n    campana_de ="
    " {}\n    for ident, (kind, _platform, padre, _) in entid"
    'ades.items():\n        if kind == "campaign":\n          '
    '  campana_de[ident] = ident\n        elif kind in ("keyw'
    'ord", "product_target") and padre in entidades:\n       '
    "     abuelo = entidades[padre][2]\n            if abuelo"
    ' in entidades and entidades[abuelo][0] == "campaign":\n '
    "               campana_de[ident] = abuelo\n    metricas "
    '= list(\n        conn.execute(\n            "SELECT ad_en'
    "tity_id,metric_date,observed_at,metric_currency::text,c"
    'ost,ad_revenue "\n            "FROM ads_metric_observati'
    'on WHERE observed_at <= %s ORDER BY observed_at",\n     '
    "       (limite_observacion,),\n        )\n    )\n    targe"
    "ts_entidad = defaultdict(lambda: defaultdict(set))\n    "
    "targets_compartidos = defaultdict(lambda: defaultdict(s"
    "et))\n    _congela_targets(conn, inicio_utc, fin_utc, ca"
    "mpana_de, targets_entidad, targets_compartidos)\n    sal"
    "ida = []\n    sin_reloj = []\n    for ciclo, platform, _s"
    "tarted_at, decidido_min, decidido_max in ciclos:\n      "
    "  if decidido_min is None or decidido_min != decidido_m"
    'ax:\n            sin_reloj.append({"cycle": ciclo, "plat'
    'form": platform, "reason": "sin_reloj_unico"})\n        '
    "    continue\n        instante = decidido_min\n        vi"
    "ntage = ventana_vintage(metricas, instante)\n        for"
    " entidad, fechas in vintage.items():\n            meta ="
    " entidades.get(entidad)\n            if (\n              "
    "  meta is None\n                or meta[1] != platform\n "
    '               or meta[0] not in ("campaign", "keyword"'
    ', "product_target")\n            ):\n                cont'
    "inue\n            resumen = agregado(fechas, instante)\n "
    "           if resumen is None:\n                continue"
    "\n            campana = campana_de.get(entidad)\n        "
    "    if campana is None:\n                continue\n      "
    "      propios = targets_entidad[ciclo].get(entidad, set"
    "())\n            if len(propios) == 1:\n                t"
    "arget, fuente = next(iter(propios))\n                pro"
    'cedencia = "freeze_entidad"\n            elif len(propio'
    "s) > 1:\n                target = fuente = None\n        "
    '        procedencia = "freeze_inconsistente"\n          '
    "  elif len(targets_compartidos[ciclo][campana]) == 1:\n "
    "               target, fuente = next(iter(targets_compa"
    "rtidos[ciclo][campana]))\n                procedencia = "
    '"freeze_compartido_campana"\n            elif len(target'
    "s_compartidos[ciclo][campana]) > 1:\n                tar"
    'get = fuente = None\n                procedencia = "free'
    'ze_compartido_inconsistente"\n            else:\n        '
    "        target = fuente = None\n                proceden"
    'cia = "sin_target_historico"\n            califica, exce'
    'so = riesgo(\n                resumen["cost"], resumen["'
    'revenue"], resumen["moneda"], target\n            )\n    '
    "        salida.append(\n                {\n              "
    '      "cycle": ciclo,\n                    "as_of": inst'
    'ante,\n                    "platform": platform,\n       '
    '             "entity": entidad,\n                    "ki'
    'nd": meta[0],\n                    "campaign": campana,\n'
    '                    "name": meta[3],\n                  '
    '  **resumen,\n                    "target": target,\n    '
    '                "target_source": fuente,\n              '
    '      "target_evidence": procedencia,\n                 '
    '   "candidate": califica,\n                    "excess":'
    ' exceso,\n                }\n            )\n    return {"r'
    'ows": salida, "cycles_without_decision_clock": sin_relo'
    "j}\n\n\ndef main():\n    parser = argparse.ArgumentParser(d"
    'escription=__doc__)\n    parser.add_argument("--desde", '
    "type=dt.date.fromisoformat, default=dt.date(2026, 9, 11"
    '))\n    parser.add_argument("--hasta", type=dt.date.from'
    "isoformat, default=dt.date(2026, 9, 24))\n    args = par"
    'ser.parse_args()\n    dsn = os.environ.get("ORBIT_DSN_RE'
    'AD")\n    if not dsn:\n        parser.error("ORBIT_DSN_RE'
    'AD no configurado")\n    with connect(dsn) as conn:\n    '
    "    filas = medir(conn, args.desde, args.hasta)\n    pri"
    "nt(json.dumps(filas, default=str, ensure_ascii=True, se"
    'parators=(",", ":")))\n\n\nif __name__ == "__main__":\n    '
    "main()\n"
)
ns = {"__name__": "replay_evidencia"}
exec(compile(SOURCE, "tools/replay_ads_economico.py", "exec"), ns)
dsn = os.environ["ORBIT_DSN_READ"]
register_secret(dsn)
try:
    with connect(dsn) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='15s'")
        now = conn.execute("SELECT now()").fetchone()[0]
        r = ns["medir"](conn, dt.date(2026, 10, 5), dt.date(2026, 11, 3))
        r["metadata"] = {
            "utc": now,
            "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
            "read_only": conn.execute("SHOW transaction_read_only").fetchone()[0],
            "timeout": conn.execute("SHOW statement_timeout").fetchone()[0],
        }
        obs = list(
            conn.execute(
                "SELECT ad_entity_id,metric_date,observed_at,metric_currency::text,cost,ad_revenue "
                "FROM ads_metric_observation WHERE metric_date >= %s AND metric_date <= %s "
                "AND observed_at <= %s ORDER BY observed_at",
                (
                    min(dt.date.fromisoformat(x["desde"]) for x in r["rows"]),
                    max(dt.date.fromisoformat(x["hasta"]) for x in r["rows"]),
                    now,
                ),
            )
        )
        cache = {}
        original_cache = {}
        maturity = []
        for row in r["rows"]:
            horizon = row["as_of"] + dt.timedelta(days=10)
            status = "horizonte_futuro" if horizon > now else None
            mature = None
            if status is None:
                if horizon not in cache:
                    cache[horizon] = ns["ventana_vintage"](obs, horizon)
                start = dt.date.fromisoformat(row["desde"])
                end = dt.date.fromisoformat(row["hasta"])
                if row["as_of"] not in original_cache:
                    original_cache[row["as_of"]] = ns["ventana_vintage"](obs, row["as_of"])
                original_dates = {
                    d
                    for d in original_cache[row["as_of"]].get(row["entity"], {})
                    if start <= d <= end
                }
                mature_dates = {
                    d for d in cache[horizon].get(row["entity"], {}) if start <= d <= end
                }
                values = [
                    v for d, v in cache[horizon].get(row["entity"], {}).items() if start <= d <= end
                ]
                if (
                    original_dates != mature_dates
                    or len(values) != row["fechas"]
                    or any(v[1] != row["moneda"] or v[2] is None or v[3] is None for v in values)
                ):
                    status = "cobertura_incompleta_o_moneda"
                elif not any(v[0] > row["as_of"] for v in values):
                    status = "sin_revision_posterior_observable"
                else:
                    cost = sum((v[2] for v in values), Decimal(0))
                    revenue = sum((v[3] for v in values), Decimal(0))
                    cand, excess = ns["riesgo"](cost, revenue, row["moneda"], row["target"])
                    mature = {
                        "cost": cost,
                        "revenue": revenue,
                        "candidate": cand,
                        "excess": excess,
                        "fechas": len(values),
                        "max_observed_at": max(v[0] for v in values),
                    }
                    status = "sin_target" if cand is None else "observable"
            maturity.append(
                {
                    "cycle": row["cycle"],
                    "entity": row["entity"],
                    "kind": row["kind"],
                    "moneda": row["moneda"],
                    "desde": row["desde"],
                    "hasta": row["hasta"],
                    "target": row["target"],
                    "horizon": horizon,
                    "candidate_asof": row["candidate"],
                    "status": status,
                    "mature": mature,
                }
            )
        r["maturity_10d"] = maturity
        print(json.dumps(r, default=str, ensure_ascii=True))
except Exception as e:
    print(json.dumps({"error_type": type(e).__name__, "error": scrub(str(e))}))
    raise SystemExit(1) from None
