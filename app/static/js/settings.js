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

function guardarConfig(form) {
  var estado = form.querySelector("[data-estado]");
  var plataforma = form.getAttribute("data-settings-config");
  var baseId = Number(form.getAttribute("data-config-id"));
  var margenOn = form.elements.margen_habilitado && form.elements.margen_habilitado.checked;
  var target = (form.elements.target_manual_pct.value || "").trim();
  var ack = form.elements.ack_respaldo && form.elements.ack_respaldo.checked;

  if (margenOn && target && !ack) {
    estado.textContent =
      "Marca el ack: el target manual NO gobierna mientras el margen este encendido.";
    return;
  }

  var cuerpo = {
    base_config_version_id: baseId,
    ack_respaldo: !!ack,
    margen: {
      habilitado: !!margenOn,
      fraccion: margenOn ? (form.elements.fraccion.value || null) : null,
    },
  };
  if (target) cuerpo.target_manual_pct = target;

  var caps = {};
  ["bid", "pause", "negative", "harvest"].forEach(function (kind) {
    var el = form.elements["cap_" + kind];
    if (el && el.value !== "") caps[kind] = Number(el.value);
  });
  if (Object.keys(caps).length) cuerpo.caps = caps;

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
  var cuerpo = {
    enabled: !!(form.elements.enabled && form.elements.enabled.checked),
  };
  var t = (form.elements.target_acos_pct.value || "").trim();
  var f = (form.elements.bid_floor.value || "").trim();
  var c = (form.elements.bid_ceiling.value || "").trim();
  if (t) cuerpo.target_acos_pct = t;
  if (f) cuerpo.bid_floor = f;
  if (c) cuerpo.bid_ceiling = c;

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
