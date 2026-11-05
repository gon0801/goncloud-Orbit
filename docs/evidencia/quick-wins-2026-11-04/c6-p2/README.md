# C6-P2: replay economico RO, 5-nov-2026 UTC

Resultado: 48 cruces numericos en 5234 filas entidad/ciclo, no decisiones elegibles ni applies. 30 ciclos sin reloj unico se excluyen. Ninguna escritura productiva, ciclo, sombra, config ni reporte Amazon nuevo.

Ventana de ciclos explicita: 2026-10-05..2026-11-03; observacion 2026-11-05 01:46:42 UTC. El reloj es `min(decided_at)=max(decided_at)`; vintage <= reloj, corte >=10 dias y >=7 fechas. Formula recalculada sobre todas las filas con Decimal y moneda: `cost > 3 * target_pct / 100 * revenue` y `cost-target_pct/100*revenue >= 80 USD / 1000 MXN`. Validacion local correcta. Se conserva target y procedencia congelados, sin recurrir al goal actual.

| Grano | Moneda | Filas | Cruza | No cruza | Sin target |
| --- | --- | ---: | ---: | ---: | ---: |
| campaign | MXN | 510 | 0 | 374 | 136 |
| campaign | USD | 238 | 6 | 130 | 102 |
| keyword | MXN | 1671 | 0 | 1484 | 187 |
| keyword | USD | 939 | 2 | 495 | 442 |
| product_target | MXN | 849 | 17 | 781 | 51 |
| product_target | USD | 1027 | 23 | 953 | 51 |

Las filas repetidas por ciclo no son entidades independientes; no sumar campanas con hojas ni sumar monedas. Son 7 entidades candidatas distintas, en 5 campanas. Con ventas: 12 cruces, 986 no cruces, 204 sin target. Sin ventas medidas: 36 cruces, 3231 no cruces, 765 sin target. Los 48 cruces usan `margen_plataforma`: 9 freeze_entidad y 39 freeze_compartido_campana. Sin target historico: 969 filas, todas unknown. No se cuantifica ahorro.

## Maduracion +10d sobre la misma ventana

Se mantiene `desde/hasta`, target y moneda; se selecciona otra vez la ultima observacion <= `as_of+10d`, sin desplazar ventana. Son 20 candidatos con ALGUNA revision posterior: ninguno deja de cruzar con esa revision parcial. Otros 13 candidatos con horizonte cumplido no tienen revision posterior y 15 aun no cumplen el horizonte. La lectura adicional por fecha muestra 368/912 pares fecha/cohorte revisados entre las 33 cohortes con horizonte cumplido: NINGUNA tiene todas sus fechas revisadas. Por tanto la maduracion completa esta desconocida en las 48 cohortes; **no se declara FP=0 ni se satisface el gate de live C.2b**. El nombre `observable` de la salida auxiliar original significa revision parcial, no maduracion acreditada completa. `cobertura.json` conserva fechas y timestamps por cohorte; son pares repetidos, no 912 fechas independientes. Maduracion economica no demuestra elegibilidad, veto ni revalidacion de apply.

En toda la poblacion: 2576 no candidatos observables; 205 sin target aunque hay revisiones; 501 sin target y sin revision posterior; 263 sin target con horizonte futuro. Hay 455 no candidatos sin revision posterior, 1183 con horizonte futuro y 3 con cobertura distinta. Detalle en `maturity_10d` de replay.json. No hay observacion futura fabricada. La comprobacion auxiliar exige el mismo conjunto exacto de fechas as-of y +10d, numero de fechas y moneda; un cambio de cobertura se clasifica unknown.

## Limites y hallazgos para P3

- El script no esta en `/app/tools/replay_ads_economico.py` del contenedor: `sha256sum` y `ls` devuelven ENOENT. Se ejecuto por stdin la copia versionada exacta de SHA256 `f461035f7f52c54e11b701535e9b0dc61e8b33c8b9ba138fa3dbcfc49982be3a`, embebida en replay_ro.py, sin instalarla. El hash ejecutado esta en metadata.
- El replay usa parentesco actual y max(metric_date) POR ENTIDAD: esto coincide con el contrato real de `windows.ventana_cortes`, no es por si solo un defecto ni un bloqueo de frescura. La hoja 3859 produce 17 cruces MXN con ventana mayo-junio, y 5892 USD queda con ventana hasta 4-oct incluso en noviembre. La guarda de frescura es de PLATAFORMA: watermark <=7 dias y MAX synced_at <=48h. Los 48 cruces pertenecen a ciclos done con decisiones, evidencia de que esa guarda paso. No se conserva la edad exacta del sync historico; no se sustituye por el cache actual. En campanas hay ademas guard individual status_synced_at <=48h, desconocido historicamente.
- La ejecucion exacta lee toda la historia <= limite y calcula vintage por ciclo en memoria. Termino sin timeout, pero no se midio huella de memoria; no usar indiscriminadamente ventanas mayores. La consulta auxiliar se acota dinamicamente al menor `desde` y mayor `hasta` del resultado; no corta ventanas antiguas. Un borrador auxiliar con limite agosto se reemplazo antes de esta evidencia final al detectar la ventana mayo-junio.
- Se recupero por GET un reporte Amazon existente y se concilio contra el dinero original, descrito abajo. Su ventana cubre parcialmente una cohorte candidata relevante; no cubre las ventanas antiguas de mayo-junio ni acredita +10d de cohortes anteriores. No se crearon reportes nuevos.
- P2 queda ejecutado con cobertura medida y gate economico incompleto: maduracion completa, estado/goal historico y conciliacion de ventanas completas siguen unknown donde falta evidencia. No se transfieren como criterios cumplidos a P3. P3 requiere resolverlos o una decision explicita sobre esos limites, ademas de aislamiento de todos los escritores BIDS02, cola y reversa antes de solicitar go H6.4. Cinco ciclos propios, cero mutaciones globales y go live separado siguen pendientes.

## Reproduccion

Desde la raiz del checkout:

```bash
ssh goncloud 'docker exec -i -e PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=15000" orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/c6-p2/replay_ro.py > replay.json
```

`replay_ro.py` conserva SQL y fuente exacta. Registra DSN en redactor; no imprime valores secretos ni los pasa por argv. La conexion verifica READ ONLY on y timeout15s; resultados en metadata. No modifica app/tools. El JSON completo permite recalcular agregados y no incluye DSN/tokens.

Validacion: `python3 docs/evidencia/quick-wins-2026-11-04/c6-p2/validar.py` recalcula formula y madurez; `uv run --frozen python -m pytest -q tests/test_replay_ads_economico.py`: 11 passed. Ruff, diff-check y pre-commit (gitleaks/conflictos) correctos sobre estos archivos. CI final corresponde al host antes de integrar.

## Delta de elegibilidad historica

`elegibilidad_ro.py` consulta solo las siete entidades, los ciclos del intervalo y metadatos de reportes pertinentes. `elegibilidad.json` conserva SQL/parametros/UTC. `clasificar.py` genera `clasificacion.json` por cada uno de los 48 cruces.

- **48/48 bloqueados para proteccion economica real:** el flag falta en las configuraciones congeladas por sus decisiones/ciclos (34 ciclos, configs identificadas en JSON). No se infiere a partir de config42 actual. Este OFF era esperado y explica el comportamiento real; no responde cuantos serian elegibles con ON ni prueba ausencia de riesgo al encender. El replay es contrafactual numerico; ninguna de estas senales fue elegible para activar la regla economica apagada.
- Guardas plataforma: los 48 estan en ciclos done con decisiones, lo que acredita el paso de la guarda global en el recorrido. Ventana madura y >=7 fechas se verifican por fila. Las ventanas antiguas NO se excluyen artificialmente.
- Estado ENABLED de hoja/ancestros y goal habilitado: solo 2/48 tienen decision individual historica en el mismo ciclo (5892/ciclo102, decision2736 `pause_umbral`; 5896/ciclo110, decision2760 `banda_menos_25`). Su existencia acredita que pasaron ese camino, y el goal consumido se conserva en inputs. No demuestra un apply economico ni todos sus gates.
- Para los otros 46 cruces, el motivo individual de no decision es **unknown**: `ad_entity_state` es cache mutable, `ads_optimizer_goal` es configuracion mutable y las notes del ciclo agregan saltos sin entidad. El inventario de esquema no muestra historia de estas tablas. No asignamos a una entidad un skip agregado ni usamos el estado actual como pasado. Para las 6 filas campana tambien falta status_synced_at historico exigido por `propuestas_campana.evalua`.
- El contrafactual completo si se encendiera sigue unknown: veto/cola, cooldown propio de PAUSE, estado/goal y revalidacion vigente no se reconstruyen desde un cruce numerico. Ninguna tabla declara applies posibles.

Contrato inspeccionado, hashes locales/remotos iguales: `app/cycle.py` a648b325d5b995b2914cb025a0850e15a6543e0e78acafdea0021707baf9e89e; `app/optimizer/windows.py` 51273d2edb4bdbd5e7a2a2ebecf5ce77315ba613a26602a65f55a24c5c9bbd0d; `app/propuestas_campana.py` 4514c5ae1aaa886cefa52e8b16d8c069c24898ce326870e88672e162e9a1758f. No se cambia codigo de producto.

## Contraste externo disponible

`reporte_existente_ro.py` recupera por GET `/reporting/reports/7d1234d4-881c-4505-a499-7c9620f79f5c` el reporte ya creado el 3-nov para perfil US 4387591165211712; HTTP200, COMPLETED, 3258bytes, rango3oct..2nov. Se descarga por su URL firmada sin guardarla ni imprimirla. No se llama create_report ni se lista el universo de reportes. De sus 248 filas se conservan solo las 62 de campanas3911/3920 (31 fechas cada una).

Comparacion contra `ads_metric_observation` usando MISMO source_report_id: **62/62 pares entidad/fecha coinciden exactamente en cost y sales30d con Decimal, moneda USD; 0 discrepancias**. La identidad externa se mapea explicitamente en el helper. `reporte-existente.json` conserva filas Amazon filtradas y SELECT original; `clasificar.py` verifica importes.

Para el candidato campana3920/ciclo164 (decision3nov; ventana25sep..24oct), el reporte cubre 3oct..24oct: **22/30 fechas**, costo196.51USD y revenue187.67USD. El replay de la ventana completa era costo200.25USD/revenue187.67USD. No se compara la suma parcial con la completa como si fueran iguales: faltan25sep..2oct (8fechas). La otra campana3911 tambien concilia por fecha, pero este reporte reciente no acredita su antigua cohorte candidata. Reporte3nov leido5nov es fuente externa de ese vintage; no es el vintage +10d de todas las cohortes previas. Esto cierra contraste parcial disponible, deja conciliacion completa unknown y no habilita sombra/live.

Reproduccion del delta: ejecutar cada `*_ro.py` por el mismo comando stdin/PGOPTIONS de arriba y redirigir a su JSON correspondiente (`elegibilidad.json`, `cobertura.json`, `reporte-existente.json`); despues `python3 docs/evidencia/quick-wins-2026-11-04/c6-p2/clasificar.py`. Son consultas READ ONLY/15s y GET/download de un reporte existente. Sin escrituras de produccion.
