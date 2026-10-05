# Prototipos que cambiaron el diseño (2026-10-04)

Lecturas de producción con `orbit_read` y una corrida de Jev autorizada y
ejecutada por el dueño. Ventana: la de cortes del motor (30 días que terminan
10 días atrás), términos de texto, última observación por día. Las consultas
y el detalle por búsqueda viven en una carpeta privada del lead; aquí van los
resultados agregados. Las búsquedas citadas son términos de compradores, no
nombres de producto.

## 1. Fichas

Tras la tarea 0.2: 249 de 249 productos con anuncio ENABLED en MX y 119 de 119
en US tienen ficha vigente que cubre todos sus listings.

## 2. Roster

- Última corrida de estructura: diaria 06:45 UTC, 60 corridas, 0 fallidas.
- Todos los product ads ENABLED y PAUSED tienen `synced_at` igual o posterior
  al inicio de la última corrida (MX 4,773 + 1,579; US 4,090 + 464). Los
  únicos con fecha vieja son los ARCHIVED.
- Anuncios ENABLED sin producto: MX 1,168, todos en un solo grupo; US 920 en
  24 grupos. En todos, `ad_entity.listing_id IS NULL`.
- **El 100% del gasto de búsquedas de la ventana está en grupos donde todo
  anuncio activo tiene producto y ficha**: 22 grupos en MX y 11 en US.
  Productos por grupo con gasto: mediana 50 y 22; máximo 237 y 91.

## 3. Qué son las búsquedas que gastan sin vender

| Pares grupo-búsqueda con gasto | MX | US |
| --- | ---: | ---: |
| Parte del gasto en pares sin venta | 44.9% | 51.0% |
| ...de búsquedas que sí venden en otro grupo | 13.2% (19 búsquedas) | 25.4% (13 búsquedas) |
| ...de búsquedas que no venden en ningún grupo | 31.7% (361 búsquedas) | 25.6% (554 búsquedas) |

Las búsquedas de más gasto que no venden en ningún grupo son casi todas del
catálogo ("arras para boda catolica", "arras matrimoniales plata", "arras de
boda oro", "wedding arras coins set"). Las claramente ajenas son pocas y
chicas.

## 4. Los 18 pares `negative` que el motor ha propuesto

- 17 son búsquedas del catálogo; 1 es ajena.
- En 11 de las 17 la misma búsqueda vende en otro grupo de la plataforma.
- En 8 el propio grupo tiene ventas de esa búsqueda en todo el historial
  observado; el corte se propuso porque su ventana de 30 días no tenía venta.
- Los 2 negativos aplicados en live fueron sobre búsquedas del catálogo con
  ventas en otros grupos.

Consecuencia: un aviso "sí corresponde" saldría en casi todas las propuestas.
Lo que separa un bloqueo sano de uno peligroso son las ventas de la misma
búsqueda en otros grupos y el historial del propio grupo.

## 5. Prototipo con Jev

Las 25 búsquedas de más gasto por mercado que no venden en ningún grupo, cada
una contra todos los productos del grupo donde más gastó. 4,811 llamadas,
unos 6 M de tokens de entrada (cerca de 0.25 USD al precio de documentación),
9 fallos del proveedor y 11 abstenciones.

| Productos del grupo que corresponden | MX: búsquedas, % del gasto | US: búsquedas, % del gasto |
| --- | --- | --- |
| Ninguno (0 de n, sin fallos ni abstenciones) | 4, 10.7% | 1, 1.6% |
| Casi ninguno (hasta 15%) | 3, 8.9% | 0 |
| Una parte (por atributo: oro o plata, ley del metal) | 12, 48.1% | 10, 22.3% |
| Todos o casi todos (95% o más) | 6, 32.3% | 10, 71.5% |

Ejemplos:

- Ninguno: "caja para arras de boda" 0 de 50; "estuche para arras de boda sin
  arras" 0 de 177; "oro 24k" 0 de 1.
- Casi ninguno: "arras de boda personalizadas" 20 de 208 (los 20 son los
  productos personalizados del grupo); "cofre para arras de boda" 8 de 176
  (8 falsos "sí").
- Una parte: "arras de plata" 102 de 208; "arras de boda oro" 26 de 50. Jev
  separa oro de plata producto por producto.
- Todos: "arras para boda catolica" 177 de 177; "arras" 22 de 22.
- **Error de Jev:** una búsqueda de una sola palabra que no tiene que ver con
  el catálogo dio "sí" en 90 de 176 productos, porque esa palabra aparecía en
  la descripción del acabado de una familia entera. Coincidencia literal. El
  dueño aprobó cambiar esa frase de la ficha.

Implicaciones:

1. La proporción "k de n" es lo más informativo que da Jev. Un diseño que pare
   al primer "sí" tira ese dato; evaluar el grupo completo cuesta centavos.
2. "Ninguno" estricto es alcanzable pero frágil: 8 falsos "sí" de 176 sacan a
   una búsqueda de esa clase.
3. Cerca de la mitad del gasto sin venta de la muestra de MX es de búsquedas
   con atributo en grupos que mezclan productos dorados y plateados. No es
   una búsqueda ajena: es una señal de estructura de campañas o de ruteo.
4. Las fichas necesitan cuidado con palabras que también son búsquedas.

## 6. Otros datos

- `search_term_observation` guarda `observed_at` desde el 2026-08-23: una
  consulta "como se veía en la fecha X" es posible con esa profundidad.
- No hay tabla de negative keywords ingeridos. Lo único en base son los que
  Orbit aplicó: 2 `negative` y 6 harvest.
- Por el permiso por omisión de `0001`, `app_decide` y `app_ingest` pueden
  leer hoy `jev_revision`, `jev_par_evento` y `jev_ficha_version`. `app_jev`
  no puede leer `decision`, `apply_queue` ni `search_term_observation`.
- Precio de documentación de TypeSafe anotado en
  `docs/evidencia/jev-ads-01/how-why.md`: 0.042 USD por millón de tokens de
  entrada, salida gratis. Sin confirmar.
