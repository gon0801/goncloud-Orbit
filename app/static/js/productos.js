"use strict";
// JS de productos sin clics (BIDS 02, P.4). Vive en /static por la CSP
// `default-src 'self'`: los <script> inline y los handlers on*= quedan
// BLOQUEADOS por esa politica. "Ver lanzamiento" pide POST
// /api/fabrica/impulso/plan con los listings marcados y pinta la vista
// previa; "Lanzar impulso" exige el literal LANZAR IMPULSO DE <N>
// PRODUCTOS antes de mandar POST /api/fabrica/impulso/lanzar.
// "Regresarlo a sus grupos de antes" exige REGRESAR <N> ANUNCIOS y manda
// POST /api/fabrica/retiro/reponer. El token viaja en el header
// x-orbit-token (la query string JAMAS autentica).

var vistaGuardada = null;

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
        zona.replaceChildren(parrafo("Error: " + (r.data.detail || r.data) + "."));
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
        if (r.status === 409 && r.data.vista_previa) {
          vistaGuardada = { solicitud: vistaGuardada.solicitud, vista: r.data.vista_previa };
          pintaVista(document.getElementById("productos-vista"), r.data.vista_previa);
          document.getElementById("productos-esperada").textContent =
            r.data.vista_previa.confirmacion_esperada;
          estado.textContent = "El plan cambió: revisa la vista nueva y confirma de nuevo.";
          return;
        }
        estado.textContent = "Error: " + (r.data.detail || r.data) + ".";
        return;
      }
      vistaGuardada = null;
      estado.textContent = "Lanzados " + (r.data.lanzados || []).length + ".";
    })
    .catch(function () {
      estado.textContent = "Error de red al lanzar.";
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
  fetch("/api/fabrica/retiro/reponer", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": form.elements.token.value,
    },
    body: JSON.stringify({
      retiro_ids: ids,
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
        estado.textContent = "Error: " + (r.data.detail || r.data) + ".";
        return;
      }
      estado.textContent = "Repuestos " + r.data.repuestos + ".";
    })
    .catch(function () {
      estado.textContent = "Error de red al reponer.";
    });
}

document.addEventListener("DOMContentLoaded", function () {
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
