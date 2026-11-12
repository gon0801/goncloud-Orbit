# Comparación de Salud (bloque B)

Antes del cambio, la página de producción `http://10.13.13.1:8010/salud` y su JSON
`/api/dashboard/salud` quedaron guardados en `antes/` (`salud.html` y
`api-salud.json`, 12 nov 2026). `despues/salud.html` es el template nuevo
renderizado con ese mismo JSON, sin volver a consultar datos.

## Comando y salida

```text
python docs/evidencia/disenos-01/B/compara.py
origen del payload: .../antes/api-salud.json
plataformas: amazon_us, amazon_mx
valores comprobados: 425
sin diferencias
```

El comparador recorre las dos plataformas y exige que cada valor del payload
siga visible en su sección del HTML nuevo: datos de métricas y sincronización,
ciclo actual, 14 ciclos con fecha/estado/decisiones/motivo, motivos y conteos de
lo saltado, reportes de Ingesta Ads con perfil/fecha/edad/estado, incidentes,
fuentes de SP-API con filas y saltadas, quota con usados/tope/fuente, precios,
avisos de campaña (campaña y texto), destino de harvest, avisos de propuesta y
los números de Jev. Son 425 comprobaciones.

## Pantallas claras

```text
python tools/pantallas_claras.py --base http://127.0.0.1:18077 --rutas /salud --tope 0
/salud: 0 (limpia)
total: 0 hallazgos en 1 rutas
```

El HTML servido para esa medición fue `despues/salud.html`.

## Copia nueva servida contra la base de producción

Con el código nuevo en un contenedor temporal (solo lectura, misma base de
producción, `orbit-app-1` sin tocar) se sirvió `/salud` y se corrió el mismo
comparador contra el JSON vivo:

```text
python docs/evidencia/disenos-01/B/compara.py --base http://127.0.0.1:18098
origen del payload: vivo http://127.0.0.1:18098
plataformas: amazon_us, amazon_mx
valores comprobados: 425
sin diferencias

python tools/pantallas_claras.py --base http://127.0.0.1:18098 --rutas /salud --tope 0
/salud: 0 (limpia)
total: 0 hallazgos en 1 rutas
```

`despues/salud-vivo.html` es ese HTML servido en vivo. El contenedor temporal se
borró al terminar. Esta corrida reproduce el estado que tendrá producción con
el código nuevo; no es el recibo del despliegue.

## Capturas

`despues/salud-1440.png` y `despues/salud-390.png` son la página servida en vivo,
tomadas con el viewport emulado de 1440 y 390 px. En 390 px el ancho real es
390 (`document.documentElement.scrollWidth` = 390) y las tablas largas se
desplazan dentro de su caja; la página no desborda. La primera versión
ensanchaba la página por las tablas dentro de contenedores flex; se corrigió
con `min-width: 0` en los contenedores y con la tabla de quota sin mínimo de
640 px.

## Corrección F1: valores de un dígito

El comparador ya no acepta una presencia suelta. Para cada fila exige las celdas
completas (ciclo, reporte de Ads, fuente SP-API, quota) y para cada lista exige
el motivo y su conteo juntos. Así un `1` no pasa por aparecer en una fecha como
`10 nov`.

Rojo: en `despues/salud.html`, cambiar las decisiones del ciclo `#180` de `1` a
`7` deja `1` presente en la fecha (`presencia_de_1 True`), pero el comparador
nuevo sale 1 con `amazon_us: fila del ciclo 180`. Verde: 557 valores, sin
diferencias, contra el JSON de antes y contra la copia nueva en vivo.

## Corrección F2: Telegram caído en el resumen

Cuando `notes.telegram` existe, el chequeo visible `Último ciclo` pasa a ámbar y
su valor termina en `· Telegram caído (<claves>)`. El detalle conserva su
`p.alerta` con el mismo aviso. La prueba
`test_ui_salud_muestra_nota_telegram_del_ciclo` exige `salud-dot-aviso` y
`Telegram caído` en la tarjeta de resumen; contra el código de `7e50c52` falla
con `el resumen visible no puede quedar verde con Telegram caído`.

## Corrección F6: 390 px con comprobación repetible

```text
python docs/evidencia/disenos-01/B/mide_390.py
{"ancho": 390, "scrollWidth": 390, "bodyScrollWidth": 390, "bodyClientWidth": 390, "bodyScroll": false}
sin desborde a 390 px
```

El comando sirve `despues/salud-vivo.html` y el CSS real, emula 390 px en un
navegador headless y falla si el documento supera 390 o si el body tiene scroll
horizontal. Está declarado en `.interno/comprobaciones.json`. Antes de la
corrección de CSS la misma medición daba `ancho: 678, bodyScroll: true`.

## Después del despliegue

La misma comparación contra la instancia viva es:

```text
python docs/evidencia/disenos-01/B/compara.py --base http://10.13.13.1:8010
```

Esa corrida la respalda el recibo del supervisor, no una comprobación local
hecha antes de desplegar.
