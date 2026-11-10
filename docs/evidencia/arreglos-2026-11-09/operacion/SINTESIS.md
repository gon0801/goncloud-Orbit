# Síntesis — frente operación (J-H1, H-H2, H-H3, J-H4)

Candidatos: `opus/DISENO.md` (544 líneas) y `sonnet/DISENO.md` (337). Sin juez cruzado.

## Hechos medidos después de los candidatos (cambian el diseño)

- Crontab real de `gon` leído el 2026-11-09 (`crontab-gon.txt`, 77 líneas). Las tareas ya
  llevan `cron-avisa`: structure, avisos-campana, impulso-vigia, cycle US y MX, jev-senales,
  jev-vigia, ads-salud (`--tras 3`), precios, costos (`refresh_costos.sh` ya envuelto).
  Sin envolver: `ingest diario` x3 y `spapi-diario.sh`. Forma real:
  `cron-avisa --nombre "<frase>" --cada 24 -- /bin/bash -c '<flock -n -E 75 ...> docker exec ...'`.
- `cron-avisa` leído (`cron-avisa.txt`, 226 líneas). Estado por nombre. Avisa al llegar a N
  fallos seguidos y manda «ya volvió a correr bien» en la primera salida 0 después de un aviso.
  La salida 75 no toca el estado. `--revisa` marca atrasada una tarea con último fin más viejo
  que 1.5 × `cada` + 1 h.
- Consecuencia: si «todavía no hay métricas» sale 0, al día siguiente de un día perdido el
  primer intento manda un «ya volvió a correr bien» falso. La espera debe salir 75.

## Decisión por hallazgo

| Hallazgo | Base | Injerto | Rechazado |
|---|---|---|---|
| J-H1 instalador borra los avisos | sonnet: `tools/despliegue/crontab-orbit.txt` + `cron_orbit.sh instala\|revisa` (mismo molde que `cron_jev_vigia.sh`); `desplegar.sh` corre `revisa` | opus: guarda `test -x /usr/local/bin/cron-avisa` y «el número de envoltorios nunca baja, si baja ABORTA». Se borra el heredoc de `docs/DEPLOY.md:1160-1212` y queda una línea que apunta al script | opus: dejar el heredoc del documento como fuente. Un documento que instala es como nació la deriva |
| H-H2 un solo horario al día | opus: horas `8,10,13,16` en la misma línea de cada tarea, `fin_de_intentos()` derivado de `HORARIOS_INGESTA` + `TOPE_PARED_SEGUNDOS` (13:15 + 2 h 45 = 16:00, comprobado en `app/ads/diario.py:56,60`) | propio, por lo medido: antes del fin sale 75 (no toca el estado del aviso); después del fin sale 1 | sonnet: banderas `--reintento` y `--ultimo`. Atan crontab y código, y si se instalan en mal orden argparse sale 2 |
| H-H3 vigilante calla una falla | sonnet: una condición en `faltantes` (`ok is False` no cuenta como presente) y texto «falló o no corrió»; se invierte `tests/test_spapi_vigilante.py:59` | ninguno | opus: `Estado`/`Par`/`evalua`. Mejor mensaje, pero tres tipos nuevos para un aviso que lleva a la misma acción |
| J-H4 despliegue corta un ciclo | opus: `recrea_contenedor()` construye la imagen aparte y en UN comando remoto espera cero procesos `app.cli` y recrea; devuelve 0, 75 o error; se borra `compose_up` y migran sus dos llamadores | sonnet: en reversa espera 300 s y recrea de todos modos con `AVISO` (una reversa no puede quedar bloqueada por un ciclo) | sonnet: releer guardas en llamadas ssh separadas. Deja ventana entre la lectura y el `up` |

## Forma final

```python
# app/metricas_listas.py
SALIDA_ESPERA = 75  # mismo código que cron-avisa trata como «saltada»: no cuenta ni como fallo ni como éxito

def fin_de_intentos(hoy: dt.date) -> dt.datetime:
    """Último HORARIOS_INGESTA + TOPE_PARED_SEGUNDOS, en UTC."""
    raise NotImplementedError

def salida_sin_metricas(comando: str, *, ahora: dt.datetime | None = None) -> int:
    """Antes del fin: imprime TEXTO_ESPERA y devuelve 75. Después: stderr y 1."""
    raise NotImplementedError
```

Llamadores: `app/cli.py:113`, `app/cli_bids.py:74-76,125`, `app/jev_senales.py:809`.

```bash
# tools/despliegue/cron_orbit.sh instala|revisa   (bloque = tools/despliegue/crontab-orbit.txt)
#   instala: exige cron-avisa en el host; cuenta envoltorios antes y después; si bajan, ABORTA y no escribe
#   revisa:  compara el bloque del host contra el archivo; sale 1 con el diff si difieren
# tools/despliegue/comun.sh
recrea_contenedor() { :; }   # adelante: 0 | 75 sin recrear | error.  reversa: espera 300 s y recrea con AVISO
```

`crontab-orbit.txt` se siembra con las líneas 57-77 de `crontab-gon.txt` (bloque Orbit real),
con las horas `8,10,13,16` en avisos-campana, impulso-vigia y los dos `cycle`.

## Orden y dependencias

1. H-H3 (independiente, chico).
2. J-H4 (independiente).
3. J-H1 (archivo + script; `revisa` en el despliegue). La instalación en el host la hace quien
   tenga escritura en producción.
4. H-H2. Depende de J-H1 (los horarios nuevos viven en el archivo) y de E-H3 del frente
   ads-escritura: hoy `motivo_de_salto` (`app/cycle.py:258-270`, comprobado) deja reabrir un
   ciclo `failed` el mismo día, y con cuatro horarios eso pasa solo.

## Pruebas (una por unidad, regla de los 4 casos)

- J-H1: `test_el_instalador_no_quita_cron_avisa` (instalar/borrar en las máquinas). Falla hoy con la sonda `J/J5`.
- H-H2: `test_cada_consumidor_tiene_horario_tras_el_ultimo_plazo_de_ingesta` (falla que ya pasó en producción).
- H-H3: invertir la aserción existente (falla que ya pasó; sonda `H/h2`).
- J-H4: prueba con `ORBIT_SIMULACION=1` en `tests/test_despliegue_motor.py` (máquinas; Linux/CI).

## Decisiones del dueño

1. Reintentos del ciclo a las 10:40, 13:40 y 16:40 UTC (mismo minuto de hoy). Recomiendo sí.
2. `ingest diario` y `spapi-diario.sh` siguen sin aviso propio porque ya avisan por dentro. Recomiendo sí.
3. `cron-avisa` sigue viviendo solo en el servidor. Recomiendo sí; el script del repo solo exige que exista.

## Riesgos abiertos

- 75 como «espera»: comprobado en `cron-avisa`; falta comprobar que ningún otro llamador de `cycle` lea ese código (inferido que no).
- Las peticiones de la pantalla (uvicorn) también se cortan al recrear; fuera de alcance.
