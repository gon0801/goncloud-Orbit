# Panel X.4: scripts de encendido MeLi (ver D.4/panel.md)

Revisor B (workflow panel-d4x4-ops, enfasis X.4): VEREDICTO
APROBADA con fixes, 0 bloqueantes. Su unico hallazgo alto es U1,
dueno de D.4/guia (el redespliegue sellado que ordena `sonda.sh`
era imposible por la guarda 1b): aplicado y probado en D.4 (ver
`../D.4/panel.md` + `../D.4/redespliegue-sellado-simulacion.txt`).

Sobre los scripts X.4 en si (sembrar/sonda/encender/apagar): sin
bloqueantes ni altos. Las medias/bajas del revisor B no se
recuperaron (solo se entrego la sintesis del workflow); quedan sin
triagear.

## GLM 5.3 #2 [media] -> anotado

`encender.sh` no verifica codigo sellado (lo asume en comentario):
con forma sin sellar encenderia modos+goals y las corridas moririan
en `EscrituraNoDisponible` hasta apagar+redesplegar. ANOTADO:
deteccion inmediata (logs del cron), dano acotado a ruido (cero
escritura por fail-closed runtime); el flujo X.4 ya exige el
redespliegue antes de encender. Follow-up: pre-chequeo de
`FORMA_ESCRITURA_MELI` en el contenedor, simetrico a `trae_resumen`.

## GLM 5.3 #3 [media] -> aplicado

`sembrar.sh` real guardaba plan+transcripto con datos PROD dentro
del repo (`GUARDA_DIR=$DIR`; repo publico). APLICADO: en real se
guarda en `mktemp -d` fuera del repo (ruta impresa); sim intacto.

## GLM 5.2 L5 [baja] -> aplicado

`--listing-ids 1,,2` reventaba en `int('')`. APLICADO: validacion
rechaza huecos (`,,`, lider, trailer); probado 2/2/2/0/0.
