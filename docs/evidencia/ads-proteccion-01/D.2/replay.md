# D.2 — replay de septiembre: inversiones que frena la regla (N = 10 vs N = 7)

Medición 2026-09-27 contra producción, SOLO SELECT con el rol `orbit_read`
(mismo patrón de lectura en contenedor que el replay económico de
[`ads/2026-09-24-proteccion-economica-replay.md`](../../ads/2026-09-24-proteccion-economica-replay.md);
no imprime DSN). Decisión del dueño: literal "N = 10"
([`decision.md`](decision.md), 2026-09-26).

**Qué mide.** Para cada decisión de septiembre con `kind='bid'`
(`decided_at` en `[2026-09-01, 2026-10-01)`; 518 live + 247 shadow), el
último bid APLICADO anterior (`verify_ok IS TRUE`, ciclo ejecutor live,
`confirmed_at < decided_at`), la dirección de cada cambio (`new_value`
contra `old_value`) y los días de evidencia posterior al cambio:
`decision.window_end − fecha UTC del confirmed_at`. Son los mismos insumos
del gate D.2 (`permite_reversa_bid` con
`fin_ventana_bids = ventanas.bids.window_end`); el corte temporal
`confirmed_at < decided_at` es implícito en vivo y aquí se hace explícito.
**No hay estimación de ahorro: solo conteos.**

## Resultado

| Clase (decisión de bid de septiembre) | Decisiones |
| --- | --- |
| Total | 765 |
| Sin bid aplicado previo → D.2 no aplica, se emite | 571 |
| Misma dirección que el último aplicado → se emite | 184 |
| Reversa con <7 días de evidencia → la frenan N=10 y N=7 | 6 |
| Reversa con exactamente 7 días → la frena N=10, la permite N=7 | 1 |
| Reversa con ≥10 días → la permite N=10 | 3 |

- Las 10 reversas tienen estos días de evidencia: 3 (×1), 4 (×4), 5 (×1),
  7 (×1), 18, 20 y 21 (×1 cada una).
- **N = 10 frena 7 de 10** inversiones de septiembre.
- **N = 7 frena 6 de 10**: el borde exacto de 7 días pasa por el comparador
  `>=` de la política (`(fin_ventana_bids − fecha_cambio).days >= N`).
- Ningún caso de historia rota ni de ventana desconocida apareció en
  septiembre (las clases fail-closed del gate quedan cubiertas solo por los
  tests).

## Caso 3835 (el que motivó D.2)

| Decisión | Instante (UTC) | Cambio | Último bid aplicado | Evidencia | Veredicto |
| --- | --- | --- | --- | --- | --- |
| 2319 | 2026-09-13 08:41 | 14.64 → 16.836 (subida) | ninguno | — | D.2 no aplica; se emite |
| 2409 | 2026-09-21 08:41 | 16.84 → 12.63 (bajada) | subida del 2026-09-13 | 3 días (ventana hasta 2026-09-16) | **frenada por N=10 y por N=7** |

La bajada del 21-sep juzgó el bid del 13-sep con solo 3 días de métrica
posterior al cambio: es la inversión que D.2 existe para frenar.

## Reproducción

El script de abajo se corrigió en este PR (R-D2-4): la clase
`reversa_frenada_n10` se pisaba con `reversa_frenada_n7` antes del
contador, así que `resultado` informaba 1 en vez de 7; ahora N=10 y N=7 se
cuentan por separado (dos contadores, sin pisar la clase). La tabla de
Resultado ya informaba los números correctos; es evidencia y no se
re-corrió contra producción.

`ssh goncloud 'docker exec -i orbit-app-1 python -' < replay_d2.py`
(archivo local; el contenedor aporta `ORBIT_DSN_READ`; reemplaza
`127.0.0.1` por `ORBIT_PG_HOST` dentro del contenedor):

```python
import datetime as dt, json, os
from collections import Counter
import psycopg

DSN = os.environ["ORBIT_DSN_READ"].replace("127.0.0.1", os.environ.get("ORBIT_PG_HOST", "db"))
SQL = """
SELECT d.id, d.ad_entity_id, d.decided_at, d.old_value, d.new_value, d.window_end,
       c.mode::text,
       last.old_value, last.new_value, last.confirmed_at
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  LEFT JOIN LATERAL (
        SELECT d2.old_value, d2.new_value, da.confirmed_at
          FROM decision_application da
          JOIN decision d2 ON d2.id = da.decision_id
          JOIN optimizer_cycle oc ON oc.id = da.applied_cycle_id AND oc.mode = 'live'
         WHERE d2.ad_entity_id = d.ad_entity_id
           AND d2.kind = 'bid'
           AND da.verify_ok IS TRUE
           AND da.confirmed_at < d.decided_at
         ORDER BY da.confirmed_at DESC
         LIMIT 1
       ) last ON TRUE
 WHERE d.kind = 'bid'
   AND d.decided_at >= %s AND d.decided_at < %s
 ORDER BY d.ad_entity_id, d.decided_at
"""
with psycopg.connect(DSN) as conn:
    filas = conn.execute(SQL, (dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
                               dt.datetime(2026, 10, 1, tzinfo=dt.UTC))).fetchall()

def signo(viejo, nuevo):
    if viejo is None or nuevo is None or nuevo == viejo:
        return None
    return 1 if nuevo > viejo else -1

por_modo = Counter()
resultado = Counter()
dias_reversa = Counter()
reversas = []
caso_3835 = []
for did, ent, decidido, d_viejo, d_nuevo, wend, modo, h_viejo, h_nuevo, conf in filas:
    por_modo[modo] += 1
    ds = signo(d_viejo, d_nuevo)
    hs = signo(h_viejo, h_nuevo)
    if hs is None and conf is not None:
        clase = "historia_rota_bloqueada"
        dias = None
    elif hs is None:
        clase, dias = "sin_historia_permite", None
    elif ds == hs:
        clase, dias = "misma_direccion_permite", None
    else:
        dias = (wend - conf.astimezone(dt.UTC).date()).days if wend is not None else None
        clase = ("reversa_frenada_n10" if dias is not None and dias < 10 else
                 "reversa_sin_ventana_bloqueada" if dias is None else "reversa_permitida_n10")
        reversas.append({"decision": did, "entidad": ent, "modo": modo,
                         "decidido": decidido.isoformat(), "dias": dias})
        if dias is not None and dias < 7:
            resultado["reversa_frenada_n7"] += 1
    resultado[clase] += 1
    if dias is not None:
        dias_reversa[dias] += 1
    if ent == 3835:
        caso_3835.append({"decision": did, "modo": modo, "decidido": decidido.isoformat(),
                          "de": str(d_viejo), "a": str(d_nuevo), "ventana_hasta": str(wend),
                          "ultimo_bid_dir": hs,
                          "ultimo_bid_fecha": conf.astimezone(dt.UTC).date().isoformat() if conf else None,
                          "clase": clase, "dias": dias})
print(json.dumps({"decisiones_bid_septiembre": len(filas), "por_modo": dict(por_modo),
                  "clases": dict(resultado), "dias_de_reversas": dict(sorted(dias_reversa.items())),
                  "caso_3835": caso_3835}, default=str, ensure_ascii=True, indent=1))
```
