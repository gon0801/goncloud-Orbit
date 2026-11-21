"""Flujo JS de productos sin clics (BIDS 02, P.4): ejecuta el JS real con DOM y fetch simulados."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from formato_js import con_formato

RAIZ = Path(__file__).resolve().parents[1]


def test_flujo_js_plan_lanza_y_repone_con_literales():
    """El JS pide el plan con los marcados, exige cada literal antes de
    mandar y reintenta tras un 409 con vista nueva. No requiere npm."""
    archivo = RAIZ / "app" / "static" / "js" / "productos.js"
    assert archivo.exists(), "Falta el cliente de productos"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(attrs = {}) {
    Object.assign(this, attrs);
    this.children = []; this.events = {}; this.value = attrs.value || "";
    this.checked = !!attrs.checked; this.hidden = !!attrs.hidden;
    this.textContent = ""; this.dataset = attrs.dataset || {};
    this.elements = attrs.elements || {};
  }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.children = items; }
  addEventListener(event, fn) { this.events[event] = fn; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const all = this.children.flatMap(c => [c, ...c.querySelectorAll("*")]);
    if (sel === "*") return all;
    if (sel === "[data-estado]") return all.filter(c => c.estado === true);
    const m = sel.match(/^\[data-([a-z-]+)(="([^"]+)")?\]$/);
    if (m) return all.filter(c => m[3] === undefined
      ? (c.dataset[m[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase())] !== undefined)
      : String(c.dataset[m[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase())]) === m[3]);
    return [];
  }
}
const root = new Element();
const casilla1 = new Element({dataset: {marcarCandidato: "", producto: "1", listings: "11"}});
casilla1.checked = true;
const casilla2 = new Element({dataset: {marcarCandidato: "", producto: "2", listings: "12"}});
casilla2.checked = true;
const sub1 = new Element({value: "aros"});
const sub2 = new Element({value: ""});
sub1.dataset = {subfamilia: "1"}; sub2.dataset = {subfamilia: "2"};
const zona = new Element(); zona.id = "productos-vista";
const bloque = new Element(); bloque.id = "productos-lanzamiento"; bloque.hidden = true;
const esperada = new Element(); esperada.id = "productos-esperada";
const estado = new Element(); estado.estado = true;
const form = new Element({dataset: {plataforma: "amazon_mx"}, hidden: true});
form.id = "productos-lanzar";
form.elements = {confirmacion: new Element(), actor: new Element({value: "dueno"}),
  token: new Element({value: "tok"})};
form.children = [estado];
root.children = [casilla1, casilla2, sub1, sub2, zona, bloque, esperada, form];
const zonaR = new Element(); const bloqueR = new Element(); bloqueR.hidden = true;
const esperadaR = new Element(); const estadoR2 = new Element(); estadoR2.estado = true;
const formR2 = new Element({dataset: {plataforma: "amazon_mx"}, hidden: true});
formR2.elements = {confirmacion: new Element(), actor: new Element({value: "dueno"}),
  token: new Element({value: "tok"})};
formR2.children = [estadoR2];
const casillaV = new Element({dataset: {marcarSinPedido: "", producto: "9", listings: "21"}});
casillaV.checked = true;
root.children.push(casillaV);
const ids = {"productos-vista": zona, "productos-lanzamiento": bloque,
  "productos-esperada": esperada, "productos-lanzar": form,
  "productos-ver-candidatos": new Element(), "productos-impulsar-aparte": new Element(),
  "productos-sacar": new Element(), "productos-retiro": bloqueR,
  "productos-retiro-vista": zonaR, "productos-retiro-esperada": esperadaR,
  "productos-retiro-ejecutar": formR2};
const docEvents = {};
global.document = {
  getElementById: id => ids[id] || null,
  createElement: () => new Element(),
  querySelector: sel => root.querySelector(sel),
  querySelectorAll: sel => root.querySelectorAll(sel),
  addEventListener: (event, fn) => { docEvents[event] = fn; },
};
const calls = [];
let vista = {huella: "a".repeat(64), confirmacion_esperada: "LANZAR IMPULSO DE 2 PRODUCTOS",
  planes: [{seller_sku: "S1", subfamilia: "aros",
    tope: {monto: "350", moneda: "MXN", presupuesto_diario: "25"},
    keywords_exact: ["k"], negatives: ["n"], bids_acotados: ["r1"],
    bid_por_rol: {category_exact: null, auto_discovery: "1.50"},
    retiros: [{ad_id_externo: "9"}]}], no_lanzables: [{listing_id: 99, motivo: "sin margen"}]};
let lanzarPrimero409 = true;
global.fetch = (url, opts) => {
  calls.push({url, body: JSON.parse(opts.body), token: opts.headers["x-orbit-token"]});
  if (url.endsWith("/impulso/plan"))
    return Promise.resolve({ok: true, json: () => Promise.resolve(vista)});
  if (url.endsWith("/impulso/lanzar")) {
    if (lanzarPrimero409) {
      lanzarPrimero409 = false;
      vista = {...vista, huella: "b".repeat(64)};
      return Promise.resolve({ok: false, status: 409,
        json: () => Promise.resolve({detail: {mensaje: "x", vista_previa: vista}})});
    }
    return Promise.resolve({ok: true, status: 200,
      json: () => Promise.resolve({lanzados: [{impulso_id: 1}, {impulso_id: 2}]})});
  }
  if (url.endsWith("/retiro/reponer") && JSON.parse(opts.body).incluye_adoptados)
    return Promise.resolve({ok: true, json: () => Promise.resolve({repuestos: 1, total: 1,
      anuncios: [{retiro_id: 7, ad_id_externo: "33", resultado: "repuesto", detalle: ""}],
      adoptados: [], requieren_recreacion: []})});
  if (url.endsWith("/retiro/reponer"))
    return Promise.resolve({ok: true, json: () => Promise.resolve({repuestos: 1, total: 3,
      anuncios: [{retiro_id: 5, ad_id_externo: "31", resultado: "repuesto", detalle: ""},
        {retiro_id: 6, ad_id_externo: "32", resultado: "requiere_recreacion",
         detalle: "ARCHIVED en Amazon"},
        {retiro_id: 7, ad_id_externo: "33", resultado: "adoptado_sin_reposicion",
         detalle: ""}],
      adoptados: [7], requieren_recreacion: [6]})});
  if (url.endsWith("/retiro/recrear"))
    return Promise.resolve({ok: true, json: () => Promise.resolve({recreados: 1, total: 1,
      anuncios: [{retiro_id: 6, ad_id_externo: "32", resultado: "recreado", detalle: ""}]})});
  if (url.endsWith("/retiro/plan"))
    return Promise.resolve({ok: true, json: () => Promise.resolve(
      {huella: "c".repeat(64), anuncios: [{sku: "SV", ad_group_nombre: "Viejos",
        ad_group_externo: "ag-v", ad_id_externo: "31"}]})});
  if (url.endsWith("/retiro/ejecutar"))
    return Promise.resolve({ok: true, json: () => Promise.resolve({retirados: 1, total: 1,
      anuncios: [{ad_id_externo: "31", resultado: "retirado", detalle: ""}]})});
  throw new Error("url inesperada " + url);
};
const codigo = fs.readFileSync(process.argv[1], "utf8");
global.module = {exports: {}};
vm.runInThisContext(codigo);
const api = global.module.exports;
(async () => {
  // Sin subfamilia en un marcado: no pide nada.
  api.verLanzamiento("[data-marcar-candidato]");
  assert.equal(calls.length, 0);
  assert.match(zona.children[0].textContent, /subfamilia/);
  // Con subfamilias: pide el plan con listings y mapa.
  sub2.value = "collares";
  api.verLanzamiento("[data-marcar-candidato]");
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].body.listing_ids, [11, 12]);
  assert.deepEqual(calls[0].body.subfamilia_por_listing, {"11": "aros", "12": "collares"});
  assert.equal(form.hidden, false);
  assert.equal(esperada.textContent, "LANZAR IMPULSO DE 2 PRODUCTOS");
  const texto = JSON.stringify(zona.children.flatMap(c => [c.textContent,
    ...c.children.map(p => p.textContent)]));
  assert.match(texto, /Pujas acotadas al tope: R1/);
  assert.match(texto, /No lanzable 99/);
  assert.match(texto, /Sin sugerencia/);
  assert.match(texto, /Exacta de la categoría/);
  // Literal distinto: no manda.
  form.elements.confirmacion.value = "LANZAR IMPULSO DE 3 PRODUCTOS";
  api.lanzar(form);
  assert.equal(calls.length, 1);
  // Literal exacto: manda con huella, actor y token; el 409 repinta.
  form.elements.confirmacion.value = "LANZAR IMPULSO DE 2 PRODUCTOS";
  api.lanzar(form);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].body.huella, "a".repeat(64));
  assert.equal(calls[1].body.actor, "dueno");
  assert.deepEqual(calls[1].body.solicitud, calls[0].body);
  assert.equal(calls[1].token, "tok");
  assert.match(estado.textContent, /cambió/);
  assert.equal(form.elements.token.value, "tok");
  // W-P: la confirmacion escrita era de la revision anterior y ya no vale.
  assert.equal(form.elements.confirmacion.value, "");
  api.lanzar(form);
  assert.equal(calls.length, 2);
  // Con el literal escrito sobre la vista nueva: exito.
  form.elements.confirmacion.value = "LANZAR IMPULSO DE 2 PRODUCTOS";
  api.lanzar(form);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls[2].body.huella, "b".repeat(64));
  assert.match(estado.textContent, /Lanzados 2/);
  assert.equal(form.elements.token.value, "");
  // Reponer: literal distinto no manda; exacto si. Dice n de m y ofrece
  // regresar el adoptado y recrear el archivado, cada uno con su literal.
  const estadoR = new Element(); estadoR.estado = true;
  const formR = new Element({dataset: {retiros: "5,6,7", esperada: "REGRESAR 3 ANUNCIOS"}});
  formR.elements = {confirmacion: new Element({value: "OTRO"}),
    actor: new Element({value: "dueno"}), token: new Element({value: "tok"})};
  formR.children = [estadoR];
  api.reponer(formR);
  assert.equal(calls.length, 3);
  formR.elements.confirmacion.value = "REGRESAR 3 ANUNCIOS";
  api.reponer(formR);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls[3].url, "/api/fabrica/retiro/reponer");
  assert.deepEqual(calls[3].body.retiro_ids, [5, 6, 7]);
  assert.equal(calls[3].body.incluye_adoptados, undefined);
  assert.match(estadoR.textContent, /Regresados 1 de 3/);
  assert.equal(formR.elements.token.value, "tok");
  const zonaRes = formR.querySelector("[data-resultado]");
  assert.match(zonaRes.children[0].textContent, /32 · archivado en Amazon/);
  assert.match(zonaRes.children[1].textContent, /33 · adoptado/);
  const [avisoAdo, literalAdo, botonAdo, estadoAdo] = zonaRes.children[2].children;
  const [avisoRec, literalRec, botonRec, estadoRec] = zonaRes.children[3].children;
  assert.match(avisoAdo.textContent, /REGRESAR 1 ANUNCIOS/);
  assert.match(avisoRec.textContent, /RECREAR 1 ANUNCIOS/);
  // Regresar el adoptado: manda incluye_adoptados y no borra el paso de recrear.
  literalAdo.value = "REGRESAR 1 ANUNCIOS";
  botonAdo.events.click();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(calls[4].body, {retiro_ids: [7], confirmacion: "REGRESAR 1 ANUNCIOS",
    actor: "dueno", incluye_adoptados: true});
  assert.match(estadoAdo.textContent, /Regresados 1 de 1/);
  assert.equal(zonaRes.children[3].children[2], botonRec);
  assert.equal(formR.elements.token.value, "tok");
  literalRec.value = "RECREAR 2 ANUNCIOS";
  botonRec.events.click();
  assert.equal(calls.length, 5);
  literalRec.value = "RECREAR 1 ANUNCIOS";
  botonRec.events.click();
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls[5].url, "/api/fabrica/retiro/recrear");
  assert.deepEqual(calls[5].body,
    {retiro_ids: [6], confirmacion: "RECREAR 1 ANUNCIOS", actor: "dueno"});
  assert.equal(calls[5].token, "tok");
  assert.match(estadoRec.textContent, /Recreados 1 de 1/);
  assert.equal(formR.elements.token.value, "");
  calls.splice(4, 2);
  // Sacar de las bolsas: plan sin_pedidos sin lote, muestra anuncios, exige literal.
  api.verRetiro();
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls[4].url, "/api/fabrica/retiro/plan");
  assert.deepEqual(calls[4].body,
    {plataforma: "amazon_mx", listing_ids: [21], motivo: "sin_pedidos"});
  assert.match(JSON.stringify(zonaR.children.map(p => p.textContent)), /SV · Viejos/);
  assert.equal(esperadaR.textContent, "SACAR 1 ANUNCIOS");
  formR2.elements.confirmacion.value = "SACAR 2 ANUNCIOS";
  api.ejecutarRetiro(formR2);
  assert.equal(calls.length, 5);
  formR2.elements.confirmacion.value = "SACAR 1 ANUNCIOS";
  api.ejecutarRetiro(formR2);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls[5].url, "/api/fabrica/retiro/ejecutar");
  assert.equal(calls[5].body.huella, "c".repeat(64));
  assert.equal(calls[5].body.actor, "dueno");
  assert.deepEqual(calls[5].body.listing_ids, [21]);
  assert.equal(calls[5].body.plataforma, "amazon_mx");
  assert.equal(calls[5].token, "tok");
  assert.match(estadoR2.textContent, /Retirados 1 de 1/);
  assert.equal(formR2.elements.token.value, "");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    proc = subprocess.run(
        [node, "-e", con_formato(guion), str(archivo)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_r09d_error_objeto_muestra_mensaje_y_fallos_sin_object_object():
    """R09-D: un 502 parcial (detail objeto con mensaje y fallos por
    producto) se pinta con su mensaje y cada fallo, nunca como
    [object Object]; un detail de texto se pinta tal cual."""
    archivo = RAIZ / "app" / "static" / "js" / "productos.js"
    assert archivo.exists(), "Falta el cliente de productos"
    # Los doce sitios de error usan mensajeError (cinco de impulso y retiro:
    # pedir la previa vale para los dos flujos, mas lanzar, ejecutar el retiro
    # y regresar/recrear; cuatro de los limites de la pausa: el rechazo de
    # simular y su estado, 409 y 422 al guardar; tres del encendido: la
    # simulacion que falla, 409 y 422 al confirmar):
    # ninguno conserva el patron que pintaba [object Object].
    assert "(r.data.detail || r.data)" not in archivo.read_text(encoding="utf-8")
    assert archivo.read_text(encoding="utf-8").count("mensajeError(r.data)") == 12
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(attrs = {}) {
    Object.assign(this, attrs);
    this.children = []; this.events = {}; this.value = attrs.value || "";
    this.checked = !!attrs.checked; this.hidden = !!attrs.hidden;
    this.textContent = ""; this.dataset = attrs.dataset || {};
    this.elements = attrs.elements || {};
  }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.children = items; }
  addEventListener(event, fn) { this.events[event] = fn; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const all = this.children.flatMap(c => [c, ...c.querySelectorAll("*")]);
    if (sel === "*") return all;
    if (sel === "[data-estado]") return all.filter(c => c.estado === true);
    const m = sel.match(/^\[data-([a-z-]+)(="([^"]+)")?\]$/);
    if (m) return all.filter(c => m[3] === undefined
      ? (c.dataset[m[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase())] !== undefined)
      : String(c.dataset[m[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase())]) === m[3]);
    return [];
  }
}
const root = new Element();
const casilla = new Element({dataset: {marcarCandidato: "", producto: "1", listings: "11"}});
casilla.checked = true;
const sub = new Element({value: "aros"});
sub.dataset = {subfamilia: "1"};
const zona = new Element(); zona.id = "productos-vista";
const bloque = new Element(); bloque.id = "productos-lanzamiento"; bloque.hidden = true;
const esperada = new Element(); esperada.id = "productos-esperada";
const estado = new Element(); estado.estado = true;
const form = new Element({dataset: {plataforma: "amazon_mx"}, hidden: true});
form.id = "productos-lanzar";
form.elements = {confirmacion: new Element(), actor: new Element({value: "dueno"}),
  token: new Element({value: "tok"})};
form.children = [estado];
const casillaV = new Element({dataset: {marcarSinPedido: "", producto: "9", listings: "21"}});
casillaV.checked = true;
const zonaR = new Element(); zonaR.id = "productos-retiro-vista";
const bloqueR = new Element(); bloqueR.id = "productos-retiro"; bloqueR.hidden = true;
const esperadaR = new Element(); esperadaR.id = "productos-retiro-esperada";
const estadoR = new Element(); estadoR.estado = true;
const formR = new Element({dataset: {plataforma: "amazon_mx"}, hidden: true});
formR.id = "productos-retiro-ejecutar";
formR.elements = {confirmacion: new Element(), actor: new Element({value: "dueno"}),
  token: new Element({value: "tok"})};
formR.children = [estadoR];
root.children = [casilla, sub, zona, bloque, esperada, form, casillaV, zonaR, bloqueR,
  esperadaR, formR];
const ids = {"productos-vista": zona, "productos-lanzamiento": bloque,
  "productos-esperada": esperada, "productos-lanzar": form,
  "productos-retiro-vista": zonaR, "productos-retiro": bloqueR,
  "productos-retiro-esperada": esperadaR, "productos-retiro-ejecutar": formR};
global.document = {
  getElementById: id => ids[id] || null,
  createElement: () => new Element(),
  querySelector: sel => root.querySelector(sel),
  querySelectorAll: sel => root.querySelectorAll(sel),
  addEventListener: () => {},
};
global.fetch = (url, opts) => {
  if (url.endsWith("/impulso/plan"))
    return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(
      {huella: "a".repeat(64), confirmacion_esperada: "LANZAR IMPULSO DE 1 PRODUCTO",
       planes: [{seller_sku: "S1", subfamilia: "aros",
         tope: {monto: "350", moneda: "MXN", presupuesto_diario: "25"},
         keywords_exact: [], negatives: [], bids_acotados: [],
         bid_por_rol: {}, retiros: []}], no_lanzables: []})});
  if (url.endsWith("/impulso/lanzar"))
    return Promise.resolve({ok: false, status: 502, json: () => Promise.resolve(
      {detail: {mensaje: "algunos productos no se lanzaron",
        lanzados: [{impulso_id: 7, lote: "impulso-12-x", estado_lote: "applied"}],
        fallos: {"11": "sin margen medido", "12": "lote en failed: reconcilia a mano"}}})});
  if (url.endsWith("/retiro/plan"))
    return Promise.resolve({ok: false, status: 422, json: () => Promise.resolve(
      {detail: "El lote no existe."})});
  throw new Error("url inesperada " + url);
};
const codigo = fs.readFileSync(process.argv[1], "utf8");
global.module = {exports: {}};
vm.runInThisContext(codigo);
const api = global.module.exports;
(async () => {
  api.verLanzamiento("[data-marcar-candidato]");
  await new Promise(r => setTimeout(r, 10));
  assert.equal(form.hidden, false);
  form.elements.confirmacion.value = "LANZAR IMPULSO DE 1 PRODUCTO";
  api.lanzar(form);
  await new Promise(r => setTimeout(r, 10));
  assert.match(estado.textContent, /algunos productos no se lanzaron/);
  assert.match(estado.textContent, /lanzados: 1/);
  assert.match(estado.textContent, /sin margen medido/);
  assert.match(estado.textContent, /reconcilia a mano/);
  assert.doesNotMatch(estado.textContent, /\[object Object\]/);
  api.verRetiro();
  await new Promise(r => setTimeout(r, 10));
  const textoR = JSON.stringify(zonaR.children.map(p => p.textContent));
  assert.match(textoR, /El lote no existe\./);
  assert.doesNotMatch(textoR, /\[object Object\]/);
  global.fetch = (url, opts) => {
    if (url.endsWith("/impulso/plan"))
      return Promise.resolve({ok: false, status: 422, json: () => Promise.resolve(
        {detail: {mensaje: "sin candidatos con margen"}})});
    throw new Error("url inesperada " + url);
  };
  api.verLanzamiento("[data-marcar-candidato]");
  await new Promise(r => setTimeout(r, 10));
  const textoV = JSON.stringify(zona.children.map(p => p.textContent));
  assert.match(textoV, /sin candidatos con margen/);
  assert.doesNotMatch(textoV, /\[object Object\]/);
  global.fetch = (url, opts) => {
    if (url.endsWith("/impulso/plan"))
      return Promise.resolve({ok: false, status: 422, json: () => Promise.resolve(
        {detail: [{msg: "listing_ids: falta el campo"}, {msg: "tope: fuera de rango"}]})});
    throw new Error("url inesperada " + url);
  };
  api.verLanzamiento("[data-marcar-candidato]");
  await new Promise(r => setTimeout(r, 10));
  const textoA = JSON.stringify(zona.children.map(p => p.textContent));
  assert.match(textoA, /listing_ids: falta el campo; tope: fuera de rango/);
  assert.doesNotMatch(textoA, /\[object Object\]/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    proc = subprocess.run(
        [node, "-e", con_formato(guion), str(archivo)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_r09r3_js_pantalla_us_pide_plan_en_us():
    """R09 B12 (J1): en la pantalla de US, el plan se pide en amazon_us."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    archivo = raiz / "app" / "static" / "js" / "productos.js"
    assert archivo.exists(), "Falta el cliente de productos"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(attrs = {}) {
    Object.assign(this, attrs);
    this.children = []; this.events = {}; this.value = attrs.value || "";
    this.checked = !!attrs.checked; this.hidden = !!attrs.hidden;
    this.textContent = ""; this.dataset = attrs.dataset || {};
    this.elements = attrs.elements || {};
  }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.children = items; }
  addEventListener(event, fn) { this.events[event] = fn; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const all = this.children.flatMap(c => [c, ...c.querySelectorAll("*")]);
    if (sel === "*") return all;
    const m = sel.match(/^\[data-([a-z-]+)(="([^"]+)")?\]$/);
    if (m) return all.filter(c => m[3] === undefined
      ? (c.dataset[m[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase())] !== undefined)
      : String(c.dataset[m[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase())]) === m[3]);
    return [];
  }
}
const root = new Element();
const casilla = new Element({dataset: {marcarCandidato: "", producto: "1", listings: "11"}});
casilla.checked = true;
const sub = new Element({value: "aros"});
sub.dataset = {subfamilia: "1"};
const zona = new Element(); zona.id = "productos-vista";
const bloque = new Element(); bloque.id = "productos-lanzamiento"; bloque.hidden = true;
const esperada = new Element(); esperada.id = "productos-esperada";
const estado = new Element();
const form = new Element({dataset: {plataforma: "amazon_us"}, hidden: true});
form.id = "productos-lanzar";
form.elements = {confirmacion: new Element(), actor: new Element({value: "dueno"}),
  token: new Element({value: "tok"})};
form.children = [estado];
root.children = [casilla, sub, zona, bloque, esperada, form];
const ids = {"productos-vista": zona, "productos-lanzamiento": bloque,
  "productos-esperada": esperada, "productos-lanzar": form};
global.document = {
  getElementById: id => ids[id] || null,
  createElement: () => new Element(),
  querySelector: sel => root.querySelector(sel),
  querySelectorAll: sel => root.querySelectorAll(sel),
  addEventListener: () => {},
};
const calls = [];
global.fetch = (url, opts) => {
  calls.push({url, body: JSON.parse(opts.body)});
  return Promise.resolve({ok: true, json: () => Promise.resolve(
    {huella: "a".repeat(64), confirmacion_esperada: "LANZAR IMPULSO DE 1 PRODUCTO",
     planes: [], no_lanzables: []})});
};
const codigo = fs.readFileSync(process.argv[1], "utf8");
global.module = {exports: {}};
vm.runInThisContext(codigo);
const api = global.module.exports;
(async () => {
  api.verLanzamiento("[data-marcar-candidato]");
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.plataforma, "amazon_us");
  assert.deepEqual(calls[0].body.listing_ids, [11]);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    proc = subprocess.run(
        [node, "-e", con_formato(guion), str(archivo)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_r09r4_t01_t07_retiros_abiertos_form_ids_escape():
    """R09-r4 B6 (T01-T07): la tabla de retiros abiertos pinta cada
    form con su retiro_id (T01), enganche JS (T07) y literal 1 (T02);
    pinta todos (T05) con su motivo (T06); sku y adId escapados
    (T03/T04 XSS). Render puro, sin base."""
    from types import SimpleNamespace

    from app.ui import templates

    env = templates.env
    html = env.get_template("productos_sin_clics.html").render(
        plataforma="amazon_mx",
        pantalla="productos-sin-clics",
        asset_version="t",
        candidatos=[],
        impulsos=[],
        sin_pedido=[],
        anuncios_sin_producto=0,
        retiros_abiertos=[
            SimpleNamespace(
                retiro_id=7,
                motivo="subfamilia",
                ad_id_externo="ad-<b>x",
                sku="<script>alert(1)</script>",
                retirado_el="hoy",
            ),
            SimpleNamespace(
                retiro_id=9,
                motivo="sin_pedidos",
                ad_id_externo="ad-9",
                sku="SS-9",
                retirado_el="hoy",
            ),
        ],
    )
    assert 'data-reponer data-retiros="7"' in html
    assert 'data-reponer data-retiros="9"' in html
    assert html.count('data-esperada="REGRESAR 1 ANUNCIOS"') == 2
    assert html.count("data-reponer") == 2
    assert "<td>Su subfamilia se retira</td>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "ad-<b>x" not in html and "ad-&lt;b&gt;x" in html


def test_guardar_limites_por_omision_sin_claves_si_envia():
    """h_c4a60451: sin las dos claves de limites en la config, guardar los
    valores por omision tal cual SI manda el POST (S8 exige los limites
    guardados antes de encender); con las claves ya guardadas e iguales, no
    se envia nada."""
    archivo = RAIZ / "app" / "static" / "js" / "productos.js"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const codigo = fs.readFileSync(process.argv[1], "utf8");
async function corre(guardados) {
  const handlers = {};
  const nodo = (attrs) => {
    const obj = {...attrs, _ev: {}};
    obj.addEventListener = (ev, fn) => { obj._ev[ev] = fn; };
    return obj;
  };
  const clics = nodo({value: "100", defaultValue: "100"});
  const gasto = nodo({value: "500", defaultValue: "500"});
  const campos = nodo({dataset: {plataforma: "amazon_mx", moneda: "MXN"},
    elements: {clics, gasto}});
  const estado = {textContent: ""};
  const token = {value: "tok"};
  const guardar = nodo({dataset: {plataforma: "amazon_mx", configId: "44", guardados},
    elements: {token}, querySelector: (sel) => sel === "[data-estado]" ? estado : null});
  const ids = {"limites-simular": campos, "limites-guardar": guardar};
  const llamadas = [];
  const ctx = {
    document: {
      getElementById: (id) => ids[id] || null,
      addEventListener: (ev, fn) => { handlers[ev] = fn; },
      querySelector: () => null, querySelectorAll: () => [],
      createElement: () => ({}),
    },
    window: {location: {reload: () => {}}},
    fetch: (url, opts) => {
      llamadas.push({url, body: JSON.parse(opts.body), token: opts.headers["x-orbit-token"]});
      return Promise.resolve({ok: true, status: 200,
        json: () => Promise.resolve({config_version_id: 45})});
    },
    console,
  };
  vm.createContext(ctx);
  vm.runInContext(codigo, ctx);
  handlers.DOMContentLoaded();
  guardar._ev.submit({preventDefault: () => {}});
  await new Promise((r) => setTimeout(r, 10));
  return {llamadas, estado: estado.textContent};
}
(async () => {
  const sin = await corre("false");
  assert.equal(sin.llamadas.length, 1);
  assert.equal(sin.llamadas[0].url, "/api/ads-optimizer/settings/amazon_mx");
  assert.equal(sin.llamadas[0].token, "tok");
  assert.deepEqual(sin.llamadas[0].body, {base_config_version_id: 44,
    limites_pausa: {clics: 100, gasto: {valor: "500", moneda: "MXN"}}});
  const con = await corre("true");
  assert.equal(con.llamadas.length, 0);
  assert.equal(con.estado, "Nada que cambiar.");
})().catch((e) => { console.error(e); process.exit(1); });
"""
    proc = subprocess.run(
        [node, "-e", con_formato(guion), str(archivo)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def _render_limites(limites: dict) -> str:
    from app.ui import templates

    env = templates.env
    html = env.get_template("productos_sin_clics.html").render(
        plataforma="amazon_mx",
        pantalla="productos-sin-clics",
        asset_version="t",
        candidatos=[],
        impulsos=[],
        sin_pedido=[],
        anuncios_sin_producto=0,
        retiros_abiertos=[],
        decisiones_producto={"cycle_id": None, "limites_pausa": limites},
    )
    inicio = html.index('id="productos-limites"')
    return html[inicio : html.index('id="productos-retiro"')]


_LIMITES_BASE = {
    "config_version_id": 44,
    "clics": 100,
    "gasto": "500",
    "gasto_texto": "500.00",
    "moneda": "MXN",
    "error": None,
    "modo_error": None,
}


def test_encender_boton_deshabilitado_sin_limites_y_volver_en_vivo():
    """I.8: sin las dos claves guardadas el boton "Encender la pausa" sale
    deshabilitado con "Guarda tus límites primero"; con ellas, habilitado;
    con la pausa en vivo el estado es "En vivo" y el boton es "Volver a
    práctica". El modo es el de la config vigente."""
    sin = _render_limites(
        {**_LIMITES_BASE, "guardados": False, "modo": "shadow", "modo_texto": "Práctica"}
    )
    assert 'data-modo-vigente="shadow">Práctica</span>' in sin
    assert (
        '<button type="button" id="pausa-encender" disabled'
        ' aria-describedby="pausa-encender-falta">Encender la pausa</button>' in sin
    )
    assert '<p id="pausa-encender-falta" data-falta-limites>Guarda tus límites primero</p>' in sin
    assert 'data-config-id="44"' in sin and 'id="pausa-volver"' not in sin
    assert 'type="password" id="pausa-token"' in sin and ">Confirmar</button>" in sin
    assert ">Cancelar</button>" in sin

    con = _render_limites(
        {**_LIMITES_BASE, "guardados": True, "modo": "shadow", "modo_texto": "Práctica"}
    )
    assert '<button type="button" id="pausa-encender">Encender la pausa</button>' in con
    assert "Guarda tus límites primero" not in con

    vivo = _render_limites(
        {**_LIMITES_BASE, "guardados": True, "modo": "live", "modo_texto": "En vivo"}
    )
    assert 'class="chip ok" data-modo-vigente="live">En vivo</span>' in vivo
    assert '<button type="button" id="pausa-volver">Volver a práctica</button>' in vivo
    assert 'id="pausa-encender"' not in vivo

    ilegible = _render_limites(
        {
            **_LIMITES_BASE,
            "guardados": False,
            "modo": None,
            "modo_texto": None,
            "modo_error": "setting ads_producto_pausa_amazon_mx: debe ser shadow o live",
        }
    )
    assert 'id="pausa-vivo"' not in ilegible and "data-pausa-modo-error" in ilegible


def test_encender_js_simula_confirma_cancela_y_vuelve_a_practica():
    """I.8 con el JS real: deshabilitado no hace nada; encender pide la
    simulacion con los limites guardados (sin clics ni gasto), muestra
    anuncios, productos y gasto, y solo Confirmar manda el POST con el token
    y el cuerpo exacto; Cancelar no manda POST; Volver a practica confirma y
    manda shadow; 422 muestra el motivo y 409 pide recargar."""
    archivo = RAIZ / "app" / "static" / "js" / "productos.js"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const codigo = fs.readFileSync(process.argv[1], "utf8");
const MOTIVO = "la pausa en vivo exige guardar antes los limites de clics y gasto de amazon_mx";
const SIM = {
  limites: {clics_min: 100, gasto_min: {tipo: "Dinero", valor: "500", moneda: "MXN"}},
  ventana: {desde: "2026-09-08", hasta_exclusivo: "2026-10-08"},
  productos: [{producto: {product_id: 7}, gasto: {valor: "600", moneda: "MXN"},
    clics: 150, anuncios: 2}],
  anuncios_total: 2,
  gasto_total: {tipo: "Dinero", valor: "600.0000", moneda: "MXN"},
  sin_cobertura_d7: 1,
};
function texto(el) {
  return [el.textContent || "", ...(el.children || []).map(texto)].join(" ");
}
async function escena({modo = "shadow", disabled = false, respuestaPost = {ok: true, status: 200,
    data: {modo: "live"}}} = {}) {
  const handlers = {};
  const el = (attrs = {}) => {
    const o = {hidden: false, textContent: "", dataset: {}, children: [], _ev: {}, ...attrs};
    o.addEventListener = (ev, fn) => { o._ev[ev] = fn; };
    o.replaceChildren = (...xs) => { o.children = xs; };
    o.appendChild = (x) => { o.children.push(x); return x; };
    o.focus = () => { foco.actual = o; };
    return o;
  };
  const foco = {actual: null};
  const caja = el({dataset: {plataforma: "amazon_mx", configId: "44", modo}});
  const encender = modo === "live" ? null : el({disabled});
  const volver = modo === "live" ? el() : null;
  const confirmar = el({hidden: true});
  const titulo = el();
  const detalle = el();
  const token = el({value: ""});
  const form = el({hidden: true, elements: {token}});
  const cancelar = el();
  const estado = el();
  const ids = {"pausa-vivo": caja, "pausa-encender": encender, "pausa-volver": volver,
    "pausa-confirmar": confirmar, "pausa-confirmar-detalle": detalle,
    "pausa-confirmar-form": form, "pausa-cancelar": cancelar, "pausa-vivo-estado": estado};
  const llamadas = [];
  let recargas = 0;
  const ctx = {
    document: {
      getElementById: (id) => ids[id] || null,
      addEventListener: (ev, fn) => { handlers[ev] = fn; },
      querySelector: (sel) => sel === "#pausa-confirmar-titulo strong" ? titulo : null,
      querySelectorAll: () => [],
      createElement: (tag) => el({tag}),
    },
    window: {location: {reload: () => { recargas += 1; }}},
    fetch: (url, opts) => {
      if (!opts) {
        llamadas.push({url, metodo: "GET"});
        return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(SIM)});
      }
      llamadas.push({url, metodo: opts.method, token: opts.headers["x-orbit-token"],
        body: JSON.parse(opts.body)});
      const r = respuestaPost;
      return Promise.resolve({ok: r.ok, status: r.status, json: () => Promise.resolve(r.data)});
    },
    console,
    Orbit,
  };
  vm.createContext(ctx);
  vm.runInContext(codigo, ctx);
  handlers.DOMContentLoaded();
  const espera = () => new Promise((r) => setTimeout(r, 10));
  return {caja, encender, volver, confirmar, titulo, detalle, token, form, cancelar, estado,
    llamadas, foco, espera, recargas: () => recargas};
}
(async () => {
  // Deshabilitado: el clic no simula ni abre la confirmacion.
  let e = await escena({disabled: true});
  e.encender._ev.click();
  await e.espera();
  assert.equal(e.llamadas.length, 0);
  assert.equal(e.confirmar.hidden, true);

  // Encender -> simulacion -> Confirmar: POST con token y cuerpo exacto.
  e = await escena();
  e.encender._ev.click();
  assert.equal(e.confirmar.hidden, false);
  assert.equal(e.form.hidden, true, "sin simulacion no hay Confirmar");
  await e.espera();
  assert.deepEqual(e.llamadas, [{url: "/api/dashboard/productos/simula-pausa?platform=amazon_mx",
    metodo: "GET"}]);
  const resumen = texto(e.detalle);
  assert.match(resumen, /pausaría 2 anuncios de 1 producto, que gastaron 600\.00 MXN/);
  assert.match(resumen, /100 clics y 500\.00 MXN/);
  assert.match(resumen, /del 2026-09-08 al 2026-10-07/);
  assert.match(resumen, /1 producto no se puede juzgar/);
  assert.equal(e.titulo.textContent, "¿Encender la pausa?");
  assert.equal(e.form.hidden, false);
  assert.equal(e.foco.actual, e.token);
  e.token.value = "tok";
  e.form._ev.submit({preventDefault: () => {}});
  await e.espera();
  assert.equal(e.llamadas.length, 2);
  assert.deepEqual(e.llamadas[1], {url: "/api/ads-optimizer/productos/pausa/amazon_mx",
    metodo: "POST", token: "tok", body: {modo: "live", base_config_version_id: 44}});
  assert.equal(e.recargas(), 1);
  assert.equal(e.token.value, "");

  // Cancelar no escribe y devuelve el foco al boton.
  e = await escena();
  e.encender._ev.click();
  await e.espera();
  e.token.value = "tok";
  e.cancelar._ev.click();
  await e.espera();
  assert.equal(e.llamadas.filter((l) => l.metodo === "POST").length, 0);
  assert.equal(e.confirmar.hidden, true);
  assert.equal(e.token.value, "");
  assert.equal(e.estado.textContent, "Cancelado: no se cambió nada.");
  assert.equal(e.foco.actual, e.encender);
  e.form._ev.submit({preventDefault: () => {}});
  await e.espera();
  assert.equal(e.llamadas.filter((l) => l.metodo === "POST").length, 0,
    "despues de cancelar, un submit suelto no manda nada");

  // Volver a practica: confirmacion sin simulacion y POST shadow.
  e = await escena({modo: "live", respuestaPost: {ok: true, status: 200, data: {modo: "shadow"}}});
  e.volver._ev.click();
  assert.equal(e.confirmar.hidden, false);
  assert.equal(e.form.hidden, false);
  assert.equal(e.titulo.textContent, "¿Volver a práctica?");
  e.token.value = "tok";
  e.form._ev.submit({preventDefault: () => {}});
  await e.espera();
  assert.deepEqual(e.llamadas, [{url: "/api/ads-optimizer/productos/pausa/amazon_mx",
    metodo: "POST", token: "tok", body: {modo: "shadow", base_config_version_id: 44}}]);
  assert.equal(e.recargas(), 1);

  // 422: el motivo del servidor, sin recargar.
  e = await escena({respuestaPost: {ok: false, status: 422,
    data: {detail: MOTIVO}}});
  e.encender._ev.click();
  await e.espera();
  e.token.value = "tok";
  e.form._ev.submit({preventDefault: () => {}});
  await e.espera();
  assert.equal(e.estado.textContent, "No se cambió: " + MOTIVO + ".");
  assert.equal(e.recargas(), 0);

  // 409: pide recargar.
  e = await escena({modo: "live", respuestaPost: {ok: false, status: 409,
    data: {detail: "la config vigente es 45, no 44"}}});
  e.volver._ev.click();
  e.token.value = "tok";
  e.form._ev.submit({preventDefault: () => {}});
  await e.espera();
  assert.match(e.estado.textContent, /la config vigente es 45, no 44.*Recarga la página\./);
  assert.equal(e.recargas(), 0);
})().catch((err) => { console.error(err); process.exit(1); });
"""
    proc = subprocess.run(
        [node, "-e", con_formato(guion), str(archivo)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_arreglos04_w_m_ver_retiro_avisa_vendidos_y_libro_viejo():
    """W-M.3: verRetiro explica cuando las ventas del libro no estan al dia
    (con fecha o sin primera carga) y cuando alguno de los productos marcados
    vendio en los ultimos 11 meses, antes de pintar los anuncios."""
    archivo = RAIZ / "app" / "static" / "js" / "productos.js"
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(attrs = {}) {
    Object.assign(this, attrs);
    this.children = []; this.events = {}; this.value = attrs.value || "";
    this.checked = !!attrs.checked; this.hidden = !!attrs.hidden;
    this.textContent = ""; this.dataset = attrs.dataset || {};
    this.elements = attrs.elements || {};
  }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.children = items; }
  addEventListener(event, fn) { this.events[event] = fn; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const all = this.children.flatMap(c => [c, ...c.querySelectorAll("*")]);
    if (sel === "*") return all;
    if (sel === "[data-estado]") return all.filter(c => c.estado === true);
    if (sel === "[data-marcar-sin-pedido]")
      return all.filter(c => c.dataset.marcarSinPedido !== undefined);
    return [];
  }
}
const root = new Element();
const casilla = new Element({dataset: {marcarSinPedido: "", producto: "9", listings: "21,22"}});
casilla.checked = true;
const casilla2 = new Element({dataset: {marcarSinPedido: "", producto: "10", listings: "23"}});
casilla2.checked = true;
const zonaR = new Element();
const bloqueR = new Element({hidden: true});
const esperadaR = new Element();
const estadoR = new Element({estado: true});
const formR = new Element({dataset: {plataforma: "amazon_mx"}, hidden: true});
formR.elements = {
  confirmacion: new Element(),
  actor: new Element({value: "dueno"}),
  token: new Element({value: "tok"}),
};
formR.children = [estadoR];
root.children = [casilla, casilla2, zonaR, bloqueR, esperadaR, formR];
const ids = {
  "productos-retiro": bloqueR,
  "productos-retiro-vista": zonaR,
  "productos-retiro-esperada": esperadaR,
  "productos-retiro-ejecutar": formR,
};
global.document = {
  getElementById: id => ids[id] || null,
  createElement: () => new Element(),
  querySelector: sel => root.querySelector(sel),
  querySelectorAll: sel => root.querySelectorAll(sel),
  addEventListener: () => {},
};
let respuestaPlan = null;
const ejecuciones = [];
global.fetch = (url, opts) => {
  if (url.endsWith("/retiro/plan"))
    return Promise.resolve({ok: true, json: () => Promise.resolve(respuestaPlan)});
  if (url.endsWith("/retiro/ejecutar")) {
    ejecuciones.push(JSON.parse(opts.body));
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({retirados: 0, total: 0, anuncios: []}),
    });
  }
  throw new Error("url inesperada " + url);
};
const codigo = fs.readFileSync(process.argv[1], "utf8");
global.module = {exports: {}};
vm.runInThisContext(codigo);
const api = global.module.exports;
(async () => {
  respuestaPlan = {
    huella: "0".repeat(64),
    anuncios: [],
    vendieron: [],
    ventas_al_dia: false,
    ventas_al_dia_hasta: "2026-11-08T17:58:03+00:00",
  };
  api.verRetiro();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(
    zonaR.children.map(p => p.textContent),
    [
      "No se sacó ningún anuncio: las ventas de la contabilidad no se actualizan desde el " +
      "8 nov, 11:58, así que Orbit no puede comprobar que estos productos no vendieron. " +
      "Vuelve a intentarlo cuando se actualicen.",
    ]
  );
  assert.equal(formR.hidden, true);
  assert.equal(bloqueR.hidden, false);
  formR.elements.confirmacion.value = "SACAR 0 ANUNCIOS";
  api.ejecutarRetiro(formR);
  assert.equal(ejecuciones.length, 0);

  respuestaPlan = {
    huella: "0".repeat(64),
    anuncios: [],
    vendieron: [],
    ventas_al_dia: false,
    ventas_al_dia_hasta: null,
  };
  api.verRetiro();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(
    zonaR.children.map(p => p.textContent),
    [
      "No se sacó ningún anuncio: las ventas de la contabilidad todavía no se han cargado, " +
      "así que Orbit no puede comprobar que estos productos no vendieron. " +
      "Vuelve a intentarlo cuando se actualicen.",
    ]
  );
  assert.equal(formR.hidden, true);

  respuestaPlan = {
    huella: "0".repeat(64),
    anuncios: [],
    vendieron: [21],
    ventas_al_dia: true,
    ventas_al_dia_hasta: null,
  };
  api.verRetiro();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(
    zonaR.children.map(p => p.textContent),
    [
      "1 de los productos marcados vendió en los últimos 11 meses: " +
      "se conservan los anuncios de 1 publicación.",
      "Nada que sacar: sin anuncios en bolsas viejas.",
    ]
  );
  assert.equal(formR.hidden, true);

  respuestaPlan = {
    huella: "c".repeat(64),
    anuncios: [
      {sku: "SV", ad_group_nombre: "Viejos", ad_group_externo: "ag-v", ad_id_externo: "31"},
    ],
    vendieron: [21, 22],
    ventas_al_dia: true,
    ventas_al_dia_hasta: null,
  };
  api.verRetiro();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(
    zonaR.children.map(p => p.textContent),
    [
      "1 de los productos marcados vendió en los últimos 11 meses: " +
      "se conservan los anuncios de 2 publicaciones.",
      "SV · Viejos",
    ]
  );
  assert.equal(formR.hidden, false);
  assert.equal(esperadaR.textContent, "SACAR 1 ANUNCIOS");

  respuestaPlan = {
    huella: "d".repeat(64),
    anuncios: [
      {sku: "SV", ad_group_nombre: "Viejos", ad_group_externo: "ag-v", ad_id_externo: "32"},
    ],
    vendieron: [21, 23],
    ventas_al_dia: true,
    ventas_al_dia_hasta: null,
  };
  api.verRetiro();
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(
    zonaR.children.map(p => p.textContent),
    [
      "2 de los productos marcados vendieron en los últimos 11 meses: " +
      "se conservan los anuncios de 2 publicaciones.",
      "SV · Viejos",
    ]
  );
})().catch(err => { console.error(err); process.exit(1); });
"""
    proc = subprocess.run(
        [node, "-e", con_formato(guion), str(archivo)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ARREGLOS 04 W-P (TN04): la previa visible es la unica solicitud ejecutable.
# Preludio compartido por los tres guiones: DOM minimo, transporte que se
# resuelve a mano y el estado por flujo que expone productos.js.
_PRELUDE_W_P = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(attrs = {}) {
    Object.assign(this, attrs);
    this.children = []; this.events = {}; this.value = attrs.value || "";
    this.checked = !!attrs.checked; this.hidden = !!attrs.hidden;
    this.textContent = ""; this.dataset = attrs.dataset || {};
    this.elements = attrs.elements || {};
  }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.append(item); return item; }
  replaceChildren(...items) { this.children = items; }
  addEventListener(event, fn) { this.events[event] = fn; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const all = this.children.flatMap(c => [c, ...c.querySelectorAll("*")]);
    if (sel === "*") return all;
    if (sel === "[data-estado]") return all.filter(c => c.estado === true);
    if (sel === "[data-marcar-sin-pedido]")
      return all.filter(c => c.dataset.marcarSinPedido !== undefined);
    if (sel === "[data-marcar-candidato]")
      return all.filter(c => c.dataset.marcarCandidato !== undefined);
    if (sel === "[data-subfamilia]")
      return all.filter(c => c.dataset.subfamilia !== undefined);
    const m = sel.match(/^\[data-([a-z-]+)(="([^"]+)")?\]$/);
    if (m) return all.filter(c => m[3] === undefined
      ? (c.dataset[m[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase())] !== undefined)
      : String(c.dataset[m[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase())]) === m[3]);
    return [];
  }
}
const root = new Element();
const casillaA = new Element({dataset: {ATRIBUTO: "", producto: "1", listings: "LISTINGS_A"}});
const casillaB = new Element({dataset: {ATRIBUTO: "", producto: "2", listings: "LISTINGS_B"}});
const subA = new Element({value: "aros"});
subA.dataset = {subfamilia: "1"};
const subB = new Element({value: "collares"});
subB.dataset = {subfamilia: "2"};
const zona = new Element();
zona.id = "ZONA";
const bloque = new Element({hidden: true});
bloque.id = "BLOQUE";
const esperada = new Element();
esperada.id = "ESPERADA";
const estado = new Element({estado: true});
const form = new Element({dataset: {plataforma: "amazon_mx"}, hidden: true});
form.id = "FORM";
form.elements = {
  confirmacion: new Element(),
  actor: new Element({value: "dueno"}),
  token: new Element({value: "tok"}),
};
const boton = new Element();
boton.disabled = false;  // en la pagina el boton nace habilitado
const buscaPropia = form.querySelector.bind(form);
form.children = [estado];
form.querySelector = sel => (sel === '[type="submit"]' ? boton : buscaPropia(sel));
root.children = [casillaA, casillaB, subA, subB, zona, bloque, esperada, form];
const ids = {"ZONA": zona, "BLOQUE": bloque, "ESPERADA": esperada, "FORM": form,
  "CASILLA_PRESUPUESTO": new Element({hidden: true}),
  "ACEPTO_PRESUPUESTO": new Element()};
const docEvents = {};
global.document = {
  getElementById: id => ids[id] || null,
  createElement: () => new Element(),
  querySelector: sel => root.querySelector(sel),
  querySelectorAll: sel => root.querySelectorAll(sel),
  addEventListener: (event, fn) => { docEvents[event] = fn; },
};
const llamadas = [];
const pendientes = [];
global.fetch = (url, opts) => {
  llamadas.push({url, body: opts && opts.body ? JSON.parse(opts.body) : null});
  return new Promise((resolve, reject) => { pendientes.push({url, resolve, reject}); });
};
const tick = () => new Promise(r => setTimeout(r, 10));
function entrega(i, cuerpo, status = 200) {
  pendientes[i].resolve({ok: status < 400, status, json: () => Promise.resolve(cuerpo)});
}
const codigo = fs.readFileSync(process.argv[1], "utf8");
global.module = {exports: {}};
vm.runInThisContext(codigo);
const api = global.module.exports;
const marca = (casilla, valor) => {
  casilla.checked = valor;
  if (docEvents.change) docEvents.change({target: casilla});
};
const ejecuta = frase => { form.elements.confirmacion.value = frase; EJECUTAR_LLAMADA(form); };
const envio = clave => {
  const cuerpo = llamadas[llamadas.length - 1].body;
  const elegido = cuerpo[clave];
  return clave === "solicitud" ? elegido.listing_ids : elegido;
};
(async () => {
"""

# A la vista -> B falla, respuestas invertidas, cambio durante la carga, doble
# clic, previa nueva con envio pendiente y envio incierto.
_GUION_W_P = r"""
  marca(casillaA, true);
  PEDIR();
  await tick();
  assert.equal(llamadas.length, 1, "una sola consulta de previa");
  entrega(0, PREVIA_A());
  await tick();
  assert.equal(form.hidden, false, "la previa A deja el formulario a la vista");
  assert.equal(esperada.textContent, ESPERADA_A, "la frase es la de A");
  assert.equal(boton.disabled, false, "con previa lista el boton esta vivo");

  // A a la vista, la persona cambia la seleccion y la previa de B falla.
  marca(casillaA, false);
  assert.equal(form.hidden, true, "cambiar la seleccion esconde el formulario de A");
  assert.equal(boton.disabled, true, "y apaga su boton");
  marca(casillaB, true);
  PEDIR();
  await tick();
  assert.equal(llamadas.length, 2);
  entrega(1, {detail: "Fallo controlado de la nueva consulta"}, 502);
  await tick();
  assert.match(zona.children[0].textContent, /Fallo controlado/, "el error de B se ve");
  assert.equal(form.hidden, true, "el fallo de B no reabre nada");
  ejecuta(ESPERADA_A);
  assert.equal(llamadas.length, 2, "cero POST de escritura tras el fallo de B");

  // Respuestas invertidas: B contesta primero y la de A llega tarde.
  marca(casillaA, true);
  PEDIR();
  await tick();
  marca(casillaA, false);
  marca(casillaB, true);
  PEDIR();
  await tick();
  const indiceA = llamadas.length - 2;
  const indiceB = llamadas.length - 1;
  entrega(indiceB, PREVIA_B());
  await tick();
  assert.equal(esperada.textContent, ESPERADA_B, "manda la previa de B");
  assert.equal(form.hidden, false);
  entrega(indiceA, PREVIA_A());
  await tick();
  assert.equal(esperada.textContent, ESPERADA_B, "la respuesta tardia de A no repinta");
  assert.equal(form.hidden, false, "ni rehabilita lo que se ve");
  ejecuta(ESPERADA_B);
  assert.ok(pendientes[pendientes.length - 1].url.endsWith(EJECUTAR),
    "se manda a la ruta del flujo");
  assert.equal(envio("huella"), HUELLA_B, "con la huella de B");
  assert.deepEqual(envio(IDS_CLAVE), IDS_B, "y los ids de B");
  entrega(pendientes.length - 1, RESULTADO());
  await tick();
  assert.match(estado.textContent, /(Lanzados|Retirados) /);

  // Cambio durante la carga: la respuesta que ya viajaba se tira aunque falle.
  marca(casillaA, true);
  PEDIR();
  await tick();
  const enVuelo = llamadas.length - 1;
  marca(casillaA, false);
  assert.equal(form.hidden, true);
  entrega(enVuelo, {detail: "Fallo controlado de la nueva consulta"}, 502);
  await tick();
  assert.doesNotMatch(zona.children[0].textContent, /Fallo controlado/,
    "la respuesta de la revision vieja no se pinta");
  assert.equal(form.hidden, true, "ni deja ejecutable la previa cancelada");

  // Doble clic: la fase enviando congela la solicitud.
  marca(casillaB, true);
  PEDIR();
  await tick();
  entrega(llamadas.length - 1, PREVIA_B());
  await tick();
  ejecuta(ESPERADA_B);
  const conUnEnvio = llamadas.length;
  assert.equal(boton.disabled, true, "el boton queda apagado mientras envia");
  ejecuta(ESPERADA_B);
  assert.equal(llamadas.length, conUnEnvio, "el segundo clic no manda otro POST");

  // Con el envio pendiente, ninguna entrada rearma Confirmar.
  PEDIR();
  assert.equal(llamadas.length, conUnEnvio, "no se pide previa mientras envia");
  assert.equal(boton.disabled, true, "Confirmar sigue apagado");
  entrega(pendientes.length - 1, RESULTADO());
  await tick();

  // Con el envio resuelto, pedir previa vuelve a funcionar.
  PEDIR();
  await tick();
  assert.equal(llamadas.length, conUnEnvio + 1, "despues del envio la previa corre");
  entrega(pendientes.length - 1, PREVIA_B());
  await tick();
  assert.equal(form.hidden, false);

  // Envio incierto: no se reenvia solo ni lo destraba otra entrada.
  ejecuta(ESPERADA_B);
  pendientes[pendientes.length - 1].reject(new Error("timeout"));
  await tick();
  assert.match(estado.textContent, /No se sabe si/);
  assert.equal(boton.disabled, true, "el reenvio queda deshabilitado");
  const antesDelReintento = llamadas.length;
  ejecuta(ESPERADA_B);
  assert.equal(llamadas.length, antesDelReintento, "no se reenvia solo");
  PEDIR();
  assert.equal(llamadas.length, antesDelReintento, "ni lo rearma una previa nueva");
  marca(casillaA, true);
  marca(casillaA, false);
  ejecuta(ESPERADA_B);
  assert.equal(llamadas.length, antesDelReintento, "ni cambiando la seleccion");
"""

# 409 con vista nueva: se limpia el literal viejo y la seleccion debe seguir
# vigente para volver a habilitar el plan que manda el servidor.
_GUION_W_P_409 = r"""
  marca(casillaA, true);
  PEDIR();
  await tick();
  entrega(0, PREVIA_A());
  await tick();
  ejecuta(ESPERADA_A);
  entrega(llamadas.length - 1, {detail: {mensaje: "x", vista_previa: PREVIA_A2()}}, 409);
  await tick();
  assert.match(estado.textContent, /cambió/);
  assert.equal(form.elements.confirmacion.value, "", "el literal viejo no sirve");
  assert.equal(form.hidden, false, "la vista nueva si se ve");
  assert.match(zona.children[0].children[0].textContent, /SB/, "y es la del servidor");
  const conVista = llamadas.length;
  ejecuta("");
  assert.equal(llamadas.length, conVista, "un clic sin escribir no basta");
  ejecuta(ESPERADA_A);
  assert.equal(envio("huella"), HUELLA_A2, "manda la huella del servidor");
  entrega(pendientes.length - 1, RESULTADO());
  await tick();

"""

# "Impulsar aparte" toma sus IDs de la tabla de sin pedido: marcar ahi deja
# vieja la previa del impulso, tambien mientras carga.
_GUION_W_P_APARTE = r"""
  marca(casillaA, true);
  PEDIR();
  await tick();
  entrega(0, PREVIA_A());
  await tick();
  assert.equal(form.hidden, false, "la previa de Impulsar aparte se ve");
  marca(casillaB, true);
  assert.equal(form.hidden, true, "marcar en la tabla sin pedido esconde el impulso");
  assert.equal(esperada.textContent, "");
  ejecuta(ESPERADA_A);
  assert.equal(llamadas.length, 1, "cero POST con la seleccion cambiada");

  // Tambien mientras la previa viaja.
  marca(casillaA, true);
  marca(casillaB, false);
  PEDIR();
  await tick();
  const enVuelo = llamadas.length - 1;
  marca(casillaB, true);
  entrega(enVuelo, PREVIA_A());
  await tick();
  assert.equal(form.hidden, true, "la respuesta en vuelo no reabre con otra seleccion");
  assert.doesNotMatch(zona.children[0].textContent, /Tope/, "ni pinta la previa vieja");
"""

# El 409 con vista nueva tampoco rearma una seleccion que ya no es la del plan.
_GUION_W_P_409_SELECCION = r"""
  marca(casillaA, true);
  PEDIR();
  await tick();
  entrega(0, PREVIA_A());
  await tick();
  ejecuta(ESPERADA_A);
  const enVuelo = llamadas.length - 1;
  marca(casillaB, true);
  marca(casillaA, false);
  assert.equal(form.hidden, false, "el envio congela la seleccion");
  entrega(enVuelo, {detail: {mensaje: "x", vista_previa: PREVIA_A2()}}, 409);
  await tick();
  assert.equal(form.hidden, true, "con otra seleccion el 409 no rearma el plan");
  ejecuta(ESPERADA_A);
  assert.equal(llamadas.length, enVuelo + 1, "ni manda con la vista vieja");

  // La subfamilia tambien es parte de la seleccion.
  marca(casillaA, true);
  marca(casillaB, false);
  PEDIR();
  await tick();
  entrega(llamadas.length - 1, PREVIA_A());
  await tick();
  ejecuta(ESPERADA_A);
  const conSubfamilia = llamadas.length - 1;
  subA.value = "collares";
  docEvents.change({target: subA});
  assert.equal(form.hidden, false, "el envio congela la subfamilia");
  entrega(conSubfamilia, {detail: {mensaje: "x", vista_previa: PREVIA_A2()}}, 409);
  await tick();
  assert.equal(form.hidden, true, "con otra subfamilia el 409 no rearma el plan");

  // Un marcado que se queda sin subfamilia tampoco es la seleccion pedida.
  subA.value = "aros";
  PEDIR();
  await tick();
  entrega(llamadas.length - 1, PREVIA_A());
  await tick();
  ejecuta(ESPERADA_A);
  const conVacia = llamadas.length - 1;
  subA.value = "";
  docEvents.change({target: subA});
  entrega(conVacia, {detail: {mensaje: "x", vista_previa: PREVIA_A2()}}, 409);
  await tick();
  assert.equal(form.hidden, true, "sin subfamilia el 409 tampoco rearma");
"""


def _sustituye_w_p(modo: str, atributo: str, pedir: str) -> dict:
    if modo == "retiro":
        return {
            "API": "{verRetiro, ejecutarRetiro}",
            "EJECUTAR_LLAMADA": "api.ejecutarRetiro",
            "PEDIR()": pedir,
            "PREVIA_A()": (
                '{huella: "a".repeat(64), ventas_al_dia: true, ventas_al_dia_hasta: null,'
                ' vendieron: [], anuncios: [{sku: "SA", ad_group_nombre: "GA",'
                ' ad_group_externo: "ga"}]}'
            ),
            "PREVIA_B()": (
                '{huella: "b".repeat(64), ventas_al_dia: true, ventas_al_dia_hasta: null,'
                ' vendieron: [], anuncios: [{sku: "SB", ad_group_nombre: "GB",'
                ' ad_group_externo: "gb"}, {sku: "SB2", ad_group_nombre: "GB",'
                ' ad_group_externo: "gb"}]}'
            ),
            "ESPERADA_A": '"SACAR 1 ANUNCIOS"',
            "ESPERADA_B": '"SACAR 2 ANUNCIOS"',
            "EJECUTAR": '"/retiro/ejecutar"',
            "HUELLA_B": '"b".repeat(64)',
            "IDS_CLAVE": '"listing_ids"',
            "IDS_B": "[22, 23]",
            "RESULTADO()": "{retirados: 2, total: 2, anuncios: []}",
            "LISTINGS_A": "21",
            "LISTINGS_B": "22,23",
            "ZONA": "productos-retiro-vista",
            "BLOQUE": "productos-retiro",
            "ESPERADA": "productos-retiro-esperada",
            "FORM": "productos-retiro-ejecutar",
            "ATRIBUTO": atributo,
        }
    return {
        "API": "{verLanzamiento, lanzar}",
        "EJECUTAR_LLAMADA": "api.lanzar",
        "PEDIR()": pedir,
        "PREVIA_A()": (
            '{huella: "a".repeat(64), confirmacion_esperada: "LANZAR IMPULSO DE 1 PRODUCTOS",'
            ' no_lanzables: [], planes: [{seller_sku: "SA", subfamilia: "aros",'
            ' tope: {monto: "100", moneda: "MXN", presupuesto_diario: "5"},'
            " keywords_exact: [], negatives: [], bid_por_rol: {}, retiros: []}]}"
        ),
        "PREVIA_A2()": (
            '{huella: "e".repeat(64), confirmacion_esperada: "LANZAR IMPULSO DE 1 PRODUCTOS",'
            ' no_lanzables: [], planes: [{seller_sku: "SB", subfamilia: "aros",'
            ' tope: {monto: "100", moneda: "MXN", presupuesto_diario: "5"},'
            " keywords_exact: [], negatives: [], bid_por_rol: {}, retiros: []}]}"
        ),
        "PREVIA_B()": (
            '{huella: "b".repeat(64), confirmacion_esperada: "LANZAR IMPULSO DE 2 PRODUCTOS",'
            ' no_lanzables: [], planes: [{seller_sku: "SB", subfamilia: "collares",'
            ' tope: {monto: "100", moneda: "MXN", presupuesto_diario: "5"},'
            " keywords_exact: [], negatives: [], bid_por_rol: {}, retiros: []},"
            ' {seller_sku: "SB2", subfamilia: "collares",'
            ' tope: {monto: "100", moneda: "MXN", presupuesto_diario: "5"},'
            " keywords_exact: [], negatives: [], bid_por_rol: {}, retiros: []}]}"
        ),
        "ESPERADA_A": '"LANZAR IMPULSO DE 1 PRODUCTOS"',
        "ESPERADA_B": '"LANZAR IMPULSO DE 2 PRODUCTOS"',
        "EJECUTAR": '"/impulso/lanzar"',
        "HUELLA_B": '"b".repeat(64)',
        "HUELLA_A2": '"e".repeat(64)',
        "IDS_CLAVE": '"solicitud"',
        "IDS_B": "[12, 13]",
        "RESULTADO()": "{lanzados: [{impulso_id: 1}]}",
        "LISTINGS_A": "11",
        "LISTINGS_B": "12,13",
        "ZONA": "productos-vista",
        "BLOQUE": "productos-lanzamiento",
        "ESPERADA": "productos-esperada",
        "FORM": "productos-lanzar",
        "ATRIBUTO": atributo,
    }


def _corre_guion_w_p(
    modo: str,
    cuerpo: str = _GUION_W_P,
    *,
    atributo: str | None = None,
    pedir: str | None = None,
    archivo: Path = RAIZ / "app" / "static" / "js" / "productos.js",
):
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el flujo JavaScript")
        pytest.skip("Node no disponible; el navegador se verifica en integracion")
    if modo == "retiro":
        atributo = atributo or "marcarSinPedido"
        pedir = pedir or "api.verRetiro()"
    else:
        atributo = atributo or "marcarCandidato"
        pedir = pedir or 'api.verLanzamiento("[data-marcar-candidato]")'
    sustituciones = _sustituye_w_p(modo, atributo, pedir)
    guion = _PRELUDE_W_P + cuerpo + "})().catch(err => { console.error(err); process.exit(1); });\n"
    for clave, valor in sustituciones.items():
        guion = guion.replace(clave, valor)
    proc = subprocess.run(
        [node, "-e", con_formato(guion), str(archivo)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_arreglos04_w_p_retiro_no_ejecuta_una_seleccion_cuya_previa_fallo():
    """W-P/TN04: con A a la vista, cambiar la seleccion y fallar la previa de B
    deja la solicitud de A sin boton; las respuestas tardias no rehabilitan otra
    revision, un doble clic manda una sola vez, ninguna entrada rearma Confirmar
    con un envio pendiente o incierto, y el envio incierto no se repite."""
    _corre_guion_w_p("retiro")


def test_arreglos04_w_p_impulso_no_ejecuta_una_seleccion_cuya_previa_fallo():
    """W-P/TN04, vecino de impulso: Ver lanzamiento e Impulsar aparte comparten
    el mismo estado y la misma compuerta."""
    _corre_guion_w_p("impulso")


def test_arreglos04_w_p_impulso_409_no_rearma_una_vista_que_ya_no_se_ve():
    """W-P: el 409 trae la vista vigente. El literal escrito era de la revision
    anterior y se limpia; si la seleccion cambio durante el envio, el plan
    devuelto no queda ejecutable."""
    _corre_guion_w_p("impulso", _GUION_W_P_409)


def test_arreglos04_w_p_409_no_rearma_una_seleccion_que_cambio_en_el_envio():
    """W-P: el cambio de seleccion durante el envio se congela; si llega un 409
    con vista nueva, esa vista no se rearma para la seleccion que ya no esta."""
    _corre_guion_w_p("impulso", _GUION_W_P_409_SELECCION)


def test_arreglos04_w_p_impulsar_aparte_invalida_su_previa_en_la_tabla_sin_pedido():
    """W-P: "Impulsar aparte" toma sus IDs de [data-marcar-sin-pedido]; marcar
    ahi, tambien mientras carga, retira la previa del impulso."""
    _corre_guion_w_p(
        "impulso",
        _GUION_W_P_APARTE,
        atributo="marcarSinPedido",
        pedir='api.verLanzamiento("[data-marcar-sin-pedido]")',
    )
