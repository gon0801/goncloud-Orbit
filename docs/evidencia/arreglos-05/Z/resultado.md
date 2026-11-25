# Resultado del bloque Z: cierre de ARREGLOS 05

- SHA conjunto de cierre: `7b06778d376eb5631b0a2863beea6a90ab804da0` (merge del
  PR #773, `arreglos-05/k`, con BASE y A-K integrados). Todo lo de este informe
  se ejecuto sobre ese commit.
- Rama: `arreglos-05/z`.
- Modelo: `opencode-go/deepseek-v4.1-flash` (OpenCode Go), por orden de David, con
  `poteto-mode` completo.
- Aviso del bloque: se publica el resultado de la revision completa del codigo
  integrado, con evidencia de cada cierre y los pendientes reales.

## Veredicto: PARCIAL

Orbit queda aprobado en todo lo que depende de ARREGLOS 05, pero **el plan no
recibe pase global porque K sigue abierto para Amazon US**. La fuente externa no
permite demostrar el impuesto sobre comisiones en US (`K/conciliacion.md`,
`K/resultado.md`): MX y MeLi estan adjudicados y el impuesto viaja una sola vez en
la politica S4, pero US queda desconocido y no se escribe 16% ni cero. Este
bloque no marca K como terminado ni suaviza ese pendiente.

No hay ningun P0/P1 real abierto de ARREGLOS 05 en la aceptacion ni en los
recorridos. La revision independiente de Z.3 (Claude Opus 5.5, distinto al
implementador) no encontro ningun P0/P1 en el alcance revisado de `7b06778d`:
`Z/review.md`. Las esquinas y la deuda de fixtures de otros planes van a
"Seguimiento (P2)".

El veredicto es PARCIAL por **dos motivos**, y ambos quedan escritos:

1. **K-US sin adjudicar.** La fuente no permite demostrar el impuesto sobre
   comisiones en Amazon US. Es el pendiente que impide el pase global del plan.
2. **Limites de alcance de la revision Z.3.** El revisor declara fuera de su
   alcance, y este informe no los presenta como revisados:
   - la logica pura de `app/optimizer/` (`politica.py`, `temporada.py`,
     `replay.py`);
   - Repricing (`app/precio/`), MeLi (catalogo y precio de mercado), reputacion y
     SP-API;
   - el pipeline de estimacion y la ingesta;
   - UI y JS (`productos.js`, pantallas) y `tools/despliegue/*.sh`;
   - las migraciones 0095 a 0097 y 0099, conocidas solo por evidencia y por el
     codigo que las usa;
   - la cosecha de hermanas y su reversa automatica en detalle,
     `archivar_inertes` y `retiro_anuncios`;
   - las filas de `COBERTURA.md` de 03 y 04 (IDs 02, 04, 05, 06, 07, 14, 15 y 16);
     de ellas el revisor solo miro S1.

   Esos limites **no se convierten en P0/P1**: son cobertura declarada. Su
   comprobacion se apoya en la aceptacion integrada y en la evidencia de su plan,
   no en la revision independiente, y asi se anota en `Z/cobertura.tsv`.

Los P2 13 y 14 del informe (`test_avisar_campanas_*` y el fallo de Windows en
`test_precio_reglas`) son fallos previos ya declarados por este bloque; no son
hallazgos nuevos de Z.

## Aceptacion integrada (Z.2)

```bash
export ORBIT_ACCEPTANCE_XML="$PWD/docs/evidencia/arreglos-05/Z/acceptance.xml"
bash docs/evidencia/arreglos-05/comprobar.sh ALL \
  > docs/evidencia/arreglos-05/Z/acceptance.log 2>&1
```

- `Z/acceptance.xml`: `tests="31" failures="0" errors="0" skipped="0"
  time="112.697"`.
- `Z/acceptance.log`: `31 passed, 1 warning in 112.73s`. La advertencia es la
  deprecacion de `httpx` en `starlette.testclient`, no un fallo.
- 31 de 31 en verde. Los 5 rojos de producto que BASE dejo (TN03, TN10, TN11,
  TN12 y TN13) quedaron cerrados por B, C, F y G.

### Porte de fixture trazado (2 casos)

En la primera corrida del SHA conjunto, `test_13_reobservar_un_termino_maduro_no_aborta_el_ciclo`
y `test_control_arreglos01_b_no_harvestea_keyword_exact_propia` fallaban por una
sola causa comun: faltaba la decision `pause`. No es un defecto de producto. J
retiro la pausa de la era bandas y sin `ads_bid_politica_<plataforma>` la hoja
solo registra `politica_apagada` (`tests/test_arreglos05_j_modos.py`,
`tests/test_cycle.py`). El escenario de aceptacion venia de antes de J.

Porte aplicado, igual que BASE hizo con TN05/06/07/11:

- `revision/optimizer.py::harvest_scenario` siembra
  `{"ads_bid_politica_amazon_us": "niveles_v3"}`, la politica vigente, para
  conservar la pausa que estas aserciones siempre exigieron.
- **Cero aserciones tocadas.** Con el porte, el control produce
  `{pause, harvest, negative}` y TN13 el mismo lote mixto que exigia.
- El original queda byte a byte en `revision/historico/optimizer.py`, con
  SHA-256 `ee41a3fbacb6b4df469aebff0b351e63e2af2955653546aa159e64289f621ccf`,
  que coincide con `revision/manifest.json`. El manifiesto no cambia.

## Regresiones promovidas y vecinos (Z.2)

`comprobar.sh N a` .. `N k`, en serie, sobre el SHA conjunto:

| Letra | Casos | Resultado |
|---|---|---|
| a | 269 | 269 passed |
| b | 40 | 40 passed |
| c | 21 | 21 passed |
| d | 23 | 23 passed |
| e | 16 | 16 passed |
| f | 10 | 10 passed |
| g | 4 | 4 passed |
| h | 21 | 21 passed |
| i | 19 | 19 passed |
| j | 6 | 6 passed |
| k | 0 | sin casos a proposito (ver abajo) |

- Logs por letra en `Z/regresiones-<letra>.log` y resumen en
  `Z/regresiones.log`. Total 429 passed, cero fallos y cero omisiones.
- **`N k` no existe por decision de K.2**, no por omision: K no cambio el
  calculo y su aceptacion pide `N k` solo si cambia. Los cuatro casos que
  cubriria tienen control vigente y estan citados en `K/resultado.md`: TaxAmount
  anidado, total inclusivo, impuesto separado y dato ausente. No se inventa una
  prueba para aparentar cobertura.

Controles y candados en el mismo SHA:

- `tests/test_config_write.py -k arreglos01_x`: 1 passed (banda manual 01X).
- `tests/test_architecture.py`: 99 passed, 2 skipped. Las dos omisiones son los
  casos de permisos de archivo que Windows no puede reproducir
  (`tests/util_fs.py`); no son de aceptacion.
- `ruff check app tests tools`: All checks passed. `ruff format --check`: 541
  files already formatted. Ningun modulo nuevo rompe el presupuesto de 900
  lineas.

### Vecinos por causa

| Grupo | Suites | Resultado |
|---|---|---|
| Ciclo, apply y reversa | `test_cycle`, `test_cycle_apply`, `test_cycle_pause_cooldown`, `test_cycle_target_ciclo`, `test_apply`, `test_apply_cola`, `test_apply_harvest`, `test_apply_producto`, `test_apply_schema` | 341 passed |
| Temporada, estimacion, precios y API | `test_temporada01_a..i`, `test_estimacion_venta`, `test_estimacion_fees`, `test_estimacion_s4_caja_real`, `test_estimacion_aislamiento_fabrica`, `test_precio_reglas`, `test_api_dashboard`, `test_api_write` | 573 passed, 1 fallo |

El unico fallo es `test_precio_reglas.py::test_r4_g8_barrido_float_entra_a_subpaquetes`,
defecto de la prueba en Windows ya documentado por K: compara `"sub/flotante.py"`
contra una ruta con `\`. En Linux la ruta usa `/` y la prueba pasa. No es del
codigo de 05.

Los 20 commits citados en `Z/cobertura.tsv` existen y son ancestros de
`7b06778d`; la comprobacion se hizo con `git cat-file -e` y
`git merge-base --is-ancestor`.

## Decision sobre los fallos previos de master en esta PC (Z.2)

Se corrieron los archivos senalados sobre el SHA conjunto. Ninguno pertenece a la
aceptacion de 31 casos ni al codigo de ARREGLOS 05; son bootstraps de fixture que
aplican una cadena parcial de migraciones y quedaron atras cuando TEMPORADA y 05
agregaron tablas o consultas.

| Archivo | Fallos | Causa observada | Decision |
|---|---|---|---|
| `test_fabrica_f2_hermanas.py` | 24 | `UndefinedTable: relation "temporada_efecto" does not exist`; `ORDEN_F2` no aplica 0093 | Pendiente P2: fixture de F2, no producto. Produccion aplica la cadena completa |
| `test_fabrica_f2_biblioteca.py` | 16 | La misma tabla ausente | Pendiente P2, misma causa |
| `test_harvest_destino.py` | 3 | `db_f2_0092` aplica 0086/0092/0094 y omite 0093 | Pendiente P2, misma causa |
| `test_d3_adversary.py` | 2 | El mapa SQL falso no conoce `SELECT id, settings FROM config_version ...` | Pendiente P2: fixture de conexion falsa, no producto |
| `test_notifica.py` | 3 | `tuple index out of range` al leer filas de un `_conn_campanas` con forma vieja | Pendiente P2: fixture, no producto |
| `test_precio02_escenario_meli.py` | 1 | `ingest_run.ok` es False porque `ORDEN_M3` aplica una cadena parcial | Pendiente P2: fixture, no producto |
| `test_orbit07_m1.py` | 0 | Ya no falla en este SHA | Cerrado |

Logs: `Z/legado-fabrica.log` y `Z/legado-resto.log`. No se ocultan en un XML: se
declaran aqui y en `Z/cobertura.tsv` como deuda de fixture. El pase de aceptacion
de ARREGLOS 05 queda limpio.

## UI del mapa de pantallas (Z.4)

Detalle y evidencia en `Z/ui.md` y `Z/ui/`. Instancia del SHA conjunto en WSL,
`launch` y `doctor` OK (`{"ok": true, "health": "{\"status\":\"ok\"}"}`).

- Diez drives ejecutados: Resumen, Campanas, Decisiones, Salud, Contribucion,
  Propuestas, Inertes, Crear campanas, Settings y Productos sin clics.
- Todos los checks funcionales, HTML, JSON, CSP y de cableado en verde. Cuatro
  pantallas (Resumen, Campanas, Salud, Contribucion) marcan `ok=false` solo por
  la captura PNG: el unico navegador es Edge de Windows y el helper lanza Chrome
  con un perfil de ruta Linux. Se capturaron las cuatro paginas con Edge headless
  de Windows contra la misma instancia (`Z/ui/*-1440.png`).
- Settings adverso con Edge real: `1,000` no envia, error visible, cero POST, sin
  version nueva; `7` si intenta el POST. `ok=true`.
- Productos cubre limites simulados frente a vigentes, 422 con minimo, no
  guardar, encendido deshabilitado en practica y volver a practica.
- Resumen: el caso de datos desconocidos frente a cero es TN16, verde en la
  aceptacion.
- `cleanup` cerro el pid y la base `orbit_verify_z_cierre`; no queda uvicorn ni
  base `orbit_verify_z%`.

## Secretos (Z.4)

`Z/secretos.txt`: `gitleaks detect` sobre el arbol del SHA conjunto encuentra 13
falsos positivos de `generic-api-key` (md5 de archivos, prosa que empieza con
"sale", tokens ficticios de pruebas), todos anteriores al SHA. Sobre el rango de
commits del bloque (`1191125c..HEAD`): `no leaks found`. No se commitea ningun
secreto.

## Matriz de cobertura (Z.1)

`Z/cobertura.tsv`: 14 IDs revalidados, 5 controles (01, 08, 09, D2 y 01X), la
nota D1 y las 53 fallas del legado agrupadas por area, cada fila con owner,
commit y comprobacion. El legado suma 53 sin doble conteo (Ciclo5 duplica
Politica1 y Politica3 se descarto con datos). K aparece como pendiente explicito
para US y no como cierre.

- 03 completo por su DoD: 20 bloques integrados y desplegados en `819fe1ff`.
- 04 completo por su DoD: 21 bloques integrados y desplegados en `f5dc86d5`, seis
  casos TN verdes (`docs/evidencia/arreglos-04/CIERRE/`).
- TEMPORADA completo por su DoD: bloques I-H y olas OLA1-OLA3 desplegados; el
  unico pendiente que su cierre traslado a 05 (conciliar antes de decidir) quedo
  cerrado por A.2 y su reproduccion legada esta verde.

## CI del PR

- `reconcile`: verde sobre la cabeza de evidencia.
- Devin Review: falla solo porque el diff supera su limite de tamano
  ("Review skipped: diff exceeds the size limit"). Es informativo y no es
  compuerta; no se recorta evidencia para que quepa.
- CodeRabbit: skipping.
- El repositorio no tiene workflow de pytest en `.github/workflows` (solo los de
  `ai-review`). Por eso la compuerta de comportamiento de este cierre es la
  aceptacion en PG16 de Z.2, no un job de CI.

## Revision global (Z.3)

Informe completo y verbatim en `Z/review.md`.

- Revisor: Claude Opus 5.5, sesion independiente `goncloud-orbit-50`, distinto
  al implementador. Fecha 2026-11-25, solo lectura, sin ejecutar pruebas.
- SHA revisado: `7b06778d`.
- Alcance: unos 23 mil lineas de los modulos mutantes; ciclo y recuperacion (A),
  pujas y cupo 150 (H), identidad y reversas (B), reposicion (C), escritor unico
  (D), cola y veto (E), meta base (F), sello (G), TEMPORADA, Ajustes (I) y modos
  (J), fronteras de redaccion, Jev, fabrica y estimacion para K.
- **P0/P1: ninguno.** Contraste y refuto la clase de defecto que domino el plan
  (camino que no pasa por la tabla de estados, guarda que se adelanta a una
  lectura, ID o valor que se pierde) en 13 hipotesis.
- **P2: 15**, listados abajo como seguimiento, sin commit.
- Veredicto del revisor: **PARCIAL**. El codigo A-J y los controles cumplen sus
  contratos; K sigue abierto para Amazon US y eso impide el pase global.
- El revisor no corrio la aceptacion; el implementador la corrio sobre el mismo
  SHA (`Z/acceptance.xml`) y lo anota en `Z/review.md` sin tocar el veredicto.

## Pendientes

1. **K abierto para Amazon US**: hace falta una fuente que demuestre el impuesto
   sobre comisiones en US. Mientras tanto el veredicto global es PARCIAL.
2. **Revision global Z.3**: hecha por Claude Opus 5.5 (sesion `goncloud-orbit-50`),
   distinto al implementador. Cero P0/P1; 15 P2 en seguimiento. Informe en
   `Z/review.md`. No abre rondas de correccion porque no hay P0/P1 reales.
3. **Deuda de fixture P2** de los 49 casos de la tabla anterior, en los planes
   duenos de esos archivos.
4. Despliegue: este bloque es `lane: fast` y no despliega; el despliegue se
   acredita aparte.

## Seguimiento (P2)

Los 15 hallazgos P2 del revisor, sin commit y sin reclasificar. Ninguno es
P0/P1 segun `AGENTS.md`; el detalle con archivo y linea esta en `Z/review.md`.

1. `cycle_target.py:71`: el ancla por hoja de familia y grupo filtra
   `status='done'`; un ciclo `degraded` con TX3 comprometida no ancla la rampa.
   F solo corrigio la base de plataforma. Frena convergencia, sin perdida de
   dinero.
2. `apply.py:426-454`: un 5xx en la lectura fresca o un 429 dejan pujas con
   `sin_respuesta`; el ejecutor no las reintenta el mismo dia. Un dia de retraso.
3. `apply_cola.py:587-607`: el rollback de `_aisla_fila` tambien descarta los
   descartes de filas anteriores aun no comprometidos; se reevaluan despues.
4. `apply_cola.py:756`: si se pierde la carrera del claim despues de cobrar la
   cuota, esa unidad se pierde. Anterior a este plan.
5. `apply_cola.py:424-428`: el `choque_clave` se atribuye al ciclo de origen y no
   al ejecutor. Solo auditoria.
6. `migrations/0098:106`: una practica shadow vigente bloquea el alta live de la
   misma clave hasta 48 h. Coincide con el comportamiento previo.
7. `cycle.py:172-185`: el plan toma el ultimo origen del dia; un shadow forzado
   tras un live con pendientes no abre ejecutor. Requiere `--forzar`.
8. `cli.py` y `cycle.py:1094`: `--forzar` abre un segundo origen con otro cupo de
   150. Es manual y documentado.
9. `apply_harvest.py:574`: tras un 5xx en el POST, la keyword creada se adopta
   como incierta y la reversa no la borra. Conservador; dos eventos.
10. `apply_reconcilia.py:222-234`: un HTTP tardio tras perder el lease puede
    chocar con el sello del sucesor. Dos fallos encadenados; declarado en
    `DISENO.md`.
11. `apply_regreso_dueno.py`: el regreso manual corre fuera del claim del ciclo;
    con un PUT simultaneo gana por ultima escritura.
12. `apply.py:660-668`: si falla el readback de una puja no se guarda el ACK.
    Solo se pierde evidencia de auditoria.
13. `tests/test_notifica.py:1843`, `:1876`, `:1918`: tres pruebas rojas previas;
    el fake `_Conn9` tiene respuestas en orden fijo desalineadas. No hay
    evidencia de ruta real rota.
14. `tests/test_precio_reglas.py::test_r4_g8_barrido_float_entra_a_subpaquetes`:
    falla solo en Windows por el separador de ruta (declarado en K).
15. K-MX: 3 de 113 productos fuera del 0.1% de la razon 1.16; no cambia la
    mediana.
