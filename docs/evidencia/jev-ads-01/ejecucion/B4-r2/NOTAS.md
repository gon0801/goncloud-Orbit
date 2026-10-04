# B4-r2 correctiva — VEREDICTO-B4-r1: CAMBIOS (B1 y B2)

Encargo: `/Users/dn/.local/state/jev-ads-01-loop/encargo-B4-r2.md`
(ACK 2026-10-04T05:37:13Z). Base: origin/master
`1136d7836dfadfe5c1679f008f4ff5c7ffa2bc01` (B3 #392); HEAD de partida
`e455615c64942b203f73f14ad49eb92fe8f78cea` (B4-r1). Filas del plan:
`plans/jev-ads-01.md` 2.1 y 2.2 (2.2 no se toca: sin hallazgos).

## SHA de codigo

`2d69858` — "jev-ads B4-r2: leer reproduce el exito de la reanudacion (B1) y
harvest compone origen/destino por separado en asesoria y /cortes (2.1)".
La bateria, Ruff y pre-commit corrieron sobre ese arbol; el commit posterior
de esta carpeta es SOLO documental (patron B4-r1).

## B1 — leer() mostraba el PRIMER resultado del par aunque la reanudacion
lo hubiera resuelto con exito

Sintoma (repro del veredicto, corrido contra `e455615` ANTES del arreglo):

```
$ cp .../repro-B4-r1/test_repro_b4_leer_fallo_luego_exito.py tests/
$ PYTHONPATH=.:tests uv run --frozen python -m pytest -q -s tests/test_repro_b4_leer_fallo_luego_exito.py
evaluar (reanudacion) -> HayCompatible(...)
leer -> Indeterminado(motivos=frozenset({'juicio_ausente', 'fallo_proveedor',
'universo_desconocido', 'ficha_ausente'}))
E AssertionError: Indeterminado(...) — assert False
1 failed in 0.32s
```

`evaluar` compuso `HayCompatible` (la reanudacion reintenta el par fallido:
intencion y resultado con ordinal 2) y la UI mostraba `Indeterminado(
fallo_proveedor...)` para la MISMA revision.

Prueba roja que discrimina (en `tests/test_api_dashboard.py`, sin archivos
nuevos): `test_asesoria_muestra_el_exito_de_la_reanudacion` — falla con el
mismo motivo antes del arreglo (`Indeterminado(motivos=frozenset({
'universo_desconocido', 'fallo_proveedor', 'juicio_ausente'}))`) y ademas
pina el escenario: `SELECT respuesta IS NOT NULL ... ORDER BY ordinal` debe
dar `[False, True]` (fallo ordinal 1 + exito ordinal 2 en ESA revision),
comprueba `leer()` y el GET de `/api/dashboard/cortes` (tipo
`hay_compatible`, con transporte HTTP parcheado que revienta).

Arreglo: `app/jev_ads.py` nueva `_evento_del_par`: por par, el ULTIMO exito
validado si existe; si no, el ULTIMO resultado (misma regla con la que
`_exito_previo` reutiliza en `evaluar`: ORDER BY ordinal DESC). Los eventos
de `reutilizacion` enlazan con `reutiliza_id` a un resultado de la MISMA
revision, y es ese resultado el que la regla resuelve; un evento de
reutilizacion nunca entra como par (respuesta y error son NULL).

Mutante (matado, evidencia en `mutante_b1_rojo.txt`): volver a la regla
previa (primer evento del par) ->
`test_asesoria_muestra_el_exito_de_la_reanudacion` FALLA con
`Indeterminado(fallo_proveedor, ...)`. Restaurado; `git diff` limpio.

## B2 — DoD 2.1 pedia "origen/destino" y no estaba (LISTO-B4-r1 lo habia
declarado CUMPLIDA sin sustento)

Estado previo: `DecisionARevisar` tenia UN censo; `VistaAsesoria` y
`cortes.html` no distinguian ambitos; ninguna prueba de asesoria cubria
origen/destino (grep sin resultados); `tools/jev_ads.py` rechaza
`--decision-id` y este encargo PROHIBE tocarlo.

Decision: opcion (a) del encargo (el plan NO difiere origen/destino: la fila
2.1 lo exige en el DoD y no hay fila posterior que lo reciba). Sin migracion
nueva: `jev_revision.censos` es JSONB (migracion sellada 0049) y el contexto
congelado gana la clave `"destino"` solo cuando el sujeto la lleva; las
revisiones viejas (sin `"destino"`) se leen igual que antes.

Implementacion:
- `DecisionARevisar.destino_censo: CensoCongelado | None = None` (harvest).
- `evaluar`: enriquece y compone el termino contra el censo del ORIGEN y
  contra el censo del DESTINO por separado (universos jamas mezclados;
  presupuesto HTTP compartido; los eventos son por par (termino, ficha,
  contrato) y sirven a ambos ambitos si una ficha coincidiera).
  `Revision.destinos` trae la composicion del destino (vacia sin destino).
- `_abrir_revision`: congela `contexto["destino"]` y las fichas de AMBOS
  ambitos en `ficha_version_ids` (la vigencia cubre los dos).
- `_vista_de_revision`: reconstruccion historica por ambito con los MISMOS
  eventos de la revision y la regla B1; `VistaAsesoria.destinos` y
  `como_dict()["destinos"]`.
- `/cortes`: `cortes.html` renderiza cada veredicto con su ambito
  ("origen · termino: ...", "destino · termino: ..."); sin destino la fila
  de destino no aparece. La relevancia sigue sin presentarse como error
  economico y el veto sigue visible.

Pruebas que discriminan origen vs destino (rojo antes del arreglo:
`KeyError: 'destinos'` en la prueba de API y `assert 'origen' in html`
fallando en la de plantilla):
- `test_cortes_asesoria_origen_y_destino_por_separado`: origen (grupo 9101,
  P1, fallo `no_satisface`) -> `Indeterminado(universo_desconocido)`;
  destino (grupo 9301, P2, `satisface`) -> `HayCompatible([p2], 1/1)`.
  Mismo termino, veredictos distintos; p2 jamas aparece en el origen;
  cero HTTP (transporte parcheado) y cero INSERT/UPDATE (conteos jev_*
  antes/despues iguales); veto visible.
- `test_cortes_plantilla_asesoria_sin_error_economico_y_veto_visible`
  (extendida): "origen" y "destino" en el HTML, "sin compatibilidad"
  (destino `ninguno_compatible`), y la palabra "error" sigue ausente.

Mutante (matado, evidencia en `mutante_b2_rojo.txt`): componer el destino
con el censo del ORIGEN -> la prueba discrimina y FALLA
(`Indeterminado(ficha_ausente, ...)` en lugar de `HayCompatible`).
Restaurado.

## Limites declarados (sin ocultarlos)

- El pegamento CLI `--decision-id` sigue RECHAZADO: `tools/jev_ads.py` esta
  prohibido en este encargo (el propio CLI dice "llega con la fila 2.1").
  La capacidad (evaluar + guardar + leer + mostrar origen/destino) queda
  implementada y probada llamando `AsesorAds` directamente; hasta que el
  CLI la exponga, una revision atada a decision no puede crearse en
  produccion y la asesoria de `/cortes` seguira `null` para decisiones
  reales. Declarado para el VEREDICTO y para el bloque de operacion.
- 2.2 no se toca (sin hallazgos del veredicto).
- `ruff format .` (comando estandar del repo) reformateo bloques Python de
  7 .md ajenos; se revirtieron (fuera de la lista de archivos permitidos).

## Verificacion (comandos y salidas reales)

- Rojo B1 (repro del veredicto, pre-arreglo): 1 failed (arriba).
- Rojo B1 (regresion, pre-arreglo): `Indeterminado(fallo_proveedor, ...)`.
- Rojo B2 (pre-arreglo): `KeyError: 'destinos'` y `assert 'origen' in html`.
- Verde focalizado: `verde_focalizado.txt` — 233 passed (jev_ads, jev_cli,
  jev_catalogo, jev_juicios, api_dashboard, api_fabrica); Ruff check/format
  limpios.
- Mutantes in vivo (2 aplicados, 2 matados): `mutante_b1_rojo.txt`,
  `mutante_b2_rojo.txt`.
- Bateria completa una vez sobre el arbol de `2d69858`:
  `PYTHONPATH=.:tests uv run --frozen python -m pytest -q` ->
  `3842 passed, 1 warning in 264.08s`.
- `pre-commit run --all-files` -> 9 hooks Passed (sin `--no-verify`; el
  commit de codigo paso sus hooks).
