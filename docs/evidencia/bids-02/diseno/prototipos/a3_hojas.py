# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""A3. Paso a paso de dos hojas danadas: 2963 (MX, exact) y 4924 (US, phrase).
Vista 1, "sobre la historia real": en cada fecha en que el motor viejo decidio un recorte, que habria decidido
la politica nueva viendo los cambios reales anteriores; y, tras cada recorte real, el primer dia en que la
guarda de desplome habria regresado el bid.
Vista 2, "desde el principio": la misma hoja con la politica nueva encendida desde el 2026-09-02 (a2)."""

import politica as P
from carga import *


def linea(c, a):
    p, u = c["propia"], c["ultimo"]
    acos = 100 * p["costo"] / p["venta"] if p["venta"] else float("inf")
    hoy = 100 * c["cpc"] * p["clics"] / p["venta"] if p["venta"] and c["cpc"] else float("nan")
    g = c["grupo"]
    ag = 100 * g["costo"] / g["venta"] if g["venta"] else float("inf")
    s = (
        f"90d pesado: {p['pedidos']:.1f} pedidos, {p['clics']:.0f} clics, ACoS {acos:.1f}% | ACoS al bid de hoy {hoy:.1f}% "
        f"(CPC {c['cpc']:.2f} {c['fuente_cpc']}) | ad group {ag:.1f}% {P.estado_nivel(g, c['target'], c['concluir'])}"
    )
    if u:
        t = c["trafico"]
        s += (
            f" | ultimo cambio {u['dia']} {u['old']:.2f}->{u['new']:.2f}, {u['dias_post']} dias, {u['clics_post']} clics nuevos, "
            f"impresiones {t['pre_impr']}->{t['post_impr']} en {t['dias_post']} d (razon {t['razon'] if t['razon'] is None else round(t['razon'], 2)})"
        )
    return (
        s + f"\n        => {a[0].upper()} {'' if a[1] is None else format(a[1], '+.0%')} ({a[2]})"
    )


for h in ("2963", "4924"):
    r = hojas[h]
    P_t = P.TARGET[r["platform"]]
    print(
        f"\n===== hoja {h} | {r['platform']} | {r['match_type']} | ad group {r['ad_group']} | target {P_t} | equilibrio {P.EQUILIBRIO[r['platform']]}"
    )
    print("  Vista 1: sobre la historia real")
    for d in decisiones:
        if d["hoja_id"] != h or d["modo_ciclo"] != "live" or d["kind"] != "bid":
            continue
        dia = D(d["dia"])
        c = P.arma_caso(h, dia, F(d["old_value"]))
        print(
            f"   {d['dia']} motor viejo: {d['motivo']} {d['old_value']}->{float(d['new_value']):.3f} ({'aplicado' if d['aplicada'] == 't' else 'no aplicado'}; 30d: {d['v_clicks']} clics, {d['v_orders']} pedidos)"
        )
        print("        nueva: " + linea(c, P.decide(c)))
    print(
        "  Guarda de desplome tras cada recorte real (primer dia en que habria regresado el bid):"
    )
    for x, old, new, _ in aplicadas[h]:
        disparo = None
        for k in range(1, 15):
            dia = x + td(days=k)
            if dia > ULTIMO + td(days=2):
                break
            hist = [y for y in aplicadas[h] if y[0] <= x]
            c = P.arma_caso(h, dia, new, historia=hist)
            a = P.decide(c)
            if a[0] == "regresar":
                disparo = (dia, c["trafico"])
                break
        if disparo:
            t = disparo[1]
            print(
                f"   recorte del {x} ({old:.2f}->{new:.2f}): regresa a {old:.2f} el {disparo[0]} (dia {(disparo[0] - x).days}); impresiones {t['pre_impr']} en 7 d antes, {t['post_impr']} en {t['dias_post']} d despues, razon {t['razon']:.2f}"
            )
        else:
            print(f"   recorte del {x} ({old:.2f}->{new:.2f}): la guarda no dispara")
import contextlib  # noqa: E402
import io  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    import a2_simulacion as S  # noqa: E402  (corre la simulacion del mes, sin repetir sus tablas)

print("\n  Vista 2: desde el principio (simulacion a2)")
for h in ("2963", "4924"):
    print(f"   hoja {h}: {S.bitacora.get(h) or 'ningun cambio en todo el mes'}")
