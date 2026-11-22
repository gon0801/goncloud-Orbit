(function () {
"use strict";
// JS de productos sin clics — flujos de impulso, retiro y reposicion (BIDS 02,
// P.4; ARREGLOS 04 W-P). Vive en /static por la CSP `default-src 'self'`.
// Token SOLO en header x-orbit-token (la query string JAMAS autentica).
// La seccion de filtro/limites/pausa vive en productos_pausa.js.

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
    incierto: "No se sabe si el impulso se aplicó: recarga la página y revisa Impulsos antes de reintentar.",
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
    incierto: "No se sabe si el retiro se aplicó: recarga la página y revisa los retiros antes de reintentar.",
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

function parrafo(texto, clase) {
  var p = document.createElement("p");
  p.textContent = texto;
  if (clase) p.className = clase;
  return p;
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

// Sin el punto final: quien llama cierra la frase con el suyo.
function mensajeError(data, status) {
  var detalle = data && data.detail;
  if (detalle && typeof detalle === "object" && !Array.isArray(detalle) && detalle.mensaje) {
    var texto = String(detalle.mensaje).replace(/\.$/, "");
    var lanzados = detalle.lanzados || [];
    if (lanzados.length) texto += " · lanzados: " + lanzados.length;
    var fallos = detalle.fallos || {};
    Object.keys(fallos).forEach(function (lid) {
      texto += " · " + lid + ": " + fallos[lid];
    });
    return texto;
  }
  var base = Orbit.errorTexto(data, status);
  if (base === "la respuesta no trae detalle") return "No se pudo completar la solicitud";
  return base.replace(/\.$/, "");
}

// Solo una respuesta de la revision vigente pasa a lista; la tardia se tira.
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
  Orbit.postJson(url, solicitud)
    .then(function (r) {
      if (!vigente(flujo, revision, solicitud)) return;
      if (!r.ok) {
        fallaPrevia(flujo, ctx, "Error: " + mensajeError(r.data, r.status) + ".");
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

function cambioDeSeleccion(flujo, ctx) {
  if (flujo.fase === "enviando" || flujo.fase === "incierta") return;
  if (flujo.fase === "vacia" && !flujo.previa) return;
  invalidaPrevia(flujo, ctx);
  if (ctx.bloque) ctx.bloque.hidden = false;
  ctx.zona.replaceChildren(parrafo(ctx.cambio));
}

// Ejecutor comun de lanzar impulso y ejecutar retiro: valida fase/literal,
// congela revision, evita doble clic, maneja 409 con vista nueva, error HTTP
// (incluido 502/504 sin JSON) y timeout incierto.
function ejecutaFlujo(flujo, ctx, form, opts) {
  var estado = form.querySelector("[data-estado]");
  if (flujo.fase === "enviando") {
    estado.textContent = "Enviando: espera la respuesta antes de reintentar.";
    return;
  }
  if (flujo.fase === "incierta") {
    estado.textContent = ctx.incierto;
    return;
  }
  if (flujo.fase !== "lista" || !flujo.previa || !flujo.solicitud) {
    estado.textContent = ctx.pidePrimero;
    return;
  }
  var esperada = opts.esperada(flujo.previa);
  var confirmacion = form.elements.confirmacion.value;
  if (confirmacion !== esperada) {
    estado.textContent = "Escribe " + esperada + " para confirmar.";
    return;
  }
  if (opts.validaExtra && !opts.validaExtra(flujo, estado)) return;
  var envio = { revision: flujo.revision, solicitud: flujo.solicitud, previa: flujo.previa };
  var cuerpo = opts.cuerpo(envio, confirmacion, form);
  flujo.fase = "enviando";
  habilitaEnvio(ctx, false);
  estado.textContent = "Enviando…";
  Orbit.postJson(opts.url, cuerpo, form.elements.token.value)
    .then(function (r) {
      if (flujo.revision !== envio.revision) return;
      if (!r.jsonOk) throw new Error(ctx.incierto);
      if (!r.ok) {
        if (opts.enError && opts.enError(r, flujo, ctx, envio, form, estado)) return;
        var texto = "Error: " + mensajeError(r.data, r.status) + ".";
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
      opts.enExito(r.data, form, estado);
    })
    .catch(function () {
      if (flujo.revision !== envio.revision) return;
      flujo.fase = "incierta";
      habilitaEnvio(ctx, false);
      estado.textContent = ctx.incierto;
    });
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

function pintaVista(zona, vista) {
  zona.replaceChildren();
  var conAviso = false;
  vista.planes.forEach(function (plan) {
    var div = document.createElement("div");
    div.appendChild(parrafo(plan.seller_sku + " · " + plan.subfamilia));
    if (plan.presupuesto_alto) {
      conAviso = true;
      var avisoP = parrafo(plan.presupuesto_alto, "aviso");
      avisoP.setAttribute("data-aviso-presupuesto", "");
      div.appendChild(avisoP);
    }
    if (plan.subfamilia_nueva) {
      var aviso = parrafo(
        "Subfamilia nueva: arranca sin negativos; Orbit los aprende con este impulso.",
        "aviso"
      );
      aviso.setAttribute("data-subfamilia-nueva", "");
      div.appendChild(aviso);
    } else if (plan.sin_negativos) {
      var avisoNeg = parrafo(
        "Esta subfamilia todavía no tiene negativos: arranca sin negativos; " +
          "Orbit los aprende con este impulso.",
        "aviso"
      );
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
      div.appendChild(
        parrafo(
          "Pujas acotadas al tope: " +
            plan.bids_acotados
              .map(function (rol) {
                return Orbit.etiqueta(rol, "rol_campana");
              })
              .join(", ")
        )
      );
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
            sinSugerencia
              .map(function (rol) {
                return Orbit.etiqueta(rol, "rol_campana");
              })
              .join(", ")
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
  var casilla = document.getElementById("productos-acepto-presupuesto-alto");
  ejecutaFlujo(flujoImpulso, contextoImpulso(), form, {
    url: "/api/fabrica/impulso/lanzar",
    esperada: function (previa) {
      return previa.confirmacion_esperada;
    },
    validaExtra: function (flujo, estado) {
      var hayAviso = (flujo.previa.planes || []).some(function (p) {
        return !!p.presupuesto_alto;
      });
      if (hayAviso && !(casilla && casilla.checked)) {
        estado.textContent = "Marca «Sí, quiero ese presupuesto» para continuar";
        return false;
      }
      return true;
    },
    cuerpo: function (envio, confirmacion, f) {
      var c = {
        solicitud: envio.solicitud,
        huella: envio.previa.huella,
        confirmacion: confirmacion,
        actor: f.elements.actor.value,
      };
      if (casilla && casilla.checked) c.acepto_presupuesto_alto = true;
      return c;
    },
    enError: function (r, flujo, ctx, envio, f, estado) {
      var detalle = r.data && r.data.detail ? r.data.detail : {};
      if (r.status !== 409 || !detalle.vista_previa) return false;
      if (!mismaSeleccion(flujo)) {
        var texto =
          "El plan cambió y la selección ya no es la de esa vista: pide la nueva antes de confirmar.";
        fallaPrevia(flujo, ctx, texto);
        estado.textContent = texto;
        return true;
      }
      flujo.revision += 1;
      flujo.solicitud = envio.solicitud;
      flujo.previa = detalle.vista_previa;
      flujo.fase = "lista";
      pintaListaImpulso(flujo, ctx);
      f.elements.confirmacion.value = "";
      estado.textContent = "El plan cambió: revisa la vista nueva y confirma de nuevo.";
      return true;
    },
    enExito: function (data, _f, estado) {
      estado.textContent = "Lanzados " + (data.lanzados || []).length + ".";
    },
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

function pintaAnuncios(zona, anuncios, dominio, bien) {
  zona.replaceChildren();
  anuncios.forEach(function (a) {
    if (bien.indexOf(a.resultado) !== -1) return;
    var texto = a.ad_id_externo + " · " + Orbit.etiqueta(a.resultado, dominio);
    zona.appendChild(parrafo(a.detalle ? texto + " (" + a.detalle + ")" : texto));
  });
}

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
  return Orbit.postJson("/api/fabrica/retiro/reponer", cuerpo, form.elements.token.value)
    .then(function (r) {
      if (!r.ok) {
        estado.textContent = "Error: " + mensajeError(r.data, r.status) + ".";
        return;
      }
      estado.textContent = "Regresados " + r.data.repuestos + " de " + r.data.total + ".";
      pintaAnuncios(zona, r.data.anuncios || [], "resultado_regreso", ["repuesto", "ya_repuesto"]);
      var adoptados = r.data.adoptados || [];
      var archivados = r.data.requieren_recreacion || [];
      if (adoptados.length) {
        pasoConLiteral(
          form,
          zona,
          adoptados,
          "REGRESAR",
          "Regresar también los adoptados",
          function (literal, estadoPaso, zonaPaso) {
            regresar(form, adoptados, literal, true, estadoPaso, zonaPaso);
          }
        );
      }
      if (archivados.length) {
        pasoConLiteral(
          form,
          zona,
          archivados,
          "RECREAR",
          "Recrear los archivados",
          function (literal, estadoPaso) {
            recrear(form, archivados, literal, estadoPaso);
          }
        );
      }
      cierraPaso(form);
    })
    .catch(function () {
      estado.textContent = "Error de red al reponer.";
    });
}

function recrear(form, ids, confirmacion, estado) {
  return Orbit.postJson(
    "/api/fabrica/retiro/recrear",
    {
      retiro_ids: ids,
      confirmacion: confirmacion,
      actor: form.elements.actor.value,
    },
    form.elements.token.value
  )
    .then(function (r) {
      if (!r.ok) {
        estado.textContent = "Error: " + mensajeError(r.data, r.status) + ".";
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
        "Vuelve a intentarlo cuando se actualicen.",
      "aviso"
    );
    ctx.zona.appendChild(alerta);
    ctx.bloque.hidden = false;
    return;
  }
  if (plan.vendieron && plan.vendieron.length) {
    ctx.zona.appendChild(parrafo(avisoVendieron(flujo.grupos, plan.vendieron), "aviso"));
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
  ejecutaFlujo(flujoRetiro, contextoRetiro(), form, {
    url: "/api/fabrica/retiro/ejecutar",
    esperada: function (previa) {
      return "SACAR " + previa.anuncios.length + " ANUNCIOS";
    },
    cuerpo: function (envio, confirmacion, f) {
      return {
        plataforma: envio.solicitud.plataforma,
        listing_ids: envio.solicitud.listing_ids,
        motivo: "sin_pedidos",
        huella: envio.previa.huella,
        confirmacion: confirmacion,
        actor: f.elements.actor.value,
      };
    },
    enExito: function (data, f, estado) {
      estado.textContent = "Retirados " + data.retirados + " de " + data.total + ".";
      pintaAnuncios(zonaResultado(f), data.anuncios || [], "resultado_retiro", ["retirado"]);
    },
  });
}

document.addEventListener("change", function (evento) {
  var destino = evento.target;
  if (!destino || !destino.dataset) return;
  if (destino.dataset.marcarCandidato !== undefined) {
    cambioDeSeleccion(flujoImpulso, contextoImpulso());
  } else if (destino.dataset.marcarSinPedido !== undefined) {
    cambioDeSeleccion(flujoRetiro, contextoRetiro());
    cambioDeSeleccion(flujoImpulso, contextoImpulso());
  } else if (destino.dataset.subfamilia !== undefined) {
    cambioDeSeleccion(flujoImpulso, contextoImpulso());
  }
});

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

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    verLanzamiento: verLanzamiento,
    lanzar: lanzar,
    reponer: reponer,
    marcados: marcados,
    verRetiro: verRetiro,
    ejecutarRetiro: ejecutarRetiro,
  };
}
})();
