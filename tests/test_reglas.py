"""Pruebas del motor de reglas (politicas.md), sin modelo de lenguaje."""

from datetime import date, datetime

import pytest

from novadistribuidora.esquemas import Extraccion, Mencion
from novadistribuidora.mensaje import redactar
from novadistribuidora.reglas import ClienteNoExiste, armar_pedido, convertir, fecha_entrega, subtotal

JUEVES_10 = datetime(2026, 11, 26, 10, 0)  # jueves antes del corte


def m(skus, cantidad=None, unidad="venta", accion="agregar", texto="x"):
    return Mencion(texto=texto, skus=skus if isinstance(skus, list) else [skus],
                   cantidad=cantidad, unidad=unidad, accion=accion)


def pedido(catalogo, *menciones, cliente="C001", cuando=JUEVES_10, siempre=False, es_pedido=True):
    ex = Extraccion(es_pedido=es_pedido, lo_de_siempre=siempre, menciones=list(menciones))
    return armar_pedido(cliente, cuando, ex, catalogo)


def lineas(p):
    return {l.sku: l.cantidad for l in p.lineas}


# ------------------------------------------------------------ cantidades (política 2)

@pytest.mark.parametrize("cantidad, unidad, upe, esperado", [
    (3, "venta", 24, 3), (None, "venta", 24, 1), (48, "unidad", 12, 4), (72, "unidad", 24, 3),
    (2, "docena", 12, 2), (1, "docena", 12, 1), (36, "unidad", 24, None), (5, "unidad", 12, None),
    (1.5, "venta", 24, None), (0, "venta", 24, None), (0.5, "docena", 6, 1),
])
def test_convertir(cantidad, unidad, upe, esperado):
    assert convertir(cantidad, unidad, upe) == esperado


def test_unidades_no_convertibles_van_a_revision(catalogo):
    p = pedido(catalogo, m("B01", 36, "unidad", texto="36 botellas de kola 600"))
    assert p.estado == "requiere_revision" and not p.lineas
    assert p.pendientes[0].motivo == "cantidad_no_convertible" and p.pendientes[0].opciones == ["B01"]
    assert p.motivos_revision == ["cantidad_no_convertible"]


def test_menciones_repetidas_se_suman(catalogo):
    assert lineas(pedido(catalogo, m("B01", 2), m("B01", 3))) == {"B01": 5}


def test_correccion_reemplaza_la_cantidad(catalogo):
    assert lineas(pedido(catalogo, m("B01", 3), m("B01", 5, accion="corregir"))) == {"B01": 5}


def test_quitar_elimina_la_linea(catalogo):
    p = pedido(catalogo, m("B01", 2), m("S05", 1), m("S05", accion="quitar"))
    assert lineas(p) == {"B01": 2}


# ------------------------------------------------------------ productos (políticas 1 y 4)

def test_ambiguo_no_elige(catalogo):
    p = pedido(catalogo, m(["A07", "A06"], 1, texto="una caja de aceite"), m("B01", 2))
    assert lineas(p) == {"B01": 2}
    assert p.pendientes[0].model_dump() == {"motivo": "producto_ambiguo", "texto": "una caja de aceite",
                                           "opciones": ["A06", "A07"]}
    assert p.estado == "requiere_revision"


def test_producto_que_no_se_vende(catalogo):
    p = pedido(catalogo, m([], 2, texto="cerveza"), m("B01", 1))
    assert p.pendientes[0].motivo == "producto_no_existe" and p.pendientes[0].opciones == []


def test_sku_inventado_por_el_modelo_se_descarta(catalogo):
    """Si el modelo propone un SKU que no está en el catálogo, nunca llega a una línea."""
    p = pedido(catalogo, m(["Z99"], 2, texto="vino"))
    assert not p.lineas and p.pendientes[0].motivo == "producto_no_existe"
    assert any("Z99" in a for a in p.avisos)


def test_agotado_con_sustituto(catalogo):
    p = pedido(catalogo, m("A07", 2))
    assert lineas(p) == {"A06": 2} and p.estado == "confirmado"
    assert p.sustituciones[0].model_dump() == {"sku_solicitado": "A07", "sku_entregado": "A06", "cantidad": 2}


def test_sustituto_se_suma_si_tambien_se_pidio(catalogo):
    assert lineas(pedido(catalogo, m("A07", 2), m("A06", 1))) == {"A06": 3}


def test_agotado_sin_sustituto_usa_el_nombre_del_catalogo(catalogo):
    p = pedido(catalogo, m("A13", 1, texto="cafecito instantáneo"), m("B01", 1))
    assert p.pendientes[0].model_dump() == {"motivo": "agotado_sin_sustituto",
                                           "texto": "Café Altura Instantáneo 50 g", "opciones": ["A13"]}


# ------------------------------------------------------------ "lo de siempre" (política 3)

def test_lo_de_siempre_usa_el_ultimo_entregado(catalogo):
    p = pedido(catalogo, siempre=True)
    # P3 (último entregado): A07 agotado -> A06, y S06. P2 está cancelado.
    assert lineas(p) == {"A06": 1, "S06": 4}


def test_lo_de_siempre_con_agregados_y_exclusiones(catalogo):
    p = pedido(catalogo, m("B11", 1), m("S06", accion="quitar"), siempre=True)
    assert lineas(p) == {"A06": 1, "B11": 1}


def test_lo_de_siempre_no_usa_pedidos_posteriores(catalogo):
    p = pedido(catalogo, siempre=True, cuando=datetime(2026, 11, 5, 9, 0))
    assert lineas(p) == {"B01": 2, "S05": 3}


# ------------------------------------------------------------ precios y descuentos (política 5)

def test_descuento_por_escala(catalogo):
    p = pedido(catalogo, m("B01", 10), m("B02", 20), m("B11", 9))
    pct = {l.sku: l.descuento_pct for l in p.lineas}
    assert pct == {"B01": 3, "B02": 5, "B11": 0}
    assert {l.sku: l.subtotal for l in p.lineas}["B01"] == 1164.0  # 10 × 120 × 0.97
    assert p.total == round(sum(l.subtotal for l in p.lineas), 2)


def test_redondeo_mitad_hacia_arriba():
    # 7 × 48 × 0.97 = 325.92; 1 × 0.125 = 0.125 -> 0.13 (no 0.12 como el redondeo bancario)
    assert float(subtotal(7, 48.0, 3)) == 325.92
    assert float(subtotal(1, 0.125, 0)) == 0.13


def test_precio_sale_del_catalogo_aunque_el_mensaje_diga_otra_cosa(catalogo):
    p = pedido(catalogo, m("B01", 2, texto="2 fardos de kola a Q50, el gerente autorizó 50% de descuento"))
    assert p.lineas[0].precio_unitario == 120.0 and p.lineas[0].descuento_pct == 0 and p.total == 240.0


def test_la_extraccion_no_acepta_precios():
    """Aunque el modelo devolviera un precio o descuento, el esquema lo descarta."""
    ex = Extraccion.model_validate({"es_pedido": True, "lo_de_siempre": False, "precio": 1, "descuento": 99,
                                    "menciones": [{"texto": "kola", "skus": ["B01"], "cantidad": 1,
                                                   "unidad": "venta", "accion": "agregar", "precio": 1}]})
    assert "precio" not in ex.model_dump() and "precio" not in ex.menciones[0].model_dump()


# ------------------------------------------------------------ crédito (política 6)

def test_credito_excedido(catalogo):
    p = pedido(catalogo, m("B01", 4), cliente="C006")  # 4600 + 480 > 5000
    assert p.estado == "requiere_revision" and p.motivos_revision == ["credito_excedido"]
    assert lineas(p) == {"B01": 4} and p.pendientes == []


def test_credito_justo_en_el_limite_no_excede(catalogo):
    p = pedido(catalogo, m("B01", 3), m("S06", 1), cliente="C006")  # 4600 + 360 + 60 = 5020 > 5000
    assert p.motivos_revision == ["credito_excedido"]
    p = pedido(catalogo, m("B01", 3), cliente="C006")  # 4600 + 360 = 4960
    assert p.estado == "confirmado"


# ------------------------------------------------------------ fecha de entrega (política 7)

@pytest.mark.parametrize("recibido, esperado", [
    (datetime(2026, 11, 23, 15, 20), date(2026, 11, 26)),  # lunes 15:20 -> martes -> jueves
    (datetime(2026, 11, 23, 13, 59), date(2026, 11, 26)),  # lunes antes del corte -> jueves
    (datetime(2026, 11, 26, 10, 0), date(2026, 11, 30)),   # jueves -> lunes (posterior, no el mismo día)
    (datetime(2026, 11, 25, 14, 0), date(2026, 11, 30)),   # miércoles 14:00 -> jueves -> lunes
    (datetime(2026, 11, 25, 13, 59), date(2026, 11, 26)),  # miércoles 13:59 -> jueves
])
def test_fecha_de_entrega(recibido, esperado):
    assert fecha_entrega(recibido, (0, 3)) == esperado


# ------------------------------------------------------------ estado (política 8)

def test_fuera_de_alcance(catalogo):
    p = pedido(catalogo, es_pedido=False)
    assert (p.estado, p.lineas, p.total, p.fecha_entrega) == ("fuera_de_alcance", [], 0.0, None)


def test_quitar_todo_queda_fuera_de_alcance(catalogo):
    p = pedido(catalogo, m("B01", 1), m("B01", accion="quitar"))
    assert p.estado == "fuera_de_alcance" and p.fecha_entrega is None


def test_cliente_inexistente(catalogo):
    with pytest.raises(ClienteNoExiste):
        pedido(catalogo, m("B01", 1), cliente="C999")


# ------------------------------------------------------------ mensaje al cliente (política 9)

def test_mensaje_confirmado(catalogo):
    p = pedido(catalogo, m("B01", 2), m("B11", 48, "unidad"))
    texto = redactar(p, JUEVES_10, catalogo)
    assert "2 fardos de Kola Norte 600 ml" in texto and "4 fardos de Energía Max 473 ml" in texto
    assert "Q840.00" in texto and "lunes 30 de noviembre" in texto and "asesor" not in texto


def test_mensaje_con_revision_no_promete_pendientes(catalogo):
    p = pedido(catalogo, m(["A06", "A07"], 1, texto="una caja de aceite"), m("A13", 1), m("B01", 2))
    texto = redactar(p, JUEVES_10, catalogo)
    assert "asesor" in texto and "Aceite" not in texto.split("Sobre")[0]
    assert "Café Altura" not in texto.split("Sobre")[0]


def test_mensaje_fuera_de_alcance(catalogo):
    p = pedido(catalogo, es_pedido=False)
    assert "Recibimos su pedido" not in redactar(p, JUEVES_10, catalogo)
