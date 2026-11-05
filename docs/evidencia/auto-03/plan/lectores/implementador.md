# Lector fresco implementador, pasada 1 (2026-11-05, master ff28eb33)

Reporte recibido del lector (opus), resumido por hallazgo. Las correcciones aplicadas se anotan en `lectores/sintesis.md`.

## Bloqueantes

1. Si `desplegar.sh` aborta en una guarda, `rollback.sh` deshace el release anterior. Evidencia: `effects.py:700-735`; a las 07:16 UTC `docker top orbit-app-1` mostraba `app.cli ingest diario --solo-faltantes`; `crontab -l -u gon | grep -c app.cli` = 19.
2. H.7 pide una prueba que necesita `revalida_pause_producto`, que nace en I.3; en H las filas son shadow y nunca se liberan.
3. La prueba-real de E depende de un ciclo posterior a D; `concilia` no tiene `--json`; B.1 (6) pide `replay` sin argumentos y D.5 no lo define.

## No bloqueantes

4. B.5 con base 0001..última no revisa ninguna reversa.
5. El checklist choca con el apagado manual.
6. El fixture `_db_temporal_c2a` (tests/test_apply_schema.py:885-905) se queda en 0063 y la prueba de espejo falla con seis peldaños.
7. C.2/C.4 no dicen qué `piso` ni qué `hoy` usar.
8. El Acepta de C.6 ya pasa hoy (`test_settings_escritura.py` 18 passed).
9. C.7 no define el formato de la clave de gasto, del cuerpo `limites_pausa` ni de `--gasto`.
10. C.3 obliga a inventar etiquetas de `PISA_POR_PELDANO`.
11. D.1: el trigger de ventana no dice cómo reconoce filas con pausa; la tabla nueva rompe `verify/Launch.md:42` y `verify/Doctor.md:23` (`TABS = 88`) que revisa `tests/test_schema_docs.py`.
12. Un error SQL de la lectura de productos en sombra aborta TX2 entera; falta SAVEPOINT.
13. La prueba-real de D no comprueba que el interruptor quedó en shadow.
14. Ninguna compuerta ejercita las pantallas; `orbit-verify launch` necesita `.venv/bin/python`.
15. E.4 no dice cómo sacar el JSON del contenedor.
16. `concilia` no define ventana ni tolerancia; el halo de 87 días incluye fechas inmaduras.
17. F.1 e I.2 solo buscan con grep el texto que la fila escribe.
18. Las sondas `meta_live`, `pausa_sombra` y `pausa_live` pasan en vacío.
19. F.2 no acota el falla cerrado; F.3 compara una huella que F.2 no congela.
20. S6 vuelve inertes las etapas (5) y (6) de A.3 y quizá la meta por 90 días; A.3 no da el comando.
21. I.2 manda correr en producción código que aún no está desplegado.
22. I.1 cita como pruebas los DO-blocks de 0066/0069.
23. Un pedazo del Acepta de I.8 ya pasa hoy (`grep decisiones-producto-01 plans/ROADMAP.md`).
24. Los Acepta corren con CPython 3.14.7 (`uv run --frozen` en copia limpia) y producción con 3.12.16.

## Verificado y correcto (resumen del lector)

motor valida ok; `uv run --frozen` crea `.venv` sin red en copia limpia; Postgres 16.15, psql, pg_dump y flock en la Mac; pruebas vigentes en verde (`test_compose_deploy -k dockerfile` 2, `test_evaluacion_catalogo` 34, `test_optimizer_goals` 55, `test_settings_escritura` 18, `test_apply_schema -k procedencia` 2, `test_cycle`+`test_cycle_target_ciclo` 74, retiro+impulso+fabrica 288, `test_apply_cola` 42); `-k limites` sale rc 5; A.0 pasa y A.1/A.2 distinguen; líneas y constantes citadas coinciden; `v_margen_producto` trae las columnas de `medicion_desde_fila`; en producción `ad_ids` es arreglo jsonb, `min(metric_date)` 2026-08-08, `ads_report_result` sin columnas de rango, 80 `written` de productos, sin claves `ads_producto_*`; contenedor con `ORBIT_DSN_ADMIN`/`READ`, workdir `/app`, `/tmp` escribible; 0076 libre; `$SRV/despliegue/sha` no existe; dependencias completas salvo el hallazgo 2.
