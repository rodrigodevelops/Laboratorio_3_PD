"""Datos publicados: productos, sinónimos, descuentos por cantidad, clientes e historial."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_SIN_TILDE = {"miercoles": "miércoles", "sabado": "sábado"}


@dataclass(frozen=True)
class Producto:
    sku: str
    nombre: str
    categoria: str
    presentacion: str
    unidad_venta: str
    unidades_por_empaque: int
    precio: float
    disponible: bool
    sustituto_sku: str | None


@dataclass(frozen=True)
class Cliente:
    cliente_id: str
    nombre_tienda: str
    propietario: str
    dias_visita: tuple[int, ...]  # 0 = lunes
    limite_credito: float
    saldo_pendiente: float


class Catalogo:
    def __init__(self, productos: pd.DataFrame, sinonimos: pd.DataFrame, escalas: pd.DataFrame,
                 clientes: pd.DataFrame, historial: pd.DataFrame):
        self.productos: dict[str, Producto] = {
            r.sku: Producto(
                sku=r.sku, nombre=r.nombre, categoria=r.categoria, presentacion=r.presentacion,
                unidad_venta=r.unidad_venta, unidades_por_empaque=int(r.unidades_por_empaque),
                precio=float(r.precio), disponible=bool(r.disponible),
                sustituto_sku=None if pd.isna(r.sustituto_sku) else str(r.sustituto_sku),
            )
            for r in productos.itertuples(index=False)
        }
        self.sinonimos: dict[str, list[str]] = {}
        for r in sinonimos.itertuples(index=False):
            self.sinonimos.setdefault(r.sku, []).append(r.alias)
        self.escalas: dict[str, list[tuple[int, float]]] = {}
        for r in escalas.itertuples(index=False):
            self.escalas.setdefault(r.categoria, []).append((int(r.cantidad_minima), float(r.descuento_pct)))
        self.clientes: dict[str, Cliente] = {
            r.cliente_id: Cliente(
                cliente_id=r.cliente_id, nombre_tienda=r.nombre_tienda, propietario=r.propietario,
                dias_visita=tuple(sorted(DIAS.index(_SIN_TILDE.get(d.strip().lower(), d.strip().lower()))
                                         for d in str(r.dias_visita).split(","))),
                limite_credito=float(r.limite_credito), saldo_pendiente=float(r.saldo_pendiente),
            )
            for r in clientes.itertuples(index=False)
        }
        h = historial.copy()
        h["fecha_entrega"] = pd.to_datetime(h["fecha_entrega"]).dt.date
        self.historial = h

    @classmethod
    def desde_carpeta(cls, carpeta: str | Path | None = None) -> "Catalogo":
        d = Path(carpeta or os.environ.get("NOVA_DATOS", "publicados/datos"))
        leer = lambda n: pd.read_parquet(d / f"{n}.parquet")  # noqa: E731
        return cls(leer("productos"), leer("sinonimos"), leer("precios_escala"),
                   leer("clientes"), leer("historial_pedidos"))

    def ultimo_entregado(self, cliente_id: str, antes_de: date | None = None) -> list[tuple[str, int]]:
        """Líneas del último pedido con estado ``entregado`` (los cancelados no cuentan)."""
        h = self.historial[(self.historial["cliente_id"] == cliente_id) & (self.historial["estado"] == "entregado")]
        if antes_de is not None:
            h = h[h["fecha_entrega"] <= antes_de]
        if h.empty:
            return []
        ultimo = h.sort_values(["fecha_entrega", "pedido_id"]).iloc[-1]["pedido_id"]
        filas = h[h["pedido_id"] == ultimo]
        return [(str(r.sku), int(r.cantidad)) for r in filas.itertuples(index=False)]
