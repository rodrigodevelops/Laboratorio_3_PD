# Servidor de MLflow (trazas y evaluaciones), con SQLite en un volumen.
FROM python:3.12.11-slim-bookworm
ENV PIP_NO_CACHE_DIR=1
RUN pip install mlflow==3.18.0
EXPOSE 5000
