# REPUTACION 01 / A.5 — Alertas + digest (implementado por el lead)

Fecha: 2026-09-08.

## Decisiones

- D-A5-1: digest = alertas abiertas creadas en ultimas 24h (novedades
  del dia, criticas primero), tope 10 lineas + resto contado. Sin
  novedades = cero lineas (regla 3). Las abiertas viejas siguen en DB
  y las muestra A.6; el digest no repite estado.
- D-A5-2: `reclamos_suben` = nuevas7d (total hoy − total −7d) >=
  previas7d + 2; requiere 3 snapshots seller con totales presentes.
  Supuesto declarado: `disputas_total` es comparable entre corridas
  (acta §2 lo dejo [CONFIRMAR] con conteo +2; esto lo implementa).
- D-A5-3: `resena_1` dedupe por mensaje canonico con review id (contra
  abiertas Y resueltas); 2a review 1 estrella del mismo item SI abre.
  Mensajes sin texto externo (solo ids/numeros): seguros para
  Telegram sin parse_mode (titulo/texto vive en DB para A.6).
- D-A5-4: alertas de cuenta con platform='meli', external_id=NULL.
- D-A5-5: mapa level peor→mejor 1_red<2_orange<3_yellow<
  4_light_green<5_green; level actual no mapeado = alerta info
  visible (no silencio); level None = sin datos = no dispara.
- D-A5-6: `caida_rating` MeLi = ultimo vs mas reciente <= hoy−7d;
  Amazon = semana N vs N−1 (dos metric_date distintas mas recientes,
  aunque haya hueco: documentado, no inventa semanas).
- D-A5-7: flanco completo: abrir al calificar (si no hay abierta
  igual), auto-resolver al dejar de calificar CON datos (sin datos =
  intactas, regla 3). `resena_1` nunca se auto-resuelve (la atiende
  el operador; sin UI de sellado en v1 queda abierta).
- D-A5-8: `reputacion alertas` corre con ORBIT_DSN_DECIDE (unico rol
  con INSERT/UPDATE en reputation_alert, 0024); lectura del digest
  con ORBIT_DSN_READ fail-silent (patron contrib).
- R2-2 atendido: `_lee_reviews_1` con DISTINCT ON por review (fila
  mas reciente por observed_at); A.5 duena del invariante.
- `reputacion.py` intacto (800/900): A.5 vive en `reputacion_alertas.py`
  nuevo (422); `notifica.py` solo aditivo; `cycle.py` no se toca (el
  bloque lo carga `notifica_digest`, patron contrib).
- Cron `30 10 * * * reputacion:alertas` (acta): se registra en A.7
  (DEPLOY.md); el comando existe desde A.5 (AC4 same-day).

## Evidencia

- `tests/test_reputacion_alertas.py`: 34 passed (17 puros: 5 tipos en
  flanco, sin datos, sin texto; 10 flanco DB incl. DISTINCT ON y
  2a-review-mismo-item; 7 digest+CLI).
- TDD por vueltas con RED registrado (3 colecciones fallidas antes de
  cada GREEN) + epsilon float documentado (4.8−4.5 < 0.3).
- `tests/test_reputacion.py` + migracion: 48 passed (sin cambios).
- `tests/test_notifica.py` + `test_cli.py`: 90 passed (digest sin
  bloque = formato intacto, test dedicado).
- ruff check + format limpios; pre-commit + CI: ver PR.
- `rating_bajo`/`caida_rating`/`salud_cuenta`/`reclamos_suben` fixtures
  disparan SOLO en flanco (2a corrida = 0 abiertas); `resena_1` 1 sola
  alerta por review aunque 0027 guarde N filas; digest tope 10 + resto.
