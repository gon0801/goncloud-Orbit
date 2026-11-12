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

## Después del despliegue

La misma comparación contra la instancia viva es:

```text
python docs/evidencia/disenos-01/B/compara.py --base http://10.13.13.1:8010
```

Esa corrida la respalda el recibo del supervisor, no una comprobación local
hecha antes de desplegar.
