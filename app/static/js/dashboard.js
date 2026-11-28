(function () {
"use strict";
// JS del dashboard (Resumen). Vive en /static por la CSP default-src 'self'.
// Los datos viajan en <script type="application/json"> (no ejecutables).

var graficas = [];

var ETIQUETAS = {
  ad_revenue: "Ventas atribuidas",
  cost: "Gasto",
  acos: "ACoS",
  roas: "ROAS",
  clicks: "Clics",
  orders: "Órdenes atribuidas"
};

function numero(s) {
  if (s === null || s === undefined || s === "") return null;
  return Number(s);
}

// La cifra del tooltip con el formato comun (formato.js), igual que la tabla:
// dinero con 2 decimales, ACoS como porcentaje y conteos con miles.
function formatoDeClave(clave) {
  if (clave === "cost" || clave === "ad_revenue") return Orbit.dinero;
  if (clave === "acos") return Orbit.pct;
  if (clave === "roas") {
    return function (valor) { return Orbit.numero(valor, 2) + "×"; };
  }
  return function (valor) { return Orbit.numero(valor); };
}

function formatoDeEje(clave) {
  if (clave === "acos") {
    return function (valor) { return Orbit.numero(valor, 0) + " %"; };
  }
  if (clave === "roas") {
    return function (valor) { return Orbit.numero(valor, 1) + "×"; };
  }
  if (clave === "cost" || clave === "ad_revenue") {
    return function (valor) { return Orbit.numero(valor); };
  }
  return undefined;
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
    texto: colorCss("--color-texto", "#1a1b25"),
    mutado: colorCss("--color-mutado", "#5b5d70"),
    borde: colorCss("--color-borde", "#e6e5f0"),
    series: [
      colorCss("--color-acento", "#635bff"),
      colorCss("--color-ok", "#15a368"),
      colorCss("--color-alerta", "#e5484d")
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
  return chart;
}

// ACoS y ROAS no viajan como serie diaria en el contrato: se derivan de las
// mismas cifras de cada fila (cost y ad_revenue). Un divisor en cero o un
// hueco siguen siendo null, nunca cero.
function valorDeFila(fila, clave) {
  if (!fila) return null;
  if (clave === "acos") {
    var cost = numero(fila.cost);
    var ventas = numero(fila.ad_revenue);
    if (cost === null || ventas === null || ventas === 0) return null;
    return (cost / ventas) * 100;
  }
  if (clave === "roas") {
    var costo = numero(fila.cost);
    var ingreso = numero(fila.ad_revenue);
    if (costo === null || ingreso === null || costo === 0) return null;
    return ingreso / costo;
  }
  return numero(fila[clave]);
}

function serieCifra(bloque, clave, media7) {
  return (bloque.series || []).map(function (fila) {
    return valorDeFila(media7 ? (fila.ma7 || {}) : fila, clave);
  });
}

function actualizarLeyenda(clave) {
  var etiqueta = ETIQUETAS[clave] || ETIQUETAS.ad_revenue;
  var ma7 = document.querySelector("[data-leyenda-ma7]");
  var dia = document.querySelector("[data-leyenda-dia]");
  if (ma7) ma7.textContent = etiqueta + " media 7 días";
  if (dia) dia.textContent = etiqueta + " día";
}

function dibujarCanal(nombre, clave) {
  var contrato = datosDe("datos-resumen");
  if (!contrato || !contrato.canales) return null;
  var bloque = contrato.canales[nombre];
  var canvas = document.getElementById("tendencia-" + nombre);
  if (!canvas || !bloque) return null;

  var fechas = [];
  var inmaduros = [];
  (bloque.series || []).forEach(function (fila) {
    fechas.push(fila.fecha);
    inmaduros.push(fila.inmaduro === true);
  });
  var tinta = pintura();
  var formato = formatoDeClave(clave);
  var etiqueta = ETIQUETAS[clave] || ETIQUETAS.ad_revenue;
  var colorBarra = colorCss("--resumen-ventas-dia", "#b9b5ff");
  var colorLinea = colorCss("--resumen-ventas-ma7", "#3d35d6");

  return reemplazar(canvas, {
    type: "bar",
    data: {
      labels: fechas,
      datasets: [
        {
          type: "bar",
          label: etiqueta + " del día " + bloque.moneda,
          data: serieCifra(bloque, clave, false),
          backgroundColor: colorBarra,
          borderColor: colorBarra,
          borderWidth: 0,
          borderRadius: 2,
          barPercentage: 0.62,
          categoryPercentage: 0.9,
          spanGaps: false,
          order: 2,
          colorToken: "--resumen-ventas-dia",
          colorRespaldo: "#b9b5ff"
        },
        {
          type: "line",
          label: "Media 7 días " + bloque.moneda,
          data: serieCifra(bloque, clave, true),
          borderColor: colorLinea,
          backgroundColor: colorLinea,
          borderWidth: 2.4,
          pointRadius: 0,
          spanGaps: false,
          fill: false,
          tension: 0.25,
          order: 1,
          colorToken: "--resumen-ventas-ma7",
          colorRespaldo: "#3d35d6"
        }
      ]
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
        ctx.fillStyle = colorCss("--o-ink3", "#6e7085");
        ctx.globalAlpha = 0.09;
        ctx.fillRect(x, area.top, area.right - x, area.bottom - area.top);
        ctx.globalAlpha = 0.75;
        ctx.fillStyle = colorCss("--color-mutado", "#5b5d70");
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
        legend: { display: false },
        tooltip: { enabled: true, callbacks: tooltipCon([formato, formato]) }
      },
      scales: {
        x: {
          ticks: {
            color: tinta.mutado,
            maxTicksLimit: 8,
            maxRotation: 0,
            minRotation: 0,
            autoSkip: true,
            callback: function (valor, indice) {
              var fecha = fechas[indice];
              return fecha ? Orbit.fechaCorta(fecha) : valor;
            }
          },
          grid: { color: tinta.borde }
        },
        y: {
          beginAtZero: true,
          ticks: { color: tinta.mutado, callback: formatoDeEje(clave) },
          grid: { color: tinta.borde }
        }
      }
    }
  });
}

function claveElegida(panel) {
  var boton = panel ? panel.querySelector("button[data-cifra].sel") : null;
  return boton ? boton.getAttribute("data-cifra") : "ad_revenue";
}

function graficarResumen() {
  var contrato = datosDe("datos-resumen");
  if (!contrato || !contrato.canales) return;
  Object.keys(contrato.canales).forEach(function (nombre) {
    dibujarCanal(nombre, "ad_revenue");
  });
  actualizarLeyenda("ad_revenue");
}

function activarCifras() {
  var botones = document.querySelectorAll("button[data-cifra]");
  botones.forEach(function (boton) {
    boton.addEventListener("click", function () {
      var panel = boton.closest("[data-panel-canal]");
      if (!panel) return;
      var nombre = panel.getAttribute("data-panel-canal");
      panel.querySelectorAll("button[data-cifra]").forEach(function (b) {
        var activo = b === boton;
        b.classList.toggle("sel", activo);
        b.setAttribute("aria-pressed", activo ? "true" : "false");
      });
      var clave = boton.getAttribute("data-cifra");
      actualizarLeyenda(clave);
      dibujarCanal(nombre, clave);
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
        if (!mostrar) return;
        var clave = claveElegida(panel);
        actualizarLeyenda(clave);
        var grafica = dibujarCanal(elegido, clave);
        if (grafica) grafica.resize();
      });
    });
  });
}

document.addEventListener("DOMContentLoaded", function () {
  graficarResumen();
  activarCifras();
  activarPestanasResumen();
  observarTema();
});
})();
