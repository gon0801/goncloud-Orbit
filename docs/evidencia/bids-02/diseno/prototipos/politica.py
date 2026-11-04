# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Politica `niveles_v3` en miniatura (espejo en float de bosquejo.py: decide(caso) -> veredicto).
Sirve a los prototipos; el motor real va en Decimal. Una sola funcion pura decide; las lecturas
(`arma_caso`) reconstruyen el caso "como se veia" el dia D con las series diarias."""

import math

from carga import *

TARGET = {"amazon_mx": 20.72, "amazon_us": 28.34}
EQUILIBRIO = {"amazon_mx": 41.43, "amazon_us": 31.49}  # margen neto antes de publicidad
GASTO_PARA_CONCLUIR = {"amazon_mx": 350.0, "amazon_us": 36.0}
CONF_RECORTE, CONF_SUBIDA = 0.80, 0.70
MULT_FUERTE, MULT_SUAVE, MULT_SUBIDA = 1.35, 1.15, 0.85
PESO_ANTIGUO = 0.5  # dias D-51..D-90
DIAS_EFECTO = 7  # dias posteriores que hacen legible un cambio
DIAS_GUARDA = 4  # dias posteriores minimos para la guarda de desplome
CLICS_NUEVOS = 20  # clics posteriores para repetir direccion
DESPLOME, RETIENE, CRECE = 0.30, 0.70, 1.10  # A4(e) y A7: contra la variacion natural de agosto
VOLUMEN_IMPR, VOLUMEN_CLICS = 1000, 20
HOLGURA_PEGADO = 0.75  # solo para la variante descartada `con_holgura` (A4 b: n=7 y n=4, no alcanza para una regla)
ORDENES_GRUPO = 3
DIAS_SALIDA = 14  # salida por tiempo para invertir direccion sin 20 clics nuevos


def poisson_cdf(n, mu):
    """P(Poisson(mu) <= n), n entero >= 0."""
    if mu <= 0:
        return 1.0
    t = s = math.exp(-mu)
    for k in range(1, n + 1):
        t *= mu / k
        s += t
    return min(s, 1.0)


def p_acos_sobre(c, n_arriba, cpc, aov, limite_pct):
    """P(ACoS verdadero > limite) con previa plana: 1 - P(Poisson(mu) <= n), mu = pedidos esperados al limite."""
    if aov <= 0 or cpc is None:
        return None
    mu = c * cpc / (limite_pct / 100 * aov)
    return 1 - poisson_cdf(n_arriba, mu)


def p_acos_bajo(c, n_abajo, cpc, aov, limite_pct):
    if aov <= 0 or cpc is None:
        return None
    mu = c * cpc / (limite_pct / 100 * aov)
    return poisson_cdf(n_abajo, mu)


def tramos(hs, dia, uno=True):
    """Ventana madura D-90..D-10 en dos tramos; devuelve pesados (clics, pedidos, venta, costo) y crudos."""
    r = suma_hojas(hs, dia - td(days=50), dia - td(days=10))
    a = suma_hojas(hs, dia - td(days=90), dia - td(days=51))
    w = PESO_ANTIGUO
    return {
        "clics": r[1] + w * a[1],
        "pedidos": r[3] + w * a[3],
        "venta": r[4] + w * a[4],
        "costo": r[2] + w * a[2],
        "costo_crudo": r[2] + a[2],
        "pedidos_crudo": r[3] + a[3],
        "clics_crudo": r[1] + a[1],
        "venta_cruda": r[4] + a[4],
    }


_cache_plat = {}


def plataforma_activa(plat, dia):
    k = (plat, dia)
    if k not in _cache_plat:
        hs = [h for h in activa if hojas[h]["platform"] == plat]
        _cache_plat[k] = tramos(hs, dia)
    return _cache_plat[k]


_cache_grupo = {}


def grupo(plat, ag, dia):
    k = (plat, ag, dia)
    if k not in _cache_grupo:
        _cache_grupo[k] = tramos(por_grupo[(plat, ag)], dia)
    return _cache_grupo[k]


def arma_caso(h, dia, bid, historia=None, target=None):
    """Reconstruye el caso de la hoja h el dia `dia` (ciclo). `historia` = cambios aplicados previos
    [(dia, old, new, origen)]; por defecto los reales."""
    r = hojas[h]
    plat = r["platform"]
    t = target or TARGET[plat]
    hist = [x for x in (aplicadas.get(h, []) if historia is None else historia) if x[0] < dia]
    propia = tramos([h], dia)
    inmaduros = suma(h, dia - td(days=9), dia - td(days=1))[3]
    g = grupo(plat, r["ad_group_id"], dia)
    resto = {k: g[k] - propia[k] for k in g}
    caso = {
        "hoja": h,
        "plat": plat,
        "dia": dia,
        "bid": bid,
        "target": t,
        "equilibrio": EQUILIBRIO[plat],
        "concluir": GASTO_PARA_CONCLUIR[plat],
        "propia": propia,
        "inmaduros": inmaduros,
        "grupo": g,
        "resto": resto,
        "cuenta": plataforma_activa(plat, dia),
        "ultimo": None,
        "trafico": None,
        "cpc": None,
        "fuente_cpc": None,
        "holgura": None,
        "piso": None,
        "previa": previa(plataforma_activa(plat, dia), resto),
    }
    # --- precio: CPC al bid de hoy (medido tras el cambio, proyectado, o de la ventana)
    fin = dia - td(days=3)
    if hist:
        x, old, new, origen = hist[-1]
        previo = hist[-2][0] + td(days=1) if len(hist) > 1 else x - td(days=30)
        pre = suma(h, max(previo, x - td(days=30)), x - td(days=1))
        post = suma(h, x + td(days=1), fin)
        caso["ultimo"] = {
            "dia": x,
            "old": old,
            "new": new,
            "dir": 1 if new > old else -1,
            "origen": origen,
            "clics_post": post[1],
            "dias_post": (dia - td(days=1) - x).days,
        }
        if post[1] >= CLICS_NUEVOS and post[2] > 0:
            caso["cpc"], caso["fuente_cpc"] = post[2] / post[1], "medido_tras_cambio"
            caso["holgura"] = caso["cpc"] / new
        elif pre[1] > 0 and pre[2] > 0:
            cpc_pre = pre[2] / pre[1]
            caso["holgura"] = cpc_pre / old
            caso["cpc"] = (
                cpc_pre * new / old
            )  # A4(c): escalar 1 a 1 erra 6 % (mediana); el CPC del grupo, 26 %
            caso["fuente_cpc"] = "proyectado"
        elif propia["clics_crudo"] > 0 and propia["costo_crudo"] > 0:
            caso["cpc"], caso["fuente_cpc"] = (
                propia["costo_crudo"] / propia["clics_crudo"],
                "ventana_madura",
            )
        # --- trafico antes y despues del ultimo cambio (anclado al calendario, no a las filas de la hoja)
        a = suma(h, x - td(days=7), x - td(days=1))
        dias = min(7, (dia - td(days=1) - x).days)
        b = suma(h, x + td(days=1), x + td(days=dias)) if dias > 0 else [0] * 5
        vendia = suma(h, x - td(days=90), x - td(days=1))[3] >= 1
        caso["trafico"] = {
            "pre_impr": a[0],
            "pre_clics": a[1],
            "post_impr": b[0],
            "post_clics": b[1],
            "dias_post": dias,
            "vendia": vendia,
            "razon": (b[0] / dias) / (a[0] / 7) if a[0] > 0 and dias > 0 else None,
        }
        # piso aprendido: bid desde el que se regreso por desplome o por orden del dueno
        pisos = [
            o for (_, o, n, org) in hist if org in ("regreso_por_desplome", "regreso_del_dueno")
        ]
        caso["piso"] = max(pisos) if pisos else None
    else:
        v = suma(h, dia - td(days=33), fin)
        if v[1] > 0 and v[2] > 0:
            caso["cpc"], caso["fuente_cpc"] = v[2] / v[1], "ventana"
        elif propia["clics_crudo"] > 0 and propia["costo_crudo"] > 0:
            caso["cpc"], caso["fuente_cpc"] = (
                propia["costo_crudo"] / propia["clics_crudo"],
                "ventana_madura",
            )
        if caso["cpc"] and bid:
            caso["holgura"] = caso["cpc"] / bid
    return caso


def previa(cuenta, resto):
    """Pliegue v2 con K=1: cuenta activa -> resto del ad group. Devuelve (cvr, aov) o (None, None)."""
    if cuenta["pedidos"] <= 0 or cuenta["clics"] <= 0:
        return (None, None)
    cvr, aov = cuenta["pedidos"] / cuenta["clics"], cuenta["venta"] / cuenta["pedidos"]
    if resto["clics"] > 0:
        cvr = (resto["pedidos"] + 1) / (resto["clics"] + 1 / cvr)
        aov = (resto["venta"] + aov) / (resto["pedidos"] + 1)
    return (cvr, aov)


def estado_nivel(n, t, concluir):
    """Estado de un nivel superior (ad group o cuenta): 'sangra', 'cumple', 'intermedio' o None (sin datos)."""
    if n["pedidos_crudo"] >= ORDENES_GRUPO and n["venta"] > 0:
        acos = 100 * n["costo"] / n["venta"]
        return "sangra" if acos > MULT_SUAVE * t else ("cumple" if acos <= t else "intermedio")
    if n["pedidos_crudo"] == 0 and n["costo_crudo"] >= concluir:
        return "sangra"
    return None


def decide(caso, variante="completa"):
    """Devuelve (accion, factor, motivo). accion: mantener | recortar | subir | regresar."""
    t, eq, u = caso["target"], caso["equilibrio"], caso["concluir"]
    p, ult, tr = caso["propia"], caso["ultimo"], caso["trafico"]
    # 1. Efecto del ultimo cambio: primero se lee lo que paso, despues se vuelve a mover.
    if ult:
        con_volumen = tr["pre_impr"] >= VOLUMEN_IMPR or tr["pre_clics"] >= VOLUMEN_CLICS
        if (
            ult["dir"] < 0
            and ult["origen"] != "regreso"
            and tr["vendia"]
            and con_volumen
            and tr["dias_post"] >= DIAS_GUARDA
            and tr["razon"] is not None
            and tr["razon"] < DESPLOME
        ):
            return ("regresar", None, "regreso_por_desplome")
        if ult["dias_post"] < DIAS_EFECTO:
            return ("mantener", None, "esperando_efecto")
    # 2. Juicio propio
    n = p["pedidos"]
    estado_grupo = estado_nivel(caso["grupo"], t, u)
    if p["pedidos_crudo"] >= 1:
        cpc = caso["cpc"]
        if cpc is None:
            return ("mantener", None, "sin_precio")
        prev_cvr, prev_aov = caso["previa"]
        aov_propio = p["venta"] / n
        # Recortar: solo datos propios (previa plana) y pedidos redondeados hacia arriba.
        arriba = lambda lim: p_acos_sobre(p["clics"], math.ceil(n), cpc, aov_propio, lim)
        factor, motivo = None, None
        pierde = arriba(eq) >= CONF_RECORTE
        sobre_fuerte = arriba(MULT_FUERTE * t) >= CONF_RECORTE
        sobre_suave = arriba(MULT_SUAVE * t) >= CONF_RECORTE
        acos_hoy = 100 * cpc * p["clics"] / p["venta"]
        if variante == "sin_presion":
            if sobre_fuerte:
                factor, motivo = -0.25, "sobre_target_fuerte"
            elif sobre_suave:
                factor, motivo = -0.12, "sobre_target"
        elif pierde:
            factor, motivo = (
                (-0.25, "pierde_dinero_fuerte") if sobre_fuerte else (-0.12, "pierde_dinero")
            )
        elif sobre_suave or (variante != "sin_herencia" and acos_hoy > t):
            # No pierde dinero con certeza: solo se recorta si su ad group sangra
            # y si el CPC sigue al bid (si no, el recorte solo quita impresiones).
            if estado_grupo != "sangra":
                if sobre_suave:
                    return ("mantener", None, "vende_dentro_del_margen")
            elif (
                variante == "con_holgura"
                and caso["holgura"] is not None
                and caso["holgura"] < HOLGURA_PEGADO
            ):
                return ("mantener", None, "recorte_no_baja_cpc")
            else:
                factor, motivo = (
                    -0.12,
                    "grupo_sangra_vendedora",
                )  # una vendedora bajo margen nunca recibe -25 %
        if factor is None:
            # Subir: la previa de su nivel frena (1 seudo pedido) y los pedidos se redondean hacia abajo.
            aov_sub = (p["venta"] + prev_aov) / (n + 1) if prev_aov else aov_propio
            c_sub = p["clics"] + (1 / prev_cvr if prev_cvr else 0)
            if p_acos_bajo(c_sub, math.floor(n), cpc, aov_sub, MULT_SUBIDA * t) >= CONF_SUBIDA:
                factor, motivo = 0.15, "bajo_target"
            else:
                return ("mantener", None, "azar_lo_explica")
    else:
        if caso["inmaduros"] >= 1:
            return ("mantener", None, "venta_reciente")
        if p["costo_crudo"] >= 2 * u:
            factor, motivo = -0.25, "gasto_sin_venta_doble"
        elif p["costo_crudo"] >= u:
            factor, motivo = -0.12, "gasto_sin_venta"
        elif p["costo_crudo"] <= 0:
            return ("mantener", None, "sin_gasto")
        elif estado_grupo == "sangra":
            if p["costo_crudo"] < u / 4:  # injerto de runner-c: materialidad de la regla de grupo
                return ("mantener", None, "hoja_delgada_sin_dinero_que_mover")
            factor, motivo = -0.12, "grupo_sangra"
        elif estado_grupo == "cumple":
            return ("mantener", None, "grupo_cumple")
        else:
            return ("mantener", None, "sin_evidencia")
    # 3. Trayectoria: no repetir direccion sin clics nuevos ni sobre un recorte que costo trafico
    if ult and (factor < 0) != (ult["dir"] < 0):
        if ult["clics_post"] < CLICS_NUEVOS and ult["dias_post"] < DIAS_SALIDA:
            return ("mantener", None, "esperando_precio_medido")
    if ult and (factor < 0) == (ult["dir"] < 0):
        if ult["clics_post"] < CLICS_NUEVOS:
            return ("mantener", None, "sin_clics_nuevos")
        if factor < 0 and tr["razon"] is not None and tr["razon"] < RETIENE:
            return ("mantener", None, "recorte_costo_trafico")
        if factor > 0 and tr["razon"] is not None and tr["razon"] < CRECE:
            return ("mantener", None, "subida_sin_trafico")
    if factor < 0 and caso["piso"] is not None and caso["bid"] * (1 + factor) <= caso["piso"]:
        return ("mantener", None, "piso_aprendido")
    return ("recortar" if factor < 0 else "subir", factor, motivo)
