# Nota de síntesis de la arena (2026-10-09)

**Tarea:** `../TAREA.md`. **Rúbrica:** `../RUBRICA.md`. **Base de hechos:** `../grounding.md`.

## Candidatos

| | Modelo | Forma | Puntaje del juez |
|---|---|---|---|
| runner-a | Opus | `niveles_v3`: `decide(CasoHoja)`; niveles para recortar, previa solo para subir; `Trayectoria`; impulso = un producto por par de campañas | 28 de 30 |
| runner-b | Fable | `valor_v3`: prueba de azar de Poisson sobre el tramo posterior al último cambio; piso de CPC; impulso = un producto por par de campañas | 20 de 30 |
| runner-c | Sonnet | `dinero_v1`: el dinero como unidad de evidencia; línea de utilidad; impulso = N ad groups por campaña con tablas propias | 26 de 30 |

Sin bajas: los tres entregaron el paquete completo. Juez: Fable, de solo lectura. runner-a leyó la rúbrica
por accidente y lo declaró; el juez descontó la forma y verificó el fondo.

## Base elegida: runner-a

El juez y la lectura propia coincidieron. Razones, criterio por criterio: resuelve las fallas medidas con un
mes simulado con historia propia; no viola ninguna restricción dura; tiene la interfaz más honda (tres
funciones públicas, `cycle.py` ciego a la política, freeze y replay con la misma función); y cada número de
su tabla tiene prototipo.

## Injertos

| De | Qué | Dónde quedó |
|---|---|---|
| runner-c | Interruptor fail-closed: clave ausente = el motor no mueve bids | `bosquejo.py::politica_bid_desde_settings`; `design.md` decisión 6 |
| runner-c | Criterio de encendido mecánico (determinismo, invariantes, cobertura de 95 %, cero recortes sobre dañadas) | `bosquejo.py::InformeRejuego.cumple` |
| runner-c | Materialidad de un cuarto del gasto para concluir en la regla de grupo | R11 y R11b de `tabla-decision.md`; `MATERIALIDAD_GRUPO` |
| runner-b y runner-c | Paso asimétrico del target (suben de una vez, bajan a 0.5) | `bosquejo.py::resuelve_target_margen`; `design.md` decisión 5 |
| runner-b | Tipo de grupo en `campana_grupo` con candado de roles | `datos.sql`, nota de la migración 0062 |

## Rechazos

- runner-b: ciclo sintético para restaurar un bid; ventana anclada en la hoja; `--desarmar` como pausa; piso
  de 1.5 veces el CPC como regla dura (14 casos); tablero de ruido leyendo JSON en SQL.
- runner-c: N ad groups por campaña; pausar hojas para el tope (choca con `decision_madurez_corte`); línea de
  utilidad como fórmula (11 casos que no discriminan); `ajuste_placement` como JSON crudo.
- runner-a: ancla con fracción y marca de era; el vigía por ritmo de 3 días que sugirió el juez no se
  injertó porque, con presupuesto diario por producto, Amazon ya acota el ritmo.

## Verificación del diseño sintetizado

- `bosquejo.py` parsea.
- `prototipos/verifica_uso.py`: 29 llamadas de `design.md` contra `bosquejo.py`, 0 fallas.
- Rejuego con el injerto de materialidad (`prototipos/s1_salida.txt`): de los recortes que decidió el motor
  viejo del 2026-09-02 al 2026-10-09, la política repetiría 6 de 405 en MX y 26 de 346 en US.
- Mes simulado con historia propia (`prototipos/s2_salida.txt`): MX con 11 recortes en 9 hojas, 33 subidas y
  1 regreso; US con 18 recortes en 14 hojas que cargan 69.6 % del gasto, 9 subidas y 1 regreso.
- Hoja 2963 (MX): ningún cambio. Hoja 4924 (US): un solo recorte de 12 %, de 0.40 a 0.352.
- Restricciones duras de `grounding.md` §3: sin violaciones. Dependencias reconocidas: archivar y reponer
  product ads; roster de hermanas vacío para el grupo de dos roles; roles declarados en el plan de la
  fábrica; ruta nueva `POST /bid/regresar` en la lista sellada de rutas.

## Lo que queda sin medir (va como pregunta o como sonda antes de construir)

- Los 14 días para invertir dirección sin clics nuevos (R14).
- Cuánto se excede Amazon de un presupuesto diario; la forma de `dynamicBidding` y del presupuesto en la
  lista de campañas; si el reporte por placement acepta datos diarios; si `PUT /sp/productAds` permite pausar.
- La duración real del ciclo con la consulta nueva por plataforma (regla de 30 %).

## Agregado después de la síntesis (2026-10-09, pruebas de solo lectura)

Las pruebas están en `PRUEBAS.md`. Cambiaron dos supuestos y agregaron una pieza:

- El presupuesto diario del impulso pasa de tope entre 7 a tope entre 14, y el vigía pausa dos presupuestos
  diarios antes del tope, porque Amazon puede gastar en un día el doble del presupuesto diario.
- El retiro de un producto de una bolsa vieja puede ser una pausa reversible: Amazon sí permite pausar un
  product ad. Falta sellar la sonda de escritura.
- Pieza nueva, "Ver y ajustar la campaña": tablas por ubicación y por campaña en la pantalla de dinero, tres
  ajustes que aprueba el dueño (`AjusteCampana`), avisos diarios y la tabla `campana_ajuste`. Un ajuste por
  ubicación entra a la `Trayectoria` de las hojas de esa campaña.

Verificación tras el agregado: `bosquejo.py` parsea y `verifica_uso.py` sigue en 29 llamadas, 0 fallas.

## Decisiones del dueño (2026-10-09)

Respondió las diez preguntas; quedaron en la tabla "Decisiones del dueño" de `design.md` (D1 a D10). Las que
cambian la forma: fracción de US en 0.8 (target 25.2 %), sin grupo de control, encendido directo a vivo,
botón "Regresar todas", impulso de dos campañas por producto y ajustes de campaña propuestos por Orbit.
Rejuego con D1: US con 22 recortes en 17 hojas que cargan 72.5 % del gasto (`prototipos/s2_us2519.txt`).
Verificación: `bosquejo.py` parsea; `verifica_uso.py` da 28 llamadas, 0 fallas (bajó una al retirar el control).
