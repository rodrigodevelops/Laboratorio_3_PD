# Reglas del proyecto para el agente de programación

Servicio de toma de pedidos por WhatsApp para NovaDistribuidora: LangChain con
modelos de OpenRouter, FastAPI, MLflow y Docker Compose. Leer `README.md`,
`DECISIONES.md` y `publicados/politicas.md` antes de proponer cambios.

## Reglas no negociables

1. **El modelo de lenguaje solo extrae.** Produce una `Extraccion` (productos,
   cantidades, unidades y acciones). Precios, descuentos, totales, crédito,
   fechas y estado se calculan en `reglas.py`. Nunca agregar a `Extraccion` un
   campo de precio, descuento, crédito o estado: es la defensa principal contra
   la manipulación.
2. **El modelo no ve precios ni disponibilidad.** `describir_catalogo` solo envía
   el SKU, el nombre, la unidad de venta y los alias. Los mensajes del cliente van
   delimitados en `<mensajes>` y se tratan como datos.
3. **Ante la duda, no adivinar.** Si un texto puede ser varios productos, el
   modelo devuelve todos los SKU y el código crea un pendiente
   `producto_ambiguo`. Los SKU que no existen en el catálogo se descartan en
   `reglas.py`. Las conversiones de unidades que no son exactas generan
   `cantidad_no_convertible`; nunca se redondean.
4. **El contrato del Anexo no se rompe.** Toda respuesta pasa por los modelos de
   `esquemas.py`. 404 para un cliente inexistente (antes de llamar al modelo),
   422 con `detail` en español para una solicitud inválida, 503 si el modelo no
   responde. Nunca se responde un pedido inventado.
5. **MLflow nunca bloquea una solicitud.** Las trazas pasan por
   `observabilidad.py`: se activan y se apagan según `/health` de MLflow y se
   envían en segundo plano. Si MLflow cae, la API sigue respondiendo y `/health`
   responde `degradado`.
6. **Las pruebas no llaman al modelo ni a MLflow.** Usar `ModeloFalso`
   (`tests/test_extraccion.py`) y `ObservadorFijo` (`tests/test_api.py`). Toda
   regla nueva de `reglas.py` va con su prueba, y los 84 casos publicados deben
   seguir dando 84/84 con la extracción ideal (`tests/test_casos_publicados.py`).
7. **No ajustar el prompt mirando los casos propios.** `casos/casos_propios.jsonl`
   es la prueba independiente. Un cambio de instrucciones o de modelo se evalúa
   con `python -m novadistribuidora.evaluacion` y se documenta en `DECISIONES.md`
   con los números.
8. **Nada de datos ni credenciales en Git ni en las imágenes.** `publicados/` y
   `.env` están en `.gitignore` y en `.dockerignore`. Nunca pedir ni mostrar la
   clave de OpenRouter.
9. **Versiones fijas** en los Dockerfiles y en los archivos `requirements*.txt`.

## Forma de trabajo

- Ramas `feature/<tema>` y Pull Request a `main`, que está protegida. El CI
  corre `pytest` en cada PR.
- `pytest -q` debe pasar antes de abrir un PR.
- Toda decisión técnica o de producto nueva se documenta en `DECISIONES.md` en
  el mismo PR, con sus alternativas y su justificación.
- Código, mensajes y documentación en español.
- Si se cambia código en `src/`, reconstruir las imágenes:
  `docker compose up -d --build`.

## Comandos

```bash
source .venv/bin/activate
pytest -q
docker compose up -d --build
curl -s localhost:8000/health
docker compose logs api --tail 50
python -m novadistribuidora.evaluacion --casos publicados/ejemplos/mensajes_dev.jsonl casos/casos_propios.jsonl --mlflow http://localhost:5000
python scripts/comparar.py evidencia/final_*.json
```

## Mapa del código

| Módulo | Responsabilidad |
|---|---|
| `esquemas.py` | Contrato de la API y esquema de la extracción |
| `catalogo.py` | Lectura de los parquet; "lo de siempre" desde el historial |
| `extraccion.py` | Instrucciones, llamada al modelo con salida estructurada, costo y tokens |
| `reglas.py` | Políticas: unidades, correcciones, agotados, descuentos, crédito, fechas, estado |
| `mensaje.py` | Confirmación al cliente con plantilla |
| `asistente.py` | Orquesta extracción -> reglas -> mensaje |
| `api.py` | HTTP, códigos de error y `/health` |
| `observabilidad.py` | Trazas de MLflow sin bloquear |
| `evaluacion.py` | Calificación por código y `mlflow.genai.evaluate` |
