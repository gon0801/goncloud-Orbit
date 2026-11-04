"use strict";
// El chrome lee las mismas APIs que Propuestas y Salud; no replica consultas.
document.addEventListener("DOMContentLoaded", function () {
  const contador = document.getElementById("propuestas-contador");
  const repContador = document.getElementById("reputacion-contador");
  const ciclo = document.getElementById("ciclo-resumen");
  const watermark = document.getElementById("watermark-resumen");
  if (!contador || !ciclo || !watermark) return;

  function pintarBadge(nodo, texto, etiqueta) {
    if (!nodo) return;
    nodo.textContent = String(texto);
    nodo.setAttribute("aria-label", etiqueta);
    nodo.hidden = false;
  }
  function badges(rol) {
    return document.querySelectorAll('[data-rol="' + rol + '"]');
  }

  async function leer(ruta) {
    const respuesta = await fetch("/api/dashboard/" + ruta, {cache: "no-store"});
    if (!respuesta.ok) throw new Error("Lectura no disponible");
    return respuesta.json();
  }

  leer("cortes").then(datos => {
    const n = datos.items.length;
    const etiqueta = n + " propuestas pendientes";
    pintarBadge(contador, n, etiqueta);
    badges("propuestas").forEach(function (nodo) { pintarBadge(nodo, n, etiqueta); });
  }).catch(() => {
    const etiqueta = "Propuestas: no se pudo consultar";
    pintarBadge(contador, "—", etiqueta);
    badges("propuestas").forEach(function (nodo) { pintarBadge(nodo, "—", etiqueta); });
  });

  // Contador de alertas de reputacion: el badge es aviso de accion, solo
  // se muestra cuando hay abiertas; en cero o error queda oculto.
  if (repContador) {
    fetch("/api/reputacion/contador", {cache: "no-store"}).then(respuesta => {
      if (!respuesta.ok) throw new Error("Lectura no disponible");
      return respuesta.json();
    }).then(datos => {
      const total = Number(datos.total_alertas) || 0;
      const etiqueta = total + " alertas abiertas";
      if (total > 0) {
        pintarBadge(repContador, total, etiqueta);
        badges("reputacion").forEach(function (nodo) { pintarBadge(nodo, total, etiqueta); });
      }
    }).catch(() => {});
  }

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
