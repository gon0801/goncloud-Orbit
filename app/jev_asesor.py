"""El asesor Jev para Ads (JEV ADS 01, 1.4 y 2.1): la UNICA capa con IO.

Une catalogo (`app/jev_catalogo.py`), juicios (`app/jev_juicios.py`) y la
revision persistida; los tipos y `componer` son del nucleo puro
(`app/jev_ads.py`) y la vista de lectura de `app/jev_vista.py` (R14).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.jev_ads import (
    CensoCongelado,
    ClavePar,
    DecisionARevisar,
    EstadoPar,
    FalloProveedor,
    FichaFaltante,
    FichaVersion,
    Juicio,
    NoAplicaTexto,
    NoComprobable,
    Obsoleta,
    PlanSeco,
    RelevanciaConjunto,
    Revision,
    Sujeto,
    Vigencia,
    Vigente,
    _canonico,
    _censo_a_json,
    _censo_de_json,
    _identidad_del_censo,
    _juicio_de_respuesta,
    _mismo_origen,
    _solo_no_activo,
    componer,
    es_asin_like,
)
from app.jev_vista import (
    _COLUMNAS_REVISION,
    ReferenciaPlan,
    VistaAsesoria,
    _evento_del_par,
    _fila_dict,
)


class AsesorAds:
    """Une catalogo, juicios y revision (1.4).

    Orden sellado: revision e intencion confirmadas ANTES del HTTP,
    resultado despues; retomar con la misma solicitud reutiliza exitos de
    ESTA revision (cero HTTP) y jamas fallos ni exitos ajenos; otro payload
    con la misma solicitud -> ValueError; sin clave, cero llamadas con
    fallos visibles; ASIN-like fuera del clasificador. `pedir` inyectable.
    """

    def __init__(
        self,
        conn,
        *,
        pedir=None,
        transporte=None,
        api_key: str = "",
        presupuesto: int = 10,
        contrato=None,
        ahora=None,
    ):
        self._conn = conn
        self._presupuesto = presupuesto
        if contrato is None:
            from app.jev_juicios import contrato_por_defecto

            contrato = contrato_por_defecto()
        self._contrato = contrato
        self._api_key = api_key
        self._ahora = ahora or (lambda: datetime.now(UTC))
        if pedir is None:
            from app.jev_juicios import pedir_juicio as pedir_real
            from app.jev_juicios import transporte_httpx

            def pedir(termino: str, ficha: FichaVersion):
                return pedir_real(
                    termino,
                    ficha,
                    self._contrato,
                    transporte=transporte or transporte_httpx,
                    api_key=self._api_key,
                )

        self._pedir = pedir

    def evaluar(self, sujeto: Sujeto, *, solicitud_id: UUID) -> Revision:
        """Evalua los terminos del sujeto contra el censo congelado y deja
        revision + eventos auditables (retomable por solicitud_id)."""
        from app.jev_juicios import clave_de, request_sha256

        destino_crudo = sujeto.destino_censo if isinstance(sujeto, DecisionARevisar) else None
        guardada = self._conn.execute(
            "SELECT censos, captured_at FROM jev_revision WHERE solicitud = %s", (solicitud_id,)
        ).fetchone()
        if guardada is None:
            ahora = self._ahora()
            censo, fichas = self._enriquecer(sujeto.censo, sujeto.plataforma, ahora)
            destino = None
            if destino_crudo is not None:
                destino, fichas_destino = self._enriquecer(destino_crudo, sujeto.plataforma, ahora)
                fichas = {**fichas, **fichas_destino}
        else:
            censo, destino, fichas, ahora = self._retomar(
                guardada, sujeto.censo, destino_crudo, sujeto.plataforma
            )
        terminos = (sujeto.termino,) if isinstance(sujeto, DecisionARevisar) else sujeto.terminos
        contexto = {
            "censo": _censo_a_json(censo),
            "plataforma": sujeto.plataforma,
            "terminos": list(terminos),
        }
        if destino is not None:
            contexto["destino"] = _censo_a_json(destino)
        self._abrir_revision(sujeto, solicitud_id, contexto, censo, destino, ahora)
        resultados = []
        destinos: list[tuple[str, RelevanciaConjunto]] = []
        http_hechos = 0
        agotado = False

        def pares_de(un_censo: CensoCongelado, termino: str) -> list[EstadoPar]:
            nonlocal http_hechos, agotado
            pares: list[EstadoPar] = []
            for miembro in un_censo.miembros:
                if _solo_no_activo(miembro.estados):
                    continue  # no anunciado (ARCHIVED): no paga intencion ni HTTP
                ficha = fichas.get(miembro.ficha_version_id) if miembro.ficha_version_id else None
                if ficha is None:
                    pares.append(FichaFaltante(producto_id=miembro.producto_id))
                    continue
                if agotado:
                    continue
                clave = clave_de(termino, ficha, self._contrato)
                exito = self._exito_previo(solicitud_id, clave)
                if exito is not None:
                    self._reutilizar(solicitud_id, clave, exito)
                    pares.append(_juicio_de_respuesta(exito[1], clave, exito[0]))
                    continue
                if http_hechos >= self._presupuesto:
                    agotado = True
                    continue
                intencion_id = self._intencion(
                    solicitud_id, clave, request_sha256(termino, ficha, self._contrato)
                )
                self._conn.commit()
                http_hechos += 1
                devuelto = self._pedir(termino, ficha)
                pares.append(self._resultado(solicitud_id, clave, intencion_id, devuelto, miembro))
            return pares

        for termino in terminos:
            if es_asin_like(termino):
                sin_texto = (NoAplicaTexto(motivo="asin_like"),)
                resultados.append((termino, componer(censo, sin_texto)))
                if destino is not None:
                    destinos.append((termino, componer(destino, sin_texto)))
                continue
            resultados.append((termino, componer(censo, tuple(pares_de(censo, termino)))))
            if destino is not None:
                destinos.append((termino, componer(destino, tuple(pares_de(destino, termino)))))
        return Revision(solicitud_id, tuple(resultados), agotado, tuple(destinos))

    # internos (confirmaciones = los tres puntos del orden sellado)

    def _retomar(self, guardada, censo_crudo, destino_crudo, plataforma):
        """Reanudacion: el censo y el captured_at CONGELADOS por la revision
        (R9). Una ficha revocada, vencida o sustituida entre intentos no cambia
        el payload (la vigencia marcara Obsoleta), pero su par ya no se
        reutiliza ni se consulta: el spec reutiliza "solo si la ficha sigue
        aprobada, cubre ese listing y no vencio". Queda como ficha faltante.
        Un censo crudo distinto si es otro payload."""
        from app.jev_catalogo import ficha_vigente, fichas_por_id

        contexto, capturado = guardada
        censo = _censo_de_json(contexto["censo"])
        destino = _censo_de_json(contexto["destino"]) if "destino" in contexto else None
        mismo_destino = (destino is None and destino_crudo is None) or (
            destino is not None
            and destino_crudo is not None
            and _mismo_origen(destino, destino_crudo)
        )
        if not _mismo_origen(censo, censo_crudo) or not mismo_destino:
            raise ValueError("misma solicitud con otro payload")
        miembros = (*censo.miembros, *(destino.miembros if destino is not None else ()))
        fichas = fichas_por_id(
            self._conn, (m.ficha_version_id for m in miembros if m.ficha_version_id)
        )
        hoy = self._ahora()

        def sigue_vigente(miembro) -> bool:
            for listing in miembro.listing_ids:
                actual = ficha_vigente(
                    self._conn,
                    producto_id=miembro.producto_id,
                    plataforma=plataforma,
                    listing_id=listing,
                    ahora=hoy,
                )
                if actual is None or actual.id != miembro.ficha_version_id:
                    return False
            return True

        vencidas = {
            m.ficha_version_id for m in miembros if m.ficha_version_id and not sigue_vigente(m)
        }
        vigentes = {ficha_id: f for ficha_id, f in fichas.items() if ficha_id not in vencidas}
        return censo, destino, vigentes, capturado

    def plan_seco(self, sujeto: Sujeto, *, solicitud_id: UUID) -> PlanSeco:
        """Lo que `evaluar` haria con esta solicitud, SIN escribir ni llamar
        (R2, dry-run fiel): mismo censo y fichas (congelados si la revision ya
        existe; otro payload -> ValueError igual que al aplicar), mismos
        miembros fuera (ARCHIVED, sin ficha, ASIN-like) y exitos de la
        revision reutilizados; los pares nuevos se cuentan por clave unica y
        se cortan con el presupuesto."""
        from app.jev_juicios import clave_de

        destino_crudo = sujeto.destino_censo if isinstance(sujeto, DecisionARevisar) else None
        guardada = self._conn.execute(
            "SELECT censos, captured_at FROM jev_revision WHERE solicitud = %s", (solicitud_id,)
        ).fetchone()
        if guardada is None:
            ahora = self._ahora()
            censo, fichas = self._enriquecer(sujeto.censo, sujeto.plataforma, ahora)
            destino = None
            if destino_crudo is not None:
                destino, fichas_destino = self._enriquecer(destino_crudo, sujeto.plataforma, ahora)
                fichas = {**fichas, **fichas_destino}
        else:
            censo, destino, fichas, _ = self._retomar(
                guardada, sujeto.censo, destino_crudo, sujeto.plataforma
            )
        terminos = (sujeto.termino,) if isinstance(sujeto, DecisionARevisar) else sujeto.terminos
        nuevas: set = set()
        reutilizables: set = set()
        for termino in terminos:
            if es_asin_like(termino):
                continue
            for un_censo in (censo, *((destino,) if destino is not None else ())):
                for miembro in un_censo.miembros:
                    ficha = fichas.get(miembro.ficha_version_id)
                    if ficha is None or _solo_no_activo(miembro.estados):
                        continue
                    clave = clave_de(termino, ficha, self._contrato)
                    if clave in reutilizables or clave in nuevas:
                        continue
                    if guardada is not None and self._exito_previo(solicitud_id, clave):
                        reutilizables.add(clave)
                    else:
                        nuevas.add(clave)
        miembros = (*censo.miembros, *(destino.miembros if destino is not None else ()))
        return PlanSeco(
            miembros=len(miembros),
            fichas=len(fichas),
            pares_nuevos=len(nuevas),
            pares_reutilizables=len(reutilizables),
            pagaria=min(len(nuevas), self._presupuesto),
            agotaria=len(nuevas) > self._presupuesto,
        )

    def _enriquecer(self, censo: CensoCongelado, plataforma, ahora):
        """Ficha vigente por miembro, congelada en el censo. Un producto con
        varios listings se acredita solo si la MISMA ficha vigente cubre todos
        (R8); una ficha que cubre una parte deja al miembro sin ficha."""
        from app.jev_catalogo import ficha_vigente

        fichas: dict[UUID, FichaVersion] = {}
        miembros = []
        for miembro in censo.miembros:
            ficha = None
            if miembro.producto_id is not None and miembro.listing_ids:
                por_listing = [
                    ficha_vigente(
                        self._conn,
                        producto_id=miembro.producto_id,
                        plataforma=plataforma,
                        listing_id=listing_id,
                        ahora=ahora,
                    )
                    for listing_id in sorted(miembro.listing_ids)
                ]
                ids = {f.id if f is not None else None for f in por_listing}
                if len(ids) == 1 and None not in ids:
                    ficha = por_listing[0]
            if ficha is not None:
                fichas[ficha.id] = ficha
                miembro = replace(miembro, ficha_version_id=ficha.id)
            miembros.append(miembro)
        return CensoCongelado(miembros=tuple(miembros), exhaustivo=censo.exhaustivo), fichas

    def _abrir_revision(self, sujeto, solicitud_id, contexto, censo, destino, ahora):
        """Revision ANTES del primer HTTP; otro payload con la misma solicitud -> ValueError."""
        contrato_json = {
            "modelo": self._contrato.modelo,
            "opciones": list(self._contrato.opciones),
            "sha256": self._contrato.sha256(),
            "version": self._contrato.version,
        }
        miembros = (*censo.miembros, *(destino.miembros if destino is not None else ()))
        fichas_ids = sorted({str(m.ficha_version_id) for m in miembros if m.ficha_version_id})
        if isinstance(sujeto, DecisionARevisar):
            columnas = ("decision_id", "decided_at")
            valores: dict = {"decision_id": sujeto.decision_id, "decided_at": sujeto.decided_at}
            esperado = ("decision", sujeto.decision_id, None)  # plan_sha256
        else:
            columnas = ("plan_canonico", "plan_sha256", "fuentes_semillas", "decided_at")
            valores = {
                "plan_canonico": _canonico(sujeto.plan_canonico),
                "plan_sha256": sujeto.plan_sha256,
                "fuentes_semillas": _canonico(sujeto.fuentes_semillas),
                "decided_at": None,
            }
            esperado = ("semillas", None, sujeto.plan_sha256)
        nombres = ", ".join((*columnas, "censos", "ficha_version_ids", "contrato", "captured_at"))
        placeholders = ", ".join(
            ["%s::jsonb" if c in ("plan_canonico", "fuentes_semillas") else "%s" for c in columnas]
            + ["%s::jsonb", "%s", "%s::jsonb", "%s"]
        )
        self._conn.execute(
            f"INSERT INTO jev_revision (solicitud, sujeto_tipo, {nombres})"
            f" VALUES (%s, %s, {placeholders}) ON CONFLICT (solicitud) DO NOTHING",
            (
                solicitud_id,
                esperado[0],
                *valores.values(),
                _canonico(contexto),
                fichas_ids,
                _canonico(contrato_json),
                ahora,
            ),
        )
        guardado = self._conn.execute(
            "SELECT sujeto_tipo, decision_id, plan_sha256, censos, contrato"
            " FROM jev_revision WHERE solicitud = %s",
            (solicitud_id,),
        ).fetchone()
        coherente = (
            guardado[0] == esperado[0]
            and guardado[1] == esperado[1]
            and guardado[2] == esperado[2]
            and guardado[3] == contexto
            and guardado[4] == contrato_json
        )
        if not coherente:
            raise ValueError("misma solicitud con otro payload")

    def _siguiente_ordinal(self, solicitud_id, clave, tipo) -> int:
        return self._conn.execute(
            "SELECT COALESCE(max(ordinal), 0) + 1 FROM jev_par_evento"
            " WHERE revision_id = %s AND termino_sha256 = %s"
            " AND ficha_version_id = %s AND contrato_sha256 = %s AND tipo = %s",
            (
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                tipo,
            ),
        ).fetchone()[0]

    def _exito_previo(self, solicitud_id, clave):
        """PRIMER exito validado de ESTA revision para la clave (spec; fallo
        jamas; otra revision jamas)."""
        return self._conn.execute(
            "SELECT id, respuesta FROM jev_par_evento"
            " WHERE revision_id = %s AND termino_sha256 = %s"
            " AND ficha_version_id = %s AND contrato_sha256 = %s"
            " AND tipo = 'resultado' AND respuesta IS NOT NULL"
            " ORDER BY ordinal LIMIT 1",
            (
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
            ),
        ).fetchone()

    def _reutilizar(self, solicitud_id, clave, exito) -> None:
        self._conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
            " ficha_version_id, contrato_sha256, ordinal, tipo, reutiliza_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'reutilizacion', %s)",
            (
                uuid.uuid4(),
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                self._siguiente_ordinal(solicitud_id, clave, "reutilizacion"),
                exito[0],
            ),
        )
        self._conn.commit()

    def _intencion(self, solicitud_id, clave, request_hash) -> UUID:
        intencion_id = uuid.uuid4()
        self._conn.execute(
            "INSERT INTO jev_par_evento (id, revision_id, termino_sha256,"
            " ficha_version_id, contrato_sha256, ordinal, tipo, request_sha256)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'intencion', %s)",
            (
                intencion_id,
                solicitud_id,
                clave.termino_literal_sha256,
                clave.ficha_version_id,
                clave.contrato_sha256,
                self._siguiente_ordinal(solicitud_id, clave, "intencion"),
                request_hash,
            ),
        )
        return intencion_id

    def _resultado(self, solicitud_id, clave, intencion_id, devuelto, miembro) -> EstadoPar:
        """Resultado (exito o fallo) tras el HTTP, y su EstadoPar."""
        ordinal = self._siguiente_ordinal(solicitud_id, clave, "resultado")
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
                    solicitud_id,
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
                solicitud_id,
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
            producto_id=miembro.producto_id,
            motivo=f"{devuelto.codigo}: {devuelto.detalle}",
        )

    def _vigencia_de_miembros(
        self,
        censo: CensoCongelado,
        destino: CensoCongelado | None,
        contexto: dict,
        ahora: datetime,
    ) -> Vigencia:
        """Vigencia de la revision: para el listing de cada miembro, la ficha
        del miembro sigue siendo la seleccionada por el MISMO predicado que
        `ficha_vigente` (no revocada, no vencida, cubre el listing, ultima por
        observado_at/created_at). Sin comprobaciones posibles: desconocida."""
        from app.jev_catalogo import ficha_vigente

        plataforma = contexto["plataforma"]
        seleccionada: dict[int, UUID | None] = {}
        comprobadas = 0
        desplazada = False
        miembros = (*censo.miembros, *(destino.miembros if destino is not None else ()))
        for miembro in miembros:
            if miembro.ficha_version_id is None or miembro.producto_id is None:
                continue
            for listing in sorted(miembro.listing_ids):
                if listing not in seleccionada:
                    vigente = ficha_vigente(
                        self._posicional(),
                        producto_id=miembro.producto_id,
                        plataforma=plataforma,
                        listing_id=listing,
                        ahora=ahora,
                    )
                    seleccionada[listing] = vigente.id if vigente is not None else None
                comprobadas += 1
                if seleccionada[listing] != miembro.ficha_version_id:
                    desplazada = True
        if not comprobadas:
            return NoComprobable()
        return Obsoleta() if desplazada else Vigente()

    def _posicional(self):
        """La conexion con filas posicionales: el catalogo desempaca filas por
        posicion y la conexion del dashboard llega con dict_row."""
        from psycopg.rows import tuple_row

        conexion = self._conn

        class _Posicional:
            def execute(self, sentencia, parametros=None):
                return conexion.cursor(row_factory=tuple_row).execute(sentencia, parametros)

        return _Posicional()

    def _destino_cambio(self, decision_id: int, congelado: CensoCongelado) -> bool:
        """R3: el destino de hoy ya no es el que la revision congelo (otro
        anuncio, listing o estado). `synced_at` no cuenta: una resincronizacion
        no cambia el destino. Un destino que ya no se puede leer, tampoco es el
        mismo."""
        from app.jev_catalogo import decision_a_revisar

        try:
            actual = decision_a_revisar(self._posicional(), decision_id).destino_censo
        except ValueError:
            return True
        return actual is None or _identidad_del_censo(actual) != _identidad_del_censo(congelado)

    def _vista_de_revision(self, fila: dict, ahora: datetime) -> VistaAsesoria:
        """Vista historica de UNA revision (vigencia por fichas congeladas)."""
        fila = _fila_dict(fila, _COLUMNAS_REVISION)
        contexto = fila["censos"]
        censo = _censo_de_json(contexto["censo"])
        destino = _censo_de_json(contexto["destino"]) if "destino" in contexto else None
        terminos = list(contexto["terminos"])
        ids_fichas = [
            x if isinstance(x, UUID) else UUID(x) for x in fila["ficha_version_ids"] or []
        ]
        columnas_ficha = (
            "id",
            "producto_id",
            "plataforma",
            "aprobador",
            "sha256",
            "observado_at",
            "revisar_antes_de",
        )
        fichas: list[FichaVersion] = []
        vigencia: Vigencia = NoComprobable()
        if ids_fichas:
            filas = [
                _fila_dict(una, columnas_ficha)
                for una in self._conn.execute(
                    "SELECT f.id, f.producto_id, f.plataforma, f.aprobador, f.sha256,"
                    " f.observado_at, f.revisar_antes_de"
                    " FROM jev_ficha_version f WHERE f.id = ANY(%s)",
                    (ids_fichas,),
                ).fetchall()
            ]
            fichas = [
                FichaVersion(
                    id=d["id"],
                    producto_id=d["producto_id"],
                    plataforma=d["plataforma"],
                    listings=frozenset(),
                    hechos=(),
                    desconocidos=frozenset(),
                    aprobador=d["aprobador"],
                    observado_at=d["observado_at"],
                    revisar_antes_de=d["revisar_antes_de"],
                    sha256=d["sha256"],
                )
                for d in filas
            ]
            vigencia = self._vigencia_de_miembros(censo, destino, contexto, ahora)
        decision_id = fila["decision_id"]
        if (
            destino is not None
            and decision_id is not None
            and self._destino_cambio(decision_id, destino)
        ):
            vigencia = Obsoleta()

        columnas_evento = (
            "id",
            "termino_sha256",
            "ficha_version_id",
            "contrato_sha256",
            "respuesta",
            "error",
        )
        eventos = [
            _fila_dict(evento, columnas_evento)
            for evento in self._conn.execute(
                "SELECT id, termino_sha256, ficha_version_id, contrato_sha256,"
                " respuesta, error FROM jev_par_evento"
                " WHERE revision_id = %s AND tipo = 'resultado' ORDER BY ordinal",
                (fila["solicitud"],),
            ).fetchall()
        ]

        def composicion_de(un_censo: CensoCongelado, termino: str, hash_termino: str):
            """Reconstruccion HISTORICA del ambito: mismos eventos, un
            universo propio (origen o destino jamas mezclados)."""
            if es_asin_like(termino):
                return componer(un_censo, (NoAplicaTexto(motivo="asin_like"),))
            pares: list[EstadoPar] = []
            for miembro in un_censo.miembros:
                if _solo_no_activo(miembro.estados):
                    continue
                if miembro.ficha_version_id is None:
                    pares.append(FichaFaltante(producto_id=miembro.producto_id))
                    continue
                propio = _evento_del_par(eventos, hash_termino, miembro.ficha_version_id)
                if propio is None:
                    continue
                clave = ClavePar(
                    termino_literal_sha256=hash_termino,
                    ficha_version_id=miembro.ficha_version_id,
                    contrato_sha256=propio["contrato_sha256"],
                )
                if propio["respuesta"] is not None:
                    pares.append(_juicio_de_respuesta(propio["respuesta"], clave, propio["id"]))
                else:
                    pares.append(
                        FalloProveedor(
                            producto_id=miembro.producto_id,
                            motivo=str(propio["error"] or "fallo del proveedor"),
                        )
                    )
            return componer(un_censo, tuple(pares))

        resultados = []
        destinos = []
        for termino in terminos:
            hash_termino = hashlib.sha256(termino.encode("utf-8")).hexdigest()
            resultados.append((termino, composicion_de(censo, termino, hash_termino)))
            if destino is not None:
                destinos.append((termino, composicion_de(destino, termino, hash_termino)))

        return VistaAsesoria(
            solicitud=fila["solicitud"],
            sujeto=fila["sujeto_tipo"],
            decision_id=fila["decision_id"],
            plan_sha256=fila["plan_sha256"],
            captured_at=fila["captured_at"],
            resultados=tuple(resultados),
            fichas=tuple(fichas),
            fuentes_semillas=fila["fuentes_semillas"],
            vigencia=vigencia,
            destinos=tuple(destinos),
        )

    def leer(self, referencias, *, ahora: datetime) -> dict:
        """Vistas de revisiones guardadas para la UI (2.1/2.2): SOLO lectura,
        eventos de SU revision; None sin revision ligada."""
        salidas: dict = {}
        for ref in referencias:
            campo, valor = (
                ("plan_sha256", ref.plan_sha256)
                if isinstance(ref, ReferenciaPlan)
                else ("decision_id", ref)
            )
            fila = self._conn.execute(
                "SELECT solicitud, sujeto_tipo, decision_id, plan_sha256, censos,"
                " ficha_version_ids, captured_at, fuentes_semillas"
                " FROM jev_revision WHERE sujeto_tipo = %s"
                f" AND {campo} = %s ORDER BY created_at DESC, solicitud LIMIT 1",
                ("semillas" if campo == "plan_sha256" else "decision", valor),
            ).fetchone()
            salidas[ref] = self._vista_de_revision(fila, ahora) if fila is not None else None
        return salidas
