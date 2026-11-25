# Bloque Z: plan de cierre de ARREGLOS 05

Estado: plan escrito antes de ejecutar la aceptacion. Fecha: 2026-11-25.

- SHA conjunto de cierre: `7b06778d376eb5631b0a2863beea6a90ab804da0` (merge del PR
  #773, `arreglos-05/k`, con BASE y A-K integrados). Se anota como el SHA de cierre
  del plan.
- Rama: `arreglos-05/z`, creada desde ese SHA.
- Alcance: filas Z.1 a Z.4 de `plans/arreglos-05.md`. `lane: fast`, sin despliegue
  propio. No se encienden precios ni pausa por producto, no hay force-push a
  `master`.
- Modelo: `opencode-go/deepseek-v4.1-flash` (OpenCode Go), por orden de David, con
  `poteto-mode` completo.

## Entorno comprobado antes de empezar

| Pieza | Estado |
|---|---|
| Python | 3.12.14 por `uv run --frozen --python 3.12` (venv recreado en `.venv`) |
| PostgreSQL | 16.15 en `127.0.0.1:55449/postgres`, rol `orbit`; `ORBIT_TEST_DSN` inyectada |
| Chromium | Edge en `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe` por `ORBIT_CHROMIUM` |
| Secretos | `gitleaks` en PATH (`Gitleaks.Gitleaks` de WinGet) |
| Pantallas | WSL Ubuntu disponible con el repo visible en `/mnt/c/...`; PG de 5432 ausente, el helper acepta `ORBIT_TEST_DSN`, asi que se usa el PG del laboratorio (55449) |
| Salida | XML y log en `docs/evidencia/arreglos-05/Z/`, sin tocar `revision/acceptance.xml` |

No falta ninguna pieza del entorno. La aceptacion global, la suite y la UI se
ejecutan en serie, una corrida a la vez, sobre el PG compartido del laboratorio.

## Comprobaciones y orden

1. **Aceptacion integrada (Z.2).** `comprobar.sh ALL` sobre el SHA conjunto con
   `ORBIT_ACCEPTANCE_XML=Z/acceptance.xml` y log en `Z/acceptance.log`. Se exige
   31 casos base, cero errores y cero omisiones, y se compara cada caso con la
   clasificacion de BASE (26 verdes, 5 rojos de producto que A-K debieron cerrar).
2. **Regresiones promovidas (Z.2).** `comprobar.sh N a` .. `N k` en serie. Cuenta
   los casos de `tests/test_arreglos05_*` por letra y exige cero fallos y cero
   omisiones; una seleccion vacia no cuenta.
3. **Vecinos por causa.** Las suites que los bloques ya usan como vecinas:
   ciclo/cron/locks, `test_ads_write`, fabrica, archivo, estimacion/precios,
   UI/API del dashboard. Se reutilizan los comandos de cada `resultado.md` sin
   repetir la suite completa por si misma.
4. **Suite completa (decision del legado).** Una corrida de `pytest -q` sobre el
   SHA conjunto para decidir explicitamente que se hace con los fallos previos de
   `master` en esta PC (`test_fabrica_f2_hermanas`, `test_fabrica_f2_biblioteca`,
   `test_harvest_destino`, `test_d3_adversary`, `test_notifica`,
   `test_orbit07_m1`, `test_precio02_escenario_meli`). Cada uno se clasifica como
   arreglado, reemplazo de fixture trazado o pendiente con motivo; ninguno queda
   solo dentro de un XML.
5. **UI del mapa (Z.4).** `launch`, `doctor`, los diez `drive-*` vigentes del
   helper (resumen, campanas, decisiones, salud, contribucion, cortes, inertes,
   fabrica, settings, productos) y `cleanup`, en WSL Linux con el PG del
   laboratorio para que `doctor` pueda introspeccionar el proceso. Despues, casos
   adversos de Productos, Settings y Resumen contra una instancia local del mismo
   SHA con Edge/CDP. Productos, Settings y Resumen no se cierran solo con el
   recorrido basico.
6. **Secretos (Z.4).** `gitleaks detect` sobre el rango del SHA conjunto y el
   arbol de trabajo; salida saneada en `Z/secretos.txt`.
7. **Revision global (Z.3).** El revisor asignado, distinto al implementador
   (Astra o el alterno que anuncie el coordinador), revisa toda la app del SHA
   integrado. `Z/review.md` registra alcance, archivos y caminos, P0/P1 frente a
   P2 y veredicto. Se solicita al coordinador al abrir el PR.

## Bloques de UI que se recorren

Los diez del mapa vigente: Resumen, Campanas, Decisiones, Salud, Contribucion,
Propuestas, Inertes, Crear campanas, Settings y Productos sin clics. Ademas:

- **Settings**: cuota `1,000` no envia, error visible, cero POST y cero version
  nueva (control de I, mismo camino).
- **Productos**: recorrido de decisiones de producto y pausa; sin POST de
  encendido en el baseline.
- **Resumen**: contrato `script#datos-resumen`, desconocido frente a cero y CSP.

## Criterio de veredicto

- **PASA** exige cero P0/P1 reales abiertos (uso normal roto, seguridad o
  secretos, o corrupcion/perdida de dinero o datos ante operacion normal o fallo
  simple), aceptacion 31/31 sin errores ni omisiones, regresiones N a-k verdes,
  UI del mapa y adversos sin fallo de producto, y secretos sin hallazgos.
- **PARCIAL** cuando todo lo anterior esta verde pero queda una fuente externa
  pendiente o un P0/P1 real abierto. El caso conocido es **K abierto para Amazon
  US**: la fuente no permite demostrar el impuesto sobre comisiones en US, y este
  bloque no lo marca terminado ni lo suaviza. Mientras US siga sin adjudicar, el
  veredicto es PARCIAL y el plan no recibe pase global.
- Las esquinas que requieren 2 o mas fallos encadenados y las notas estructurales
  van a "Seguimiento (P2)" y no bloquean.

## Entregables

- `Z/cobertura.tsv`: 14 IDs, controles 01/08/09/D2, banda manual 01X, D1 como nota
  y el legado de 53 fallas/decisiones, cada fila con owner, commit y comprobacion.
- `Z/acceptance.xml` y `Z/acceptance.log`: corrida integrada.
- `Z/review.md`: revision global del revisor asignado.
- `Z/resultado.md`: SHA, XML, UI, secretos y pendientes.
- `Z/secretos.txt`: salida saneada de gitleaks.
