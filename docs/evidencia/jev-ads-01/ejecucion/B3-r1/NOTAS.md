# E/B3-r1 — filas 1.3 y 1.4 del plan JEV ADS 01 (transporte y asesor)

Fecha UTC: 2026-10-04T02:08:28Z (ACK) a cierre. Rama `docs/jev-ads-b3`
desde origin/master `224449fd97b479292a1ae38c585bdac713b652a3` (B2 #391
squash). Un solo escritor; SIN push (lo hace Claw tras VEREDICTO:
APROBADO).

## SHA

| Commit | Contenido |
| --- | --- |
| `a03c3dd5933405c55b58b76b63e1178a2c7f3da8` | 1.3 `app/jev_juicios.py` + 1.4 `AsesorAds.evaluar` y CLI `tools/jev_ads.py`, con sus pruebas |

HEAD final de CODIGO (40): `a03c3dd5933405c55b58b76b63e1178a2c7f3da8`
(el commit de evidencia posterior, si lo hay, es SOLO documental).

## Modelo fijo: verificado contra la documentacion viva

`jev-1.13.0` sigue vigente (docs.typesafe.ai/models.md, releido en esta
sesion; alias `jev-latest` apunta a el). El contrato de wire se tomo de
docs.typesafe.ai/api.md (POST /v1/systemone; state/model/questions;
answer choice con probabilities que suman 1 y confidence en [0,1];
usage con input/output tokens). Cero llamadas reales: todo el DoD con
HTTP falso.

## 1.3 — `app/jev_juicios.py` (25 pruebas)

- ROJO: `ModuleNotFoundError: No module named 'app.jev_juicios'`
  (`jev_b3_rojo_13.txt`).
- VERDE (`jev_b3_verde_13.txt`):
  - WIRE: el transporte falso captura el payload: SOLO
    `state={termino, ficha}` (hechos/desconocidos/listings/fechas de ESA
    ficha), `model=jev-1.13.0`, pregunta Choice con criterios EN ORDEN
    (satisface, no_satisface, informacion_insuficiente) y
    `Authorization: Bearer ...`; timeout 30 s. Sin grupo, campana,
    metricas ni otros productos.
  - VALIDACION ESTRICTA (9 mutaciones de respuesta): modelo distinto,
    respuesta ausente, type no-choice, choice fuera de opciones,
    probabilities incompletas, NaN, inf, suma != 1 y confidence fuera de
    [0,1] → `respuesta_invalida`. Exito → Juicio con Decimals.
  - CLAVE: estable con el mismo contrato; cambia con el ORDEN de opciones
    y con la VERSION del contrato; request_sha256 estable (hash canonico
    del request completo para la intencion).
  - ESTADOS VISIBLES: timeout, red, redireccion (3xx no se sigue, no se
    reenvia autorizacion), 4xx/5xx (red), contexto_excedido ANTES del
    HTTP por bytes de termino y de ficha serializada (sin truncar, cero
    llamadas), sin_api_key (cero llamadas). usage faltante = costo
    desconocido. `scrub` + `register_secret`: NINGUN estado contiene la
    clave (probado con un timeout cuyo mensaje la incluia).
  - Secretos: `<ORBIT_SECRETS_DIR>/typesafe.json` (patron notifica.py).
- MUTANTES demostrados en vivo: (a) validacion de modelo a `if False:` →
  1 failed; (b) `register_secret` a `pass` → 1 failed; restaurados →
  25 passed.

## 1.4 — `AsesorAds.evaluar` (app/jev_ads.py) + `tools/jev_ads.py` (13+5 pruebas)

- ROJO: `ImportError` de AsesorAds/SemillasARevisar/DecisionARevisar
  (`jev_b3_rojo_14.txt`).
- VERDE:
  - ORDEN SELLADO: el transporte espia consulta la base AL INVOCARSE y
    observa revision confirmada + intencion con request hash SIN
    resultado; el resultado llega tras el HTTP (fila confirmada). La
    revision congela censos (con fichas), terminos, plataforma,
    contrato (modelo/version/sha) y captured_at; decided_at separado.
  - CRASH/REANUDACION: crash simulado (excepcion del pedir tras la
    intencion) deja intencion huerfana; retomar con la MISMA solicitud
    reutiliza el exito previo (evento reutilizacion, CERO HTTP), registra
    intencion nueva ordinal 2 y completa.
  - IDEMPOTENCIA: misma solicitud con OTRO payload (otros terminos) →
    ValueError; misma huella retoma sin rechazar.
  - REUTILIZACION SOLO DE EXITOS VALIDOS: un fallo del proveedor no se
    reutiliza (nueva intencion + HTTP); el exito de OTRA revision tampoco
    (consulta acotada a revision_id); el fallo queda como resultado con
    error, jamas como exito (trigger de 0049 de refuerzo).
  - PRESUPUESTO: presupuesto=1 con dos terminos agota el lote
    (presupuesto_agotado=True, segundo termino sin par, retomable);
    retomar con presupuesto termina y no repaga el exito.
  - APAGADO: sin clave, cero llamadas (transporte que revienta si se usa)
    y resultado Indeterminado con fallo_proveedor. Y
    `tests/test_cycle.py + test_apply_cola.py + test_apply_harvest.py`:
    154 passed SIN diferencias (los consumidores no importan al asesor;
    candado AST nuevo `test_los_consumidores_no_importan_al_asesor`).
  - ASIN-like fuera del clasificador EN EL FLUJO: cero HTTP y resultado
    texto_no_aplica.
  - DECISION: sujeto `DecisionARevisar` escribe sujeto_tipo='decision' y
    la FK de jev_revision muerde con decision inexistente (el parseo de
    decisions reales es 2.1).
  - Pureza de 1.1 preservada: `test_modulo_puro_sin_red_ni_db` ahora
    exige top-level del modulo sin red/DB/modulos Jev de IO (los imports
    de AsesorAds son perezosos) y que el codigo de `componer` no
    referencia conn/httpx/requests.
  - CLI: dry-run por omision (cero escritura, cero llamadas, plan
    impreso); `--aplicar` ejecuta y RETOMA la misma solicitud reutilizando
    exitos; sin DSN → codigo 2. Presupuesto y --solicitud explicitos.
- MUTANTES demostrados en vivo: (c) reutilizacion de fallos (quitar
  `respuesta IS NOT NULL`) → 1 failed; (d) comparacion de mismo-payload a
  `if False:` → 1 failed; (e) ASIN-like a `if False:` → 1 failed;
  restaurados → verde total. Nota de proceso: un sed de restauracion con
  `$` no caso por la comilla de cierre y dejo el SQL de `_exito_previo`
  sin el filtro; detectado por el TRIGGER de 0049 (reutilizacion a exito)
  y corregido con edit — el candado de DB hizo su trabajo.

## Comandos

```
uv run --frozen python -m pytest tests/test_jev_juicios.py -q            # rojo 1.3, verde 25
uv run --frozen python -m pytest tests/test_jev_cli.py -q                # rojo 1.4, verde 18
uv run --frozen python -m pytest tests/test_jev_ads.py tests/test_jev_catalogo.py tests/test_jev_juicios.py tests/test_jev_cli.py -q   # 81 passed
uv run --frozen python -m pytest tests/test_cycle.py tests/test_apply_cola.py tests/test_apply_harvest.py -q                            # 154 passed
uv run --frozen python -m pytest -q                                      # 3,828 passed, 1 warning in 228.51s
uv run ruff check .  &&  pre-commit run --all-files                      # limpio
git commit sin --no-verify
```

Focalizadas del encargo: 81 passed (`jev_b3_verde_focalizadas.txt`);
bateria completa una vez sobre `a03c3dd`: 3,828 passed
(`jev_b3_bateria.txt`).

## Limites respetados

- Archivos: `app/jev_juicios.py`, `app/jev_ads.py` (SOLO anadir sujetos +
  AsesorAds; componer intacto), `tools/jev_ads.py`,
  `tests/test_jev_juicios.py`, `tests/test_jev_cli.py`, ajuste del test
  de pureza de `tests/test_jev_ads.py` (justificado arriba) y esta
  evidencia. NO tocados: migrations/*, cycle.py, apply_cola.py,
  apply_harvest.py, api_dashboard, fabrica_web, api_fabrica.
- Cero llamadas a TypeSafe/Jev real; cero claves; sin secretos (fake HTTP
  + base de prueba; la clave de prueba es literal inofensiva de fixture).
- GET no implementado (2.1); el asesor no se llama desde cycle/apply.
- Sin push: commit local `a03c3dd`; arbol limpio.

## Skills/agentes/modelos

- Sesion principal opencode: `zai-coding-plan/glm-5.3-flash`, un solo
  escritor, dueno del diff.
- Skills: poteto-mode (playbook Feature, skips razonados: delegacion
  omitida por contrato de un-escritor del loop; architect omitido: la
  forma viene sellada por el spec; PR omitido: push prohibido), unslop,
  tdd, typesafe-ai + docs vivas (llms.txt, api.md, models.md).
- Subagentes: NINGUNO; paneles: NINGUNO (diseño no contestado).
