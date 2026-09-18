# A.5 — Catálogo de mutantes (REPRICING 01, carril A)

Un mutante por regla y borde que la corrida protege (fila A.5 del plan +
puntos a–h del brief). Corridos con base real (`ORBIT_TEST_DSN` local),
uno por uno con restauración verificada por hash, `-p no:cacheprovider` y
caché de bytecode fresca por corrida (`PYTHONPYCACHEPREFIX=$(mktemp -d)`;
reusar el prefijo dio una muerte falsa en A5-6: el `.pyc` viejo hizo pasar
el test con el mutante puesto; con prefijo fresco muere). Sembrados a mano
y **revertidos sin commit**. **Cero sobrevivientes.**

A5-3 y A5-4 sobrevivieron primero: `repartir_cupo` (reglas puras, defensa
en profundidad) re-ordena y pre-limita, así que el test de resultado no
discrimina el cableado de la fase 2/3. Se cerraron con tests de cableado
(`test_fase3_respeta_reserva_denegada`, `test_fase2_aplica_en_orden_de_prioridad`)
y contra ellos los mutantes mueren.

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| A5-1 | Lock: dos hilos, exactamente un PATCH (claim + advisory) | `corrida._tomar_lock`: `return` antes del claim | `test_dos_hilos_exactamente_un_patch` | MUERTO (exit 1): el segundo hilo no sale `ocupado` |
| A5-2 | Orden S5: el cambio nace `pendiente` (el ack decide `enviado`) | `precio_write.cambiar_precio`: INSERT nace `'enviado'` en vez de `'pendiente'` | `test_live_subir_aplica_un_patch` | MUERTO (exit 1): el trigger `precio_cambio_nacimiento` revienta (`CheckViolation`) |
| A5-3 | Cuota: la fase 3 cobra reserva atómica por candidata | `or True` al `cuota_mod.reservar(...)` de fase 3 | `test_fase3_respeta_reserva_denegada` (cierre; el de resultado no discrimina: `repartir_cupo` ya pre-limita) | MUERTO (exit 1): con reserva negada aplica igual (`escritas=1`) |
| A5-4 | Prioridad: fase 2 ordena `\|m-goal\|*ingreso` desc | quitar el `-` del sort de fase 2 | `test_fase2_aplica_en_orden_de_prioridad` (cierre; con prioridades empatadas el mutante es invisible) | MUERTO (exit 1): `lineas` sale `[B, A]` en vez de `[A, B]` |
| A5-5 | Huérfana: la `pendiente` no-reversa se cierra a `error` al arrancar | `huerfanas = 0` (sin `cerrar_huerfanas`) | `test_huerfana_se_cierra_sin_get_y_sale_en_resumen` | MUERTO (exit 1): la huérfana sigue `pendiente` |
| A5-6 | Cierre: el `enviado` de ayer se cierra ANTES de decidir | `cerrados = {}` (sin `cerrar_por_observacion`) | `test_enviado_de_ayer_se_cierra_antes_de_decidir` | MUERTO (exit 1): el cambio de ayer queda `enviado` |
| A5-7 | Freno S6: `error` en TODOS los días previos, no en uno | `freno_por_error`: `all(...)` → `any(...)` | `test_freno_tres_dias_de_error_unidad` | MUERTO (exit 1): con 2 días de error ya frena |
| A5-8 | Sombra: el virtual nace `confirmado`/`virtual`, cero PATCH | sombra nace `'pendiente'` en vez de `'confirmado'` | `test_shadow_subir_crea_virtual_sin_patch` | MUERTO (exit 1): el virtual no nace `confirmado` |
| A5-9 | Cubo: la corrida aplica con el limitador inyectado (uno por plataforma) | `limitador=None` en la llamada a `cambiar_precio` | `test_corrida_usa_un_solo_cubo_por_plataforma` | MUERTO (exit 1): cero consumos en el cubo espiado |
| A5-10 | Segunda corrida del día no decide (UNIQUE + salto) | `if lid in decididas:` → `if False:` | `test_segunda_corrida_del_dia_no_decide` | MUERTO (exit 1): decide de más |
| A5-11 | Reversas del día consumen cupo (las cuenta el dueño) | `reversas = 0` (sin `reversas_hoy`) | `test_reversa_del_dia_consume_cupo` | MUERTO (exit 1): la reversa no reduce el cupo |
| A5-12 | Candado: corrida solo importa lo permitido (ni `httpx`) | `corrida.py` + `import httpx` | `test_corrida_solo_importa_permitido` | MUERTO (exit 1) |
| A5-13 | Candado: cero literales float en `app/precio` (dinero = Decimal) | `corrida.py` + `__MUTANTE__ = 0.5` | `test_reglas_sin_decimal_no_hay_float` | MUERTO (exit 1) |
| A5-14 | Crontab pinzado: 13:10 UTC con flock y log | `docs/DEPLOY.md`: `10 13` → `11 13` | `test_linea_crontab_en_deploy` | MUERTO (exit 1) |
| A5-15 | Umbral: cap fuera de cota [0, 20] aborta al arrancar | `cuota.validar_cap`: `if not 0 <= cap <= 20:` → `if False:` | `test_umbral_fuera_de_cota_aborta_al_arrancar` | MUERTO (exit 1): cap 21 no aborta |

## Notas

- A5-6 pasó una vez con el mutante puesto por `.pyc` viejo (mismo
  `PYTHONPYCACHEPREFIX` para toda la corrida). Con prefijo fresco por
  mutante muere. Lección: pycache fresca por corrida, como pide el brief.
- A5-2 lo mata la base (trigger `precio_cambio_nacimiento`), no el
  código: defensa en profundidad declarada.
- A5-9 valida la inyección del cubo: la corrida no construye `CuboTasa`
  (el 0.5/s de Pricing vive en el CLI); el mutante simula ignorar el
  inyectado.
