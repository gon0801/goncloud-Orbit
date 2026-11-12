#!/usr/bin/env python3
"""Compara la pagina Salud anterior con la nueva (DISENOS 01, bloque B).

Modo local: renderiza el template nuevo con el JSON de `antes/` y comprueba que
cada valor del payload siga en el HTML. Modo `--base`: baja el JSON y el HTML
de una instancia viva; el JSON es la pagina de antes y el HTML, la nueva.

Uso:
    python docs/evidencia/disenos-01/B/compara.py
    python docs/evidencia/disenos-01/B/compara.py --base http://10.13.13.1:8010
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RAIZ))

from app import formato  # noqa: E402
from app.etiquetas import etiqueta  # noqa: E402
from app.ui import templates  # noqa: E402
from app.ui_salud import vista_salud  # noqa: E402

AQUI = Path(__file__).resolve().parent
ANTES = AQUI / "antes" / "api-salud.json"
DESPUES = AQUI / "despues"

_MARCADORES = (
    ": se queda sin presupuesto",
    ": este presupuesto no limita",
)


def _numero(valor) -> str:
    return formato.numero(valor)


def _texto(valor) -> str:
    return "—" if valor is None else str(valor)


def _instante(fila: dict) -> str:
    return formato.fecha_hora(fila.get("finished_at") or fila.get("started_at"))


def _suma(conteos) -> int:
    if isinstance(conteos, dict):
        return sum(int(v or 0) for v in conteos.values())
    return sum(int(v or 0) for v in conteos or [])


def _partir_aviso(frase: str) -> tuple[str, str]:
    for marcador in _MARCADORES:
        indice = frase.rfind(marcador)
        if indice >= 0:
            return frase[:indice], frase[indice + 2 :]
    campana, separador, texto = frase.partition(": ")
    return (campana, texto) if separador else (frase, "")


def _seccion(html: str, plataforma: str) -> str:
    patron = re.compile(
        rf'<section class="salud-detalle" id="detalle-{re.escape(plataforma)}"'
        r".*?>(.*?)</section>",
        re.S,
    )
    encontrado = patron.search(html)
    if not encontrado:
        raise SystemExit(f"no encontre la seccion de {plataforma} en el HTML")
    return encontrado.group(1)


def _render_local(datos: dict) -> str:
    vista = vista_salud(datos.get("plataformas"), datos.get("jev"), None)
    return templates.env.get_template("salud.html").render(pantalla="salud", vista=vista)


def _bajar(base: str) -> tuple[dict, str]:
    with urllib.request.urlopen(f"{base.rstrip('/')}/api/dashboard/salud", timeout=30) as r:
        datos = json.loads(r.read().decode("utf-8"))
    with urllib.request.urlopen(f"{base.rstrip('/')}/salud", timeout=30) as r:
        html = r.read().decode("utf-8")
    return datos, html


# Un solo mapa de lo visible: partir esta funcion esconde que valor se compara.
def _esperados(datos: dict) -> list[tuple[str, str, str]]:  # noqa: C901, PLR0912, PLR0915
    """[(plataforma, texto, donde)] con cada valor visible del payload."""
    esperados: list[tuple[str, str, str]] = []
    plataformas = datos.get("plataformas") or {}

    def detalle(plataforma: str, valor, etiqueta_texto: str = "") -> None:
        esperados.append((plataforma, _texto(valor), etiqueta_texto))

    def resumen(valor) -> None:
        esperados.append(("__resumen__", _texto(valor), ""))

    for plataforma, p in plataformas.items():
        resumen(p.get("watermark") or "—")
        resumen(formato.fecha_hora(p.get("synced_at")))

        ciclo = p.get("ultimo_ciclo") or {}
        if ciclo:
            detalle(plataforma, f"#{ciclo.get('id')}")
            detalle(plataforma, formato.fecha_hora(ciclo.get("started_at")))
            if ciclo.get("status"):
                detalle(plataforma, etiqueta(ciclo.get("status"), "estado_ciclo"))
            detalle(plataforma, _numero(ciclo.get("decisions_count")))
            detalle(plataforma, _numero(ciclo.get("applied_count")))

        for fila in p.get("historico_14d") or []:
            detalle(plataforma, f"#{fila.get('cycle_id')}")
            detalle(plataforma, formato.fecha_hora(fila.get("fecha")))
            if fila.get("status"):
                detalle(plataforma, etiqueta(fila.get("status"), "estado_ciclo"))
            detalle(plataforma, _numero(fila.get("decisions_count")))
            detalle(plataforma, fila.get("motivo") or "—")

        skips = p.get("skips") or {}
        for lado in ("entidad", "termino"):
            for fila in skips.get(lado) or []:
                detalle(plataforma, fila.get("motivo_es") or "")
                detalle(plataforma, _numero(fila.get("count")))

        ads = p.get("ads_ingest")
        if ads:
            detalle(plataforma, etiqueta(ads.get("fuente"), "fuente_ingesta"))
            for reporte in ads.get("reportes") or []:
                detalle(plataforma, _texto(reporte.get("profile_id")))
                detalle(plataforma, etiqueta(reporte.get("reporte"), "reporte_ads"))
                detalle(plataforma, _texto(reporte.get("metric_date")))
                edad = reporte.get("edad_dias")
                detalle(plataforma, f"{edad} dias" if edad is not None else "—")
                if reporte.get("ultimo_estado"):
                    detalle(
                        plataforma,
                        etiqueta(reporte.get("ultimo_estado"), "estado_reporte"),
                    )
            for incidente in ads.get("incidentes") or []:
                perfil = incidente.get("profile_id")
                perfil_texto = f"perfil {perfil}" if perfil is not None else "todos los perfiles"
                aviso = "entregado" if incidente.get("estado_entrega") == "sent" else "pendiente"
                detalle(plataforma, perfil_texto)
                detalle(plataforma, aviso)
                detalle(plataforma, _numero(incidente.get("intentos")))
                detalle(plataforma, formato.fecha_hora(incidente.get("abierto_desde")))

        for fuente, s in (p.get("spapi") or {}).items():
            detalle(plataforma, etiqueta(fuente, "fuente_ingesta"))
            ultima = s.get("ultima")
            if ultima:
                detalle(plataforma, f"#{ultima.get('id')}")
                detalle(plataforma, _instante(ultima))
                detalle(plataforma, _numero(ultima.get("rows_written")))
                detalle(plataforma, _numero(ultima.get("rows_skipped")))
                detalle(plataforma, (ultima.get("skip_reason") or "—").replace("_", " "))
            for clave in ("ultima_429", "ultimo_lwa"):
                if s.get(clave):
                    detalle(plataforma, f"#{s[clave].get('id')}")
                    detalle(plataforma, _instante(s[clave]))

        for forma, q in (p.get("quota") or {}).items():
            detalle(plataforma, etiqueta(forma, "kind"))
            detalle(plataforma, _numero(q.get("used")))
            detalle(
                plataforma,
                _numero(q.get("cap")) if q.get("cap") is not None else "—",
            )
            detalle(plataforma, etiqueta(q.get("fuente"), "fuente_quota"))

        precios = p.get("precios")
        if precios:
            detalle(plataforma, _texto(precios.get("hoy")))
            for clave in ("evaluados", "movidos", "sombra", "goal_inalcanzable", "huerfanas"):
                detalle(plataforma, _numero(precios.get(clave)))
            detalle(plataforma, _numero(_suma(precios.get("no_evaluados"))))
            detalle(plataforma, _numero(_suma(precios.get("frenados"))))
        elif "precios" in p:
            detalle(plataforma, "Sin datos de precios del día.")

        for frase in p.get("avisos_campana") or []:
            campana, texto = _partir_aviso(frase)
            detalle(plataforma, campana)
            detalle(plataforma, texto)

        harvest = p.get("harvest_destino")
        if harvest:
            for clave in ("grupo", "excepcion", "terna"):
                detalle(plataforma, _numero(harvest["resueltos"].get(clave)))
            detalle(plataforma, harvest.get("terna_es") or "")
            for salto in harvest.get("saltos_grupo") or []:
                detalle(plataforma, f"#{salto.get('campaign_id')}")
                detalle(plataforma, salto.get("motivo_es") or "")

        propuesta = p.get("avisos_propuesta")
        if propuesta:
            detalle(plataforma, _numero(propuesta.get("pendientes")))
            if propuesta.get("fallo"):
                detalle(plataforma, propuesta["fallo"])

    jev = datos.get("jev")
    if jev:
        for valor in (
            jev["interruptor"].get("tope_diario"),
            jev["interruptor"].get("min_clics"),
            jev.get("llamadas_hoy"),
            jev.get("pendientes_de_jev"),
            jev.get("grupos_probados"),
            jev.get("grupos_con_gasto"),
            jev.get("ajena_impedida_por_abstencion"),
            jev.get("avisos_sin_entrega"),
            jev.get("fichas_por_vencer_14d"),
        ):
            esperados.append(("__jev__", _numero(valor), ""))
        if jev.get("ultima_fin"):
            esperados.append(("__jev__", formato.fecha_hora(jev["ultima_fin"]), ""))
        corrida = jev.get("ultima_corrida") or {}
        for valor in (corrida.get("unidades"), corrida.get("llamadas"), corrida.get("senales")):
            esperados.append(("__jev__", _numero(valor), ""))

    return [item for item in esperados if item[1]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="instancia viva; sin el, usa antes/ y el template local")
    args = parser.parse_args(argv)
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.base:
        datos, html = _bajar(args.base)
        origen = f"vivo {args.base}"
    else:
        datos = json.loads(ANTES.read_text(encoding="utf-8"))
        html = _render_local(datos)
        DESPUES.mkdir(parents=True, exist_ok=True)
        (DESPUES / "salud.html").write_text(html, encoding="utf-8")
        origen = str(ANTES)

    plataformas = list((datos.get("plataformas") or {}).keys())
    secciones = {pid: _seccion(html, pid) for pid in plataformas}
    faltantes: list[str] = []
    checks = 0
    for plataforma, texto, donde in _esperados(datos):
        checks += 1
        if plataforma in ("__jev__", "__resumen__"):
            presente = texto in html
        else:
            presente = texto in secciones[plataforma]
        if not presente:
            faltantes.append(f"{plataforma}: {texto!r} {donde}".strip())

    print(f"origen del payload: {origen}")
    print(f"plataformas: {', '.join(plataformas)}")
    print(f"valores comprobados: {checks}")
    if faltantes:
        print(f"DIFERENCIAS: {len(faltantes)}")
        for linea in faltantes[:40]:
            print(f"  - {linea}")
        return 1
    print("sin diferencias")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
