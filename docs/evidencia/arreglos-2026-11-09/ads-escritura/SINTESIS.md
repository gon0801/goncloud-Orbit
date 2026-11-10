# Síntesis — frente ads-escritura (I-H1, E-H1=F-H2, E-H3, F-H1, I-H2, I-H4)

Candidatos: `opus/DISENO.md` (604 líneas) y `sonnet/DISENO.md` (368). Sin juez cruzado.
Base: **opus**. Quita dos piezas en vez de agregar guardas y halló una causa más honda en I-H2.

## Comprobado por mí contra el código y producción (2026-11-09)

- `ads_listado_plataforma` existe (`migrations/0051_ads_acta_listado.sql:22`) y la escribe el sync
  (`app/ads/structure.py:253`, `SOURCE = "amazon_ads_structure_v2"`).
- Producción: las dos plataformas tienen acta de hoy 06:45:55 UTC. El cambio de E-H1 no detiene nada al desplegar.
- `veredicto_impulso` (`app/impulso.py:168-187`): con impresiones y clics suficientes, sin venta y gasto
  bajo el umbral, devuelve `en_curso` para siempre. La causa ampliada de opus en I-H2 es cierta.
- Producción: 0 impulsos desarmados sin lectura final. Nada que reparar.
- `reconcilia_bids` solo corre en live (`app/cycle.py:3497-3501`).
- `fp.valida_parametros` tiene 4 llamadores dentro de `app/fabrica_plan.py` (536, 664, 810, 905) además de
  los bordes de alta (`app/api_fabrica.py:226`, `tools/fabrica_campanas.py:587,612`).
- `docs/APPLY.md:529` dice «divergencia → reintento».
- Última migración en master: 0082. La siguiente libre es 0083.

## Decisión por hallazgo

| Hallazgo | Base | Injerto | Rechazado |
|---|---|---|---|
| I-H1 cap gigante | opus: lector único `cap_desde_settings` con `CAP_MAXIMO`, usado por el motor y por `proxima_config` | sonnet: no tocar `api_write.py:253`; el 422 ya sale de `SettingsInvalido` y el CLI no pasa por pydantic | opus: `strict=True` en pydantic (cambia qué acepta la API sin ser el hallazgo) |
| E-H1 guarda de 48 h | opus: `_SQL_SYNC_PLATAFORMA` lee la última corrida ok del sync con acta de esa plataforma. Sin migración. Precedente `0016:106-120` | ninguno | sonnet: columna nueva `estructura_sync_at` + permisos por columna. Su objeción («la corrida no dice qué plataforma») la resuelve el acta por plataforma |
| E-H3 bid sin sello | opus: `apply.hojas_con_bid_sin_sello()` con el mismo predicado del reconciliador; bloquea solo movimientos de bid, la pausa pasa | ninguno | sonnet: familia nueva dentro de `claves_bloqueadas`. Salta la hoja entera, pausa incluida, y mezcla el ledger de bids con la cola |
| F-H1 reenvío viejo | opus: `reconcilia_bids` confirma o cierra y **nunca** reenvía; se borra `_reintento_divergente` | ninguno | sonnet: conservar el reenvío con guarda de modo y «sin decisión más nueva». Mantiene dos escritores de bids |
| I-H2 vigía con 409 | opus: veredicto `desarmado` (final, pausar), `pausa_huerfanas`, migración del CHECK, frase nueva | ninguno | sonnet: tratar `desarmado` como `pausado` en la rama pausar (3 líneas). Quita el 409 pero deja el impulso sin cerrar cuando el veredicto es `en_curso` |
| I-H4 sin tope humano | opus: `TECHOS_HUMANOS_POR_MONEDA` con dos cifras por moneda (puja, presupuesto diario); el tope del impulso se valida por su diario derivado; meta manual en la banda 10–45 que ya usa la fábrica | sonnet: el chequeo de la fábrica va en los bordes de alta (`api_fabrica.py:226`, `fabrica_campanas.py:587,612`), **no** dentro de `valida_parametros`, que también corre al releer planes guardados | sonnet: tercera cifra `tope_impulso` y meta manual máxima de 100 % |

## Unidades y orden

1. **U1 I-H1** (ALTO). Sin cambio en vivo. Prueba: `proxima_config(..., caps={"bid": 2**31})` levanta `SettingsInvalido`.
2. **U2 E-H1**. Cambia solo el caso de falla (sync muerto 48 h → plataforma `degraded`). Prueba con base en `tests/test_optimizer_windows.py`.
3. **U3 E-H3 + F-H1** juntos. Cambio en vivo: se quita un camino que escribe a Amazon; la hoja con bid dudoso espera un día. Dos pruebas (una por hallazgo). Actualiza `docs/APPLY.md:529`.
4. **U4 I-H2**. Migración 0083 + reversa. Cambio en vivo: el vigía cierra impulsos desarmados. Prueba en `tests/test_impulso_io.py`.
5. **U5 I-H4**. Bloqueada por las cifras del dueño. Prueba: `planea_ajuste` con 5000 MXN levanta `ValueError`.

Cruce con operación: H-H2 (cuatro horarios al día) va después de U3.

## Decisiones del dueño

1. Quitar el reenvío de pujas dudosas (cambia la decisión ADV-04 y una fila de `docs/APPLY.md`). Recomiendo sí.
2. Cifras máximas por moneda para puja y presupuesto diario. Recomiendo puja = 4 veces el techo por omisión (USD 10, MXN 180) y presupuesto = el doble del más alto de hoy.
3. Meta manual de ACoS dentro de 10–45 %, validada solo al cambiarla. Recomiendo sí.
4. Un impulso que pausaste con «PAUSAR GRUPO» se da por terminado. Recomiendo sí.
5. Aparte: el impulso pausado con su propio botón puede atorarse igual (mismo `impulso.py:187`). Recomiendo unidad propia después.

## Riesgos

- Una hoja con bid sin sello en una plataforma que pasa a shadow u off se queda sin decisión hasta volver a live (hoy 0 filas). Queda visible con el motivo `bid_en_vuelo`.
- U4 agrega un valor a un vocabulario que vive en tres listas (CHECK, `EstadoVeredicto`, `FRASES_FINALES`). La prueba de U4 llama a `frase_final("desarmado")`.
- U5: falta medir el presupuesto diario más alto de hoy por moneda (no encontré la tabla en esta pasada).
