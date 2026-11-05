# Encargo común de los lectores frescos del plan AUTO-03

Eres un lector fresco del plan `plans/decisiones-producto-01.md`. Lo va a ejecutar el **motor**: agentes sin contexto, un bloque a la vez, que no pueden preguntarle nada a nadie. Escribe en español. Los términos de dominio harvest, keyword, ad group, product ad y negative no se traducen.

## Qué leer, en orden

Lee todo desde el worktree limpio en master: `/Users/dn/dev/orbit-lectores-auto03` (detached en `origin/master` `ff28eb33`). No leas del checkout `/Users/dn/dev/goncloud-Orbit`, que está en otra rama.

1. `plans/decisiones-producto-01.md` completo. Es lo que verificas.
2. Cómo lo ejecuta el motor: `~/dev/goncloud-motor/skill/motor/SKILL.md`, el docstring de `~/dev/goncloud-motor/motor/plan.py` y `~/dev/goncloud-motor/motor/effects.py` líneas 455-500 (Acepta) y 640-760 (despliegue y prueba-real). Datos medidos:
   - Cada `Acepta` corre con `bash -c`, en una copia limpia del repo en el SHA entregado (`git worktree add --detach`, sin `.venv`), con el entorno de la máquina más `[pruebas.env]` de `.motor.toml` (`ORBIT_TEST_DSN`).
   - La `prueba-real` corre en otra copia limpia del commit ya mergeado, con `MOTOR_RAIZ` igual a esa copia y un tope de 1800 s.
   - Un bloque `release` corre `bash <desplegar>` y `bash <checklist>` en la copia con `MOTOR_RAIZ` y `MOTOR_SHA`. Si falla, corre `bash <rollback>`.
3. Contexto de diseño, solo para consultar: `docs/evidencia/auto-03/plan/sintesis.md` y `docs/evidencia/auto-03/plan/ground/00-explicacion.md`. Antes de reportar algo, revisa la sección "Correcciones por la revisión del PR #531" de la síntesis: esos hallazgos ya se arreglaron. No los repitas.
4. El código de Orbit en el mismo worktree. Regla del repo: si existe `graphify-out/graph.json`, corre `graphify query "<pregunta>"` antes de tu primer grep, y lee archivos crudos solo en líneas ya ubicadas.

## Qué puedes ejecutar y qué no

- **Sí:** leer y buscar. Correr `cd ~/dev/goncloud-motor && python3 -m motor valida <ruta> --home ~/.motor`. Correr en el worktree de lectores cualquier comando que no escriba fuera de `/tmp` ni de tu scratchpad, incluidas las pruebas que ya existen (`PYTHONPATH=. .venv/bin/python -m pytest -q <archivo> -k <filtro>`). Copiar el worktree a un directorio temporal para ensayar un `Acepta`. En producción, solo lectura: `ssh goncloud` con comandos que no cambien nada, y SQL con el rol `ORBIT_DSN_READ` dentro de `BEGIN READ ONLY` y `SET LOCAL statement_timeout='15s'`, con el patrón `ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -v ON_ERROR_STOP=1' <<'SQL' ... SQL`.
- **No:** editar archivos del repo, commit, push, PR, merge, borrar, escribir en la base o en Amazon, reiniciar o desplegar, `motor entrega`. Nunca imprimas valores de credenciales ni de DSN.

## Las cuatro fallas que siempre aparecen

Búscalas con estas palabras:

1. **La comprobación decorativa.** Una compuerta que no puede salir del otro lado: compara algo consigo mismo, se cumple por construcción, o espera un valor que el comando nunca produce. Fuerza el caso de falla de cada `Acepta` y `prueba-real` y mira si de verdad falla. Cuenta como decorativa solo si **ninguna otra compuerta del mismo bloque** (otra fila o su `prueba-real`) atrapa la falta del comportamiento. Una sintaxis válida, un archivo presente o un grep del texto que la misma fila escribe no prueban comportamiento.
2. **El hecho leído de la rama equivocada.** Cualquier archivo, línea, firma, tabla, columna o constante que el plan cite como existente: verifícalo tú en `origin/master`.
3. **La herramienta que no está.** Cada comando que el plan manda correr, con esos valores exactos: binarios, rutas, subcomandos, flags, nombres de pruebas y filtros `-k`. Incluye lo que exista en la copia limpia que usa el motor (sin `.venv`). Si un comando depende de algo que crea otra fila u otro bloque, comprueba que esa fila u ese bloque va antes según `depende:`.
4. **El mecanismo sin comando.** Cualquier frase que nombre un mecanismo sin invocación, parámetros, salida esperada y qué hacer si falla.

## Formato de salida

- Lista numerada por gravedad: primero **bloqueante** (haría fallar o hacer daño al ejecutor, siempre con el comando que lo prueba y su salida real) y después **no bloqueante**.
- Cada punto dice el hallazgo en una frase, la fila o el bloque, la evidencia (el comando que corriste y lo que salió) y **la corrección pegable**: el texto exacto que lo arregla.
- Al final, una sección corta **"Verificado y correcto"**: lo que comprobaste y está bien, una línea cada cosa, para que nadie lo vuelva a gastar.
- Si algo está bien, no lo menciones fuera de esa sección. Sin introducción ni resumen.
