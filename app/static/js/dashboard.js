"use strict";
// JS del dashboard. Vive en /static por la CSP default-src 'self'.
// Los canvas declaran data-serie/data-claves/data-etiquetas; los datos
// viajan en <script type="application/json"> (no ejecutables).

var graficas = [];

function numero(s) {
  if (s === null || s === undefined || s === "") return null;
  return Number(s);
}

function colorCss(nombre, respaldo) {
  var valor = getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();
  return valor || respaldo;
}

function pintura() {
  return {
    texto: colorCss("--color-texto", "#1a1a1a"),
    mutado: colorCss("--color-mutado", "#5c574f"),
    borde: colorCss("--color-borde", "#c8c2b6"),
    series: [
      colorCss("--color-acento", "#9c3d12"),
      colorCss("--color-ok", "#2d6a3a"),
      colorCss("--color-alerta", "#a11f1f")
    ]
  };
}

function datosDe(id) {
  var el = document.getElementById(id);
  if (!el) return null;
  return JSON.parse(el.textContent);
}

function vestir(chart, tinta) {
  (chart.data.datasets || []).forEach(function (ds, i) {
    var color = tinta.series[i % tinta.series.length];
    ds.borderColor = color;
    ds.backgroundColor = color;
  });
  if (chart.options.plugins && chart.options.plugins.legend && chart.options.plugins.legend.labels) {
    chart.options.plugins.legend.labels.color = tinta.texto;
  }
  ["x", "y"].forEach(function (eje) {
    var escala = chart.options.scales && chart.options.scales[eje];
    if (!escala) return;
    if (escala.ticks) escala.ticks.color = tinta.mutado;
    if (!escala.grid) escala.grid = {};
    escala.grid.color = tinta.borde;
  });
  chart.update("none");
}

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
  var tinta = pintura();
  var chart = new Chart(canvas, {
    type: "line",
    data: { labels: fechas, datasets: etiquetas.map(function (etiqueta, i) {
      return {
        label: etiqueta,
        data: columnas[seriesClaves[i]],
        borderWidth: 2,
        pointRadius: 3,
        spanGaps: false,
        fill: false,
        segment: {
          borderDash: function (ctx) {
            return inmaduros[ctx.p1DataIndex] ? [4, 3] : undefined;
          }
        }
      };
    }) },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: tinta.texto } } },
      scales: {
        x: { ticks: { color: tinta.mutado, maxTicksLimit: 8 }, grid: { color: tinta.borde } },
        y: { ticks: { color: tinta.mutado }, grid: { color: tinta.borde } }
      }
    }
  });
  vestir(chart, tinta);
  graficas.push(chart);
}

function graficarBarras(canvasId, etiquetas, valores, color) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var tinta = pintura();
  var chart = new Chart(canvas, {
    type: "bar",
    data: { labels: etiquetas, datasets: [{ label: "conteo", data: valores, backgroundColor: color || tinta.series[2] }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: tinta.mutado }, grid: { color: tinta.borde } },
        y: { ticks: { color: tinta.mutado }, grid: { color: tinta.borde } }
      }
    }
  });
  vestir(chart, tinta);
  graficas.push(chart);
}

function observarTema() {
  var ultimo = document.documentElement.dataset.tema;
  new MutationObserver(function () {
    var ahora = document.documentElement.dataset.tema;
    if (ahora === ultimo) return;
    ultimo = ahora;
    var tinta = pintura();
    graficas.forEach(function (g) { vestir(g, tinta); });
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-tema"] });
}

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("canvas[data-serie]").forEach(function (canvas) {
    graficarSeries(
      canvas.id,
      canvas.dataset.serie,
      canvas.dataset.claves.split(","),
      canvas.dataset.etiquetas.split(",")
    );
  });
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
      if (etiquetas.length) graficarBarras("skips-" + plataforma, etiquetas, valores, pintura().series[2]);
    }
  );
  observarTema();
});
