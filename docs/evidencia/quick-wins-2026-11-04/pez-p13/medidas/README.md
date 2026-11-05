# P13: qué miden 16, 19 y 22 en las nueve variantes PEZ

Las fuentes digitales discrepan sobre el tamaño de las arras. MeLi afirma un diámetro común de 16 o19 mm para cada publicación de seis diseños; Odoo registra PEZ con familia22 en el nombre del SET y en los componentes de arras y cofre. No se obtuvo una medición física ni una ficha técnica que confirme el diámetro real de PEZ. Por tanto, no está acreditado sustituir16/19 por 22 como medida física.

## Evidencia fresca y alcance

- [meli-actual.json](meli-actual.json): GET de nueve publicaciones y sus descripciones, observadas el 5-nov-2026 entre 04:05:21 y04:05:24 UTC. Incluye las54 variantes, sus atributos y fotos. No hubo PUT, refresh de token ni cambios de estado.
- [odoo-actual.json](odoo-actual.json): lecturas XML-RPC terminadas a 04:07:05 UTC. Nueve SET y sus BOM activos; componentes, nombres, atributos, descripciones, referencias de proveedor y contraste dimensional de los54 SET de esas mismas plantillas. Solo `read`/`search_read`, además de autenticación; timeout15s.
- [matriz.md](matriz.md) y [matriz.json](matriz.json): una fila por listing/variante PEZ, con texto, componentes, estuche, campos logísticos y estado. El JSON incluye el contraste acotado de los otros45 diseños; no incorpora nuevos mappings.
- Las nueve descripciones y nueve composiciones BOM siguen iguales a las capturas de [identidad](../../pez-correccion/identidad/README.md). Los `write_date` de Odoo cambian por actividad del sistema, pero no prueban cambio de medida ni de composición.
- [fotos.json](fotos.json) conserva URL pública, ID, hash SHA-256 y OCR de las65 imágenes de las variantes PEZ. Se conservaron únicamente las nueve imágenes que muestran cotas de estuche; las otras56 descargas propias se retiraron. Las nueve fotos principales ya estaban en `pez-correccion/identidad/imagenes/`. [ocr.json](ocr.json) conserva la extracción auxiliar completa; se comprobaron visualmente las nueve imágenes con cotas.

## Qué representa cada número

| Dato | Componente y significado acreditado | Límite |
|---|---|---|
|16 mm MeLi|El texto dice expresamente «diámetro de las arras» en Carte dorado, Carte plateado e Italia; tres menciones por descripción.|Es descripción del item, sin diámetro separado por variante. No acredita medición real ni se refiere al estuche/cofre.|
|19 mm MeLi|El texto dice expresamente «diámetro de las arras» en Blanco con broche, cuatro Gamuza y Platino CH; tres menciones por descripción.|Mismo límite: dato comercial común a seis diseños.|
|22 Odoo|Nueve nombres SET contienen `22 mm`; cada BOM PEZ contiene `ARR-22-DOR-PEZ` o `ARR-22-PLA-PEZ`, además de `COF-22-DOR` o `COF-22-PLA`.|Es una familia/tamaño nominal documentado. El nombre del cofre no declara su ancho exterior; tampoco hay un campo de diámetro ni ficha que permita convertir por sí sola esa nomenclatura en una medición física.|
|10×10×5 cm|Estuche Carte y Platino CH: texto y fotografía acotada coinciden.|Anotación digital de estuche cerrado, sin medición física realizada en esta revisión.|
|9×9×4 cm|Estuche Blanco con broche: texto y fotografía acotada coinciden.|Igual límite.|
|8×11×4 cm|Estuche Italia: texto y fotografía acotada coinciden.|La imagen distingue ejes8 y11; no es diámetro de las arras.|
|9×9×5 cm|Estuches Gamuza: texto y fotografías acotadas coinciden.|No es diámetro de arras ni tamaño del cofre interior.|
|`SELLER_PACKAGE_*`|Alto/largo/ancho y peso del paquete declarados en MeLi; véase cada fila.|Campos logísticos separados de las dimensiones del estuche. Se conservaron sin corregirlos ni deducir medidas del producto. `shipping.dimensions` está vacío en las nueve lecturas.|

Estos productos son sets de arras ceremoniales con estuche y cofre. Las fuentes examinadas no vinculan los16/19/22 mm a placa, dije, tira o pulsera. No se reinterpretaron los términos ni se usaron proporciones de píxeles para medir.

En Odoo, `meli_dimensions` está vacío en los nueve SET y diez componentes. Los componentes PEZ no tienen atributos de medida ni descripciones técnicas; el componente Italia solo aporta una referencia de modelo. Las doce referencias de proveedor no contienen código o nombre alternativo de producto. La consulta acotada de adjuntos de esos productos/plantillas y mensajes con `mm` no devolvió registros. La introspección mediante `ir.model.fields.search_read` identificó `meli_dimensions` como único campo de dimensión por nombre/etiqueta en `product.product` y `product.template`; es un campo de texto, vacío en el alcance. `weight`/`volume` con cero no constituyen medidas físicas válidas aquí.

## Causa probada y causa pendiente

Está probado que existe una afirmación de diámetro universal en una descripción común a seis diseños, mientras las fuentes vigentes de composición usan familias diferentes de la publicada. Esto explica dónde vive el dato contradictorio y por qué corregir solo el mapping de PEZ no modifica la descripción. No está probado quién originó16/19, cuándo se midieron, si proceden de una receta anterior ni cuál es el diámetro físico correcto. No se atribuye la causa a una copia o migración sin evidencia.

El contraste actual de 54 SET homólogos por código, limitado a componentes de diámetro, encontró51 recetas con `ARR-22` y `COF-22`. En **Italia** hay tres con familia22 (Centenario, Corazón, PEZ) y tres con familia19 (San Benito, Virgen busto, Virgen completa). Es un contraste de recetas registradas, no una verificación física ni una autorización para corregir las otras45 variantes. En particular, cambiar toda la descripción de Italia a «22 mm» extendería la afirmación a diseños con receta19.

## Borrador mínimo, sin publicar

[borradores.json](borradores.json) contiene, por cada item, los tres fragmentos exactos a retirar y una descripción neutra completa con el resto del texto conservado. No modifica acabados, materiales, modelos, contenido, medidas de estuche, SKU, precio ni fotos. Algunas descripciones presentan contradicciones adicionales de modelo/acabado; quedaron fuera de este borrador de medidas.

| Item / listing | Afirmaciones comunes a retirar en la alternativa neutra | Adición exclusivamente condicional |
|---|---|---|
|MLM2163407464 /1562|Las tres referencias de 16 mm.|Nota PEZ22 solo tras confirmación física.|
|MLM1890653227 /1563|Las tres referencias de 16 mm.|Misma condición.|
|MLM4735255462 /1578|Las tres referencias de 19 mm.|Misma condición.|
|MLM2724304859 /1579|Las tres referencias de 16 mm.|Misma condición; no asignar22 a sus otros diseños.|
|MLM2727437671 /1582|Las tres referencias de 19 mm.|Misma condición.|
|MLM2727363007 /1583|Las tres referencias de 19 mm.|Misma condición.|
|MLM2727328203 /1584|Las tres referencias de 19 mm.|Misma condición.|
|MLM4749600210 /1585|Las tres referencias de 19 mm.|Misma condición.|
|MLM4763716652 /1590|Las tres referencias de 19 mm.|Misma condición.|

La alternativa neutra elimina únicamente: la viñeta «Medidas: Arras de Nmm de diámetro…», la línea «Diámetro de las arras: Nmm» y la oración «Su tamaño de Nmm de diámetro ofrece una presencia visual equilibrada y acorde al ritual». No inventa otra cifra. Los datos digitales sostienen retirar una afirmación universal no conciliada, pero no permiten llamar físicamente falso al 16/19.

**Si se confirma explícitamente que el diámetro físico de las arras PEZ es22 mm**, añadir únicamente: «Variante Sello cristiano (PEZ): arras de 22 mm de diámetro». Esta nota describe el diámetro de las arras, no el tamaño del cofre ni del estuche. No transferir22 mm a los otros cinco diseños por inferencia. Si no se confirma, conservar el borrador sin una cifra PEZ.

Las nueve descripciones son compartidas: cualquier modificación afectaría54 variantes comerciales, no solo nueve PEZ. Los borradores no se publicaron. Los estados siguen siendo siete `active`, uno `under_review` y uno `paused`; no se propone reactivar ninguno.

## Reproducción

```bash
ssh -o ConnectTimeout=15 goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/meli.py > docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/meli-actual.json
ssh -o ConnectTimeout=15 goncloud 'python3 -' < docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/odoo.py > docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/odoo-actual.json
python3 docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/fotos.py
swift docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/ocr.swift docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/fotos/*.jpg > docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/ocr.json
python3 docs/evidencia/quick-wins-2026-11-04/pez-p13/medidas/resumir.py
```

Los secretos se leen en memoria en el servidor y no se imprimen. No se consultó PostgreSQL ni se escribieron Odoo, Orbit, bridge, accounting o MeLi. Las capturas no son una transacción atómica entre sistemas. [huellas.json](huellas.json) fija las capturas y derivados de esta lectura.

Validación local: Ruff, AST de los scripts, seis hashes de capturas/derivados y reproducción de los27 retiros exactos previstos. `pre-commit run --files` pasó revisión de secretos y conflictos. No se añadieron pruebas de producto: este trabajo solo captura evidencia y prepara textos sin publicarlos.
