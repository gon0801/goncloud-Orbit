# G.1 — plan de goals a `app/` (REPRICING 02, Sección 3)

`siembra.planear` (puro, `Pedidos → Plan`) + `goals_write.leer_candidatos` /
`aplicar_plan` (todo o nada); `tools/precio_goal.py` queda como despachador
delgado (argparse/CSV/ceremonia). Rama `rp02/sec3-seguridad-goals` sobre S.6.

## Comandos y salidas

TDD rojo primero (`rojo-tdd.txt`): 14 pruebas nuevas + 3 reescritas en rojo
(`ModuleNotFoundError: siembra` / comportamiento viejo), 57 verdes intactas
sobre ORDEN54.

```bash
PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider tests/test_precio_goals.py
# 75 passed in 12.84s (60 heredadas + 15 nuevas; 3 reescritas in situ)
```

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_architecture.py tests/test_arq_precio_g.py
# 97 passed (candados: escritor único, pureza de app/precio, tamaño ≤900)
```

```bash
.venv/bin/ruff check app/precio/siembra.py app/precio/goals_write.py \
  tools/precio_goal.py tests/test_precio_goals.py   # All checks passed!
.venv/bin/ruff format --check <los 4>                # 4 files already formatted
```

```bash
bash docs/evidencia/repricing-02/ejecucion/G.1/comparar.sh
# COMPARACION VERDE: A y B planean lo mismo (lids 9,10)  [comparar-verde.txt]
bash docs/evidencia/repricing-02/ejecucion/G.1/comparar.sh --romper-b
# COMPARACION ROJA: 3 diferencia(s), exit 1             [comparar-rojo.txt]
```

El comparar levanta dos bases sembradas (`orbit_cmp_g1_a/b`, ORDEN54 +
2 cadenas), corre el tool de S.6 (worktree pristino) contra el de G.1
(árbol) y exige diff vacío en: goal 30.00, CSV 30.00+40.00, salto 55.00
(abortan igual, exit 2, mismo stderr) y goal+CSV iguales por lado. Las
bases y el worktree se borran al salir (verificado: 0 bases `orbit_cmp*`).

Mutantes: `mutantes.md` (M0 obligatorio + 18; 1 equivalente documentado).

## Tres cambios de conducta (textual del DoD, para el PR)

1. el go es todo o nada
2. resembrar el mismo día se permite
3. un goal vigente se reemplaza

Más: idéntico goal+modo sale `sin_cambio` (no bate churn); el plan muestra
`reemplaza` con `goal_actual`; cada fila sembrada deja `origen/lote/m_referencia`.

## Desviaciones con razón

1. Guía G.1 vive en `plans/repricing-02-construccion.md:658-691`, no 633-648
   (líneas del encargo desactualizadas); se siguió el texto real.
2. `_referencia` se BORRÓ del tool (no se reescribió sobre `leer_cuenta`):
   lo mandan el encargo y bosquejo V (diseño > guía), y `leer_cuenta` es
   incompatible con el comportamiento congelado (overlay de frescura +
   `observed <= as_of` + sin `contribucion_pct`; los fixtures fijan la
   lectura cruda con `m_actual=24.00%/41.00%`). `leer_candidatos` replica
   el SQL crudo del tool; el cálculo vive en `siembra.py`.
3. `leer_candidatos(conn, *, filtro)` sin `vitrina`: ningún `Vitrina`
   devuelve `Insumos` aún (llega con F.4) y `MercadoAmazon.insumos` no
   cumple el protocolo; el parámetro llegaría muerto en G.1 (evoluciona
   en G.2, precedente: `correr` en 0.b). `Candidato.lectura` es
   `LecturaHoy | None` (regla 3) en vez de `Insumos | SinInsumos`.
4. Rellenos del bosquejo (huecos, documentados en código): `PedidoValores`
   plural (el CSV trae un goal por fila y el go es atómico; el singular
   queda para selección en bloque de G.3); `FilaPlan.goal_id` (el plan es
   el modelo de display); `ResultadoPlan.hechos` (el tool imprime
   `[hecho]` por fila, como `precio_reversa`); `aplicar_plan(estricto)`
   (el tool aborta en vez de omitir; `omitir y contar` es el default).
5. `confirmable` ⟺ confirmar deja una fila escribible (fuera_de_banda de
   valor a mano: no; su borde de margen-de-hoy: sí); `m_hoy` exacto y goal
   propuesto a 4 decimales (escala de la columna, visible en el plan).
6. No se implementó `exit 3` ni `salto de fee 10%`: no existen en ningún
   contrato (exit 2 congelado por ~20 pruebas; bloques reales:
   `fuera_de_banda` 10–60% y `salto_mayor_25`). La guía no trae `comparar`
   para G.1: se construyó por analogía con 0.b, sin datos de producción.
7. Tensión heredada sin tocar (fuera del carril): `app/estimacion_reader`
   se declara "ÚNICO lugar" del JSON `componentes`, pero bosquejo V manda
   `_componente` a `siembra.py`. Se siguió el diseño; conciliar docstring.
