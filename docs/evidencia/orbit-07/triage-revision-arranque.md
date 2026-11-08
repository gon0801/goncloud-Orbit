# Tratamiento de la revision de arranque

Opus emitio APPROVE_START para M0 mas M1. La ejecucion sigue pendiente de elegir la via que admite la API propia de DeepSeek Flash.

- Aceptado: incorporar el plan y su evidencia al master recibido; anadir solo su entrada al manifest. Ya preparado en este worktree. Se conserva el active y todas las entradas actuales.
- Aceptado: tratar el estado antiguo de AUTO-03 como historico. El orquestador consulto de nuevo la PC: enc_07844b614147c266 terminado, entrega desplegada, cero encargos activos. El servidor conserva f86ed7dcd77f0af7b540217c3df9422ebe5c7154, ESTADO=verificado y health=ok.
- Aceptado: M0 necesita la consulta de pedidos recientes con rol de lectura antes de fijar invariantes. Se entrega al implementador acceso de lectura o el resultado de su consulta. Usar una API de inferencia no demuestra que el agente carezca de SSH; esa limitacion sugerida por el revisor no esta comprobada.
- No se adopta sustituir la comparacion base/candidato por la duracion global de ciclos anteriores. Se conserva una comparacion proporcional sobre la misma carga aislada, como pide el plan; no exige una espera de calendario ni copiar necesariamente toda produccion.
- Primera entrega: M0 necesario para M1 y M1 completo, con pruebas monetarias relevantes, regresion demostrada en la base, recorrido de ciclo y cola, revisiones de Opus y CI verde. Merge, despliegue y encendido de pausa quedan fuera.
- M3/M4 y M6 conservan las decisiones monetarias del dueno que declara el plan; no bloquean M1.
