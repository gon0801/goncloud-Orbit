"use strict";
// JS de productos sin clics (BIDS 02, P.4). Vive en /static por la CSP
// `default-src 'self'`: los <script> inline y los handlers on*= quedan
// BLOQUEADOS por esa politica. "Ver lanzamiento" pide POST
// /api/fabrica/impulso/plan con los listings marcados y pinta la vista
// previa; "Lanzar impulso" exige el literal LANZAR IMPULSO DE <N>
// PRODUCTOS antes de mandar POST /api/fabrica/impulso/lanzar.
// "Regresarlo a sus grupos de antes" exige REGRESAR <N> ANUNCIOS y manda
// POST /api/fabrica/retiro/reponer. Sacar y regresar dicen "n de m" y
// nombran cada anuncio que no quedo (AUTO-03 G.4); los adoptados y los
// archivados piden su propio literal: REGRESAR <K> ANUNCIOS con
// incluye_adoptados, o RECREAR <K> ANUNCIOS a /api/fabrica/retiro/recrear.
// El token viaja en el header x-orbit-token (la query string JAMAS
// autentica).
// "Meta por producto" (AUTO-03 E.1) solo filtra filas ya pintadas por el
// servidor: con meta, sin meta o por motivo.

var vistaGuardada = null;
var retiroGuardado = null;

function marcados(selector) {
  var listings = [];
  var subfamilias = {};
  var faltan = [];
  document.querySelectorAll(selector).forEach(function (casilla) {
    if (!casilla.checked) return;
    var producto = casilla.dataset.producto;
    var campo = document.querySelector('[data-subfamilia="' + producto + '"]');
    var sub = campo ? campo.value.trim() : "";
    if (!sub) {
      faltan.push(producto);
      return;
    }
    casilla.dataset.listings
      .split(",")
      .map(function (x) {
        return Number(x);
      })
      .filter(function (x) {
        return x > 0;
      })
      .forEach(function (lid) {
        listings.push(lid);
        subfamilias[String(lid)] = sub;
      });
  });
  return { listings: listings, subfamilias: subfamilias, faltan: faltan };
}

function mensajeError(data) {
  var detalle = data && data.detail;
  if (typeof detalle === "string") return detalle;
  if (detalle && detalle.mensaje) {
    var texto = detalle.mensaje;
    var lanzados = detalle.lanzados || [];
    if (lanzados.length) texto += " · lanzados: " + lanzados.length;
    var fallos = detalle.fallos || {};
    Object.keys(fallos).forEach(function (lid) {
      texto += " · " + lid + ": " + fallos[lid];
    });
    return texto;
  }
  if (Array.isArray(detalle)) {
    return detalle
      .map(function (e) {
        return e.msg;
      })
      .join("; ");
  }
  return "No se pudo completar la solicitud.";
}

function parrafo(texto) {
  var p = document.createElement("p");
  p.textContent = texto;
  return p;
}

function pintaVista(zona, vista) {
  zona.replaceChildren();
  vista.planes.forEach(function (plan) {
    var div = document.createElement("div");
    div.appendChild(parrafo(plan.seller_sku + " · " + plan.subfamilia));
    div.appendChild(
      parrafo(
        "Tope " +
          plan.tope.monto +
          " " +
          plan.tope.moneda +
          " · " +
          plan.tope.presupuesto_diario +
          " al día · " +
          plan.keywords_exact.length +
          " keywords · " +
          plan.negatives.length +
          " negatives"
      )
    );
    if (plan.bids_acotados && plan.bids_acotados.length) {
      div.appendChild(parrafo("Bids acotados: " + plan.bids_acotados.join(", ")));
    }
    var sinSugerencia = plan.bid_por_rol
      ? Object.keys(plan.bid_por_rol).filter(function (rol) {
          return plan.bid_por_rol[rol] === null || plan.bid_por_rol[rol] === undefined;
        })
      : [];
    if (sinSugerencia.length) {
      div.appendChild(
        parrafo("Sin sugerencia (salen al diario de su campana): " + sinSugerencia.join(", "))
      );
    }
    if (plan.retiros && plan.retiros.length) {
      div.appendChild(parrafo("Sale de " + plan.retiros.length + " anuncios viejos."));
    }
    zona.appendChild(div);
  });
  (vista.no_lanzables || []).forEach(function (n) {
    zona.appendChild(parrafo("No lanzable " + n.listing_id + ": " + n.motivo));
  });
}

function verLanzamiento(selectorCasillas) {
  var zona = document.getElementById("productos-vista");
  var bloque = document.getElementById("productos-lanzamiento");
  var form = document.getElementById("productos-lanzar");
  var marca = marcados(selectorCasillas);
  if (!marca.listings.length) {
    zona.replaceChildren(parrafo("Marca al menos un producto."));
    return;
  }
  if (marca.faltan.length) {
    zona.replaceChildren(parrafo("Falta la subfamilia de " + marca.faltan.length + " marcados."));
    return;
  }
  var solicitud = {
    plataforma: form.dataset.plataforma,
    listing_ids: marca.listings,
    subfamilia_por_listing: marca.subfamilias,
  };
  zona.replaceChildren(parrafo("Pidiendo la vista previa…"));
  fetch("/api/fabrica/impulso/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(solicitud),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, data: data };
      });
    })
    .then(function (r) {
      if (!r.ok) {
        zona.replaceChildren(parrafo("Error: " + mensajeError(r.data) + "."));
        return;
      }
      vistaGuardada = { solicitud: solicitud, vista: r.data };
      pintaVista(zona, r.data);
      document.getElementById("productos-esperada").textContent = r.data.confirmacion_esperada;
      form.elements.confirmacion.placeholder = r.data.confirmacion_esperada;
      form.hidden = false;
      bloque.hidden = false;
    })
    .catch(function () {
      zona.replaceChildren(parrafo("Error de red al pedir la vista previa."));
    });
}

function lanzar(form) {
  var estado = form.querySelector("[data-estado]");
  if (!vistaGuardada) {
    estado.textContent = "Pide primero la vista previa.";
    return;
  }
  var esperada = vistaGuardada.vista.confirmacion_esperada;
  var confirmacion = form.elements.confirmacion.value;
  if (confirmacion !== esperada) {
    estado.textContent = "Escribe " + esperada + " para confirmar.";
    return;
  }
  estado.textContent = "Enviando…";
  fetch("/api/fabrica/impulso/lanzar", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": form.elements.token.value,
    },
    body: JSON.stringify({
      solicitud: vistaGuardada.solicitud,
      huella: vistaGuardada.vista.huella,
      confirmacion: confirmacion,
      actor: form.elements.actor.value,
    }),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, status: resp.status, data: data };
      });
    })
    .then(function (r) {
      if (!r.ok) {
        var detalle = r.data && r.data.detail ? r.data.detail : {};
        if (r.status === 409 && detalle.vista_previa) {
          vistaGuardada = { solicitud: vistaGuardada.solicitud, vista: detalle.vista_previa };
          pintaVista(document.getElementById("productos-vista"), detalle.vista_previa);
          document.getElementById("productos-esperada").textContent =
            detalle.vista_previa.confirmacion_esperada;
          estado.textContent = "El plan cambió: revisa la vista nueva y confirma de nuevo.";
          return;
        }
        estado.textContent = "Error: " + mensajeError(r.data) + ".";
        return;
      }
      vistaGuardada = null;
      form.elements.token.value = "";
      estado.textContent = "Lanzados " + (r.data.lanzados || []).length + ".";
    })
    .catch(function () {
      estado.textContent = "Error de red al lanzar.";
    });
}

var ETIQUETA_RETIRO = {
  retirado: "retirado",
  adoptado: "ya estaba pausado: queda adoptado",
  ya_retirado: "ya estaba retirado",
  cerrado: "archivado o inexistente en Amazon",
  pendiente: "sin confirmar: se reintenta",
  rechazado: "Amazon lo rechazó",
  no_existe: "no existe en Amazon",
  archivado: "archivado en Amazon",
};

var ETIQUETA_REGRESO = {
  repuesto: "regresado",
  recreado: "recreado",
  ya_repuesto: "ya estaba regresado",
  adoptado_sin_reposicion: "adoptado: Orbit lo encontró ya pausado",
  requiere_recreacion: "archivado en Amazon: regresar no lo recrea",
  no_requiere_recreacion: "no está archivado: usa regresar",
  fallo: "no se pudo",
};

function postJson(url, token, cuerpo) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-orbit-token": token },
    body: JSON.stringify(cuerpo),
  }).then(function (resp) {
    return resp.json().then(function (data) {
      return { ok: resp.ok, data: data };
    });
  });
}

function zonaResultado(form) {
  var zona = form.querySelector("[data-resultado]");
  if (!zona) {
    zona = document.createElement("div");
    zona.dataset.resultado = "";
    form.appendChild(zona);
  }
  return zona;
}

// Una linea por anuncio que no quedo como se pidio (los que si, solo
// cuentan en el n de "n de m").
function pintaAnuncios(zona, anuncios, etiquetas, bien) {
  zona.replaceChildren();
  anuncios.forEach(function (a) {
    if (bien.indexOf(a.resultado) !== -1) return;
    var texto = a.ad_id_externo + " · " + (etiquetas[a.resultado] || a.resultado);
    zona.appendChild(parrafo(a.detalle ? texto + " (" + a.detalle + ")" : texto));
  });
}

// Segundo paso que pide su propio literal: regresar tambien los
// adoptados, o recrear los archivados. Usa el actor y el token del form.
function pasoConLiteral(form, zona, ids, literal, boton, enviar) {
  var esperada = literal + " " + ids.length + " ANUNCIOS";
  var caja = document.createElement("div");
  caja.appendChild(parrafo("Escribe " + esperada + " para " + boton.toLowerCase() + "."));
  var entrada = document.createElement("input");
  entrada.type = "text";
  entrada.placeholder = esperada;
  var accion = document.createElement("button");
  accion.type = "button";
  accion.textContent = boton;
  var estado = document.createElement("span");
  accion.addEventListener("click", function () {
    if (entrada.value !== esperada) {
      estado.textContent = "Escribe " + esperada + " para confirmar.";
      return;
    }
    estado.textContent = "Enviando…";
    enviar(entrada.value, estado);
  });
  caja.append(entrada, accion, estado);
  zona.appendChild(caja);
  return { entrada: entrada, accion: accion, estado: estado };
}

function regresar(form, ids, confirmacion, incluyeAdoptados, estado) {
  var cuerpo = {
    retiro_ids: ids,
    confirmacion: confirmacion,
    actor: form.elements.actor.value,
  };
  if (incluyeAdoptados) cuerpo.incluye_adoptados = true;
  return postJson("/api/fabrica/retiro/reponer", form.elements.token.value, cuerpo)
    .then(function (r) {
      if (!r.ok) {
        estado.textContent = "Error: " + mensajeError(r.data) + ".";
        return;
      }
      estado.textContent = "Regresados " + r.data.repuestos + " de " + r.data.total + ".";
      var zona = zonaResultado(form);
      pintaAnuncios(zona, r.data.anuncios || [], ETIQUETA_REGRESO, ["repuesto", "ya_repuesto"]);
      var adoptados = r.data.adoptados || [];
      var archivados = r.data.requieren_recreacion || [];
      if (adoptados.length) {
        pasoConLiteral(form, zona, adoptados, "REGRESAR", "Regresar también los adoptados",
          function (literal, estadoPaso) {
            regresar(form, adoptados, literal, true, estadoPaso);
          });
      }
      if (archivados.length) {
        pasoConLiteral(form, zona, archivados, "RECREAR", "Recrear los archivados",
          function (literal, estadoPaso) {
            recrear(form, archivados, literal, estadoPaso);
          });
      }
      if (!adoptados.length && !archivados.length) form.elements.token.value = "";
    })
    .catch(function () {
      estado.textContent = "Error de red al reponer.";
    });
}

function recrear(form, ids, confirmacion, estado) {
  return postJson("/api/fabrica/retiro/recrear", form.elements.token.value, {
    retiro_ids: ids,
    confirmacion: confirmacion,
    actor: form.elements.actor.value,
  })
    .then(function (r) {
      if (!r.ok) {
        estado.textContent = "Error: " + mensajeError(r.data) + ".";
        return;
      }
      var fallidos = (r.data.anuncios || []).filter(function (a) {
        return a.resultado !== "recreado";
      });
      var texto = "Recreados " + r.data.recreados + " de " + r.data.total + ".";
      fallidos.forEach(function (a) {
        texto += " " + a.ad_id_externo + ": " + (ETIQUETA_REGRESO[a.resultado] || a.resultado) + ".";
      });
      estado.textContent = texto;
      form.elements.token.value = "";
    })
    .catch(function () {
      estado.textContent = "Error de red al recrear.";
    });
}

function reponer(form) {
  var estado = form.querySelector("[data-estado]");
  var esperada = form.dataset.esperada;
  var confirmacion = form.elements.confirmacion.value;
  if (confirmacion !== esperada) {
    estado.textContent = "Escribe " + esperada + " para confirmar.";
    return;
  }
  estado.textContent = "Enviando…";
  var ids = form.dataset.retiros
    .split(",")
    .map(function (x) {
      return Number(x);
    })
    .filter(function (x) {
      return x > 0;
    });
  regresar(form, ids, confirmacion, false, estado);
}


function listingsMarcados(selector) {
  var listings = [];
  document.querySelectorAll(selector).forEach(function (casilla) {
    if (!casilla.checked) return;
    casilla.dataset.listings
      .split(",")
      .map(function (x) {
        return Number(x);
      })
      .filter(function (x) {
        return x > 0;
      })
      .forEach(function (lid) {
        listings.push(lid);
      });
  });
  return listings;
}

function verRetiro() {
  var zona = document.getElementById("productos-retiro-vista");
  var bloque = document.getElementById("productos-retiro");
  var form = document.getElementById("productos-retiro-ejecutar");
  var listings = listingsMarcados("[data-marcar-sin-pedido]");
  if (!listings.length) {
    zona.replaceChildren(parrafo("Marca al menos un producto."));
    return;
  }
  var pedido = {
    plataforma: form.dataset.plataforma,
    listing_ids: listings,
    motivo: "sin_pedidos",
  };
  zona.replaceChildren(parrafo("Pidiendo el plan de retiro…"));
  fetch("/api/fabrica/retiro/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pedido),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, data: data };
      });
    })
    .then(function (r) {
      if (!r.ok) {
        zona.replaceChildren(parrafo("Error: " + mensajeError(r.data) + "."));
        return;
      }
      retiroGuardado = { pedido: pedido, plan: r.data };
      zona.replaceChildren();
      if (!r.data.anuncios.length) {
        zona.appendChild(parrafo("Nada que sacar: sin anuncios en bolsas viejas."));
        form.hidden = true;
        bloque.hidden = false;
        return;
      }
      r.data.anuncios.forEach(function (a) {
        zona.appendChild(parrafo(a.sku + " · " + (a.ad_group_nombre || a.ad_group_externo)));
      });
      var esperada = "SACAR " + r.data.anuncios.length + " ANUNCIOS";
      document.getElementById("productos-retiro-esperada").textContent = esperada;
      form.elements.confirmacion.placeholder = esperada;
      form.hidden = false;
      bloque.hidden = false;
    })
    .catch(function () {
      zona.replaceChildren(parrafo("Error de red al pedir el plan de retiro."));
    });
}

function ejecutarRetiro(form) {
  var estado = form.querySelector("[data-estado]");
  if (!retiroGuardado) {
    estado.textContent = "Pide primero el plan de retiro.";
    return;
  }
  var esperada = "SACAR " + retiroGuardado.plan.anuncios.length + " ANUNCIOS";
  var confirmacion = form.elements.confirmacion.value;
  if (confirmacion !== esperada) {
    estado.textContent = "Escribe " + esperada + " para confirmar.";
    return;
  }
  estado.textContent = "Enviando…";
  fetch("/api/fabrica/retiro/ejecutar", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": form.elements.token.value,
    },
    body: JSON.stringify({
      plataforma: retiroGuardado.pedido.plataforma,
      listing_ids: retiroGuardado.pedido.listing_ids,
      motivo: "sin_pedidos",
      huella: retiroGuardado.plan.huella,
      confirmacion: confirmacion,
      actor: form.elements.actor.value,
    }),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, data: data };
      });
    })
    .then(function (r) {
      if (!r.ok) {
        estado.textContent = "Error: " + mensajeError(r.data) + ".";
        return;
      }
      retiroGuardado = null;
      form.elements.token.value = "";
      estado.textContent = "Retirados " + r.data.retirados + " de " + r.data.total + ".";
      pintaAnuncios(zonaResultado(form), r.data.anuncios || [], ETIQUETA_RETIRO, ["retirado"]);
    })
    .catch(function () {
      estado.textContent = "Error de red al ejecutar el retiro.";
    });
}

function filtraDecisiones(valor) {
  document.querySelectorAll("tr[data-decision-producto]").forEach(function (fila) {
    var motivos = fila.dataset.motivos ? fila.dataset.motivos.split(" ") : [];
    var visible = true;
    if (valor === "con-meta") visible = fila.dataset.conMeta === "si";
    else if (valor === "sin-meta") visible = fila.dataset.conMeta === "no";
    else if (valor.indexOf("motivo:") === 0) visible = motivos.indexOf(valor.slice(7)) !== -1;
    fila.hidden = !visible;
  });
}

document.addEventListener("DOMContentLoaded", function () {
  var filtro = document.getElementById("decisiones-filtro");
  if (filtro) {
    filtro.addEventListener("change", function () {
      filtraDecisiones(filtro.value);
    });
  }
  var verCandidatos = document.getElementById("productos-ver-candidatos");
  if (verCandidatos) {
    verCandidatos.addEventListener("click", function () {
      verLanzamiento("[data-marcar-candidato]");
    });
  }
  var aparte = document.getElementById("productos-impulsar-aparte");
  if (aparte) {
    aparte.addEventListener("click", function () {
      verLanzamiento("[data-marcar-sin-pedido]");
    });
  }
  var sacar = document.getElementById("productos-sacar");
  if (sacar) {
    sacar.addEventListener("click", verRetiro);
  }
  var formRetiro = document.getElementById("productos-retiro-ejecutar");
  if (formRetiro) {
    formRetiro.addEventListener("submit", function (evento) {
      evento.preventDefault();
      ejecutarRetiro(formRetiro);
    });
  }
  var form = document.getElementById("productos-lanzar");
  if (form) {
    form.addEventListener("submit", function (evento) {
      evento.preventDefault();
      lanzar(form);
    });
  }
  document.querySelectorAll("form[data-reponer]").forEach(function (f) {
    f.addEventListener("submit", function (evento) {
      evento.preventDefault();
      reponer(f);
    });
  });
});
