import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import ui


def _base(pantalla: str) -> str:
    return ui.templates.env.get_template("base.html").render(pantalla=pantalla)


def test_subnavegacion_agrupa_todas_las_rutas():
    esperado = {
        "cortes": ("Oportunidades", "/gasto-sin-venta", "/inertes"),
        "gasto-sin-venta": ("Oportunidades", "/cortes", "/busquedas-sin-venta"),
        "busquedas-sin-venta": ("Oportunidades", "/cortes", "/keywords-danadas"),
        "keywords-danadas": ("Oportunidades", "/cortes", "/productos-sin-clics"),
        "productos-sin-clics": ("Oportunidades", "/cortes", "/inertes"),
        "inertes": ("Oportunidades", "/cortes", "/gasto-sin-venta"),
        "decisiones": ("Decisiones", "/ruido", "/decisiones"),
        "ruido": ("Decisiones", "/decisiones", "/ruido"),
        "contribucion": ("Rendimiento", "/donde-poner-el-dinero", "/contribucion"),
        "donde-poner-el-dinero": ("Rendimiento", "/contribucion", "/donde-poner-el-dinero"),
        "precios": ("Catálogo", "/reputacion", "/envios"),
        "reputacion": ("Catálogo", "/precios", "/envios"),
        "envios": ("Catálogo", "/reputacion", "/familias"),
        "familias": ("Catálogo", "/precios", "/envios"),
    }
    for pantalla, (grupo, primera, ultima) in esperado.items():
        html = _base(pantalla)
        assert f"Secciones de {grupo}" in html
        assert f'href="{primera}"' in html
        assert f'href="{ultima}"' in html
        assert 'class="subnavegacion"' in html


def test_crear_campana_es_accion_y_configuracion_es_destino():
    campanas = _base("campanas")
    settings = _base("settings")
    assert 'href="/campanas/nuevas"' in campanas
    assert 'aria-label="Secciones de Campañas"' not in campanas
    assert 'href="/settings"' in settings
    assert 'aria-current="page"' in settings


def test_encabezado_sin_rectangulos_de_contexto():
    """El header deja solo la accion principal y el boton de tema; los
    contadores del menu se siguen pintando desde shell.js."""
    for pantalla in ("resumen", "campanas", "envios"):
        html = _base(pantalla)
        assert 'class="leyenda-zona"' not in html
        assert 'id="watermark-resumen"' not in html
        assert 'class="alcance"' not in html
        assert "Horas del centro de México" not in html
        assert "Cifras hasta" not in html
        assert 'id="propuestas-contador"' in html
        assert 'id="reputacion-contador"' in html
        assert 'id="envios-contador"' in html
        assert 'id="ciclo-resumen"' in html
        assert 'id="btn-tema"' in html


def test_fabrica_marca_campanas_en_el_sidebar_no_solo_en_movil():
    html = _base("fabrica")
    sidebar = re.search(r'<nav id="nav-secciones"[^>]*>(.*?)</nav>', html, re.DOTALL)
    assert sidebar is not None
    campanas = re.search(r'<a href="/campanas"[^>]*>', sidebar.group(1))
    assert campanas is not None
    assert 'class="nav-activo"' in campanas.group(0)
    assert 'aria-current="true"' in campanas.group(0)
    movil = re.search(r'<nav class="tab-bar"[^>]*>(.*)</nav>\s*<script', html, re.DOTALL)
    assert movil is not None
    assert re.search(
        r'<a class="tab tab-activo" href="/campanas" aria-current="page">', movil.group(1)
    )


def test_menu_mas_funciona_sin_badges_y_con_apis_caidas():
    node = shutil.which("node")
    if not node:
        if "CI" in os.environ:
            pytest.fail("Node es obligatorio en CI para verificar el menu movil")
        pytest.skip("Node no disponible")
    shell = Path(ui.__file__).resolve().parent / "static" / "js" / "shell.js"
    guion = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Nodo {
  constructor(id) {
    this.id = id;
    this.hidden = true;
    this.atributos = {};
    this.escuchas = {};
    this.hijos = [];
    this.textContent = "";
  }
  addEventListener(tipo, fn) {
    (this.escuchas[tipo] ||= []).push(fn);
  }
  setAttribute(nombre, valor) {
    this.atributos[nombre] = String(valor);
  }
  getAttribute(nombre) {
    return this.atributos[nombre];
  }
  focus() {
    document.activeElement = this;
  }
  querySelector(selector) {
    return selector === "a" ? this.enlace : null;
  }
  contains(otro) {
    return otro === this || this.hijos.includes(otro);
  }
  replaceChildren() {}
  append() {}
  despachar(tipo, evento = {}) {
    evento.target ||= this;
    for (const fn of this.escuchas[tipo] || []) fn(evento);
    if (tipo === "click") document.despachar(tipo, evento);
  }
}

async function probar(conBadges) {
  const boton = new Nodo("nav-mas-toggle");
  const menu = new Nodo("nav-mas");
  const enlace = new Nodo("rendimiento");
  const etiqueta = new Nodo("etiqueta");
  menu.enlace = enlace;
  boton.hijos.push(etiqueta);
  const nodos = {"nav-mas-toggle": boton, "nav-mas": menu};
  if (conBadges) {
    for (const id of ["propuestas-contador", "ciclo-resumen"]) {
      nodos[id] = new Nodo(id);
    }
  }
  global.document = {
    activeElement: null,
    escuchas: {},
    addEventListener(tipo, fn) {
      (this.escuchas[tipo] ||= []).push(fn);
    },
    despachar(tipo, evento) {
      for (const fn of this.escuchas[tipo] || []) fn(evento);
    },
    getElementById(id) {
      return nodos[id] || null;
    },
    querySelectorAll() {
      return [];
    },
    createElement() {
      return new Nodo("creado");
    },
  };
  global.Orbit = {
    getJson() {
      return Promise.reject(new Error("API caida"));
    },
  };
  vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
  document.escuchas.DOMContentLoaded[0]();

  boton.despachar("click", {target: etiqueta});
  assert.equal(menu.hidden, false, "el menu abre aunque el click venga del contenido del boton");
  assert.equal(boton.getAttribute("aria-expanded"), "true");
  assert.equal(document.activeElement, enlace, "el foco entra al primer destino");

  menu.despachar("keydown", {key: "Escape", target: enlace});
  assert.equal(menu.hidden, true, "Escape cierra el menu");
  assert.equal(boton.getAttribute("aria-expanded"), "false");
  assert.equal(document.activeElement, boton, "Escape devuelve el foco al boton");

  boton.despachar("click");
  document.despachar("click", {target: new Nodo("fuera")});
  assert.equal(menu.hidden, true, "un click fuera cierra el menu");
  await new Promise(resolve => setImmediate(resolve));
}

(async () => {
  await probar(false);
  await probar(true);
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""
    resultado = subprocess.run(
        [node, "-e", guion, str(shell)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
