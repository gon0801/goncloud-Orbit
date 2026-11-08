# Ajuste del plan después de la revisión cruzada

El dueño pidió incorporar las conclusiones de la revisión con Grok, Kimi, Argon y Fable.
Se ajustó el [plan ORBIT 07](../../../plans/orbit-07-motor-decisiones.md) y su descripción
en el manifest. La construcción y la activación siguen pendientes.

La [revisión cruzada](cross-review/README.md) examinó el plan con SHA256
`9818ffc135d3871c8b6b908cbc98e9d0fcc796081c85bafc77dbca29a03d1a78`.
Sus informes y reproducciones se conservan sin cambios. El hash del plan ajustado y las
comprobaciones de esta edición están en [validacion.json](validacion.json), bajo
`revision_contenido.ajuste_post_cross_review`.

| Conclusión aceptada | Ajuste del plan | Comprobación exigida al implementar |
|---|---|---|
| R1. Economía distinta al decidir y liberar | M2 incluye `apply_cola.py` y un resolutor compartido. La revalidación usa datos frescos y conserva los inputs históricos. | Recorrer decisión, veto y liberación con meta individual del 4 % y general del 20 %. Comprobar también cambios legítimos de elegibilidad. |
| R2. Espera que congela la hoja | M1 permite evaluar pujas después del veto original. La protección conserva una sola pausa pendiente, su ancla y su vencimiento. Los vetos del dueño y las aplicaciones en curso siguen bloqueando. | Comprobar evaluación de pujas, ausencia de cuota por espera y de pausas duplicadas, precedencia económica y vencimiento intacto. |
| R3. Meta aplicada diferente del objetivo nuevo | M2 aplica directamente la meta del producto desde el primer ciclo elegible. El caso de 25 % previo y 4 % calculado aplica 4 %. La puja conserva sus límites vigentes. | Cubrir activación, cambio de margen, pérdida y recuperación de elegibilidad. Verificar respaldo completo e historial propio por peldaño. |
| Nota 1. M3 puede no añadir un freno efectivo | M0 exige identificar la decisión que cambia. M3 se entrega con M4 salvo que tenga efecto propio. `politica.decide` conserva la decisión del freno. | Mostrar un antes y después de la decisión bajo la regla acordada. No ampliar la regla ni esperar datos futuros para justificar otra entrega. |
| Nota 2. Subidas anteriores a D+17 | M5 distingue comparabilidad histórica de aplicabilidad actual. M6 usa el episodio maduro del último cambio cuyas condiciones sigan vigentes. | Comprobar repetición antes de D+17, último cambio maduro y cambio posterior que deja el episodio anterior como historia. |
| Nota 3. Reutilización del colapso existente | M0 y M3 nombran `evaluacion_catalogo.py` y sus pruebas. La consolidación de campañas conserva las pruebas de ingesta. | Comprobar colapso dentro de cada identidad y madurez por fecha antes de agregar por producto. |

El programa tiene cuatro entregas funcionales por defecto, o cinco si M3 acredita efecto
propio. M5 más M6 no depende del retiro M4 cuando M3 solo prepara datos para ese retiro.
Se mantienen el veto original, los pasos de puja, la madurez por fuente y la activación tras
verificación técnica. No se añaden pilotos, cohortes, pruebas comerciales largas ni una
espera global de 17 días.

Los detalles ya exigidos por el plan, incluidos permisos, reversa, autoría e historia,
conservan su alcance. Las observaciones descartadas no se incorporaron como obligaciones
nuevas. Se aclaró que un segundo pedido inmaduro no renueva un episodio consumido, sin
cambiar la regla de vencimiento fijo.

La comprobación de esta edición es una autorrevisión del diff, las dependencias, los
criterios de aceptación y los enlaces, junto con el validador de planes y `pre-commit`.
No se repitió la revisión de cuatro modelos ni se presenta aquella revisión como aprobación
de este texto nuevo. La evidencia del comportamiento actual sigue siendo válida porque
no cambió código de producto. No se ejecutó otra suite de producto para esta edición documental.

Se aplicó [receiving-code-review](/Users/dn/.codex/plugins/cache/openai-curated-remote/superpowers/7.0.1/skills/receiving-code-review/SKILL.md)
para incorporar el dictamen ya contrastado y conservar los descartes. La documentación
sigue poteto-mode, `technical-writing` y `unslop`.
