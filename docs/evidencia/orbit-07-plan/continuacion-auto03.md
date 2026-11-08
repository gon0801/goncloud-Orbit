# ORBIT 07 continúa sobre la entrega de AUTO-03

El dueño pidió que ORBIT 07 sea una continuación del encargo de Orbit en marcha.
Se ajustaron el [plan](../../../plans/orbit-07-motor-decisiones.md) y su descripción en
`plans/manifest.json`. Esta edición no registra ni lanza un encargo, no modifica el plan
que ejecuta AUTO-03 y no cambia código, configuración ni datos de producción.

## Fuente comprobada

Se consultó por SSH de solo lectura el ejecutor de la PC con
`motor director ver enc_07844b614147c266 --base C:/Users/ehven/.motor-encargos`.
El [estado resumido](auto03-estado.json) conserva hora UTC, encargo, fase, PRs y niveles
alcanzados. La consulta fue el 6 de noviembre de 2026 en America/Vancouver.
AUTO-03 figuraba ejecutándose en E, fase 4 de 8; B/C integradas y D desplegada.
Es el estado informado por el ejecutor, sin una nueva comprobación del servidor de Orbit.

Se leyó `C:/dev/goncloud-Orbit/plans/decisiones-producto-01.md` en la PC.
Su SHA256 es `9564c925ba81483e46f042537ce83bc6bb9fb79b83952e7e1e052969e9263e07`.
La copia de trabajo usada para comparar quedó fuera del repositorio, en
`/tmp/orbit07-auto03-continuacion/decisiones-producto-01.md`. La continuación exige
volver a leer el plan y el código integrados cuando reciba AUTO-03; esta captura no
declara terminadas E-I ni congela lo que todavía construye ese encargo.

## Diferencias conservadas y trabajo heredado

| Fuente AUTO-03 | Tratamiento en ORBIT 07 |
|---|---|
| C-F crea meta, resolutor, lector y evaluación por ciclo | M2 extiende esas piezas; elimina la orden de volver a crearlas. |
| C/S2 aplica la primera meta sin ancla propia y limita bajadas posteriores | Reutilizar la primera entrada. M2 conserva como diferencia la aplicación directa de cambios posteriores de margen y el respaldo completo. |
| S3 mantiene el equilibrio de plataforma | M2 incorpora economía compatible por producto cuando los datos la justifican. |
| F.3/S12 conserva el peldaño de producto congelado al revalidar | M2 resuelve economía fresca por el mismo camino del ciclo, sin reescribir la decisión histórica. |
| D/H ya agrega y propone pausas con límites por mercado | M3 debe demostrar el efecto adicional de su episodio o freno. Si el resultado está cubierto, se reutiliza la evidencia y se omite esa construcción. |
| G-I entrega retiro, cola, autorización y reversa | M4 solo cambia la condición pendiente de aprendizaje. Conserva `pause` sobre `product_ad`, `apply_producto` y `retiro_anuncios`; descarta crear `pause_product_ad`. |
| I/S8 reserva al dueño guardar límites y encender la pausa | ORBIT 07 conserva esa decisión. Recibir la entrega en práctica permite continuar las demás mejoras. |
| D.7/S6 exige cobertura conocida y madura | Reutilizar la cobertura válida; un hueco sigue siendo desconocido. No convertir la madurez en una espera global o en un piloto obligatorio. |

M1 mantiene la protección de ventas recientes sin congelar pujas tras el veto.
M5 más M6 mantiene la evaluación económica del último cambio aplicable. Ambas se
contrastan de nuevo con la base recibida antes de construirlas.

## Dependencia y autorización

AUTO-03 termina su construcción, integra sus entregas y cumple los despliegues que
le corresponden. ORBIT 07 recibe ese `master`, la evidencia y los archivos liberados.
La dependencia debe registrarse al encargar la continuación; no existe en el motor
solo porque se haya escrito este plan. Un fallo o pausa de AUTO-03 no transfiere su alcance.

Mientras tanto pueden prepararse lecturas, casos y documentación propios. La construcción
del motor compartido queda secuenciada. La pausa publicitaria puede seguir en práctica
sin bloquear la continuación y sin que este ajuste autorice encenderla.

Se aplicó el principio
[Separate Before Serializing Shared State](/Users/dn/.agents/skills/principle-separate-before-serializing-shared-state/SKILL.md).
La preparación tiene archivos propios y la modificación del motor tiene un único dueño
por entrega. Una rama distinta por sí sola no resuelve el solapamiento funcional.

## Verificación de la edición

La [validación](validacion.json), en `revision_contenido.continuacion_auto03`, registra
hash anterior y final, comparación contra AUTO-03, enlaces y revisión de secretos.
La revisión es documental y proporcional. No se repitió la suite del producto ni la
revisión de cuatro modelos; sus informes originales siguen ligados a sus hashes anteriores.
El validador genérico de planes conserva su incompatibilidad conocida con los encabezados
en español, documentada en [su salida](check-plan-continuacion.txt).
