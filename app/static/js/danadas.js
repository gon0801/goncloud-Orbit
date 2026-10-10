"use strict";
// JS de la pantalla de keywords danadas (BIDS 02, P.3b). Vive en /static
// por la CSP `default-src 'self'`: los <script> inline y los handlers on*=
// quedan BLOQUEADOS por esa politica. El submit hace fetch POST
// /api/ads-optimizer/bid/regresar con el token en el header x-orbit-token
// (la query string JAMAS autentica). "Regresar todas" exige el literal
// REGRESAR <N> KEYWORDS exacto antes de mandar POST
// /api/ads-optimizer/bid/regresar-todas.

function regresar(form) {
  var estado = form.querySelector("[data-estado]");
  estado.textContent = "Enviando…";
  var cuerpo = {
    hoja_id: Number(form.dataset.regreso),
    actor: form.elements.actor.value,
  };
  fetch("/api/ads-optimizer/bid/regresar", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": form.elements.token.value,
    },
    body: JSON.stringify(cuerpo),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, data: data };
      });
    })
    .then(function (r) {
      if (r.ok) {
        estado.textContent =
          "Regresado a " + (r.data.bid_ahora || "?") + " " + (r.data.moneda || "") + ".";
      } else {
        estado.textContent = "Error: " + (r.data.detail || r.data) + ".";
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al regresar.";
    });
}

function regresarTodas(form) {
  var estado = form.querySelector("[data-estado]");
  var esperada = form.dataset.esperada;
  var confirmacion = form.elements.confirmacion.value;
  if (confirmacion !== esperada) {
    estado.textContent = "Escribe " + esperada + " para confirmar.";
    return;
  }
  estado.textContent = "Enviando…";
  var cuerpo = {
    plataforma: form.dataset.plataforma,
    confirmacion: confirmacion,
    actor: form.elements.actor.value,
  };
  fetch("/api/ads-optimizer/bid/regresar-todas", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": form.elements.token.value,
    },
    body: JSON.stringify(cuerpo),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, data: data };
      });
    })
    .then(function (r) {
      if (!r.ok) {
        estado.textContent = "Error: " + (r.data.detail || r.data) + ".";
        return;
      }
      var resultados = r.data.resultados || [];
      var hechas = resultados.filter(function (x) {
        return x.ok;
      });
      var texto =
        "Regresadas " + hechas.length + " de " + resultados.length + ".";
      var pendientes = resultados
        .filter(function (x) {
          return !x.ok;
        })
        .map(function (x) {
          return x.motivo;
        });
      if (pendientes.length) {
        texto += " Pendientes: " + pendientes.join("; ") + ".";
      }
      estado.textContent = texto;
    })
    .catch(function () {
      estado.textContent = "Error de red al regresar todas.";
    });
}

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("button[data-regresar]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      var fila = document.getElementById("regreso-" + boton.dataset.regresar);
      if (fila) fila.hidden = !fila.hidden;
    });
  });
  document.querySelectorAll("form[data-regreso]").forEach(function (form) {
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      regresar(form);
    });
  });
  document.querySelectorAll("form[data-regresar-todas]").forEach(function (form) {
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      regresarTodas(form);
    });
  });
});
