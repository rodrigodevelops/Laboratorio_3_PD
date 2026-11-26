"""Servicio HTTP según el contrato del Anexo.

    uvicorn novadistribuidora.api:app --host 0.0.0.0 --port 8000

- ``POST /v1/pedidos/interpretar``: 200 con el pedido; 404 si el cliente no existe;
  422 si la solicitud es inválida, con un mensaje que explica el motivo; 503 si el
  modelo de lenguaje no responde (el servicio no inventa un pedido).
- ``GET /health``: ``ok``, o ``degradado`` con el motivo (MLflow caído, falta configuración).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from novadistribuidora.asistente import Asistente, ClienteNoExiste
from novadistribuidora.catalogo import Catalogo
from novadistribuidora.esquemas import RespuestaPedido, SolicitudInterpretar
from novadistribuidora.extraccion import ErrorExtraccion, Extractor
from novadistribuidora.observabilidad import Observador, etiquetar, span

log = logging.getLogger("novadistribuidora")

_MENSAJES = {
    "missing": "es obligatorio",
    "too_short": "no puede estar vacío",
    "string_type": "debe ser texto",
    "list_type": "debe ser una lista de textos",
    "dict_type": "el cuerpo debe ser un objeto JSON",
    "model_attributes_type": "el cuerpo debe ser un objeto JSON",
    "json_invalid": "el cuerpo no es un JSON válido",
}


def explicar(errores: list[dict]) -> tuple[str, list[dict]]:
    """Convierte los errores de validación en un mensaje legible en español."""
    detalle = []
    for e in errores:
        campo = ".".join(str(x) for x in e.get("loc", []) if x != "body") or "cuerpo"
        mensaje = _MENSAJES.get(e.get("type", "")) or str(e.get("msg", "")).removeprefix("Value error, ")
        detalle.append({"campo": campo, "mensaje": mensaje})
    texto = "Solicitud inválida: " + "; ".join(f"{d['campo']} {d['mensaje']}" for d in detalle)
    return texto, detalle


def _crear_asistente() -> tuple[Asistente | None, str | None]:
    """Arma el asistente con los datos y el .env. Si falla, la API arranca igual y lo reporta."""
    try:
        catalogo = Catalogo.desde_carpeta()
    except Exception as ex:
        return None, f"no se pudieron leer los datos publicados ({type(ex).__name__}: {ex})"
    if not os.environ.get("OPENROUTER_API_KEY"):
        return None, "falta OPENROUTER_API_KEY en el .env"
    try:
        return Asistente(catalogo, Extractor(catalogo)), None
    except Exception as ex:
        return None, f"no se pudo configurar el modelo ({type(ex).__name__}: {ex})"


def crear_app(asistente: Asistente | None = None, observador: Observador | None = None) -> FastAPI:
    """``asistente`` y ``observador`` se inyectan en las pruebas (modelo falso, sin MLflow)."""
    estado: dict = {"asistente": asistente, "error": None, "observador": observador}

    @asynccontextmanager
    async def ciclo(_app: FastAPI):
        from dotenv import load_dotenv

        load_dotenv()  # fuera de Docker; en Docker las variables llegan por env_file
        if estado["asistente"] is None:
            estado["asistente"], estado["error"] = _crear_asistente()
            if estado["error"]:
                log.error("el servicio arrancó sin asistente: %s", estado["error"])
        if estado["observador"] is None:
            estado["observador"] = Observador()
        estado["observador"].iniciar()
        yield
        estado["observador"].detener()

    app = FastAPI(title="NovaDistribuidora: toma de pedidos", version="1.0", lifespan=ciclo)

    @app.exception_handler(RequestValidationError)
    async def _invalida(_request: Request, exc: RequestValidationError):
        texto, detalle = explicar(exc.errors())
        return JSONResponse(status_code=422, content={"detail": texto, "errores": detalle})

    @app.post("/v1/pedidos/interpretar", response_model=RespuestaPedido)
    def interpretar(solicitud: SolicitudInterpretar):
        # Función síncrona: FastAPI la ejecuta en un hilo y atiende otras solicitudes mientras tanto.
        a: Asistente | None = estado["asistente"]
        entradas = {"cliente_id": solicitud.cliente_id,
                    "fecha_hora": solicitud.fecha_hora.strftime("%Y-%m-%dT%H:%M"),
                    "mensajes": solicitud.mensajes}
        if a is None:
            return JSONResponse(status_code=503, content={"detail": f"Servicio no disponible: {estado['error']}"})
        try:
            with span("interpretar_pedido", "CHAIN", entradas) as s:
                etiquetar(cliente_id=solicitud.cliente_id, modelo=a.extractor.modelo, variante=a.extractor.variante)
                r = a.interpretar(solicitud.cliente_id, solicitud.fecha_hora, solicitud.mensajes)
                respuesta = r.respuesta.model_dump(mode="json")
                if s is not None:
                    s.set_outputs(respuesta)
                    s.set_attributes({
                        "modelo": r.extraccion.modelo, "latencia_modelo_s": round(r.extraccion.latencia_s, 3),
                        "tokens_entrada": r.extraccion.tokens_entrada, "tokens_salida": r.extraccion.tokens_salida,
                        "costo_usd": r.extraccion.costo_usd or 0.0, "avisos": r.avisos,
                    })
                etiquetar(estado=respuesta["estado"])
            return respuesta
        except ClienteNoExiste:
            return JSONResponse(status_code=404, content={"detail": f"El cliente {solicitud.cliente_id} no existe"})
        except ErrorExtraccion as ex:
            log.warning("falló la extracción: %s", ex)
            return JSONResponse(status_code=503, content={
                "detail": "No se pudo interpretar el pedido porque el modelo de lenguaje no respondió "
                          "correctamente. Intente de nuevo o páselo a un asesor."})

    @app.get("/health")
    def health():
        problemas = []
        if estado["error"]:
            problemas.append(estado["error"])
        obs: Observador | None = estado["observador"]
        if obs is not None and obs.motivo():
            problemas.append(obs.motivo())
        cuerpo = {"estado": "degradado" if problemas else "ok"}
        if problemas:
            cuerpo["motivo"] = "; ".join(problemas)
        a = estado["asistente"]
        cuerpo["modelo"] = a.extractor.modelo if a else None
        cuerpo["mlflow"] = "disponible" if obs is not None and obs.disponible else "no disponible"
        return cuerpo

    return app


app = crear_app()
