"""Agregados de presentacion desde la fuente del dashboard, sin nuevas consultas.

RESUMEN-ADS-02: las sumas, la cobertura y el ACoS salen de la MISMA agregacion
del Resumen (`app.dashboard_resumen`); aqui solo se formatea a la presentacion
que ya consumian las plantillas (dinero a 2 decimales, clics enteros).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from app.dashboard_resumen import acos_texto, agrega_filas, roas_texto, variacion
from app.etiquetas import etiqueta as etiqueta_de


def _dec(valor) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        dec = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return dec if dec.is_finite() else None


def _fmt_dinero(valor: Decimal | None) -> str:
    if valor is None:
        return "—"
    return f"${valor:,.2f}"


def _fmt_entero(valor: int | Decimal | None) -> str:
    if valor is None:
        return "—"
    return f"{round(Decimal(str(valor))):,}"


def _total(filas: list[dict], campo: str) -> Decimal | None:
    valores = [Decimal(str(f[campo])) for f in filas if f.get(campo) is not None]
    return sum(valores, Decimal(0)) if valores else None


def kpis_serie(datos: dict) -> dict:
    """Suma solo dias observados de UNA plataforma; los huecos se declaran."""
    filas = datos["series"]
    agregado = agrega_filas(filas)
    acos = acos_texto(agregado.cost, agregado.ad_revenue) if agregado.dinero_alineado else None
    return {
        "cost": f"{agregado.cost:.2f}" if agregado.cost is not None else None,
        "ad_revenue": (f"{agregado.ad_revenue:.2f}" if agregado.ad_revenue is not None else None),
        "clicks": agregado.clicks,
        "acos": acos,
        "cobertura": {
            "cost": agregado.dias_cost,
            "ad_revenue": agregado.dias_ad_revenue,
            "clicks": agregado.dias_clicks,
            "acos": agregado.dias_pares,
        },
        "dias": len(filas),
    }


def kpis_inertes(items: list[dict]) -> dict:
    """Gasto observado separado por moneda; antiguedad desconocida explicita."""
    monedas = sorted({i["moneda"] for i in items if i.get("moneda")})
    gasto = {}
    for moneda in monedas:
        total = _total([i for i in items if i.get("moneda") == moneda], "gasto_90d")
        gasto[moneda] = f"{total:.2f}" if total is not None else None
    return {
        "hojas": len(items),
        "en_espera": sum(i.get("en_espera") is True for i in items),
        "sin_antiguedad": sum(i.get("en_espera") is None for i in items),
        "gasto": gasto,
    }


def clase_cambio(nuevo: str | None, anterior: str | None) -> str:
    """Color de una comparacion monetaria exacta, sin convertir a float."""
    if nuevo is None or anterior is None:
        return "mutado"
    diferencia = Decimal(nuevo) - Decimal(anterior)
    return "ok" if diferencia > 0 else "alerta" if diferencia < 0 else "mutado"


def _spark_paths(series: list[dict]) -> tuple[str, str]:
    """Rutas SVG (linea y area) para ma7.ad_revenue en viewBox 280x34."""
    n = len(series)
    if n == 0:
        return "", ""
    puntos: list[tuple[int, Decimal | None]] = [
        (i, _dec((f.get("ma7") or {}).get("ad_revenue"))) for i, f in enumerate(series)
    ]
    presentes = [v for _, v in puntos if v is not None]
    if not presentes:
        return "", ""
    max_v = max(presentes)
    ancho = Decimal("280")
    alto_util = Decimal("26")
    y_base = Decimal("30")
    segmentos: list[list[tuple[Decimal, Decimal]]] = []
    actual: list[tuple[Decimal, Decimal]] = []
    for idx, val in puntos:
        if val is None:
            if actual:
                segmentos.append(actual)
                actual = []
            continue
        x = Decimal(0) if n == 1 else (Decimal(idx) * ancho / Decimal(n - 1))
        y = y_base if max_v <= 0 else y_base - (val / max_v) * alto_util
        actual.append((x.quantize(Decimal("0.1")), y.quantize(Decimal("0.1"))))
    if actual:
        segmentos.append(actual)
    partes_linea: list[str] = []
    partes_area: list[str] = []
    for seg in segmentos:
        if len(seg) == 1:
            x0, y0 = seg[0]
            partes_linea.append(f"M{x0},{y0} L{x0},{y0}")
            continue
        trazo = " L".join(f"{x},{y}" for x, y in seg)
        partes_linea.append(f"M{trazo}")
        partes_area.append(f"M{trazo} L{seg[-1][0]},34 L{seg[0][0]},34 Z")
    return " ".join(partes_linea), " ".join(partes_area)


def _dias_gasto_sin_venta(series: list[dict]) -> list[dict]:
    salida: list[dict] = []
    for fila in series:
        cost = _dec(fila.get("cost"))
        rev = _dec(fila.get("ad_revenue"))
        if cost is not None and cost > 0 and rev is not None and rev == 0:
            salida.append(fila)
    return salida


def _totales_filas(filas: list[dict], dias_rango: int) -> dict:
    agregado = agrega_filas(filas)
    alineado = agregado.dinero_alineado
    return {
        "cost": f"{agregado.cost:.4f}" if agregado.cost is not None else None,
        "ad_revenue": f"{agregado.ad_revenue:.4f}" if agregado.ad_revenue is not None else None,
        "clicks": agregado.clicks,
        "orders": agregado.orders,
        "acos": acos_texto(agregado.cost, agregado.ad_revenue) if alineado else None,
        "roas": roas_texto(agregado.cost, agregado.ad_revenue) if alineado else None,
        "sin_ventas": (
            agregado.ad_revenue is not None
            and agregado.ad_revenue == 0
            and agregado.dias_ad_revenue == dias_rango
        ),
        "dias_con_dato": agregado.dias_con_dato,
        "dias_cost": agregado.dias_cost,
        "dias_ad_revenue": agregado.dias_ad_revenue,
        "dias_clicks": agregado.dias_clicks,
        "dinero_alineado": alineado,
    }


def _picos_ma7(series: list[dict]) -> dict | None:
    con_cost = [
        (fila, _dec((fila.get("ma7") or {}).get("cost")))
        for fila in series
        if _dec((fila.get("ma7") or {}).get("cost")) is not None
    ]
    con_cost = [(f, v) for f, v in con_cost if v is not None and v > 0]
    if len(con_cost) < 2:
        return None
    pico_f, pico_c = max(con_cost, key=lambda par: par[1])
    ult_f, ult_c = con_cost[-1]
    if ult_c >= pico_c or pico_f.get("fecha", "") >= ult_f.get("fecha", ""):
        return None
    caida_pct = ((ult_c - pico_c) / pico_c * Decimal(100)).quantize(Decimal("0.1"))
    con_rev = [
        (fila, _dec((fila.get("ma7") or {}).get("ad_revenue")))
        for fila in series
        if _dec((fila.get("ma7") or {}).get("ad_revenue")) is not None
    ]
    pico_rf, pico_r = max(con_rev, key=lambda par: par[1]) if con_rev else (None, None)
    ult_r = _dec((ult_f.get("ma7") or {}).get("ad_revenue"))
    return {
        "pico_cost": pico_c.quantize(Decimal("0.01")),
        "fecha_pico_cost": pico_f.get("fecha"),
        "ult_cost": ult_c.quantize(Decimal("0.01")),
        "fecha_ult_cost": ult_f.get("fecha"),
        "caida_pct": caida_pct,
        "pico_rev": pico_r.quantize(Decimal("0.01")) if pico_r is not None else None,
        "fecha_pico_rev": pico_rf.get("fecha") if pico_rf else None,
        "ult_rev": ult_r.quantize(Decimal("0.01")) if ult_r is not None else None,
    }


def _estado_tarjeta(
    cost: Decimal | None,
    rev: Decimal | None,
    acos: Decimal | None,
    meta: Decimal | None,
    rev_pct: Decimal | None,
    acos_pp: Decimal | None,
    sin_ventas: bool,
    cobertura_completa: bool,
) -> tuple[str, str, str]:
    if cost is None and rev is None:
        return "Sin dato", "status-neutral", "#64748B"
    if meta is not None:
        if acos is not None and acos <= meta:
            return "Bien", "status-good", "#0F766E"
        return "Atención", "status-warn", "#D97706"
    if rev_pct is not None and acos_pp is not None:
        if rev_pct > 0 and acos_pp < 0:
            return "Bien", "status-good", "#0F766E"
        return "Atención", "status-warn", "#D97706"
    if sin_ventas or not cobertura_completa:
        return "Atención", "status-warn", "#D97706"
    return "Sin dato", "status-neutral", "#64748B"


def _delta_mini(
    delta: Decimal | None, previo_txt: str | None, sufijo: str, *, neutro: bool = False
) -> tuple[str, str]:
    if delta is not None:
        clase = "flat" if neutro else ("up" if delta > 0 else "down" if delta < 0 else "flat")
        pref = "▲ +" if delta > 0 else "▼ " if delta < 0 else ""
        return clase, f"{pref}{delta}{sufijo} vs ant."
    if previo_txt is not None:
        return "flat", f"antes {previo_txt}*"
    return "flat", "sin comparacion"


def _frase_tarjeta(
    *,
    bloque: Mapping,
    cost: Decimal | None,
    rev: Decimal | None,
    acos: Decimal | None,
    meta: Decimal | None,
    rev_pct: Decimal | None,
    acos_pp: Decimal | None,
    series: list[dict],
) -> str:
    moneda = bloque.get("moneda", "")
    comp = bloque.get("comparison") or {}
    var = comp.get("variacion") or {}
    cov = bloque.get("coverage") or {}
    if cost is None and rev is None:
        return "Sin observaciones de gasto ni ventas atribuidas en la ventana seleccionada."
    if meta is not None:
        if acos is not None and acos <= meta:
            return (
                f"ACoS en {acos}% cumple la meta configurada ({meta}%), "
                f"con {_fmt_dinero(rev)} {moneda} en ventas atribuidas."
            )
        if acos is not None:
            return (
                f"ACoS en {acos}% supera la meta configurada ({meta}%) "
                f"por +{(acos - meta).quantize(Decimal('0.01'))} pp."
            )
        return f"Sin ventas atribuidas frente a la meta de {meta}%."
    if rev_pct is not None and acos_pp is not None:
        if rev_pct > 0 and acos_pp < 0:
            return (
                f"Las ventas subieron +{rev_pct}% y el ACoS bajo {acos_pp} pp "
                "respecto al periodo anterior."
            )
        if rev_pct < 0:
            dias_sv = _dias_gasto_sin_venta(series)
            gasto_sv = sum((_dec(d.get("cost")) or Decimal(0) for d in dias_sv), Decimal(0))
            extra = (
                f" Hubo {len(dias_sv)} dias con gasto y cero ventas "
                f"({_fmt_dinero(gasto_sv)} {moneda})."
                if dias_sv
                else ""
            )
            signo = "+" if acos_pp > 0 else ""
            return (
                f"Las ventas bajaron {rev_pct}% y el ACoS cambio "
                f"{signo}{acos_pp} pp vs el periodo anterior.{extra}"
            )
        return (
            f"Las ventas subieron +{rev_pct}%, pero el ACoS subio "
            f"+{acos_pp} pp respecto al periodo anterior."
        )
    picos = _picos_ma7(series)
    if picos is not None and var.get("motivo") == "cobertura_incompleta":
        dias_ant = (comp.get("coverage") or {}).get("dias_con_dato", 0)
        return (
            f"El gasto diario medio bajo {picos['caida_pct']}% desde el pico del "
            f"{picos['fecha_pico_cost']} ({_fmt_dinero(picos['pico_cost'])} -> "
            f"{_fmt_dinero(picos['ult_cost'])} {moneda}/dia). "
            f"*El periodo anterior solo tiene {dias_ant}/{comp.get('dias', 0)} dias con dato."
        )
    return (
        f"Cobertura del periodo: {cov.get('dias_con_dato', 0)}/{bloque.get('dias', 0)} "
        "dias con dato."
    )


def _tarjeta_canal(nombre: str, bloque: Mapping, meta_str: str | None) -> dict:
    totals = bloque.get("totals") or {}
    comp = bloque.get("comparison") or {}
    prev_totals = comp.get("totals") or {}
    var = comp.get("variacion") or {}
    cov = bloque.get("coverage") or {}
    series = list(bloque.get("series") or [])
    moneda = bloque.get("moneda", "")

    acos = _dec(totals.get("acos"))
    rev = _dec(totals.get("ad_revenue"))
    cost = _dec(totals.get("cost"))
    meta = _dec(meta_str)
    rev_pct = _dec(var.get("ad_revenue_pct"))
    cost_pct = _dec(var.get("cost_pct"))
    acos_pp = _dec(var.get("acos_pp"))
    roas_delta = _dec(var.get("roas_delta"))

    estado_texto, estado_clase, color_spark = _estado_tarjeta(
        cost,
        rev,
        acos,
        meta,
        rev_pct,
        acos_pp,
        bool(totals.get("sin_ventas")),
        cov.get("completo") is not False,
    )

    if acos_pp is None:
        acos_delta_clase, acos_delta_texto = "delta-neutral", "sin comparacion"
    elif acos_pp > 0:
        acos_delta_clase, acos_delta_texto = "delta-up-bad", f"▲ +{acos_pp} pp"
    elif acos_pp < 0:
        acos_delta_clase, acos_delta_texto = "delta-down-good", f"▼ {acos_pp} pp"
    else:
        acos_delta_clase, acos_delta_texto = "delta-neutral", "0.00 pp"

    prev_rev = _dec(prev_totals.get("ad_revenue"))
    v_clase, v_texto = _delta_mini(
        rev_pct, _fmt_dinero(prev_rev) if prev_rev is not None else None, "%"
    )
    prev_cost = _dec(prev_totals.get("cost"))
    _, g_texto = _delta_mini(
        cost_pct,
        _fmt_dinero(prev_cost) if prev_cost is not None else None,
        "%",
        neutro=True,
    )
    prev_roas = prev_totals.get("roas")
    r_clase, r_texto = _delta_mini(
        roas_delta, f"{prev_roas}×" if prev_roas is not None else None, "×"
    )
    frase = _frase_tarjeta(
        bloque=bloque,
        cost=cost,
        rev=rev,
        acos=acos,
        meta=meta,
        rev_pct=rev_pct,
        acos_pp=acos_pp,
        series=series,
    )
    linea_svg, area_svg = _spark_paths(series)
    return {
        "nombre": nombre,
        "etiqueta": etiqueta_de(nombre, "plataforma"),
        "moneda": moneda,
        "meta_acos": meta_str,
        "estado_texto": estado_texto,
        "estado_clase": estado_clase,
        "color_spark": color_spark,
        "acos_delta_clase": acos_delta_clase,
        "acos_delta_texto": acos_delta_texto,
        "ventas_delta_clase": v_clase,
        "ventas_delta_texto": v_texto,
        "gasto_delta_texto": g_texto,
        "roas_delta_clase": r_clase,
        "roas_delta_texto": r_texto,
        "spark_linea": linea_svg,
        "spark_area": area_svg,
        "frase": frase,
    }


def _item_gasto_sin_venta(
    nombre: str, bloque: Mapping, conv_por_canal: Mapping[str, Decimal]
) -> tuple[Decimal, dict] | None:
    series = list(bloque.get("series") or [])
    dias_sv = _dias_gasto_sin_venta(series)
    if not dias_sv:
        return None
    etiqueta = etiqueta_de(nombre, "plataforma")
    moneda = bloque.get("moneda", "")
    tot = bloque.get("totals") or {}
    mad = bloque.get("maduracion") or {}
    gasto_sv = sum((_dec(d.get("cost")) or Decimal(0) for d in dias_sv), Decimal(0))
    cost_total = _dec(tot.get("cost"))
    pct_sv = (
        (gasto_sv / cost_total * Decimal(100)).quantize(Decimal("0.1"))
        if cost_total and cost_total > 0
        else Decimal(0)
    )
    evidencia = (
        f"{len(dias_sv)} de {bloque.get('dias', len(series))} dias tuvieron gasto "
        f"y $0.00 en ventas atribuidas ({_fmt_dinero(gasto_sv)} {moneda}, "
        f"{pct_sv}% del gasto del periodo)."
    )
    if nombre in conv_por_canal:
        conv = conv_por_canal[nombre]
        contraste = (
            f" frente a {conv_por_canal['amazon_mx']}% en Amazon MX"
            if nombre != "amazon_mx" and "amazon_mx" in conv_por_canal
            else ""
        )
        evidencia += (
            f" La conversion clic -> orden fue {conv}% "
            f"({_fmt_entero(tot.get('orders'))} ordenes / "
            f"{_fmt_entero(tot.get('clicks'))} clics){contraste}."
        )
    caveat = None
    if mad.get("aplica"):
        n_inm = sum(1 for d in dias_sv if d.get("inmaduro"))
        vent = mad.get("ventana_inmaduros") or {}
        if n_inm > 0 and vent.get("desde") and vent.get("hasta"):
            caveat = (
                f"Ojo: {n_inm} de esos {len(dias_sv)} dias caen en la ventana "
                f"inmadura ({vent['desde']} a {vent['hasta']}); Amazon puede "
                "atribuir ventas hasta 7 dias despues del clic."
            )
    enlaces = (
        [
            ("Ver gasto sin venta →", f"/gasto-sin-venta?plataforma={nombre}"),
            ("Busquedas que gastan sin vender →", f"/busquedas-sin-venta?plataforma={nombre}"),
        ]
        if nombre in ("amazon_mx", "amazon_us")
        else [("Ver campañas →", "/campanas")]
    )
    return pct_sv, {
        "canal": nombre,
        "titulo": f"Gasto sin venta en {etiqueta}",
        "evidencia": evidencia,
        "caveat": caveat,
        "enlaces": enlaces,
    }


def _item_caida_ma7(nombre: str, bloque: Mapping) -> tuple[Decimal, dict] | None:
    series = list(bloque.get("series") or [])
    picos = _picos_ma7(series)
    if picos is None or picos["caida_pct"] > Decimal("-10.0"):
        return None
    etiqueta = etiqueta_de(nombre, "plataforma")
    mad = bloque.get("maduracion") or {}
    evidencia = (
        f"La media de 7 dias de gasto paso de {_fmt_dinero(picos['pico_cost'])}/dia "
        f"({picos['fecha_pico_cost']}) a {_fmt_dinero(picos['ult_cost'])}/dia "
        f"({picos['fecha_ult_cost']}), {picos['caida_pct']}%."
    )
    if picos["pico_rev"] is not None and picos["ult_rev"] is not None:
        evidencia += (
            f" Las ventas atribuidas medias bajaron de "
            f"{_fmt_dinero(picos['pico_rev'])}/dia ({picos['fecha_pico_rev']}) "
            f"a {_fmt_dinero(picos['ult_rev'])}/dia."
        )
    caveat = None
    if nombre == "meli":
        caveat = "El ultimo dia puede ajustarse entre 24 y 48 horas despues del cierre."
    elif mad.get("aplica"):
        vent = mad.get("ventana_inmaduros") or {}
        if vent.get("desde") and vent.get("hasta"):
            caveat = f"Cifras inmaduras del {vent['desde']} al {vent['hasta']}."
    return abs(picos["caida_pct"]), {
        "canal": nombre,
        "titulo": f"Caida de gasto en {etiqueta}",
        "evidencia": evidencia,
        "caveat": caveat,
        "enlaces": [("Ver campañas →", "/campanas")],
    }


def _item_ultima_semana(nombre: str, bloque: Mapping) -> tuple[Decimal, dict] | None:
    series = list(bloque.get("series") or [])
    if len(series) < 14:
        return None
    prev7 = _totales_filas(series[-14:-7], 7)
    ult7 = _totales_filas(series[-7:], 7)
    var7 = variacion(ult7, prev7)
    c_ult, r_ult = _dec(ult7.get("cost")), _dec(ult7.get("ad_revenue"))
    c_prev, r_prev = _dec(prev7.get("cost")), _dec(prev7.get("ad_revenue"))
    if (
        c_ult is None
        or r_ult is None
        or c_prev is None
        or r_prev is None
        or (c_ult == 0 and c_prev == 0)
    ):
        return None
    etiqueta = etiqueta_de(nombre, "plataforma")
    moneda = bloque.get("moneda", "")
    mad = bloque.get("maduracion") or {}
    r_pct = _dec(var7.get("ad_revenue_pct"))
    c_pct = _dec(var7.get("cost_pct"))
    a_pp = _dec(var7.get("acos_pp"))
    r_txt = f" ({'+' if r_pct and r_pct > 0 else ''}{r_pct}%)" if r_pct is not None else ""
    c_txt = f" ({'+' if c_pct and c_pct > 0 else ''}{c_pct}%)" if c_pct is not None else ""
    acos_txt = ""
    if prev7.get("acos") is not None and ult7.get("acos") is not None:
        signo_a = "+" if a_pp and a_pp > 0 else ""
        pp_txt = f" ({signo_a}{a_pp} pp)" if a_pp is not None else ""
        verbo = "subiendo" if (a_pp or Decimal(0)) >= 0 else "bajando"
        acos_txt = f", {verbo} el ACoS de {prev7['acos']}% a {ult7['acos']}%{pp_txt}"
    evidencia = (
        f"En los ultimos 7 dias ({series[-7]['fecha']} a {series[-1]['fecha']}) vs "
        f"los 7 previos ({series[-14]['fecha']} a {series[-8]['fecha']}), las "
        f"ventas fueron {_fmt_dinero(r_ult)} vs {_fmt_dinero(r_prev)} {moneda}"
        f"{r_txt} y el gasto {_fmt_dinero(c_ult)} vs {_fmt_dinero(c_prev)} "
        f"{moneda}{c_txt}{acos_txt}."
    )
    caveat = None
    if mad.get("aplica"):
        vent = mad.get("ventana_inmaduros") or {}
        if vent.get("desde") and vent.get("hasta"):
            caveat = (
                f"La ultima semana coincide con la ventana inmadura "
                f"({vent['desde']} a {vent['hasta']}); conviene esperar cierre "
                "antes de tocar pujas grandes."
            )
    return c_ult, {
        "canal": nombre,
        "titulo": f"{etiqueta}, ultima semana",
        "evidencia": evidencia,
        "caveat": caveat,
        "enlaces": [("Ver decisiones recientes →", "/decisiones")],
    }


def _que_revisar(canales: Mapping[str, Mapping]) -> list[dict]:
    """Construye la lista priorizada 'Que revisar' desde el contrato."""
    items_sv: list[tuple[Decimal, dict]] = []
    items_caida: list[tuple[Decimal, dict]] = []
    items_sem: list[tuple[Decimal, dict]] = []

    conv_por_canal: dict[str, Decimal] = {}
    for nombre, bloque in canales.items():
        tot = bloque.get("totals") or {}
        orders, clicks = tot.get("orders"), tot.get("clicks")
        if orders is not None and clicks is not None and clicks > 0:
            conv_por_canal[nombre] = (Decimal(orders) / Decimal(clicks) * Decimal(100)).quantize(
                Decimal("0.01")
            )

    for nombre, bloque in canales.items():
        sv = _item_gasto_sin_venta(nombre, bloque, conv_por_canal)
        if sv is not None:
            items_sv.append(sv)
        caida = _item_caida_ma7(nombre, bloque)
        if caida is not None:
            items_caida.append(caida)
        sem = _item_ultima_semana(nombre, bloque)
        if sem is not None:
            items_sem.append(sem)

    items_sv.sort(key=lambda par: par[0], reverse=True)
    items_caida.sort(key=lambda par: par[0], reverse=True)
    items_sem.sort(key=lambda par: par[0], reverse=True)

    if len(canales) == 1:
        combinados = (
            [it for _, it in items_sv] + [it for _, it in items_caida] + [it for _, it in items_sem]
        )
        return combinados[:3]

    usados: set[str] = set()
    seleccionados: list[dict] = []
    for lista in (items_sv, items_caida, items_sem):
        for _, item in lista:
            if item["canal"] not in usados:
                seleccionados.append(item)
                usados.add(item["canal"])
                break
    for lista in (items_sv, items_caida, items_sem):
        for _, item in lista:
            if len(seleccionados) < 3 and item not in seleccionados:
                seleccionados.append(item)
    return seleccionados


def vista_resumen(contrato: Mapping, metas_override: Mapping | None = None) -> dict:
    """Datos derivados de presentacion para resumen.html (sin SQL ni inventos)."""
    canales = contrato.get("canales") or {}
    metas = dict(contrato.get("metas_acos") or {})
    if isinstance(metas_override, Mapping):
        metas.update(metas_override)
    tarjetas = {
        nombre: _tarjeta_canal(nombre, bloque, metas.get(nombre))
        for nombre, bloque in canales.items()
    }
    primer_comp = next(
        (
            b.get("comparison")
            for b in canales.values()
            if isinstance(b, Mapping) and b.get("comparison")
        ),
        None,
    )
    return {
        "tarjetas": tarjetas,
        "que_revisar": _que_revisar(canales),
        "comparison_ventana": primer_comp,
    }
