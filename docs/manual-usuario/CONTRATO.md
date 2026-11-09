# Contrato para escribir páginas del manual de Orbit

Meta del dueño: una persona sin conocimientos técnicos ni contexto aprende a usar TODO Orbit
con un solo PDF. Las hojas existentes en `paginas/` son el modelo de tono, largo y estructura.

Raíz de trabajo: `docs/manual-usuario/` del repo (abajo, `<M>`).

## Qué entregas

1. `<M>/paginas/<tu-archivo>.html`: solo elementos `<section>`, uno por hoja A4.
2. `<M>/notas/<tu-archivo>.md`: por cada afirmación sobre cómo funciona una pantalla, la fuente
   (`archivo:línea` del repo o la URL de producción que leíste). El lead verifica contra esto.
3. Opcional: capturas en `<M>/img/<pantalla>.png` (ver Capturas).

No edites nada fuera de tus archivos. No edites `estilo.css`, `build.py`, `CONTRATO.md` ni las páginas de otros.

## Formato de cada hoja

```html
<section data-pantalla="<id del menú>" data-etiqueta="Panel · Resumen">
  <h1>Título corto que dice qué aprende</h1>
  <p class="lead">Una o dos frases: qué muestra esta pantalla y para qué te sirve.</p>
  ...
  <div class="caja comprobar"><h4>Lo que ya puedes comprobar</h4><p>...</p></div>
</section>
```

- `data-pantalla` va SOLO en la primera hoja de cada pantalla, con el id exacto del menú
  (`app/templates/base.html`, diccionario `paginas`). Las hojas siguientes de la misma pantalla
  llevan solo `data-etiqueta`.
- `data-etiqueta`: «<Bloque> · <Pantalla>» o un subtema; se imprime arriba a la derecha.
- Piezas permitidas (no inventes otras clases):
  - `<ol class="pasos"><li>…</li></ol>` para pasos numerados del ejercicio.
  - `<div class="caja cuenta"><h4>Tu cuenta · consulta del 8 de noviembre</h4><p>…</p></div>` datos reales.
  - `<div class="caja ejemplo"><h4>Ejemplo para aprender</h4><p>…</p></div>` cifras inventadas y redondas.
  - `<div class="caja aviso"><h4>…</h4><p>…</p></div>` (ámbar) SOLO para botones que cambian algo o trampas serias.
  - `<div class="caja comprobar"><h4>Lo que ya puedes comprobar</h4><p>…</p></div>` cierra cada pantalla.
  - `<div class="flujo"><div><b>Paso</b>texto</div><i>→</i><div>…</div></div>` de 2 a 4 cajas.
  - `<table><tr><th>…</th><th>…</th></tr><tr><td>…</td><td>…</td></tr></table>` dos columnas: «Lo que ves» / «Qué significa».
  - `<h2>`, `<h3>`, `<p>`, `<ul>`, `<b>`, `<p class="nota">` (letra chica gris).
  - `<figure><img src="img/<pantalla>.png"><figcaption>…</figcaption></figure>`.
- Una hoja A4 cabe aprox.: h1 + lead + 1 tabla de 6 filas + 3 cajas cortas. Si una pantalla
  necesita más, usa una segunda hoja. Tope: 2 hojas por pantalla salvo que se indique otro.
- Para referirte a otra página usa `{{pag:<id>}}` (ej. «ver página {{pag:cortes}}»). Ids válidos:
  ids del menú más `inicio`, `mirar-o-cambiar`, `rutina`, `consulta`, y cualquier `data-ancla`.
  Para apuntar a una hoja que no es la primera de su pantalla, ponle `data-ancla="<id>-<tema>"`
  (ej. `cortes-rechazar`) y refiérete a ella. NUNCA escribas un número de página a mano:
  `lint.py` lo marca como error.

## Estructura de cada pantalla (en este orden, adaptando)

1. Qué es y cuándo entrar (lead).
2. Cómo llegar y qué mirar primero (pasos 1-4, con los nombres EXACTOS de botones, columnas y
   títulos tal como salen en pantalla, en negritas).
3. Tabla «Lo que ves / Qué significa» con las columnas o etiquetas que más confunden.
4. Un «Ejemplo para aprender» con cifras inventadas.
5. Un «Tu cuenta · consulta del 8 de noviembre» con un dato real (producto, campaña, cifra) que la
   persona pueda encontrar. Si la pantalla está vacía en producción, dilo y explica que vacío es normal.
6. Si hay botones que cambian algo: caja aviso que diga qué cambia, qué pide (Token, Actor, frase)
   y cómo comprobar el resultado. Si la pantalla es solo de lectura, dilo en una frase.
7. «Lo que ya puedes comprobar».

## Cómo escribir

- Español de México, tú. Frases cortas, una idea por frase. Voz activa.
- Para alguien que nunca vio Orbit ni sabe de publicidad. Explica cada palabra técnica la primera
  vez, o remite al glosario de la página {{pag:inicio}}. Palabras que la pantalla muestra en inglés
  (keyword, harvest, negative, target, bid, ACoS, spend, ad group, search term) se dejan en inglés
  tal como aparecen y se explican; no las traduzcas ni las renombres.
- Nada de rutas de código, nombres de tablas, endpoints, JSON, «cron», «API», «SQL», «flag».
  Si algo técnico importa, di qué ve y qué hace la persona.
- Sin guiones largos (—) como conector, sin dos puntos a mitad de frase, sin «crucial», «clave»,
  «robusto», «potente», «asegura», «permite» vago. Di qué pasa, con números si los hay.
- Un dato faltante nunca es cero. Una propuesta no es una acción confirmada. Dilo cuando aplique.
- No prometas resultados («vas a vender más»). Explica lo que la pantalla dice.

## De dónde sacar los hechos

- Lee la plantilla de la pantalla en `app/templates/`, su JS en `app/static/js/`, y la ruta/handler
  en `app/` que arma sus datos (busca la URL en `app/*.py`). Si existe ficha en
  `.cursor/skills/verify-orbit/features/<pantalla>.md`, úsala como guía pero la plantilla manda
  (algunas fichas están viejas).
- Este repo tiene grafo: corre `graphify query "<pregunta>"` antes de tu primer grep o lectura de
  código, y lee archivos solo en las líneas que el grafo o una lectura previa ya ubicó.
- Datos reales: producción responde en `http://10.13.13.1:8010` por VPN. SOLO peticiones GET de
  lectura (`curl -s -m 20 http://10.13.13.1:8010/<ruta>` o `/api/dashboard/<recurso>`).
  PROHIBIDO cualquier POST/PUT/DELETE, enviar formularios, pulsar botones que guardan, lanzan,
  rechazan, regresan o encienden. No pongas tokens en ninguna parte.

## Capturas (opcional, máximo 1 por pantalla)

Solo si ayuda a ubicar la pantalla. Con `agent-browser --session <tu-archivo> set viewport 1280 900`,
`open http://10.13.13.1:8010/<ruta>`, `wait 2500`, y `screenshot <M>/img/<pantalla>.png` (puedes
pasar un selector como `main` si tu versión lo permite; si no, la ventana completa sirve).
Solo abrir y capturar: no hagas clic en nada que cambie datos. Cierra tu sesión al final
(`agent-browser --session <tu-archivo> close`). Si la captura falla, sigue sin ella.

## Antes de terminar

Corre `python3 <M>/lint.py` (0 errores en tus archivos) y `python3 <M>/build.py --borrador /tmp/<tu-archivo>.pdf`, y corrige toda línea `DESBORDA` o
`IMAGEN ROTA` de tus hojas (recorta texto o parte en dos hojas). Las hojas de otros pueden aparecer;
ignóralas. Relee tus hojas buscando jerga y frases largas.

Tu respuesta final: lista de hojas (título + pantalla), qué datos reales usaste y de qué URL,
qué no pudiste confirmar, y cualquier cosa de la pantalla que te pareció confusa o rota para un
usuario (eso le sirve al dueño).
