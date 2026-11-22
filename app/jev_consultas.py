"""Consultas y tipos compartidos de JEV ADS 02 (hoja de lectura).

Extraido de `jev_senales`, `jev_salud` y `jev_vigia` (Ola F2) para romper el
ciclo de 4 modulos (`jev_senales <-> jev_avisos <-> jev_salud <-> jev_vigia`)
y eliminar el N+1 de resolucion de `ad_group` en `_propuestas`.

DAG resultante:
`jev_lectura -> jev_consultas -> {jev_avisos, jev_salud, jev_vigia} -> jev_senales`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.jev_lectura import (
    Ajustes,
    Apagado,
    ClaveBusqueda,
    PropuestaEnVeto,
    SenalVista,
    ajustes_desde_settings,
    vista_de_dict,
)

HORAS_SIN_CORRER = 13

MotivoCierre = Literal["apagado", "ocupado", "completa", "tope", "proveedor_caido", "sin_api_key"]

MOTIVOS_SIN_JUICIO: frozenset[MotivoCierre] = frozenset({"sin_api_key", "proveedor_caido", "tope"})
TEXTO_SIN_JUICIO: dict[MotivoCierre, str] = {
    "sin_api_key": "falta la llave del proveedor de JEV",
    "proveedor_caido": "el proveedor de JEV no respondió",
    "tope": "se acabó el tope diario de consultas de JEV",
}


@dataclass(frozen=True)
class CierreCorrida:
    corrida_id: UUID | None
    motivo: MotivoCierre
    unidades: int
    llamadas: int
    fallos: int
    senales_nuevas: int
    avisos_enviados: int
    avisos_error: str | None = None


_COLUMNAS_SENAL = (
    "senal_id",
    "plataforma",
    "ad_group_id",
    "termino",
    "lectura",
    "motivos_lectura",
    "relevancia",
    "motivos_jev",
    "productos_ok",
    "evaluados",
    "miembros",
    "regla_version",
    "roster_probado",
    "roster_prueba",
    "ventana_inicio",
    "ventana_fin",
    "datos_hasta",
    "moneda",
    "clics",
    "gasto",
    "ordenes",
    "otros_que_venden",
    "otros_sin_dato",
    "historial",
    "valida_hasta",
    "created_at",
    "vigente",
)

_SQL_SENAL_BASE = (
    "SELECT s.id AS senal_id, s.plataforma::text AS plataforma, s.ad_group_id,"
    " s.termino, s.lectura, s.motivos_lectura, s.relevancia, s.motivos_jev,"
    " s.productos_ok, s.evaluados, s.miembros, s.regla_version,"
    " s.roster_probado, s.roster_prueba, s.ventana_inicio, s.ventana_fin,"
    " s.datos_hasta, s.moneda::text AS moneda, s.clics, s.gasto, s.ordenes,"
    " s.otros_que_venden, s.otros_sin_dato, s.historial,"
    " s.valida_hasta, s.created_at, v.vigente"
    " FROM jev_senal_vigente v JOIN jev_senal s ON s.id = v.senal_id"
)


def _ajustes(lector) -> Ajustes | Apagado:
    fila = lector.execute(
        "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if fila is None:
        return Apagado("sin config vigente")
    if isinstance(fila, dict):
        return ajustes_desde_settings(fila["id"], fila["settings"])
    return ajustes_desde_settings(*fila)


def _grupo_interno(lector, plataforma: str, externo: object) -> int | None:
    fila = lector.execute(
        "SELECT id FROM ad_entity WHERE platform = %s AND kind = 'ad_group' AND external_id = %s",
        (plataforma, str(externo)),
    ).fetchone()
    if fila is None:
        return None
    return fila["id"] if isinstance(fila, dict) else fila[0]


def _grupos_internos_lote(lector, pares: set[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """Resuelve por lote `(platform, external_id)` -> `ad_entity.id` para ad_groups."""
    if not pares:
        return {}
    ordenados = sorted(pares)
    plataformas = [p for p, _ in ordenados]
    externos = [e for _, e in ordenados]
    filas = lector.execute(
        "SELECT e.platform::text, e.external_id, e.id"
        " FROM ad_entity e"
        " JOIN unnest(%s::text[], %s::text[]) AS t(platform, external_id)"
        "   ON e.platform::text = t.platform AND e.external_id = t.external_id"
        " WHERE e.kind = 'ad_group'",
        (plataformas, externos),
    ).fetchall()
    salida: dict[tuple[str, str], int] = {}
    for fila in filas:
        if isinstance(fila, dict):
            salida[(fila["platform"], fila["external_id"])] = fila["id"]
        else:
            salida[(fila[0], fila[1])] = fila[2]
    return salida


def _propuestas(lector) -> tuple[tuple[PropuestaEnVeto, ...], tuple[int, ...]]:
    """Propuestas en veto (negative/harvest) resolviendo destinos harvest por lote."""
    filas = lector.execute(
        "SELECT q.id, q.decision_id, q.kind::text, q.modo, q.vence_el, q.ad_entity_id,"
        " q.search_term, q.platform::text, d.inputs"
        " FROM apply_queue q JOIN decision d ON d.id = q.decision_id"
        " WHERE q.kind IN ('negative', 'harvest') AND q.estado IN ('pending_veto', 'released')"
        " AND q.platform IN ('amazon_mx', 'amazon_us') ORDER BY q.vence_el, q.id"
    ).fetchall()
    norm_filas = []
    destinos_externos: set[tuple[str, str]] = set()
    for fila in filas:
        if isinstance(fila, dict):
            item = (
                fila["id"],
                fila["decision_id"],
                fila["kind"],
                fila["modo"],
                fila["vence_el"],
                fila["ad_entity_id"],
                fila["search_term"],
                fila["platform"],
                fila["inputs"],
            )
        else:
            item = tuple(fila)
        norm_filas.append(item)
        if item[2] == "harvest":
            cosecha = ((item[8] or {}).get("goal") or {}).get("harvest") or {}
            externo = cosecha.get("ad_group_id")
            if externo is not None:
                destinos_externos.add((item[7], str(externo)))

    mapa_grupos = _grupos_internos_lote(lector, destinos_externos)
    propuestas: list[PropuestaEnVeto] = []
    ilegibles: list[int] = []
    for cola, decision, kind, modo, vence, grupo, termino, plataforma, inputs in norm_filas:
        origen = ClaveBusqueda(plataforma, grupo, termino)
        destino = None
        ilegible = False
        if kind == "harvest":
            cosecha = ((inputs or {}).get("goal") or {}).get("harvest") or {}
            externo = cosecha.get("ad_group_id")
            interno = mapa_grupos.get((plataforma, str(externo))) if externo is not None else None
            if interno is None:
                ilegible = True
                ilegibles.append(cola)
            else:
                destino = ClaveBusqueda(plataforma, interno, termino)
        propuestas.append(
            PropuestaEnVeto(cola, decision, kind, modo, origen, destino, ilegible, vence)
        )
    return tuple(propuestas), tuple(ilegibles)


def _fila_senal(fila) -> dict:
    """Normaliza fila psycopg (dict_row o tuple) al dict que lee `vista_de_dict`."""
    return dict(fila) if isinstance(fila, dict) else dict(zip(_COLUMNAS_SENAL, fila, strict=True))


def _vistas_de_claves(conn, claves: set[ClaveBusqueda]) -> dict[ClaveBusqueda, SenalVista]:
    """La senal mas reciente de cada clave (vacia si el job no la sello)."""
    if not claves:
        return {}
    marcas = ",".join(["(%s,%s,%s)"] * len(claves))
    parametros = [
        valor for clave in claves for valor in (clave.plataforma, clave.ad_group_id, clave.termino)
    ]
    filas = conn.execute(
        _SQL_SENAL_BASE + f" WHERE (s.plataforma::text, s.ad_group_id, s.termino) IN ({marcas})",
        parametros,
    ).fetchall()
    return {vista.clave: vista for vista in (vista_de_dict(_fila_senal(f)) for f in filas)}
