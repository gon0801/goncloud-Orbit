# R14-r4: hallazgos de la revision automatica del SHA final

La revision automatica del PR #443 sobre el SHA aprobado dejo un
hallazgo High y tres menores abiertos. Esta ronda los atiende con
commits locales, sin push.

## F6 (High): payload de instrucciones en la evidencia del panel

El archivo `panel/panel-sintesis-y-pruebas.txt` de R14-r2 guardaba el
sobre crudo del orquestador, con un bloque de instrucciones y dos
llamadas literales a la herramienta de lectura con rutas de sesion.
Se reescribio como descripcion inerte: conserva la sintesis del panel
y la evidencia de la lente de pruebas, sin bloques de instrucciones,
sin llamadas con parametros y sin rutas de sesion. La comprobacion
contra la carpeta de R14-r2 no encuentra ninguna de esas formas.

## F4 (Medium): roles app_ingest y app_decide fuera de la 0071

No se agrego el grant, porque ningun camino de codigo conecta a esos
roles con la vista. El unico lector de `v_impulso_estado` en el codigo
es la pantalla de productos, que entra con el rol de lectura, y los
servicios de ingesta y decision no mencionan el impulso. La 0071 deja
la intencion declarada en su encabezado: esos roles conservan la
lectura por los default privileges y la migracion no les declara
grant. El ensayo de la migracion sigue en verde.

## F5 (Low): evidencia de R09-G con el contrato anterior

La evidencia de R09-G citaba una prueba que ya no existe y el rechazo
con 409. Quedo citada la prueba vigente y el conflicto como fallo del
resultado parcial 502, que es el contrato que dejo R09-H.

## F7 (Low): desarmar un lote ya desarmado repetia la pausa

`_desarmar` no consultaba el estado del lote y una segunda corrida
rehacia los PUT y agregaba filas al ledger. Ahora aborta al inicio si
el lote ya esta desarmado. La prueba nueva corre contra un lote
desarmado con pasos aplicados y afirma el aborto sin ningun PUT; sin
la guarda, la prueba cae.
