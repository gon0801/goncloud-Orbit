# Rediseño completo de la interfaz de Orbit

Fecha: 2026-11-25
Estado: diseño aprobado en conversación, pendiente de revisión del documento

## Objetivo

Rediseñar todas las pantallas de Orbit para que sean más visuales, amigables y fáciles de navegar. La dirección elegida es la propuesta A2, con textos directos y sin saludos ni frases promocionales genéricas.

El cambio comprende presentación, arquitectura de información, navegación y organización de los controles. No cambia la lógica de negocio, los cálculos, las fuentes de datos, las monedas, los permisos ni las operaciones disponibles.

## Decisiones aprobadas

- Aplicar la apariencia A2 a toda la aplicación, no solo al Resumen.
- Reorganizar el menú por tareas y agrupar los reportes secundarios en subpestañas.
- Conservar todas las rutas y APIs actuales.
- Mantener Jinja2, CSS y JavaScript locales; no añadir un framework frontend.
- Conservar los temas claro y oscuro.
- Reducir el menú móvil a cinco destinos: Resumen, Campañas, Oportunidades, Decisiones y Más.
- Usar encabezados factuales como «Resumen»; no usar «Hola, David», «Tu negocio, más claro» ni textos equivalentes.
- Comprobar el resultado en todas las pantallas, en escritorio y móvil.

## Sistema visual

La interfaz usa fondos neutros suaves, superficies blancas, acento índigo, tarjetas redondeadas y sombras discretas de la propuesta A2. El color y la decoración deben reforzar la jerarquía, no competir con las cifras.

- Tipografía del sistema, sin descargas externas.
- Títulos entre 24 y 30 px en escritorio, ajustados en móvil.
- Cifras con `font-variant-numeric: tabular-nums` y moneda siempre visible.
- Radio de 12 a 18 px en superficies principales y de 8 a 12 px en controles.
- Acento índigo para navegación activa y acciones principales.
- Verde, ámbar y rojo reservados para estados semánticos, con texto además de color.
- Iconos SVG locales y sencillos. No se añaden ilustraciones, emoji ni iconos decorativos sin función.
- Los tokens compartidos viven en `app/static/css/dashboard.css`; los estilos específicos permanecen en los CSS de cada pantalla.
- El tema oscuro deriva de los mismos tokens y conserva contraste y significado de estados.

## Navegación

### Escritorio

El sidebar presenta estos destinos principales:

- **Resumen** — `/`.
- **Campañas** — `/campanas`.
- **Oportunidades** — `/cortes` como entrada inicial.
- **Decisiones** — `/decisiones`.
- **Rendimiento** — `/contribucion` como entrada inicial.
- **Catálogo** — grupo para Precios, Reputación y Familias.
- **Salud** — `/salud`.
- **Configuración** — `/settings`, al pie.

La creación de campañas es una acción dentro de Campañas, no otro destino principal. El contador de Oportunidades usa los datos existentes de propuestas; no se inventan contadores para reportes que no los tienen.

### Móvil

La barra inferior contiene Resumen, Campañas, Oportunidades, Decisiones y Más. «Más» abre una navegación accesible con Rendimiento, Catálogo, Salud y Configuración. El destino activo debe seguir siendo visible cuando el usuario está en una subpantalla.

### Mapa completo de rutas

Todas las rutas siguen disponibles y muestran su ubicación mediante navegación secundaria:

- **Campañas:** `/campanas` y `/campanas/nuevas`.
- **Oportunidades:** `/cortes`, `/gasto-sin-venta`, `/busquedas-sin-venta`, `/keywords-danadas`, `/productos-sin-clics` e `/inertes`.
- **Decisiones:** `/decisiones` y `/ruido`, presentado como «Detalle de ciclos».
- **Rendimiento:** `/contribucion` y `/donde-poner-el-dinero`.
- **Catálogo:** `/precios`, `/reputacion` y `/familias`.
- **Sistema:** `/salud` y `/settings`.

Las subpestañas son enlaces GET reales. No se crean rutas duplicadas ni redirecciones para simular la nueva agrupación.

## Estructura de las pantallas

### Resumen

Conserva el contrato actual y reorganiza su contenido en este orden:

1. Encabezado «Resumen», periodo y acción Crear campaña.
2. Filtros de canal y periodo, con la fecha de corte visible.
3. Pendientes de revisión, ordenados por el impacto ya calculado.
4. Métricas principales con comparación contra el periodo anterior.
5. Gráfica principal de ventas e inversión con las series actuales.
6. Estado por canal y accesos al detalle.
7. Totales por moneda y tablas detalladas plegadas.

No se mezclan MXN y USD. Los datos ausentes permanecen como `None` o raya, nunca como cero.

### Campañas

- Resumen de campañas y rendimiento en una superficie compacta.
- Filtros principales en una barra; filtros por métricas plegados.
- Tabla como protagonista, con orden, estados, metas y procedencia legibles.
- Crear campaña como única acción primaria del encabezado.
- En móvil, la tabla conserva su scroll propio, foco de teclado y las columnas necesarias para decidir.

### Oportunidades

Todas las pantallas del grupo comparten subnavegación y una estructura común:

1. Qué ocurrió.
2. Cuántas entidades o cuánto dinero implica, si el dato existe.
3. Qué acción puede tomar el usuario.
4. Detalle y evidencia.

Propuestas conserva vencimiento, rechazo irreversible y confirmaciones. Los reportes de gasto, keywords, productos e inertes conservan sus filtros, agrupaciones y explicaciones de elegibilidad.

### Decisiones

- Historial agrupado por fecha y filtros visibles.
- Cambios «antes → después» con moneda y contexto.
- Paginación y conteos actuales.
- «Detalle de ciclos» conserva la información actualmente llamada Ruido, sin ocultar abstenciones ni reducciones de cambios.

### Rendimiento

- Contribución presenta primero el rango, la contribución y la comparación visual por plataforma.
- Dónde poner el dinero usa resúmenes y gráficas antes del detalle tabular cuando esos datos ya existen.
- No se agregan conversiones de moneda ni métricas nuevas.

### Catálogo

Precios, Reputación y Familias comparten encabezado, subnavegación, filtros y estados. Cada pantalla conserva sus flujos, avisos y operaciones específicas.

### Salud y Configuración

Salud prioriza el estado actual, ciclos y plataformas; el detalle histórico queda plegado. Configuración agrupa metas, topes y ajustes en secciones claras, con una acción principal por formulario y sin alterar autenticación ni validaciones.

### Crear campaña y formularios

Se mantiene el flujo existente. Las secciones avanzadas se revelan progresivamente, la selección y el resumen son visibles y cada formulario tiene una sola acción primaria. No se simplifican confirmaciones ni se omiten advertencias de operaciones irreversibles.

## Componentes compartidos

El rediseño se implementa con bloques Jinja2, clases y utilidades ya compatibles con el stack:

- Encabezado de página con título, descripción factual y una acción principal opcional.
- Navegación principal, subnavegación y barra móvil.
- Barra de filtros y filtros avanzados plegables.
- Grupo de métricas y tarjeta individual.
- Lista de pendientes y filas de acción.
- Superficie de gráfica con leyenda y notas de cobertura.
- Tabla con scroll, orden, estados y paginación.
- Chips de estado, avisos y mensajes vacíos o de error.
- Campos, grupos de formulario, resumen y barra de acciones.

No se incorporan búsqueda global, centro de notificaciones, ayuda ni selectores que no tengan una función real. Los controles ilustrativos del mockup que no existen en Orbit no forman parte de la entrega.

## Arquitectura y datos

`app/templates/base.html` sigue siendo el shell común. Las plantillas específicas conservan sus contratos y bloques, con componentes parciales solo cuando eliminan repetición real.

`app/static/css/dashboard.css` contiene tokens, shell y componentes compartidos. Los archivos CSS por pantalla conservan únicamente variaciones propias. `app/static/js/shell.js` gestiona navegación móvil y estados del shell; el JavaScript específico actual sigue siendo el dueño de cada flujo.

Los datos continúan llegando por el render actual y por los endpoints existentes. No se añaden consultas SQL, APIs, dependencias de navegador ni fuentes de métricas. La CSP actual se mantiene: CSS y JavaScript externos, sin manejadores inline; los únicos bloques inline permitidos son los datos JSON inertes ya admitidos.

## Estados, errores y accesibilidad

- Los estados vacíos explican qué no hay y qué acción real existe.
- Los errores conservan el traductor actual y no muestran secretos ni trazas.
- Datos faltantes, cobertura incompleta, maduración y retraso de atribución permanecen visibles cerca de la cifra afectada.
- La navegación usa enlaces, `aria-current`, foco visible y un enlace para saltar al contenido.
- «Más» en móvil tiene apertura, cierre, foco y etiqueta accesibles; funciona sin depender de coordenadas o gestos.
- Los controles tienen objetivos táctiles de al menos 44 px en móvil.
- Las tablas mantienen regiones desplazables y acceso por teclado.
- Los temas claro y oscuro cumplen contraste legible para texto, estados, líneas y controles.

## Verificación

- Añadir o actualizar pruebas de regresión para la nueva navegación, agrupación, encabezados y componentes compartidos; cada regresión nueva debe fallar contra el código anterior.
- Ejecutar pruebas focalizadas de templates, frontend, accesibilidad existente, CSP y flujos modificados.
- Ejecutar la suite completa por tratarse de un cambio transversal de UI.
- Actualizar las fichas de `.cursor/skills/verify-orbit/features/` cuando cambien selectores, filtros o flujos.
- Lanzar una base desechable con `verify-orbit`, ejecutar `doctor` y recorrer todas las pantallas mediante sus drives.
- Capturar Resumen y pantallas representativas de cada tipo en escritorio y móvil; incluir temas claro y oscuro.
- Comprobar navegación principal, subpestañas, menú Más, filtros, paginación, formularios, estados vacíos y errores sin ejecutar operaciones destructivas.
- Confirmar que los JSON gemelos y las cifras visibles no cambian por el rediseño.

## Entregable

Todas las pantallas usan el sistema A2 y la navegación por tareas. El usuario puede llegar a cualquier ruta actual desde la nueva estructura, entender el estado principal de cada pantalla y encontrar una acción clara sin recorrer un menú de reportes sin agrupar. La aplicación conserva intactos sus datos, cálculos, contratos, permisos y operaciones.
