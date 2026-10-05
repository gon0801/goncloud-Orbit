# 0.2 — Fichas completas en MX y US (2026-10-04)

Adelantada por instrucción del dueño antes del visto bueno del plan: "primero
hay que hacer todas las fichas México y US 100% completas".

## Resultado

| Mercado | Productos con anuncio ENABLED | Con ficha completa antes | Después |
| --- | ---: | ---: | ---: |
| Amazon MX | 249 | 11 | 249 |
| Amazon US | 119 | 66 | 119 |

"Completa" es como lo cuenta el asesor: para cada listing del producto se
elige la ficha vigente más reciente que lo cubre, y todos sus listings tienen
que elegir la misma. Dos fichas distintas que entre las dos cubren todo no
cuentan. Consulta y salida: `cobertura.sql` y
`cobertura.salida.txt` (solo lectura, `orbit_read`). El "antes" es la lectura
de planificación (`../../planificacion/tamano.salida.txt`, sección 3).

Grupos con todos sus anuncios ligados a producto y con ficha de todos: 32 de 33
en MX y 24 de 48 en US. Los demás tienen anuncios sin producto ligado; eso es
la tarea 0.4, no falta de fichas.

## Método

El mismo del piloto. Las 291 fichas nuevas (238 de MX y 53 de US) se armaron
con las reglas que el dueño dio en el piloto, y el generador reprodujo
idénticas las 102 fichas ya aprobadas antes de usarse. El dueño contestó dos
preguntas para las 60 que no se podían armar sin él:

- "Solo Arras" (57 productos): son estuche con arras; el nombre es el modelo
  del estuche; las monedas pueden ser de 16, 19 o 22 mm. El tamaño quedó como
  dato desconocido en la ficha.
- Dos modelos de arras que faltaban en la lista de códigos (3 productos).

El dueño registró todo él mismo: ensayo en seco de 231 fichas (0 fallos),
aplicación de 231 (0 fallos) y aplicación de las 60 restantes (0 fallos).
Aprobador de todas: el dueño. Ningún nombre de producto ni SKU entra al repo;
las fichas viven en la base y sus insumos en una carpeta privada.

## Corrección posterior: una palabra de la ficha que también era una búsqueda

El prototipo con Jev (`../../diseno/prototipos.md`, sección 5) mostró un falso
"sí": una búsqueda de una sola palabra, ajena al catálogo, correspondía a 90 de
los 177 productos de su grupo (86 "no" y 1 fallo) porque esa palabra estaba en la frase del acabado de todos los
productos dorados. El dueño aprobó cambiar la frase. Se registró una versión
nueva de las 191 fichas de productos dorados (127 en MX y 64 en US, 0 fallos)
en la que solo cambia ese renglón. Comprobado en producción: ninguna ficha
vigente conserva la palabra y la cobertura sigue en 249 de 249 y 119 de 119.

Se repitió la misma búsqueda contra los 177 productos del mismo grupo: 0 "sí",
177 "no", sin abstenciones ni fallos. El resultado de grupo sigue saliendo
indeterminado, por las dos razones que el diseño de JEV ADS 02 corrige: el
grupo conserva productos solo archivados y su roster no está probado.

## Límites

- Todas vencen a los 90 días (`revisar_antes_de`): las primeras el 2027-01-02.
  Hoy nada avisa de fichas por vencer (hueco conocido del plan).
- 25 productos de US tenían una ficha que no cubría todos sus listings; se
  registró una versión nueva que sí los cubre. Las versiones anteriores siguen
  aprobadas y gana la más reciente.
- Dos productos no tienen nombre en el catálogo; su familia se tomó del SKU
  hermano y la ficha lo dice.
- El material exacto por producto sigue sin dato: la ficha dice plata (.999 o
  .925) o baño de oro según el color.
