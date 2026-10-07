# JEV ADS 02 — la señal por búsqueda-en-grupo

Estado: plan del 2026-10-04, rediseñado el mismo día tras los prototipos. El
dueño selló el diseño ese día ("va, a las cuatro cosas"). Fichas (0.2), regla
de roster (0.4) y spec (0.1) cerrados; sigue el bloque S. Nace del acta 3.2 de
[JEV ADS 01](jev-ads-01.md) ("seguir y ampliar"). Base de planificación:
commit `25cebe7`. `team_validation_mode: manual-pass`.

**Resultado:** para cada búsqueda en cada ad group, Orbit le dice al dueño una
de cinco cosas: ya vendió aquí, vende en otro grupo, corresponde y no vende en
ninguno, es ajena, o no se puede leer. Lo dice a tiempo en cada propuesta de
corte y en una pantalla de las búsquedas que gastan sin vender. Ningún efecto
sobre Ads nace de esa señal sola.

**Spec:** [diseño de JEV ADS 02](../docs/superpowers/specs/2026-10-04-jev-ads-02-design.md),
que es delta del [diseño de JEV ADS 01](../docs/superpowers/specs/2026-10-03-jev-ads-design.md).
Precedencia: `docs/CONTEXTO.md` > `docs/APPLY.md` > diseño 01 > diseño 02 >
este plan. En lo que el diseño 02 declara cambiar (la reutilización de
juicios, el roster probado y el job automático), prevalece sobre el 01. Cada
efecto sobre Ads tiene además su propia fila de spec y sello. El detalle paso
a paso del bloque S está en [la guía de construcción](jev-ads-02-bloque-s.md).

**Alcance:** las cinco ampliaciones que el dueño pidió el 2026-10-04,
reordenadas por lo que mostraron los prototipos. Reseñas, generación de
keywords nuevas y cambios a los umbrales económicos del motor quedan fuera.

## Qué cambió con los prototipos

El primer borrador partía de que Jev avisaría "sí corresponde" en un bloqueo
equivocado y encontraría búsquedas ajenas entre las que gastan sin vender.
Cinco lecturas de producción y una corrida de Jev (resultados en
`docs/evidencia/jev-ads-02/diseno/prototipos.md`) mostraron otra cosa:

- **"Sí corresponde" no discrimina.** De los 18 pares `negative` que el motor
  ha propuesto, 17 son búsquedas del catálogo.
- **Lo que distingue un bloqueo sano de uno peligroso son las ventas.** En 11
  de esas 17 la búsqueda vende en otro grupo (bloquear aquí consolida); en 8 el
  propio grupo tiene ventas en su historial (bloquear es el riesgo).
- **Lo ajeno es poco.** En la muestra, 10.7% del gasto sin venta en MX y 1.5%
  en US.
- **La proporción es lo útil de Jev.** "Corresponde a 20 de 208 productos" y
  "a 177 de 177" son búsquedas distintas. Cerca de la mitad del gasto sin
  venta de la muestra de MX es de búsquedas con atributo (oro, plata) en
  grupos que mezclan los dos.
- **El roster sí es demostrable.** Todo el gasto de búsquedas está en grupos
  donde cada anuncio activo tiene producto y ficha.

Con eso, las opciones 1, 2 y 4 se vuelven una sola pieza (la señal), la opción
3 (ruteo) gana datos reales, y la 5 (fábrica) sigue esperando a tener qué
filtrar.

## Tamaño medido

Lectura de producción del 2026-10-04 con `orbit_read`; consulta y salida en
`docs/evidencia/jev-ads-02/planificacion/`. Ventana de cortes del motor.

| Dato | Amazon MX | Amazon US |
| --- | ---: | ---: |
| Pares grupo-búsqueda de texto con gasto en la ventana | 622 | 774 |
| De esos, sin venta | 597 | 748 |
| Sin venta y con 3 clics o más | 161 | 143 |
| Parte del gasto de búsquedas que fue a pares sin venta | 44.9% | 51.0% |
| Anuncios ENABLED / sin producto ligado | 4,773 / 1,168 | 4,090 / 920 |
| Productos con anuncio ENABLED / con ficha completa (tras 0.2) | 249 / 249 | 119 / 119 |
| Grupos con gasto de búsquedas / con roster y fichas completos | 22 / 22 | 11 / 11 |
| Llamadas para evaluar las búsquedas sin venta con 3 clics o más | 15,282 | 3,976 |

- **La cola de cortes es chica.** En live entraron 4 `negative` (2 aplicados,
  2 descartados) y 7 harvest (6 aplicados, 1 vetado). El histórico de
  decisiones tiene 52 `negative` sobre 18 pares distintos y 11 harvest sobre
  10 pares.
- **Costo.** El prototipo midió unas 96 llamadas por búsqueda (el grupo
  completo) y 259 ms de mediana. La evidencia del diseño anota un precio de
  documentación de 0.042 USD por millón de tokens de entrada; con ese precio
  el prototipo de 4,811 llamadas costó cerca de 0.25 USD. Lo que escasea es el
  tope diario, no el dinero. Falta confirmar la tarifa (0.3).

## Contratos

**Se heredan de JEV ADS 01 sin cambio:**

- El juicio recibe solo el término literal y una versión de ficha. Un fallo
  del proveedor es fallo, nunca evidencia.
- El motor, la cola y `app/optimizer` no importan módulos de Jev. La guarda se
  invierte y pasa a `tests/test_architecture.py`: recorre todo `app/` y
  `tools/` y solo deja importar Jev a una lista corta de archivos.
- Un fallo o apagado de Jev deja igual la decisión y su aplicación, incluido
  el veto de 48 horas. "Default al vencer = APLICAR" no se toca.
- `app_jev` no gana permisos: no lee ni escribe decisiones, cola, ledger,
  goals, bibliotecas ni observaciones.
- Tablas de Jev append-only. GET no escribe ni llama a TypeSafe.
- La madurez no cambia (regla 6): toda afirmación de "no vende" usa la ventana
  de cortes.

**Cambian con este plan** (sellados por el dueño el 2026-10-04):

- **Jev corre sola**, en un job fuera del ciclo, con tope diario de llamadas.
- **Un juicio pagado vale para cualquier señal que lo cite** (cambia la
  decisión R4 del diseño 01). El CLI manual conserva su regla.
- **Un ad group puede dar "ninguno corresponde" cuando su roster está
  probado.** La ingesta de estructura pasa a registrar un acta de lo que listó.
- **Un login nuevo** (`orbit_jev`) para que el job no corra con `orbit_admin`.
- **Se revoca a `app_decide` y `app_ingest` la lectura de las tablas `jev_*`**,
  que hoy tienen por el permiso por omisión de `0001`.

**Los efectos siguen condicionados** (bloque E). Cambian reglas selladas y
cada uno necesita su spec y su sello:

- Abstención del motor: cambia que el juicio de Jev no altera decisiones.
  `docs/CONTEXTO.md` la prevé como señal que solo puede abstenerse (Fase 4),
  si demuestra en sombra que mejora la tasa de acción útil.
- Bloqueo o ruteo aprobado por el dueño: el diseño v2 rechazó una cola de
  aprobación humana y `cortes-ui-01` rechazó un botón «aprobar». Aquí la
  aprobación no frena nada que el motor decidió; agrega una acción que el
  motor no propone. No puede ir por la cola de hoy, porque la liberación
  re-evalúa la regla económica completa y lo descartaría: el camino
  recomendado es una herramienta del dueño con ledger, readback y reversa,
  como `tools/archiva_inertes.py`.

## Interruptores y reversa

Claves en `config_version.settings`; ausente o corrupta significa apagado.

| Efecto | Interruptor | Apagar deja | Reversa de lo ya hecho |
| --- | --- | --- | --- |
| Señales | `jev.senales` con `jev.tope_diario` y `jev.min_clics` | Cero filas y cero llamadas; las pantallas dicen "desactualizada" | No hay efecto que revertir |
| Avisos | `jev.avisos` | Sin Telegram; las señales siguen | No hay efecto que revertir |
| Abstención (E.1) | el que fije su spec | El motor decide como hoy | Apagar; nada se escribió en Amazon |
| Bloqueo aprobado (E.2) | el que fije su spec | Ninguna aprobación produce efecto | Revocar la aprobación; aplicado: delete del negativo (`docs/APPLY.md` §7) |
| Ruteo aprobado (E.3) | el que fije su spec | Ningún ruteo produce efecto | Revocar; aplicado: keyword primero, negativo después |
| Exclusión en fábrica (5.3) | `fabrica.exclusion_jev` | El plan sale con todas sus semillas | Antes de confirmar no hay efecto; después, la pausa de lote |

## Tareas y dependencias

Evidencia de cada fila en `docs/evidencia/jev-ads-02/ejecucion/<id>/`.
`cc:TODO` significa que no ha empezado. CONDICIONAL: si su medición no cumple
el criterio, la fila se cierra como "no se construye", con el motivo.

### Fase 0 — bases

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 0.1 | `[stage:planificacion] [lane:gate]` Spec delta y sello del dueño. | El dueño sella los contratos que cambian y los valores de `jev.tope_diario`, `jev.min_clics` y el horario. | - | cc:DONE (sellado 2026-10-04: dos corridas al día, reutilización de juicios, "ninguno" con roster probado, tope 5,000 y 3 clics) |
| 0.2 | `[stage:medicion] [lane:release]` Completar fichas en MX y US. | Todo producto con anuncio ENABLED tiene una ficha vigente que cubre todos sus listings. | - | cc:DONE (249 de 249 y 119 de 119: `ejecucion/0.2/NOTAS.md`) |
| 0.3 | `[stage:investigacion] [lane:fast]` Confirmar y versionar la tarifa de TypeSafe. | Tarifa con su fuente y fecha; costo del piloto y del prototipo calculado con su `usage`. | - | cc:TODO |
| 0.4 | `[stage:investigacion] [lane:gate]` Roster probado: regla y viabilidad. | Regla escrita; grupos que la cumplirían; conteo de anuncios sin producto. | - | cc:DONE (regla en el spec; viabilidad medida: Amazon declara los totales en las dos plataformas y en los 22 y 11 grupos con gasto todo anuncio activo liga a producto; las condiciones de descartados y huella las mide S.1; los anuncios sin producto no tienen `listing_id` y están en grupos sin gasto: `diseno/prototipos.md`) |
| 0.5 | `[stage:medicion] [lane:release]` Sensibilidad al orden de las opciones Choice. | Reporte con denominadores: cuántos pares cambian de respuesta al invertir el orden. El dueño decide con ese dato si se corrige el contrato antes de S.7. | 0.1 | cc:TODO |

### Bloque S — la señal (opciones 1, 2 y 4)

Los pasos son los del "Orden de entrega" del spec. Cada uno se despliega sin
cambiar nada visible hasta su interruptor.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| S.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Acta de listado en la ingesta de estructura. | Una corrida ok deja acta por plataforma y por ad group en su misma transacción; una que falla no deja ninguna. Tras la primera corrida real: cuántos grupos cumplen la regla. | 0.1 | cc:DONE J1 #403 436b877, deploy 20261005-1657 |
| S.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Núcleo puro `jev_lectura.py` y los dos traslados sin cambio de comportamiento (`Libro.pagar`, `resolver_fichas`). En el mismo bloque, el arreglo de la reanudación del CLI manual: `_mismo_origen` compara la identidad del censo sin `synced_at`, con su prueba roja. | La parte pura de las pruebas 1, 2, 3, 10 y 11 del spec, como tablas de `leer`, `probar_roster` y `anunciados_hoy`; las pruebas del asesor de JEV ADS 01 pasan sin tocarse. | 0.1 | cc:DONE J2 #404 eda1f90, deploy 20261006-1000 (sello de J3; S.2 salió con ese despliegue) |
| S.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Migración de señales, login `orbit_jev` y `ORBIT_DSN_JEV` (`docker-compose.yml`, el script de logins de `docs/DEPLOY.md` y `tests/test_compose_deploy.py`, que hoy fija cuatro DSN). | Prueba 7 (perímetro de roles, incluidas las tablas de 0049); la mitad de base de la prueba 1 (los CHECK rechazan `ajena` sin roster o con NULL) y el CHECK de moneda; reversa de la migración ensayada. | S.2 | cc:DONE J3 #405 85e25a8, deploy 20261006-1000 |
| S.4 | `[stage:implementacion] [lane:gate] [tdd:required]` El job `jev-senales`, su cron y el bloque de `/salud`. Guarda de imports invertida. | Pruebas 4, 5 y 6 del spec, la mitad de madurez de la 3 y la 10 de punta a punta (el job arma el roster con `anunciados_hoy`); seco por omisión; con el interruptor ausente no escribe ni llama. | S.1, S.3 | cc:DONE J4 #406 96daae6, deploy 20261007-0116 |
| S.5 | `[stage:implementacion] [lane:gate] [tdd:required]` Señal en `/cortes` y pantalla `/gasto-sin-venta`. | Prueba 8; la proporción y los productos que dijeron "sí" a la vista; nunca "ninguno" sin roster probado. | S.4 | cc:DONE J5b #408 15e4327, deploy 20261007-0640 (contrato puro en J5a #407 80da876) |
| S.6 | `[stage:cierre-pr] [lane:release]` Encender con `jev.tope_diario = 0`. | Go del dueño; señales solo con ventas durante una semana; el ciclo y la cola sin diferencias. | S.5 | cc:TODO |
| S.7 | `[stage:medicion] [lane:release]` Subir el tope y medir con el dueño. | Reporte por mercado, por búsqueda distinta y con etiquetas a ciegas: acuerdo con cada lectura, falsos "ajena", falsos "sí" por ficha, y cuántas de las señaladas vendieron después. | 0.3, 0.5, S.6, J6-31 | cc:TODO |
| S.8 | `[stage:implementacion] [lane:gate] [tdd:required]` Aviso por Telegram de cada propuesta en veto. | Prueba 9; `jev.avisos` aparte del interruptor del job. | S.7 | cc:TODO |

### Notas abiertas del cierre S.1–S.5 (J6)

Una fila por cada nota no bloqueante que quedó abierta en los veredictos
del loop (fuente entre paréntesis). Ya resueltas y sin fila: la línea de
evidencia de J2-r1 (corregida en r2), la partición de `jev_senales.py`
(hecha en J4-r3), el cableado `dict_row` y el `ORDER BY s.id` de J5-r1
(hechos en J5b), y `ingest-manual-20261005T2346Z.txt` (excluido de la
evidencia en este cierre por traer identificadores de Amazon).

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| J6-1 | `[stage:cierre-pr] [lane:fast]` Nombrar en un PR (o reponer en fases intermedias) que S.1 quitó del preflight la guarda de harvest en vuelo del patrón 2.3. (VEREDICTO-J1-r1 N1) | Nombrado o repuesto. | - | cc:TODO |
| J6-2 | `[stage:cierre-pr] [lane:fast]` Corregir la guía: el "Comprueba" necesita `PYTHONPATH=.`. (VEREDICTO-J1-r1 N2) | Guía corregida. | - | cc:TODO |
| J6-3 | `[stage:cierre-pr] [lane:fast]` `docs/DATABASE.md`: documentar `ads_listado_plataforma` y `ads_listado_grupo`. (VEREDICTO-J1-r1 N3) | Documentadas. | - | cc:TODO |
| J6-4 | `[stage:cierre-pr] [lane:fast]` Docstring de `_db_21`: mencionar la 0051. (VEREDICTO-J1-r1 N4) | Mencionada. | - | cc:TODO |
| J6-5 | `[stage:implementacion] [lane:gate]` `checklist.sh` S.1: imprimir las filas del acta por plataforma. (VEREDICTO-J1-r1 N5) | Se ven por plataforma. | - | cc:TODO |
| J6-6 | `[stage:implementacion] [lane:gate]` `armar_acta`: un adId repetido contaría dos veces en `vivos` (sin efecto hoy). (VEREDICTO-J1-r1 N6) | Deduplicado o descartado. | - | cc:TODO |
| J6-7 | `[stage:cierre-pr] [lane:fast]` Registro: LISTO-J1-deploy no citó la ruta del respaldo de código (`predeploy-20261005-1657/`). (VEREDICTO-J1-deploy) | Anotado. | - | cc:TODO |
| J6-8 | `[stage:implementacion] [lane:gate]` `planear` salta una propuesta sin entrada en `economia`; el job debe leer siempre la economía de las claves de propuesta, fijado en e2e. (VEREDICTO-J2-r1 N2) | Prueba e2e que lo fija. | - | cc:TODO |
| J6-9 | `[stage:implementacion] [lane:gate]` `leer` confía en que el historial cubre la ventana; prueba que fije que el historial siempre cubre la ventana madura. (VEREDICTO-J2-r1 N3) | Prueba que lo fija. | - | cc:TODO |
| J6-10 | `[stage:implementacion] [lane:gate]` La mutación de B1 (S.2) tampoco la detectan los tests de JEV ADS 01 (deuda de 01; el roster quedó cubierto). (VEREDICTO-J2-r1 N4) | Detectada o aceptada. | - | cc:TODO |
| J6-11 | `[stage:implementacion] [lane:gate]` `test_jev_perimetro.py:80` arma el DSN temporal con `rsplit` (perdería `sslmode`); usar `make_conninfo`. (VEREDICTO-J3-r1) | Usa `make_conninfo`. | - | cc:TODO |
| J6-12 | `[stage:cierre-pr] [lane:fast]` Nombrar que S.3 quitó del preflight la guarda de ciclo en `running` (queda cubierto por `app.cli`). (VEREDICTO-J3-r1) | Nombrado. | - | cc:TODO |
| J6-13 | `[stage:cierre-pr] [lane:fast]` `checklist.sh` S.3: documentar la precedencia 4-antes-que-3 y que el sello es opcional. (VEREDICTO-J3-r2) | Documentado. | - | cc:TODO |
| J6-14 | `[stage:implementacion] [lane:gate]` `test_compose_deploy.py:97`: las aserciones literales siguen exigiendo la clave sin comillas; tolerancia a medias. (VEREDICTO-J3-r2) | Unificado. | - | cc:TODO |
| J6-15 | `[stage:cierre-pr] [lane:fast]` Registro: LISTO-J3-deploy cita `checklist-20261006T1002Z.txt`; el archivo es `...T1001Z.txt`. (VEREDICTO-J3-deploy N1) | Anotado. | - | cc:TODO |
| J6-16 | `[stage:cierre-pr] [lane:fast]` Proceso: toda corrida manual queda con quién la pidió y por dónde (caso 2026-10-05 23:46). (VEREDICTO-J3-deploy N3) | Regla escrita. | - | cc:TODO |
| J6-17 | `[stage:implementacion] [lane:gate]` `correr` lee todo el mundo antes de mirar el interruptor (~2,500 consultas para "apagado"); leer ajustes primero. (VEREDICTO-J4-r1) | Ajustes primero. | - | cc:TODO |
| J6-18 | `[stage:cierre-pr] [lane:fast]` `windows.py`: conservar la razón del `NULLS LAST` en una línea. (VEREDICTO-J4-r1) | Razón en una línea. | - | cc:TODO |
| J6-19 | `[stage:cierre-pr] [lane:fast]` `docs/DEPLOY.md` dice que el dueño instala el cron; en este loop lo instala el encargo: dejar uno. (VEREDICTO-J4-r1) | Un solo camino. | - | cc:TODO |
| J6-20 | `[stage:cierre-pr] [lane:fast]` Los tres `panel-glm-*.txt` suman 5,400 líneas crudas en el repo; basta `panel.md`. (VEREDICTO-J4-r1) | Podados o aceptados. | - | cc:TODO |
| J6-21 | `[stage:planificacion] [lane:gate]` F1 (Low): precedencia no-pago vs `FichaFaltante`; requiere decisión del dueño. (VEREDICTO-J4-r1) | Decisión tomada. | - | cc:TODO |
| J6-22 | `[stage:cierre-pr] [lane:fast]` Dos caminos documentados para instalar el cron (`crontab -e` y el bloque de `desplegar.sh`); dejar uno. (VEREDICTO-J4-r1 kimi) | Un solo camino. | - | cc:TODO |
| J6-23 | `[stage:implementacion] [lane:gate]` El `trap ERR` de `desplegar.sh` (S.4) usa `$DIR` y `$STAMP` antes de definirlos. (VEREDICTO-J4-r1 kimi) | Ordenado. | - | cc:TODO |
| J6-24 | `[stage:implementacion] [lane:gate]` `checklist.sh:113` (S.4): seco que corre sin decir "apagado" sale 4, debería ser 1. (VEREDICTO-J4-r1 kimi) | Sale 1. | - | cc:TODO |
| J6-25 | `[stage:implementacion] [lane:gate]` `git archive \| tar` no borra en el servidor archivos eliminados entre SHAs. (VEREDICTO-J4-r1 kimi) | Sincroniza borrados. | - | cc:TODO |
| J6-26 | `[stage:implementacion] [lane:gate]` `sleep 5` fijo antes del `curl /health`. (VEREDICTO-J4-r1 kimi) | Espera con reintento. | - | cc:TODO |
| J6-27 | `[stage:implementacion] [lane:gate]` `fallo_tras_http` decide por prefijo de texto; un tipo o campo sería más firme. (VEREDICTO-J4-r2) | Tipo o campo. | - | cc:TODO |
| J6-28 | `[stage:implementacion] [lane:gate]` `desplegar.sh` S.4 lista archivos por nombre para md5 sin `app/jev_salud.py` (ya desplegado así). (VEREDICTO-J4-r3) | Lista completa. | - | cc:TODO |
| J6-29 | `[stage:implementacion] [lane:gate]` `de_propuestas` relee toda la cola en veto en cada GET de `/cortes` (hoy chica). (VEREDICTO-J5-r1) | Medido o acotado. | - | cc:TODO |
| J6-30 | `[stage:cierre-pr] [lane:fast]` `jev_lectura.py` en 796 líneas; quedan 104 de presupuesto. (VEREDICTO-J5-r1) | Bajo control. | - | cc:TODO |
| J6-31 | `[stage:implementacion] [lane:gate]` N1: la frase de `ajena` muestra `datos_hasta` en vez del `listado_de` del diseño; llevar `listado_de` hasta `SenalVista`, usarlo en la frase, y prueba que distinga las dos fechas. ARREGLAR ANTES de S.7 (encargo J5c; S.7 la lista en Depends). (VEREDICTO-J5b-r1 N1) | Frase con `listado_de` + prueba. | - | cc:TODO |
| J6-32 | `[stage:implementacion] [lane:gate]` `/gasto-sin-venta` no degrada sin tablas Jev (daría 500); envolver como `_jev_de`. (VEREDICTO-J5b-r1) | Degrada. | - | cc:TODO |
| J6-33 | `[stage:implementacion] [lane:gate]` `_senal.html` pinta "k de evaluados"; mostrar también los miembros. (VEREDICTO-J5b-r1) | Muestra miembros. | - | cc:TODO |
| J6-34 | `[stage:implementacion] [lane:gate]` `_ORDEN_LECTURAS` duplica el vocabulario del CHECK de `jev_senal.lectura`. (VEREDICTO-J5b-r1) | Una sola fuente. | - | cc:TODO |
| J6-35 | `[stage:implementacion] [lane:gate]` La pantalla `/gasto-sin-venta` no pagina (hoy pocas filas). (VEREDICTO-J5b-r1) | Pagina o aceptado. | - | cc:TODO |
| J6-36 | `[stage:cierre-pr] [lane:fast]` El docstring de módulo de `app/jev_vista.py` quedó viejo. (VEREDICTO-J5b-r1) | Actualizado. | - | cc:TODO |
| J6-37 | `[stage:implementacion] [lane:gate]` `rollback.sh` S.5: línea informativa expande `$(cat ...)` en la Mac y no en el servidor. (VEREDICTO-J5b-r1 kimi) | Expande en servidor. | - | cc:TODO |
| J6-38 | `[stage:implementacion] [lane:gate]` `checklist.sh` S.5: dos comprobaciones (sin nombrar en el veredicto) saldrían 4 debiendo 1. (VEREDICTO-J5b-r1 kimi) | Salen 1. | - | cc:TODO |
| J6-39 | `[stage:implementacion] [lane:gate]` El checklist S.5 no avisa de un ciclo en `degraded`. (VEREDICTO-J5b-r1 kimi) | Avisa. | - | cc:TODO |
| J6-40 | `[stage:implementacion] [lane:gate]` El `trap ERR` S.5 usa variables antes de definirlas. (VEREDICTO-J5b-r1 kimi) | Ordenado. | - | cc:TODO |
| J6-41 | `[stage:implementacion] [lane:gate]` El sello tiene resolución de minuto. (VEREDICTO-J5b-r1 kimi) | Resolución mayor. | - | cc:TODO |
| J6-42 | `[stage:implementacion] [lane:gate]` El checklist S.5 corre sin `-e`. (VEREDICTO-J5b-r1 kimi) | Con `-e` o justificado. | - | cc:TODO |
| J6-43 | `[stage:implementacion] [lane:gate]` La guarda "ningún app.cli corriendo" no protege de un proceso por empezar (deploy 5 min antes de la ingesta; salió bien). (VEREDICTO-J5b-deploy) | Guarda reforzada. | - | cc:TODO |
| J6-44 | `[stage:medicion] [lane:release]` Ciclos 106 y 107 fuera de horario (2026-10-06 16:27 UTC, `done`): si los disparó este loop, anotar quién; el loop nunca corre `cycle`, sin evidencia de que sean de aquí. (VEREDICTO-J5b-deploy) | Origen aclarado. | - | cc:TODO |
| J6-45 | `[stage:medicion] [lane:release]` Prueba del ciclo pendiente tras S.5 (no se esperó, por orden del dueño). (VEREDICTO-J5b-r1) | Ciclo posterior visto. | - | cc:TODO |

### Bloque E — efectos, todos condicionales

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| E.1 | `[stage:planificacion] [lane:gate]` CONDICIONAL. Abstención: el motor no propone un corte según la señal vigente. Primero en sombra. | Spec propio y sello; define la "tasa de acción útil" que la sombra debe mejorar; qué lecturas justifican abstenerse; la decisión guarda `senal_id` en `inputs` y el replay la reproduce. | S.7 | cc:TODO |
| E.2 | `[stage:planificacion] [lane:gate]` CONDICIONAL. Bloqueo aprobado por el dueño de una búsqueda `ajena`. | Spec propio y sello; herramienta con ledger antes del HTTP, readback y reversa probada; una venta posterior o una señal nueva dejan la aprobación sin efecto. | S.7 | cc:TODO |
| E.3 | `[stage:planificacion] [lane:gate]` CONDICIONAL. Ruteo (opción 3): mandar una búsqueda al grupo donde vende o a los productos a los que corresponde. | Spec propio y sello: de dónde sale el destino (`otros_que_venden`, `productos_ok`), la acción, su costo y su reversa. | S.7 | cc:TODO |

### Fase 5 — opción 5: filtrar semillas en la fábrica

Hoy no tiene qué filtrar: las bibliotecas están vacías y el export solo manda
a Jev términos de biblioteca.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 5.1 | `[stage:implementacion] [lane:gate] [tdd:required]` El export de fábrica coteja también las semillas de términos vendedores, con su rol. | Un plan sin biblioteca produce términos a cotejar; el plan y su huella no cambian. | 0.1 | cc:TODO |
| 5.2 | `[stage:medicion] [lane:release]` Piloto con un plan de fábrica real, por CLI. | Reporte: semillas, proporción por semilla, acuerdo del dueño. Decide si 5.3 se construye. | 5.1 | cc:TODO |
| 5.3 | `[stage:implementacion] [lane:gate] [tdd:required]` CONDICIONAL. La pantalla de fábrica muestra la asesoría y el dueño puede excluir una semilla marcada. | Sin exclusiones la huella es idéntica; excluir cambia huella y el dueño confirma el plan nuevo; nada toca Amazon antes de confirmar. | S.4, 5.2 | cc:TODO |

### Cierre

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| R | `[stage:revision] [lane:gate]` Cada bloque de código pasa `cross-review.ps1` con un revisor distinto del autor. | Regla 4 de quality-kit. | cada bloque | cc:TODO |
| D | `[stage:cierre-pr] [lane:release]` Despliegue por paso, apagado, con scripts nuevos que siguen el patrón de `jev-ads-01/ejecucion/2.3` y rollback ensayado. | Checklist verde tras el primer ciclo; cada interruptor se enciende en un cambio de config aparte. | cada paso | cc:TODO |

## Criterios medidos

Valores propuestos por el autor del plan, sin derivación. Se cuentan por búsqueda distinta y por mercado. Las etiquetas se ponen
a ciegas: el dueño contesta sin ver la señal. Las búsquedas de una medición no
pueden ser las que se usaron para corregir fichas.

| Paso | Criterio para avanzar |
| --- | --- |
| S.6 (encender con tope 0) | S.5 desplegado; el dueño vio la pantalla vacía y el bloque de salud. |
| S.8 (avisos) | En S.7, el dueño coincide con la lectura en 90% o más de al menos 50 búsquedas; ningún fallo contado como señal. |
| E.1 (abstención) | Al menos 50 búsquedas medidas; definida la tasa de acción útil; la sombra la mejora. |
| E.2 (bloqueo aprobado) | De las `ajena` de S.7, 95% o más confirmadas; ninguna vendió en los 30 días siguientes de una prueba hacia atrás. |
| E.3 (ruteo) | Al menos 30 casos con destino confirmado en 90% o más. |
| 5.3 (exclusión) | 5.2 encuentra semillas ajenas confirmadas y ninguna semilla buena marcada. |

## Huecos conocidos

| Hueco | Estado |
| --- | --- |
| Un producto con todos sus anuncios archivados impedía `ajena` en 8 de los 22 grupos con gasto de MX. | Resuelto en el spec: el roster de la señal cuenta solo lo anunciado hoy (prueba 10). |
| ¿Amazon declara los totales del listado? | Sí, comprobado el 2026-10-04 en las dos plataformas (`diseno/prototipos.md`). S.1 registra que lo siga haciendo en cada corrida. |
| Los negativos puestos a mano en Amazon no se ingieren. | Aceptado en el spec: la lista puede mostrar una búsqueda que ya no gasta. |
| Un `negative` aplicado no bloquea su clave; el motor puede volver a proponerlo. | El job excluye los cortes aplicados por Orbit; el resto no cambia. |
| Una abstención de Jev impide `ajena`. | Se mide en `/salud` desde S.4. |
| Una palabra de la ficha que también es una búsqueda da un falso "sí". | La pantalla muestra qué productos dijeron "sí"; se corrige con una versión nueva de la ficha. |
| No hay aviso de fichas por vencer (las primeras, el 2027-01-02). | Bloque de `/salud` de S.4. |
| El CLI manual no retoma una revisión después de la ingesta diaria (`synced_at`). | Lo cierra S.2. |
| El CLI manual y el job reutilizan juicios con reglas distintas. | Aceptado; se unifica o se retira el CLI cuando el job lleve un mes. |
| El CLI manual no toma el candado del job ni respeta el tope diario. | Aceptado en el spec: lo que paga cuenta para el día; el total puede pasar del tope por el presupuesto de esa corrida. |
| Una decisión de harvest sin destino legible. | El job la avisa con ese motivo, no revienta. |
| Las semillas del plan van normalizadas y los términos vendedores crudos. | Lo cierra 5.1. |

## Pruebas que deben discriminar

Las once del spec. Las que no pueden faltar en ningún bloque:

1. Interruptor ausente: el ciclo y la cola producen lo mismo que hoy.
2. `ajena` nunca sin roster probado, ni en pantalla ni en la base.
3. Un juicio no tapa una venta.
4. El login que escribe señales no puede leer decisiones, cola ni
   observaciones; el del ciclo no puede leer tablas de Jev.

Cada bug encontrado en el camino trae la prueba que lo habría atrapado.

## Orden y salida

Orden: 0 → S.1 a S.8 → E y 5 según sus mediciones. S.1 va primero porque es la
única pieza que necesita corridas reales y decide si `ajena` es alcanzable.
S.2 puede avanzar en paralelo.

El plan cierra cuando la señal está encendida con su medición y cada efecto
quedó encendido con la suya, o cerrado con el motivo escrito. Ninguna fila
autoriza por sí sola encender un interruptor en producción: eso es un cambio
de config aparte, con el go del dueño.
