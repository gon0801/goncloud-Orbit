# E/B6b — cierre de ledger (cc:DONE + seguimientos R1-R20 + triage)

Fecha UTC: ACK 2026-10-04T17:39:45Z. Rama `docs/jev-ads-cierre-ledger`, base
origin/master `8d8cdca9bdec7b31e2ca95d275c4d682b9349044` (B6a #395). Un solo
escritor; sin push (lo hace Claw tras el VEREDICTO: APROBADO). Solo docs:
ningún cambio en `app/`, `tools/`, `tests/`, `migrations/` ni `.github/`.

## Alcance

- `plans/jev-ads-01.md`: cc:DONE en 0.1, 0.2, 1.1, 1.2, 1.3, 1.4, 2.1, 2.2 y
  2.R con PR y SHA squash; sección "Seguimientos de la revisión" (R1-R20);
  texto de migración corregido (residual de B0-r1/B1-r1) y encabezado Estado.
- `docs/evidencia/jev-ads-01/ejecucion/B2-r1/NOTAS.md`: R19 (cifras).
- `docs/evidencia/jev-ads-01/ejecucion/B5-r1/reporte-g4-pantallas.txt`:
  R20 (rutas locales anonimizadas).
- `docs/evidencia/jev-ads-01/ejecucion/B6b/`: este NOTAS,
  `triage-no-bloqueantes.md` y `filas-para-pr.md`.

## Verificación de evidencia por fila (TAREA 1)

Cada fila marcada cc:DONE tiene su directorio de evidencia con NOTAS y
artefactos (rojo/verde, batería, comandos). Verificado con `ls` antes de
marcar; el bloque → filas quedó:

| Bloque (PR, squash) | Filas | Evidencia | Veredicto final |
| --- | --- | --- | --- |
| B0 #389 fa2039ee | creó plan y diseño | `B0-r1/` | APROBADO |
| B1 #390 afc1f3b | 0.1, 0.2 | `0.1/`, `0.2/` | APROBADO |
| B2 #391 224449f | 1.1, 1.2 | `B2-r1..r4/` | APROBADO (r4) |
| B3 #392 1136d78 | 1.3, 1.4 | `B3-r1..r3/` | APROBADO (r3) |
| B4 #393 a470866 | 2.1, 2.2 | `B4-r1..r3/` | APROBADO (r3) |
| B5 #394 58d1ea4 | 2.R | `B5-r1..r4/` | APROBADO (r4) |
| B6a #395 8d8cdca | concurrencia CI (fuera de tabla) | `B6a/` | APROBADO |

SHAs squash confirmados contra `git log` (40 caracteres):

- B0 `fa2039ee94ddc0742f1f3895f267fccbc59c8aa9`
- B1 `afc1f3b68715c5bf05bdf939eefc9a6683adc0cd`
- B2 `224449fd97b479292a1ae38c585bdac713b652a3`
- B3 `1136d7836dfadfe5c1679f008f4ff5c7ffa2bc01`
- B4 `a47086651ceae2e055d842fc41bc07262b93e46e`
- B5 `58d1ea43c6bcd5524a78226764edbdb0f4c790b4`
- B6a `8d8cdca9bdec7b31e2ca95d275c4d682b9349044`

2.3, 3.1 y 3.2 quedan `cc:TODO` (intactas).

## Residuales R1-R20 (TAREA 2)

Sección "Seguimientos de la revisión" en `plans/jev-ads-01.md` con las 20 filas
(id, contenido, DoD, Depends, Status). R1-R4 con Depends 2.3 (R2, requisito de
3.1; R14, Depends 2.3 por B4-r1). R19 y R20 arregladas en este mismo cambio
(`cc:DONE B6b`); R1-R18 quedan `cc:TODO`.

- R19: `B2-r1/NOTAS.md` decía 15 y 17 pruebas; los artefactos dicen 14
  (`jev_b2_r1_verde_11.txt`: "14 passed") y 32 combinadas
  (`jev_b2_r1_verde_12.txt`: "32 passed" = 14 + 18 propias). Corregido.
- R20: `/Users/dn` (2 ocurrencias) y el slug `-Users-dn-dev-goncloud-Orbit`
  sustituidos por `~` y `-usuario-...`. `grep Users/dn` queda vacío.

## Triage (TAREA 3)

`triage-no-bloqueantes.md`: censo completo de los reportes G1-G6 de B5-r1 =
**100 hallazgos no bloqueantes** (17 G1 + 20 G2 + 16 G3 + 14 G4 + 15 G5 +
18 G6), todos con destino: 25 a filas R#, 20 resueltos (SHA o inspección con
archivo:línea) y 55 descartados con razón. El VEREDICTO-B5-r1 citó "83"; el
censo completo de los mismos reportes da 100 y va triageado entero.

Verificaciones por inspección destacadas (HEAD `8d8cdca`):

- `with connect(dsn)` confirma al salir en ambos CLI (`tools/jev_ads.py:113`,
  `tools/jev_fichas.py:175`): resuelve G1-5.
- `follow_redirects=False` en `app/jev_juicios.py:178` (G5-5).
- `ad_entity_id BIGINT PRIMARY KEY` (`migrations/0001_initial.sql:645`): G2-18.
- `GRANT SELECT ON ALL TABLES ... TO app_admin` (`0001:1460`): G2-17.
- `autoescape` activo (`app/ui.py:59`): G4-10.
- `Indeterminado` sin `miembros_totales` (`app/jev_ads.py:181`): G3-9.
- `hash_ficha` normaliza UTC (`app/jev_catalogo.py:52,76,79`): G3-17.
- La fecha fija 2026-11-02 solo vive en registrar
  (`tests/test_jev_catalogo.py:925,964`), sin comparación contra el reloj: G6-8.
- `/export-semillas` y `/asesoria/{huella}` sin token y el router sin
  dependencies globales (`app/api_fabrica.py:60,306,314`): confirma G4-15 → R2.

## Precondiciones verificadas

- `git fetch origin master`: HEAD = origin/master = `8d8cdca…`, árbol limpio.
- CI de master sobre el squash de B6a: run 37219953951 `completa=success`,
  `gate=success` (el run citado en el encargo, 37195080800, fue re-colocado por
  el re-run manual 37195410638, también `success`).

## Comandos

```
git fetch origin master && git status --short --branch && git rev-parse HEAD origin/master
gh run view 37219953951 --json jobs   # completa: success
for d in 0.1 0.2 B0-r1 B2-r1..r4 B3-r1..r3 B4-r1..r3 B5-r1..r4 B6a; do ls docs/evidencia/jev-ads-01/ejecucion/$d; done
grep -c "NO BLOQUEANTE\|VERIFICAR" docs/evidencia/jev-ads-01/ejecucion/B5-r1/reporte-g*.txt
tail -1 docs/evidencia/jev-ads-01/ejecucion/B2-r1/jev_b2_r1_verde_11.txt   # 14 passed
tail -1 docs/evidencia/jev-ads-01/ejecucion/B2-r1/jev_b2_r1_verde_12.txt   # 32 passed
pre-commit run --files <archivos tocados>
git commit ...    # sin --no-verify, sin push
```

## Límites

- Cero TypeSafe real, cero claves, sin secretos. Solo documentación.
- La rama queda local; el push y la PR los hace Claw tras el veredicto.
