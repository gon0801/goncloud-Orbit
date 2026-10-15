"""Contratos puros de REPRICING 02, corte 0.a.

Seccion A del bosquejo (dinero con moneda, envio con origen, Fees partidos,
Cuenta), protocolo Atribuidor, registro de universos, lector de config de la
seccion H y raiz de composicion de mercados. Sin base y sin red.
"""

from __future__ import annotations

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import precio_mercados
from app.estimacion_universo import universo_de
from app.estimacion_venta import MOTIVOS_ESTIMACION
from app.precio import config, puerto
from app.precio.tipos import (
    Cuenta,
    EnvioImputado,
    EnvioIncluido,
    EnvioPropio,
    Fees,
    Importe,
    MonedasMezcladas,
    MuestraEnvio,
    TasaFx,
    convertir,
)

RAIZ = Path(__file__).resolve().parents[1]


def _mxn(valor: str) -> Importe:
    return Importe(Decimal(valor), "MXN")


def _muestra(*, origen: str, donantes: int | None = None) -> MuestraEnvio:
    return MuestraEnvio(
        id=1,
        product_id=7,
        plataforma="amazon_mx",
        canal="fbm",
        origen=origen,
        valor=_mxn("90"),
        envios=3,
        donantes=donantes,
        ventana=(date(2026, 4, 1), date(2026, 9, 30)),
        regla_version="v1",
    )


def _fees(total: str = "12", variable: str = "10", fijo: str = "2") -> Fees:
    return Fees(
        total=_mxn(total),
        variable=_mxn(variable),
        fijo=_mxn(fijo),
        impuesto_pendiente=False,
        detalle=(),
    )


def _cuenta(
    *,
    precio="100",
    ingreso="100",
    costo="20",
    fees=None,
    envio=None,
    retencion="8",
    moneda="MXN",
    moneda_envio=None,
) -> Cuenta:
    def imp(valor: str, mon: str) -> Importe:
        return Importe(Decimal(valor), mon)

    return Cuenta(
        precio=imp(precio, moneda),
        ingreso=imp(ingreso, moneda),
        costo=imp(costo, moneda),
        fees=fees if fees is not None else _fees(),
        envio=envio if envio is not None else EnvioIncluido(importe=imp("0", moneda)),
        retencion=imp(retencion, moneda),
    )


# ---------------------------------------------------------------------------
# A. dinero con moneda
# ---------------------------------------------------------------------------


def test_importe_suma_resta_escala_y_compara_en_la_misma_moneda():
    a, b = _mxn("10"), _mxn("3")
    assert (a + b).valor == Decimal("13")
    assert (a - b).valor == Decimal("7")
    assert (a + b).moneda == "MXN"
    assert a.escalar(Decimal("0.5")).valor == Decimal("5")
    assert a > b and b < a and a >= _mxn("10")


def test_importe_con_monedas_distintas_revierta():
    a, b = _mxn("10"), Importe(Decimal("3"), "USD")
    with pytest.raises(MonedasMezcladas):
        a + b
    with pytest.raises(MonedasMezcladas):
        a - b
    with pytest.raises(MonedasMezcladas):
        _ = a > b


def test_convertir_es_la_unica_puerta_entre_monedas():
    tasa = TasaFx(
        base="MXN", quote="USD", tasa=Decimal("0.05"), fecha=date(2026, 10, 13), fuente="fx_resolve"
    )
    convertido = convertir(_mxn("100"), tasa)
    assert convertido.en.moneda == "USD"
    assert convertido.en.valor == Decimal("5")
    assert convertido.original is not None and convertido.original.moneda == "MXN"
    with pytest.raises(ValueError, match="no aplica"):
        convertir(Importe(Decimal("100"), "USD"), tasa)


# ---------------------------------------------------------------------------
# A. fees partidos y Cuenta
# ---------------------------------------------------------------------------


def test_fees_exige_total_como_variable_mas_fijo():
    with pytest.raises(ValueError):
        _fees(total="12", variable="9", fijo="2")
    assert _fees().total.valor == _fees().variable.valor + _fees().fijo.valor


def test_cuenta_no_se_construye_con_monedas_mezcladas():
    envio_usd = EnvioIncluido(importe=Importe(Decimal("1"), "USD"))
    with pytest.raises(MonedasMezcladas):
        _cuenta(envio=envio_usd)


def test_cuenta_margen_es_ingreso_menos_todo_sobre_ingreso():
    cuenta = _cuenta(
        ingreso="100",
        costo="20",
        fees=_fees(total="12", variable="10", fijo="2"),
        envio=EnvioIncluido(importe=_mxn("5")),
        retencion="8",
    )
    assert cuenta.margen == Decimal("0.55")


# ---------------------------------------------------------------------------
# A. envio con origen: un imputado no se confunde con un medido
# ---------------------------------------------------------------------------


def test_envio_proprio_exige_muestra_propia():
    assert EnvioPropio(importe=_mxn("90"), muestra=_muestra(origen="propio")).muestra.id == 1
    with pytest.raises(ValueError):
        EnvioPropio(importe=_mxn("90"), muestra=_muestra(origen="familia", donantes=2))


def test_envio_imputado_rechaza_muestra_propia_y_sin_donantes():
    assert (
        EnvioImputado(
            importe=_mxn("90"), muestra=_muestra(origen="familia", donantes=3)
        ).muestra.donantes
        == 3
    )
    with pytest.raises(ValueError):
        EnvioImputado(importe=_mxn("90"), muestra=_muestra(origen="propio"))
    with pytest.raises(ValueError):
        EnvioImputado(importe=_mxn("90"), muestra=_muestra(origen="familia", donantes=0))


# ---------------------------------------------------------------------------
# B. puerto: miembro que manda
# ---------------------------------------------------------------------------


def _miembro(sku: str) -> puerto.Miembro:
    return puerto.Miembro(product_id=1, seller_sku=sku, variante=None)


def test_miembro_que_manda_toma_el_de_precio_estrella_mas_alto():
    caro = _cuenta(costo="40")
    barato = _cuenta(costo="10")
    elegido, cuenta = puerto.miembro_que_manda(
        ((_miembro("SKU-B"), barato), (_miembro("SKU-A"), caro)),
        goal_de=lambda _m: Decimal("0.30"),
    )
    assert elegido.seller_sku == "SKU-A"
    assert cuenta is caro


def test_miembro_que_manda_desempata_por_sku_ascendente():
    misma = _cuenta()
    elegido, _ = puerto.miembro_que_manda(
        ((_miembro("SKU-B"), misma), (_miembro("SKU-A"), _cuenta())),
        goal_de=lambda _m: Decimal("0.30"),
    )
    assert elegido.seller_sku == "SKU-A"


def test_puerto_expone_vitrina_y_mercado():
    assert puerto.Vitrina is not None and puerto.Mercado is not None
    for nombre in ("catalogo", "insumos", "observado"):
        assert hasattr(puerto.Vitrina, nombre)
    for nombre in ("cotizar", "precio_vivo", "escribir", "cerrar"):
        assert hasattr(puerto.Mercado, nombre)


# ---------------------------------------------------------------------------
# M. registro de universos
# ---------------------------------------------------------------------------


def test_registro_de_universos_trae_las_cuatro_entradas():
    claves = {}
    for plataforma, canal in (
        ("amazon_mx", "fba"),
        ("amazon_mx", "fbm"),
        ("amazon_us", "fbm"),
        ("meli", "meli"),
    ):
        universo = universo_de(plataforma, canal)
        assert universo is not None, f"falta el universo {plataforma}/{canal}"
        claves[universo.clave] = universo
    assert set(claves) == {"amazon_mx/fba", "amazon_mx/fbm", "amazon_us/fbm", "meli/meli"}
    assert claves["amazon_us/fbm"].moneda_venta == "USD"
    assert claves["amazon_us/fbm"].fx_max_dias == 3
    assert claves["amazon_mx/fba"].envio == "politica"
    assert claves["meli/meli"].envio == "muestra"
    assert universo_de("amazon_us", "fba") is None


# ---------------------------------------------------------------------------
# H. config: claves nuevas y modo_de
# ---------------------------------------------------------------------------


def _settings_completos() -> dict:
    return {
        "precio_modo_global": "shadow",
        "precio_modo_universo": {"amazon_mx/fba": "shadow"},
        "precio_goal_min_pct": "0.10",
        "precio_goal_max_pct": "0.60",
        "precio_freno_dias_error": 3,
        "precio_catalogo_max_dias_sin_reportar": 3,
        "precio_aviso_dias_sin_evaluar": 3,
        "precio_fusible_frac": "0.50",
        "precio_fusible_min_movimientos": 10,
        "precio_insumo_salto_pct": "0.05",
        "precio_insumo_salto_frac": "0.30",
        "precio_corte_errores_consecutivos": 5,
        "precio_senal_trafico_min": 200,
        "precio_cohorte_caida_pct": "0.25",
        "precio_envio_ventana_dias": 180,
    }


def test_lector_de_config_falla_y_nombra_la_clave_que_falta():
    settings = _settings_completos()
    del settings["precio_modo_global"]
    with pytest.raises(ValueError, match="precio_modo_global"):
        config.leer_config_motor(settings)


@pytest.mark.parametrize(
    ("clave", "valor"),
    [
        ("precio_fusible_frac", "0.05"),
        ("precio_fusible_frac", "0.95"),
        ("precio_fusible_min_movimientos", 0),
        ("precio_insumo_salto_pct", "0.001"),
        ("precio_insumo_salto_frac", "0.91"),
        ("precio_corte_errores_consecutivos", 1),
        ("precio_senal_trafico_min", 19),
        ("precio_cohorte_caida_pct", "0.04"),
        ("precio_envio_ventana_dias", 29),
    ],
)
def test_config_motor_rechaza_claves_fuera_de_cota(clave: str, valor):
    settings = _settings_completos()
    settings[clave] = valor
    with pytest.raises(ValueError, match=clave):
        config.leer_config_motor(settings)


def test_config_motor_rechaza_modo_invalido_y_mapeo_basura():
    settings = _settings_completos()
    settings["precio_modo_global"] = "turbo"
    with pytest.raises(ValueError, match="precio_modo_global"):
        config.leer_config_motor(settings)
    settings = _settings_completos()
    settings["precio_modo_universo"] = {"amazon_mx/fba": "live", "meli/meli": "off"}.copy()
    settings["precio_modo_universo"] = {"meli/meli": "apagado"}
    with pytest.raises(ValueError, match="meli/meli"):
        config.leer_config_motor(settings)


def test_modo_de_da_off_para_universo_sin_entrada():
    motor = config.leer_config_motor(_settings_completos())
    assert motor.modo_de("amazon_mx", "fba") == "shadow"
    assert motor.modo_de("amazon_mx", "fbm") == "off"
    settings = _settings_completos()
    settings["precio_modo_global"] = "off"
    assert config.leer_config_motor(settings).modo_de("amazon_mx", "fba") == "off"
    settings["precio_modo_global"] = "shadow"
    settings["precio_modo_universo"] = {"amazon_mx/fba": "live"}
    assert config.leer_config_motor(settings).modo_de("amazon_mx", "fba") == "shadow"


# ---------------------------------------------------------------------------
# S. raiz de composicion
# ---------------------------------------------------------------------------


def test_abrir_meli_da_vitrina_de_solo_lectura():
    """M.4 jubila el stub 0.b: `abrir("meli")` compone `MercadoMeli`.

    Sin clientes es vitrina (sirve en la pantalla): leer no exige
    clientes; escribir si (`_exigir_clientes`, sin tocar la red).
    """
    from app.meli.precio_mercado import MercadoMeli

    with precio_mercados.abrir("meli") as mercado:
        assert isinstance(mercado, MercadoMeli)
        assert mercado.clave == "meli"
        with pytest.raises(RuntimeError, match="vitrina de solo lectura"):
            mercado.escribir(None, None)


def test_abrir_desconocida_levanta_config_invalida():
    with pytest.raises(precio_mercados.ConfigInvalida), precio_mercados.abrir("ebay"):
        pass


# ---------------------------------------------------------------------------
# P. protocolo Atribuidor
# ---------------------------------------------------------------------------


def test_atribuidor_es_un_protocolo_con_atribucion_concreta():
    from app.ledger_atribucion import Atribucion, Atribuidor, atribuidor_amazon

    assert atribuidor_amazon.atribuir(object()) is None
    fila = Atribucion(listing_id=5, variante=None, resuelto_por="sku")
    assert fila.resuelto_por == "sku"
    assert Atribuidor is not None


# ---------------------------------------------------------------------------
# vocabulario: la estimacion es duena de sus motivos
# ---------------------------------------------------------------------------


_MODULOS_ESTIMACION = (
    "estimacion_insumos",
    "estimacion_venta",
    "estimacion_ingest",
    "estimacion_repository",
    "estimacion_reader",
    "estimacion_fees",
    # M.3: el cotizador/resolver MeLi tambien emite motivos persistidos.
    "meli/estimacion",
)

# Literales que aparecen en posiciones de motivo pero no son motivos de
# escenario: estados de frescura/escenario y mensajes de validadores.
_NO_SON_MOTIVOS = {
    "fresco",
    "futura",
    "desactualizada",
    # M.3: `_estado_meli_union` devuelve estados, no motivos.
    "incompleta",
    "asin distinto",
    "canal desconocido",
    "fetched_at invalido",
    "plataforma distinta",
    "plataforma fuera de dominio",
    "precio con mas de 4 decimales (dato faltante)",
    "precio fuera de rango NUMERIC(14,4)",
    "precio no positivo (dato faltante)",
}


def _literales_de_motivo(ruta: Path) -> set[str]:
    """Recorre el AST y junta los literales usados como motivo: keyword
    `motivo=`, `return "..."` (solo o en tupla), `motivos.append(...)` y
    listas/sets asignados a nombres con `motivo` o `bloqueante`."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    hallados: set[str] = set()

    def es_cadena(nodo) -> bool:
        return isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)

    def junta(elementos) -> None:
        for elemento in elementos:
            if es_cadena(elemento):
                hallados.add(elemento.value)

    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            funcion = nodo.func
            if (
                isinstance(funcion, ast.Attribute)
                and funcion.attr in ("append", "add")
                and isinstance(funcion.value, ast.Name)
                and (
                    "motivo" in funcion.value.id.lower() or "bloqueante" in funcion.value.id.lower()
                )
                and len(nodo.args) == 1
                and es_cadena(nodo.args[0])
            ):
                hallados.add(nodo.args[0].value)
            for palabra in nodo.keywords:
                if palabra.arg == "motivo" and es_cadena(palabra.value):
                    hallados.add(palabra.value.value)
        elif isinstance(nodo, ast.Return):
            if es_cadena(nodo.value):
                hallados.add(nodo.value.value)
            elif isinstance(nodo.value, (ast.Tuple, ast.List)):
                junta(nodo.value.elts)
        elif isinstance(nodo, ast.Assign):
            for objetivo in nodo.targets:
                if (
                    isinstance(objetivo, ast.Name)
                    and ("motivo" in objetivo.id.lower() or "bloqueante" in objetivo.id.lower())
                    and isinstance(nodo.value, (ast.List, ast.Tuple, ast.Set))
                ):
                    junta(nodo.value.elts)
    return hallados


def test_todo_literal_de_motivo_de_la_estimacion_esta_en_motivos_estimacion():
    """Un motivo nuevo en la estimacion ya no puede caer en
    `estimacion_motivo_desconocido` sin que esta prueba falle."""
    for nombre in _MODULOS_ESTIMACION:
        for literal in sorted(_literales_de_motivo(RAIZ / "app" / f"{nombre}.py")):
            if literal in _NO_SON_MOTIVOS:
                continue
            assert literal in MOTIVOS_ESTIMACION, (
                f"{nombre} emite el motivo {literal!r} y no esta en MOTIVOS_ESTIMACION"
            )


def test_motivos_no_evaluado_es_estimacion_mas_los_propios_del_motor():
    from app.precio.tipos import MOTIVOS_NO_EVALUADO

    propios = {
        "moneda_divergente",
        "precio_divergente",
        "precio_sin_observar",
        "fee_error",
        "ingreso_no_positivo",
        "escenario_incoherente",
        "estimacion_motivo_desconocido",
        "envio_sin_historia",
        "variante_sin_mapear",
        "fx_desactualizado",
        "cotizacion_de_otro_goal",
    }
    assert set(MOTIVOS_NO_EVALUADO) == set(MOTIVOS_ESTIMACION) | propios
    assert len(MOTIVOS_NO_EVALUADO) == len(set(MOTIVOS_NO_EVALUADO))
