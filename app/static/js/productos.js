"use strict";
// JS de productos sin clics (BIDS 02, P.4). Vive en /static por la CSP
// `default-src 'self'`: los <script> inline y los handlers on*= quedan
// BLOQUEADOS por esa politica. "Ver lanzamiento" pide POST
// /api/fabrica/impulso/plan con los listings marcados y pinta la vista
// previa (avisa la subfamilia nueva, que arranca sin negativos); "Lanzar
// impulso" exige el literal LANZAR IMPULSO DE <N> PRODUCTOS antes de
// mandar POST /api/fabrica/impulso/lanzar.
// "Regresarlo a sus grupos de antes" exige REGRESAR <N> ANUNCIOS y manda
// POST /api/fabrica/retiro/reponer. Sacar y regresar dicen "n de m" y
// nombran cada anuncio que no quedo (AUTO-03 G.4); los adoptados y los
// archivados piden su propio literal: REGRESAR <K> ANUNCIOS con
// incluye_adoptados, o RECREAR <K> ANUNCIOS a /api/fabrica/retiro/recrear.
// El token viaja en el header x-orbit-token (la query string JAMAS
// autentica).
// "Sacar", "Ver lanzamiento" e "Impulsar aparte" (ARREGLOS 04 W-P): cada flujo
// lleva su revision; cambiar la seleccion o fallar la previa nueva esconde el
// formulario, y solo se manda la solicitud de la previa que se esta viendo.
// "Meta por producto" (AUTO-03 E.1) solo filtra filas ya pintadas por el
// servidor: con meta, sin meta o por motivo.
// "Limites de la pausa" (AUTO-03 H.9): al cambiar un limite pide GET
// /api/dashboard/productos/simula-pausa (lectura, no guarda) y pinta lo que
// pausaria, el gasto que se habria evitado y las ventas que se habrian
// perdido; un 422 muestra el minimo que dice el servidor. "Guardar limites"
// manda POST /api/ads-optimizer/settings/<plataforma> con x-orbit-token,
// como settings.js.
// "Encender la pausa" y "Volver a practica" (AUTO-03 I.8): ver cableaPausa.

// --- Previa visible = solicitud ejecutable (ARREGLOS 04 W-P, TN04) ---------
// Un estado por flujo: revision, solicitud, previa y fase. La revision sube
// con cada previa pedida y con cada cambio de seleccion, y una solicitud con
// su previa solo vale para su revision. Lo que se pinta y lo que se manda
// salen de aqui, asi que una previa que dejo de verse no se puede ejecutar.
// La API valida la huella de los IDs que recibe, pero no sabe que hay marcado
// en la pantalla: esta revision es la que cierra la carrera.

function flujoNuevo() {
  return {
    revision: 0,
    fase: "vacia",
    solicitud: null,
    grupos: null,
    selector: null,
    previa: null,
  };
}

var flujoImpulso = flujoNuevo();
var flujoRetiro = flujoNuevo();

function contextoImpulso() {
  return {
    zona: document.getElementById("productos-vista"),
    bloque: document.getElementById("productos-lanzamiento"),
    form: document.getElementById("productos-lanzar"),
    esperada: document.getElementById("productos-esperada"),
    pidiendo: "Pidiendo la vista previa…",
    errorRed: "Error de red al pedir la vista previa.",
    pidePrimero: "Pide primero la vista previa.",
    cambio: "La selección cambió: pide la vista previa otra vez.",
    conPresupuesto: true,
  };
}

function contextoRetiro() {
  return {
    zona: document.getElementById("productos-retiro-vista"),
    bloque: document.getElementById("productos-retiro"),
    form: document.getElementById("productos-retiro-ejecutar"),
    esperada: document.getElementById("productos-retiro-esperada"),
    pidiendo: "Pidiendo el plan de retiro…",
    errorRed: "Error de red al pedir el plan de retiro.",
    pidePrimero: "Pide primero el plan de retiro.",
    cambio: "La selección cambió: pide el plan de retiro otra vez.",
    conPresupuesto: false,
  };
}

function botonEnvio(form) {
  return form ? form.querySelector('[type="submit"]') : null;
}

// Apagar el boton es la segunda barrera del doble clic; la primera es la fase.
function habilitaEnvio(ctx, habilitado) {
  var boton = botonEnvio(ctx.form);
  if (boton) boton.disabled = !habilitado;
}

// Deja el flujo sin previa ejecutable: plan, huella, frase de confirmacion y
// casilla de presupuesto dejan de valer.
function invalidaPrevia(flujo, ctx) {
  flujo.revision += 1;
  flujo.fase = "vacia";
  flujo.solicitud = null;
  flujo.grupos = null;
  flujo.selector = null;
  flujo.previa = null;
  if (!ctx.form) return;
  ctx.form.hidden = true;
  habilitaEnvio(ctx, false);
  if (ctx.esperada) ctx.esperada.textContent = "";
  if (ctx.form.elements.confirmacion) {
    ctx.form.elements.confirmacion.value = "";
    ctx.form.elements.confirmacion.placeholder = "";
  }
  if (ctx.conPresupuesto) {
    var casilla = document.getElementById("productos-acepto-presupuesto-alto");
    if (casilla) casilla.checked = false;
    var caja = document.getElementById("productos-casilla-presupuesto");
    if (caja) caja.hidden = true;
  }
}

// La previa no se pudo ver: se explica y no queda nada que ejecutar.
function fallaPrevia(flujo, ctx, texto) {
  invalidaPrevia(flujo, ctx);
  if (ctx.bloque) ctx.bloque.hidden = false;
  ctx.zona.replaceChildren(parrafo(texto));
}

function vigente(flujo, revision, solicitud) {
  return flujo.revision === revision && flujo.solicitud === solicitud;
}

// La seleccion que el flujo pidio sigue siendo la que hay marcada. Cuenta la
// subfamilia, no solo los IDs: cambiar una tambien deja vieja la previa.
function mismoMapa(pedido, actual) {
  var claves = Object.keys(pedido);
  if (claves.length !== Object.keys(actual).length) return false;
  return claves.every(function (clave) {
    return pedido[clave] === actual[clave];
  });
}

function mismaLista(pedido, actual) {
  if (pedido.length !== actual.length) return false;
  for (var i = 0; i < pedido.length; i += 1) {
    if (pedido[i] !== actual[i]) return false;
  }
  return true;
}

function mismaSeleccion(flujo) {
  var pedida = flujo.solicitud;
  if (!pedida) return false;
  if (flujo.selector) {
    var marca = marcados(flujo.selector);
    // Un marcado que se quedo sin subfamilia ya no es lo que se pidio.
    if (marca.faltan.length) return false;
    if (!mismoMapa(marca.subfamilias, pedida.subfamilia_por_listing || {})) return false;
    return mismaLista(marca.listings, pedida.listing_ids);
  }
  var actuales = [].concat.apply([], listingsPorCasilla("[data-marcar-sin-pedido]"));
  return mismaLista(actuales, pedida.listing_ids);
}

// Con una ejecucion en curso, o con su resultado sin conocer, ninguna entrada
// puede rearmar Confirmar: seria un segundo POST sin saber el primero.
function envioPendiente(flujo, ctx) {
  if (flujo.fase !== "enviando" && flujo.fase !== "incierta") return false;
  if (ctx.form) {
    var estado = ctx.form.querySelector("[data-estado]");
    if (estado) {
      estado.textContent =
        flujo.fase === "enviando"
          ? "Enviando: espera la respuesta antes de pedir otra vista previa."
          : "No se sabe si la ejecución se aplicó: recarga la página y revisa el resultado antes de reintentar.";
    }
  }
  return true;
}

// Solo una respuesta de la revision vigente pasa a lista; la tardia se tira
// con su error. Abortar ayuda, pero no impide toda respuesta fuera de orden.
function pidePrevia(flujo, ctx, url, solicitud, grupos, selector, pintaLista) {
  if (envioPendiente(flujo, ctx)) return;
  invalidaPrevia(flujo, ctx);
  var revision = flujo.revision;
  flujo.fase = "cargando";
  flujo.solicitud = solicitud;
  flujo.grupos = grupos;
  flujo.selector = selector;
  if (ctx.bloque) ctx.bloque.hidden = false;
  ctx.zona.replaceChildren(parrafo(ctx.pidiendo));
  fetch(url, {
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
      if (!vigente(flujo, revision, solicitud)) return;
      if (!r.ok) {
        fallaPrevia(flujo, ctx, "Error: " + mensajeError(r.data) + ".");
        return;
      }
      flujo.previa = r.data;
      flujo.fase = "lista";
      pintaLista(flujo, ctx);
    })
    .catch(function () {
      if (!vigente(flujo, revision, solicitud)) return;
      fallaPrevia(flujo, ctx, ctx.errorRed);
    });
}

// Cambiar la seleccion mientras algo viaja o se ve deja vieja la previa.
// Con un envio en curso o incierto no se toca el estado: la solicitud ya
// salio y el resultado manda hasta que se recargue la pagina.
function cambioDeSeleccion(flujo, ctx) {
  if (flujo.fase === "enviando" || flujo.fase === "incierta") return;
  if (flujo.fase === "vacia" && !flujo.previa) return;
  invalidaPrevia(flujo, ctx);
  if (ctx.bloque) ctx.bloque.hidden = false;
  ctx.zona.replaceChildren(parrafo(ctx.cambio));
}

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

// Sin el punto final: quien llama cierra la frase con el suyo.
function mensajeError(data) {
  var detalle = data && data.detail;
  if (typeof detalle === "string") return detalle.replace(/\.$/, "");
  if (detalle && detalle.mensaje) {
    var texto = detalle.mensaje.replace(/\.$/, "");
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
  var conAviso = false;
  vista.planes.forEach(function (plan) {
    var div = document.createElement("div");
    div.appendChild(parrafo(plan.seller_sku + " · " + plan.subfamilia));
    if (plan.presupuesto_alto) {
      // W-N: aviso, no rechazo; cada previa nueva deja la casilla sin marcar.
      conAviso = true;
      var avisoP = parrafo(plan.presupuesto_alto);
      avisoP.className = "aviso";
      avisoP.setAttribute("data-aviso-presupuesto", "");
      div.appendChild(avisoP);
    }
    if (plan.subfamilia_nueva) {
      var aviso = parrafo(
        "Subfamilia nueva: arranca sin negativos; Orbit los aprende con este impulso."
      );
      aviso.className = "aviso";
      aviso.setAttribute("data-subfamilia-nueva", "");
      div.appendChild(aviso);
    } else if (plan.sin_negativos) {
      var avisoNeg = parrafo(
        "Esta subfamilia todavía no tiene negativos: arranca sin negativos; " +
          "Orbit los aprende con este impulso."
      );
      avisoNeg.className = "aviso";
      avisoNeg.setAttribute("data-sin-negativos", "");
      div.appendChild(avisoNeg);
    }
    div.appendChild(
      parrafo(
        "Tope " +
          Orbit.dinero(plan.tope.monto) +
          " " +
          plan.tope.moneda +
          " · " +
          Orbit.dinero(plan.tope.presupuesto_diario) +
          " al día · " +
          Orbit.numero(plan.keywords_exact.length) +
          " keywords · " +
          Orbit.numero(plan.negatives.length) +
          " negativos"
      )
    );
    if (plan.bids_acotados && plan.bids_acotados.length) {
      div.appendChild(parrafo("Pujas acotadas al tope: " + plan.bids_acotados.map(function (rol) {
        return Orbit.etiqueta(rol, "rol_campana");
      }).join(", ")));
    }
    var sinSugerencia = plan.bid_por_rol
      ? Object.keys(plan.bid_por_rol).filter(function (rol) {
          return plan.bid_por_rol[rol] === null || plan.bid_por_rol[rol] === undefined;
        })
      : [];
    if (sinSugerencia.length) {
      div.appendChild(
        parrafo(
          "Sin sugerencia de Amazon (usan el presupuesto diario de su campaña): " +
            sinSugerencia.map(function (rol) { return Orbit.etiqueta(rol, "rol_campana"); }).join(", ")
        )
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
  var casilla = document.getElementById("productos-acepto-presupuesto-alto");
  if (casilla) casilla.checked = false;
  var caja = document.getElementById("productos-casilla-presupuesto");
  if (caja) caja.hidden = !conAviso;
}

function pintaListaImpulso(flujo, ctx) {
  pintaVista(ctx.zona, flujo.previa);
  ctx.esperada.textContent = flujo.previa.confirmacion_esperada;
  ctx.form.elements.confirmacion.placeholder = flujo.previa.confirmacion_esperada;
  ctx.form.hidden = false;
  ctx.bloque.hidden = false;
  habilitaEnvio(ctx, true);
}

function verLanzamiento(selectorCasillas) {
  var ctx = contextoImpulso();
  if (envioPendiente(flujoImpulso, ctx)) return;
  var marca = marcados(selectorCasillas);
  if (!marca.listings.length) {
    fallaPrevia(flujoImpulso, ctx, "Marca al menos un producto.");
    return;
  }
  if (marca.faltan.length) {
    fallaPrevia(flujoImpulso, ctx, "Falta la subfamilia de " + marca.faltan.length + " marcados.");
    return;
  }
  pidePrevia(
    flujoImpulso,
    ctx,
    "/api/fabrica/impulso/plan",
    {
      plataforma: ctx.form.dataset.plataforma,
      listing_ids: marca.listings,
      subfamilia_por_listing: marca.subfamilias,
    },
    null,
    selectorCasillas,
    pintaListaImpulso
  );
}

function lanzar(form) {
  var ctx = contextoImpulso();
  var flujo = flujoImpulso;
  var estado = form.querySelector("[data-estado]");
  if (flujo.fase === "enviando") {
    estado.textContent = "Enviando: espera la respuesta antes de reintentar.";
    return;
  }
  if (flujo.fase === "incierta") {
    estado.textContent =
      "No se sabe si el impulso se aplicó: recarga la página y revisa Impulsos antes de reintentar.";
    return;
  }
  if (flujo.fase !== "lista" || !flujo.previa || !flujo.solicitud) {
    estado.textContent = ctx.pidePrimero;
    return;
  }
  var esperada = flujo.previa.confirmacion_esperada;
  var confirmacion = form.elements.confirmacion.value;
  if (confirmacion !== esperada) {
    estado.textContent = "Escribe " + esperada + " para confirmar.";
    return;
  }
  var casilla = document.getElementById("productos-acepto-presupuesto-alto");
  var hayAviso = (flujo.previa.planes || []).some(function (p) {
    return !!p.presupuesto_alto;
  });
  if (hayAviso && !(casilla && casilla.checked)) {
    estado.textContent = "Marca «Sí, quiero ese presupuesto» para continuar";
    return;
  }
  // Se congela lo que se manda: la revision, la solicitud y su previa.
  var envio = { revision: flujo.revision, solicitud: flujo.solicitud, previa: flujo.previa };
  var cuerpo = {
    solicitud: envio.solicitud,
    huella: envio.previa.huella,
    confirmacion: confirmacion,
    actor: form.elements.actor.value,
  };
  if (casilla && casilla.checked) cuerpo.acepto_presupuesto_alto = true;
  flujo.fase = "enviando";
  habilitaEnvio(ctx, false);
  estado.textContent = "Enviando…";
  fetch("/api/fabrica/impulso/lanzar", {
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
      if (flujo.revision !== envio.revision) return;
      var texto;
      if (!r.ok) {
        var detalle = r.data && r.data.detail ? r.data.detail : {};
        if (r.status === 409 && detalle.vista_previa) {
          // La unica previa ejecutable es la que acaba de mandar el servidor,
          // y solo si la seleccion marcada sigue siendo la que la pidio.
          if (!mismaSeleccion(flujo)) {
            texto = "El plan cambió y la selección ya no es la de esa vista: pide la nueva antes de confirmar.";
            fallaPrevia(flujo, ctx, texto);
            estado.textContent = texto;
            return;
          }
          flujo.revision += 1;
          flujo.solicitud = envio.solicitud;
          flujo.previa = detalle.vista_previa;
          flujo.fase = "lista";
          pintaListaImpulso(flujo, ctx);
          // El literal escrito era de la revision anterior: se pide otra vez.
          form.elements.confirmacion.value = "";
          estado.textContent = "El plan cambió: revisa la vista nueva y confirma de nuevo.";
          return;
        }
        texto = "Error: " + mensajeError(r.data) + ".";
        fallaPrevia(flujo, ctx, texto);
        estado.textContent = texto;
        return;
      }
      flujo.revision += 1;
      flujo.fase = "resultado";
      flujo.solicitud = null;
      flujo.previa = null;
      form.elements.token.value = "";
      habilitaEnvio(ctx, false);
      estado.textContent = "Lanzados " + (r.data.lanzados || []).length + ".";
    })
    .catch(function () {
      if (flujo.revision !== envio.revision) return;
      // Timeout o desconexion: el servidor pudo haber actuado.
      flujo.fase = "incierta";
      habilitaEnvio(ctx, false);
      estado.textContent =
        "No se sabe si el impulso se aplicó: recarga la página y revisa Impulsos antes de reintentar.";
    });
}

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
function pintaAnuncios(zona, anuncios, dominio, bien) {
  zona.replaceChildren();
  anuncios.forEach(function (a) {
    if (bien.indexOf(a.resultado) !== -1) return;
    var texto = a.ad_id_externo + " · " + Orbit.etiqueta(a.resultado, dominio);
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
      pintaAnuncios(zona, r.data.anuncios || [], "resultado_regreso", ["repuesto", "ya_repuesto"]);
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
        texto += " " + a.ad_id_externo + ": " + Orbit.etiqueta(a.resultado, "resultado_regreso");
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


function listingsPorCasilla(selector) {
  var porCasilla = [];
  document.querySelectorAll(selector).forEach(function (casilla) {
    if (!casilla.checked) return;
    porCasilla.push(
      casilla.dataset.listings
        .split(",")
        .map(function (x) {
          return Number(x);
        })
        .filter(function (x) {
          return x > 0;
        })
    );
  });
  return porCasilla;
}

function avisoVendieron(porCasilla, vendieron) {
  var excluidas = new Set(vendieron);
  var productos = porCasilla.filter(function (listings) {
    return listings.some(function (lid) {
      return excluidas.has(lid);
    });
  }).length;
  var publicaciones = excluidas.size;
  return (
    (productos === 1
      ? "1 de los productos marcados vendió"
      : productos + " de los productos marcados vendieron") +
    " en los últimos 11 meses: se conservan los anuncios de " +
    (publicaciones === 1 ? "1 publicación." : publicaciones + " publicaciones.")
  );
}

function pintaListaRetiro(flujo, ctx) {
  var plan = flujo.previa;
  ctx.zona.replaceChildren();
  if (plan.ventas_al_dia === false) {
    invalidaPrevia(flujo, ctx);
    var motivo = plan.ventas_al_dia_hasta
      ? "no se actualizan desde el " + Orbit.fechaHora(plan.ventas_al_dia_hasta)
      : "todavía no se han cargado";
    var alerta = parrafo(
      "No se sacó ningún anuncio: las ventas de la contabilidad " +
        motivo +
        ", así que Orbit no puede comprobar que estos productos no vendieron. " +
        "Vuelve a intentarlo cuando se actualicen."
    );
    alerta.className = "aviso";
    ctx.zona.appendChild(alerta);
    ctx.bloque.hidden = false;
    return;
  }
  if (plan.vendieron && plan.vendieron.length) {
    var excluidos = parrafo(avisoVendieron(flujo.grupos, plan.vendieron));
    excluidos.className = "aviso";
    ctx.zona.appendChild(excluidos);
  }
  if (!plan.anuncios.length) {
    invalidaPrevia(flujo, ctx);
    ctx.zona.appendChild(parrafo("Nada que sacar: sin anuncios en bolsas viejas."));
    ctx.bloque.hidden = false;
    return;
  }
  plan.anuncios.forEach(function (a) {
    ctx.zona.appendChild(parrafo(a.sku + " · " + (a.ad_group_nombre || a.ad_group_externo)));
  });
  var esperada = "SACAR " + plan.anuncios.length + " ANUNCIOS";
  ctx.esperada.textContent = esperada;
  ctx.form.elements.confirmacion.placeholder = esperada;
  ctx.form.hidden = false;
  ctx.bloque.hidden = false;
  habilitaEnvio(ctx, true);
}

function verRetiro() {
  var ctx = contextoRetiro();
  if (envioPendiente(flujoRetiro, ctx)) return;
  var porCasilla = listingsPorCasilla("[data-marcar-sin-pedido]");
  var listings = [].concat.apply([], porCasilla);
  if (!listings.length) {
    fallaPrevia(flujoRetiro, ctx, "Marca al menos un producto.");
    return;
  }
  pidePrevia(
    flujoRetiro,
    ctx,
    "/api/fabrica/retiro/plan",
    {
      plataforma: ctx.form.dataset.plataforma,
      listing_ids: listings,
      motivo: "sin_pedidos",
    },
    porCasilla,
    null,
    pintaListaRetiro
  );
}

function ejecutarRetiro(form) {
  var ctx = contextoRetiro();
  var flujo = flujoRetiro;
  var estado = form.querySelector("[data-estado]");
  if (flujo.fase === "enviando") {
    estado.textContent = "Enviando: espera la respuesta antes de reintentar.";
    return;
  }
  if (flujo.fase === "incierta") {
    estado.textContent =
      "No se sabe si el retiro se aplicó: recarga la página y revisa los retiros antes de reintentar.";
    return;
  }
  if (flujo.fase !== "lista" || !flujo.previa || !flujo.solicitud) {
    estado.textContent = ctx.pidePrimero;
    return;
  }
  var esperada = "SACAR " + flujo.previa.anuncios.length + " ANUNCIOS";
  var confirmacion = form.elements.confirmacion.value;
  if (confirmacion !== esperada) {
    estado.textContent = "Escribe " + esperada + " para confirmar.";
    return;
  }
  var envio = { revision: flujo.revision, solicitud: flujo.solicitud, previa: flujo.previa };
  flujo.fase = "enviando";
  habilitaEnvio(ctx, false);
  estado.textContent = "Enviando…";
  fetch("/api/fabrica/retiro/ejecutar", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-orbit-token": form.elements.token.value,
    },
    body: JSON.stringify({
      plataforma: envio.solicitud.plataforma,
      listing_ids: envio.solicitud.listing_ids,
      motivo: "sin_pedidos",
      huella: envio.previa.huella,
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
      if (flujo.revision !== envio.revision) return;
      if (!r.ok) {
        var texto = "Error: " + mensajeError(r.data) + ".";
        fallaPrevia(flujo, ctx, texto);
        estado.textContent = texto;
        return;
      }
      flujo.revision += 1;
      flujo.fase = "resultado";
      flujo.solicitud = null;
      flujo.previa = null;
      form.elements.token.value = "";
      habilitaEnvio(ctx, false);
      estado.textContent = "Retirados " + r.data.retirados + " de " + r.data.total + ".";
      pintaAnuncios(zonaResultado(form), r.data.anuncios || [], "resultado_retiro", ["retirado"]);
    })
    .catch(function () {
      if (flujo.revision !== envio.revision) return;
      flujo.fase = "incierta";
      habilitaEnvio(ctx, false);
      estado.textContent =
        "No se sabe si el retiro se aplicó: recarga la página y revisa los retiros antes de reintentar.";
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

function dineroTexto(d) {
  return Orbit.dinero(d.valor) + " " + d.moneda;
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
    fila.appendChild(nodo("td", Orbit.numero(x.clics)));
    fila.appendChild(nodo("td", Orbit.numero(x.anuncios)));
    cuerpo.appendChild(fila);
  });
  var total = nodo("tr");
  total.appendChild(nodo("th", "Total"));
  total.appendChild(nodo("td", dineroTexto(sim.gasto_total)));
  total.appendChild(nodo("td", ""));
  total.appendChild(nodo("td", Orbit.numero(sim.anuncios_total)));
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
      : "Sin dato: " + (dato.motivos || []).map(function (m) {
        return Orbit.etiqueta(m, "motivo_producto");
      }).join(", ") + ".";
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
      "Con " + Orbit.numero(lim.clics_min) + " clics y " + dineroTexto(lim.gasto_min) +
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
        "tiene fila ni cobertura demostrada del reporte. No es cero: falta el dato.",
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
    anterior +=
      " " + sim.sin_dato_despues + " de los pausados no tienen dato completo después: no suman.";
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
      return resp.json().then(
        function (data) {
          return { ok: resp.ok, status: resp.status, data: data };
        },
        function () {
          return { ok: false, status: resp.status, data: {} };
        }
      );
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
          r.status === 422 ? "La simulación no corrió: " + mensajeError(r.data) + "." : "La simulación falló.";
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
  // Sin las dos claves guardadas los valores son los de omision: guardarlos
  // tal cual SI es un cambio.
  if (
    form.dataset.guardados === "true" &&
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

// --- Encender la pausa o volver a practica (AUTO-03 I.8) -------------------
// "Encender la pausa" pide primero la simulacion de H.8 con los limites
// guardados (GET simula-pausa sin clics ni gasto) y muestra cuantos anuncios
// y productos pausaria y su gasto; solo "Confirmar" manda POST
// /api/ads-optimizer/productos/pausa/<plataforma> con x-orbit-token y
// {modo, base_config_version_id}. "Cancelar" no escribe. "Volver a practica"
// pide la misma confirmacion. 422 muestra el motivo del servidor y 409 pide
// recargar; al encender o volver, la pagina recarga.

var pasoPausa = null;

function cuentaAnuncios(n) {
  return n + (n === 1 ? " anuncio" : " anuncios");
}

function cajaPausa() {
  var caja = document.getElementById("pausa-vivo");
  return {
    caja: caja,
    confirmar: document.getElementById("pausa-confirmar"),
    titulo: document.querySelector("#pausa-confirmar-titulo strong"),
    detalle: document.getElementById("pausa-confirmar-detalle"),
    form: document.getElementById("pausa-confirmar-form"),
    estado: document.getElementById("pausa-vivo-estado"),
  };
}

function resumenEncender(sim) {
  var lim = sim.limites;
  var partes = [
    parrafo(
      "Con tus límites guardados (" + Orbit.numero(lim.clics_min) + " clics y " + dineroTexto(lim.gasto_min) +
        "), Orbit pausaría " + cuentaAnuncios(sim.anuncios_total) + " de " +
        cuentaProductos(sim.productos.length) + ", que gastaron " + dineroTexto(sim.gasto_total) +
        " en la ventana " + ventanaTexto(sim.ventana) + "."
    ),
  ];
  partes[0].dataset.resumenEncender = "";
  if (sim.sin_cobertura_d7 > 0) {
    var cobertura = nodo(
      "p",
      cuentaProductos(sim.sin_cobertura_d7) + (sim.sin_cobertura_d7 === 1 ? " no se puede" : " no se pueden") +
        " juzgar todavía: falta cobertura del reporte. No se pausan hasta tener el dato.",
      "aviso"
    );
    cobertura.dataset.sinCobertura = String(sim.sin_cobertura_d7);
    partes.push(cobertura);
  }
  partes.push(
    parrafo(
      "Encendida, Orbit pausa después de las 48 horas de veto y puedes regresar cualquier anuncio " +
        "desde esta pantalla. Las propuestas de práctica abiertas se descartan y el ciclo siguiente " +
        "propone las reales."
    )
  );
  return partes;
}

function abrePasoPausa(modo, disparador) {
  var p = cajaPausa();
  pasoPausa = { modo: modo, disparador: disparador };
  p.confirmar.hidden = false;
  p.form.hidden = true;
  p.form.elements.token.value = "";
  p.estado.textContent = "";
  if (modo === "shadow") {
    p.titulo.textContent = "¿Volver a práctica?";
    p.detalle.replaceChildren(
      parrafo(
        "La pausa vuelve a práctica: Orbit deja de pausar anuncios y las propuestas quedan " +
          "registradas sin efecto. Los anuncios que Orbit ya pausó siguen pausados."
      )
    );
    p.form.hidden = false;
    p.form.elements.token.focus();
    return;
  }
  p.titulo.textContent = "¿Encender la pausa?";
  p.detalle.replaceChildren(parrafo("Simulando con tus límites guardados…"));
  p.confirmar.focus();
  var paso = pasoPausa;
  fetch(
    "/api/dashboard/productos/simula-pausa?platform=" + encodeURIComponent(p.caja.dataset.plataforma)
  )
    .then(function (resp) {
      return resp.json().then(
        function (data) {
          return { ok: resp.ok, status: resp.status, data: data };
        },
        function () {
          return { ok: false, status: resp.status, data: {} };
        }
      );
    })
    .then(function (r) {
      // Un "Cancelar" mientras simulaba cierra el paso: no se pinta nada.
      if (pasoPausa !== paso) return;
      if (!r.ok) {
        var error = nodo(
          "p",
          "No se puede encender sin ver la simulación: " + mensajeError(r.data) + ".",
          "alerta"
        );
        error.dataset.simulacionFallo = String(r.status);
        p.detalle.replaceChildren(error);
        return;
      }
      p.detalle.replaceChildren.apply(p.detalle, resumenEncender(r.data));
      p.form.hidden = false;
      p.form.elements.token.focus();
    })
    .catch(function () {
      if (pasoPausa !== paso) return;
      p.detalle.replaceChildren(nodo("p", "Error de red al simular: no se puede encender.", "alerta"));
    });
}

function cancelaPasoPausa() {
  var p = cajaPausa();
  var disparador = pasoPausa && pasoPausa.disparador;
  pasoPausa = null;
  p.confirmar.hidden = true;
  p.form.hidden = true;
  p.form.elements.token.value = "";
  p.detalle.replaceChildren();
  p.estado.textContent = "Cancelado: no se cambió nada.";
  if (disparador) disparador.focus();
}

function confirmaPasoPausa() {
  var p = cajaPausa();
  if (!pasoPausa || pasoPausa.enviando) return;
  var paso = pasoPausa;
  var modo = paso.modo;
  paso.enviando = true;
  var cuerpo = { modo: modo, base_config_version_id: Number(p.caja.dataset.configId) };
  p.estado.textContent = "Enviando…";
  fetch("/api/ads-optimizer/productos/pausa/" + p.caja.dataset.plataforma, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-orbit-token": p.form.elements.token.value },
    body: JSON.stringify(cuerpo),
  })
    .then(function (resp) {
      return resp.json().then(
        function (data) {
          return { ok: resp.ok, status: resp.status, data: data };
        },
        function () {
          return { ok: false, status: resp.status, data: {} };
        }
      );
    })
    .then(function (r) {
      if (r.ok) {
        p.form.elements.token.value = "";
        p.estado.textContent =
          modo === "live" ? "Pausa en vivo. Recargando…" : "La pausa volvió a práctica. Recargando…";
        window.location.reload();
        return;
      }
      paso.enviando = false;
      if (r.status === 409) {
        p.estado.textContent =
          "No se cambió: la config cambió mientras mirabas (" + mensajeError(r.data) +
          "). Recarga la página.";
        return;
      }
      p.estado.textContent =
        (r.status === 422 ? "No se cambió: " : "Error: ") + mensajeError(r.data) + ".";
    })
    .catch(function () {
      paso.enviando = false;
      p.estado.textContent = "Error de red: no se cambió la pausa.";
    });
}

function cableaPausa() {
  var caja = document.getElementById("pausa-vivo");
  if (!caja) return;
  var encender = document.getElementById("pausa-encender");
  if (encender) {
    encender.addEventListener("click", function () {
      if (encender.disabled) return;
      abrePasoPausa("live", encender);
    });
  }
  var volver = document.getElementById("pausa-volver");
  if (volver) {
    volver.addEventListener("click", function () {
      abrePasoPausa("shadow", volver);
    });
  }
  document.getElementById("pausa-cancelar").addEventListener("click", cancelaPasoPausa);
  var form = document.getElementById("pausa-confirmar-form");
  form.addEventListener("submit", function (evento) {
    evento.preventDefault();
    confirmaPasoPausa();
  });
}

// Un cambio de selección o de subfamilia deja vieja la previa que se ve.
document.addEventListener("change", function (evento) {
  var destino = evento.target;
  if (!destino || !destino.dataset) return;
  if (destino.dataset.marcarCandidato !== undefined) {
    cambioDeSeleccion(flujoImpulso, contextoImpulso());
  } else if (destino.dataset.marcarSinPedido !== undefined) {
    // Esa tabla alimenta las dos rutas: "Sacar" e "Impulsar aparte".
    cambioDeSeleccion(flujoRetiro, contextoRetiro());
    cambioDeSeleccion(flujoImpulso, contextoImpulso());
  } else if (destino.dataset.subfamilia !== undefined) {
    cambioDeSeleccion(flujoImpulso, contextoImpulso());
  }
});

document.addEventListener("DOMContentLoaded", function () {
  cableaPausa();
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
