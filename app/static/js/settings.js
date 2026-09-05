"use strict";
// JS de /settings (DASHBOARD 01 3.1). Vive en /static por la CSP
// default-src 'self'. Token SOLO en header x-orbit-token (sellado 18).

function tokenSettings() {
  var campo = document.getElementById("settings-token");
  return campo ? campo.value : "";
}

function postJson(url, cuerpo) {
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": tokenSettings(),
    },
    body: JSON.stringify(cuerpo),
  }).then(function (resp) {
    return resp.json().then(function (data) {
      return { ok: resp.ok, status: resp.status, data: data };
    });
  });
}

// Solo viaja lo que el operador CAMBIO respecto a lo que el servidor pinto
// (defaultValue/defaultChecked): reenviar el form entero pediria el ack por
// un target que nadie toco y moveria updated_at de un goal sin edicion.
// Un campo vaciado no viaja (la API no des-configura; None = no tocar).
function cambio(el) {
  if (!el) return false;
  if (el.type === "checkbox") return el.checked !== el.defaultChecked;
  return el.value.trim() !== el.defaultValue.trim() && el.value.trim() !== "";
}

function guardarConfig(form) {
  var estado = form.querySelector("[data-estado]");
  var plataforma = form.getAttribute("data-settings-config");
  var baseId = Number(form.getAttribute("data-config-id"));
  var target = form.elements.target_manual_pct;
  var toggle = form.elements.margen_habilitado;
  var fraccion = form.elements.fraccion;
  var margenOn = toggle.checked;
  var ack = form.elements.ack_respaldo && form.elements.ack_respaldo.checked;

  if (margenOn && cambio(target) && !ack) {
    estado.textContent =
      "Marca el ack: el target manual NO gobierna mientras el margen este encendido.";
    return;
  }

  var cuerpo = { base_config_version_id: baseId, ack_respaldo: !!ack };
  if (cambio(target)) cuerpo.target_manual_pct = target.value.trim();
  if (cambio(toggle) || (margenOn && cambio(fraccion))) {
    cuerpo.margen = margenOn
      ? { habilitado: true, fraccion: fraccion.value.trim() || null }
      : { habilitado: false };
  }

  var caps = {};
  ["bid", "pause", "negative", "harvest"].forEach(function (kind) {
    var el = form.elements["cap_" + kind];
    if (cambio(el)) caps[kind] = Number(el.value);
  });
  if (Object.keys(caps).length) cuerpo.caps = caps;

  if (!cuerpo.target_manual_pct && !cuerpo.margen && !cuerpo.caps) {
    estado.textContent = "Nada que cambiar.";
    return;
  }

  estado.textContent = "Enviando…";
  postJson("/api/ads-optimizer/settings/" + plataforma, cuerpo)
    .then(function (r) {
      if (r.ok) {
        estado.textContent = "Guardado: " + (r.data.label || r.data.config_version_id);
        window.location.reload();
      } else {
        estado.textContent = "Error: " + (r.data.detail || r.status);
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al guardar config.";
    });
}

function guardarGoal(form) {
  var estado = form.querySelector("[data-estado]");
  var goalId = form.getAttribute("data-settings-goal");
  var cuerpo = {};
  if (cambio(form.elements.enabled)) cuerpo.enabled = form.elements.enabled.checked;
  ["target_acos_pct", "bid_floor", "bid_ceiling"].forEach(function (nombre) {
    var el = form.elements[nombre];
    if (cambio(el)) cuerpo[nombre] = el.value.trim();
  });
  if (!Object.keys(cuerpo).length) {
    estado.textContent = "Nada que cambiar.";
    return;
  }

  estado.textContent = "Enviando…";
  postJson("/api/ads-optimizer/goals/" + goalId, cuerpo)
    .then(function (r) {
      if (r.ok) {
        estado.textContent = "Goal #" + goalId + " actualizado.";
        window.location.reload();
      } else {
        estado.textContent = "Error: " + (r.data.detail || r.status);
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al guardar goal.";
    });
}

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("form[data-settings-config]").forEach(function (form) {
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      guardarConfig(form);
    });
  });
  document.querySelectorAll("form[data-settings-goal]").forEach(function (form) {
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      guardarGoal(form);
    });
  });
});
