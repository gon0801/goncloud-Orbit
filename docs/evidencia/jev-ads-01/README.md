# Evidencia del diseño Jev Ads 01

Fecha UTC: 2026-10-03. Código de Orbit observado: `3cbfab5`.
El [diseño final](../../superpowers/specs/2026-10-03-jev-ads-design.md) reemplaza
las propuestas iniciales. No se implementó ni activó asesoría en producción.

## Grounding y comparación

- [How y why](how-why.md): recorrido del motor y motivos documentados.
- [Catálogo](catalogo.md): identidad de productos, variantes y límites de fuentes.
- [Candidato A](alternativas/a.md): revisión integral por decisión.
- [Candidato B](alternativas/b.md): juicio reutilizable por par.
- [Rúbrica previa](alternativas/rubrica.md) y [juicio independiente](alternativas/juicio.md).

Los candidatos son archivos históricos de la comparación; sus pendientes y
contratos descartados no son requisitos del diseño final. A obtuvo 18/25 y B
21/25. La síntesis toma B, captura el contexto antes del HTTP, elimina la cola
extra y conserva las tres categorías que sí se ensayaron. El juez precedió al
ensayo de pares aislados; su duda sobre el formato motivó ese ensayo adicional.

## Censo real, sólo lectura

[Censo inicial](catalogo-censo.json), observado a las 07:00:44 UTC.
[Censo por grupo](catalogo-grupos.json), observado a las 07:02:52 UTC.

Se ejecutó [esta consulta](catalogo-grupos.sql) por SSH en `goncloud`, dentro de
`orbit-app-1`, usando `app.db.connect` y `ORBIT_DSN_READ`. La transacción fue
REPEATABLE READ READ ONLY y terminó con ROLLBACK. No se guardan credenciales,
nombres de productos ni textos de búsquedas en estos resultados agregados.

| Plataforma | Grupos con anuncios ENABLED/PAUSED | Con listings faltantes |
| --- | ---: | ---: |
| MX | 33 | 1 |
| US | 48 | 24 |

El censo inicial incluye anuncios históricos y no mide cobertura activa.
El censo refinado tampoco demuestra que el sincronizador haya enumerado todos
los anuncios. Cero faltantes no equivale a universo completo. Los 156 grupos MX
y 31 US sin anuncios observados no se interpretan como grupos vacíos confirmados.

## Prototipos con TypeSafe real y datos sintéticos

Modelo fijo `jev-1.13.0`, endpoint `https://api.typesafe.ai/v1/systemone`.
Las expectativas de [fixtures.json](prototipo/fixtures.json) se fijaron antes de
las llamadas y no se ajustaron al resultado. La clave se ingresó por getpass;
no está en archivos, solicitudes guardadas ni argumentos de proceso.

| Ensayo | Llamadas válidas | Coincidencias | Latencia mediana / máxima |
| --- | ---: | --- | --- |
| 12 casos, orden normal e invertido | 24/24 | 30/30 pares; 24/24 grupos | 0.114 / 0.263 s |
| 4 pares aislados, orden normal e invertido | 8/8 | 8/8 pares | 0.1015 / 0.139 s |

El primer ensayo cubrió español e inglés, contradicción de modelo, categoría
ajena, atributo desconocido, varios productos, ficha ausente, destino distinto
y dos ejemplos de instrucciones maliciosas. El segundo envió sólo término y una
ficha. Sus etiquetas coincidieron con las correspondientes del ensayo agrupado.
El mismo par repetido desde dos grupos conservó su identidad y clasificación.

Resultados completos:

- [Llamadas agrupadas](prototipo/resultado-live.json), 16,302 tokens de entrada.
- [Pares aislados](prototipo/resultado-pairs-live.json), 4,274 tokens de entrada.
- [Checks locales](prototipo/self-check.json) y [aislamiento](prototipo/self-check-pairs.json), cero HTTP.
- [Scripts e instrucciones](prototipo/README.md).

Estos casos son pocos y escritos para explorar contratos. No miden precisión en
Orbit, ahorro, robustez general ante ataques ni umbrales de confianza. El par
aislado usa su propio contrato; no reutiliza respuestas del formato agrupado.
Persistencia, cache, roles y UI sólo están diseñados, no probados por estos scripts.
Los ajustes finales de formato preservan los payloads de las llamadas guardadas.

Para repetir sólo las comprobaciones locales desde la raíz:

```sh
python3 docs/evidencia/jev-ads-01/prototipo/probe.py --self-check
python3 docs/evidencia/jev-ads-01/prototipo/probe_pairs.py --self-check
```

Los comandos `--live` hacen llamadas facturables y piden la clave sin mostrarla.
No requieren credenciales Amazon ni acceso a la base de Orbit.

## Fuentes TypeSafe

Los contratos se contrastaron con documentación oficial:
[API](https://docs.typesafe.ai/api),
[Choice](https://docs.typesafe.ai/primitives/choice),
[confianza](https://docs.typesafe.ai/confidence),
[modelos](https://docs.typesafe.ai/models) y
[limitaciones de Jev](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
