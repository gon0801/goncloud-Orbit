# Cross-review Grok — PR 177 (FABRICA 01 tareas 7–10)

HEAD: `faa943f137a84337c92377698a42b4cb6eec3c62` (`origin/fabrica-01-7-10-ejecucion`)
Base: `origin/master` (`41bfabbf` = #176)
Postura: escéptica. No se re-ejecutó la suite (el verifier ya la corrió).
Gaps adversary 1–4 se verificaron en el código actual; este informe busca huecos **nuevos**.

## Conteos

| Cubo | N |
|---|---|
| Block | 0 |
| Should-fix | 4 |
| Nit | 3 |
| Dismissed | 8 |

## Should-fix

1. **tools/fabrica_campanas.py:767-770 y :965-982.** Readback que no cuadra sella el paso `failed` **con** `external_id` (el ledger y `--desarmar` lo ven), pero **no** lo agrega a `conocidos` y el `Abortar` no nombra el id. El detalle del lote queda `creadas: [] | conocidos: []` si falla el primer paso. El JSON `paso` sí trae el external. Un operador que solo lea el resumen puede creer que no nació nada y volver a autorizar → otro grupo ENABLED. No reintentar POST ni adoptar por nombre. Meter el id en `conocidos` **antes** del raise y copiarlo al motivo/detalle.

2. **tools/fabrica_campanas.py:867-878.** El predicado “goal listo” (cierre del gap 4) exige `enabled`, `mode`, harvest ids y `h_bid is not None`. No compara `harvest_default_bid` con el bid-exact del plan, ni `target_acos_pct` / `bid_currency`. Un goal enabled con terna apuntando a la exact pero ACoS/moneda/bid distintos se salta y el lote se sella `applied`. En el reintento del mismo lote el riesgo es bajo (crea_goal ya escribió los valores del plan). Completar la terna o documentar el residual.

3. **tools/fabrica_campanas.py:1142-1151.** LIST 200 vacío, LIST no-200 y excepción de LIST caen todos en `sin_verificar`. El brief pide ausente ≠ sin verificar. Ambos abortan (no hay éxito silencioso). Distinguir “el LIST respondió y no está” (`ausentes`) de “no pude preguntar”.

4. **tools/fabrica_campanas.py:1010-1032 y :1028-1032.** El PUT de `--desarmar` no atrapa timeout/excepción. Un corte de red sale por traza (exit 1), no por `Abortar` INCERTO. No sella `desarmado` (fail-closed). Re-correr pausa de nuevo. Alinear el envoltorio con `_post` para no llamar “rechazado” a un corte, y no dejar al operador sin lote en el mensaje.

Residual ya declarado (no es hallazgo nuevo; no se cierra en este PR): `_lote_nuevo` siempre abre lote; la misma huella no bloquea un segundo `--acepto-mutacion-real`. ASK de producto.

## Nit

5. **tools/fabrica_campanas.py:645-678.** `_readback_cuadra` no mira `expressionType`, `startDate` ni `dynamicBidding`. El brief pide identidad, estado, expresión, bid y budget — eso sí está (padres + `budgetType` post-gap-2). Fuera de contrato explícito; HIPÓTESIS hasta la sonda.

6. **tests/test_fabrica_campanas.py:760-786 y :861-923.** El camino feliz de mutación stubbea `_registrar` (D-GLM-7-10-5). No hay mutación US con perfil 102; MXN/USD se cubre en dry-run CLI. `_ClienteLectura.list_objects` ignora `profile_id` (el Scope del LIST lo cubre `test_readback_contra_ads_client_real`).

7. **tools/fabrica_campanas.py:271-282.** `_SQL_PENDIENTES` no excluye lote `desarmado`. Un paso `failed` de un lote ya pausado, si alguien lo re-ENABLED a mano y cuadra, se promovería. `--registrar` sigue rechazando desarmado. Borde.

## Dismissed

- **Gaps 1–4 del adversary (cerrados en `8b9066b`).** Timeout/ack sin id → `INCERTO`, no `rechazado`. Readback exige `campaignId`/`adGroupId`/`budgetType`. `--reconciliar --lote` + `--plataforma` cruzados aborta. Goal apagado aborta y no sella `applied`.
- **#176 intacto.** Dry-run sin HTTP ni DSN de escritura; `conn_read.close` en éxito y abortos de fracción/bid; ventanas UTC `[D-105,D-15)` / exact `[D-39,D-9)`; NULL vigente no fabrica ACoS; candado `app.ads.write` + allowlist + `__main__` último.
- **Adoptar por nombre / re-POST automático.** Prohibido a propósito. Correcto.
- **Host Ads distinto MX/US.** Ambos NA; mismo `DEFAULT_BASE_URL` que `archiva_inertes`.
- **207 con cuerpo lista.** `errores_207` lo trata como error (fail-closed). Shape anidado es HIPÓTESIS de sonda.
- **Dos perfiles aceptados por plataforma.** `_perfiles` se queda con el último. Supuesto ya declarado en `app/ads/structure.py`.
- **Reconciliar pendientes=0 → exit 0.** No es ausente/divergente/sin verificar; hay log. Lote inexistente aborta.
- **CHAT-CONTEXT / DATABASE.** No afirman creaciones reales; HIPÓTESIS hasta sonda.

## Veredicto

**Ship con residuales.** Cero Block nuevo. Los four candados de honestidad del review previo siguen en pie. #176 no se rompió. No mergear la **sonda** (tarea 11) hasta que el lead documente el ASK de huella y, si se quiere, el Should-fix 1 (declarar el id cuando el readback no cuadra).

Este PR puede entrar a `master`. No autoriza crear campañas reales.

## Lo que ves

La herramienta ya ensaya, crea con tu literal, anota, pausa y reconcilia en pruebas. Los cuatro candados que pedía el review anterior están: no llama “rechazado” a un corte de red, no traga un presupuesto de otro tipo, no calla un filtro contradictorio y no marca “listo” un grupo con metas apagadas. Queda un hueco de honestidad: si Amazon creó la campaña pero la verificación no cuadra, el resumen puede parecer vacío aunque la campaña exista y se pueda pausar. Hasta la sonda, no uses la creación real.
