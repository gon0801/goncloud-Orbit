"""Flujo JS de productos sin clics (BIDS 02, P.4): ejecuta el JS real con DOM y fetch simulados."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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
        json: () => Promise.resolve({vista_previa: vista})});
    }
    return Promise.resolve({ok: true, status: 200,
      json: () => Promise.resolve({lanzados: [{impulso_id: 1}, {impulso_id: 2}]})});
  }
  if (url.endsWith("/retiro/reponer"))
    return Promise.resolve({ok: true, json: () => Promise.resolve({repuestos: 1})});
  if (url.endsWith("/retiro/plan"))
    return Promise.resolve({ok: true, json: () => Promise.resolve(
      {huella: "c".repeat(64), anuncios: [{sku: "SV", ad_group_nombre: "Viejos",
        ad_group_externo: "ag-v", ad_id_externo: "31"}]})});
  if (url.endsWith("/retiro/ejecutar"))
    return Promise.resolve({ok: true, json: () => Promise.resolve({retirados: 1})});
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
  assert.match(texto, /acotados/);
  assert.match(texto, /No lanzable 99/);
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
  assert.equal(calls[1].token, "tok");
  assert.match(estado.textContent, /cambió/);
  // Segundo intento con la vista nueva: exito.
  api.lanzar(form);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls[2].body.huella, "b".repeat(64));
  assert.match(estado.textContent, /Lanzados 2/);
  // Reponer: literal distinto no manda; exacto si.
  const estadoR = new Element(); estadoR.estado = true;
  const formR = new Element({dataset: {retiros: "5", esperada: "REGRESAR 1 ANUNCIOS"}});
  formR.elements = {confirmacion: new Element({value: "OTRO"}),
    actor: new Element({value: "dueno"}), token: new Element({value: "tok"})};
  formR.children = [estadoR];
  api.reponer(formR);
  assert.equal(calls.length, 3);
  formR.elements.confirmacion.value = "REGRESAR 1 ANUNCIOS";
  api.reponer(formR);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(calls[3].url, "/api/fabrica/retiro/reponer");
  assert.deepEqual(calls[3].body.retiro_ids, [5]);
  assert.match(estadoR.textContent, /Repuestos 1/);
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
  assert.equal(calls[5].token, "tok");
  assert.match(estadoR2.textContent, /Retirados 1/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    proc = subprocess.run(
        [node, "-e", guion, str(archivo)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
