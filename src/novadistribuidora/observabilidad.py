"""Trazas en MLflow que nunca bloquean al servicio.

Un hilo consulta ``/health`` de MLflow cada pocos segundos (timeout de 1 s).

- Si MLflow responde, se activan las trazas: ``mlflow.langchain.autolog()`` registra
  la llamada al modelo (instrucciones, respuesta, tokens) y ``span()`` agrega los
  pasos propios (la solicitud completa y las reglas del negocio).
- Si no responde, las trazas se apagan y la API sigue atendiendo sin esperar a
  MLflow; ``GET /health`` lo reporta como ``degradado``. Cuando MLflow vuelve, las
  trazas se reactivan solas.

Las trazas se envían en segundo plano (``MLFLOW_ENABLE_ASYNC_TRACE_LOGGING``), así
que ni siquiera una caída entre dos consultas agrega latencia a una solicitud.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.request
from contextlib import contextmanager

log = logging.getLogger(__name__)

_activo = False  # lo cambia el Observador; span() lo consulta en cada solicitud


@contextmanager
def span(nombre: str, tipo: str = "UNKNOWN", entradas: dict | None = None):
    """Un span de MLflow si las trazas están activas; si no, no hace nada (``None``)."""
    if not _activo:
        yield None
        return
    import mlflow

    with mlflow.start_span(name=nombre, span_type=tipo) as s:
        if entradas is not None:
            s.set_inputs(entradas)
        yield s


def etiquetar(**etiquetas) -> None:
    """Etiquetas de la traza actual (modelo, estado...), para filtrar en la interfaz de MLflow."""
    if _activo:
        import mlflow

        try:
            mlflow.update_current_trace(tags={k: str(v) for k, v in etiquetas.items()})
        except Exception:  # una etiqueta nunca debe tumbar una solicitud
            log.debug("no se pudo etiquetar la traza", exc_info=True)


class Observador:
    def __init__(self, uri: str | None = None, experimento: str | None = None,
                 intervalo: float = 5.0, timeout: float = 1.0):
        self.uri = (uri if uri is not None else os.environ.get("MLFLOW_TRACKING_URI", "")).rstrip("/")
        self.experimento = experimento or os.environ.get("NOVA_EXPERIMENTO", "novadistribuidora-pedidos")
        self.intervalo, self.timeout = intervalo, timeout
        self.disponible = False
        self.configurado = False
        self._alto = threading.Event()
        self._hilo: threading.Thread | None = None

    # ------------------------------------------------------------ estado
    def motivo(self) -> str | None:
        if not self.uri:
            return "MLflow no está configurado (falta MLFLOW_TRACKING_URI): no se registran trazas"
        if not self.disponible:
            return f"MLflow no responde en {self.uri}: la API funciona, pero sin registrar trazas"
        return None

    def _responde(self) -> bool:
        if not self.uri.startswith("http"):
            return False
        try:
            with urllib.request.urlopen(f"{self.uri}/health", timeout=self.timeout) as r:
                return r.status == 200
        except Exception:
            return False

    def sondear(self) -> bool:
        """Consulta MLflow y enciende o apaga las trazas según responda."""
        global _activo
        responde = self._responde()
        if responde and not self.configurado:
            responde = self._configurar()
        if responde != self.disponible:
            log.warning("MLflow %s", "disponible: trazas activas" if responde else "no disponible: trazas en pausa")
            if self.configurado:
                import mlflow

                (mlflow.tracing.enable if responde else mlflow.tracing.disable)()
        self.disponible = responde
        _activo = responde and self.configurado
        return responde

    def _configurar(self) -> bool:
        try:
            import mlflow

            mlflow.set_tracking_uri(self.uri)
            mlflow.set_experiment(self.experimento)
            mlflow.langchain.autolog()
            self.configurado = True
            return True
        except Exception:
            log.warning("no se pudo configurar MLflow en %s", self.uri, exc_info=True)
            return False

    # ------------------------------------------------------------ hilo
    def iniciar(self) -> None:
        self.sondear()
        if self.uri and self._hilo is None:
            self._hilo = threading.Thread(target=self._bucle, name="sondeo-mlflow", daemon=True)
            self._hilo.start()

    def _bucle(self) -> None:
        while not self._alto.wait(self.intervalo):
            try:
                self.sondear()
            except Exception:
                log.exception("error al consultar MLflow")

    def detener(self) -> None:
        global _activo
        self._alto.set()
        if self.configurado and self.disponible:
            try:
                import mlflow

                mlflow.flush_trace_async_logging()
            except Exception:
                pass
        _activo = False
