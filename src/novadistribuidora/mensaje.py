"""Mensaje de confirmación para el cliente, armado a partir del pedido calculado.

Se arma con una plantilla y no con el modelo de lenguaje: así nunca promete un
producto agotado o pendiente, ni menciona un precio distinto al calculado, y no
agrega latencia ni costo (ver DECISIONES.md).
"""

from __future__ import annotations

from datetime import datetime

from novadistribuidora.catalogo import DIAS, Catalogo
from novadistribuidora.reglas import Pedido

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
PLURALES = {"fardo": "fardos", "caja": "cajas", "display": "displays", "quintal": "quintales"}


def _saludo(recibido: datetime) -> str:
    if recibido.hour < 12:
        return "¡Buenos días!"
    if recibido.hour < 19:
        return "¡Buenas tardes!"
    return "¡Buenas noches!"


def _cantidad(cantidad: int, unidad: str) -> str:
    return f"{cantidad} {unidad if cantidad == 1 else PLURALES.get(unidad, unidad + 's')}"


def _lista(elementos: list[str]) -> str:
    if len(elementos) <= 1:
        return "".join(elementos)
    return ", ".join(elementos[:-1]) + " y " + elementos[-1]


def redactar(pedido: Pedido, recibido: datetime, catalogo: Catalogo) -> str:
    saludo = _saludo(recibido)
    if pedido.estado == "fuera_de_alcance":
        return f"{saludo} Gracias por escribirnos. Un asesor le responderá en breve."

    partes = [saludo]
    if pedido.lineas:
        items = [f"{_cantidad(l.cantidad, catalogo.productos[l.sku].unidad_venta)} de "
                 f"{catalogo.productos[l.sku].nombre}" for l in pedido.lineas]
        partes.append(f"Recibimos su pedido: {_lista(items)}, por un total de Q{pedido.total:,.2f}.")
    else:
        partes.append("Recibimos su mensaje.")

    for s in pedido.sustituciones:
        partes.append(f"{catalogo.productos[s.sku_solicitado].nombre} está agotado; "
                      f"le enviamos {catalogo.productos[s.sku_entregado].nombre} en su lugar.")

    if pedido.pendientes:
        textos = _lista([f"«{p.texto}»" for p in pedido.pendientes])
        partes.append(f"Sobre {textos}, un asesor le confirmará.")
    if "credito_excedido" in pedido.motivos_revision:
        partes.append("Un asesor revisará su crédito antes de enviar el pedido.")
    elif pedido.motivos_revision and not pedido.pendientes:
        partes.append("Un asesor confirmará su pedido.")

    if pedido.fecha_entrega is not None and pedido.lineas:
        f = pedido.fecha_entrega
        verbo = "Entrega estimada" if pedido.motivos_revision else "Entrega"
        partes.append(f"{verbo}: {DIAS[f.weekday()]} {f.day} de {MESES[f.month - 1]}.")
    partes.append("¡Gracias!")
    return " ".join(partes)
