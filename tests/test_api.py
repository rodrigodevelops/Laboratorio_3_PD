"""Pruebas del contrato HTTP con un modelo falso: no llaman a OpenRouter ni necesitan MLflow."""

import pytest
from fastapi.testclient import TestClient

from novadistribuidora.api import crear_app, explicar
from novadistribuidora.asistente import Asistente
from novadistribuidora.extraccion import Extractor
from novadistribuidora.observabilidad import Observador
from test_extraccion import KOLA_Y_ACEITE, ModeloFalso

SOLICITUD = {"cliente_id": "C001", "fecha_hora": "2026-11-23T15:20",
             "mensajes": ["Buenas, mándeme 2 fardos de kola 600, una caja de aceite", "y 48 latas de energía max"]}
NO_PEDIDO = {"es_pedido": False, "lo_de_siempre": False, "menciones": []}


class ObservadorFijo(Observador):
    """Simula MLflow arriba o abajo sin red."""

    def __init__(self, responde):
        super().__init__(uri="http://mlflow:5000")
        self._fijo = responde

    def sondear(self):
        self.disponible = self._fijo
        return self._fijo

    def iniciar(self):
        self.sondear()


def cliente(catalogo, salidas, mlflow_arriba=True):
    modelo = ModeloFalso(salidas)
    app = crear_app(Asistente(catalogo, Extractor(catalogo, modelo="falso/modelo", llm=modelo)),
                    ObservadorFijo(mlflow_arriba))
    return TestClient(app), modelo


def test_respuesta_cumple_el_contrato(catalogo):
    with cliente(catalogo, [KOLA_Y_ACEITE])[0] as c:
        r = c.post("/v1/pedidos/interpretar", json=SOLICITUD)
    assert r.status_code == 200
    d = r.json()
    assert list(d) == ["cliente_id", "estado", "lineas", "total", "fecha_entrega", "sustituciones",
                       "pendientes", "motivos_revision", "mensaje_cliente"]
    assert d["estado"] == "requiere_revision" and d["total"] == 840.0 and d["fecha_entrega"] == "2026-11-26"
    assert d["lineas"][0] == {"sku": "B01", "cantidad": 2, "precio_unitario": 120.0, "descuento_pct": 0, "subtotal": 240.0}
    assert d["pendientes"] == [{"motivo": "producto_ambiguo", "texto": "una caja de aceite", "opciones": ["A06", "A07"]}]
    assert d["motivos_revision"] == ["producto_ambiguo"] and d["mensaje_cliente"]


def test_fuera_de_alcance(catalogo):
    with cliente(catalogo, [NO_PEDIDO])[0] as c:
        d = c.post("/v1/pedidos/interpretar", json={**SOLICITUD, "mensajes": ["¿a qué hora pasa el camión?"]}).json()
    assert d["estado"] == "fuera_de_alcance" and d["lineas"] == [] and d["total"] == 0 and d["fecha_entrega"] is None


def test_cliente_inexistente_404_sin_llamar_al_modelo(catalogo):
    c, modelo = cliente(catalogo, [KOLA_Y_ACEITE])
    with c:
        r = c.post("/v1/pedidos/interpretar", json={**SOLICITUD, "cliente_id": "C999"})
    assert r.status_code == 404 and "C999" in r.json()["detail"]
    assert modelo.llamadas == []


@pytest.mark.parametrize("cuerpo, campo", [
    ({k: v for k, v in SOLICITUD.items() if k != "cliente_id"}, "cliente_id es obligatorio"),
    ({k: v for k, v in SOLICITUD.items() if k != "mensajes"}, "mensajes es obligatorio"),
    ({**SOLICITUD, "mensajes": []}, "mensajes no puede estar vacío"),
    ({**SOLICITUD, "mensajes": ["  ", ""]}, "mensajes no puede estar vacío"),
    ({**SOLICITUD, "mensajes": "hola"}, "mensajes debe ser una lista"),
    ({**SOLICITUD, "mensajes": [3]}, "mensajes.0 debe ser texto"),
    ({**SOLICITUD, "fecha_hora": "23/11/2026 15:20"}, "fecha_hora debe tener el formato AAAA-MM-DDTHH:MM"),
    ({**SOLICITUD, "fecha_hora": "2026-11-31T10:00"}, "fecha_hora debe tener el formato"),
    ({**SOLICITUD, "fecha_hora": 20261123}, "fecha_hora debe ser texto"),
    ({**SOLICITUD, "cliente_id": ""}, "cliente_id"),
    ({**SOLICITUD, "cliente_id": 1}, "cliente_id debe ser texto"),
    ([], "el cuerpo debe ser un objeto JSON"),
])
def test_solicitud_invalida_422_con_motivo(catalogo, cuerpo, campo):
    c, modelo = cliente(catalogo, [])
    with c:
        r = c.post("/v1/pedidos/interpretar", json=cuerpo)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("Solicitud inválida") and campo in r.json()["detail"]
    assert modelo.llamadas == []


def test_json_malformado_422(catalogo):
    with cliente(catalogo, [])[0] as c:
        r = c.post("/v1/pedidos/interpretar", content=b'{"cliente_id": "C001",', headers={"content-type": "application/json"})
    assert r.status_code == 422 and "JSON" in r.json()["detail"]


def test_falla_del_modelo_503_sin_inventar_pedido(catalogo):
    with cliente(catalogo, [ValueError("salida inválida")])[0] as c:
        r = c.post("/v1/pedidos/interpretar", json=SOLICITUD)
    assert r.status_code == 503 and "asesor" in r.json()["detail"]


def test_health_ok_y_degradado(catalogo):
    with cliente(catalogo, [], mlflow_arriba=True)[0] as c:
        assert c.get("/health").json()["estado"] == "ok"
    with cliente(catalogo, [KOLA_Y_ACEITE], mlflow_arriba=False)[0] as c:
        h = c.get("/health")
        assert h.status_code == 200 and h.json()["estado"] == "degradado" and "MLflow" in h.json()["motivo"]
        assert c.post("/v1/pedidos/interpretar", json=SOLICITUD).status_code == 200  # sigue atendiendo


def test_sin_clave_arranca_degradado(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVA_DATOS", str(tmp_path))  # carpeta sin datos
    monkeypatch.chdir(tmp_path)  # sin .env
    with TestClient(crear_app(observador=ObservadorFijo(True))) as c:
        h = c.get("/health").json()
        assert h["estado"] == "degradado" and "datos" in h["motivo"]
        assert c.post("/v1/pedidos/interpretar", json=SOLICITUD).status_code == 503


def test_explicar_traduce_errores():
    texto, detalle = explicar([{"loc": ("body", "mensajes"), "type": "too_short", "msg": "x"}])
    assert texto == "Solicitud inválida: mensajes no puede estar vacío" and detalle[0]["campo"] == "mensajes"
