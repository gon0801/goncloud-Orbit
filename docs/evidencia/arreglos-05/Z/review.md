# Revision global del SHA integrado (Z.3)

Estado: **pendiente de la revision del revisor asignado**. El implementador no
puede firmar su propia revision. Este archivo fija el alcance, los caminos
prioritarios y el criterio; los hallazgos y el veredicto los escribe el revisor.

- SHA revisado: `7b06778d376eb5631b0a2863beea6a90ab804da0`.
- Implementador: `opencode-go/deepseek-v4.1-flash` (OpenCode Go).
- Revisor asignado: Astra (`gpt-6.2-astra`) o el alterno que anuncie el
  coordinador, siempre distinto al implementador.
- Politica: `AGENTS.md`, tope de 3 rondas. Solo un P0/P1 real (uso normal roto,
  seguridad o secretos, corrupcion o perdida de dinero o datos ante operacion
  normal o fallo simple) bloquea. Esquinas de 2 o mas fallos encadenados y notas
  estructurales van a "Seguimiento (P2)".

## Alcance

- 284 archivos de runtime en `app/` (`.py`, `.js`, `.html`, `.css`): `ads/` 20,
  `meli/` 9, `optimizer/` 24, `precio/` 19, `spapi/` 13, `static/` 23,
  `templates/` 22 y 154 en la raiz de `app/`.
- Modulos nuevos o tocados por TEMPORADA: `app/api_temporada.py`,
  `app/cycle_temporada.py`, `app/dashboard_temporada.py`, `app/temporada_io.py`,
  mas `temporada_*` en el ciclo y la cola.
- Fronteras: cliente Ads protegido (`app/ads/client.py`, `app/ads/write.py`),
  ledger y estados (`app/apply*.py`, `app/apply_estados.py`), ingestas
  (`app/estimacion_ingest.py`, `app/ingest*.py`), API y plantillas del
  dashboard, y migraciones `0093` a `0099`.
- Rutas ejecutables: routers `api_*.py` montados en `app/main.py`, ademas de
  `/health`. CLI `app.cli` y `tools/` que escriben por el cliente protegido.

## Caminos prioritarios (patron de los P1 de las rondas anteriores)

1. Que todo efecto pase por la tabla de estados: intencion antes del HTTP,
   transicion unica, sello terminal, y ningun camino paralelo que escriba sin
   estado.
2. Guardas que no se adelanten a una lectura: una guarda no debe decidir sobre
   datos que aun no se leyeron ni saltarse la conciliacion previa.
3. Valores que no se pierdan: bid previo, identidad del ACK, target base live,
   observed_at original y ventana bitemporal.
4. Acumulacion de condiciones: modo global, temporada, practica shadow, apagador
   por producto y cupo de 150. Verificar que la combinacion no abre un efecto no
   autorizado ni cierra uno que debia seguir abierto.

## Lo que el implementador ya comprobo en este SHA

- Aceptacion integrada 31/31, cero errores y cero omisiones (`Z/acceptance.xml`).
- Regresiones `N a`..`N j` verdes; `N k` sin casos por decision K.2.
- Controles 01, 08, 09, D2 y 01X verdes; arquitectura y lint verdes.
- UI del mapa y casos adversos de Settings, Productos y Resumen sin fallo de
  producto (`Z/ui.md`).
- Secretos sin hallazgos nuevos (`Z/secretos.txt`).

Nada de esto sustituye la revision independiente. El veredicto PASA exige cero
P0/P1 reales abiertos segun el revisor.

## Hallazgos

_Pendiente: los escribe el revisor asignado con archivo, camino, severidad
(P0/P1 frente a P2) y prueba o argumento._

## Veredicto

_Pendiente. No se declara PASA antes de la revision. Con K abierto para Amazon
US, el resultado global del plan es PARCIAL aunque no quede ningun P0/P1._
