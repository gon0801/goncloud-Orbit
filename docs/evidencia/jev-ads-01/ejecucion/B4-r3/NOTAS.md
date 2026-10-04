# B4-r3 NOTAS (correctiva del ciclo automatico post-push de B4-r2)

Base: cdc45cff5cf77c64bf755a893b25aea26b9fc52f (PR #393). Cuatro hallazgos
nuevos de la revision automatica (DeepSeek sticky + CodeRabbit); dos
reproducibles sobre codigo de lectura, por eso no se mergea y va esta ronda.

## B1 (Medium, bloqueante): vigencia por el MISMO predicado que ficha_vigente

- Antes: el chip Obsoleta/Vigente de `_vista_de_revision` usaba un EXISTS por
  producto_id+plataforma (una ficha posterior no revocada con created_at
  mayor) mas vencimiento propio. `ficha_vigente` (app/jev_catalogo.py) exige
  cubrir EL LISTING, no estar vencida y SER LA SELECCIONADA (observado_at
  DESC, created_at DESC). Discrepancia en ambos sentidos (reproducida en
  rojo_b1.txt: `assert Obsoleta() == Vigente()`).
- Arreglo: nueva `AsesorAds._vigencia_de_miembros`: para el listing de cada
  miembro (origen y destino) pregunta a `ficha_vigente` SI MISMA (import
  perezoso, misma fuente del predicado: un numero, una fuente) y compara el
  id seleccionado con la ficha congelada del miembro. Sin comprobaciones
  posibles -> NoComprobable; alguna desplazada -> Obsoleta; resto Vigente.
  La revocacion y el vencimiento quedan cubiertos por el predicado (una
  ficha revocada o vencida ya no es seleccionable). El SELECT de fichas de
  la vista pierde created_at/revocada/hay_mas_nueva (ya no se usan).
- Detalle de IO: la conexion del GET (/cortes) llega con
  `row_factory = dict_row` (app/api_dashboard.py:1345) y `ficha_vigente`
  desempaca la fila por posicion (revienta "hechos de ficha con forma
  inesperada"). Adaptador `_FilasPosicionales` (clase local dentro del
  metodo): ejecuta por `conn.cursor(row_factory=tuple_row)`, patron ya usado
  en api_dashboard.py:1281. El import de psycopg.rows es perezoso dentro de
  AsesorAds: el candado AST de tests/test_jev_ads.py sigue en verde.
- Miembros sin ficha o sin producto_id se saltan (no aportan listing a
  comprobar); fichas no referenciadas por ningun miembro no afectan la
  vigencia (no forman parte de lo que la vista compone).
- Regresion: test_asesoria_vigencia_usa_el_predicado_de_ficha_vigente
  (control Vigente; sentido 1 ficha posterior de OTRO listing -> Vigente;
  sentido 2 ficha posterior VENCIDA -> Vigente; inverso ficha que SI
  desplaza -> Obsoleta). Mutante EXISTS: FAILED (mutante_b1_rojo.txt).
  El helper _ficha_jev del test gano el parametro opcional
  revisar_antes_de (default intacto).

## B2 (Low, bloqueante por fidelidad): composicion_de salta solo-no-activos

- Antes: evaluar.pares_de salta miembros _solo_no_activo (ARCHIVED) y
  composicion_de (lectura historica) no: un ARCHIVED sin ficha aportaba
  FichaFaltante de mas y la vista lista ficha_ausente aunque evaluar no lo
  compuso (rojo_b2.txt: leer={ficha_ausente, no_anunciado,
  universo_desconocido} vs evaluar={no_anunciado, universo_desconocido}).
- Arreglo: la linea sugerida, `if _solo_no_activo(miembro.estados): continue`
  al inicio del bucle de composicion_de (app/jev_ads.py).
- Regresion: test_asesoria_leer_salta_solo_no_activos_como_evaluar: censo
  real (censo_grupo) con P1 ENABLED con ficha + P2 ARCHIVED sin ficha;
  evaluar y leer deben dar los MISMOS motivos y ficha_ausente ausente.
  Mutante (guarda quitada): FAILED (mutante_b2_rojo.txt).

## B3 (Low): muerta eliminada

`_juicio_de_respuesta_guardada` (fin de app/jev_ads.py) duplicaba exacto a
`_juicio_de_respuesta` y nadie la llamaba (grep en app/ y tests/). Borrada;
Ruff y bateria en verde sin ella.

## B4 (Low): Finalidad conservada en codigo

La spec docs/superpowers/specs/2026-10-03-jev-ads-design.md:96 declara
`Finalidad = Literal["exclusion", "ruteo", "keyword"]` y la tabla :151 la
nombra; el codigo ya no la traia. Opcion preferida del encargo: una linea en
app/jev_ads.py junto a los otros alias (`Relacion`, `PlataformaAmazon`); la
spec queda congelada, sin reescribirla. Alias sin usos aun (futuras filas
de fabrica); Ruff no lo marca.

## Limites declarados (no ocultos)

- El arreglo B1 consulta `ficha_vigente` por listing distinto de los
  miembros de la revision (cache local por lectura); en /cortes el GET itera
  las decisiones de la pagina, igual que ya hacia la vista con su SELECT.
  Cero HTTP externo y cero INSERT/UPDATE siguen probados
  (test_cortes_asesoria_guardada_sin_escritura_ni_http, en verde).
- La vigencia de fichas NO referenciadas por ningun miembro del censo queda
  fuera del chip (no cambia lo que la vista compone); si el operador
  necesitara ese caso, es fila nueva del plan.
- Commits: codigo 5f6f315a728806ae91952e866dc59a5060cf87cc (bateria,
  Ruff y pre-commit ALL en verde sobre ese arbol); este commit de evidencia
  es docs-only bajo docs/evidencia/jev-ads-01/ejecucion/B4-r3/ (patron
  B4-r1/B4-r2). SIN push: Claw pushea tras VEREDICTO: APROBADO.
