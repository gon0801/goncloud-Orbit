# C. Economía del último cambio de puja (M5/M6): resultado

Copia revisada: `orbit07-wt` en `3c841f4` (master con ORBIT 07 `7cf7c9a` y CLARIDAD 01 K encima).
Sondas en `scratchpad/orbit07-review/sondas/C_*.py`, cada una con su salida en el `.txt` del mismo nombre.
Se corren con `/Users/dn/dev/goncloud-Orbit/.venv/bin/python -I <sonda>` desde esa carpeta (la sonda
inserta la ruta de la copia). Todas son puras: no usan base.

Conclusión general: M6 **no puede subir el gasto** por encima de lo que hacía el motor antes de ORBIT 07.
`_juicio_subida` solo devuelve `sin_evidencia` o `frena`, y `permite` existe solo para los recortes
(C_m6_politica.txt §3). El ciclo no se cae con ceros, None ni montos extremos (§1). El riesgo está en el
lado contrario: subidas de hojas rentables frenadas por mucho tiempo con evidencia de una sola semana, más
un hueco donde el freno prometido no actúa.

## Hallazgos confirmados

### H1. Una sola semana con ruido frena las subidas de una hoja rentable hasta 74 días
- Severidad: MEDIO (ventas perdidas; no gasta de más)
- Frente: dinero
- Dónde: `app/optimizer/economia_cambio.py:114-116` (frena si `post.venta <= pre.venta`) y `:134-142`
  (`_utilizable` no tiene vencimiento). `app/lecturas_caso.py:721-725`: `aplicable` solo cae con un evento
  posterior y `madura` nunca vuelve atrás. `app/optimizer/politica.py:385-392`. La ventana que se juzga es
  `[x+1, x+7]` (`lecturas_caso.py:578`), pero el mínimo de 20 clics (R15, `politica.py:380`) se cuenta en
  `[x+1, D-3]` (`lecturas_caso.py:570,616`).
- Escenario: el motor sube una hoja en `x`. La semana anterior tuvo 1 venta de 30 USD; la siguiente, 3 clics
  y 0 ventas (o la misma venta de 30 USD con ACoS 16.7 % contra target 25 %). En la ventana madura completa la
  hoja vende al 10 % de ACoS y R6 pide +15 % cada día. Desde `x+17` el episodio queda maduro y aplicable, y
  `decide` devuelve `Mantener(subida_sin_economia)` todos los días hasta `x+90`. En `x+91` el cambio sale de
  la ventana de 90 días y la subida vuelve a pasar. Lo esperado: un freno con una muestra mínima en la
  ventana que se juzga (la política promete "20 clics al bid nuevo", `m6/politica-implementacion.md:40`, pero
  esos clics se cuentan en otra ventana mucho más larga) o con vencimiento. Nada invalida la comparación
  cuando la semana posterior bajó por causas ajenas a la puja:
  - el negative EXACT que el propio harvest crea en el ad group de origen (`app/apply_harvest.py:14-15`)
    quita justo el término que convertía;
  - un producto agotado;
  - una pausa de la propia hoja.

  Ninguna de las tres está entre las fuentes de `lecturas_caso.py:278-312` ni en `v_cambio_bid`. Las
  decisiones de negative, harvest y pausa sí existen en el ledger de Orbit, así que no aplica la excusa de
  "fuente histórica que no existe" (`m5/contrato-episodio.md:56`).
- Alcance con la config actual: se dispara hoy en MX y US (en live) en toda hoja cuya última subida ya tenga
  episodio maduro (17 días o más) sin otro evento posterior. Una subida no compra una venta más en una semana
  con pocos pedidos en buena parte de los casos, solo por azar. El repricing, los cambios de presupuesto o los
  product ads nuevos acortan el freno porque vuelven el episodio no aplicable. La frecuencia real no está
  medida (consulta abajo, no ejecutada).
- Evidencia (sonda ejecutada):
  ```
  C_m6_politica.txt §2
    sin episodio (R14-R17 como antes): Mover(factor=0.15, bid_nuevo=1.1500, motivo='bajo_target')
    episodio post 3 clics / 0 ventas: Mantener(motivo='subida_sin_economia')
    episodio venta igual, ACoS post 16.7 %: Mantener(motivo='subida_sin_economia')
    efecto.clics_post usado por R15 = 40 | clics de la ventana post que juzga M6 = 3
  C_m5_episodio.txt §1 (camino real LecturasPlataforma.caso -> decide)
    D=x+ 16: inmaduro -> Mover(+15 %)
    D=x+ 17 / 30 / 60 / 90: maduro aplicable -> Mantener(subida_sin_economia)
    D=x+ 91: sin cambios en 90 dias -> Mover(+15 %)
  ```
- Cómo reproducir: `python -I C_m6_politica.py` y `python -I C_m5_episodio.py`. Para medir en producción,
  no ejecutado: `SELECT id, platform, notes->'skips'->'entidad'->>'subida_sin_economia' FROM optimizer_cycle
  ORDER BY id DESC LIMIT 20;`

### H2. El freno de subida no actúa cuando la semana previa no tuvo ventas
- Severidad: MEDIO
- Frente: dinero
- Dónde: `app/optimizer/economia_cambio.py:112-113` (`if pre.venta <= 0: return SIN_EVIDENCIA`) va antes
  de la regla "no creció la venta" (`:114`) y de la regla de contribución (`:117-120`).
- Escenario: la semana previa a una subida tuvo gasto pero 0 ventas, algo común en hojas de poco volumen.
  - La semana posterior tuvo 0 ventas con el doble de gasto. La política manda frenar ("no creció la venta",
    `m6/politica-implementacion.md:19-22,39`). El código devuelve `sin_evidencia` y la subida +15 % pasa.
  - Con margen del 8 %, la semana posterior vendió 10 con 50 de gasto: la contribución cayó de -10 a -49.2 y
    la política manda frenar. También pasa.
  - Discontinuidad: si la semana previa tuvo 1 centavo de venta, el mismo caso sí se frena.
- Alcance con la config actual: live hoy en MX y US. El motor se comporta igual que antes de ORBIT 07 (solo
  R16 por tráfico): no es una regresión, pero el freno prometido falta justo en las hojas de bajo volumen.
  Junto con H1, que la regla frene durante 74 días o no frene nunca depende de si la semana previa tuvo al
  menos una venta.
- Evidencia (sonda ejecutada, C_h2_pre_sin_venta.txt):
  ```
  pre 0 ventas/10 gasto, post 0 ventas/20 gasto      -> juicio=sin_evidencia decide=Mover(+15 %)
  pre 0/10, post 10/50, margen 8 (contrib -10 -> -49.2) -> juicio=sin_evidencia decide=Mover(+15 %)
  control: pre 0.01 de venta, mismo post              -> juicio=frena decide=Mantener(subida_sin_economia)
  ```
- Cómo reproducir: `python -I C_h2_pre_sin_venta.py`

### H3. Un cambio de puja dentro de `[x-7, x-1]` deja de contar al pasar de D-90 y el episodio contaminado queda "maduro"
- Severidad: MEDIO (alcance bajo)
- Frente: dinero
- Dónde: `app/lecturas_caso.py:155` y `:816` (`_SQL_CAMBIOS` solo trae `confirmado_el >= D-90`). Los
  eventos de puja del episodio salen solo de esa lista (`:714`, `:721`). Los eventos de campaña, precio y
  anuncios sí se leen desde D-180 (`:829-839`). El contrato dice que un cambio de puja dentro de
  `[x-7, x+7]` vuelve el episodio incomparable (`m5/contrato-episodio.md:31`).
- Escenario: hay un cambio de puja en `x-3`, por ejemplo un recorte del motor seguido de R1 en `x`, o una
  reversa del dueño. Después la hoja no cambia en 83 días o más. Hasta `D=x+87` el episodio queda
  `incomparable`. En `D=x+88` el cambio de `x-3` sale de la consulta y el episodio pasa a `maduro`, aunque su
  ventana previa `[x-7, x-1]` mezcla dos pujas. Entre `x+88` y `x+90` M6 frena la subida (o permite un
  recorte) con una comparación contaminada.
- Alcance con la config actual: live, pero necesita dos cambios separados por 7 días o menos y luego casi 3
  meses quieta. La ventana dura como máximo 7 días por hoja.
- Evidencia (sonda ejecutada, C_m5_episodio.txt §2):
  ```
  D=x+87: cambios visibles=['2026-07-29','2026-08-01'] estado=incomparable motivo=cambio_en_ventana -> Mover(+15 %)
  D=x+88: cambios visibles=['2026-08-01']              estado=maduro       motivo=None -> Mantener(subida_sin_economia)
  ```
- Cómo reproducir: `python -I C_m5_episodio.py`

### H4. Con la protección por venta reciente, una hoja que antes se pausaba ahora puede recibir +15 %
- Severidad: MEDIO (puede ser diseño de M1; lo reporto porque la pregunta 7 lo pide; ver la sospecha 2 de A)
- Frente: dinero
- Dónde: `app/optimizer/bid.py:262-267` suprime la pausa. Después el juicio corre normal (`politica.py:225-228`
  y `:261-264`), y R6 (`:339`) usa la ventana ponderada D-90..D-10. `app/cycle.py:2199` solo detiene un
  `Pausar`. M6 solo frena si hay episodio maduro, y la mayoría de las hojas no lo tiene.
- Escenario (datos coherentes): ventana de cortes D-40..D-10 con 0 pedidos, 150 clics y 60 USD (califica
  para pausa). Todos los pedidos del tramo reciente cayeron en D-50..D-41, más 3 en el tramo antiguo. Hay 2
  pedidos en D-9..D-1. Antes de ORBIT 07 la hoja se pausaba. Hoy M1 dice "esperar" y R6 le sube la puja +15 %
  durante la espera de hasta 10 días. Ninguna de las tres mejoras pedía subirla: M1 pedía esperar, M2 no
  interviene y M6 no tiene evidencia. La subida sale de pedidos de hace 41 a 90 días. Las pruebas de "puja
  autorizada durante la espera" solo cubren un recorte (`tests/test_optimizer_proteccion.py:191`).
- Alcance con la config actual: `ads_proteccion_venta` está encendido en todo el sistema, así que pasa hoy
  en live en las hojas protegidas cuyo juicio da `bajo_target`.
- Evidencia (sonda ejecutada, C_m1_subida_coherente.txt):
  ```
  con M1 (venta reciente protege): Mover(factor=0.15, bid_nuevo=1.1500, motivo='bajo_target')
  sin M1 (semantica previa)      : Pausar(motivo='pause_umbral')
  ```
- Cómo reproducir: `python -I C_m1_subida_coherente.py`

## Sospechas sin confirmar

1. **Cambio manual de puja fuera de Orbit.** `v_cambio_bid` solo trae cambios de Orbit: el motor, las
   reversas por Orbit y los ajustes de ubicación (`migrations/0067...sql:65-100`). `ad_entity_state` es un
   caché sin historia. El episodio no compara `caso.bid.valor` con el `bid_despues` del cambio ancla, así que
   M6 juzga con un cambio del motor que el dueño ya sustituyó en Seller Central. R14-R17 ya tenían esa
   ceguera; M6 la hereda y la usa para permitir recortes o frenar subidas. Para confirmarlo hay que contar
   en producción las hojas con `current_bid` distinto del último `bid_despues` de `v_cambio_bid` (que no sea
   un ajuste de campaña). Consulta de solo lectura, no ejecutada.
2. **Cobertura real de episodios maduros.** De ella depende cuánto pesan H1 y H2. Falta correr
   `docs/evidencia/orbit-07/m5/consulta-episodios.sql` con `orbit_read`.

## Zonas revisadas sin hallazgos

- **Aritmética (pregunta 2):** no hay excepciones con gasto 0, venta 0, todo en 0, None, 0.0001 ni montos de
  14 dígitos, con margen None, 8, 0 o -5. Todo es Decimal. M6 nunca devuelve `permite` para una subida
  (C_m6_politica.txt §1 y §3). M5 y M6 no tocan el redondeo de pujas: `_paso` no cambió en el diff.
- **Recorte repetido (pregunta 3):** `permite` exige episodio maduro, es decir 17 días o más desde el último
  cambio. Tras el recorte permitido, el episodio nuevo queda inmaduro 17 días y R16 vuelve a mandar, así que
  M6 no acelera el ritmo de recortes. R1 (regreso), R17 (piso aprendido, `politica.py:402-407`) y los límites
  de `_paso` (-30 % y piso del goal) siguen intactos. No hay espiral más rápida que antes.
- **Caída a R14-R17 sin episodio (pregunta 5):** en `politica.py` el diff solo envuelve R16. Con episodio
  None, inmaduro, incomparable o no aplicable, `juicio_repeticion` devuelve `sin_evidencia` y el código es el
  de antes. El SQL económico conserva idénticas las 13 primeras columnas, los parámetros cuadran (46 y 90) y
  el `EfectoCambio` es el mismo con M5 encendido o apagado (C_sql_params.txt). Los cambios de `bid.py` son
  de M1, no de M5/M6.
- **Congelado y replay (pregunta 6):** `como_json` y `desde_json` dan una ida y vuelta exacta, y `reproduce`
  coincide con `decide`, incluso con contribuciones de 28 dígitos (C_replay.txt). Las pujas no pasan por la
  cola: `apply_cola.py:177` solo maneja pause, negative y harvest, así que nada del episodio se relee fresco.
  `tools/rejuega_niveles.py:369` no pasa `con_economia`, pero es una herramienta fuera de línea que no mueve
  dinero.
- **Ancla contra ventanas (pregunta 1):** `_lee_efecto` mide las ventanas alrededor de `cambios[-1]`
  (`lecturas_caso.py:561`) y el episodio se ancla en el último cambio real (`:710`). Cuando no coinciden, el
  cambio posterior vuelve el episodio no aplicable o incomparable, así que nunca se usa (C_m5_episodio.txt
  §3). Solo queda una etiqueta engañosa en la auditoría.
- **Error de un día y zona horaria:** `x` es la fecha UTC de confirmación. El cron corre a las 08:40 UTC, que
  son las 00:40 en Pacífico (horario de invierno), las 01:40 (horario de verano) y las 02:40 en CDMX, el mismo
  día local, así que no hay corrimiento. Los eventos en UTC y el retraso de `observed_at` solo empujan hacia
  "posterior" o "dentro de la ventana", que son los lados conservadores. Ventanas de 7 fechas exactas,
  madurez `x+7 <= D-10`.
- **Interacción con M2 (pregunta 7):** R3 y la contribución usan el mismo equilibrio
  (`lecturas_caso.py:480-497`). En la sombra de `_anota_sombra`, la contribución se queda con el equilibrio
  vivo aunque se pruebe otro, pero es solo una anotación y nunca se aplica. Ninguna combinación de M1, M2 y
  M6 inventa un valor de puja: M6 solo convierte una subida en `Mantener` o quita el freno de tráfico de un
  recorte (C_m1_m2_m6.txt).
- **Tiempo (pregunta 8):** sigue habiendo una consulta por hoja con cambio, igual que antes, y M5 agrega 4
  consultas por plataforma. La mediana de TX2 pasó de 1980.78 a 1975.52 ms con 2000 hojas y 200 cambios
  (`m6/rendimiento-comparado.json`). No hay riesgo de pasar el tiempo límite.
- **Config:** un valor ajeno en `ads_bid_economia_<plat>` provoca `ValueError` y el ciclo falla (fail-closed).
  El escritor quita la clave con `off` y valida antes de guardar (`config_write.py:337-341,507`). Los ids de
  eventos (`campana_ajuste.campana_id`, `ads_campana_config_observation.ad_entity_id` y `product_ad.parent_id`)
  están en el mismo espacio que `v_hoja_activa`. Las hojas decisoras exigen ENABLED, igual que la vista.
