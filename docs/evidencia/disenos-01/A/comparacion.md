# DISEÑOS 01 A · comparación antes/después

Antes: `antes/decisiones-pagina-1.html`, `antes/decisiones-pagina-2.html`, `antes/total-decisiones.txt` y `antes/api-decisiones.json`, tomados de producción antes de tocar código (11 nov 2026).

Después: `despues/pagina-1.html`, `despues/pagina-2.html`, `despues/pagina-3.html` y `despues/api-decisiones.json`, tomados de una copia de la rama nueva servida en solo lectura contra los mismos datos de producción. La comprobación post-despliegue repite el mismo comando contra `http://10.13.13.1:8010`.

Comando:

```bash
python docs/evidencia/disenos-01/A/compara.py --base http://10.13.13.1:8010
```

Salida de esta corrida (copia local del código nuevo, misma base):

```text
total antes: 2903 · total despues: 2903
pagina 1: 50 antes · 50 despues; pagina 2: 50 antes · 50 despues
dia partido 9 oct: pagina 2 9 oct · pagina 3 9 oct
sin diferencias
```

Qué compara:

- El total de decisiones.
- Las 50 decisiones de la página 1 y las 50 de la página 2: misma entidad (`#ad_entity_id`) y mismo `decision #id`, sin importar el orden de las filas plegadas.
- El JSON `GET /api/dashboard/decisiones`, que no cambia.
- El día partido entre las páginas 2 y 3: el encabezado «9 oct» aparece al final de la 2 y al inicio de la 3.
