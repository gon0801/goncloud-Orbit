# R14-r2: segunda relectura del remate

La primera ronda dejo el PR #443 pusheado con head 3430b2d. El
verificador de poteto-mode marco que faltaban tres piezas de la
revision cruzada. Esta ronda las corrio sobre el mismo diff
(30cc62f..3430b2d) y aplico lo que encontraron como commits locales,
sin push.

## Quien reviso

- Tres subagentes `poteto-agent` de solo lectura, uno por tajada:
  migraciones y despliegue (`poteto-migraciones.txt`), impulso, vigia
  y fabrica (`poteto-impulso.txt`), y pruebas y JS
  (`poteto-pruebas.txt`).
- Un panel en paralelo con tres hijos Muse (esquema, correccion y
  pruebas) y una sintesis, en `panel/`.
- Revisores de otra familia con `opencode run` y modelos
  `zai-coding-plan`, guardados en `glm-5.3.txt` y `glm-5-turbo.txt`.
  El encargo no fijo modelo de los hijos Muse: corrieron con el
  modelo de la sesion.
- Un `comment-sicko` sobre el diff de esta ronda (3430b2d..HEAD) para
  el paso de comentarios: 15 bloques marcados para morir y 4 que se
  quedan.

## Lo que se corrigio

- `lanza` sellaba un lote huerfano applied sin pasar por el chequeo de
  vivo del producto: dos impulsos del mismo producto podian quedar
  vivos. La guardia de vivo ahora va antes de reutilizar huerfanos y
  vive en su propia funcion (`_falla_si_hay_vivo`). Prueba primero:
  `test_r14r2_huerfano_no_sella_un_segundo_vivo_del_producto` caia con
  `DID NOT RAISE` y el mutante que apaga la guardia la vuelve a tumbar.
- La 0071 no se podia detectar aplicada: `has_table_privilege` da
  verdadero en produccion por los default privileges, asi que el
  despliegue la daba por aplicada sin correrla y el rollback nunca la
  reversaba. La migracion deja una marca (el comentario de la vista) y
  la deteccion, el checklist y la reversa usan esa marca. La reversa
  quita la marca y el privilegio; si la vista deja de ser legible
  porque el `REVOKE` tambien quito el privilegio previo, el rollback
  vuelve a otorgar el `SELECT` suelto y lo deja dicho en su salida.
- La 0072 no miraba el dato previo: un impulso ya colgado de un grupo
  que no es tipo impulso quedaba bajo el candado en silencio. La
  verificacion de la migracion ahora aborta si ese dato existe, y
  confirma los dos trigger sobre sus tablas con la guardia activa. La
  lectura del tipo en el trigger de insercion toma la fila del grupo
  con `FOR UPDATE` para cerrar la carrera con un `UPDATE` de tipo
  simultaneo.
- Los scripts de D.6 verificaban uno solo de los dos trigger de la
  0072: ahora verifican los dos en desplegar, checklist y rollback.
- El paso 7v del despliegue corria el vigia y exigia cero filas en
  `impulso_lectura`: con impulsos vivos del dueno abortaba despues de
  recrear el contenedor. Ahora el vigia solo corre en el despliegue si
  no hay vivos, y compara el conteo de lecturas antes y despues.
- Desarmar recorria una fila por paso historico: un lote con pasos
  repetidos del mismo rol recibia un PUT por paso y el ledger crecia
  en cada corrida. La consulta ahora trae una fila por rol (la mas
  nueva) y conserva el orden del ledger. Si el PUT de pausa levanta
  `Abortar` (timeout o red), el paso nuevo queda sellado `failed` en
  vez de `planeado`.
- Pruebas que no discriminaban: la rama 502 de la pausa (applied con
  campanas y rechazo de Amazon) ya se afirma en la prueba de R09-A;
  el CLI del vigia tiene el caso mixto (uno atendido y otro que
  falla, sale 0); el desarme tiene el PUT rechazado con su sello
  `failed` y la no duplicacion por rol; la rama de detalle en arreglo
  de `mensajeError` se ejercita en el guion Node; la 0072 prueba el
  grupo sin impulsos que si cambia de tipo y el aborto con dato previo
  roto; la 0071 afirma la matriz completa de privilegios y su marca.
- Comentarios: murieron los que narraban el codigo de esta ronda y la
  guardia de vivo quedo con nombre propio para no depender de la
  prosa. Se quedan los que avisan un comportamiento de PostgreSQL que
  el codigo no expresa (la ACL no distingue el origen del GRANT y la
  semantica de `FOR UPDATE`).

## Lo que dijeron los GLM

El revisor con `glm-5.3` marco dos bloqueantes: el sello del huerfano
sin chequeo de vivo y la reversa de la 0071 que revoca de mas. El
primero es el arreglo de `lanza` de esta ronda. Del segundo, la
compensacion vive en el rollback y no en la reversa suelta, porque en
la base del ensayo la reversa debe dejar el privilegio en falso para
que el ensayo distinga; el ciclo de simulacion repetido muestra el
rollback reotorgando el `SELECT` y terminando con la vista legible.
El mismo revisor y el de `glm-5-turbo` dejaron no bloqueantes que ya
quedaron cubiertos en esta ronda (el paso `planeado` del PUT que
falla por red, los duplicados del desarme y el dato previo de la
0072), y dos que se refutan con el repo: los roles `app_ingest` y
`app_decide` leen la vista por los default privileges desde antes de
esta ronda (la fila no cambia eso, segun el propio `permisos.sql` de
R09-E), y el `CREATE OR REPLACE FUNCTION` es el patron de las
migraciones del repo (0020, 0038, 0047, entre otras). La verificacion
de la 0072 ademas exige ahora la funcion exacta de cada trigger.

## Lo que no se corrigio y por que

- La clasificacion 409/502 de la pausa lee el estado del lote despues
  del fallo, sin candado: la ventana es estrecha, no corrompe datos y
  corregirla pide candado por lote en toda la pausa, fuera de esta
  tajada.
- El conteo de vivos del CLI del vigia se lee antes de barrer y puede
  diferir de lo que el barrido ve si el cron se solapa consigo mismo:
  mismo alcance que el punto anterior.
- El orden del ledger con `MAX(orden)` de la fabrica no toma candado
  por lote: es el patron que ya traian los caminos de pausa y reanudar
  antes de esta ronda, y cerrarlo pide candado por lote en toda la
  fabrica.
- La vista previa sigue sin marcar el vivo del mismo listing con otro
  plan (lo caza `lanza` como fallo del parcial): es el comportamiento
  anterior a esta ronda y no lo senalo ningun bloqueante.
- Las migraciones conservan su `BEGIN`/`COMMIT` dentro del archivo,
  como todas las del repo; correrlas con `psql -1` avisa que ya hay
  una transaccion en curso, sin efecto hoy porque no hay sentencias
  despues del `COMMIT`.
- El respaldo `predeploy` de la simulacion se compara contra el mismo
  SHA: en simulacion no hay servidor del cual copiar el codigo
  vigente; el camino real si lo copia.

## Simulacion repetida

El ciclo de D.6 se repitio con el codigo final (ensayo, despliegue,
checklist y rollback contra las bases locales): el ensayo cierra con
`ENSAYO OK`, el despliegue aplica y anota la 0071 y la 0072, el
checklist da 0 fallas y 3 sin medir (insumos del dueno), y el rollback
reversa las dos migraciones y termina con la vista legible tras
reotorgar el `SELECT`. Las salidas estan en la carpeta de D.6.
