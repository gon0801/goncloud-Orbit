"""Lectura de `/cortes`, `/gasto-sin-venta` y `/inertes` (ORBIT 16 / JEV ADS 02).

Extraido de `app/api_dashboard.py` (Ola F1). Elimina el N+1 de `_hermanas_de`
cargando las hermanas de todos los grupos harvest en una sola consulta por lote.
"""

from __future__ import annotations

import datetime as dt
import logging

from psycopg.rows import tuple_row

from app.api import propuestas_campana_visibles
from app.api_common import _dec_str
from app.api_precios import _dinero_2
from app.apply_harvest import ROLES_DISCOVERY
from app.dashboard_pagina import _CAMPANA_ANCESTRO, _JOINS_ANCESTROS
from app.dashboard_salud import motivo_es
from app.db import cursor_dict
from app.etiqueta_entidad import etiqueta_entidad, linea_entidad
from app.etiquetas import etiqueta
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

_SQL_CORTES_PENDIENTES = (
    """
SELECT q.id, q.platform::text, q.familia, q.kind, q.ad_entity_id, e.external_id,
       q.search_term, q.estado, q.modo, q.vence_el, q.encolado_at, q.decision_id,
       e.kind::text AS entidad_kind, e.name, e.keyword_text,
       d.inputs AS decision_inputs,
       """
    + _CAMPANA_ANCESTRO
    + """ AS campana,
       CASE e.kind WHEN 'campaign' THEN e.id WHEN 'ad_group' THEN padre.id
            ELSE abuelo.id END AS campana_id
  FROM apply_queue q
  LEFT JOIN ad_entity e ON e.id = q.ad_entity_id
  JOIN decision d ON d.id = q.decision_id
"""
    + _JOINS_ANCESTROS
    + """
 WHERE q.estado IN ('pending_veto', 'released')
 ORDER BY q.vence_el, q.id
"""
)

_SQL_HERMANAS_GRUPOS = """
SELECT r.grupo_id, r.ad_entity_id, r.rol::text, c.external_id, c.name
  FROM campana_grupo_rol r
  JOIN ad_entity c ON c.id = r.ad_entity_id
 WHERE r.grupo_id = ANY(%s) AND r.rol <> 'category_exact'
"""

_SQL_INERTES = """
SELECT v.platform::text, v.kind::text, v.keyword_text, v.name, v.external_id,
       v.campaign_name, v.ad_group_name, v.clasificacion, v.dias_sin_impresiones,
       v.ultima_impresion, v.gasto_90d, v.moneda::text, v.ordenes_90d,
       (e.first_seen_at AT TIME ZONE 'UTC')::date AS first_seen_fecha,
       e.first_seen_at > (now() AT TIME ZONE 'UTC')::date - 30 AS en_espera,
       (e.first_seen_at AT TIME ZONE 'UTC')::date + 30 AS archivable_desde
  FROM v_entidad_inerte v
  JOIN ad_entity e ON e.id = v.id
 ORDER BY v.platform, v.clasificacion, v.gasto_90d DESC NULLS LAST, v.id
"""

_SQL_LOTES_INERTES = """
SELECT lote, (min(intentado_at) AT TIME ZONE 'UTC')::date AS primer_intento,
       count(*) FILTER (WHERE estado = 'planeado') AS planeado,
       count(*) FILTER (WHERE estado = 'applied') AS applied,
       count(*) FILTER (WHERE estado = 'failed') AS failed,
       count(*) FILTER (WHERE estado = 'repuesto') AS repuesto
  FROM keyword_archivo_manual
 GROUP BY lote
 ORDER BY min(intentado_at) DESC
 LIMIT 5
"""

DIRECCION_POR_KIND = {
    "pause": "recorta",
    "negative": "recorta",
    "harvest": "crece",
}

_ORDEN_LECTURAS = (
    "vendio_aqui",
    "vende_en_otro",
    "relevante_sin_venta",
    "ajena",
    "sin_lectura",
)

_ORDEN_BANDAS = ("ninguno", "pocos", "una_parte", "todos", "sin_dato")


def _clave_texto(kind: str, entidad_kind: str | None) -> str:
    return f"{kind}:{entidad_kind}" if kind == "pause" and entidad_kind else kind


def _indicador_harvest(kind: str, decision_inputs: dict | None) -> dict | None:
    if kind != "harvest" or not isinstance(decision_inputs, dict):
        return None
    termino = decision_inputs.get("termino")
    if not isinstance(termino, dict):
        return None
    if (
        termino.get("orders") is None
        or termino.get("ad_revenue") is None
        or termino.get("moneda") is None
    ):
        return None
    return {
        "ordenes": termino.get("orders"),
        "ingreso": _dec_str(termino.get("ad_revenue")),
        "clics": termino.get("clicks"),
        "moneda": termino.get("moneda"),
    }


def _destino_harvest(kind: str, decision_inputs: dict | None) -> dict | None:
    if kind != "harvest" or not isinstance(decision_inputs, dict):
        return None
    goal = decision_inputs.get("goal")
    if not isinstance(goal, dict):
        return None
    harvest = goal.get("harvest")
    if not isinstance(harvest, dict):
        return None
    return {
        "campaign_id": harvest.get("campaign_id"),
        "ad_group_id": harvest.get("ad_group_id"),
        "resuelto_por": harvest.get("resuelto_por"),
        "grupo_id": harvest.get("grupo_id"),
        "motivo": harvest.get("motivo"),
        "motivo_es": motivo_es(harvest.get("motivo")),
    }


def _ordena_hermanas(por_rol: dict[str, tuple[str, str | None]]) -> list[dict]:
    return [
        {"rol": rol, "campaign_id": por_rol[rol][0], "nombre": por_rol[rol][1] or por_rol[rol][0]}
        for rol in ROLES_DISCOVERY
        if rol in por_rol
    ]


def _hermanas_por_grupo(
    conn, grupos: set[int]
) -> dict[int, list[tuple[int, str, str, str | None]]] | None:
    if not grupos:
        return {}
    try:
        with conn.cursor(row_factory=tuple_row) as cur:
            filas = cur.execute(_SQL_HERMANAS_GRUPOS, (sorted(grupos),)).fetchall()
    except Exception as exc:
        logger.warning("cortes: hermanas de grupos ilegibles: %s", scrub(str(exc)))
        return None
    por_grupo: dict[int, list[tuple[int, str, str, str | None]]] = {}
    for grupo_id, entity_id, rol, external, nombre in filas:
        por_grupo.setdefault(int(grupo_id), []).append((int(entity_id), rol, external, nombre))
    return por_grupo


def _motivo_sin_lectura(motivo: str) -> str:
    return etiqueta(motivo, "motivo_sin_lectura")


def _senal_presentada_vista(vista) -> dict | None:
    from app.jev_vista import frase_de_lectura, titulo_de_lectura

    if vista is None:
        return None
    fila = vista if isinstance(vista, dict) else vista.como_dict()
    fila = dict(fila)
    fila["titulo"] = titulo_de_lectura(fila["lectura"])
    fila["frase"] = frase_de_lectura(
        fila["lectura"],
        fila.get("motivos") or [],
        fila.get("miembros", 0),
        fila.get("listado_de"),
        motivo_texto=_motivo_sin_lectura,
    )
    return fila


def _senal_presentada(propuesta) -> dict | None:
    origen = _senal_presentada_vista(propuesta.origen)
    destino = _senal_presentada_vista(propuesta.destino)
    if origen is None and destino is None and not propuesta.destino_ilegible:
        return None
    return {
        "origen": origen,
        "destino": destino,
        "destino_ilegible": propuesta.destino_ilegible,
    }


def _bloqueos_jev(conn) -> tuple[dict, bool]:
    from app.bloqueo_dueno import lee_pantalla
    from app.jev_vista import frase_de_lectura

    pantalla = lee_pantalla(conn)
    for plataforma in pantalla["plataformas"]:
        for propuesta in plataforma["propuestas"]:
            propuesta["frase"] = frase_de_lectura(
                "ajena",
                propuesta.pop("motivos") or (),
                int(propuesta.pop("miembros") or 0),
                propuesta.pop("listado_de"),
                motivo_texto=_motivo_sin_lectura,
            )
    return pantalla, True


def _banda_honesta(fila: dict) -> str:
    from app.jev_vista import banda_de_proporcion

    if not fila.get("roster_probado"):
        return "sin_dato"
    return banda_de_proporcion(fila.get("satisfacen", 0), fila.get("evaluados", 0))


def _proporcion_de(fila: dict) -> float:
    evaluados = fila.get("evaluados", 0)
    return (fila.get("satisfacen", 0) / evaluados) if evaluados else 0.0


def _calculo_viejo(calculado_el: str | None) -> bool:
    if not calculado_el:
        return True
    try:
        calculado = dt.datetime.fromisoformat(calculado_el)
    except ValueError:
        return True
    ahora = dt.datetime.now(dt.UTC)
    if calculado.tzinfo is None:
        calculado = calculado.replace(tzinfo=dt.UTC)
    return (ahora - calculado) > dt.timedelta(hours=36)


def leer_cortes(conn) -> dict:
    """Cortes pendientes de veto + propuestas de campana + bloqueos JEV."""
    with cursor_dict(conn) as cur:
        filas = cur.execute(_SQL_CORTES_PENDIENTES).fetchall()
    senal_disponible = True
    try:
        from app import jev_salud as pantallas_jev

        senales = pantallas_jev.de_propuestas(conn, [fila["decision_id"] for fila in filas])
        senal_por_decision = {
            decision_id: _senal_presentada(vista) for decision_id, vista in senales.items()
        }
    except Exception as exc:
        logger.warning("cortes: senal Jev ilegible: %s", scrub(str(exc)))
        senal_por_decision = {}
        senal_disponible = False

    destinos = [_destino_harvest(fila["kind"], fila["decision_inputs"]) for fila in filas]
    grupos_necesarios = {
        destino["grupo_id"]
        for fila, destino in zip(filas, destinos, strict=True)
        if (
            destino is not None
            and destino["resuelto_por"] == "grupo"
            and isinstance(destino["grupo_id"], int)
            and fila["campana_id"] is not None
        )
    }
    mapa_hermanas = _hermanas_por_grupo(conn, grupos_necesarios)

    items = []
    for fila, destino in zip(filas, destinos, strict=True):
        hermanas = None
        if (
            destino is not None
            and destino["resuelto_por"] == "grupo"
            and isinstance(destino["grupo_id"], int)
            and fila["campana_id"] is not None
            and mapa_hermanas is not None
        ):
            por_rol = {
                rol: (external, nombre)
                for entity_id, rol, external, nombre in mapa_hermanas.get(destino["grupo_id"], ())
                if entity_id != fila["campana_id"]
            }
            hermanas = _ordena_hermanas(por_rol)
        clave_texto = _clave_texto(fila["kind"], fila["entidad_kind"])
        items.append(
            {
                "id": fila["id"],
                "plataforma": fila["platform"],
                "familia": fila["familia"],
                "kind": fila["kind"],
                "ad_entity_id": fila["ad_entity_id"],
                "external_id": fila["external_id"],
                "nombre": linea_entidad(
                    kind=fila["entidad_kind"],
                    name=fila["name"],
                    keyword_text=fila["keyword_text"],
                    campana=fila["campana"],
                ),
                "search_term": fila["search_term"],
                "estado": fila["estado"],
                "modo": fila["modo"],
                "vence_el": fila["vence_el"].isoformat(),
                "encolado_at": fila["encolado_at"].isoformat(),
                "decision_id": fila["decision_id"],
                "etiqueta": etiqueta(clave_texto, "propuesta"),
                "direccion": DIRECCION_POR_KIND.get(fila["kind"]),
                "efecto_rechazo": etiqueta(clave_texto, "efecto_rechazo"),
                "indicador": _indicador_harvest(fila["kind"], fila["decision_inputs"]),
                "destino": destino,
                "hermanas": hermanas,
                "senal": senal_por_decision.get(fila["decision_id"]),
            }
        )
    try:
        propuestas = propuestas_campana_visibles(conn)
    except Exception as exc:
        logger.warning("cortes: propuestas de campana ilegibles: %s", scrub(str(exc)))
        propuestas = []
    try:
        bloqueos_jev, bloqueos_disponibles = _bloqueos_jev(conn)
    except Exception as exc:
        logger.warning("cortes: bloqueos Jev ilegibles: %s", scrub(str(exc)))
        bloqueos_jev = {"interruptor": False, "plataformas": [], "bloqueos": []}
        bloqueos_disponibles = False
    return {
        "items": items,
        "propuestas_campana": propuestas,
        "senal_disponible": senal_disponible,
        "bloqueos_jev": bloqueos_jev,
        "bloqueos_disponibles": bloqueos_disponibles,
    }


def leer_gasto_sin_venta(conn, mercado: str) -> dict:
    """Busquedas que gastan y no venden, agrupadas por lectura y bandas."""
    from app import jev_salud as pantallas_jev
    from app.jev_vista import titulo_de_banda, titulo_de_lectura

    pantalla = pantallas_jev.gasto_sin_venta(conn, plataforma=mercado)
    base = pantalla.como_dict()
    filas = []
    for cruda in base["filas"]:
        fila = _senal_presentada_vista(cruda)
        assert fila is not None
        economia = dict(fila.get("economia") or {})
        if economia.get("aqui") and economia["aqui"].get("gasto") is not None:
            economia["aqui"] = {
                **economia["aqui"],
                "gasto": _dinero_2(economia["aqui"]["gasto"]),
            }
        fila["economia"] = economia
        fila["banda"] = _banda_honesta(fila)
        filas.append(fila)
    secciones = []
    for lectura in _ORDEN_LECTURAS:
        propias = [fila for fila in filas if fila["lectura"] == lectura]
        if not propias:
            continue
        total = base["totales"].get(lectura, {"busquedas": 0, "gasto": "0"})
        monedas = {fila["economia"]["moneda"] for fila in propias}
        seccion: dict = {
            "lectura": lectura,
            "titulo": titulo_de_lectura(lectura),
            "busquedas": total["busquedas"],
            "gasto": _dinero_2(total["gasto"]),
            "moneda": next(iter(monedas)) if len(monedas) == 1 else None,
            "filas": propias,
        }
        if lectura == "relevante_sin_venta":
            propias.sort(key=_proporcion_de)
            seccion["bandas"] = [
                {
                    "banda": banda,
                    "titulo": titulo_de_banda(banda),
                    "filas": [fila for fila in propias if fila["banda"] == banda],
                }
                for banda in _ORDEN_BANDAS
                if any(fila["banda"] == banda for fila in propias)
            ]
        secciones.append(seccion)
    calculado = base["calculado_el"]
    return {
        "plataforma": mercado,
        "calculado_el": calculado,
        "aviso_desactualizado": _calculo_viejo(calculado),
        "secciones": secciones,
        "total_filas": len(filas),
    }


def leer_inertes(conn) -> dict:
    """Hojas sin trafico con su clasificacion y ultimos lotes de archivado."""
    with cursor_dict(conn) as cur:
        filas_inertes = cur.execute(_SQL_INERTES).fetchall()
        filas_lotes = cur.execute(_SQL_LOTES_INERTES).fetchall()
    items = [
        {
            "plataforma": fila["platform"],
            "kind": fila["kind"],
            "texto": etiqueta_entidad(
                kind=fila["kind"],
                name=fila["name"],
                keyword_text=fila["keyword_text"],
                campana=fila["campaign_name"],
            ).hoja,
            "external_id": fila["external_id"],
            "campana": fila["campaign_name"],
            "ad_group": fila["ad_group_name"],
            "clasificacion": fila["clasificacion"],
            "dias_sin_impresiones": fila["dias_sin_impresiones"],
            "ultima_impresion": (
                fila["ultima_impresion"].isoformat() if fila["ultima_impresion"] else None
            ),
            "gasto_90d": _dec_str(fila["gasto_90d"]),
            "moneda": fila["moneda"],
            "ordenes_90d": int(fila["ordenes_90d"]),
            "first_seen_at": (
                fila["first_seen_fecha"].isoformat() if fila["first_seen_fecha"] else None
            ),
            "en_espera": bool(fila["en_espera"]) if fila["en_espera"] is not None else None,
            "archivable_desde": (
                fila["archivable_desde"].isoformat() if fila["archivable_desde"] else None
            ),
        }
        for fila in filas_inertes
    ]
    totales: dict[str, dict[str, int]] = {}
    for item in items:
        por_clase = totales.setdefault(item["plataforma"], {})
        por_clase[item["clasificacion"]] = por_clase.get(item["clasificacion"], 0) + 1
    lotes = [
        {
            "lote": fila["lote"],
            "fecha": fila["primer_intento"].isoformat(),
            "planeado": fila["planeado"],
            "applied": fila["applied"],
            "failed": fila["failed"],
            "repuesto": fila["repuesto"],
        }
        for fila in filas_lotes
    ]
    return {"totales": totales, "items": items, "lotes": lotes}
