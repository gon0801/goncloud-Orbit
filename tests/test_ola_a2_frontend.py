"""Pruebas de regresion de Ola A2 (P0 Frontend: IIFE, cero innerHTML, sin float).

Fija:
- P0-9a: base.html no carga scripts de pantalla ni Chart.js en otras vistas; cada
  script vive aislado en su IIFE sin filtrar simbolos a window/globalThis.
- P0-9b: cero usos de innerHTML en app/static/js/*.js; precios.js construye tabla,
  resumen y formulario de Soltar con Orbit.el (sin XSS, formulario real).
- P0-6 (frontend): Orbit.fraccionPct desplaza decimales sin float (0.29 -> 29.00 %),
  dinero.js envia presupuesto como texto exacto (no Number).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from formato_js import con_formato

from app import ui

JS_DIR = Path(ui.__file__).resolve().parent / "static" / "js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar los scripts JS")
        pytest.skip("Node no disponible")
    return node


def test_cero_innerhtml_en_scripts_del_frontend():
    """P0-9b: ningun archivo en app/static/js/*.js debe usar .innerHTML."""
    violaciones = [
        p.name
        for p in sorted(JS_DIR.glob("*.js"))
        if re.search(r"\.innerHTML\b", p.read_text(encoding="utf-8"))
    ]
    assert violaciones == [], f"innerHTML prohibido en: {violaciones}"


def test_dashboard_js_sin_codigo_muerto_graficar_barras():
    """P0-9a: graficarBarras y renglones murieron al migrar /salud a barras CSS."""
    fuente = (JS_DIR / "dashboard.js").read_text(encoding="utf-8")
    assert "graficarBarras" not in fuente
    assert "renglones" not in fuente
    assert "datos-skips-" not in fuente


def test_base_html_no_carga_scripts_de_otras_pantallas():
    """P0-9a: chart.umd, dashboard.js, cortes.js y settings.js solo van en sus templates."""
    base = (Path(ui.__file__).resolve().parent / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    for prohibido in ("chart.umd.min.js", "dashboard.js", "cortes.js", "settings.js"):
        assert prohibido not in base
    resumen = (Path(ui.__file__).resolve().parent / "templates" / "resumen.html").read_text(
        encoding="utf-8"
    )
    assert "chart.umd.min.js" in resumen and "dashboard.js" in resumen
    cortes = (Path(ui.__file__).resolve().parent / "templates" / "cortes.html").read_text(
        encoding="utf-8"
    )
    assert "cortes.js" in cortes
    settings = (Path(ui.__file__).resolve().parent / "templates" / "settings.html").read_text(
        encoding="utf-8"
    )
    assert "settings.js" in settings
    salud = (Path(ui.__file__).resolve().parent / "templates" / "salud.html").read_text(
        encoding="utf-8"
    )
    assert 'id="datos-skips-' in salud


def test_scripts_aislados_en_iife_sin_colisiones_en_window():
    """P0-9a: cargar todos los scripts de pagina no filtra simbolos globales."""
    node = _node()
    archivos = [
        str(JS_DIR / nombre)
        for nombre in (
            "shell.js",
            "dashboard.js",
            "cortes.js",
            "settings.js",
            "dinero.js",
            "danadas.js",
            "familias.js",
            "precios.js",
            "productos.js",
            "productos_pausa.js",
            "fabrica_vistas.js",
            "fabrica_lotes.js",
            "fabrica.js",
        )
    ]
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
global.window = global;
global.document = {
  addEventListener() {},
  getElementById() { return null; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  documentElement: { dataset: {} },
};
const antes = new Set(Object.getOwnPropertyNames(global));
for (const ruta of process.argv.slice(1)) {
  vm.runInThisContext(fs.readFileSync(ruta, "utf8"));
}
const filtrados = Object.getOwnPropertyNames(global).filter(k => !antes.has(k));
assert.deepEqual(
  filtrados,
  [],
  "ningun script debe filtrar simbolos globales: " + filtrados.join(", "),
);
"""
    r = subprocess.run(
        [node, "-e", con_formato(guion), *archivos],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_orbit_fraccion_pct_y_precios_js_sin_innerhtml_ni_float():
    """P0-6 + P0-9b: Orbit.fraccionPct evita deriva float y precios.js usa DOM seguro."""
    node = _node()
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

assert.equal(Orbit.fraccionPct("0.29"), "29.00 %");
assert.equal(Orbit.fraccionPct("0.14"), "14.00 %");
assert.equal(Orbit.fraccionPct("0.004"), "0.40 %");
assert.equal(Orbit.fraccionPct("-0.1234"), "-12.34 %");
assert.equal(Orbit.fraccionPct("1"), "100.00 %");
assert.equal(Orbit.fraccionPct("0.00"), "0.00 %");
assert.equal(Orbit.fraccionPct(null), "—");

class Nodo {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attrs = {};
    this.children = [];
    this.events = {};
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.parentNode = null;
    this.nextSibling = null;
  }
  get firstChild() { return this.children[0] || null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) {
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  insertBefore(n, ref) {
    n.parentNode = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(n);
    else this.children.splice(i, 0, n);
    return n;
  }
  replaceChildren(...items) {
    this.children = [];
    items.forEach(c => this.appendChild(c));
  }
  remove() {
    if (!this.parentNode) return;
    const i = this.parentNode.children.indexOf(this);
    if (i >= 0) this.parentNode.children.splice(i, 1);
  }
  addEventListener(e, fn) { this.events[e] = fn; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    const visita = n => {
      for (const c of n.children || []) {
        if (coincide(c, sel)) out.push(c);
        visita(c);
      }
    };
    visita(this);
    return out;
  }
}
function coincide(n, sel) {
  if (!n.tagName) return false;
  if (sel === "[data-confirmar]:checked") return "data-confirmar" in n.attrs && n.checked;
  if (sel === "[data-liberar-form]") return "data-liberar-form" in n.attrs;
  if (sel === "[data-recargar]") return "data-recargar" in n.attrs;
  if (sel === "form") return n.tagName === "FORM";
  if (sel === "input") return n.tagName === "INPUT";
  return false;
}
function textoTotal(n) {
  if (!n.children) return n.nodeValue || "";
  return n.children.map(textoTotal).join("");
}

const vista = new Nodo("div");
const estado = new Nodo("span");
const aplicarBtn = new Nodo("button");
aplicarBtn.disabled = true;
const contenedorForm = new Nodo("section");
const form = new Nodo("form");
form.setAttribute("data-platform", "amazon_mx");
form.elements = {
  token: {value: "tok-sec"},
  pedido: {value: "valor"},
  mode: {value: "shadow"},
  goal_pct: {value: "29.00"},
  sin_goal: {checked: true},
  actor: {value: "david"},
  go_literal: {value: ""},
};
form.querySelector = sel =>
  sel === "[data-estado]" ? estado : sel === "[data-aplicar]" ? aplicarBtn : null;
form.closest = () => contenedorForm;
contenedorForm.appendChild(form);
contenedorForm.querySelector = sel => sel === "[data-vista]" ? vista : null;

const cajaSoltar = new Nodo("div");
const btnSoltar = new Nodo("button");
btnSoltar.setAttribute("data-liberar-corrida", "44");
cajaSoltar.appendChild(btnSoltar);

const docEvents = {};
global.window = { location: { reload() {} } };
global.document = {
  createElement: tag => new Nodo(tag),
  createTextNode: text => ({nodeValue: String(text)}),
  addEventListener: (e, fn) => { docEvents[e] = fn; },
  querySelectorAll(sel) {
    if (sel === "form[data-precios-bar]") return [form];
    if (sel === "[data-liberar-corrida]") return [btnSoltar];
    return [];
  },
};
const xss = "<img src=x onerror='alert(1)'>";
const calls = [];
global.fetch = async (url, opts) => {
  calls.push({url, opts});
  if (url === "/api/precios/goals/plan") {
    return {
      ok: true, status: 200,
      json: async () => ({
        huella: xss,
        filas: [{
          listing_id: 7, seller_sku: xss, accion: "poner", m_hoy: "0.29",
          goal_actual: null, goal_propuesto: "0.29",
          p_estrella: {valor: "199.50", moneda: "MXN"},
          bloqueo: "sin_costo", motivo: null, confirmable: true,
        }],
      }),
    };
  }
  if (url === "/api/precios/goals/aplicar") {
    return {
      ok: true, status: 200,
      json: async () => ({sembrados: 1, cerrados: 0, lote: xss, hechos: []}),
    };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
(async () => {
  docEvents.DOMContentLoaded();
  const tick = async () => {
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
  };
  form.events.submit({preventDefault() {}});
  await tick();
  assert.equal(aplicarBtn.disabled, false);
  assert.equal(vista.innerHTML, undefined, "pintarVista nunca toca innerHTML");
  const txt = textoTotal(vista);
  assert.ok(txt.includes("29.00 %"), "fraccion exacta en tabla: " + txt);
  assert.ok(txt.includes(xss), "XSS queda en TextNode literal");

  aplicarBtn.events.click();
  await tick();
  assert.equal(vista.innerHTML, undefined, "aplicar nunca toca innerHTML");
  assert.ok(vista.children[0].tagName === "P", "resumen prepended al inicio");

  btnSoltar.events.click();
  const libForm = cajaSoltar.querySelector("[data-liberar-form]");
  assert.ok(libForm && libForm.tagName === "FORM", "cablearSoltar crea un <form> real");
  assert.equal(libForm.innerHTML, undefined);
  const inputsLib = libForm.querySelectorAll("input");
  const porNombre = Object.fromEntries(inputsLib.map(i => [i.getAttribute("name"), i]));
  porNombre.actor.value = "david";
  porNombre.nota.value = "destrabar prueba";
  porNombre.token.value = "tok-soltar";
  libForm.events.submit({preventDefault() {}});
  await tick();
  const callLib = calls.find(c => c.url === "/api/precios/compuerta/44/liberar");
  assert.ok(callLib, "submit de Soltar envia POST /liberar");
  assert.equal(callLib.opts.headers["x-orbit-token"], "tok-soltar");
  assert.deepEqual(JSON.parse(callLib.opts.body), {actor: "david", nota: "destrabar prueba"});
})().catch(err => { console.error(err); process.exitCode = 1; });
"""
    r = subprocess.run(
        [node, "-e", con_formato(guion), str(JS_DIR / "precios.js")],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_ola_g1_orbit_http_error_texto_sin_fetch_manual_y_css_dividido():
    """Ola G1 (S9): Orbit.getJson/postJson/errorTexto/fechaCorta, cero fetch
    manual fuera de formato.js, contribucion SVG sin 200 clases CSS y modo noche."""
    node = _node()
    # Solo formato.js puede llamar fetch() directamente.
    con_fetch = [
        p.name
        for p in sorted(JS_DIR.glob("*.js"))
        if p.name != "formato.js" and re.search(r"\bfetch\s*\(", p.read_text(encoding="utf-8"))
    ]
    assert con_fetch == [], f"fetch() directo fuera de formato.js: {con_fetch}"

    css_dir = JS_DIR.parent / "css"
    contrib_css = (css_dir / "contribucion.css").read_text(encoding="utf-8")
    decisiones_css = (css_dir / "decisiones.css").read_text(encoding="utf-8")
    assert ".contribucion-izq-0" not in contrib_css
    assert ".contribucion-ancho-0" not in contrib_css
    assert 'html[data-tema="noche"]' in contrib_css
    assert 'html[data-tema="noche"]' in decisiones_css
    assert (css_dir / "resumen.css").exists()
    assert (css_dir / "salud.css").exists()

    guion = r"""
const assert = require("node:assert/strict");
assert.equal(Orbit.fechaCorta("2026-09-08"), "8 sep");
assert.equal(
  Orbit.errorTexto({detail: [{loc: ["body", "dias"], msg: "entero >= 1"}]}, 422, {dias: "Días"}),
  "Días: entero >= 1",
);
assert.equal(Orbit.errorTexto({detail: {message: "conflicto"}}, 409), "conflicto");
assert.equal(Orbit.errorTexto({detail: {mensaje: "fallo lote"}}, 500), "fallo lote");
assert.equal(Orbit.errorTexto({detail: "token invalido"}, 401), "token invalido");
assert.equal(Orbit.errorTexto({}, 502), "HTTP 502");

global.fetch = async () => ({
  ok: false,
  status: 502,
  json: async () => { throw new SyntaxError("Unexpected token <"); },
});
(async () => {
  const r = await Orbit.postJson("/api/test", {a: 1}, "tok");
  assert.deepEqual(r, {ok: false, status: 502, data: {}, jsonOk: false});
  const g = await Orbit.getJson("/api/test");
  assert.deepEqual(g, {ok: false, status: 502, data: {}, jsonOk: false});
})().catch(err => { console.error(err); process.exitCode = 1; });
"""
    r = subprocess.run(
        [node, "-e", con_formato(guion)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert r.returncode == 0, r.stdout + r.stderr
