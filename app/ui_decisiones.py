"""Presentacion de la pagina Decisiones (DISEÑOS 01, bloque A).

Agrupa por dia, junta las pausas y los negativos del mismo dia con el mismo
tipo y motivo, y numera lo que se ve en la pagina. No consulta datos: recibe
las filas del endpoint y solo las ordena para el template.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

from app import formato
from app.etiquetas import etiqueta

TIPOS_TAB = (
    ("todas", "Todas"),
    ("pausa", "Pausas"),
    ("puja_sube", "Pujas ↑"),
    ("puja_baja", "Pujas ↓"),
    ("negativo", "Negativos"),
    ("captura", "Captura"),
)

_ETIQUETA_TIPO = {
    "pausa": "Pausa",
    "puja_sube": "Puja ↑",
    "puja_baja": "Puja ↓",
    "puja_igual": "Puja",
    "negativo": "Negativo",
    "captura": "Captura",
}

_CLASE_CHIP = {
    "pausa": "chip-tipo-pausa",
    "puja_sube": "chip-tipo-puja-sube",
    "puja_baja": "chip-tipo-puja-baja",
    "puja_igual": "chip-tipo-pausa",
    "negativo": "chip-tipo-negativo",
    "captura": "chip-tipo-captura",
}

_NOMBRES = {
    "pausa": ("pausa", "pausas"),
    "puja_sube": ("puja subida", "pujas subidas"),
    "puja_baja": ("puja bajada", "pujas bajadas"),
    "negativo": ("negativo", "negativos"),
    "captura": ("captura", "capturas"),
    "puja_igual": ("puja sin cambio", "pujas sin cambio"),
    "otro": ("decision", "decisiones"),
}

_ORDEN_RESUMEN = ("pausa", "puja_sube", "puja_baja", "negativo", "captura", "puja_igual", "otro")


def _decimal(valor) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return numero if numero.is_finite() else None


def tipo_de_decision(item: dict) -> str:
    """Tipo de presentacion derivado del kind y del bid antes/despues."""
    kind = item.get("kind")
    if kind == "pause":
        return "pausa"
    if kind == "negative":
        return "negativo"
    if kind == "harvest":
        return "captura"
    if kind == "bid":
        nuevo = _decimal(item.get("new_value"))
        anterior = _decimal(item.get("old_value"))
        if nuevo is None or anterior is None:
            return "otro"
        if nuevo > anterior:
            return "puja_sube"
        if nuevo < anterior:
            return "puja_baja"
        return "puja_igual"
    return "otro"


def _nombre(tipo: str, cantidad: int) -> str:
    singular, plural = _NOMBRES.get(tipo, _NOMBRES["otro"])
    return singular if cantidad == 1 else plural


def _fila(item: dict) -> dict:
    kind = item.get("kind")
    tipo = tipo_de_decision(item)
    nuevo = _decimal(item.get("new_value"))
    anterior = _decimal(item.get("old_value"))
    tiene_bid = kind == "bid" and nuevo is not None and anterior is not None
    if not tiene_bid:
        tono = "mutado"
    elif nuevo > anterior:
        tono = "decisiones-bid-sube"
    elif nuevo < anterior:
        tono = "decisiones-bid-baja"
    else:
        tono = "mutado"
    search_term = item.get("search_term")
    if search_term in (None, "", "—"):
        search_term = None
    etiqueta_entidad = item.get("entidad") or item.get("nombre") or "—"
    return {
        "id": item.get("id"),
        "tipo": tipo,
        "chip_label": _ETIQUETA_TIPO.get(tipo) or etiqueta(kind, "kind"),
        "chip_clase": _CLASE_CHIP.get(tipo, "chip-tipo-pausa"),
        "entidad": etiqueta_entidad,
        "campana": item.get("campana"),
        "meta": f"#{item.get('ad_entity_id')} · decision #{item.get('id')}",
        "search_term": search_term,
        "motivo": item.get("motivo_es"),
        "evidencia_v2": item.get("evidencia_v2"),
        "harvest_job": item.get("harvest_job"),
        "target": item.get("target_acos_pct_usado"),
        "tiene_bid": tiene_bid,
        "bid_antes": item.get("old_value"),
        "bid_despues": item.get("new_value"),
        "moneda": item.get("value_currency"),
        "tono_bid": tono,
        "fecha": formato.fecha_dia(item.get("decided_at")),
        "hora": formato.hora(item.get("decided_at")),
    }


def _grupo(filas: list[dict]) -> dict:
    primera = filas[0]
    entidades = {f["entidad"] for f in filas if f["entidad"]}
    campanas = {f["campana"] for f in filas if f["campana"]}
    objetivos = {f["target"] for f in filas if f["target"] is not None}
    if not objetivos:
        objetivo = None
    elif len(objetivos) == 1:
        objetivo = primera["target"]
    else:
        objetivo = "varios"
    cantidad = len(filas)
    return {
        "single": cantidad == 1,
        "multi": cantidad > 1,
        "n": cantidad,
        "tipo": primera["tipo"],
        "chip_label": primera["chip_label"],
        "chip_clase": primera["chip_clase"],
        "motivo": primera["motivo"],
        "count_label": f"{cantidad} {_nombre(primera['tipo'], cantidad)}",
        "scope": f"{len(entidades)} entidades en {len(campanas)} campañas",
        "target": objetivo,
        "hora": primera["hora"],
        "first": primera,
        "filas": filas,
        "ver": f"Ver las {cantidad} decisiones",
    }


def _resumen_dia(filas: list[dict]) -> str:
    cuenta = Counter(f["tipo"] for f in filas)
    partes = [f"{len(filas)} {'decision' if len(filas) == 1 else 'decisiones'}"]
    for tipo in _ORDEN_RESUMEN:
        cantidad = cuenta.get(tipo)
        if cantidad:
            partes.append(f"{cantidad} {_nombre(tipo, cantidad)}")
    return " · ".join(partes)


def _dias(filas: list[dict]) -> list[dict]:
    orden: list[str] = []
    por_fecha: dict[str, list[dict]] = {}
    for fila in filas:
        fecha = fila["fecha"]
        if fecha not in por_fecha:
            por_fecha[fecha] = []
            orden.append(fecha)
        por_fecha[fecha].append(fila)
    dias: list[dict] = []
    for fecha in orden:
        filas_dia = por_fecha[fecha]
        grupos: list[dict] = []
        indices: dict[tuple[str, str | None], dict] = {}
        for fila in filas_dia:
            if fila["tipo"] in ("pausa", "negativo"):
                clave = (fila["tipo"], fila["motivo"])
                grupo = indices.get(clave)
                if grupo is not None:
                    grupo["filas"].append(fila)
                    continue
                grupo = {"filas": [fila]}
                indices[clave] = grupo
                grupos.append(grupo)
            else:
                grupos.append({"filas": [fila]})
        dias.append(
            {
                "fecha": fecha,
                "resumen": _resumen_dia(filas_dia),
                "grupos": [_grupo(g["filas"]) for g in grupos],
            }
        )
    return dias


def vista_decisiones(items: list[dict] | None) -> dict:
    """Agrupa las filas de la pagina y cuenta lo que muestra cada contador."""
    filas = [_fila(item) for item in (items or [])]
    cuenta = Counter(f["tipo"] for f in filas)
    rango = ""
    if filas:
        rango = f"{len(filas)} decisiones · {filas[-1]['fecha']} – {filas[0]['fecha']}"
    en_pagina = {
        "total": len(filas),
        "pausa": cuenta.get("pausa", 0),
        "puja_sube": cuenta.get("puja_sube", 0),
        "puja_baja": cuenta.get("puja_baja", 0),
        "negativo": cuenta.get("negativo", 0),
        "captura": cuenta.get("captura", 0),
    }
    return {"dias": _dias(filas), "en_pagina": en_pagina, "rango": rango}
