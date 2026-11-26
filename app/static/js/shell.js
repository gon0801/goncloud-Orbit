(function () {
"use strict";
// El chrome lee las mismas APIs que Propuestas y Salud; no replica consultas.
document.addEventListener("DOMContentLoaded", function () {
  const contador = document.getElementById("propuestas-contador");
  const repContador = document.getElementById("reputacion-contador");
  const enviosContador = document.getElementById("envios-contador");
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

  async function leer(url) {
    const r = await Orbit.getJson(url);
    if (!r.ok || !r.jsonOk) throw new Error("Lectura no disponible");
    return r.data;
  }

  // Endpoint liviano (/chrome): evita disparar /cortes + /salud completos en cada pagina.
  leer("/api/dashboard/chrome").then(datos => {
    const prop = datos.propuestas || {};
    const n = Number(prop.live) || 0;
    const practica = Number(prop.shadow) || 0;
    const etiqueta = n + " pendientes (" + practica + " de práctica)";
    pintarBadge(contador, n, etiqueta);
    badges("propuestas").forEach(function (nodo) { pintarBadge(nodo, n, etiqueta); });

    ciclo.replaceChildren();
    const marcas = [];
    Object.entries(datos.plataformas || {}).forEach(([plataforma, salud]) => {
      const nombre = plataforma === "amazon_mx" ? "MX" : "US";
      marcas.push(nombre + ": " + (salud.watermark || "sin datos"));
      const linea = document.createElement("div");
      const ultimo = salud.ultimo_ciclo;
      if (!ultimo) linea.textContent = nombre + " · sin ciclo";
      else {
        const estado = document.createElement("span");
        estado.className = "chip " + (ultimo.status === "done" ? "ok" : ["failed", "degraded"].includes(ultimo.status) ? "alerta" : "");
        estado.textContent = nombre + " · " + Orbit.etiqueta(ultimo.status, "estado_ciclo") + " · " + Orbit.etiqueta(ultimo.mode, "modo");
        estado.title = ultimo.status + " · " + ultimo.mode;
        const detalle = document.createElement("small");
        detalle.className = "mutado";
        detalle.textContent = Orbit.fechaHora(ultimo.started_at) + " · " + Orbit.numero(ultimo.decisions_count) + " decisiones";
        linea.append(estado, detalle);
      }
      ciclo.append(linea);
    });
    watermark.textContent = "Cifras hasta · " + marcas.join(" · ");
  }).catch(() => {
    const etiqueta = "Propuestas: no se pudo consultar";
    pintarBadge(contador, "—", etiqueta);
    badges("propuestas").forEach(function (nodo) { pintarBadge(nodo, "—", etiqueta); });
    ciclo.textContent = "No se pudo consultar";
    watermark.textContent = "Cifras hasta: no disponible";
  });

  // Contador de alertas de reputacion: el badge es aviso de accion, solo
  // se muestra cuando hay abiertas; en cero o error queda oculto.
  if (repContador) {
    leer("/api/reputacion/contador").then(datos => {
      const total = Number(datos.total_alertas) || 0;
      const etiqueta = total + " alertas abiertas";
      if (total > 0) {
        pintarBadge(repContador, total, etiqueta);
        badges("reputacion").forEach(function (nodo) { pintarBadge(nodo, total, etiqueta); });
      }
    }).catch(() => {});
  }

  // Envios que requieren atencion: mismo trato que el contador de reputacion.
  if (enviosContador) {
    leer("/api/envios/contador").then(datos => {
      const total = Number(datos.atencion) || 0;
      const etiqueta = "Envíos que requieren atención: " + total;
      if (total > 0) {
        pintarBadge(enviosContador, total, etiqueta);
        badges("envios").forEach(function (nodo) { pintarBadge(nodo, total, etiqueta); });
      }
    }).catch(() => {});
  }
});
})();
