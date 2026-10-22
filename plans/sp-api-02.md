# SP-API 02 — el 429 de Amazon no tira la corrida nocturna

**Estado: CERRADO 2026-10-22** (desplegado en `f273213`).

- **A** (`9bda38c`): `app/spapi/client.py` reintenta el 429 hasta 5 veces (`get` y `post_fees`), con espera igual a la mayor entre `Retry-After` y 2, 4, 8, 16, 32 s; la suma por llamada no pasa de 120 s. Cada 429 deja una línea `spapi 429 ...` (ruta sin parámetros, intento, espera, `Retry-After`). Aviso de Telegram "SP-API recuperada" cuando una fuente vuelve a correr bien tras una corrida fallida.
- **B** (`f273213`): `scripts/spapi-diario.sh` repite una vez, al final y en el mismo orden, lo que salió distinto de 0, salvo que arrancara después de las 06:15 UTC. Receta de despliegue/checklist/rollback en `docs/evidencia/sp-api-02/ejecucion/B.3/`.
- **C**: cierre de ledger y documentos. Evidencia en `docs/evidencia/sp-api-02/ejecucion/C.1/NOTA.md`.

Pendiente de observación: la primera corrida nocturna con el código nuevo es la del 2026-10-23 05:00 UTC.
Deuda no bloqueante: prueba con `date` falso que avance entre repeticiones; `rollback.sh` aborta en seco si hay una ingesta corriendo; los scripts de B.3 no tienen pruebas propias.
