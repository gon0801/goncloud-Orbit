"use strict";
// JS del dashboard (ORBIT 16). Vive en /static por la CSP `default-src
// 'self'`: los <script> inline y los handlers on*= quedan BLOQUEADOS por esa
// politica (hallazgo mayor de la review del bloque 2). El cableado por pagina
// es DECLARATIVO: los canvas llevan data-serie/data-claves/data-etiquetas y
// los datos viajan en bloques INERTES <script type="application/json"> (esos
// no los toca la CSP: no son ejecutables).

// Decision 7 del header: el dinero viaja como STRING desde la API
// ("363.1400"); el cliente lo parsea con Number() para graficar. El backend
// jamas emite floats de dinero.
function numero(s) {
  if (s === null || s === undefined || s === "") return null;
  return Number(s);
}

function datosDe(id) {
  var el = document.getElementById(id);
  if (!el) return null;
  return JSON.parse(el.textContent);
}

// Grafica de lineas (spend/revenue/ACoS): un hueco (null) jamas se pinta
// como 0 (regla 3, spanGaps false). Los dias INMADUROS (D-8..D-1) se marcan
// con "*" en la etiqueta y tramo punteado (hallazgo media de codex: los
// flags de la API no se reflejaban en la grafica).
function graficarSeries(canvasId, datosId, seriesClaves, etiquetas) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var datos = datosDe(datosId);
  if (!datos) return;
  var fechas = [];
  var inmaduros = [];
  var columnas = {};
  seriesClaves.forEach(function (clave) { columnas[clave] = []; });
  (datos.series || []).forEach(function (fila) {
    fechas.push(fila.inmaduro ? fila.fecha + " *" : fila.fecha);
    inmaduros.push(Boolean(fila.inmaduro));
    seriesClaves.forEach(function (clave) {
      columnas[clave].push(numero(fila[clave]));
    });
  });
  new Chart(canvas, {
    type: "line",
    data: { labels: fechas, datasets: etiquetas.map(function (etiqueta, i) {
      return {
        label: etiqueta,
        data: columnas[seriesClaves[i]],
        borderWidth: 1.5,
        spanGaps: false,
        segment: {
          borderDash: function (ctx) {
            return inmaduros[ctx.p1DataIndex] ? [4, 3] : undefined;
          }
        }
      };
    }) },
    options: { responsive: true, plugins: { legend: { labels: { color: "#e2e8f0" } } },
               scales: { x: { ticks: { color: "#94a3b8", maxTicksLimit: 8 } },
                         y: { ticks: { color: "#94a3b8" } } } }
  });
}

// Grafica de barras (skips, decisiones por kind).
function graficarBarras(canvasId, etiquetas, valores, color) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  new Chart(canvas, {
    type: "bar",
    data: { labels: etiquetas, datasets: [{ label: "conteo", data: valores, backgroundColor: color }] },
    options: { responsive: true, plugins: { legend: { display: false } },
               scales: { x: { ticks: { color: "#94a3b8" } }, y: { ticks: { color: "#94a3b8" } } } }
  });
}

// Cursor del feed: la pagina siguiente pide id < next_cursor (decision 8).
function cargarMas() {
  var boton = document.getElementById("btn-mas");
  if (!boton) return;
  var cursor = boton.dataset.cursor;
  window.location.href = "/decisiones?cursor=" + encodeURIComponent(cursor);
}

document.addEventListener("DOMContentLoaded", function () {
  // Resumen: cada canvas declara su serie en data-attributes.
  document.querySelectorAll("canvas[data-serie]").forEach(function (canvas) {
    graficarSeries(
      canvas.id,
      canvas.dataset.serie,
      canvas.dataset.claves.split(","),
      canvas.dataset.etiquetas.split(",")
    );
  });
  // Salud: skips por motivo, etiquetados con su TRADUCCION motivo_es
  // (hallazgo baja de codex: el id crudo es vocabulario interno).
  document.querySelectorAll('script[type="application/json"][id^="datos-skips-"]').forEach(
    function (el) {
      var plataforma = el.id.slice("datos-skips-".length);
      var datos = JSON.parse(el.textContent);
      var etiquetas = [];
      var valores = [];
      ["entidad", "termino"].forEach(function (lado) {
        Object.keys(datos[lado] || {}).forEach(function (motivo) {
          etiquetas.push(datos[lado][motivo].motivo_es || motivo);
          valores.push(datos[lado][motivo].count);
        });
      });
      if (etiquetas.length) graficarBarras("skips-" + plataforma, etiquetas, valores, "#f87171");
    }
  );
  // Decisiones: el boton de paginacion se cablea aqui (onclick= inline lo
  // bloquea la CSP).
  var boton = document.getElementById("btn-mas");
  if (boton) boton.addEventListener("click", cargarMas);
});
