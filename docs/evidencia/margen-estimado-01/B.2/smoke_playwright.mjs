#!/usr/bin/env node
/**
 * Smoke B.2: Chrome/Playwright sobre /campanas/nuevas con APIs mockeadas.
 * Evidencia visual en screenshots/ (390px, tema dia/noche, detalle abierto).
 */
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../../../..");
const OUT = path.join(__dirname, "screenshots");
const PORT = process.env.ORBIT_SMOKE_PORT || "8765";
const BASE = `http://127.0.0.1:${PORT}`;

fs.mkdirSync(OUT, { recursive: true });

const AS_OF = "2026-09-08T19:00:00+00:00";
const componentes = [
  {
    nombre: "referral",
    importe_original: "15.0000",
    moneda_original: "MXN",
    importe_normalizado: "15.0000",
    moneda_normalizada: "MXN",
    fuente: "product_fees",
    fecha_fuente: "2026-09-08",
    observed_at: "2026-09-08T12:00:00+00:00",
    vigencia: "2026-09-08",
    estado: "incluido",
    pertenencia: true,
    unidad: "1",
  },
];

function sobre(over = {}) {
  return Object.assign(
    {
      estado: "disponible",
      motivos: [],
      snapshot_id: 3,
      escenario: {
        unidad: "1",
        canal: "fba",
        fecha_valoracion: "2026-09-08",
        version_formula: "S3",
        version_politica: "amazon_mx_pf_rfc_valid_2026_01",
      },
      moneda: "MXN",
      contribucion: "42.5000",
      contribucion_pct: "36.6379",
      base_porcentaje: "ingreso_normalizado",
      componentes,
      exclusiones: ["ads"],
      detalle: null,
    },
    over,
  );
}

function pubCat(id, asin, est) {
  return {
    id,
    asin,
    seller_sku: "SKU-" + asin.slice(-1),
    platform: "amazon_mx",
    margen_neto_pct: "40",
    dias_con_venta: 70,
    ventana_desde: "2026-02-20",
    ventana_hasta: "2026-08-22",
    historial_ads: null,
    elegible: true,
    motivos: [],
    url: "https://www.amazon.com.mx/dp/" + asin,
    estimacion: est,
  };
}

const catalogo = {
  plataforma: "amazon_mx",
  as_of: AS_OF,
  moneda: "MXN",
  tipos_producto: [],
  productos: [
    {
      id: 1,
      sku: "GORRA",
      nombre: "Gorras",
      publicaciones: [
        pubCat(11, "B0AAAAAAAA", sobre()),
        pubCat(
          13,
          "B0CCCCCCCC",
          sobre({
            estado: "incompleta",
            contribucion: null,
            contribucion_pct: null,
            motivos: ["fee_ausente", "politica_ausente"],
            detalle: {
              contribucion: "42.5000",
              contribucion_pct: "36.6379",
              estado: "disponible",
            },
          }),
        ),
      ],
    },
  ],
};

const evaluacion = {
  plataforma: "amazon_mx",
  as_of: AS_OF,
  ventana_ads: { desde: "2026-08-06", hasta: "2026-09-05" },
  orden: "margen_observado",
  direccion: "desc",
  publicaciones: [],
};

function waitForServer(url, ms = 20000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      http
        .get(url, (res) => {
          res.resume();
          resolve();
        })
        .on("error", () => {
          if (Date.now() - start > ms) reject(new Error("server timeout"));
          else setTimeout(tick, 200);
        });
    };
    tick();
  });
}

async function main() {
  const env = {
    ...process.env,
    PYTHONPATH: ROOT,
    ORBIT_UI_SMOKE: "1",
  };
  const server = spawn(
    path.join(ROOT, ".venv/bin/python"),
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(PORT)],
    { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] },
  );
  let stderr = "";
  server.stderr.on("data", (d) => {
    stderr += d.toString();
  });
  try {
    await waitForServer(`${BASE}/campanas/nuevas`);
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 2,
    });
    const page = await context.newPage();
    await page.route("**/api/fabrica/**", async (route) => {
      const url = route.request().url();
      if (url.includes("/catalogo")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(catalogo),
        });
      }
      if (url.includes("/lotes")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: [] }),
        });
      }
      if (url.includes("/evaluacion")) {
        if (!url.includes("as_of=")) {
          throw new Error("evaluacion sin as_of compartido: " + url);
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(evaluacion),
        });
      }
      return route.fulfill({ status: 200, body: "{}" });
    });

    await page.goto(`${BASE}/campanas/nuevas`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".fabrica-estimacion", { timeout: 15000 });

    const bodyText = await page.locator("#fabrica-productos").innerText();
    if (!bodyText.includes("Contribución estimada por venta")) {
      throw new Error("falta etiqueta de estimacion");
    }
    if (!bodyText.includes("Falta la cotización de comisiones")) {
      throw new Error("motivo fee_ausente no legible");
    }
    if (!bodyText.includes("Falta la política de cálculo")) {
      throw new Error("motivo politica_ausente no legible");
    }
    if (/\bfee_ausente\b|\bpolitica_ausente\b/.test(bodyText)) {
      throw new Error("motivos internos crudos visibles");
    }

    // Overflow horizontal real a 390 px
    const overflow = await page.evaluate(() => {
      const root = document.documentElement;
      return {
        scrollWidth: root.scrollWidth,
        clientWidth: root.clientWidth,
        bodyScroll: document.body.scrollWidth,
      };
    });
    if (overflow.scrollWidth > overflow.clientWidth + 1) {
      throw new Error(
        `overflow horizontal: scrollWidth=${overflow.scrollWidth} clientWidth=${overflow.clientWidth}`,
      );
    }

    // Tema dia
    await page.evaluate(() => {
      document.documentElement.dataset.tema = "dia";
    });
    await page.screenshot({
      path: path.join(OUT, "dia-390.jpg"),
      fullPage: false, type: "jpeg", quality: 70,
    });

    // Tema noche
    await page.evaluate(() => {
      document.documentElement.dataset.tema = "noche";
    });
    await page.screenshot({
      path: path.join(OUT, "noche-390.jpg"),
      fullPage: false, type: "jpeg", quality: 70,
    });

    // Teclado / foco en el desglose
    await page.locator("details.fabrica-estimacion-detalle summary").first().focus();
    const focused = await page.evaluate(
      () => document.activeElement && document.activeElement.tagName,
    );
    if (focused !== "SUMMARY") {
      throw new Error("summary del desglose no recibe foco");
    }
    await page.keyboard.press("Enter");
    await page.waitForTimeout(200);
    const open = await page
      .locator("details.fabrica-estimacion-detalle")
      .first()
      .evaluate((el) => el.open);
    if (!open) throw new Error("Enter no abre el desglose");
    const detalle = await page
      .locator("details.fabrica-estimacion-detalle")
      .first()
      .innerText();
    for (const needle of [
      "fecha 2026-09-08",
      "vigencia 2026-09-08",
      "estado incluido",
      "pertenece al total",
    ]) {
      if (!detalle.includes(needle)) {
        throw new Error("falta en desglose: " + needle);
      }
    }
    await page.screenshot({
      path: path.join(OUT, "detalle-abierto-noche-390.jpg"),
      fullPage: false, type: "jpeg", quality: 70,
    });

    // Contraste basico: color de titulo vs fondo
    const contraste = await page.evaluate(() => {
      const el = document.querySelector(".fabrica-estimacion-titulo");
      const cs = getComputedStyle(el);
      const bg = getComputedStyle(document.body).backgroundColor;
      return { color: cs.color, bg, fontSize: cs.fontSize };
    });
    fs.writeFileSync(
      path.join(__dirname, "smoke-resultado.json"),
      JSON.stringify(
        {
          ok: true,
          viewport: { width: 390, height: 844 },
          overflow,
          contraste,
          screenshots: [
            "screenshots/dia-390.jpg",
            "screenshots/noche-390.jpg",
            "screenshots/detalle-abierto-noche-390.jpg",
          ],
        },
        null,
        2,
      ) + "\n",
    );

    await browser.close();
    console.log("smoke B.2 OK");
  } catch (err) {
    console.error(err);
    console.error(stderr.slice(-2000));
    process.exitCode = 1;
  } finally {
    server.kill("SIGTERM");
  }
}

main();
