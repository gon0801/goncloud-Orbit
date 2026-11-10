"use strict";
// Espejo en el navegador de app/formato.py y app/etiquetas.py (CLARIDAD 01,
// I.1). base.html emite el registro una vez en <script type="application/json"
// id="orbit-etiquetas"> y carga este archivo antes que los demas: los scripts
// traducen y dan formato con los mismos textos y reglas que las plantillas.
// Nunca se usa para un value= ni para lo que viaja en un POST.

var Orbit = (function () {
  var RAYA = "—";
  var MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  var NUMERO = /^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i;
  var INSTANTE = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.(\d+))?(Z|[+-]\d{2}(?::?\d{2})?)?$/;
  // Partes numericas de la hora del centro de Mexico; el mes sale de MESES,
  // igual que en app/formato.py.
  var HORA_CENTRO = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Mexico_City", year: "numeric", month: "numeric", day: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  });
  var formatos = {};
  var registro = null;

  function leerRegistro() {
    if (registro) return registro;
    var nodo = document.getElementById("orbit-etiquetas");
    try {
      registro = JSON.parse(nodo.textContent);
    } catch (_) {
      registro = { etiquetas: {}, sufijo_libre: [] };
    }
    return registro;
  }

  function humaniza(codigo) {
    var texto = codigo.replace(/_/g, " ").trim();
    return texto.charAt(0).toUpperCase() + texto.slice(1);
  }

  function sufijoLibre(dominio, base) {
    return leerRegistro().sufijo_libre.some(function (par) {
      return par[0] === dominio && par[1] === base;
    });
  }

  // Codigo a texto de pantalla. `a:b` sin entrada propia se traduce por
  // partes; un codigo desconocido sale sin guiones bajos y avisa en consola.
  function etiqueta(codigo, dominio) {
    if (codigo === null || codigo === undefined || codigo === "") return RAYA;
    var texto = String(codigo);
    var tabla = leerRegistro().etiquetas[dominio] || {};
    if (Object.prototype.hasOwnProperty.call(tabla, texto)) return tabla[texto];
    var corte = texto.indexOf(":");
    if (corte >= 0) {
      var base = texto.slice(0, corte), sub = texto.slice(corte + 1);
      if (sufijoLibre(dominio, base)) return tabla[base] + " (" + humaniza(sub).toLowerCase() + ")";
      return etiqueta(base.trim(), dominio) + ": " + etiqueta(sub.trim(), dominio);
    }
    if (typeof console !== "undefined") {
      console.warn("etiqueta sin texto: dominio=" + dominio + " codigo=" + texto);
    }
    return humaniza(texto);
  }

  function cifra(valor, decimales, sufijo) {
    if (valor === null || valor === undefined) return RAYA;
    var texto = typeof valor === "boolean" ? "" : String(valor).trim();
    if (!NUMERO.test(texto)) return String(valor);
    if (!formatos[decimales]) {
      // Sin decimales, Python redondea a entero y el cero no lleva signo.
      formatos[decimales] = new Intl.NumberFormat("es-MX", {
        minimumFractionDigits: decimales, maximumFractionDigits: decimales,
        roundingMode: "halfEven", signDisplay: decimales === 0 ? "negative" : "auto"
      });
    }
    // Intl formatea el texto decimal exacto: el dinero no pasa por float.
    return formatos[decimales].format(texto) + sufijo;
  }

  // «1234.5» -> «1,234.50».
  function dinero(valor) { return cifra(valor, 2, ""); }

  // Valor que ya viene en porcentaje: «19.3661» -> «19.37 %».
  function pct(valor) { return cifra(valor, 2, " %"); }

  // «424436» -> «424,436»; con decimales, «1234.5» -> «1,234.50».
  function numero(valor, decimales) { return cifra(valor, decimales || 0, ""); }

  // Un instante sin zona viene de la base en UTC. Una fecha sola no es un
  // instante: sale igual.
  function momento(valor) {
    if (valor instanceof Date) return isNaN(valor.getTime()) ? null : valor;
    if (typeof valor !== "string") return null;
    var partes = INSTANTE.exec(valor.trim());
    if (!partes) return null;
    var fraccion = partes[3] ? "." + (partes[3] + "00").slice(0, 3) : "";
    var zona = partes[4] || "Z";
    if (zona !== "Z") zona = zona.slice(0, 3) + ":" + (zona.slice(3).replace(":", "") || "00");
    var fecha = new Date(partes[1] + "T" + partes[2] + fraccion + zona);
    return isNaN(fecha.getTime()) ? null : fecha;
  }

  // «2026-11-08T17:58:03+00:00» -> «8 nov, 11:58» (centro de Mexico).
  function fechaHora(valor) {
    if (valor === null || valor === undefined) return RAYA;
    var fecha = momento(valor);
    if (!fecha) return String(valor);
    var p = {};
    HORA_CENTRO.formatToParts(fecha).forEach(function (parte) { p[parte.type] = parte.value; });
    return Number(p.day) + " " + MESES[Number(p.month) - 1] + ", " + p.hour + ":" + p.minute;
  }

  return { etiqueta: etiqueta, dinero: dinero, pct: pct, numero: numero, fechaHora: fechaHora };
})();
