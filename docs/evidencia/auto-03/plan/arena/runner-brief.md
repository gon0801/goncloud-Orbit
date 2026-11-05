# Brief común para los runners de la arena AUTO-03

Eres un runner de la exploración paralela del skill **architect** (pstack) corriendo dentro de **arena**. Produces UN candidato: el plan de construcción de AUTO-03 para el motor, más su rationale. Trabajas en /Users/dn/dev/goncloud-Orbit, en español (mexicano; harvest/keyword/ad group/product ad/negative no se traducen; sin acentos en código ni en identificadores). No edites ningún archivo fuera de tu carpeta de salida. No implementes código. No corras nada que escriba en la base ni en Amazon.

Regla del repo: `graphify-out/graph.json` existe; corre `graphify query "<pregunta>"` antes de tu primer grep o lectura de fuente y lee archivos crudos solo en líneas ya ubicadas por el grafo o por los documentos de ground.

## Lee primero, completo y en este orden
1. `docs/evidencia/auto-03/plan/arena/frame.md` (artefacto, grounding, rúbrica, tu semilla de forma, rutas de salida).
2. Los documentos de grounding que lista el frame (spec, explicación con gotchas, los tres hallazgos, la lectura de producción, CONTEXTO.md y AGENTS.md).
3. `~/dev/goncloud-motor/skill/motor/SKILL.md` y el docstring de `~/dev/goncloud-motor/motor/plan.py` (formato y validaciones).
4. `~/.claude/skills/architect/references/rationale-template.md` y `~/.claude/skills/architect/references/design-red-flags.md`.

## Disciplina (el orquestador compara candidatos en estos ejes)
- **Uso del implementador primero.** Antes de los bloques, escribe en la rationale cómo lo leerá el agente del motor que implementa un bloque viendo solo ese bloque y los archivos que abre: qué archivos toca, qué firma cambia, qué comando lo acepta. Deriva los bloques de eso.
- **Datos primero.** Las migraciones (tabla de evaluación, kind/CHECK/trigger/cuota, motivo de retiro, procedencia) van antes del código que las usa, y cada una con su `_reversa_`. Si una fila dice "agregaremos un índice/caché después", la estructura está mal.
- **Fidelidad al código.** Cada fila nombra módulos, firmas, tablas y CHECKs vigentes tal como los documentan los ground (archivo:línea). Donde la spec contradice al código (gotchas consolidados), el plan lo resuelve explícito: corrige la spec en una fila `fast` o la obedece y dice por qué.
- **Un dueño, un camino.** Sin segundo encolador ni segundo escritor; sin duplicar colapso/madurez de métricas de producto; una sola fuente de margen; un peldaño nuevo migra TODOS sus consumidores en el mismo bloque.
- **Riesgo creciente y reversa antes de encender (regla 7).** Interruptor propio apagado por omisión; shadow antes de live; la medición de elegibilidad real (cuántos productos/grupos cumplen identidad, ventana, halo y margen) ocurre antes de prometer cobertura. Nada "pide go": las decisiones de política abiertas en la spec se fijan como supuestos explícitos, reversibles y visibles en el plan.
- **Pruebas solo en los 4 casos** de AGENTS.md (dinero, escritura productiva, máquinas, falla real), una regresión demostrada fallando; nada de pruebas de texto, de pruebas ni mutantes. `Acepta` = comando que sale 0 en el SHA entregado. `prueba-real` = comando de solo lectura contra producción (vía `ssh goncloud`, rol de lectura) o contra el repo si el bloque es solo documentos.
- **Formato del motor, exacto.** Encabezado `# título`, `autopiloto: motor`, `plan: decisiones-producto-01`, `repo: git@github.com:gon0801/goncloud-Orbit.git`. Bloques `## Bloque X — …` con `lane:` (`fast` documentos, `gate` código, `release` código que se despliega), `depende:`, `aviso:` (frase llana para David), `prueba-real:`, tabla `| Fila | Contenido | Acepta |`. Un bloque `release` lleva `despliegue: desplegar=<ruta> checklist=<ruta> rollback=<ruta>` apuntando a scripts que una fila anterior del plan crea en el repo (Orbit no tiene receta por omisión; la manual está en `docs/DEPLOY.md`). Sin `implementa:`/`revisa:` (David no pidió modelos). Bloques humanos solo si de verdad hacen falta y solo `login|2fa|captcha|pago`.
- **Valida antes de entregar:** `cd ~/dev/goncloud-motor && python3 -m motor valida <ruta a tu plan.md> --home ~/.motor` debe decir que está listo. Pega su salida al final de tu rationale.
- **Carga del lector.** ≤ ~250 líneas de plan; bloques del tamaño de un PR revisable; archivos exactos; cero narrativa. Respeta `MAX_LINEAS_MODULO=900` y la allowlist (`apply_cola.py` ya está en 1345 con nota "partir la próxima vez que se toque en grande").
- **Banderas rojas.** Revisa tu candidato contra `design-red-flags.md` (módulo superficial, fuga de información, descomposición temporal, pass-through, dueño partido, dos caminos, internals importables, lista sincronizada a mano) y anótalo en la rationale.

## Rationale
Sigue `rationale-template.md` con sus secciones (Problem, Usage, Shape, Synthesis decision [déjala "la llena el lead"], Tradeoffs accepted, Alternatives considered, Open questions and risks, Next implementation step). En "Alternatives considered" nombra al menos la otra semilla de forma del frame y por qué perdió o ganó para ti. Anota también qué correcciones a la spec propone tu plan (lista corta).

Eres uno de dos runners con modelos distintos. Produce el mejor plan que tu modelo pueda; no te cubras contra el otro. Las diferencias son la señal para elegir base e injertar.
