# ORBIT 19 A.5 - preflight de despliegue

Fecha: 2026-09-06 UTC. Lecturas hechas por SSH en `goncloud`; no hubo DDL,
mutacion de datos, POST a Amazon ni llamada a `/crear`.

## Esquema y estado

La consulta de preflight devolvio:

```text
campana_grupo|f|0
```

El primer campo confirma la tabla F1; el segundo indica que `target_origen`
aun no existe; el ultimo es el numero de lotes, cero. La inspeccion ampliada
confirmo tambien `campana_grupo=0` y `campana_grupo_producto=0`.

Existen los prerequisitos de 0019: `product`, `listing`, `ledger_event`,
`sku_cost`, `ingest_run`, `ad_entity_state`, `v_target_margen_plataforma` y
el tipo `campana_rol`; los cuatro roles de aplicacion existen. La
configuracion vigente no contiene `fabrica.creacion`, que el codigo resuelve
como `v1` fail-closed.

## Sonda de solo lectura

- `GET /health` devolvio `{"status":"ok"}`.
- `GET /api/fabrica/catalogo?plataforma=amazon_mx` devolvio 249 productos. La
  primera publicacion observada fue `listing_id=1249`, ASIN `B0CJT48QYC` y
  SKU de Amazon no vacio. La sonda de preview posterior puede usarla sin
  crear ni modificar una campana.
- `orbit-app` y `orbit-db-1` estaban sanos; no se tocaran `bridge` ni
  `accounting`.

## Secuencia preparada

El runbook 0019 en `docs/DEPLOY.md` exige backup validado, transaccion unica,
verificacion de columnas/constraints/conteos, despliegue de solo `orbit-app`
y smoke de catalogo y preview. El setting queda en `v1`; no se habilitan altas
v2 ni se usa una campana real para ensayar recuperacion. La recuperacion de
lote v2 parcial se demuestra con el doble controlado de la suite de CI.
