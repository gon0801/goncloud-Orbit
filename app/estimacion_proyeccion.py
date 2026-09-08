"""Proyeccion S5 de estimacion: serializer puro + attach batch de lectura.

GET catalogo/evaluacion no recalculan. Overlay de frescura vive en el reader.
detalle siempre esta en el sobre: None si estado==disponible o si el
resultado congelado no trae contribucion ni contribucion_pct.

Procedencia de componentes: solo campos reales.
- `fecha` persistida -> `fecha_fuente` (p.ej. FX).
- refs del reader (oferta/fee/costo/politica) rellenan fecha_fuente,
  observed_at y vigencia cuando el JOIN las trae.
- `estado` solo si viene en el componente; nunca se inventa desde pertenencia.
- `pertenencia` renombra `pertenece_a_total`.
unidad del escenario = "1" (acta 0.3: una unidad vendible por listing).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import psycopg

from app.estimacion_reader import EscenarioLeido, ProcedenciaRefs, leer_escenarios

# Acta 0.3 / spec S1: un listing modela una unidad vendible; no hay kits.
UNIDAD_ESCENARIO_V1 = "1"

_CLAVES_ESCENARIO = (
    "unidad",
    "canal",
    "fecha_valoracion",
    "version_formula",
    "version_politica",
)


def _str_o_none(valor: Any) -> str | None:
    return str(valor) if valor is not None else None


def _iso_o_none(valor: datetime | date | None) -> str | None:
    if valor is None:
        return None
    return valor.isoformat()


def _sobre_ausente() -> dict:
    return {
        "estado": "incompleta",
        "motivos": ["escenario_ausente"],
        "snapshot_id": None,
        "escenario": {
            "unidad": UNIDAD_ESCENARIO_V1,
            "canal": None,
            "fecha_valoracion": None,
            "version_formula": None,
            "version_politica": None,
        },
        "moneda": None,
        "contribucion": None,
        "contribucion_pct": None,
        "base_porcentaje": "ingreso_normalizado",
        "componentes": [],
        "exclusiones": [],
        "detalle": None,
    }


def _pertenencia(raw: dict[str, Any]) -> bool | None:
    if "pertenencia" in raw:
        return raw.get("pertenencia")
    if "pertenece_a_total" in raw:
        return raw.get("pertenece_a_total")
    return None


def _fecha_fuente_persistida(raw: dict[str, Any]) -> str | None:
    if raw.get("fecha_fuente") is not None:
        return _str_o_none(raw.get("fecha_fuente"))
    if raw.get("fecha") is not None:
        return _str_o_none(raw.get("fecha"))
    return None


def _es_precio(nombre: str) -> bool:
    return nombre in ("precio_bruto", "ingreso_normalizado")


def _es_fee(nombre: str) -> bool:
    return nombre == "fee_total" or nombre.startswith("fee")


def _es_costo(nombre: str) -> bool:
    return nombre.startswith("costo")


def _es_politica_comp(nombre: str) -> bool:
    return nombre in ("isr", "logistica", "retencion_iva_conciliacion")


def _procedencia_componente(
    raw: dict[str, Any], refs: ProcedenciaRefs | None
) -> tuple[str | None, str | None, str | None, str | None]:
    """fecha_fuente, observed_at, vigencia, estado — sin inventar."""
    fecha_fuente = _fecha_fuente_persistida(raw)
    observed_at = _str_o_none(raw.get("observed_at"))
    vigencia = _str_o_none(raw.get("vigencia"))
    estado = _str_o_none(raw.get("estado"))
    nombre = raw.get("nombre") or ""
    if refs is None:
        return fecha_fuente, observed_at, vigencia, estado
    if _es_precio(nombre):
        if fecha_fuente is None:
            fecha_fuente = _iso_o_none(refs.oferta_fetched_at)
        if observed_at is None:
            observed_at = _iso_o_none(refs.oferta_observed_at)
    elif _es_fee(nombre):
        if fecha_fuente is None:
            fecha_fuente = _iso_o_none(refs.fee_fees_estimated_at)
        if observed_at is None:
            observed_at = _iso_o_none(refs.fee_observed_at)
    elif _es_costo(nombre):
        if vigencia is None:
            vigencia = _iso_o_none(refs.costo_valid_from)
    elif _es_politica_comp(nombre):
        if vigencia is None:
            vigencia = _iso_o_none(refs.politica_valid_from)
    return fecha_fuente, observed_at, vigencia, estado


def _componente_s5(raw: dict[str, Any], escenario: EscenarioLeido) -> dict[str, Any]:
    fecha_fuente, observed_at, vigencia, estado = _procedencia_componente(
        raw, escenario.procedencia
    )
    return {
        "nombre": raw["nombre"],
        "importe_original": _str_o_none(raw.get("importe_original")),
        "moneda_original": raw.get("moneda_original"),
        "importe_normalizado": _str_o_none(raw.get("importe_normalizado")),
        "moneda_normalizada": raw.get("moneda_normalizada"),
        "fuente": raw.get("fuente"),
        "fecha_fuente": fecha_fuente,
        "observed_at": observed_at,
        "vigencia": vigencia,
        "estado": estado,
        "pertenencia": _pertenencia(raw),
    }


def _detalle_opcional(escenario: EscenarioLeido) -> dict[str, Any] | None:
    if escenario.estado == "disponible":
        return None
    resultado = (escenario.canonical_input or {}).get("resultado") or {}
    contribucion = resultado.get("contribucion")
    contribucion_pct = resultado.get("contribucion_pct")
    if contribucion is None and contribucion_pct is None:
        return None
    return {
        "contribucion": _str_o_none(contribucion),
        "contribucion_pct": _str_o_none(contribucion_pct),
        "estado": resultado.get("estado"),
    }


def proyeccion_s5(escenario: EscenarioLeido | None) -> dict:
    """EscenarioLeido -> sobre S5. Sin IO. Sin recalculo."""
    if escenario is None:
        return _sobre_ausente()
    disponible = escenario.estado == "disponible"
    fecha = escenario.valoracion_date
    return {
        "estado": escenario.estado,
        "motivos": list(escenario.motivos),
        "snapshot_id": escenario.id,
        "escenario": {
            "unidad": UNIDAD_ESCENARIO_V1,
            "canal": escenario.canal,
            "fecha_valoracion": fecha.isoformat() if fecha is not None else None,
            "version_formula": escenario.formula_version,
            "version_politica": escenario.politica_version_id,
        },
        "moneda": escenario.moneda,
        "contribucion": _str_o_none(escenario.contribucion) if disponible else None,
        "contribucion_pct": (_str_o_none(escenario.contribucion_pct) if disponible else None),
        "base_porcentaje": "ingreso_normalizado",
        "componentes": [_componente_s5(c, escenario) for c in escenario.componentes],
        "exclusiones": list(escenario.exclusiones),
        "detalle": _detalle_opcional(escenario),
    }


def adjuntar_estimaciones(
    conn: psycopg.Connection,
    listing_ids: list[int],
    *,
    as_of: datetime,
) -> dict[int, dict]:
    """Un as_of tz-aware. Un batch leer_escenarios. Stub por listing omitido."""
    if as_of.tzinfo is None:
        raise ValueError("as_of debe incluir zona horaria")
    leidos = {e.listing_id: e for e in leer_escenarios(conn, listing_ids, as_of=as_of)}
    return {lid: proyeccion_s5(leidos.get(lid)) for lid in listing_ids}
