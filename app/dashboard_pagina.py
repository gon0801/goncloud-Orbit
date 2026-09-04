"""Pagina HTML de Decisiones: ventana y SQL compartido del listado.

PageWindow es pura (page/offset/prev/next). Las constantes `_SQL_DECISIONES_*`
son statements completos (pglast). El JOIN comun vive en `_DECISIONES_FROM`
(fragmento, no statement: el prefijo `_SQL_` queda para SQL parseable).
"""

from __future__ import annotations

from dataclasses import dataclass

# Origen unico del JOIN: el feed JSON y la pagina HTML leen las mismas
# columnas (las que _fila_decision toma por indice). padre/abuelo resuelven
# la campana de una hoja (keyword/target cuelga de ad_group).
_DECISIONES_FROM = """
  FROM decision d
  JOIN ad_entity e ON e.id = d.ad_entity_id
  LEFT JOIN ad_entity padre ON padre.id = e.parent_id
  LEFT JOIN ad_entity abuelo ON abuelo.id = padre.parent_id
"""

_SQL_DECISIONES_SELECT = (
    """
SELECT d.id, d.cycle_id, d.ad_entity_id, e.name, e.platform, d.kind,
       d.decided_at, d.search_term, d.old_value, d.new_value, d.value_currency,
       d.inputs,
       e.kind::text, e.keyword_text,
       CASE e.kind
         WHEN 'campaign' THEN e.name
         WHEN 'ad_group' THEN padre.name
         ELSE abuelo.name
       END
"""
    + _DECISIONES_FROM
)

# Feed JSON: CURSOR, jamas OFFSET (decision 8). Candado pglast existente.
_SQL_DECISIONES_FEED = _SQL_DECISIONES_SELECT + " WHERE {filtros}\n ORDER BY d.id DESC\n LIMIT %s\n"

# Pagina HTML: OFFSET sobre una PageWindow ya clampada. Candado propio.
_SQL_DECISIONES_PAGINA = _SQL_DECISIONES_SELECT + " ORDER BY d.id DESC\n LIMIT %s OFFSET %s\n"

_SQL_DECISIONES_TOTAL = "SELECT count(*)" + _DECISIONES_FROM


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
