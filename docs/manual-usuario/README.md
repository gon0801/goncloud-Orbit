# Entrenamiento de usuario de Orbit

PDF que enseña todo Orbit, pantalla por pantalla, a una persona sin conocimientos técnicos.

- `paginas/*.html`: una `<section>` por hoja A4, en orden de archivo. `CONTRATO.md` fija formato, tono y fuentes.
- `notas/*.md`: la fuente (archivo:línea o URL de producción) de cada afirmación.
- `python3 docs/manual-usuario/lint.py`: marca jerga, guiones largos y números de página escritos a mano.
- `python3 docs/manual-usuario/build.py [salida.pdf]`: arma el PDF. Falla si falta una pantalla del menú de `app/templates/base.html`, si una hoja se desborda o si una imagen no carga. Necesita `agent-browser`.

Reemplaza al manual «Orbit - Manual de usuario - Decisiones por producto».
