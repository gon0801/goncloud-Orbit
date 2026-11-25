# Recorrido de pantallas del SHA conjunto (ARREGLOS 05 Z)

SHA verificado: `7b06778d376eb5631b0a2863beea6a90ab804da0`.

Copiloto de control: `.cursor/skills/verify-orbit` leido antes del recorrido
(`SKILL.md` y las fichas de las pantallas). Instancia: copia limpia del SHA en
`/tmp/orbit-z/repo` (WSL Ubuntu), Python 3.12.16, PostgreSQL 16.15 del
laboratorio (`127.0.0.1:55449`), puerto 18010. La instancia se cerro con
`cleanup`: no queda uvicorn ni base `orbit_verify_z%`.

## Arranque y doctor

```text
launch --run-id z-cierre
{"run_id": "z-cierre", "pid": 821353, "port": 18010,
 "base_url": "http://127.0.0.1:18010", "db": "orbit_verify_z_cierre"}
doctor
{"run_id": "z-cierre", "ok": true, "pid": 821353, "port": 18010,
 "health": "{\"status\":\"ok\"}", "fallos": []}
```

## Diez drives del mapa

| Pantalla | Checks funcionales | Resultado |
|---|---|---|
| Resumen | HTTP 200, `data-pantalla=resumen`, series US/MX, contrato `datos-resumen`, periodo y rango, CSP, tabla enfocable | Pasa; solo PNG pendiente por entorno |
| Campanas | 200, fila `Campana A`, JSON gemelo, filtro MX/US, orden por coste, huecos sin chip | Pasa; solo PNG pendiente por entorno |
| Decisiones | 200, 2 decisiones, timeline, filtro, paginador, motivos, JSON gemelo | Pasa completo |
| Salud | 200, tarjetas, pestanas US/MX, ciclos, skips, cuota, historico 14d, JSON | Pasa; solo PNG pendiente por entorno |
| Contribucion | 200, tarjetas, sin filas, sin estilos inline, JSON y ventana | Pasa; solo PNG pendiente por entorno |
| Propuestas | 200, crecen/recortan, `por que`, `zapato blanco`, veto, token, JSON | Pasa completo |
| Inertes | 200, vacio de semilla, KPIs, lotes, JSON | Pasa completo |
| Crear campanas | 200, comparador, formulario, token, aviso de activas, catalogo y lotes | Pasa completo |
| Settings | 200, token, plataforma US, peldano, target 25, sin goals de campana, JSON | Pasa completo |
| Productos sin clics | decisiones, grupos, limites simular/guardar, pausa encender/volver en practica, JSON | Pasa completo |

Los cuatro `ok=false` corresponden solo a las claves `screenshot*`: el helper
lanza Chrome con `--user-data-dir=/tmp/orbit-foco-<pid>` y el unico navegador de
esta PC es Edge de Windows, que no puede usar un perfil de ruta Linux. Todos los
checks HTML, JSON, CSP y de cableado de esas cuatro pantallas salieron en `true`.

Para no perder la evidencia visual, se capturaron las cuatro paginas con Edge
headless de Windows contra la misma instancia (mismo SHA, misma base):
`resumen-1440.png`, `campanas-1440.png`, `salud-1440.png` y
`contribucion-1440.png`, las cuatro de mas de 60 KB.

## Casos adversos

- **Settings**: `docs/evidencia/arreglos-05/I/adverso.py` con Edge real contra
  `orbit_verify_z_cierre`. Resultado `ok=true`: `1,000` no envia, error visible,
  cero POST, sin version nueva, sin recarga; `7` si intenta el POST y el unico
  error es la falta de token del servidor de verificacion. Salida en
  `ui/settings-adverso.txt`.
- **Productos**: el drive cubre limites simulados frente a vigentes (422 con
  minimo), `simular_no_guarda`, boton de encendido deshabilitado en practica,
  habilitado con limites, simulacion visible y volver a practica. No se hace
  POST de encendido.
- **Resumen**: el caso adverso de datos desconocidos frente a cero es
  `test_16_resumen_no_afirma_ausencia_de_ventas_que_api_reporta`, verde en la
  aceptacion integrada (`Z/acceptance.xml`).

## Evidencia

`Z/ui/` conserva `launch-ready.txt`, `doctor.json`, los `PROOF.json` y HTML/JSON
de los diez drives, los cuatro PNG y `settings-adverso.txt`.
