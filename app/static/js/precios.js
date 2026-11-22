(function () {
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
  return Orbit.postJson(url, cuerpo, tokenPrecios(form));
}

function pct(fraccion) {
  return Orbit.fraccionPct(fraccion);
}

function importeCorto(importe) {
  if (!importe) return "—";
  return Orbit.dinero(importe.valor) + " " + (importe.moneda || "");
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
  var filas = plan.filas.map(function (f) {
    if (f.bloqueo) bloqueadas += 1;
    var meta = pct(f.goal_actual) + " → " + pct(f.goal_propuesto);
    var bloq = f.bloqueo ? Orbit.etiqueta(f.bloqueo, "bloqueo_siembra") : "—";
    if (f.motivo) bloq += " (" + Orbit.etiqueta(f.motivo, "motivo_siembra") + ")";
    var confirma = null;
    if (f.bloqueo && f.confirmable) {
      confirma = Orbit.el("input", {
        type: "checkbox",
        "data-confirmar": f.listing_id,
        "aria-label": "Confirmar " + (f.seller_sku || f.listing_id),
      });
    }
    return Orbit.el(
      "tr",
      null,
      Orbit.el("td", { class: "mono" }, f.seller_sku || f.listing_id),
      Orbit.el("td", null, Orbit.etiqueta(f.accion, "accion_siembra")),
      Orbit.el("td", { class: "num" }, pct(f.m_hoy)),
      Orbit.el("td", { class: "num" }, meta),
      Orbit.el("td", { class: "num" }, importeCorto(f.p_estrella)),
      Orbit.el("td", f.bloqueo ? { class: "alerta" } : null, bloq),
      Orbit.el("td", null, confirma)
    );
  });
  var cabecera = Orbit.el(
    "tr",
    null,
    Orbit.el("th", null, "SKU"),
    Orbit.el("th", null, "Acción"),
    Orbit.el("th", { class: "num" }, "Margen hoy"),
    Orbit.el("th", { class: "num" }, "Meta"),
    Orbit.el("th", { class: "num" }, "Precio objetivo"),
    Orbit.el("th", null, "Bloqueo"),
    Orbit.el("th", null, "Confirmar")
  );
  var resumen = Orbit.el(
    "p",
    { class: "mutado" },
    "Huella " + plan.huella + " · " +
      Orbit.numero(plan.filas.length) + " filas · " +
      Orbit.numero(bloqueadas) + " bloqueadas"
  );
  var cajaTabla = Orbit.el(
    "div",
    { class: "tabla-scroll", role: "region", "aria-label": "Vista previa del plan", tabindex: "0" },
    Orbit.el("table", null, Orbit.el("thead", null, cabecera), Orbit.el("tbody", null, filas))
  );
  vista.replaceChildren(resumen, cajaTabla);
}

function planear(form) {
  var estado = estadoDe(form);
  var plataforma = form.getAttribute("data-platform");
  var aplicarBtn = botonAplicarDe(form);
  aplicarBtn.disabled = true;
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
        aplicarBtn.disabled = false;
        estado.textContent = "Plan listo: revisa bloqueos y aplica.";
      } else {
        estado.textContent = "Error: " + Orbit.errorTexto(r.data, r.status);
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
        var nodos = [
          Orbit.el(
            "p",
            null,
            "Aplicado: " + Orbit.numero(r.data.sembrados) + " metas puestas, " +
              Orbit.numero(r.data.cerrados) + " quitadas (lote " + r.data.lote + "). " +
              "Las bloqueadas quedaron fuera; recarga para ver la tabla al dia."
          ),
        ];
        var hechos = r.data.hechos || [];
        if (hechos.length) {
          nodos.push(
            Orbit.el(
              "ul",
              null,
              hechos.map(function (h) {
                return Orbit.el(
                  "li",
                  null,
                  Orbit.etiqueta(h.platform, "plataforma") + " #" + h.listing_id + ": " +
                    Orbit.etiqueta(h.accion, "accion_siembra")
                );
              })
            )
          );
        }
        var btnRecargar = Orbit.el("button", { type: "button", "data-recargar": true }, "Recargar");
        btnRecargar.addEventListener("click", function () { window.location.reload(); });
        nodos.push(Orbit.el("p", null, btnRecargar));
        for (var i = nodos.length - 1; i >= 0; i--) {
          vista.insertBefore(nodos[i], vista.firstChild);
        }
        estado.textContent = "Aplicado.";
      } else if (r.status === 409 && r.data.detail && r.data.detail.plan_nuevo) {
        planesVistos[plataforma] = { huella: r.data.detail.plan_nuevo.huella, spec: visto.spec };
        pintarVista(form, r.data.detail.plan_nuevo);
        estado.textContent = "El plan cambio: revisalo y aplica de nuevo.";
      } else {
        estado.textContent = "Error: " + Orbit.errorTexto(r.data, r.status);
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
    var inActor = Orbit.el("input", { type: "text", name: "actor", autocomplete: "off" });
    var inNota = Orbit.el("input", { type: "text", name: "nota", autocomplete: "off" });
    var inToken = Orbit.el("input", { type: "password", name: "token", autocomplete: "off" });
    var btnOk = Orbit.el("button", { type: "submit", "data-liberar-ok": true }, "Confirmar soltar");
    var btnNo = Orbit.el("button", { type: "button", "data-liberar-no": true }, "Cancelar");
    var estado = Orbit.el("span", { "data-liberar-estado": true, class: "mutado" });
    var caja = Orbit.el(
      "form",
      { "data-liberar-form": "true" },
      " ",
      Orbit.el("label", null, "Actor ", inActor),
      " ",
      Orbit.el("label", null, "Nota ", inNota),
      " ",
      Orbit.el("label", null, "Token ", inToken),
      " ",
      btnOk,
      " ",
      btnNo,
      " ",
      estado
    );
    boton.parentNode.insertBefore(caja, boton.nextSibling);
    btnNo.addEventListener("click", function () {
      caja.remove();
    });
    caja.addEventListener("submit", function (evento) {
      evento.preventDefault();
      var actor = inActor.value.trim();
      var nota = inNota.value.trim();
      var token = inToken.value;
      if (!actor || !nota) {
        estado.textContent = "Soltar firma: actor y nota.";
        return;
      }
      estado.textContent = "Soltando…";
      Orbit.postJson("/api/precios/compuerta/" + corrida + "/liberar", { actor: actor, nota: nota }, token)
        .then(function (r) {
          if (r.ok) {
            estado.textContent = "Soltada.";
            window.location.reload();
          } else {
            estado.textContent = "Error: " + Orbit.errorTexto(r.data, r.status);
          }
        })
        .catch(function () {
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
})();
