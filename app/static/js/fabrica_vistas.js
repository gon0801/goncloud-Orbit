(function () {
"use strict";
// Vistas puras de Crear campanas (/campanas/nuevas). Sin estado global ni IO;
// comparte su API interna en Orbit._fabricaVistas (cero simbolos en window).

const ROLES = [
  "category_exact",
  "category_phrase",
  "category_broad",
  "product_targeting",
  "auto_discovery",
];

const nombreRol = codigo => Orbit.etiqueta(codigo, "rol_campana");
const ausente = dato => dato === null || dato === undefined;
const valor = dato => (ausente(dato) ? "Sin dato" : String(dato));
const porcentaje = dato => (ausente(dato) ? "Sin dato" : Orbit.pct(dato));
const fechaHora = dato => (ausente(dato) ? "Sin dato" : Orbit.fechaHora(dato));
const fraccion = dato => (ausente(dato) ? "Sin dato" : Orbit.fraccionPct(dato));

function dinero(monto, moneda) {
  if (ausente(monto)) return "Sin dato";
  return Orbit.dinero(monto) + (moneda ? " " + moneda : "");
}

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
  if (ausente(margen)) return "fabrica-sin-dato";
  return Number(margen) > 0 ? "fabrica-positivo" : "fabrica-negativo";
}

function avisoMargen(publicacion, objetivo) {
  const margen = publicacion.margen_neto_pct;
  const avisos = Array.isArray(publicacion.motivos) ? [...publicacion.motivos] : [];
  if (publicacion.historial_ads === null) avisos.push("Historial Ads sin dato en este catálogo.");
  if (ausente(margen)) avisos.push("Margen sin medir; el objetivo manual no acredita rentabilidad.");
  else if (Number(margen) === 0) avisos.push("Margen cero; el objetivo manual no acredita rentabilidad.");
  else if (Number(margen) < 0) avisos.push("Margen negativo; el objetivo manual no acredita rentabilidad.");
  else if (objetivo && Number(margen) < Number(objetivo)) avisos.push("Margen inferior al objetivo manual.");
  return avisos.length ? avisos.join(" ") : "Margen medido disponible.";
}

function muestraMargen(publicacion) {
  const dias = publicacion.dias_con_venta;
  const desde = publicacion.ventana_desde;
  const hasta = publicacion.ventana_hasta;
  if (ausente(dias) || !desde || !hasta) return "Muestra de margen: sin dato.";
  const limitada = Number(dias) < 30;
  return (limitada ? "Muestra limitada: " : "Muestra de margen: ") + Orbit.numero(dias)
    + " días con venta, del " + desde + " hasta antes del " + hasta + ".";
}

function chipsPublicacion(publicacion) {
  const contenedor = conClase("span", "fabrica-chips");
  const detalle = avisoMargen(publicacion), margen = publicacion.margen_neto_pct;
  if (!publicacion.elegible) contenedor.append(chip("Identidad incompleta", "negativo", detalle));
  if (ausente(margen)) contenedor.append(chip("Margen sin medir", "negativo", detalle));
  else if (Number(margen) <= 0) contenedor.append(chip(Number(margen) === 0 ? "Margen cero" : "Margen negativo", "negativo", detalle));
  else contenedor.append(chip("Margen medido", "positivo", detalle));
  if (!ausente(publicacion.dias_con_venta) && Number(publicacion.dias_con_venta) < 30) {
    contenedor.append(chip("Muestra limitada", "aviso", muestraMargen(publicacion)));
  }
  if (publicacion.historial_ads === null) contenedor.append(chip("Historial Ads sin dato", "neutro", detalle));
  return contenedor;
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

function exclusiones(codigos) {
  return codigos.map(codigo => Orbit.etiqueta(codigo, "exclusion_estimacion")).join(", ");
}

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

function mostrarPlan(contenedor, plan, enRevision = false) {
  const esV2 = plan.schema_version === 2;
  const objetivo = esV2 ? plan.objetivo : null;
  const target = esV2 ? objetivo.acos_pct : plan.target_acos_pct;
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
  const publicaciones = plan.publicaciones;
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
  if (enRevision) {
    contenedor.append(nodo("p", "Estas búsquedas vendieron en campañas donde esta publicación se anuncia junto con otros productos. Alguna puede ser de otro producto del mismo grupo, por ejemplo de otra medida. Revisa la lista antes de crear."));
  }
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

function semillasDeCampana(semillas, rol) {
  if (rol === "auto_discovery") return ["Amazon crea los grupos automáticos; sin semillas manuales."];
  if (rol === "category_exact") return (semillas.exact || []).map(semilla => semilla + " (exacta)");
  if (rol === "category_phrase") return (semillas.keywords || []).map(semilla => semilla + " (frase)");
  if (rol === "category_broad") return (semillas.keywords || []).map(semilla => semilla + " (amplia)");
  if (rol === "product_targeting") return [...(semillas.asins || [])];
  return [];
}

function objetivoPuja(r) {
  return Orbit.etiqueta(r.tipo, "objetivo_puja") + (r.valor ? ": " + r.valor : "");
}

function mostrarDetalleBids(contenedor, datosPorRol, moneda, incluirFaltantes = false) {
  if (!datosPorRol) return;
  const conDatos = ROLES.filter(rol => {
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

function mostrarPreview(porId, datos, pujaManualSinSugerencia) {
  const contenedor = porId("preview-datos");
  contenedor.replaceChildren();
  if (datos.advertencia_mezcla) {
    const aviso = nodo("p", "Advertencia: " + datos.advertencia_mezcla);
    aviso.setAttribute("role", "alert");
    contenedor.append(aviso);
  }
  const avisosPresupuesto = datos.avisos_presupuesto || [];
  const casillaPresupuesto = porId("acepto-presupuesto-alto");
  if (casillaPresupuesto) casillaPresupuesto.checked = false;
  porId("casilla-presupuesto").hidden = !avisosPresupuesto.length;
  if (avisosPresupuesto.length) {
    const alerta = nodo("div");
    alerta.setAttribute("role", "alert");
    alerta.setAttribute("data-aviso-presupuesto", "");
    avisosPresupuesto.forEach(linea => alerta.append(nodo("p", linea)));
    contenedor.append(alerta);
  }
  mostrarPlan(contenedor, datos.plan, true);
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
    datos.existentes.forEach(existente => contenedor.append(nodo("pre", JSON.stringify(existente, null, 2))));
  } else contenedor.append(nodo("p", "No se encontraron campañas existentes."));
  ficha(contenedor, [["Lote", datos.lote], ["Huella del plan", datos.huella]]);
  porId("preview").hidden = false;
  porId("preview-titulo").focus();
}

function textoEtiquetaAds(ads) {
  if (!ads || ads.muestra === 0) return "Sin datos";
  const base = ads.etiqueta ? Orbit.etiqueta(ads.etiqueta, "etiqueta_ads") : "Datos sin etiqueta";
  return base + (ads.provisional ? " (provisional)" : "");
}

function textoMuestraMargen(econ) {
  if (econ && econ.muestra_limitada === true) {
    return "Limitada: " + valor(econ.dias_con_venta) + " días con venta, margen "
      + porcentaje(econ.muestra_margen_neto_pct) + " (no entra al orden)";
  }
  if (!econ || ausente(econ.dias_con_venta)) return "Sin dato";
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
    publicaciones.filter(p => !ausente(p.objetivo_acos_pct)).map(p => p.objetivo_acos_pct))];
  if (!valores.length) return "Sin objetivo de comparación";
  return valores.map(Orbit.pct).join(", ");
}

function valorUnicoMargen(publicaciones, sacar) {
  const valores = [...new Set(
    publicaciones.map(p => (p.economia ? sacar(p.economia) : null)).filter(v => !ausente(v)))];
  if (!valores.length) return null;
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

function textosMotivosEstimacion(motivos) {
  return (motivos || []).map(codigo => Orbit.etiqueta(codigo, "motivo_estimacion"));
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
    if (!ausente(est.contribucion_pct)) meta.push(porcentaje(est.contribucion_pct));
    if (est.base_porcentaje) meta.push("sobre " + Orbit.etiqueta(est.base_porcentaje, "componente_estimacion").toLowerCase());
    if (escenario.unidad) meta.push("unidad " + escenario.unidad);
    if (escenario.fecha_valoracion) meta.push(escenario.fecha_valoracion);
    if (escenario.canal) meta.push(String(escenario.canal).toUpperCase());
    if (meta.length) principal.append(conClase("span", "fabrica-apoyo", meta.join(" · ")));
  } else {
    principal.append(conClase("strong", "fabrica-sin-dato",
      est.estado ? Orbit.etiqueta(est.estado, "estado_estimacion") : "Sin dato"));
    const textos = textosMotivosEstimacion(est.motivos);
    if (textos.length) principal.append(conClase("span", "fabrica-apoyo", textos.join(" · ")));
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
  if (textosDetalle.length) detalle.append(conClase("span", "fabrica-apoyo", "Motivos: " + textosDetalle.join(" · ")));
  if (est.exclusiones && est.exclusiones.length) {
    detalle.append(conClase("span", "fabrica-apoyo", "Sin contar: " + exclusiones(est.exclusiones)));
  }
  const versiones = [];
  if (escenario.version_formula) versiones.push(escenario.version_formula);
  if (!ausente(escenario.version_politica)) versiones.push("politica " + escenario.version_politica);
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

function renderComparador(porId, comparadorDatos, activas) {
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
  const envoltura = conClase("div", "fabrica-tabla fabrica-comparacion-scroll");
  envoltura.tabIndex = 0;
  envoltura.setAttribute("role", "region");
  const orden = porId("comparador-orden");
  const opcion = orden.options && orden.options[orden.selectedIndex];
  envoltura.setAttribute("aria-label", "Comparación de publicaciones ordenada por "
    + (opcion ? opcion.text.toLowerCase() : orden.value));
  const tbl = conClase("table", "fabrica-comparacion");
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
    const superaObjetivo = !ausente(p.objetivo_acos_pct) && !ausente(p.ads.acos_pct)
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
  tbl.append(thead, tbody); envoltura.append(tbl); contenedor.append(envoltura);
  if (!visibles.length) contenedor.append(nodo("p", "Ninguna publicación coincide con este filtro."));
}

Orbit._fabricaVistas = {
  ROLES,
  nombreRol,
  ausente,
  valor,
  dinero,
  porcentaje,
  fechaHora,
  fraccion,
  nodo,
  conClase,
  chip,
  fotoPublicacion,
  tonoMargen,
  avisoMargen,
  muestraMargen,
  chipsPublicacion,
  tabla,
  ficha,
  mostrarPlan,
  mostrarDetalleBids,
  mostrarPreview,
  piezasEstimacion,
  renderComparador,
};
})();
