"use strict";
// El chrome lee las mismas APIs que Propuestas y Salud; no replica consultas.
document.addEventListener("DOMContentLoaded", function () {
  const menu = document.getElementById("nav-toggle");
  if (menu) {
    menu.hidden = false;
    menu.closest(".sidebar").classList.add("nav-lista");
    menu.addEventListener("click", () => {
      const abierto = menu.getAttribute("aria-expanded") !== "true";
      menu.setAttribute("aria-expanded", String(abierto));
      menu.textContent = abierto ? "Cerrar menu" : "Menu";
    });
  }
  const contador = document.getElementById("propuestas-contador");
  const ciclo = document.getElementById("ciclo-resumen");
  const watermark = document.getElementById("watermark-resumen");
  if (!contador || !ciclo || !watermark) return;

  async function leer(ruta) {
    const respuesta = await fetch("/api/dashboard/" + ruta, {cache: "no-store"});
    if (!respuesta.ok) throw new Error("Lectura no disponible");
    return respuesta.json();
  }

  leer("cortes").then(datos => {
    contador.textContent = String(datos.items.length);
    contador.setAttribute("aria-label", datos.items.length + " propuestas pendientes");
    contador.hidden = false;
  }).catch(() => {
    contador.textContent = "—";
    contador.setAttribute("aria-label", "Propuestas: no se pudo consultar");
    contador.hidden = false;
  });

  leer("salud").then(datos => {
    ciclo.replaceChildren();
    const marcas = [];
    Object.entries(datos.plataformas).forEach(([plataforma, salud]) => {
      const nombre = plataforma === "amazon_mx" ? "MX" : "US";
      marcas.push(nombre + ": " + (salud.watermark || "sin datos"));
      const linea = document.createElement("div");
      const ultimo = salud.ultimo_ciclo;
      if (!ultimo) linea.textContent = nombre + " · sin ciclo";
      else {
        const estado = document.createElement("span");
        estado.className = "chip " + (ultimo.status === "done" ? "ok" : ["failed", "degraded"].includes(ultimo.status) ? "alerta" : "");
        estado.textContent = nombre + " · " + ultimo.status + " · " + ultimo.mode;
        const detalle = document.createElement("small");
        detalle.className = "mutado";
        detalle.textContent = String(ultimo.started_at).replace("T", " ").slice(0, 16) + " · " + ultimo.decisions_count + " decisiones";
        linea.append(estado, detalle);
      }
      ciclo.append(linea);
    });
    watermark.textContent = "Watermark · " + marcas.join(" · ");
  }).catch(() => {
    ciclo.textContent = "No se pudo consultar";
    watermark.textContent = "Watermark: no disponible";
  });
});
