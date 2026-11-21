(function () {
"use strict";
// JS del dashboard (Resumen). Vive en /static por la CSP default-src 'self'.
// Los datos viajan en <script type="application/json"> (no ejecutables).

var graficas = [];

function numero(s) {
  if (s === null || s === undefined || s === "") return null;
  return Number(s);
}

// La cifra del tooltip con el formato comun (formato.js), igual que la tabla:
// dinero con 2 decimales, ACoS como porcentaje y conteos con miles. Los datos
// que dibujan la linea no cambian.
function formatoDeClave(clave) {
  if (clave === "cost" || clave === "ad_revenue") return Orbit.dinero;
  if (clave === "acos") return Orbit.pct;
  return function (valor) { return Orbit.numero(valor); };
}

function tooltipCon(formatos) {
  return {
    label: function (ctx) {
      return ctx.dataset.label + ": " + formatos[ctx.datasetIndex](ctx.raw);
    }
  };
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
    var respaldo = ds.colorRespaldo || tinta.series[i % tinta.series.length];
    var color = ds.colorToken ? colorCss(ds.colorToken, respaldo) : respaldo;
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

function reemplazar(canvas, config) {
  var previo = typeof Chart.getChart === "function" ? Chart.getChart(canvas) : null;
  if (previo) {
    graficas = graficas.filter(function (grafica) { return grafica !== previo; });
    previo.destroy();
  }
  var chart = new Chart(canvas, config);
  vestir(chart, pintura());
  graficas.push(chart);
}

function serieCanal(bloque, clave, campo) {
  return (bloque.series || []).map(function (fila) {
    var origen = campo === "ma7" ? (fila.ma7 || {}) : fila;
    return numero(origen[clave]);
  });
}

function graficarResumen() {
  var contrato = datosDe("datos-resumen");
  if (!contrato || !contrato.canales) return;
  Object.keys(contrato.canales).forEach(function (nombre) {
    var bloque = contrato.canales[nombre];
    var canvas = document.getElementById("tendencia-" + nombre);
    if (!canvas || !bloque) return;
    var fechas = [];
    var inmaduros = [];
    (bloque.series || []).forEach(function (fila) {
      fechas.push(fila.fecha);
      inmaduros.push(fila.inmaduro === true);
    });
    var tinta = pintura();
    var pares = [
      ["ad_revenue", "Ventas media 7", "ma7", "--resumen-ventas-ma7", "#1D4ED8"],
      ["ad_revenue", "Ventas del dia", "dia", "--resumen-ventas-dia", "#93C5FD"],
      ["cost", "Gasto media 7", "ma7", "--resumen-gasto-ma7", "#C2410C"],
      ["cost", "Gasto del dia", "dia", "--resumen-gasto-dia", "#FDBA74"]
    ];
    reemplazar(canvas, {
      type: "line",
      data: {
        labels: fechas,
        datasets: pares.map(function (par) {
          return {
            label: par[1] + " " + bloque.moneda,
            data: serieCanal(bloque, par[0], par[2]),
            borderWidth: par[2] === "ma7" ? 2.5 : 1.2,
            pointRadius: par[2] === "ma7" ? 1.5 : 0,
            spanGaps: false,
            fill: false,
            colorToken: par[3],
            colorRespaldo: par[4],
            segment: par[2] === "dia" ? {
              borderDash: function (ctx) {
                return inmaduros[ctx.p1DataIndex] ? [4, 3] : [3, 2];
              }
            } : undefined
          };
        })
      },
      plugins: [{
        id: "franjaInmaduraResumen",
        beforeDraw: function (grafica) {
          var inicio = inmaduros.indexOf(true);
          if (inicio < 0 || !grafica.chartArea) return;
          var x = grafica.scales.x.getPixelForValue(inicio);
          var area = grafica.chartArea;
          var ctx = grafica.ctx;
          ctx.save();
          ctx.fillStyle = colorCss("--o-ink3", "#64748B");
          ctx.globalAlpha = 0.09;
          ctx.fillRect(x, area.top, area.right - x, area.bottom - area.top);
          ctx.globalAlpha = 0.75;
          ctx.fillStyle = colorCss("--color-mutado", "#64748B");
          ctx.font = "600 10px -apple-system, BlinkMacSystemFont, sans-serif";
          ctx.textAlign = "right";
          ctx.fillText("cifras inmaduras", area.right - 6, area.top + 14);
          ctx.restore();
        }
      }],
      options: {
        locale: "es-MX",
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: false,
            position: "top",
            align: "end",
            labels: { color: tinta.texto, usePointStyle: true, pointStyle: "line", boxWidth: 18 }
          },
          tooltip: { enabled: true, callbacks: tooltipCon(pares.map(function (par) { return formatoDeClave(par[0]); })) }
        },
        scales: {
          x: { ticks: { color: tinta.mutado, maxTicksLimit: 8 }, grid: { color: tinta.borde } },
          y: { beginAtZero: true, ticks: { color: tinta.mutado }, grid: { color: tinta.borde } }
        }
      }
    });
  });
}

function activarPestanasResumen() {
  var botones = document.querySelectorAll("button[data-tab-canal]");
  if (!botones.length) return;
  botones.forEach(function (boton) {
    boton.addEventListener("click", function () {
      var elegido = boton.getAttribute("data-tab-canal");
      botones.forEach(function (b) {
        var activo = b.getAttribute("data-tab-canal") === elegido;
        b.classList.toggle("active", activo);
        b.setAttribute("aria-selected", activo ? "true" : "false");
      });
      document.querySelectorAll("[data-panel-canal]").forEach(function (panel) {
        var mostrar = panel.getAttribute("data-panel-canal") === elegido;
        panel.hidden = !mostrar;
        if (mostrar && typeof Chart !== "undefined" && typeof Chart.getChart === "function") {
          var canvas = panel.querySelector("canvas");
          var grafica = canvas ? Chart.getChart(canvas) : null;
          if (grafica) grafica.resize();
        }
      });
    });
  });
}

document.addEventListener("DOMContentLoaded", function () {
  graficarResumen();
  activarPestanasResumen();
  observarTema();
});
})();
