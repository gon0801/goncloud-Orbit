"use strict";
// JS de /familias (A2). Vive en /static por la CSP default-src 'self'.
// Token SOLO en header x-orbit-token (mismo patron que settings.js).

function tokenFamilias() {
  var campo = document.getElementById("familias-token");
  return campo ? campo.value : "";
}

function postFamilias(url, cuerpo) {
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": tokenFamilias(),
    },
    body: JSON.stringify(cuerpo),
  }).then(function (resp) {
    return resp.json().then(function (data) {
      return { ok: resp.ok, status: resp.status, data: data };
    });
  });
}

function crearFamilia(form, plataforma) {
  var estado = form.querySelector("[data-estado]");
  var nombre = form.elements.nombre.value.trim();
  var padre = form.elements.padre_id.value;
  if (!nombre) {
    estado.textContent = "Escribe el nombre de la familia.";
    return;
  }
  var cuerpo = { platform: plataforma, nombre: nombre };
  if (padre) cuerpo.padre_id = Number(padre);
  estado.textContent = "Enviando…";
  postFamilias("/api/familias", cuerpo)
    .then(function (r) {
      if (r.ok) {
        estado.textContent = "Familia creada: " + r.data.nombre;
        window.location.reload();
      } else {
        estado.textContent = "Error: " + (r.data.detail || r.status);
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al crear la familia.";
    });
}

function asignarSeleccionados(form) {
  var estado = form.querySelector("[data-estado]");
  var familiaId = form.elements.familia_id.value;
  var ids = Array.prototype.map.call(
    document.querySelectorAll(".familias-check:checked"),
    function (c) { return Number(c.value); }
  );
  if (!familiaId) {
    estado.textContent = "Elige la familia destino.";
    return;
  }
  if (!ids.length) {
    estado.textContent = "Selecciona al menos un producto.";
    return;
  }
  estado.textContent = "Enviando…";
  postFamilias("/api/familias/asignar", { familia_id: Number(familiaId), product_ids: ids })
    .then(function (r) {
      if (r.ok) {
        estado.textContent = "Asignados: " + r.data.asignados;
        window.location.reload();
      } else {
        estado.textContent = "Error: " + (r.data.detail || r.status);
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al asignar.";
    });
}

document.addEventListener("DOMContentLoaded", function () {
  var plataforma = document.querySelector("#familias-filtros select[name=plataforma]");
  var crear = document.getElementById("familias-crear");
  if (crear) {
    crear.addEventListener("submit", function (evento) {
      evento.preventDefault();
      crearFamilia(crear, plataforma ? plataforma.value : "amazon_mx");
    });
  }
  var asignar = document.getElementById("familias-asignar");
  if (asignar) {
    asignar.addEventListener("submit", function (evento) {
      evento.preventDefault();
      asignarSeleccionados(asignar);
    });
  }
});
