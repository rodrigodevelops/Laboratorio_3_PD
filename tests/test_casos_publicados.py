"""Los 84 casos publicados, con una extracción ideal deducida de su respuesta esperada.

Verifica que el motor de reglas calcule exactamente lo que espera el docente:
líneas, precios, descuentos, total, fecha, sustituciones, motivos y estado. Se
salta si los datos publicados no están (por ejemplo, en el CI).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from novadistribuidora.catalogo import Catalogo
from novadistribuidora.esquemas import Extraccion, Mencion
from novadistribuidora.reglas import armar_pedido

PUBLICADOS = Path(__file__).resolve().parents[1] / "publicados"
pytestmark = pytest.mark.skipif(not (PUBLICADOS / "datos").exists(), reason="sin datos publicados")


def extraccion_ideal(esp: dict) -> Extraccion:
    if esp["estado"] == "fuera_de_alcance":
        return Extraccion(es_pedido=False, lo_de_siempre=False, menciones=[])
    sust = {s["sku_entregado"]: s for s in esp["sustituciones"]}
    menciones = []
    for l in esp["lineas"]:
        s = sust.get(l["sku"])
        if s:
            menciones.append(Mencion(texto="", skus=[s["sku_solicitado"]], cantidad=s["cantidad"], unidad="venta", accion="agregar"))
            if l["cantidad"] > s["cantidad"]:
                menciones.append(Mencion(texto="", skus=[l["sku"]], cantidad=l["cantidad"] - s["cantidad"], unidad="venta", accion="agregar"))
        else:
            menciones.append(Mencion(texto="", skus=[l["sku"]], cantidad=l["cantidad"], unidad="venta", accion="agregar"))
    for p in esp["pendientes"]:
        menciones.append(Mencion(texto=p["texto"], skus=p["opciones"], cantidad=None, unidad="venta", accion="agregar"))
    return Extraccion(es_pedido=True, lo_de_siempre=False, menciones=menciones)


def _casos():
    ruta = PUBLICADOS / "ejemplos" / "mensajes_dev.jsonl"
    return [json.loads(l) for l in ruta.read_text().splitlines()] if ruta.exists() else []


@pytest.fixture(scope="module")
def cat():
    return Catalogo.desde_carpeta(PUBLICADOS / "datos")


@pytest.mark.parametrize("caso", _casos(), ids=lambda c: c["id"])
def test_motor_reproduce_la_respuesta_esperada(cat, caso):
    e, esp = caso["entrada"], caso["esperado"]
    p = armar_pedido(e["cliente_id"], datetime.strptime(e["fecha_hora"], "%Y-%m-%dT%H:%M"), extraccion_ideal(esp), cat)
    assert p.estado == esp["estado"]
    assert p.total == esp["total"]
    assert (p.fecha_entrega.isoformat() if p.fecha_entrega else None) == esp["fecha_entrega"]
    assert p.motivos_revision == esp["motivos_revision"]
    assert sorted((l.model_dump() for l in p.lineas), key=lambda x: x["sku"]) == sorted(esp["lineas"], key=lambda x: x["sku"])
    assert [s.model_dump() for s in p.sustituciones] == esp["sustituciones"]
