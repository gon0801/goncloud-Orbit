"""Pantalla de fabrica: entrada, seguridad y contrato del formulario sin Amazon."""

from __future__ import annotations

import json
import os
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


def test_flujo_js_no_se_omite_en_ci_sin_node(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    try:
        with pytest.raises(pytest.fail.Exception, match="Node"):
            test_flujo_js_invalida_plan_bloquea_duplicados_y_recupera_lote()
    except pytest.skip.Exception:
        pytest.fail("CI no debe omitir la prueba JavaScript si falta Node")


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
    assert 'src="/static/js/fabrica.js?v=' in respuesta.text
    assert 'href="/static/css/fabrica.css?v=' in respuesta.text
    for tag, attrs in Elementos(respuesta.text).elementos:
        assert not any(nombre.startswith("on") for nombre in attrs)
        assert "style" not in attrs
        if tag == "script":
            assert attrs.get("src", "").startswith("/static/")


def test_selector_v2_exige_objetivo_y_envia_listings_no_productos():
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    elementos = Elementos(respuesta.text).elementos
    ids = {atributos.get("id") for _, atributos in elementos}
    assert {"fabrica-objetivo-origen", "fabrica-objetivo-acos"} <= ids
    codigo = (RAIZ / "static/js/fabrica.js").read_text()
    assert "listing_ids" in codigo
    assert "objetivo: objetivo" in codigo
    assert "productos:" not in codigo


def test_dinero_vacio_y_token_sin_nombre_para_no_enviarlo_por_formulario():
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    elementos = Elementos(respuesta.text).elementos
    dinero = [
        a
        for t, a in elementos
        if t == "input" and a.get("inputmode") == "decimal" and a.get("name")
    ]
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


def test_selector_publicaciones_se_adapta_a_movil():
    css = (RAIZ / "static/css/fabrica.css").read_text()
    assert "minmax(min(100%, 300px), 1fr)" in css
    assert re.search(
        r"@media \(max-width: 40rem\) \{[\s\S]*?\.fabrica-publicacion-foto \{\s*"
        r"width: 56px; height: 56px;",
        css,
    )


def test_flujo_js_invalida_plan_bloquea_duplicados_y_recupera_lote():
    """Ejecuta JS real con DOM y transporte simulados; no requiere npm ni Amazon."""
    archivo = RAIZ / "static/js/fabrica.js"
    assert archivo.exists(), "Falta el cliente de fabrica"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
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
  createElement: tag => new Element({tagName: tag}),
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
  productos: [{id: 1, sku: "GORRA <img src=x>", nombre: "Nombre interno <img src=x>",
    publicaciones: [{id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A",
      platform: "amazon_mx", margen_neto_pct: "40.0000000", dias_con_venta: 70,
      ventana_desde: "2026-02-20", ventana_hasta: "2026-08-22", historial_ads: null,
      elegible: true, motivos: [], url: "https://www.amazon.com.mx/dp/B0AAAAAAAA"}]},
    {id: 2, sku: "SIN MARGEN", nombre: null, publicaciones: [
      {id: 12, asin: "B0BBBBBBBB", seller_sku: "SKU-AMAZON-B", platform: "amazon_mx",
        margen_neto_pct: null, dias_con_venta: 12, ventana_desde: "2026-02-20",
        ventana_hasta: "2026-08-22", historial_ads: null, elegible: true,
        motivos: ["Margen sin medir."],
        url: "https://www.amazon.com.mx/dp/B0BBBBBBBB"},
      {id: 13, asin: "B0CCCCCCCC", seller_sku: null, platform: "amazon_mx",
        margen_neto_pct: null, historial_ads: null, elegible: false,
        motivos: ["SKU de Amazon ausente."],
        url: "javascript:alert(1)"}]},
    {id: 3, sku: "MARGEN DESCONOCIDO", nombre: null, publicaciones: [
      {id: 14, asin: "B0DDDDDDDD", seller_sku: "SKU-AMAZON-D", platform: "amazon_mx",
        margen_neto_pct: null, dias_con_venta: 70, ventana_desde: "2026-02-20",
        ventana_hasta: "2026-08-22", historial_ads: null, elegible: true,
        motivos: ["Margen sin medir."],
        url: "https://www.amazon.com.mx/dp/B0DDDDDDDD"}]}]};
const roles = ["category_exact", "category_phrase", "category_broad",
  "product_targeting", "auto_discovery"];
const plan = {huella: "abc", lote: "web-abc", presupuesto_diario_total: "600.00", existentes: [],
  campanas: roles.map(rol => ({rol, nombre: rol, budget: "120.00", bid: "4.00"})),
  plan: {schema_version: 2, platform: "amazon_mx", moneda: "MXN", modo: "shadow",
    fecha: "2026-09-06",
    nombre_base: "Gorras", tipo_producto: "gorras", objetivo: {origen: "manual_lanzamiento",
      acos_pct: "25.00", procedencia: "confirmado", fraccion: null, derivado: null},
    publicaciones: [
      {listing_id: 11, product_id: 1, seller_sku: "SKU-AMAZON-A", asin: "B0AAAAAAAA",
        margen_neto_pct: null, motivos: ["Margen sin medir."]}],
    semillas: {exact: ["gorra"], keywords: [], asins: [], negativos: []}}};
const lote = {lote: "web-abc", plataforma: "amazon_mx", estado: "failed",
  detalle: "Error recuperable",
  created_at: "2026-09-06", finished_at: null, plan: plan.plan, pasos: []};
const evaluacionComparador = {plataforma: "amazon_mx",
  ventana_ads: {desde: "2026-08-06", hasta: "2026-09-05"},
  orden: "margen_observado", direccion: "desc", publicaciones: []};
let crearPendiente, planPendiente, demorarPlan = false;
let pausarPendiente, detallePendiente, demorarDetalle = false;
let rechazarToken = true;
const ok = body => ({ok: true, status: 200, json: async () => body});
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return ok(catalogo);
  if (url.includes("/evaluacion")) return ok(evaluacionComparador);
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
  assert.equal(productos.length, 4); assert.equal(productos[1].disabled, false);
  assert.equal(productos[2].disabled, true);
  assert.match(text(el("productos")), /Margen sin medir/);
  assert.match(text(el("productos")), /Nombre interno <img src=x>/);
  assert.match(text(el("productos")), /SKU de Odoo: GORRA <img src=x>/);
  assert.match(text(el("productos")), /SKU de Amazon: SKU-AMAZON-A/);
  assert.match(text(el("productos")), /Nombre interno: Sin dato/);
  assert.match(text(el("productos")), /SKU de Amazon: Sin dato/);
  assert.match(text(el("productos")), /ASIN: B0CCCCCCCC/);
  assert.match(text(el("catalogo-estado")), /0 publicaciones seleccionadas de 3 seleccionables/);
  const tarjetas = el("productos").querySelectorAll("*").filter(e => e.tagName === "label");
  assert.ok(tarjetas.every(t => t.querySelectorAll("*").some(e => e.src)),
    "Cada tarjeta seleccionable incluye su foto dentro del label");
  const fotos = el("productos").querySelectorAll("*").filter(e => e.src);
  assert.deepEqual(fotos.map(e => e.src),
    [11, 12, 13, 14].map(id => `/api/fabrica/publicaciones/${id}/imagen`));
  assert.ok(fotos.every(e => e.loading === "lazy" && e.width === 72 &&
    e.alt.includes("publicación")));
  fotos[0].events.error();
  assert.equal(fotos[0].hidden, true);
  const cajas = el("productos").querySelectorAll("*")
    .filter(e => e.className === "fabrica-publicacion-foto");
  assert.equal(cajas[0].children[1].hidden, false, "Foto fallida muestra Sin foto");
  assert.equal(productos[0].checked, false, "El fallo de foto no selecciona el producto");
  assert.equal(productos[0].disabled, false, "El fallo de foto no cambia elegibilidad");
  const enlaces = el("productos").querySelectorAll("*").filter(e => e.href);
  assert.deepEqual(enlaces.map(e => e.href), [
    "https://www.amazon.com.mx/dp/B0AAAAAAAA", "https://www.amazon.com.mx/dp/B0BBBBBBBB",
    "https://www.amazon.com.mx/dp/B0DDDDDDDD"]);
  assert.ok(enlaces.every(e => e.target === "_blank" && e.rel.includes("noopener")));
  assert.equal(el("productos").querySelectorAll("*").filter(e => e.htmlFor).length, 4);
  const labels = el("productos").querySelectorAll("*").filter(e => e.htmlFor);
  assert.ok(labels.every(label => !label.querySelectorAll("*").some(e => e.href)),
    "Abrir Amazon no debe seleccionar un producto: los enlaces van fuera del label");
  assert.match(text(el("productos")), /40 %/);
  assert.match(text(el("productos")), /Muestra de margen: 70 dias con venta/);
  assert.match(text(el("productos")), /Muestra limitada: 12 dias con venta/);
  assert.equal(
    (text(el("productos")).match(/Muestra de margen: 70 dias con venta/g) || []).length,
    2,
    "Un margen sin medir con 70 dias conserva muestra de margen normal",
  );
  assert.ok(!text(el("productos")).includes("40.0000000"));
  productos[0].checked = true;
  await emit("productos", "change");
  assert.match(text(el("catalogo-estado")), /1 publicación seleccionada de 3 seleccionables/);
  el("tipo").value = "gorras"; el("nombre").value = "Gorras"; el("modo").value = "shadow";
  el("objetivo-origen").value = "manual_lanzamiento";
  await emit("objetivo-origen", "change");
  el("objetivo-acos").value = "25.00";
  for (const rol of roles) {
    ids[rol + "-budget"].value = "120.00"; ids[rol + "-bid"].value = "4.00";
  }
  await emit("plan", "input");
  assert.equal(el("presupuesto-total").textContent, "600.00 MXN");
  ids[roles[0] + "-budget"].value = "";
  await emit("plan", "input");
  assert.match(el("presupuesto-total").textContent, /Completa/);
  ids[roles[0] + "-budget"].value = "120.00";
  await emit("plan");
  assert.equal(el("preview").hidden, false); assert.equal(el("crear-boton").disabled, false);
  const solicitud = JSON.parse(calls.find(c => c.url.endsWith("/plan")).options.body);
  assert.equal(solicitud.parametros.category_exact.budget, "120.00");
  assert.deepEqual(solicitud.listing_ids, [11]); assert.deepEqual(solicitud.objetivo,
    {origen: "manual_lanzamiento", acos_pct: "25.00"}); assert.equal(solicitud.modo, "shadow");
  assert.match(text(el("preview-datos")), /600.00 MXN/);
  assert.match(text(el("preview-datos")), /SKU-AMAZON-A/);
  assert.match(text(el("preview-datos")), /manual_lanzamiento/);
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


def test_comparador_renderiza_controles_de_orden_filtro_y_sin_por_probar():
    """B.5: la seccion comparador existe con las 8 metricas de orden (§7 D1),
    direccion, filtro, estado vivo y encabezado etiquetado. La etiqueta
    'Por probar' no existe en la UI (regla cerrada 0.4 §4.2)."""
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    m = re.search(
        r'<section id="fabrica-comparador"[^>]*aria-labelledby="fabrica-comparador-titulo"',
        respuesta.text,
    )
    assert m, "Falta la seccion comparador con encabezado etiquetado"
    for orden in (
        "margen_observado",
        "ventas_totales",
        "revenue_ads",
        "gasto",
        "acos",
        "cpc",
        "cvr",
        "compras",
    ):
        assert f'<option value="{orden}">' in respuesta.text
    for control in (
        "fabrica-comparador-orden",
        "fabrica-comparador-direccion",
        "fabrica-comparador-filtro",
        "fabrica-comparador-recargar",
        "fabrica-comparador-estado",
        "fabrica-comparador-datos",
    ):
        assert f'id="{control}"' in respuesta.text
    js = (RAIZ / "static/js/fabrica.js").read_text()
    assert "Por probar" not in js
    assert "por_probar" not in js


def test_flujo_js_comparador_etiquetas_orden_filtro_y_cero_escrituras():
    """Comparador (B.5) con JS real: estados honestos (error/vacio/exito),
    etiquetas por precedencia 0.4 seccion 4, muestra limitada aparte (D4),
    disponibilidad tres estados (AC8), orden/filtros sin perder la
    seleccion (AC10) y cero escrituras (0 POST)."""
    archivo = RAIZ / "static/js/fabrica.js"
    assert archivo.exists(), "Falta el cliente de fabrica"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
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
// El navegador preselecciona la primera opcion de cada select; el mock no.
el("comparador-orden").value = "margen_observado";
el("comparador-direccion").value = "desc";
el("comparador-filtro").value = "todas";
el("plan").elements = Object.fromEntries(attrs.filter(a => a.name).map(a => [a.name, ids[a.id]]));
const docEvents = {};
global.document = {
  getElementById: id => ids[id],
  createElement: tag => new Element({tagName: tag}),
  addEventListener: (event, fn) => { docEvents[event] = fn; },
};
global.window = {
  location: {href: "http://orbit.test/campanas/nuevas", search: ""},
  addEventListener: () => {},
  history: { replaceState: () => {} },
};
const calls = [];
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [],
  productos: [{id: 1, sku: "GORRA", nombre: "Gorras", publicaciones: [
    {id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A", platform: "amazon_mx",
      margen_neto_pct: "40", dias_con_venta: 70, ventana_desde: "2026-02-20",
      ventana_hasta: "2026-08-22", historial_ads: null, elegible: true, motivos: [],
      url: null}]}]};
const publica = (over, ads, econ, disp) => Object.assign({
  listing_id: 99, platform: "amazon_mx", product_id: 1, asin: null, seller_sku: null,
  seleccionable: true, motivos: [], objetivo_acos_pct: null,
  economia: Object.assign({ventana_desde: "2026-02-20", ventana_hasta: "2026-08-22",
    moneda: "MXN", venta_total: "7000", venta_cubierta: "7000", cobertura: "1",
    dias_con_venta: 70, margen_neto_pct: "40", integridad_ok: true,
    muestra_limitada: false, muestra_venta: null, muestra_margen_neto_pct: null,
    ledger_fresco_at: "2026-09-05T10:00:00+00:00"}, econ),
  ads: Object.assign({etiqueta: null, maduro: true, provisional: false, muestra: 5,
    moneda: "MXN", cost: "100", clicks: 200, sales30d: "400", purchases30d: "4",
    promoted30d: "350", halo30d: "50", acos_pct: "25", cpc: "0.5", cvr_pct: "2"}, ads),
  disponibilidad: disp,
}, over);
const llena = {plataforma: "amazon_mx",
  ventana_ads: {desde: "2026-08-06", hasta: "2026-09-05"},
  orden: "margen_observado", direccion: "desc", publicaciones: [
    publica({listing_id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A",
      objetivo_acos_pct: "25.00"},
      {etiqueta: "dentro_del_objetivo", muestra: 10, purchases30d: "100",
        cost: "250", sales30d: "1000", acos_pct: "25", cpc: "3.06666666666666666666"},
      {}, {estado: "positivo", cantidad: {fba: 12}, fuente: ["fba"],
        freshness: {fba: "2026-09-05T00:00:00+00:00"}}),
    publica({listing_id: 12, asin: "B0BBBBBBBB", seller_sku: "SKU-AMAZON-B"},
      {etiqueta: "gasto_sin_ventas", muestra: 3, cost: "10", sales30d: "0",
        purchases30d: "0", acos_pct: null},
      {}, {estado: "cero", cantidad: {fba: 0}, fuente: ["fba"],
        freshness: {fba: "2026-09-05T00:00:00+00:00"}}),
    publica({listing_id: 13, asin: "B0CCCCCCCC", seller_sku: "SKU-AMAZON-C"},
      {etiqueta: "sin_datos", maduro: false, muestra: 0, cost: null, clicks: null,
        sales30d: null, purchases30d: null, acos_pct: null, cpc: null, cvr_pct: null},
      {}, {estado: "desconocido", cantidad: null, fuente: null, freshness: null}),
    publica({listing_id: 14, asin: "B0DDDDDDDD", seller_sku: "SKU-AMAZON-D",
      objetivo_acos_pct: "25.00", motivos: ["Margen negativo."]},
      {etiqueta: "por_encima_del_objetivo", provisional: true, muestra: 2,
        cost: "40", sales30d: "100", acos_pct: "40"},
      {margen_neto_pct: null, muestra_limitada: true, dias_con_venta: 12,
        muestra_venta: "500", muestra_margen_neto_pct: "-5"},
      {estado: "desconocido", cantidad: null, fuente: null, freshness: null}),
  ]};
const respuestas = [
  {ok: false, status: 503, json: async () => (
    {detail: {mensaje: "No se pudo consultar la evaluación del catálogo."}})},
  {ok: true, status: 200, json: async () => ({plataforma: "amazon_mx",
    ventana_ads: {desde: "2026-08-06", hasta: "2026-09-05"}, publicaciones: []})},
  {ok: true, status: 200, json: async () => llena},
  {ok: true, status: 200, json: async () => llena},
];
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return {ok: true, status: 200, json: async () => catalogo};
  if (url.includes("/lotes?")) return {ok: true, status: 200, json: async () => ({items: []})};
  if (url.includes("/evaluacion")) return respuestas.shift();
  return {ok: true, status: 200, json: async () => ({})};
};
const emit = async (id, event = "change") => {
  const handler = el(id).events[event]; assert.ok(handler, `Falta evento ${id}:${event}`);
  handler({preventDefault() {}, target: el(id)});
  await new Promise(resolve => setImmediate(resolve));
};
const text = node => node.textContent + node.children.map(text).join(" ");
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  const datos = el("comparador-datos"), estado = el("comparador-estado");
  assert.match(estado.textContent, /No se pudo consultar la evaluación/, "503 honesto");
  assert.equal(text(datos).trim(), "", "El error no inventa filas");
  await emit("comparador-recargar", "click");
  assert.match(text(datos), /Sin datos\./);
  assert.match(text(datos), /2026-08-06 a 2026-09-05/, "Ventana siempre visible");
  assert.match(text(datos), /Grano de comparación/);
  await emit("comparador-recargar", "click");
  const nodosTabla = datos.querySelectorAll("*");
  const cabeceras = nodosTabla.filter(e => e.tagName === "thead")[0];
  assert.equal(cabeceras.children.length, 2, "Dos niveles de cabecera accesibles");
  assert.deepEqual(cabeceras.children[0].children.map(e => Number(e.colspan || 1)),
    [1, 3, 8, 1, 1], "Los grupos abarcan exactamente las 14 columnas");
  const filas = nodosTabla.filter(e => e.tagName === "tbody")[0].children;
  assert.ok(filas.every(f => f.children.length === 14), "Ningun dato se desplaza de columna");
  assert.equal(filas[0].children[0].scope, "row", "Publicacion identifica la fila");
  assert.match(text(filas[0].children[4]), /1,000\.00 MXN/, "Revenue Ads conserva moneda");
  assert.match(text(filas[1].children[4]), /0\.00 MXN/, "Cero ventas sigue siendo cero");
  assert.equal(text(filas[2].children[4]), "Sin dato", "Ausente no se convierte en cero");
  assert.match(filas[3].children[6].className, /fabrica-negativo/, "ACoS sobre objetivo resaltado");
  const tabla = text(datos);
  for (const esperado of ["Dentro del objetivo", "Gasto sin ventas", "Sin datos",
    "Por encima del objetivo (provisional)"]) assert.ok(tabla.includes(esperado), esperado);
  assert.ok(!tabla.includes("Por probar"), "Por probar no existe");
  assert.match(tabla, /Limitada\s+12 días con venta, margen -5 %/);
  assert.match(tabla, /Margen negativo\./);
  assert.match(tabla, /Stock en 0\s+FBA 0/);
  assert.match(tabla, /Desconocido\s+Featured Offer: Sin verificar/);
  assert.match(tabla, /Con stock\s+FBA 12/);
  assert.match(text(datos), /Objetivo ACoS del grupo en preparación\s*25\.00 %/);
  assert.match(tabla, /70 días con venta/);
  assert.match(estado.textContent, /4 publicaciones/);
  // AC10: la seleccion del catalogo sobrevive a filtro y orden.
  const caja = el("productos").querySelectorAll('input[type="checkbox"]')[0];
  caja.checked = true;
  await emit("comparador-filtro");
  assert.ok(text(datos).includes("Seleccionada"), "La seleccion se ve en el comparador");
  el("comparador-orden").value = "gasto";
  await emit("comparador-orden");
  const ultima = calls.filter(c => c.url.includes("/evaluacion")).at(-1);
  assert.match(ultima.url, /orden=gasto&direccion=desc/);
  assert.equal(caja.checked, true, "El orden no pierde la seleccion");
  assert.ok(text(datos).includes("Seleccionada"), "La seleccion sigue visible tras reordenar");
  el("comparador-filtro").value = "sin_datos_ads";
  await emit("comparador-filtro");
  const filtrada = text(datos);
  assert.ok(filtrada.includes("B0CCCCCCCC"), "El filtro muestra el Sin datos");
  assert.ok(!filtrada.includes("Gasto sin ventas"), "El filtro oculta los con Ads");
  assert.equal(caja.checked, true, "El filtro no pierde la seleccion");
  // Una consulta vieja que falla no borra los datos ni el estado de la nueva.
  let rechazarVieja;
  respuestas.push(
    new Promise((resolve, reject) => { rechazarVieja = reject; }),
    {ok: true, status: 200, json: async () => llena},
  );
  await emit("comparador-recargar", "click");
  await emit("comparador-recargar", "click");
  const tablaVigente = text(datos), estadoVigente = estado.textContent;
  assert.ok(tablaVigente.includes("B0CCCCCCCC"), "La consulta nueva ya se mostro");
  rechazarVieja(new Error("Fallo de una consulta anterior"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(estado.textContent, estadoVigente, "El fallo antiguo no cambia el estado");
  await emit("comparador-filtro");
  assert.equal(text(datos), tablaVigente, "El fallo antiguo no borra la comparacion al filtrar");
  assert.equal(caja.checked, true, "La seleccion sigue intacta");
  assert.equal(calls.filter(c => (c.options.method || "GET") === "GET").length, calls.length,
    "Cero escrituras: ningun POST");
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


def test_flujo_js_comparador_recibe_objetivo_manual_y_derivado():
    """Hallazgo cross-review codex 2026-09-07 (JS real, DOM simulado): el
    comparador refresca con el objetivo del grupo en preparacion -- el manual
    del formulario (incluso al cambiarlo) y el derivado del margen del preview
    (schema v2). Solo GET /evaluacion; cero escrituras."""
    archivo = RAIZ / "static/js/fabrica.js"
    assert archivo.exists(), "Falta el cliente de fabrica"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
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
el("comparador-orden").value = "margen_observado";
el("comparador-direccion").value = "desc";
el("comparador-filtro").value = "todas";
el("objetivo-origen").value = "margen_medido";
el("plan").elements = Object.fromEntries(attrs.filter(a => a.name).map(a => [a.name, ids[a.id]]));
const docEvents = {};
global.document = {
  getElementById: id => ids[id],
  createElement: tag => new Element({tagName: tag}),
  addEventListener: (event, fn) => { docEvents[event] = fn; },
};
global.window = {
  location: {href: "http://orbit.test/campanas/nuevas", search: ""},
  addEventListener: () => {},
  history: { replaceState: () => {} },
};
const calls = [];
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [],
  productos: [{id: 1, sku: "GORRA", nombre: "Gorras", publicaciones: [
    {id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A", platform: "amazon_mx",
      margen_neto_pct: "40", dias_con_venta: 70, ventana_desde: "2026-02-20",
      ventana_hasta: "2026-08-22", historial_ads: null, elegible: true, motivos: [],
      url: null}]}]};
const preview = {plan: {schema_version: 2, nombre_base: "Gorra", tipo_producto: "gorra",
    platform: "amazon_mx", moneda: "MXN", fecha: "2026-09-07", modo: "shadow",
    objetivo: {origen: "margen_medido", acos_pct: "20", derivado: "40", fraccion: "0.5",
      procedencia: "margen_medido"},
    publicaciones: [], semillas: {exact: [], keywords: [], asins: [], negativos: []}},
  campanas: [], presupuesto_diario_total: "0", existentes: [], lote: "L1", huella: "H1"};
const evaluacion = (over) => Object.assign({plataforma: "amazon_mx",
  ventana_ads: {desde: "2026-08-06", hasta: "2026-09-05"}, publicaciones: []}, over);
let respuestaEvaluacion = evaluacion();
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return {ok: true, status: 200, json: async () => catalogo};
  if (url.includes("/lotes?")) return {ok: true, status: 200, json: async () => ({items: []})};
  if (url.includes("/plan")) return {ok: true, status: 200, json: async () => preview};
  if (url.includes("/evaluacion")) {
    return {ok: true, status: 200, json: async () => respuestaEvaluacion};
  }
  return {ok: true, status: 200, json: async () => ({})};
};
const emit = async (id, event = "change") => {
  const handler = el(id).events[event]; assert.ok(handler, `Falta evento ${id}:${event}`);
  handler({preventDefault() {}, target: el(id)});
  await new Promise(resolve => setImmediate(resolve));
};
const consultas = () => calls.filter(c => c.url.includes("/evaluacion"));
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  // Sin manual y sin preview: la consulta inicial NO lleva objetivo.
  assert.ok(!consultas().at(-1).url.includes("objetivo="), "sin objetivo sin preview");
  // Preview con objetivo margen_medido (schema v2): el comparador lo recibe.
  el("productos").querySelectorAll('input[type="checkbox"]')[0].checked = true;
  el("nombre").value = "Gorra";
  el("tipo").value = "gorra";
  await emit("plan", "submit");
  assert.ok(consultas().some(c => c.url.includes("objetivo=20")),
    "el objetivo derivado del margen llega al comparador");
  // Cambiar los productos seleccionados invalida el preview: el comparador NO
  // conserva las etiquetas del objetivo derivado viejo (hallazgo 2a ronda).
  const antesInvalidar = consultas().length;
  await emit("plan", "change");  // el checkbox de un producto burbujea al form
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.ok(consultas().length > antesInvalidar, "invalidar el preview consulta de nuevo");
  assert.ok(!consultas().at(-1).url.includes("objetivo="),
    "sin objetivo tras invalidar el preview");
  // Un preview nuevo restablece el objetivo derivado para el comparador.
  await emit("plan", "submit");
  assert.ok(consultas().some(c => c.url.includes("objetivo=20")),
    "el preview reconsulta con el objetivo derivado");
  // Cambiar el ACoS manual a 10 refresca el comparador con objetivo=10.
  el("objetivo-origen").value = "manual_lanzamiento";
  el("objetivo-acos").value = "10";
  const antesOrigen = consultas().length;
  await emit("objetivo-origen");
  assert.ok(consultas().some(c => c.url.includes("objetivo=10")),
    "el manual del formulario llega al comparador");
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(consultas().length, antesOrigen + 1, "El cambio de origen hace un solo GET");
  // El tipeo en el campo manual refresca con debounce (~400 ms).
  await emit("plan", "submit");  // preview vigente antes de editar el objetivo
  const antes = consultas().length;
  el("objetivo-acos").value = "12";
  await emit("objetivo-acos", "input");
  await emit("plan", "input");  // burbujeo del mismo evento hasta el formulario
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(consultas().length, antes + 1, "El input y su burbujeo hacen un solo GET");
  assert.ok(consultas().at(-1).url.includes("objetivo=12"), "el debounce manda el 12");
  // El comparador jamas escribe: todas SUS consultas son GET (el POST /plan
  // es del preview, no del comparador).
  assert.ok(consultas().length > 0);
  for (const consulta of consultas()) {
    assert.equal(consulta.options.method || "GET", "GET", "el comparador solo consulta");
  }
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


def test_flujo_js_comparador_muestra_ventana_cobertura_y_actualizacion_del_margen():
    """Hallazgo cross-review codex 2026-09-07 (JS real, DOM simulado): la
    ficha del comparador declara la ventana, la cobertura y la actualizacion
    del margen (ledger_fresco_at) por publicacion; si varia entre publicaciones
    se dice 'Varia por publicacion', sin inventar un valor unico."""
    archivo = RAIZ / "static/js/fabrica.js"
    assert archivo.exists(), "Falta el cliente de fabrica"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
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
el("comparador-orden").value = "margen_observado";
el("comparador-direccion").value = "desc";
el("comparador-filtro").value = "todas";
el("plan").elements = Object.fromEntries(attrs.filter(a => a.name).map(a => [a.name, ids[a.id]]));
const docEvents = {};
global.document = {
  getElementById: id => ids[id],
  createElement: tag => new Element({tagName: tag}),
  addEventListener: (event, fn) => { docEvents[event] = fn; },
};
global.window = {
  location: {href: "http://orbit.test/campanas/nuevas", search: ""},
  addEventListener: () => {},
  history: { replaceState: () => {} },
};
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [], productos: []};
const publica = (listing, econ) => ({
  listing_id: listing, platform: "amazon_mx", product_id: 1,
  asin: "B0AAAAAAAA", seller_sku: "SKU-" + listing, seleccionable: true, motivos: [],
  objetivo_acos_pct: null,
  economia: Object.assign({ventana_desde: "2026-02-20", ventana_hasta: "2026-08-22",
    moneda: "MXN", venta_total: "7000", venta_cubierta: "7000", cobertura: "1",
    dias_con_venta: 70, margen_neto_pct: "40", integridad_ok: true,
    muestra_limitada: false, muestra_venta: null, muestra_margen_neto_pct: null,
    ledger_fresco_at: "2026-09-05T10:00:00+00:00"}, econ),
  ads: {etiqueta: null, maduro: false, provisional: false, muestra: 0, moneda: "MXN",
    cost: null, clicks: null, sales30d: null, purchases30d: null, promoted30d: null,
    halo30d: null, acos_pct: null, cpc: null, cvr_pct: null},
  disponibilidad: {estado: "desconocido", cantidad: null, fuente: null, freshness: null},
});
const iguales = {plataforma: "amazon_mx",
  ventana_ads: {desde: "2026-08-06", hasta: "2026-09-05"},
  publicaciones: [publica(11), publica(12)]};
const variadas = {plataforma: "amazon_mx",
  ventana_ads: {desde: "2026-08-06", hasta: "2026-09-05"},
  publicaciones: [publica(11), publica(12, {ventana_desde: "2026-03-01",
    ventana_hasta: "2026-08-30", cobertura: "0.5",
    ledger_fresco_at: "2026-09-01T09:00:00+00:00"})]};
const respuestas = [iguales, variadas];
global.fetch = async (url, options = {}) => {
  if (url.includes("/catalogo")) return {ok: true, status: 200, json: async () => catalogo};
  if (url.includes("/lotes?")) return {ok: true, status: 200, json: async () => ({items: []})};
  if (url.includes("/evaluacion")) {
    return {ok: true, status: 200, json: async () => respuestas.shift()};
  }
  return {ok: true, status: 200, json: async () => ({})};
};
const emit = async (id, event = "change") => {
  const handler = el(id).events[event]; assert.ok(handler, `Falta evento ${id}:${event}`);
  handler({preventDefault() {}, target: el(id)});
  await new Promise(resolve => setImmediate(resolve));
};
const text = node => node.textContent + node.children.map(text).join(" ");
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  const datos = el("comparador-datos");
  const ficha = text(datos);
  for (const esperado of ["Ventana del margen", "Cobertura del margen",
    "Actualización del margen"]) assert.ok(ficha.includes(esperado), esperado);
  assert.match(ficha, /Ventana del margen 2026-02-20 a 2026-08-22/);
  assert.match(ficha, /Cobertura del margen 1\b/);
  assert.match(ficha, /Actualización del margen 2026-09-05T10:00:00\+00:00/);
  await emit("comparador-recargar", "click");
  const variada = text(datos);
  // Al variar se DECLARA y ademas se listan los valores individuales para
  // poder consultarlos sin salir de la ficha (observacion cross-review 2a ronda).
  assert.match(
    variada,
    /Ventana del margen Varia por publicacion: 2026-02-20 a 2026-08-22; 2026-03-01 a 2026-08-30/,
  );
  assert.match(variada, /Cobertura del margen Varia por publicacion: 1; 0\.5/);
  assert.match(
    variada,
    new RegExp(
      "Actualización del margen Varia por publicacion: 2026-09-05T10:00:00\\+00:00; "
      + "2026-09-01T09:00:00\\+00:00"),
  );
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
