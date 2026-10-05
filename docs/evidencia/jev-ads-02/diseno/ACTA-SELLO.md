# Sello del dueño al diseño de JEV ADS 02

**Fecha:** 2026-10-04, 20:33 (hora del Pacífico). **Sella:** David (dueño).
**Versión sellada:** commit `741fa86` del spec; el sello quedó anotado en `f81caac`.

## Qué se le presentó

El lead le explicó el diseño en llano y le pidió su visto bueno a cuatro cosas:

1. Que Jev corra sola dos veces al día, con un tope diario de preguntas.
2. Que una respuesta ya pagada se reutilice entre grupos y entre días, en vez
   de pagarla otra vez.
3. Que Jev pueda decir "no corresponde a ninguno" cuando Orbit pueda probar
   que conoce todos los productos del grupo.
4. Tope inicial de 5,000 preguntas al día y revisar búsquedas desde 3 clics.

## Qué contestó

"va, a las cuatro cosas".

Después pidió: "no implementes solo deja el diseño al 100". Nada se construyó.

## Qué no cubre el sello

- Encender cualquier interruptor `jev.*` en producción. Cada uno lleva su go.
- Los criterios de avance entre pasos del plan (porcentajes y tamaños de
  muestra). Son propuestas del autor, sin sellar.
- Las correcciones que entraron al spec después de las 20:33. Están listadas
  en el spec, sección "Correcciones posteriores al sello". No cambian las
  cuatro cosas de arriba, pero el dueño no las vio una por una.
- Cualquier efecto sobre Ads (bloque E del plan).

## De dónde salió la petición

El acta 3.2 de JEV ADS 01 decidió "seguir y ampliar". El mismo día el dueño
pidió cinco ampliaciones. La petición y el sello ocurrieron en el chat con el
lead; este archivo es su único registro en el repo.
