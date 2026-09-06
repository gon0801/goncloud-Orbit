"""Pantalla de fabrica: entrada, seguridad y contrato del formulario sin Amazon."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ui
from app.api import _conexion_lectura
from app.main import app

RAIZ = Path(ui.__file__).resolve().parent


class Elementos(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elementos = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elementos.append((tag, dict(attrs)))


def test_entrada_crear_desde_campanas(monkeypatch):
    monkeypatch.setattr(ui.dash, "campanas", lambda **kwargs: {"items": []})
    app.dependency_overrides[_conexion_lectura] = lambda: None
    try:
        respuesta = TestClient(app).get("/campanas")
    finally:
        app.dependency_overrides.pop(_conexion_lectura)
    assert respuesta.status_code == 200
    assert re.search(r'href="/campanas/nuevas"[^>]*>Crear campañas</a>', respuesta.text)


def test_pantalla_sin_db_con_semantica_de_gasto_y_activos_locales():
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    assert 'data-pantalla="fabrica"' in respuesta.text
    assert "ACTIVAS" in respuesta.text and "pueden gastar" in respuesta.text
    assert "shadow" in respuesta.text and "live" in respuesta.text
    assert "CREAR 5 CAMPAÑAS" in respuesta.text
    assert "default-src 'self'" in respuesta.headers["content-security-policy"]
    assert respuesta.headers["cache-control"] == "no-store"
    assert 'src="/static/js/fabrica.js"' in respuesta.text
    assert 'href="/static/css/fabrica.css"' in respuesta.text
    for tag, attrs in Elementos(respuesta.text).elementos:
        assert not any(nombre.startswith("on") for nombre in attrs)
        assert "style" not in attrs
        if tag == "script":
            assert attrs.get("src", "").startswith("/static/")


def test_dinero_vacio_y_token_sin_nombre_para_no_enviarlo_por_formulario():
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    elementos = Elementos(respuesta.text).elementos
    dinero = [a for t, a in elementos if t == "input" and a.get("inputmode") == "decimal"]
    assert len(dinero) == 10
    assert all(a.get("value", "") == "" and "required" in a for a in dinero)
    token = [a for t, a in elementos if a.get("id") == "fabrica-token"]
    assert len(token) == 1
    assert token[0]["type"] == "password"
    assert token[0]["autocomplete"] == "off"
    assert "name" not in token[0]
    for literal in ("PAUSAR GRUPO", "RECONCILIAR GRUPO", "REGISTRAR GRUPO"):
        assert literal in respuesta.text


def test_js_datos_externos_sin_html_y_token_sin_persistencia():
    archivo = RAIZ / "static/js/fabrica.js"
    assert archivo.exists(), "Falta el cliente de fabrica"
    codigo = archivo.read_text()
    assert "textContent" in codigo
    assert "innerHTML" not in codigo
    assert "insertAdjacentHTML" not in codigo
    assert "localStorage" not in codigo and "sessionStorage" not in codigo
    assert '"x-orbit-token"' in codigo


def test_hash_del_lote_envuelve_en_movil_sin_quitar_scroll_de_tablas():
    css = (RAIZ / "static/css/fabrica.css").read_text()
    assert re.search(
        r"#fabrica-lote-id,\s*\.fabrica #fabrica-historial a\s*\{\s*"
        r"overflow-wrap:\s*anywhere;",
        css,
    )
    assert re.search(r"\.fabrica-tabla\s*\{\s*overflow-x:\s*auto;", css)


def test_flujo_js_invalida_plan_bloquea_duplicados_y_recupera_lote():
    """Ejecuta JS real con DOM y transporte simulados; no requiere npm ni Amazon."""
    archivo = RAIZ / "static/js/fabrica.js"
    assert archivo.exists(), "Falta el cliente de fabrica"
    node = shutil.which("node")
    if not node:
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    html = TestClient(app).get("/campanas/nuevas").text
    elementos = [attrs for _, attrs in Elementos(html).elementos if "id" in attrs]
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(attrs = {}) {
    Object.assign(this, attrs);
    this.children = []; this.events = {}; this.value = attrs.value || "";
    this.disabled = "disabled" in attrs; this.hidden = "hidden" in attrs;
    this.checked = false; this.textContent = ""; this.dataset = {};
  }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.children = items; this.textContent = ""; }
  addEventListener(event, fn) { this.events[event] = fn; }
  setAttribute(name, value) { this[name] = value; }
  focus() {}
  reportValidity() { return true; }
  querySelectorAll(selector) {
    const all = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
    if (selector === "*") return all;
    return all.filter(child => child.type === "checkbox" &&
      (!selector.includes(":checked") || child.checked));
  }
}
const attrs = JSON.parse(process.argv[1]);
const ids = Object.fromEntries(attrs.map(a => [a.id, new Element(a)]));
const el = id => ids["fabrica-" + id];
el("plataforma").value = "amazon_mx";
el("plan").elements = Object.fromEntries(attrs.filter(a => a.name).map(a => [a.name, ids[a.id]]));
const docEvents = {}, windowEvents = {};
global.document = {
  getElementById: id => ids[id],
  createElement: () => new Element(),
  addEventListener: (event, fn) => { docEvents[event] = fn; },
};
global.window = {
  location: {href: "http://orbit.test/campanas/nuevas", search: ""},
  addEventListener: (event, fn) => { windowEvents[event] = fn; },
  history: { replaceState: (_state, _title, url) => {
    window.location.href = String(url); window.location.search = new URL(url).search;
  } },
};
const calls = [];
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [],
  productos: [{id: 1, sku: "GORRA <img src=x>", margen_neto_pct: "40.0000000", elegible: true},
    {id: 2, sku: "SIN MARGEN", margen_neto_pct: null, elegible: false, motivo: "Sin margen"}]};
const roles = ["category_exact", "category_phrase", "category_broad",
  "product_targeting", "auto_discovery"];
const plan = {huella: "abc", lote: "web-abc", presupuesto_diario_total: "600.00", existentes: [],
  campanas: roles.map(rol => ({rol, nombre: rol, budget: "120.00", bid: "4.00"})),
  plan: {platform: "amazon_mx", moneda: "MXN", modo: "shadow", fecha: "2026-09-06",
    nombre_base: "Gorras", tipo_producto: "gorras", target_acos_pct: "20",
    target_derivado_pct: "20",
    target_procedencia: "margen_producto", fraccion: "0.5", productos: [
      {product_id: 1, odoo_sku: "GORRA <img src=x>", seller_sku: "GORRA",
        asin: "B01", margen_neto_pct: "40"}],
    semillas: {exact: ["gorra"], keywords: [], asins: [], negativos: []}}};
const lote = {lote: "web-abc", plataforma: "amazon_mx", estado: "failed",
  detalle: "Error recuperable",
  created_at: "2026-09-06", finished_at: null, plan: plan.plan, pasos: []};
let crearPendiente, planPendiente, demorarPlan = false;
let pausarPendiente, detallePendiente, demorarDetalle = false;
let rechazarToken = true;
const ok = body => ({ok: true, status: 200, json: async () => body});
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return ok(catalogo);
  if (url.includes("/lotes?")) return ok({items: []});
  if (url.endsWith("/plan")) {
    if (demorarPlan) return new Promise(resolve => { planPendiente = () => resolve(ok(plan)); });
    return ok(plan);
  }
  if (url.endsWith("/crear")) {
    if (rechazarToken) {
      rechazarToken = false;
      return {ok: false, status: 401, json: async () => ({detail: "Token incorrecto"})};
    }
    return new Promise((_resolve, reject) => { crearPendiente = reject; });
  }
  if (url.endsWith("/pausar")) {
    return new Promise(resolve => { pausarPendiente = resolve; });
  }
  if (demorarDetalle && url.endsWith("/lotes/web-abc")) {
    return new Promise(resolve => { detallePendiente = resolve; });
  }
  return ok(lote);
};
const emit = async (id, event = "submit") => {
  const handler = el(id).events[event]; assert.ok(handler, `Falta evento ${id}:${event}`);
  handler({preventDefault() {}, target: el(id)});
  await new Promise(resolve => setImmediate(resolve));
};
const text = node => node.textContent + node.children.map(text).join(" ");
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  const productos = el("productos").querySelectorAll('input[type="checkbox"]');
  assert.equal(el("tipos").children.length, 0);
  assert.equal(el("tipo").disabled, false, "El primer tipo debe poder escribirse sin biblioteca");
  assert.equal(productos.length, 2); assert.equal(productos[1].disabled, true);
  assert.match(text(el("productos")), /Sin margen/);
  assert.match(text(el("productos")), /40 %/);
  assert.ok(!text(el("productos")).includes("40.0000000"));
  productos[0].checked = true;
  el("tipo").value = "gorras"; el("nombre").value = "Gorras"; el("modo").value = "shadow";
  for (const rol of roles) {
    ids[rol + "-budget"].value = "120.00"; ids[rol + "-bid"].value = "4.00";
  }
  await emit("plan");
  assert.equal(el("preview").hidden, false); assert.equal(el("crear-boton").disabled, false);
  const solicitud = JSON.parse(calls.find(c => c.url.endsWith("/plan")).options.body);
  assert.equal(solicitud.parametros.category_exact.budget, "120.00");
  assert.deepEqual(solicitud.productos, [1]); assert.equal(solicitud.modo, "shadow");
  assert.match(text(el("preview-datos")), /600.00 MXN/);
  assert.match(text(el("preview-datos")), /GORRA <img src=x>/);
  await emit("plan", "input");
  assert.equal(el("preview").hidden, true); assert.equal(el("crear-boton").disabled, true);
  demorarPlan = true;
  await emit("plan"); await emit("plan", "input"); planPendiente();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(el("preview").hidden, true, "Una respuesta vieja no valida datos nuevos");
  demorarPlan = false; await emit("plan");
  el("token").value = "secreto-solo-header"; el("confirmacion").value = "incorrecta";
  await emit("crear"); assert.equal(calls.filter(c => c.url.endsWith("/crear")).length, 0);
  el("confirmacion").value = "CREAR 5 CAMPAÑAS";
  await emit("crear");
  assert.equal(calls.filter(c => c.url.endsWith("/crear")).length, 1);
  assert.equal(el("crear-boton").disabled, false, "Un 401 permite corregir el token");
  assert.equal(el("preview").hidden, false, "El 401 conserva el plan revisado");
  el("token").value = "secreto-solo-header";
  el("confirmacion").value = "CREAR 5 CAMPAÑAS";
  await emit("crear"); await emit("crear");
  assert.equal(calls.filter(c => c.url.endsWith("/crear")).length, 2);
  const crear = calls.filter(c => c.url.endsWith("/crear")).at(-1);
  assert.equal(crear.options.headers["x-orbit-token"], "secreto-solo-header");
  assert.ok(!crear.options.body.includes("secreto-solo-header"));
  assert.equal(el("token").value, "");
  assert.equal(new URL(window.location.href).searchParams.get("lote"), "web-abc");
  assert.equal(el("lote-recargar").disabled, false, "GET disponible durante la creacion");
  await emit("lote-recargar", "click");
  assert.ok(calls.some(c => c.url.endsWith("/lotes/web-abc") && c.options.method === "GET"));
  assert.equal(el("recuperacion").disabled, true, "El GET no habilita mutaciones concurrentes");
  crearPendiente(new Error("conexion perdida"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(el("crear-boton").disabled, true);
  await emit("lote-recargar", "click");
  assert.match(text(el("lote-datos")), /Error recuperable/);
  assert.match(text(el("lote-datos")), /Interrumpido/);
  assert.match(text(el("lote-datos")), /Verificación de creación/);
  el("token").value = "otro-token"; el("accion-tipo").value = "pausar";
  el("accion-confirmacion").value = "PAUSAR GRUPO";
  await emit("accion");
  assert.equal(calls.filter(c => c.url.endsWith("/pausar")).length, 1);
  demorarDetalle = true;
  await emit("lote-recargar", "click");
  assert.ok(detallePendiente, "El GET comenzo durante la pausa");
  pausarPendiente(ok({...lote, estado: "desarmado", detalle: "Pausa final"}));
  await new Promise(resolve => setImmediate(resolve));
  assert.match(text(el("lote-datos")), /Pausado/);
  detallePendiente(ok(lote));
  await new Promise(resolve => setImmediate(resolve));
  assert.match(text(el("lote-datos")), /Pausado/, "El GET viejo no pisa la pausa final");
  assert.match(text(el("lote-datos")), /Pausa final/);
  assert.equal(calls.filter(c => c.url.endsWith("/crear")).length, 2);
  for (const call of calls) assert.ok(!call.url.includes("token"));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    resultado = subprocess.run(
        [node, "-e", guion, json.dumps(elementos), str(archivo)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
