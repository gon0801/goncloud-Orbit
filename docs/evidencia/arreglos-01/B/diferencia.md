# Diferencia menor con el pseudocodigo del diseno (E-S1)

El diseno (`docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`, E-S1)
sugiere `comunes = dict(..., keywords_propias=exactas)` y asi pasarlo a ambos
procesadores. En el codigo real `comunes` alimenta tambien a `_procesa_decisora`,
cuya firma es keyword-only y cerrada: recibir `keywords_propias` ahi reventaria con
`TypeError`.

Forma implementada, con el mismo efecto (una lectura por plataforma en TX2):
`_procesa_grupo(..., keywords_propias=keywords_propias, **comunes)`. El valor se
lee una sola vez en TX2 junto a `ventanas_evidencia_ad_group` y solo viaja al
camino de terminos, que es el unico que llama `decide_hygiene`. No cambia ninguna
firma del motor puro ni la semantica pedida.
