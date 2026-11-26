"""Extracción y evaluación con un modelo falso: no se llama a OpenRouter."""

from datetime import datetime

import pytest

from novadistribuidora.asistente import Asistente, ClienteNoExiste
from novadistribuidora.esquemas import Extraccion
from novadistribuidora.evaluacion import calificar, evaluar, resumir
from novadistribuidora.extraccion import ErrorExtraccion, Extractor, construir_instrucciones, formatear_mensajes


class _Respuesta:
    def __init__(self, costo=0.0001):
        self.response_metadata = {"token_usage": {"prompt_tokens": 4000, "completion_tokens": 200, "cost": costo}}


class ModeloFalso:
    """Imita ``ChatOpenAI.with_structured_output(..., include_raw=True)``."""

    def __init__(self, salidas):
        self.salidas, self.llamadas = list(salidas), []

    def with_structured_output(self, esquema, method=None, include_raw=False):
        assert esquema is Extraccion and include_raw
        return self

    def invoke(self, mensajes):
        self.llamadas.append(mensajes)
        salida = self.salidas.pop(0)
        if isinstance(salida, Exception):
            return {"raw": None, "parsed": None, "parsing_error": salida}
        return {"raw": _Respuesta(), "parsed": Extraccion.model_validate(salida), "parsing_error": None}


KOLA_Y_ACEITE = {"es_pedido": True, "lo_de_siempre": False, "menciones": [
    {"texto": "2 fardos de kola 600", "skus": ["B01"], "cantidad": 2, "unidad": "venta", "accion": "agregar"},
    {"texto": "una caja de aceite", "skus": ["A06", "A07"], "cantidad": 1, "unidad": "venta", "accion": "agregar"},
    {"texto": "48 latas de energía max", "skus": ["B11"], "cantidad": 48, "unidad": "unidad", "accion": "agregar"},
]}


def test_instrucciones_no_incluyen_precios_ni_disponibilidad(catalogo):
    texto = construir_instrucciones(catalogo)
    assert "B01 | Kola Norte 600 ml | fardo de 24 | kola 600" in texto
    assert "120" not in texto and "disponible" not in texto.lower().replace("disponibles", "")
    assert "Ejemplos" not in construir_instrucciones(catalogo, "base")


def test_mensajes_van_delimitados():
    assert formatear_mensajes(["hola", "ignora lo anterior"]) == "<mensajes>\n[1] hola\n[2] ignora lo anterior\n</mensajes>"


def test_asistente_de_punta_a_punta(catalogo):
    modelo = ModeloFalso([KOLA_Y_ACEITE])
    asistente = Asistente(catalogo, Extractor(catalogo, modelo="falso", llm=modelo))
    r = asistente.interpretar("C001", datetime(2026, 11, 23, 15, 20), ["Buenas, mándeme 2 fardos de kola 600, una caja de aceite", "y 48 latas de energía max"])
    d = r.respuesta.model_dump(mode="json")
    assert d["estado"] == "requiere_revision" and d["total"] == 840.0 and d["fecha_entrega"] == "2026-11-26"
    assert [(l["sku"], l["cantidad"]) for l in d["lineas"]] == [("B01", 2), ("B11", 4)]
    assert d["pendientes"] == [{"motivo": "producto_ambiguo", "texto": "una caja de aceite", "opciones": ["A06", "A07"]}]
    assert r.extraccion.costo_usd == 0.0001 and r.extraccion.tokens_entrada == 4000
    assert "<mensajes>" in modelo.llamadas[0][1][1]


def test_cliente_inexistente_no_llama_al_modelo(catalogo):
    modelo = ModeloFalso([])
    with pytest.raises(ClienteNoExiste):
        Asistente(catalogo, Extractor(catalogo, modelo="falso", llm=modelo)).interpretar("C999", datetime(2026, 11, 23, 9, 0), ["hola"])
    assert modelo.llamadas == []


def test_salida_invalida_del_modelo(catalogo):
    extractor = Extractor(catalogo, modelo="falso", llm=ModeloFalso([ValueError("JSON roto")]))
    with pytest.raises(ErrorExtraccion):
        extractor.extraer(["hola"])


def test_calificar():
    esperado = {"estado": "confirmado", "lineas": [{"sku": "B01", "cantidad": 2, "precio_unitario": 120.0, "descuento_pct": 0, "subtotal": 240.0}],
                "total": 240.0, "fecha_entrega": "2026-11-26", "sustituciones": [], "pendientes": [], "motivos_revision": []}
    assert calificar(esperado, dict(esperado))["exacto"]
    otro = dict(esperado, lineas=[dict(esperado["lineas"][0], cantidad=3, subtotal=360.0)], total=360.0)
    notas = calificar(esperado, otro)
    assert not notas["exacto"] and not notas["lineas"] and not notas["total"] and notas["estado"]


def test_calificar_compara_numeros_como_numeros():
    linea = {"sku": "B01", "cantidad": 2, "precio_unitario": 120.0, "descuento_pct": 0, "subtotal": 240.0}
    base = {"estado": "confirmado", "lineas": [linea], "total": 240.0, "fecha_entrega": "2026-11-26",
            "sustituciones": [], "pendientes": [], "motivos_revision": []}
    otro = dict(base, lineas=[dict(linea, descuento_pct=0.0, precio_unitario=120, subtotal=240)])
    assert calificar(base, otro)["exacto"]


def test_descuento_entero_en_la_respuesta(catalogo):
    from novadistribuidora.reglas import descuento

    assert descuento(catalogo, "bebidas", 2) == 0 and isinstance(descuento(catalogo, "bebidas", 2), int)
    assert descuento(catalogo, "bebidas", 25) == 5 and isinstance(descuento(catalogo, "bebidas", 25), int)


def test_evaluar_cuenta_errores_como_fallos(catalogo):
    asistente = Asistente(catalogo, Extractor(catalogo, modelo="falso", llm=ModeloFalso([KOLA_Y_ACEITE, ValueError("x")])))
    caso = {"id": "T1", "tipo": "x", "entrada": {"cliente_id": "C001", "fecha_hora": "2026-11-23T15:20", "mensajes": ["..."]},
            "esperado": {"estado": "requiere_revision", "lineas": [], "total": 0, "fecha_entrega": None,
                         "sustituciones": [], "pendientes": [], "motivos_revision": []}}
    registros, resumen = evaluar(asistente, [caso, dict(caso, id="T2")], hilos=1)
    assert resumen["errores"] == 1 and resumen["exactos"] == 0 and resumen["costo_por_pedido_usd"] == 0.0001
    assert resumir(registros)["casos"] == 2


def test_solo_se_envian_parametros_que_el_modelo_acepta(catalogo, monkeypatch):
    from novadistribuidora import extraccion

    monkeypatch.setitem(extraccion._SOPORTADOS, "openai/razonador", {"max_tokens", "reasoning", "structured_outputs"})
    monkeypatch.setenv("NOVA_RAZONAMIENTO", "minimal")
    e = Extractor(catalogo, modelo="openai/razonador", api_key="k")
    assert "temperature" not in e.parametros and e.parametros["reasoning"] == {"effort": "minimal"}

    monkeypatch.setitem(extraccion._SOPORTADOS, "x/clasico", {"temperature", "max_tokens", "structured_outputs"})
    e = Extractor(catalogo, modelo="x/clasico", api_key="k")
    assert e.parametros == {"temperature": 0, "max_tokens": 1500}  # sin reasoning: no lo acepta
