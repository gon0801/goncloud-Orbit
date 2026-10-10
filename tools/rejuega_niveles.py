"""Rejuego de niveles_v3 sobre ciclos pasados (BIDS 02 M.4). SOLO SELECT.

Por cada ciclo live del rango: arma cada caso con el target que ese ciclo
congelo en `target_acos_ciclo`, el bid de ese dia (caminando `v_cambio_bid`
hacia atras desde el bid de hoy), el piso y el techo de hoy del goal y los
`InsumosPausa` anclados en el ciclo; las metricas y los cambios se leen con
`visto_el` (lo que el ciclo veia). Decide con `decide` y califica el
`InformeRejuego` del bosquejo (los cuatro criterios de `cumple`).

Aproximaciones declaradas (el plan fija solo los cuatro insumos):
- Equilibrio: el de `notes.target` del ciclo (margen_neto_pct con
  procedencia margen_plataforma; si no, None y R3 no dispara).
- Settings, goals, umbrales de pausa, vetos e inertes: vigentes (al cierre
  del periodo). Los goals no tienen historia; los settings si, pero el plan
  no pide leerlos por ciclo.
- La ventana de cortes se ancla en cada ciclo pero sobre `v_metric_latest`
  (no hay lector bitemporal de cortes): termina 10+ dias antes del ciclo,
  con metricas maduras que ya no se mueven.
- Elegible sin target congelado ese ciclo: se excluye de cobertura (no
  llego al calculo entonces; no es falla de ingesta).
- Dañadas vigentes al cierre, no por ciclo: un recorte viejo se juzga
  contra la lista de hoy.
- `por_motivo` cuenta Mover (recortes y subidas); las pausas no se cuentan
  (el bosquejo no trae campo y R0 es identico al de hoy).
- El veto se lee con un espejo del SQL de `app/apply_cola.py` (este modulo
  no importa la cola de escritura).
- Sin casos armados, determinismo 100 (nada se contradice) y cobertura 0
  (nada evidencia ingesta sana).

Uso: ORBIT_DSN_READ=<dsn> PYTHONPATH=. python tools/rejuega_niveles.py
--platform amazon_mx --ciclos 30. Sale 0 si cumple, 1 si no; la ultima
linea dice `cumple: true|false`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal

from app.db import connect
from app.lecturas_caso import lee_plataforma
from app.optimizer import bid as motor_bid
from app.optimizer import cortes, windows
from app.optimizer import goals as g
from app.optimizer import politica as pol
from app.optimizer.caso import (
    BidVigente,
    CasoHoja,
    EconomiaPlataforma,
    InsumosPausa,
    Plataforma,
)
from app.optimizer.politica import Mantener, Mover, Regresar, decide
from app.optimizer.replay import reproduce
from app.pantalla_danadas import lee_danadas

# Motivos de recorte que salen de R3, R4, R9 u R11 (criterio 2 de cumple).
MOTIVOS_RECORTE = frozenset(
    {
        pol.MOTIVO_PIERDE_DINERO,
        pol.MOTIVO_PIERDE_DINERO_FUERTE,
        pol.MOTIVO_GRUPO_SANGRA_VENDEDORA,
        pol.MOTIVO_GASTO_SIN_VENTA,
        pol.MOTIVO_GASTO_SIN_VENTA_DOBLE,
        pol.MOTIVO_GRUPO_SANGRA,
    }
)

_SQL_CICLOS = """
SELECT id, started_at, notes
  FROM optimizer_cycle
 WHERE platform = %s::platform AND mode = 'live'
   AND (started_at AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
 ORDER BY started_at
"""

_SQL_ULTIMOS_CICLOS = """
SELECT id, started_at
  FROM optimizer_cycle
 WHERE platform = %s::platform AND mode = 'live'
 ORDER BY started_at DESC
 LIMIT %s
"""

_SQL_CONFIG = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""

_SQL_CAMPANAS = """
SELECT id FROM ad_entity WHERE platform = %s::platform AND kind = 'campaign'
"""

_SQL_GOALS = """
SELECT scope, ad_entity_id, platform, target_acos_pct, bid_floor, bid_ceiling,
       bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode
  FROM ads_optimizer_goal
 WHERE platform = %s::platform OR ad_entity_id = ANY(%s::bigint[])
"""

_SQL_HOJAS = """
SELECT hoja_id, ad_group_id, campana_id, current_bid, bid_currency
  FROM v_hoja_activa WHERE platform = %s
"""

_SQL_INERTES = """
SELECT id FROM v_entidad_inerte WHERE platform = %s::platform
"""

# Espejo de app/apply_cola.py:_SQL_CLAVES_BLOQUEADAS (este modulo no importa
# la cola de escritura; solo necesita las claves entity_cut vigentes).
_SQL_VETOS = """
SELECT ad_entity_id
  FROM apply_queue
 WHERE platform = %s::platform AND familia = 'entity_cut' AND search_term IS NULL
   AND (estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
        OR (estado = 'vetoed' AND vence_el > %s))
"""

_SQL_TARGETS = """
SELECT ad_entity_id, target_acos_pct FROM target_acos_ciclo WHERE cycle_id = %s
"""

_SQL_CAMBIOS = """
SELECT c.hoja_id, c.confirmado_el, c.bid_antes, c.bid_despues, c.origen
  FROM v_cambio_bid c
  JOIN ad_entity k ON k.id = c.hoja_id
 WHERE k.platform = %s::platform
 ORDER BY c.hoja_id, c.confirmado_el, c.decision_id
"""

_SQL_VIEJOS = """
SELECT c.hoja_id, (c.confirmado_el AT TIME ZONE 'UTC')::date
  FROM v_cambio_bid c
  JOIN ad_entity k ON k.id = c.hoja_id
 WHERE k.platform = %s::platform AND c.origen = 'motor'
   AND c.bid_despues < c.bid_antes
   AND (c.confirmado_el AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
"""

_SQL_GUARDADOS = """
SELECT kind, new_value, value_currency, inputs
  FROM decision
 WHERE cycle_id = ANY(%s)
   AND kind IN ('bid', 'pause')
   AND inputs->>'politica' = 'niveles_v3'
   AND inputs ? 'caso'
"""

_SQL_GASTO = """
SELECT m.ad_entity_id, sum(m.cost)
  FROM v_metric_latest m
  JOIN ad_entity e ON e.id = m.ad_entity_id
 WHERE e.platform = %s::platform
   AND e.kind IN ('keyword', 'product_target')
   AND m.metric_date BETWEEN %s AND %s
 GROUP BY m.ad_entity_id
"""


@dataclass(frozen=True)
class InformeRejuego:
    plataforma: Plataforma
    desde: dt.date
    hasta: dt.date
    por_motivo: dict[str, int]
    recortes_del_motor_viejo: int
    recortes_que_se_repetirian: int
    hojas_recortadas: int
    parte_del_gasto_recortada_pct: Decimal
    regresos: int
    recortes_sobre_danadas: int
    deterministas_pct: Decimal = Decimal("0")
    invariantes_rotos: int = 0
    cobertura_pct: Decimal = Decimal("0")
    vendedoras_que_recortaria: tuple[tuple[int, str], ...] = ()
    ciclos: int = 0
    casos: int = 0

    def cumple(self) -> bool:
        """Criterio de encendido, mecanico y del mismo dia:
        1. deterministas_pct == 100
        2. invariantes_rotos == 0
        3. cobertura_pct >= 95
        4. recortes_sobre_danadas == 0."""
        return (
            self.deterministas_pct == 100
            and self.invariantes_rotos == 0
            and self.cobertura_pct >= 95
            and self.recortes_sobre_danadas == 0
        )


def _equilibrio(notes) -> Decimal | None:
    """Margen neto que el ciclo resolvio (notes.target): solo con
    procedencia margen_plataforma (espejo de _resuelve_target_ciclo); con
    otra procedencia o sin clave es None y R3 no dispara."""
    if not isinstance(notes, str):
        return None
    try:
        cuerpo = json.loads(notes)
    except ValueError:
        return None
    target = cuerpo.get("target") if isinstance(cuerpo, dict) else None
    if not isinstance(target, dict) or target.get("procedencia") != "margen_plataforma":
        return None
    valor = target.get("margen_neto_pct")
    return None if valor is None else Decimal(str(valor))


def _elegibles(conn, plataforma: str, ahora: dt.datetime) -> dict:
    """Hojas que pasan los filtros que no cambian: ENABLED triple (vista),
    goal resuelto habilitado y no off, sin veto vigente y no inerte.
    Devuelve hoja_id -> (ad_group_id, bid_hoy, piso, techo)."""
    campanas = [fila[0] for fila in conn.execute(_SQL_CAMPANAS, (plataforma,)).fetchall()]
    meta = conn.execute(_SQL_GOALS, (plataforma, campanas)).fetchall()
    goal_plat = None
    por_campana: dict[int, g.Goal] = {}
    for fila in meta:
        goal = g.Goal(
            scope=fila[0],
            ad_entity_id=fila[1],
            platform=fila[2],
            target_acos_pct=fila[3],
            bid_floor=fila[4],
            bid_ceiling=fila[5],
            bid_currency=fila[6],
            harvest_campaign_id=fila[7],
            harvest_ad_group_id=fila[8],
            harvest_default_bid=fila[9],
            enabled=fila[10],
            mode=fila[11],
        )
        if goal.scope == "platform":
            goal_plat = goal
        elif goal.ad_entity_id is not None:
            por_campana[goal.ad_entity_id] = goal
    vetadas = {fila[0] for fila in conn.execute(_SQL_VETOS, (plataforma, ahora)).fetchall()}
    inertes = {fila[0] for fila in conn.execute(_SQL_INERTES, (plataforma,)).fetchall()}
    moneda = motor_bid.PLATAFORMAS_MONEDA[plataforma]
    hojas = {}
    for hoja_id, grupo_id, campana_id, bid_hoy, _moneda in conn.execute(
        _SQL_HOJAS, (plataforma,)
    ).fetchall():
        if hoja_id in vetadas or hoja_id in inertes:
            continue
        resuelto = g.resuelve_goal(por_campana.get(campana_id), goal_plat)
        if resuelto is None or not resuelto.enabled or resuelto.mode == "off":
            continue
        piso, techo = g.resuelve_floor_ceiling(resuelto, moneda)
        hojas[hoja_id] = (grupo_id, bid_hoy, piso, techo)
    return hojas


def _bid_del_dia(current, cambios, started_at: dt.datetime):
    """Bid que la hoja tenia cuando el ciclo decidio: camina los cambios
    posteriores al ciclo hacia atras desde el bid de hoy. El bid_antes de
    un regreso del dueno se deriva del cambio anterior (como lecturas);
    sin cambio anterior es desconocido (regla 3: None)."""
    posteriores = [c for c in cambios if c[0] > started_at]
    if not posteriores:
        return current
    bid = current
    for i in range(len(cambios) - 1, -1, -1):
        if cambios[i][0] <= started_at:
            break
        antes = cambios[i][1]
        if antes is None:
            antes = cambios[i - 1][2] if i > 0 else None
            if antes is None:
                return None
        bid = antes
    return bid


def _viola(caso: CasoHoja, veredicto: Mover) -> bool:
    """Un recorte que no sale de R3, R4, R9 u R11, o que viola R2, R15, R16
    o R17. `decide` ya aplica esas reglas: esto es el candado que lo
    comprueba por fuera."""
    if veredicto.motivo not in MOTIVOS_RECORTE:
        return True
    ultimo = caso.trayectoria.ultimo
    if ultimo is None:
        return False
    efecto = caso.trayectoria.efecto
    assert efecto is not None
    if efecto.dias_post < pol.DIAS_EFECTO:
        return True
    if ultimo.direccion == -1:
        if efecto.clics_post is None or efecto.clics_post < pol.CLICS_NUEVOS:
            return True
        if efecto.impresiones_pre7 is None or efecto.impresiones_post is None:
            return True
        razon = efecto.razon_trafico()
        if razon is not None and razon < pol.RAZON_RETIENE:
            return True
    piso = caso.trayectoria.piso_aprendido
    return piso is not None and veredicto.bid_nuevo <= piso


def _pct(numerador: int, denominador: int) -> Decimal:
    return Decimal(100 * numerador) / Decimal(denominador)


def rejuega(conn, *, plataforma: Plataforma, desde: dt.date, hasta: dt.date) -> InformeRejuego:
    """Por cada ciclo live de [desde, hasta]: lee_plataforma(...,
    visto_el=ciclo.started_at) y decide cada hoja elegible con su historia
    REAL de bids hasta ese dia (rejuego a un paso). No espera ciclos
    nuevos ni escribe ninguna fila."""
    ciclos = conn.execute(_SQL_CICLOS, (plataforma, desde, hasta)).fetchall()
    if not ciclos:
        return InformeRejuego(
            plataforma,
            desde,
            hasta,
            {},
            0,
            0,
            0,
            Decimal("0"),
            0,
            0,
            deterministas_pct=Decimal("100"),
        )
    fila_config = conn.execute(_SQL_CONFIG).fetchone()
    settings = fila_config[0] if fila_config is not None else {}
    moneda = motor_bid.PLATAFORMAS_MONEDA[plataforma]
    gasto_concluir = g.gasto_para_concluir_desde_settings(settings, plataforma)
    conf_recorte = g.confianza_recorte_desde_settings(settings, plataforma)
    conf_subida = g.confianza_subida_desde_settings(settings, plataforma)
    economica = (
        motor_bid.POLITICA_PAUSE_ECONOMICA if g.pause_economica_desde_settings(settings) else None
    )
    referencia = max(fila[1] for fila in ciclos)
    elegibles = _elegibles(conn, plataforma, referencia)
    danadas = {h.hoja_id for h in lee_danadas(conn, plataforma=plataforma).hojas}
    evidencia_grupos = windows.ventanas_evidencia_ad_group(conn, plataforma, referencia)
    umbrales: dict = {}
    cambios: dict[int, list] = {}
    for hoja_id, confirmado, antes, despues, _origen in conn.execute(
        _SQL_CAMBIOS, (plataforma,)
    ).fetchall():
        cambios.setdefault(hoja_id, []).append((confirmado, antes, despues))
    por_motivo: dict[str, int] = {}
    recortes: set[tuple[int, int]] = set()
    hojas_recortadas: set[int] = set()
    vendedoras: set[tuple[int, str]] = set()
    regresos = 0
    invariantes = 0
    det_ok = 0
    det_total = 0
    completos = 0
    armados = 0
    for ciclo_id, started, notes in ciclos:
        economia = EconomiaPlataforma(
            moneda=moneda,
            equilibrio_acos_pct=_equilibrio(notes),
            gasto_para_concluir=gasto_concluir,
            confianza_recorte=conf_recorte,
            confianza_subida=conf_subida,
        )
        lecturas = lee_plataforma(conn, plataforma, started, economia=economia, visto_el=started)
        targets = {fila[0]: fila[1] for fila in conn.execute(_SQL_TARGETS, (ciclo_id,)).fetchall()}
        for hoja_id, (grupo_id, bid_hoy, piso, techo) in elegibles.items():
            if hoja_id not in targets:
                continue
            if grupo_id not in umbrales:
                umbrales[grupo_id] = cortes.umbral_corte(evidencia_grupos.get(grupo_id), "pause")
            corte = umbrales[grupo_id]
            caso = lecturas.caso(
                conn,
                hoja_id=hoja_id,
                ad_group_id=grupo_id,
                bid=BidVigente(
                    valor=_bid_del_dia(bid_hoy, cambios.get(hoja_id, ()), started),
                    moneda=moneda,
                    piso=piso,
                    techo=techo,
                ),
                target_acos_pct=targets[hoja_id],
                pausa=InsumosPausa(
                    cortes=windows.ventana_cortes(conn, hoja_id, started),
                    umbral_clics=corte.umbral,
                    gasto_minimo=motor_bid.PAUSE_COST_MIN[plataforma],
                    expected_clicks=corte.expected_clicks,
                    politica_economica=economica,
                ),
            )
            veredicto = decide(caso)
            armados += 1
            if not (
                isinstance(veredicto, Mantener) and veredicto.motivo == pol.MOTIVO_DATO_FALTANTE
            ):
                completos += 1
            det_total += 1
            if decide(CasoHoja.desde_json(caso.como_json())) == veredicto:
                det_ok += 1
            if isinstance(veredicto, Mover):
                por_motivo[veredicto.motivo] = por_motivo.get(veredicto.motivo, 0) + 1
                if veredicto.factor < 0:
                    recortes.add((ciclo_id, hoja_id))
                    hojas_recortadas.add(hoja_id)
                    if _viola(caso, veredicto):
                        invariantes += 1
                    crudos = caso.propia.pedidos_crudos() if caso.propia is not None else 0
                    if crudos is not None and crudos >= 1:
                        vendedoras.add((hoja_id, veredicto.motivo))
            elif isinstance(veredicto, Regresar):
                regresos += 1
    for kind, nuevo, curr, inputs in conn.execute(
        _SQL_GUARDADOS, ([ciclo_id for ciclo_id, _, _ in ciclos],)
    ).fetchall():
        det_total += 1
        try:
            reproducido = reproduce(inputs)
        except Exception:
            continue
        if reproducido == (kind, nuevo, curr):
            det_ok += 1
    viejos = conn.execute(_SQL_VIEJOS, (plataforma, desde, hasta)).fetchall()
    dia_ciclos: dict[dt.date, list[int]] = {}
    for ciclo_id, started, _notes in ciclos:
        dia_ciclos.setdefault(started.astimezone(dt.UTC).date(), []).append(ciclo_id)
    repetidos = sum(
        1
        for hoja_id, fecha in viejos
        if any((ciclo_id, hoja_id) in recortes for ciclo_id in dia_ciclos.get(fecha, ()))
    )
    gastos = {
        fila[0]: fila[1] for fila in conn.execute(_SQL_GASTO, (plataforma, desde, hasta)).fetchall()
    }
    gasto_recortado = sum((gastos.get(hoja_id) or Decimal("0")) for hoja_id in hojas_recortadas)
    gasto_total = sum(
        (gasto or Decimal("0")) for hoja_id, gasto in gastos.items() if hoja_id in elegibles
    )
    parte = Decimal("0") if gasto_total <= 0 else Decimal("100") * gasto_recortado / gasto_total
    danadas_recorte = sum(1 for _ciclo_id, hoja_id in recortes if hoja_id in danadas)
    return InformeRejuego(
        plataforma=plataforma,
        desde=desde,
        hasta=hasta,
        por_motivo=por_motivo,
        recortes_del_motor_viejo=len(viejos),
        recortes_que_se_repetirian=repetidos,
        hojas_recortadas=len(hojas_recortadas),
        parte_del_gasto_recortada_pct=parte,
        regresos=regresos,
        recortes_sobre_danadas=danadas_recorte,
        deterministas_pct=_pct(det_ok, det_total) if det_total else Decimal("100"),
        invariantes_rotos=invariantes,
        cobertura_pct=_pct(completos, armados) if armados else Decimal("0"),
        vendedoras_que_recortaria=tuple(sorted(vendedoras)),
        ciclos=len(ciclos),
        casos=armados,
    )


def texto_informe(informe: InformeRejuego) -> str:
    """El informe impreso: metricas, recortes y subidas por motivo, las
    vendedoras que recortaria y la ultima linea `cumple: true|false`."""
    lineas = [
        f"rejuego {informe.plataforma} {informe.desde.isoformat()}.."
        f"{informe.hasta.isoformat()}: {informe.ciclos} ciclos live, "
        f"{informe.casos} casos",
        f"deterministas_pct: {informe.deterministas_pct}",
        f"invariantes_rotos: {informe.invariantes_rotos}",
        f"cobertura_pct: {informe.cobertura_pct}",
        f"recortes_sobre_danadas: {informe.recortes_sobre_danadas}",
        f"recortes_del_motor_viejo: {informe.recortes_del_motor_viejo}",
        f"recortes_que_se_repetirian: {informe.recortes_que_se_repetirian}",
        f"hojas_recortadas: {informe.hojas_recortadas}",
        f"parte_del_gasto_recortada_pct: {informe.parte_del_gasto_recortada_pct}",
        f"regresos: {informe.regresos}",
        "por_motivo:",
    ]
    for motivo, n in sorted(informe.por_motivo.items(), key=lambda kv: (-kv[1], kv[0])):
        lineas.append(f"  {motivo}: {n}")
    lineas.append(f"vendedoras_que_recortaria ({len(informe.vendedoras_que_recortaria)}):")
    for hoja_id, motivo in informe.vendedoras_que_recortaria:
        lineas.append(f"  hoja {hoja_id}: {motivo}")
    lineas.append(f"cumple: {'true' if informe.cumple() else 'false'}")
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=("amazon_mx", "amazon_us"))
    parser.add_argument("--ciclos", required=True, type=int)
    args = parser.parse_args(argv)
    if args.ciclos <= 0:
        parser.error("--ciclos debe ser >= 1")
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        parser.error("ORBIT_DSN_READ no configurado")
    with connect(dsn) as conn:
        ultimos = conn.execute(_SQL_ULTIMOS_CICLOS, (args.platform, args.ciclos)).fetchall()
        if not ultimos:
            print(
                f"sin ciclos live para {args.platform}: nada que rejugar",
                file=sys.stderr,
            )
            return 1
        fechas = sorted(fila[1].astimezone(dt.UTC).date() for fila in ultimos)
        informe = rejuega(conn, plataforma=args.platform, desde=fechas[0], hasta=fechas[-1])
        print(texto_informe(informe))
        return 0 if informe.cumple() else 1


if __name__ == "__main__":
    sys.exit(main())
