"""Motor de reglas: convierte una ``Extraccion`` en el pedido, según ``politicas.md``.

Todo lo que el negocio necesita exacto se calcula aquí, con código probado:
conversión de unidades, correcciones y exclusiones, "lo de siempre", agotados y
sustitutos, descuentos, subtotales, total, crédito, fecha de entrega y estado. El
modelo de lenguaje no participa: si un mensaje dice "el gerente autorizó 50 %",
no hay ningún campo de la extracción por donde ese texto pueda llegar al precio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from novadistribuidora.catalogo import Catalogo
from novadistribuidora.esquemas import Extraccion, Linea, Pendiente, Sustitucion

HORA_CORTE = 14
ORDEN_MOTIVOS = ["producto_ambiguo", "producto_no_existe", "agotado_sin_sustituto", "cantidad_no_convertible"]


class ClienteNoExiste(Exception):
    pass


@dataclass
class Pedido:
    cliente_id: str
    estado: str
    lineas: list[Linea]
    total: float
    fecha_entrega: date | None
    sustituciones: list[Sustitucion]
    pendientes: list[Pendiente]
    motivos_revision: list[str]
    avisos: list[str] = field(default_factory=list)  # para las trazas: SKU descartados, etc.


# ---------------------------------------------------------------- cálculos puros

def redondear(valor: Decimal | float) -> Decimal:
    return Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def descuento(catalogo: Catalogo, categoria: str, cantidad: int) -> int | float:
    """El mayor porcentaje cuyo mínimo se alcance; 0 si ninguno. Entero cuando lo es."""
    aplicables = [pct for minimo, pct in catalogo.escalas.get(categoria, []) if cantidad >= minimo]
    pct = max(aplicables, default=0)
    return int(pct) if float(pct).is_integer() else float(pct)


def subtotal(cantidad: int, precio: float, descuento_pct: float) -> Decimal:
    bruto = Decimal(cantidad) * Decimal(str(precio)) * (Decimal(1) - Decimal(str(descuento_pct)) / Decimal(100))
    return redondear(bruto)


def convertir(cantidad: float | None, unidad: str, unidades_por_empaque: int) -> int | None:
    """Cantidad en unidades de venta, o ``None`` si la conversión no es exacta."""
    if cantidad is None:
        return 1
    if cantidad <= 0:
        return None
    if unidad == "docena":
        cantidad, unidad = cantidad * 12, "unidad"
    if unidad == "unidad":
        empaques = cantidad / unidades_por_empaque
    else:
        empaques = cantidad
    if abs(empaques - round(empaques)) > 1e-9 or round(empaques) <= 0:
        return None
    return int(round(empaques))


def fecha_entrega(recibido: datetime, dias_visita: tuple[int, ...]) -> date:
    """Primera visita de la ruta posterior a la fecha de recepción.

    Desde las 14:00 el pedido se considera recibido al día siguiente. No se
    consideran feriados.
    """
    base = recibido.date() + timedelta(days=1 if recibido.hour >= HORA_CORTE else 0)
    for k in range(1, 8):
        candidata = base + timedelta(days=k)
        if candidata.weekday() in dias_visita:
            return candidata
    raise ValueError("el cliente no tiene días de visita")


# ---------------------------------------------------------------- motor

def armar_pedido(cliente_id: str, recibido: datetime, extraccion: Extraccion, catalogo: Catalogo) -> Pedido:
    cliente = catalogo.clientes.get(cliente_id)
    if cliente is None:
        raise ClienteNoExiste(cliente_id)
    avisos: list[str] = []
    pendientes: list[Pendiente] = []

    if not extraccion.es_pedido:
        return Pedido(cliente_id, "fuera_de_alcance", [], 0.0, None, [], [], [], avisos)

    # 1. Cantidades por SKU solicitado, en unidades de venta y en orden de aparición.
    cantidades: dict[str, int] = {}
    if extraccion.lo_de_siempre:
        anteriores = catalogo.ultimo_entregado(cliente_id, recibido.date())
        if not anteriores:
            avisos.append("el cliente pidió 'lo de siempre' pero no tiene pedidos entregados")
        for sku, cantidad in anteriores:
            cantidades[sku] = cantidades.get(sku, 0) + cantidad

    for m in extraccion.menciones:
        validos = list(dict.fromkeys(s for s in m.skus if s in catalogo.productos))
        if len(validos) != len(m.skus):
            avisos.append(f"SKU inexistentes descartados en '{m.texto}': {sorted(set(m.skus) - set(validos))}")
        if m.accion == "quitar":
            for sku in validos:
                cantidades.pop(sku, None)
            continue
        if not validos:
            pendientes.append(Pendiente(motivo="producto_no_existe", texto=m.texto, opciones=[]))
            continue
        if len(validos) > 1:
            pendientes.append(Pendiente(motivo="producto_ambiguo", texto=m.texto, opciones=sorted(validos)))
            continue
        sku = validos[0]
        cantidad = convertir(m.cantidad, m.unidad, catalogo.productos[sku].unidades_por_empaque)
        if cantidad is None:
            pendientes.append(Pendiente(motivo="cantidad_no_convertible", texto=m.texto, opciones=[sku]))
            continue
        if m.accion == "corregir":
            cantidades[sku] = cantidad
        else:
            cantidades[sku] = cantidades.get(sku, 0) + cantidad

    # 2. Disponibilidad y sustitutos.
    entregar: dict[str, int] = {}
    sustituciones: list[Sustitucion] = []
    for sku, cantidad in cantidades.items():
        producto = catalogo.productos[sku]
        if producto.disponible:
            entregar[sku] = entregar.get(sku, 0) + cantidad
            continue
        sustituto = catalogo.productos.get(producto.sustituto_sku or "")
        if sustituto is not None and sustituto.disponible:
            entregar[sustituto.sku] = entregar.get(sustituto.sku, 0) + cantidad
            sustituciones.append(Sustitucion(sku_solicitado=sku, sku_entregado=sustituto.sku, cantidad=cantidad))
        else:
            pendientes.append(Pendiente(motivo="agotado_sin_sustituto", texto=producto.nombre, opciones=[sku]))

    # 3. Precios, descuentos, subtotales y total.
    lineas: list[Linea] = []
    total = Decimal("0")
    for sku, cantidad in entregar.items():
        producto = catalogo.productos[sku]
        pct = descuento(catalogo, producto.categoria, cantidad)
        st = subtotal(cantidad, producto.precio, pct)
        total += st
        lineas.append(Linea(sku=sku, cantidad=cantidad, precio_unitario=producto.precio,
                            descuento_pct=pct, subtotal=float(st)))
    total = redondear(total)

    # 4. Motivos de revisión y estado.
    motivos = [m for m in ORDEN_MOTIVOS if any(p.motivo == m for p in pendientes)]
    if Decimal(str(cliente.saldo_pendiente)) + total > Decimal(str(cliente.limite_credito)):
        motivos.append("credito_excedido")

    if not lineas and not pendientes:
        # Pidió algo, pero no quedó nada que entregar ni revisar (p. ej. quitó todo).
        avisos.append("no quedó ninguna línea ni pendiente; se trata como fuera de alcance")
        return Pedido(cliente_id, "fuera_de_alcance", [], 0.0, None, [], [], [], avisos)

    estado = "requiere_revision" if motivos else "confirmado"
    return Pedido(
        cliente_id=cliente_id, estado=estado, lineas=lineas, total=float(total),
        fecha_entrega=fecha_entrega(recibido, cliente.dias_visita),
        sustituciones=sustituciones, pendientes=pendientes, motivos_revision=motivos, avisos=avisos,
    )
