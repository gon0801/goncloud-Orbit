# JEV ADS 03 — el dueño bloquea lo que Jev marca como ajeno

Estado: plan del 2026-10-17. El dueño selló el diseño ese día en el chat con
el lead. Nace de la fila E.2 de [JEV ADS 02](jev-ads-02.md), cerrada como "no
se construye" sin medición; la medición ya está en
`docs/evidencia/jev-ads-03/diseno/`. Base de planificación: commit `2e95738`.
`team_validation_mode: manual-pass`.

**Resultado:** en `/cortes`, el dueño ve las búsquedas que Jev marca como
ajenas en MX y US, y con un clic las bloquea como negative exacto en su ad
group o las rechaza. Orbit escribe el ledger antes de llamar a Amazon,
confirma con un readback y deja quitar cada bloqueo. Rechazar no toca Amazon y
se recuerda mientras no cambien los productos ni las fichas del grupo. Si el
dueño aprueba menos del 70%, las propuestas de esa plataforma se pausan.

**Spec:** [diseño de JEV ADS 03](../docs/superpowers/specs/2026-10-17-jev-ads-03-design.md).
Precedencia: `docs/CONTEXTO.md` > `docs/APPLY.md` > diseño 02 > diseño 03 >
este plan.

**Alcance:** solo el bloqueo aprobado (E.2). La abstención del motor (E.1), el
ruteo (E.3) y la fábrica (5.x) siguen sin construirse: el ruteo no pasó la
revisión a ciegas (6 de 35).

## Contratos

- Nada entra a `apply_queue`, `cycle`, `apply_cola` ni `app/optimizer`. La
  lista de propuestas lee `apply_queue` solo para no duplicar al motor.
- Los módulos nuevos no se llaman `jev_*` ni importan `jev_*`. Leen
  `jev_senal_vigente` con SQL crudo. La guarda de imports de Jev no cambia.
- Solo `app/bloqueo_dueno_efecto.py` importa `app.ads.write`, y entra a
  `PERMITIDOS_IMPORTAR_ADS_WRITE` con su razón.
- Ledger antes del HTTP, LIST por identidad antes del POST, readback después.
  Tablas append-only.
- Quitar funciona con el interruptor apagado.
- `app_jev` no gana permisos y el job de Jev no cambia.

## Interruptores y reversa

| Efecto | Interruptor | Apagar deja | Reversa |
| --- | --- | --- | --- |
| Propuestas y aprobación | `jev.bloqueo` en `config_version.settings`; ausente o distinto de `true` es apagado | Sin propuestas; aprobar, rechazar y reanudar dan 409 | Quitar cada bloqueo desde "Tus bloqueos" (`docs/APPLY.md` §7: el negative pasa a `ARCHIVED`) |

## Tareas y dependencias

Evidencia de cada fila en `docs/evidencia/jev-ads-03/ejecucion/<id>/`.
Entrega por secciones: la sección B es una rama y un PR, con un commit por
fila. D y C van cada una en su PR. El lead revisa cada PR según la regla 4 de
quality-kit. La batería `completa` corre en la rama antes del APROBADO, porque
en un PR solo corre la de arquitectura.

### Sección 0 — diseño

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 0.1 | `[stage:planificacion] [lane:fast]` Diseño, medición y sello del dueño. | Spec y evidencia en master; las cinco decisiones del dueño citadas. | - | cc:DONE (sellado 2026-10-17: meta 70%, ahorro desde 2,000 MXN al año, MX y US, propuestas en `/cortes`, rechazo recordado) |

### Sección B — construcción

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| B.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Migración `0074_jev_ads03_bloqueo.sql` y su reversa: `bloqueo_veredicto` y `bloqueo_efecto` con las FK, los CHECK por tipo y por paso del spec, triggers append-only, permisos y bloque `DO` de verificación. Confirmar antes con `gh pr list` que 0074 sigue libre. | Prueba 9 del spec con logins reales; `tests/test_migracion_0074.py` con la reversa, aplicada antes que la de 0052; 0074 entra a `ORDEN_PERIMETRO` de `tests/test_jev_perimetro.py` y una prueba fija que `app_jev`, `app_decide` y `app_ingest` no leen las tablas nuevas; los CHECK rechazan `aplicado` sin `negative_keyword_id`, `fallido` sin `motivo` y `quitado` sin `origen_id`. | 0.1 | cc:TODO |
| B.2 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/bloqueo_dueno.py`: propuestas (cinco condiciones del spec), `huella_catalogo` con la receta del spec, rechazo recordado y pausa. Parte pura con tipos, lecturas aparte. | Pruebas 5 y 6 del spec y la mitad de propuestas de la 8 como tablas contra la función pura, más una prueba con Postgres real de la consulta de propuestas; la prueba 10 en su mitad (no importa `app.ads.write`). | B.1 | cc:TODO |
| B.3 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/bloqueo_dueno_efecto.py`: aprobar, rechazar, quitar y reconciliar como en el spec, con el candado de sesión sostenido hasta el readback; comando `python -m app.cli bloqueo-reconcilia` con import tardío y `ORBIT_DSN_ADMIN`. Reusar `_lista_filtrada` e `_identidad` de `app/apply_harvest.py`, `_SQL_EXTERNALES_GRUPO` de `app/apply_cola.py` y la resolución de modo de `aplica_ajuste_campana`. Agregar a `docs/APPLY.md` §8.3 la excepción del spec. Leer qué hace `apply_cola._ejecuta_negative` si Amazon rechaza un negative duplicado y fijarlo en una prueba con la respuesta simulada. | Pruebas 2, 3, 4, 7, 10 y 11 del spec y la mitad de aprobar de la 8, con `MockTransport` y Postgres real; la 4 con dos conexiones; una lista `truncado` o `ambiguo` no hace POST; la prueba del duplicado del motor; entrada en `PERMITIDOS_IMPORTAR_ADS_WRITE` con su razón; §8.3 actualizado. | B.1, B.2 | cc:TODO |
| B.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Router nuevo con prefijo `/api/jev-bloqueo` en `app/api_write.py` (`aprobar`, `rechazar`, `quitar`, `reanudar`), registrado en `app/main.py`; clave `bloqueos_jev` en `GET /api/dashboard/cortes`; sección en `app/templates/cortes.html` y los POST en `app/static/js/cortes.js`. `api_write.py` tiene 774 líneas de un tope de 900. | Pruebas 1 y 12 del spec; `test_toda_ruta_post_declara_exige_token_antes_de_la_conexion` recorre todos los routers del módulo y cubre las rutas nuevas; con la consulta rota `/cortes` responde 200 con `bloqueos_disponibles = false`; captura de la pantalla con datos de prueba en MX y US, con pausa y sin ella. | B.2, B.3 | cc:TODO |

### Sección D — despliegue y encendido

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| D.1 | `[stage:cierre-pr] [lane:release]` Desplegar apagado con `desplegar.sh`, `checklist.sh` y `rollback.sh` en `ejecucion/D.1/`, con el patrón de `docs/evidencia/jev-ads-02/ejecucion/S.5/` (la única con `rollback.sh` y migración). Aplica 0074 por el procedimiento de `docs/DEPLOY.md`, "Aplicar migraciones". | Checklist 0 mal: md5 de los archivos, `/cortes` 200 sin propuestas, tablas vacías, `bloqueo-reconcilia` sale 0, permisos comprobados como `orbit_read` y `orbit_decide`; rollback ensayado. | B.4 | cc:TODO |
| D.2 | `[stage:cierre-pr] [lane:release]` Encender `jev.bloqueo` en una `config_version` nueva que copia la vigente y cambia solo esa clave, con el JSON `true` (`jsonb_set(settings, '{jev.bloqueo}', 'true'::jsonb)`), no el texto `"true"`, que deja el interruptor apagado. | `/cortes` muestra las propuestas de MX y US; su número coincide con una consulta de solo lectura; ningún POST corrido por el loop. Avisar al dueño por Telegram que ya puede revisar. | D.1 | cc:TODO |
| D.3 | `[stage:medicion] [lane:release]` Programar en goncloud con `at`, a 30 días, un reporte de solo lectura por Telegram, con el patrón de `/mnt/data/appdata/orbit/recordatorios/` en el servidor (`.sql` de solo lectura, `.sh` y `envia_recordatorio.py`): veredictos por plataforma, porcentaje aprobado, gasto de la ventana de las búsquedas bloqueadas y ventas posteriores en el grupo bloqueado. Copia del `.sql` y del `.sh` en `ejecucion/D.3/`. | `.sql` probado sin enviar; `atq` muestra el trabajo; no bloquea el cierre. | D.2 | cc:TODO |

### Cierre

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| C.1 | `[stage:cierre-pr] [lane:fast]` Ledger al día, `docs/CHAT-CONTEXT.md`, `plans/manifest.json`, y E.2 de `plans/jev-ads-02.md` apuntando aquí. | Todas las filas en `cc:DONE` con su evidencia. | D.3 | cc:TODO |

## Criterios medidos

| Paso | Criterio |
| --- | --- |
| Construir (0.1) | Cumplido: acuerdo de `ajena` 85% contra la meta de 70% del dueño; ahorro de MX 976 MXN en la ventana contra 2,000 MXN al año. |
| Pausa automática | Por plataforma: 20 veredictos o más y menos de 70% aprobado en los últimos 30. El 70% es del dueño; 20 y 30 son del autor. |
| Reporte a 30 días (D.3) | Informativo. Si MX ahorra menos de 167 MXN al mes, el lead propone apagar `jev.bloqueo` al dueño. |

## Pruebas que deben discriminar

Las doce del spec. Las que no pueden faltar:

1. Interruptor ausente: cero HTTP y 409, con quitar disponible.
2. La señal cambió entre la vista y el clic: `anulado`, cero HTTP.
3. El ledger existe antes del HTTP y una caída se reconcilia por LIST.
4. El motor no importa los módulos nuevos ni lee sus tablas.

Cada bug encontrado en el camino trae la prueba que lo habría atrapado.

## Orden y salida

Orden: 0 → B.1 → B.2 → B.3 → B.4 → D.1 → D.2 → D.3 → C.1. El plan cierra
cuando el interruptor está encendido, el reporte de 30 días programado y el
ledger al día. Este plan es la autorización completa para desplegar, migrar y
encender; las escrituras en Amazon solo las inicia el dueño con su clic.
