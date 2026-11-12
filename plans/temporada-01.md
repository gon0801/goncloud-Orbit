# TEMPORADA 01: Orbit invierte según la temporada

Base: rama desde `origin/master` con ARREGLOS 01 terminado (su último bloque integrado). Migraciones nuevas: dos, `<n>_temporada01_margen_vigente.sql` (bloque I) y `<n>_temporada01_episodio.sql` (bloque B), cada una con su `_reversa_`, la cabecera `-- sonda-aplicada: <select que da t solo con la migración aplicada>` en una línea dentro de las primeras 10 y sin BEGIN/COMMIT (`tools/despliegue/comun.sh`). El número es el siguiente libre en master al abrir el PR.
Precondición: este plan y `docs/evidencia/temporada-01/DISENO.md` ya están en master (fila I.0). El plan se entrega como encargo con `motor director encargar`.
Para quién: el dueño. Vende productos de temporada y todos comparten los mismos meses buenos y malos. Hoy Orbit gasta igual todo el año: en los meses bajos paga clics que no venden y en los altos no compite más.
Diseño: `docs/evidencia/temporada-01/DISENO.md` es el único contrato. Tipos, firmas, seudocódigo, migración y textos de pantalla están ahí; cada bloque cita su sección. Léela antes de tocar código. Si el código real contradice el diseño, para el bloque y escribe la diferencia en `docs/evidencia/temporada-01/<bloque>/diferencia.md`; no inventes una forma nueva. Donde este plan y el diseño difieren, vale el plan.
Antes del bloque I: el diseño cita P, T, U, V, W y X de ARREGLOS 01 por su plan (`frena_subidas`, `version_economia`, `TECHOS_HUMANOS_POR_MONEDA`, `exige_bajo_techo`). Relee sus firmas en el código integrado y usa las reales.

Decisiones del dueño (2026-11-11 y 2026-11-12). Ningún bloque espera permiso: este plan es la autorización completa, incluidas las lecturas, los despliegues, las escrituras de settings y el paso a `live`.
1. Calendario de 12 meses, igual para todos los productos. Baja: enero, febrero, mayo y junio. Alta: marzo, abril, octubre y noviembre. Normal: julio, agosto, septiembre y diciembre.
2. Al entrar a un mes bajo, los bids bajan 30 % una sola vez. Al entrar a un mes normal, o unos días antes de un mes alto, regresan a como estaban.
3. En los meses altos la meta de ACoS es 25 %, aplicada después de la cascada. Las metas que el dueño puso a mano en una campaña no cambian.
4. La meta de alta es libre: puede pasar el punto de equilibrio. Si lo pasa, el motor acepta perder hasta esa meta (R3 recorta solo arriba de ella) y Ajustes lo avisa en rojo. Hoy, con 25 %, US queda arriba de su equilibrio de 23.8 % y MX debajo del suyo de 40.3 %.
5. Los bids recortados suben rápido cuando la evidencia lo justifica, con los topes del diseño.
6. Los presupuestos solo se proponen. Se aplican cuando el dueño escribe `APLICAR AJUSTE`.
7. Lo último que hace el dueño siempre gana: si él cambió un bid o una pausa, Orbit no lo deshace. En esa temporada esa keyword no recibe la bajada, el regreso ni la subida rápida, y su pausa no se reactiva. Los ajustes normales por evidencia siguen como hoy, desde el valor que él dejó (diseño, «Variantes de la misma keyword»).
8. El calendario y todos los parámetros se editan en Ajustes.
9. Temporada encendida desde el principio en Amazon MX y Amazon US, las dos juntas.
10. El punto de equilibrio se calcula siempre con el costo de hoy de cada producto. Un costo que baja o sube cuenta desde el día siguiente a su publicación, no después de meses de ventas (bloque I).
11. Ajustes muestra el punto de equilibrio de cada plataforma junto a su meta, para que el dueño vea si va a perder dinero, y avisa en rojo cuando una meta lo pasa.

Reglas para todos los bloques:
- Quien implementa sigue poteto-mode completo en cada bloque: nombra la forma de los datos antes del código, prueba contra lo real y no declara terminado sin evidencia. El motor ya exige su marca de poteto en cada intento.
- Pruebas nuevas solo en los 4 casos de `AGENTS.md`; el campo `pruebas:` de cada bloque nombra su caso. Cada prueba nueva se llama `test_temporada01_<letra del bloque>_<qué comprueba>`, es la que nombra la tabla del diseño (sección «Plan por bloques») y debe fallar en el SHA previo al bloque. Lo demás se comprueba corriendo lo real. `Acepta` sale 0 en el SHA entregado con `uv run --frozen --python 3.12`. `prueba-real` es solo lectura.
- Regla de replay: lo que toca `decide` va detrás de marcadores congelados, y la consulta previa de la migración de origen da 0 filas (diseño, «Migración»).
- Ayudas de solo lectura ya probadas, en `docs/evidencia/arreglos-01/`: `lee_prod.sh` (un SELECT por la entrada estándar), `desplegado.sh [--no] texto...`, `migracion_aplicada.sh <tema>` y `en_prod.sh archivo.py`. `escribe_api.sh <ruta> <json>` llama un endpoint de escritura desde dentro del contenedor; su salida se guarda completa en la evidencia.
- `graphify query` antes de explorar y `graphify update .` después de cambiar código. Si cambian selectores o textos de una pantalla, se actualiza su ficha en `.cursor/skills/verify-orbit/features/`. Los textos que ve la persona van en español llano, sin códigos internos.
- Llaves: ninguna llave, token ni contraseña entra al repo ni a la evidencia.
- Orden: los bloques van en cadena porque comparten `app/cycle.py`, `app/optimizer/politica.py`, `app/lecturas_caso.py`, `app/apply.py` y `app/apply_cola.py`. Ningún bloque espera una medición: la práctica dura un ciclo.

## Bloque I — El punto de equilibrio con el costo de hoy
lane: release
depende: -
pruebas: dinero, instala
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: El punto de equilibrio y la meta que sale del margen ya usan el costo de hoy de cada producto, no el que tenía cuando se vendió. Si un costo baja, la meta lo refleja desde el día siguiente.
prueba-real: echo "select platform, margen_vigente_pct from v_target_margen_plataforma" | bash docs/evidencia/arreglos-01/lee_prod.sh | grep -q amazon_us && bash docs/evidencia/arreglos-01/migracion_aplicada.sh temporada01_margen_vigente
| Fila | Contenido | Acepta |
| --- | --- | --- |
| I.0 | Precondición: el plan y el diseño están versionados en master. | git ls-files --error-unmatch plans/temporada-01.md docs/evidencia/temporada-01/DISENO.md |
| I.1 | `tools/despliegue/comun.sh` `migraciones_de`: el patrón suma `temporada01`. Sin esto el despliegue no aplica las migraciones de I y B. Prueba (instalar), en `tests/test_despliegue_motor.py`: una migración `<n>_temporada01_x.sql` con cabecera y reversa sale en `migraciones_de`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py -k temporada01_i_ |
| I.2 | Diseño, «Punto de equilibrio con el costo vigente», puntos 1 a 5. Migración `<n>_temporada01_margen_vigente.sql` y su reversa: las dos vistas agregan al final `cogs_vigente`, `cobertura_vigente` y `margen_vigente_pct`, con el costo abierto en la misma moneda y las mismas guardas. Antes de desplegar, la consulta nueva corre en solo lectura con `lee_prod.sh` y deja en `docs/evidencia/temporada-01/I/antes.md` el margen histórico y el vigente por plataforma y por producto con meta propia. | test -s docs/evidencia/temporada-01/I/antes.md |
| I.3 | La meta derivada y `equilibrio_acos_pct` (plataforma y producto) salen de `margen_vigente_pct`; `notes.target` guarda `margen_neto_pct` (vigente) y `margen_historico_pct`; la pantalla de productos muestra el margen vigente. Prueba (dinero): `test_temporada01_i_equilibrio_con_costo_vigente`, con los casos del diseño. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_i_ && PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_goals.py tests/test_cycle_target_ciclo.py tests/test_cycle.py tests/test_lecturas_caso.py |

## Bloque A — Calendario y parámetros en Ajustes
lane: release
depende: I
pruebas: dinero, escribe en producción
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Ajustes tiene una tarjeta de Temporada: los 12 meses en baja, normal o alta, el porcentaje de la baja, la meta de alta, el punto de equilibrio de cada plataforma y una vista previa de cómo queda el año. Todavía no cambia ningún bid.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh reduccion_baja_pct ads_campana_topada
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.2 | Diseño, «Uso» secciones 1 y 2, y en «Forma» el módulo `app/optimizer/temporada.py`: config, `valida` con tipos crudos, lectores, `resuelve`, `eventos`, `resumen`. `config_write` y `CuerpoSettings` con todos los campos y sus rangos; booleanos rechazados en campos de número. `ads_campana_topada` (90 %, 5 de 7) lo leen avisos y dinero. Pruebas: dinero `test_temporada01_a_fechas_del_calendario` y escribe en producción `test_temporada01_a_post_rechaza_booleanos`, con los casos del diseño. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_a_ |
| A.3 | Tarjeta en Ajustes, vista previa `GET /api/ads-optimizer/temporada/vista-previa` sin token, «Terminar la baja hoy» y «Mantener pausada», con los textos y errores del diseño. Por plataforma, el punto de equilibrio con el costo de hoy, el aviso rojo cuando la meta vigente o la de alta lo iguala o pasa, y la razón en llano cuando no se puede calcular (diseño, «Punto de equilibrio con el costo vigente», «En Ajustes»); la vista previa lo devuelve al escribir la meta. Ficha de Ajustes en `features/` al día; captura a 1440 px y a 390 px en `docs/evidencia/temporada-01/A/`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_config_write.py tests/test_settings_escritura.py tests/test_avisos_campana.py tests/test_pantalla_dinero.py |

## Bloque B — Registro de cada temporada baja y origen de los cambios
lane: release
depende: A
pruebas: dinero, escribe en producción
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Orbit guarda cada temporada baja y qué bids bajó en ella, en la misma operación que confirma Amazon. Si se cae a medias, al volver no baja dos veces.
prueba-real: bash docs/evidencia/arreglos-01/migracion_aplicada.sh temporada01_episodio && bash docs/evidencia/arreglos-01/desplegado.sh abre_efectos
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Diseño, «Migración»: tablas `temporada_episodio`, `temporada_efecto` y `temporada_exclusion` con índices únicos, origen `temporada` en `v_cambio_bid`, reversa y sonda. La sonda va en una sola línea. Antes de desplegar, la consulta previa corre con `lee_prod.sh`: el SQL se guarda en `docs/evidencia/temporada-01/B/consulta-previa.sql` y solo su salida en `consulta-previa.txt`. | grep -qx 0 docs/evidencia/temporada-01/B/consulta-previa.txt |
| B.2 | Diseño, «Forma» y «Firmas y seudocódigo»: `OrigenCambio`, `CambioFechado`, `HistoriaBid`, `recuperacion.cadena_bid`, `temporada.pendiente`, `destino_entrada`, `frontera`; `app/temporada_io.py` (`lee_estado`, `registra_ciclo`, `estampa`, `abre_efectos`) es el único escritor de episodios. Los efectos se cierran en `_confirma_resumen` y `registra_sin_aplicar`; la reconciliación corre antes de decidir. `pantalla_danadas` ignora el origen `temporada`. `temporada.reduccion_vigente` es la única resolución de si una hoja sigue reducida: la usan `pendiente`, `frontera`, `estampa` y TX3, y `temporada_io.registra_fines` guarda en `temporada_fin_reduccion`, en la misma transacción que las decisiones, el fin de toda hoja leída, aunque no salga decisión. Pruebas: dinero `test_temporada01_b_pendiente` con los casos del diseño; escribe en producción `test_temporada01_b_episodio_reparado_tras_caida`; y escribe en producción `test_temporada01_b_fin_de_reduccion`, con base, hasta la confirmación y el ciclo siguiente: (1) E1 deja 20 → 10 → 7 → 14 y sale sin regreso (`ya_arriba`, fin guardado); R6 sube a 16.10; en E2 la entrada da 11.27 estampada en E2 y, confirmada, el ciclo siguiente no decide otra entrada; (2) E1 deja 8 → 5.60 con el regreso retenido, E2 está abierto y el dueño pone 7 hoy: no hay entrada, se guarda la exclusión de E2 y el bid sigue en 7; en E3 la entrada da 4.90; (3) lo mismo que (2) el mismo día en que E2 se abre, una vez con el bid cambiado sin registro (`dueno_al_final`) y otra con la mano ya registrada hoy; (4) los casos (1) y (2) cuando el juicio da `Mantener` y cuando la keyword es inerte: el fin y la exclusión se guardan igual; (5) una keyword sin reducción anterior con una mano del dueño registrada la mañana del día en que E2 se abre no recibe entrada ese día ni el siguiente, y una registrada el día anterior (mes normal) sí deja la entrada; si la temporada se enciende a media baja, el día de encendido es el de apertura; (6) en práctica no se escribe ningún fin ni exclusión. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_b_ |
| B.3 | Lo que ya existe sigue igual. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_cycle.py tests/test_apply.py tests/test_apply_cola.py tests/test_lecturas_caso.py tests/test_replay_ads_economico.py |

## Bloque C — Subida rápida de bids recortados, en práctica
lane: release
depende: B
pruebas: dinero, escribe en producción
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Orbit ya calcula cuándo un bid recortado puede regresar rápido a su nivel anterior. Por ahora solo lo anota; todavía no lo aplica.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh destino_recuperacion && bash docs/evidencia/temporada-01/C/prueba.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Diseño, «Recuperación rápida (R6r)»: `techo_conocido`, `multiplicador_sostenible`, `destino_recuperacion`, `presupuesto_topado`, R6r y BUD, frenos del primer y segundo salto, `MOTIVOS_ES`, campos de plataforma. Prueba (dinero): `test_temporada01_c_recupera_con_evidencia`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_c_ && PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_politica.py |
| C.2 | Antes del despliegue, escribir `docs/evidencia/temporada-01/C/prueba.sh`: solo lectura con `lee_prod.sh` sobre el `config_version` más reciente; sale 0 solo si `ads_bid_recuperacion_amazon_mx` y `ads_bid_recuperacion_amazon_us` valen `shadow` o `live`. | bash -n docs/evidencia/temporada-01/C/prueba.sh |
| C.3 | Después del despliegue, las dos claves en `shadow` con `escribe_api.sh` sobre `POST /api/ads-optimizer/settings/{platform}` (diseño, «Uso» sección 2). Respuesta y lectura posterior en `docs/evidencia/temporada-01/C/settings.txt`; la comprueba la `prueba-real`. | bash -n docs/evidencia/temporada-01/C/prueba.sh |

## Bloque D — Temporada en las decisiones, en práctica
lane: release
depende: C
pruebas: dinero, escribe en producción
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Orbit ya decide con la temporada: bajar 30 % al entrar a la baja, regresar al salir y competir con meta de 25 % en la alta. Por ahora solo lo anota; todavía no cambia bids.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh _paso_a_centavo frontera_hoja && bash docs/evidencia/temporada-01/D/prueba.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | Diseño, «Temporada baja, salida y alta» y `politica.py` en «Firmas y seudocódigo»: el regreso pendiente va antes de R0, entrada de la baja, `_paso_a_centavo`, guarda de R1, meta de alta libre con `equilibrio_del_juicio` (decisión 4), HOLD, techo de baja compartido por R6 y R6r, frenos. `cortes` con `mult_baja` obligatorio y exceso económico; `windows` y tramos con la cerca; la revalidación de la cola usa `frontera_hoja`; las keywords inertes también entran; sombra en `notes.temporada`. Pruebas (dinero): `test_temporada01_d_salida_antes_de_pausa`, `_dia_mixto_excluido`, `_centavos_de_la_entrada`, `_techo_de_baja_compartido`, `_baja_entra_y_retiene`, `_meta_alta`, `_cortes_por_multiplicador`, con los casos del diseño. En `_meta_alta`, US con base 19.05 y equilibrio 23.81 da meta 25 y R3 con límite 25; fuera de alta R3 vuelve a 23.81. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_d_ |
| D.2 | Lo que ya existe sigue igual. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_politica.py tests/test_optimizer_windows.py tests/test_optimizer_goals.py tests/test_cycle.py tests/test_cycle_target_ciclo.py tests/test_apply_cola.py tests/test_replay_ads_economico.py |
| D.3 | Antes del despliegue, escribir `docs/evidencia/temporada-01/D/prueba.sh`: solo lectura con `lee_prod.sh` sobre el `config_version` más reciente; sale 0 solo si los 12 meses son los de la decisión 1 y `ads_temporada_amazon_mx` y `ads_temporada_amazon_us` valen `shadow` o `live`. | bash -n docs/evidencia/temporada-01/D/prueba.sh |
| D.4 | Después del despliegue, guardar con `escribe_api.sh` los meses de la decisión 1 y las dos claves en `shadow`. Respuesta y lectura posterior en `docs/evidencia/temporada-01/D/settings.txt`; la comprueba la `prueba-real`. | bash -n docs/evidencia/temporada-01/D/prueba.sh |

## Bloque E — Lectura del bid antes de enviarlo y la mano del dueño
lane: release
depende: D
pruebas: escribe en Amazon
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Antes de cambiar un bid, Orbit lee el que tiene Amazon. Si el dueño lo cambió, Orbit no lo pisa y en esa temporada no le aplica la bajada, el regreso ni la subida rápida.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh bid_cambiado_fuera
| Fila | Contenido | Acepta |
| --- | --- | --- |
| E.1 | Diseño, `apply.py` en «Firmas y seudocódigo»: lectura fresca antes del cupo y del PUT para toda decisión de bid; `bid_cambiado_fuera`; `temporada_exclusion` por mano del dueño y por diferencia; prioridad bajo cupo. Una keyword excluida no recibe entrada, regreso ni `Recupera` en ese episodio (decisión 7). Prueba (escribe en Amazon): `test_temporada01_e_mano_del_dueno_excluye`, que además comprueba que al día siguiente, con evidencia que dispararía R6r, no hay `Recupera`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_e_ && PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_apply.py tests/test_cycle_apply.py tests/test_smoke_apply.py |

## Bloque F — Fin de la baja: reactivar lo que Orbit pausó
lane: release
depende: E
pruebas: escribe en Amazon
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Al terminar una temporada baja, Orbit reactiva solo las keywords que él mismo pausó en esa baja. Lo que pausó el dueño se queda como está.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh reactivaciones_pendientes
| Fila | Contenido | Acepta |
| --- | --- | --- |
| F.1 | Antes de desplegar, la consulta de solo lectura del diseño confirma 0 decisiones `resume` del motor `temporada`; el SQL se guarda en `docs/evidencia/temporada-01/F/consulta-previa.sql` y solo su salida en `consulta-previa.txt`. | grep -qx 0 docs/evidencia/temporada-01/F/consulta-previa.txt |
| F.2 | Diseño, `apply_cola.py` en «Firmas y seudocódigo»: `reactivaciones_pendientes`, `pendiente_reactivacion`, `aplica_reactivaciones` con sus guardas, `_SQL_PAUSE_PROPIO` (`app/apply_cola.py:263`), replay del motor `temporada`, `aviso_del_dia`. Pruebas (escribe en Amazon): `test_temporada01_f_reactiva_solo_pausas_de_baja` y `test_temporada01_f_reactivar_no_bloquea_pausas`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_f_ && PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_apply_cola.py tests/test_cycle_pause_cooldown.py |

## Bloque G — Propuestas de presupuesto que aprueba el dueño
lane: release
depende: F
pruebas: dinero, escribe en Amazon
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: «Dónde poner el dinero» propone un presupuesto por campaña según la temporada. Nada se aplica hasta que el dueño escribe APLICAR AJUSTE. Dos aprobaciones de la misma campaña ya no se pisan.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh propuesta_presupuesto
| Fila | Contenido | Acepta |
| --- | --- | --- |
| G.1 | Diseño, «Presupuestos» y el escritor de presupuestos (H1) en «Firmas y seudocódigo»: `propuesta_presupuesto` con montos absolutos por episodio, columna y botón con `APLICAR AJUSTE`, candado por campaña y campo en `_aplica_y_confirma` (también al regresar), tope de X. Ficha de la pantalla en `features/` al día. Pruebas: dinero `test_temporada01_g_propuestas_absolutas` y escribe en Amazon `test_temporada01_g_presupuesto_misma_campana_en_orden`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests -k temporada01_g_ && PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_campana_ajustes.py tests/test_propuestas_campana.py tests/test_pantalla_dinero.py |

## Bloque H — En vivo en MX y US
lane: gate
depende: G
aviso: La temporada y la subida rápida ya cambian bids en Amazon MX y Amazon US. Se apagan desde Ajustes, y «Terminar la baja hoy» regresa los bids.
prueba-real: echo "select settings->>'ads_temporada_amazon_mx', settings->>'ads_temporada_amazon_us', settings->>'ads_bid_recuperacion_amazon_mx', settings->>'ads_bid_recuperacion_amazon_us' from config_version order by id desc limit 1" | bash docs/evidencia/arreglos-01/lee_prod.sh | grep -o live | wc -l | grep -q 4
| Fila | Contenido | Acepta |
| --- | --- | --- |
| H.1 | Con el último ciclo que ya corrió en práctica (D en adelante), leer `notes.temporada` de las dos plataformas con `lee_prod.sh`: entradas, regresos, retenidos, metas de alta, reactivaciones, cuántos `recupera_nivel`, el m* máximo y el gasto extra proyectado. Resumen en `docs/evidencia/temporada-01/H/practica.md`. Si aparece un caso de paro de H.3, se corrige en un bloque nuevo antes de H.2. | test -s docs/evidencia/temporada-01/H/practica.md |
| H.2 | Las cuatro claves a `live` con `escribe_api.sh`. Respuesta (trae `config_version_id`) y lectura posterior del mismo `config_version` en `docs/evidencia/temporada-01/H/settings.txt`. | grep -q config_version_id docs/evidencia/temporada-01/H/settings.txt |
| H.3 | El chequeo del primer ciclo en vivo no frena el cierre: queda programado con `at` en goncloud, en `/mnt/data/appdata/orbit/recordatorios/`, para las 09:05 UTC del día siguiente al cambio (el ciclo de Ads corre a las 08:40). Es un `.sql` de solo lectura (`BEGIN READ ONLY` … `ROLLBACK`) y un `.sh` que manda el resultado por Telegram con `envia_recordatorio.py`. Solo mira los ciclos terminados que empezaron después del `config_version` que dejó las claves en `live`; si todavía no hay ninguno, el mensaje lo dice. Busca los casos de paro: un bid enviado que baja más que la reducción, un regreso que baja un bid, un PUT después de una lectura fresca distinta, dos entradas para una keyword en un episodio, un presupuesto escrito sin `APLICAR AJUSTE`. Copia del `.sql`, del `.sh`, el número de trabajo que da `at` y la salida de `at -c <número>` en `docs/evidencia/temporada-01/H/`. | test -s docs/evidencia/temporada-01/H/primer-ciclo.sql && grep -q primer-ciclo docs/evidencia/temporada-01/H/at-c.txt |
