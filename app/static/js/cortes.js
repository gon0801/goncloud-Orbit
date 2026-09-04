"use strict";
// JS de la pantalla de propuestas (ORBIT 04 3.1; CORTES UI 01: el boton dice
// Rechazar). Vive en /static por la CSP `default-src 'self'`: los <script>
// inline y los handlers on*= quedan BLOQUEADOS por esa politica. El boton
// abre el mini-form de su fila y el submit hace fetch POST
// /api/ads-optimizer/veto con el token en el header x-orbit-token (la query
// string JAMAS autentica, sellado 18). El fetch y el payload NO cambian.

function vetar(form) {
  var estado = form.querySelector("[data-estado]");
  estado.textContent = "Enviando…";
  var cuerpo = {
    queue_id: Number(form.dataset.veto),
    actor: form.elements.actor.value,
    dias: Number(form.elements.dias.value),
  };
  fetch("/api/ads-optimizer/veto", {
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
        estado.textContent = "Rechazado hasta " + (r.data.vence_el || "?") + ".";
      } else {
        estado.textContent = "Error: " + (r.data.detail || r.data) + ".";
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al rechazar.";
    });
}

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("button[data-vetar]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      var fila = document.getElementById("form-" + boton.dataset.vetar);
      if (fila) fila.hidden = !fila.hidden;
    });
  });
  document.querySelectorAll("form[data-veto]").forEach(function (form) {
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      vetar(form);
    });
  });
});
