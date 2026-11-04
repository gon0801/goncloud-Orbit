# H2 — medicion B.2a (2026-09-25 UTC, solo lectura)

CAND = `cd9eaa7` (`origin/master` tras #336/#337).

## Testigos md5 (prod vs master)

| archivo | prod | `cd9eaa7` | coincide |
| --- | --- | --- | --- |
| app/cycle.py | fbb2c83a | 88f3abb2 | NO |
| app/optimizer/goals.py | b3698b60 | 44d74a78 | NO |
| app/ads/reports.py | fd2ebe23 | fd2ebe23 | SI |

Prod no es master. Discriminacion B.2 (B.2 = PR #332, `beae9ec`):

| archivo | prod | `beae9ec` | `f98b30a` |
| --- | --- | --- | --- |
| app/cycle.py | fbb2c83a | 88f3abb2 | fbb2c83a |
| app/optimizer/goals.py | b3698b60 | 44d74a78 | b3698b60 |

**Conclusion: B.2 NO esta en prod.** Los 2 archivos que B.2 toco coinciden
con `f98b30a` (pre-B.2) y difieren de `beae9ec`. SHA productivo exacto queda
`unknown` (solo se compararon 3 testigos); compatible con `f98b30a`.
`app.bak` mas reciente: `app.bak-predeploy-20260916` (+ `.diff`).
`app/ads/reports.py` igual en prod y master: el poll de 25 min
(`fix/ads-report-timeout`) esta fuera en ambos.

## Goals y modes efectivos

| id | scope | platform | ad_entity_id | mode | enabled |
| --- | --- | --- | --- | --- | --- |
| 4 | platform | amazon_mx | NULL | live | t |
| 5 | platform | amazon_us | NULL | live | t |
| 6 | campaign | NULL | 3909 | live | t |
| 7 | campaign | NULL | 3926 | live | t |
| 8 | campaign | NULL | 415287 | live | t |
| 9 | campaign | NULL | 415285 | live | t |
| 10 | campaign | NULL | 415286 | live | t |
| 11 | campaign | NULL | 415284 | live | t |
| 12 | campaign | NULL | 415288 | live | t |

9 goals, todos en `live` (8-12 = grupo 1, FABRICA D.3 en curso).

## Consecuencia para B.2a

Nada que contener en prod hoy; la contencion se exige antes del primer
deploy con SHA >= `beae9ec`. Decision literal 25-sep-2026: "Flag off
(Recomendado)" — contencion (a): PR nuevo con flag apagado por defecto +
test de aislamiento; se enciende con go en H5.
