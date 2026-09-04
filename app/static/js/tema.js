"use strict";
// Vive en /static: la CSP bloquea script inline. En HEAD aplica data-tema
// antes del primer paint (sin esperar DOMContentLoaded).

var TEMA_DIA = "dia";
var TEMA_NOCHE = "noche";
var CLAVE_TEMA = "orbit-tema";

function temaValido(valor) {
  return valor === TEMA_NOCHE ? TEMA_NOCHE : TEMA_DIA;
}

function leerTema() {
  try {
    return temaValido(window.localStorage.getItem(CLAVE_TEMA));
  } catch (err) {
    return TEMA_DIA;
  }
}

function persistirTema(tema) {
  try {
    window.localStorage.setItem(CLAVE_TEMA, tema);
  } catch (err) {
    // private mode / quota: el tema de esta sesion igual se aplica
  }
}

function aplicarTema(tema) {
  document.documentElement.dataset.tema = temaValido(tema);
}

function syncBoton(tema) {
  var boton = document.getElementById("btn-tema");
  if (!boton) return;
  boton.textContent = "Tema: " + tema;
  boton.setAttribute("aria-pressed", tema === TEMA_NOCHE ? "true" : "false");
}

aplicarTema(leerTema());

document.addEventListener("DOMContentLoaded", function () {
  var tema = leerTema();
  aplicarTema(tema);
  syncBoton(tema);
  var boton = document.getElementById("btn-tema");
  if (!boton) return;
  boton.addEventListener("click", function () {
    var actual = temaValido(document.documentElement.dataset.tema);
    var siguiente = actual === TEMA_NOCHE ? TEMA_DIA : TEMA_NOCHE;
    aplicarTema(siguiente);
    persistirTema(siguiente);
    syncBoton(siguiente);
  });
});
