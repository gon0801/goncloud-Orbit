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
    assert "flex: 0 0 150px" in css


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
