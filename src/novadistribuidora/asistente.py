"""El asistente completo: extracción (modelo) -> pedido (reglas) -> mensaje (plantilla)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from novadistribuidora.catalogo import Catalogo
from novadistribuidora.esquemas import RespuestaPedido
from novadistribuidora.extraccion import Extractor, ResultadoExtraccion
from novadistribuidora.mensaje import redactar
from novadistribuidora.reglas import ClienteNoExiste, armar_pedido

__all__ = ["Asistente", "ClienteNoExiste", "Interpretacion"]


@dataclass
class Interpretacion:
    respuesta: RespuestaPedido
    extraccion: ResultadoExtraccion
    avisos: list[str]


class Asistente:
    def __init__(self, catalogo: Catalogo, extractor: Extractor):
        self.catalogo, self.extractor = catalogo, extractor

    def interpretar(self, cliente_id: str, recibido: datetime, mensajes: list[str]) -> Interpretacion:
        if cliente_id not in self.catalogo.clientes:
            raise ClienteNoExiste(cliente_id)  # antes de gastar una llamada al modelo
        resultado = self.extractor.extraer(mensajes)
        pedido = armar_pedido(cliente_id, recibido, resultado.extraccion, self.catalogo)
        respuesta = RespuestaPedido(
            cliente_id=cliente_id, estado=pedido.estado, lineas=pedido.lineas, total=pedido.total,
            fecha_entrega=pedido.fecha_entrega, sustituciones=pedido.sustituciones,
            pendientes=pedido.pendientes, motivos_revision=pedido.motivos_revision,
            mensaje_cliente=redactar(pedido, recibido, self.catalogo),
        )
        return Interpretacion(respuesta, resultado, pedido.avisos)
