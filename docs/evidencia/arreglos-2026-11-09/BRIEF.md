# Brief: diseño de arreglos de Orbit (un candidato por diseñador)

## Para qué
El dueño de Orbit pidió revisar que la app completa no tenga fallas peligrosas de dinero ni del
funcionamiento del motor. La revisión dejó 4 fallas altas y varias medias. Ahora pide **diseñar**
el arreglo de todas. **No se implementa nada.** Tu entrega es un diseño que después se convierte
en un plan por fases que implementa otro agente.

## Tu parte
Eres un candidato de diseño para UN grupo de hallazgos (te lo dice quien te lanzó). Otro diseñador
trabaja el mismo grupo por separado. No busques su trabajo ni escribas fuera de tu carpeta.

1. Lee `/Users/dn/.claude/skills/architect/SKILL.md` y `/Users/dn/.claude/skills/architect/references/runner-prompt.md`,
   `rationale-template.md` y `design-red-flags.md` de esa misma carpeta `references/`. Esa es la disciplina.
2. Lee tu grupo en `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/arreglos-diseno/HALLAZGOS.md` y, por cada hallazgo, su sección en el reporte de origen y su sonda.
3. Lee `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit-master/docs/CONTEXTO.md` (reglas de diseño innegociables) y el plan que gobierna
   la zona que tocas en `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit-master/plans/` (por ejemplo `orbit-07-motor-decisiones.md`, `bids-02.md`,
   `ingesta-01.md`, los de repricing). Un arreglo no puede contradecir la letra de un plan sin decirlo.
4. Lee el código real en `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit-master` (master `3c841f4`). Si en esa carpeta existe
   `graphify-out/graph.json`, corre `graphify query "<pregunta>"` antes de tu primer grep; si no existe, usa `rg`.
5. Escribe tu candidato en tu carpeta de salida: un solo archivo `DISENO.md`.

## Qué debe traer DISENO.md
Por cada hallazgo del grupo, una sección con su ID:
- **Causa raíz.** El primer lugar donde el sistema se equivoca, con `archivo:línea` que tú leíste.
- **Uso primero.** Cómo se ve desde quien llama o desde el operador una vez arreglado (2 o 3 sitios reales de llamada).
- **Forma.** Tipos, firmas y límites de módulo en Python, con cuerpos `raise NotImplementedError` y pseudocódigo
  donde la lógica es delicada. Migración SQL esbozada si cambia datos. Di qué invariante queda en la estructura
  (tipo, CHECK, llave) y cuál queda en validación de borde.
- **Datos que ya existen.** Qué pasa con las filas de producción ya escritas (por ejemplo los negatives aplicados
  en septiembre, los caps actuales, la cola en `pending_veto`). Si hace falta una reparación de datos, descríbela
  como paso aparte; escribir en producción lo ejecuta otro agente con plan.
- **Cambio de comportamiento en vivo.** Si el arreglo cambia lo que el motor escribe a Amazon/MeLi con dinero real,
  dilo y di cómo se enciende (interruptor existente, shadow primero, o directo) y cómo se deshace.
- **Prueba.** UNA comprobación que falla hoy y pasa con el arreglo. Regla del repo: pruebas nuevas solo en 4 casos
  (dinero; escribir datos de producción; instalar/borrar/deshacer en máquinas; una falla que ya pasó en producción).
  Fuera de esos basta comprobar que corre. Nada de baterías ni mutantes. Las pruebas del repo dan fallas falsas en
  esta Mac, así que no las corras; las sondas puras sí puedes correrlas.
- **Alternativas.** Al menos una forma estructuralmente distinta y por qué pierde.
- **Tamaño.** Archivos que toca y si cabe en una sola unidad verificable.

Al final: orden propuesto de las unidades (cuál primero y por qué), dependencias entre ellas, decisiones que solo
el dueño puede tomar (formuladas como pregunta, con tu recomendación) y riesgos.

## Reglas
- Diseña el cambio más chico que cierra la causa raíz. Nada de capas nuevas, opciones especulativas ni una segunda
  forma de hacer algo que el repo ya hace. Si la causa compartida de dos hallazgos es una, arréglala una vez.
- Un candado aguas abajo solo es arreglo cuando el estado que atiende es legítimo.
- Idempotencia: di qué pasa si la operación corre dos veces o se cae a la mitad.
- Solo lectura: no edites `/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit-master` ni `/Users/dn/dev/goncloud-Orbit`. No cambies de rama. No hagas commit.
  No te conectes a producción (`ssh goncloud` prohibido); la evidencia de producción ya está en HALLAZGOS.md.
- Escribe solo dentro de tu carpeta de salida.
- DISENO.md en español. Código, nombres de archivo, claves y términos de dominio (harvest, keyword, ad group,
  negative, bid) sin traducir.
- No pidas revisión a otro modelo. No lances subagentes.
- Sé concreto. Cada afirmación sobre el código lleva `archivo:línea`. Marca como "inferido" lo que no leíste.

Tu respuesta final a quien te lanzó: 10 líneas como máximo con la ruta de DISENO.md, las decisiones de dueño
abiertas y cualquier hallazgo cuya causa raíz resultó distinta a la descrita.
