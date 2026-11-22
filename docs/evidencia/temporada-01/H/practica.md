# TEMPORADA 01 H.1 — Lo que vio la práctica (y lo que falta para la puerta)

Lectura de solo lectura sobre producción (`docs/evidencia/arreglos-01/lee_prod.sh`, rol `orbit_read`,
transacción `READ ONLY`). **Este documento no cierra H.1 todavía**: la puerta exige datos de una práctica
que compare la subida rápida, y esa práctica es el ciclo de las 09:40 UTC del 2026-11-23, el primero que
corre con los dos arreglos de abajo ya desplegados. Aquí queda lo medido, el hueco que encontró la
revisión del dueño y lo que se va a medir.

## El ciclo que sí corrió en práctica (204 US y 205 MX)

P dejó las claves en `shadow` con los `config_version` **55**, **56** y **57** (2026-11-22 02:49–02:50 UTC).
El primer ciclo terminado después de esa config fue el de ese mismo día:

| Ciclo | Plataforma | Terminó | Decisiones | `notes.temporada` |
| --- | --- | --- | --- | --- |
| 204 | amazon_us | 2026-11-22 09:40:07 UTC | 0 | `comparadas` 82, `coinciden` 82, `diferencias` 0, `reactivaciones` 0 |
| 205 | amazon_mx | 2026-11-22 09:41:10 UTC | 0 | `comparadas` 112, `coinciden` 110, `diferencias` 2, `reactivaciones` 0 |

El calendario guardado tiene **noviembre en alta**, y la vista previa de P.2 ya lo decía: «Hoy: temporada
alta. Meta de ACoS 25 % en MX y US.» El ciclo cayó en **temporada alta**, con 9 días por delante antes
del `2026-12-01` (`sale_alta`).

Eso fija qué podía aparecer y qué no:

- **Entradas a la baja: 0, por la fase.** En un mes alto no hay entrada que decidir; la línea solo se
  puede juzgar en enero, febrero, mayo o junio.
- **Regresos: 0 por diseño.** La práctica no abre episodios ni confirma nada.
- **Retenidos por presupuesto: 0 por diseño.** `_anota_regreso_retenido` solo cuenta con la temporada en
  `live`; en práctica ni se evalúa.
- **Reactivaciones: 0.** Sin episodio no hay pausa de la baja que reactivar.
- **Metas de alta: sí actuaron.** Es lo único que esta práctica sí pudo ejercer.

### Las metas de alta, con la cuenta hecha

Las dos diferencias de MX son la meta de alta en acción. Las dos hojas tenían el veredicto real
`Mantener` (`azar_lo_explica`, meta base 23.1920 %) y la sombra dio `Mover` (`bajo_target`) con la meta
del juicio en 25 %: el umbral de R6 sube de 0.85 × 23.1920 = 19.72 % a 0.85 × 25 = 21.25 %, y esas dos
hojas cruzan.

Se reprodujo el caso congelado con el `decide` del repo, primero tal cual y después con los marcadores de
temporada que la sombra lleva (estación `alta`, `meta_alta_pct` 25, frontera `alta`), sin tocar nada más:

```
2457 | real: Mantener(azar_lo_explica) | sombra: Mover(bajo_target, 23.48) | base 23.19201313987196647242583925 | alta 25
2969 | real: Mantener(azar_lo_explica) | sombra: Mover(bajo_target, 7.70)  | base 23.19201313987196647242583925 | alta 25
```

Sale idéntico a lo que guardó el ciclo, así que la causa de las dos diferencias es la meta de alta.

Lo proyectable con lo medido, con el mismo gasto y los mismos clics (CPC lineal, el supuesto del diseño):

| Hoja | Bid hoy | Destino sombra | Factor | Gasto de la ventana madura | Gasto extra |
| --- | --- | --- | --- | --- | --- |
| 2457 | 20.42 MXN | 23.48 | 1.149853 | 2,306.83 MXN | +345.69 MXN |
| 2969 | 6.70 MXN | 7.70 | 1.149254 | 153.80 MXN | +22.96 MXN |
| Total | | | | 2,460.63 MXN | **+368.64 MXN** |

La suma exacta es 368.64 MXN; los dos renglones redondeados por separado dan 368.65, la diferencia es
el redondeo a centavos.

## El hueco que encontró la revisión: la práctica no evaluaba R6r

`recupera_nivel`: **0, y no porque la evidencia no lo justificara**. En el código de entonces
(`app/cycle_recorrido.py:654`), los insumos de la subida rápida solo se construían con
`ads_bid_recuperacion_<plat>` en `live`; en `shadow` el caso no llevaba el marcador y el ciclo no
calculaba ningún candidato. Los dos casos congelados del ciclo 205 no traen `recuperacion` ni `historia`.
Consecuencia: **m\* máximo y gasto extra de R6r no eran medibles** con esa práctica.

Era una desviación de la decisión 12 del diseño («el caso que decide va sin marcadores y se decide un
segundo caso con marcadores»), y el dueño la señaló en la revisión del PR 730. Se corrigió en tres pasos:

1. **`49f8734`** — en práctica el caso que decide sigue sin marcadores y el caso de sombra lleva
   `recuperacion` y `presupuesto`; la diferencia guarda `destino_sombra`. Prueba:
   `test_temporada01_h_practica_compara_recuperacion` (falla contra `88a421e` con `assert 0 == 1` en las
   diferencias y pasa con el arreglo).
2. **`ff2c1e3`** — la memoria de la subida rápida en práctica (90 a 730 días) ya no acorta el historial
   que decide la temporada, para no perder la mano del dueño ya sincronizada. Prueba:
   `test_temporada01_h_practica_no_pierde_la_mano_del_dueno` (falla contra `49f8734`: la entrada recorta
   una puja de 7.00 a 4.90; con el arreglo no hay entrada y el bid sigue en 7.00).
3. **`2a8a6b0`** — la diferencia guarda también `caso_sombra` con sus marcadores, y la prueba rejuega ese
   caso con `decide` y exige el mismo veredicto y destino. Sin él, un `recupera_nivel` no es
   reproducible: los topes y el redondeo esconden el multiplicador de R6r.

Los tres commits están desplegados (`2a8a6b0`, checklist verde) y la temporada quedó otra vez en
`shadow` (config **61**) para que el ciclo del 2026-11-23 09:40 UTC sea una práctica de verdad.

## Lo que se va a medir en el ciclo del 2026-11-23 y cómo

Con la temporada y la subida rápida en `shadow` y el código ya desplegado, `notes.temporada` de ese
ciclo traerá, por cada diferencia, `motivo_sombra`, `destino_sombra` y `caso_sombra`. El resumen leerá:

- **`recupera_nivel`**: cuántas diferencias tienen `motivo_sombra = "recupera_nivel"`.
- **m\* máximo**: del `caso_sombra` de cada una, con la misma lógica de R6r (`politica._fondo_juicio`,
  `_proyeccion_subida` y `recuperacion.multiplicador_sostenible`), no del cociente entre pujas.
- **Gasto extra proyectado**: `gasto de la ventana × (destino / bid - 1)` para cada salto, con el CPC
  lineal del diseño.
- **Entradas, regresos, retenidos, metas de alta y reactivaciones**: con las mismas reglas de esta
  lectura, y diciendo en qué fase cayó el ciclo.

Si el ciclo vuelve a caer en una fase donde R6r no tenga candidatos, el documento lo dirá con el número
de comparaciones a la vista, y ese 0 no se leerá como un aprobado.

## Lo que la práctica no toca (y por eso no se puede dar por bueno)

En los ciclos 204 y 205: **0 decisiones**, **0 temporada**, **0 filas** en `temporada_episodio`,
`temporada_efecto`, `temporada_fin_reduccion` y `temporada_exclusion`, **0** `decision_application`,
**0** `decision_sin_aplicar` y **0** `campana_ajuste`. La práctica cumplió su contrato: anotó y no
escribió.

Quedan sin ejercitar, por diseño, las rutas que solo existen en vivo (diseño, «Riesgos y límites
honestos», punto 16): la lectura fresca antes del PUT, la exclusión por la mano del dueño, la
reactivación de pausas de la baja, la retención del regreso por presupuesto y el candado de las
propuestas. Lo vigila H.3 desde el primer ciclo en vivo.

## Casos de paro de H.3 en la práctica

Las cinco condiciones de `docs/evidencia/temporada-01/H/primer-ciclo.sql`, aplicadas a los ciclos 204 y
205:

| Caso de paro | Resultado en práctica |
| --- | --- |
| bid enviado que baja más que la reducción | 0 (0 entradas enviadas ni confirmadas) |
| regreso que baja un bid | 0 (0 regresos enviados ni confirmados) |
| PUT después de una lectura fresca distinta | 0 (`decision_sin_aplicar` con `bid_cambiado_fuera`: 0) |
| dos entradas para una keyword en un episodio | 0 (0 episodios, 0 entradas) |
| presupuesto escrito sin `APLICAR AJUSTE` | 0 (`campana_ajuste`: 0 filas) |

Ninguno aparece. Y conviene decirlo: **que los cinco den 0 en práctica no significa que los cinco estén
bien**; tres de ellos solo pueden fallar cuando hay escrituras, y la práctica no escribe.
