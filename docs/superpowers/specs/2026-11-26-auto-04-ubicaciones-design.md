# AUTO-04: Especificacion de ajustes automaticos por ubicacion

- Fecha: 2026-11-26
- Base arquitectonica: diseno `/architect` de Claude Fable 5.2, auditado contra `origin/master` (hasta migracion `0103` y rediseño UI) y verificado en solo lectura contra produccion (2026-11-26).
- Rama base obligatoria para implementar: `origin/master` actualizado.

## 1. Objetivo, alcance y exclusiones

Orbit evalua cada dia las tres ubicaciones con multiplicador de puja (`arriba_de_busqueda`, `resto_de_busqueda`, `paginas_de_producto`) en las campanas activas existentes de Amazon MX y Amazon US. Cuando la evidencia madura lo justifica, propone un ajuste porcentual acotado, lo registra en practica (`shadow`) sin tocar Amazon ni las pujas, y en `live` lo ejecuta con el mismo camino reversible de `campana_ajuste`.

### Alcance incluido

- Campanas existentes de `amazon_mx` y `amazon_us` con `dynamicBidding` conocido (`LEGACY_FOR_SALES`, `AUTO_FOR_SALES`, `MANUAL`).
- Arranque en modo practica (`shadow`) con interruptor independiente por mercado (`ads_ubicacion_amazon_mx`, `ads_ubicacion_amazon_us`).
- Registro diario auditable y determinista (`ubicacion_evaluacion`), visible en **Donde poner el dinero** y resumido en **Salud**.
- Camino de escritura `live` con cuota diaria fail-closed, tope de campanas simultaneas en espera de 7 dias, intencion pre-HTTP, lectura viva previa al `PUT`, verificacion por readback, reconciliacion y reversa individual por ubicacion.

### Fuera de alcance

- Creacion de campanas aisladas por keyword tipo Keyword Boost.
- Ajustes automaticos de `offAmazonSettings` (`fuera_de_amazon`) o de presupuesto diario.
- Mercado Libre.
- Publicacion en `v_cambio_bid` de cambios hechos directo en la consola de Amazon (hueco previo independiente).

### Anti-objetivos

- Decidir un segundo paso usando datos anteriores al primer cambio.
- Congelar casi todas las pujas de US a la vez por disparar el freno de 7 dias en demasiadas campanas.
- Sobrescribir un cambio que el dueno hizo en la consola de Amazon despues del sync de las 06:45 UTC.
- Filtrar evaluaciones de `shadow` hacia `campana_ajuste` o `v_cambio_bid`.
- Falsificar la confirmacion humana `APLICAR AJUSTE` en filas creadas por el motor.

## 2. Decisiones aprobadas del dueno (2026-11-26)

1. Ritmo simultaneo: maximo `6` campanas en espera de 7 dias a la vez en MX y `2` en US, con cuota diaria sugerida al encender `live` de `2` en MX y `1` en US.
2. Campanas con metas distintas por producto o familia: construir la banda `[exigente_pct, laxa_pct]` con las metas ya congeladas del ciclo. Subir exige vencer a `exigente_pct` y bajar exige perder contra `laxa_pct`.
3. Regla relativa para subir: subir una ubicacion exige ganar contra la meta y rendir mejor que el resto de la misma campana.
4. Volumen minimo para subir: al menos `3` pedidos (`PEDIDOS_GRUPO`) y `20` clics (`CLICS_NUEVOS`) en la ventana.
5. Ajustes vigentes por encima del techo: el techo solo limita subidas y nunca fuerza una bajada sin evidencia.
6. Estados Unidos en primera etapa `live`: solo bajadas por defecto (`ads_ubicacion_solo_bajadas_amazon_us = true`) hasta validar un periodo de practica.
7. Practica visible en pantalla: mostrar la sugerencia en **Donde poner el dinero** junto al boton existente **Cambiar ajuste de ubicacion**. Si el dueno la aplica a mano, cuenta como ajuste manual y activa la espera de 14 dias.

## 3. Restricciones globales

- Reglas 1 a 10 de `docs/CONTEXTO.md`: un dueno por dato, una fuente por numero, ausente igual a `None`, `Decimal` exacto y `NUMERIC(14,4)` sin `float`, tablas append-only, madurez de datos, reversa probada antes de encender, verificacion contra datos reales, pruebas de regresion que fallan antes del cambio y conciliacion externa.
- Evidencia de produccion medida el 2026-11-26 (solo lectura): la suma de `ads_placement_observation.cost` coincide al centavo (`0.00` maximo de diferencia) con el gasto de campana en las 22 campanas MX y 8 campanas US con gasto; `ads_campana_config_observation` tiene 246 filas con 0 nulos en estrategia y porcentajes; `campana_ajuste` tiene 0 filas.
- Candados de `tests/test_architecture.py`: `app/optimizer/` no importa `httpx`, `psycopg`, `app.ads` ni `app.db`; `app/ads/write.py` conserva su lista cerrada de importadores; `app/cycle.py` tiene techo de 1,148 lineas (hoy 1,146) y `app/etiquetas.py` tiene techo de 1,022 lineas (hoy 1,020); modulos nuevos deben quedar bajo 900 lineas.
- Convencion de migraciones (`tools/despliegue/comun.sh`): linea 1 con `-- sonda-aplicada:`, sin `BEGIN`/`COMMIT`, no re-ejecutable y con archivo `*_reversa_*.sql`.

## 4. Arquitectura elegida (Forma A)

El ajuste automatico por ubicacion usa `campana_ajuste` como registro unico de cambios ejecutados en Amazon. La evaluacion diaria del motor vive en `ubicacion_evaluacion`, separada del registro de escritura para que `shadow` nunca toque `campana_ajuste` ni `v_cambio_bid`.

### Mapa de modulos y fronteras

- `app/optimizer/ubicacion.py` (puro, sin IO): tipos del dominio (`EvidenciaUbicacion`, `MetaCampana`, `CasoCampana`, `MantenerUbicacion`, `MoverUbicacion`), lectores de settings (`modo_ubicacion_desde_settings`, `solo_bajadas_desde_settings`, `max_en_espera_desde_settings`, `techo_ubicacion`), politica pura `decide_ubicacion(caso)` y serializacion de replay (`caso_a_json`, `caso_desde_json`).
- `app/ubicacion_io.py` (IO de lectura y tabla de evaluacion): `lee_casos`, `guarda_evaluaciones`, `marca_producto_sin_evidencia`, `ultimas_evaluaciones_por_campana` para UI y `resumen_ubicaciones_ciclo` para Salud.
- `app/apply_ubicacion.py` (ejecucion y reconciliacion en `live`, sin importar `app.ads.write`): `reconcilia_automaticos`, `aplica_automaticos` y persistencia en `ubicacion_sin_aplicar`. Usa una conexion propia corta con `autocommit=False` para ejecutar `_aplica_y_confirma` con su candado de transaccion activo durante todo el `PUT`.
- `app/apply_ajustes.py`: agregar verificacion de porcentaje vivo antes del `PUT` en `_aplica_y_confirma` (protege tanto el camino manual como el automatico contra cambios en consola) y exponer `aplica_ajuste_motor` como entrada compartida sin exigir el literal humano `APLICAR AJUSTE`.
- `app/cycle_recorrido.py` y `app/cycle_apply.py`: cablear lectura en TX2 (al cierre del recorrido de hojas), guardado en TX3 (despues de `_descarta_sin_evidencia`), reconciliacion antes de TX2 y aplicacion en TX4 solo despues de que las pujas del origen cerraron sin pendientes. No crecer `app/cycle.py` sobre su techo.
- `app/apply_cuota.py` y `app/config_write.py`: sumar `"ubicacion"` a `KINDS_QUOTA` y validar los tres settings nuevos de ubicacion.
- `app/pantalla_dinero.py`, `app/templates/donde_poner_el_dinero.html`, `app/dashboard_salud.py` y `app/api_dashboard.py`: mostrar la sugerencia congelada por campana y el estado de cuota y ciclo en Salud.

## 5. Esquema SQL (`migrations/0104_auto04_ubicacion.sql` + reversa)

### Tabla `ubicacion_evaluacion`

Append-only (`prohibir_mutacion()` en `BEFORE UPDATE OR DELETE` y `BEFORE TRUNCATE`):

- `id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`
- `cycle_id BIGINT NOT NULL REFERENCES optimizer_cycle(id)`
- `campana_id BIGINT NOT NULL REFERENCES ad_entity(id)`
- `platform platform NOT NULL CHECK (platform IN ('amazon_mx', 'amazon_us'))`
- `modo TEXT NOT NULL CHECK (modo IN ('shadow', 'live'))`
- `politica TEXT NOT NULL CHECK (politica <> '')`
- `config_version_id BIGINT NOT NULL REFERENCES config_version(id)`
- `antes_config_id BIGINT NOT NULL REFERENCES ads_campana_config_observation(id)`
- `decided_at TIMESTAMPTZ NOT NULL`
- `ventana_desde DATE NOT NULL`
- `ventana_hasta DATE NOT NULL` (`CHECK (ventana_desde <= ventana_hasta)`)
- `moneda currency NOT NULL`
- `entrada JSONB NOT NULL` (`CasoCampana` congelado)
- `veredicto TEXT NOT NULL CHECK (veredicto IN ('mantener', 'mover'))`
- `motivo TEXT NOT NULL CHECK (motivo <> '')`
- `ubicacion TEXT NULL CHECK (ubicacion IN ('arriba_de_busqueda', 'resto_de_busqueda', 'paginas_de_producto'))`
- `de_pct INTEGER NULL CHECK (de_pct BETWEEN 0 AND 900)`
- `a_pct INTEGER NULL CHECK (a_pct BETWEEN 0 AND 900)`
- Coherencia de veredicto: `veredicto = 'mantener'` exige `ubicacion IS NULL AND de_pct IS NULL AND a_pct IS NULL`; `veredicto = 'mover'` exige los tres `NOT NULL` y `de_pct <> a_pct`.
- `UNIQUE (cycle_id, campana_id)`
- Indice: `CREATE INDEX ubicacion_evaluacion_campana_idx ON ubicacion_evaluacion (platform, campana_id, id DESC);`
- Permisos: `SELECT` a `app_read, app_decide, app_ingest, app_admin`; `INSERT` y uso de secuencia a `app_decide`.

### Tabla `ubicacion_sin_aplicar`

Append-only (`prohibir_mutacion()`), registra por que una evaluacion `live` con `veredicto = 'mover'` no se envio:

- `evaluacion_id BIGINT PRIMARY KEY REFERENCES ubicacion_evaluacion(id)`
- `motivo TEXT NOT NULL CHECK (motivo IN ('cuota_agotada', 'tope_campanas_en_espera', 'cambio_externo', 'config_cambio', 'modo_no_live', 'error_put', 'producto_sin_evidencia', 'ajuste_en_curso', 'pujas_pendientes'))`
- `registrado_el TIMESTAMPTZ NOT NULL`
- Permisos: `SELECT` a `app_read, app_decide, app_admin`; `INSERT` a `app_decide`.

### Extension a `campana_ajuste`

- `ALTER TABLE campana_ajuste ADD COLUMN evaluacion_id BIGINT NULL REFERENCES ubicacion_evaluacion(id);`
- `CREATE UNIQUE INDEX campana_ajuste_evaluacion_uniq ON campana_ajuste (evaluacion_id) WHERE evaluacion_id IS NOT NULL;`
- Reemplazar `campana_ajuste_0067_solo_confirmado()` para prohibir tambien cambios sobre `evaluacion_id`.
- Agregar trigger `BEFORE INSERT` `campana_ajuste_0104_frontera_roles()`:
  - Si `pg_has_role(current_user, 'app_decide', 'MEMBER')` y no es `app_admin`: exige `evaluacion_id IS NOT NULL`, `clase = 'ajuste_ubicacion'`, `regresa_a IS NULL`, `go_literal = 'AUTO_UBICACION'`, `actor = 'orbit_ubicacion'`, y que la fila referenciada en `ubicacion_evaluacion` tenga `modo = 'live'`, `veredicto = 'mover'`, `campana_id = NEW.campana_id`, `platform = NEW.platform` y `antes_config_id = NEW.antes_config_id`.
  - Si no (camino `app_admin`): exige `evaluacion_id IS NULL`.
- Permisos nuevos: `GRANT INSERT, UPDATE (confirmado_el) ON campana_ajuste TO app_decide;`, `GRANT INSERT ON ads_campana_config_observation TO app_decide;`, `GRANT USAGE, SELECT ON SEQUENCE campana_ajuste_id_seq, ads_campana_config_observation_id_seq TO app_decide;`.

### Cuotas en `apply_cap_de_config`

Extender `apply_cap_de_config(p_motor TEXT)` con los dos mapeos:

- `'ads_optimizer:amazon_us:ubicacion' -> 'ads_apply_cap_amazon_us_ubicacion'`
- `'ads_optimizer:amazon_mx:ubicacion' -> 'ads_apply_cap_amazon_mx_ubicacion'`

Sin clave presente en `config_version.settings`, devuelve `NULL` y la cuota falla cerrada (cero applies).

## 6. Politica pura (`app/optimizer/ubicacion.py`)

Identificador de politica: `POLITICA = "ubicacion_v1"`.

### Tipos del dominio

```python
Ubicacion = Literal["arriba_de_busqueda", "resto_de_busqueda", "paginas_de_producto"]

@dataclass(frozen=True)
class EvidenciaUbicacion:
	ubicacion: Ubicacion
	ajuste_vigente_pct: int  # NULL normalizado a 0 en el lector
	clics: int | None
	gasto: Decimal | None
	pedidos: int | None
	venta: Decimal | None
	pedidos_inmaduros: int | None  # ultimos dias D-2..D-1; None abstiene recortes sin ventas

@dataclass(frozen=True)
class MetaCampana:
	exigente_pct: Decimal  # menor meta congelada entre las hojas activas de la campana
	laxa_pct: Decimal      # mayor meta congelada entre las hojas activas de la campana
	procedencias: tuple[str, ...]

@dataclass(frozen=True)
class CasoCampana:
	campana_id: int
	plataforma: Literal["amazon_mx", "amazon_us"]
	config_id: int
	estrategia: str | None
	ventana_desde: dt.date
	ventana_hasta: dt.date
	ubicaciones: tuple[EvidenciaUbicacion, ...]  # exactamente las 3 ubicaciones ajustables
	meta: MetaCampana | None
	economia: EconomiaPlataforma
	gasto_campana: Decimal | None
	gasto_placements_total: Decimal | None  # suma de las 4 ubicaciones en la misma ventana
	placements_frescos: bool
	dias_desde_ultimo_cambio: int | None
	ajuste_en_vuelo: bool
	elegible: bool
	en_impulso: bool
	solo_bajadas: bool
	dias_topados_7d: int | None
	topada: CampanaTopada
	fase_temporada: Literal["baja", "normal", "alta"] | None

@dataclass(frozen=True)
class MantenerUbicacion:
	motivo: str

@dataclass(frozen=True)
class MoverUbicacion:
	ubicacion: Ubicacion
	de_pct: int
	a_pct: int
	motivo: str
```

### Settings (fail-closed)

| Clave | Tipo y dominio | Ausente | Valor invalido |
|---|---|---|---|
| `ads_ubicacion_<platform>` | `"off"`, `"shadow"`, `"live"` | `"off"` | `ValueError` |
| `ads_ubicacion_solo_bajadas_<platform>` | `"true"` o `"false"` (o bool) | `True` en `amazon_us`, `False` en `amazon_mx` | `ValueError` |
| `ads_ubicacion_max_en_espera_<platform>` | entero `>= 0` | `6` en `amazon_mx`, `2` en `amazon_us` | `ValueError` |
| `ads_apply_cap_<platform>_ubicacion` | entero en `[0, CAP_MAXIMO]` | `None` (0 applies en live) | `ValueError` |

El modo efectivo del ciclo para una campana es `min(ads_optimizer_mode, ads_ubicacion_<platform>, goal.mode)` bajo `off < shadow < live`.

### Constantes de la politica

- `DIAS_ENFRIAMIENTO = 14`
- `DIAS_VENTANA_MAX = 30`
- `DIAS_REZAGO = 3` (`ventana_hasta = decided_at.date() - 3d`)
- `DIAS_VENTANA_MIN = 10`
- `PEDIDOS_MIN_SUBIDA = 3` (importado de `politica.PEDIDOS_GRUPO`)
- `CLICS_MIN_SUBIDA = 20` (importado de `politica.CLICS_NUEVOS`)
- `CLICS_MIN_RESTO = 20`
- `MULT_SUBIDA = Decimal("0.85")`
- `MULT_BAJA_SUAVE = Decimal("1.15")`
- `MULT_BAJA_FUERTE = Decimal("1.35")`
- `RAZON_RPC_MIN_SUBIDA = Decimal("1.15")`
- `PASO_SUBIDA_PTS = 15`
- `PASO_BAJA_SUAVE_PTS = 15`
- `PASO_BAJA_FUERTE_PTS = 25`
- `TOLERANCIA_CONCILIACION_PCT = Decimal("0.01")`
- `TOLERANCIA_CONCILIACION_ABS = Decimal("0.05")`

### Techos por estrategia y ubicacion

| Estrategia (`estrategia_puja`) | `arriba_de_busqueda` | `resto_de_busqueda` | `paginas_de_producto` |
|---|---:|---:|---:|
| `LEGACY_FOR_SALES`, `MANUAL` | `100` | `50` | `50` |
| `AUTO_FOR_SALES` | `50` | `25` | `25` |

### Ventana de datos y deteccion de ultimo cambio

- En `ads_campana_config_observation`, ordenar por `observed_at ASC, id ASC`. Normalizar `NULL` a `0` en los tres porcentajes. La primera observacion de la campana no cuenta como cambio.
- `ultimo_cambio_fecha` es la fecha UTC de la observacion mas reciente donde cambio `estrategia_puja` o alguno de los tres porcentajes respecto a la observacion previa. Esto unifica cambios automaticos, manuales en Orbit, reversas y cambios en consola de Amazon.
- `dias_desde_ultimo_cambio = (decided_at.date() - ultimo_cambio_fecha).days` (o `None` si nunca cambio).
- Ventana de evaluacion: `ventana_hasta = decided_at.date() - 3d`. Si `ultimo_cambio_fecha is None`, `ventana_desde = ventana_hasta - 29d` (30 dias). Si existe `ultimo_cambio_fecha`, `ventana_desde = max(ventana_hasta - 29d, ultimo_cambio_fecha + 1d)`. Asi el segundo paso nunca usa datos anteriores al primero.

### Orden de evaluacion en `decide_ubicacion(caso)`

1. Compuertas de campana (primer motivo que aplique devuelve `MantenerUbicacion`):
   - `not caso.elegible` -> `campana_no_elegible`
   - `caso.en_impulso` -> `campana_en_impulso`
   - `caso.estrategia is None` -> `sin_dynamic_bidding`
   - `caso.estrategia not in TECHOS_POR_ESTRATEGIA` -> `estrategia_desconocida`
   - `caso.ajuste_en_vuelo` -> `ajuste_en_vuelo`
   - `caso.dias_desde_ultimo_cambio is not None and caso.dias_desde_ultimo_cambio < 14` -> `en_enfriamiento_14d`
   - `not caso.placements_frescos` o dias de ventana `< 10` -> `datos_placements_incompletos`
   - `caso.gasto_campana is None` o `caso.gasto_placements_total is None` -> `datos_placements_incompletos`
   - `abs(caso.gasto_placements_total - caso.gasto_campana) > max(Decimal("0.05"), Decimal("0.01") * caso.gasto_campana)` -> `descuadre_placements_campana`
   - `caso.meta is None` -> `sin_meta_campana`

2. Evaluacion de recortes (prioridad sobre subidas; solo ubicaciones con `ajuste_vigente_pct > 0`):
   - Si cualquier metrica requerida es `None`, esa ubicacion se abstiene.
   - Con pedidos (`pedidos >= 1`, `venta > 0`, `gasto > 0`):
     - `base_mu = Decimal(pedidos) * gasto / venta`
     - `a = Decimal(pedidos + 1)`
     - Si `gamma_p(a, base_mu / (Decimal("1.35") * meta.laxa_pct / 100)) >= economia.confianza_recorte`: candidato `-25` (`acos_sobre_meta_fuerte`).
     - Si no, si `gamma_p(a, base_mu / (Decimal("1.15") * meta.laxa_pct / 100)) >= economia.confianza_recorte`: candidato `-15` (`acos_sobre_meta`).
   - Sin pedidos (`pedidos == 0`):
     - Si `pedidos_inmaduros is None`: abstiene esa ubicacion.
     - Si `pedidos_inmaduros >= 1`: no recorta (`venta_reciente`).
     - Si `gasto >= 2 * economia.gasto_para_concluir`: candidato `-25` (`gasto_sin_venta_doble`).
     - Si `gasto >= economia.gasto_para_concluir`: candidato `-15` (`gasto_sin_venta`).
   - Destino: `a_pct = max(0, ajuste_vigente_pct - paso)`.
   - Si hay uno o mas candidatos de recorte, elige el de mayor exceso `gasto - (meta.laxa_pct / 100) * venta` (desempate fijo: `paginas_de_producto`, `resto_de_busqueda`, `arriba_de_busqueda`) y devuelve `MoverUbicacion`.

3. Evaluacion de subidas (solo si hubo cero candidatos de recorte):
   - Si `caso.solo_bajadas`: devuelve `MantenerUbicacion("solo_bajadas_activo")`.
   - Si `caso.fase_temporada == "baja"`: devuelve `MantenerUbicacion("temporada_baja_sin_subida")`.
   - Si `caso.dias_topados_7d is None`: devuelve `MantenerUbicacion("presupuesto_sin_medir")`.
   - Si `caso.dias_topados_7d >= caso.topada.dias`: devuelve `MantenerUbicacion("campana_topada")`.
   - Por cada ubicacion con `ajuste_vigente_pct < techo(estrategia, ubicacion)`:
     - Exige metricas no `None`, `pedidos >= 3`, `clics >= 20`, `venta > 0`, `gasto > 0`.
     - Exige `gamma_q(Decimal(pedidos + 1), (Decimal(pedidos) * gasto / venta) / (Decimal("0.85") * meta.exigente_pct / 100)) >= economia.confianza_subida`.
     - Resto de la campana (suma de las otras dos ubicaciones ajustables): exige `clics_resto >= 20`, `venta_resto is not None`, `gasto_resto is not None`, y ventaja de RPC de al menos 15 por ciento (`venta * clics_resto * 100 >= 115 * venta_resto * clics`). Si `venta_resto > 0`, exige tambien mejor ACoS (`gasto * venta_resto < gasto_resto * venta`).
     - Destino: `a_pct = min(ajuste_vigente_pct + 15, techo)`.
   - Si hay uno o mas candidatos de subida, elige el de mayor holgura `(meta.exigente_pct / 100) * venta - gasto` (desempate fijo: `arriba_de_busqueda`, `resto_de_busqueda`, `paginas_de_producto`) y devuelve `MoverUbicacion(motivo="bajo_meta_con_ventaja")`.
   - Si ninguna ubicacion califica, devuelve `MantenerUbicacion("sin_evidencia_ubicacion")`.

## 7. Ciclo, concurrencia, reconciliacion y reversa

1. **Reconciliacion previa a TX2:** `reconcilia_automaticos` busca filas de `campana_ajuste` con `evaluacion_id IS NOT NULL AND confirmado_el IS NULL`, consulta Amazon en vivo por `/sp/campaigns/list` y sella `confirmado_el` + guarda observacion cuando el campo coincide con el destino.
2. **Lectura en TX2 (`cycle_recorrido.py`):** si `ads_ubicacion_<platform> != "off"`, despues del ciclo de hojas, `lee_casos` construye los `CasoCampana` usando la banda de metas de `contadores.targets` (solo hojas de esa campana) y la misma foto de `conn`.
3. **Guardado en TX3:** despues de `_descarta_sin_evidencia`, cualquier campana cuya hoja perdio su meta por fallo al guardar `evaluacion_producto_ciclo` se reemplaza con veredicto `mantener` y motivo `producto_sin_evidencia` antes de insertar en `ubicacion_evaluacion`.
4. **Aplicacion en TX4 (`cycle_apply.py` -> `apply_ubicacion.py`):**
   - Solo corre si `modo_efectivo == "live"`, `origen` completo su fase de pujas sin pendientes ni fallo, y existen evaluaciones `mover` del ciclo sin fila previa en `campana_ajuste` ni `ubicacion_sin_aplicar`.
   - Orden determinista: recortes primero (mayor exceso), luego subidas (mayor holgura), desempate por `campana_id`.
   - Por cada evaluacion:
     1. Releer configuracion vigente y modo efectivo. Si cambio config -> `ubicacion_sin_aplicar("config_cambio")`. Si ya no es live -> `ubicacion_sin_aplicar("modo_no_live")`.
     2. Contar campanas distintas con `campana_ajuste` confirmado de `ajuste_ubicacion` en los ultimos 7 dias. Si `>= max_en_espera` -> `ubicacion_sin_aplicar("tope_campanas_en_espera")`.
     3. Cobrar cuota diaria (`consume_quota_y_sello(conn, platform, "ubicacion")`). Si agotada -> `ubicacion_sin_aplicar("cuota_agotada")`.
     4. Abrir conexion corta dedicada con `autocommit=False` (`dsn` de `orbit_decide`). Insertar fila en `campana_ajuste` con `evaluacion_id`, `huella = f"auto:{evaluacion_id}:{plan.huella()}"`, `actor = "orbit_ubicacion"`, `go_literal = "AUTO_UBICACION"` y hacer `commit()` antes del HTTP.
     5. Llamar `_aplica_y_confirma` con la conexion sin autocommit. Dentro del candado `pg_try_advisory_xact_lock`, leer Amazon en vivo y verificar que tanto `estrategia_puja` como el porcentaje actual de esa ubicacion coinciden con `antes` (si difieren, aborta sin `PUT` y registra `cambio_externo`). Enviar `PUT` de un solo placement, verificar readback, sellar `confirmado_el` y guardar la observacion mezclada.
5. **Reversa:** `regresa_ajuste_campana` (y el boton **Regresar** de **Donde poner el dinero**) ya funciona sobre cualquier fila confirmada de `campana_ajuste` de clase `ajuste_ubicacion`. La fila hija se inserta como `app_admin` con `evaluacion_id = NULL` y `regresa_a = ajuste_id`.

## 8. Interfaz y visibilidad

- **Donde poner el dinero (`app/pantalla_dinero.py`, `app/templates/donde_poner_el_dinero.html`):** cada fila de campana lee su evaluacion mas reciente de `ubicacion_evaluacion` (si tiene `veredicto = 'mover'` y su `antes_config_id` sigue vigente y sin ajuste posterior). Muestra una linea breve con modo (`practica` o `automatico`), ubicacion, porcentaje de y a, y motivo en espanol junto al boton actual **Cambiar ajuste de ubicacion**.
- **Salud (`app/dashboard_salud.py`, `app/api_dashboard.py`):** al agregar `"ubicacion"` a `KINDS_QUOTA`, la tabla de topes diarios muestra la cuota de ubicaciones automaticamente. Se agrega ademas el conteo del ultimo ciclo (`evaluadas`, `mover`, `en_practica`, `aplicadas`).

## 9. Compuerta de encendido a `live`

Antes de cambiar `ads_ubicacion_<platform>` de `shadow` a `live`:

1. Ejecutar un ajuste manual real de `ajuste_ubicacion` y su **Regresar** en una campana de MX y otra de US, verificando readback y `campana_ajuste` en produccion (Regla 7).
2. Acumular al menos 14 dias de evaluaciones `shadow` y correr el verificador de replay determinista (`tools/auto04_verifica.py`) con cero discrepancias.
3. Sembrar explicitamente `ads_apply_cap_amazon_mx_ubicacion` (`2`) y `ads_apply_cap_amazon_us_ubicacion` (`1`) con aprobacion de David. En US mantener `ads_ubicacion_solo_bajadas_amazon_us = true` durante la primera etapa.
