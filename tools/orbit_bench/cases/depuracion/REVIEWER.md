# Clave privada: depuracion

Causa: `decidir` ignora `mode` y `frozen_policy`, por lo que una fila historica
cambia de resultado cuando cambian los umbrales vigentes.

La correccion debe:

- conservar la rama `current`;
- leer exclusivamente los tres valores congelados en `replay`;
- abstenerse con `politica_historica_ausente` cuando falta el marcador;
- validar la moneda congelada y usar Decimal;
- evitar defaults historicos inventados o una seleccion parcial de politica.
