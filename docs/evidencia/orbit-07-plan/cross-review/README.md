# Revisión cruzada del plan ORBIT 07

El objetivo es mejorar las decisiones del motor y activar mejoras comprobadas sin pilotos
largos. Se revisó el plan antes de implementar con Grok, Kimi, Argon y Fable.

**Dictamen del coordinador. Ajustar el plan antes de implementar.** Hay una incompatibilidad
concreta en la revalidación de M2 y dos decisiones monetarias que conviene cerrar en el texto.
No se modificó el plan ni código de producto. Tampoco se consultó producción, se escribió
en Amazon o se inició una activación. Los informes originales son opiniones asesoras;
la clasificación de este documento prevalece sobre sus etiquetas de severidad.

## Versión y panel

- Base `5adf1be0bb8bdd84d46c7b7e8588b66daeea6a99`.
- Plan `plans/orbit-07-motor-decisiones.md`.
- SHA256 del plan `9818ffc135d3871c8b6b908cbc98e9d0fcc796081c85bafc77dbca29a03d1a78`.
- Fecha local de la revisión, 6 de noviembre de 2026. Los registros usan UTC del día 7.
- Cuatro copias aisladas de la misma base, el mismo plan y el [mismo encargo](brief.txt).

| Revisor | Modelo y herramienta utilizados | Informe |
|---|---|---|
| Grok | Cursor, `grok-4.7-xhigh`; cierre de la misma sesión con `grok-4.7-high-fast` | [Grok](grok.md) |
| Kimi | Cliente Kimi, `kimi-code/k3.1-pro` | [Kimi](kimi.md) |
| Argon | OpenCode, `opencode-go/gemini-4-argon` | [Argon](argon.md) |
| Fable | Claude Code, alias `fable`, respuesta identificada como `claude-fable-5-1`, esfuerzo xhigh | [Fable](fable.md) |

Cursor rechazó inicialmente Kimi, Argon y Fable por créditos fuera del uso semanal. Se
completaron con clientes locales configurados, sin comprar créditos ni cambiar cuentas.
La primera ejecución de Grok agotó 1200 segundos. Se retomó su sesión para cerrar con la
evidencia ya obtenida, sin mostrarle los otros informes. Los cuatro entregaron dictamen.
Los detalles y códigos de salida están en [ejecucion.json](ejecucion.json).

## Cambios recomendados antes de implementar

### R1. Llevar la economía del producto hasta la revalidación de la cola

**Alta. Origen Kimi 1.** M2 cambia la economía usada al decidir. Su lista de archivos y su
recorrido no incluyen la resolución económica de la pausa después del veto.
`app/apply_cola.py:618` obtiene la meta desde `notes.target` del ciclo y la cascada general.
`_revalida_pause` vuelve a decidir la pausa con esa meta en `app/apply_cola.py:800`.

La sonda ejecutada sobre el motor actual usa gasto de 5000 MXN, ventas de 10000 MXN, un pedido
y 120 clics. Con meta de producto del 4 % produce `pause_economica`. Con meta general del
20 % deja de calificar como pausa. Son datos sintéticos, no una pérdida observada.

**Impacto.** Si M2 cambia solo el ciclo y deja esa revalidación intacta, una pausa propuesta
con la economía individual puede descartarse con otra referencia económica. Es una
incompatibilidad entre dos caminos existentes, no una afirmación de que M2 ya esté construida.

**Ajuste mínimo.** Incluir `app/apply_cola.py` y su recorrido en M2. Decisión y revalidación
deben resolver la economía efectiva por producto con el mismo contrato y datos frescos,
incluidos goal explícito, identidad, atribución, moneda y fallback completo. Los insumos de
la decisión original permanecen congelados para replay.

**Comprobación.** Repetir el caso con metas distintas en decisión, veto y liberación, además
de un cambio legítimo de elegibilidad durante el veto. La futura prueba debe demostrar la
regresión contra la versión anterior. La revisión actual ejecutó esta sonda sin base ni red.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests .venv/bin/python docs/evidencia/orbit-07-plan/cross-review/sondas/kimi-h1.py
```

[Salida repetida por el coordinador](sondas/kimi-h1-verificado.txt).

### R2. Definir qué puede hacer una hoja mientras su pausa está protegida

**Media. Origen Argon 5 y Fable 3.** El plan prohíbe cobrar cuota, entrar en `applying` y
reiniciar el veto durante la espera de M1. Falta hacer explícito su efecto sobre las pujas.
Actualmente una fila `released` mantiene bloqueada la clave `entity_cut` y
`cycle._procesa_decisora` salta toda la hoja antes de armar su caso.

Una ejecución directa de esa función, con entidad y ancestros habilitados y una clave
bloqueada, produjo `veto_pendiente: 1`, cero casos evaluados y cero decisiones pendientes.
La sonda usa un objeto que rechaza cualquier consulta de base. Se comprobó también el filtro
de estados en `app/apply_cola.py:140`.

**Impacto.** Una implementación que conserve la espera en `released` hereda la congelación
de pujas de esa hoja. No se demostró que durante esa espera hubiera una puja distinta y
beneficiosa que ejecutar. Sí quedó comprobada la consecuencia operativa que debe elegirse.

**Ajuste recomendado.** Expresar que la protección bloquea la pausa por cero ventas, mientras
las demás decisiones autorizadas siguen bajo el mismo evaluador. Mantener el ancla y el veto
originales y registrar la espera con un motivo válido. Si se elige congelar la hoja completa,
declararlo como decisión monetaria explícita. No adoptar sin más la propuesta de descartar y
reencolar de los revisores, porque puede introducir otras 48 horas de veto y contradice M1.

**Comprobación.** Un recorrido local debe mostrar qué ocurre con la puja durante la espera,
que no se duplica la pausa, que no se consume cuota y que el vencimiento original no cambia.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests .venv/bin/python docs/evidencia/orbit-07-plan/cross-review/sondas/verifica_espera_cola.py
```

[Salida del coordinador](sondas/verifica_espera_cola.py.txt).

### R3. Separar la meta calculada de la meta aplicada al activar M2

**Media. Origen Grok 1, Fable 4 y una parte de Argon 1.** El plan ya elimina el piso del
10 % para el producto y exige mover juntos meta y equilibrio. Esos cambios están cubiertos.
La cuestión pendiente es si el producto conserva la bajada máxima de 0.5 puntos por ciclo
del resolutor actual y qué ocurre al perder elegibilidad.

La sonda obtuvo derivado 4 % con margen 8 % y fracción 0.5. Con un aplicado previo de 25 %,
el resolutor vigente entrega 24.5 %. Quitar solo el piso no elimina esa rampa. Fable obtuvo
20.22 % desde 20.72 %. El contrato actual está documentado en `docs/CONTEXTO.md:336`.

**Impacto.** Dos implementaciones pueden cumplir la fórmula del plan y aplicar metas muy
distintas en el primer ciclo. La rampa de 0.5 puntos supone 34 ciclos para ir de 20.72 a 4;
es una cuenta del límite vigente, no una duración observada ni una necesidad de prueba.

**Ajuste recomendado para la preferencia del dueño.** Declarar que la economía individual
resuelta se utiliza desde la activación, con los límites vigentes de cambio de puja. Documentar
la excepción a la rampa de la meta y el retorno a la economía completa de respaldo cuando
se pierda elegibilidad. No mezclar una meta individual en transición con un margen incompatible.
La revisión no activa esa elección ni altera los pasos de puja.

**Comprobación.** Cubrir entrada desde una meta previa mayor, cambio de margen, pérdida y
recuperación de elegibilidad. Verificar meta aplicada, equilibrio, puja resultante y replay.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests .venv/bin/python docs/evidencia/orbit-07-plan/cross-review/sondas/grok-sonda.py
```

[Salida repetida por el coordinador](sondas/grok-sonda.py.txt).

## Notas que deben resolverse con M0 o precisarse en la ejecución

1. **M3 debe tener un efecto propio para entregarse sola.** Fable comprobó 1344 combinaciones
   con cero pedidos propios y ninguna subida ordinaria. El resultado se reprodujo. No demuestra
   que M3 sea inútil en general, porque el plan delega a M0 el tratamiento de ventas y M4 añade
   su condición de cero ventas por separado. Si M0 limita el aprendizaje a cero pedidos en el
   mismo ámbito y ventana, unir M3 con M4 evita una entrega sin efecto. Si lo aplica también
   a productos con ventas, presentar un caso monetario en el que el bloqueo cambie la decisión.
   Nombrar `politica.py` y sus pruebas en M3 es una aclaración útil, no un segundo bloqueo.
2. **M6 tiene cobertura limitada para subidas frecuentes.** Grok demostró que a D+8 la regla
   vigente puede repetir una subida, mientras el post económico de siete días madura en D+17.
   El plan ya permite fallback mientras faltan datos. No está probado que M6 falle en todas
   las hojas. Definir qué cambios dentro de la ventana invalidan un episodio y cuáles posteriores
   solo limitan su uso para una nueva decisión. Mostrar el caso de subidas sucesivas en M0.
   No prometer que el freno económico detiene una subida anterior a tener sus datos, ni añadir
   una espera general de 17 días para resolver esa limitación.
3. **Reutilización concreta.** Añadir `app/evaluacion_catalogo.py` y los casos de
   `tests/test_evaluacion_catalogo.py` al mapa de M0/M3. Allí están el colapso de reobservaciones
   y la madurez por fecha. Las pruebas de ingesta citadas siguen sirviendo para consolidar
   campañas y evitar duplicados. El coordinador ejecutó los tres casos pertinentes, todos
   pasaron. El colapso existente es por identidad; no debe aplicarse por fecha sobre varios
   productos o identidades juntos.
4. **Detalles de implementación ya exigidos.** Conservar como referencias los permisos mínimos
   de M3/M4, el transporte protegido por el dueño del ciclo, el cierre de intenciones, los
   consumidores de retiros, las migraciones de enums, el freeze versionado y la rama de
   `v_cambio_bid` que usa estado actual. El plan ya exige permisos, autoría, reversa, historia
   y no contaminar el pasado. No cuentan como siete fallos adicionales por no estar construidos.

## Clasificación de cada observación

| Revisor y observación | Resolución del coordinador |
|---|---|
| Grok 1, rampa de meta | R3. Se conserva la indefinición de meta aplicada; se excluye como fallo el piso que M2 ya manda quitar. |
| Grok 2, D+8 frente a D+17 | Nota 2. Fechas y veredicto reproducidos; la conclusión universal depende del alcance de comparabilidad, que no quedó demostrado. |
| Kimi 1, meta en revalidación | R1. Aceptado con sonda de decisión y lectura del camino ejecutor. |
| Kimi 2, `politica.py` fuera de la lista M3 | Nota 1. La obligación de impedir subidas y tener un solo evaluador ya existe. El código actual sin M3 no prueba que el plan incumpla esa obligación. |
| Argon 1, piso, equilibrio y convergencia | Solo la transición queda en R3. Piso y equilibrio están expresamente corregidos en M2. No ampliar este trabajo a reparar toda la economía familiar. |
| Argon 2, permisos y escritor | Nota 4. M4 ya exige ampliar permisos y conservar dueño, claim y escritor. No se reprodujo `InsufficientPrivilege` sobre una implementación ni se acepta conceder permisos amplios sin diseñar el acceso mínimo. |
| Argon 3, atribución por anuncio | Excluido como defecto verificado. El comando publicado falla por firma incorrecta. Además conserva `ad_ids` y el ejemplo con ventas no satisface la condición de cero ventas de M4. M0 ya debe acreditar el alcance. |
| Argon 4, freeze y replay | Nota 4. Versionar el caso y preservar eras ya es una obligación explícita. Añadir una clave a la versión antigua y ver su rechazo no demuestra una contradicción del plan. |
| Argon 5, cola bloquea hoja | R2, junto con Fable 3. La congelación se comprobó; la eventual pérdida económica no. |
| Argon 6, esquema y reversa | Nota 4. El plan ya exige adaptar consumidores y prohíbe recrear anuncios archivados. La transacción concreta de una migración futura no existe para probar que vaya a fallar. |
| Argon 7, historia y consultas M5 | Nota 4. La fuente tiene límites reales, pero M5 ya exige punto en el tiempo, ventas maduras y comprobar que añadir lecturas no cambia decisiones. |
| Fable 1, M3 sin efecto | Nota 1, condicionada a cómo M0 defina aprendizaje y ventanas. M4 no define por sí sola el cierre de M3. |
| Fable 2, segundo pedido reciente | No conservado como contradicción. M1 fija el ancla, prohíbe que ventas posteriores extiendan la protección y consume el episodio si el ancla se corrige. Abrir episodios sucesivos, como propone el informe, puede renovar indefinidamente la gracia. Añadir el caso a la especificación puede aclararlo sin cambiar esa regla. |
| Fable 3, espera de cola | R2. Se excluye reiniciar el veto como arreglo automático. |
| Fable 4, rampa | R3. No se impone una rampa larga por defecto. |
| Fable 5, módulo de colapso | Nota 3. Se comprobaron los tests correctos; no se descartan las pruebas de ingesta que cubren otro nivel. |
| Fable 6, tocar `eras.py` | No conservado como defecto. Cambiar un import hacia una implementación histórica congelada puede ser necesario para preservar su comportamiento. La obligación es conservar el resultado, no inmovilizar el archivo. |

Grok presentó dos observaciones, Kimi dos, Argon siete y Fable seis. Se consolidaron en R1,
R2 y R3 las partes sostenidas por evidencia. Las demás quedaron como notas condicionadas,
detalles ya cubiertos o descartes con motivo. No se suman etiquetas de los revisores para
dar una cantidad artificial de bloqueos.

## Verificación y límites

Se repitieron las dos sondas de Kimi, las siete publicadas por Argon, tres de Fable y la
sonda completa exitosa de Grok. Se añadieron dos sondas pequeñas del coordinador para la
meta de la cola y el salto de la hoja bloqueada. Las salidas están en [sondas](sondas).
El comando Argon 3 terminó con `TypeError`; se conserva la salida como descarte. No se
modificó silenciosamente su reproducción para presentarla como un pase.

Fable ejecutó 76 pruebas existentes de política y caso; Kimi ejecutó tres casos focalizados.
El coordinador reutilizó esa evidencia y ejecutó tres pruebas existentes de colapso y madurez.
No se añadieron pruebas de producto, no se ejecutó una suite PostgreSQL completa ni se afirma
que la implementación futura esté validada. La revisión de secretos y el chequeo de cambios
de esta entrega se registran en [validacion-host.json](validacion-host.json).

Se aplicó [interrogate](/Users/dn/.agents/skills/interrogate/SKILL.md) para confrontar el
plan y reportar sin aplicar fixes. [Prove It Works](/Users/dn/.agents/skills/principle-prove-it-works/SKILL.md)
determinó repetir sondas y descartar la reproducción fallida.
[Separate Before Serializing Shared State](/Users/dn/.agents/skills/principle-separate-before-serializing-shared-state/SKILL.md)
determinó usar copias independientes. Ninguna recomendación exige un piloto largo,
semanas de simulación o resultados estadísticos futuros como condición de activación.
