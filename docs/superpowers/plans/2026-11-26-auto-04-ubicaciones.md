# AUTO-04: Plan de implementacion de ajustes automaticos por ubicacion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar ajustes automaticos por ubicacion (`arriba_de_busqueda`, `resto_de_busqueda`, `paginas_de_producto`) sobre campanas existentes de Amazon MX y US en modo practica (`shadow`), con camino `live` completo, cuota diaria fail-closed, tope de espera simultanea, replay determinista y reversa comprobada.

**Architecture:** Forma A con `campana_ajuste` como dueno unico de los ajustes ejecutados en Amazon y dos tablas append-only nuevas (`ubicacion_evaluacion` y `ubicacion_sin_aplicar`). La politica pura vive en `app/optimizer/ubicacion.py`, la lectura y persistencia en `app/ubicacion_io.py` y el ejecutor en vivo en `app/apply_ubicacion.py`, usando conexion propia sin autocommit para sostener el candado de transaccion durante el `PUT`.

**Tech Stack:** Python 3.12+, FastAPI, PostgreSQL 16, `psycopg` 3, `Decimal`, `pytest`, `ruff`, `graphify`.

**Spec:** [2026-11-26-auto-04-ubicaciones-design.md](../specs/2026-11-26-auto-04-ubicaciones-design.md)

## Global Constraints

- Arrancar la rama de trabajo desde `origin/master` actualizado (migracion base `0103`, rediseño UI integrado).
- Cero `float` en dinero, porcentajes, ACoS o RPC: usar `Decimal` y `NUMERIC(14,4)`.
- Dato faltante es `None` o fila ausente, nunca cero inventado.
- `app/optimizer/` no importa `httpx`, `psycopg`, `app.ads` ni `app.db` (`tests/test_architecture.py`).
- `app/ads/write.py` no agrega nuevos importadores (`PERMITIDOS_IMPORTAR_ADS_WRITE`).
- `app/cycle.py` no supera 1,148 lineas; `app/etiquetas.py` no supera 1,022 lineas; modulos nuevos quedan bajo 900 lineas.
- Migracion `0104_auto04_ubicacion.sql` lleva `-- sonda-aplicada:` en la linea 1, sin `BEGIN`/`COMMIT`, y su reversa `0104_reversa_auto04_ubicacion.sql`.
- Todo codigo, docstring y comentario en espanol sin acentos en codigo; `ruff check` y `ruff format` limpios en cada tarea.

## Review Focus

1. Conexion `autocommit=True` del ciclo suelta `pg_try_advisory_xact_lock` antes del `PUT`: `apply_ubicacion.py` debe usar conexion dedicada con `autocommit=False` y retener el candado hasta confirmar o revertir (probado en Task 4).
2. Cambio en consola de Amazon posterior al sync de las 06:45 UTC: `_aplica_y_confirma` debe comparar el porcentaje vivo contra el juzgado y abortar sin `PUT` cuando difieren, tanto en manual como en automatico (probado en Task 4).
3. Fuga de `shadow` hacia `campana_ajuste` o `v_cambio_bid`: el trigger `campana_ajuste_0104_frontera_roles` debe rechazar en base de datos cualquier insercion de `app_decide` cuya evaluacion tenga `modo = 'shadow'` o `veredicto = 'mantener'` (probado en Task 1).
4. Colision de `UNIQUE(huella)` tras ciclo subir, regresar a mano y volver a subir el mismo valor: la huella automatica incluye `evaluacion_id` (`auto:<id>:<huella>`) y permite el segundo ajuste sin error 500 ni bloqueo (probado en Task 4).
5. Campana con hoja descartada por `_descarta_sin_evidencia` en TX3 o perteneciente a un `impulso` abierto: el motor se abstiene (`producto_sin_evidencia` o `campana_en_impulso`) y nunca propone ni aplica movimiento (probado en Tasks 2, 3 y 5).

## Mapa de archivos

- Crear `migrations/0104_auto04_ubicacion.sql` y `migrations/0104_reversa_auto04_ubicacion.sql`: tablas `ubicacion_evaluacion`, `ubicacion_sin_aplicar`, columna/triggers/permisos de `campana_ajuste` y extension de `apply_cap_de_config`.
- Crear `app/optimizer/ubicacion.py`: tipos, lectores de settings, `decide_ubicacion`, serializacion y replay.
- Crear `app/ubicacion_io.py`: lectura congelada de `CasoCampana`, persistencia de `ubicacion_evaluacion` y lecturas para UI/Salud.
- Crear `app/apply_ubicacion.py`: reconciliacion de pendientes automaticas, control de tope simultaneo de 7 dias, cobro de cuota, conexion dedicada sin autocommit y persistencia de `ubicacion_sin_aplicar`.
- Modificar `app/apply_ajustes.py`: guardia de porcentaje vivo en `_aplica_y_confirma` y funcion `aplica_ajuste_motor`.
- Modificar `app/apply_cuota.py` y `app/config_write.py`: agregar `"ubicacion"` a `KINDS_QUOTA` y validar `ads_ubicacion_*`, `ads_ubicacion_solo_bajadas_*`, `ads_ubicacion_max_en_espera_*`.
- Modificar `app/cycle_grupos.py`, `app/cycle_recorrido.py`, `app/cycle_apply.py`: integrar lectura en TX2, guardado en TX3 post-`_descarta_sin_evidencia`, reconciliacion pre-TX2 y ejecucion post-bids en TX4.
- Modificar `app/pantalla_dinero.py`, `app/templates/donde_poner_el_dinero.html`, `app/dashboard_salud.py`, `app/api_dashboard.py`: sugerencia por campana en practica y resumen en Salud.
- Crear `tools/auto04_verifica.py`: verificador de replay determinista y resumen de practica sobre la base.
- Crear pruebas: `tests/test_migracion_0104.py`, `tests/test_optimizer_ubicacion.py`, `tests/test_ubicacion_io.py`, `tests/test_apply_ubicacion.py`, `tests/test_cycle_ubicacion.py`, `tests/test_ubicacion_ui.py`.

---

### Task 1: Migracion 0104, privilegios de roles y cuota fail-closed

**Files:**
- Create: `migrations/0104_auto04_ubicacion.sql`
- Create: `migrations/0104_reversa_auto04_ubicacion.sql`
- Test: `tests/test_migracion_0104.py`

**Interfaces:**
- Consumes: esquema hasta `0103_envios01_ventana_cierre.sql`, `prohibir_mutacion()`, `campana_ajuste` (`0067`), `apply_cap_de_config` (`0091`).
- Produces: tablas `ubicacion_evaluacion` y `ubicacion_sin_aplicar`, columna `campana_ajuste.evaluacion_id`, trigger `campana_ajuste_0104_frontera_roles`, `apply_cap_de_config` con `ads_optimizer:{amazon_mx,amazon_us}:ubicacion`.

- [ ] **Step 1: Escribir pruebas de esquema, triggers, roles y reversa en `tests/test_migracion_0104.py`**

```python
def test_0104_ubicacion_evaluacion_append_only_y_coherencia(db_admin, db_decide):
    # Insertar mantener con ubicacion != NULL falla CHECK.
    # Insertar mover con de_pct == a_pct falla CHECK.
    # UPDATE y DELETE sobre ubicacion_evaluacion y ubicacion_sin_aplicar fallan prohibir_mutacion.
    # Segunda fila (cycle_id, campana_id) falla UNIQUE.

def test_0104_frontera_roles_bloquea_shadow_y_fuerza_admin_sin_evaluacion(db_admin, db_decide):
    # app_decide intentando insertar en campana_ajuste apuntando a evaluacion shadow -> RAISE check_violation.
    # app_decide intentando insertar apuntando a veredicto='mantener' -> RAISE check_violation.
    # app_decide insertando con evaluacion live+mover, clase='ajuste_ubicacion', go_literal='AUTO_UBICACION',
    #   actor='orbit_ubicacion', regresa_a IS NULL -> OK, y UPDATE solo de confirmado_el -> OK.
    # app_admin intentando insertar con evaluacion_id != NULL -> RAISE check_violation.
    # app_admin insertando regreso (regresa_a = auto_id, evaluacion_id IS NULL) -> OK.

def test_0104_apply_cap_de_config_ubicacion_fail_closed(db_admin):
    # Sin clave ads_apply_cap_amazon_mx_ubicacion -> apply_cap_de_config devuelve NULL.
    # Con clave sembrada en 2 -> devuelve 2; con amazon_us en 1 -> devuelve 1.

def test_0104_reversa_restaura_esquema_y_sonda_aplicada(db_tmp):
    # Aplica 0104 -> sonda-aplicada devuelve True -> aplica 0104_reversa -> tablas desaparecen,
    # columna evaluacion_id desaparece, app_decide pierde INSERT en campana_ajuste y
    # apply_cap_de_config vuelve a 8 mapeos.
```

- [ ] **Step 2: Correr pruebas para verificar que fallan antes de crear la migracion**

Run: `uv run --frozen python -m pytest tests/test_migracion_0104.py -v`
Expected: FAIL (tablas y migracion 0104 no existen).

- [ ] **Step 3: Escribir `migrations/0104_auto04_ubicacion.sql` y `migrations/0104_reversa_auto04_ubicacion.sql`**

Cumplir exactamente la seccion 5 de la spec: linea 1 `-- sonda-aplicada:`, sin `BEGIN`/`COMMIT`, bloque final `DO $$` que audita privilegios de `app_read`, `app_decide`, `app_ingest` y `app_admin` igual que `0067`.

- [ ] **Step 4: Verificar migracion, reversa y pruebas de arquitectura**

Run: `uv run --frozen python -m pytest tests/test_migracion_0104.py tests/test_migracion_0067.py tests/test_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/0104_auto04_ubicacion.sql migrations/0104_reversa_auto04_ubicacion.sql tests/test_migracion_0104.py
git commit -m "feat(auto-04): agrega migracion 0104 de ubicaciones, frontera de roles y cuota"
```

---

### Task 2: Politica pura `app/optimizer/ubicacion.py` y readers de settings

**Files:**
- Create: `app/optimizer/ubicacion.py`
- Test: `tests/test_optimizer_ubicacion.py`

**Interfaces:**
- Consumes: `gamma_p`, `gamma_q` (`app/optimizer/evidencia.py`), `PEDIDOS_GRUPO`, `CLICS_NUEVOS` (`app/optimizer/politica.py`), `EconomiaPlataforma` (`app/optimizer/caso.py`), `CampanaTopada` (`app/optimizer/temporada.py`).
- Produces: `POLITICA`, `EvidenciaUbicacion`, `MetaCampana`, `CasoCampana`, `MantenerUbicacion`, `MoverUbicacion`, `VeredictoUbicacion`, `decide_ubicacion(caso: CasoCampana) -> VeredictoUbicacion`, `modo_ubicacion_desde_settings(settings, platform) -> str`, `solo_bajadas_desde_settings(settings, platform) -> bool`, `max_en_espera_desde_settings(settings, platform) -> int`, `techo_ubicacion(estrategia: str, ubicacion: str) -> int`, `caso_a_json(caso: CasoCampana) -> dict`, `caso_desde_json(doc: dict) -> CasoCampana`.

- [ ] **Step 1: Escribir pruebas unitarias completas en `tests/test_optimizer_ubicacion.py`**

```python
def test_compuertas_de_campana_en_orden():
    # Prueba cada compuerta: no elegible, en_impulso, sin_dynamic_bidding, estrategia_desconocida,
    # ajuste_en_vuelo, en_enfriamiento_14d (13 dias bloquea, 14 dias pasa), ventana < 10 dias,
    # placements no frescos, descuadre > max(0.05, 1%), sin_meta_campana.

def test_recorte_por_acos_usa_meta_laxa_y_piso_cero():
    # Con meta exigente 18% y laxa 24%, ACoS 22% NO recorta; ACoS 38% con certeza >= 0.80 recorta -25 pts.
    # Ajuste vigente +10% recortado -25 pts queda en 0%, nunca negativo.

def test_recorte_gasto_sin_venta_respeta_venta_reciente_e_inmaduros_none():
    # pedidos=0, gasto >= gasto_para_concluir y pedidos_inmaduros=0 -> -15 pts (-25 si >= 2x).
    # pedidos_inmaduros >= 1 -> no recorta. pedidos_inmaduros is None -> abstiene.

def test_recorte_gana_a_subida_en_misma_campana():
    # Campana US con PDP +40% perdiendo (118% ACoS) y ToS 0% ganando (5% ACoS, 5 pedidos):
    # devuelve recorte de paginas_de_producto a +15%, no subida de arriba_de_busqueda.

def test_subida_exige_3_pedidos_20_clics_ventaja_relativa_y_holgura_presupuesto():
    # 1 pedido a 10% ACoS vs 20% meta -> Mantener (bloqueado por PEDIDOS_MIN_SUBIDA=3).
    # 4 pedidos bajo 0.85*exigente pero RPC igual al resto -> Mantener.
    # 4 pedidos bajo 0.85*exigente, RPC >= 1.15x resto, clics_resto >= 20, sin tope -> +15 pts.
    # Campana topada o temporada baja o solo_bajadas=True -> Mantener con motivo exacto.

def test_techo_por_estrategia_no_fuerza_bajada_de_vigente_alto():
    # AUTO_FOR_SALES con paginas_de_producto=40% (techo 25%) y sin evidencia de perdida -> Mantener.
    # AUTO_FOR_SALES con arriba_de_busqueda=45% ganando -> sube a 50% (topado en 50).

def test_replay_roundtrip_json_determinista():
    # caso_desde_json(caso_a_json(caso)) == caso y decide_ubicacion produce el mismo resultado.

def test_settings_fail_closed_y_defaults():
    # ads_ubicacion ausente -> 'off', invalido -> ValueError.
    # solo_bajadas ausente -> True en amazon_us, False en amazon_mx.
    # max_en_espera ausente -> 6 en amazon_mx, 2 en amazon_us; negativo -> ValueError.
```

- [ ] **Step 2: Correr pruebas para verificar que fallan**

Run: `uv run --frozen python -m pytest tests/test_optimizer_ubicacion.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.optimizer.ubicacion'`).

- [ ] **Step 3: Implementar `app/optimizer/ubicacion.py`**

Modulo puro con toda la aritmetica en `Decimal` bajo `localcontext(prec=PRECISION)` siguiendo la seccion 6 de la spec. Sin imports de IO.

- [ ] **Step 4: Verificar pruebas unitarias y candado de motor puro**

Run: `uv run --frozen python -m pytest tests/test_optimizer_ubicacion.py tests/test_architecture.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/optimizer/ubicacion.py tests/test_optimizer_ubicacion.py
git commit -m "feat(auto-04): implementa politica pura ubicacion_v1 y lectores de settings"
```

---

### Task 3: Lectura congelada y persistencia en `app/ubicacion_io.py`

**Files:**
- Create: `app/ubicacion_io.py`
- Test: `tests/test_ubicacion_io.py`

**Interfaces:**
- Consumes: `app/optimizer/ubicacion.py`, `ads_placement_observation`, `ads_campana_config_observation`, `campana_ajuste`, `impulso`, `impulso_lectura`, `v_metric_latest`, `v_hoja_activa`, `app/optimizer/temporada.py`.
- Produces: `lee_casos(conn, platform: str, decided_at: dt.datetime, *, settings: Mapping, goals: Mapping, metas_hoja: Mapping[int, tuple[Decimal, str]], economia: EconomiaPlataforma, fase_temporada: str | None) -> tuple[CasoCampana, ...]`, `guarda_evaluaciones(conn, *, cycle_id: int, config_version_id: int, platform: str, decided_at: dt.datetime, evaluaciones: Sequence[tuple[CasoCampana, str, VeredictoUbicacion]]) -> dict[int, int]`, `ultimas_evaluaciones_por_campana(conn, platform: str) -> dict[int, SugerenciaUbicacion]`, `resumen_ubicaciones_ciclo(conn, cycle_id: int) -> dict`.

- [ ] **Step 1: Escribir pruebas de integracion DB en `tests/test_ubicacion_io.py`**

```python
def test_ultimo_cambio_config_ignora_primera_obs_y_unifica_null_cero(db_admin, db_decide):
    # Primera observacion con top=9 -> dias_desde_ultimo_cambio es None (ventana 30d).
    # Segunda observacion identica (NULL vs 0 en resto) -> sigue None.
    # Tercera observacion cambia estrategia o top=15 hace 10 dias -> dias_desde_ultimo_cambio=10.
    # Cambio hace 16 dias -> ventana_desde = ultimo_cambio + 1d, ventana_hasta = D-3.

def test_excluye_campana_en_impulso_abierto_y_detecta_ajuste_en_vuelo(db_admin, db_decide):
    # Campana con impulso sin impulso_lectura.es_final=true -> caso.en_impulso=True.
    # Campana con campana_ajuste pendiente (confirmado_el IS NULL) -> caso.ajuste_en_vuelo=True.

def test_banda_de_metas_desde_hojas_y_conciliacion_de_gasto(db_admin, db_decide):
    # Dos hojas activas con metas 18% y 25% -> MetaCampana(exigente=18, laxa=25).
    # Suma de los 4 placements igual a gasto_campana -> gasto_placements_total == gasto_campana.
    # Placements atrasados (max metric_date < D-3) -> placements_frescos=False.

def test_guarda_evaluaciones_y_lee_sugerencia_vigente(db_admin, db_decide):
    # Guarda 1 mover y 1 mantener -> devuelve mapa campana_id -> evaluacion_id.
    # ultimas_evaluaciones_por_campana devuelve solo la sugerencia mover mientras antes_config_id siga vigente.
```

- [ ] **Step 2: Correr pruebas para verificar que fallan**

Run: `uv run --frozen python -m pytest tests/test_ubicacion_io.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.ubicacion_io'`).

- [ ] **Step 3: Implementar `app/ubicacion_io.py`**

Todas las consultas de lectura sobre la misma `conn` de TX2 (snapshot consistente), usando `DISTINCT ON` por `(ad_entity_id, placement, metric_date)` ordenado por `observed_at DESC` para `ads_placement_observation` y el mismo criterio de dias topados que `pantalla_dinero.py`.

- [ ] **Step 4: Correr pruebas y ruff**

Run: `uv run --frozen python -m pytest tests/test_ubicacion_io.py -q && ruff check app/ubicacion_io.py tests/test_ubicacion_io.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ubicacion_io.py tests/test_ubicacion_io.py
git commit -m "feat(auto-04): implementa lectura congelada y persistencia de ubicaciones"
```

---

### Task 4: Guardia de porcentaje vivo en `apply_ajustes.py` y ejecutor `app/apply_ubicacion.py`

**Files:**
- Modify: `app/apply_ajustes.py:285-365`
- Create: `app/apply_ubicacion.py`
- Test: `tests/test_apply_ubicacion.py`
- Test: `tests/test_aplica_ajuste.py` (agregar regresion de cambio en consola en camino manual)

**Interfaces:**
- Consumes: `app/apply_ajustes.py::_aplica_y_confirma`, `app/apply_cuota.py::consume_quota_y_sello`, `app/campana_ajustes.py::planea_ajuste`, `app/apply.py::_cliente_ajuste`.
- Produces: `app/apply_ajustes.py::CambioExternoDetectado`, `app/apply_ajustes.py::aplica_ajuste_motor(...)`, `app/apply_ubicacion.py::reconcilia_automaticos(conn, cliente, platform: str, *, tick=None) -> ResumenReconcilia`, `app/apply_ubicacion.py::aplica_automaticos(conn, *, dsn: str, cliente, platform: str, cycle_id: int, settings: Mapping, tick=None) -> ResumenAplica`.

- [ ] **Step 1: Escribir pruebas de concurrencia, candado, cambio externo, huella y reversa**

En `tests/test_apply_ubicacion.py` y `tests/test_aplica_ajuste.py`:

```python
def test_put_aborta_si_porcentaje_vivo_en_amazon_difiere_del_juzgado():
    # Vigente en DB tiene top=10%; lectura viva antes del PUT trae top=25% (cambio en consola).
    # No se manda PUT; en manual levanta HuellaDesactualizada/CambioExternoDetectado;
    # en automatico registra ubicacion_sin_aplicar('cambio_externo').

def test_ejecutor_usa_conexion_sin_autocommit_y_sostiene_advisory_lock():
    # Conexion principal en autocommit=True; mientras _aplica_y_confirma esta dentro del PUT,
    # una segunda conexion intenta pg_try_advisory_xact_lock sobre la misma llave y recibe False.

def test_huella_automatica_permite_reaplicar_tras_regreso_del_dueno():
    # Evaluacion 1 aplica top 0 -> 15%; dueno llama regresa_ajuste_campana (15 -> 0% como app_admin);
    # Evaluacion 2 vuelve a aplicar top 0 -> 15% sin chocar en UNIQUE(huella).

def test_tope_campanas_en_espera_y_cuota_fail_closed():
    # Con max_en_espera=2 y 2 campanas ya confirmadas en ultimos 7d -> registra tope_campanas_en_espera sin PUT ni cuota.
    # Sin clave ads_apply_cap_<plat>_ubicacion -> registra cuota_agotada sin PUT.

def test_reconcilia_automaticos_sella_pendiente_confirmada_en_amazon():
    # Fila campana_ajuste con evaluacion_id != NULL y confirmado_el IS NULL cuyo valor ya coincide en Amazon:
    # reconcilia_automaticos la sella y guarda observacion de config sin mandar PUT.
```

- [ ] **Step 2: Correr pruebas para verificar que fallan**

Run: `uv run --frozen python -m pytest tests/test_apply_ubicacion.py -v`
Expected: FAIL.

- [ ] **Step 3: Actualizar `app/apply_ajustes.py` e implementar `app/apply_ubicacion.py`**

En `_aplica_y_confirma`, al leer `actual = _config_leida(...)` para `CambiarAjusteUbicacion`, verificar tambien que `(getattr(actual, campo) or 0) == (getattr(previa, campo) or 0)` y que `actual.estrategia_puja == previa.estrategia_puja`; si difieren, abortar antes del `PUT`. En `apply_ubicacion.py`, abrir conexion dedicada con `autocommit=False` para el insert pre-HTTP + `_aplica_y_confirma`, ordenar recortes antes de subidas y nunca importar `app.ads.write`.

- [ ] **Step 4: Verificar pruebas nuevas y suite de ajustes existente**

Run: `uv run --frozen python -m pytest tests/test_apply_ubicacion.py tests/test_aplica_ajuste.py tests/test_architecture.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/apply_ajustes.py app/apply_ubicacion.py tests/test_apply_ubicacion.py tests/test_aplica_ajuste.py
git commit -m "feat(auto-04): implementa ejecutor live con candado dedicado y guardia de cambio externo"
```

---

### Task 5: Cuota en `apply_cuota.py`, settings en `config_write.py` e integracion en el ciclo

**Files:**
- Modify: `app/apply_cuota.py:245`
- Modify: `app/config_write.py`
- Modify: `app/cycle_grupos.py`
- Modify: `app/cycle_recorrido.py`
- Modify: `app/cycle_apply.py`
- Test: `tests/test_cycle_ubicacion.py`

**Interfaces:**
- Consumes: `app/ubicacion_io.py`, `app/apply_ubicacion.py`, `app/optimizer/ubicacion.py`.
- Produces: `"ubicacion"` en `KINDS_QUOTA`, validacion en `proxima_config` de los settings de ubicacion, ejecucion completa en el ciclo (`shadow` y `live`).

- [ ] **Step 1: Escribir pruebas de ciclo extremo a extremo en `tests/test_cycle_ubicacion.py`**

```python
def test_ciclo_en_shadow_guarda_ubicacion_evaluacion_y_no_toca_campana_ajuste():
    # ads_optimizer_mode='live', ads_ubicacion_amazon_mx='shadow'.
    # El ciclo escribe ubicacion_evaluacion con modo='shadow', cero filas en campana_ajuste,
    # cero filas nuevas en v_cambio_bid y cero PUT de campana.

def test_ciclo_en_live_corre_ubicaciones_solo_tras_pujas_completas():
    # Si la fase de pujas deja pendientes o falla -> ubicaciones no aplica (registra pujas_pendientes).
    # Si pujas termina completa -> aplica ubicacion, sella campana_ajuste y v_cambio_bid publica las hojas.

def test_descarta_sin_evidencia_anula_ubicacion_de_esa_campana():
    # Si guardar evaluacion_producto_ciclo falla para una hoja de la campana, la evaluacion de ubicacion
    # de esa campana se guarda como mantener('producto_sin_evidencia').

def test_config_write_valida_settings_y_cap_ubicacion():
    # proxima_config acepta ads_ubicacion_*, solo_bajadas, max_en_espera y cap 'ubicacion';
    # rechaza valores fuera de dominio con SettingsInvalido.
```

- [ ] **Step 2: Correr pruebas para verificar que fallan**

Run: `uv run --frozen python -m pytest tests/test_cycle_ubicacion.py -v`
Expected: FAIL.

- [ ] **Step 3: Cablear `apply_cuota.py`, `config_write.py`, `cycle_grupos.py`, `cycle_recorrido.py` y `cycle_apply.py`**

Mantener `app/cycle.py` igual o debajo de 1,148 lineas delegando las llamadas de ubicacion dentro de `cycle_recorrido.py` y `cycle_apply.py`. Verificar en `test_architecture.py` que ninguna frontera ni techo se rompa.

- [ ] **Step 4: Verificar ciclo, configuracion y arquitectura**

Run: `uv run --frozen python -m pytest tests/test_cycle_ubicacion.py tests/test_cycle.py tests/test_config_write.py tests/test_architecture.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/apply_cuota.py app/config_write.py app/cycle_grupos.py app/cycle_recorrido.py app/cycle_apply.py tests/test_cycle_ubicacion.py
git commit -m "feat(auto-04): integra evaluacion y ejecucion de ubicaciones en ciclo y settings"
```

---

### Task 6: Pantalla Donde poner el dinero, Salud y verificador de replay

**Files:**
- Modify: `app/pantalla_dinero.py`
- Modify: `app/templates/donde_poner_el_dinero.html`
- Modify: `app/dashboard_salud.py`
- Modify: `app/api_dashboard.py`
- Create: `tools/auto04_verifica.py`
- Test: `tests/test_ubicacion_ui.py`

**Interfaces:**
- Consumes: `app/ubicacion_io.py::ultimas_evaluaciones_por_campana`, `resumen_ubicaciones_ciclo`, `app/optimizer/ubicacion.py::decide_ubicacion`, `caso_desde_json`.
- Produces: campo `sugerencia_ubicacion` en `FilaCampana`, vista HTML en **Donde poner el dinero**, cuota y resumen de ubicaciones en **Salud**, CLI `python -m tools.auto04_verifica`.

- [ ] **Step 1: Escribir pruebas de UI, Salud y verificador en `tests/test_ubicacion_ui.py`**

```python
def test_donde_poner_el_dinero_muestra_sugerencia_practica_vigente():
    # Campana con evaluacion shadow veredicto='mover' y mismo antes_config_id:
    # GET /donde-poner-el-dinero muestra el texto con modo practica, ubicacion y porcentajes.
    # Tras cambiar config vigente, la sugerencia vieja deja de mostrarse.

def test_salud_incluye_cuota_y_resumen_ubicaciones():
    # GET /api/salud incluye kind 'ubicacion' en cuotas y resumen de evaluaciones del ultimo ciclo.

def test_auto04_verifica_replay_determinista_sin_discrepancias():
    # Ejecuta verifica_replay(conn) sobre evaluaciones guardadas -> discrepancias == 0.
```

- [ ] **Step 2: Correr pruebas para verificar que fallan**

Run: `uv run --frozen python -m pytest tests/test_ubicacion_ui.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar cambios en pantalla, Salud y `tools/auto04_verifica.py`**

Mantener textos de motivos de ubicacion dentro de `app/optimizer/ubicacion.py` (o diccionario local consumido por la plantilla) si agregar entradas a `app/etiquetas.py` excede su techo de 1,022 lineas; no inflar el techo sin justificacion. Actualizar `.cursor/skills/verify-orbit/features/` para **Donde poner el dinero** y **Salud** si cambian selectores visibles.

- [ ] **Step 4: Verificar UI, Salud, arquitectura, lint y pre-commit**

Run: `uv run --frozen python -m pytest tests/test_ubicacion_ui.py tests/test_architecture.py -q && ruff check app tests tools && ruff format --check app tests tools && pre-commit run --all-files`
Expected: PASS en todas las comprobaciones.

- [ ] **Step 5: Commit**

```bash
git add app/pantalla_dinero.py app/templates/donde_poner_el_dinero.html app/dashboard_salud.py app/api_dashboard.py tools/auto04_verifica.py tests/test_ubicacion_ui.py
git commit -m "feat(auto-04): muestra sugerencias de ubicacion en pantalla, salud y verificador de replay"
```

---

### Task 7: Verificacion integral, actualizacion de grafo y documentacion de despliegue

**Files:**
- Modify: `docs/APPLY.md` (documentar cuota `ubicacion`, conexion dedicada y frontera de roles de `campana_ajuste`)
- Modify: `docs/DATABASE.md` (documentar migracion `0104`, `ubicacion_evaluacion` y `ubicacion_sin_aplicar`)
- Modify: `plans/ROADMAP.md`, `plans/ORDEN-IMPLEMENTACION.md`, `plans/manifest.json` (registrar AUTO-04 al integrar el PR)

- [ ] **Step 1: Correr suite dirigida completa de AUTO-04 y vecinos**

Run: `uv run --frozen python -m pytest tests/test_migracion_0104.py tests/test_optimizer_ubicacion.py tests/test_ubicacion_io.py tests/test_apply_ubicacion.py tests/test_cycle_ubicacion.py tests/test_ubicacion_ui.py tests/test_aplica_ajuste.py tests/test_architecture.py -q`
Expected: PASS.

- [ ] **Step 2: Verificar pantallas reales con `orbit-verify`**

Run: `.cursor/skills/verify-orbit/helpers/orbit-verify launch && .cursor/skills/verify-orbit/helpers/orbit-verify doctor` y validar **Donde poner el dinero** y **Salud** con `cleanup` al cierre.
Expected: estado saludable y sugerencias renderizadas sin errores de consola.

- [ ] **Step 3: Actualizar grafo y documentacion**

Run: `graphify update . && ruff check app tests tools && pre-commit run --all-files`
Expected: PASS sin hallazgos de secretos ni conflictos.

- [ ] **Step 4: Cierre para PR en modo `shadow`**

El despliegue inicial deja `ads_ubicacion_amazon_mx = shadow` y `ads_ubicacion_amazon_us = shadow`, sin claves `ads_apply_cap_*_ubicacion` sembradas (cuota fail-closed en 0). El encendido a `live` queda separado tras cumplir la seccion 9 de la spec (sonda real de ajuste + reversa en MX y US, 14 dias de practica y `tools/auto04_verifica.py` en 0 discrepancias).
