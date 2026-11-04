# B5-r1: revision cruzada del diff acumulado (fila 2.R)

- Fecha: 2026-10-04 (ACK 2026-10-04T07:46:57Z).
- Base revisada: `afc1f3b68715c5bf05bdf939eefc9a6683adc0cd` (B1, PR 390) hasta HEAD
  `a47086651ceae2e055d842fc41bc07262b93e46e` (B4 #393, rama `docs/jev-ads-b5`,
  arbol limpio durante toda la ronda).
- Cambios cubiertos: 70 archivos, +8612/-5 (B2 1.1/1.2, B3 1.3/1.4, B4 2.1/2.2).
- Implementador excluido: glm (OpenCode, `zai-coding-plan/glm-5.3-flash`). Revisor
  efectivo en los 6 grupos: `claude` (CLI headless via cross-review.ps1, cadena
  auto `claude -> grok -> kimi -> qwen -> codex` con glm excluido; claude respondio
  primero en todos). Diversidad de la ronda: un solo revisor externo (claude) porque
  fue el primero disponible de la cadena en cada grupo.

## Alcance dividido (el diff completo pesa 415 KB > tope de 60 KB del script)

| Grupo | Pathspec | Caracteres | Reporte |
|---|---|---|---|
| G1 dominio | `app/jev_ads.py, app/jev_juicios.py` | 54519 | reporte-g1-dominio.txt |
| G2 catalogo/migracion | `app/jev_catalogo.py, migrations/` | 35847 | reporte-g2-catalogo-migracion.txt |
| G3 adaptador/CLI | `tools/, tests/test_jev_cli.py` | 43454 | reporte-g3-adaptador-cli.txt |
| G4 pantallas | `app/api_dashboard.py, app/api_fabrica.py, app/fabrica_web.py, app/templates/cortes.html, tests/test_api_dashboard.py, tests/test_api_fabrica.py, tests/test_architecture.py, verify/` | 56137 | reporte-g4-pantallas.txt |
| G5 pruebas dominio | `tests/test_jev_ads.py, tests/test_jev_juicios.py` | 27652 | reporte-g5-pruebas-dominio.txt |
| G6 pruebas catalogo | `tests/test_jev_catalogo.py` | 39238 | reporte-g6-pruebas-catalogo.txt |

Ningun grupo trunco (todos < 60000). Los 6 comandos exactos (formato identico, solo
cambia `-Archivos`):

```
export PATH=/opt/homebrew/bin:/Users/dn/.local/bin:/Users/dn/bin:$PATH
/Users/dn/.local/bin/pwsh -NoProfile -File /Users/dn/quality-kit/cross-review.ps1 \
  -Con auto -Excluir glm -Base afc1f3b68715c5bf05bdf939eefc9a6683adc0cd \
  -RepoPath /Users/dn/dev/wt/jev-ads-worker -Archivos "<pathspec de la tabla>"
```

Fuera de alcance declarado: `docs/evidencia/...` (161 KB de NOTAS/salidas de rondas
anteriores, texto de operacion, no codigo de datos ni permisos) y `docs/` general.

## Veredicto de la ronda: REABRE. 8 bloqueantes, los 8 reproducidos

Todo bloqueante fue demostrado contra el arbol real (mutacion temporal corrida y
REVERTIDA en el mismo paso; el arbol quedo limpio; evidencia en repro-*.txt).
Comando generico de cada repro: desde la raiz del repo, `uv run --frozen ...`.

| # | Grupo | Hallazgo | Repro (archivo) | Resultado |
|---|---|---|---|---|
| 1 | G1 | `_abrir_revision` no compara el contrato: misma solicitud con otro contrato no lanza ValueError y `jev_par_evento` queda con 2 `contrato_sha256` distintos bajo una revision | `repro-B1-contrato.txt` + `repro_b1_contrato.py` (`uv run --frozen python docs/evidencia/jev-ads-01/ejecucion/B5-r1/repro_b1_contrato.py`, exit 0) | REPRODUCIDO (exit 0: sin ValueError, eventos con 2 contratos) |
| 2 | G3 | `tools/jev_ads.py` sin `if __name__ == "__main__"`: `python -m tools.jev_ads evaluar ... --aplicar` es un no-op silencioso con exit 0 (sin DSN deberia salir 2). No hay console-script en pyproject | `repro-B3-main.txt` (`env -u ORBIT_DSN_ADMIN uv run --frozen python -m tools.jev_ads evaluar ... --aplicar; echo $?` -> exit 0 sin salida) | REPRODUCIDO |
| 3 | G3 | `test_los_consumidores_no_importan_al_asesor` no discrimina: `from app import jev_ads` en cycle.py pasa la guarda (compara `nodo.module`/`alias.name` exactos) | `repro-B3-guarda.txt` (mutante: `printf '\nfrom app import jev_ads\n' >> app/cycle.py`, correr `uv run --frozen python -m pytest tests/test_jev_cli.py::test_los_consumidores_no_importan_al_asesor -q`, revertir) | REPRODUCIDO (1 passed con el mutante puesto) |
| 4 | G4 | `test_cortes_plantilla_asesoria_sin_error_economico_y_veto_visible` no discrimina el ambito: intercambiar origen/destino en cortes.html sigue verde | `repro-B4-etiquetas.txt` (mutante: swap `veredicto("origen", r)` <-> `veredicto("destino", r)` en `app/templates/cortes.html`, correr `uv run --frozen python -m pytest tests/test_api_dashboard.py::test_cortes_plantilla_asesoria_sin_error_economico_y_veto_visible -q`, revertir) | REPRODUCIDO (1 passed con etiquetas cruzadas) |
| 5 | G5 | `test_modulo_puro_sin_red_ni_db_en_top_level` no discrimina: `from app import db` anidado en `componer()` (fuera de AsesorAds) pasa | `repro-B5-guarda-imports.txt` (mutante: anadir el import anidado en `app/jev_ads.py::componer`, correr `uv run --frozen python -m pytest tests/test_jev_ads.py::test_modulo_puro_sin_red_ni_db_en_top_level -q`, revertir) | REPRODUCIDO (1 passed con el mutante) |
| 6 | G5 | `test_wire_lleva_solo_termino_y_ficha` nunca verifica `payload["state"]["termino"]`: el wire puede mandar el termino alterado y la suite queda verde | `repro-B5-wire-termino.txt` (mutante: `termino.lower()` en `_payload` de `app/jev_juicios.py`, correr `uv run --frozen python -m pytest tests/test_jev_juicios.py -q`, revertir) | REPRODUCIDO (26 passed con el termino alterado) |
| 7 | G5 | `test_clave_estable_y_request_hash_con_mismo_contrato` es tautologia `f(x)==f(x)`: `request_sha256` constante pasa. Ningun otro test cubre la funcion con valores reales (grep verificado) | `repro-B5-request-hash.txt` (mutante: `return "0" * 64` en `request_sha256`, correr `uv run --frozen python -m pytest tests/test_jev_juicios.py -q`, revertir) | REPRODUCIDO (26 passed con hash constante) |
| 8 | G6 | `test_migracion_trae_fks_y_roles` no ve GRANT multi-tabla: `GRANT SELECT ON listing, decision TO app_jev;` (cede `decision`, prohibida por contrato) pasa la guarda de minimo privilegio | `repro-B6-grants.txt` (mutante: anadir esa linea a `migrations/0049_jev_ads.sql`, correr `uv run --frozen python -m pytest tests/test_jev_catalogo.py::test_migracion_trae_fks_y_roles -q`, revertir) | REPRODUCIDO (1 passed con el GRANT hostil dentro) |

Los 1-8 son de las clases que el repo trata como bloqueantes: contrato del asesor
violable por otro payload (1), dato de auditoria mezclado (1), comportamiento pedido
roto (2, el CLI no corre), y pruebas que no discrimina (3, 4, 5, 6, 7, 8; 3 y 8
ademas son las guardas de los contratos "no se llama desde cycle.py" y "sin
permisos sobre decisiones").

## No bloqueantes: van al PR, no reabren

Los NO BLOQUEANTE y VERIFICAR de los 6 reportes quedan anotados tal cual en los
reportes de cada grupo (seccion "Respuesta de claude"), para incluir en la
descripcion del PR: G2 trae 20 (sin bloqueantes), G1 trae 17 adicionales, G3 trae
16, G4 trae 14, G5 trae 15, G6 trae 18. Ninguno se corrigio en esta ronda (ronda de
revision: documentar y esperar la correctiva).

## Estado del arbol

- Un solo escritor (esta sesion). Cero cambios en `app/*`, `tools/*`, `templates/*`,
  `migrations/*`, `tests/*`: cada mutante se revirtio con `git checkout --` dentro
  del mismo paso y `git status` quedo limpio tras cada repro.
- Cero llamadas a TypeSafe/Jev real: solo `pedir` falso (`ResultadoPar`) y base
  temporal `orbit_jev01_b5r1_*` creada y borrada por el script. Cero secretos.
- Cero push: commit local de esta evidencia; el push va despues del VEREDICTO.
