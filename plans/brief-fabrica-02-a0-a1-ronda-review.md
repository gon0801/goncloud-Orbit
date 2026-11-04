# Brief para Muse: ronda de correcciones del PR #258 (F2 · A.0 + A.1)

Antecedente: este brief se entregó pegado en el chat el 2026-09-12 y se guarda
aquí para que el contrato de la ronda viaje en el repo. La ronda se aplicó en
`4fd3255` (misma rama) y el PR se mergeó como `a01aed0`.

La entrega es buena: los 10 casos del brief están, los cuatro sitios de cableado
también, `21 passed / 0 skipped` con `ORBIT_TEST_DSN` apuntado, y las 131 pruebas
vecinas siguen verdes. Muté 12 veces el código: **8 mutantes mueren**. Para
comparar, en A.5 de SP-API sobrevivían 21 de 29. Esto está muy por encima.

Lo que sigue es una sola ronda: un bloqueante que encontré yo, tres hallazgos de
CodeRabbit que verifiqué uno por uno, y los cuatro huecos que destaparon los
mutantes sobrevivientes.

## BLOQUEANTE — una decisión puede congelarse con `default_bid: null` y reventar el replay

No es lectura, lo ejecuté:

```
1) decide() -> DestinoHarvest | bid = None
2) congelado.harvest.default_bid = None
3) replay: Decimal(default_bid) -> TypeError:
   conversion from NoneType to Decimal is not supported
```

Cadena: `_monto()` devuelve `goal.harvest_default_bid`, que puede ser `None`. El
camino **grupo** de `decide()` construye un `DestinoHarvest` válido igual, sin
exigir bid. `_procesa_grupo` pasa ese destino a `_pendiente_termino` para
**toda** decisión no-skip, no solo las de harvest. `_goal_json` entra al camino
F2 por `isinstance(destino, DestinoHarvest)` y congela
`"default_bid": _dec_str(None)` → `null`. Y `replay.py:134` hace
`Decimal(harvest["default_bid"])` **sin guarda**.

El candidato a harvest sí está protegido: `_config_harvest_de` devuelve config
`None` y salta. Pero una decisión `negative` del mismo grupo se crea igual y se
lleva el congelado roto.

Y se conecta con el hallazgo 1 de abajo: combinar las dos banderas de limpieza
borra el `harvest_default_bid`, que es justo el camino que fabrica goals sin bid.

**Criterio del arreglo** (no te dicto la implementación): el congelado tiene que
reflejar lo que el motor **realmente usó**. Si el destino no trae bid, el motor
no pudo hacer harvest — así que no debería congelarse un harvest que el replay no
puede reproducir. Si eliges otra salida (guarda en `replay.py`, o `decide()`
devolviendo `SaltoHarvest`), decláralo en el PR con el porqué.

**Test que lo habría atrapado**: campaña en grupo + goal sin
`harvest_default_bid` + un término que produce decisión `negative` → la decisión
congelada tiene que poder pasar por `reproduce()` sin excepción.

## Los tres de CodeRabbit — los tres válidos

1. **`goals_write.py:221`** — `harvest_limpia` y `harvest_limpia_destino` juntas
   se aplican las dos: la primera anula los tres campos (bid incluido), la
   segunda solo re-anula dos. El bid se pierde en silencio, que es lo contrario
   de lo que `harvest_limpia_destino` promete. Rechaza la combinación.

2. **`test_architecture.py:397`** — el candado se evade. Lo comprobé:

   ```
   DETECTA  UPDATE ads_optimizer_goal SET x=1
   EVADE    UPDATE public.ads_optimizer_goal SET x=1
   EVADE    INSERT INTO "ads_optimizer_goal" (a)
   ```

   Acepta identificador con esquema y entre comillas, y agrega casos negativos
   para ambas variantes. El brief pedía extender el candado a `tools/`: quedó
   extendido pero con agujeros.

3. **`test_fabrica_f2.py:375`** — el test solo mira status codes; pasaría aunque
   el handler guardara el negative de `62001` antes de devolver 400. Consulta el
   almacén de los dos ad groups después de los POST.

## Los cuatro huecos de test (mutantes que sobrevivieron)

Ninguno es un bug hoy: el código está bien. Lo que falta es la prueba que impida
que alguien lo revierta sin enterarse.

1. **`completa` derivado del destino, no de la terna del goal.** Mutarlo a
   `goal.harvest_campaign_id is not None` no mata ningún test — y es exactamente
   lo que tu propio docstring advierte que rompería el replay tras D.2.
2. **La guarda `pertenece` contra `ad_entity`** en `_contexto_congelado`.
   Anularla no mata nada. Es el único cinturón de los caminos `excepcion` y
   `terna`, donde no hay re-resolución que los cubra; el único test de
   `destino_desincronizado` va por el camino `grupo`.
3. **`harvest_limpia_destino` sin exigir scope `campaign`.** Hay test para el
   grupo, ninguno para el scope.
4. **Grupo y excepción a la vez.** Mover la excepción antes que el grupo no mata
   nada: ningún caso tiene los dos. Fija el orden con un test.

## Proceso

- Rojo antes del arreglo en los cinco puntos con test nuevo, y cítalo en el PR.
- **Vuelvo a mutar.** Los cuatro sobrevivientes de arriba tienen que morir.
- `pytest_focal` con `ORBIT_TEST_DSN` apuntado: `0 skipped`.
- Cero `--no-verify`.
- **No toques** el tracker ni `docs/CHAT-CONTEXT.md`: el cierre de A.0/A.1 lo
  hago yo en un PR aparte.
- Sigue en el mismo PR #258 y la misma rama; no abras uno nuevo.

## Resultado de la ronda (cierre del lead, 2026-09-13)

- Bloqueante: salida elegida «el congelado refleja lo usado» — destino sin monto
  congela `harvest: null`. Rojo citado: `TypeError` en `replay.py:134`.
- CodeRabbit: los tres corregidos con rojo previo.
- Re-mutación del lead: 5 de 6 mueren. El sobreviviente (`completa` desde la
  terna) es **equivalente**: en el camino F2 `completa` se asigna y nunca se lee.
- Suite focal: 47 passed, 0 skipped con `ORBIT_TEST_DSN`. CI verde en `4fd3255`.
- Residuales declarados, no reabren el ciclo: comentarios SQL `/**/` evaden el
  regex del candado (menor); el candado es texto, no barrera en Postgres —
  `app_admin` tiene `INSERT, UPDATE` sobre `ads_optimizer_goal` desde 0001 y
  `tools/fabrica_campanas.py` abre `ORBIT_DSN_ADMIN` (decisión de arquitectura
  del dueño; va a R.1).
