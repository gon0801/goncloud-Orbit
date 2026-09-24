# FABRICA 02 D.3: go 1 y encendido (2026-09-23 UTC)

El dueño escribió el literal `D.3 encender grupo 1 kit_arras` y pidió seguir.
Su primer intento de escribirlo en zsh devolvió `command not found`: zsh intentó
ejecutarlo como programa y no mutó Orbit. El lead usó exactamente ese texto en
`--go` para el **go 1**. La aprobación del go 2 sigue pendiente.

## Preflight inmediato

Lectura como `orbit_read`: `config_version` 20, `ads_optimizer_mode=live`,
`ads_apply_cap_amazon_mx_harvest=2` y `_amazon_us_harvest=2`. Cola harvest no
terminal 0, jobs en curso 0, jobs `done` con `external_ids.hermanas` 0.
El cron del usuario `gon` programa `app.cli cycle --platform amazon_mx` a las
08:41 UTC cada día; el grupo no se fuerza con `/run`.

Dry-run de `tools/goals_modo_grupo.py --grupo 1 --mode live`:

```text
grupo: id=1 platform=amazon_mx tipo_producto=kit_arras
envolvente: ads_optimizer_mode=live (modo efectivo = meet)
goal=11 rol=product_targeting shadow → live bid-solo
goal=9 rol=category_phrase shadow → live bid-solo
goal=10 rol=category_broad shadow → live bid-solo
goal=8 rol=category_exact shadow → live bid-solo
goal=12 rol=auto_discovery shadow → live bid-solo
candidatas: 5
huella: 2bfabc992adf0951
```

## Ejecución y readback

Se ejecutó por stdin dentro de `orbit-app-1` con
`--grupo 1 --mode live --acepto-mutacion-real --esperado 5
--huella 2bfabc992adf0951 --go "D.3 encender grupo 1 kit_arras"`.
Salió con código 0: los goals 11, 9, 10, 8 y 12 informaron `cambiado` y
`readback mode=live efectivo=live`.

Una segunda consulta independiente como `orbit_read` confirmó:

| Goal | Rol | Mode | Destino | Bid |
|---|---|---|---|---|
| 8 | category_exact | live | terna NULL | 11.6200 MXN |
| 9 | category_phrase | live | terna NULL | 11.6200 MXN |
| 10 | category_broad | live | terna NULL | 11.6200 MXN |
| 11 | product_targeting | live | terna NULL | 11.6200 MXN |
| 12 | auto_discovery | live | terna NULL | 11.6200 MXN |

A las 02:44 UTC, cola harvest no terminal 0 y jobs en curso 0. El siguiente
paso es observar el primer harvest natural y verificarlo hasta `done` con
IDs de keyword y hermanas, biblioteca, ledger y `/cortes`. Solo entonces se
prepara el dry-run de `tools/reversa_harvest.py` y se solicita el go 2.

## Suspension tras revision adversarial

El 2026-09-23 a las 03:35 UTC, el dueno autorizo pausar el grupo tras la
revision adversarial. Se ejecuto el kill switch documentado sobre los goals
8, 9, 10, 11 y 12, uno por uno. Una lectura independiente como `orbit_read`
confirmo los cinco en `shadow`, **0** filas harvest no terminales y **0**
jobs en vuelo. No hubo llamada a Amazon ni reversa de harvest.

La observacion del primer harvest y el go 2 quedan suspendidos. La revision
encontro fallos reproducibles en el apagado de colas, la revalidacion del
target y de jobs que esperan cuota, y la procedencia de objetos adoptados
en la reversa. La correccion se prepara en una rama separada; este archivo
no afirma que este desplegada. El grupo sigue en `shadow` hasta una nueva
decision explicita del dueno.

## Reencendido tras PR #324 (2026-09-24 UTC)

El PR #324 ya estaba mergeado y desplegado. El PR #327 corrigio solo CI;
su corrida en `master` termino verde. Tras pedir una nueva decision para
reactivar el grupo, el dueno respondio literalmente: `tienes mi go literal y
especifico`. Se uso de nuevo el go 1 registrado arriba,
`D.3 encender grupo 1 kit_arras`, para este encendido. No se interpreto
como go 2 para revertir un harvest.

Preflight inmediato, de solo lectura: `config_version` 20,
`ads_optimizer_mode=live`, caps de harvest MX/US `2/2`; cinco candidatas
`shadow` (goals 11, 9, 10, 8 y 12), huella `2bfabc992adf0951`.

Se ejecuto `tools/goals_modo_grupo.py` por stdin dentro de `orbit-app-1` con
`--grupo 1 --mode live --acepto-mutacion-real --esperado 5
--huella 2bfabc992adf0951 --go "D.3 encender grupo 1 kit_arras"`.
Codigo de salida 0: cinco `cambiado` y cinco readbacks `mode=live
efectivo=live`. Una lectura independiente como `orbit_read` confirmo los
goals 8-12 `live`, habilitados, terna NULL, `harvest_default_bid=11.6200`
MXN y `updated_at=2026-09-24 04:03:55.326946 UTC`. La cola harvest no
terminal y los jobs en vuelo quedaron en 0. El tool no llama a Amazon.

Sigue observar el primer harvest natural del cron MX de las 08:41 UTC hasta
`done`. La reversa del job exige su propio dry-run y go 2 separado.
