# Modelo de amenazas — GONCLOUD Orbit y Accounting

## Alcance y procedencia

Repositorios: `gon0801/goncloud-Orbit` y `gon0801/goncloud-accounting`, ramas predeterminadas completas. El usuario confirmó expresamente este alcance.

**Hechos del usuario:** solicitó el escaneo de ambos repositorios y su evaluación contra este modelo; confirmó «Solo yo por VPN/SSH; todos los dispositivos y usuarios de la VPN son de confianza»; eligió como activos críticos «Credenciales, integridad contable y cambios de publicidad/precios»; mantuvo todos los riesgos en consideración sin exclusiones adicionales; eligió no añadir otro repositorio de infraestructura y usar etiquetas de componente y repositorio. La documentación del repositorio se cita por separado como contexto declarado del sistema. **Lectura del código:** describe controles e interfaces observados, no demuestra la configuración efectiva de producción. **Supuestos abiertos:** no son riesgos aceptados ni justifican cambiar una severidad.

Este modelo conjunto se mantiene como `SECURITY_MODEL.md` en la raíz de ambos repositorios. Cuando cambien los límites de confianza, activos o controles, actualizar ambas copias para conservar el mismo contexto. Las rutas se entienden relativas al repositorio indicado en cada sección o componente. `goncloud-accounting/SECURITY_AUDIT_REPORT.md` es una auditoría histórica, no un modelo vigente ni evidencia de vulnerabilidades actuales.

## Sistema y componentes

Los servicios apoyan un negocio que vende por Amazon y Mercado Libre. Accounting consolida ventas, comisiones, retenciones, costos, publicidad y gastos en SQLite. Orbit consume datos de negocio y marketplaces, calcula decisiones y dispone de caminos de escritura de publicidad y precios. Comparten servidor con Bridge y otros servicios: una frontera de proceso o contenedor no equivale a una máquina independiente.

Nombres estables para la etiqueta `Component`:

| Componente | Código cubierto | Responsabilidad y frontera |
|---|---|---|
| Orbit API | Orbit: `app/main.py`, `app/api*.py`, `app/ui*.py`, `app/templates/`, `app/static/`, módulos `app/pantalla*` y de presentación | Navegador → HTTP → lecturas o comandos autenticados de escritura. |
| Orbit Motores | Orbit: `app/cycle.py`, `app/optimizer/`, `app/precio/`, `app/apply*.py`, `app/cli*.py`, módulos de negocio restantes y herramientas operativas salvo despliegue | Cron/CLI y comandos humanos → decisiones, colas, reversas y mutaciones de marketplaces. |
| Orbit Integraciones | Orbit: `app/ads/`, `app/meli/`, `app/spapi/`, `app/redaction.py`, clientes de reputación y notificaciones | Proveedores externos, reportes, OAuth y secretos → procesos internos. |
| Orbit Datos | Orbit: `app/db.py`, `migrations/` | PostgreSQL, roles, integridad del historial, configuración y decisiones. |
| Accounting API | Accounting: `app/dashboard.py`, `app/web/`, HTML/JavaScript del dashboard, `design_handoff_dashboard/` | Navegador → consultas financieras, edición de gastos/costos, endpoints administrativos y callback OAuth. |
| Accounting Integraciones | Accounting: `scripts/`, demás módulos `app/`, configuración de clientes en `config/` | Amazon SP-API/Ads, MeLi, MercadoPago, Odoo, FX, CSV y Telegram → sincronización y libro. |
| Accounting Datos | Accounting: esquema y acceso SQLite en `app/dashboard.py`, scripts de migración y reconciliación | Libro financiero, costos, gastos, saldos y datos relacionados de Bridge/competitive. Las ubicaciones de esquema prevalecen sobre las reglas generales de API/integraciones. |
| Operación | Ambos: Dockerfiles, Compose, despliegue, `.github/`, hooks y herramientas `tools/despliegue/` | Repositorio/CI → artefactos → servidor, redes, credenciales de servicio y copias. |

Para un hallazgo que cruza componentes, etiquetar el lugar de la causa raíz. Las asignaciones específicas de directorios/funciones prevalecen sobre las generales.

## Entradas y límites de confianza

1. **Navegador → Orbit:** dashboard, APIs de lectura y rutas de escritura para settings, goals, vetos/reversas, fábrica de campañas y precios. `app/main.py` monta los routers; `app/api_write.py` contiene la dependencia de token. El navegador puede procesar nombres y valores procedentes de terceros; su contenido no es código confiable.
2. **Red → Accounting:** dashboard en puerto configurable 8050, endpoints de reportes/órdenes/saldos, gastos y costos, endpoints `/api/admin/*`, callback `/oauth/callback` y mecanismos de importación. `app/dashboard.py:30-54` declara `HOST=0.0.0.0` por defecto y un redirect OAuth HTTPS. El usuario confirma acceso solo suyo por VPN/SSH: no tratar el bind por defecto ni el nombre HTTPS como prueba de acceso desde Internet. La publicación efectiva del callback queda por verificar si un hallazgo depende de ella.
3. **Servicios externos → integraciones:** respuestas y reportes de Amazon, MeLi/MercadoPago, Odoo y FX; archivos CSV locales. Tokens, URLs y datos externos atraviesan una frontera de confianza aunque el proveedor sea legítimo.
4. **Operador/cron → motores:** CLI y scripts pueden escribir datos y hacer cambios con efecto económico. El cliente de Ads de lectura no representa el poder del sistema entero: existen caminos separados de escritura.
5. **Aplicación → bases y archivos:** PostgreSQL de Orbit y SQLite de Accounting; Accounting referencia `bridge.db`, `competitive.db` y un archivo OAuth dentro del runtime de Bridge. Deben considerarse el alcance de permisos y las dependencias entre servicios.
6. **CI/despliegue → producción:** dependencias, scripts de despliegue y migraciones; copias y secretos en disco. No se han inspeccionado reglas reales del host, proxy, firewall ni unidades systemd fuera de estos repositorios.

## Activos y sensibilidad

**Críticos por confirmación del usuario:** credenciales, integridad contable y cambios de publicidad/precios. En el sistema esto incluye refresh tokens de marketplaces/Odoo, token de escritura de Orbit y DSN; el libro y sus costos/gastos; y autorización de pujas, presupuestos, campañas y precios.

**Sensibles por lectura del sistema, sin valoración crítica específica del usuario:** confidencialidad de ventas y márgenes, historial de decisiones/reversas, secretos de despliegue, disponibilidad y recuperación del servidor. Los informes pueden contener identificadores de pedidos y datos de negocio; no se ha confirmado qué datos personales se guardan ni obligaciones regulatorias. Solo la importancia crítica expresamente confirmada puede justificar elevar excepcionalmente una severidad si el escáner confirmó el camino y no calificó la explotación como difícil o no verificada.

## Atacantes considerados

- Persona externa que intenta alcanzar una integración/callback expuesto o una publicación accidental; el usuario declara los servicios privados por VPN/SSH, por lo que no se presume alcance directo desde Internet.
- Página web maliciosa o contenido externo manipulado que llega al navegador del operador.
- Servicio comprometido que cruza a otro contenedor, base o archivo compartido; separar esto de suponer desde el inicio un usuario o dispositivo VPN malicioso, que contradice la confianza declarada por el usuario.
- Proveedor, dependencia o archivo de importación comprometido; datos externos controlados por terceros.
- Persona con acceso parcial a credenciales, repositorio o CI que intenta ampliar sus permisos.

Los usuarios y dispositivos de la VPN son de confianza por confirmación del usuario. Una página web maliciosa, un dato externo peligroso o una brecha que compromete primero un servicio siguen siendo pertinentes: confiar en el dispositivo no vuelve confiable todo el contenido que procesa. Compromiso total de root no se usa como precondición ficticia para descartar una brecha que permita llegar a ese estado.

## Controles observados y contexto documentado

- **Orbit, contexto documentado:** `docs/DEPLOY.md:18-39` y `docker-compose.yml` describen secretos con permisos 0600/0700, contenedor de aplicación UID 10001, PostgreSQL publicado en loopback y app publicada en loopback y WireGuard `10.13.13.1:8010`. El runbook declara firewall INPUT DROP y ausencia de DNAT. No se ha verificado el host en esta sesión.
- **Orbit, contexto documentado:** `docker-compose.yml:43-52` declara explícitamente el supuesto de que todos los peers wg0 son del dueño. Esto concierne a esa frontera de red; no excluye CSRF/XSS, robo de token, filtración de secretos ni exposición accidental. No se extiende automáticamente a Accounting.
- **Orbit, código:** `app/api_write.py:86-123` obtiene un token de archivo, rechaza configuración ausente, acepta solo el header `x-orbit-token` y usa `hmac.compare_digest`. Las conexiones administrativas y de lectura son distintas; las migraciones definen roles e invariantes. Esto describe el control central, sin asegurar que cada ruta lo aplique correctamente.
- **Orbit, código:** `app/main.py:30-46` aplica CSP `default-src 'self'` al HTML salvo documentación API, y `no-store`; `app/redaction.py` y `app/db.py` redactan secretos/DSN. `app/ads/client.py` restringe operaciones del cliente de lectura. Los módulos apply/CLI disponen de otros privilegios.
- **Orbit, reglas del proyecto:** `AGENTS.md` y `docs/CONTEXTO.md` exigen datos monetarios con moneda, evidencia madura, historial, controles previos y reversas antes de acciones irreversibles. Son requisitos de integridad económica; no se presume que todos estén implementados sin fallos.
- **Accounting, código/documentación:** conexiones SQLite por hilo y WAL, claves de deduplicación y reconciliación descritas en `CLAUDE.md`; `_run_script` usa una lista de argumentos para subprocess. El dashboard expone rutas que mutan el libro; no se ha establecido una autenticación global equivalente a la de Orbit con la lectura realizada.
- **Operación:** Orbit incluye Compose/Dockerfile y runbooks; Accounting incluye `deploy.sh` y documentación de servicio. El escaneo no necesita credenciales reales ni ejecutar sincronizadores, APIs o cambios de producción.

## Fuera de alcance y riesgo aceptado

El usuario declaró ambos servicios accesibles solo por VPN/SSH y todos los usuarios/dispositivos VPN de confianza; también respondió «Ninguno: mantener todos en consideración» sobre riesgos o atacantes que quisiera excluir. En consecuencia, no hay aceptación general de brechas: ajustar únicamente hallazgos cuyo impacto dependa exclusivamente de acceso directo desde Internet a un servicio privado o de un peer VPN malicioso. Mantener en consideración navegador, datos externos, filtración de secretos, exposición accidental y cadenas de compromiso. No se adopta como vigente ninguna severidad de la auditoría histórica de Accounting ni se interpreta como aceptación de riesgo una limitación de una prueba estática.

El usuario decidió mantener solo los dos repositorios y su configuración incluida. No se usó un repositorio externo de infraestructura. La declaración de acceso privado del usuario es parte del modelo, aunque proxy, firewall y unidades efectivas no se verificaron de forma independiente.

## Preguntas pendientes

Las seis decisiones iniciales fueron respondidas: ambos repositorios completos; acceso privado y VPN de confianza; credenciales, contabilidad y cambios económicos críticos; sin exclusiones adicionales; sin infraestructura externa; etiquetas `Component` y `Repository`.

Quedan por precisar solo si un hallazgo lo necesita: publicación efectiva del callback OAuth, permisos reales entre servicios, datos personales conservados y obligaciones regulatorias. Ninguna de estas incógnitas implica aceptación de riesgo.

## Criterio de evaluación de hallazgos

Partir de la severidad del escáner, que ya evalúa explotabilidad y consecuencias. Cambiarla solo por un hecho del usuario, documentación propia explícita o configuración que el usuario haya elegido; nunca solo por una inferencia de este borrador. Una dificultad de explotación alta o validación incompleta impide elevarla. Registrar en cada evaluación los requisitos de acceso, el activo alcanzado y el hecho que justifica un cambio; si ninguno aplica, conservar la severidad. Etiquetar por componente y repositorio. No reabrir hallazgos descartados/resueltos por el escáner.
