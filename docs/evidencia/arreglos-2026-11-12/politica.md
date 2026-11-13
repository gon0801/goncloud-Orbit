# Revisión de la política de pujas (niveles_v3)

Revisión de solo lectura sobre master `a4644aad` (igual a lo que corre en producción).
Pruebas de reproducción: `docs/evidencia/arreglos-2026-11-12/pruebas/politica/test_revision_politica.py` (9 pruebas, las 9 fallan
por la falla que describen). Casos reales congelados con la sonda en `docs/evidencia/arreglos-2026-11-12/pruebas/politica/datos/`.

Correr:

    PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q docs/evidencia/arreglos-2026-11-12/pruebas/politica/

## Cómo funciona

1. Cada ciclo (uno al día por plataforma; las corridas de las 10, 13 y 16 h se saltan si ya hubo un ciclo `done`)
   arma, por cada keyword o product target activo, un "caso" (`CasoHoja`, `app/optimizer/caso.py`) con todo lo que
   la decisión necesita: el bid de hoy, el piso y techo, 90 días de ventas y clics en dos tramos (el viejo pesa la
   mitad), el ad group y la cuenta, los cambios de bid de 90 días y qué pasó después del último, y los datos de pausa.
   Lo lee `app/lecturas_caso.py` con cuatro consultas por plataforma más una por hoja que cambió de bid.
2. `decide(caso)` (`app/optimizer/politica.py`) es una función pura. Orden: primero la pausa (cero pedidos, 100 clics
   o más, 40 USD / 500 MXN; con la espera por venta reciente); después la trayectoria (regreso de una vez si un
   recorte del motor desplomó el tráfico; si no, esperar 7 días tras cualquier cambio); después el juicio
   (con pedidos: probabilidad de que la hoja pierda dinero o esté bajo el target, con la gamma; sin pedidos: gasto
   sin venta); por último los frenos (repetir dirección pide 20 clics nuevos, invertirla 20 clics o 14 días, el piso
   aprendido) y los límites (paso entre −30 % y +20 %, dentro de piso y techo, mínimo un centavo de cambio).
3. El resultado es Mantener (solo se cuenta), Mover, Regresar o Pausar. El caso completo se guarda en
   `decision.inputs["caso"]` y `reproduce()` vuelve a llamar a la MISMA `decide`: comprobé las 49 decisiones de
   niveles_v3 de los últimos 60 días y las 49 se reproducen exactas.
4. El bid que sale de `decide` lleva 4 decimales (por ejemplo 0.2904). El apply lo redondea a centavos justo antes de
   mandarlo a Amazon (`app/ads/write.py:185-198`), pero la decisión y el historial guardan el de 4 decimales.

## Fallas (con reproducción)

### F1. El piso aprendido no protege: el redondeo regresa la hoja justo al bid donde perdió su tráfico (dinero, ocurre hoy)

**Qué te pasa.** Cuando un recorte del motor hunde el tráfico de una keyword que vende, el motor la regresa y "aprende"
ese bid como piso que no debe volver a tocar. Pero el siguiente recorte calcula, por ejemplo, 0.2904 contra un piso
de 0.29: como 0.2904 es "más" que 0.29, pasa. El apply lo redondea y Amazon recibe **0.29, exactamente el piso**. La
keyword vuelve a perder el tráfico, a los 4-5 días el motor la regresa, y a los 7-8 días la vuelve a recortar.
En producción hay cuatro hojas en ese círculo:

| Hoja | Piso aprendido | Recorte calculado | Lo que recibió Amazon | Historial |
|---|---|---|---|---|
| 4934 (US) | 0.29 | 0.2904 | 0.29 | regreso 17-oct, recorte 25-oct, regreso 30-oct, recorte 7-nov, regreso 12-nov |
| 5336 (US) | 0.42 | 0.4224 | 0.42 | recorte 18-oct, regreso 23-oct, recorte 7-nov, regreso 12-nov |
| 4924 (US) | 0.26 | 0.2625 | 0.26 | regreso 4-nov, recorte 12-nov (el regreso toca hacia el 16-nov) |
| 5895 (US) | 0.12 | 0.1232 | 0.12 | decisión 2846 del 28-oct |

En la hoja 4934 el tráfico tras el regreso fue cinco veces mayor que al bid del piso (11,582 contra 2,182
impresiones en 7 días), y la hoja vende (13 pedidos en su ventana).

**Dónde.** `app/optimizer/politica.py:423-427` y `:636-637` comparan el piso contra el bid sin redondear;
`app/cycle.py:1063` guarda ese bid de 4 decimales como `new_value`; `app/ads/write.py:198` redondea al mandar
(ROUND_HALF_EVEN). La vista `v_cambio_bid` (`migrations/0067_bids02_campana_ajuste.sql:65-78`) registra como
`bid_despues` el 0.2904 que Amazon nunca tuvo.

**Prueba.** `test_f1_piso_aprendido_se_pisa_al_redondear_caso_real[2865|2866|2904]` (casos reales) y
`test_f1_piso_aprendido_sintetico`:

    AssertionError: decision 2865: bid enviado 0.29 cae en el piso aprendido 0.2900
    AssertionError: decision 2866: bid enviado 0.42 cae en el piso aprendido 0.4200
    AssertionError: decision 2904: bid enviado 0.26 cae en el piso aprendido 0.2600
    AssertionError: assert Decimal('0.29') > Decimal('0.29')

Sonda que lo muestra: `SELECT hoja_id, confirmado_el, bid_antes, bid_despues, origen FROM v_cambio_bid WHERE hoja_id IN (4934,5336,4924,5567) ORDER BY 1,2`.

**Qué debería pasar.** El piso se compara contra el bid que de verdad recibe Amazon. Con 0.29 de piso, un recorte
que redondeado queda en 0.29 o menos es `Mantener(piso_aprendido)`.

### F2. La regla anti-inversión de 10 días (D.2) que CONTEXTO da por vigente ya no existe (dinero, ocurre hoy)

**Qué te pasa.** `docs/CONTEXTO.md:84-89` dice que, tras un bid aplicado, mover en dirección contraria exige al menos
10 días de evidencia posterior, y que una historia rota o una ventana desconocida bloquean. Ese código se borró en
BIDS 02 (`plans/bids-02-construccion.md:912`: "`DIAS_EVIDENCIA_INVERSION` y `POLITICA_INVERSION*` ... borra") y en su
lugar quedó la regla de 20 clics o 14 días (`politica.py:391-397`). Con 20 clics, el motor invierte a los 7 días. En
los últimos 30 días pasó 4 veces: decisiones 2834 (hoja 3760, 8 días), 2842 y 2865 (hoja 4934, 7 días cada una) y
2904 (hoja 4924, 7 días). Tres de esas cuatro son los recortes de la falla F1.

**Dónde.** `app/optimizer/politica.py:391-397` (R14) con `CLICS_NUEVOS = 20` y `DIAS_SALIDA = 14` (`:35-36`); no hay
ningún `inversion_sin_evidencia` en `app/`. CONTEXTO se contradice a sí mismo: la línea 148-151 describe la regla
nueva y la 84-89 la vieja.

**Prueba.** `test_f2_inversion_antes_de_10_dias_caso_real[2865|2904]`:

    AssertionError: decision 2865: invierte con 7 dias
    AssertionError: decision 2904: invierte con 7 dias

**Qué debería pasar.** Una de las dos reglas manda y la otra sale del documento. Si D.2 sigue siendo tuya, una
inversión antes de 10 días de evidencia es `Mantener`. Si la regla de 20 clics la reemplazó, hay que borrar el
párrafo de `CONTEXTO.md:84-89` para que ningún revisor la vuelva a exigir.

### F3. "Heredar el veredicto del ad group" recorta con el ACoS crudo de la propia hoja (dinero, ocurre hoy)

**Qué te pasa.** La regla dice: la hoja se juzga con sus datos y, si no concluyen, hereda el veredicto de su
ad group. Pero el "ad group" incluye a la propia hoja. Cuando la hoja es casi todo el grupo, el "veredicto del
grupo" es la misma hoja medida con su ACoS crudo, justo lo que la política quería evitar al usar la probabilidad.
La hoja 4934 es el 98 % del gasto de su grupo (337.43 de 344.19, los mismos 13 pedidos) y la recortaron tres veces
por `grupo_sangra_vendedora`. En la prueba sintética, la misma keyword (30 % de ACoS, 3 pedidos) en un grupo sano
recibe `Mantener(azar_lo_explica)` porque la probabilidad de que pierda dinero es apenas 0.18 (pide 0.80). Cuando
el grupo es ella misma, recibe −12 %.

**Dónde.** `app/optimizer/politica.py:352-353` (R4) y `:513-534` (`estado_grupo`); el grupo se arma con la hoja dentro
(`app/optimizer/caso.py:393`, `app/lecturas_caso.py:857-861`). En cambio, la previa de la subida sí excluye a la hoja
(`politica.py:499-501`, "resto del ad group"): la misma idea con dos criterios.

**Prueba.** `test_f3_grupo_de_una_hoja_recorta_por_acos_crudo` y `test_f3_caso_real_4934_grupo_es_la_hoja`:

    AssertionError: el grupo-hoja recorta: Mover(factor=Decimal('-0.12'), bid_nuevo=Decimal('8.8000'), motivo='grupo_sangra_vendedora', nivel='ad_group')
    AssertionError: el 'veredicto del grupo' es la propia hoja: 337.43000/344.19000

**Qué debería pasar.** El veredicto que se hereda sale del resto del grupo (sin la hoja), como ya hace la previa. Un
grupo sin otras hojas con pedidos no tiene veredicto que heredar.

### F4. Un ajuste de campaña se trata como si el bid hubiera subido (latente: hoy no hay ajustes)

**Qué te pasa.** Cuando apruebas un ajuste de ubicación, cada keyword de la campaña recibe en su historial un "cambio"
con el bid igual antes y después. El código le asigna dirección "subida". Diez días después, una keyword que vende
barato y debería subir +15 % recibe `Mantener(subida_sin_trafico)`: el motor cree que ya subió y que la subida no
trajo tráfico. Sin el ajuste, la misma keyword sube a 11.50. Un recorte después del ajuste también espera como
"inversión" (20 clics o 14 días). Hoy no pasa porque la tabla `campana_ajuste` está vacía en producción.

**Dónde.** `app/optimizer/caso.py:136-138` (`direccion` da +1 con bids iguales), `app/optimizer/politica.py:392` y
`:419-422`.

**Prueba.** `test_f4_ajuste_de_campana_se_juzga_como_subida_repetida`:

    AssertionError: un ajuste de campana no es una subida
    assert Mantener(motivo='subida_sin_trafico') != Mantener(motivo='subida_sin_trafico')

**Qué debería pasar.** Un ajuste solo hace esperar los 7 días y no proyecta precio (eso dice su propio docstring,
`caso.py:125-129`). No cuenta como subida ni como bajada para los frenos de repetición o inversión.

## Sospechas

- **El regreso del dueño enseña un piso demasiado bajo.** El 11-oct regresaste la hoja 4924 a 0.40 (revertiste el
  recorte 0.40→0.30 de la decisión 1993). El piso aprendido quedó en 0.1056 (el último bid antes de tu regreso), no
  en 0.30 (el recorte que rechazaste). Para el 30-oct el motor ya la había bajado a 0.26, debajo de lo que
  rechazaste. `app/lecturas_caso.py:392-401`, `caso.py:250-261`. No es falla segura porque no está escrito qué
  quiere decir tu regreso.
- **Precio de un solo clic.** La hoja 5336 (decisión 2866) juzgó con un costo por clic proyectado desde 1 clic
  (`clics_pre=1`, 0.42 USD). El paso 2 de la escalera de precio solo exige `clics_pre > 0`
  (`politica.py:589-603`).
- **Una métrica rota frena todas las subidas.** Un solo valor NULL o negativo en cualquier hoja activa de la
  plataforma en 90 días deja la "cuenta" en None y toda subida sale `dato_faltante` (`politica.py:356-358`,
  `lecturas_caso.py:354-358`). Hoy no pasa (1 `dato_faltante` en MX, 0 en US).
- **El paso de 0.5 del target depende de correr una vez al día.** El ancla es el ciclo anterior, no el día anterior
  (`app/cycle.py:2913-2926`). La guarda de un ciclo por día lo protege, pero una corrida con `--forzar` (hubo una el
  8-nov a las 17:58) baja otro medio punto si el derivado sigue abajo.
- **Equilibrio de plataforma con target propio.** Si una hoja toma su target de un goal de campaña, de la familia o
  de una meta de producto de versión vieja, R3 juzga contra el margen promedio de la plataforma
  (`app/lecturas_caso.py:481-485`, `app/cycle.py:2147-2153`). `plans/orbit-07-motor-decisiones.md:236` lo prohíbe.
  Latente: hoy todas las decisiones vienen de `margen_plataforma`.
- **La protección por venta reciente pausa cuando no sabe.** Si la fuente reciente está incompleta y no hay pedido
  conocido, `proteccion.evalua` devuelve "aplicar" y la pausa procede (`app/optimizer/proteccion.py:88-93`), mientras
  el docstring del módulo (`:18-19`) dice "desconocido: se espera".
- **Hoja con pedidos y venta 0** queda en `dato_faltante` para siempre (`politica.py:435`); con la pausa económica
  apagada, nada la pausa tampoco.
- **Piso de costo del negative con venta 0**: un grupo elegible con `ad_revenue` 0 da AOV 0 y el piso baja al legado
  8 USD / 130 MXN en vez del respaldo 45 / 600 (`app/optimizer/cortes.py:172-177`).

## Estructura: movimientos que borran complejidad

1. **Redondear en la política, no en el apply.** Hoy hay dos números para el mismo bid: el de 4 decimales que decide,
   se guarda y alimenta el historial, y el de 2 que recibe Amazon. Eso es lo que rompe F1. Movimiento: `_paso` y R1
   redondean con la misma función `_bid_payload` antes de comparar con piso, techo, piso aprendido y delta mínimo.
   Desaparece: la diferencia entre `decision.new_value` y lo enviado, y el `bid_despues` falso de `v_cambio_bid`.
   Riesgo: las 49+ decisiones viejas se reproducirían redondeadas; hace falta marcar la versión en `inputs.politica`
   para que el replay histórico siga usando la de 4 decimales.
2. **El piso aprendido en un solo lugar.** Está escrito dos veces (`politica.py:423-427` y `:636-637`); la segunda
   solo se alcanza cuando el techo queda bajo el piso. Movimiento: dejarlo solo en `_paso`, después de los límites y
   el redondeo. Desaparecen 5 líneas y la duda de cuál manda.
3. **Borrar los "espejos pineados".** `politica.py:23-32` y `:74-75` copian las bandas, pasos, límites y motivos de
   `bid.py:117-151`; `politica.DIAS_EFECTO` copia `goals.COOLDOWN`, igual que `lecturas_caso.DIAS_EFECTO`;
   `caso.DIAS_MADUREZ`, `caso.DIAS_LOOKBACK` y `caso.PRECISION` copian `windows`/`evidencia`. Un test
   (`tests/test_arq_bids_m.py:53-70`) vigila que sigan iguales. `politica.py` ya importa `_decide_pause` de `bid.py`,
   así que la razón para copiar ya no existe. Movimiento: importar y borrar unas 16 constantes y el test de espejos.
   Riesgo: casi ninguno (mismos valores). De paso, el docstring de 80 líneas de `bid.py` aún describe las bandas que
   ya no viven ahí.
4. **Serialización genérica del caso.** `caso.py` tiene 1,039 líneas; unas 600 (`:446-1039`) son `como_json` y
   `desde_json` escritos a mano, con cada campo repetido en la dataclass, en el `_json`, en el `_desde` y en un
   `frozenset` de claves. La regla de "claves nuevas opcionales" (`_objeto_versionado`) es exactamente "campo con
   valor por omisión" (`episodio`, `proteccion`, `frena_subidas`). Movimiento: un serializador por type hints que
   conserve el formato actual (Decimal como texto, None explícito, claves estrictas). Desaparecen unas 500 líneas.
   Riesgo: tiene que leer byte por byte las filas congeladas. Se comprueba con las fixtures existentes y las 49
   decisiones reales.
5. **Una sola "evidencia del ad group".** Para la misma ventana D-90..D-10 hay dos lecturas distintas: la del umbral
   de cortes (`windows.py:391-409` y `:617-657`: todas las hojas, veneno solo por NULL) y la del juicio de bids
   (`lecturas_caso.py:361-379` y `:857-862`: solo hojas activas, veneno también por negativos, dos tramos). La pausa y
   el bid ven grupos distintos. Movimiento: derivar las dos de la lectura por plataforma que ya hace `lee_plataforma`.
   Desaparecen `_SQL_EVIDENCIA_AD_GROUP`, `EvidenciaAdGroup` y una consulta por ciclo (unas 70 líneas). Riesgo:
   cambia el umbral de pausa de algunos grupos (quedarían fuera las hojas inactivas); lo decides tú.
6. **Un solo SQL del efecto.** `lecturas_caso.py:163-197` (`_SQL_EFECTO`) se conserva "byte a byte" para cuando la
   regla económica está apagada, junto a su gemelo ampliado `:203-269`. La regla está encendida en las dos
   plataformas. Movimiento: borrar el SQL viejo y la rama `_con_economia` de `_lee_efecto`. Desaparecen unas 60
   líneas. Riesgo: si algún día se apaga la regla, se leen unas columnas de más, sin costo real. Relacionado:
   `_ventanas_medidas` existe dos veces con reglas distintas (`lecturas_caso.py:754` y `economia_cambio.py:145`, que
   además exige gasto previo mayor que 0). Debería quedar una.

## Qué comprobé que está bien

- **Replay fiel:** las 49 decisiones niveles_v3 de los últimos 60 días en producción se reproducen exactas con
  `reproduce()` (tipo y bid). `decide` no lee reloj, settings ni target fuera del caso.
- **Bandas, pasos y límites** coinciden con CONTEXTO (−25 / −12 / +15, 1.35 / 1.15 / 0.85, de −30 % a +20 %, un
  centavo de mínimo). Un bid que ya está fuera del rango nunca se mueve en la dirección equivocada: sobre el techo con
  subida, o bajo el piso con cualquier movimiento, sale `rango_bloquea_ajuste`; en el piso, `delta_bajo_umbral`.
- **El redondeo nunca convierte un cambio en "sin cambio"**: con bids de 2 decimales el mínimo de 0.01 garantiza que
  el valor redondeado también cambia, así que no se gastan 7 días en un movimiento nulo.
- **Gamma:** no hay división entre cero; venta 0 o None, o pedidos 0, dan `dato_faltante`; gasto 0 da `sin_precio`;
  todo en Decimal. Con más de 9,999 pedidos levanta un error, pero eso está fuera de la realidad de la cuenta.
- **"No opino" nunca recorta:** con `sin_veredicto`, R4 y R11 no disparan.
- **Trayectoria:** el regreso por desplome va antes de la espera de 7 días y exige volumen previo; repetir dirección
  pide 20 clics; `DIAS_EFECTO = 7` y cuenta días con ingesta hasta ayer.
- **Ventanas:** la de cortes se calcula aparte y con madurez de 10 días; completitud de 7 fechas o más; fechas en
  UTC con `timedelta` (no hay problemas en cambios de mes o de año).
- **PAUSE:** clics con piso max(100, bruto) y costo 40 / 500, congelados en `caso.pausa`, evaluados antes que todo.
  La pausa económica está apagada en producción.
- **Target:** banda [10, 45], baja máximo 0.5 por ciclo y sube de una vez, converge al setting si el dato se
  invalida. En producción bajó 0.5 o menos por día entre el 25-oct y el 12-nov (un ciclo por día por la guarda
  `motivo_de_salto`). La moneda MXN en las notas de US es la del ledger; el margen es un porcentaje y no se mezcla.
