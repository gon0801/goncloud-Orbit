# ORBIT 07: arranque, dependencia y matriz M1-M6

Fecha de arranque: 2026-11-07 (UTC). Plan que manda:
[`plans/orbit-07-motor-decisiones.md`](../../../plans/orbit-07-motor-decisiones.md).
No se usa `manifest.active` (`resumen-ads-multicanal`) como estado del ejecutor.

## Dependencia de ejecución

- Encargo predecesor: **AUTO-03**, `enc_07844b614147c266` del registro
  `C:/Users/ehven/.motor-encargos`.
- Base recibida: `f86ed7dcd77f0af7b540217c3df9422ebe5c7154` (`origin/master`).
  El servidor la tuvo desplegada y verificada; `/health` respondio ok. La
  consulta de arranque no encontro otro encargo activo en la PC ni en la Mac.
- La captura historica que situaba AUTO-03 en E es historia
  ([`prearranque`](../orbit-07-plan/prearranque-2026-11-07/README.md)); manda
  [`preparacion-2026-11-07.json`](preparacion-2026-11-07.json).
- Revisión de arranque por Opus:
  [`revision-arranque-opus.md`](revision-arranque-opus.md), con dictamen
  `APPROVE_START` para M0 necesario para M1 más M1. Su triage:
  [`triage-revision-arranque.md`](triage-revision-arranque.md).

## Roles, modelos y habilidad

- Implementador exclusivo: **DeepSeek Flash**, referencia
  `deepseek/deepseek-flash`, proveedor directo `api.deepseek.com` (API propia
  de David). Sin fallback a otro modelo ni cuenta.
- Revisor independiente exclusivo: **`anthropic/claude-opus-5-5`**. Lee y
  aplica poteto-mode para su lectura y juicio; no modifica el producto ni
  ejecuta el playbook de implementación.
- poteto-mode aplicado por el implementador:
  `C:/Users/ehven/.motor-encargos/runtime/enc_be56b9f573270946/6b8e2839f90742eeb44624c902baf0a3/poteto/skills/poteto-mode/SKILL.md`.
  La copia conocida de la PC es
  `C:/Users/ehven/motor-conf/pstack/skills/poteto-mode/SKILL.md`; sus
  referencias viven junto a ella. Se adaptaron las herramientas al runtime
  nativo y prevalecieron las instrucciones del dueño y `AGENTS.md`.
- Primer trabajo concreto: commit de documentación (plan, evidencia, manifest,
  esta matriz), después M0 mínimo para M1 y M1 completo con su regresión,
  recorrido de ciclo/cola y CI.

## Matriz M1-M6

| Fase | Comportamiento requerido | Implementación heredada de AUTO-03 | SHA base | Evidencia | Diferencia pendiente |
|---|---|---|---|---|---|
| M1 | Proteger una venta reciente con ancla y vencimiento fijos; esperar sin congelar las pujas; misma subpolítica pura al decidir y revalidar; reversa de selección por setting | AUTO-03 dejó la pausa por umbral antes que la venta reciente (`politica.py` R0 antes de R8) y la revalidación de cola solo con ventana madura | `f86ed7d` | Pruebas puras y de integración, `m1/antes-despues.json`, episodio append-only (migración 0081), regresión de la espera vencida, selección `ads_proteccion_venta`, CI del candidato | Activación y primer ciclo real (fuera de esta entrega) |
| M2 | Resolver la economía compatible y fresca por un solo camino; aplicar directamente los cambios posteriores de meta del producto | AUTO-03 C-F, `productos.py`, `productos_io.py`, `evaluacion_producto_ciclo`, S3 y F.3/S12 | `c4121c5` | [`m2/politica-implementacion.md`](m2/politica-implementacion.md), [`m2/economias.json`](m2/economias.json), [`m2/sonda_economias.py`](m2/sonda_economias.py), [`m2/rendimiento-comparado.json`](m2/rendimiento-comparado.json), `tests/test_orbit07_m2.py`, `tests/test_productos_meta.py` | Cumplida: meta directa (excepción a la rampa) y respaldo completo, con el equilibrio del producto en el caso; la cola usa el mismo lector familiar del ciclo; la versión se elige con `ads_producto_economia_<plat>` (ausente/`auto03` = cascada previa; `producto_directo_v1` = M2) y `ads_producto_meta_<plat>` sigue apagando la meta |
| M3 | Un episodio de aprendizaje por producto y mercado; agregar sin doble conteo | AUTO-03 D/H ya agrega gasto y propone pausas con límites por mercado | `f86ed7d` | [`m3m4/comparacion-auto03.md`](m3m4/comparacion-auto03.md), [`m3m4/aprendizaje.json`](m3m4/aprendizaje.json), [`m3m4/sonda_aprendizaje.py`](m3m4/sonda_aprendizaje.py) | Ninguna acreditada: el agregado de AUTO-03 ya colapsa por fecha y no duplica; el freno de subidas solo aplica a grupos exclusivos y la intersección candidato×exclusivo es 0 en MX y US. Omitido |
| M4 | Extender la pausa existente con el aprendizaje pendiente, con venta reciente inmadura del producto | AUTO-03 G-I: `kind='pause'` sobre `product_ad`, `apply_producto`, `retiro_anuncios` y reversa; H.2 solo ve `[D-60, D-30)` | `f86ed7d` | [`m3m4/comparacion-auto03.md`](m3m4/comparacion-auto03.md), [`m3m4/aprendizaje.json`](m3m4/aprendizaje.json), [`m3m4/regresion-venta-reciente.txt`](m3m4/regresion-venta-reciente.txt), [`m3m4/regresion-venta-reciente-verde.txt`](m3m4/regresion-venta-reciente-verde.txt), `tests/test_productos_pausa.py`, `tests/test_apply_producto.py` | Cumplida en código verificado: política v3 con ventana `[D-29, D-1]`, ancla fija y madurez de 30 días; la pausa espera al decidir y al revalidar, sin PUT ni cuota. v2 conserva el replay histórico |
| M5 | Episodios económicos del último cambio con ventanas y madurez | AUTO-03 dejó `v_cambio_bid` y métricas point-in-time; M2 aporta la economía compatible | `4e0e78d` | [`m5/contrato-episodio.md`](m5/contrato-episodio.md), [`m5/episodios.json`](m5/episodios.json), [`m5/sonda_episodios.py`](m5/sonda_episodios.py), [`m5/consulta-episodios.sql`](m5/consulta-episodios.sql) | Cumplida: ventanas [x-7,x-1] y [x+1,x+7] sin el día del cambio, madurez D+17, comparabilidad y aplicabilidad separadas, contribución solo con margen compatible |
| M6 | Aplicar la evaluación económica del último cambio maduro | AUTO-03 conserva la trayectoria vigente y el replay por eras | `4e0e78d` | [`m6/politica-implementacion.md`](m6/politica-implementacion.md), [`m6/rendimiento-comparado.json`](m6/rendimiento-comparado.json), `tests/test_orbit07_m5m6.py` | Cumplida en código verificado: recorte con ventas conservadas y ACoS mejor, subida frenada sin compensación, `ads_bid_economia_<plat>` para activar/apagar; activación es del dueño |

## Distinciones de estado

- **Preparado**: el plan, la base y la lectura están disponibles.
- **Verificado**: la comprobación ejecutable local pasa.
- **Integrado**: el dueño hizo merge (fuera de este encargo).
- **Activo**: el modo y el encendido de la pausa los decide el dueño. Una PR
  no es despliegue ni activación.
