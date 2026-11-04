# S.6: manda los avisos — evidencia

Rama: `rp02/sec3-seguridad-goals`, sobre S.5 (`388bcc1`). Fuentes (en
orden): fila S.6 de `plans/repricing-02.md` (DoD de 5 clausulas),
`plans/repricing-02-construccion.md` S.6 (lineas 611-628), bosquejo U
(`docs/evidencia/repricing-02/diseno/bosquejo.py` 1832-1866) y decision
G7 del diseno (resumen y compuerta con sello; /salud avisa si falta).
TDD: las 7 pruebas nuevas se escribieron primero y se miraron fallar
por la razon correcta antes de implementar (ver §2).

Comando base (se omite por brevedad):

```console
$ PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
  PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider
```

## 1. Archivos

Tocados (3):
`app/notifica.py` (6 payloads + 6 builders puros, `TIPOS_PRECIO` y
`FRASEO_TIPO_PRECIO` extendidos, despacho de `notifica_precio` por
tabla — resolucion tardia para no romper la costura de monkeypatch —,
`_avisar_resumen_precio` con sello `resumen_enviado_at`,
`_avisar_compuerta_precio` con sello `avisada_at`, gancho en
`avisar_precio` fuera de la guarda B2),
`app/api_precios.py` (`corrida_ausente` dentro del bloque `precios` de
/salud + docstrings),
`tests/test_precio_pantalla.py` (bloque S.6: 7 pruebas + ayudantes
`_s6_corrida_cerrada`/`_s6_medicion`/`_s6_textos`).
Contratos intactos: bosquejo, plan, migraciones (cero ediciones),
`app/precio/*`, `tools/*`. Sin red real en ninguna prueba
(`_canal_falso` / `httpx.MockTransport`).

## 2. Rojo TDD (antes de implementar)

```console
$ pytest tests/test_precio_pantalla.py -k "test_s6_"
7 failed, 78 deselected in 1.48s
```

- T1/T2: `assert 0 == 1` (sin resumen enviado).
- T3: `assert 0 == 1` (sin aviso de compuerta).
- T4: `assert 0 == 1` (sin resumen).
- T5: `assert 0 == 1` (el repaso no manda nada).
- T6: `AttributeError: module 'app.notifica' has no attribute
  'aviso_precio_resumen'`.
- T7: `KeyError: 'corrida_ausente'`.

## 3. Verde final (arbol limpio, mutantes revertidos)

```console
$ pytest tests/test_precio_pantalla.py tests/test_notifica.py \
  tests/test_api_dashboard.py tests/test_architecture.py
351 passed, 1 warning in 20.13s
$ ruff check app/notifica.py app/api_precios.py tests/test_precio_pantalla.py
All checks passed!
$ ruff format --check app/notifica.py app/api_precios.py tests/test_precio_pantalla.py
3 files already formatted
```

85 del archivo tocado + 266 vecinos (sender, salud, arquitectura). No
se corrio la bateria completa (fuera de alcance S.6).

## 4. DoD S.6 (clausula por clausula)

1. Un resumen por plataforma y dia, tambien si nada se movio:
   `test_s6_resumen_diario_uno_por_plataforma_y_dia_aunque_nada_se_mueva`
   (mantener x2 -> "hoy no se movio ningun precio"; MX no calla a US).
2. Dos corridas el mismo dia mandan un resumen:
   `test_s6_dos_corridas_mismo_dia_mandan_un_resumen` (sello en la
   primera; la segunda, con `decisiones=0`, manda 0).
3. Compuerta una vez por universo+causa+dia con repasos:
   `test_s6_compuerta_una_vez_por_universo_causa_y_dia_con_repasos`
   (1 -> 1 -> 3; sellos `avisada_at` correspondientes).
4. Ningun texto trae costo, margen ni goal:
   `test_s6_textos_enviados_sin_costo_margen_ni_goal` (lo enviado al
   canal, con insumo `costo` y motivo `precio_no_cubre_costo`).
5. Fallo de envio no tumba la corrida:
   `test_s6_fallo_de_envio_no_tumba_la_corrida_y_el_repaso_reintenta`
   (corrida real: tumbar -> resumen normal + 0 sellos; repaso no-op con
   canal sano -> 1 resumen + sello).
6. Extra del plan (texto de cada aviso nuevo):
   `test_s6_builders_nuevos_sin_costo_margen_ni_goal` (8 textos, 6
   builders, payloads adversos).
7. Cambios (/salud): `test_s6_salud_corrida_ausente_dentro_del_bloque_precios`
   (True sin cerrada, False con cerrada, abierta no cuenta; juego de
   claves de alto nivel intacto).

## 5. Desviaciones con razon

- D1. El resumen sale aunque la corrida decidio 0 (fuera de la guarda
  B2), no solo "en la primera corrida que cierra con decisiones nuevas"
  (bosquejo U). Razon: el plan exige que el repaso reintente un envio
  fallido, y el repaso tipico es no-op; con la guarda B2 nunca
  reintentaria. El que no repite es el sello, no la guarda.
- D2. El insumo `costo` se nombra "coste" en el aviso (bosquejo: "nombra
  el insumo"; DoD: ningun texto trae "costo"). Razon: el DoD es literal
  (convencion de los builders viejos); "coste" es el sinonimo RAE.
- D3. `cambio_error`, `corte_errores`, `reversa` y `cohorte` quedan con
  builder + sender, sin disparo cableado. Razon: S.6 no especifica
  disparador ni sello para ellos (G7 solo sella resumen y compuerta);
  el cableado es de pasos posteriores (cohorte: S.7).
- D4. El despacho de `notifica_precio` paso de dos cadenas if/elif a
  tabla (mismo comportamiento). Razon: seis tipos mas rompian el tope
  de complejidad (C901 23 > 22); la tabla lo baja. Los nombres de
  builder se resuelven tarde para no romper la costura de monkeypatch
  que usa `test_precio_aviso_excepcion_warning_scrub_y_false`.
- D5. `corrida_ausente` solo va en /salud, no en /precios ni en el HTML.
  Razon: el plan pide /salud; sin dato de corridas (sin 0054) vale True
  en vez de tumbar el bloque.
