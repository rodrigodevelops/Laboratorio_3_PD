"""Catálogo de prueba con la misma estructura que los archivos publicados (que no están en el repo)."""

import pandas as pd
import pytest

from novadistribuidora.catalogo import Catalogo


@pytest.fixture(scope="session")
def catalogo():
    productos = pd.DataFrame([
        # sku, nombre, marca, categoria, presentacion, unidad_venta, upe, precio, disponible, sustituto
        ("B01", "Kola Norte 600 ml", "Kola Norte", "bebidas", "600 ml", "fardo", 24, 120.0, True, None),
        ("B02", "Kola Norte 1.5 L", "Kola Norte", "bebidas", "1.5 L", "fardo", 12, 144.0, True, None),
        ("B03", "Kola Norte 3 L", "Kola Norte", "bebidas", "3 L", "fardo", 6, 132.0, False, "B02"),
        ("B11", "Energía Max 473 ml", "Energía Max", "bebidas", "473 ml", "fardo", 12, 150.0, True, None),
        ("A06", "Aceite Girasol Dorado 750 ml", "Dorado", "abarrotes", "750 ml", "caja", 12, 228.0, True, None),
        ("A07", "Aceite Palma Real 750 ml", "Palma Real", "abarrotes", "750 ml", "caja", 12, 192.0, False, "A06"),
        ("A13", "Café Altura Instantáneo 50 g", "Altura", "abarrotes", "50 g", "caja", 24, 360.0, False, None),
        ("L03", "Cloro Blancura 1 L", "Blancura", "limpieza", "1 L", "caja", 12, 108.0, True, None),
        ("S05", "Papitas Doradas 40 g", "Doradas", "snacks", "12 bolsitas", "display", 12, 48.0, True, None),
        ("S06", "Maní Salado La Finca 50 g", "La Finca", "snacks", "24 bolsitas", "display", 24, 60.0, True, None),
    ], columns=["sku", "nombre", "marca", "categoria", "presentacion", "unidad_venta",
                "unidades_por_empaque", "precio", "disponible", "sustituto_sku"])
    sinonimos = pd.DataFrame({"sku": ["B01", "B11"], "alias": ["kola 600", "energia max"]})
    escalas = pd.DataFrame({
        "categoria": ["bebidas", "bebidas", "abarrotes", "abarrotes", "limpieza", "snacks"],
        "cantidad_minima": [10, 20, 5, 10, 6, 10],
        "descuento_pct": [3, 5, 2, 4, 3, 3],
    })
    clientes = pd.DataFrame({
        "cliente_id": ["C001", "C006"], "nombre_tienda": ["La Bendición", "El Milagro"],
        "propietario": ["Marta", "Ana"], "municipio": ["Mixco", "Villa Canales"],
        "dias_visita": ["lunes,jueves", "sábado"], "limite_credito": [8000, 5000], "saldo_pendiente": [1200, 4600],
    })
    historial = pd.DataFrame({
        "pedido_id": ["P1", "P1", "P2", "P3", "P3"],
        "cliente_id": ["C001"] * 5,
        "fecha_entrega": pd.to_datetime(["2026-11-02", "2026-11-02", "2026-11-09", "2026-11-16", "2026-11-16"]),
        "sku": ["B01", "S05", "L03", "A07", "S06"],
        "cantidad": [2, 3, 9, 1, 4],
        "estado": ["entregado", "entregado", "entregado", "entregado", "entregado"],
    })
    historial.loc[historial["pedido_id"] == "P2", "estado"] = "cancelado"
    return Catalogo(productos, sinonimos, escalas, clientes, historial)
