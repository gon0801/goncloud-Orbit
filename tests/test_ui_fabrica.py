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


def test_pantalla_carga_bids_sugeridos_y_firma_su_fuente():
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    assert 'id="fabrica-bids-amazon"' in respuesta.text
    codigo = (RAIZ / "static/js/fabrica.js").read_text()
    assert 'solicitar("/bids-sugeridos"' in codigo
    assert 'fuente_bid: "amazon_v4"' in codigo
    assert "sugerencias[rol].disponible" in codigo
    assert "recomendaciones: sugerencias[rol].recomendaciones.map" in codigo
    assert "invalidar(true);\n    const solicitud = solicitudActual();" in codigo
    assert "if (version !== revision)" in codigo
    assert 'nombre.endsWith("_bid")' in codigo
    assert "sugerencias[rol] = {...sugerencias[rol], disponible: false}" in codigo
    for titulo in ("Objetivo", "Mínimo", "Sugerido", "Máximo"):
        assert titulo in codigo


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


def test_ui_estimacion_etiqueta_separada_y_sin_columna_ancha():
    """B.2: la estimacion tiene etiqueta propia, no se llama margen neto y
    no abre un colgroup nuevo en el comparador."""
    js = (RAIZ / "static/js/fabrica.js").read_text()
    css = (RAIZ / "static/css/fabrica.css").read_text()
    assert "Contribución estimada por venta" in js
    assert "Antes de Ads" in js
    assert "fabrica-estimacion" in js
    assert "fabrica-estimacion" in css
    assert re.search(
        r"\.fabrica-estimacion[^{]*\{[^}]*overflow-wrap:\s*anywhere",
        css,
    )
    assert "no disponible para anunciar" not in js.lower()
    for prohibido in ("break-even", "rentable", "bueno", "malo"):
        assert prohibido not in js.lower()
    assert "motivosEstimacion" in js
    assert "asOfEstimacion" in js
    assert "pertenece al total" in js or "fuera del total" in js
    assert "politica_ausente" in js
    assert "Falta la política de cálculo" in js
    assert '["Publicación", 1, 2], ["Margen observado · antes de Ads", 3, 1]' in js
    assert '["Estim' not in js
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert 'value="margen_observado"' in respuesta.text
    assert "estimacion" not in respuesta.text.lower()


def test_flujo_js_estimacion_estados_detalle_y_seleccion():
    """B.2 en JS real: estados, desglose fuera del checkbox, detalle congelado
    nunca como principal, y seleccion intacta al filtrar/ordenar/actualizar."""
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
const sobre = (over = {}) => Object.assign({
  estado: "disponible", motivos: [], snapshot_id: 1,
  escenario: {unidad: "1", canal: "fba", fecha_valoracion: "2026-09-08",
    version_formula: "S3", version_politica: 3},
  moneda: "MXN", contribucion: "42.5000", contribucion_pct: "36.6379",
  base_porcentaje: "ingreso_normalizado",
  componentes: [{nombre: "referral", importe_original: "15.0000",
    moneda_original: "MXN", importe_normalizado: "15.0000",
    moneda_normalizada: "MXN", fuente: "product_fees",
    fecha_fuente: "2026-09-08", observed_at: "2026-09-08T12:00:00+00:00",
    vigencia: "2026-09-08", estado: null, pertenencia: true}],
  exclusiones: ["iva_trasladado"], detalle: null,
}, over);
const pubCat = (id, asin, est, extra = {}) => Object.assign({
  id, asin, seller_sku: "SKU-" + asin.slice(-1), platform: "amazon_mx",
  margen_neto_pct: extra.margen_neto_pct !== undefined ? extra.margen_neto_pct : "40",
  dias_con_venta: extra.dias_con_venta, ventana_desde: "2026-02-20",
  ventana_hasta: "2026-08-22", historial_ads: null, elegible: true, motivos: [],
  url: "https://www.amazon.com.mx/dp/" + asin, estimacion: est,
}, extra);
const catalogo = {plataforma: "amazon_mx", moneda: "MXN",
  as_of: "2026-09-08T18:00:00+00:00", tipos_producto: [],
  productos: [{id: 1, sku: "GORRA", nombre: "Gorras", publicaciones: [
    pubCat(11, "B0AAAAAAAA", sobre(), {dias_con_venta: 70}),
    pubCat(12, "B0BBBBBBBB", sobre({contribucion: "39.0000", contribucion_pct: "39.0000"}),
      {dias_con_venta: null, margen_neto_pct: null}),
    pubCat(13, "B0CCCCCCCC", sobre({estado: "incompleta", contribucion: null,
      contribucion_pct: null, motivos: ["fee_ausente"],
      detalle: {contribucion: "42.5000", contribucion_pct: "36.6379",
        estado: "disponible"}})),
    pubCat(14, "B0DDDDDDDD", sobre({estado: "desactualizada", contribucion: null,
      contribucion_pct: null, motivos: ["oferta_desactualizada"], exclusiones: []})),
    pubCat(15, "B0EEEEEEEE", sobre({estado: "identidad_ambigua", contribucion: null,
      contribucion_pct: null, motivos: ["identidad_ambigua"], exclusiones: []})),
    pubCat(16, "B0FFFFFFFF", sobre({contribucion: "0.0000", contribucion_pct: "0.0000",
      exclusiones: []})),
    pubCat(17, "B0GGGGGGGG", sobre({contribucion: "-10.0000", contribucion_pct: "-10.0000",
      exclusiones: []})),
  ]}]};
const publica = (over, est) => Object.assign({
  listing_id: 11, platform: "amazon_mx", product_id: 1, asin: "B0AAAAAAAA",
  seller_sku: "SKU-A", seleccionable: true, motivos: [], objetivo_acos_pct: null,
  economia: {ventana_desde: "2026-02-20", ventana_hasta: "2026-08-22",
    moneda: "MXN", venta_total: "7000", venta_cubierta: "7000", cobertura: "1",
    dias_con_venta: 70, margen_neto_pct: "40", integridad_ok: true,
    muestra_limitada: false, muestra_venta: null, muestra_margen_neto_pct: null,
    ledger_fresco_at: "2026-09-05T10:00:00+00:00"},
  ads: {etiqueta: "dentro_del_objetivo", maduro: true, provisional: false,
    muestra: 10, moneda: "MXN", cost: "250", clicks: 200, sales30d: "1000",
    purchases30d: "100", promoted30d: "350", halo30d: "50", acos_pct: "25",
    cpc: "0.5", cvr_pct: "2"},
  disponibilidad: {estado: "positivo", cantidad: {fba: 12}, fuente: ["fba"],
    freshness: {fba: "2026-09-05T00:00:00+00:00"}},
  estimacion: est,
}, over);
const evaluacion = {plataforma: "amazon_mx",
  as_of: "2026-09-08T18:00:00+00:00",
  ventana_ads: {desde: "2026-08-06", hasta: "2026-09-05"},
  orden: "margen_observado", direccion: "desc", publicaciones: [
    publica({listing_id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-A"}, sobre()),
    publica({listing_id: 13, asin: "B0CCCCCCCC", seller_sku: "SKU-C",
      ads: {etiqueta: "sin_datos", maduro: false, muestra: 0, cost: null,
        clicks: null, sales30d: null, purchases30d: null, acos_pct: null,
        cpc: null, cvr_pct: null}},
      sobre({estado: "incompleta", contribucion: null, contribucion_pct: null,
        motivos: ["fee_ausente"],
        detalle: {contribucion: "42.5000", contribucion_pct: "36.6379",
          estado: "disponible"}})),
  ]};
const llamadas = [];
global.fetch = async (url, options = {}) => {
  llamadas.push(String(url));
  if (url.includes("/catalogo")) return {ok: true, status: 200, json: async () => catalogo};
  if (url.includes("/lotes?")) return {ok: true, status: 200, json: async () => ({items: []})};
  if (url.includes("/evaluacion")) return {ok: true, status: 200, json: async () => evaluacion};
  return {ok: true, status: 200, json: async () => ({})};
};
const emit = async (id, event = "change") => {
  const handler = el(id).events[event]; assert.ok(handler, `Falta evento ${id}:${event}`);
  handler({preventDefault() {}, target: el(id)});
  await new Promise(resolve => setImmediate(resolve));
};
const text = node => node.textContent + node.children.map(text).join(" ");
const all = node => node.querySelectorAll("*");
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  const productos = el("productos");
  const catalogoTxt = text(productos);
  assert.match(catalogoTxt, /margen neto antes de Ads/);
  assert.match(catalogoTxt, /Contribución estimada por venta/);
  assert.match(catalogoTxt, /Antes de Ads/);
  const bloques = all(productos).filter(e => e.className === "fabrica-estimacion");
  assert.ok(bloques.length >= 7, "Cada publicacion muestra su bloque de estimacion");
  for (const bloque of bloques) {
    const t = text(bloque);
    assert.match(t, /Contribución estimada por venta/);
    assert.match(t, /Antes de Ads/);
    assert.ok(!/margen neto/i.test(t), "la estimacion no se llama margen neto");
  }
  assert.match(catalogoTxt, /42\.50 MXN/);
  assert.match(catalogoTxt, /36\.64\s*%/);
  assert.match(catalogoTxt, /ingreso_normalizado/);
  assert.match(catalogoTxt, /2026-09-08/);
  assert.match(catalogoTxt, /fba/);
  assert.match(catalogoTxt, /iva_trasladado/);
  assert.match(catalogoTxt, /39\.00 MXN/, "sin ventas sigue mostrando el estimado");
  assert.match(catalogoTxt, /Incompleta/);
  assert.match(catalogoTxt, /Falta la cotización de comisiones/);
  assert.match(catalogoTxt, /Desactualizada/);
  assert.match(catalogoTxt, /La oferta de publicación ya no está vigente/);
  assert.match(catalogoTxt, /Identidad ambigua/);
  assert.match(catalogoTxt, /Hay más de una oferta compatible/);
  assert.match(catalogoTxt, /0\.00 MXN/);
  assert.match(catalogoTxt, /-10\.00 MXN/);
  assert.ok(!/fee_ausente|oferta_desactualizada/.test(catalogoTxt)
    || /Falta la cotización/.test(catalogoTxt),
    "los motivos internos se traducen a texto legible");
  assert.ok(!/no disponible para anunciar/i.test(catalogoTxt));
  assert.ok(!/break-even|rentable|\bbueno\b|\bmalo\b/i.test(catalogoTxt));
  const cajas = productos.querySelectorAll('input[type="checkbox"]');
  assert.equal(cajas.length, 7);
  assert.ok(cajas.every(c => c.disabled === false), "Ningun estado de estimacion bloquea");
  const labels = all(productos).filter(e => e.tagName === "label");
  assert.ok(labels.every(l => !all(l).some(e => e.tagName === "details")),
    "Abrir el desglose no vive dentro del label");
  const detalles = all(productos).filter(e => e.tagName === "details");
  assert.ok(detalles.length >= 7, "El desglose es plegable en cada tarjeta");
  const desglose = text(detalles[0]);
  assert.match(desglose, /referral/);
  assert.match(desglose, /product_fees/);
  assert.match(desglose, /15\.0000/);
  assert.match(desglose, /fecha 2026-09-08/);
  assert.match(desglose, /captura 2026-09-08T12:00:00/);
  assert.match(desglose, /vigencia 2026-09-08/);
  assert.match(desglose, /pertenece al total/);
  assert.ok(!/estado incluido/.test(desglose), "estado no se inventa desde pertenencia");
  assert.match(desglose, /S3/);
  assert.match(desglose, /3/);
  const incompleta = all(productos).filter(e => e.tagName === "li")[2];
  const principalInc = text(all(incompleta).find(e => e.className === "fabrica-estimacion"));
  assert.ok(!principalInc.includes("42.50"), "detalle congelado no es el principal");
  assert.match(text(incompleta), /42\.5000/, "el numero congelado vive en el desglose");
  const fotos = all(productos).filter(e => e.src);
  fotos[0].events.error();
  assert.equal(cajas[0].checked, false, "La foto no cambia la seleccion");
  const enlaces = all(productos).filter(e => e.href);
  assert.ok(enlaces.length);
  assert.ok(labels.every(l => !all(l).some(e => e.href)),
    "El enlace no selecciona");
  cajas[0].checked = true;
  await emit("productos");
  const datos = el("comparador-datos");
  const tabla = text(datos);
  assert.match(tabla, /Contribución estimada por venta/);
  assert.match(tabla, /Antes de Ads/);
  const cabeceras = all(datos).filter(e => e.tagName === "thead")[0];
  assert.deepEqual(cabeceras.children[0].children.map(e => Number(e.colspan || 1)),
    [1, 3, 8, 1, 1], "Sin colgroup nuevo de estimacion");
  const filas = all(datos).filter(e => e.tagName === "tbody")[0].children;
  assert.ok(filas.every(f => f.children.length === 14));
  const identidad = filas[0].children[0];
  assert.ok(all(identidad).some(e => e.tagName === "details"),
    "El desglose del comparador vive en la identidad fija");
  assert.match(text(identidad), /42\.50 MXN/);
  const idIncompleta = filas[1].children[0];
  assert.match(text(idIncompleta), /Incompleta/);
  const principalCmp = all(idIncompleta).find(e => e.className === "fabrica-estimacion");
  assert.ok(!text(principalCmp).includes("42.50"));
  await emit("comparador-filtro");
  assert.equal(cajas[0].checked, true, "El filtro no pierde la seleccion");
  el("comparador-orden").value = "gasto";
  await emit("comparador-orden");
  assert.equal(cajas[0].checked, true, "El orden no pierde la seleccion");
  await emit("recargar-catalogo", "click");
  const cajasTras = el("productos").querySelectorAll('input[type="checkbox"]');
  assert.equal(cajasTras[0].checked, true, "Actualizar conserva la seleccion");
  assert.equal(cajasTras[0].value, "11");
  assert.match(text(el("productos")), /Contribución estimada por venta/);
  assert.ok(llamadas.some(u => u.includes("/evaluacion") && u.includes("as_of=")),
    "evaluacion reusa el as_of del catalogo");
  await emit("comparador-recargar", "click");
  assert.equal(cajasTras[0].checked, true, "Releer evaluacion no toca la seleccion");
  assert.match(text(el("comparador-datos")), /Contribución estimada por venta/);
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


# ---------------------------------------------------------------------------
# Sonda real 2026-09-09 (F1-F4): ayudante y pruebas del flujo JavaScript.
# ---------------------------------------------------------------------------

_PRELUDIO_FABRICA_JS = r"""
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
const ok = body => ({ok: true, status: 200, json: async () => body});
const emit = async (id, event = "submit") => {
  const handler = el(id).events[event]; assert.ok(handler, `Falta evento ${id}:${event}`);
  handler({preventDefault() {}, target: el(id)});
  await new Promise(resolve => setImmediate(resolve));
};
const text = node => node.textContent + node.children.map(text).join(" ");
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
"""

_ROLES_FABRICA = [
    "category_exact",
    "category_phrase",
    "category_broad",
    "product_targeting",
    "auto_discovery",
]


def _correr_flujo_fabrica(guion: str):
    """JS real de fabrica con DOM simulado y frontera API simulada."""
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    html = TestClient(app).get("/campanas/nuevas").text
    elementos = [attrs for _, attrs in Elementos(html).elementos if "id" in attrs]
    resultado = subprocess.run(
        [
            node,
            "-e",
            _PRELUDIO_FABRICA_JS + guion,
            json.dumps(elementos),
            str(RAIZ / "static/js/fabrica.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def test_buscador_publicaciones_es_accesible():
    """F1 estatico: filtro de texto con label, usable por teclado, sin red."""
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    elementos = Elementos(respuesta.text).elementos
    por_id = {a.get("id"): (t, a) for t, a in elementos if a.get("id")}
    tag, attrs = por_id["fabrica-buscar"]
    assert tag == "input" and attrs.get("type") == "search"
    labels = [a for t, a in elementos if t == "label" and a.get("for") == "fabrica-buscar"]
    assert len(labels) == 1


def test_flujo_js_buscador_filtra_en_vivo_y_conserva_seleccion():
    """F1: filtra lo ya cargado (sin red), no pierde seleccion ni invalida el preview."""
    _correr_flujo_fabrica(r"""
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [],
  productos: [
    {id: 1, sku: "GORRA-01", nombre: "Gorras bordadas", publicaciones: [
      {id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A", platform: "amazon_mx",
        margen_neto_pct: null, dias_con_venta: null, ventana_desde: null,
        ventana_hasta: null, historial_ads: null, elegible: true, motivos: [], url: null}]},
    {id: 2, sku: "PLAYERA-02", nombre: "Playeras", publicaciones: [
      {id: 12, asin: "B0BBBBBBBB", seller_sku: "SKU-AMAZON-B", platform: "amazon_mx",
        margen_neto_pct: null, dias_con_venta: null, ventana_desde: null,
        ventana_hasta: null, historial_ads: null, elegible: true, motivos: [], url: null}]}]};
const plan = {huella: "abc", lote: "web-abc", presupuesto_diario_total: "600.00",
  existentes: [],
  campanas: ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"].map(rol => ({rol, nombre: rol,
    budget: "120.00", bid: "4.00", fuente_bid: "manual"})),
  plan: {schema_version: 2, platform: "amazon_mx", moneda: "MXN", modo: "shadow",
    fecha: "2026-09-06", nombre_base: "Gorras", tipo_producto: "gorras",
    objetivo: {origen: "manual_lanzamiento", acos_pct: "25.00",
      procedencia: "confirmado", fraccion: null, derivado: null},
    publicaciones: [], semillas: {exact: [], keywords: [], asins: [], negativos: []}}};
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return ok(catalogo);
  if (url.includes("/evaluacion")) return ok({plataforma: "amazon_mx", publicaciones: []});
  if (url.includes("/lotes?")) return ok({items: []});
  if (url.endsWith("/plan")) return ok(plan);
  return ok({});
};
(async () => {
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  const tarjetas = () => el("productos").querySelectorAll("*")
    .filter(e => e.className === "fabrica-producto");
  assert.equal(tarjetas().length, 2);
  const lecturas = () => calls.filter(c => c.url.includes("/catalogo")).length;
  el("buscar").value = "gorra";
  await emit("buscar", "input");
  assert.equal(tarjetas()[0].hidden, false);
  assert.equal(tarjetas()[1].hidden, true);
  assert.equal(lecturas(), 1, "filtrar no consulta de nuevo: sin red");
  el("buscar").value = "B0BBBBBBBB";
  await emit("buscar", "input");
  assert.equal(tarjetas()[0].hidden, true);
  assert.equal(tarjetas()[1].hidden, false, "filtra por ASIN");
  el("buscar").value = "sku-amazon-a";
  await emit("buscar", "input");
  assert.equal(tarjetas()[0].hidden, false, "filtra por SKU de Amazon");
  const cajas = el("productos").querySelectorAll('input[type="checkbox"]');
  cajas[0].checked = true;
  await emit("productos", "change");
  el("buscar").value = "playera";
  await emit("buscar", "input");
  assert.equal(cajas[0].checked, true, "filtrar no pierde la seleccion");
  el("buscar").value = "";
  await emit("buscar", "input");
  assert.ok(tarjetas().every(t => t.hidden === false), "limpiar muestra todo");
  assert.equal(cajas[0].checked, true);
  el("tipo").value = "gorras"; el("nombre").value = "Gorras"; el("modo").value = "shadow";
  el("objetivo-origen").value = "manual_lanzamiento";
  await emit("objetivo-origen", "change");
  el("objetivo-acos").value = "25.00";
  for (const rol of ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"]) {
    ids[rol + "-budget"].value = "120.00"; ids[rol + "-bid"].value = "4.00";
  }
  await emit("plan");
  assert.equal(el("preview").hidden, false);
  el("buscar").value = "gorra";
  await emit("buscar", "input");
  assert.equal(el("preview").hidden, false, "filtrar no invalida el plan revisado");
  assert.equal(el("crear-boton").disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_flujo_js_revision_muestra_semillas_por_campana():
    """F2: cada campana muestra sus semillas; lista vacia dice "0 semillas"."""
    _correr_flujo_fabrica(r"""
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [],
  productos: [{id: 1, sku: "GORRA-01", nombre: "Gorras", publicaciones: [
    {id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A", platform: "amazon_mx",
      margen_neto_pct: null, dias_con_venta: null, ventana_desde: null,
      ventana_hasta: null, historial_ads: null, elegible: true, motivos: [],
      url: null}]}]};
const plan = {huella: "abc", lote: "web-abc", presupuesto_diario_total: "600.00",
  existentes: [],
  campanas: ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"].map(rol => ({rol, nombre: rol,
    budget: "120.00", bid: "4.00", fuente_bid: "manual"})),
  plan: {schema_version: 2, platform: "amazon_mx", moneda: "MXN", modo: "shadow",
    fecha: "2026-09-06", nombre_base: "Gorras", tipo_producto: "gorras",
    objetivo: {origen: "manual_lanzamiento", acos_pct: "25.00",
      procedencia: "confirmado", fraccion: null, derivado: null},
    publicaciones: [],
    semillas: {exact: ["gorra plana"], keywords: [], asins: ["B0BBBBBBBB"],
      negativos: []}}};
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return ok(catalogo);
  if (url.includes("/evaluacion")) return ok({plataforma: "amazon_mx", publicaciones: []});
  if (url.includes("/lotes?")) return ok({items: []});
  if (url.endsWith("/plan")) return ok(plan);
  return ok({});
};
(async () => {
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  el("productos").querySelectorAll('input[type="checkbox"]')[0].checked = true;
  el("tipo").value = "gorras"; el("nombre").value = "Gorras"; el("modo").value = "shadow";
  el("objetivo-origen").value = "manual_lanzamiento";
  await emit("objetivo-origen", "change");
  el("objetivo-acos").value = "25.00";
  for (const rol of ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"]) {
    ids[rol + "-budget"].value = "120.00"; ids[rol + "-bid"].value = "4.00";
  }
  await emit("plan");
  const revision = text(el("preview-datos"));
  assert.match(revision, /gorra plana \(exacta\)/, "la exacta muestra texto y match");
  assert.match(revision, /0 semillas/, "lista vacia se declara, no se oculta");
  assert.match(revision, /B0BBBBBBBB/, "product targeting muestra sus ASIN");
  assert.match(revision, /automáticos/, "auto avisa grupos automaticos de Amazon");
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_crear_expone_progreso_y_banner_de_resultado():
    """F3 estatico: indicador junto al boton y banner enfocable para el resultado."""
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    elementos = Elementos(respuesta.text).elementos
    por_id = {a.get("id"): (t, a) for t, a in elementos if a.get("id")}
    tag, attrs = por_id["fabrica-crear-progreso"]
    assert tag == "p" and attrs.get("role") == "status" and "hidden" in attrs
    tag, attrs = por_id["fabrica-crear-resultado"]
    assert attrs.get("role") == "status" and attrs.get("tabindex") == "-1"
    assert "hidden" in attrs


def test_flujo_js_crear_muestra_progreso_y_banner_con_enlace():
    """F3 ruta feliz: boton en progreso, banner con resultado y enlace al lote."""
    _correr_flujo_fabrica(r"""
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [],
  productos: [{id: 1, sku: "GORRA-01", nombre: "Gorras", publicaciones: [
    {id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A", platform: "amazon_mx",
      margen_neto_pct: null, dias_con_venta: null, ventana_desde: null,
      ventana_hasta: null, historial_ads: null, elegible: true, motivos: [],
      url: null}]}]};
const esquema = {schema_version: 2, platform: "amazon_mx", moneda: "MXN",
  modo: "shadow", fecha: "2026-09-06", nombre_base: "Gorras",
  tipo_producto: "gorras",
  objetivo: {origen: "manual_lanzamiento", acos_pct: "25.00",
    procedencia: "confirmado", fraccion: null, derivado: null},
  publicaciones: [],
  semillas: {exact: ["gorra"], keywords: [], asins: [], negativos: []}};
const plan = {huella: "abc", lote: "web-abc", presupuesto_diario_total: "600.00",
  existentes: [],
  campanas: ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"].map(rol => ({rol, nombre: rol,
    budget: "120.00", bid: "4.00", fuente_bid: "manual"})),
  plan: esquema};
const lote = {lote: "web-abc", plataforma: "amazon_mx", estado: "applied",
  detalle: "Grupo creado", created_at: "2026-09-09", finished_at: "2026-09-09",
  plan: esquema, pasos: [{orden: 1, rol: "category_exact", recurso: "campaign",
    external_id: "111", estado: "applied", readback_estado: "ok"}]};
let resolverCrear;
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return ok(catalogo);
  if (url.includes("/evaluacion")) return ok({plataforma: "amazon_mx", publicaciones: []});
  if (url.includes("/lotes?")) return ok({items: []});
  if (url.endsWith("/plan")) return ok(plan);
  if (url.endsWith("/crear")) return new Promise(resolve => { resolverCrear = resolve; });
  return ok(lote);
};
(async () => {
  let enfocado = null;
  el("crear-resultado").focus = () => { enfocado = "banner"; };
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  el("productos").querySelectorAll('input[type="checkbox"]')[0].checked = true;
  el("tipo").value = "gorras"; el("nombre").value = "Gorras"; el("modo").value = "shadow";
  el("objetivo-origen").value = "manual_lanzamiento";
  await emit("objetivo-origen", "change");
  el("objetivo-acos").value = "25.00";
  for (const rol of ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"]) {
    ids[rol + "-budget"].value = "120.00"; ids[rol + "-bid"].value = "4.00";
  }
  await emit("plan");
  el("token").value = "secreto-solo-header";
  el("confirmacion").value = "CREAR 5 CAMPAÑAS";
  await emit("crear");
  assert.match(el("crear-boton").textContent, /Creando… no cierres la página/);
  assert.equal(el("crear-progreso").hidden, false, "indicador visible junto al boton");
  assert.equal(el("crear-boton").disabled, true);
  resolverCrear(ok(lote));
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  const banner = el("crear-resultado");
  assert.equal(banner.hidden, false);
  assert.match(text(banner), /Creado y registrado/, "el banner trae el resultado");
  const enlaces = banner.querySelectorAll("*").filter(e => e.tagName === "a");
  assert.equal(enlaces.length, 1, "el banner enlaza al detalle del lote");
  assert.equal(enlaces[0].href, "#fabrica-lote");
  assert.equal(enfocado, "banner", "el foco se mueve al banner");
  assert.equal(el("crear-boton").textContent, "Crear las cinco campañas");
  assert.equal(el("crear-progreso").hidden, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_flujo_js_crear_error_de_red_muestra_banner():
    """F3 error: falla la red y el banner lo declara con enlace al lote."""
    _correr_flujo_fabrica(r"""
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [],
  productos: [{id: 1, sku: "GORRA-01", nombre: "Gorras", publicaciones: [
    {id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A", platform: "amazon_mx",
      margen_neto_pct: null, dias_con_venta: null, ventana_desde: null,
      ventana_hasta: null, historial_ads: null, elegible: true, motivos: [],
      url: null}]}]};
const esquema = {schema_version: 2, platform: "amazon_mx", moneda: "MXN",
  modo: "shadow", fecha: "2026-09-06", nombre_base: "Gorras",
  tipo_producto: "gorras",
  objetivo: {origen: "manual_lanzamiento", acos_pct: "25.00",
    procedencia: "confirmado", fraccion: null, derivado: null},
  publicaciones: [],
  semillas: {exact: ["gorra"], keywords: [], asins: [], negativos: []}};
const plan = {huella: "abc", lote: "web-abc", presupuesto_diario_total: "600.00",
  existentes: [],
  campanas: ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"].map(rol => ({rol, nombre: rol,
    budget: "120.00", bid: "4.00", fuente_bid: "manual"})),
  plan: esquema};
let rechazarCrear;
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return ok(catalogo);
  if (url.includes("/evaluacion")) return ok({plataforma: "amazon_mx", publicaciones: []});
  if (url.includes("/lotes?")) return ok({items: []});
  if (url.endsWith("/plan")) return ok(plan);
  if (url.endsWith("/crear")) return new Promise((_resolve, reject) => { rechazarCrear = reject; });
  return ok({});
};
(async () => {
  let enfocado = null;
  el("crear-resultado").focus = () => { enfocado = "banner"; };
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  el("productos").querySelectorAll('input[type="checkbox"]')[0].checked = true;
  el("tipo").value = "gorras"; el("nombre").value = "Gorras"; el("modo").value = "shadow";
  el("objetivo-origen").value = "manual_lanzamiento";
  await emit("objetivo-origen", "change");
  el("objetivo-acos").value = "25.00";
  for (const rol of ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"]) {
    ids[rol + "-budget"].value = "120.00"; ids[rol + "-bid"].value = "4.00";
  }
  await emit("plan");
  el("token").value = "secreto-solo-header";
  el("confirmacion").value = "CREAR 5 CAMPAÑAS";
  await emit("crear");
  rechazarCrear(new Error("conexion perdida"));
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  const banner = el("crear-resultado");
  assert.equal(banner.hidden, false);
  assert.match(text(banner), /interrumpida/i, "el banner declara el fallo");
  assert.equal(banner.querySelectorAll("*").filter(e => e.tagName === "a").length, 1);
  assert.equal(enfocado, "banner");
  assert.equal(el("crear-boton").textContent, "Crear las cinco campañas");
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_avisos_puja_manual_tienen_elemento_por_rol():
    """F4 estatico: cada rol tiene su aviso junto al campo de puja."""
    respuesta = TestClient(app).get("/campanas/nuevas")
    assert respuesta.status_code == 200
    elementos = Elementos(respuesta.text).elementos
    ids = {a.get("id") for _, a in elementos if a.get("id")}
    for rol in _ROLES_FABRICA:
        assert f"fabrica-{rol}-bid-aviso" in ids


def test_flujo_js_puja_manual_sin_sugerencia_visible_en_campo_y_revision():
    """F4: el campo sin sugerencia avisa y la revision lo repite por campana."""
    _correr_flujo_fabrica(r"""
const catalogo = {plataforma: "amazon_mx", moneda: "MXN", tipos_producto: [],
  productos: [{id: 1, sku: "GORRA-01", nombre: "Gorras", publicaciones: [
    {id: 11, asin: "B0AAAAAAAA", seller_sku: "SKU-AMAZON-A", platform: "amazon_mx",
      margen_neto_pct: null, dias_con_venta: null, ventana_desde: null,
      ventana_hasta: null, historial_ads: null, elegible: true, motivos: [],
      url: null}]}]};
const plan = {huella: "abc", lote: "web-abc", presupuesto_diario_total: "600.00",
  existentes: [],
  campanas: ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"].map(rol => ({rol, nombre: rol,
    budget: "120.00", bid: "11.00",
    fuente_bid: rol === "category_exact" ? "amazon_v4" : "manual"})),
  plan: {schema_version: 2, platform: "amazon_mx", moneda: "MXN", modo: "shadow",
    fecha: "2026-09-06", nombre_base: "Gorras", tipo_producto: "gorras",
    objetivo: {origen: "manual_lanzamiento", acos_pct: "25.00",
      procedencia: "confirmado", fraccion: null, derivado: null},
    publicaciones: [],
    semillas: {exact: ["gorra"], keywords: [], asins: [], negativos: []}}};
const sugeridos = {fuente: "amazon_v4", roles: {
  category_exact: {disponible: true, bid: "11.00", recomendaciones: [],
    faltantes: []},
  category_phrase: {disponible: false, bid: null, recomendaciones: [],
    faltantes: [{tipo: "KEYWORD_PHRASE_MATCH", valor: "gorra"}]},
  category_broad: {disponible: false, bid: null, recomendaciones: [],
    faltantes: [{tipo: "KEYWORD_BROAD_MATCH", valor: "gorra"}]},
  product_targeting: {disponible: false, bid: null, recomendaciones: [],
    faltantes: [{tipo: "PAT_ASIN", valor: "B0AAAAAAAA"}]},
  auto_discovery: {disponible: false, bid: null, recomendaciones: [],
    faltantes: []}}};
global.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url.includes("/catalogo")) return ok(catalogo);
  if (url.includes("/evaluacion")) return ok({plataforma: "amazon_mx", publicaciones: []});
  if (url.includes("/lotes?")) return ok({items: []});
  if (url.endsWith("/bids-sugeridos")) return ok(sugeridos);
  if (url.endsWith("/plan")) return ok(plan);
  return ok({});
};
const AVISO = "Amazon no devolvió sugerencia: puja manual";
(async () => {
  await docEvents.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  el("productos").querySelectorAll('input[type="checkbox"]')[0].checked = true;
  el("tipo").value = "gorras"; el("nombre").value = "Gorras"; el("modo").value = "shadow";
  el("objetivo-origen").value = "manual_lanzamiento";
  await emit("objetivo-origen", "change");
  el("objetivo-acos").value = "25.00";
  await emit("bids-amazon", "click");
  assert.equal(el("category_exact-bid-aviso").textContent, "",
    "con sugerencia no hay aviso");
  for (const rol of ["category_phrase", "category_broad", "product_targeting",
    "auto_discovery"]) {
    assert.match(el(rol + "-bid-aviso").textContent, /Amazon no devolvió sugerencia/,
      "el campo sin sugerencia lo dice junto al valor");
  }
  for (const rol of ["category_exact", "category_phrase", "category_broad",
    "product_targeting", "auto_discovery"]) {
    ids[rol + "-budget"].value = "120.00"; ids[rol + "-bid"].value = "11.00";
  }
  await emit("plan");
  const revision = text(el("preview-datos"));
  const veces = (revision.match(/Amazon no devolvió sugerencia: puja manual/g) || []).length;
  assert.equal(veces, 4, "la revision repite el aviso en las cuatro campanas manuales");
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
