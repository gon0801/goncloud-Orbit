"use strict";
// Cliente de fabrica: un plan vigente, sin persistir secretos ni reintentar mutaciones.
document.addEventListener("DOMContentLoaded", function () {
  const porId = nombre => document.getElementById("fabrica-" + nombre);
  const formulario = porId("plan");
  if (!formulario) return;
  const roles = ["category_exact", "category_phrase", "category_broad", "product_targeting", "auto_discovery"];
  const etiquetas = {
    category_exact: "Categoría · exacta", category_phrase: "Categoría · frase",
    category_broad: "Categoría · amplia", product_targeting: "Segmentación por producto",
    auto_discovery: "Descubrimiento automático",
  };
  const confirmaciones = {
    pausar: "PAUSAR GRUPO", reconciliar: "RECONCILIAR GRUPO", registrar: "REGISTRAR GRUPO",
  };
  const estadosLote = {
    applied: "Creado y registrado", desarmado: "Pausado", failed: "Interrumpido",
    planeado: "Pendiente de verificación",
  };
  const estadosPaso = {applied: "Verificado", failed: "Falló", planeado: "Pendiente"};
  // Comparador (ORBIT 19 B.5): SOLO presenta lo que devuelve /evaluacion.
  // El orden lo resuelve la API (null al final); el filtro es de presentacion
  // y jamas toca la seleccion del catalogo (AC10). Cero escrituras.
  const etiquetasAds = {
    gasto_sin_ventas: "Gasto sin ventas",
    dentro_del_objetivo: "Dentro del objetivo",
    por_encima_del_objetivo: "Por encima del objetivo",
  };
  const estadosDisponibilidad = {
    positivo: "Con stock", cero: "Stock en 0", desconocido: "Desconocido",
  };
  let revision = 0;
  let versionCatalogo = 0;
  let versionHistorial = 0;
  let versionDetalle = 0;
  let catalogoDisponible = false;
  let consultandoPlan = false;
  let mutando = false;
  let preview = null;
  let loteActual = null;
  let detalleDisponible = false;
  let versionComparador = 0;
  let comparadorDatos = null;
  const intentados = new Set();

  function nodo(tag, texto) {
    const elemento = document.createElement(tag);
    if (texto !== undefined) elemento.textContent = String(texto);
    return elemento;
  }

  function valor(dato) { return dato === null || dato === undefined ? "Sin dato" : String(dato); }
  function porcentaje(dato) {
    if (dato === null || dato === undefined) return "Sin dato";
    // Solo presentacion de porcentajes; el dinero conserva su string original.
    return new Intl.NumberFormat("es-MX", {maximumFractionDigits: 2}).format(Number(dato)) + " %";
  }

  function estado(nombre, mensaje, error = false) {
    const elemento = porId(nombre);
    elemento.textContent = mensaje;
    elemento.className = error ? "alerta" : "";
  }

  function mensajeError(datos, status) {
    const detalle = datos && datos.detail;
    if (typeof detalle === "string") return detalle;
    if (detalle && detalle.mensaje) return detalle.mensaje;
    if (Array.isArray(detalle)) return detalle.map(error => error.msg).join("; ");
    return "No se pudo completar la solicitud (HTTP " + status + ").";
  }

  async function solicitar(ruta, cuerpo, token) {
    const opciones = {method: cuerpo === undefined ? "GET" : "POST", cache: "no-store", headers: {}};
    if (cuerpo !== undefined) {
      opciones.headers["Content-Type"] = "application/json";
      opciones.body = JSON.stringify(cuerpo);
    }
    if (token !== undefined) opciones.headers["x-orbit-token"] = token;
    let respuesta;
    try { respuesta = await fetch("/api/fabrica" + ruta, opciones); }
    catch (_) { throw new Error("Se perdió la conexión. Consulta el lote antes de continuar."); }
    let datos;
    try { datos = await respuesta.json(); }
    catch (_) { throw new Error("Respuesta no legible. Consulta el estado del lote."); }
    if (!respuesta.ok) {
      const error = new Error(mensajeError(datos, respuesta.status));
      error.status = respuesta.status;
      error.lote = datos && datos.detail && datos.detail.lote;
      throw error;
    }
    return datos;
  }

  function actualizarBotones() {
    porId("configuracion").disabled = mutando;
    porId("previsualizar").disabled = mutando || consultandoPlan || !catalogoDisponible;
    porId("crear-boton").disabled = mutando || !preview || intentados.has(preview.huella);
    porId("recuperacion").disabled = mutando || !detalleDisponible;
    porId("lote-recargar").disabled = !loteActual;
    porId("historial-recargar").disabled = mutando;
  }

  function actualizarTotal() {
    const salida = porId("presupuesto-total");
    const moneda = porId("moneda").textContent;
    let centavos = 0n;
    for (const rol of roles) {
      const valor = formulario.elements[rol + "_budget"].value.trim();
      if (!/^[0-9]+([.][0-9]{1,2})?$/.test(valor) || valor.length > 14) {
        salida.textContent = "Completa los cinco presupuestos";
        return;
      }
      const [entero, fraccion = ""] = valor.split(".");
      centavos += BigInt(entero) * 100n + BigInt(fraccion.padEnd(2, "0"));
    }
    salida.textContent = moneda === "—" ? "Moneda sin consultar"
      : (centavos / 100n) + "." + String(centavos % 100n).padStart(2, "0") + " " + moneda;
  }

  function invalidar() {
    actualizarTotal();
    revision += 1;
    preview = null;
    porId("preview").hidden = true;
    porId("confirmacion").value = "";
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

  function tabla(contenedor, encabezados, filas) {
    const envoltura = nodo("div");
    envoltura.className = "fabrica-tabla";
    envoltura.tabIndex = 0;
    const elemento = nodo("table"), thead = nodo("thead"), tr = nodo("tr"), tbody = nodo("tbody");
    encabezados.forEach(titulo => {
      const th = nodo("th", titulo); th.scope = "col"; tr.append(th);
    });
    thead.append(tr);
    filas.forEach(celdas => {
      const fila = nodo("tr");
      celdas.forEach(celda => fila.append(nodo("td", valor(celda))));
      tbody.append(fila);
    });
    elemento.append(thead, tbody); envoltura.append(elemento); contenedor.append(envoltura);
  }

  function ficha(contenedor, filas) {
    const dl = nodo("dl");
    filas.forEach(([nombre, dato]) => dl.append(nodo("dt", nombre), nodo("dd", valor(dato))));
    contenedor.append(dl);
  }

  function avisoMargen(publicacion, objetivo) {
    const margen = publicacion.margen_neto_pct;
    const avisos = Array.isArray(publicacion.motivos) ? [...publicacion.motivos] : [];
    if (publicacion.historial_ads === null) avisos.push("Historial Ads sin dato en este catálogo.");
    if (margen === null || margen === undefined) avisos.push("Margen sin medir; el objetivo manual no acredita rentabilidad.");
    else if (Number(margen) === 0) avisos.push("Margen cero; el objetivo manual no acredita rentabilidad.");
    else if (Number(margen) < 0) avisos.push("Margen negativo; el objetivo manual no acredita rentabilidad.");
    else if (objetivo && Number(margen) < Number(objetivo)) avisos.push("Margen inferior al objetivo manual.");
    return avisos.length ? avisos.join(" ") : "Margen medido disponible.";
  }

  function muestraMargen(publicacion) {
    const dias = publicacion.dias_con_venta;
    const desde = publicacion.ventana_desde;
    const hasta = publicacion.ventana_hasta;
    if (dias === null || dias === undefined || !desde || !hasta) return "Muestra de margen: sin dato.";
    const limitada = Number(dias) < 30;
    return (limitada ? "Muestra limitada: " : "Muestra de margen: ") + dias
      + " dias con venta, ventana [" + desde + ", " + hasta + ").";
  }

  function mostrarPlan(contenedor, plan) {
    const esV2 = plan.schema_version === 2;
    const objetivo = esV2 ? plan.objetivo : null;
    const target = esV2 ? objetivo.acos_pct : plan.target_acos_pct;
    ficha(contenedor, [
      ["Grupo", plan.nombre_base], ["Tipo de producto", plan.tipo_producto],
      ["Plataforma / moneda", plan.platform + " / " + plan.moneda], ["Fecha del plan", plan.fecha],
      ["Modo del optimizador", plan.modo], ["Target ACoS aplicado", porcentaje(target)],
      ["Origen del objetivo", esV2 ? objetivo.origen : "margen_medido"],
      ["Target derivado del margen", esV2 ? porcentaje(objetivo.derivado) : porcentaje(plan.target_derivado_pct)],
      ["Fracción del margen", esV2 ? objetivo.fraccion : plan.fraccion],
      ["Procedencia del target", esV2 ? objetivo.procedencia : plan.target_procedencia],
    ]);
    const publicaciones = esV2 ? plan.publicaciones : plan.productos;
    contenedor.append(nodo("h4", esV2 ? "Publicaciones y márgenes netos" : "Productos y márgenes netos"));
    tabla(
      contenedor,
      esV2 ? ["Listing", "Producto", "SKU de Amazon", "ASIN", "Margen neto", "Aviso"]
        : ["SKU", "SKU de Amazon", "ASIN", "Margen neto"],
      publicaciones.map(publicacion => esV2 ? [
        publicacion.listing_id, publicacion.product_id, publicacion.seller_sku, publicacion.asin,
        porcentaje(publicacion.margen_neto_pct), avisoMargen(publicacion, objetivo.origen === "manual_lanzamiento" ? target : null),
      ] : [
        publicacion.odoo_sku, publicacion.seller_sku, publicacion.asin, porcentaje(publicacion.margen_neto_pct),
      ]),
    );
    contenedor.append(nodo("h4", "Semillas del grupo"));
    const nombres = {exact: "Palabras exactas", keywords: "Palabras para frase y amplia", asins: "ASIN objetivo", negativos: "Negativos"};
    Object.keys(nombres).forEach(clave => {
      contenedor.append(nodo("h5", nombres[clave]));
      const semillas = plan.semillas[clave];
      if (!semillas.length) contenedor.append(nodo("p", "Sin semillas para este tipo."));
      else {
        const lista = nodo("ul");
        semillas.forEach(semilla => lista.append(nodo("li", semilla)));
        contenedor.append(lista);
      }
    });
  }

  function mostrarPreview(datos) {
    const contenedor = porId("preview-datos");
    contenedor.replaceChildren();
    mostrarPlan(contenedor, datos.plan);
    contenedor.append(nodo("h4", "Las cinco campañas"));
    tabla(contenedor, ["Rol", "Nombre", "Presupuesto diario", "Puja inicial"], datos.campanas.map(campana => [
      etiquetas[campana.rol] || campana.rol, campana.nombre,
      campana.budget + " " + datos.plan.moneda, campana.bid + " " + datos.plan.moneda,
    ]));
    contenedor.append(nodo("p", "Presupuesto diario total: " + datos.presupuesto_diario_total + " " + datos.plan.moneda));
    contenedor.append(nodo("h4", "Campañas existentes para estos productos"));
    if (datos.existentes.length) {
      contenedor.append(nodo("p", "Ya existen campañas. Revisa posibles duplicados antes de confirmar."));
      // La API conserva los campos auditables de cada coincidencia sin interpretarlos.
      datos.existentes.forEach(existente => contenedor.append(nodo("pre", JSON.stringify(existente, null, 2))));
    } else contenedor.append(nodo("p", "No se encontraron campañas existentes."));
    ficha(contenedor, [["Lote", datos.lote], ["Huella del plan", datos.huella]]);
    porId("preview").hidden = false;
    porId("preview-titulo").focus();
  }

  // ---------------------------------------------------------------------------
  // Comparador (B.5): tabla por publicacion con economia observada, Ads y
  // disponibilidad. Solo GET /evaluacion; sin escrituras ni escritura a Amazon.
  // ---------------------------------------------------------------------------

  function seleccionadas() {
    return new Set(
      // NodeList no tiene .map en el navegador (si en el mock de Node del test).
      Array.from(porId("productos").querySelectorAll('input[type="checkbox"]:checked'))
        .map(input => Number(input.value)),
    );
  }

  function dinero(monto, moneda) {
    if (monto === null || monto === undefined) return "Sin dato";
    return String(monto) + (moneda ? " " + moneda : "");
  }

  function textoEtiquetaAds(ads) {
    // Precedencia 0.4 §4 presentada tal cual; sin cobertura demostrada no
    // existe otra etiqueta que "Sin datos" (regla cerrada).
    if (!ads || ads.muestra === 0) return "Sin datos";
    const base = etiquetasAds[ads.etiqueta] || "Datos sin etiqueta";
    return base + (ads.provisional ? " (provisional)" : "");
  }

  function textoMuestraMargen(econ) {
    // D4: la muestra limitada se ve APARTE del margen maduro; no entra al sort.
    if (econ && econ.muestra_limitada === true) {
      return "Limitada: " + valor(econ.dias_con_venta) + " días con venta, margen "
        + porcentaje(econ.muestra_margen_neto_pct) + " (no entra al orden)";
    }
    if (!econ || econ.dias_con_venta === null || econ.dias_con_venta === undefined) {
      return "Sin dato";
    }
    return econ.dias_con_venta + " días con venta";
  }

  function textoDisponibilidad(disp) {
    const featured = "Featured Offer: Sin verificar";
    if (!disp || !disp.estado) return "Sin dato. " + featured;
    const base = estadosDisponibilidad[disp.estado] || disp.estado;
    const porFuente = disp.cantidad && typeof disp.cantidad === "object"
      ? Object.keys(disp.cantidad).map(fuente =>
        fuente.toUpperCase() + " " + valor(disp.cantidad[fuente]) + " (desde " + valor((disp.freshness || {})[fuente]) + ")")
      : [];
    return porFuente.length ? base + ": " + porFuente.join(", ") + ". " + featured
      : base + ". " + featured;
  }

  function objetivoComparador(publicaciones) {
    const valores = [...new Set(
      publicaciones.filter(p => p.objetivo_acos_pct !== null && p.objetivo_acos_pct !== undefined)
        .map(p => p.objetivo_acos_pct))];
    if (!valores.length) return "Sin objetivo de comparación";
    return valores.map(v => v + " %").join(", ");
  }

  function pasaFiltro(publicacion, filtro) {
    if (filtro === "con_ads") return publicacion.ads.muestra > 0;
    if (filtro === "sin_datos_ads") return publicacion.ads.muestra === 0;
    if (filtro === "muestra_limitada") return publicacion.economia.muestra_limitada === true;
    return true;
  }

  function renderComparador() {
    const contenedor = porId("comparador-datos");
    contenedor.replaceChildren();
    if (!comparadorDatos) return;
    const publicaciones = comparadorDatos.publicaciones || [];
    ficha(contenedor, [
      ["Ventana Ads", comparadorDatos.ventana_ads
        ? comparadorDatos.ventana_ads.desde + " a " + comparadorDatos.ventana_ads.hasta : "Sin dato"],
      ["Grano de comparación", "Publicación (ASIN + SKU de Amazon), sumas por fila"],
      ["Objetivo ACoS del grupo en preparación", objetivoComparador(publicaciones)],
      ["Muestra limitada", "Visible aparte; no entra al orden (D4)"],
    ]);
    if (!publicaciones.length) {
      contenedor.append(nodo("p", "Sin datos."));
      return;
    }
    const filtro = porId("comparador-filtro").value;
    const visibles = publicaciones.filter(p => pasaFiltro(p, filtro));
    const activas = seleccionadas();
    const envoltura = nodo("div");
    envoltura.className = "fabrica-tabla";
    envoltura.tabIndex = 0;
    envoltura.setAttribute("role", "region");
    envoltura.setAttribute("aria-label", "Comparación de publicaciones ordenada por "
      + porId("comparador-orden").value);
    const tabla = nodo("table"), thead = nodo("thead"), tbody = nodo("tbody"), encabezado = nodo("tr");
    ["Publicación", "Selección", "Margen neto maduro", "Aviso de margen", "Muestra de margen",
      "Ventas totales", "Revenue Ads", "Gasto", "ACoS", "Etiqueta Ads", "CPC", "CVR", "Compras",
      "Muestra Ads", "Disponibilidad", "Objetivo ACoS"].forEach(titulo => {
      const th = nodo("th", titulo);
      th.setAttribute("scope", "col");
      encabezado.append(th);
    });
    thead.append(encabezado);
    visibles.forEach(p => {
      const fila = nodo("tr");
      const th = nodo("th", valor(p.asin) + " / " + valor(p.seller_sku));
      th.setAttribute("scope", "row");
      fila.append(th);
      [activas.has(p.listing_id) ? "Seleccionada" : "—",
        porcentaje(p.economia.margen_neto_pct),
        p.motivos && p.motivos.length ? p.motivos.join(" ") : "—",
        textoMuestraMargen(p.economia),
        dinero(p.economia.venta_total, p.economia.moneda),
        dinero(p.ads.sales30d, p.ads.moneda),
        dinero(p.ads.cost, p.ads.moneda),
        p.ads.acos_pct === null || p.ads.acos_pct === undefined ? "Sin dato" : porcentaje(p.ads.acos_pct),
        textoEtiquetaAds(p.ads),
        p.ads.cpc === null || p.ads.cpc === undefined ? "Sin dato" : dinero(p.ads.cpc, p.ads.moneda),
        p.ads.cvr_pct === null || p.ads.cvr_pct === undefined ? "Sin dato" : porcentaje(p.ads.cvr_pct),
        p.ads.purchases30d === null || p.ads.purchases30d === undefined ? "Sin dato" : String(p.ads.purchases30d),
        p.ads.muestra + (p.ads.muestra === 1 ? " fecha" : " fechas"),
        textoDisponibilidad(p.disponibilidad),
        porcentaje(p.objetivo_acos_pct),
      ].forEach(celda => fila.append(nodo("td", celda)));
      tbody.append(fila);
    });
    tabla.append(thead, tbody);
    envoltura.append(tabla);
    contenedor.append(envoltura);
  }

  async function cargarComparador() {
    const version = ++versionComparador;
    porId("comparador-datos").replaceChildren();
    estado("comparador-estado", "Consultando la comparación…");
    try {
      const datos = await solicitar("/evaluacion?plataforma="
        + encodeURIComponent(porId("plataforma").value)
        + "&orden=" + encodeURIComponent(porId("comparador-orden").value)
        + "&direccion=" + encodeURIComponent(porId("comparador-direccion").value));
      if (version !== versionComparador) return;
      comparadorDatos = datos;
      renderComparador();
      estado("comparador-estado", (datos.publicaciones || []).length
        + " publicaciones. Sin dato al final del orden.");
    } catch (error) {
      if (version === versionComparador) estado("comparador-estado", error.message, true);
    }
  }

  async function cargarCatalogo() {
    const version = ++versionCatalogo;
    invalidar();
    catalogoDisponible = false;
    porId("productos").replaceChildren();
    porId("tipos").replaceChildren();
    porId("moneda").textContent = "—";
    actualizarTotal();
    actualizarBotones();
    estado("catalogo-estado", "Consultando productos…");
    try {
      const datos = await solicitar("/catalogo?plataforma=" + encodeURIComponent(porId("plataforma").value));
      if (version !== versionCatalogo) return;
      porId("moneda").textContent = datos.moneda;
      actualizarTotal();
      datos.tipos_producto.forEach(tipo => { const opcion = nodo("option"); opcion.value = tipo; porId("tipos").append(opcion); });
      datos.productos.forEach(producto => {
        const tarjeta = nodo("div");
        tarjeta.className = "fabrica-producto";
        tarjeta.append(nodo("h4", "Nombre interno: " + valor(producto.nombre)),
          nodo("p", "SKU de Odoo: " + producto.sku));
        const publicaciones = nodo("ul");
        (producto.publicaciones || []).forEach(publicacion => {
          const fila = nodo("li");
          fila.className = "fabrica-publicacion";
          const label = nodo("label"), input = nodo("input");
          input.id = "fabrica-listing-" + publicacion.id;
          label.htmlFor = input.id;
          input.type = "checkbox"; input.value = String(publicacion.id);
          input.disabled = !publicacion.elegible;
          label.append(input, nodo("strong", "Seleccionar publicación " + valor(publicacion.asin)));
          const foto = nodo("div"), imagen = nodo("img"), sinFoto = nodo("span", "Sin foto");
          foto.className = "fabrica-publicacion-foto";
          sinFoto.hidden = true;
          imagen.alt = "Foto de la publicación " + valor(publicacion.asin);
          imagen.width = 96; imagen.height = 96;
          imagen.loading = "lazy"; imagen.decoding = "async";
          imagen.addEventListener("error", () => { imagen.hidden = true; sinFoto.hidden = false; });
          imagen.src = "/api/fabrica/publicaciones/" + encodeURIComponent(publicacion.id) + "/imagen";
          foto.append(imagen, sinFoto);
          fila.append(foto, label);
          fila.append(nodo("span", "ASIN: " + valor(publicacion.asin)),
            nodo("span", "SKU de Amazon: " + valor(publicacion.seller_sku)),
            nodo("span", "Margen neto: " + porcentaje(publicacion.margen_neto_pct)),
            nodo("span", muestraMargen(publicacion)),
            nodo("span", avisoMargen(publicacion)));
          // Los enlaces estan fuera del label: abrir Amazon no selecciona el producto.
          if (/^https:\/\/www\.amazon\.com(?:\.mx)?\/dp\/[A-Za-z0-9]{10}$/.test(publicacion.url || "")) {
            const enlace = nodo("a", "Abrir en Amazon");
            enlace.href = publicacion.url; enlace.target = "_blank"; enlace.rel = "noopener noreferrer";
            enlace.setAttribute("aria-label", "Abrir ASIN " + publicacion.asin + " en Amazon");
            fila.append(enlace);
          }
          publicaciones.append(fila);
        });
        tarjeta.append(publicaciones);
        porId("productos").append(tarjeta);
      });
      catalogoDisponible = true;
      const publicaciones = datos.productos.flatMap(producto => producto.publicaciones || []);
      estado("catalogo-estado", datos.productos.length + " productos. " + publicaciones.filter(p => p.elegible).length + " publicaciones seleccionables.");
    } catch (error) {
      if (version === versionCatalogo) estado("catalogo-estado", error.message, true);
    } finally { if (version === versionCatalogo) actualizarBotones(); }
  }

  function solicitudActual() {
    const parametros = {};
    roles.forEach(rol => {
      parametros[rol] = {
        budget: formulario.elements[rol + "_budget"].value.trim(),
        bid: formulario.elements[rol + "_bid"].value.trim(),
      };
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

  async function revisar(evento) {
    evento.preventDefault();
    if (mutando || consultandoPlan || !catalogoDisponible || !formulario.reportValidity()) return;
    invalidar();
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
      mostrarPreview(datos);
      estado("estado", intentados.has(datos.huella)
        ? "Este plan ya se envió. Consulta el lote; no vuelvas a crear el grupo."
        : "Plan listo para revisar. Todavía no se ha creado ninguna campaña.");
    } catch (error) { if (version === revision) estado("estado", error.message, true); }
    finally { consultandoPlan = false; actualizarBotones(); }
  }

  function seleccionarLote(lote) {
    loteActual = lote;
    detalleDisponible = false;
    versionDetalle += 1;
    porId("lote").hidden = false;
    porId("lote-id").textContent = lote;
    porId("lote-datos").replaceChildren();
    porId("accion-confirmacion").value = "";
    const url = new URL(window.location.href);
    url.searchParams.set("lote", lote);
    window.history.replaceState(null, "", url);
    actualizarBotones();
  }

  function mostrarLote(datos) {
    if (datos.lote !== loteActual) return;
    const contenedor = porId("lote-datos");
    contenedor.replaceChildren();
    ficha(contenedor, [["Plataforma", datos.plataforma], ["Estado", estadosLote[datos.estado] || datos.estado], ["Detalle", datos.detalle],
      ["Creado", datos.created_at], ["Finalizado", datos.finished_at]]);
    tabla(contenedor, ["Paso", "Rol", "Recurso", "ID externo", "Estado", "Verificación de creación"], datos.pasos.map(paso => [
      paso.orden, etiquetas[paso.rol] || paso.rol, paso.recurso, paso.external_id,
      estadosPaso[paso.estado] || paso.estado, paso.readback_estado,
    ]));
    if (datos.plan) {
      const detalle = nodo("details"); detalle.append(nodo("summary", "Ver plan persistido del lote"));
      mostrarPlan(detalle, datos.plan);
      if (datos.plan.parametros) tabla(detalle, ["Rol", "Presupuesto diario", "Puja inicial"], roles.map(rol => [
        etiquetas[rol], datos.plan.parametros[rol].budget + " " + datos.plan.moneda,
        datos.plan.parametros[rol].bid + " " + datos.plan.moneda,
      ]));
      contenedor.append(detalle);
    }
    detalleDisponible = true;
    actualizarBotones();
  }

  async function cargarLote() {
    if (!loteActual) return;
    const lote = loteActual, version = ++versionDetalle;
    estado("lote-estado", "Consultando el lote…");
    try {
      const datos = await solicitar("/lotes/" + encodeURIComponent(lote));
      if (version !== versionDetalle) return;
      mostrarLote(datos); estado("lote-estado", "Estado actualizado.");
    } catch (error) { if (version === versionDetalle) estado("lote-estado", error.message, true); }
  }

  async function cargarHistorial() {
    const version = ++versionHistorial;
    estado("historial-estado", "Consultando historial…");
    porId("historial").replaceChildren();
    try {
      const datos = await solicitar("/lotes?plataforma=" + encodeURIComponent(porId("plataforma").value));
      if (version !== versionHistorial) return;
      const lista = nodo("ul");
      datos.items.forEach(lote => {
        const fila = nodo("li"), enlace = nodo("a", lote.lote + " · " + (estadosLote[lote.estado] || lote.estado) + " · " + lote.created_at);
        enlace.href = "/campanas/nuevas?lote=" + encodeURIComponent(lote.lote);
        enlace.addEventListener("click", evento => {
          evento.preventDefault(); if (mutando) return;
          seleccionarLote(lote.lote); cargarLote(); porId("lote-titulo").focus();
        });
        fila.append(enlace); lista.append(fila);
      });
      porId("historial").append(lista);
      estado("historial-estado", datos.items.length ? "Selecciona un lote para ver el detalle." : "Todavía no hay lotes para esta plataforma.");
    } catch (error) { if (version === versionHistorial) estado("historial-estado", error.message, true); }
  }

  function tomarToken() {
    const token = porId("token").value;
    if (!token.trim()) throw new Error("Escribe el token de Orbit para confirmar la operación.");
    porId("token").value = "";
    return token;
  }

  async function crear(evento) {
    evento.preventDefault();
    if (mutando || !preview || intentados.has(preview.huella)) return;
    if (porId("confirmacion").value !== "CREAR 5 CAMPAÑAS") {
      estado("estado", "Escribe exactamente CREAR 5 CAMPAÑAS para confirmar.", true); return;
    }
    let token;
    try { token = tomarToken(); } catch (error) { estado("estado", error.message, true); return; }
    const datos = preview;
    intentados.add(datos.huella);
    seleccionarLote(datos.lote);
    mutando = true; actualizarBotones();
    porId("confirmacion").value = "";
    estado("estado", "Creando el grupo. Conserva esta página para consultar el lote.");
    estado("lote-estado", "Creación en curso…");
    let tokenRechazado = false;
    try {
      const resultado = await solicitar("/crear", {
        solicitud: datos.solicitud, huella: datos.huella, confirmacion: "CREAR 5 CAMPAÑAS",
      }, token);
      versionDetalle += 1;
      mostrarLote(resultado);
      estado("estado", "Solicitud finalizada. Revisa el estado y los pasos del lote.");
      estado("lote-estado", "Resultado recibido: " + (estadosLote[resultado.estado] || resultado.estado));
    } catch (error) {
      versionDetalle += 1;
      if (error.status === 401) {
        intentados.delete(datos.huella);
        tokenRechazado = true;
        estado("estado", "Token rechazado. Corrígelo y confirma de nuevo el plan revisado.", true);
        estado("lote-estado", "La solicitud fue rechazada antes de crear el lote.", true);
        return;
      }
      if (error.lote && error.lote !== loteActual) seleccionarLote(error.lote);
      estado("estado", error.message + " Conserva el lote y consulta su estado; no se reintentará la creación.", true);
      estado("lote-estado", "Resultado pendiente de consultar. " + error.message, true);
    } finally {
      token = null; mutando = false;
      if (tokenRechazado) actualizarBotones();
      else invalidar();
      cargarHistorial();
    }
  }

  async function ejecutarAccion(evento) {
    evento.preventDefault();
    if (mutando || !loteActual || !detalleDisponible) return;
    const accion = porId("accion-tipo").value, confirmacion = porId("accion-confirmacion").value;
    if (!confirmaciones[accion] || confirmacion !== confirmaciones[accion]) {
      estado("lote-estado", "Escribe exactamente la frase de la acción seleccionada.", true); return;
    }
    let token;
    try { token = tomarToken(); } catch (error) { estado("lote-estado", error.message, true); return; }
    mutando = true; versionDetalle += 1; actualizarBotones();
    porId("accion-confirmacion").value = "";
    estado("lote-estado", "Ejecutando " + accion + "…");
    try {
      const resultado = await solicitar("/lotes/" + encodeURIComponent(loteActual) + "/" + accion, {confirmacion}, token);
      versionDetalle += 1;
      mostrarLote(resultado);
      estado("lote-estado", "Acción finalizada. Revisa el estado y los pasos del lote.");
    } catch (error) {
      versionDetalle += 1;
      estado("lote-estado", error.message + " Consulta el estado antes de repetir la acción.", true);
    }
    finally { token = null; mutando = false; actualizarBotones(); cargarHistorial(); }
  }

  formulario.addEventListener("input", invalidar);
  formulario.addEventListener("change", invalidar);
  formulario.addEventListener("submit", revisar);
  porId("objetivo-origen").addEventListener("change", actualizarObjetivoManual);
  porId("crear").addEventListener("submit", crear);
  porId("accion").addEventListener("submit", ejecutarAccion);
  porId("accion-tipo").addEventListener("change", () => { porId("accion-confirmacion").value = ""; });
  porId("plataforma").addEventListener("change", () => {
    roles.forEach(rol => {
      formulario.elements[rol + "_budget"].value = "";
      formulario.elements[rol + "_bid"].value = "";
    });
    cargarCatalogo(); cargarHistorial(); cargarComparador();
  });
  porId("recargar-catalogo").addEventListener("click", cargarCatalogo);
  porId("historial-recargar").addEventListener("click", cargarHistorial);
  porId("lote-recargar").addEventListener("click", cargarLote);
  porId("comparador-recargar").addEventListener("click", cargarComparador);
  porId("comparador-orden").addEventListener("change", cargarComparador);
  porId("comparador-direccion").addEventListener("change", cargarComparador);
  // El filtro no consulta de nuevo: la seleccion del catalogo queda intacta (AC10).
  porId("comparador-filtro").addEventListener("change", renderComparador);
  window.addEventListener("pagehide", () => { porId("token").value = ""; });
  actualizarObjetivoManual();
  cargarCatalogo(); cargarHistorial(); cargarComparador();
  const lote = new URL(window.location.href).searchParams.get("lote");
  if (lote) { seleccionarLote(lote); cargarLote(); }
});
