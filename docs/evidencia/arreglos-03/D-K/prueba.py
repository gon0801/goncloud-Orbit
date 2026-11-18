# ARREGLOS 03 D-K, prueba real (estimacion.md F7: una alerta critica nunca llega).
# Uso: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-03/D-K/prueba.py
# Solo funciones puras y el codigo fuente desplegado: no lee la base ni manda nada.
# Con el codigo anterior sale 1: ImportError: cannot import name 'aviso_reputacion'.
import inspect

from app import notifica, reputacion_alertas
from app.notifica import aviso_reputacion

texto = aviso_reputacion([{"severidad": "critica", "mensaje": "reseña de 1 estrella en MLM1"},
                          {"severidad": "aviso", "mensaje": "el rating bajó a 4.1"}])
assert "Urgente: reseña de 1 estrella en MLM1" in texto and "Aviso: el rating bajó a 4.1" in texto, texto
assert not hasattr(reputacion_alertas, "carga_reputacion_digest"), "las alertas siguen colgadas del resumen del ciclo"
assert "reputacion" not in inspect.getsource(notifica.notifica_digest), "el resumen del ciclo sigue cargando reputación"
assert "avisa_pendientes" in inspect.getsource(reputacion_alertas.ejecuta_alertas), "la revisión de las 10:30 no manda las alertas"
print("D-K desplegado")
