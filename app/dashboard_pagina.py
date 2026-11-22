"""Pagina HTML de Decisiones: ventana y SQL compartido del listado.

PageWindow es pura (page/offset/prev/next). Las constantes `_SQL_DECISIONES_*`
son statements completos (pglast). El JOIN comun vive en `_DECISIONES_FROM`
(fragmento, no statement: el prefijo `_SQL_` queda para SQL parseable).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.api_common import _dec_str
from app.etiqueta_entidad import etiqueta_entidad, linea_entidad
from app.etiquetas import TIPO_TARGET, motivo_decision_es

# Origen unico del JOIN: el feed JSON y la pagina HTML leen las mismas
# columnas (las que _fila_decision toma por indice).
_CAMPANA_ANCESTRO = (
    "CASE e.kind WHEN 'campaign' THEN e.name WHEN 'ad_group' THEN padre.name ELSE abuelo.name END"
)
_JOINS_ANCESTROS = """
  LEFT JOIN ad_entity padre ON padre.id = e.parent_id
  LEFT JOIN ad_entity abuelo ON abuelo.id = padre.parent_id
"""
_DECISIONES_FROM = (
    """
  FROM decision d
  JOIN ad_entity e ON e.id = d.ad_entity_id
"""
    + _JOINS_ANCESTROS
)

# FABRICA 02 (A.6): el job de harvest de la decision (el ultimo por id; las
# decisiones que no son harvest no tienen job y traen NULLs). Fragmento
# propio, concatenado SOLO en el SELECT: _DECISIONES_FROM no se toca y el
# TOTAL sigue contando decisiones, no jobs.
_JOIN_HARVEST_JOB = """
  LEFT JOIN LATERAL (
    SELECT j.id, j.fase, j.external_ids
      FROM harvest_job j
     WHERE j.decision_id = d.id
     ORDER BY j.id DESC
     LIMIT 1
  ) hj ON true
"""

_SQL_DECISIONES_SELECT = (
    """
SELECT d.id, d.cycle_id, d.ad_entity_id, e.name, e.platform, d.kind,
       d.decided_at, d.search_term, d.old_value, d.new_value, d.value_currency,
       d.inputs,
       e.kind::text, e.keyword_text,
       """
    + _CAMPANA_ANCESTRO
    + ", hj.id, hj.fase, hj.external_ids"
    + _DECISIONES_FROM
    + _JOIN_HARVEST_JOB
)

# Feed JSON: CURSOR, jamas OFFSET (decision 8). Candado pglast existente.
_SQL_DECISIONES_FEED = _SQL_DECISIONES_SELECT + " WHERE {filtros}\n ORDER BY d.id DESC\n LIMIT %s\n"

# Pagina HTML: OFFSET sobre una PageWindow ya clampada. Candado propio.
_SQL_DECISIONES_PAGINA = (
    _SQL_DECISIONES_SELECT + " WHERE {filtros}\n ORDER BY d.id DESC\n LIMIT %s OFFSET %s\n"
)

_SQL_DECISIONES_TOTAL = "SELECT count(*)" + _DECISIONES_FROM

# Totales de la pagina filtrada (tipo + busqueda). El total sin filtros sigue
# siendo _SQL_DECISIONES_TOTAL: el conteo global no depende de la vista.
_SQL_DECISIONES_TOTAL_FILTRADO = "SELECT count(*)" + _DECISIONES_FROM + " WHERE {filtros}"

# Conteo por tipo de TODAS las decisiones que cumplen la busqueda: las
# pestanas muestran ese numero, no el de la pagina (decision del dueno 1).
_SQL_DECISIONES_CONTEOS = (
    """
SELECT count(*) AS todas,
       count(*) FILTER (WHERE d.kind = 'pause'::decision_kind) AS pausa,
       count(*) FILTER (WHERE d.kind = 'negative'::decision_kind) AS negativo,
       count(*) FILTER (WHERE d.kind = 'harvest'::decision_kind) AS captura,
       count(*) FILTER (
           WHERE d.kind = 'bid'::decision_kind
             AND d.new_value IS NOT NULL AND d.old_value IS NOT NULL
             AND d.new_value > d.old_value
       ) AS puja_sube,
       count(*) FILTER (
           WHERE d.kind = 'bid'::decision_kind
             AND d.new_value IS NOT NULL AND d.old_value IS NOT NULL
             AND d.new_value < d.old_value
       ) AS puja_baja
"""
    + _DECISIONES_FROM
    + " WHERE {filtros}\n"
)

# Tipos del filtro de Decisiones. Cualquier otro valor se trata como `todas`.
TIPOS_DECISION = ("todas", "pausa", "puja_sube", "puja_baja", "negativo", "captura")


@dataclass(frozen=True, slots=True)
class PageWindow:
    """Ventana de la pagina HTML de decisiones. page/page_size/total se
    almacenan; pages/offset/prev/next se derivan. Construir con desde_total."""

    page: int
    page_size: int
    total: int

    def __post_init__(self) -> None:
        if self.page_size < 1:
            raise ValueError("page_size")
        if self.total < 0:
            raise ValueError("total")
        if self.page < 1 or self.page > self.pages:
            raise ValueError("page")

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def prev(self) -> int | None:
        return self.page - 1 if self.page > 1 else None

    @property
    def next(self) -> int | None:
        return self.page + 1 if self.page < self.pages else None

    @classmethod
    def desde_total(cls, total: int, page: int, page_size: int) -> PageWindow:
        if page_size < 1:
            raise ValueError("page_size")
        if total < 0:
            raise ValueError("total")
        pages = max(1, (total + page_size - 1) // page_size)
        return cls(page=min(max(1, page), pages), page_size=page_size, total=total)


# FABRICA 02 (A.6): fase del harvest_job -> etiqueta.
FASES_ES_HARVEST: dict[str, str] = {
    "pending": "Pendiente de aplicar",
    "negative_created": "Negativo en la campana origen creado",
    "exact_created": "Keyword exacta creada en el destino",
    "hermanas_negadas": "Negando el termino en las campanas hermanas",
    "done": "Aplicado",
    "failed": "Fallido",
}

_CAMPOS_BUSQUEDA_DECISIONES = (
    "coalesce(" + _CAMPANA_ANCESTRO + ", '')",
    "coalesce(e.name, '')",
    "coalesce(e.keyword_text, '')",
    "coalesce(d.search_term, '')",
)
_NOMBRE_SIN_SIGNOS = "regexp_replace(lower(e.name), '[^a-z0-9]+', ' ', 'g')"


def _filtros_feed(platform, kind) -> tuple[list[str], list]:
    """Fragmentos SQL FIJOS + parametros de los filtros del feed."""
    clausulas: list[str] = []
    params: list = []
    if platform is not None:
        clausulas.append("e.platform = %s::platform")
        params.append(platform)
    if kind is not None:
        clausulas.append("d.kind = %s::decision_kind")
        params.append(kind)
    return (clausulas, params)


def _normaliza_busqueda(texto: str) -> str:
    """Texto comparable contra la etiqueta visible: minusculas y sin acentos."""
    sin_acentos = "".join(
        caracter
        for caracter in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caracter)
    )
    return " ".join(re.sub(r"[^0-9a-z]+", " ", sin_acentos.lower()).split())


def _pares_busqueda_decisiones(texto: str) -> list[tuple[str, tuple[str, ...]]]:
    pares: list[tuple[str, tuple[str, ...]]] = [
        (f"strpos(lower({campo}), lower(%s)) > 0", (texto,))
        for campo in _CAMPOS_BUSQUEDA_DECISIONES
    ]
    normalizado = _normaliza_busqueda(texto)
    if not normalizado:
        return pares
    for codigo, etiqueta_target in TIPO_TARGET.items():
        etiqueta_norm = _normaliza_busqueda(etiqueta_target)
        if not etiqueta_norm:
            continue
        if etiqueta_norm in normalizado:
            resto = normalizado.replace(etiqueta_norm, "", 1).strip()
            if resto:
                pares.append(
                    (
                        "(strpos(lower(e.name), lower(%s)) > 0"
                        " AND strpos(lower(e.name), lower(%s)) > 0)",
                        (codigo, resto),
                    )
                )
            else:
                pares.append(("strpos(lower(e.name), lower(%s)) > 0", (codigo,)))
        elif normalizado in etiqueta_norm:
            pares.append(("strpos(lower(e.name), lower(%s)) > 0", (codigo,)))
    pares.append((f"strpos(lower({_NOMBRE_SIN_SIGNOS}), lower(%s)) > 0", (normalizado,)))
    return pares


def _filtros_decisiones(tipo: str | None, q: str | None) -> tuple[str, list]:
    """Clausulas del filtro de Decisiones (vocabulario cerrado + strpos)."""
    clausulas: list[str] = []
    params: list = []
    texto = (q or "").strip()
    if texto:
        pares = _pares_busqueda_decisiones(texto)
        busqueda = " OR ".join(expresion for expresion, _valores in pares)
        clausulas.append(f"({busqueda})")
        params.extend(valor for _expresion, valores in pares for valor in valores)
    tipo = tipo if tipo in TIPOS_DECISION else "todas"
    if tipo == "pausa":
        clausulas.append("d.kind = 'pause'::decision_kind")
    elif tipo == "negativo":
        clausulas.append("d.kind = 'negative'::decision_kind")
    elif tipo == "captura":
        clausulas.append("d.kind = 'harvest'::decision_kind")
    elif tipo == "puja_sube":
        clausulas.append(
            "d.kind = 'bid'::decision_kind"
            " AND d.new_value IS NOT NULL AND d.old_value IS NOT NULL"
            " AND d.new_value > d.old_value"
        )
    elif tipo == "puja_baja":
        clausulas.append(
            "d.kind = 'bid'::decision_kind"
            " AND d.new_value IS NOT NULL AND d.old_value IS NOT NULL"
            " AND d.new_value < d.old_value"
        )
    return (" AND ".join(clausulas) or "true"), params


def _fila_decision(fila) -> dict:
    inputs = fila[11] if isinstance(fila[11], dict) else {}
    motivo = inputs.get("motivo")
    frozen_v2 = inputs.get("evidencia_v2")
    evidencia_v2 = None
    if isinstance(frozen_v2, dict):
        veredicto = frozen_v2.get("veredicto") or {}
        motivo_v2 = veredicto.get("motivo")
        abstencion = frozen_v2.get("abstencion_v2")
        fallback = frozen_v2.get("via") == "decide" and abstencion is not None
        motivo_mostrado = abstencion if fallback else motivo_v2
        evidencia_v2 = {
            "kind": veredicto.get("kind"),
            "motivo": motivo_mostrado,
            "motivo_es": motivo_decision_es(motivo_mostrado, fila[12]),
            "factor": veredicto.get("factor"),
            "new_value": veredicto.get("new_value"),
            "mantiene": (
                veredicto.get("kind") == fila[5] and veredicto.get("factor") == inputs.get("factor")
            ),
            "fallback_v1": fallback,
        }
    harvest_job = None
    if fila[15] is not None:
        externos = fila[17] if isinstance(fila[17], dict) else {}
        pendientes = externos.get("hermanas_pendientes")
        harvest_job = {
            "id": fila[15],
            "fase": fila[16],
            "fase_es": FASES_ES_HARVEST.get(fila[16], fila[16]),
            "hermanas_pendientes": pendientes if isinstance(pendientes, dict) else {},
        }
    return {
        "id": fila[0],
        "cycle_id": fila[1],
        "ad_entity_id": fila[2],
        "nombre": linea_entidad(
            kind=fila[12],
            name=fila[3],
            keyword_text=fila[13],
            campana=fila[14],
        ),
        "plataforma": fila[4],
        "kind": fila[5],
        "decided_at": fila[6],
        "search_term": fila[7],
        "old_value": _dec_str(fila[8]),
        "new_value": _dec_str(fila[9]),
        "value_currency": fila[10],
        "target_acos_pct_usado": inputs.get("target_acos_pct_usado"),
        "motivo_es": motivo_decision_es(motivo, fila[12]),
        "harvest_job": harvest_job,
        "evidencia_v2": evidencia_v2,
    }


def _fila_decision_pagina(fila) -> dict:
    item = _fila_decision(fila)
    etiqueta_ent = etiqueta_entidad(
        kind=fila[12],
        name=fila[3],
        keyword_text=fila[13],
        campana=fila[14],
    )
    item["entidad"] = etiqueta_ent.hoja
    item["campana"] = etiqueta_ent.campana
    return item


def decisiones_pagina(
    conn,
    page: int,
    page_size: int,
    tipo: str | None = "todas",
    q: str | None = None,
) -> tuple[list[dict], PageWindow]:
    """Pagina HTML: count filtrado -> ventana clampada -> LIMIT/OFFSET."""
    filtros, params = _filtros_decisiones(tipo, q)
    consulta = conn.execute(_SQL_DECISIONES_TOTAL_FILTRADO.format(filtros=filtros), params)
    total = consulta.fetchone()[0]
    ventana = PageWindow.desde_total(total=total, page=page, page_size=page_size)
    filas = conn.execute(
        _SQL_DECISIONES_PAGINA.format(filtros=filtros),
        [*params, ventana.page_size, ventana.offset],
    ).fetchall()
    return [_fila_decision_pagina(fila) for fila in filas], ventana


def decisiones_conteos(conn, q: str | None = None) -> dict[str, int]:
    """Cuenta TODAS las decisiones que cumplen la busqueda, por tipo."""
    filtros, params = _filtros_decisiones("todas", q)
    fila = conn.execute(_SQL_DECISIONES_CONTEOS.format(filtros=filtros), params).fetchone()
    return {
        "todas": fila[0],
        "pausa": fila[1],
        "negativo": fila[2],
        "captura": fila[3],
        "puja_sube": fila[4],
        "puja_baja": fila[5],
    }
