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
// "Limites de la pausa" (AUTO-03 H.9): al cambiar un limite pide GET
// /api/dashboard/productos/simula-pausa (lectura, no guarda) y pinta lo que
// pausaria, el gasto que se habria evitado y las ventas que se habrian
// perdido; un 422 muestra el minimo que dice el servidor. "Guardar limites"
// manda POST /api/ads-optimizer/settings/<plataforma> con x-orbit-token,
// como settings.js.

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

/// Segundo paso que pide su propio literal: regresar tambien los
// adoptados, o recrear los archivados. Usa el actor y el token del form;
// pinta su respuesta en su propia caja, sin borrar la de los demas.
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
  var respuesta = document.createElement("div");
  accion.addEventListener("click", function () {
    if (entrada.value !== esperada) {
      estado.textContent = "Escribe " + esperada + " para confirmar.";
      return;
    }
    estado.textContent = "Enviando…";
    enviar(entrada.value, estado, respuesta);
  });
  caja.append(entrada, accion, estado, respuesta);
  zona.appendChild(caja);
  form.pasosAbiertos = (form.pasosAbiertos || 0) + 1;
}

// El token se borra cuando ya no queda ningun paso por confirmar.
function cierraPaso(form) {
  form.pasosAbiertos = Math.max(0, (form.pasosAbiertos || 0) - 1);
  if (!form.pasosAbiertos) form.elements.token.value = "";
}

function regresar(form, ids, confirmacion, incluyeAdoptados, estado, zona) {
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
      pintaAnuncios(zona, r.data.anuncios || [], ETIQUETA_REGRESO, ["repuesto", "ya_repuesto"]);
      var adoptados = r.data.adoptados || [];
      var archivados = r.data.requieren_recreacion || [];
      if (adoptados.length) {
        pasoConLiteral(form, zona, adoptados, "REGRESAR", "Regresar también los adoptados",
          function (literal, estadoPaso, zonaPaso) {
            regresar(form, adoptados, literal, true, estadoPaso, zonaPaso);
          });
      }
      if (archivados.length) {
        pasoConLiteral(form, zona, archivados, "RECREAR", "Recrear los archivados",
          function (literal, estadoPaso) {
            recrear(form, archivados, literal, estadoPaso);
          });
      }
      cierraPaso(form);
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
        texto += " " + a.ad_id_externo + ": " + (ETIQUETA_REGRESO[a.resultado] || a.resultado);
        texto += a.detalle ? " (" + a.detalle + ")." : ".";
      });
      estado.textContent = texto;
      cierraPaso(form);
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
  // Un envio nuevo repinta la zona: los pasos de la respuesta anterior
  // desaparecen con ella.
  form.pasosAbiertos = 1;
  regresar(form, ids, confirmacion, false, estado, zonaResultado(form));
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

// Texto de un Decimal del servidor sin pasar por float: recorta ceros de
// sobra a dos decimales ("600.0000" -> "600.00") y deja intacto lo demas.
function decimalTexto(texto) {
  var s = String(texto);
  var m = s.match(/^(-?\d+)(?:\.(\d*))?$/);
  if (!m) return s;
  var dec = m[2] || "";
  if (!/^0*$/.test(dec.slice(2))) return s;
  return m[1] + "." + (dec + "00").slice(0, 2);
}

function dineroTexto(d) {
  return decimalTexto(d.valor) + " " + d.moneda;
}

function cuentaProductos(n) {
  return n + (n === 1 ? " producto" : " productos");
}

function nodo(tag, texto, clase) {
  var el = document.createElement(tag);
  if (texto !== undefined && texto !== null) el.textContent = texto;
  if (clase) el.className = clase;
  return el;
}

function nombreProducto(productId) {
  var fila = document.querySelector('tr[data-pausa-producto="' + productId + '"]');
  return fila && fila.dataset.nombre ? fila.dataset.nombre : "producto " + productId;
}

function tablaSimulacion(sim) {
  var envoltura = nodo("div", null, "tabla-scroll");
  envoltura.setAttribute("role", "region");
  envoltura.setAttribute("aria-label", "Productos que pausaría");
  envoltura.tabIndex = 0;
  var tabla = nodo("table");
  tabla.id = "limites-tabla";
  var cabeza = nodo("tr");
  ["Producto", "Gasto en la ventana", "Clics", "Anuncios"].forEach(function (t) {
    cabeza.appendChild(nodo("th", t));
  });
  tabla.appendChild(nodo("thead")).appendChild(cabeza);
  var cuerpo = tabla.appendChild(nodo("tbody"));
  sim.productos.forEach(function (x) {
    var fila = nodo("tr");
    fila.dataset.simulado = String(x.producto.product_id);
    fila.appendChild(nodo("td", nombreProducto(x.producto.product_id)));
    fila.appendChild(nodo("td", dineroTexto(x.gasto)));
    fila.appendChild(nodo("td", String(x.clics)));
    fila.appendChild(nodo("td", String(x.anuncios)));
    cuerpo.appendChild(fila);
  });
  var total = nodo("tr");
  total.appendChild(nodo("th", "Total"));
  total.appendChild(nodo("td", dineroTexto(sim.gasto_total)));
  total.appendChild(nodo("td", ""));
  total.appendChild(nodo("td", String(sim.anuncios_total)));
  tabla.appendChild(nodo("tfoot")).appendChild(total);
  envoltura.appendChild(tabla);
  return envoltura;
}

// Gasto evitado o ventas perdidas: Dinero, o SinDato con su porque. Nunca
// un cero en lugar de un dato que falta.
function cajaCosto(titulo, dato, marca, sim) {
  var caja = nodo("div", null, "limites-costo");
  caja.dataset[marca] = "";
  caja.appendChild(nodo("p", titulo, "kpi-etiqueta"));
  if (dato && dato.tipo === "SinDato") {
    caja.appendChild(nodo("p", "sin dato", "kpi-valor"));
    var porque = (dato.motivos || []).indexOf("sin_historia") !== -1
      ? sim.primer_dato
        ? "Sin historia: el reporte de productos empieza el " + sim.primer_dato +
          ", después del " + sim.ventana_anterior.desde + "."
        : "Sin historia: todavía no hay datos del reporte de productos."
      : "Sin dato: " + (dato.motivos || []).join(", ") + ".";
    caja.appendChild(nodo("p", porque, "kpi-contexto"));
  } else {
    caja.appendChild(nodo("p", dineroTexto(dato), "kpi-valor"));
  }
  return caja;
}

// "del 2026-09-08 al 2026-10-07" para la ventana [desde, hasta_exclusivo).
function ventanaTexto(ventana) {
  var fin = new Date(Date.parse(ventana.hasta_exclusivo + "T00:00:00Z") - 86400000);
  return "del " + ventana.desde + " al " + fin.toISOString().slice(0, 10);
}

function pintaSimulacion(zona, sim) {
  zona.replaceChildren();
  var lim = sim.limites;
  zona.appendChild(
    parrafo(
      "Con " + lim.clics_min + " clics y " + dineroTexto(lim.gasto_min) +
        " como mínimos, en la ventana " + ventanaTexto(sim.ventana) + " se pausaría:"
    )
  );
  if (sim.productos.length) {
    zona.appendChild(tablaSimulacion(sim));
  } else {
    var vacio = parrafo("Ningún producto con datos completos llegaría a la pausa con estos límites.");
    vacio.dataset.simulacionVacia = "";
    zona.appendChild(vacio);
  }
  if (sim.sin_cobertura_d7 > 0) {
    var cobertura = nodo(
      "p",
      cuentaProductos(sim.sin_cobertura_d7) + (sim.sin_cobertura_d7 === 1 ? " no se puede" : " no se pueden") +
        " juzgar: alguna fecha de la ventana no " +
        "tiene fila ni cobertura demostrada del reporte (D.7). No es cero: falta el dato.",
      "aviso"
    );
    cobertura.dataset.sinCobertura = String(sim.sin_cobertura_d7);
    zona.appendChild(cobertura);
  }
  var costos = nodo("div", null, "limites-costos");
  costos.appendChild(
    cajaCosto("Gasto que se habría evitado", sim.gasto_evitado, "gastoEvitado", sim)
  );
  costos.appendChild(
    cajaCosto("Ventas que se habrían perdido", sim.ventas_perdidas, "ventasPerdidas", sim)
  );
  zona.appendChild(costos);
  var anterior =
    "Con estos límites, la ventana " + ventanaTexto(sim.ventana_anterior) + " habría pausado " +
    cuentaProductos(sim.productos_anteriores.length) + "; arriba, lo que esos productos gastaron y " +
    "vendieron después, " + ventanaTexto(sim.ventana) + ".";
  if (sim.sin_cobertura_d7_anterior > 0) {
    anterior += " " + cuentaProductos(sim.sin_cobertura_d7_anterior) + " de aquella ventana sin cobertura.";
  }
  if (sim.sin_dato_despues > 0) {
    anterior += " " + sim.sin_dato_despues + " de los pausados ya no tienen dato después.";
  }
  zona.appendChild(nodo("p", anterior, "mutado"));
  var frase = nodo(
    "p",
    "El gasto pasado no es ahorro seguro: es lo que esos productos gastaron, no lo que dejarán de gastar."
  );
  frase.dataset.fraseAhorro = "";
  zona.appendChild(frase);
  var desde = nodo(
    "p",
    sim.primer_dato
      ? "Hay datos del reporte de productos desde el " + sim.primer_dato + "."
      : "Todavía no hay datos del reporte de productos.",
    "mutado"
  );
  desde.dataset.primerDato = sim.primer_dato || "";
  zona.appendChild(desde);
  zona.appendChild(nodo("p", "Sin guardar: los límites vigentes no cambiaron.", "mutado"));
}

var simulacionEnCurso = 0;

function simularLimites(form) {
  var zona = document.getElementById("limites-simulacion");
  var estado = document.getElementById("limites-sim-estado");
  var clics = form.elements.clics.value.trim();
  var gasto = form.elements.gasto.value.trim();
  var url =
    "/api/dashboard/productos/simula-pausa?platform=" + encodeURIComponent(form.dataset.plataforma) +
    "&clics=" + encodeURIComponent(clics) + "&gasto=" + encodeURIComponent(gasto);
  var turno = ++simulacionEnCurso;
  estado.textContent = "Simulando sin guardar…";
  fetch(url)
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, status: resp.status, data: data };
      });
    })
    .then(function (r) {
      // Solo pinta la respuesta del ultimo cambio.
      if (turno !== simulacionEnCurso) return;
      if (!r.ok) {
        var error = nodo(
          "p",
          (r.status === 422 ? "No se puede simular: " : "Error: ") + mensajeError(r.data) + ".",
          "alerta"
        );
        error.dataset.limitesRechazo = String(r.status);
        zona.replaceChildren(error);
        estado.textContent =
          r.status === 422 ? "La simulación no corrió: el límite está bajo el mínimo." : "La simulación falló.";
        return;
      }
      pintaSimulacion(zona, r.data);
      estado.textContent = "Simulación lista: pausaría " + cuentaProductos(r.data.productos.length) + ".";
    })
    .catch(function () {
      if (turno !== simulacionEnCurso) return;
      estado.textContent = "Error de red al simular.";
    });
}

function guardarLimites(form, campos) {
  var estado = form.querySelector("[data-estado]");
  var clics = campos.elements.clics;
  var gasto = campos.elements.gasto;
  if (!/^\d+$/.test(clics.value.trim())) {
    estado.textContent = "Clics mínimos: escribe un número entero.";
    return;
  }
  if (
    clics.value.trim() === clics.defaultValue.trim() &&
    gasto.value.trim() === gasto.defaultValue.trim()
  ) {
    estado.textContent = "Nada que cambiar.";
    return;
  }
  var cuerpo = {
    base_config_version_id: Number(form.dataset.configId),
    limites_pausa: {
      clics: Number(clics.value.trim()),
      gasto: { valor: gasto.value.trim(), moneda: campos.dataset.moneda },
    },
  };
  estado.textContent = "Enviando…";
  fetch("/api/ads-optimizer/settings/" + form.dataset.plataforma, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-orbit-token": form.elements.token.value },
    body: JSON.stringify(cuerpo),
  })
    .then(function (resp) {
      return resp.json().then(function (data) {
        return { ok: resp.ok, status: resp.status, data: data };
      });
    })
    .then(function (r) {
      if (r.ok) {
        form.elements.token.value = "";
        estado.textContent = "Guardado: " + (r.data.label || r.data.config_version_id) + ".";
        window.location.reload();
        return;
      }
      if (r.status === 409) {
        estado.textContent =
          "No se guardó: la config cambió mientras editabas (" + mensajeError(r.data) +
          "). Recarga la página.";
        return;
      }
      estado.textContent =
        (r.status === 422 ? "No se guardó: " : "Error: ") + mensajeError(r.data) + ".";
    })
    .catch(function () {
      estado.textContent = "Error de red al guardar los límites.";
    });
}

document.addEventListener("DOMContentLoaded", function () {
  var campos = document.getElementById("limites-simular");
  if (campos) {
    campos.addEventListener("submit", function (evento) {
      evento.preventDefault();
      simularLimites(campos);
    });
    [campos.elements.clics, campos.elements.gasto].forEach(function (campo) {
      campo.addEventListener("change", function () {
        simularLimites(campos);
      });
    });
    var guardar = document.getElementById("limites-guardar");
    if (guardar) {
      guardar.addEventListener("submit", function (evento) {
        evento.preventDefault();
        guardarLimites(guardar, campos);
      });
    }
  }
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
