"""El Libro de juicios por par de Jev (JEV ADS 02, S.2).

Unico que inserta eventos `intencion` y `resultado` (lo que cuesta
dinero). En este paso trae `pagar`, traslado textual del asesor manual:
intencion confirmada ANTES del HTTP, resultado despues, sin reutilizar y
sin tope; y `siguiente_ordinal`, que `_reutilizar` sigue necesitando.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from app.jev_ads import (
    ClavePar,
    EstadoPar,
    FalloProveedor,
    FichaVersion,
    Juicio,
    _canonico,
)


class Libro:
    """Paga juicios a TypeSafe y deja el rastro auditable. Recibe la misma
    conexion de quien lo construye; el `__init__` no toca la base."""

    def __init__(self, conn, *, pedir, contrato):
        self._conn = conn
        self._pedir = pedir
        self._contrato = contrato

    def pagar(
        self, termino: str, ficha: FichaVersion, *, revision: UUID
    ) -> Juicio | FalloProveedor:
        """Intencion -> HTTP -> resultado bajo `revision`, sin reutilizar y
        sin tope. Sin clave el pedir devuelve fallo y tambien quedan
        intencion y resultado con error, como en el CLI manual."""
        from app.jev_juicios import clave_de, request_sha256

        clave = clave_de(termino, ficha, self._contrato)
        intencion_id = self._intencion(
            revision, clave, request_sha256(termino, ficha, self._contrato)
        )
        self._conn.commit()
        devuelto = self._pedir(termino, ficha)
        return self._resultado(revision, clave, intencion_id, devuelto, ficha)

    def siguiente_ordinal(self, revision: UUID, clave: ClavePar, tipo: str) -> int:
        return self._conn.execute(
            "SELECT COALESCE(max(ordinal), 0) + 1 FROM jev_par_evento"
            " WHERE revision_id = %s AND termino_sha256 = %s"
            " AND ficha_version_id = %s AND contrato_sha256 = %s AND tipo = %s",
            (
                revision,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                tipo,
            ),
        ).fetchone()[0]

    def _intencion(self, revision: UUID, clave: ClavePar, request_hash: str) -> UUID:
        intencion_id = uuid.uuid4()
        self._conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
            " ficha_version_id, contrato_sha256, ordinal, tipo, request_sha256)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'intencion', %s)",
            (
                intencion_id,
                revision,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                self.siguiente_ordinal(revision, clave, "intencion"),
                request_hash,
            ),
        )
        return intencion_id

    def _resultado(
        self, revision: UUID, clave: ClavePar, intencion_id: UUID, devuelto, ficha: FichaVersion
    ) -> EstadoPar:
        """Resultado (exito o fallo) tras el HTTP, y su EstadoPar."""
        ordinal = self.siguiente_ordinal(revision, clave, "resultado")
        if hasattr(devuelto, "juicio"):
            juicio_proveedor = devuelto.juicio
            evento_id = uuid.uuid4()
            respuesta = {
                "confidence": str(juicio_proveedor.confidence),
                "observado_at": juicio_proveedor.observado_at.isoformat(),
                "probabilidades": {k: str(v) for k, v in juicio_proveedor.probabilidades.items()},
                "relacion": juicio_proveedor.relacion,
            }
            self._conn.execute(
                "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
                " ficha_version_id, contrato_sha256, ordinal, tipo, respuesta,"
                " intencion_id, duracion_ms, usage)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'resultado', %s::jsonb, %s, %s,"
                " %s::jsonb)",
                (
                    evento_id,
                    revision,
                    clave.termino_literal_sha256,
                    clave.ficha_version_id,
                    clave.contrato_sha256,
                    ordinal,
                    _canonico(respuesta),
                    intencion_id,
                    devuelto.duracion_ms,
                    _canonico(devuelto.usage) if devuelto.usage else None,
                ),
            )
            self._conn.commit()
            return Juicio(
                intento_id=evento_id,
                clave=clave,
                relacion=juicio_proveedor.relacion,
                probabilidades=juicio_proveedor.probabilidades,
                confidence=juicio_proveedor.confidence,
                observado_at=juicio_proveedor.observado_at,
            )
        self._conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
            " ficha_version_id, contrato_sha256, ordinal, tipo, error,"
            " intencion_id, duracion_ms, usage)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'resultado', %s, %s, %s, %s::jsonb)",
            (
                uuid.uuid4(),
                revision,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                ordinal,
                f"{devuelto.codigo}: {devuelto.detalle}",
                intencion_id,
                devuelto.duracion_ms,
                _canonico(devuelto.usage) if devuelto.usage else None,
            ),
        )
        self._conn.commit()
        return FalloProveedor(
            producto_id=ficha.producto_id,
            motivo=f"{devuelto.codigo}: {devuelto.detalle}",
        )
