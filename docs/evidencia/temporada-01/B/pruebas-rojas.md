# TEMPORADA 01 B: pruebas rojas (regla 9) y notas de comprobacion

SHA previo aplicable: b9b325cfda3187f1614b342532dcda2977dde27b (master con el
bloque A integrado y desplegado). Las tres pruebas nombradas por la fila B.2
se copiaron SIN cambios a un worktree de ese SHA y la coleccion entera falla
porque las firmas que usan no existen todavia:

```
$ PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q \
    tests/test_temporada01_b.py -k "temporada01_b_pendiente or \
    temporada01_b_episodio_reparado_tras_caida or temporada01_b_fin_de_reduccion"
E   ImportError: cannot import name 'CambioFechado' from 'app.optimizer.caso'
E       (...\sha-previo\app\optimizer\caso.py)
ERROR tests/test_temporada01_b.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 1.59s
```

Las pruebas DB (episodio_reparado_tras_caida y fin_de_reduccion) fallan en el
SHA previo por la misma raiz y ademas porque las cuatro tablas de temporada no
existen (su fixture usa la cadena canonica del propio SHA, que no trae 0093).
La prueba de migracion usa `migrations/0093_temporada01_episodio.sql`, que no
existe en el SHA previo (FileNotFoundError al leer el archivo).

## Notas de comprobacion

- `tests/test_replay_ads_economico.py` (citado en el Acepta de B.3 y D.2) no
  existe ni en `tests/` ni en `tools/` en el SHA entregado: el replay del motor
  economico vive en `tests/test_optimizer_politica.py` y los candados de replay
  del ciclo en `tests/test_cycle.py` (test_replay_*, test_golden_replay_* y
  test_constantes_historicas_de_replay_son_inmutables). El Acepta de B.3 se
  corrio con ese equivalente (acepta-b3.txt: los 4 archivos del plan mas
  test_optimizer_politica.py) y queda registrada la diferencia con el texto de
  la fila.
- Windows: pytest corre con `--basetemp` propio (el cleanup de symlinks de
  pytest falla en este usuario), misma nota de la evidencia del bloque A.
- gitleaks no esta instalado en esta PC (pre-commit trueno: command not
  found). El escaneo de secretos del diff fue manual con patrones
  (password/token/secret/AKIA/PEM/ghp_/sk-): sin coincidencias en app/, tests/,
  verify/ ni en los archivos nuevos (migraciones, temporada_io, recuperacion,
  test_temporada01_b). Las credenciales de los mocks son falsas (patron del
  repo).
- La cadena de migraciones de las pruebas que ejercitan el aplicador
  (test_apply, test_apply_cola, test_apply_harvest, test_cycle,
  test_cycle_apply, test_api_write, test_apply_schema) paso a incluir
  0060/0061/0067/0093: la cadena de prueba ES la de produccion (patron de
  0081). El aplicador ahora cierra temporada_efecto en registra_sin_aplicar y
  en _confirma_resumen.
- _db_ajustes de test_api_write ya no aplica 0061/0067 a mano: la cadena base
  las trae.

## Ronda de revision (fix del bloqueo persistente)

Review de PR #697: si `reconcilia_bids` sella un intento sin resumen
(fallo:readback_ambiguo, fallo:reconciliado_sin_identidad,
fallo:ancestro_no_enabled), el efecto de temporada quedaba abierto y TX3
reventaba contra `temporada_efecto_uno_pendiente` en cada corrida.

Fix en dos partes: (1) los tres sellos sin resumen de `reconcilia_bids`
registran ahora el desenlace terminal (`registra_sin_aplicar` con
entidad_no_decisora o sin_respuesta y el sello en el detalle); (2) TX3
cierra como cinturon los efectos con TODOS los intentos sellados sin
desenlace ('sin_aplicar'), conservando abiertos los de intento sin sello.

Prueba nueva: `test_temporada01_b_efecto_con_intento_sellado_se_cierra`
(entrada 8->5.60 sin confirmar, intento sellado ambiguo, Amazon sigue en
8.00; el ciclo siguiente cierra el efecto viejo, decide la entrada otra vez
y la confirma). Demostrada fallando contra el SHA entregado del PR:

```
$ git worktree add ... e307da170df66badf30ce97f4d6d3fa88a81997b
$ pytest -q tests/test_temporada01_b.py::test_temporada01_b_efecto_con_intento_sellado_se_cierra
E           psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "temporada_efecto_uno_pendiente"
1 failed in 7.60s
```

## Ronda de revision 2 (cinco hallazgos de devin)

Pruebas nuevas (todas demostradas fallando contra el SHA entregado
8579569120ae78b4731bdefa4ed5f1766d966547 con el worktree):

1. `test_temporada01_b_pendiente` (ampliada): piso fraccionario 5.6049 ->
   `destino_entrada` devolvia 5.6049 (fuera de centavos); techo fraccionario
   13.999 -> 13.999; nivel de regreso clampeado sin redondear.
2. `test_temporada01_b_trayectoria_con_temporada_rejuega`: round-trip del
   caso con un cambio de temporada dentro de `Trayectoria` -> `_cambio_desde`
   levantaba ValueError (el origen `temporada` no estaba en su tupla) y el
   rejugado/ruido descartaban la decision.
3. `test_temporada01_b_episodio_reparado_tras_caida` (ampliada): el regreso
   con el episodio YA CERRADO no abria fila en temporada_efecto (TX3 solo
   abria efectos con episodio abierto) -> sin desenlace ni exclusion
   divergente del regreso.
4. `test_apply.py::test_regreso_del_dueno_ignora_los_movimientos_de_temporada`:
   la racha de la ACCION contaba la entrada de temporada (8 -> 5.60) y el
   destino del regreso era 8.00 en vez de 5.60.
5. `test_lecturas_caso.py::test_trayectoria_en_practica_mira_sus_90_dias`:
   con la historia extendida, `_cambios_por_hoja` recibia 365 dias y el
   backfill del regreso tomaba el recorte de hace 95/100 dias (piso 5.00 en
   practica vs 7.00 con la temporada apagada).

Salida de la corrida roja (extracto): `assert Decimal('5.6049') ==
Decimal('5.61')`, `assert Decimal('8.0') == Decimal('5.60')`, `AssertionError:
la trayectoria de la practica es la de 90 dias`, y las tres pruebas DB
fallidas (UniqueViolation ya no: en esta ronda el fallo DB del regreso fue
ausencia de fila divergencia).

## Ronda de revision 3 (rango sin centavos factibles tumba la plataforma)

Review de PR #697: un goal con rango SIN ningun centavo factible (5.6050 a
5.6090, la validacion de goals acepta cuatro decimales) hacia que
`nivel_de_regreso` levantara ValueError al salir la baja: la excepcion
escapaba por `_frontera_temporada`, `corre_ciclo` marcaba el ciclo failed y
TODA la plataforma quedaba sin decisiones hasta cambiar el rango.

Fix: `nivel_de_regreso` devuelve None (sin excepcion) cuando el rango no
tiene centavos; `reduccion_vigente` conserva la reduccion SIN fin falso;
`_pendiente_desde` devuelve SinPendiente(dato_faltante); `frontera` mantiene
la hoja en fase "baja" (conservadora, como un regreso retenido). El ciclo
corre y las demas hojas deciden.

Prueba nueva: `test_temporada01_b_rango_sin_centavos_no_tumba_plataforma`
(dos campanas: la del rango roto guardado a media baja deja su regreso
pendiente sin fin falso; la sana regresa a 8.00; el ciclo corre done).
Demostrada fallando contra ceb175a con el ValueError del review:

```
E           ValueError: nivel_de_regreso sin centavo factible en el rango del goal: [5.6050, 5.6090]
1 failed in 8.07s
```

## Ronda de revision 4 (doble recorte tras lectura ambigua)

Review de PR #697 (devin, hilo BUG_pr-review-job-993312df_0001): si Amazon
APLICA la entrada pero la lectura posterior al PUT falla y la de
reconciliacion tambien, el intento queda sellado 'fallo:readback_ambiguo'
sin resumen; el cinturon TX3 de la ronda 2 lo cerraba como 'sin_aplicar' y
liberaba la hoja a ciegas: el sync trae 5.60 al cache y `pendiente` calcula
OTRA entrada 5.60 -> 3.92 (doble recorte).

Fix (la recomendacion del review): la reconciliacion retoma los intentos
sellados ambiguos sin desenlace con GET fresco JAMAS reenviando el PUT:
puja == destino -> CONFIRMAR la decision (resumen + probe
'ok:reconciliado' + cache); puja == old_value -> no aplico ('perdida' +
efecto 'sin_aplicar', hoja libre); tercer valor -> divergencia (efecto +
exclusion); GET ambiguo de nuevo -> queda abierto y el siguiente ciclo lo
reintenta. `lee_estado` clasifica esos efectos 'en_vuelo' (la hoja no
re-decide) y el cinturon TX3 los excluye del cierre a ciegas.

Prueba nueva:
`test_temporada01_b_entrada_ambigua_se_confirma_al_reconciliar` (entrada
8->5.60, intento sellado ambiguo, cache SINCRONIZADO a 5.60; el ciclo
siguiente confirma la entrada original con GET fresco y NO decide otro
recorte; el ciclo posterior rejuega la cadena confirmada). Demostrada
fallando contra 236cefbb854de81c839a3630a1480f6f44121d15 con el doble
recorte exacto del review:

```
E       AssertionError: no doble recorte
E       assert [(3, Decimal('3.92'), 'entra_temporada_baja', '1')] == []
E         Left contains one more item: (3, Decimal('3.92'), 'entra_temporada_baja', '1')
1 failed in 7.60s
```

## Ronda de revision 5 (segunda pasada de Devin: 4 hallazgos sobre la
## reconciliacion de sellados-ambiguos)

Todos demostrados fallando contra el SHA entregado
17f53fbc4cd24aa07d24d570641d56d6db57b1d2:

1. `test_temporada01_b_pendiente_ambigua_bloquea_nuevas_pujas` (apply.py:527):
   tras sellar el intento ambiguo, `hojas_con_bid_sin_sello` dejaba de cubrir
   la hoja y el ciclo podia generar y enviar otra puja. Fix: el criterio de
   intentos pendientes se comparte entre la conciliacion y la guarda del
   ciclo (efecto abierto + rastro ambiguo sin desenlace); la excepcion de
   Pausar se mantiene. Roja: decision R9 aplicada de nuevo (sin skip
   bid_en_vuelo).
2. `test_temporada01_b_tercer_valor_divergencia_definitiva` (apply.py:429):
   el tercer valor cerraba el efecto sin resumen y la decision seguia
   calificando: una puja posterior al destino la confirmaba atribuyendo al
   optimizador un cambio del operador. Fix: resumen verify_ok FALSE en la
   misma transaccion que el probe y el cache. Roja: segunda conciliacion
   volvia a leer la decision.
3. `test_temporada01_b_ambigua_campana_pausa_no_descarta` (apply.py:355):
   con la campana pausada, el gate escribia un desenlace terminal sin
   verificar Amazon (perdida de la base de regreso). Fix: sin desenlace
   terminal para un intento ambiguo; sin HTTP mientras tanto; al reactivar,
   confirma la reduccion sin reenviar el PUT. Roja: el ciclo consultaba
   Amazon y descartaba.
4. `test_temporada01_b_ambigua_sin_puja_legible_reintenta` (apply.py:424):
   un listado sin la fila ceraba el efecto como divergencia sin constatar
   valor. Fix: sin puja legible es NO concluyente: el efecto queda abierto y
   el siguiente ciclo con la fila legible confirma la entrada original
   (jamás 5.60 -> 3.92). Roja: efecto cerrado sin valor y sin
   confirmacion posterior.

## Ronda de revision 6 (el flujo que COMIENZA sin sello con campana pausada)

Review del dueno (PRRT_kwDOUANqbs66ZGTU): el tratamiento conservador solo
cubria intentos YA sellados ambiguos; si el crash deja el intento SIN sello
y la campana aparece PAUSED en la primera reconciliacion, el gate grababa
'entidad_no_decisora' terminal y cerraba el efecto SIN leer Amazon: la
entrada aplicada pierde su base de regreso y se repite como otro recorte al
reactivar.

Fix en reconcilia_bids (loop de abiertos): si el intento pendiente tiene
efecto de temporada ABIERTO, el gate de ancestros NO escribe desenlace ni
consulta Amazon (queda pendiente, sin sello); al reactivar los ancestros el
mismo reconciliador lo resuelve con GET fresco. El camino historico (sin
efecto de temporada) se mantiene intacto.

Prueba nueva:
`test_temporada01_b_sin_sello_campana_pausa_reconcilia_al_reactivar`
(decision sin resumen + intento SIN sello + efecto abierto + campana
PAUSED + cache sincronizado a 5.60; ciclo 1 sin HTTP y sin descarte; ciclo
2 con la campana reactivada confirma con GET fresco; ciclo 3 jamas otro
recorte 5.60->3.92). Demostrada fallando contra
7bd7d1127d880d9ad51246d91801b02f81c7b414: el intento quedaba sellado
('entidad_no_decisora') en vez de pendiente.

## Ronda de revision nativa 2 (tres hallazgos sobre 7bd7d11)

1. `test_temporada01_b_sin_sello_sin_bid_reintenta` (apply.py:550): la
   primera lectura SIN fila o sin bid NO es concluyente para un intento
   SIN sello con efecto de temporada abierto: queda pendiente sin sello ni
   resumen (antes se sellaba fallo:readback_sin_bid con resumen FALSE y
   efecto divergencia). El ciclo siguiente, con la fila legible, confirma
   la entrada original con GET fresco. Roja contra dcddd0d: el efecto
   quedaba cerrado ('divergencia') y el intento sellado.
2. `test_temporada01_b_bid_ordinario_ambiguo_bloqueado` (apply_cuota.py:444
   + apply.py:491): la guarda del ciclo incluye TODOS los intentos sellados
   ambiguos sin desenlace (temporada Y ordinarios; antes solo temporada).
   Roja contra dcddd0d: la hoja ordinaria NO estaba bloqueada y TX2 creaba
   la decision obsoleta (otro recorte sobre el bid ya reducido).
3. El hallazgo apply.py:491 de la nativa coincide con el hilo del dueno
   PRRT_kwDOUANqbs66ZGTU resuelto en dcddd0d (el gate ya no escribe
   desenlace cuando hay efecto de temporada abierto); esta ronda ademas
   cubre el caso bid None del loop de abiertos (punto 1) y la guarda
   general (punto 2).

## Estado final de los Aceptas sobre el SHA de cierre

- B.1: `grep -qx 0 docs/evidencia/temporada-01/B/consulta-previa.txt` -> OK.
- B.2: `pytest tests -k temporada01_b_` -> 17 passed (14 del bloque + las 3
  de las rondas de revision: campana_pausa sin sello, sin_bid reintenta y
  bid ordinario bloqueado).
- B.3: test_cycle + test_apply + test_apply_cola + test_lecturas_caso +
  test_optimizer_politica -> 244 passed. Techo de apply.py actualizado a
  1475 (lineas reales tras las ramas conservadoras de la revision nativa).
