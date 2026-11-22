# TEMPORADA 01 H.1 — Lo que vio el ciclo en práctica

Lectura de solo lectura sobre producción (`docs/evidencia/arreglos-01/lee_prod.sh`, rol `orbit_read`,
transacción `READ ONLY`) el **2026-11-22**, después del bloque P y antes de encender.

## El ciclo que se leyó

P dejó las claves en `shadow` con los `config_version` **55** (subida rápida MX), **56** (subida rápida
US) y **57** (calendario y temporada MX y US), creados el `2026-11-22 02:49`–`02:50 UTC`. El primer
ciclo terminado después de esa config es el de ese mismo día:

```
select c.id, c.platform, c.mode, c.started_at, c.finished_at, c.status, c.decisions_count,
       (c.notes::jsonb)->'temporada' as temporada
  from optimizer_cycle c
 where c.started_at > (select created_at from config_version where id = 57)
 order by c.id
```

| Ciclo | Plataforma | Terminó | Decisiones | `notes.temporada` |
| --- | --- | --- | --- | --- |
| 204 | amazon_us | 2026-11-22 09:40:07 UTC | 0 | `comparadas` 82, `coinciden` 82, `diferencias` 0, `reactivaciones` 0 |
| 205 | amazon_mx | 2026-11-22 09:41:10 UTC | 0 | `comparadas` 112, `coinciden` 110, `diferencias` 2, `reactivaciones` 0 |

Los dos ciclos corrieron en `live` (el modo del motor) con los interruptores de temporada y subida
rápida en `shadow`, que es lo que distingue la práctica.

## En qué fase cayó y qué pudo ver

El calendario guardado tiene **noviembre en alta** (decisión 1: baja enero, febrero, mayo y junio;
alta marzo, abril, octubre y noviembre), y la vista previa de P.2 ya lo decía: «Hoy: temporada alta.
Meta de ACoS 25 % en MX y US.» El ciclo cayó en **temporada alta**, con 9 días por delante antes del
`2026-12-01` (`sale_alta`).

Eso fija qué podía aparecer en `notes.temporada` y qué no:

- **Entradas a la baja: 0.** No es «todo bien»: en un mes alto no hay entrada que decidir. La línea
  de entradas solo se puede juzgar en enero, febrero, mayo o junio.
- **Regresos: 0 por diseño.** La práctica no abre episodios ni confirma nada, así que no hay nada
  que regresar. Este 0 no prueba el regreso.
- **Retenidos por presupuesto: 0 por diseño.** `_anota_regreso_retenido` solo cuenta con la temporada
  en `live`; en práctica ni se evalúa la retención.
- **Reactivaciones: 0** (`reactivaciones` en las dos plataformas). También por diseño: sin episodio no
  hay pausa de la baja que reactivar.
- **Metas de alta: sí actuaron.** Es lo único que la práctica sí pudo ejercer, y lo ejerció.

## Las metas de alta, con la cuenta hecha

Las dos diferencias de MX son la meta de alta en acción. Las dos hojas tenían el mismo veredicto real
(`Mantener`, `azar_lo_explica`, con la meta base de la plataforma 23.1920 %) y la sombra dio `Mover`
(`bajo_target`) con la meta del juicio en 25 %: el umbral de R6 sube de 0.85 × 23.1920 = 19.72 % a
0.85 × 25 = 21.25 %, y esas dos hojas cruzan.

Para no dejarlo en una afirmación, se reprodujo el caso congelado (`notes.temporada.diferencias[].caso`)
con el `decide` del repo, primero tal cual y después con los marcadores de temporada que la sombra
lleva (estación `alta`, `meta_alta_pct` 25, frontera `alta`), sin tocar nada más:

```
2457 | real: Mantener(azar_lo_explica) | sombra: Mover(bajo_target, 23.48) | base 23.19201313987196647242583925 | alta 25
2969 | real: Mantener(azar_lo_explica) | sombra: Mover(bajo_target, 7.70)  | base 23.19201313987196647242583925 | alta 25
```

Sale idéntico a lo que guardó el ciclo (mismo veredicto, mismo motivo y mismo destino), así que la
causa de las dos diferencias es la meta de alta y ninguna otra.

## `recupera_nivel`, m\* y gasto extra proyectado

**`recupera_nivel`: 0, y no porque la evidencia no lo justificara.** La práctica nunca evaluó R6r: en
el código desplegado (`20b90d9`, `app/cycle_recorrido.py:654`), los insumos de la subida rápida solo
se construyen con `ads_bid_recuperacion_<plat>` en `live`; en `shadow` el caso no lleva el marcador y
el ciclo no calcula ningún candidato. Lo confirma el propio registro: los dos casos congelados del
ciclo 205 no traen `recuperacion` ni `historia`, y el test `test_temporada01_c_practica_no_envia_pujas`
fija ese comportamiento a propósito (en práctica el camino real no puede enviar el salto).

Consecuencia honesta: **m\* máximo y gasto extra proyectado de R6r no son medibles con esta práctica**.
No hay ningún `m*` que reportar, porque no hubo ni un candidato. Ese 0 no es un aprobado de R6r; es
una casilla sin datos.

Lo único proyectable con lo que sí se midió son las dos subidas de 15 % de la meta de alta, con el
mismo gasto y los mismos clics (CPC lineal, que es el supuesto del diseño):

| Hoja | Bid hoy | Destino sombra | Factor | Gasto de la ventana madura | Gasto extra |
| --- | --- | --- | --- | --- | --- |
| 2457 | 20.42 MXN | 23.48 | 1.149853 | 2,306.83 MXN | +345.69 MXN |
| 2969 | 6.70 MXN | 7.70 | 1.149254 | 153.80 MXN | +22.96 MXN |
| Total | | | | 2,460.63 MXN | **+368.64 MXN** |

Son dos hojas de 112 comparadas en MX; en US no hubo ninguna diferencia. La cifra es el techo de lo
que la meta de alta costaría en la ventana leída si las dos subidas se confirman y el clic se comporta
como hoy.

## Lo que la práctica no tocó (y por eso no se puede dar por bueno)

En los ciclos 204 y 205: **0 decisiones** (de cualquier tipo), **0 temporada** (`entra`,
`ajusta`, `sale`), **0 filas** en `temporada_episodio`, `temporada_efecto`, `temporada_fin_reduccion`
y `temporada_exclusion`, **0** `decision_application` y **0** `decision_sin_aplicar`, y **0** filas en
`campana_ajuste`. La práctica cumplió su contrato: anotó y no escribió.

Quedan sin ejercitar, por diseño y por fase, las rutas que solo existen en vivo (diseño, «Riesgos y
límites honestos», punto 16): la lectura fresca antes del PUT, la exclusión por la mano del dueño, la
reactivación de pausas de la baja, la retención del regreso por presupuesto y el candado de las
propuestas. Nada de eso lo prueba esta lectura; lo vigila H.3 desde el primer ciclo en vivo.

## Casos de paro de H.3 en la práctica

Se corrieron las mismas cinco consultas de `docs/evidencia/temporada-01/H/primer-ciclo.sql` sobre los
ciclos terminados después de la config 57:

| Caso de paro | Resultado en práctica |
| --- | --- |
| bid enviado que baja más que la reducción | 0 (0 entradas confirmadas) |
| regreso que baja un bid | 0 (0 regresos confirmados) |
| PUT después de una lectura fresca distinta | 0 (`decision_sin_aplicar` con `bid_cambiado_fuera`: 0) |
| dos entradas para una keyword en un episodio | 0 (0 episodios, 0 entradas) |
| presupuesto escrito sin `APLICAR AJUSTE` | 0 (`campana_ajuste`: 0 filas) |

Ninguno aparece, así que no hay bloque nuevo que abrir antes de H.2. Y conviene decirlo: **que los
cinco den 0 en práctica no significa que los cinco estén bien**; tres de ellos solo pueden fallar
cuando hay escrituras, y la práctica no escribe.

## Resumen para decidir el encendido

La práctica corrió un ciclo completo, en mes alto, y lo que se vio es coherente: 194 comparaciones,
2 diferencias explicadas por la meta de alta, 0 escrituras y 0 casos de paro. Lo que la práctica
**no** vio: entradas (mes alto), regresos, retenidos y reactivaciones (por diseño) y toda la subida
rápida R6r (el modo `shadow` no la calcula). El encendido de H.2 se apoya en la práctica para la
temporada y en las pruebas de base para la subida rápida y para las rutas de escritura; esas quedan
bajo la vigilancia de H.3.
