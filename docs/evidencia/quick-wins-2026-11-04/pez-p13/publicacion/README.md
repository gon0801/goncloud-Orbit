# P13: publicacion de medidas PEZ confirmadas

El usuario confirmo **«22mm»** para las arras PEZ. La confirmacion resuelve
el dato fisico pendiente de PEZ 02; no se atribuye a una medicion del agente.
Las instrucciones anteriores «ok hazlo» autorizaban completar la correccion.
[Confirmacion](confirmacion.json) y [lote aprobado](lote.json).

## Resultado comprobado: ocho de nueve

Lectura independiente final: **5-nov-2026 04:50:48 UTC**. Ocho descripciones
muestran PEZ de 22 mm; la inhabilitada conserva el texto original. Se retiraron
24 afirmaciones universales y se publicaron ocho notas especificas de PEZ.
Los 54 registros de variantes conservan integramente sus datos, incluidos
precio, existencias y SKU. Los nueve items conservan estados/subestados,
atributos, imagenes, titulo, precios y cantidades. Solo variaron las fechas
`last_updated` y `expiration_time` en siete items; el pausado no mostro
otros cambios ademas de su descripcion.

| Publicacion | Variante PEZ | Resultado | Estado conservado |
|---|---|---|---|
| MLM2163407464 | NH-CAR-BLA-PEZ-DOR | Aplicado | active |
| MLM1890653227 | NH-CAR-BLA-PEZ-PLA | Aplicado | active |
| MLM4735255462 | NH-BLA-BRO-PEZ-DOR | Aplicado | active |
| MLM2724304859 | NH-ITA-PEZ-PLA | Aplicado | active |
| MLM2727437671 | NH-GAM-AZU-PEZ-PLA | HTTP 400; texto original | under_review / forbidden |
| MLM2727363007 | NH-GAM-NEG-PEZ-DOR | Aplicado | active |
| MLM2727328203 | NH-GAM-AZU-PEZ-DOR | Aplicado | active |
| MLM4749600210 | NH-GAM-NEG-PEZ-PLA | Aplicado; MeLi normalizo 12 TAB | active |
| MLM4763716652 | NH-PLA-CH-PEZ-PLA | Aplicado | paused / out_of_stock |

[Lectura final de MeLi](despues.json), [comparacion completa](verificacion-final.json),
[remate: un PUT y una ya aplicada](remate.json), [ledger original](ledger.jsonl)
y [huellas finales del respaldo inmutable](respaldo-servidor-final.json).
El intento de MLM4749600210 queda historicamente como `no_confirmado` en el
ledger; la lectura final y el remate lo reconocen aplicado, sin repetir PUT.

Comprobacion reproducible de ocho aplicadas y una original:

```bash
python docs/evidencia/quick-wins-2026-11-04/pez-p13/publicacion/verificar.py despues.json --item MLM2163407464 --item MLM1890653227 --item MLM4735255462 --item MLM2724304859 --item MLM2727363007 --item MLM2727328203 --item MLM4749600210 --item MLM4763716652
```

## Alcance

Nueve descripciones compartidas por seis variantes cada una. Cada texto
retira tres fragmentos que asignaban 16/19 mm a todos los modelos y agrega:

> Variante Sello cristiano (PEZ): arras de 22 mm de diámetro.

El lote conserva literalmente el resto. No asigna 22 mm a los otros cinco
modelos, cofres o estuches. La API normalizo doce tabulaciones a espacios en
MLM4749600210; se acepta exclusivamente esa conversion completa, sin recortar,
colapsar espacios ni ignorar ningun otro cambio de contenido.

## Ejecucion y comprobacion

Herramienta: `tools/corrige_descripciones_pez.py`. Solo permite los nueve
IDs exactos y `PUT /items/{id}/description` con cuerpo `plain_text`, host fijo,
sin redirects, renovacion de token ni reintentos. Antes de cualquier PUT
comprueba vendedor del pase, ID, estado, variante PEZ, SKU y descripcion de
los nueve items. Respaldo privado 0600 y ledger durables antes de escribir.

Se uso un contenedor puntual con UID 10001, el token existente montado en
solo lectura y el directorio de respaldo. No hubo despliegue del servicio,
migracion, escritura SQL, recosteo ni modificacion de bridge/accounting.

- [Antes](antes.json), [ensayo de nueve pendientes](ensayo.json),
  [canario de un PUT](canario.json) y [comprobacion del canario](verificacion-canario.json).
- [Primera continuacion](aplicacion.json): se detuvo ante HTTP 400 de
  MLM2727437671; su texto permanecio original.
- [Segunda continuacion](continuacion.json): se detuvo al observar que MeLi
  normalizo las tabulaciones de MLM4749600210. No se repitio ese PUT.
  [Lectura parcial independiente](despues-parcial.json) y
  [conciliacion de siete textos](verificacion-parcial.json).
- La regresion de tabulaciones se reprodujo contra la herramienta anterior,
  se corrigio y reviso con GPT Sol antes de continuar con el mismo respaldo.

## Publicacion inhabilitada

MLM2727437671 / NH-GAM-AZU-PEZ-PLA ya estaba `under_review/forbidden` antes
de esta operacion. La [moderacion leida directamente de MeLi](moderacion.json)
indica `DOMAIN_WRONG_CATEG_V2`, creada el **31-oct-2026 01:17:31 UTC**
(30-oct 21:17:31 -0400 en la fuente), motivo:
«Estaba en una categoría incorrecta». La respuesta no incluye `REMEDY`.
No se activo, recategorizo, republico ni reintento el PUT rechazado.

El texto corregido queda en el lote. P13 conserva este bloqueo externo;
resolver la baja requiere un alcance distinto, con la categoria real y el
camino que admita MeLi. No cambiar el estado esperado del lote para forzarlo.

## Verificacion del codigo

24 pruebas focalizadas pasan: 17 de aplicacion/reversa y siete del pase MeLi.
Incluyen vendedor/SKU/texto ajeno, respaldo durable, reanudacion parcial,
timeout/5xx con lectura antes de repetir, canario por CLI real y reversa.
Revision independiente GPT Sol: APPROVE de herramienta, lote y arreglo TAB.

Se detectaron dos fallos antes de produccion: el CLI no transmitia `--item`
y un respaldo nuevo podia apropiarse de textos previamente aplicados. Ambos
se demostraron fallando y se corrigieron antes del primer PUT.
La regresion productiva TAB se demuestra fallando contra la version inicial
`d649ff1e24765594d2a71357dcd7996b28c8dc66cf0e2678984aa1d64c7748b5`.
[Regresion roja](regresion-tabs-rojo.txt) y [suite verde](regresion-tabs-verde.txt).
La version corregida usada para terminar es
`9dacd4bc79ce9c947714f6eda24db5aa76c8822bac964c83c1a8428b7272c587`.

Ruff, compilacion y pre-commit/gitleaks verificados. La integracion del PR
requiere el gate completo verde del commit final; estas comprobaciones
locales no sustituyen CI.

## Respaldo y reversa

Servidor: `/mnt/data/appdata/orbit/backups/pez-medidas-20261105/`.
`respaldo.json` conserva los nueve textos originales y el lote aprobado;
no se recreo ni modifico para reanudar. [Huellas y permisos](respaldo-servidor.json).
El ledger anexa intentos y confirmaciones; una lectura posterior aclara
la normalizacion aceptada en MLM4749600210 sin reescribir su historial.

Reversa del contenido, comprobada en pruebas antes de aplicar (no ejecutada
contra las publicaciones corregidas):

```bash
ssh goncloud 'docker run --rm --user 10001:10001 --network orbit_default -e PYTHONPATH=/app -e ORBIT_SECRETS_DIR=/run/pez -v /mnt/data/appdata/orbit/secrets/meli_tokens.json:/run/pez/meli_tokens.json:ro -v /mnt/data/appdata/orbit/backups/pez-medidas-20261105:/respaldo orbit-pez-descripciones-base:20261105 python /respaldo/corrige_descripciones_pez.py --revertir /respaldo/respaldo.json'
```

La reversa exige vendedor, estado, variante y texto conocidos; si hay cambios
posteriores se detiene. Restaura el texto original, con tabulaciones convertidas
a espacios si MeLi vuelve a normalizarlas. No promete restaurar las fechas
`last_updated`/`expiration_time` que la plataforma renueva al editar.

No hay compare-and-swap de servidor: persiste una ventana entre el GET previo
y el PUT. Las guardas detectan cambios observables; no garantizan exclusividad
frente a otra edicion simultanea. Los snapshots son lecturas secuenciales.

## Fuentes de API

- [Descripcion de articulos: PUT plain_text](https://developers.mercadolibre.com.mx/es_ar/atributos-y-variaciones/descripcion-de-articulos).
- [Moderaciones: estado inactivo y consulta de motivo](https://developers.mercadolibre.com.mx/es_ar/preguntas-y-respuestas/gestionar-moderaciones).
