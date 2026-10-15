"use strict";
// JS de /precios (REPRICING 02 G.3). Vive en /static por la CSP
// default-src 'self'. Token SOLO en header x-orbit-token, tecleado en
// cada barra y sin guardarlo (mismo patron que settings.js/familias.js).
// Sin acentos en este archivo (convencion del repo para .js).

// El plan que el dueno vio, por plataforma: {huella, spec}. Aplicar
// reenvia el MISMO spec planeado (no relee los campos): si algo cambio
// entre mirar y aceptar, el servidor responde 409 con el plan nuevo.
var planesVistos = {};

function tokenPrecios(form) {
  var campo = form.elements.token;
  return campo ? campo.value : "";
}

function postPrecios(form, url, cuerpo) {
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": tokenPrecios(form),
    },
    body: JSON.stringify(cuerpo),
  }).then(function (resp) {
    return resp.json().catch(function () { return {}; }).then(function (data) {
      return { ok: resp.ok, status: resp.status, data: data };
    });
  });
}

function escapar(texto) {
  return String(texto === null || texto === undefined ? "" : texto)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Espejos de porcentaje_ui y dinero_ui (app/ui.py) para la vista previa.
function pct(fraccion) {
  if (fraccion === null || fraccion === undefined) return "—";
  var n = Number(fraccion);
  if (isNaN(n)) return String(fraccion);
  return (n * 100).toFixed(2) + " %";
}

function dinero(valor) {
  if (valor === null || valor === undefined) return "—";
  var n = Number(valor);
  if (isNaN(n)) return String(valor);
  return n.toFixed(2);
}

function importeCorto(importe) {
  if (!importe) return "—";
  return dinero(importe.valor) + " " + (importe.moneda || "");
}

function estadoDe(form) {
  return form.querySelector("[data-estado]");
}

function vistaDe(form) {
  return form.parentNode.querySelector("[data-vista]");
}

function botonAplicarDe(form) {
  return form.querySelector("[data-aplicar]");
}

function armarPedido(form, estado) {
  var tipo = form.elements.pedido.value;
  var mode = form.elements.mode.value;
  var pedido = { tipo: tipo, mode: mode };
  if (tipo === "valor") {
    var goal = form.elements.goal_pct.value.trim();
    if (!goal) {
      estado.textContent = "El pedido valor necesita el goal en por ciento (ej. 30.00).";
      return null;
    }
    pedido.goal_pct = goal;
  }
  return pedido;
}

function seleccionDe(form, estado) {
  if (form.elements.sin_goal.checked) return { sin_goal: true };
  var seccion = form.closest("section");
  var ids = Array.prototype.map.call(
    seccion.querySelectorAll(".precios-check:checked"),
    function (c) { return Number(c.value); }
  );
  if (!ids.length) {
    estado.textContent = "Selecciona al menos una unidad o marca todos los sin goal.";
    return null;
  }
  return { listing_ids: ids };
}

function pintarVista(form, plan) {
  var vista = vistaDe(form);
  var bloqueadas = 0;
  var celdas = plan.filas.map(function (f) {
    if (f.bloqueo) bloqueadas += 1;
    var meta = pct(f.goal_actual) + " → " + pct(f.goal_propuesto);
    var bloq = f.bloqueo || "—";
    if (f.motivo) bloq += " (" + f.motivo + ")";
    var confirma = "";
    if (f.bloqueo && f.confirmable) {
      confirma = '<input type="checkbox" data-confirmar="' + f.listing_id +
        '" aria-label="Confirmar ' + escapar(f.seller_sku || f.listing_id) + '">';
    }
    return "<tr><td class=\"mono\">" + escapar(f.seller_sku || f.listing_id) +
      "</td><td>" + escapar(f.accion) +
      "</td><td class=\"num\">" + pct(f.m_hoy) +
      "</td><td class=\"num\">" + meta +
      "</td><td class=\"num\">" + escapar(importeCorto(f.p_estrella)) +
      "</td><td" + (f.bloqueo ? " class=\"alerta\"" : "") + ">" + escapar(bloq) +
      "</td><td>" + confirma + "</td></tr>";
  });
  vista.innerHTML =
    "<p class=\"mutado\">huella " + escapar(plan.huella) + " · " +
    plan.filas.length + " filas · " + bloqueadas + " bloqueadas</p>" +
    "<div class=\"tabla-scroll\" role=\"region\" aria-label=\"Vista previa del plan\" tabindex=\"0\">" +
    "<table><thead><tr><th>SKU</th><th>Accion</th><th class=\"num\">M hoy</th>" +
    "<th class=\"num\">Goal</th><th class=\"num\">P*</th><th>Bloqueo</th><th>Confirmar</th></tr></thead>" +
    "<tbody>" + celdas.join("") + "</tbody></table></div>";
}

function planear(form) {
  var estado = estadoDe(form);
  var plataforma = form.getAttribute("data-platform");
  var aplicar = botonAplicarDe(form);
  aplicar.disabled = true;
  delete planesVistos[plataforma];
  var pedido = armarPedido(form, estado);
  if (!pedido) return;
  var seleccion = seleccionDe(form, estado);
  if (!seleccion) return;
  var spec = { platform: plataforma, pedido: pedido };
  if (seleccion.sin_goal) {
    spec.sin_goal = true;
  } else {
    spec.listing_ids = seleccion.listing_ids;
  }
  estado.textContent = "Planeando…";
  postPrecios(form, "/api/precios/goals/plan", spec)
    .then(function (r) {
      if (r.ok) {
        planesVistos[plataforma] = { huella: r.data.huella, spec: spec };
        pintarVista(form, r.data);
        aplicar.disabled = false;
        estado.textContent = "Plan listo: revisa bloqueos y aplica.";
      } else {
        var detalle = r.data && r.data.detail;
        estado.textContent = "Error: " + (detalle || r.status);
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al planear.";
    });
}

function aplicar(form) {
  var estado = estadoDe(form);
  var plataforma = form.getAttribute("data-platform");
  var visto = planesVistos[plataforma];
  if (!visto) {
    estado.textContent = "Primero planea: Aplicar solo vale despues del plan.";
    return;
  }
  var actor = form.elements.actor.value.trim();
  if (!actor) {
    estado.textContent = "El actor firma: escribe quien aplica.";
    return;
  }
  var vista = vistaDe(form);
  var confirmados = Array.prototype.map.call(
    vista.querySelectorAll("[data-confirmar]:checked"),
    function (c) { return Number(c.getAttribute("data-confirmar")); }
  );
  var cuerpo = {
    platform: visto.spec.platform,
    pedido: visto.spec.pedido,
    huella: visto.huella,
    confirmados: confirmados,
    actor: actor,
  };
  if (visto.spec.sin_goal) {
    cuerpo.sin_goal = true;
  } else {
    cuerpo.listing_ids = visto.spec.listing_ids;
  }
  // El go solo viaja en live (la ruta rechaza go en shadow).
  if (visto.spec.pedido.mode === "live") {
    var go = form.elements.go_literal.value;
    if (go.trim() !== "") cuerpo.go_literal = go;
  }
  estado.textContent = "Aplicando…";
  postPrecios(form, "/api/precios/goals/aplicar", cuerpo)
    .then(function (r) {
      if (r.ok) {
        delete planesVistos[plataforma];
        botonAplicarDe(form).disabled = true;
        var lineas = r.data.hechos.map(function (h) {
          return "<li>" + escapar(h.platform) + " #" + h.listing_id + ": " +
            escapar(h.accion) + "</li>";
        });
        vista.innerHTML =
          "<p>Aplicado: " + r.data.sembrados + " sembrados, " +
          r.data.cerrados + " cerrados (lote " + escapar(r.data.lote) + "). " +
          "Las bloqueadas quedaron fuera; recarga para ver la tabla al dia.</p>" +
          (lineas.length ? "<ul>" + lineas.join("") + "</ul>" : "") +
          "<p><button type=\"button\" data-recargar>Recargar</button></p>" +
          vista.innerHTML;
        var recargar = vista.querySelector("[data-recargar]");
        if (recargar) {
          recargar.addEventListener("click", function () { window.location.reload(); });
        }
        estado.textContent = "Aplicado.";
      } else if (r.status === 409 && r.data.detail && r.data.detail.plan_nuevo) {
        planesVistos[plataforma] = { huella: r.data.detail.plan_nuevo.huella, spec: visto.spec };
        pintarVista(form, r.data.detail.plan_nuevo);
        estado.textContent = "El plan cambio: revisalo y aplica de nuevo.";
      } else {
        var detalle = r.data && r.data.detail;
        estado.textContent = "Error: " + (detalle && detalle.message ? detalle.message : detalle || r.status);
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al aplicar.";
    });
}

// Soltar (S.3): el boton vive en _precios_seguridad.html y esta plantilla
// no se toca; el mini-formulario nace aqui, junto al boton.
function cablearSoltar(boton) {
  boton.addEventListener("click", function () {
    if (boton.parentNode.querySelector("[data-liberar-form]")) return;
    var corrida = boton.getAttribute("data-liberar-corrida");
    var caja = document.createElement("span");
    caja.setAttribute("data-liberar-form", "true");
    caja.innerHTML =
      " <label>Actor <input type=\"text\" name=\"actor\" autocomplete=\"off\"></label>" +
      " <label>Nota <input type=\"text\" name=\"nota\" autocomplete=\"off\"></label>" +
      " <label>Token <input type=\"password\" name=\"token\" autocomplete=\"off\"></label>" +
      " <button type=\"button\" data-liberar-ok>Confirmar soltar</button>" +
      " <button type=\"button\" data-liberar-no>Cancelar</button>" +
      " <span data-liberar-estado class=\"mutado\"></span>";
    boton.parentNode.insertBefore(caja, boton.nextSibling);
    caja.querySelector("[data-liberar-no]").addEventListener("click", function () {
      caja.remove();
    });
    caja.querySelector("[data-liberar-ok]").addEventListener("click", function () {
      var estado = caja.querySelector("[data-liberar-estado]");
      var actor = caja.querySelector("input[name=actor]").value.trim();
      var nota = caja.querySelector("input[name=nota]").value.trim();
      var token = caja.querySelector("input[name=token]").value;
      if (!actor || !nota) {
        estado.textContent = "Soltar firma: actor y nota.";
        return;
      }
      estado.textContent = "Soltando…";
      fetch("/api/precios/compuerta/" + corrida + "/liberar", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-orbit-token": token,
        },
        body: JSON.stringify({ actor: actor, nota: nota }),
      }).then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (data) {
          if (resp.ok) {
            estado.textContent = "Soltada.";
            window.location.reload();
          } else {
            var detalle = data && data.detail;
            estado.textContent = "Error: " + (detalle && detalle.message ? detalle.message : detalle || resp.status);
          }
        });
      }).catch(function () {
        estado.textContent = "Error de red al soltar.";
      });
    });
  });
}

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("form[data-precios-bar]").forEach(function (form) {
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      planear(form);
    });
    var aplicarBtn = botonAplicarDe(form);
    if (aplicarBtn) {
      aplicarBtn.addEventListener("click", function () {
        aplicar(form);
      });
    }
  });
  document.querySelectorAll("[data-liberar-corrida]").forEach(cablearSoltar);
});
