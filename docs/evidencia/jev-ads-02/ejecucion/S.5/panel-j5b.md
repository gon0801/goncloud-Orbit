# S.5 J5b: revision (panel GLM + deslop/no-comments a mano)

Rama `jev02/s5b-pantallas`, base `80da8768` (merge J5a). Alcance: el
diff dirty heredado (senal en /cortes y pantalla /gasto-sin-venta).

## Panel GLM (solo lectura, mismo prompt de 7 puntos)

Tres revisores por `opencode run -m zai-coding-plan/<modelo> --dir
<worktree>` (suscripcion Z.AI Coding Plan; sin `opencode-go/*` ni
`zai/*`). Cada uno inspecciono el diff con git/grep/read y devolvio
veredicto por punto. Diversidad: familia GLM (ver abajo por que no
hay hijos Muse).

| Modelo | Veredicto | Salida |
|---|---|---|
| glm-4.7 | 7/7 OK, sin fallas | `panel-glm-47-j5b.txt` |
| glm-5.2 | 7/7 OK, sin fallas | `panel-glm-52-j5b.txt` |
| glm-5.3 | 7/7 OK, sin fallas | `panel-glm-53-j5b.txt` |

Los tres confirman: bloque senal espejo del de asesoria, aviso (a)
(tuple_row + finally + lectura por nombre), aviso (b) (ninguna
plantilla pinta `otros_sin_venta`), aviso (c) (`ORDER BY s.gasto
DESC, s.id`), prohibiciones intactas (cero menciones en el diff),
apagado (sin claves jev.*, sin TypeSafe en app, sin JS/handlers
nuevos, bandas solo presentacion). Sus mutantes propuestos coinciden
con J1/J3/J6/J15 ejecutados en `mutantes.md`.

## Hijos Muse: no disponibles en este contexto

La lista de herramientas de esta sesion no trae `subagent_spawn` ni
`workflow`, asi que no se lanzaron hijos `poteto-agent` ni
`comment-sicko`. En su lugar, deslop/no-comments se aplicaron a mano
sobre el diff completo:

- Bloque senal espejo exacto del de asesoria (mismo `noqa: BLE001`,
  mismo patron warning+scrub+dict+False). Consistente, se queda.
- `# type: ignore[arg-type]` en el endpoint delgado: mismo caso ya
  usado en `app/jev_catalogo.py:115,198` (plataforma validada como
  str). Consistente, se queda.
- Comentarios nuevos (6 bloques): todos fijan un por-que no obvio
  (cambio de row_factory, guarda honesta de banda, 36 h, contrato
  viejo sin la clave). Estilo del repo (espanol, conciso). Cero
  supresiones.
- Sin try/catch defensivos extra (el unico es el patron que pide la
  guia), sin casts, sin anidacion nueva.

La revision cruzada del loop corre sobre el PR, fuera de este bloque.
