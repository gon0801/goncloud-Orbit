"""El Libro de juicios por par de Jev (JEV ADS 02, S.2 + S.4).

Unico que inserta eventos `intencion` y `resultado` (lo que cuesta
dinero) y unico que conoce el tope diario. `pagar` es el traslado textual
del asesor manual (sin reutilizar y sin tope); `juicio` es la pregunta del
job: reutiliza el primer exito GLOBAL, respeta el tope con reserva
atomica y jamas abre intencion sin cupo.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.jev_ads import (
    ClavePar,
    EstadoPar,
    FalloProveedor,
    FichaVersion,
    Juicio,
    _canonico,
    _juicio_de_respuesta,
)


@dataclass(frozen=True)
class SinCupo:
    """El tope diario no deja pagar este par hoy. No es fallo ni evidencia."""


class _CupoAgotado(Exception):
    """Sale del bloque de reserva sin confirmar nada."""


def exito_global(conn, clave: ClavePar) -> Juicio | None:
    """Primer exito de la clave en CUALQUIER revision, por (created_at, id).
    Las tablas son append-only, asi que ese primero no cambia nunca."""
    fila = conn.execute(
        "SELECT id, respuesta FROM jev_par_evento"
        " WHERE termino_sha256 = %s AND ficha_version_id = %s AND contrato_sha256 = %s"
        " AND tipo = 'resultado' AND respuesta IS NOT NULL"
        " ORDER BY created_at, id LIMIT 1",
        (clave.termino_literal_sha256, clave.ficha_version_id, clave.contrato_sha256),
    ).fetchone()
    if fila is None:
        return None
    return _juicio_de_respuesta(fila[1], clave, fila[0])


def intenciones_del_dia(conn, ahora: datetime) -> int:
    """Intenciones del dia UTC de `ahora`, por rango: usa el indice parcial
    de S.3 (una igualdad sobre `::date` no lo usaria)."""
    dia = ahora.date()
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=UTC)
    return conn.execute(
        "SELECT count(*) FROM jev_par_evento WHERE tipo = 'intencion'"
        " AND created_at >= %s AND created_at < %s",
        (inicio, inicio + timedelta(days=1)),
    ).fetchone()[0]


def _exceso_contexto(termino: str, ficha: FichaVersion, contrato) -> str | None:
    """Lo que no cabe en el contrato, con el mismo motivo que `pedir_juicio`
    (determinista: no abre intencion, no gasta cupo)."""
    from app.jev_juicios import _ficha_a_state

    bytes_termino = len(termino.encode("utf-8"))
    if bytes_termino > contrato.max_bytes_termino:
        return (
            f"contexto_excedido: termino de {bytes_termino} bytes supera el maximo"
            f" {contrato.max_bytes_termino}"
        )
    bytes_ficha = len(
        json.dumps(_ficha_a_state(ficha), ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    if bytes_ficha > contrato.max_bytes_ficha:
        return (
            f"contexto_excedido: ficha de {bytes_ficha} bytes supera el maximo"
            f" {contrato.max_bytes_ficha}"
        )
    return None


class Libro:
    """Paga juicios a TypeSafe y deja el rastro auditable. Recibe la misma
    conexion de quien lo construye; el `__init__` no toca la base."""

    def __init__(self, conn, *, pedir, contrato, ahora=None):
        self._conn = conn
        self._pedir = pedir
        self._contrato = contrato
        self._ahora = ahora or (lambda: datetime.now(UTC))

    def juicio(
        self, termino: str, ficha: FichaVersion, *, lote: UUID, tope_diario: int
    ) -> Juicio | FalloProveedor | SinCupo:
        """El juicio del par, GLOBAL por ClavePar (cambia R4): el primer
        exito de cualquier revision vale sin HTTP ni filas; lo que no cabe
        en el contrato falla sin intencion; la reserva (candado, conteo,
        intencion) es atomica y el HTTP va despues del commit."""
        from app.jev_juicios import clave_de, request_sha256

        clave = clave_de(termino, ficha, self._contrato)
        exito = exito_global(self._conn, clave)
        if exito is not None:
            return exito
        exceso = _exceso_contexto(termino, ficha, self._contrato)
        if exceso is not None:
            return FalloProveedor(producto_id=ficha.producto_id, motivo=exceso)
        try:
            with self._conn.transaction():
                self._conn.execute("SELECT pg_advisory_xact_lock(hashtext('jev:cupo'))")
                if self.llamadas_de_hoy() >= tope_diario:
                    raise _CupoAgotado
                intencion_id = self._intencion(
                    lote, clave, request_sha256(termino, ficha, self._contrato)
                )
        except _CupoAgotado:
            return SinCupo()
        devuelto = self._pedir(termino, ficha)
        return self._resultado(lote, clave, intencion_id, devuelto, ficha)

    def llamadas_de_hoy(self) -> int:
        """Gasto del dia: intenciones de hoy UTC, de cualquier origen."""
        return intenciones_del_dia(self._conn, self._ahora())

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
