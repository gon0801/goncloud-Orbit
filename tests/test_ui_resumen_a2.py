import os
import re
import shutil
import subprocess

import pytest
from test_ui import _ctx_resumen_con_payload

from app import ui

CIFRAS = ("ad_revenue", "cost", "acos", "roas", "clicks", "orders")


def _canal(moneda, *, acos, orders, observado_hasta=True):
    comparison = {
        "desde": "2026-09-28",
        "hasta": "2026-10-27",
        "dias": 30,
        "observado_hasta": "2026-10-27" if observado_hasta else None,
        "totals": {
            "cost": "100.0000",
            "ad_revenue": "400.0000",
            "acos": "25.00",
            "roas": "4.00",
            "clicks": 10,
            "orders": None if orders is None else 2,
        },
        "variacion": {
            "cost_pct": "12.50",
            "ad_revenue_pct": "33.33",
            "clicks_pct": "25.00",
            "acos_pp": "-1.50",
            "roas_delta": "0.25",
            "motivo": None,
            "motivo_por_metrica": {
                "cost_pct": None,
                "ad_revenue_pct": None,
                "clicks_pct": None,
                "acos_pp": None,
                "roas_delta": None,
            },
        },
    }
    return {
        "moneda": moneda,
        "dias": 30,
        "totals": {
            "cost": "100.0000",
            "ad_revenue": "400.0000",
            "clicks": 10,
            "orders": orders,
            "acos": acos,
            "roas": "4.00",
            "sin_ventas": False,
        },
        "comparison": comparison,
        "series": [
            {
                "fecha": "2026-10-28",
                "cost": "100.0000",
                "ad_revenue": "400.0000",
                "clicks": 10,
                "orders": orders,
                "inmaduro": True,
                "ma7": {
                    "cost": "100.0000",
                    "ad_revenue": "400.0000",
                    "clicks": 10,
                    "orders": orders,
                },
            }
        ],
        "coverage": {
            "dias": 30,
            "dias_con_dato": 1,
            "completo": False,
            "por_metrica": {
                "cost": True,
                "ad_revenue": True,
                "clicks": True,
                "orders": orders is not None,
            },
        },
        "maduracion": {
            "aplica": True,
            "ventana_inmaduros": {"desde": "2026-11-19", "hasta": "2026-11-26"},
            "motivo": None,
        },
    }


def _ctx_con_canales() -> dict:
    contexto = _ctx_resumen_con_payload()
    contexto["contrato"]["metas_acos"] = {"amazon_mx": "25"}
    contexto["contrato"]["canales"] = {
        "amazon_mx": _canal("MXN", acos="25.00", orders=2),
        "meli": _canal("MXN", acos="20.00", orders=None, observado_hasta=False),
    }
    contexto["contrato"]["totales_por_moneda"] = [
        {
            "moneda": "MXN",
            "canales": ["amazon_mx", "meli"],
            "canales_sin_dato": [],
            "cost": "200.0000",
            "ad_revenue": "800.0000",
            "acos": "25.00",
            "roas": "4.00",
            "clicks": 20,
            "orders": None,
            "cobertura": {
                "cost": True,
                "ad_revenue": True,
                "clicks": True,
                "orders": False,
            },
            "completo": False,
        }
    ]
    return contexto


def test_resumen_a2_prioriza_pendientes_y_evita_saludos():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_resumen_con_payload())
    assert "<h1>Resumen</h1>" in html
    assert 'class="accion-primaria" href="/campanas/nuevas"' in html
    assert "Hola, David" not in html
    assert "Esto es lo más importante" not in html
    assert "Pendientes de revisión" in html
    assert "Ventas atribuidas" in html
    assert "Canales" in html
    assert 'class="resumen-pendientes superficie"' in html
    assert 'class="resumen-alto"' in html
    assert 'class="chart-card"' in html
    assert 'class="canales-card"' in html
    assert html.index("resumen-pendientes") < html.index("chart-card")
    assert "resumen-monedas-principales" not in html
    assert 'id="datos-resumen"' in html
    assert 'class="custom-range"' in html


def test_resumen_a2_no_duplica_la_navegacion_en_el_pie():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_resumen_con_payload())
    assert 'class="footer-links"' not in html


def test_resumen_tendencia_va_antes_de_canales_y_totales_son_claros():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_con_canales())
    assert html.index("sec-tendencia") < html.index("sec-canales") < html.index("sec-detalle")
    assert "resumen-canales" not in html

    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "resumen.css").read_text(encoding="utf-8")
    curr = re.search(r"\.curr-card\s*\{([^}]+)\}", css)
    assert curr is not None
    assert "background: var(--o-card)" in curr.group(1)
    assert "color: var(--o-ink)" in curr.group(1)
    assert "background: #0f172a" not in curr.group(1)


def test_resumen_ordena_las_cifras_en_celular_sin_duplicar_el_html():
    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "resumen.css").read_text(encoding="utf-8")
    assert ".resumen-alto .resumen-tendencia { order: 1; }" in css
    assert ".resumen-alto .resumen-pendientes { order: 2; }" in css


def test_seis_recuadros_de_cifra_por_canal_con_su_data():
    contexto = _ctx_con_canales()
    html = ui.templates.env.get_template("resumen.html").render(**contexto)
    assert html.count("data-cifra=") == 6 * len(contexto["contrato"]["canales"])
    for clave in CIFRAS:
        assert f'data-cifra="{clave}"' in html
    assert html.count('aria-pressed="true"') == len(contexto["contrato"]["canales"])
    assert 'class="cifra-tile sel"' in html
    assert "Meta 25.00 %" in html
    assert "Meta sin configurar" in html


def test_totales_grandes_de_moneda_ya_no_se_repiten():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_con_canales())
    assert "resumen-monedas-principales" not in html
    assert html.count('class="curr-code">Totales MXN') == 1
    assert html.count('class="curr-code">Totales USD') == 0


def test_tabla_de_canales_una_fila_por_canal_con_estado_y_configurar():
    contexto = _ctx_con_canales()
    html = ui.templates.env.get_template("resumen.html").render(**contexto)
    assert html.count("data-canal-fila=") == len(contexto["contrato"]["canales"])
    assert 'data-canal-fila="amazon_mx"' in html
    assert 'data-canal-fila="meli"' in html
    assert 'class="status-pill status-good"' in html
    assert "Bien" in html
    assert "sin configurar" in html
    assert 'href="/settings"' in html
    assert "ACoS en 25.00 % cumple la meta configurada (25.00 %)" in html


def test_frase_de_comparacion_sigue_en_la_pagina():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_con_canales())
    assert 'class="ch-comparacion mutado"' in html
    assert "en las ventas que todavía llegan" in html
    assert 'class="ch-madurez mutado"' in html


def test_ventana_se_lee_con_fechas_cortas_y_acentos():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_con_canales())
    assert "Ventana <strong>25 jul – 23 ago</strong>" in html
    assert "2026-07-25 .. 2026-08-23" not in html
    assert "días" in html
    assert "Maduración" in html
    assert "Órdenes atribuidas" in html


def test_cifras_grandes_no_se_parten_y_caben_en_su_recuadro():
    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "resumen.css").read_text(encoding="utf-8")
    valor = re.search(r"\.cifra-tile-val\s*\{([^}]+)\}", css)
    assert valor is not None
    assert "white-space: nowrap" in valor.group(1)
    assert "font-size: clamp(18px, 1.5vw, 22px)" in valor.group(1)
    assert ".cifra-tile-val { font-size: 18px; }" in css
    assert ".cifra-tile-val.larga { font-size: 15px; }" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css


def test_tabla_canales_no_parte_cifras_ni_variaciones():
    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "resumen.css").read_text(encoding="utf-8")
    th = re.search(r"\.tabla-canales th\s*\{([^}]+)\}", css)
    num = re.search(r"\.tabla-canales \.num\s*\{([^}]+)\}", css)
    assert th is not None and "white-space: nowrap" in th.group(1)
    assert num is not None and "white-space: nowrap" in num.group(1)
    assert ".tabla-canales .v-delta { white-space: nowrap; }" in css


def test_eje_de_la_grafica_no_rota_y_salta_etiquetas():
    js = (ui._TEMPLATES_DIR.parent / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "maxRotation: 0" in js
    assert "minRotation: 0" in js
    assert "autoSkip: true" in js


def test_eje_de_la_grafica_nunca_repite_etiquetas_por_redondeo():
    """Sin datos, con ROAS chico y con ventas grandes, cada marca del eje es
    distinta al formatearse (antes Chart.js daba 0.1, 0.2... y se leian 1,1)."""
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar los scripts JS")
        pytest.skip("Node no disponible")
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const bloque = src.slice(src.indexOf("function formatoDeEje"), src.indexOf("function tooltipCon"));
const numero = s => (s === null || s === undefined || s === "") ? null : Number(s);
const Orbit = { numero: (valor, decimales) => Number(valor).toFixed(decimales || 0) };
eval(bloque);
function etiquetas(valores, entero, clave) {
  const eje = pasoDeEje(valores, entero);
  const formato = formatoDeEje(clave, eje.decimales) || (v => String(v));
  const salida = [];
  for (let i = 0; i < 12; i++) salida.push(formato(i * eje.paso));
  return salida;
}
const casos = [
  ["sin datos", [null, null], false, "ad_revenue"],
  ["clics chicos", [1, 0, 1], true, "clicks"],
  ["clics grandes", [8287], true, "clicks"],
  ["roas 0 a 6", [0, 1.4, 2.8, 4.2, 6], false, "roas"],
  ["roas 0 a 0.8", [0, 0.2, 0.8], false, "roas"],
  ["ventas chicas", [0.04], false, "ad_revenue"],
  ["ventas grandes", [162961.91], false, "ad_revenue"],
  ["acos", [15.87], false, "acos"],
];
for (const [nombre, valores, entero, clave] of casos) {
  const marcas = etiquetas(valores, entero, clave);
  assert.equal(new Set(marcas).size, marcas.length, nombre + ": " + marcas.join(" | "));
}
"""
    resultado = subprocess.run(
        [node, "-e", guion, str(ui._TEMPLATES_DIR.parent / "static" / "js" / "dashboard.js")],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def test_barras_usan_el_lila_claro_en_cualquier_tamano():
    js = (ui._TEMPLATES_DIR.parent / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert 'colorCss("--resumen-ventas-dia", "#b9b5ff")' in js
    assert "backgroundColor: colorBarra" in js
    assert "borderColor: colorLinea" in js


def test_grafica_usa_acos_y_roas_del_contrato_cuando_vienen():
    js = (ui._TEMPLATES_DIR.parent / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "var directo = numero(fila[clave]);" in js
    assert "if (directo !== null) return directo;" in js
    assert "Si faltan" in js


def test_pendientes_compactos_en_celular_con_objetivos_de_44():
    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "dashboard.css").read_text(
        encoding="utf-8"
    )
    summary = re.search(r"\.resumen-pendientes \.action-desc summary\s*\{([^}]+)\}", css)
    enlace = re.search(r"\.resumen-pendientes \.action-link\s*\{([^}]+)\}", css)
    assert summary is not None and "min-height: 44px" in summary.group(1)
    assert "margin: -6px 0 -8px" in summary.group(1)
    assert enlace is not None and "min-height: 44px" in enlace.group(1)
    assert "margin-top: -8px" in css


def _lista_canales(html: str) -> str:
    encontrado = re.search(r'<ul class="canales-lista".*?</ul>', html, re.S)
    assert encontrado is not None
    return encontrado.group(0)


def _canal_con_dias_sin_venta(dias: int, gasto_por_dia: str) -> dict:
    canal = _canal("MXN", acos=None, orders=0)
    canal["series"] = [
        {
            "fecha": f"2026-10-{28 + i:02d}",
            "cost": gasto_por_dia,
            "ad_revenue": "0.0000",
            "clicks": 5,
            "orders": 0,
            "inmaduro": False,
            "ma7": {
                "cost": gasto_por_dia,
                "ad_revenue": "0.0000",
                "clicks": 5,
                "orders": 0,
            },
        }
        for i in range(dias)
    ]
    canal["totals"].update({"ad_revenue": "0.0000", "acos": None, "roas": None})
    canal["comparison"]["totals"].update({"ad_revenue": "0.0000", "acos": None, "roas": None})
    canal["comparison"]["variacion"].update({"ad_revenue_pct": "-3.40", "acos_pp": "2.20"})
    return canal


def _ctx_una_serie_sin_venta(dias: int, gasto_por_dia: str) -> dict:
    contexto = _ctx_resumen_con_payload()
    contexto["contrato"]["metas_acos"] = {}
    contexto["contrato"]["canales"] = {"amazon_mx": _canal_con_dias_sin_venta(dias, gasto_por_dia)}
    return contexto


def test_lista_de_celular_trae_tres_columnas_y_no_repite_frase_ni_roas():
    contexto = _ctx_con_canales()
    html = ui.templates.env.get_template("resumen.html").render(**contexto)
    lista = _lista_canales(html)
    assert lista.count('class="canal-cifras"') == len(contexto["contrato"]["canales"])
    assert lista.count('class="canal-cifra"') == 3 * len(contexto["contrato"]["canales"])
    for etiqueta in ("Ventas", "Gasto", "ACoS"):
        assert f'<span class="canal-cifra-lab">{etiqueta}</span>' in lista
    assert "ROAS" not in lista
    assert "Las ventas subieron +33.33%" not in lista
    assert "meta sin configurar" not in lista.lower()


def test_frase_y_renglon_de_gasto_sin_venta_concuerdan_en_singular_y_plural():
    uno = _ctx_una_serie_sin_venta(1, "690.3300")
    html_uno = ui.templates.env.get_template("resumen.html").render(**uno)
    assert "Hubo 1 día con gasto y cero ventas ($690.33 MXN)" in html_uno
    assert "Hubo 1 días" not in html_uno
    assert "1 día con gasto y sin ventas ($690.33 MXN)" in _lista_canales(html_uno)

    cuatro = _ctx_una_serie_sin_venta(4, "736.2675")
    html_cuatro = ui.templates.env.get_template("resumen.html").render(**cuatro)
    assert "Hubo 4 días con gasto y cero ventas ($2,945.07 MXN)" in html_cuatro
    assert "4 días con gasto y sin ventas ($2,945.07 MXN)" in _lista_canales(html_cuatro)


def test_sin_dias_con_gasto_sin_venta_no_hay_renglon():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_con_canales())
    lista = _lista_canales(html)
    assert "canal-aviso" not in lista
    assert "con gasto y sin ventas" not in lista


def test_un_solo_aviso_cuando_ningun_canal_tiene_meta():
    contexto = _ctx_con_canales()
    contexto["contrato"]["metas_acos"] = {}
    html = ui.templates.env.get_template("resumen.html").render(**contexto)
    assert html.count("Ningún canal tiene meta de ACoS.") == 1
    assert 'href="/settings">Configurar metas</a>' in html
    lista = _lista_canales(html)
    assert "Sin meta" not in lista


def test_canal_sin_meta_lo_dice_cuando_otros_si_tienen():
    contexto = _ctx_con_canales()
    html = ui.templates.env.get_template("resumen.html").render(**contexto)
    lista = _lista_canales(html)
    assert "Ningún canal tiene meta de ACoS." not in html
    tramo_meli = lista[lista.index('data-canal-lista="meli"') :]
    assert '<span class="canal-meta">Sin meta</span>' in tramo_meli
    assert '<span class="canal-meta">Meta 25.00 %</span>' in lista


def test_notas_de_canales_van_plegadas_con_comparacion_y_madurez():
    contexto = _ctx_con_canales()
    html = ui.templates.env.get_template("resumen.html").render(**contexto)
    inicio = html.index('<details class="canales-notas">')
    fin = html.index("</details>", inicio)
    notas = html[inicio:fin]
    assert "<summary>Cómo se calculan estas cifras</summary>" in notas
    assert 'class="ch-comparacion mutado"' in notas
    assert notas.count('class="ch-madurez mutado"') == len(contexto["contrato"]["canales"])
