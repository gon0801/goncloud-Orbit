# Rubrica de comparacion (0-5 por criterio)

1. Fidelidad de datos: censo LEFT JOIN completo, catalogo sin atributos y estados/frescura, no lookahead ni probabilidades globales inventadas.
2. Limites de autoridad: asesor v1, cero mutacion Ads desde Jev/GET, no gate motor ni negativa de ruteo en biblioteca, cola48h explicitada.
3. Profundidad/simplicidad: caller usage legible, 3 modulos max, sin framework generico, contratos dominio no HTTP en callers, esquema minimo necesario.
4. Reproducibilidad e invalidacion: versiones modelo/pregunta/ficha/censo, dos tiempos, append-only, cache honesta, resultados tardios no vigentes silenciosamente.
5. Implementabilidad/verificacion: SQL/tipos/firmas coherentes, errores y permisos, pruebas discriminantes, 3 usos Ads conectados con fuente y UI real.

Comparar estructuras completas: A por decision/contexto, B por pares reutilizables. Ninguno gana por menor cantidad de lineas si omite contratos. Señalar bloqueante solo con contraejemplo reproducible. Elegir base y injertos concretos. No implementar producto.
