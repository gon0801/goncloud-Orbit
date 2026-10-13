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
