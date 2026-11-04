"""Exporta casos del prototipo a fixtures JSON y reproduce s1 (BIDS 02 M.1).

Corre DONDE existe datos/ (la carpeta prototipos/, solo lectura):

    cd ~/.claude/orchestrate/motor-poca-data/docs/diseno/sintesis/prototipos
    W=~/dev/wt-bids-02-s2
    PYTHONPATH=$W:. $W/.venv/bin/python $W/docs/evidencia/bids-02/ejecucion/M.1/exporta_casos.py

Sin argumentos escribe tests/fixtures/politica_dorada_a1.json (a lo mas 300
casos y 500 KB: todos los que el prototipo recorta, sube o regresa, mas una
muestra estratificada del resto) y tests/fixtures/politica_2963_a2.json (la
hoja 2963 dia por dia por el camino simulado de a2). Con --completo rejuega
las 820 decisiones live con app/optimizer/politica.py e imprime la tabla con
el formato de prototipos/s1_salida.txt. Los CSV no entran al repo.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, ".")  # carga y politica viven en el directorio actual

import carga  # noqa: E402
import politica as P  # noqa: E402

import app  # noqa: E402
from app.optimizer.caso import (  # noqa: E402
    BidVigente,
    CambioBid,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    EvidenciaNivel,
    InsumosPausa,
    PrecioVentana,
    Tramo,
    Trayectoria,
)
from app.optimizer.politica import Mantener, Mover, Regresar, decide  # noqa: E402

W = Path(app.__file__).resolve().parent.parent
FIX_DORADA = W / "tests" / "fixtures" / "politica_dorada_a1.json"
FIX_2963 = W / "tests" / "fixtures" / "politica_2963_a2.json"
MONEDA = {"amazon_mx": "MXN", "amazon_us": "USD"}

# Piso y techo que no aprietan: el prototipo no modela rango de bid y la
# comparacion exige que los clamps no cambien ningun veredicto (sondeo s1:
# el delta minimo es 0.024, sobre el umbral de 0.01). Andamio del rejuego,
# no numeros del producto.
PISO_HARNESS = Decimal(0)
TECHO_HARNESS = Decimal(999999)


def _dec(valor: float) -> Decimal:
    return Decimal(str(valor))


def _tramo(sumas) -> Tramo:
    impr, clics, costo, pedidos, venta = sumas
    return Tramo(
        clics=int(clics),
        pedidos=int(pedidos),
        venta=_dec(venta),
        gasto=_dec(costo),
        impresiones=int(impr),
    )


def _evidencia(hojas, dia) -> EvidenciaNivel:
    td = carga.td
    reciente = carga.suma_hojas(hojas, dia - td(days=50), dia - td(days=10))
    antiguo = carga.suma_hojas(hojas, dia - td(days=90), dia - td(days=51))
    return EvidenciaNivel(reciente=_tramo(reciente), antiguo=_tramo(antiguo))


_cache_cuenta = {}


def _cuenta(plat, dia) -> EvidenciaNivel:
    if (plat, dia) not in _cache_cuenta:
        hojas = [h for h in carga.activa if carga.hojas[h]["platform"] == plat]
        _cache_cuenta[(plat, dia)] = _evidencia(hojas, dia)
    return _cache_cuenta[(plat, dia)]


def _origen(motivo: str) -> str:
    if motivo.startswith("banda"):
        return "motor"
    if motivo in ("motor", "regreso_por_desplome"):
        return motivo
    raise ValueError(f"motivo sin origen conocido: {motivo!r}")


def convierte(h, dia, bid: float, historia) -> CasoHoja:
    """CasoHoja equivalente al caso que arma_caso del prototipo para (h, dia).

    `historia` son los cambios aplicados previos [(dia, old, new, motivo)]:
    los reales (aplicadas) para a1/s1, los simulados para a2. El grupo
    replica al prototipo (todas las hojas con serie, incluidas inactivas);
    M.2 lo filtra por v_hoja_activa (ver NOTA en mutantes.md).
    """
    plat = carga.hojas[h]["platform"]
    hist = [x for x in historia if x[0] < dia]
    cambios = [
        CambioBid(fecha=x, bid_antes=_dec(o), bid_despues=_dec(n), origen=_origen(m))
        for (x, o, n, m) in hist
    ]
    efecto = None
    if hist:
        td = carga.td
        x, _o, _n, _m = hist[-1]
        previo = hist[-2][0] + td(days=1) if len(hist) > 1 else x - td(days=30)
        pre = carga.suma(h, max(previo, x - td(days=30)), x - td(days=1))
        post = carga.suma(h, x + td(days=1), dia - td(days=3))
        dias_cal = (dia - td(days=1) - x).days
        a = carga.suma(h, x - td(days=7), x - td(days=1))
        dias_traf = min(7, dias_cal)
        b = carga.suma(h, x + td(days=1), x + td(days=dias_traf)) if dias_traf > 0 else [0] * 5
        efecto = EfectoCambio(
            dias_post=dias_cal,
            impresiones_pre7=int(a[0]),
            clics_pre7=int(a[1]),
            impresiones_post=int(b[0]),
            dias_post_trafico=dias_traf,
            clics_post=int(post[1]),
            gasto_post=_dec(post[2]),
            clics_pre=int(pre[1]),
            gasto_pre=_dec(pre[2]),
            vendia=carga.suma(h, x - td(days=90), x - td(days=1))[3] >= 1,
        )
    td = carga.td
    ventana = carga.suma(h, dia - td(days=33), dia - td(days=3))
    ag = carga.hojas[h]["ad_group_id"]
    return CasoHoja(
        plataforma=plat,
        hoja_id=int(h),
        ad_group_id=int(ag),
        bid=BidVigente(
            valor=_dec(bid), moneda=MONEDA[plat], piso=PISO_HARNESS, techo=TECHO_HARNESS
        ),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda=MONEDA[plat],
                equilibrio_acos_pct=_dec(P.EQUILIBRIO[plat]),
                gasto_para_concluir=_dec(P.GASTO_PARA_CONCLUIR[plat]),
                confianza_recorte=_dec(P.CONF_RECORTE),
                confianza_subida=_dec(P.CONF_SUBIDA),
            ),
            target_acos_pct=_dec(P.TARGET[plat]),
        ),
        propia=_evidencia([h], dia),
        pedidos_inmaduros=int(carga.suma(h, dia - td(days=9), dia - td(days=1))[3]),
        grupo=_evidencia(carga.por_grupo[(plat, ag)], dia),
        cuenta=_cuenta(plat, dia),
        precio=PrecioVentana(gasto=_dec(ventana[2]), clics=int(ventana[1])),
        trayectoria=Trayectoria(cambios=tuple(cambios), efecto=efecto),
        pausa=InsumosPausa(None, 0, Decimal(0), None, None),
        ventana_desde=dia - td(days=90),
        ventana_hasta=dia - td(days=10),
        observado_al=None,
    )


def _decision_a1():
    """Las 820 decisiones que rejuega a1, en su mismo orden y filtro."""
    for d in carga.decisiones:
        if d["kind"] != "bid" or d["modo_ciclo"] != "live" or not d["new_value"]:
            continue
        if d["hoja_id"] not in carga.hojas:
            continue
        yield d


def _etiqueta(veredicto):
    if isinstance(veredicto, Mantener):
        return ("mantener", None, veredicto.motivo)
    if isinstance(veredicto, Mover):
        return (
            "recortar" if veredicto.factor < 0 else "subir",
            veredicto.factor,
            veredicto.motivo,
        )
    if isinstance(veredicto, Regresar):
        return ("regresar", None, veredicto.motivo)
    raise AssertionError(f"veredicto imposible en s1 (sin cortes): {veredicto!r}")


def completo():
    """Rejuego completo estilo a1_rejuego.py, decidiendo con politica.py."""
    res = collections.defaultdict(collections.Counter)
    gasto = collections.defaultdict(collections.Counter)
    vistos = collections.defaultdict(set)
    for d in _decision_a1():
        old, new = carga.F(d["old_value"]), carga.F(d["new_value"])
        plat = d["platform"]
        caso = convierte(d["hoja_id"], carga.D(d["dia"]), old, carga.aplicadas[d["hoja_id"]])
        accion, _factor, motivo = _etiqueta(decide(caso))
        sentido = "recorte" if new < old else "subida"
        res[(plat, sentido)][f"{accion}:{motivo}"] += 1
        res[(plat, sentido)]["_total"] += 1
        gasto[(plat, sentido)][f"{accion}:{motivo}"] += carga.F(d["v_costo"])
        if accion == "recortar":
            vistos[plat].add(d["hoja_id"])
    for (plat, sentido), c in sorted(res.items()):
        tot = c.pop("_total")
        print(
            f"\n== {plat} | {sentido}s decididos en vivo: {tot} | "
            f"target {P.TARGET[plat]} | variante completa"
        )
        quedan = sum(v for k, v in c.items() if k.startswith("recortar"))
        for k, v in sorted(c.items(), key=lambda kv: -kv[1]):
            print(
                f"   {v:4}  {k:45} gasto 30d de esas decisiones {gasto[(plat, sentido)][k]:10.2f}"
            )
        if sentido == "recorte":
            print(
                f"   => recortes que la politica nueva tambien haria: {quedan} de {tot} "
                f"({100 * quedan / tot:.0f}%), en {len(vistos[plat])} hojas"
            )


def _entrada(h, dia, caso, proto):
    accion, factor, motivo = proto
    return {
        "dia": dia.isoformat(),
        "hoja_id": int(h),
        "caso": caso.como_json(),
        "prototipo": {"accion": accion, "factor": factor, "motivo": motivo},
    }


def exporta():
    """Escribe los dos fixtures. Todos los de accion mas muestra estratificada.

    "La politica" es niveles_v3 (decide de app/optimizer): el fixture trae
    todos los casos donde ELLA actua, mas una muestra estratificada (por
    plataforma y motivo suyo) de los que mantiene. Los casos donde ella y
    el prototipo difieren (divergencia D1, ver nota-D1-previa.md) se
    excluyen de la muestra y se listan en procedencia.
    """
    todas = []
    divergentes = []
    for d in _decision_a1():
        h = d["hoja_id"]
        dia = carga.D(d["dia"])
        old = carga.F(d["old_value"])
        proto_caso = P.arma_caso(h, dia, old)
        proto = P.decide(proto_caso, "completa")
        caso = convierte(h, dia, old, carga.aplicadas[h])
        mia_accion, _f, mia_motivo = _etiqueta(decide(caso))
        entrada = _entrada(h, dia, caso, proto)
        todas.append((d["platform"], mia_accion, mia_motivo, entrada))
        if (mia_accion, mia_motivo) != (proto[0], proto[2]):
            divergentes.append(
                {
                    "dia": dia.isoformat(),
                    "hoja_id": int(h),
                    "prototipo": f"{proto[0]}:{proto[2]}",
                    "niveles_v3": f"{mia_accion}:{mia_motivo}",
                }
            )
    accion = [e for e in todas if e[1] != "mantener"]
    resto = [
        e
        for e in todas
        if e[1] == "mantener"
        and (e[1], e[2]) == (e[3]["prototipo"]["accion"], e[3]["prototipo"]["motivo"])
    ]
    grupos = collections.defaultdict(list)
    for plat, _acc, motivo, entrada in resto:
        grupos[(plat, motivo)].append(entrada)
    for lista in grupos.values():
        lista.sort(key=lambda e: (e["dia"], e["hoja_id"]))
    elegidas = [
        e for (_p, _a, _m, e) in sorted(accion, key=lambda t: (t[0], t[3]["dia"], t[3]["hoja_id"]))
    ]
    claves = sorted(grupos)
    while len(elegidas) < 300:
        agrego = False
        for clave in claves:
            if grupos[clave] and len(elegidas) < 300:
                elegidas.append(grupos[clave].pop(0))
                agrego = True
        if not agrego:
            break
    elegidas.sort(key=lambda e: (e["dia"], e["hoja_id"]))
    cuerpo = {
        "procedencia": {
            "descripcion": "rejuego a1 (historia real de bids) con politica niveles_v3: "
            "todos los casos donde ella recorta, sube o regresa, mas muestra "
            "estratificada por plataforma y motivo de los que mantiene",
            "extraido_en": dt.datetime.now(dt.UTC).isoformat(),
            "criterio": "decisiones kind=bid live con new_value, 2026-09-02..2026-10-09",
            "conteo": len(elegidas),
            "acciones": len(accion),
            "divergentes_excluidos_D1": divergentes,
        },
        "casos": elegidas,
    }
    texto = json.dumps(cuerpo, separators=(",", ":"))
    assert len(elegidas) <= 300 and len(texto.encode("utf-8")) < 500 * 1024, (
        len(elegidas),
        len(texto.encode("utf-8")),
    )
    FIX_DORADA.write_text(texto, encoding="utf-8")
    kb = len(texto.encode("utf-8"))
    print(f"dorada: {len(elegidas)} casos ({len(accion)} de accion), {kb} bytes")

    hoja = "2963"
    ini, fin = dt.date(2026, 9, 2), dt.date(2026, 10, 9)
    if hoja in carga.aplicadas:
        bid = carga.aplicadas[hoja][0][1]
    else:
        bid = carga.F(carga.hojas[hoja]["bid_actual"])
    hist = []
    td = carga.td
    dias = []
    dia = ini
    while dia <= fin:
        if carga.suma(hoja, dia - td(days=15), dia - td(days=2))[0] == 0:
            dia += td(days=1)
            continue
        proto_caso = P.arma_caso(hoja, dia, bid, historia=hist)
        proto = P.decide(proto_caso, "completa")
        dias.append(_entrada(hoja, dia, convierte(hoja, dia, bid, hist), proto))
        accion_p, factor, _motivo = proto
        if accion_p != "mantener":
            viejo = bid
            nuevo = hist[-1][1] if accion_p == "regresar" else viejo * (1 + factor)
            origen = "regreso_por_desplome" if accion_p == "regresar" else "motor"
            hist.append((dia, viejo, nuevo, origen))
            bid = nuevo
        dia += td(days=1)
    cuerpo2963 = {
        "procedencia": {
            "descripcion": "hoja 2963 dia por dia por el camino simulado de a2 "
            "(la historia que deja la propia politica)",
            "extraido_en": dt.datetime.now(dt.UTC).isoformat(),
            "criterio": "hoja 2963, dias de ciclo 2026-09-02..2026-10-09 no inertes",
            "conteo": len(dias),
        },
        "casos": dias,
    }
    texto2963 = json.dumps(cuerpo2963, separators=(",", ":"))
    assert len(texto2963.encode("utf-8")) < 500 * 1024
    FIX_2963.write_text(texto2963, encoding="utf-8")
    print(f"2963: {len(dias)} casos, {len(texto2963.encode('utf-8'))} bytes")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exporta fixtures M.1 o reproduce s1.")
    parser.add_argument(
        "--completo",
        action="store_true",
        help="rejuega las 820 decisiones con politica.py (formato s1_salida.txt)",
    )
    args = parser.parse_args(argv)
    if args.completo:
        completo()
    else:
        exporta()


if __name__ == "__main__":
    main()
