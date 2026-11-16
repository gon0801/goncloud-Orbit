# ARREGLOS 03 — Contrato vigente de datos y precios

Ampliación documental del 2026-11-15 sobre el review revalidado `ff03ed61f7a847ab190b1695c2efe8d785baa364`. El estado observado no certifica otro commit: BASE rebasa sobre master actualizado antes de implementar y fija firmas, esquema y evidencia. Esta edición no implementa producto ni ejecuta pruebas, red, producción o despliegues.

## Autoridad, orden y alcance

`plans/arreglos-03.md` manda. Este documento es el punto de entrada: refuerza D-G y define D-M, D-N y P-G. `DISENO-datos.md` y `DISENO-precios.md` conservan el resto del diseño y sus decisiones; donde su conteo u orden no incluya la ampliación, prevalecen el plan y este documento. No se reabren D-D ni P-E ni variantes sin existencias descartadas.
El plan tiene **20 bloques**: BASE, 13 D (A-C y E-N) y 6 P (A-D, F-G). Primero BASE; D-M tras D-L, D-N tras D-C/D-J, P-G tras P-F. D-G conserva D-E y se integra por contenido con D-F, que toca el mismo planificador.
La cola conservadora es **03 -> 04 -> TEMPORADA01 -> 05**, especificada en [ORDEN-IMPLEMENTACION.md](../../../plans/ORDEN-IMPLEMENTACION.md). Evita escritores simultáneos en cli/ui/notifica; no impone dependencia total entre planes. Las dependencias concretas de cada bloque siguen mandando. Antes de cada implementación, rebase y comprobación del contrato integrado; un cambio de firma se documenta y adapta, no crea otro camino.
No cambian decisiones de negocio, modos, permisos ni umbrales. Precios global shadow sigue dominando universos live. Cotización íntegra no autoriza levantar shadow ni cambiar tratamiento de impuestos. 05 conserva conciliación fee/impuesto y solo verificará los hallazgos TN de este plan.

## Matriz única y aceptación común

Fuentes versionadas: `docs/evidencia/arreglos-05/revision/{informe.txt,hallazgos.json,matriz.tsv,criterio-cierre.txt,reproducir.txt,test_accept_ingest.py,test_accept_pricing.py}`; el legado se conserva en `docs/evidencia/arreglos-2026-11-12/`. Los candidatos A/B aportan diseño, no evidencia dinámica nueva. Ninguna ruta temporal es interfaz del ejecutor.

| Hallazgo y efecto | Owner | Contrato y comprobación |
| --- | --- | --- |
| TN05: un término que vendió queda con subtotal como completo | D-G | Invalidación permanente por clave durante el reporte; `test_05` |
| TN06: catálogo parcial desactiva ausentes | D-M | Solo cobertura completa autoriza bajas; `test_06` |
| TN14: rollback de item cuenta escrituras inexistentes | D-M | Delta tras savepoint y publicación tras commit; `test_14` |
| TNS1: error de proveedor puede persistir un secreto registrado | D-N | Redacción antes del límite durable; `test_S1` |
| TN07: fresca/cache pierden fecha y desglose | P-G | Cotización preserva evidencia original; `test_07` en sus dos variantes |

Cinco hallazgos, seis casos portados. El [contrato común ACEPTACION](../arreglos-05/ACEPTACION.md) define entorno, rojo/verde, evidencia por SHA, fixtures y cierre global. BASE exige su integración junto al review y al orden antes de ejecutar. Los comandos de este documento se corren desde raíz con su entorno: Python3.12/PG16 desechable, DSN de prueba, `PYTHONPATH=.:tests:docs/evidencia/arreglos-05/revision` y variables ORBIT_REVIEW del checkout. Las guardas del runner deben aceptar esa base; no se relajan para apuntar a producción. Corridas seriales; cero skips, xfail, errores de colección/fixture o filtros sin casos.
Reusar evidencia que siga válida. Una nueva regresión demuestra el fallo contra el código previo aplicable; TN parte de `ff03ed61`, mientras los reproductores históricos citan `a4644aad`. Si un caso ya pasa tras el rebase, es control resuelto y no trabajo nuevo. Elegir comprobaciones por riesgo y evidencia faltante, sin prohibiciones por categoría.

## D-G — Un constituyente corrupto invalida el total (TN05)

Owner existente: `app/ads/reports.py`, `_planea_filas_terminos`. Este apartado refuerza ST de `DISENO-datos.md`; no crea otro agregador ni mueve TN05 a 05.
Uso: `escribir_grupo` recibe el reporte, escribe únicamente claves completas y `windows.terminos_cortes` no ve un subtotal utilizable. Una clave válida antes de una fila corrupta se elimina y queda envenenada; una tercera fila no la resucita. Una fila corrupta primero y una válida después producen el mismo resultado.
La clave se resuelve antes de validar métricas. El conjunto `envenenadas` dura todo el reporte y comparte el helper de productos ya existente: `_envenenar_clave_producto` se generaliza una vez. Cada causa conserva su motivo de skip; no convertir dato faltante en pedidos cero, ni limitar la invalidación al último constituyente. Si no puede identificarse la clave de una fila, conservar el rechazo de fuente existente: no atribuirla arbitrariamente.
El planificador no publica `entidad_completa=True` para una clave excluida. Mantener productos, negativos inválidos y madurez aprobada; no cambiar ventanas para conseguir pase. Verificar la persistencia y el consumidor real, además del resultado puro.
Comprobación: `CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "test_05 or test_control"`. Vecinos: órdenes distintos, clave vaciada y recreada, clics/gasto con pedidos ausentes, control válido de productos. Evidencia en D-G/aceptacion-tn.md; 05 referencia ese cierre.

## D-M — Cobertura del catálogo y resultado transaccional (TN06/TN14)

### Uso y frontera de cobertura

`ClienteMeli.items_seller` es dueño de decidir si terminó el scan; catálogo y reputación consumen su certificado. Conservar `(ids, truncado)` evita romper consumidores si expresa la regla completa: `truncado=True` significa cobertura desconocida, no solo alcanzar un número de páginas. No crear un segundo criterio en catálogo.
Cada página exige objeto, contenedor `results` lista e IDs válidos; normalizar IDs sin convertir bool, estructura o vacío en identidad. Contar el conjunto de IDs únicos, no la suma de longitudes. `paging.total`, cuando se usa para certificar, debe ser entero no booleano, no negativo y coherente entre páginas; cantidad única mayor al total o total cambiante implica parcial.
La condición suficiente de completitud es alcanzar exactamente el total válido mediante páginas válidas e IDs únicos. Total cero con contenedor válido vacío puede demostrar catálogo vacío. No hacer otra petición con scroll expirado cuando esa evidencia ya es suficiente. Sin total verificable, ausencia de scroll o página vacía por sí solas no prueban completitud; devolver parcial con los IDs observados. No inventar la semántica de un terminador externo no documentado.
Con páginas por leer: scroll ausente/ilegible, token repetido sin avance, fin prematuro, página malformada o tope agotado producen parcial. No declarar completo solo porque duplicados alcanzaron el total; si tras un solapamiento se logra el total único con avance y datos coherentes, el conteo único es lo que manda. Los errores de transporte conservan su tratamiento seguro y nunca se convierten en lista vacía completa.
`app/meli/catalogo.py` puede conservar observaciones válidas de un scan parcial, pero `_reconcilia_ausentes` solo corre con cobertura completa. Reputación recibe la misma señal; no permitir que la migración de D-B/D-L vuelva a asumir completo ante scroll ausente. No cambia variantes, stock ni modo de precios.

### Forma del delta y frontera de commit

`_aplica_plan` deja de mutar el resultado global y mapas compartidos mientras está dentro del savepoint. Devuelve un delta local de observaciones, miembros, categorías de mapeo y skips de ese item, junto a los cambios de mapas que sobrevivirán. No crear un journal general: el dueño de cada item conoce sus escrituras.
Secuencia: preparar delta local -> abrir savepoint -> observar y aplicar miembros -> salir del savepoint -> fusionar delta y mapas. Un error SQL revierte el item y añade solo el skip de fallo; no deja `observadas`, `existentes` ni contadores de éxito adelantados. El siguiente item parte del mapa confirmado.
La TX exterior escribe filas y sello de corrida juntos. Si revierte, el resultado no se publica como exitoso; no confundir salida del savepoint con commit global. Con items solicitados y todas sus escrituras revertidas, `observadas=0` y `ok=False`; catálogo vacío demostrado no es lo mismo que scan fallido. Mantener las reglas existentes de éxito parcial donde corresponda.
Reintento: misma fuente no causa bajas por ausencia no probada y solo cuenta escrituras del intento confirmado. Métricas conservan append-only; no corregir historia para cuadrar contadores. Rollback de código no habilita volver a desactivar ausentes si no puede interpretar la cobertura nueva.
Comprobación principal: `CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "test_06 or test_14"`. Extender `tests/test_precio02_meli_catalogo.py` y `tests/test_reputacion.py` con duplicados, scroll perdido, total incoherente, completo válido con baja, fallo tras `_observa` seguido de item válido y rollback exterior. Comparar resultado, filas reales y sello, no contadores entre sí.

## D-N — El libro no persiste errores crudos (TNS1)

`Libro._resultado` en `app/jev_libro.py` es el límite durable y aplica `scrub` al código/detalle final antes de escribir y antes de devolver diagnóstico. `app/jev_juicios.py` aplica la misma utilidad a fallos de `_validar_respuesta` antes de salir al resto del sistema. No se agrega otra implementación de redacción ni se guarda cuerpo original para depurar.
Esto cubre respuestas con model distinto y choice inválido que incluyan un secreto ya registrado. Conservar clasificación, duración y demás datos útiles; no convertir todos los errores en un éxito o en cadena vacía. Si se añade contexto, se sanea el mensaje completo que realmente se persiste.
El secreto del caso es ficticio. Se demuestra el riesgo condicional y su cierre, no una filtración real. Usar SQL y libro reales con transporte simulado; revisar diagnóstico devuelto y registro persistido. TN09 ya está cerrado: no mover la guarda ni cambiar quién abre/cierra la transacción.
Comprobación: `CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "test_S1 or test_09"`; revisión dirigida de `tests/test_jev_juicios.py` y del arnés real del libro. Evidencia redactada en D-N/aceptacion.md y revisión de secretos del cambio.

## P-G — Una cotización, fresca o guardada, conserva su evidencia (TN07)

Owner `app/meli/precio_mercado.py`; `app/precio/puerto.py` y `app/precio/tipos.py` ya definen `Cotizacion`, `Importe` y `Fees`. No crear tipos monetarios paralelos ni hacer depender a `corrida` del JSON del proveedor.
Uso: `MercadoMeli.cotizar` obtiene precio, fecha y árbol del cotizador; `cotizacion_guardada` lee esos mismos datos de su fila. Ambos llaman al constructor común ampliado:
```python
def _cotizacion_de_arbol_meli(
    precio: Importe, total: Decimal, detalles: tuple[DetalleFee, ...], estimada_en: datetime,
) -> puerto.Cotizacion:
    """Valida y conserva evidencia original; firma documental."""
    raise NotImplementedError
```
La adaptación verifica suma de componentes, moneda y desglose; asigna `Fees.detalle` completo, no solo variable/fijo. El SELECT de cache incluye la columna real de fecha confirmada en BASE y pasa su valor original; el resultado fresco usa la fecha de su fuente, no otra llamada al reloj. `now()` nunca reemplaza una fecha vieja para aparentar frescura.
Éxito exige evidencia completa y fecha; si falta, devuelve el error de dominio coherente con el puerto y no deja que `_persistir_cotizacion` escriba success. No debilitar esa guarda para hacer pasar TN07. Precio, moneda, oferta e identidad del pedido conservan sus validaciones de cache; un cache de otra oferta no se adapta como si fuera nuevo.
`Fees.impuesto_pendiente` y tratamiento del árbol conservan el contrato vigente de estimación. La sospecha IVA/fee queda en 05: no inferir tasa de importes, no sumar 16%, no eliminar `impuesto_fee_pendiente` al ver tax_amount. No se habilita precios live.
Comprobación: `CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs docs/evidencia/arreglos-05/revision/test_accept_pricing.py -k "test_07 or test_08 or test_D2"`. Ambas variantes 07 deben persistir la fecha y detalle correctos; 08 (unidades reales) y D2 (retención entre días) son controles. Vecinos: suma inválida, fecha ausente, fecha antigua conservada, oferta distinta y error de fuente; tests de cotizador/corrida solo donde aporten evidencia faltante.

## Alternativas, límites y terminado

Se eligen propietarios existentes: lector conoce cobertura, catálogo conoce commit, adaptador conoce cotización y libro conoce persistencia. Un coordinador/journal transversal añadiría traducción y doble estado sin corregir por sí solo ninguna de estas pérdidas; se descarta. A cambio se revisan las fronteras compartidas y se migran todos los consumidores afectados en el mismo bloque.
Sin migraciones nuevas previstas para D-M/D-N/P-G/D-G: usan columnas y registros existentes. Si BASE descubre necesidad real, registrar diseño, SELECT que sustente el invariante y reversa antes de aplicar; no inventar schema por comodidad ni cambiar permisos. Las cinco migraciones históricas del plan conservan sus dueños.
DoD: cinco hallazgos, seis casos verdes sobre un mismo SHA; rojo anterior verificable; controles resueltos intactos; fuentes y consumidores reales; sin secretos ni modos alterados. Aplicar ACEPTACION para Quality/PG16 y revisión global. 05 solo consume la evidencia; queda un owner por hallazgo.
