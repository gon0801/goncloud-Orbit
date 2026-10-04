# B5-r2: ronda correctiva de la revision 2.R (bloqueantes de B5-r1)

- Fecha: 2026-10-04 (ACK 2026-10-04T08:38:18Z).
- Partida: rama `docs/jev-ads-b5`, base `a47086651ceae2e055d842fc41bc07262b93e46e`
  (origin/master), SHA visto por la ronda anterior `1bcbed12a187394038d69d232b93954d2c9b8215`.
- VEREDICTO-B5-r1: CAMBIOS. Confirmados 7 de 8 bloqueantes; B6 REFUTADO (el GRANT
  multi-tabla SI rompe test_roles_de_minimo_privilegio contra base real; la guarda
  estatica debil queda como residual, NO se toca en esta ronda).

## Arreglos aplicados (commit de codigo 42d07f5f3b10fbcde638c71c8f4ae0dce936597d)

Cada arreglo lleva en el MISMO cambio la prueba que lo habria detectado, demostrada
en rojo antes del arreglo y con su mutante registrado en rojo despues:

| # | Archivo | Arreglo | Prueba | Evidencia |
|---|---|---|---|---|
| B1 | app/jev_ads.py `_abrir_revision` | el SELECT de coherencia trae `contrato` y la comparacion exige `guardado[4] == contrato_json`; misma solicitud con OTRO contrato -> ValueError y un solo `contrato_sha256` por revision | `test_misma_solicitud_otro_contrato_rechazado` (nueva, en tests/test_jev_cli.py) | rojo-b1-b2.txt, mutante-b1-rojo.txt |
| B2 | tools/jev_ads.py | bloque `if __name__ == "__main__": raise SystemExit(main())` (patron identico a tools/jev_fichas.py:184; el encargo decia `sys.exit(main())`, misma semantica y el patron del archivo hermano) | `test_cli_modulo_ejecutable_da_error_config_y_no_es_no_op` (subprocess, exit 2 + mensaje) | rojo-b1-b2.txt, mutante-b2-rojo.txt |
| B3 | tests/test_jev_cli.py | guarda de consumidores RESUELVE formas de import: `from app import X` -> "app.X", relativo `from .x import y` -> "app.x.y", en cualquier profundidad (ast.walk ya cubria anidados) | misma prueba, endurecida | mutante-b3-rojo.txt (2 mutantes: `from app import jev_ads` y `from .jev_ads import AsesorAds`, ambos FAILED) |
| B4 | tests/test_api_dashboard.py | el rotulo se afirma PEGADO a su resultado con espacios normalizados: "origen · tenis blancos: compatible (7) · cobertura 1/1" y "destino · tenis blancos: sin compatibilidad en 3 producto(s)" | misma prueba, endurecida | mutante-b4-rojo.txt (etiquetas cruzadas FAILED) |
| B5a | tests/test_jev_ads.py | la guarda de pureza RESUELVE los imports anidados de todo nodo fuera de AsesorAds y compara por igualdad O prefijo con punto (`from app import db` -> "app.db", `import urllib.request` -> bajo "urllib") | misma prueba, endurecida | mutante-b5a-rojo.txt (FAILED en componer) |
| B5b | tests/test_jev_juicios.py | el wire afirma el termino LITERAL con fixture "Soporte MESA" (con mayusculas: con "soporte mesa" el mutante `.lower()` es un no-op y no discrimina; corrida previa descartada y documentada) | `test_wire_lleva_solo_termino_y_ficha`, endurecida | mutante-b5b-rojo.txt (FAILED) |
| B5c | tests/test_jev_juicios.py | `request_sha256` se fija contra el literal `6ddbb70b...b1d9` (ficha de id determinista) y diferenciales: cambia con otro termino, otros hechos y otro modelo. NOTA de diseno verificada: el id de ficha y la version del contrato NO van en el payload wire (viajan en la clave), asi que esos dos diferenciales propuestos por el revisor NO aplican y se dejan documentados en la prueba | `test_request_sha256_valor_fijo_y_sensible_al_contenido` (nueva) | mutante-b5c-rojo.txt (hash constante FAILED) |

- B6: NO tocado (refutado). Residual para el PR: la guarda estatica
  `test_migracion_trae_fks_y_roles` busca substrings `ON <tabla> TO app_jev` y no ve
  GRANTs multi-tabla; la red de seguridad REAL es `test_roles_de_minimo_privilegio`
  contra base viva, que si atrapa el caso (demonstrado por el revisor del VEREDICTO).

## Hallazgo de la correccion que merece registro

- B5b primera corrida: el mutante `.lower()` NO discriminaba porque el fixture usaba
  "soporte mesa" (ya minuscula). Corregido fijando "Soporte MESA" en el test del
  wire. La corrida descartada no se conserva como evidencia de mutante (no era un
  resultado valido de discriminacion) y se explica aqui.
- B5c: los diferenciales "otra ficha" y "otro contrato" sugeridos por el revisor
  contradecian el diseno del wire (solo hechos/desconocidos viajan; la identidad
  viaja por ficha id en la clave y el modelo en el payload): se fijaron los
  diferenciales que SI corresponden (termino, hechos, modelo) y la prueba lo
  documenta.

## Verificacion (salidas completas en este directorio)

- Focalizado: tests/test_jev_cli.py 20 passed; test_jev_ads.py 22 passed;
  test_jev_juicios.py 27 passed; test_jev_catalogo.py + cli + ads + juicios
  90 passed; test_api_dashboard.py 73 passed.
- Bateria completa sobre 42d07f5: `uv run --frozen python -m pytest -q` ->
  3847 passed, 1 warning, 224.49 s (bateria-completa.txt; 3844 de B4-r3 + 3 pruebas
  nuevas: B1, B2, B5c).
- pre-commit run --all-files: 9 hooks Passed (precommit.txt).
- Ruff check + format sobre los 7 archivos tocados: ok.
- Delta de codigo revisado: delta-codigo.diff (1bcbed1..42d07f5, 299 lineas; el
  hook trim-trailing-whitespace normaliza espacios finales al commitear: el diff
  exacto se regenera con `git diff 1bcbed12..42d07f5`).
- Auto-revision delta con revisor DISTINTO de claude y glm: ver delta-reporte.txt.

## Comandos de reproduccion (desde la raiz del repo)

- ROJO B1/B2 (arreglos guardados en stash): los dos comandos de
  `uv run --frozen python -m pytest -q tests/test_jev_cli.py::<prueba>` en rojo-b1-b2.txt.
- Mutantes: cada mutante-*.txt trae el comando exacto y el resultado FAILED; cada
  mutante se retiro dentro del mismo paso (sin `git checkout` global: el arreglo B1
  convive con app/jev_ads.py, los mutantes de ese archivo se retiran con edicion
  puntual verificada por grep).
- Estado del arbol: LIMPIO tras cada mutante (verificado con git status --porcelain);
  cero migracion; cero push; cero TypeSafe real (solo _Pedido/ResultadoPar falsos y
  bases temporales de db_jev).

## Skills/agentes/modelos

Sesion unica opencode (GLM zai-coding-plan/glm-5.3-flash); poteto-mode + unslop +
typesafe-ai cargadas; sin subagentes. Revisor delta: grok (ver delta-reporte.txt).
