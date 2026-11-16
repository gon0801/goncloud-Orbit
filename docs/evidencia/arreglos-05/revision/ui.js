"use strict";
// Controlador externo: DOM y listeners de producto reales, sin reemplazarlos.
window.addEventListener("load", async function () {
  const result = document.createElement("pre");
  result.id = "ui-result";
  const wait = () => new Promise(resolve => setTimeout(resolve, 150));
  try {
    const modo = new URL(location.href).searchParams.get("ui_modo");
    const retiro = modo === "retiro";
    const boxes = [...document.querySelectorAll("[data-marcar-sin-pedido]")];
    if (boxes.length !== 2) throw new Error("Se esperaban dos productos reales sin pedido: " + boxes.length);
    document.querySelectorAll("[data-subfamilia]").forEach(e => {e.value = "ui";});
    boxes[0].click();
    const btn = document.getElementById(retiro ? "productos-sacar" : "productos-impulsar-aparte");
    const form = document.getElementById(retiro ? "productos-retiro-ejecutar" : "productos-lanzar");
    const zona = document.getElementById(retiro ? "productos-retiro-vista" : "productos-vista");
    btn.click();
    for (let i = 0; i < 30 && form.hidden; ++i) await wait();
    if (form.hidden) throw new Error("Primera previa no disponible: " + zona.textContent);
    const previaA = zona.textContent;
    boxes[0].click(); boxes[1].click();
    btn.click();
    for (let i = 0; i < 30 && !zona.textContent.includes("Fallo controlado"); ++i) await wait();
    if (!zona.textContent.includes("Fallo controlado")) throw new Error("No se ejercito error de la segunda consulta");
    const errorB = zona.textContent;
    form.elements.actor.value = "review-local";
    form.elements.token.value = "token-ficticio-no-secreto";
    form.elements.confirmacion.value = retiro ? "SACAR 1 ANUNCIOS" : "LANZAR IMPULSO DE 1 PRODUCTOS";
    // Un usuario solo puede pulsar el submit si sigue disponible.
    const submit = form.querySelector('[type="submit"]');
    const ejecutable = !!form.getClientRects().length && !submit.disabled;
    if (ejecutable) submit.click();
    await wait(); await wait();
    result.textContent = JSON.stringify({previaA, errorB, ejecutable,
      seleccionActual: boxes.filter(e => e.checked).map(e => e.dataset.listings),
      estado: form.querySelector("[data-estado]").textContent});
  } catch (e) {
    result.textContent = JSON.stringify({error: String(e)});
  }
  document.body.append(result);
});
