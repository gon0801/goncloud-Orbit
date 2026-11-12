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
import html
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


def _valor_ingesta(ads: dict | None) -> str:
    reportes = (ads or {}).get("reportes") or []
    if not reportes:
        return "—"
    escritos = sum(1 for r in reportes if r.get("ultimo_estado") == "written")
    con_fecha = next((r for r in reportes if r.get("metric_date")), None)
    metrica = con_fecha.get("metric_date") if con_fecha else "—"
    edad = con_fecha.get("edad_dias") if con_fecha else None
    edad_texto = f"{edad} dias" if edad is not None else "—"
    return f"{escritos} de {len(reportes)} reportes escritos · métrica {metrica} · {edad_texto}"


def _valor_spapi(spapi: dict | None) -> str:
    filas = list((spapi or {}).items())
    if not filas:
        return "—"
    ok = sum(1 for _, fuente in filas if (fuente.get("ultima") or {}).get("ok") is True)
    saltadas = sum(
        int((fuente.get("ultima") or {}).get("rows_skipped") or 0)
        for _, fuente in filas
        if fuente.get("ultima")
    )
    return f"{ok} de {len(filas)} fuentes ok · {formato.numero(saltadas)} filas saltadas"


def _valor_ciclo(ciclo: dict) -> str:
    if not ciclo:
        return "—"
    valor = (
        f"#{ciclo.get('id')} · {formato.fecha_hora(ciclo.get('started_at'))}"
        f" · decisiones {formato.numero(ciclo.get('decisions_count'))}"
        f" · aplicadas {formato.numero(ciclo.get('applied_count'))}"
    )
    notas = ciclo.get("notes") if isinstance(ciclo.get("notes"), dict) else {}
    telegram = notas.get("telegram")
    if telegram:
        claves = (
            ", ".join(str(clave) for clave in telegram) if isinstance(telegram, dict) else telegram
        )
        valor = f"{valor} · Telegram caído ({claves})"
    return valor


def _valor_historico(historico: list[dict]) -> str:
    if not historico:
        return "—"
    completos = sum(1 for fila in historico if fila.get("status") == "done")
    decisiones = sum(int(fila.get("decisions_count") or 0) for fila in historico)
    return f"{completos} de 14 completos · {formato.numero(decisiones)} decisiones"


def _tipo_aviso(frase: str) -> str:
    _, texto = _partir_aviso(frase)
    if texto.startswith("se queda sin presupuesto"):
        return "sin"
    if texto.startswith("este presupuesto no limita"):
        return "no"
    return "otros"


def _valor_avisos(avisos) -> str:
    if avisos is None:
        return "—"
    sin = sum(1 for frase in avisos if _tipo_aviso(frase) == "sin")
    no = sum(1 for frase in avisos if _tipo_aviso(frase) == "no")
    return f"{sin} sin presupuesto · {no} con presupuesto que no limita"


def _valor_propuesta(propuesta: dict | None) -> str:
    if propuesta is None:
        return "—"
    valor = f"{formato.numero(propuesta.get('pendientes'))} pendientes"
    if propuesta.get("fallo"):
        valor = f"{valor} · {propuesta['fallo']}"
    return valor


def _valor_precios(precios: dict | None) -> str:
    if not precios:
        return "Sin datos de precios del día."
    return (
        f"Hoy {precios.get('hoy')}"
        f" · evaluados {formato.numero(precios.get('evaluados'))}"
        f" · movidos {formato.numero(precios.get('movidos'))}"
        f" · en práctica {formato.numero(precios.get('sombra'))}"
        f" · no evaluados {formato.numero(_suma(precios.get('no_evaluados')))}"
        f" · frenados {formato.numero(_suma(precios.get('frenados')))}"
        f" · objetivo inalcanzable {formato.numero(precios.get('goal_inalcanzable'))}"
        f" · huerfanas pendientes: {formato.numero(precios.get('huerfanas'))}"
    )


def _valor_harvest(harvest: dict | None) -> str | None:
    if not harvest:
        return None
    resueltos = harvest.get("resueltos") or {}
    return (
        f"Destino de harvest: por grupo {formato.numero(resueltos.get('grupo'))}"
        f" · por excepcion {formato.numero(resueltos.get('excepcion'))}"
        f" · por terna {formato.numero(resueltos.get('terna'))}"
        f" ({harvest.get('terna_es')})"
    )


def _valor_jev_primera(jev: dict) -> str:
    interruptor = jev.get("interruptor") or {}
    if interruptor.get("encendido"):
        avisos = "sí" if interruptor.get("avisos") else "no"
        return (
            f"Encendido · tope diario {formato.numero(interruptor.get('tope_diario'))}"
            f" · mínimo de clics {formato.numero(interruptor.get('min_clics'))}"
            f" · avisos {avisos}"
        )
    motivo = interruptor.get("motivo_apagado")
    return f"Apagado ({motivo})" if motivo else "Apagado"


def _valor_jev_fin(jev: dict) -> str:
    if jev.get("ultima_fin"):
        return f"Último cálculo terminado: {formato.fecha_hora(jev['ultima_fin'])}"
    return "Último cálculo terminado: ninguno"


def _valor_jev_corrida(jev: dict) -> str | None:
    corrida = jev.get("ultima_corrida")
    if not corrida:
        return None
    return (
        f"Última corrida: {etiqueta(corrida.get('motivo'), 'motivo_cierre_jev')}"
        f" · {formato.numero(corrida.get('unidades'))} unidades"
        f" · {formato.numero(corrida.get('llamadas'))} llamadas"
        f" · {formato.numero(corrida.get('senales'))} señales"
    )


def _valor_jev_ultima(jev: dict) -> str:
    return (
        f"Llamadas hoy: {formato.numero(jev.get('llamadas_hoy'))}"
        f" · pendientes de Jev: {formato.numero(jev.get('pendientes_de_jev'))}"
        f" · grupos probados: {formato.numero(jev.get('grupos_probados'))}"
        f" de {formato.numero(jev.get('grupos_con_gasto'))}"
        f" · ajena impedida: {formato.numero(jev.get('ajena_impedida_por_abstencion'))}"
        f" · avisos sin entrega: {formato.numero(jev.get('avisos_sin_entrega'))}"
        f" · fichas por vencer: {formato.numero(jev.get('fichas_por_vencer_14d'))}"
    )


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


def _limpiar(fragmento: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragmento)).split())


def _filas_de(bloque: str, tag: str) -> list[str]:
    return re.findall(rf"<{tag}\b.*?</{tag}>", bloque, re.S)


def _celdas(fila: str) -> list[str]:
    return [_limpiar(celda) for celda in re.findall(r"<td\b[^>]*>(.*?)</td>", fila, re.S)]


def _fila_exacta(bloque: str, celdas: list[str]) -> bool:
    return any(_celdas(fila) == celdas for fila in _filas_de(bloque, "tr"))


def _texto_con_tokens(bloque: str, tag: str, tokens: list[str]) -> bool:
    return any(all(token in _limpiar(fila) for token in tokens) for fila in _filas_de(bloque, tag))


# Un solo mapa de filas: cada valor de un digito se exige en su celda o su fila.
def _contexto(datos: dict, html_texto: str, secciones: dict) -> tuple[int, list[str]]:  # noqa: C901, PLR0912, PLR0915
    total = 0
    faltan: list[str] = []

    def exige(cond: bool, descripcion: str) -> None:
        nonlocal total
        total += 1
        if not cond:
            faltan.append(descripcion)

    def en_html(valor: str | None, descripcion: str) -> None:
        if valor:
            exige(valor in html_texto, f"resumen: {descripcion}")

    def en_seccion(plataforma: str, valor: str | None, descripcion: str) -> None:
        if valor:
            exige(valor in secciones[plataforma], f"{plataforma}: {descripcion}")

    def fila(plataforma: str, celdas: list[str], descripcion: str) -> None:
        exige(_fila_exacta(secciones[plataforma], celdas), f"{plataforma}: {descripcion}")

    def texto(plataforma: str, tag: str, tokens: list[str], descripcion: str) -> None:
        exige(
            _texto_con_tokens(secciones[plataforma], tag, tokens),
            f"{plataforma}: {descripcion}",
        )

    for plataforma, p in (datos.get("plataformas") or {}).items():
        en_html(
            f"Últimos datos de métricas: {p.get('watermark') or '—'}"
            f" · Estructura sincronizada: {formato.fecha_hora(p.get('synced_at'))}",
            "datos de metricas",
        )
        en_html(_valor_ingesta(p.get("ads_ingest")), "Ingesta Ads")
        en_html(_valor_spapi(p.get("spapi")), "SP-API")
        en_html(_valor_ciclo(p.get("ultimo_ciclo") or {}), "Último ciclo")
        en_html(_valor_historico(p.get("historico_14d") or []), "Últimos 14 ciclos")
        en_html(_valor_avisos(p.get("avisos_campana")), "Avisos de campaña")
        en_html(_valor_propuesta(p.get("avisos_propuesta")), "Avisos de propuesta")

        ciclo = p.get("ultimo_ciclo") or {}
        if ciclo:
            en_seccion(
                plataforma,
                f"#{ciclo.get('id')} · {formato.fecha_hora(ciclo.get('started_at'))}"
                f" · decisiones: {formato.numero(ciclo.get('decisions_count'))}"
                f" · aplicadas: {formato.numero(ciclo.get('applied_count'))}",
                "linea del ultimo ciclo",
            )
        propuesta = p.get("avisos_propuesta")
        if propuesta:
            en_seccion(
                plataforma,
                f"Avisos de propuesta: {formato.numero(propuesta.get('pendientes'))} pendientes",
                "linea de avisos de propuesta",
            )
        if "precios" in p:
            en_seccion(plataforma, _valor_precios(p.get("precios")), "precios")
        en_seccion(plataforma, _valor_harvest(p.get("harvest_destino")), "harvest")

        for fila_ciclo in p.get("historico_14d") or []:
            fila(
                plataforma,
                [
                    f"#{fila_ciclo.get('cycle_id')}",
                    formato.fecha_hora(fila_ciclo.get("fecha")),
                    etiqueta(fila_ciclo.get("status"), "estado_ciclo")
                    if fila_ciclo.get("status")
                    else "—",
                    formato.numero(fila_ciclo.get("decisions_count")),
                    fila_ciclo.get("motivo") or "—",
                ],
                f"fila del ciclo {fila_ciclo.get('cycle_id')}",
            )

        skips = p.get("skips") or {}
        for lado in ("entidad", "termino"):
            for skip in skips.get(lado) or []:
                texto(
                    plataforma,
                    "li",
                    [f"{skip.get('motivo_es') or ''} {formato.numero(skip.get('count'))}"],
                    f"skip {skip.get('motivo')}",
                )

        ads = p.get("ads_ingest")
        if ads:
            for reporte in ads.get("reportes") or []:
                edad = reporte.get("edad_dias")
                edad_texto = f"{edad} dias" if edad is not None else "—"
                estado = (
                    etiqueta(reporte.get("ultimo_estado"), "estado_reporte")
                    if reporte.get("ultimo_estado")
                    else "—"
                )
                fila(
                    plataforma,
                    [
                        _texto(reporte.get("profile_id")),
                        etiqueta(reporte.get("reporte"), "reporte_ads"),
                        formato.fecha_hora(reporte.get("ultimo_exito")),
                        _texto(reporte.get("metric_date")),
                        edad_texto,
                        estado,
                    ],
                    f"reporte {reporte.get('reporte')}",
                )
            for incidente in ads.get("incidentes") or []:
                perfil = incidente.get("profile_id")
                perfil_texto = f"perfil {perfil}" if perfil is not None else "todos los perfiles"
                texto(
                    plataforma,
                    "p",
                    [
                        perfil_texto,
                        "aviso",
                        f"{formato.numero(incidente.get('intentos'))} intentos",
                        formato.fecha_hora(incidente.get("abierto_desde")),
                    ],
                    "incidente de ingesta",
                )

        for fuente, s in (p.get("spapi") or {}).items():
            ultima = s.get("ultima")
            if ultima:
                estado = (
                    "ok"
                    if ultima.get("ok") is True
                    else "fallo"
                    if ultima.get("ok") is False
                    else "—"
                )
                limite = (
                    f"#{s['ultima_429'].get('id')} · {_instante(s['ultima_429'])}"
                    if s.get("ultima_429")
                    else "—"
                )
                falla = (
                    f"#{s['ultimo_lwa'].get('id')} · {_instante(s['ultimo_lwa'])}"
                    if s.get("ultimo_lwa")
                    else "—"
                )
                fila(
                    plataforma,
                    [
                        etiqueta(fuente, "fuente_ingesta"),
                        f"{estado} #{ultima.get('id')} · {_instante(ultima)}",
                        formato.numero(ultima.get("rows_written")),
                        formato.numero(ultima.get("rows_skipped")),
                        (ultima.get("skip_reason") or "—").replace("_", " "),
                        limite,
                        falla,
                    ],
                    f"fuente spapi {fuente}",
                )
            else:
                fila(
                    plataforma,
                    [
                        etiqueta(fuente, "fuente_ingesta"),
                        "sin corridas",
                        "—",
                        "—",
                        "—",
                        "—",
                        "—",
                    ],
                    f"fuente spapi {fuente}",
                )

        for forma, q in (p.get("quota") or {}).items():
            if q.get("cap") is None:
                tope = (
                    "— (configuración ilegible)"
                    if q.get("fuente") == "config_rota"
                    else "— (sin tope)"
                )
            else:
                tope = formato.numero(q.get("cap"))
            estado = etiqueta(q.get("fuente"), "fuente_quota")
            if q.get("cap") is not None and int(q.get("used") or 0) >= int(q["cap"]):
                estado = f"{estado} saturada"
            fila(
                plataforma,
                [
                    etiqueta(forma, "kind"),
                    formato.numero(q.get("used")),
                    tope,
                    estado,
                ],
                f"quota {forma}",
            )

        for frase in p.get("avisos_campana") or []:
            campana, aviso = _partir_aviso(frase)
            texto(plataforma, "li", [campana, aviso], f"aviso {campana}")

        harvest = p.get("harvest_destino") or {}
        for salto in harvest.get("saltos_grupo") or []:
            texto(
                plataforma,
                "li",
                [f"#{salto.get('campaign_id')}", salto.get("motivo_es") or ""],
                f"salto de harvest {salto.get('campaign_id')}",
            )

    jev = datos.get("jev")
    if jev:
        en_html(_valor_jev_primera(jev), "Jev encendido")
        en_html(_valor_jev_fin(jev), "Jev ultimo calculo")
        en_html(_valor_jev_corrida(jev), "Jev ultima corrida")
        en_html(_valor_jev_ultima(jev), "Jev llamadas")

    return total, faltan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="instancia viva; sin el, usa antes/ y el template local")
    parser.add_argument("--html", help="HTML ya renderizado para comparar contra antes/")
    args = parser.parse_args(argv)
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.base:
        datos, html = _bajar(args.base)
        origen = f"vivo {args.base}"
    else:
        datos = json.loads(ANTES.read_text(encoding="utf-8"))
        if args.html:
            html = Path(args.html).read_text(encoding="utf-8")
            origen = args.html
        else:
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

    contexto_total, contexto_faltan = _contexto(datos, html, secciones)
    checks += contexto_total
    faltantes.extend(contexto_faltan)

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
