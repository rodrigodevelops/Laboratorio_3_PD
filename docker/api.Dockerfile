# API de toma de pedidos. Sin datos ni credenciales: los datos se montan en /datos
# y las variables llegan desde el .env (ver docker-compose.yaml).
FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements-servicio.txt .
RUN pip install -r requirements-servicio.txt

COPY src/ src/
ENV PYTHONPATH=/app/src NOVA_DATOS=/datos

RUN useradd --create-home --uid 1000 nova
USER nova

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"
CMD ["uvicorn", "novadistribuidora.api:app", "--host", "0.0.0.0", "--port", "8000"]
