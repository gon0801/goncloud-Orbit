# Arena de diseño y juicio cruzado (2026-10-04)

Tres candidatos independientes, cada uno en un modelo distinto, recibieron la
misma tarea y el mismo fundamento (lectura del código y `prototipos.md` hasta
la sección 4; el prototipo con Jev corrió después). Un juez en otro modelo
los puntuó con la rúbrica de abajo. Los paquetes completos de cada candidato
viven en una carpeta privada del lead.

## Los candidatos

- **A, "tres hechos, una lectura".** Roster, juicio y economía con una fuente
  de verdad cada uno; la lectura es una función pura congelada en una fila.
  Las ventas mandan y Jev desempata. Juicios globales por par citados por id.
  Roster probado por huella de anuncios.
- **B, "la lectura por búsqueda-en-grupo".** Una revisión por búsqueda con
  solicitud determinista y reutilización entre revisiones relajando el
  trigger. La regla consulta primero a Jev. Roster por `synced_at` más skips.
- **C, "el caso, la postura, la fila".** Ventas primero; a Jev solo se le
  pregunta cuando las ventas callan y se para al primer "sí". Roster por
  conteos.

## Rúbrica y puntajes del juez (1 a 5)

| Criterio | A | B | C |
| --- | ---: | ---: | ---: |
| Señal que discrimina | 5 | 2 | 3 |
| Contratos honrados | 5 | 3 | 4 |
| Profundidad de la interfaz | 4 | 3 | 3 |
| Modelo de datos e idempotencia | 5 | 3 | 3 |
| Regla de roster probado | 5 | 3 | 4 |
| Implementable en este repo | 4 | 3 | 3 |

## Decisión

**Base: A.** El lead y el juez coincidieron en la base. Motivos: su regla,
su unidad de persistencia y su prueba de roster sobreviven al prototipo con
Jev sin cambiar de forma; lleva la proporción como columnas; no depende de
arreglar la reanudación del asesor ni de relajar el trigger de 0049; y cierra
con REVOKE explícito el permiso por omisión de `0001`.

Un desacuerdo, resuelto a favor del juez: el lead se inclinaba por la
persistencia de B y C (una revisión por búsqueda). Se quedó la de A porque su
idempotencia no depende del asesor y no toca el trigger.

**Injertos.** De C: versión de la regla y prueba que recorre toda fila
guardada; aviso separado de su entrega; seco por omisión; "no evaluada" como
valor propio. De B: totales de ad groups en el acta; prueba de
resincronización entre corridas.

**Del prototipo, que los tres pasaron por alto.** Bandas de proporción y
orden por proporción en la pantalla; mostrar qué productos dijeron "sí";
medir en salud cuántas veces una abstención impide "ajena"; el tope diario, no
el dinero, es el cuello (unas 96 llamadas por búsqueda).

**Rechazado.** El orden de B (un "ninguno" de Jev taparía órdenes del
historial, y sin cupo no habría lectura). Parar al primer "sí" y no consultar
a Jev cuando hay ventas (C): tira la proporción. Roster por `synced_at` o por
conteo.

## Lector fresco sobre el diseño sintetizado

Un lector en otro modelo revisó el spec, el bosquejo y el plan contra el
código antes de darlos por cerrados. Encontró un bloqueante y once no
bloqueantes; todos se corrigieron en una ronda:

- **Bloqueante:** `jev_senal` guardaba importes sin atarlos a una moneda
  (regla 4 de CONTEXTO). Ahora la moneda es obligatoria y un CHECK la liga a
  la plataforma.
- **El más grave de los no bloqueantes:** cuando un grupo tiene un producto
  con todos sus anuncios archivados, `componer` lo saca del universo y marca
  el motivo `no_anunciado`, y con ese motivo nunca da "ninguno". Comprobado en producción: afectaba a 8 de los 22 grupos con
  gasto de MX. El roster de la señal ahora cuenta solo lo anunciado hoy.
- El resto: DDL explícito del sujeto `lote`; definición de `insumos_sha256` y
  `valida_hasta`; dónde vive la lectura del destino de un harvest; precedencia
  frente al diseño 01; `listar_todo` como envoltura; el historial no puede
  reusar el agregado del motor (un día sin dato borraría una venta) ni cabe
  en `windows.py` (874 de 900 líneas); entrega falsa con el canal de Telegram
  inactivo; texto del aviso con fecha en vez de horas restantes; y varios
  desajustes entre plan y spec.

Segunda ronda, con otro revisor y solo sobre el delta de los arreglos: cero
bloqueantes. Sus seis notas (una función pura `anunciados_hoy` para que la
prueba del universo tenga dueño, el filtro como complemento exacto del de
`componer`, el cotejo del total también al final del listado, la firma de
`leer_destino`, un parámetro sobrante y una cuenta de días) se aplicaron en la
misma ronda.

## Errores que el juez encontró en los candidatos

- B afirmaba que `app_decide` no tendría acceso a las tablas nuevas sin
  revocarlo; el permiso por omisión de `0001` se lo da. Comprobado después en
  producción para las tablas de 0049.
- C tenía dos contradicciones internas: el tope ausente valía 0 en el
  documento y apagaba el job en el bosquejo; y su regla de reescritura dejaba
  la vista vacía un día de cada dos.
- Los tres usaban como ejemplo de "ajena" la búsqueda que el prototipo mostró
  que Jev juzga mal.
