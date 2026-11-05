# Lector fresco escéptico, pasada 1 (2026-11-05, master ff28eb33)

Reporte recibido tal cual del lector (opus). Las correcciones aplicadas se anotan en `lectores/sintesis.md`.

## Bloqueantes

1. Un despliegue que aborta en una guarda lanza un rollback que deshace el despliegue anterior; reintentar exige cambiar código (B.1, B.2). Evidencia: `effects.py:709-717` corre rollback ante cualquier salida ≠0; nada limpia `ultimo.env` antes de las guardas; 18 crons con `app.cli` en goncloud (`crontab -l -u gon`) y la ingesta de las 07:10 dura 9-17 min; reentregar el mismo SHA da `sin_cambios` (`effects.py:449-455`); `ls /mnt/data/appdata/orbit/despliegue` no existe.
2. El bloque B completo pasa con cuatro scripts `exit 0`; B.5 pasa con un desplegar que no hace nada. Evidencia: copia en scratchpad `wtB`, todas las Acepta y la prueba-real de B salen 0.
3. `sonda.sh` como la define B.4 da verde con SQL roto o ssh caído. Evidencia: implementación literal en scratchpad `sonda_literal.sh`: tabla inexistente rc=0, host inexistente rc=0.
4. La prueba-real de F (`meta_live`) falla justo después del despliegue o pasa sin revisar nada. Evidencia: los 12 ciclos live corren 08:40/08:41 UTC; recién desplegado F el último ciclo tiene la meta en shadow. SQL de solo lectura: 0 goals con target en producción; 5 ad groups exclusivos con margen >0 y 76 hojas ENABLED en MX.
5. Una falla del camino de productos queda escondida en sombra y se vuelve apagón del optimizador al encender (D.4, E, B.1 (6), F.2). `replay` da `diferencias: 0` con un ciclo sin evaluaciones; `estricto=True` en TX3 (`cycle.py:2852`) pierde todas las decisiones de la plataforma; `--cycle-id ultimo` solo ve MX.
6. `concilia` no puede pedir la ventana de la meta en un reporte: la API permite 31 días (`reports.py:296`, `MAX_RANGO_DIAS = 31`; `_validar_rango` rechaza 87 y 60 días).
7. La evidencia previa a encender (F.1, I.2) es un archivo que escribe el implementador; pasa sin correr replay ni concilia (ensayado: rc=0 con las cuatro líneas a mano).
8. Tras un apagado manual, el checklist hace fallar y revertir cada despliegue siguiente (B.1 (6), B.2, B.3).

## No bloqueantes

9. Pantallas con compuertas que no prueban nada (E.1 import, E.2 `--help` y `ls` de directorio vacío, H.5 grep, H.9 grep y `--help`).
10. Migraciones de otros planes sin cabecera traban AUTO-03 (B.1 paso 4).
11. La simulación H.8 no muestra nada ~60 días tras D; el aviso de F promete "ya pujan con la meta" con posible cobertura cero.
12. B.1 (6) no dice dónde se guarda el valor previo de cada clave.

## Verificado y correcto (resumen del lector)

motor valida ok; A.0 sale 0 y A.1/A.2 distinguen; citas de goals.py, cycle.py, apply_cola.py, config_write.py, api_write.py, retiro_anuncios.py, api_fabrica.py, impulso_io.py, apply_harvest_reconciliacion.py, replay.py, windows.py, evaluacion_catalogo.py, pantalla_productos.py y migraciones 0002/0040/0063/0066/0069 correctas; constantes F_PAUSE, LEGACY_NEGATIVE, NEGATIVE_COST_MIN, PAUSE_COST_MIN correctas; en producción existen ORBIT_DSN_ADMIN/DECIDE/READ en el contenedor, meli_sku_alias, el CHECK con cinco peldaños, `ad_ids` como arreglo jsonb en 597 177 filas y filas `written` de productos desde 2026-09-25; `app_read` tiene los SELECT que el plan usa; el dashboard responde 200 sin token; las pruebas `-k procedencia` y `-k dockerfile` existen; Postgres 16 local y `uv` disponibles para el motor en la Mac; tope de verificación 3600 s y de despliegue 900 s; el daemon ejecuta acciones en serie; las métricas de producto casi no cambian entre reobservaciones.
