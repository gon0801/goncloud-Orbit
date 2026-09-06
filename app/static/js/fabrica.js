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

  function mostrarPlan(contenedor, plan) {
    ficha(contenedor, [
      ["Grupo", plan.nombre_base], ["Tipo de producto", plan.tipo_producto],
      ["Plataforma / moneda", plan.platform + " / " + plan.moneda], ["Fecha del plan", plan.fecha],
      ["Modo del optimizador", plan.modo], ["Target ACoS aplicado", porcentaje(plan.target_acos_pct)],
      ["Target derivado del margen", porcentaje(plan.target_derivado_pct)],
      ["Fracción del margen", plan.fraccion], ["Procedencia del target", plan.target_procedencia],
    ]);
    contenedor.append(nodo("h4", "Productos y márgenes netos"));
    tabla(contenedor, ["SKU", "SKU de Amazon", "ASIN", "Margen neto"], plan.productos.map(producto => [
      producto.odoo_sku, producto.seller_sku, producto.asin, porcentaje(producto.margen_neto_pct),
    ]));
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
        const tarjeta = nodo("div"), label = nodo("label"), input = nodo("input");
        tarjeta.className = "fabrica-producto";
        input.id = "fabrica-producto-" + producto.id;
        label.htmlFor = input.id;
        input.type = "checkbox"; input.value = String(producto.id); input.disabled = !producto.elegible;
        label.append(input, nodo("strong", "Nombre interno: " + valor(producto.nombre)));
        tarjeta.append(label, nodo("p", "SKU de Odoo: " + producto.sku),
          nodo("p", "Margen neto: " + porcentaje(producto.margen_neto_pct)));
        if (!producto.elegible) tarjeta.append(nodo("p", "No elegible: " + valor(producto.motivo)));
        const publicaciones = nodo("ul");
        (producto.publicaciones || []).forEach(publicacion => {
          const fila = nodo("li");
          fila.className = "fabrica-publicacion";
          const foto = nodo("div"), imagen = nodo("img"), sinFoto = nodo("span", "Sin foto");
          foto.className = "fabrica-publicacion-foto";
          sinFoto.hidden = true;
          imagen.alt = "Foto de la publicación " + valor(publicacion.asin);
          imagen.width = 96; imagen.height = 96;
          imagen.loading = "lazy"; imagen.decoding = "async";
          imagen.addEventListener("error", () => { imagen.hidden = true; sinFoto.hidden = false; });
          imagen.src = "/api/fabrica/publicaciones/" + encodeURIComponent(publicacion.id) + "/imagen";
          foto.append(imagen, sinFoto);
          fila.append(foto);
          fila.append(nodo("span", "ASIN: " + valor(publicacion.asin)),
            nodo("span", "SKU de Amazon: " + valor(publicacion.seller_sku)));
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
      estado("catalogo-estado", datos.productos.length + " productos. " + datos.productos.filter(p => p.elegible).length + " elegibles.");
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
    return {
      plataforma: porId("plataforma").value, tipo_producto: porId("tipo").value.trim(),
      nombre_base: porId("nombre").value.trim(), modo: porId("modo").value,
      productos: Array.from(porId("productos").querySelectorAll('input[type="checkbox"]:checked')).map(input => Number(input.value)),
      parametros,
    };
  }

  async function revisar(evento) {
    evento.preventDefault();
    if (mutando || consultandoPlan || !catalogoDisponible || !formulario.reportValidity()) return;
    invalidar();
    const solicitud = solicitudActual();
    if (!solicitud.productos.length) { estado("estado", "Selecciona al menos un producto elegible.", true); return; }
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
  porId("crear").addEventListener("submit", crear);
  porId("accion").addEventListener("submit", ejecutarAccion);
  porId("accion-tipo").addEventListener("change", () => { porId("accion-confirmacion").value = ""; });
  porId("plataforma").addEventListener("change", () => {
    roles.forEach(rol => {
      formulario.elements[rol + "_budget"].value = "";
      formulario.elements[rol + "_bid"].value = "";
    });
    cargarCatalogo(); cargarHistorial();
  });
  porId("recargar-catalogo").addEventListener("click", cargarCatalogo);
  porId("historial-recargar").addEventListener("click", cargarHistorial);
  porId("lote-recargar").addEventListener("click", cargarLote);
  window.addEventListener("pagehide", () => { porId("token").value = ""; });
  cargarCatalogo(); cargarHistorial();
  const lote = new URL(window.location.href).searchParams.get("lote");
  if (lote) { seleccionarLote(lote); cargarLote(); }
});
