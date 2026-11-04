# Panel adverso Seccion 3 (REPRICING 02): veredicto y seguimiento

Fecha: 2026-10-15. Rama: `rp02/sec3-seguridad-goals`.
Panel: 3 hijos poteto-agent (motor S / goals y G / reversa-avisos-deploy) +
sintesis; 3 revisores GLM (5.3, 5.2, 4.7) caidos por cuota agotada
(`Weekly/Monthly Limit Exhausted... reset at 2026-10-15 12:22:09`, guardado
en su archivo). Diversidad reducida: GLM sin cuota.

Resultado: 31 hallazgos (8 bloqueantes, 13 considerar, 9 anotados,
1 descartado A-N2). Tesis transversal: silencio operativo (decidir y
aplicar reportaban exito sin actuar).

## Corregidos en la ronda panel (bloqueantes, con prueba)

- B-C1+W5: `aplicar` lista omitidas con bloqueo y motivo; live con filas
  accionables pero cero escribibles exige go (422).
- A-W1: universo sin clave deja error visible (sigue off, sin fila).
- B-C2: referencia fallida en live a mano = bloqueo `sin_referencia`
  confirmable (tool: `--confirmar-salto`).
- A-W2: la excepcion de escritura alimenta el cortacircuito.
- C-W6+N5: repo publico saneado (sims 100 % sinteticas, DSN enmascarado;
  commit D.2+X.1 enmendado antes del push: nunca se publico el dato real).
- C-W1: reintento real (`desplegar-simulacion-reintento.txt`, exit=0).
- C-W2: X.1 filtra canal FBA (FBM probada intacta).
- C-W4: 7b unificado sim+real + drill real en checklist-antes-del-deploy.
- C-W3 (one-liner): `base64 -w0` en encender.sh.

Cambios del lead en revision: restaurada la guarda `modo_desconocido`
(la habia quitado el delegado), revertida la indireccion PGPASSWORD,
`grep -qFx` en 1c (probado en /tmp/prueba-1c-guion.sh).

## Seguimiento (no bloqueante: fila + PR, quality-kit regla 4)

### Considerar

- [ ] B-W1: la huella no cubre bloqueo ni motivo (siembra.py:337-358).
      Incluirlos o revalidar al aplicar.
- [ ] B-W2: liberar_compuerta fija autocommit sin rollback previo
      (api_precios_write.py:333 vs 286). Igualar el patron.
- [ ] B-W3: triple N+1 en goals (goals_write.py:389-428,278,483).
      Batch en leer_candidatos; una sola lectura en aplicar.
- [ ] B-W4: plan_goals abre ConexionEscritura admin para preview de solo
      lectura (api_precios_write.py:246-261). Usar rol de lectura.
- [ ] B-W6: precio_modo_universo sin borrado; llave obsoleta bloquea
      settings (config_write.py:167-177,283-284). Dar borrado o migracion.
- [ ] A-W3: freno_por_error cuenta huerfana_sin_efecto
      (corrida.py:205-221, cambios.py:931). Excluir lo sin efecto.
- [ ] A-W4: 6 insumos vigilados contra 5 del diseno; fee_variable puede
      auto disparar insumo_sistemico
      (compuerta.py:60-67, corrida.py:917). Declarar o alinear + dedup.
- [ ] A-W5: pendiente excluye decisiones con CUALQUIER fila cambio pero el
      docstring dice sin cambio con efecto (corrida.py:1094-1103,1078).
      Alinear codigo o docstring y decidir el reintento.
- [ ] C-W3: hecho en la ronda (ver arriba).
- [ ] C-W5: la reversa es muda (precio_reversa.py, notifica.py:1403-1414).
      Avisar el lote manual en Telegram.
- [ ] C-W7: dry run exige credenciales SPAPI (precio_reversa.py:178-188).
      Clientes perezosos.
- [ ] C-N4: 0058 quedo NOT VALID sin VALIDATE programado (~120 filas
      viejas exentas). Fechar la validacion.
- [ ] C-N6: rollback restaura la vieja al final y no verifica md5 post
      restore (rollback.sh:93-148). Restaurar posicion y verificar.

### Anotados

- [ ] B-N1: FilaPlan.envio_origen siempre None (leer_candidatos no lee
      logistica_origen). Limpieza.
- [ ] B-N2: fallback NaN sin escapar en innerHTML; detail objeto pinta
      [object Object] (precios.js:41-53,114-120,155-158). Endurecer.
- [ ] B-N3: confirmados sin max_length. Poner tope y documentar.
- [ ] B-N4: ningun test pinea el despacho de api_precios_write. Agregar pin.
- [ ] B-N5: margen_hoy sin cuantizar en display, a 4 en escrito
      (api_precios.py:488). Alinear.
- [ ] A-N1: tras parche_sin_sellar el resto live se salta sin conteo
      (corrida.py:1188-1189). Contarlo en el resumen.
- [ ] C-N1: evidencia S.5 dice 9 pruebas en rojo pero cita 8. Corregir cuenta.
- [ ] C-N2: detalle.replace es substring ciego (notifica.py:1337).
      Comparar insumo exacto.
- [ ] C-N3: COALESCE puede dar NULL y el builder imprime None
      (notifica.py:1844-1887). Sanear display.

## Triage bots PR #454 (2026-10-15)

Corregidos: selector global sin off fantasma + test; X.1 real con
`docker exec -e` (los 3 scripts); F1 500 global-sin-universo + test.
Declinados con razon en sus hilos: `AssertionError` residual en
`precio_goal.py` (pin de invariante inalcanzable).

- [ ] F2 (auto-review Low): `verificar_config.py` importa `cuota`
      borrado en S.1 (solo carga por el shim de `test_precio_d0.py`).
      Diseno S.1 declarado y sellado; si se toca, hacer el import
      condicional y retirar el shim juntos.
