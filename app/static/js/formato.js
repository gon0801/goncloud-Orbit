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

  // Desplaza el punto decimal de un texto numerico sin pasar por float.
  function desplazaDecimal(texto, lugares) {
    var m = /^([-+]?)(\d*)(?:\.(\d*))?(?:[eE]([-+]?\d+))?$/.exec(texto);
    if (!m || (!m[2] && !m[3])) return null;
    var signo = m[1] === "-" ? "-" : "";
    var enteros = m[2] || "";
    var frac = m[3] || "";
    var exp = (m[4] ? parseInt(m[4], 10) : 0) + lugares;
    var bruto = enteros + frac;
    var digitos = bruto.replace(/^0+/, "");
    if (!digitos) return "0";
    var pos = enteros.length - (bruto.length - digitos.length) + exp;
    if (pos <= 0) return signo + "0." + "0".repeat(-pos) + digitos;
    if (pos >= digitos.length) return signo + digitos + "0".repeat(pos - digitos.length);
    return signo + digitos.slice(0, pos) + "." + digitos.slice(pos);
  }

  // Fraccion (0.30 = 30 %) a porcentaje exacto sin float.
  function fraccionPct(valor) {
    if (valor === null || valor === undefined) return RAYA;
    var texto = typeof valor === "boolean" ? "" : String(valor).trim();
    if (!NUMERO.test(texto)) return String(valor);
    var escalado = desplazaDecimal(texto, 2);
    return escalado === null ? String(valor) : pct(escalado);
  }

  // Constructor seguro de nodos DOM: los textos viajan en TextNode, jamas innerHTML.
  function el(tag, attrs) {
    var nodo = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class" || k === "className") nodo.className = String(v);
        else if (v === true) nodo.setAttribute(k, "");
        else nodo.setAttribute(k, String(v));
      });
    }
    function anade(hijo) {
      if (hijo === null || hijo === undefined || hijo === false) return;
      if (Array.isArray(hijo)) hijo.forEach(anade);
      else nodo.appendChild(typeof hijo === "object" ? hijo : document.createTextNode(String(hijo)));
    }
    for (var i = 2; i < arguments.length; i++) anade(arguments[i]);
    return nodo;
  }

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

  // «2026-09-08» -> «8 sep» (usa la unica lista MESES).
  function fechaCorta(iso) {
    if (!iso) return "";
    var partes = String(iso).split("-");
    if (partes.length < 3) return String(iso);
    return Number(partes[2]) + " " + (MESES[Number(partes[1]) - 1] || "");
  }

  // Extrae un mensaje legible de un error FastAPI (string, objeto o lista 422)
  // para evitar "[object Object]" en cualquier pantalla.
  function errorTexto(data, status, campos) {
    var detalle = data && data.detail;
    if (typeof detalle === "string" && detalle) return detalle;
    if (Array.isArray(detalle) && detalle.length) {
      return detalle
        .map(function (d) {
          var campo = d && d.loc ? d.loc[d.loc.length - 1] : null;
          var nombre = campos && campo ? campos[campo] || campo : null;
          return (nombre ? nombre + ": " : "") + ((d && d.msg) || "valor no válido");
        })
        .join("; ");
    }
    if (detalle && typeof detalle === "object") {
      if (detalle.message) return String(detalle.message);
      if (detalle.mensaje) return String(detalle.mensaje);
    }
    if (status) return "HTTP " + status;
    return "la respuesta no trae detalle";
  }

  function leeJson(resp) {
    return resp.json().then(
      function (data) {
        return { ok: resp.ok, status: resp.status, data: data, jsonOk: true };
      },
      function () {
        return { ok: false, status: resp.status, data: {}, jsonOk: false };
      }
    );
  }

  // GET JSON sin cache con parseo tolerante (un 502/504 HTML no rompe la cadena).
  function getJson(url) {
    return fetch(url, { method: "GET", cache: "no-store" }).then(leeJson);
  }

  // POST JSON autenticado SOLO por header x-orbit-token (nunca query string).
  // Sin token (undefined/null) omite el header (rutas de solo lectura/plan).
  function postJson(url, cuerpo, token) {
    var headers = { "Content-Type": "application/json" };
    if (token !== undefined && token !== null) headers["x-orbit-token"] = token;
    return fetch(url, {
      method: "POST",
      cache: "no-store",
      headers: headers,
      body: JSON.stringify(cuerpo),
    }).then(leeJson);
  }

  return {
    etiqueta: etiqueta,
    dinero: dinero,
    pct: pct,
    fraccionPct: fraccionPct,
    numero: numero,
    fechaHora: fechaHora,
    fechaCorta: fechaCorta,
    errorTexto: errorTexto,
    getJson: getJson,
    postJson: postJson,
    el: el,
  };
})();
