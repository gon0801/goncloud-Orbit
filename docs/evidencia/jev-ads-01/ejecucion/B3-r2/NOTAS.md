# E/B3-r2 — correccion VEREDICTO-B3-r1 (B1/B2 + dos ajustes de una linea)

Fecha UTC: 2026-10-04T02:46:36Z (ACK) a cierre. Rama `docs/jev-ads-b3`,
partiendo limpia de `292ed1ea5a067629eb253561872fd6d391123bf0`. Un solo
escritor; SIN push (lo hace Claw tras VEREDICTO: APROBADO).

## SHA

| Commit | Contenido |
| --- | --- |
| `d431dc7b7c4570b5497a1bb257ab8b7a4f35df43` | B1 contexto congelado en AMBOS sujetos, B2 orden observado desde otra sesion, wire minimo, ARCHIVED no paga |

HEAD final de CODIGO (40): `d431dc7b7c4570b5497a1bb257ab8b7a4f35df43`
(commits posteriores, si los hay, SOLO evidencia documental).

## B1 (bloqueante): mismo id con otro payload, ahora para AMBOS sujetos

- ROJO: la repro del veredicto contra `292ed1e`: `Failed: DID NOT RAISE
  ValueError` (`jev_b3r2_rojo_b1.txt`). Causa: `_abrir_revision` en la
  rama DecisionARevisar solo comparaba sujeto_tipo/decision_id/
  plan_sha256 IS NULL, sin el contexto congelado (termino + censo +
  plataforma).
- ARREGLO: `coherente` incluye `guardado[3] == contexto` en la rama
  decision (la rama semillas ya lo hacia).
- REGRESIONES integradas en `tests/test_jev_cli.py`:
  - `test_decision_misma_solicitud_otro_termino_rechazado` (la repro,
    con decision real sembrada via config_version + optimizer_cycle;
    nombre definitivo; el archivo repro suelto fue BORRADO).
  - `test_semillas_mismo_plan_sha_otro_censo_rechazado`: MISMO
    plan_sha256 con otro censo congelado (otra ficha) → rechazado por
    el CONTEXTO y no por el hash del plan (la prueba que faltaba para
    la rama semillas).
- MUTANTES registrados (aplicar → restaurar, verificado 1 failed cada
  uno): (a) quitar `guardado[3] == contexto` en la rama decision →
  `test_decision_misma_solicitud_otro_termino_rechazado` 1 failed;
  (b) quitar `and guardado[2] == contexto` en la rama semillas →
  `test_semillas_mismo_plan_sha_otro_censo_rechazado` 1 failed (el
  mutante del veredicto que sobrevivia con 38 passed).

## B2 (bloqueante): el orden sellado se observa desde OTRA sesion

- PROBLEMA: el test de orden consultaba desde la MISMA conexion
  (autocommit) y veia filas sin confirmar; el mutante que quita el
  `self._conn.commit()` previo al HTTP sobrevivia.
- ARREGLO DE LA PRUEBA (el codigo ya hacia el commit; lo que faltaba era
  la prueba): `test_revision_e_intencion_confirmadas_antes_del_http`
  corre el asesor sobre la conexion SIN autocommit y el pedir espia
  consulta con una SEGUNDA conexion (otra sesion, solo ve confirmado):
  revision + 1 intencion visibles y 0 resultados EN EL MOMENTO DEL HTTP.
- `test_reanudacion_tras_crash_la_intencion_huerfana_esta_confirmada`:
  mismo observador; al crash de t2, 2 intenciones confirmadas (t1 y t2)
  y solo 1 resultado (t1); luego la reanudacion reutiliza t1 (cero HTTP).
- MUTANTE registrado (el reproductor del veredicto, aplicado y
  restaurado con edit): quitar el commit previo al HTTP → AMBOS tests
  2 failed (diagnostico en vivo: con el mutante el asesor queda INTRANS
  y el observador ve 0 intenciones).
- Nota de proceso honesta: el `git checkout app/jev_ads.py` del
  reproductor del veredicto descarto DOS VECES fixes sin commitear de
  esta misma ronda (B1 y ARCHIVED); detectado por rg y re-aplicados con
  edit. Los mutantes se verificaron con reemplazos exactos y restauracion
  por edit, jamas con checkout sobre trabajo pendiente.

## Ajustes de una linea (pedidos por el VEREDICTO)

1. WIRE: `_ficha_a_state` ahora envia SOLO `{hechos, desconocidos}`; los
   `listings`, `observado_at` y `revisar_antes_de` salieron del state
   (la identidad viaja por ficha id en clave y request hash). El test del
   wire exige exactamente `state={termino, ficha:{hechos, desconocidos}}`.
   ROJO primero (5 campos vs 2); MUTANTE registrado: restaurar `listings`
   en el state → 1 failed.
2. LLAMADAS PAGADAS INUTILES: `AsesorAds.evaluar` salta a los miembros
   cuyo estado es conocido no activo (`_solo_no_activo`, p. ej. ARCHIVED)
   ANTES de la intencion: no pagan intencion ni HTTP. ROJO primero
   (2 llamadas y 2 intenciones con el censo del test); prueba
   `test_miembro_solo_archived_no_paga_http`: censo con un ARCHIVED (con
   ficha vigente) y un ENABLED → 1 sola llamada, 1 sola intencion y
   HayCompatible con universo 1. MUTANTE registrado: quitar el skip →
   1 failed.

## Comandos y salida real

```
cp repro -> tests; pytest -q tests/test_repro_b3_decision_otro_payload.py
  -> 1 failed (DID NOT RAISE ValueError)  [rojo B1; repro borrada al integrar]
pytest tests/test_jev_cli.py tests/test_jev_juicios.py -q
  -> 3 failed (B1 decision, ARCHIVED, wire)  [rojo del delta]
mutantes in vivo: 5 aplicados, 5 matados (1 failed cada uno; B2 mato 2)
pytest focalizadas Jev (4 archivos) -> 85 passed
pytest tests/test_cycle.py tests/test_apply_cola.py tests/test_apply_harvest.py
  (corridos en la bateria; sin diferencias)
pytest -q -> 3,832 passed, 1 warning in 257.75s  (bateria una vez, sobre
  el commit de codigo d431dc7)
uv run ruff check . -> All checks passed!
pre-commit run --all-files -> 9 hooks Passed
git commit sin --no-verify
```

Evidencia cruda: `jev_b3r2_rojo_b1.txt`, `jev_b3r2_rojo.txt`,
`jev_b3r2_verde.txt` (85 passed), `jev_b3r2_bateria.txt`.

## Limites respetados

- Archivos: `app/jev_ads.py`, `app/jev_juicios.py`, `tests/test_jev_cli.py`,
  `tests/test_jev_juicios.py` y esta evidencia. cycle.py, apply_cola.py,
  apply_harvest.py, migrations/*, api_dashboard, fabrica_web, api_fabrica
  INTACTOS. Sin migracion nueva.
- Cero llamadas a TypeSafe/Jev real; cero claves; sin secretos.
- Sin push: commit local `d431dc7`; arbol limpio.

## Skills/agentes/modelos

- Sesion principal opencode: `zai-coding-plan/glm-5.3-flash`, un solo
  escritor, dueno del diff.
- Skills activas de la sesion: poteto-mode (playbook Feature, skips
  razonados), unslop, tdd (rojo -> verde + mutantes por punto),
  typesafe-ai + docs vivas (sin novedad: jev-1.13.0 sigue vigente).
- Subagentes: NINGUNO; paneles: NINGUNO (arreglo pautado).
