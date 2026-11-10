# D. Funcionamiento del motor (orquestación, cola, config, despliegue): resultado

Copia revisada: `orbit07-wt` en `3c841f4` (master, contiene `7cf7c9a` y CLARIDAD 01).
Sondas en `scratchpad/orbit07-review/sondas/`: `D_sonda_motor.py` (+`.txt`),
`D_sonda_fila_inerte.py` (+`.txt`), diffs por archivo en `sondas/D-diffs/`.
Comando común: `/Users/dn/dev/goncloud-Orbit/.venv/bin/python -I <sonda> <ruta de orbit07-wt>`.

Conteo: 0 CRÍTICO, 0 ALTO, 1 MEDIO.

## Hallazgos confirmados

### H1. La reversa documentada de ORBIT 07 pausa de golpe las keywords que vendieron en los últimos 9 días

- Severidad: MEDIO
- Frente: dinero
- Dónde:
  - `app/cycle.py:3831` (la cola recibe `proteccion_activa` del setting del ciclo)
  - `app/apply_cola.py:1576-1578` (sin M1 no hay caché de protección)
  - `app/apply_cola.py:1155-1174` y `:1178`, `:1215-1216` (la revalidación decide la pausa con `proteccion=None`)
  - `app/apply_cola.py:198-204` (`_SQL_VENCIDAS` vuelve a tomar toda fila `released` en cada ciclo)
  - `app/apply_cola.py:1664-1679` (cobra cuota, claim y PUT)
  - `docs/evidencia/orbit-07/activacion-orbit07.md:60-72`, `:90-91`, `:110-112` (receta de reversa)
- Escenario:
  - Entrada: con M1 encendida, una hoja tiene una fila de pausa `released` en espera
    (`espera_proteccion`) porque vendió hace 4 días. Su ventana madura de cortes
    sigue con 0 pedidos, clics ≥ umbral y gasto ≥ piso.
  - Disparador: el operador corre la reversa documentada. La receta de US apaga M1,
    M2 y M5/M6 en una sola llamada (`--economia off --bid-economia off --proteccion off`),
    y la guía dice que se corra "ante un defecto comprobado" de cualquiera de las
    mejoras. Lo mismo pasa con `rollback.sh`, porque el código anterior no tiene M1.
  - Qué pasa: en el ciclo siguiente `activa_desde_settings` da False, así que
    `libera_vencidos` corre sin caché. `_revalida_pause` llama `decide_pause` con
    `proteccion=None` y sale `pause_umbral`: la fila califica. `vendio_en_ventana` no
    la salva, porque la venta reciente está fuera de la ventana madura (orders=0).
    Cobra cuota, hace claim y manda el PUT PAUSED. Así salen todas las filas en
    espera, hasta el tope diario de pausas, y las que sobren salen en los días
    siguientes.
  - Qué debería pasar: apagar una mejora para corregir otra (M2 o M5/M6) no debería
    pausar en bloque keywords con venta reciente sin aviso. Hay dos salidas: que la
    receta separe la reversa de M1 de la de M2/M5-M6 y advierta el efecto sobre las
    filas en espera, o que al apagar M1 esas filas se descarten (el ciclo vuelve a
    decidir desde cero) en lugar de aplicarse.
- Alcance con la config actual de producción: hoy no se dispara, porque M1 está
  encendida y las filas esperan. Se dispara el día que se corra la reversa de US (o
  `--proteccion off` desde cualquier plataforma, porque la clave es global) o
  `rollback.sh`. Afecta solo filas live. No medí cuántas filas hay hoy en espera.
  La consulta para medirlo está abajo y no se ejecutó.
- Evidencia: corrí la sonda y además leí el camino línea por línea.
  - `D_sonda_motor.py`, sección 2. Es la misma hoja con la misma ventana madura;
    lo único que cambia es la protección:
    ```
    M1 encendida (cola con proteccion_activa=True): kind=None motivo=venta_reciente_protegida
    M1 apagada / codigo anterior (proteccion=None):   kind=pause motivo=pause_umbral
    orders en la ventana fresca de la revalidacion = 0 -> no aplica vendio_en_ventana
    ```
  - Camino leído:
    1. `cycle.py:3831` pasa `proteccion_activa`.
    2. `apply_cola.py:1576` deja la caché en None.
    3. `:1155-1163` deja `proteccion=None`.
    4. `:1164-1174` es `decide_pause`.
    5. `:1215-1216` devuelve `califica`.
    6. `:1636` no se toma el descarte.
    7. `:1664-1679` es cobro, claim y `_ejecuta_rama`.
  - La guía de activación no menciona este efecto.
    `m1/politica-implementacion.md` tampoco.
- Cómo reproducir:
  - Pura: `/Users/dn/dev/goncloud-Orbit/.venv/bin/python -I sondas/D_sonda_motor.py <orbit07-wt>`.
  - Con base (no ejecutado): en `tests/test_orbit07_m1.py`, tomar el caso
    "cola que espera sin cuota". Después de la espera, insertar una
    `config_version` sin `ads_proteccion_venta` y correr otro ciclo live. Esperado
    con el código actual: la fila pasa a `applying`/`applied` y se cobra una unidad
    de pausa.
  - Medición en producción (no ejecutado; con `orbit_read`, READ ONLY):
    ```sql
    SELECT q.platform, count(*)
      FROM apply_queue q
     WHERE q.estado = 'released' AND q.modo = 'live'
       AND EXISTS (SELECT 1 FROM decision_sin_aplicar s
                    WHERE s.decision_id = q.decision_id AND s.motivo = 'espera_proteccion')
     GROUP BY 1;
    ```

## Sospechas sin confirmar

1. **Pausas descartadas por un LIST que no llega a la fila.**
   - Qué pasa: cada fila en `espera_proteccion` se revalida todos los días durante
     la espera (hasta 10 días o más). Cada revalidación empieza con un LIST de
     readback sin filtro por id, hasta 20 páginas (`apply.py:1036-1069`,
     `TOPE_PAGINAS_READBACK=20` en `apply.py:201`). Si la keyword cae después de la
     página 20, la fila se descarta como `entidad_no_viva` (`apply_cola.py:1089-1095`)
     y esa pausa ya no ocurre.
   - Relación con ORBIT 07: el mecanismo ya existía, pero antes cada fila pasaba una
     sola vez por este LIST. Con M1 pasa hasta unas 10 veces.
   - Para confirmarlo falta: cuántas keywords y targets tiene cada perfil, el tamaño
     de página real del LIST v3, y cuántas filas `discarded` con `entidad_no_viva`
     hay desde el 11-08 cuyo `decision_sin_aplicar` previo era `espera_proteccion`.

## Zonas revisadas sin hallazgos

- **Excepciones nuevas en TX2:** `economia_desde_settings`, `activa_desde_settings` y
  `economia_cambio_desde_settings` (`cycle.py:3201`, `:3254`, `:3290`) lanzan
  ValueError con el literal `off`, con cadena vacía o con `True`. Eso deja el ciclo
  en failed. Ausente o JSON null equivale a apagado (sonda, sección 1).
  - M1 es global: un valor corrupto tumba las dos plataformas completas, apply y
    liberación de la cola incluidos.
  - Es fail-closed por diseño y está documentado. Ni el CLI (`off` quita la clave)
    ni la API (no expone estas claves) pueden escribirlo. Un valor corrupto también
    bloquea todo guardado desde pantalla con 422 (sonda, sección 4).
  - Sin `config_version`, el ciclo queda en escalera `off` (`cycle.py:1440-1444`).
- **Lecturas de M1 en TX2 sin SAVEPOINT** (`cycle.py:3254-3256`): un error de SQL
  aborta el ciclo entero, no solo la regla. No encontré un disparador concreto.
  - Tabla, grant de SELECT/INSERT, USAGE de la secuencia, triggers y CHECK los
    acredita la sonda de 0081 antes del código (`desplegar.sh` paso 4, antes del
    paso 5).
  - Las tablas de eventos de M5 (`ads_campana_config_observation`, `precio_cambio`)
    las lee `app_decide` por el `ALTER DEFAULT PRIVILEGES` de `0001:1521`; no hay
    REVOKE.
  - `v_hoja_activa` sí trae `campana_id` (`0060:31`).
  - Los sellos de eventos son TIMESTAMPTZ, así que `astimezone` no revienta.
- **Transacciones:**
  - Los episodios del ciclo se insertan en TX3 junto con decisiones y targets
    (`cycle.py:3792-3817`): todo o nada.
  - Inserción idempotente: `ON CONFLICT (platform, ad_entity_id, version, ancla) DO NOTHING`
    (`proteccion_venta.py:59-64`).
  - `vence_el = ancla+10` y `observado_el <= abierta_el` se cumplen por
    construcción (`proteccion_venta.py:137-152`).
  - En la cola, el episodio y la transición de la fila viven en la misma
    transacción implícita. Un aborto de la fase los revierte juntos y degrada el
    ciclo (`cycle.py:3560-3564`); el reintento del día siguiente los recalcula.
  - No hay decisión sin episodio ni episodio huérfano.
  - Un rol por DSN: el ciclo usa `ORBIT_DSN_DECIDE` (`cli.py:89`) y el CLI de config
    usa `ORBIT_DSN_ADMIN` sin autocommit (`db.py:47-60`).
- **Cola:**
  - No libera dos veces: el claim es atómico `WHERE estado='released'`
    (`apply_cola.py:219-223`, `:1676`).
  - La espera no cobra cuota: retorna antes del cobro (`:1629-1635` contra `:1665`).
  - No descarta en masa por error de lectura: solo se captura `AdsApiError`
    (`:1613`); cualquier otro error aborta el barrido sin descartar y deja
    `apply_error` y `degraded`.
  - Las filas anteriores al despliegue se revalidan con la lectura fresca, no con
    `inputs.caso`. El deserializador tolera casos sin `proteccion`/`episodio`
    (`caso.py:812-821`, `:940-950`).
  - Única fila atorada que encontré: una hoja que vende el día A y después no tiene
    más filas de métricas. La ventana de cortes termina en `max-3`, la protección
    queda `indeterminada` y la fila espera mientras la hoja siga sin impresiones
    (`D_sonda_fila_inerte.txt`). No hay gasto. Cuesta un LIST diario y se resuelve
    sola cuando la hoja vuelve a tener filas.
- **Shadow contra live:**
  - `libera_vencidos` solo toma `modo='live'` (`apply_cola.py:198-204`).
  - El encolado y la pausa por producto (shadow) no cambiaron en el diff.
  - El juicio de la sombra (`_juicio_hoja`, `_anota_sombra`) no registra episodios.
  - Los episodios son filas de base, no escrituras a Amazon.
  - La revalidación M2 exige meta live (`apply_cola.py:769`, `_producto_fresco`).
  - No encontré un camino de shadow a una escritura en Amazon.
- **Pausa duplicada:** con fila en vuelo, el veredicto Pausar se descarta en los dos
  caminos (`cycle.py:2107-2109`, `:2199-2204`).
  - `decide()` y `_proteccion_activa` usan los mismos insumos de pausa, así que no
    divergen.
  - La puerta de pujas para filas `released` con episodio (aunque sea viejo, sonda
    sección 3) solo deja moverse a una hoja cuya pausa ya no califica. En ese caso
    la cola también la descarta.
- **Config:**
  - `guarda_config` toma el advisory lock y compara la base en la misma transacción
    sin autocommit (`config_write.py:557-567`). Un segundo escritor recibe
    `ConfigObsoleta`.
  - Todos los escritores copian la config entera (`proxima_config`) y la validan
    completa con los validadores del motor (`config_write.py:504-513`).
  - El CLI no toca meta, pausa ni límites (sonda, sección 4).
- **Reversa contra código y base:**
  - El código anterior ignora las claves nuevas y la tabla 0081, y el CHECK nuevo
    es un superconjunto. Su único efecto de dinero es H1.
  - El código nuevo sin las claves corre la semántica histórica (sonda, sección 1).
  - Con episodios ya escritos, la reversa de 0081 aborta, el diario queda en
    `rollback_incompleto` y `desplegar.sh` rechaza cualquier SHA (`desplegar.sh:80-84`)
    hasta que el dueño decida. Está documentado en `DEPLOY.md:191-201` y en la
    guía; no lo cuento como hallazgo.
- **Cambios posteriores (`7cf7c9a..HEAD`):**
  - CLARIDAD 01 K cambia decisiones de bid a propósito: `vendia` de R1 (regreso por
    desplome) se mide ahora en la ventana de cortes, no en los 90 días previos al
    cambio (`lecturas_caso.py:575-609`, `caso.py:159`). Sin ventana de cortes,
    `vendia` es None y R1 da dato faltante. No toca la cola.
  - 0082 reescribe `v_entidad_inerte` con las mismas filas: el watermark sobre
    `ads_metric_observation` equivale al de `v_metric_latest`, que es un DISTINCT ON
    de esa tabla (`0001:1159-1162`).
  - `metricas_listas.guarda` solo cierra su transacción.
  - `efecto-hoja` es de solo lectura.
- **Tiempo del ciclo:**
  - Por hoja hay las mismas consultas que antes: `_SQL_EFECTO_ECONOMIA` reemplaza a
    `_SQL_EFECTO` y no se suma.
  - M1 agrega 2 consultas por plataforma y barrido; M5 agrega 4 por plataforma.
  - La cola agrega unas cuantas consultas por fila en espera.
  - Corre un ciclo por plataforma al día y un `done`/`degraded` no se repite
    (`cycle.py:247-269`): no hay choque con el siguiente cron.
- **Visibilidad (nota, no hallazgo):** `notes.apply.cortes_liberados` no trae el
  conteo `espera_proteccion` (`cycle.py:3529-3537`); solo queda en
  `decision_sin_aplicar`. La revisión del envelope que pide la guía de activación no
  muestra cuántas pausas esperan.
