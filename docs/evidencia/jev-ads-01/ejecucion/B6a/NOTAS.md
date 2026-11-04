# B6a-r1: concurrencia del CI (schedule no cancela la bateria de un push)

Fecha UTC: 2026-10-04 (ACK 2026-10-04T16:42:08Z). Base: origin/master
`58d1ea43c6bcd5524a78226764edbdb0f4c790b4` (B5 #394, vigente por fetch).
Rama: `ci/quality-concurrencia-por-evento`. Arbol limpio al empezar.

## Defecto

En `.github/workflows/quality.yml`, el grupo de concurrencia
`quality-${{ github.event.pull_request.number || github.ref }}` con
`cancel-in-progress: true` NO distingue el evento: el `schedule` nocturno y
un push a master calculan el mismo grupo (`quality-refs/heads/master`) y se
cancelan entre si. Medido en el run 37195080800 (squash de B5): la corrida
schedule dejo `completa=cancelled` y `gate=failure` en el push.

## Arreglo

El grupo antepon `github.event_name`:

    quality-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}

Propiedades que se conservan o introducen:

- Dos pushes de la misma rama o PR siguen cancelandose entre si (mismo
  evento y mismo ref): solo la ultima corrida.
- `schedule` y `push` ya no comparten grupo: la pesada nocturna no mata la
  bateria completa de un push, y viceversa.
- `cancel-in-progress: true` se mantiene.
- El gate sigue fail-closed: solo acepta `success` o `skipped`; un job
  `cancelled` deja `rc=1` y `exit 1` (sin verde falso por cancelacion).

## TDD (prueba: test_concurrencia_de_ci_distingue_evento_y_gate_sigue_fail_closed)

| Paso | Resultado | Archivo |
|---|---|---|
| ROJO antes | `1 failed` (assert `github.event_name in grupo`) contra el workflow base | `rojo-antes.txt` |
| VERDE despues | `1 passed` con el arreglo | `verde-despues.txt` |
| Mutante (quitar `github.event_name` del grupo) | `1 failed`, prueba discriminante | `mutante-rojo.txt` |
| Bateria focalizada del modulo | `9 passed` + Ruff check/format limpios | `bateria-focalizada.txt` |
| pre-commit run --all-files | 9 hooks Passed | `precommit.txt` |

La prueba afirma las tres condiciones del encargo: (1) el grupo depende de
`github.event_name`; (2) `cancel-in-progress: true` se mantiene; (3) el gate
sigue fail-closed ante cancelaciones (`if: always()`, case que solo acepta
`*=success|*=skipped`, y `exit 1` en el veredicto). El mutante del encargo
(quitar `github.event_name` del grupo) deja la prueba roja.

## Alcance

Solo se tocaron los archivos permitidos: `.github/workflows/quality.yml`
(comentario del bloque concurrency + la linea `group`) y
`tests/test_precommit_hooks.py` (una funcion de prueba nueva, +35 lineas).
app/*, tools/*, migrations/* y plans/* INTACTOS. Sin cambios de producto ni
migracion. Cero llamadas a TypeSafe/Jev real; cero claves; sin secretos.
Commit local SIN push (el push lo hace Claw tras VEREDICTO: APROBADO).

## Limites declarados

- La corroboracion del comportamiento real de cancelacion de GitHub Actions
  no se puede correr local; la prueba pinea la ESTRUCTURA del workflow y el
  incidente real (run 37195080800) es la medida de fondo. El verdadero
  contra el schedule nocturno lo dara la primera corrida cruce en CI.
- La asercion del gate es estructural (parseo del YAML), igual que el resto
  del modulo; no ejecuta el shell del gate.
