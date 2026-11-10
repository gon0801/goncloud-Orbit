# Síntesis — frente ads-decisión (E-S1, E-H2, A-H1, A-H2, A-H3, B-H1, C-H1..C-H4, D-H1)

Candidatos: `opus/DISENO.md` y `sonnet/DISENO.md`. Sin juez cruzado.
Base: **opus** en todas las unidades. Tres afirmaciones de sonnet no pasaron la comprobación contra el código.

## Comprobado por mí contra el código y producción (2026-11-09, solo lectura)

- **Decisión 2869, urgente.** `apply_queue.id = 26`, amazon_us, ad group 4012, negative EXACT
  «wedding arras», `modo = live`, `pending_veto`, vence 2026-11-10 08:40:02 UTC. «wedding arras»
  es keyword EXACT de ese mismo ad group (`ad_entity.id = 1107058`). El ciclo de US de las 08:40 UTC
  la liberaría y la aplicaría.
- 2321 («arras de boda», cola 10) y 2322 («arras for wedding ceremony», cola 11) están `applied`
  desde el 16-sep en el ad group 3989. Las dos son keywords EXACT de ese ad group (`ad_entity`
  4926 y 4925). No medí si los negatives siguen vivos en Amazon.
- El ciclo regresa antes de evaluar la protección cuando no hay episodio ni venta reciente
  (`app/cycle.py:1249-1254`). Por eso la regla de hueco de sonnet dentro de `evalua` nunca correría.
- La cola arma el ancla de familia con una copia a mano (`app/economia_revalidacion_io.py:150-155`),
  no con la función del ciclo. La afirmación de sonnet de que comparten función es falsa.
- `_motivos_sin_meta` (`app/optimizer/productos.py:464-465`) parte de `entrada.impedimentos`. Un
  impedimento nuevo (A-H3 de sonnet) bloquearía la meta de producto, no solo la pausa.
- 0 episodios de protección de venta, 0 filas `espera_proteccion`, 0 reversas de subida. Las
  consultas de «antes de desplegar» de E-H2 y A-H1 dan 0 hoy. Se repiten el día del despliegue.
- Regla de replay: lo que cambia una función de decisión lleva marcador congelado o consulta previa
  en 0 (`app/optimizer/replay.py:17-22`, `app/optimizer/caso.py:965-974`).

## Decisión por hallazgo

| Hallazgo | Base | Rechazado |
|---|---|---|
| E-S1 negative sobre keyword propia (ALTO) | opus: `decide_hygiene(..., *, keywords_propias: frozenset[str])` obligatorio y sin valor por omisión; salto `termino_es_keyword_propia` antes de separar negative y harvest; lector por lote `hygiene.keywords_exactas_por_grupo(conn, platform, *, solo_grupo=None)`; llamadores `app/cycle.py` (TX2 y `_procesa_grupo`), `app/apply_cola.py:1282`, `app/apply_harvest_reconciliacion.py:231`, `app/optimizer/replay.py:96` (vacío explícito) | sonnet: valor por omisión `frozenset()` (un llamador olvidado vuelve al bug sin que nada falle) y una consulta por ad group |
| E-H2 piso aprendido al revertir una subida | idéntico en los dos: `Trayectoria.piso_aprendido` solo cuenta regresos con `direccion == 1` | nada |
| A-H1 + A-H2 protección de venta | opus: sin episodio, `if lectura.ancla is not None: espera(abre=True)` antes de la decisión 6, que queda intacta; `lee_cache` conserva el ancla sin cobertura; la ventana reciente de cada hoja empieza el día siguiente al fin de su ventana de cortes; se borra `hay_venta` y la compuerta de `cycle.py:1250-1254` pasa a «episodio o ancla reciente» | sonnet: `_hay_hueco` dentro de `evalua` (inalcanzable en el ciclo) y voltear la decisión 6 |
| C-H4 subir puja mientras se espera venta | opus: marcador congelado `frena_subidas: bool = False` en `InsumosProteccion`; en `politica.decide`, `factor > 0` con espera activa → `Mantener("subida_en_espera_de_venta")` | sonnet: el mismo cambio sin marcador (cambia el replay de casos viejos) |
| D-H1 apagar la protección deja pausas atoradas | opus: `apply_cola.descarta_esperas_proteccion(conn, platform) -> int`, llamada desde `libera_vencidos` con la protección apagada y desde `app/cli_productos.py` al apagarla, en la misma transacción | sonnet: chequeo por fila sobre una ruta JSON inferida y cubrir `rollback.sh` solo con texto de runbook |
| C-H1 / C-H2 / C-H3 economía del cambio | opus en dos piezas. **U6a lectura:** quinta fuente de invalidación (negatives y harvests aplicados en el mismo ad group) y caducidad del episodio en x+31 (`evidencia_vencida`). **U6b juicio v2:** `economia_cambio_v2`, mínimo de clics para juzgar, `post.venta <= pre.venta → frena` antes de `pre.venta <= 0`. La versión se elige por setting | sonnet: reordenar el juicio sin versión nueva (cambia el replay de decisiones desde el 8-nov) |
| B-H1 salto de economía de producto a familia | opus: `productos.ancla_familia(previo, destino, *, version) -> tuple[Decimal \| None, bool]`, única fuente para ciclo (`app/cycle.py:3136`) y cola (`app/economia_revalidacion_io.py:151`); `MOTIVOS_DATO_TRANSITORIO = frozenset({"ledger_rancio"})` converge en vez de saltar | sonnet: `min(previo.target, destino)` (interpola entre economías, que la letra del plan prohíbe en `plans/orbit-07-motor-decisiones.md:237`, y solo arregla el ciclo) |
| A-H3 venta reciente sin medir en el producto | opus: campo `dias_recientes_sin_medir` en `EntradaProducto`, motivo `venta_reciente_sin_medir` en `motivos_economicos_pausa` | sonnet: impedimento nuevo (bloquea también la meta de producto) |

Nota al implementador: `DIAS_LECTURA = 40` de opus es más ancho de lo necesario. La cota sale de las
constantes que ya existen (ventana de cortes + `DIAS_RECIENTES`).

Con la caducidad de U6a aceptada, C-H3 (x+84 a x+90) queda inalcanzable y no necesita código propio.

## Unidades y orden

0. **R1** vetar la 2869 antes del 10-nov 08:40 UTC. Sin código. La hace el dueño en la pantalla Cortes.
1. **U1 E-S1.** Cambio en vivo: deja de proponer negatives y harvests sobre keywords propias. Prueba en `tests/test_optimizer_hygiene.py`: «Arras de Boda » con `keywords_propias={"arras de boda"}` → `kind None`, motivo `termino_es_keyword_propia`.
2. **R2** tras desplegar U1: revertir 2321 y 2322 con la ruta que ya existe (`POST /api/ads-optimizer/reversa/negative`, colas 10 y 11).
3. **U2 E-H2.** Prueba pura en `tests/test_optimizer_caso.py`: 1.00→1.15 y regreso a 1.00 → `piso_aprendido is None`.
4. **U5 D-H1.** Antes de tocar la protección, para que apagarla sea una salida limpia. Prueba con base en `tests/test_orbit07_m1.py`.
5. **U3 A-H1 + A-H2.** Prueba pura en `tests/test_optimizer_proteccion.py` (`pedidos=None, ancla=D-3, cubierta=True` → `esperar`, abre episodio) y una con base para el hueco.
6. **U7 C-H4.** Depende de U3.
7. **U6a** y luego **U6b.** U6b se enciende por plataforma con `python -m app.cli productos-modo --platform <p> --bid-economia economia_cambio_v2`. El plan debe incluir ese paso.
8. **U8 B-H1.** Cambia la expectativa de `tests/test_orbit07_m2.py:402-438`.
9. **U4 A-H3.** Sin prueba nueva (corre en shadow). Sonda `A_venta_reciente.py` §P5.

## Decisiones del dueño

1. B-H1: al perder la meta de producto, la familia aplica su propio valor en ese mismo ciclo; con ledger atrasado, converge en vez de saltar a 25.5. Recomiendo sí a las dos.
2. C-H1: el freno por un cambio dura 14 días después de madurar (día 31 desde el cambio). Recomiendo sí.
3. C-H4: prohibir subir la puja mientras una pausa espera venta reciente. Recomiendo sí.
4. E-S1: vetar la 2869 hoy y revertir 2321 y 2322 si siguen activos. Recomiendo sí.
5. E-S1: la guarda también bloquea el harvest de un término que ya es keyword EXACT de su ad group de origen. Recomiendo sí.
6. E-H2: ¿«techo aprendido» después de revertir una subida? Recomiendo no.

## Riesgos

- U3 cambia cuándo se abre un episodio de espera. La salida es apagar `ads_proteccion_venta`; por eso U5 va antes.
- U6b: decisiones juzgadas con v1 conservan su veredicto; solo los episodios nuevos usan v2.
- U8 no verifiqué `converge_al_destino(None, ...)` (inferido: cae a plataforma como hoy).
