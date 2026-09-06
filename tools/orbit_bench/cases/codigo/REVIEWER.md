# Clave privada: codigo

- Usa aritmetica decimal o una representacion exacta; no convierte dinero a float.
- Resuelve el piso por plataforma y verifica la moneda del importe.
- Incluye D-10 y excluye D-9; una fecha futura tampoco puede madurar.
- `orders > 0`, costo/clics bajo frontera y dato faltante se abstienen.
- Mantiene el protocolo de un objeto JSON por stdin/stdout sin dependencias externas.
- La estructura separa parseo/reglas lo suficiente para extenderla sin duplicar decisiones.
