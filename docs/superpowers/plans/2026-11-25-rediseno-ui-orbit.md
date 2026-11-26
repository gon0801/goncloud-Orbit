# Rediseño integral de la interfaz de Orbit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aplicar la dirección visual A2 y una navegación por tareas a las 18 rutas del dashboard sin alterar datos, cálculos, APIs ni operaciones.

**Architecture:** `base.html` seguirá siendo el shell Jinja2 y derivará la navegación principal y secundaria de `data-pantalla`. Los tokens y componentes compartidos vivirán en `dashboard.css`; cada plantilla conservará sus contratos, endpoints y JavaScript de flujo. Un menú móvil de cinco destinos y una navegación «Más» accesible reemplazarán la barra actual de siete destinos.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, CSS, JavaScript sin framework, pytest, Node para arneses JS existentes, PostgreSQL 16 y `verify-orbit` con curl/Chrome.

**Spec:** `docs/superpowers/specs/2026-11-25-rediseno-ui-orbit-design.md`

## Global Constraints

- No cambiar la lógica de negocio, cálculos, fuentes de datos, monedas, permisos ni operaciones disponibles.
- Conservar todas las rutas y APIs actuales, incluido el alias `/propuestas`.
- Mantener Jinja2, CSS y JavaScript locales; no añadir un framework frontend ni dependencias de navegador.
- No añadir consultas SQL, APIs ni fuentes de métricas.
- Mantener `None` como raya o fila ausente; nunca convertirlo en cero.
- No mezclar MXN y USD ni añadir conversiones implícitas.
- Conservar autenticación, validaciones, confirmaciones y avisos de irreversibilidad.
- Conservar CSP `default-src 'self'`: sin CSS inline, handlers `on*` ni JavaScript ejecutable inline.
- Mantener `script[type="application/json"]` como único bloque inline inerte permitido.
- Usar textos directos: no incorporar «Hola, David», «Tu negocio, más claro» ni frases promocionales equivalentes.
- No incorporar búsqueda global, centro de notificaciones, ayuda ni selectores sin función real.
- Conservar los selectores funcionales existentes salvo que una prueba y la ficha de la pantalla se actualicen en el mismo bloque.
- Usar español en comentarios, pruebas y documentación, sin acentos en el código y siguiendo Ruff.
- Toda prueba de regresión nueva debe fallar primero contra el código anterior.
- Ejecutar `graphify update .` después de modificar código.

## Estructura de archivos

- `app/templates/base.html`: shell, títulos, mapa de secciones, navegación de escritorio y móvil.
- `app/templates/_subnavegacion.html`: enlaces GET del grupo activo; no contiene datos ni consultas.
- `app/templates/*.html`: orden y clases visuales de cada pantalla, sin nuevas fuentes de datos.
- `app/static/css/dashboard.css`: tokens A2, temas, shell y componentes compartidos.
- `app/static/css/{resumen,campanas,fabrica,decisiones,contribucion,salud}.css`: variaciones específicas ya existentes.
- `app/static/js/shell.js`: menú «Más», foco, Escape y badges actuales.
- `tests/test_ui*.py`, `tests/test_pantalla*.py`, `tests/test_precio_pantalla.py`: contratos HTML y regresiones visuales.
- `tests/test_verify_orbit.py`: comportamiento del harness real.
- `.cursor/skills/verify-orbit/helpers/orbit-verify`: recorrido común de rutas sin drive específico.
- `.cursor/skills/verify-orbit/features/*.md` y `SKILL.md`: mapa vigente de selectores y flujos.

---

### Task 1: Sistema visual A2 y componentes base

**Files:**
- Modify: `app/static/css/dashboard.css`
- Test: `tests/test_ui_tema.py`
- Test: `tests/test_ui_assets.py`

**Interfaces:**
- Consumes: los nombres actuales `.tarjeta`, `.kpi`, `.kpis`, `.pagina-header`, `.boton-primario`, `.chip`, `.filtros` y `html[data-tema]`.
- Produces: tokens `--o-bg`, `--o-card`, `--o-ac`, `--o-ac2`, `--o-sombra`, `--o-radio`, clases `.accion-primaria`, `.grupo-metricas`, `.superficie` y `.seccion-encabezado` para las tareas siguientes.

- [ ] **Step 1: Escribir la regresión de tokens y componentes**

Añadir a `tests/test_ui_tema.py`:

```python
def test_css_a2_define_tokens_y_componentes_compartidos():
    css = (_RAIZ / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")
    assert "--o-bg: #f6f7fb" in css
    assert "--o-ac: #6673e8" in css
    assert "--o-ac2: #7c68e9" in css
    assert "--o-radio: 18px" in css
    assert "--o-sombra: 0 8px 30px rgba(44, 49, 83, .055)" in css
    assert ".accion-primaria" in css
    assert ".grupo-metricas" in css
    assert ".superficie" in css
    assert ".seccion-encabezado" in css
    dia, noche = css.split('html[data-tema="noche"]', 1)
    assert "--o-bg: #f6f7fb" in dia
    assert "--o-bg: #171820" in noche
    assert "--o-card: #20222c" in noche
    assert "--o-ac: #a8afff" in noche
```

Actualizar la expectativa antigua `--color-acento: #9385f8` para que use el alias nuevo `--color-acento: #a8afff`; conservar las pruebas de scroll, envoltura y tarjetas que no dependen de la paleta.

- [ ] **Step 2: Ejecutar la prueba y confirmar el rojo**

Run: `uv run --frozen python -m pytest tests/test_ui_tema.py::test_css_a2_define_tokens_y_componentes_compartidos -q`
Expected: FAIL por ausencia de `--o-bg: #f6f7fb` y las clases nuevas.

- [ ] **Step 3: Implementar tokens, alias y componentes**

Sustituir la paleta base en `dashboard.css`, conservando los alias consumidos por pantallas y gráficas:

```css
:root, html[data-tema="dia"] {
  --o-bg: #f6f7fb;
  --o-bg2: #fafbfc;
  --o-card: #ffffff;
  --o-hover: #f0f1f7;
  --o-borde: #e8eaf1;
  --o-borde2: #eff0f5;
  --o-borde-h: #d5d8e4;
  --o-ink: #20232d;
  --o-ink2: #656b7a;
  --o-ink3: #8b91a0;
  --o-ink4: #a8adb9;
  --o-ac: #6673e8;
  --o-ac2: #7c68e9;
  --o-ac-bg: #eef0ff;
  --o-on-ac: #ffffff;
  --o-radio: 18px;
  --o-sombra: 0 8px 30px rgba(44, 49, 83, .055);
  --color-fondo: var(--o-bg);
  --color-tarjeta: var(--o-card);
  --color-texto: var(--o-ink);
  --color-mutado: var(--o-ink2);
  --color-acento: var(--o-ac);
  --color-alerta: var(--o-mal);
  --color-ok: var(--o-ok);
  --color-borde: var(--o-borde);
  color-scheme: light;
}
html[data-tema="noche"] {
  --o-bg: #171820;
  --o-bg2: #1b1d26;
  --o-card: #20222c;
  --o-hover: #292c38;
  --o-borde: #343743;
  --o-borde2: #2b2e39;
  --o-borde-h: #484c5c;
  --o-ink: #f1f2f6;
  --o-ink2: #b6bac7;
  --o-ink3: #9298a8;
  --o-ink4: #737989;
  --o-ac: #a8afff;
  --o-ac2: #bbb0ff;
  --o-ac-bg: #30334f;
  --o-on-ac: #171820;
  --color-acento: #a8afff;
  color-scheme: dark;
}
.accion-primaria {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 42px;
  padding: 10px 16px;
  border: 0;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--o-ac), var(--o-ac2));
  color: #ffffff;
  font-weight: 700;
}
.superficie {
  background: var(--o-card);
  border: 1px solid var(--o-borde);
  border-radius: var(--o-radio);
  box-shadow: var(--o-sombra);
}
.grupo-metricas { display: grid; gap: 15px; }
.seccion-encabezado { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
```

Aplicar `--o-radio` y `--o-sombra` a `.tarjeta`, `.kpi` y `.grafica`; mantener `.tabla-scroll` con `overflow-x: auto` y los valores semánticos existentes para éxito, aviso y error. En el bloque móvil, fijar objetivos de al menos 44 px para `.accion-primaria`, `.seg-btn`, `.nav-mas-toggle` y controles del shell sin alterar el alto de las celdas.

- [ ] **Step 4: Ejecutar las pruebas de tema y assets**

Run: `uv run --frozen python -m pytest tests/test_ui_tema.py tests/test_ui_assets.py tests/test_ui.py::test_ui_templates_compatibles_con_csp_self -q`
Expected: PASS; sin hosts externos, CSS inline ni regresiones de cache.

- [ ] **Step 5: Actualizar el grafo y commit**

Run: `graphify update .`

```bash
git add app/static/css/dashboard.css tests/test_ui_tema.py
git commit -m "feat(ui): define sistema visual A2"
```

---

### Task 2: Navegación por tareas y menú móvil accesible

**Files:**
- Modify: `app/templates/base.html`
- Create: `app/templates/_subnavegacion.html`
- Modify: `app/static/js/shell.js`
- Modify: `app/static/css/dashboard.css`
- Modify: `tests/test_ui.py`
- Create: `tests/test_ui_navegacion.py`

**Interfaces:**
- Consumes: `pantalla` y los badges `#propuestas-contador`, `#reputacion-contador`, `[data-rol]` ya leídos por `shell.js`.
- Produces: `nav#nav-secciones`, `nav.subnavegacion[aria-label="Secciones de <grupo>"]`, `nav.tab-bar` con cinco controles, `button#nav-mas-toggle`, `nav#nav-mas[hidden]` y estado activo por `aria-current="page"`.

- [ ] **Step 1: Reemplazar la prueba de siete tabs por el contrato nuevo**

Sustituir `test_ui_menu_movil_es_tab_bar_no_drawer` en `tests/test_ui.py` por una prueba que renderice `cortes.html` con `_ctx_propuestas()` y compruebe:

```python
def test_ui_menu_movil_agrupado_con_mas():
    html = ui.templates.env.get_template("cortes.html").render(**_ctx_propuestas())
    assert 'class="tab-bar"' in html
    assert html.count('class="tab-label"') == 5
    assert 'id="nav-mas-toggle"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-controls="nav-mas"' in html
    assert 'id="nav-mas"' in html
    assert ">Resumen<" in html
    assert ">Campañas<" in html
    assert ">Oportunidades<" in html
    assert ">Decisiones<" in html
    assert ">Más<" in html
    assert ">Contribucion<" not in html
    assert ">Settings<" not in html
    assert 'href="/cortes"' in html
    assert 'data-rol="propuestas"' in html
```

Crear `tests/test_ui_navegacion.py`:

```python
from app import ui


def _base(pantalla: str) -> str:
    return ui.templates.env.get_template("base.html").render(pantalla=pantalla)


def test_subnavegacion_agrupa_todas_las_rutas():
    esperado = {
        "cortes": ("Oportunidades", "/gasto-sin-venta", "/inertes"),
        "gasto-sin-venta": ("Oportunidades", "/cortes", "/busquedas-sin-venta"),
        "busquedas-sin-venta": ("Oportunidades", "/cortes", "/keywords-danadas"),
        "keywords-danadas": ("Oportunidades", "/cortes", "/productos-sin-clics"),
        "productos-sin-clics": ("Oportunidades", "/cortes", "/inertes"),
        "inertes": ("Oportunidades", "/cortes", "/gasto-sin-venta"),
        "decisiones": ("Decisiones", "/ruido", "/decisiones"),
        "ruido": ("Decisiones", "/decisiones", "/ruido"),
        "contribucion": ("Rendimiento", "/donde-poner-el-dinero", "/contribucion"),
        "donde-poner-el-dinero": ("Rendimiento", "/contribucion", "/donde-poner-el-dinero"),
        "precios": ("Catálogo", "/reputacion", "/familias"),
        "reputacion": ("Catálogo", "/precios", "/familias"),
        "familias": ("Catálogo", "/precios", "/reputacion"),
    }
    for pantalla, (grupo, primera, ultima) in esperado.items():
        html = _base(pantalla)
        assert f'Secciones de {grupo}' in html
        assert f'href="{primera}"' in html
        assert f'href="{ultima}"' in html
        assert 'class="subnavegacion"' in html


def test_crear_campana_es_accion_y_configuracion_es_destino():
    campanas = _base("campanas")
    settings = _base("settings")
    assert 'href="/campanas/nuevas"' in campanas
    assert 'aria-label="Secciones de Campañas"' not in campanas
    assert 'href="/settings"' in settings
    assert 'aria-current="page"' in settings
```

- [ ] **Step 2: Confirmar que las pruebas nuevas fallan**

Run: `uv run --frozen python -m pytest tests/test_ui.py::test_ui_menu_movil_agrupado tests/test_ui_navegacion.py -q`
Expected: FAIL por seis destinos esperados frente a la barra actual y ausencia de `_subnavegacion.html`.

- [ ] **Step 3: Implementar el mapa y la subnavegación**

En `base.html`, sustituir las listas Panel/Decidir/Catálogo por un mapa único. Mantener los títulos propios de cada pantalla y cambiar Settings visible a Configuración:

```jinja2
{% set grupos = {
  'oportunidades': ('Oportunidades', [
    ('cortes', '/cortes', 'Propuestas'),
    ('gasto-sin-venta', '/gasto-sin-venta', 'Gasto sin venta'),
    ('busquedas-sin-venta', '/busquedas-sin-venta', 'Búsquedas sin vender'),
    ('keywords-danadas', '/keywords-danadas', 'Keywords dañadas'),
    ('productos-sin-clics', '/productos-sin-clics', 'Productos sin clics'),
    ('inertes', '/inertes', 'Inertes')
  ]),
  'decisiones': ('Decisiones', [
    ('decisiones', '/decisiones', 'Historial'),
    ('ruido', '/ruido', 'Detalle de ciclos')
  ]),
  'rendimiento': ('Rendimiento', [
    ('contribucion', '/contribucion', 'Contribución'),
    ('donde-poner-el-dinero', '/donde-poner-el-dinero', 'Dónde poner el dinero')
  ]),
  'catalogo': ('Catálogo', [
    ('precios', '/precios', 'Precios'),
    ('reputacion', '/reputacion', 'Reputación'),
    ('familias', '/familias', 'Familias')
  ])
} %}
{% set seccion = {
  'cortes': 'oportunidades', 'gasto-sin-venta': 'oportunidades',
  'busquedas-sin-venta': 'oportunidades', 'keywords-danadas': 'oportunidades',
  'productos-sin-clics': 'oportunidades', 'inertes': 'oportunidades',
  'decisiones': 'decisiones', 'ruido': 'decisiones',
  'contribucion': 'rendimiento', 'donde-poner-el-dinero': 'rendimiento',
  'precios': 'catalogo', 'reputacion': 'catalogo', 'familias': 'catalogo'
}.get(pantalla) %}
```

Crear `_subnavegacion.html`:

```jinja2
{% if grupo %}
<nav class="subnavegacion" aria-label="Secciones de {{ grupo[0] }}">
  {% for id, href, nombre in grupo[1] %}
  <a href="{{ href }}"{% if pantalla == id %} aria-current="page"{% endif %}>{{ nombre }}</a>
  {% endfor %}
</nav>
{% endif %}
```

Incluirla inmediatamente después de `header.pagina-header` y antes de `main`:

```jinja2
{% include '_subnavegacion.html' with context %}
```

Antes del include, asignar `grupo` desde el mapa:

```jinja2
{% set grupo = grupos[seccion] if seccion else none %}
```

El sidebar tendrá enlaces directos a Resumen, Campañas, Oportunidades (`/cortes`), Decisiones, Rendimiento (`/contribucion`), Catálogo (`/precios`), Salud y Configuración. Cuando `seccion` corresponda al grupo, el enlace padre usará la clase activa y `aria-current="true"`; el enlace exacto de la subnavegación usará `aria-current="page"`. Conservar el contador con `id="propuestas-contador"` en Oportunidades y el de Reputación en Catálogo. El botón Crear campaña de Campañas seguirá usando `cabecera_accion`.

- [ ] **Step 4: Implementar cinco controles móviles y «Más»**

La barra tendrá cuatro enlaces con `.tab-label` y un botón «Más»; el menú superior contendrá los demás destinos:

```html
<button type="button" id="nav-mas-toggle" class="tab" aria-expanded="false" aria-controls="nav-mas">
  <span class="tab-label">Más</span>
</button>
<nav id="nav-mas" class="nav-mas" aria-label="Más secciones" hidden>
  <a href="/contribucion">Rendimiento</a>
  <a href="/precios">Catálogo</a>
  <a href="/salud">Salud</a>
  <a href="/settings">Configuración</a>
</nav>
```

En `shell.js`, registrar esta función antes del guard de badges para que el menú no dependa de las APIs:

```javascript
function configurarMas() {
  const boton = document.getElementById("nav-mas-toggle");
  const menu = document.getElementById("nav-mas");
  if (!boton || !menu) return;
  function cerrar() {
    menu.hidden = true;
    boton.setAttribute("aria-expanded", "false");
  }
  boton.addEventListener("click", function () {
    const abrir = menu.hidden;
    menu.hidden = !abrir;
    boton.setAttribute("aria-expanded", abrir ? "true" : "false");
    if (abrir) menu.querySelector("a").focus();
  });
  menu.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      cerrar();
      boton.focus();
    }
  });
  document.addEventListener("click", function (event) {
    if (!menu.hidden && !menu.contains(event.target) && event.target !== boton) cerrar();
  });
}
configurarMas();
```

Añadir CSS para `.subnavegacion` como fila desplazable de enlaces GET, `.nav-mas` anclado sobre la barra y estados activos. Mantener `position: fixed` en `.tab-bar`, safe-area y cuatro enlaces más el botón con la misma anchura.

- [ ] **Step 5: Ejecutar contratos de navegación y CSP**

Run: `uv run --frozen python -m pytest tests/test_ui_navegacion.py tests/test_ui.py::test_ui_menu_movil_agrupado tests/test_ui.py::test_ui_templates_compatibles_con_csp_self tests/test_ui_assets.py -q`
Expected: PASS.

- [ ] **Step 6: Actualizar el grafo y commit**

Run: `graphify update .`

```bash
git add app/templates/base.html app/templates/_subnavegacion.html app/static/js/shell.js app/static/css/dashboard.css tests/test_ui.py tests/test_ui_navegacion.py
git commit -m "feat(ui): agrupa navegacion por tareas"
```

---

### Task 3: Resumen A2 con datos y contrato actuales

**Files:**
- Modify: `app/templates/resumen.html`
- Modify: `app/static/css/resumen.css`
- Modify: `app/static/js/dashboard.js` solo si cambian selectores de gráficas
- Test: `tests/test_ui.py`
- Create: `tests/test_ui_resumen_a2.py`
- Verify: `.cursor/skills/verify-orbit/features/resumen.md`

**Interfaces:**
- Consumes: `contrato`, `filtro`, `vista_resumen`, `vista.que_revisar`, `totales_por_moneda`, `canvas#tendencia-<canal>` y `script#datos-resumen`.
- Produces: `.resumen-pendientes`, `.resumen-monedas-principales`, `.resumen-principal`, `.resumen-canales` y el orden visual aprobado; conserva los ids de canvas y datos.

- [ ] **Step 1: Escribir la prueba de orden y lenguaje**

Crear `tests/test_ui_resumen_a2.py` reutilizando el contexto existente:

```python
from test_ui import _ctx_resumen_con_payload
from app import ui


def test_resumen_a2_prioriza_pendientes_y_evita_saludos():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_resumen_con_payload())
    assert "<h1>Resumen</h1>" in html
    assert 'class="accion-primaria" href="/campanas/nuevas"' in html
    assert "Hola, David" not in html
    assert "Esto es lo más importante" not in html
    assert "Pendientes de revisión" in html
    assert "Ventas atribuidas" in html
    assert "Canales" in html
    assert 'class="resumen-pendientes superficie"' in html
    assert 'class="resumen-monedas-principales"' in html
    assert html.index("resumen-pendientes") < html.index("resumen-monedas-principales")
    assert html.index("resumen-monedas-principales") < html.index("chart-card")
    assert 'id="datos-resumen"' in html
    assert 'class="custom-range"' in html
```

- [ ] **Step 2: Confirmar el rojo**

Run: `uv run --frozen python -m pytest tests/test_ui_resumen_a2.py -q`
Expected: FAIL por las clases nuevas y el orden anterior.

- [ ] **Step 3: Reordenar la plantilla sin crear métricas**

Añadir la acción real al encabezado, después del bloque `estilos`:

```jinja2
{% block cabecera_accion %}
<a class="accion-primaria" href="/campanas/nuevas">Crear campañas</a>
{% endblock %}
```

Conservar el bloque `.resumen-filter-bar` actual como primer elemento de contenido. Sustituir la sección actual `sec-revisar` por este bloque, que usa las mismas variables y enlaces:

```jinja2
<section class="resumen-pendientes superficie" aria-labelledby="sec-revisar">
  <div class="seccion-encabezado"><h2 id="sec-revisar">Pendientes de revisión</h2></div>
  <div class="actions-card">
    {% for item in vista.que_revisar %}
    <div class="action-row">
      <span class="action-rank">{{ loop.index }}</span>
      <div class="action-body">
        <div class="action-title">{{ item.titulo }}</div>
        <div class="action-desc">{{ item.evidencia }}</div>
        {% if item.caveat %}<div class="action-caveat">{{ item.caveat }}</div>{% endif %}
      </div>
      <div class="action-links">
        {% for texto_link, href_link in item.enlaces %}
        <a class="action-link" href="{{ href_link }}">{{ texto_link }}</a>
        {% endfor %}
      </div>
    </div>
    {% else %}
    <p class="mutado">Sin alertas en la ventana seleccionada.</p>
    {% endfor %}
  </div>
</section>
```

Colocar después las métricas por moneda, sin sumar grupos:

```jinja2
<section class="resumen-monedas-principales" aria-label="Métricas por moneda">
  {% for grupo in contrato.totales_por_moneda %}
  <article class="resumen-moneda" data-moneda="{{ grupo.moneda }}">
    <h2>Totales {{ grupo.moneda }}</h2>
    <div class="grupo-metricas">
      {% for clave, etiqueta, tipo in metricas %}
      <div class="kpi">
        <span class="kpi-etiqueta">{{ etiqueta }}</span>
        <strong class="kpi-valor">{{ cifra(grupo[clave], tipo) }}</strong>
        {% if tipo == 'dinero' %}<span class="kpi-contexto">{{ grupo.moneda }}</span>{% endif %}
      </div>
      {% endfor %}
    </div>
  </article>
  {% endfor %}
</section>
```

Después de las métricas, abrir `<div class="resumen-principal">`. Mover dentro la sección actual `sec-tendencia`, conservando íntegros `.chart-card`, sus tabs, canvas y notas; mover la sección actual `sec-canales` a un `<aside class="resumen-canales">` dentro del mismo grid. Cerrar el grid antes de `sec-detalle`. Mover la sección actual `sec-monedas` dentro de `sec-detalle`, antes de las tablas plegadas, para no duplicar los totales visibles. Mantener `data-canal-card`, `data-tab-canal`, `data-panel-canal`, `tendencia-<canal>`, `serie-<canal>` y `datos-resumen`. Mantener cobertura, madurez, comparación y ausencia de datos junto a cada cifra.

Añadir `.sr-only` a `dashboard.css` si no existe:

```css
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
```

En `resumen.css`, convertir `.resumen-principal` en grid `minmax(0, 1.75fr) minmax(260px, 1fr)` a más de 1050 px y una columna por debajo. Las métricas usan cuatro columnas solo cuando hay espacio, dos en tableta y dos a 390 px. No añadir tabs de ACoS ni datos nuevos a la gráfica.

- [ ] **Step 4: Ejecutar las regresiones del Resumen**

Run: `uv run --frozen python -m pytest tests/test_ui_resumen_a2.py tests/test_ui.py tests/test_ui_metricas.py tests/test_ui_tema.py -q`
Expected: PASS, incluidos XSS, monedas, huecos y CSP.

- [ ] **Step 5: Verificar el Resumen real y actualizar su ficha**

Actualizar `features/resumen.md` con el nuevo orden, `.subnavegacion`, `.resumen-pendientes`, `.resumen-monedas-principales` y la barra móvil de cinco controles. Conservar los contratos de filtros, canvas y API.

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify launch --run-id rediseno-resumen
.cursor/skills/verify-orbit/helpers/orbit-verify doctor --run-id rediseno-resumen
.cursor/skills/verify-orbit/helpers/orbit-verify drive-resumen --run-id rediseno-resumen
.cursor/skills/verify-orbit/helpers/orbit-verify cleanup --run-id rediseno-resumen
```

Expected: `PROOF.json` con `ok: true`, capturas de escritorio, 390 px y noche; cuerpo sin overflow y tabla con scroll propio.

- [ ] **Step 6: Actualizar el grafo y commit**

Run: `graphify update .`

```bash
git add app/templates/resumen.html app/static/css/resumen.css app/static/css/dashboard.css app/static/js/dashboard.js tests/test_ui.py tests/test_ui_resumen_a2.py .cursor/skills/verify-orbit/features/resumen.md
git commit -m "feat(ui): rediseña resumen con jerarquia A2"
```

---

### Task 4: Campañas y creación de campañas

**Files:**
- Modify: `app/templates/campanas.html`
- Modify: `app/templates/fabrica.html`
- Modify: `app/static/css/campanas.css`
- Modify: `app/static/css/fabrica.css`
- Test: `tests/test_ui_copy_campana.py`
- Test: `tests/test_ui_fabrica.py`
- Test: `tests/test_ui_tema.py`
- Verify: `.cursor/skills/verify-orbit/features/campanas.md`
- Verify: `.cursor/skills/verify-orbit/features/fabrica.md`

**Interfaces:**
- Consumes: `presentacion`, `filas`, `vista`, `form.filtros`, `details.filtros-avanzados`, `.tabla-scroll`, `#fabrica-plan` y los ids actuales de fábrica.
- Produces: `.campanas-resumen`, `.campanas-lista.superficie`, una sola `a.accion-primaria[href="/campanas/nuevas"]` y secciones progresivas de fábrica sin cambiar nombres de campos.

- [ ] **Step 1: Añadir regresiones de estructura y acción**

Añadir a `tests/test_ui_tema.py`, usando su helper `_html_sin_db`:

```python
def test_campanas_a2_tiene_una_accion_principal_y_conserva_filtros(monkeypatch):
    html = _html_sin_db(
        monkeypatch, "/campanas", campanas=lambda conn, platform=None: {"items": []}
    )
    assert html.count('href="/campanas/nuevas"') == 1
    assert "campanas-resumen" in html
    assert 'class="filtros"' in html
    assert 'class="filtros-avanzados"' in html
    assert 'class="tabla-scroll"' in html
    assert 'aria-label="Vista rápida"' in html
```

En `tests/test_ui_fabrica.py`, añadir:

```python
def test_fabrica_conserva_flujo_con_accion_principal_unica():
    html = TestClient(app).get("/campanas/nuevas").text
    assert 'id="fabrica-plan"' in html
    assert 'id="fabrica-productos"' in html
    assert 'id="fabrica-comparador"' in html
    assert 'class="fabrica-resumen"' in html
    assert html.count("Crear las cinco campañas") == 1
    assert "Hola, David" not in html
```

- [ ] **Step 2: Confirmar los rojos**

Run: `uv run --frozen python -m pytest tests/test_ui_tema.py::test_campanas_a2_tiene_una_accion_principal_y_conserva_filtros tests/test_ui_fabrica.py::test_fabrica_conserva_flujo_con_accion_principal_unica -q`
Expected: FAIL solo en las nuevas clases o en la acción primaria; las pruebas de comportamiento previas deben seguir llegando a sus aserciones.

- [ ] **Step 3: Aplicar la estructura A2 a Campañas**

Aplicar tres cambios exactos a `campanas.html`, sin mover los bucles ni formularios:

```jinja2
{% block cabecera_accion %}
<a class="btn-crear accion-primaria" href="/campanas/nuevas">Crear campañas</a>
{% endblock %}
```

```html
<section class="bloque-targets campanas-resumen" aria-labelledby="h-plat">
```

```html
<section class="lista-campanas campanas-lista superficie" aria-labelledby="h-lista">
```

Mantener el aviso `.aviso-inmaduro`, las vistas rápidas, `.filtros`, `.filtros-avanzados`, `.tabla-scroll` y la tabla dentro de `.campanas-lista`. No cambiar filtros GET, `aria-sort`, filas, chips, targets ni textos de procedencia. Mantener `min-width: 1100px` de la tabla en móvil. En `campanas.css`, aplicar `padding: 20px` a `.campanas-lista` y conservar el scroll en `.tabla-scroll`, no en `.campanas-lista`.

- [ ] **Step 4: Aplicar progresión visual a la fábrica**

Agrupar los bloques existentes en este orden: plataforma y modo, productos, tipos de campaña, presupuestos/metas, comparador y resumen. Añadir `class="fabrica-resumen"` al bloque que ya muestra selección y totales. Usar `<details>` solo para opciones avanzadas ya existentes; no plegar plataforma, productos, presupuesto, token ni confirmación. Mantener todos los ids, `name`, botones y textos de error. El botón `#fabrica-crear-boton` conserva el texto «Crear las cinco campañas» y su handler actual.

- [ ] **Step 5: Ejecutar pruebas y drives reales**

Run: `uv run --frozen python -m pytest tests/test_ui_copy_campana.py tests/test_ui_fabrica.py tests/test_ui_tema.py tests/test_ui_assets.py -q`
Expected: PASS.

Actualizar ambas fichas con las clases nuevas y ejecutar:

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify launch --run-id rediseno-campanas
.cursor/skills/verify-orbit/helpers/orbit-verify doctor --run-id rediseno-campanas
.cursor/skills/verify-orbit/helpers/orbit-verify drive-campanas --run-id rediseno-campanas
.cursor/skills/verify-orbit/helpers/orbit-verify drive-fabrica --run-id rediseno-campanas
.cursor/skills/verify-orbit/helpers/orbit-verify cleanup --run-id rediseno-campanas
```

Expected: ambos `PROOF.json` con `ok: true`; filtros, orden, selector, comparador y APIs sin cambios.

- [ ] **Step 6: Actualizar el grafo y commit**

Run: `graphify update .`

```bash
git add app/templates/campanas.html app/templates/fabrica.html app/static/css/campanas.css app/static/css/fabrica.css tests/test_ui_copy_campana.py tests/test_ui_fabrica.py tests/test_ui_tema.py .cursor/skills/verify-orbit/features/campanas.md .cursor/skills/verify-orbit/features/fabrica.md
git commit -m "feat(ui): rediseña campañas y fabrica"
```

---

### Task 5: Oportunidades con patrón común y advertencias intactas

**Files:**
- Modify: `app/templates/cortes.html`
- Modify: `app/templates/gasto_sin_venta.html`
- Modify: `app/templates/busquedas_sin_venta.html`
- Modify: `app/templates/keywords_danadas.html`
- Modify: `app/templates/productos_sin_clics.html`
- Modify: `app/templates/inertes.html`
- Modify: `app/templates/_senal.html`
- Modify: `app/static/css/dashboard.css`
- Test: `tests/test_ui.py`
- Test: `tests/test_pantalla_busquedas.py`
- Test: `tests/test_pantalla_danadas.py`
- Test: `tests/test_pantalla_productos.py`
- Test: `tests/test_ui_productos.py`
- Test: `tests/test_jev_pantallas.py`
- Create: `tests/test_ui_oportunidades.py`

**Interfaces:**
- Consumes: la subnavegación de Task 2 y todos los formularios, tablas, detalles, botones y datos actuales.
- Produces: `.oportunidad-resumen`, `.oportunidad-contenido` y `.oportunidad-detalle` en las seis plantillas, sin selectores funcionales nuevos.

- [ ] **Step 1: Escribir el contrato común**

Crear `tests/test_ui_oportunidades.py`:

```python
import pytest
from app import ui


@pytest.mark.parametrize(
    "archivo",
    [
        "cortes.html",
        "gasto_sin_venta.html",
        "busquedas_sin_venta.html",
        "keywords_danadas.html",
        "productos_sin_clics.html",
        "inertes.html",
    ],
)
def test_oportunidades_comparten_jerarquia_visual(archivo):
    fuente = (ui._TEMPLATES_DIR / archivo).read_text(encoding="utf-8")
    assert 'class="oportunidad-resumen' in fuente
    assert 'class="oportunidad-contenido' in fuente
    assert 'class="oportunidad-detalle' in fuente
    assert "Hola, David" not in fuente
```

Añadir a `tests/test_ui.py`, usando `_ctx_propuestas()`:

```python
def test_propuestas_a2_conserva_vencimiento_e_irreversibilidad():
    html = ui.templates.env.get_template("cortes.html").render(**_ctx_propuestas())
    assert 'class="oportunidad-resumen' in html
    assert "se aplica solo" in html
    assert "no se puede deshacer" in html
    assert 'data-vetar=' in html or 'data-veto=' in html
```

- [ ] **Step 2: Confirmar el rojo**

Run: `uv run --frozen python -m pytest tests/test_ui_oportunidades.py tests/test_ui.py::test_propuestas_a2_conserva_vencimiento_e_irreversibilidad -q`
Expected: FAIL por las tres clases ausentes.

- [ ] **Step 3: Aplicar la estructura a cada pantalla**

En cada plantilla, envolver el contenido ya existente en tres regiones. Los selectores de apertura serán exactamente `<section class="oportunidad-resumen superficie">`, `<section class="oportunidad-contenido">` y `<section class="oportunidad-detalle">`; cada una se cierra después de los bloques asignados abajo. Añadir `aria-labelledby` cuando la región contenga un encabezado con id y, si no lo tiene, usar `aria-label` con el nombre de la pantalla.

Añadir a `dashboard.css`:

```css
.oportunidad-resumen { padding: 20px; }
.oportunidad-contenido { margin-top: 20px; }
.oportunidad-detalle { margin-top: 16px; }
.oportunidad-contenido > .tabla-scroll,
.oportunidad-detalle > .tabla-scroll { width: 100%; }
```

Asignaciones específicas:

- `cortes.html`: resumen = explicación y conteos de Recorta/Crece/Campañas; contenido = propuestas y acciones; detalle = bloqueos y evidencia. Mantener `data-vetar`, `data-veto`, `data-bloqueo`, token, días y confirmación.
- `gasto_sin_venta.html`: resumen = gasto y agrupación; contenido = lecturas principales; detalle = filas por búsqueda. Mantener monedas separadas.
- `busquedas_sin_venta.html`: resumen = alcance de 90 días y conteos; contenido = filtros/tabla; detalle = agrupaciones y evidencia.
- `keywords_danadas.html`: resumen = keywords afectadas; contenido = tabla y filtros; detalle = antes/después y motivos.
- `productos_sin_clics.html`: resumen = candidatos e impulsos; contenido = decisiones por producto; detalle = pausa, límites y motivos. Mantener todos los `data-*` y formularios.
- `inertes.html`: resumen = clasificación y antigüedad; contenido = lotes; detalle = criterios de archivable. No convertir «Antigüedad cumplida» en «archivable».
- `_senal.html`: adoptar las clases de fila de acción sin cambiar el macro, sus argumentos ni sus enlaces.

No ocultar tablas ni detalles por CSS; las regiones solo cambian jerarquía y espaciado.

- [ ] **Step 4: Ejecutar todas las regresiones del grupo**

Run: `uv run --frozen python -m pytest tests/test_ui_oportunidades.py tests/test_ui.py tests/test_pantalla_busquedas.py tests/test_pantalla_danadas.py tests/test_pantalla_productos.py tests/test_ui_productos.py tests/test_jev_pantallas.py -q`
Expected: PASS, incluidos XSS, veto, bloqueos, pausa y traducciones.

- [ ] **Step 5: Actualizar el grafo y commit**

Run: `graphify update .`

```bash
git add app/templates/cortes.html app/templates/gasto_sin_venta.html app/templates/busquedas_sin_venta.html app/templates/keywords_danadas.html app/templates/productos_sin_clics.html app/templates/inertes.html app/templates/_senal.html app/static/css/dashboard.css tests/test_ui.py tests/test_ui_oportunidades.py tests/test_pantalla_busquedas.py tests/test_pantalla_danadas.py tests/test_pantalla_productos.py tests/test_ui_productos.py tests/test_jev_pantallas.py
git commit -m "feat(ui): unifica pantallas de oportunidades"
```

---

### Task 6: Decisiones y Rendimiento

**Files:**
- Modify: `app/templates/decisiones.html`
- Modify: `app/templates/ruido.html`
- Modify: `app/templates/contribucion.html`
- Modify: `app/templates/donde_poner_el_dinero.html`
- Modify: `app/static/css/decisiones.css`
- Modify: `app/static/css/contribucion.css`
- Modify: `app/static/css/dashboard.css`
- Test: `tests/test_ui.py`
- Test: `tests/test_pantalla_ruido.py`
- Test: `tests/test_ui_contribucion.py`
- Test: `tests/test_pantalla_dinero.py`
- Create: `tests/test_ui_decisiones_rendimiento_a2.py`
- Verify: `.cursor/skills/verify-orbit/features/decisiones.md`
- Verify: `.cursor/skills/verify-orbit/features/contribucion.md`
- Verify: `.cursor/skills/verify-orbit/features/dinero.md`

**Interfaces:**
- Consumes: `.decisiones-filtros`, `.decisiones-dia`, `.decisiones-grupo`, `.paginador`, `.contribucion-*`, formularios y datos actuales.
- Produces: `.historial-resumen`, `.rendimiento-resumen` y superficies A2 sin cambiar gráficos SVG ni enlaces.

- [ ] **Step 1: Añadir contratos de lectura**

Crear `tests/test_ui_decisiones_rendimiento_a2.py`:

```python
from app import ui


def test_decisiones_prioriza_historial_y_conserva_filtros():
    fuente = (ui._TEMPLATES_DIR / "decisiones.html").read_text(encoding="utf-8")
    assert 'class="historial-resumen' in fuente
    assert 'class="decisiones-filtros' in fuente
    assert 'class="decisiones-dia' in fuente
    assert 'class="paginador' in fuente
    assert "Detalle de ciclos" not in fuente


def test_ruido_y_rendimiento_usan_superficies_sin_cambiar_datos():
    ruido = (ui._TEMPLATES_DIR / "ruido.html").read_text(encoding="utf-8")
    contribucion = (ui._TEMPLATES_DIR / "contribucion.html").read_text(encoding="utf-8")
    dinero = (ui._TEMPLATES_DIR / "donde_poner_el_dinero.html").read_text(encoding="utf-8")
    assert 'class="historial-resumen' in ruido
    assert 'class="rendimiento-resumen' in contribucion
    assert 'class="rendimiento-resumen' in dinero
    assert 'class="contribucion-plataformas' in contribucion
    assert "/donde-poner-el-dinero?plataforma=amazon_us" in dinero
    assert "por_campana" in dinero
```

- [ ] **Step 2: Confirmar el rojo**

Run: `uv run --frozen python -m pytest tests/test_ui_decisiones_rendimiento_a2.py -q`
Expected: FAIL por las clases nuevas.

- [ ] **Step 3: Aplicar las estructuras específicas**

- `decisiones.html`: `.historial-resumen` contiene conteos y tabs; los filtros quedan inmediatamente después; las secciones por fecha conservan `details.decisiones-grupo`, anterior → nuevo y paginador.
- `ruido.html`: `.historial-resumen` contiene totales por ciclo y explicación; conservar decisiones, abstenciones y reducción de cambios en el detalle.
- `contribucion.html`: `.rendimiento-resumen` contiene rango y tarjetas por plataforma; mantener `.contribucion-barra`, `.contribucion-rango`, `.contribucion-punto`, `.contribucion-real`, `.contribucion-eje` y tablas.
- `donde_poner_el_dinero.html`: `.rendimiento-resumen` contiene gasto/ACoS por tipo; mantener formularios de presupuesto, casillas de confirmación, avisos y tablas.

Aplicar estas aperturas y cierres sin alterar las expresiones internas:

- En `decisiones.html` y `ruido.html`, abrir `<section class="historial-resumen superficie">` antes del bloque de conteos y cerrarlo antes de los filtros o del historial.
- En `contribucion.html`, cambiar la apertura actual a `<section class="contribucion-plataformas rendimiento-resumen" aria-label="Resumen por plataforma">`; conservar el cierre actual.
- En `donde_poner_el_dinero.html`, abrir `<section class="rendimiento-resumen superficie">` antes del párrafo introductorio y cerrarlo después de la primera tabla por tipo. Mantener los enlaces de plataforma y las tablas siguientes fuera del resumen.
- Añadir `seccion-encabezado` a `.lista-cabeza`, `.decisiones-cabeza` y `.contribucion-cab` cuando esos contenedores estén presentes; conservar sus clases actuales.

Aplicar `.superficie` a tarjetas principales y conservar los estilos de modo noche en `decisiones.css` y `contribucion.css`. No crear periodos, conteos ni variables de presentación nuevas.

- [ ] **Step 4: Ejecutar pruebas y drives**

Run: `uv run --frozen python -m pytest tests/test_ui_decisiones_rendimiento_a2.py tests/test_ui.py tests/test_pantalla_ruido.py tests/test_ui_contribucion.py tests/test_pantalla_dinero.py -q`
Expected: PASS.

Actualizar las tres fichas y ejecutar:

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify launch --run-id rediseno-rendimiento
.cursor/skills/verify-orbit/helpers/orbit-verify doctor --run-id rediseno-rendimiento
.cursor/skills/verify-orbit/helpers/orbit-verify drive-decisiones --run-id rediseno-rendimiento
.cursor/skills/verify-orbit/helpers/orbit-verify drive-contribucion --run-id rediseno-rendimiento
.cursor/skills/verify-orbit/helpers/orbit-verify cleanup --run-id rediseno-rendimiento
```

Expected: `ok: true`; capturas de contribución a escritorio y 390 px sin overflow.

- [ ] **Step 5: Actualizar el grafo y commit**

Run: `graphify update .`

```bash
git add app/templates/decisiones.html app/templates/ruido.html app/templates/contribucion.html app/templates/donde_poner_el_dinero.html app/static/css/decisiones.css app/static/css/contribucion.css app/static/css/dashboard.css tests/test_ui.py tests/test_ui_decisiones_rendimiento_a2.py tests/test_pantalla_ruido.py tests/test_ui_contribucion.py tests/test_pantalla_dinero.py .cursor/skills/verify-orbit/features/decisiones.md .cursor/skills/verify-orbit/features/contribucion.md .cursor/skills/verify-orbit/features/dinero.md
git commit -m "feat(ui): rediseña decisiones y rendimiento"
```

---

### Task 7: Catálogo, Salud y Configuración

**Files:**
- Modify: `app/templates/precios.html`
- Modify: `app/templates/_precios_goals.html`
- Modify: `app/templates/_precios_seguridad.html`
- Modify: `app/templates/reputacion.html`
- Modify: `app/templates/familias.html`
- Modify: `app/templates/salud.html`
- Modify: `app/templates/settings.html`
- Modify: `app/static/css/salud.css`
- Modify: `app/static/css/dashboard.css`
- Test: `tests/test_precio_pantalla.py`
- Test: `tests/test_ui_reputacion.py`
- Test: `tests/test_familias.py`
- Test: `tests/test_ui.py`
- Test: `tests/test_settings_escritura.py`
- Create: `tests/test_ui_catalogo_sistema_a2.py`
- Verify: `.cursor/skills/verify-orbit/features/precios.md`
- Verify: `.cursor/skills/verify-orbit/features/salud.md`
- Verify: `.cursor/skills/verify-orbit/features/settings.md`

**Interfaces:**
- Consumes: formularios, ids `data-pantalla`, `.salud-*`, `#settings-token`, `[data-settings-config]`, `[data-settings-goal]` y controles de precios/reputación/familias.
- Produces: `.catalogo-resumen`, `.sistema-resumen` y formularios con una acción primaria por formulario, sin cambiar campos ni handlers.

- [ ] **Step 1: Añadir el contrato común**

Crear `tests/test_ui_catalogo_sistema_a2.py`:

```python
import pytest
from app import ui


@pytest.mark.parametrize("archivo", ["precios.html", "reputacion.html", "familias.html"])
def test_catalogo_usa_resumen_y_conserva_contenido(archivo):
    fuente = (ui._TEMPLATES_DIR / archivo).read_text(encoding="utf-8")
    assert 'class="catalogo-resumen' in fuente
    assert "Hola, David" not in fuente


@pytest.mark.parametrize("archivo", ["salud.html", "settings.html"])
def test_sistema_usa_resumen_y_no_oculta_formularios(archivo):
    fuente = (ui._TEMPLATES_DIR / archivo).read_text(encoding="utf-8")
    assert 'class="sistema-resumen' in fuente
    if archivo == "settings.html":
        assert 'id="settings-token"' in fuente
        assert "data-settings-config" in fuente
        assert "data-settings-goal" in fuente
    else:
        assert 'class="salud-plataformas' in fuente
        assert 'class="salud-tabs' in fuente
```

- [ ] **Step 2: Confirmar el rojo**

Run: `uv run --frozen python -m pytest tests/test_ui_catalogo_sistema_a2.py -q`
Expected: FAIL por las clases nuevas.

- [ ] **Step 3: Aplicar la estructura sin tocar operaciones**

- `precios.html`: `.catalogo-resumen` contiene compuerta, cobertura y conteos; conservar goals, seguridad, no evaluados, formularios y scripts.
- `_precios_goals.html` y `_precios_seguridad.html`: usar `.superficie` y `.seccion-encabezado`, manteniendo macros, campos y botones.
- `reputacion.html`: `.catalogo-resumen` contiene calificación y alertas; mantener `.rep-*`, estrellas, preguntas, tabs y contador.
- `familias.html`: `.catalogo-resumen` contiene explicación y conteos; mantener selector, árbol de dos niveles y formularios.
- `salud.html`: `.sistema-resumen` contiene tarjetas por plataforma; mantener `.salud-plataformas`, `.salud-tabs`, `.salud-ciclos-*`, canvas y `datos-skips`.
- `settings.html`: `.sistema-resumen` contiene alcance y estado; cada `form` conserva campos y un solo `button.accion-primaria[type="submit"]`. No cambiar token, nombres, validaciones ni textos de error.

En `precios.html`, `reputacion.html` y `familias.html`, abrir `<section class="catalogo-resumen superficie">` antes del primer bloque de conteos o estado y cerrarlo antes del contenido detallado. En `salud.html` y `settings.html`, usar `<section class="sistema-resumen superficie">` con la misma separación. Dentro de cada región, añadir `<div class="seccion-encabezado">` alrededor del encabezado y el estado o conteo ya existentes. No crear estados, conteos ni variables nuevas.

Añadir a `dashboard.css`:

```css
.catalogo-resumen,
.sistema-resumen { padding: 20px; margin-bottom: 20px; }
.catalogo-resumen > .seccion-encabezado,
.sistema-resumen > .seccion-encabezado { margin-bottom: 14px; }
```

- [ ] **Step 4: Ejecutar pruebas y drives**

Run: `uv run --frozen python -m pytest tests/test_ui_catalogo_sistema_a2.py tests/test_precio_pantalla.py tests/test_ui_reputacion.py tests/test_familias.py tests/test_ui.py tests/test_settings_escritura.py -q`
Expected: PASS.

Actualizar las fichas de Precios, Salud y Settings y ejecutar:

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify launch --run-id rediseno-sistema
.cursor/skills/verify-orbit/helpers/orbit-verify doctor --run-id rediseno-sistema
.cursor/skills/verify-orbit/helpers/orbit-verify drive-salud --run-id rediseno-sistema
.cursor/skills/verify-orbit/helpers/orbit-verify drive-settings --run-id rediseno-sistema
.cursor/skills/verify-orbit/helpers/orbit-verify cleanup --run-id rediseno-sistema
```

Expected: `ok: true`; canvas, tabs y formularios sin cambios funcionales.

- [ ] **Step 5: Actualizar el grafo y commit**

Run: `graphify update .`

```bash
git add app/templates/precios.html app/templates/_precios_goals.html app/templates/_precios_seguridad.html app/templates/reputacion.html app/templates/familias.html app/templates/salud.html app/templates/settings.html app/static/css/salud.css app/static/css/dashboard.css tests/test_precio_pantalla.py tests/test_ui_reputacion.py tests/test_familias.py tests/test_ui.py tests/test_settings_escritura.py tests/test_ui_catalogo_sistema_a2.py .cursor/skills/verify-orbit/features/precios.md .cursor/skills/verify-orbit/features/salud.md .cursor/skills/verify-orbit/features/settings.md
git commit -m "feat(ui): rediseña catalogo y sistema"
```

---

### Task 8: Verificación integral de las 18 rutas

**Files:**
- Modify: `.cursor/skills/verify-orbit/helpers/orbit-verify`
- Modify: `.cursor/skills/verify-orbit/SKILL.md`
- Modify: `.cursor/skills/verify-orbit/features/README.md`
- Modify: `.cursor/skills/verify-orbit/features/cortes.md`
- Modify: `.cursor/skills/verify-orbit/features/inertes.md`
- Modify: `.cursor/skills/verify-orbit/features/productos.md`
- Create: `.cursor/skills/verify-orbit/features/gasto_sin_venta.md`
- Create: `.cursor/skills/verify-orbit/features/busquedas_sin_venta.md`
- Create: `.cursor/skills/verify-orbit/features/keywords_danadas.md`
- Create: `.cursor/skills/verify-orbit/features/ruido.md`
- Create: `.cursor/skills/verify-orbit/features/reputacion.md`
- Create: `.cursor/skills/verify-orbit/features/familias.md`
- Modify: `tests/test_verify_orbit.py`
- Modify: `docs/diseno/orbit-ui.md`

**Interfaces:**
- Consumes: todos los drives existentes y las rutas HTML reales.
- Produces: `orbit-verify drive-secundarias`, evidencia por ruta y mapa actualizado de las 18 pantallas.

- [ ] **Step 1: Escribir la prueba del recorrido común**

Añadir a `tests/test_verify_orbit.py`:

```python
def test_drive_secundarias_recorre_rutas_sin_drive_especifico(dashboard):
    dashboard.cmd_drive_secundarias(argparse.Namespace(run_id="prueba"))
    prueba = json.loads((dashboard.EVIDENCE_ROOT / "prueba/secundarias/PROOF.json").read_text())
    assert prueba["ok"] is True
    assert set(prueba["pantallas"]) == {
        "gasto-sin-venta",
        "busquedas-sin-venta",
        "keywords-danadas",
        "ruido",
        "donde-poner-el-dinero",
        "reputacion",
        "familias",
        "precios",
    }
    for pantalla in prueba["pantallas"].values():
        assert pantalla["status"] == 200
        assert pantalla["nav_activa"] is True
```

- [ ] **Step 2: Confirmar el rojo**

Run: `uv run --frozen python -m pytest tests/test_verify_orbit.py::test_drive_secundarias_recorre_rutas_sin_drive_especifico -q`
Expected: FAIL con `AttributeError` porque el comando todavía no existe.

- [ ] **Step 3: Implementar `drive-secundarias` en el harness**

Añadir al helper un mapa y una función que use `_get_html` y `_evidence_dir`; no crear otro cliente HTTP:

```python
SECUNDARIAS = {
    "/gasto-sin-venta": "gasto-sin-venta",
    "/busquedas-sin-venta": "busquedas-sin-venta",
    "/keywords-danadas": "keywords-danadas",
    "/ruido": "ruido",
    "/donde-poner-el-dinero": "donde-poner-el-dinero",
    "/reputacion": "reputacion",
    "/familias": "familias",
    "/precios": "precios",
}


def cmd_drive_secundarias(args: argparse.Namespace) -> None:
    run_id = args.run_id or _latest_run_id()
    state = _load_state(run_id)
    base = state["base_url"]
    if any(h in base for h in FORBIDDEN_HOSTS) or state["port"] in FORBIDDEN_PORTS:
        _die(f"rehuso conducir {base}: no es instancia local de verificacion")
    evidence = _evidence_dir(run_id) / "secundarias"
    evidence.mkdir(parents=True, exist_ok=True)
    resultados = {}
    for ruta, pantalla in SECUNDARIAS.items():
        status, raw = _get_html(base, ruta)
        (evidence / f"{pantalla}.html").write_bytes(raw)
        html = raw.decode("utf-8", errors="replace")
        resultados[pantalla] = {
            "status": status,
            "nav_activa": (
                f'data-pantalla="{pantalla}"' in html
                and 'aria-current="page"' in html
                and f'href="{ruta}"' in html
            ),
        }
    prueba = {
        "feature": "secundarias",
        "entry_point": "subnavegacion",
        "base_url": base,
        "pantallas": resultados,
        "ok": all(
            item["status"] == 200 and item["nav_activa"]
            for item in resultados.values()
        ),
        "evidence_dir": str(evidence),
    }
    (evidence / "PROOF.json").write_text(
        json.dumps(prueba, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(prueba, indent=2))
    if not prueba["ok"]:
        _die("drive-secundarias FALLA")
```

Registrar el comando en el parser:

```python
p_secundarias = sub.add_parser(
    "drive-secundarias", help="Conduce las pantallas sin drive específico"
)
p_secundarias.set_defaults(func=cmd_drive_secundarias)
```

Añadir `cmd_drive_secundarias` a la secuencia de `maintain-verification-skill` sin eliminar los drives específicos.

- [ ] **Step 4: Actualizar mapa y fichas**

En `SKILL.md`, sustituir la descripción de siete tabs por cinco controles, documentar `#nav-mas-toggle`, `#nav-mas`, `.subnavegacion` y `drive-secundarias`. En `features/README.md`, enlazar las seis fichas nuevas y las ya existentes.

Cada ficha nueva debe incluir: propósito, entrada desde Resumen, subnavegación activa, selectores funcionales, comando curl, JSON gemelo cuando exista, estado vacío y operaciones POST prohibidas en baseline. Actualizar `docs/diseno/orbit-ui.md` con A2, mapa por tareas y el enlace a la especificación; conservar las reglas de monedas, cache y CSP.

- [ ] **Step 5: Ejecutar pruebas del harness y lint**

Run: `uv run --frozen python -m pytest tests/test_verify_orbit.py tests/test_ui_navegacion.py tests/test_ui_assets.py -q`
Expected: PASS.

Run: `ruff check --fix app tests tools && ruff format app tests tools`
Expected: sin errores; revisar el diff para no incorporar cambios ajenos.

Run: `pre-commit run --all-files`
Expected: PASS de secretos y conflictos.

- [ ] **Step 6: Ejecutar el pase real completo**

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify launch --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify doctor --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-resumen --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-campanas --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-decisiones --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-salud --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-contribucion --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-cortes --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-inertes --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-fabrica --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-settings --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-productos --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify drive-secundarias --run-id rediseno-final
.cursor/skills/verify-orbit/helpers/orbit-verify cleanup --run-id rediseno-final
```

Expected: todos los `PROOF.json` con `ok: true`; las 18 rutas responden 200, muestran navegación activa, conservan sus datos y no ejecutan POST. Revisar capturas de escritorio, 390 px y tema noche; comprobar apertura/cierre de «Más», foco y Escape en navegador real.

- [ ] **Step 7: Ejecutar la suite completa y limpiar artefactos**

Run: `uv run --frozen python -m pytest -q`
Expected: PASS; pruebas que dependen de PostgreSQL se ejecutan si el DSN local está disponible y se saltan solo por la condición ya diseñada.

Run: `graphify update .`
Run: `git status --short`

Eliminar únicamente logs, capturas temporales o archivos generados por esta ejecución que no pertenezcan a la evidencia ignorada del harness. No borrar evidencia previa ni archivos ajenos.

- [ ] **Step 8: Commit final**

```bash
git add .cursor/skills/verify-orbit docs/diseno/orbit-ui.md tests/test_verify_orbit.py
git commit -m "test(ui): verifica rediseño completo de pantallas"
```
