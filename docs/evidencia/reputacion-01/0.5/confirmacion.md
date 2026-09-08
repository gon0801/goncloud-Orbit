# REPUTACION 01 / 0.5 — Acta APROBADA

Fecha propuesta: 2026-09-08. Estado: **APROBADA por el dueno
2026-09-08** (sin ajustes: los 3 puntos [CONFIRMAR] van como
propuesta). Cierra D1–D7, fija granularidad/ventanas/latencia y
libera A.1 (y en orden A.2→A.4→A.5→A.6). A.3 sigue bloqueada.

Evidencia base: `E/0.1` (inventario), `E/0.2` (Apify: junglee
verificado semanal, texto no_verificado), `E/0.3` (7 endpoints
MeLi), `E/0.4` (Keepa sin plan, Sellers OK, Account Health no).

## 1. D1–D7 ratificadas (con ajustes por evidencia)

| ID | Cierre propuesto |
|---|---|
| D1 cadencia | MeLi **diaria 09:30 UTC** + CLI bajo demanda. Amazon **semanal lunes 10:00 UTC** + CLI (decision dueno 2026-09-08; cabe en D6). |
| D2 alcance | Aprobada + matiz padre/hijo: snapshot por ASIN pedido, rating del PADRE declarado, columna `parent_asin`. ASIN sueltos fuera. |
| D3 umbrales | Aprobada + granularidad §2. Ventanas: MeLi 7d sobre dato diario; Amazon N vs N-1 semanal. Flanco en todas. 1★: **MeLi si** (texto oficial A.4), Amazon no (A.3 bloqueada). |
| D4 canal | Digest existente de los ciclos 08:40/41, sin canal nuevo. |
| D5 preguntas | Solo lectura + conteo; pendiente = UNANSWERED + **48h** sin respuesta [CONFIRMAR ventana]. |
| D6 tope $10 | Cumplida: ~$7.8/mes est. Subir plan Apify **autorizado** (FREE corta en $5). Reabrir solo si la primera corrida real excede $10. |
| D7 texto UI | Completo con truncado + reviewer visible. |

## 2. Granularidad por alerta (que fuente dispara cada tipo)

| Tipo | Fuente | Condicion v1 |
|---|---|---|
| `rating_bajo` | MeLi avg item / junglee stars | < 4.2 en ultimo snapshot, en flanco |
| `resena_1` | MeLi reviews (rate==1, published) | nueva desde ultima evaluacion, en flanco |
| `reclamos_suben` | MeLi disputes (player+stage) | nuevas 7d >= previas 7d + 2 [CONFIRMAR: el plan dice ">1pt" pero sin denominador; propongo conteo +2] |
| `caida_rating` | MeLi 7d / Amazon N vs N-1 | baja >= 0.3, en flanco |
| `salud_cuenta` | MeLi seller_reputation | `level_id` a peor (mapa explicito; desconocido = info visible) |

Amazon excluido de `resena_1` y `reclamos_suben` en v1. Severidades:
`resena_1` critica, resto aviso (info para level no mapeado).

## 3. Latencia

Digest D+1 (la ingesta 09:30/10:00 corre tras los ciclos 08:40/41,
entra al digest siguiente) + CLI same-day bajo demanda (AC4).

## 4. Propiedad y concurrencia (ratifica plan)

Orden: A.1 → A.2 → A.4 → A.5 → A.6, un editor por archivo;
`app/reputacion.py` unico para A.2/A.4 (+ snapshots Amazon A.2);
`app/cli.py` subcomando; A.5 puro + lineas aditivas al digest (no
cambia formato existente); A.6 `api_reputacion.py` + ruta + template.
A.3 bloqueada (sin actor de texto).

## 5. Contrato migracion (A.1)

Numero **0024** (HEAD master en 0023, verificado 2026-09-08).
Expansiva, no re-runnable. 4 tablas append-only + alertas:

- `reputation_snapshot` (listing|cuenta, metric_date, observed_at,
  rating, review_count, `parent_asin`, fetched_at, extra) + trigger
  append-only.
- `review_event` (MeLi texto: rate, titulo, contenido, fecha,
  published) + dedup `(platform, review_external_id)`.
- `seller_reputation_snapshot` (MeLi cuenta diaria: level,
  power_seller, tx, disputes) — tabla propia, no `extra`.
- `meli_question` (append-only con estado ANSWERED/UNANSWERED).
- `reputation_alert` (mutable solo sellado resolved/resolved_at).

FKs a `listing`, GRANTs por rol, invariantes con test. Insumo:
`E/A.1` borrador pre-0.5 (ajustar: preguntas y seller como tablas
propias, `parent_asin`, tipos de §2).

## 6. Contrato API/pantalla (A.6)

`GET /reputacion`: por listing (rating, count, tendencia, estado de
fuente incl. Sin-verificar Amazon-texto), cuenta MeLi, preguntas
pendientes, alertas abiertas. Sidebar: Reputacion pasa a enlace;
**chip Reviews se retira** (pestana dentro de /reputacion)
[CONFIRMAR: el plan lo deja objetable]. CSP sin inline, textContent
para texto externo, imagenes fuera v1.

## 7. Contrato cron (A.7, lead)

Crontab gon, job_keys nuevos (huecos verificados 2026-09-08, no
pisan accounting ni bloque Orbit):

- `30 9 * * *` `reputacion:meli` (diaria; log propio).
- `0 10 * * 1` `reputacion:amazon` (lunes; log propio).
- `30 10 * * *` `reputacion:alertas` (evalua con lo ultimo; log propio).

Ingesta manual por CLI antes de encender cada cron (secuencia §8).

## 8. Secuencia de despliegue y reversa (A.7, lead)

1. Backup verificable; migracion 0024.
2. Ingesta manual CLI (MeLi, luego Amazon un lunes) + alertas; smoke
   GET /reputacion.
3. Cron diario tras manual sana; semanal Amazon tras su manual.
4. Reversa: deshabilitar los 3 crons, conservar datos y pantalla
   (v1 solo-lectura: nada que deshacer). Documentar en DEPLOY.md.

## 9. Manifest

Al aprobarse esta acta, el lead commitea la entrada `reputacion-01`
(snippet del plan) y la marca activa.

## 10. Aprobacion

- [x] Dueno: aprueba acta sin ajustes. Fecha: 2026-09-08.
- [x] A.1 liberada; briefs A.2/A.4/A.5/A.6 sobre este contrato
      (los briefs pre-0.5 quedaron descartados).

Ajustes del dueno: ninguno.
