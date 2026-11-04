# A.7: readback del recuadro de cobertura en producción (2026-09-18)

**Qué es.** Es la evidencia de producción de la fila **A.7** del plan `plans/repricing-01.md` v1.3: el recuadro de cobertura (spec S10, decisión 14) armado con los datos reales de hoy. Lo corrió el lead de la Fase 10 del autopilot (regla 9 del runbook `docs/runbooks/autopilot-fase10.md` de goncloud-openclaw). **Solo lecturas**: rol lector `ORBIT_DSN_READ`, cada consulta dentro de `BEGIN READ ONLY … ROLLBACK`, con el corredor `readback.sh` de esta carpeta.

**Corrida.** `salidas/CORRIDA.txt`:

- `estado: COMPLETA`, de 2026-09-18T11:33:35Z a 11:33:55Z UTC;
- `commit_del_repo: 74d99ab` y `consultas_o_corredor_con_cambios_sin_commitear: 0`;
- `omitidas: 03_goals 04_decisiones`, por la razón de abajo.

Las consultas de `consultas/*.sql` son **las mismas que ejecuta `app/precio/fuentes.py`**; la prueba `test_consultas_iguales_a_las_que_ejecuta_fuentes` lo exige. El recuadro se armó en local con `recuadro_desde_salidas.py`, que alimenta la función pura `app/precio/cobertura.py`, con **`--max-dias-sin-reportar 3`**. Ese es el valor inicial del plan: producción no tiene la clave `precio_catalogo_max_dias_sin_reportar` hasta D.0.

## Resultado

| plataforma | activas (fuente canónica) | evaluadas | no evaluadas | sin goal | fuera de alcance | cuadra |
|---|---|---|---|---|---|---|
| `amazon_mx` | **260** | 0 | 102 (`canal_sin_dato`) | 158 | 0 | **sí** (`[CUADRA]`) |
| `amazon_us` | **100** | 0 | 100 (`canal_sin_dato`) | 0 | 0 | **sí** (`[CUADRA]`) |

Salidas literales: `salidas/amazon_mx/recuadro.txt` y `salidas/amazon_us/recuadro.txt`. Las 158 publicaciones `sin_goal` de MX están listadas ahí con precio y canal.

**Las activas son las de hoy, no las del plan.** El plan decía 264 en MX y 106 en US (hecho 19, 2026-09-16). La DoD pide que el recuadro cuadre exacto con lo que la fuente canónica diga **el día del readback**: hoy son 260 y 100, y el recuadro cuadra con esas.

## Qué dice cada número

- **Ninguna evaluada y ningún goal.** Las tablas `precio_goal` y `precio_decision` **no existen en producción**, porque la migración 0039 no está aplicada: es la fila **D.0** del plan, del dueño. Por eso `03_goals` y `04_decisiones` se omitieron con su razón escrita en `CORRIDA.txt`, y el recuadro se armó con goals y decisiones vacíos, que es el estado real de hoy. **Consecuencia que se declara**: `tools/precio_cobertura.py` en producción necesita D.0; antes de D.0 sale `exit 2` con `relation "precio_goal" does not exist`. Falla cerrado, no inventa ceros.
- **`canal_sin_dato` = 102 en MX y 100 en US.** El canal sale de `estimacion_oferta_observation`, que hoy solo cubre el universo de la estimación: MX FBA, 221 listings (hecho 5; `02_canales` trae 221 filas en MX y **0** en US). Las FBM de MX y todo US no tienen canal todavía, y la DoD (c) dice «canal desconocido → `canal_sin_dato`, nunca un default». Por eso **no** aparecen como `fuera_de_alcance(fase_E_envio_fbm)`: para decir que son FBM hay que saber el canal. Cuando E.3/0.3 amplíen el universo de la estimación a FBM y a US, el canal se conocerá y las FBM pasarán a `fuera_de_alcance` con su fase.
- **`sin_goal` = 158 en MX.** Son las activas de MX con canal conocido (FBA): el trabajo del dueño cuando existan las tablas (D.0) y la herramienta de goals (A.1, ya en `master`).
- **`catalogo_desactualizado` = 0 y `sin_listing` = 0.** Las 360 observaciones canónicas son del 2026-09-18 y todas tienen fila en `listing`.

## Contraste con el bridge

El aviso del 5 % que pide la DoD (e) está implementado (`aviso_puente`) y probado. **La cuenta de activas del bridge no está en Orbit**: `listing` es identidad y no ciclo de vida (`app/listings.py` l.28, «`status` NO filtra»), así que `listing_identidad` (342 en MX, 176 en US) incluye inactivas y se muestra sin aviso. El estado del bridge vive en su propia base.

- Sin cuenta del bridge: `puente_activas=unknown` (`salidas/*/recuadro.txt`).
- Con las cifras del bridge del **hecho 19 del plan (2026-09-16: 284 en MX y 109 en US)**, pasadas con `--puente-activas` como referencia y **no** como lectura de hoy (`salidas/*/recuadro-con-puente-hecho19.txt`): MX `puente bridge=284 vs canonica=260`, **aviso 9.2 % > 5 %**; US `puente bridge=109 vs canonica=100`, **aviso 9.0 % > 5 %**. Es el mismo orden de diferencia que el hecho 19 describe como «dos definiciones de activa».

Para el contraste de un día concreto, el dueño pasa la cuenta de la caché del bridge de ese día: `python tools/precio_cobertura.py --platform amazon_mx --puente-activas <N>`.

## Reproducir

```
OMITIR="03_goals 04_decisiones" RAZON_OMISION="<razon>" bash docs/evidencia/repricing-01/A.7/readback.sh
./.venv/bin/python docs/evidencia/repricing-01/A.7/recuadro_desde_salidas.py \
  --canonicas docs/evidencia/repricing-01/A.7/salidas/amazon_mx/01_canonicas.txt \
  --canales docs/evidencia/repricing-01/A.7/salidas/amazon_mx/02_canales.txt \
  --goals /dev/null --decisiones /dev/null \
  --puente docs/evidencia/repricing-01/A.7/salidas/amazon_mx/05_listing_identidad.txt \
  --hoy docs/evidencia/repricing-01/A.7/salidas/amazon_mx/06_hoy.txt \
  --platform amazon_mx --max-dias-sin-reportar 3
```

Después de D.0, `OMITIR` se deja vacío y los goals y las decisiones salen de `03_goals` y `04_decisiones`.
