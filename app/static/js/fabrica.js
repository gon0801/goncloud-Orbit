"use strict";
// Cliente de fabrica: un plan vigente, sin persistir secretos ni reintentar mutaciones.
document.addEventListener("DOMContentLoaded", function () {
  const porId = nombre => document.getElementById("fabrica-" + nombre);
  const formulario = porId("plan");
  if (!formulario) return;
  const roles = ["category_exact", "category_phrase", "category_broad", "product_targeting", "auto_discovery"];
  const confirmaciones = {
    pausar: "PAUSAR GRUPO", reconciliar: "RECONCILIAR GRUPO", registrar: "REGISTRAR GRUPO",
  };
  // Comparador (ORBIT 19 B.5): SOLO presenta lo que devuelve /evaluacion.
  // El orden lo resuelve la API (null al final); el filtro es de presentacion
  // y jamas toca la seleccion del catalogo (AC10). Cero escrituras.
  const nombreRol = codigo => Orbit.etiqueta(codigo, "rol_campana");
  let revision = 0;
  let versionCatalogo = 0;
  let versionHistorial = 0;
  let versionDetalle = 0;
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
  let loteActual = null;
  let detalleDisponible = false;
  let lotePlan = null, lotePlataforma = null, planRetiro = null, retiroElegible = false;
  let versionComparador = 0;
  let comparadorDatos = null;
  const intentados = new Set();
  let temporizadorComparador = null;

  function nodo(tag, texto) {
    const elemento = document.createElement(tag);
    if (texto !== undefined) elemento.textContent = String(texto);
    return elemento;
  }

  function conClase(tag, clase, texto) {
    const elemento = nodo(tag, texto);
    elemento.className = clase;
    return elemento;
  }

  function chip(texto, tono = "neutro", detalle = texto) {
    const elemento = conClase("span", "fabrica-chip fabrica-chip-" + tono, texto);
    elemento.title = detalle;
    return elemento;
  }

  function fotoPublicacion(id, asin, tamano = 72) {
    const foto = conClase("span", "fabrica-publicacion-foto");
    const imagen = nodo("img"), sinFoto = nodo("span", "Sin foto");
    sinFoto.hidden = true;
    imagen.alt = "Foto de la publicación " + valor(asin);
    imagen.width = tamano; imagen.height = tamano;
    imagen.loading = "lazy"; imagen.decoding = "async";
    imagen.addEventListener("error", () => { imagen.hidden = true; sinFoto.hidden = false; });
    imagen.src = "/api/fabrica/publicaciones/" + encodeURIComponent(id) + "/imagen";
    foto.append(imagen, sinFoto);
    return foto;
  }

  function tonoMargen(margen) {
    if (margen === null || margen === undefined) return "fabrica-sin-dato";
    return Number(margen) > 0 ? "fabrica-positivo" : "fabrica-negativo";
  }

  function chipsPublicacion(publicacion) {
    const contenedor = conClase("span", "fabrica-chips");
    const detalle = avisoMargen(publicacion), margen = publicacion.margen_neto_pct;
    if (!publicacion.elegible) contenedor.append(chip("Identidad incompleta", "negativo", detalle));
    if (margen === null || margen === undefined) contenedor.append(chip("Margen sin medir", "negativo", detalle));
    else if (Number(margen) <= 0) contenedor.append(chip(Number(margen) === 0 ? "Margen cero" : "Margen negativo", "negativo", detalle));
    else contenedor.append(chip("Margen medido", "positivo", detalle));
    if (publicacion.dias_con_venta !== null && publicacion.dias_con_venta !== undefined
      && Number(publicacion.dias_con_venta) < 30) contenedor.append(chip("Muestra limitada", "aviso", muestraMargen(publicacion)));
    if (publicacion.historial_ads === null) contenedor.append(chip("Historial Ads sin dato", "neutro", detalle));
    return contenedor;
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

  const ausente = dato => dato === null || dato === undefined;
  function valor(dato) { return ausente(dato) ? "Sin dato" : String(dato); }
  function porcentaje(dato) { return ausente(dato) ? "Sin dato" : Orbit.pct(dato); }
  function fechaHora(dato) { return ausente(dato) ? "Sin dato" : Orbit.fechaHora(dato); }
  // Fraccion (1 = todo) como porcentaje.
  function fraccion(dato) { return ausente(dato) ? "Sin dato" : Orbit.pct(Number(dato) * 100); }

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
    porId("bids-amazon").disabled = mutando || consultandoPlan || consultandoBids || !catalogoDisponible;
    porId("crear-boton").disabled = mutando || !preview || intentados.has(preview.huella);
    porId("recuperacion").disabled = mutando || !detalleDisponible;
    porId("lote-recargar").disabled = !loteActual;
    porId("historial-recargar").disabled = mutando;
    porId("retiro-boton").disabled = mutando || !detalleDisponible || !retiroElegible;
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
      : dinero((centavos / 100n) + "." + String(centavos % 100n).padStart(2, "0"), moneda);
  }

  function invalidar(conservarSugerencias = false) {
    actualizarTotal();
    revision += 1;
    if (preview !== null) {
      // El objetivo derivado del margen vivia en el preview que se descarta:
      // el comparador no puede conservar etiquetas calculadas con el (hallazgo
      // cross-review 2026-09-07, 2a ronda). Debounce: invalidar dispara con
      // cada cambio del formulario, la consulta va una sola vez.
      programarComparador();
    }
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
    return (limitada ? "Muestra limitada: " : "Muestra de margen: ") + Orbit.numero(dias)
      + " días con venta, del " + desde + " hasta antes del " + hasta + ".";
  }

  function exclusiones(codigos) {
    return codigos.map(codigo => Orbit.etiqueta(codigo, "exclusion_estimacion")).join(", ");
  }

  // La procedencia la arma app/fabrica_plan.py con el calculo crudo
  // («margen_minimo_grupo: 0.5 x 42.157… = 21.078… -> redondeo NUMERIC(6,2) = 21.08»):
  // se lee con el origen traducido y cifras de 2 decimales.
  function textoProcedencia(texto) {
    if (ausente(texto)) return "Sin dato";
    const corte = String(texto).indexOf(":");
    const origen = corte < 0 ? String(texto) : String(texto).slice(0, corte);
    const calculo = corte < 0 ? "" : String(texto).slice(corte + 1)
      .replace(/ -> clamp /g, " → acotado a la banda ")
      .replace(/ -> redondeo NUMERIC\(6,2\)/g, " → redondeado")
      .replace(/\d+\.\d{3,}/g, cifra => Orbit.numero(cifra, 2));
    return Orbit.etiqueta(origen, "origen_objetivo") + (calculo ? ":" + calculo : "");
  }

  function mostrarPlan(contenedor, plan) {
    const esV2 = plan.schema_version === 2;
    const objetivo = esV2 ? plan.objetivo : null;
    const target = esV2 ? objetivo.acos_pct : plan.target_acos_pct;
    // A1-r2/F5: el plan registra si sus goals fijan el numero; ausente en
    // lotes viejos (congelado entonces). Solo `false` muestra la linea.
    const fijan = plan.goals_fijan_target !== false;
    const aplicado = fijan ? porcentaje(target)
      : "target: margen de la plataforma (se ajusta cada ciclo)";
    ficha(contenedor, [
      ["Grupo", plan.nombre_base], ["Tipo de producto", plan.tipo_producto],
      ["Plataforma / moneda", Orbit.etiqueta(plan.platform, "plataforma") + " / " + plan.moneda], ["Fecha del plan", plan.fecha],
      ["Modo del optimizador", Orbit.etiqueta(plan.modo, "modo")], ["Target ACoS aplicado", aplicado],
      ["Origen del objetivo", Orbit.etiqueta(esV2 ? objetivo.origen : "margen_medido", "origen_objetivo")],
      ["Target derivado del margen", esV2 ? porcentaje(objetivo.derivado) : porcentaje(plan.target_derivado_pct)],
      ["Fracción del margen", esV2 ? objetivo.fraccion : plan.fraccion],
      ["Procedencia del target", textoProcedencia(esV2 ? objetivo.procedencia : plan.target_procedencia)],
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

  // Presentacion de semillas por campana. El reparto rol -> semilla vive en
  // app/fabrica_plan.py::expresiones_bid (fuente unica); aqui solo se presenta
  // lo que ya trae el preview, con textContent y sin inventar semillas.
  function semillasDeCampana(semillas, rol) {
    if (rol === "auto_discovery") return ["Amazon crea los grupos automáticos; sin semillas manuales."];
    if (rol === "category_exact") return (semillas.exact || []).map(semilla => semilla + " (exacta)");
    if (rol === "category_phrase") return (semillas.keywords || []).map(semilla => semilla + " (frase)");
    if (rol === "category_broad") return (semillas.keywords || []).map(semilla => semilla + " (amplia)");
    if (rol === "product_targeting") return [...(semillas.asins || [])];
    return [];
  }

  function mostrarPreview(datos) {
    const contenedor = porId("preview-datos");
    contenedor.replaceChildren();
    // A2: el plan avisa cuando el grupo mezcla familias (regla operativa:
    // no mezclar familias de margen distinto en una misma campana).
    if (datos.advertencia_mezcla) {
      const aviso = nodo("p", "Advertencia: " + datos.advertencia_mezcla);
      aviso.setAttribute("role", "alert");
      contenedor.append(aviso);
    }
    mostrarPlan(contenedor, datos.plan);
    contenedor.append(nodo("h4", "Las cinco campañas"));
    tabla(contenedor, ["Rol", "Nombre", "Presupuesto diario", "Puja predeterminada del grupo", "Fuente"], datos.campanas.map(campana => [
      nombreRol(campana.rol), campana.nombre,
      dinero(campana.budget, datos.plan.moneda), dinero(campana.bid, datos.plan.moneda),
      campana.fuente_bid === "amazon_v4" ? "Amazon sugerido" : "Manual",
    ]));
    contenedor.append(nodo("h4", "Semillas y puja por campaña"));
    datos.campanas.forEach(campana => {
      contenedor.append(nodo("h5", nombreRol(campana.rol)));
      const semillas = semillasDeCampana(datos.plan.semillas || {}, campana.rol);
      if (!semillas.length) contenedor.append(nodo("p", "0 semillas para esta campaña."));
      else {
        const lista = nodo("ul");
        semillas.forEach(semilla => lista.append(nodo("li", semilla)));
        contenedor.append(lista);
      }
      // La repeticion por campana se deriva del preview firmado (datos.bids
      // trae fuente por expresion): sobrevive sin estado de sesion. El aviso
      // manual de cero sugerencias sigue usando la consulta de la sesion.
      let linea = "Puja inicial: " + (campana.fuente_bid === "amazon_v4" ? "Amazon sugerido" : "Manual");
      if (campana.fuente_bid === "amazon_v4") {
        const objetivos = ((datos.bids || {})[campana.rol] || []).filter(r => r.fuente === "promedio_rol");
        const todos = ((datos.bids || {})[campana.rol] || []).length;
        if (objetivos.length) {
          linea += ". Amazon no devolvió sugerencia para " + objetivos.length
            + " de " + todos + ": usan el promedio del rol ("
            + dinero(objetivos[0].sugerido, datos.plan.moneda) + ")";
        }
      } else if (pujaManualSinSugerencia.has(campana.rol)) {
        linea += ". Amazon no devolvió sugerencia: puja manual";
      }
      contenedor.append(nodo("p", linea));
    });
    mostrarDetalleBids(contenedor, datos.bids, datos.plan.moneda);
    contenedor.append(nodo("p", "Presupuesto diario total: " + dinero(datos.presupuesto_diario_total, datos.plan.moneda)));
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

  // Aviso junto al campo de puja (F4 + promedio del rol 2026-09-09):
  // cero sugerencias => puja manual; parcial => N de M con el promedio.
  // Sin consulta no hay aviso, y un override manual de una sugerencia
  // recibida tampoco lo genera.
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

  // El valor (keyword o ASIN) es dato libre y va tal cual.
  function objetivoPuja(r) {
    return Orbit.etiqueta(r.tipo, "objetivo_puja") + (r.valor ? ": " + r.valor : "");
  }

  function mostrarDetalleBids(contenedor, datosPorRol, moneda, incluirFaltantes = false) {
    if (!datosPorRol) return;
    const conDatos = roles.filter(rol => {
      const datos = datosPorRol[rol];
      const recomendaciones = Array.isArray(datos) ? datos : (datos && datos.recomendaciones) || [];
      const faltantes = Array.isArray(datos && datos.faltantes) ? datos.faltantes : [];
      return recomendaciones.length || (incluirFaltantes && faltantes.length);
    });
    if (!conDatos.length) return;
    contenedor.append(nodo("h4", "Detalle de pujas sugeridas por Amazon"));
    conDatos.forEach(rol => {
      const datos = datosPorRol[rol];
      const recomendaciones = Array.isArray(datos) ? datos : (datos.recomendaciones || []);
      contenedor.append(nodo("h5", nombreRol(rol)));
      if (recomendaciones.length) tabla(
        contenedor,
        ["Objetivo", "Mínimo", "Sugerido", "Máximo", "Puja que se usa"],
        recomendaciones.map(r => [
          objetivoPuja(r),
          dinero(r.minimo, moneda),
          dinero(r.sugerido, moneda),
          dinero(r.maximo, moneda),
          ausente(r.bid_efectivo) ? "Captura manual" : dinero(r.bid_efectivo, moneda),
        ]),
      );
      const faltantes = Array.isArray(datos && datos.faltantes) ? datos.faltantes : [];
      if (incluirFaltantes && faltantes.length) {
        contenedor.append(conClase("p", "alerta", "Captura manual requerida. Amazon no sugirió: "
          + faltantes.map(objetivoPuja).join(", ") + "."));
      }
    });
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
    if (ausente(monto)) return "Sin dato";
    return Orbit.dinero(monto) + (moneda ? " " + moneda : "");
  }

  function textoEtiquetaAds(ads) {
    // Precedencia 0.4 §4 presentada tal cual; sin cobertura demostrada no
    // existe otra etiqueta que "Sin datos" (regla cerrada).
    if (!ads || ads.muestra === 0) return "Sin datos";
    const base = ads.etiqueta ? Orbit.etiqueta(ads.etiqueta, "etiqueta_ads") : "Datos sin etiqueta";
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
    const featured = "Oferta destacada: sin verificar";
    if (!disp || !disp.estado) return "Sin dato. " + featured;
    const base = Orbit.etiqueta(disp.estado, "estado_disponibilidad");
    const porFuente = disp.cantidad && typeof disp.cantidad === "object"
      ? Object.keys(disp.cantidad).map(fuente =>
        fuente.toUpperCase() + " " + (ausente(disp.cantidad[fuente]) ? "Sin dato" : Orbit.numero(disp.cantidad[fuente]))
          + " (desde " + fechaHora((disp.freshness || {})[fuente]) + ")")
      : [];
    return porFuente.length ? base + ": " + porFuente.join(", ") + ". " + featured
      : base + ". " + featured;
  }

  function objetivoComparador(publicaciones) {
    const valores = [...new Set(
      publicaciones.filter(p => p.objetivo_acos_pct !== null && p.objetivo_acos_pct !== undefined)
        .map(p => p.objetivo_acos_pct))];
    if (!valores.length) return "Sin objetivo de comparación";
    return valores.map(Orbit.pct).join(", ");
  }

  // Ficha del margen (hallazgo cross-review 2026-09-07): el payload trae por
  // publicacion ventana/cobertura/ledger_fresco_at en economia; si varia entre
  // publicaciones se DECLARA, no se inventa un valor unico.
  function valorUnicoMargen(publicaciones, sacar) {
    const valores = [...new Set(
      publicaciones.map(p => (p.economia ? sacar(p.economia) : null))
        .filter(v => v !== null && v !== undefined))];
    if (!valores.length) return null;  // ficha lo pinta como "Sin dato"
    // Al variar se DECLARA y se listan los valores para consultarlos sin
    // salir de la ficha (observacion cross-review 2026-09-07, 2a ronda).
    return valores.length === 1 ? valores[0] : "Varia por publicacion: " + valores.join("; ");
  }

  function ventanaMargen(publicaciones) {
    const pares = [...new Set(
      publicaciones
        .filter(p => p.economia && p.economia.ventana_desde && p.economia.ventana_hasta)
        .map(p => p.economia.ventana_desde + " a " + p.economia.ventana_hasta))];
    if (!pares.length) return null;
    return pares.length === 1 ? pares[0] : "Varia por publicacion: " + pares.join("; ");
  }

  function pasaFiltro(publicacion, filtro) {
    if (filtro === "con_ads") return publicacion.ads.muestra > 0;
    if (filtro === "sin_datos_ads") return publicacion.ads.muestra === 0;
    if (filtro === "muestra_limitada") return publicacion.economia.muestra_limitada === true;
    return true;
  }

  function celdaNumero(texto, clase = "") {
    return conClase("td", "num " + clase + (texto === "Sin dato" ? " fabrica-sin-dato" : ""), texto);
  }

  function celdaDinero(monto, moneda) {
    const celda = celdaNumero(dinero(monto, moneda));
    celda.title = ausente(monto) ? "Sin dato" : Orbit.numero(monto, 4) + (moneda ? " " + moneda : "");
    return celda;
  }

  function celdaMuestra(economia) {
    const celda = conClase("td", "fabrica-muestra");
    if (economia.muestra_limitada === true) {
      celda.append(chip("Limitada", "aviso", textoMuestraMargen(economia)),
        conClase("span", "fabrica-apoyo", valor(economia.dias_con_venta)
          + " días con venta, margen " + porcentaje(economia.muestra_margen_neto_pct)));
    } else celda.append(nodo("span", textoMuestraMargen(economia)));
    return celda;
  }

  function celdaDisponibilidad(disp) {
    const celda = conClase("td", "fabrica-disponibilidad");
    const estadoStock = disp && disp.estado;
    const etiqueta = estadoStock ? Orbit.etiqueta(estadoStock, "estado_disponibilidad") : "Sin dato";
    const tono = estadoStock === "positivo" ? "positivo" : estadoStock === "cero" ? "negativo" : "neutro";
    const detalle = textoDisponibilidad(disp);
    celda.append(chip(etiqueta, tono, detalle),
      conClase("span", "fabrica-apoyo", detalle.slice(etiqueta.length).replace(/^[:.]\s*/, "")));
    return celda;
  }

  function textoMotivoEstimacion(codigo) {
    return Orbit.etiqueta(codigo, "motivo_estimacion");
  }

  function textosMotivosEstimacion(motivos) {
    return (motivos || []).map(textoMotivoEstimacion);
  }

  function piezasEstimacion(est) {
    if (!est) return null;
    const escenario = est.escenario || {};
    const principal = conClase("div", "fabrica-estimacion");
    principal.setAttribute("aria-label", "Contribución estimada por venta · antes de Ads");
    principal.append(
      conClase("span", "fabrica-estimacion-titulo", "Contribución estimada por venta"),
      conClase("span", "fabrica-estimacion-subtitulo", "Antes de Ads"),
    );
    if (est.estado === "disponible") {
      principal.append(conClase("strong", tonoMargen(est.contribucion), dinero(est.contribucion, est.moneda)));
      const meta = [];
      if (est.contribucion_pct !== null && est.contribucion_pct !== undefined) {
        meta.push(porcentaje(est.contribucion_pct));
      }
      if (est.base_porcentaje) meta.push("sobre " + Orbit.etiqueta(est.base_porcentaje, "componente_estimacion").toLowerCase());
      if (escenario.unidad) meta.push("unidad " + escenario.unidad);
      if (escenario.fecha_valoracion) meta.push(escenario.fecha_valoracion);
      if (escenario.canal) meta.push(String(escenario.canal).toUpperCase());
      if (meta.length) principal.append(conClase("span", "fabrica-apoyo", meta.join(" · ")));
    } else {
      principal.append(conClase("strong", "fabrica-sin-dato",
        est.estado ? Orbit.etiqueta(est.estado, "estado_estimacion") : "Sin dato"));
      const textos = textosMotivosEstimacion(est.motivos);
      if (textos.length) {
        principal.append(conClase("span", "fabrica-apoyo", textos.join(" · ")));
      }
    }
    if (est.exclusiones && est.exclusiones.length) {
      principal.append(conClase("span", "fabrica-apoyo", "Sin contar: " + exclusiones(est.exclusiones)));
    }
    const detalle = conClase("details", "fabrica-estimacion-detalle");
    detalle.append(nodo("summary", "Ver desglose de la estimación"));
    (est.componentes || []).forEach(componente => {
      const linea = conClase("div", "fabrica-estimacion-componente");
      const meta = [
        componente.fuente ? Orbit.etiqueta(componente.fuente, "fuente_ingesta") : null,
        componente.fecha_fuente ? ("fecha " + componente.fecha_fuente) : null,
        componente.observed_at ? ("captura " + Orbit.fechaHora(componente.observed_at)) : null,
        componente.vigencia ? ("vigencia " + componente.vigencia) : null,
        componente.estado ? ("estado " + Orbit.etiqueta(componente.estado, "estado_estimacion").toLowerCase()) : null,
        componente.pertenencia === true ? "pertenece al total"
          : componente.pertenencia === false ? "fuera del total" : null,
      ].filter(Boolean).join(" · ");
      linea.append(conClase("strong", "fabrica-estimacion-componente-nombre",
        ausente(componente.nombre) ? "Sin dato" : Orbit.etiqueta(componente.nombre, "componente_estimacion")));
      if (meta) linea.append(conClase("span", "fabrica-apoyo", meta));
      // El tipo de cambio necesita sus 4 decimales; lo demas es dinero.
      const importe = monto => ausente(monto) ? "Sin dato"
        : componente.nombre === "fx" ? Orbit.numero(monto, 4) : Orbit.dinero(monto);
      linea.append(
        conClase("span", "fabrica-apoyo", "Original: " + importe(componente.importe_original)
          + (componente.moneda_original ? " " + componente.moneda_original : "")),
        conClase("span", "fabrica-apoyo", "En moneda de venta: " + importe(componente.importe_normalizado)
          + (componente.moneda_normalizada ? " " + componente.moneda_normalizada : "")),
      );
      detalle.append(linea);
    });
    const textosDetalle = textosMotivosEstimacion(est.motivos);
    if (textosDetalle.length) {
      detalle.append(conClase("span", "fabrica-apoyo", "Motivos: " + textosDetalle.join(" · ")));
    }
    if (est.exclusiones && est.exclusiones.length) {
      detalle.append(conClase("span", "fabrica-apoyo", "Sin contar: " + exclusiones(est.exclusiones)));
    }
    const versiones = [];
    if (escenario.version_formula) versiones.push(escenario.version_formula);
    if (escenario.version_politica !== null && escenario.version_politica !== undefined) {
      versiones.push("politica " + escenario.version_politica);
    }
    if (escenario.unidad) versiones.push("unidad " + escenario.unidad);
    if (versiones.length) detalle.append(conClase("span", "fabrica-apoyo", versiones.join(" · ")));
    if (est.detalle) {
      const partes = ["Número congelado (no actual): " + dinero(est.detalle.contribucion)];
      if (!ausente(est.detalle.contribucion_pct)) partes.push(porcentaje(est.detalle.contribucion_pct));
      if (est.detalle.estado) partes.push(Orbit.etiqueta(est.detalle.estado, "estado_estimacion"));
      detalle.append(conClase("span", "fabrica-apoyo", partes.join(" · ")));
    }
    return {principal, detalle};
  }

  function renderComparador() {
    const contenedor = porId("comparador-datos");
    contenedor.replaceChildren();
    if (!comparadorDatos) return;
    const publicaciones = comparadorDatos.publicaciones || [];
    const metadatos = conClase("dl", "fabrica-metadatos");
    [
      ["Ventana Ads", comparadorDatos.ventana_ads
        ? comparadorDatos.ventana_ads.desde + " a " + comparadorDatos.ventana_ads.hasta : "Sin dato"],
      ["Grano de comparación", "Publicación (ASIN + SKU de Amazon), sumas por fila"],
      ["Objetivo ACoS del grupo en preparación", objetivoComparador(publicaciones)],
      ["Ventana del margen", ventanaMargen(publicaciones)],
      ["Cobertura del margen", valorUnicoMargen(publicaciones, e => ausente(e.cobertura) ? null : fraccion(e.cobertura))],
      ["Actualización del margen", valorUnicoMargen(publicaciones, e => ausente(e.ledger_fresco_at) ? null : fechaHora(e.ledger_fresco_at))],
    ].forEach(([nombre, dato]) => {
      const par = nodo("div");
      par.append(nodo("dt", nombre), nodo("dd", valor(dato)));
      metadatos.append(par);
    });
    contenedor.append(metadatos);
    if (!publicaciones.length) {
      contenedor.append(nodo("p", "Sin datos."));
      return;
    }
    const visibles = publicaciones.filter(p => pasaFiltro(p, porId("comparador-filtro").value));
    const activas = seleccionadas();
    const envoltura = conClase("div", "fabrica-tabla fabrica-comparacion-scroll");
    envoltura.tabIndex = 0;
    envoltura.setAttribute("role", "region");
    const orden = porId("comparador-orden");
    const opcion = orden.options && orden.options[orden.selectedIndex];
    envoltura.setAttribute("aria-label", "Comparación de publicaciones ordenada por "
      + (opcion ? opcion.text.toLowerCase() : orden.value));
    const tabla = conClase("table", "fabrica-comparacion");
    const thead = nodo("thead"), tbody = nodo("tbody"), grupos = nodo("tr"), encabezado = nodo("tr");
    [["Publicación", 1, 2], ["Margen observado · antes de Ads", 3, 1],
      ["Ads · 30 días", 8, 1], ["Disponibilidad", 1, 1], ["Objetivo ACoS", 1, 2]]
      .forEach(([nombre, columnas, filas]) => {
        const th = nodo("th", nombre);
        th.setAttribute("scope", columnas > 1 ? "colgroup" : "col");
        th.setAttribute("colspan", String(columnas)); th.setAttribute("rowspan", String(filas));
        grupos.append(th);
      });
    ["Neto maduro", "Muestra", "Ventas totales", "Revenue Ads", "Gasto", "ACoS", "CPC", "CVR",
      "Compras", "Muestra Ads", "Etiqueta", "Estado"].forEach(titulo => {
      const th = nodo("th", titulo); th.setAttribute("scope", "col"); encabezado.append(th);
    });
    thead.append(grupos, encabezado);
    visibles.forEach(p => {
      const fila = nodo("tr"), publicacion = conClase("th", "fabrica-identidad");
      publicacion.setAttribute("scope", "row");
      const identidad = conClase("div", "fabrica-identidad-cabecera");
      const nombre = conClase("div", "fabrica-identidad-texto");
      nombre.append(conClase("strong", "fabrica-mono", valor(p.asin)));
      if (activas.has(p.listing_id)) nombre.append(chip("Seleccionada", "positivo"));
      nombre.append(conClase("span", "fabrica-apoyo fabrica-mono", valor(p.seller_sku)));
      identidad.append(fotoPublicacion(p.listing_id, p.asin, 34), nombre);
      publicacion.append(identidad);
      if (p.motivos && p.motivos.length) publicacion.append(conClase("span", "fabrica-apoyo fabrica-aviso-margen", p.motivos.join(" ")));
      const piezas = piezasEstimacion(p.estimacion);
      if (piezas) publicacion.append(piezas.principal, piezas.detalle);
      const tonoAds = p.ads.etiqueta === "dentro_del_objetivo" ? "positivo"
        : ["gasto_sin_ventas", "por_encima_del_objetivo"].includes(p.ads.etiqueta) ? "negativo" : "neutro";
      const etiqueta = nodo("td"); etiqueta.append(chip(textoEtiquetaAds(p.ads), tonoAds));
      const superaObjetivo = p.objetivo_acos_pct !== null && p.objetivo_acos_pct !== undefined
        && p.ads.acos_pct !== null && p.ads.acos_pct !== undefined
        && Number(p.ads.acos_pct) > Number(p.objetivo_acos_pct);
      fila.append(publicacion,
        celdaNumero(porcentaje(p.economia.margen_neto_pct), tonoMargen(p.economia.margen_neto_pct)),
        celdaMuestra(p.economia), celdaDinero(p.economia.venta_total, p.economia.moneda),
        celdaDinero(p.ads.sales30d, p.ads.moneda), celdaDinero(p.ads.cost, p.ads.moneda),
        celdaNumero(porcentaje(p.ads.acos_pct), superaObjetivo ? "fabrica-negativo" : ""),
        celdaDinero(p.ads.cpc, p.ads.moneda), celdaNumero(porcentaje(p.ads.cvr_pct)),
        celdaNumero(ausente(p.ads.purchases30d) ? "Sin dato" : Orbit.numero(p.ads.purchases30d)),
        celdaNumero(Orbit.numero(p.ads.muestra) + (p.ads.muestra === 1 ? " fecha" : " fechas")),
        etiqueta, celdaDisponibilidad(p.disponibilidad), celdaNumero(porcentaje(p.objetivo_acos_pct)));
      tbody.append(fila);
    });
    tabla.append(thead, tbody); envoltura.append(tabla); contenedor.append(envoltura);
    if (!visibles.length) contenedor.append(nodo("p", "Ninguna publicación coincide con este filtro."));
  }

  // Objetivo del grupo que el dueno ESTA preparando (D2/0.4 §3): el manual
  // del formulario viaja a /evaluacion; sin el, la API solo usa grupos con
  // lote 'planeado' (que en el flujo real aun no existen).
  function objetivoFormulario() {
    if (porId("objetivo-origen").value !== "manual_lanzamiento") return null;
    const valor = Number(porId("objetivo-acos").value);
    return Number.isFinite(valor) && valor > 0 && valor <= 100 ? valor : null;
  }

  // Objetivo que viaja a /evaluacion (hallazgo cross-review 2026-09-07): el
  // manual del formulario cuando el origen es manual; si el origen es
  // margen_medido, el DERIVADO del preview vigente (schema v2); si no, null.
  // Sin esto el comparador conservaba el objetivo anterior o nunca recibia el
  // derivado del margen.
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
    const version = ++versionComparador;
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
      if (version !== versionComparador) return;
      comparadorDatos = datos;
      if (datos.as_of) asOfEstimacion = datos.as_of;
      renderComparador();
      estado("comparador-estado", (datos.publicaciones || []).length
        + " publicaciones · sin dato al final del orden · muestra limitada fuera del orden.");
    } catch (error) {
      if (version !== versionComparador) return;
      comparadorDatos = null;
      estado("comparador-estado", error.message, true);
    }
  }

  // Buscador (F1): filtra las tarjetas ya cargadas por nombre interno,
  // SKU de Odoo, SKU de Amazon y ASIN. Sin red, sin tocar checkboxes:
  // la seleccion y el preview vigente sobreviven al filtro.
  function filtrarCatalogo() {
    const consulta = porId("buscar").value.trim().toLowerCase();
    tarjetasCatalogo.forEach(({elemento, texto}) => {
      elemento.hidden = consulta !== "" && !texto.includes(consulta);
    });
    actualizarConteoBuscador();
  }

  // Contador junto al buscador (F5): una marcada pero filtrada no pasa
  // desapercibida. Solo presenta lo ya cargado; sin red.
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

  async function cargarCatalogo() {
    const previas = seleccionadas();
    const version = ++versionCatalogo;
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
      if (version !== versionCatalogo) return;
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
          const datos = conClase("span", "fabrica-publicacion-datos");
          datos.append(conClase("strong", "fabrica-mono fabrica-asin", "ASIN: " + valor(publicacion.asin)),
            conClase("span", "fabrica-mono fabrica-apoyo", "SKU de Amazon: " + valor(publicacion.seller_sku)));
          const margen = conClase("span", "fabrica-margen");
          const monto = publicacion.margen_neto_pct === null || publicacion.margen_neto_pct === undefined
            ? "—" : porcentaje(publicacion.margen_neto_pct);
          margen.append(conClase("strong", tonoMargen(publicacion.margen_neto_pct), monto),
            nodo("span", "margen neto antes de Ads"));
          datos.append(margen, conClase("span", "fabrica-apoyo", muestraMargen(publicacion)), chipsPublicacion(publicacion));
          const piezas = piezasEstimacion(publicacion.estimacion);
          if (piezas) datos.append(piezas.principal);
          if (!publicacion.elegible) datos.append(conClase("span", "fabrica-identidad-incompleta",
            "No seleccionable: " + (publicacion.motivos || []).join(" ")));
          label.append(input, fotoPublicacion(publicacion.id, publicacion.asin), datos);
          fila.append(label);
          if (piezas) fila.append(piezas.detalle);
          // El enlace es hermano del label: abrir Amazon nunca cambia la seleccion.
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
        tarjetasCatalogo.push({
          elemento: tarjeta,
          texto: piezas.filter(Boolean).join(" ").toLowerCase(),
        });
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
    if (objetivo.origen === "manual_lanzamiento") {
      objetivo.acos_pct = porId("objetivo-acos").value.trim();
    }
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
        plataforma: porId("plataforma").value,
        tipo_producto: tipo,
        listing_ids: listingIds,
        objetivo,
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
      const pendientes = roles.filter(rol => !datos.roles[rol].disponible)
        .map(nombreRol).join(", ");
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
      mostrarPreview(datos);
      // El preview puede traer el objetivo DERIVADO del margen (schema v2):
      // refresca el comparador con el objetivo vigente del grupo en preparacion.
      cargarComparador();
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
    lotePlan = datos.plan || null;
    lotePlataforma = datos.plataforma;
    planRetiro = null;
    porId("retiro-datos").replaceChildren();
    porId("retiro-ejecutar").hidden = true;
    porId("retiro-confirmacion").value = "";
    const pubs = lotePlan && lotePlan.schema_version === 2 && Array.isArray(lotePlan.publicaciones)
      ? lotePlan.publicaciones.map(p => p.listing_id).filter(x => x > 0)
      : [];
    retiroElegible = datos.estado === "applied" && pubs.length > 0;
    estado("retiro-estado", retiroElegible ? "" : (datos.estado === "applied"
      ? "Este lote no trae publicaciones: nada que sacar."
      : "El retiro solo se ofrece con el lote creado y registrado."));
    const contenedor = porId("lote-datos");
    contenedor.replaceChildren();
    ficha(contenedor, [["Plataforma", Orbit.etiqueta(datos.plataforma, "plataforma")], ["Estado", Orbit.etiqueta(datos.estado, "estado_lote")], ["Detalle", datos.detalle],
      ["Creado", fechaHora(datos.created_at)], ["Finalizado", fechaHora(datos.finished_at)]]);
    tabla(contenedor, ["Paso", "Rol", "Recurso", "ID externo", "Estado", "Verificación de creación"], datos.pasos.map(paso => [
      paso.orden, nombreRol(paso.rol), Orbit.etiqueta(paso.recurso, "recurso_fabrica"), paso.external_id,
      Orbit.etiqueta(paso.estado, "estado_paso"),
      ausente(paso.readback_estado) ? null : Orbit.etiqueta(paso.readback_estado, "estado_campana"),
    ]));
    if (datos.plan) {
      const detalle = nodo("details"); detalle.append(nodo("summary", "Ver plan persistido del lote"));
      mostrarPlan(detalle, datos.plan);
      if (datos.plan.parametros) tabla(detalle, ["Rol", "Presupuesto diario", "Puja inicial", "Fuente"], roles.map(rol => [
        nombreRol(rol), dinero(datos.plan.parametros[rol].budget, datos.plan.moneda),
        dinero(datos.plan.parametros[rol].bid, datos.plan.moneda),
        datos.plan.parametros[rol].fuente_bid === "amazon_v4" ? "Amazon sugerido" : "Manual",
      ]));
      mostrarDetalleBids(detalle, datos.bids, datos.plan.moneda);
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
        const fila = nodo("li"), enlace = nodo("a", lote.lote + " · " + Orbit.etiqueta(lote.estado, "estado_lote") + " · " + Orbit.fechaHora(lote.created_at));
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

  // Banner de resultado junto al boton de crear (F3): mensaje con textContent
  // mas enlace al detalle del lote; el foco se mueve al banner como con
  // preview-titulo. La logica de intentados/huella y el manejo de 401 no cambian.
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
    let token;
    try { token = tomarToken(); } catch (error) { estado("estado", error.message, true); return; }
    const datos = preview;
    intentados.add(datos.huella);
    seleccionarLote(datos.lote);
    mutando = true; actualizarBotones();
    porId("crear-boton").textContent = "Creando… no cierres la página";
    porId("crear-progreso").hidden = false;
    ocultarResultadoCrear();
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
      mostrarResultadoCrear("Grupo creado: " + Orbit.etiqueta(resultado.estado, "estado_lote"), resultado.lote);
      estado("estado", "Solicitud finalizada. Revisa el estado y los pasos del lote.");
      estado("lote-estado", "Resultado recibido: " + Orbit.etiqueta(resultado.estado, "estado_lote"));
    } catch (error) {
      versionDetalle += 1;
      if (error.status === 401) {
        intentados.delete(datos.huella);
        tokenRechazado = true;
        mostrarResultadoCrear("Token rechazado. Corrígelo y confirma de nuevo el plan revisado.", null);
        estado("estado", "Token rechazado. Corrígelo y confirma de nuevo el plan revisado.", true);
        estado("lote-estado", "La solicitud fue rechazada antes de crear el lote.", true);
        return;
      }
      if (error.lote && error.lote !== loteActual) seleccionarLote(error.lote);
      mostrarResultadoCrear("Creación interrumpida. Consulta el detalle del lote; no se reintentará la creación.",
        error.lote || loteActual);
      estado("estado", error.message + " Conserva el lote y consulta su estado; no se reintentará la creación.", true);
      estado("lote-estado", "Resultado pendiente de consultar. " + error.message, true);
    } finally {
      token = null; mutando = false;
      porId("crear-boton").textContent = "Crear las cinco campañas";
      porId("crear-progreso").hidden = true;
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

  async function pedirRetiro() {
    if (mutando || !loteActual || !detalleDisponible || !retiroElegible) return;
    const listings = lotePlan.publicaciones.map(p => p.listing_id).filter(x => x > 0);
    estado("retiro-estado", "Pidiendo el plan de retiro…");
    try {
      const datos = await solicitar("/retiro/plan", {
        plataforma: lotePlataforma, listing_ids: listings, motivo: "subfamilia", lote: loteActual,
      });
      planRetiro = datos;
      const contenedor = porId("retiro-datos");
      contenedor.replaceChildren();
      if (!datos.anuncios.length) {
        contenedor.append(nodo("p", "Nada que sacar: sin anuncios en bolsas viejas."));
        porId("retiro-ejecutar").hidden = true;
        estado("retiro-estado", "");
        return;
      }
      tabla(contenedor, ["Anuncio", "Grupo", "Campaña", "SKU"], datos.anuncios.map(a =>
        [a.ad_id_externo, a.ad_group_nombre || a.ad_group_externo, a.campana_externa, a.sku]));
      const esperada = "SACAR " + datos.anuncios.length + " ANUNCIOS";
      porId("retiro-esperada").textContent = esperada;
      porId("retiro-confirmacion").placeholder = esperada;
      porId("retiro-ejecutar").hidden = false;
      estado("retiro-estado", "");
    } catch (error) { estado("retiro-estado", error.message, true); }
  }

  async function ejecutarRetiro(evento) {
    evento.preventDefault();
    if (mutando || !loteActual || !planRetiro) return;
    const esperada = "SACAR " + planRetiro.anuncios.length + " ANUNCIOS";
    const confirmacion = porId("retiro-confirmacion").value;
    if (confirmacion !== esperada) {
      estado("retiro-estado", "Escribe exactamente " + esperada + " para confirmar.", true); return;
    }
    const actor = porId("retiro-actor").value;
    if (!actor.trim()) { estado("retiro-estado", "Escribe quién saca los anuncios.", true); return; }
    let token;
    try { token = tomarToken(); } catch (error) { estado("retiro-estado", error.message, true); return; }
    mutando = true; actualizarBotones();
    porId("retiro-confirmacion").value = "";
    estado("retiro-estado", "Sacando los anuncios…");
    try {
      const listings = lotePlan.publicaciones.map(p => p.listing_id).filter(x => x > 0);
      const resultado = await solicitar("/retiro/ejecutar", {
        plataforma: lotePlataforma, listing_ids: listings, motivo: "subfamilia",
        lote: loteActual, huella: planRetiro.huella, confirmacion, actor: actor.trim(),
      }, token);
      planRetiro = null;
      porId("retiro-ejecutar").hidden = true;
      estado("retiro-estado", "Retirados " + resultado.retirados + ".");
    } catch (error) { estado("retiro-estado", error.message, true); }
    finally { token = null; mutando = false; actualizarBotones(); }
  }

  function cambioFormulario(evento) {
    // El buscador filtra lo ya cargado: no invalida el plan revisado (F1).
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
  // El objetivo del grupo en preparacion cambia => el comparador vuelve a
  // consultar (solo GET /evaluacion). El tipeo manual lleva debounce (~400 ms)
  // para no disparar una consulta por cada tecla.
  porId("objetivo-acos").addEventListener("input", programarComparador);
  porId("objetivo-origen").addEventListener("change", () => {
    actualizarObjetivoManual();
    cargarComparador();
  });
  porId("crear").addEventListener("submit", crear);
  porId("bids-amazon").addEventListener("click", cargarBidsAmazon);
  porId("accion").addEventListener("submit", ejecutarAccion);
  porId("accion-tipo").addEventListener("change", () => { porId("accion-confirmacion").value = ""; });
  porId("plataforma").addEventListener("change", () => {
    roles.forEach(rol => {
      formulario.elements[rol + "_budget"].value = "";
      formulario.elements[rol + "_bid"].value = "";
    });
    // Catalogo fija asOfEstimacion; evaluacion reusa el mismo corte (S5).
    cargarCatalogo().then(() => cargarComparador());
    cargarHistorial();
  });
  porId("productos").addEventListener("change", () => {
    actualizarResumenCatalogo(); renderComparador(); actualizarConteoBuscador();
  });
  porId("recargar-catalogo").addEventListener("click", () => {
    cargarCatalogo().then(() => cargarComparador());
  });
  porId("buscar").addEventListener("input", filtrarCatalogo);
  // Enter dentro del buscador no envia el formulario (F5): el buscador vive
  // dentro de #fabrica-plan y el submit implicito llamaria a revisar().
  porId("buscar").addEventListener("keydown", evento => {
    if (evento.key === "Enter") evento.preventDefault();
  });
  porId("historial-recargar").addEventListener("click", cargarHistorial);
  porId("lote-recargar").addEventListener("click", cargarLote);
  porId("retiro-boton").addEventListener("click", pedirRetiro);
  porId("retiro-ejecutar").addEventListener("submit", ejecutarRetiro);
  porId("comparador-recargar").addEventListener("click", cargarComparador);
  porId("comparador-orden").addEventListener("change", cargarComparador);
  porId("comparador-direccion").addEventListener("change", cargarComparador);
  // El filtro no consulta de nuevo: la seleccion del catalogo queda intacta (AC10).
  porId("comparador-filtro").addEventListener("change", renderComparador);
  window.addEventListener("pagehide", () => { porId("token").value = ""; });
  actualizarObjetivoManual();
  // Arranque en serie catalogo -> evaluacion para compartir as_of.
  cargarCatalogo().then(() => cargarComparador());
  cargarHistorial();
  const lote = new URL(window.location.href).searchParams.get("lote");
  if (lote) { seleccionarLote(lote); cargarLote(); }
});
