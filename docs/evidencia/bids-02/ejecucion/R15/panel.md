# Panel poteto de R15

Diversidad: familias Muse y GLM. Los hijos Muse corrieron con
muse.workflow en paralelo (agente poteto-agent, modelo y esfuerzo de
la sesion). Los GLM corrieron con `opencode run -m
zai-coding-plan/<modelo>` en solo lectura sobre el diff contra la
base 83d2d73.

## Hijos poteto-agent (Muse)

- Ajustes y rejuego: un bloqueante. La reconciliacion no sellaba la
  H#N pendiente cuando el sync ya observo el destino, reproducido
  con su sonda contra datos reales. Corregido en 886bd43 con la
  regresion test_r07_09c_reconcilia_sella_la_pendiente_hn, nacida en
  rojo (devolvia la base id 1 en vez de la H#2 id 3).
- Busquedas: sin bloqueantes. Dos no bloqueantes: la guarda de texto
  de G1c (ya declarada en la evidencia de R13-F) y la prueba de
  R13-K a nivel endpoint. La de la pagina se agrego en 627c2e7
  (test_r13k_pagina_busquedas_pinta_el_gasto_en_dos_decimales).
- Mutantes y D.7 (lector fresco que ejecuta): bloqueantes de
  evidencia. La evidencia de D.7 no levantaba la app del checklist,
  no declaraba psql y pg_dump en el PATH, la reversa simulada decia
  codigo restaurado sin restaurar nada y el md5 simulado compara el
  SHA contra si mismo. Corregidos en 627c2e7 (comandos literales en
  la evidencia, cierre honesto de la reversa y alcance del md5
  declarado). La corrida de mutantes de R07-M se repitio y se guardo
  en su evidencia (ce7a5c9).

## Revisores GLM

- glm-5.3 (zai-coding-plan): un bloqueante. El tar del despliegue
  real de D.7 no copiaba tools/rejuega_niveles.py y la guarda md5 lo
  exigia; toda corrida real abortaba. Corregido en 1706166 (el tar
  copia todo tools, como el respaldo y la reversa). Aceptado tambien
  lo del regreso pendiente ya observado: f62f20d, con la regresion
  test_r07h_ofrece_regresar_si_hay_un_regreso_pendiente nacida en
  rojo. Su nota del redondeo de _dinero_2 se refuta: la fila pide dos
  decimales y ningun criterio del plan fija HALF_UP para esa
  presentacion (el HALF_UP vive en _pct de la referencia, que si lo
  fija la regla de P.6).
- glm-5.2 (zai-coding-plan): sin bloqueantes. Su primera corrida no
  llego a veredicto porque intento montar su propio Postgres; se
  relanzo con el entorno ya dicho y cerro.
- glm-4.7 (zai-coding-plan): sin bloqueantes.

## No bloqueantes que quedan nombrados

- Consultas extra por ajuste regresable en la pantalla de dinero
  (crece con el historico; hoy irrelevante).
- La prueba de R07-D ejerce la consulta interna y no la funcion
  publica del rejuego; si discrimina (el mutante la pone en rojo).
- config_por_id devuelve None y sus dos consumidores no lo tratan;
  inalcanzable hoy por la llave foranea de antes_config_id.
- Bases de simulacion de rondas anteriores (orbit_sim_d5,
  orbit_sim_d6 y otras) dejan el rol app_jev con objetos vivos y la
  prueba test_reversa_deja_la_base_como_0001 falla en este cluster
  local; en CI, con Postgres fresco, esa falla no ocurre. Las dos bases de esta
  ronda (orbit_sim_d7 y orbit_copia_d7) ya se borraron.
- El sello de despliegue tiene precision de minuto y un ensayo y un
  despliegue dentro del mismo minuto comparten respaldo; patron
  heredado de D.6.

## deslop y no-comments

- deslop sobre el diff: sin slop que corregir; los comentarios
  nuevos explican el por que de cada puerta.
- no-comments con comment-sicko: marco el docstring de
  campana_config.config_por_id que repetia la firma (borrado en
  b1d8c9b) y los comentarios que atan cada arreglo a su fila y a su
  invariante. Esos se quedan: son el unico lugar donde las dos
  puertas de R07-09C y el contrato 409 de R07-H quedan escritos
  junto al codigo que los cumple.

## Pasada interrogate de R15-r2

Skills cargadas con read_skill en esta pasada: poteto-mode,
interrogate, unslop, lectores-frescos, deslop, no-comments,
durable-test-collateral y git. El panel de interrogate corrio con dos
hijos poteto-agent en paralelo sobre el diff 83d2d73..0083793 y con
revisores GLM por opencode (glm-5.3 y glm-5.2).

- Hijo poteto-agent de correccion: un bloqueante. La reconciliacion
  sellaba solo la primera pendiente en destino y la segunda quedaba
  huerfana. Regresion nacida en rojo y arreglo en 0166976, que sella
  todas las pendientes en destino con un ayudante compartido por las
  dos puertas.
- Hijo poteto-agent de simplicidad: sin bloqueantes. Se acepto la
  cache del vigente por campana en b892ae5, la vuelta muerta de
  historial en el formato de gasto en 57a59dc y un solo constructor
  de config desde fila en 24fa141.
- glm-5.3 interrogate: sin bloqueantes. Verifico baterias enfocadas
  y dos mutantes muertos.
- glm-5.2 interrogate: su sonda de dos pendientes confirmo el mismo
  bloqueante ya corregido en 0166976. Su bateria completa dio 4887
  passed con una sola falla ambiental conocida, la reversa que no
  puede borrar el rol app_jev por bases de simulacion viejas. Su
  grep final destapo que la guarda de D.7 seguia buscando el nombre
  viejo en singular y abortaba todo despliegue. Corregido en
  c3b7e92 con la prueba del grep en rojo antes y en verde despues.

## comment-sicko de R15-r2

Marco como MUST KILL los docstrings nuevos de los ayudantes de
sellado y un docstring de prueba. Se rechaza el borrado con el mismo
criterio aceptado en la primera pasada. Esos textos atan cada
arreglo a su fila y a su invariante junto al codigo que los cumple,
y quitarlos dejaria el por que fuera del repo. La deduplicacion de
constructores que si era slop se aplico en 24fa141.

## No bloqueantes de R15-r2 que quedan nombrados

- El predicado 409 de regresar sigue duplicado entre la pantalla y
  la ruta. Moverlo es un refactor mayor que esta pasada no toca.
- El config de antes de cada regresable se sigue leyendo por fila.
  Solo el vigente se cachea por campana desde b892ae5.
- config_por_id devuelve None sin trato en dos consumidores.
  Sigue inalcanzable por la llave foranea de antes_config_id.
- La carrera transitoria de 422 entre el plan y la relectura del
  vigente que noto glm-5.3 se cura sola en el siguiente intento.
