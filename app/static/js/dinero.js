"use strict";
// JS de donde-poner-el-dinero (BIDS 02, P.2b). Vive en /static por la CSP
// `default-src 'self'`: los <script> inline y los handlers on*= quedan
// BLOQUEADOS por esa politica. Sin el literal `APLICAR AJUSTE` exacto no sale el POST
// campana-ajuste/aplicar. El boton Regresar manda POST
// campana-ajuste/{ajuste_id}/regresar con actor y token en x-orbit-token
// (la query string JAMAS autentica). El plan jamas se calcula en JS: todo
// numero lo resuelve la API.

function paramsAjuste(form) {
  var params = "campana_id=" + encodeURIComponent(form.dataset.campanaId)
    + "&clase=" + encodeURIComponent(form.dataset.clase);
  if (form.dataset.clase === "presupuesto" && form.elements.presupuesto) {
    params += "&presupuesto=" + encodeURIComponent(form.elements.presupuesto.value);
  }
  if (form.dataset.clase === "ajuste_ubicacion") {
    params += "&ubicacion=" + encodeURIComponent(form.elements.ubicacion.value)
      + "&porcentaje=" + encodeURIComponent(form.elements.porcentaje.value);
  }
  return params;
}

function verPlan(form) {
  var frase = form.querySelector("[data-frase]");
  var estado = form.querySelector("[data-estado]");
  var aplicar = form.querySelector('button[type="submit"]');
  estado.textContent = "Consultando…";
  fetch("/api/ads-optimizer/campana-ajuste/plan?" + paramsAjuste(form))
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
      frase.textContent = r.data.frase;
      form.dataset.huella = r.data.huella;
      aplicar.disabled = false;
      estado.textContent = "";
    })
    .catch(function () {
      estado.textContent = "Error de red al pedir el plan.";
    });
}

function aplicar(form) {
  var estado = form.querySelector("[data-estado]");
  if (!form.dataset.huella) {
    estado.textContent = "Pide el plan primero.";
    return;
  }
  if (form.elements.confirmacion.value !== "APLICAR AJUSTE") {
    estado.textContent = "Escribe APLICAR AJUSTE para confirmar.";
    return;
  }
  var aplicarBtn = form.querySelector('button[type="submit"]');
  aplicarBtn.disabled = true;
  estado.textContent = "Enviando…";
  var cuerpo = {
    campana_id: Number(form.dataset.campanaId),
    clase: form.dataset.clase,
    huella: form.dataset.huella,
    confirmacion: form.elements.confirmacion.value,
    actor: form.elements.actor.value,
  };
  if (form.dataset.clase === "presupuesto") {
    cuerpo.presupuesto = Number(form.elements.presupuesto.value);
  }
  if (form.dataset.clase === "ajuste_ubicacion") {
    cuerpo.ubicacion = form.elements.ubicacion.value;
    cuerpo.porcentaje = Number(form.elements.porcentaje.value);
  }
  fetch("/api/ads-optimizer/campana-ajuste/aplicar", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": form.elements.token.value,
    },
    body: JSON.stringify(cuerpo),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, status: resp.status, data: data };
      });
    })
    .then(function (r) {
      if (r.ok) {
        estado.textContent = "Ajuste aplicado.";
        return;
      }
      var detalle = r.data.detail;
      estado.textContent = "Error: " + ((detalle && detalle.message) || detalle || r.data) + ".";
      aplicarBtn.disabled = false;
    })
    .catch(function () {
      estado.textContent = "Error de red al aplicar.";
      aplicarBtn.disabled = false;
    });
}

function regresar(form) {
  var estado = form.querySelector("[data-estado]");
  var boton = form.querySelector('button[type="submit"]');
  boton.disabled = true;
  estado.textContent = "Enviando…";
  fetch("/api/ads-optimizer/campana-ajuste/" + form.dataset.ajusteId + "/regresar", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": form.elements.token.value,
    },
    body: JSON.stringify({ actor: form.elements.actor.value }),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, data: data };
      });
    })
    .then(function (r) {
      if (r.ok) {
        estado.textContent = "Ajuste regresado.";
      } else {
        estado.textContent = "Error: " + (r.data.detail || r.data) + ".";
        boton.disabled = false;
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al regresar.";
      boton.disabled = false;
    });
}

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("button[data-ajuste-clase]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      var form = document.getElementById(
        "ajuste-" + boton.dataset.campanaId + "-" + boton.dataset.ajusteClase
      );
      if (!form) return;
      form.hidden = !form.hidden;
      // Fuera de Amazon no pide parametros: el boton pide el plan directo.
      if (!form.hidden && boton.dataset.ajusteClase === "fuera_de_amazon") {
        verPlan(form);
      }
    });
  });
  document.querySelectorAll("button[data-regresar-ajuste]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      var form = document.getElementById("regreso-ajuste-" + boton.dataset.regresarAjuste);
      if (form) form.hidden = !form.hidden;
    });
  });
  document.querySelectorAll("form[data-ajuste-form]").forEach(function (form) {
    form.querySelector("[data-ver-plan]").addEventListener("click", function () {
      verPlan(form);
    });
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      aplicar(form);
    });
  });
  document.querySelectorAll("form[data-regresar-form]").forEach(function (form) {
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      regresar(form);
    });
  });
});
