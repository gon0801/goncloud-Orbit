"""Avisos de Telegram del job jev-senales (JEV ADS 02, S.8).

El lazo de insertar-antes-de-enviar se prueba con un escritor falso: la
corrida contra Postgres vive en test_jev_senales y se salta sin base.
"""

from __future__ import annotations

from datetime import UTC, datetime

from test_jev_pantallas import _fila_base

from app.jev_avisos import _armado_aviso, _avisos_de, _enviar_avisos
from app.jev_lectura import Ajustes, ClaveBusqueda, PropuestaEnVeto, SenalPropuesta, vista_de_dict

VENCE = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)


class _Escritor:
    def __init__(self):
        self.avisos: dict = {}
        self.entregas: set = set()
        self._fila = None

    def execute(self, sql, params=None):
        compacto = " ".join(sql.split())
        if compacto.startswith("SELECT a.id"):
            guardado = self.avisos.get((params[0], params[1]))
            self._fila = (
                None
                if guardado is None
                else (guardado["id"], guardado["texto"], guardado["id"] in self.entregas)
            )
        elif compacto.startswith("INSERT INTO jev_aviso "):
            clave = (params[2], params[6])
            if clave in self.avisos:
                self._fila = None
            else:
                self.avisos[clave] = {
                    "id": params[0],
                    "texto": params[7],
                    "origen": params[4],
                    "destino": params[5],
                }
                self._fila = (params[0], params[7])
        elif compacto.startswith("SELECT id, texto FROM jev_aviso"):
            guardado = self.avisos[(params[0], params[1])]
            self._fila = (guardado["id"], guardado["texto"])
        elif compacto.startswith("INSERT INTO jev_aviso_entrega"):
            self.entregas.add(params[0])
            self._fila = None
        else:
            raise AssertionError(compacto)
        return self

    def fetchone(self):
        return self._fila


def _vista(**cambios):
    return vista_de_dict(_fila_base(**cambios))


def _propuesta(vista, *, kind="negative", destino=None, ilegible=False):
    return PropuestaEnVeto(
        41,
        7,
        kind,
        "live",
        vista.clave,
        None if destino is None else destino.clave,
        ilegible,
        VENCE,
    )


def test_fallo_guarda_el_texto_y_el_reintento_no_lo_cambia():
    vista = _vista(lectura="vendio_aqui")
    propuesta = _propuesta(vista)
    vistas = {vista.clave: vista}
    escritor = _Escritor()
    enviados: list[str] = []

    def falla(texto):
        enviados.append(texto)
        return False

    assert _enviar_avisos(escritor, "lote-1", (propuesta,), vistas, {7: "NOMBRE UNO"}, falla) == 0
    assert len(escritor.avisos) == 1
    assert escritor.entregas == set()

    def entrega(texto):
        enviados.append(texto)
        return True

    assert _enviar_avisos(escritor, "lote-2", (propuesta,), vistas, {7: "NOMBRE DOS"}, entrega) == 1
    assert enviados[0] == enviados[1]
    assert "NOMBRE UNO" in enviados[1]
    assert "NOMBRE DOS" not in enviados[1]
    assert len(escritor.avisos) == 1
    assert len(escritor.entregas) == 1

    def sobra(texto):
        enviados.append(texto)
        return True

    assert _enviar_avisos(escritor, "lote-3", (propuesta,), vistas, {7: "NOMBRE TRES"}, sobra) == 0
    assert len(enviados) == 2


def test_otra_lectura_es_otro_aviso():
    primera = _vista(lectura="vendio_aqui")
    propuesta = _propuesta(primera)
    escritor = _Escritor()
    assert (
        _enviar_avisos(
            escritor, "lote", (propuesta,), {primera.clave: primera}, {7: "G"}, lambda texto: True
        )
        == 1
    )
    segunda = _vista(lectura="vende_en_otro", senal_id=primera.senal_id)
    assert (
        _enviar_avisos(
            escritor, "lote", (propuesta,), {segunda.clave: segunda}, {7: "G"}, lambda texto: False
        )
        == 0
    )
    assert set(escritor.avisos) == {(41, "vendio_aqui"), (41, "vende_en_otro")}
    assert len(escritor.entregas) == 1


def test_harvest_guarda_la_lectura_del_destino():
    origen = _vista(lectura="vendio_aqui", ad_group_id=7, termino="arras")
    destino = _vista(
        lectura="ajena", relevancia="ajena", ad_group_id=8, termino="arras", productos_ok=[]
    )
    propuesta = _propuesta(origen, kind="harvest", destino=destino)
    armado = _armado_aviso(propuesta, {origen.clave: origen, destino.clave: destino})
    assert armado[0] == "ajena"
    assert armado[2] == destino.senal_id
    escritor = _Escritor()
    _enviar_avisos(
        escritor,
        "lote",
        (propuesta,),
        {origen.clave: origen, destino.clave: destino},
        {8: "Destino"},
        lambda texto: True,
    )
    guardado = escritor.avisos[(41, "ajena")]
    assert guardado["destino"] == destino.senal_id
    assert "Destino" in guardado["texto"]
    assert "AJENA" in guardado["texto"]


def test_harvest_sin_destino_anuncia_sin_lectura():
    origen = _vista(lectura="vendio_aqui")
    propuesta = PropuestaEnVeto(9, 3, "harvest", "live", origen.clave, None, True, VENCE)
    armado = _armado_aviso(propuesta, {origen.clave: origen})
    assert armado is not None
    assert armado[0] == "sin_lectura"
    assert armado[2] is None
    assert isinstance(armado[3], SenalPropuesta)


def test_sin_senal_de_origen_no_inserta():
    vista = _vista()
    propuesta = _propuesta(vista)
    escritor = _Escritor()
    llamados = []
    assert (
        _enviar_avisos(
            escritor,
            "lote",
            (propuesta,),
            {},
            {7: "G"},
            lambda texto: llamados.append(texto) or True,
        )
        == 0
    )
    assert escritor.avisos == {}
    assert llamados == []


def test_avisos_apagados_no_llaman(monkeypatch):
    def bomba(*_args, **_kwargs):
        raise AssertionError("jev.avisos apagado no debe armar avisos")

    monkeypatch.setattr("app.jev_avisos._avisar", bomba)
    ajustes = Ajustes(1, 5000, 3, False)
    assert _avisos_de(ajustes, lambda texto: True, None, None, None, ()) == 0
    assert _avisos_de(Ajustes(1, 5000, 3, True), None, None, None, None, ()) == 0


def test_avisos_encendidos_delegan(monkeypatch):
    visto = {}

    def falso(lector, escritor, lote, propuestas, avisar):
        visto["lote"] = lote
        visto["avisar"] = avisar
        return 4

    monkeypatch.setattr("app.jev_avisos._avisar", falso)
    ajustes = Ajustes(1, 5000, 3, True)

    def marca(_texto):
        return True

    assert _avisos_de(ajustes, marca, "l", "e", "lote-9", ()) == 4
    assert visto == {"lote": "lote-9", "avisar": marca}


def test_armado_no_confunde_claves():
    """Una clave que no es la del origen no alcanza para avisar."""
    vista = _vista()
    otra = ClaveBusqueda("amazon_us", 7, "zapato rojo")
    assert _armado_aviso(_propuesta(vista), {otra: vista}) is None
