"""Vista de Contribucion (DISENOS 01, bloque C).

Solo presentacion: consume el payload de `dashboard_contribucion` y calcula las
tarjetas por plataforma, los conteos por clase y la barra de rango de cada fila.
El signo, la escala y el recorte son los del diseno; ningun dato se recalcula.
Los colores y posiciones viajan como clases CSS (la CSP prohibe el estilo inline).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.optimizer.bid import PLATAFORMAS_MONEDA

_CLASES = {
    "pos": "Positiva",
    "cruza": "Cruza 0",
    "neg": "Negativa",
    "sin": "Sin dato",
}
_ORDEN_CLASES = ("pos", "cruza", "neg", "sin")
_RAYA = "—"
_PASOS_NICE = (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(5), Decimal(10))
_PLATAFORMA = {"amazon_us": "Amazon US", "amazon_mx": "Amazon MX"}
_METODO_FX = {"nearest_prior": "del último día con dato"}
_ETIQUETA_FALLBACK = "contribucion pre-cargos · no decisoria"


def _dec(valor) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        dec = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return dec if dec.is_finite() else None


def _nice(valor: Decimal) -> Decimal:
    """Redondeo del eje hacia arriba al paso 1, 2, 2.5 o 5 (como el diseno)."""
    if valor <= 0:
        return Decimal(0)
    base = Decimal(10) ** valor.adjusted()
    for paso in _PASOS_NICE:
        if paso * base >= valor:
            return paso * base
    return Decimal(10) * base


def _pct(valor: Decimal, minimo: Decimal, span: Decimal) -> Decimal:
    if span <= 0:
        return Decimal(0)
    return min(Decimal(100), max(Decimal(0), (valor - minimo) / span * 100))


def _clase_pct(valor: Decimal) -> int:
    return int(valor.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _texto_eje(valor: Decimal) -> str:
    return _dinero(valor).replace(".00", "") or "0"


def _dinero(valor) -> str:
    if valor is None:
        return _RAYA
    return f"{Decimal(str(valor)):,.2f}"


def _clase_rango(sin_halo: Decimal, con_halo: Decimal) -> str:
    if sin_halo > 0:
        return "pos"
    if con_halo < 0:
        return "neg"
    return "cruza"


def _sin_dato_texto(motivo: str | None) -> str:
    return f"{_RAYA} ({motivo})" if motivo else _RAYA


def _rango(datos: dict) -> str:
    ventana = datos.get("ventana") or {}
    desde = ventana.get("desde")
    hasta = ventana.get("hasta")
    return f"{desde} .. {hasta}" if desde and hasta else ""


def _eje(filas: list[tuple[Decimal, Decimal]]) -> dict | None:
    if not filas:
        return None
    lows = [sin for sin, _ in filas]
    highs = sorted((con for _, con in filas), reverse=True)
    minimo = -_nice(max(Decimal(0), -min(Decimal(0), min(lows))))
    tope = highs[0]
    if len(highs) > 1 and highs[1] > 0 and highs[0] > highs[1] * 3:
        tope = highs[1] * Decimal("1.1")
    maximo = _nice(max(Decimal(0), tope))
    if maximo == minimo:
        maximo = minimo + Decimal(1)
    span = maximo - minimo
    cero = _pct(Decimal(0), minimo, span)
    return {
        "minimo": minimo,
        "maximo": maximo,
        "span": span,
        "cero": cero,
        "cero_clase": _clase_pct(cero),
        "mostrar_min": _clase_pct(cero) > 12,
        "min_texto": _texto_eje(minimo),
        "max_texto": _texto_eje(maximo),
    }


def _segmentos(conteos: dict[str, int], total: int) -> list[dict]:
    segmentos: list[dict] = []
    for clase in _ORDEN_CLASES:
        n = conteos[clase]
        if not n:
            continue
        ancho = max(1, _clase_pct(Decimal(n) * 100 / Decimal(total)))
        segmentos.append(
            {"clase": clase, "etiqueta": _CLASES[clase], "n": n, "ancho": min(100, ancho)}
        )
    return segmentos


def _fila(item: dict, eje: dict | None) -> dict:
    sin_halo = _dec(item.get("contrib_sin_halo"))
    con_halo = _dec(item.get("contrib_con_halo"))
    motivo = item.get("motivo_ausencia")
    ausente = sin_halo is None or con_halo is None
    clase = "sin" if ausente else _clase_rango(sin_halo, con_halo)
    fecha_desde = item.get("metric_date_from")
    fecha_hasta = item.get("metric_date_to")
    fx_texto = None
    if item.get("fx_source") == "nearest_prior":
        fx_texto = "Tipo de cambio aproximado: " + _METODO_FX["nearest_prior"]
    fila = {
        "nombre": item.get("nombre") or _RAYA,
        "clase": clase,
        "etiqueta": _CLASES[clase],
        "texto": _sin_dato_texto(motivo)
        if ausente
        else (f"{_dinero(sin_halo)} .. {_dinero(con_halo)}"),
        "motivo": motivo or "",
        "mostrar_parcial": bool(motivo) and not ausente,
        "datos": f"{fecha_desde} .. {fecha_hasta}" if fecha_desde and fecha_hasta else _RAYA,
        "multilisting": bool(item.get("precio_min_multilisting")),
        "fx_texto": fx_texto,
        "punto": False,
        "izquierda": 0,
        "ancho": 0,
        "fuera_escala": False,
        "recortado": False,
        "real_texto": "",
    }
    if ausente or eje is None:
        return fila
    span = eje["span"]
    izquierda = _pct(sin_halo, eje["minimo"], span)
    derecha = _pct(min(con_halo, eje["maximo"]), eje["minimo"], span)
    fila["recortado"] = con_halo > eje["maximo"]
    fila["real_texto"] = _dinero(con_halo)
    fila["izquierda"] = _clase_pct(izquierda)
    if sin_halo == con_halo:
        fila["punto"] = True
    elif sin_halo >= eje["maximo"]:
        # Todo el rango cae fuera del borde recortado: se marca el borde.
        fila["fuera_escala"] = True
    else:
        ancho = max(1, _clase_pct(derecha - izquierda))
        ancho = min(ancho, 100 - fila["izquierda"])
        if ancho <= 0:
            # El redondeo dejo la barra en el borde: se marca, no se oculta.
            fila["fuera_escala"] = True
        else:
            fila["ancho"] = ancho
    return fila


def _plataforma(plataforma: str, datos: dict) -> dict:
    filas = datos.get("filas") or []
    moneda = next((f.get("moneda") for f in filas if f.get("moneda")), None)
    moneda = moneda or PLATAFORMAS_MONEDA.get(plataforma, "")
    etiqueta_contribucion = next(
        (f.get("etiqueta") for f in filas if f.get("etiqueta")), _ETIQUETA_FALLBACK
    )
    rangos = [
        (lo, hi)
        for lo, hi in (
            (_dec(f.get("contrib_sin_halo")), _dec(f.get("contrib_con_halo"))) for f in filas
        )
        if lo is not None and hi is not None
    ]
    eje = _eje(rangos)
    filas_vista = [_fila(f, eje) for f in filas]
    conteos = {clase: 0 for clase in _ORDEN_CLASES}
    for fila in filas_vista:
        conteos[fila["clase"]] += 1
    total = len(filas_vista)
    campanas = "1 campaña" if total == 1 else f"{total} campañas"
    if eje is not None:
        eje = {
            "min_texto": eje["min_texto"],
            "max_texto": eje["max_texto"],
            "cero_clase": eje["cero_clase"],
            "mostrar_min": eje["mostrar_min"],
        }
    return {
        "id": plataforma,
        "nombre": _PLATAFORMA.get(plataforma, plataforma),
        "moneda": moneda,
        "rango": _rango(datos),
        "n": total,
        "campanas": campanas,
        "leyenda": (
            f"{etiqueta_contribucion} · {moneda} · el tipo de cambio aproximado se marca en la fila"
        ),
        "segmentos": _segmentos(conteos, total) if total else [],
        "eje": eje,
        "filas": filas_vista,
    }


def vista_contribucion(plataformas: dict) -> dict:
    """Vista lista para `contribucion.html`; conserva el orden del payload."""
    return {
        "plataformas": [
            _plataforma(plataforma, datos) for plataforma, datos in (plataformas or {}).items()
        ]
    }
