# Cómo comprobar cada cierre

Estos comandos son para la **implementación futura**. Esta entrega documental
reutiliza la revisión de `ff03ed61`, no afirma haber ejecutado arreglos inexistentes.
Los artefactos en `revision/` conservan los originales, incluidos sus hashes.

## Entorno y seguridad de las reproducciones

Usar Linux, Python3.12, PostgreSQL16 desechable, git, Node y Chromium. Instalar
dependencias de Quality (`fastapi`, `psycopg[binary]`, `pytest`, `httpx`, `pglast`,
`jinja2`, `pyyaml`) y las herramientas de pruebas operativas cuando correspondan.
El servicio PG tiene que permitir crear bases/roles de prueba. Nunca usar DSN,
credenciales, `.env` ni `ORBIT_SECRETS_DIR` de producción.

Las guardas requieren `orbit-review-current-pg:5432/postgres` en la red de
contenedores propia, o `127.0.0.1:55449/postgres` para el PG desechable publicado
solo localmente. Crear red/contenedores con esos nombres únicamente si no existen;
si existen, no borrarlos ni reutilizarlos sin comprobar que pertenecen a esta
ejecución. Verificar PostgreSQL16 antes de correr. Las bases `thermo_*_current`
deben estar ausentes: el runner crea y elimina las suyas, no toma bases ajenas.
Ejecutar serialmente; no `pytest -n` ni dos revisiones simultáneas sobre ese PG.

En Docker, clonar normalmente un bundle del SHA elegido en `/repo`, con índice
poblado, y copiar la evidencia en `/evidence`; no usar `--no-checkout` sin poblarlo.
Si el montaje del host aparece vacío, usar `docker cp`. Registrar SHA real del
checkout y rutas de imports; no fijar `ORBIT_REVIEW_SHA` a un commit distinto.
El transporte de las reproducciones está simulado; SQL, clientes, parseo, negocio
y navegador ejecutan código real. No sustituir Chromium por un DOM falso.

Desde la raíz del checkout de prueba, con el DSN de ese laboratorio ya definido:

```bash
export ORBIT_TEST_DSN="${ORBIT_TEST_DSN:?Falta DSN del PG16 desechable}"
export ORBIT_REVIEW_REPO="$PWD" ORBIT_REVIEW_ROOT="$PWD"
export ORBIT_REVIEW_DSN="$ORBIT_TEST_DSN" ORBIT_REVIEW_PG_DSN="$ORBIT_TEST_DSN"
export ORBIT_OPTIMIZER_TEST_DSN="$ORBIT_TEST_DSN" ORBIT_UI_DSN="$ORBIT_TEST_DSN"
export ORBIT_ACCEPT_EVIDENCE="$PWD/docs/evidencia/arreglos-05/revision"
export PYTHONPATH="$PWD:$PWD/tests:$ORBIT_ACCEPT_EVIDENCE"
export PYTHONDONTWRITEBYTECODE=1
T() {
  CI=1 python -B -m pytest -q -p no:cacheprovider \
    "$ORBIT_ACCEPT_EVIDENCE/test_accept_$1.py" -k "$2"
}
N() {
  CI=1 python -B -m pytest -q -p no:cacheprovider tests -k "arreglos05_$1_"
}
```

Las letras de `N` son minúsculas: `N a`, `N b`, etc. Son regresiones por incorporar
en los módulos de tests correspondientes; no se finge que existen hoy. Pytest
con cero casos devuelve5: no ocultarlo con `|| true`. Revisar también XML y salida:
una selección vacía, un skip o fallo de fixture no son aceptación. Para03/04,
los planes describen sus pruebas nuevas; `T` permite ejecutar ya el caso original.

Para ejecutar una fila en un shell nuevo sin depender de esas funciones, usar el
lanzador versionado: `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh T writes test_02`
o `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N a`.
El lanzador calcula la raíz, configura variables y rechaza un DSN fuera del
laboratorio **antes de conectar**. También funciona con Python3.12 ya preparado
en PATH, o `ORBIT_ACCEPT_PYTHON` apuntando a su ejecutable. No prepara servicios.
`--help` no requiere entorno; `--collect-only` después del filtro sirve para
comprobar selección, pero no cuenta como pase de comportamiento.

Cuando el primer grupo que se ejecute no cree roles, preparar previamente los
roles compartidos según `verify/Launch.md`; no modificar permisos de producción.
El runner completo arranca por writes, que instala primero la cadena y roles.

## Comandos existentes por hallazgo

| Owner | Comando | Resultado exigido |
|---|---|---|
| 04 B-C / TN02 | `T writes test_02` | Missing/truncated: cero repost y ningún terminal por ausencia supuesta |
| 05 B / TN03,10 | `T writes 'test_03 or test_10'` | No archiva keyword ajena; reversa del negative inequívoco confirma una vez |
| 04 W-P / TN04 | `T ui test_04` | Retiro e impulso no envían la previa anterior |
| 03 D-G / TN05 | `T ingest test_05` | Una clave contaminada no publica subtotal ni reaparece |
| 03 D-M / TN06,14 | `T ingest 'test_06 or test_14'` | Scan parcial no da bajas; rollback no cuenta escrituras |
| 03 P-G / TN07 | `T pricing test_07` | Fresca/cache conservan fecha y detalle |
| 05 C / TN11 | `T writes test_11` | Un solo POST tras ACK y fallo de readback |
| 05 F / TN12 | `T optimizer test_12` | Base29/28.5/28, sin fixture que envejezca la columna equivocada |
| 05 G / TN13 | `T optimizer test_13` | Reobservación madura no revierte el lote |
| 04 W-E / TN15 | `T ui test_15` | Gasto parcialmente desconocido implica ratio desconocido |
| 04 W-D / TN16 | `T ui test_16` | No dice «sin ventas» cuando la API sí trae ventas |
| 03 D-N / TNS1 | `T ingest test_S1` | Clave ficticia ausente del error durable, model y choice |

Controles existentes: `T writes test_01`, `T pricing 'test_08 or test_D2'`,
`T ingest test_09`, `T optimizer test_control`, `T ui test_control`,
`T pricing test_arreglos02` y `T ingest test_control`.

## Red/green y mantenimiento de la evidencia

1. Ejecutar el caso en el padre real del arreglo, con su fixture válido. Conservar
   fallo de aserción que muestra el impacto, SHA y comando; no error de import/DB.
2. Implementar y demostrar la misma conducta corregida. Promover a `tests/` los
   escenarios útiles mediante fixtures normales, sin depender de `/tmp` ni de logs.
3. Si TEMPORADA o una eliminación cambia el camino público, adaptar la entrada del
   fixture y registrar correspondencia caso original → regresión nueva. Mantener
   el efecto observable; no quitar la aserción para lograr verde.
4. Añadir vecinos por causa: lista incompleta/corrupta/tokens repetidos, concurrencia
   y crash, reversa tras intervención humana, cache/fresca, rollback externo, UI
   tardía y doble envío. Reutilizar pruebas vigentes que ya demuestren esos vecinos.

El legado tiene reproducciones en
`docs/evidencia/arreglos-2026-11-12/pruebas/{ciclo,apply,web}/`. Sus aserciones pueden
describir el contrato previo a TEMP: revalidar antes de promoverlas. En particular,
conciliación antes de decidir pertenece a TEMP B.2 y banda manual a01X; si ya pasan,
guardar el control y no volver a implementar. Una prueba de texto no reemplaza
el comportamiento de HTTP/SQL/UI.

## Corrida global y UI

Guardar salida fuera de los originales; el runner por defecto sobrescribiría
`revision/acceptance.xml`, por eso se fija el destino explícitamente:

```bash
mkdir -p docs/evidencia/arreglos-05/Z
export ORBIT_ACCEPTANCE_XML="$PWD/docs/evidencia/arreglos-05/Z/acceptance.xml"
bash "$ORBIT_ACCEPT_EVIDENCE/run-acceptance.sh" "$PWD" \
  > docs/evidencia/arreglos-05/Z/acceptance.log 2>&1
```

El shell debe propagar cualquier salida no cero; no cerrar el bloque por tener
un XML. Base:31 casos; los nuevos vecinos aumentan esa cobertura, no la sustituyen
sin trazabilidad. Los 17 rojos de la base corresponden a14 IDs porque02/04/07 tienen
dos variantes; 14 verdes son controles, no14 hallazgos adicionales.

Antes de verificar UI leer `.cursor/skills/verify-orbit/SKILL.md` y fichas afectadas.
Usar `.cursor/skills/verify-orbit/helpers/orbit-verify`: `launch`, `doctor`, los diez
`drive-*` existentes, `cleanup`. La lista concreta vive en el helper/mapa vigente;
no inventar `drive-ruido`. Productos, Settings y Resumen requieren además los
casos adversos: el recorrido básico por sí solo no envía retiro ni reproduce una
respuesta tardía. Actualizar fichas si cambian selectores o flujos.

Si una regresión Chromium se promueve a la suite de CI, su bloque incorpora la
dependencia del navegador al entorno de CI y verifica allí la ejecución; no
permitir skip por navegador ausente. Otra opción es conservar el caso de navegador
en el arnés externo y adjuntar su corrida como aceptación obligatoria, manteniendo
en pytest los contratos API/SQL. Elegir una forma y dejarla registrada en el bloque.

Finalmente, en entorno de pruebas apropiado:

```bash
pre-commit run --all-files
python -m compileall -q app tools verify
CI=1 PYTHONPATH=. python -m pytest -q -x
```

Guardar Quality verde del SHA integrado, resultados completos PG y secretos; no
reusar la CI de `ff03ed61` para certificar código futuro. La CI de un PR solo de
docs omite por diseño la batería; eso sirve para esta entrega documental, no
para acreditar la implementación de 03/04/TEMP/05 ni su despliegue.

## Veredicto Z

Volver a ejecutar el skill thermo sobre **toda Orbit**, con inventario de alcance,
fronteras y caminos; incluir código nuevo y vecinos. No basta volver verdes estas
reproducciones. Registrar por hallazgo impacto, comando, evidencia, dueño y cierre.
PASA solo sin bloqueos confirmados abiertos, con CI/secretos/aceptación/UI del mismo
estado integrado. Las notas estructurales siguen el criterio de AGENTS; no se
debilitan candados para lograr pase. Si K carece de fuente, se declara pendiente y
no se afirma cerrado todo el legado. Los seguimientos operativos se acreditan aparte.
