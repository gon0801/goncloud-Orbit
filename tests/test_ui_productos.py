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
vm.runInThisContext(codigo + "\n" + "this.__api = {verLanzamiento, lanzar, reponer,"
  + " marcados, verRetiro, ejecutarRetiro};");
const api = vm.runInThisContext("this.__api");
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
  // Segundo intento con la vista nueva: exito.
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
    # Los trece sitios de error usan mensajeError (seis de impulso y retiro,
    # cuatro de los limites de la pausa: el rechazo de simular y su estado,
    # 409 y 422 al guardar; tres del encendido: la simulacion que falla, 409
    # y 422 al confirmar):
    # ninguno conserva el patron que pintaba [object Object].
    assert "(r.data.detail || r.data)" not in archivo.read_text(encoding="utf-8")
    assert archivo.read_text(encoding="utf-8").count("mensajeError(r.data)") == 13
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
vm.runInThisContext(codigo + "\n" + "this.__api = {verLanzamiento, lanzar, verRetiro};");
const api = vm.runInThisContext("this.__api");
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
vm.runInThisContext(codigo + "\n" + "this.__api = {verLanzamiento};");
const api = vm.runInThisContext("this.__api");
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
