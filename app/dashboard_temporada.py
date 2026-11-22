"""Lecturas de la tarjeta «Temporadas de venta» (TEMPORADA 01 A/B).

Extraido de `app/api_dashboard.py` (Ola F1) para romper la inversion de
dependencia desde `app/config_write.py` y `app/api_temporada.py`: ahora ambos
importan este modulo hoja en vez del router HTTP.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal

from app.api_common import target_margen_del_ciclo as _target_margen_del_ciclo
from app.etiquetas import etiqueta
from app.optimizer import goals as g
from app.optimizer import temporada
from app.optimizer.bid import PLATAFORMAS_MONEDA

_SQL_CONFIG_VIGENTE = """
SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1
"""

_SQL_GOALS_SETTINGS = """
SELECT g.id, g.scope, g.ad_entity_id, g.platform, g.target_acos_pct, g.bid_floor,
       g.bid_ceiling, g.bid_currency, g.harvest_campaign_id, g.harvest_ad_group_id,
       g.harvest_default_bid, g.enabled, g.mode, e.platform, e.name
  FROM ads_optimizer_goal g
  LEFT JOIN ad_entity e ON e.id = g.ad_entity_id
 ORDER BY g.id
"""

_SQL_EQUILIBRIO_TEMPORADA = """
SELECT platform, ventana_desde, ventana_hasta, venta_total, venta_cubierta,
       cargos_con_orden, cargos_sin_orden, cogs, cobertura, dias_con_venta,
       fees_sin_tipo, margen_neto_pct, ledger_fresco_at, moneda,
       cogs_vigente, cobertura_vigente, margen_vigente_pct
  FROM v_target_margen_plataforma
 WHERE platform = %s::platform
"""

COLUMNAS_EQUILIBRIO_TEMPORADA = (
    "platform",
    "ventana_desde",
    "ventana_hasta",
    "venta_total",
    "venta_cubierta",
    "cargos_con_orden",
    "cargos_sin_orden",
    "cogs",
    "cobertura",
    "dias_con_venta",
    "fees_sin_tipo",
    "margen_neto_pct",
    "ledger_fresco_at",
    "moneda",
    "cogs_vigente",
    "cobertura_vigente",
    "margen_vigente_pct",
)

RAZON_SIN_EQUILIBRIO = {
    "cobertura_baja": "faltan costos de algunos productos",
    "ledger_rancio": "los datos de ventas tienen más de 3 días",
    "ventana_corta": "hay pocas ventas en los últimos 90 días",
    "sin_margen": "sin margen medido en la ventana",
}

_SQL_ESTADO_BAJA_DECISIONES = """
SELECT ultima.ad_entity_id,
       EXISTS (SELECT 1 FROM decision_application da
                WHERE da.decision_id = ultima.decision_id
                  AND da.verify_ok IS TRUE) AS confirmada
  FROM (
    SELECT DISTINCT ON (d.ad_entity_id)
           d.ad_entity_id, d.id AS decision_id
      FROM decision d
      JOIN ad_entity e ON e.id = d.ad_entity_id
      JOIN optimizer_cycle c ON c.id = d.cycle_id
     WHERE e.platform = %s::platform
       AND c.mode = 'live'
       AND d.kind = 'bid'
       AND d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja')
       AND (d.decided_at AT TIME ZONE 'UTC')::date >= %s
     ORDER BY d.ad_entity_id, d.decided_at DESC, d.id DESC
  ) ultima
"""

_SQL_ESTADO_BAJA_KEYWORDS = """
SELECT count(*) AS total,
       count(*) FILTER (WHERE s.current_bid IS NULL AND NOT EXISTS (
           SELECT 1 FROM decision d
             JOIN optimizer_cycle c ON c.id = d.cycle_id
            WHERE d.ad_entity_id = e.id AND c.mode = 'live' AND d.kind = 'bid'
              AND d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja')
              AND (d.decided_at AT TIME ZONE 'UTC')::date >= %s)) AS grupo,
       count(*) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM decision d
             JOIN optimizer_cycle c ON c.id = d.cycle_id
            WHERE d.ad_entity_id = e.id AND c.mode = 'live' AND d.kind = 'bid'
              AND d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja')
              AND (d.decided_at AT TIME ZONE 'UTC')::date >= %s)) AS sin_tocar
  FROM ad_entity e
  JOIN ad_entity_state s ON s.ad_entity_id = e.id AND s.status = 'ENABLED'
 WHERE e.platform = %s::platform AND e.kind = 'keyword'
"""

_SQL_PAUSADAS_DE_BAJA = """
SELECT DISTINCT ON (q.ad_entity_id) q.ad_entity_id,
       COALESCE(e.keyword_text, e.name) AS texto
  FROM apply_queue q
  JOIN decision d ON d.id = q.decision_id
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
  JOIN ad_entity e ON e.id = q.ad_entity_id
  JOIN ad_entity_state s ON s.ad_entity_id = q.ad_entity_id AND s.status = 'PAUSED'
 WHERE q.platform = %s::platform
   AND q.kind = 'pause' AND q.modo = 'live' AND q.estado = 'applied'
   AND c.mode = 'live'
   AND d.inputs->'caso'->'temporada'->'frontera'->>'fase_hoja' = 'baja'
   AND (d.decided_at AT TIME ZONE 'UTC')::date >= %s
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                    WHERE a.decision_id = d.id AND a.tipo = 'reversa'
                    GROUP BY a.decision_id
                    HAVING bool_or(a.resultado = 'ok') OR count(*) >= 3)
   AND NOT EXISTS (SELECT 1 FROM reactivacion_manual r
                    WHERE r.ad_entity_id = q.ad_entity_id
                      AND r.detectada_en > q.applied_at)
   AND NOT (q.ad_entity_id = ANY(%s::bigint[]))
   AND q.id = (SELECT q2.id FROM apply_queue q2
                WHERE q2.ad_entity_id = q.ad_entity_id
                  AND q2.kind = 'pause' AND q2.estado = 'applied'
                ORDER BY q2.applied_at DESC LIMIT 1)
 ORDER BY q.ad_entity_id
"""


def _config_vigente_con_id(conn) -> tuple[int | None, dict]:
    """(id, settings) de la config_version VIGENTE; sin config -> (None, {})."""
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    return (fila[0], fila[1]) if fila is not None else (None, {})


def _goal_desde_fila(fila) -> g.Goal:
    """Reconstruye un Goal desde una fila de `ads_optimizer_goal`."""
    (
        scope,
        ad_entity_id,
        platform,
        target_acos_pct,
        bid_floor,
        bid_ceiling,
        bid_currency,
        harvest_campaign_id,
        harvest_ad_group_id,
        harvest_default_bid,
        enabled,
        mode,
    ) = fila
    return g.Goal(
        scope=scope,
        ad_entity_id=ad_entity_id,
        platform=platform,
        target_acos_pct=target_acos_pct,
        bid_floor=bid_floor,
        bid_ceiling=bid_ceiling,
        bid_currency=bid_currency,
        harvest_campaign_id=harvest_campaign_id,
        harvest_ad_group_id=harvest_ad_group_id,
        harvest_default_bid=harvest_default_bid,
        enabled=enabled,
        mode=mode,
    )


def _pct_un_decimal(valor: Decimal) -> str:
    return f"{valor.quantize(Decimal('0.1')):.1f}"


def _numero_corto(valor: Decimal) -> str:
    return format(valor.normalize(), "f")


def _equilibrio_de_fila(fila: Mapping, hoy: dt.date) -> dict:
    medicion = g.medicion_desde_fila(fila)
    motivo = g.motivo_dato_invalido(medicion, hoy)
    if motivo is None:
        return {"equilibrio": medicion.margen_neto_pct, "razon": None}
    if motivo == "sin_margen" and (fila.get("fees_sin_tipo") or 0) > 0:
        razon = "hay cargos de Amazon sin clasificar"
    else:
        razon = RAZON_SIN_EQUILIBRIO.get(motivo, RAZON_SIN_EQUILIBRIO["sin_margen"])
    return {"equilibrio": None, "razon": razon}


def equilibrio_temporada(
    conn,
    plataforma: str,
    *,
    meta_hoy: Decimal | None,
    peldano: str | None,
    meta_alta: Decimal | None,
    hoy: dt.date,
) -> dict:
    """Equilibrio con el costo vigente y avisos para la tarjeta de temporada."""
    sale_de_margen = (
        " (sale de tu margen)"
        if peldano in ("margen_plataforma", "margen_familia", "margen_producto")
        else ""
    )
    frase_meta_hoy = (
        None if meta_hoy is None else f"Hoy tu meta es {_numero_corto(meta_hoy)} %{sale_de_margen}."
    )
    fila = conn.execute(_SQL_EQUILIBRIO_TEMPORADA, (plataforma,)).fetchone()
    resuelto = (
        _equilibrio_de_fila(dict(zip(COLUMNAS_EQUILIBRIO_TEMPORADA, fila, strict=True)), hoy)
        if fila is not None
        else {"equilibrio": None, "razon": RAZON_SIN_EQUILIBRIO["sin_margen"]}
    )
    equilibrio = resuelto["equilibrio"]
    razon = resuelto["razon"]
    linea = (
        None
        if equilibrio is None
        else (
            "Punto de equilibrio con el costo de hoy:"
            f" {_pct_un_decimal(equilibrio)} %. Arriba de ese ACoS pierdes dinero en cada venta."
        )
    )
    nombre = etiqueta(plataforma, "plataforma")
    avisos: list[str] = []
    if equilibrio is not None:
        eq = _pct_un_decimal(equilibrio)
        if meta_alta is not None and meta_alta >= equilibrio:
            meta_txt = _numero_corto(meta_alta)
            avisos.append(
                f"Con una meta de {meta_txt} % en temporada alta, Orbit acepta ventas que"
                f" pierden dinero en {nombre}: las que salen con un ACoS entre tu punto de"
                f" equilibrio, {eq} %, y {meta_txt} %."
            )
        if meta_hoy is not None and meta_hoy >= equilibrio:
            meta_txt = _numero_corto(meta_hoy)
            avisos.append(
                f"Con tu meta de hoy de {meta_txt} %, Orbit ya acepta ventas que pierden"
                f" dinero en {nombre}: las que salen con un ACoS entre tu punto de"
                f" equilibrio, {eq} %, y {meta_txt} %."
            )
    return {
        "equilibrio": None if equilibrio is None else _pct_un_decimal(equilibrio),
        "linea": linea,
        "razon": razon,
        "aviso": " ".join(avisos) or None,
        "frase_meta_hoy": frase_meta_hoy,
        "meta_alta_frase": (
            None if meta_alta is None else f"En temporada alta: {_numero_corto(meta_alta)} %."
        ),
    }


def _goals_plataforma(conn) -> dict[str, tuple[int, g.Goal]]:
    """Lee goals de plataforma UNA sola vez (evita N+1 en `metas_vigentes_temporada`)."""
    salida: dict[str, tuple[int, g.Goal]] = {}
    for fila in conn.execute(_SQL_GOALS_SETTINGS).fetchall():
        if fila[1] == "platform":
            goal = _goal_desde_fila(fila[1:13])
            if goal.platform is not None and goal.platform not in salida:
                salida[goal.platform] = (fila[0], goal)
    return salida


def metas_vigentes_temporada(conn) -> tuple[dict[str, Decimal], dict[str, str | None]]:
    """Meta que gobierna hoy cada plataforma y su peldano (1 sola lectura de goals)."""
    metas: dict[str, Decimal] = {}
    peldanos: dict[str, str | None] = {}
    settings = _config_vigente_con_id(conn)[1]
    por_plat = _goals_plataforma(conn)
    for plataforma in PLATAFORMAS_MONEDA:
        par = por_plat.get(plataforma)
        goal = par[1] if par is not None else None
        valor, peldano = g.cascada_target_acos_con_procedencia(
            None,
            goal,
            settings,
            None,
            plataforma,
            _target_margen_del_ciclo(conn, plataforma),
        )
        peldanos[plataforma] = peldano
        if valor is not None:
            metas[plataforma] = valor
    return metas, peldanos


def pausadas_de_baja(
    conn, plataforma: str, inicio: dt.date, mantener: frozenset[int] | set[int]
) -> list[dict]:
    """Keywords que Orbit pauso en esta baja y va a reactivar al terminar."""
    filas = conn.execute(_SQL_PAUSADAS_DE_BAJA, (plataforma, inicio, sorted(mantener))).fetchall()
    return [{"ad_entity_id": fila[0], "nombre": fila[1] or f"keyword #{fila[0]}"} for fila in filas]


def estado_de_baja(conn, plataforma: str, config: temporada.ConfigTemporada, hoy: dt.date) -> dict:
    """Conteos y lista de pausadas de la baja vigente."""
    estacion = temporada.resuelve(config, hoy)
    if estacion.fase != "baja":
        return {}
    inicio = estacion.inicio_bloque
    confirmados = pendientes = 0
    for fila in conn.execute(_SQL_ESTADO_BAJA_DECISIONES, (plataforma, inicio)).fetchall():
        if fila[1]:
            confirmados += 1
        else:
            pendientes += 1
    _total, grupo, sin_tocar = conn.execute(
        _SQL_ESTADO_BAJA_KEYWORDS, (inicio, inicio, plataforma)
    ).fetchone()
    frase_sin_tocar = f"Sin tocar: {sin_tocar}."
    if grupo:
        frase_sin_tocar += (
            f" {grupo} usan el bid de su grupo (Orbit no los toca hasta que tengan bid propio)."
        )
    return {
        "fase": "baja",
        "desde": inicio.isoformat(),
        "confirmados": confirmados,
        "pendientes": pendientes,
        "sin_tocar": sin_tocar,
        "frase_sin_tocar": frase_sin_tocar,
        "pausadas": pausadas_de_baja(conn, plataforma, inicio, config.mantener_pausadas),
    }


def resumen_temporada_para_guardado(
    conn, settings_antes: Mapping, settings_nuevo: Mapping, hoy: dt.date
) -> temporada.ResumenTemporada | None:
    """«Así queda tu año» para la respuesta del guardado."""
    config_antes = temporada.desde_settings(settings_antes)
    config_nueva = temporada.desde_settings(settings_nuevo)
    if config_nueva is None:
        return None
    meta_alta = {
        p: temporada.meta_alta_desde_settings(settings_nuevo, p)
        for p in temporada.PLATAFORMAS_TEMPORADA
    }
    return temporada.resumen(
        config_nueva,
        hoy,
        antes=config_antes,
        metas_hoy=metas_vigentes_temporada(conn)[0],
        meta_alta=meta_alta,
    )


def temporada_formulario(settings: Mapping) -> dict:
    """Prellenado de la tarjeta: config GUARDADA o defaults de formulario."""
    guardada = temporada.desde_settings(settings)
    salida = {
        "guardada": guardada is not None,
        "meses": list(temporada.MESES_POR_DEFECTO),
        "fin_anticipado": None,
        "mantener_pausadas": [],
        **temporada.FORMULARIO_POR_DEFECTO,
    }
    if guardada is not None:
        blob = temporada.como_guardada(guardada)
        salida["meses"] = list(blob["meses"])
        salida["fin_anticipado"] = blob["fin_anticipado"]
        salida["mantener_pausadas"] = list(blob["mantener_pausadas"])
        for clave in (
            "reduccion_baja_pct",
            "dias_preparacion",
            "mult_cortes_baja",
            "aumento_presupuesto_alta_pct",
        ):
            salida[clave] = blob[clave]
    return salida
