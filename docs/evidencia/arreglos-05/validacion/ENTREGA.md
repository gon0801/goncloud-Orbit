# Verificación de esta entrega documental

15-nov-2026, base `ff03ed61`, rama `plan/arreglos-05-cierre-review-20261115`.
No se implementó producto, migración ni cambio de configuración operativa.

Comprobado por el coordinador:

- Manifest JSON válido:50 entradas únicas con rutas existentes; siguiente03.
- Dependencias sin referencias ausentes ni ciclos:03 tiene20 bloques,04 tiene21,
  TEMPORADA tiene10 y05 tiene13. Se conservan los bloques anteriores y se agregan
  los cierres identificados; BASE nuevo de04 explica parte del aumento.
- Enlaces relativos de documentos modificados existentes; sintaxis Python de
  bocetos/artefactos válida. No se importan los bocetos como código de la app.
- Los14 SHA256 del manifest de la revisión coinciden también con el índice Git,
  incluidos XML originales. No se alteraron aserciones ni resultados históricos.
- `bash -n docs/evidencia/arreglos-05/comprobar.sh` y `--help`: salida0.
- Con Python3.12, `comprobar.sh T writes test_02 --collect-only`: dos variantes
  recogidas, siete casos deseleccionados, salida0. **No ejecuta los escenarios**.
- DSN local en puerto5432, fuera del laboratorio permitido: el lanzador lo
  rechazó antes de conectar, salida1 esperada; no se usó PostgreSQL en esta prueba.
- Control existente01X de banda manual en Ajustes:1 passed,25 deselected;
  salida guardada en `../base/controles-settings.txt`.
- `pre-commit run --all-files`: Gitleaks y conflictos aprobados.
- `git diff --cached --check` sin la carpeta histórica `revision/`: aprobado.
  El check global informa espacios finales de los logs/XML originales y la
  celda vacía del TSV. Se conservan para no modificar evidencia y hashes; no se
  deshabilitó ningún hook ni gate.

La [revisión independiente](revision-diseno.md) dio **plan apto, cero bloqueos**;
se incorporaron sus dos precisiones sobre HTTP tardío y K pendiente. Sus hashes
identifican los contratos revisados. Donde el informe dice «usuario» al atribuir
las comprobaciones recibidas, se refiere al coordinador que le envió el encargo,
no a una ejecución manual del dueño.

La CLI `motor` no está disponible en este entorno: no se afirma haber ejecutado
`motor valida`. El chequeo de estructura/dependencias y la revisión del contenido
son los descritos arriba; antes de encargarlo, usar el validador del motor vigente
en su entorno. Esto no exige esperar para revisar o integrar el diseño.

No se repitió la batería de5996 ni los diez recorridos UI: la app de esta entrega
no cambia y su evidencia de base sigue siendo válida. La aceptación de los
arreglos futuros y el pase global quedan en05 Z sobre el SHA integrado futuro.
La CI de este PR documental verifica sus gates de documentación/secretos/carga;
su omisión prevista de la batería no es un pase de la futura implementación.
