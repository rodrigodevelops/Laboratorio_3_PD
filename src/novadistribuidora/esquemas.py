"""Modelos de datos: lo que entrega el modelo de lenguaje y el contrato de la API.

El modelo de lenguaje **solo** produce una ``Extraccion``: qué se pidió, cuánto y en
qué unidad. No produce precios, descuentos, totales, fechas ni estados: esos los
calcula ``reglas.py``. Por eso ningún mensaje puede alterarlos.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Motivo = Literal["producto_ambiguo", "producto_no_existe", "agotado_sin_sustituto",
                 "cantidad_no_convertible", "credito_excedido"]
Estado = Literal["confirmado", "requiere_revision", "fuera_de_alcance"]


# ---------------------------------------------------------------- salida del modelo de lenguaje

class Mencion(BaseModel):
    """Un producto mencionado por el cliente, tal como lo entendió el modelo."""

    texto: str = Field(description="Fragmento del mensaje que menciona el producto, tal como lo escribió el cliente.")
    skus: list[str] = Field(
        description="SKU del catálogo a los que puede referirse. Uno si es inequívoco; todos los posibles si es "
                    "ambiguo; lista vacía si la distribuidora no vende ese producto.")
    cantidad: float | None = Field(
        description="Número que dijo el cliente (en dígitos), o null si no dijo cantidad.")
    unidad: Literal["venta", "unidad", "docena"] = Field(
        description="'venta' si habla de fardos, cajas, displays, quintales o no dice unidad; 'unidad' si pide "
                    "unidades sueltas (latas, botellas, bolsitas, sobres, libras); 'docena' si pide docenas.")
    accion: Literal["agregar", "corregir", "quitar"] = Field(
        description="'agregar' por defecto; 'corregir' si el cliente cambia la cantidad que había dicho antes "
                    "('no, mejor que sean 5'); 'quitar' si pide eliminar el producto ('sin las papitas').")


class Extraccion(BaseModel):
    es_pedido: bool = Field(description="false si los mensajes no piden productos (consultas, saludos, reclamos).")
    lo_de_siempre: bool = Field(description="true si el cliente pide 'lo de siempre', 'lo mismo de la otra vez' o similar.")
    menciones: list[Mencion] = Field(description="Productos mencionados, en el orden en que aparecen.")


# ---------------------------------------------------------------- contrato de la API

class SolicitudInterpretar(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cliente_id: str = Field(min_length=1)
    fecha_hora: datetime
    mensajes: list[str] = Field(min_length=1)

    @field_validator("fecha_hora", mode="before")
    @classmethod
    def _formato(cls, v):
        if not isinstance(v, str):
            raise ValueError("debe ser texto con formato AAAA-MM-DDTHH:MM")
        try:
            return datetime.strptime(v.strip(), "%Y-%m-%dT%H:%M")
        except ValueError:
            raise ValueError("debe tener el formato AAAA-MM-DDTHH:MM (hora de Guatemala)") from None

    @field_validator("mensajes")
    @classmethod
    def _no_vacios(cls, v: list[str]):
        if not any(m.strip() for m in v):
            raise ValueError("no puede estar vacío")
        return v


class Linea(BaseModel):
    sku: str
    cantidad: int = Field(gt=0)
    precio_unitario: float
    descuento_pct: int | float  # entero cuando lo es (0, 3, 5), como en el contrato
    subtotal: float


class Sustitucion(BaseModel):
    sku_solicitado: str
    sku_entregado: str
    cantidad: int


class Pendiente(BaseModel):
    motivo: Motivo
    texto: str
    opciones: list[str] = []


class RespuestaPedido(BaseModel):
    cliente_id: str
    estado: Estado
    lineas: list[Linea]
    total: float
    fecha_entrega: date | None
    sustituciones: list[Sustitucion]
    pendientes: list[Pendiente]
    motivos_revision: list[Motivo]
    mensaje_cliente: str
