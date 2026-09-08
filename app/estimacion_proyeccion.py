"""Proyeccion S5 de estimacion: serializer puro + attach batch de lectura.

GET catalogo/evaluacion no recalculan. Overlay de frescura vive en el reader.
detalle siempre esta en el sobre: None si estado==disponible o si el
resultado congelado no trae contribucion ni contribucion_pct.

Procedencia de componentes: mapea `fecha` persistida a `fecha_fuente`, toma
`observed_at` del escenario si el componente no lo trae, y deriva vigencia
y estado desde el componente / entrada canónica sin inventar importes.
unidad del escenario = "1" (acta 0.3: una unidad vendible por listing).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg

from app.estimacion_repository import EscenarioLeido, leer_escenarios

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


def _fecha_fuente(raw: dict[str, Any]) -> str | None:
    if raw.get("fecha_fuente") is not None:
        return _str_o_none(raw.get("fecha_fuente"))
    if raw.get("fecha") is not None:
        return _str_o_none(raw.get("fecha"))
    return None


def _observed_at_componente(raw: dict[str, Any], escenario: EscenarioLeido) -> str | None:
    if raw.get("observed_at") is not None:
        return _str_o_none(raw.get("observed_at"))
    if escenario.observed_at is not None:
        return escenario.observed_at.isoformat()
    return None


def _vigencia_componente(raw: dict[str, Any], escenario: EscenarioLeido) -> str | None:
    if raw.get("vigencia") is not None:
        return _str_o_none(raw.get("vigencia"))
    entrada = (escenario.canonical_input or {}).get("entrada") or {}
    nombre = raw.get("nombre") or ""
    if nombre in ("costo_original", "costo_normalizado") or nombre.startswith("costo"):
        return _str_o_none(entrada.get("costo_validated_at"))
    if nombre == "fx":
        return _str_o_none(entrada.get("fx_rate_date"))
    if (
        nombre.startswith("fee")
        or nombre
        in (
            "precio_bruto",
            "ingreso_normalizado",
            "logistica",
            "isr",
            "retencion_iva_conciliacion",
        )
    ) and escenario.valoracion_date is not None:
        return escenario.valoracion_date.isoformat()
    return None


def _estado_componente(raw: dict[str, Any]) -> str | None:
    if raw.get("estado") is not None:
        return _str_o_none(raw.get("estado"))
    pertenencia = _pertenencia(raw)
    if pertenencia is False:
        return "excluido_del_total"
    if pertenencia is True:
        return "incluido"
    return None


def _componente_s5(raw: dict[str, Any], escenario: EscenarioLeido) -> dict[str, Any]:
    return {
        "nombre": raw["nombre"],
        "importe_original": _str_o_none(raw.get("importe_original")),
        "moneda_original": raw.get("moneda_original"),
        "importe_normalizado": _str_o_none(raw.get("importe_normalizado")),
        "moneda_normalizada": raw.get("moneda_normalizada"),
        "fuente": raw.get("fuente"),
        "fecha_fuente": _fecha_fuente(raw),
        "observed_at": _observed_at_componente(raw, escenario),
        "vigencia": _vigencia_componente(raw, escenario),
        "estado": _estado_componente(raw),
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
