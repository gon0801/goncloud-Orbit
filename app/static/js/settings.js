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

// A6: el select del motor de bids compara contra la opcion que el
// servidor pinto (defaultSelected); viaja si cambio (bandas_v1 o
// evidencia_v2 explicitos; la API jamas popea: ausente lee v1).
function cambioSelect(sel) {
  if (!sel) return false;
  var inicial = null;
  for (var i = 0; i < sel.options.length; i++) {
    if (sel.options[i].defaultSelected) inicial = sel.options[i].value;
  }
  return sel.value !== inicial;
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
  // A3: solo viaja lo cambiado (cambio), como texto (Decimal exacto).
  ["confianza_recorte", "confianza_subida"].forEach(function (nombre) {
    var el = form.elements[nombre];
    if (cambio(el)) cuerpo[nombre] = el.value.trim();
  });
  // A6: el motor viaja si el select cambio (bandas_v1 o evidencia_v2).
  var motor = form.elements.motor_bid;
  if (cambioSelect(motor)) cuerpo.motor_bid = motor.value;
  // S.2: apagador de precios (global + por universo): solo viaja lo cambiado.
  var precioGlobal = form.elements.precio_modo_global;
  if (cambioSelect(precioGlobal) && precioGlobal.value) cuerpo.precio_modo_global = precioGlobal.value;
  var modosUniverso = {};
  form.querySelectorAll("select[data-precio-universo]").forEach(function (sel) {
    if (cambioSelect(sel)) modosUniverso[sel.getAttribute("data-precio-universo")] = sel.value;
  });
  if (Object.keys(modosUniverso).length) cuerpo.precio_modo_universo = modosUniverso;
  var metasResumen = {};
  form.querySelectorAll("input[data-resumen-acos]").forEach(function (inp) {
    var actual = inp.value.trim();
    var previo = inp.defaultValue.trim();
    if (actual !== previo) {
      metasResumen[inp.getAttribute("data-resumen-acos")] = actual === "" ? null : actual;
    }
  });
  if (Object.keys(metasResumen).length) cuerpo.resumen_acos_objetivo = metasResumen;
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

  // TEMPORADA 01 A: subida rapida de bids (bloque «Subida rápida», bajo el
  // motor de bids). El modo compara contra el que el servidor pinto.
  var recuperacion = form.querySelector("input[name^='recuperacion_']:checked");
  var inicialRecuperacion = null;
  var bloqueRecuperacion = form.querySelector("[data-recuperacion]");
  if (bloqueRecuperacion) inicialRecuperacion = bloqueRecuperacion.getAttribute("data-recuperacion-inicial");
  if (recuperacion && recuperacion.value !== (inicialRecuperacion || "off")) {
    cuerpo.recuperacion_modo = recuperacion.value;
  }
  var memoria = form.elements.recuperacion_memoria_dias;
  if (cambio(memoria)) cuerpo.recuperacion_memoria_dias = Number(memoria.value);

  if (
    !cuerpo.target_manual_pct &&
    !cuerpo.margen &&
    !cuerpo.caps &&
    !cuerpo.confianza_recorte &&
    !cuerpo.confianza_subida &&
    !("motor_bid" in cuerpo) &&
    !("precio_modo_global" in cuerpo) &&
    !("precio_modo_universo" in cuerpo) &&
    !("resumen_acos_objetivo" in cuerpo) &&
    !("recuperacion_modo" in cuerpo) &&
    !("recuperacion_memoria_dias" in cuerpo)
  ) {
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
  prepararTemporada();
});

// ---------------------------------------------------------------------------
// TEMPORADA 01 A: tarjeta «Temporadas de venta» (global: se guarda igual
// desde cualquier plataforma; el POST va por amazon_mx solo para el label).
// El JS no repite ninguna regla: los rangos y los mensajes viven en el
// servidor (temporada.valida) y la vista previa en temporada.resumen.
// ---------------------------------------------------------------------------

var MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
var tarjetaTemporada = null;
var temporadaInicial = null;
var finGuardado = null;
var pausadasGuardadas = [];

function fechaCorta(iso) {
  if (!iso) return "";
  var partes = iso.split("-");
  return Number(partes[2]) + " " + (MESES_CORTOS[Number(partes[1]) - 1] || "");
}

function valorCampo(nombre) {
  var el = tarjetaTemporada.querySelector("[data-temporada-campo='" + nombre + "']");
  return el ? el.value.trim() : "";
}

function temporadaDesdeFormulario() {
  var meses = [];
  tarjetaTemporada.querySelectorAll("select[data-mes]").forEach(function (sel) {
    meses.push(sel.value);
  });
  var modos = {};
  tarjetaTemporada.querySelectorAll("[data-temporada-plataforma]").forEach(function (bloque) {
    var plataforma = bloque.getAttribute("data-temporada-plataforma");
    var marcado = bloque.querySelector("input[name='modo_" + plataforma + "']:checked");
    modos[plataforma] = marcado ? marcado.value : "off";
  });
  var metas = {};
  tarjetaTemporada.querySelectorAll("input[data-meta-alta]").forEach(function (inp) {
    var valor = inp.value.trim();
    if (valor !== "") metas[inp.getAttribute("data-meta-alta")] = valor;
  });
  var pctTopada = tarjetaTemporada.querySelector("[data-topada-campo='pct']");
  var diasTopada = tarjetaTemporada.querySelector("[data-topada-campo='dias']");
  return {
    base_config_version_id: Number(tarjetaTemporada.getAttribute("data-config-id")),
    temporada: {
      version: "temporada_v1",
      meses: meses,
      reduccion_baja_pct: valorCampo("reduccion_baja_pct"),
      dias_preparacion: valorCampo("dias_preparacion"),
      mult_cortes_baja: valorCampo("mult_cortes_baja"),
      aumento_presupuesto_alta_pct: valorCampo("aumento_presupuesto_alta_pct"),
      fin_anticipado: finGuardado,
      mantener_pausadas: pausadasGuardadas,
    },
    campana_topada: {
      pct: pctTopada ? pctTopada.value.trim() : "",
      dias: diasTopada ? Number(diasTopada.value) : 0,
    },
    temporada_modo: modos,
    meta_alta: metas,
  };
}

function temporadaCuerpoComparable() {
  var meses = [];
  tarjetaTemporada.querySelectorAll("select[data-mes]").forEach(function (sel) {
    meses.push(sel.value);
  });
  var campos = {};
  ["reduccion_baja_pct", "dias_preparacion", "mult_cortes_baja", "aumento_presupuesto_alta_pct"].forEach(
    function (nombre) {
      campos[nombre] = valorCampo(nombre);
    }
  );
  var topada = {
    pct: (tarjetaTemporada.querySelector("[data-topada-campo='pct']") || {}).value || "",
    dias: (tarjetaTemporada.querySelector("[data-topada-campo='dias']") || {}).value || "",
  };
  var modos = {};
  tarjetaTemporada.querySelectorAll("[data-temporada-plataforma]").forEach(function (bloque) {
    var plataforma = bloque.getAttribute("data-temporada-plataforma");
    var marcado = bloque.querySelector("input[name='modo_" + plataforma + "']:checked");
    modos[plataforma] = marcado ? marcado.value : "off";
  });
  var metas = {};
  tarjetaTemporada.querySelectorAll("input[data-meta-alta]").forEach(function (inp) {
    var valor = inp.value.trim();
    if (valor !== "") metas[inp.getAttribute("data-meta-alta")] = valor;
  });
  return JSON.stringify({
    meses: meses,
    campos: campos,
    topada: topada,
    modos: modos,
    metas: metas,
  });
}

function pintaPlataformas(plataformas) {
  Object.keys(plataformas || {}).forEach(function (plataforma) {
    var dato = plataformas[plataforma];
    var frase = tarjetaTemporada.querySelector("[data-frase-meta='" + plataforma + "']");
    if (frase) {
      var partes = [];
      if (dato.frase_meta_hoy) partes.push(dato.frase_meta_hoy);
      if (dato.meta_alta_frase) partes.push(dato.meta_alta_frase);
      frase.textContent = partes.join(" ");
    }
    var linea = tarjetaTemporada.querySelector("[data-linea-equilibrio='" + plataforma + "']");
    if (linea) {
      linea.textContent = dato.linea || "";
      linea.hidden = !dato.linea;
    }
    var razon = tarjetaTemporada.querySelector("[data-razon-equilibrio='" + plataforma + "']");
    if (razon) {
      razon.textContent = dato.razon || "";
      razon.hidden = !dato.razon;
    }
    var aviso = tarjetaTemporada.querySelector("[data-aviso-rojo='" + plataforma + "']");
    if (aviso) {
      aviso.textContent = dato.aviso || "";
      aviso.hidden = !dato.aviso;
    }
  });
}

function pintaVistaPrevia(data) {
  var aviso = tarjetaTemporada.querySelector("[data-aviso-cambio]");
  aviso.textContent = (data.resumen && data.resumen.aviso_cambio) || "";
  aviso.hidden = !aviso.textContent;
  var lista = tarjetaTemporada.querySelector("[data-proximos]");
  lista.innerHTML = "";
  (data.resumen ? data.resumen.proximos : []).forEach(function (evento) {
    var li = document.createElement("li");
    li.textContent = fechaCorta(evento.fecha) + " — " + evento.frase;
    lista.appendChild(li);
  });
  pintaPlataformas(data.plataformas);
}

function consultaVistaPrevia() {
  var params = new URLSearchParams();
  var meses = [];
  tarjetaTemporada.querySelectorAll("select[data-mes]").forEach(function (sel) {
    meses.push(sel.value);
  });
  params.set("meses", meses.join(","));
  ["reduccion_baja_pct", "dias_preparacion", "mult_cortes_baja", "aumento_presupuesto_alta_pct"].forEach(
    function (nombre) {
      params.set(nombre, valorCampo(nombre));
    }
  );
  tarjetaTemporada.querySelectorAll("input[data-meta-alta]").forEach(function (inp) {
    var valor = inp.value.trim();
    if (valor !== "") params.set("meta_alta_" + inp.getAttribute("data-meta-alta"), valor);
  });
  fetch("/api/ads-optimizer/temporada/vista-previa?" + params.toString())
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, status: resp.status, data: data };
      });
    })
    .then(function (r) {
      var error = tarjetaTemporada.querySelector("[data-error-previa]");
      if (r.ok) {
        error.hidden = true;
        pintaVistaPrevia(r.data);
      } else {
        error.textContent = "Error: " + ((r.data && r.data.detail) || r.status);
        error.hidden = false;
      }
    })
    .catch(function () {
      var error = tarjetaTemporada.querySelector("[data-error-previa]");
      error.textContent = "Error de red con la vista previa.";
      error.hidden = false;
    });
}

function guardarTemporada() {
  var estado = tarjetaTemporada.querySelector("[data-estado-temporada]");
  // Sin calendario guardado, guardar los valores sugeridos ES el cambio
  // (hallazgo de revision: la comparacion solo aplica con config guardada).
  var guardada = tarjetaTemporada.getAttribute("data-guardada") === "true";
  if (guardada && temporadaCuerpoComparable() === temporadaInicial) {
    estado.textContent = "Nada que cambiar.";
    return;
  }
  estado.textContent = "Enviando…";
  postJson("/api/ads-optimizer/settings/amazon_mx", temporadaDesdeFormulario())
    .then(function (r) {
      if (r.ok) {
        estado.textContent = r.data.mensaje || "Guardado: " + (r.data.label || r.data.config_version_id);
        window.location.reload();
      } else {
        estado.textContent = "Error: " + ((r.data && r.data.detail) || r.status);
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al guardar temporadas.";
    });
}

function terminarBaja() {
  var estado = tarjetaTemporada.querySelector("[data-estado-baja]");
  estado.textContent = "Enviando…";
  postJson("/api/ads-optimizer/temporada/terminar-baja", {
    base_config_version_id: Number(tarjetaTemporada.getAttribute("data-config-id")),
  })
    .then(function (r) {
      if (r.ok) {
        estado.textContent = "La baja termina hoy: los bids regresan en el siguiente ciclo.";
        window.location.reload();
      } else {
        estado.textContent = "Error: " + ((r.data && r.data.detail) || r.status);
      }
    })
    .catch(function () {
      estado.textContent = "Error de red al terminar la baja.";
    });
}

function prepararTemporada() {
  tarjetaTemporada = document.querySelector("[data-temporada]");
  if (!tarjetaTemporada) return;
  finGuardado = tarjetaTemporada.getAttribute("data-fin-anticipado") || null;
  try {
    pausadasGuardadas = JSON.parse(tarjetaTemporada.getAttribute("data-mantener-pausadas")) || [];
  } catch (e) {
    pausadasGuardadas = [];
  }
  temporadaInicial = temporadaCuerpoComparable();
  tarjetaTemporada
    .querySelectorAll("select[data-mes], input[data-temporada-campo], input[data-meta-alta], input[data-topada-campo]")
    .forEach(function (el) {
      el.addEventListener("change", consultaVistaPrevia);
    });
  tarjetaTemporada.querySelectorAll("input[name^='modo_']").forEach(function (radio) {
    radio.addEventListener("change", consultaVistaPrevia);
  });
  var botonGuardar = tarjetaTemporada.querySelector("[data-guardar-temporada]");
  if (botonGuardar) botonGuardar.addEventListener("click", guardarTemporada);
  var botonBaja = tarjetaTemporada.querySelector("[data-terminar-baja]");
  if (botonBaja) botonBaja.addEventListener("click", terminarBaja);
  var desde = tarjetaTemporada.querySelector("[data-temporada-desde]");
  if (desde && desde.textContent.trim()) {
    desde.textContent = fechaCorta(desde.textContent.trim());
  }
  consultaVistaPrevia();
}
