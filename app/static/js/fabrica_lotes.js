(function () {
"use strict";
// Lotes, historial, acciones de recuperacion y retiro de Crear campanas.
// Comparte su fabrica de modulo en Orbit._fabricaLotes (cero simbolos en window).

const CONFIRMACIONES = {
  pausar: "PAUSAR GRUPO",
  reconciliar: "RECONCILIAR GRUPO",
  registrar: "REGISTRAR GRUPO",
};

function crearModuloLotes(deps) {
  const {
    porId,
    estado,
    solicitar,
    tomarToken,
    ultimaRespuesta,
    vistas,
    mutandoGet,
    mutandoSet,
    actualizarBotones,
  } = deps;
  const { ROLES, nombreRol, ausente, dinero, fechaHora, nodo, tabla, ficha, mostrarPlan, mostrarDetalleBids } = vistas;

  let loteActual = null;
  let detalleDisponible = false;
  let lotePlan = null;
  let lotePlataforma = null;
  let planRetiro = null;
  let retiroElegible = false;
  const sigDetalle = ultimaRespuesta();
  const sigHistorial = ultimaRespuesta();

  function estadoLote() {
    return { loteActual, detalleDisponible, retiroElegible };
  }

  function invalidarDetalle() {
    sigDetalle();
  }

  function seleccionarLote(lote) {
    loteActual = lote;
    detalleDisponible = false;
    sigDetalle();
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
    ficha(contenedor, [
      ["Plataforma", Orbit.etiqueta(datos.plataforma, "plataforma")],
      ["Estado", Orbit.etiqueta(datos.estado, "estado_lote")],
      ["Detalle", datos.detalle],
      ["Creado", fechaHora(datos.created_at)],
      ["Finalizado", fechaHora(datos.finished_at)],
    ]);
    tabla(contenedor, ["Paso", "Rol", "Recurso", "ID externo", "Estado", "Verificación de creación"], datos.pasos.map(paso => [
      paso.orden, nombreRol(paso.rol), Orbit.etiqueta(paso.recurso, "recurso_fabrica"), paso.external_id,
      Orbit.etiqueta(paso.estado, "estado_paso"),
      ausente(paso.readback_estado) ? null : Orbit.etiqueta(paso.readback_estado, "estado_campana"),
    ]));
    if (datos.plan) {
      const detalle = nodo("details"); detalle.append(nodo("summary", "Ver plan persistido del lote"));
      mostrarPlan(detalle, datos.plan);
      if (datos.plan.parametros) tabla(detalle, ["Rol", "Presupuesto diario", "Puja inicial", "Fuente"], ROLES.map(rol => [
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
    const lote = loteActual;
    const esVigente = sigDetalle();
    estado("lote-estado", "Consultando el lote…");
    try {
      const datos = await solicitar("/lotes/" + encodeURIComponent(lote));
      if (!esVigente()) return;
      mostrarLote(datos);
      estado("lote-estado", "Estado actualizado.");
    } catch (error) {
      if (esVigente()) estado("lote-estado", error.message, true);
    }
  }

  async function cargarHistorial() {
    const esVigente = sigHistorial();
    estado("historial-estado", "Consultando historial…");
    porId("historial").replaceChildren();
    try {
      const datos = await solicitar("/lotes?plataforma=" + encodeURIComponent(porId("plataforma").value));
      if (!esVigente()) return;
      const lista = nodo("ul");
      datos.items.forEach(lote => {
        const fila = nodo("li");
        const enlace = nodo("a", lote.lote + " · " + Orbit.etiqueta(lote.estado, "estado_lote") + " · " + Orbit.fechaHora(lote.created_at));
        enlace.href = "/campanas/nuevas?lote=" + encodeURIComponent(lote.lote);
        enlace.addEventListener("click", evento => {
          evento.preventDefault();
          if (mutandoGet()) return;
          seleccionarLote(lote.lote); cargarLote(); porId("lote-titulo").focus();
        });
        fila.append(enlace); lista.append(fila);
      });
      porId("historial").append(lista);
      estado("historial-estado", datos.items.length ? "Selecciona un lote para ver el detalle." : "Todavía no hay lotes para esta plataforma.");
    } catch (error) {
      if (esVigente()) estado("historial-estado", error.message, true);
    }
  }

  async function ejecutarAccion(evento) {
    evento.preventDefault();
    if (mutandoGet() || !loteActual || !detalleDisponible) return;
    const accion = porId("accion-tipo").value, confirmacion = porId("accion-confirmacion").value;
    if (!CONFIRMACIONES[accion] || confirmacion !== CONFIRMACIONES[accion]) {
      estado("lote-estado", "Escribe exactamente la frase de la acción seleccionada.", true); return;
    }
    let token;
    try { token = tomarToken(); } catch (error) { estado("lote-estado", error.message, true); return; }
    mutandoSet(true); sigDetalle(); actualizarBotones();
    porId("accion-confirmacion").value = "";
    estado("lote-estado", "Ejecutando " + accion + "…");
    try {
      const resultado = await solicitar("/lotes/" + encodeURIComponent(loteActual) + "/" + accion, {confirmacion}, token);
      sigDetalle();
      mostrarLote(resultado);
      estado("lote-estado", "Acción finalizada. Revisa el estado y los pasos del lote.");
    } catch (error) {
      sigDetalle();
      estado("lote-estado", error.message + " Consulta el estado antes de repetir la acción.", true);
    } finally {
      token = null; mutandoSet(false); actualizarBotones(); cargarHistorial();
    }
  }

  async function pedirRetiro() {
    if (mutandoGet() || !loteActual || !detalleDisponible || !retiroElegible) return;
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
    if (mutandoGet() || !loteActual || !planRetiro) return;
    const esperada = "SACAR " + planRetiro.anuncios.length + " ANUNCIOS";
    const confirmacion = porId("retiro-confirmacion").value;
    if (confirmacion !== esperada) {
      estado("retiro-estado", "Escribe exactamente " + esperada + " para confirmar.", true); return;
    }
    const actor = porId("retiro-actor").value;
    if (!actor.trim()) { estado("retiro-estado", "Escribe quién saca los anuncios.", true); return; }
    let token;
    try { token = tomarToken(); } catch (error) { estado("retiro-estado", error.message, true); return; }
    mutandoSet(true); actualizarBotones();
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
    finally { token = null; mutandoSet(false); actualizarBotones(); }
  }

  return {
    estadoLote,
    invalidarDetalle,
    seleccionarLote,
    mostrarLote,
    cargarLote,
    cargarHistorial,
    ejecutarAccion,
    pedirRetiro,
    ejecutarRetiro,
  };
}

Orbit._fabricaLotes = crearModuloLotes;
})();
