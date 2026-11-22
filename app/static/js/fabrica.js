(function () {
"use strict";
// Cliente de fabrica: un plan vigente, sin persistir secretos ni reintentar mutaciones.
// Las vistas puras viven en fabrica_vistas.js y los lotes en fabrica_lotes.js.

function ultimaRespuesta() {
  let version = 0;
  return () => {
    const actual = ++version;
    return () => actual === version;
  };
}

document.addEventListener("DOMContentLoaded", function () {
  const porId = nombre => document.getElementById("fabrica-" + nombre);
  const formulario = porId("plan");
  if (!formulario) return;

  const vistas = Orbit._fabricaVistas;
  const {
    ROLES: roles,
    nombreRol,
    ausente,
    valor,
    dinero,
    porcentaje,
    nodo,
    conClase,
    fotoPublicacion,
    tonoMargen,
    muestraMargen,
    chipsPublicacion,
    mostrarDetalleBids,
    mostrarPreview,
    piezasEstimacion,
    renderComparador: pintarComparador,
  } = vistas;

  let revision = 0;
  const sigCatalogo = ultimaRespuesta();
  const sigComparador = ultimaRespuesta();
  let asOfEstimacion = null;
  let tarjetasCatalogo = [];
  let pujaManualSinSugerencia = new Set();
  let detallePujas = {};
  let catalogoDisponible = false;
  let resumenCatalogo = null;
  let consultandoPlan = false;
  let consultandoBids = false;
  let sugerencias = null;
  let mutando = false;
  let preview = null;
  let comparadorDatos = null;
  const intentados = new Set();
  let temporizadorComparador = null;

  function estado(nombre, mensaje, error = false) {
    const elemento = porId(nombre);
    elemento.textContent = mensaje;
    elemento.className = error ? "alerta" : "";
  }

  async function solicitar(ruta, cuerpo, token) {
    let r;
    try {
      r = cuerpo === undefined
        ? await Orbit.getJson("/api/fabrica" + ruta)
        : await Orbit.postJson("/api/fabrica" + ruta, cuerpo, token);
    } catch (_) {
      throw new Error("Se perdió la conexión. Consulta el lote antes de continuar.");
    }
    if (!r.jsonOk) {
      throw new Error("Respuesta no legible. Consulta el estado del lote.");
    }
    if (!r.ok) {
      const texto = Orbit.errorTexto(r.data, r.status);
      const mensaje = texto === "HTTP " + r.status || texto === "la respuesta no trae detalle"
        ? "No se pudo completar la solicitud (HTTP " + r.status + ")."
        : texto;
      const error = new Error(mensaje);
      error.status = r.status;
      error.lote = r.data && r.data.detail && r.data.detail.lote;
      error.faltaConfirmar = !!(r.data && r.data.detail && r.data.detail.falta_confirmar);
      throw error;
    }
    return r.data;
  }

  function tomarToken() {
    const token = porId("token").value;
    if (!token.trim()) throw new Error("Escribe el token de Orbit para confirmar la operación.");
    porId("token").value = "";
    return token;
  }

  let lotes;

  function actualizarBotones() {
    const l = lotes ? lotes.estadoLote() : { loteActual: null, detalleDisponible: false, retiroElegible: false };
    porId("configuracion").disabled = mutando;
    porId("previsualizar").disabled = mutando || consultandoPlan || !catalogoDisponible;
    porId("bids-amazon").disabled = mutando || consultandoPlan || consultandoBids || !catalogoDisponible;
    porId("crear-boton").disabled = mutando || !preview || intentados.has(preview.huella);
    porId("recuperacion").disabled = mutando || !l.detalleDisponible;
    porId("lote-recargar").disabled = !l.loteActual;
    porId("historial-recargar").disabled = mutando;
    porId("retiro-boton").disabled = mutando || !l.detalleDisponible || !l.retiroElegible;
  }

  lotes = Orbit._fabricaLotes({
    porId,
    estado,
    solicitar,
    tomarToken,
    ultimaRespuesta,
    vistas,
    mutandoGet: () => mutando,
    mutandoSet: v => { mutando = v; },
    actualizarBotones,
  });

  function seleccionadas() {
    return new Set(
      Array.from(porId("productos").querySelectorAll('input[type="checkbox"]:checked'))
        .map(input => Number(input.value)),
    );
  }

  function actualizarResumenCatalogo() {
    if (!catalogoDisponible || !resumenCatalogo) return;
    const cantidad = seleccionadas().size;
    const contador = nodo("strong", cantidad);
    porId("catalogo-estado").replaceChildren(contador,
      nodo("span", (cantidad === 1 ? "publicación seleccionada de " : "publicaciones seleccionadas de ") + resumenCatalogo.seleccionables
        + " seleccionables"),
      conClase("span", "fabrica-catalogo-total", "· " + resumenCatalogo.totalProductos + " productos en el catálogo"));
  }

  function actualizarTotal() {
    const salida = porId("presupuesto-total");
    const moneda = porId("moneda").textContent;
    let centavos = 0n;
    for (const rol of roles) {
      const v = formulario.elements[rol + "_budget"].value.trim();
      if (!/^[0-9]+([.][0-9]{1,2})?$/.test(v) || v.length > 14) {
        salida.textContent = "Completa los cinco presupuestos";
        return;
      }
      const [entero, frac = ""] = v.split(".");
      centavos += BigInt(entero) * 100n + BigInt(frac.padEnd(2, "0"));
    }
    salida.textContent = moneda === "—" ? "Moneda sin consultar"
      : dinero((centavos / 100n) + "." + String(centavos % 100n).padStart(2, "0"), moneda);
  }

  function actualizarAvisosPujas() {
    roles.forEach(rol => {
      const detalle = detallePujas[rol];
      let texto = "";
      if (detalle && !detalle.disponible) {
        texto = "Amazon no devolvió sugerencia: puja manual";
      } else if (detalle && detalle.disponible && detalle.promediadas > 0) {
        texto = "Amazon no devolvió sugerencia para " + detalle.promediadas
          + " de " + detalle.total + ": usan el promedio del rol ("
          + dinero(detalle.promedio, detalle.moneda) + ")";
      }
      porId(rol + "-bid-aviso").textContent = texto;
    });
  }

  function invalidar(conservarSugerencias = false) {
    actualizarTotal();
    revision += 1;
    if (preview !== null) programarComparador();
    preview = null;
    if (!conservarSugerencias) {
      sugerencias = null;
      pujaManualSinSugerencia = new Set();
      detallePujas = {};
      actualizarAvisosPujas();
      porId("bids-detalle").replaceChildren();
      porId("bids-detalle").hidden = true;
    }
    porId("preview").hidden = true;
    porId("confirmacion").value = "";
    porId("casilla-presupuesto").hidden = true;
    porId("acepto-presupuesto-alto").checked = false;
    actualizarBotones();
  }

  function actualizarObjetivoManual() {
    const manual = porId("objetivo-origen").value === "manual_lanzamiento";
    const campo = porId("objetivo-acos");
    campo.disabled = !manual;
    campo.required = manual;
    if (!manual) campo.value = "";
    invalidar();
  }

  function renderComparador() {
    pintarComparador(porId, comparadorDatos, seleccionadas());
  }

  function objetivoFormulario() {
    if (porId("objetivo-origen").value !== "manual_lanzamiento") return null;
    const v = Number(porId("objetivo-acos").value);
    return Number.isFinite(v) && v > 0 && v <= 100 ? v : null;
  }

  function objetivoConsultaComparador() {
    if (porId("objetivo-origen").value === "manual_lanzamiento") return objetivoFormulario();
    if (preview && preview.plan && preview.plan.schema_version === 2
      && preview.plan.objetivo && preview.plan.objetivo.origen === "margen_medido") {
      const derivado = Number(preview.plan.objetivo.acos_pct);
      return Number.isFinite(derivado) && derivado > 0 && derivado <= 100 ? derivado : null;
    }
    return null;
  }

  function programarComparador() {
    clearTimeout(temporizadorComparador);
    temporizadorComparador = setTimeout(cargarComparador, 400);
  }

  async function cargarComparador() {
    clearTimeout(temporizadorComparador);
    temporizadorComparador = null;
    const esVigente = sigComparador();
    porId("comparador-datos").replaceChildren();
    estado("comparador-estado", "Consultando la comparación…");
    try {
      let url = "/evaluacion?plataforma="
        + encodeURIComponent(porId("plataforma").value)
        + "&orden=" + encodeURIComponent(porId("comparador-orden").value)
        + "&direccion=" + encodeURIComponent(porId("comparador-direccion").value);
      const objetivo = objetivoConsultaComparador();
      if (objetivo !== null) url += "&objetivo=" + encodeURIComponent(String(objetivo));
      if (asOfEstimacion) url += "&as_of=" + encodeURIComponent(asOfEstimacion);
      const datos = await solicitar(url);
      if (!esVigente()) return;
      comparadorDatos = datos;
      if (datos.as_of) asOfEstimacion = datos.as_of;
      renderComparador();
      estado("comparador-estado", (datos.publicaciones || []).length
        + " publicaciones · sin dato al final del orden · muestra limitada fuera del orden.");
    } catch (error) {
      if (!esVigente()) return;
      comparadorDatos = null;
      estado("comparador-estado", error.message, true);
    }
  }

  function actualizarConteoBuscador() {
    let ocultas = 0, selOcultas = 0;
    tarjetasCatalogo.forEach(({elemento}) => {
      if (!elemento.hidden) return;
      ocultas += 1;
      selOcultas += elemento.querySelectorAll('input[type="checkbox"]:checked').length;
    });
    const sel = seleccionadas().size;
    let texto = sel + (sel === 1 ? " seleccionada" : " seleccionadas")
      + " · " + ocultas + (ocultas === 1 ? " oculta" : " ocultas") + " por el filtro";
    if (selOcultas) texto += " (" + selOcultas
      + (selOcultas === 1 ? " seleccionada oculta" : " seleccionadas ocultas") + ")";
    porId("buscar-conteo").textContent = texto;
  }

  function filtrarCatalogo() {
    const consulta = porId("buscar").value.trim().toLowerCase();
    tarjetasCatalogo.forEach(({elemento, texto}) => {
      elemento.hidden = consulta !== "" && !texto.includes(consulta);
    });
    actualizarConteoBuscador();
  }

  async function cargarCatalogo() {
    const previas = seleccionadas();
    const esVigente = sigCatalogo();
    invalidar();
    catalogoDisponible = false;
    tarjetasCatalogo = [];
    porId("productos").replaceChildren();
    porId("tipos").replaceChildren();
    porId("moneda").textContent = "—";
    actualizarTotal();
    actualizarBotones();
    estado("catalogo-estado", "Consultando productos…");
    try {
      const datos = await solicitar("/catalogo?plataforma=" + encodeURIComponent(porId("plataforma").value));
      if (!esVigente()) return;
      asOfEstimacion = datos.as_of || null;
      porId("moneda").textContent = datos.moneda;
      actualizarTotal();
      datos.tipos_producto.forEach(tipo => { const opcion = nodo("option"); opcion.value = tipo; porId("tipos").append(opcion); });
      datos.productos.forEach(producto => {
        const tarjeta = nodo("div");
        tarjeta.className = "fabrica-producto";
        const cabecera = conClase("div", "fabrica-producto-cabecera");
        cabecera.append(nodo("h4", "Nombre interno: " + valor(producto.nombre)),
          conClase("span", "fabrica-mono fabrica-apoyo", "SKU de Odoo: " + producto.sku),
          conClase("span", "fabrica-producto-conteo", (producto.publicaciones || []).length + ((producto.publicaciones || []).length === 1 ? " publicación" : " publicaciones")));
        tarjeta.append(cabecera);
        const publicaciones = nodo("ul");
        (producto.publicaciones || []).forEach(publicacion => {
          const fila = conClase("li", "fabrica-publicacion");
          const label = conClase("label", "fabrica-publicacion-tarjeta"), input = nodo("input");
          input.id = "fabrica-listing-" + publicacion.id; label.htmlFor = input.id;
          input.type = "checkbox"; input.value = String(publicacion.id);
          input.disabled = !publicacion.elegible;
          input.setAttribute("aria-label", "Seleccionar publicación " + valor(publicacion.asin));
          const info = conClase("span", "fabrica-publicacion-datos");
          info.append(conClase("strong", "fabrica-mono fabrica-asin", "ASIN: " + valor(publicacion.asin)),
            conClase("span", "fabrica-mono fabrica-apoyo", "SKU de Amazon: " + valor(publicacion.seller_sku)));
          const margen = conClase("span", "fabrica-margen");
          const monto = ausente(publicacion.margen_neto_pct) ? "—" : porcentaje(publicacion.margen_neto_pct);
          margen.append(conClase("strong", tonoMargen(publicacion.margen_neto_pct), monto),
            nodo("span", "margen neto antes de Ads"));
          info.append(margen, conClase("span", "fabrica-apoyo", muestraMargen(publicacion)), chipsPublicacion(publicacion));
          const piezas = piezasEstimacion(publicacion.estimacion);
          if (piezas) info.append(piezas.principal);
          if (!publicacion.elegible) info.append(conClase("span", "fabrica-identidad-incompleta",
            "No seleccionable: " + (publicacion.motivos || []).join(" ")));
          label.append(input, fotoPublicacion(publicacion.id, publicacion.asin), info);
          fila.append(label);
          if (piezas) fila.append(piezas.detalle);
          if (/^https:\/\/www\.amazon\.com(?:\.mx)?\/dp\/[A-Za-z0-9]{10}$/.test(publicacion.url || "")) {
            const enlace = conClase("a", "fabrica-publicacion-enlace", "Abrir en Amazon ↗");
            enlace.href = publicacion.url; enlace.target = "_blank"; enlace.rel = "noopener noreferrer";
            enlace.setAttribute("aria-label", "Abrir ASIN " + publicacion.asin + " en Amazon");
            fila.append(enlace);
          }
          publicaciones.append(fila);
        });
        tarjeta.append(publicaciones);
        const piezas = [producto.nombre, producto.sku];
        (producto.publicaciones || []).forEach(publicacion => {
          piezas.push(publicacion.asin, publicacion.seller_sku);
        });
        tarjetasCatalogo.push({ elemento: tarjeta, texto: piezas.filter(Boolean).join(" ").toLowerCase() });
        porId("productos").append(tarjeta);
      });
      filtrarCatalogo();
      catalogoDisponible = true;
      porId("productos").querySelectorAll('input[type="checkbox"]').forEach(input => {
        if (previas.has(Number(input.value)) && !input.disabled) input.checked = true;
      });
      const publicaciones = datos.productos.flatMap(producto => producto.publicaciones || []);
      resumenCatalogo = {totalProductos: datos.productos.length, seleccionables: publicaciones.filter(p => p.elegible).length};
      actualizarResumenCatalogo();
      renderComparador();
    } catch (error) {
      if (esVigente()) estado("catalogo-estado", error.message, true);
    } finally { if (esVigente()) actualizarBotones(); }
  }

  function solicitudActual() {
    const parametros = {};
    roles.forEach(rol => {
      parametros[rol] = {
        budget: formulario.elements[rol + "_budget"].value.trim(),
        bid: formulario.elements[rol + "_bid"].value.trim(),
      };
      if (sugerencias && sugerencias[rol] && sugerencias[rol].disponible) {
        parametros[rol] = {
          ...parametros[rol],
          fuente_bid: "amazon_v4",
          recomendaciones: sugerencias[rol].recomendaciones.map(r => ({
            tipo: r.tipo, valor: r.valor, minimo: r.minimo,
            sugerido: r.sugerido, maximo: r.maximo, fuente: r.fuente,
          })),
        };
      }
    });
    const objetivo = {origen: porId("objetivo-origen").value};
    if (objetivo.origen === "manual_lanzamiento") objetivo.acos_pct = porId("objetivo-acos").value.trim();
    return {
      plataforma: porId("plataforma").value, tipo_producto: porId("tipo").value.trim(),
      nombre_base: porId("nombre").value.trim(), modo: porId("modo").value,
      listing_ids: Array.from(porId("productos").querySelectorAll('input[type="checkbox"]:checked')).map(input => Number(input.value)),
      objetivo: objetivo,
      parametros,
    };
  }

  async function cargarBidsAmazon() {
    if (mutando || consultandoBids || !catalogoDisponible) return;
    const listingIds = Array.from(
      porId("productos").querySelectorAll('input[type="checkbox"]:checked')
    ).map(input => Number(input.value));
    const tipo = porId("tipo").value.trim();
    const objetivo = {origen: porId("objetivo-origen").value};
    if (objetivo.origen === "manual_lanzamiento") objetivo.acos_pct = porId("objetivo-acos").value.trim();
    if (!listingIds.length || !tipo || !objetivo.origen
      || (objetivo.origen === "manual_lanzamiento" && !objetivo.acos_pct)) {
      estado("estado", "Elige publicaciones, tipo de producto y objetivo ACoS antes de consultar Amazon.", true);
      return;
    }
    consultandoBids = true; actualizarBotones();
    const version = revision;
    estado("estado", "Consultando pujas sugeridas en Amazon…");
    try {
      const datos = await solicitar("/bids-sugeridos", {
        plataforma: porId("plataforma").value, tipo_producto: tipo, listing_ids: listingIds, objetivo,
      });
      if (version !== revision) {
        estado("estado", "La configuración cambió. Consulta de nuevo las pujas de Amazon.");
        return;
      }
      invalidar();
      let cargadas = 0;
      roles.forEach(rol => {
        if (datos.roles[rol].disponible) {
          formulario.elements[rol + "_bid"].value = datos.roles[rol].bid;
          cargadas += 1;
        }
      });
      sugerencias = datos.roles;
      pujaManualSinSugerencia = new Set(roles.filter(rol => !datos.roles[rol].disponible));
      detallePujas = {};
      roles.forEach(rol => {
        const info = datos.roles[rol] || {};
        detallePujas[rol] = {
          disponible: !!info.disponible,
          promediadas: Number(info.promediadas) || 0,
          total: (info.recomendaciones || []).length,
          promedio: info.promedio,
          moneda: porId("moneda").textContent,
        };
      });
      actualizarAvisosPujas();
      const detalle = porId("bids-detalle");
      detalle.replaceChildren();
      mostrarDetalleBids(detalle, datos.roles, porId("moneda").textContent, true);
      detalle.hidden = false;
      const pendientes = roles.filter(rol => !datos.roles[rol].disponible).map(nombreRol).join(", ");
      estado("estado", cargadas + " de 5 pujas sugeridas por Amazon cargadas."
        + (pendientes ? " Captura manualmente: " + pendientes + "." : "")
        + " Revisa presupuestos y prepara el plan.");
    } catch (error) {
      estado("estado", error.message, true);
    } finally {
      consultandoBids = false; actualizarBotones();
    }
  }

  async function revisar(evento) {
    evento.preventDefault();
    if (mutando || consultandoPlan || !catalogoDisponible || !formulario.reportValidity()) return;
    invalidar(true);
    const solicitud = solicitudActual();
    if (!solicitud.listing_ids.length) { estado("estado", "Selecciona al menos una publicación.", true); return; }
    if (!solicitud.objetivo.origen) { estado("estado", "Selecciona el origen del objetivo ACoS.", true); return; }
    if (solicitud.objetivo.origen === "manual_lanzamiento" && !solicitud.objetivo.acos_pct) {
      estado("estado", "Escribe un ACoS manual positivo con hasta dos decimales.", true); return;
    }
    const version = revision;
    consultandoPlan = true; actualizarBotones();
    estado("estado", "Preparando la revisión sin crear campañas…");
    try {
      const datos = await solicitar("/plan", solicitud);
      if (version !== revision) { estado("estado", "La configuración cambió. Revisa el plan de nuevo."); return; }
      preview = {...datos, solicitud};
      mostrarPreview(porId, datos, pujaManualSinSugerencia);
      cargarComparador();
      estado("estado", intentados.has(datos.huella)
        ? "Este plan ya se envió. Consulta el lote; no vuelvas a crear el grupo."
        : "Plan listo para revisar. Todavía no se ha creado ninguna campaña.");
    } catch (error) { if (version === revision) estado("estado", error.message, true); }
    finally { consultandoPlan = false; actualizarBotones(); }
  }

  function ocultarResultadoCrear() {
    const banner = porId("crear-resultado");
    banner.replaceChildren();
    banner.hidden = true;
  }

  function mostrarResultadoCrear(mensaje, lote) {
    const banner = porId("crear-resultado");
    banner.replaceChildren();
    banner.append(nodo("strong", mensaje));
    if (lote) {
      const enlace = nodo("a", "Ver detalle del lote " + lote);
      enlace.href = "#fabrica-lote";
      enlace.addEventListener("click", () => { porId("lote-titulo").focus(); });
      banner.append(nodo("span", " "), enlace);
    }
    banner.hidden = false;
    banner.focus();
  }

  async function crear(evento) {
    evento.preventDefault();
    if (mutando || !preview || intentados.has(preview.huella)) return;
    if (porId("confirmacion").value !== "CREAR 5 CAMPAÑAS") {
      estado("estado", "Escribe exactamente CREAR 5 CAMPAÑAS para confirmar.", true); return;
    }
    if ((preview.avisos_presupuesto || []).length && !porId("acepto-presupuesto-alto").checked) {
      estado("estado", "Marca «Sí, quiero ese presupuesto» para continuar", true); return;
    }
    let token;
    try { token = tomarToken(); } catch (error) { estado("estado", error.message, true); return; }
    const datos = preview;
    intentados.add(datos.huella);
    lotes.seleccionarLote(datos.lote);
    mutando = true; actualizarBotones();
    porId("crear-boton").textContent = "Creando… no cierres la página";
    porId("crear-progreso").hidden = false;
    ocultarResultadoCrear();
    porId("confirmacion").value = "";
    estado("estado", "Creando el grupo. Conserva esta página para consultar el lote.");
    estado("lote-estado", "Creación en curso…");
    let tokenRechazado = false;
    let rechazoConfirmacion = false;
    const cuerpo = { solicitud: datos.solicitud, huella: datos.huella, confirmacion: "CREAR 5 CAMPAÑAS" };
    if (porId("acepto-presupuesto-alto").checked) cuerpo.acepto_presupuesto_alto = true;
    try {
      const resultado = await solicitar("/crear", cuerpo, token);
      lotes.invalidarDetalle();
      lotes.mostrarLote(resultado);
      mostrarResultadoCrear("Grupo creado: " + Orbit.etiqueta(resultado.estado, "estado_lote"), resultado.lote);
      estado("estado", "Solicitud finalizada. Revisa el estado y los pasos del lote.");
      estado("lote-estado", "Resultado recibido: " + Orbit.etiqueta(resultado.estado, "estado_lote"));
    } catch (error) {
      lotes.invalidarDetalle();
      if (error.status === 401) {
        intentados.delete(datos.huella);
        tokenRechazado = true;
        mostrarResultadoCrear("Token rechazado. Corrígelo y confirma de nuevo el plan revisado.", null);
        estado("estado", "Token rechazado. Corrígelo y confirma de nuevo el plan revisado.", true);
        estado("lote-estado", "La solicitud fue rechazada antes de crear el lote.", true);
        return;
      }
      if (error.status === 422 && error.faltaConfirmar) {
        intentados.delete(datos.huella);
        rechazoConfirmacion = true;
        mostrarResultadoCrear(error.message, null);
        estado("estado", error.message, true);
        estado("lote-estado", "La solicitud fue rechazada antes de crear el lote.", true);
        return;
      }
      if (error.lote && error.lote !== lotes.estadoLote().loteActual) lotes.seleccionarLote(error.lote);
      mostrarResultadoCrear("Creación interrumpida. Consulta el detalle del lote; no se reintentará la creación.",
        error.lote || lotes.estadoLote().loteActual);
      estado("estado", error.message + " Conserva el lote y consulta su estado; no se reintentará la creación.", true);
      estado("lote-estado", "Resultado pendiente de consultar. " + error.message, true);
    } finally {
      token = null; mutando = false;
      porId("crear-boton").textContent = "Crear las cinco campañas";
      porId("crear-progreso").hidden = true;
      if (tokenRechazado || rechazoConfirmacion) actualizarBotones();
      else invalidar();
      lotes.cargarHistorial();
    }
  }

  function cambioFormulario(evento) {
    if (evento.target === porId("buscar")) return;
    const nombre = evento.target && evento.target.name;
    if (sugerencias && typeof nombre === "string" && nombre.endsWith("_bid")) {
      const rol = nombre.slice(0, -4);
      if (roles.includes(rol) && sugerencias[rol]) {
        sugerencias[rol] = {...sugerencias[rol], disponible: false};
      }
      invalidar(true);
      return;
    }
    const conservaSugerencias = nombre === "nombre_base" || nombre === "modo"
      || (typeof nombre === "string" && nombre.endsWith("_budget"));
    invalidar(conservaSugerencias);
  }

  formulario.addEventListener("input", cambioFormulario);
  formulario.addEventListener("change", cambioFormulario);
  formulario.addEventListener("submit", revisar);
  porId("objetivo-acos").addEventListener("input", programarComparador);
  porId("objetivo-origen").addEventListener("change", () => { actualizarObjetivoManual(); cargarComparador(); });
  porId("crear").addEventListener("submit", crear);
  porId("bids-amazon").addEventListener("click", cargarBidsAmazon);
  porId("accion").addEventListener("submit", lotes.ejecutarAccion);
  porId("accion-tipo").addEventListener("change", () => { porId("accion-confirmacion").value = ""; });
  porId("plataforma").addEventListener("change", () => {
    roles.forEach(rol => {
      formulario.elements[rol + "_budget"].value = "";
      formulario.elements[rol + "_bid"].value = "";
    });
    cargarCatalogo().then(() => cargarComparador());
    lotes.cargarHistorial();
  });
  porId("productos").addEventListener("change", () => {
    actualizarResumenCatalogo(); renderComparador(); actualizarConteoBuscador();
  });
  porId("recargar-catalogo").addEventListener("click", () => {
    cargarCatalogo().then(() => cargarComparador());
  });
  porId("buscar").addEventListener("input", filtrarCatalogo);
  porId("buscar").addEventListener("keydown", evento => { if (evento.key === "Enter") evento.preventDefault(); });
  porId("historial-recargar").addEventListener("click", lotes.cargarHistorial);
  porId("lote-recargar").addEventListener("click", lotes.cargarLote);
  porId("retiro-boton").addEventListener("click", lotes.pedirRetiro);
  porId("retiro-ejecutar").addEventListener("submit", lotes.ejecutarRetiro);
  porId("comparador-recargar").addEventListener("click", cargarComparador);
  porId("comparador-orden").addEventListener("change", cargarComparador);
  porId("comparador-direccion").addEventListener("change", cargarComparador);
  porId("comparador-filtro").addEventListener("change", renderComparador);
  window.addEventListener("pagehide", () => { porId("token").value = ""; });
  actualizarObjetivoManual();
  cargarCatalogo().then(() => cargarComparador());
  lotes.cargarHistorial();
  const lote = new URL(window.location.href).searchParams.get("lote");
  if (lote) { lotes.seleccionarLote(lote); lotes.cargarLote(); }
});
})();
