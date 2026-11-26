# NovaDistribuidora: toma de pedidos por WhatsApp

Servicio que convierte los mensajes informales que los tenderos envían por
WhatsApp en pedidos estructurados. Calcula las líneas, los precios, los
descuentos, el total y la fecha de entrega. Envía a revisión humana lo que no
puede resolver con certeza y redacta la confirmación para el cliente.

El modelo de lenguaje **solo lee**: extrae qué productos se piden, en qué cantidad
y unidad. Todo lo demás (precios, descuentos, crédito, fechas y estado) lo
calcula código probado según `politicas.md`. Así ningún mensaje puede alterar un
precio ni una condición. Las decisiones y su justificación, incluida la elección
del modelo, están en [DECISIONES.md](DECISIONES.md).

| Componente | Tecnología | Dirección |
|---|---|---|
| API | FastAPI | <http://localhost:8000> (documentación en `/docs`) |
| Extracción | LangChain + OpenRouter (`google/gemini-2.5-flash-lite`) | externa |
| Trazas y evaluaciones | MLflow 3 | <http://localhost:5000> |

---

## Levantar el producto

### Requisitos

- Docker con Docker Compose v2. En Windows, Docker Desktop con WSL 2.
- Una clave de [OpenRouter](https://openrouter.ai/settings/keys).

### 1. Clonar

```bash
git clone https://github.com/rodrigodevelops/Laboratorio_3_PD.git
cd Laboratorio_3_PD
git checkout entrega-lab3
```

### 2. Colocar los archivos publicados

Descomprimir el archivo publicado en la carpeta **`publicados/`**, en la raíz del
repositorio, de modo que queden así:

```
publicados/datos/productos.parquet
publicados/datos/sinonimos.parquet
publicados/datos/precios_escala.parquet
publicados/datos/clientes.parquet
publicados/datos/historial_pedidos.parquet
publicados/politicas.md
publicados/catalogo_problemas.md
publicados/ejemplos/mensajes_dev.jsonl     (para reproducir la evaluación)
```

`publicados/` está en `.gitignore` y no entra en las imágenes: el contenedor de la
API la monta como solo lectura.

### 3. Configurar el `.env`

```bash
cp .env.example .env
```

Completar `OPENROUTER_API_KEY`. El modelo entregado ya está en `.env.example`
(`OPENROUTER_MODEL=google/gemini-2.5-flash-lite`). El `.env` no se sube al
repositorio.

### 4. Levantar

```bash
docker compose up -d --build
```

La primera vez tarda unos 3 minutos en construir las imágenes. Para comprobar
que todo funciona:

```bash
curl -s localhost:8000/health
# {"estado":"ok","modelo":"google/gemini-2.5-flash-lite","mlflow":"disponible"}

curl -s -X POST localhost:8000/v1/pedidos/interpretar \
  -H 'content-type: application/json' \
  -d '{"cliente_id":"C001","fecha_hora":"2026-11-23T15:20",
       "mensajes":["Buenas, mándeme 2 fardos de kola 600, una caja de aceite",
                   "y 48 latas de energía max"]}'
```

Durante los primeros segundos `/health` puede responder `degradado` mientras
MLflow termina de arrancar.

Para detener todo: `docker compose down`. Las trazas y evaluaciones se conservan
en el volumen `mlflow-datos`.

---

## Contrato del servicio

Puerto 8000. Detalle completo en el Anexo del enunciado y en `/docs`.

| Solicitud | Respuesta |
|---|---|
| `POST /v1/pedidos/interpretar` válido | **200** con el pedido: `estado`, `lineas`, `total`, `fecha_entrega`, `sustituciones`, `pendientes`, `motivos_revision`, `mensaje_cliente` |
| Cliente inexistente | **404** con `detail` (no se llama al modelo) |
| Solicitud inválida (campos faltantes, lista de mensajes vacía, fecha mal formada, JSON roto) | **422** con `detail` en español, por ejemplo `"Solicitud inválida: mensajes no puede estar vacío"`, y la lista `errores` por campo |
| El modelo de lenguaje no responde (caída, timeout de 20 s con un reintento o salida inválida) | **503** con `detail`; el servicio no inventa un pedido |
| `GET /health` | **200** con `estado` = `ok`, o `degradado` y `motivo` si MLflow no responde o falta configuración |

**Si MLflow se detiene,** la API sigue respondiendo con la misma latencia,
`/health` pasa a `degradado` en menos de 5 s y, cuando MLflow vuelve, las trazas
se reanudan solas:

```bash
docker compose stop mlflow
sleep 6; curl -s localhost:8000/health
docker compose start mlflow
```

---

## Reproducir la evaluación

La evaluación corre fuera de Docker y usa el MLflow del `docker compose`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

C="publicados/ejemplos/mensajes_dev.jsonl casos/casos_propios.jsonl"
python -m novadistribuidora.evaluacion --casos $C --mlflow http://localhost:5000 \
    --modelo google/gemini-2.5-flash-lite --variante ejemplos --salida evidencia/final_gemini_ejemplos.json
python -m novadistribuidora.evaluacion --casos $C --mlflow http://localhost:5000 \
    --modelo google/gemini-2.5-flash-lite --variante base --salida evidencia/final_gemini_base.json
python -m novadistribuidora.evaluacion --casos $C --mlflow http://localhost:5000 \
    --modelo anthropic/claude-haiku-5.5 --variante ejemplos --salida evidencia/final_haiku_ejemplos.json
python scripts/comparar.py evidencia/final_*.json > evidencia/comparacion.md
```

- **Casos:** los 84 publicados más los 22 propios de
  [`casos/casos_propios.jsonl`](casos/casos_propios.jsonl), con el mismo formato:
  intentos de manipulación, cantidades no convertibles y situaciones nuevas.
- **Calificación con código** de cada campo contra la respuesta esperada: estado,
  líneas, total, fecha, sustituciones, motivos y pendientes, además de latencia y
  costo por pedido (OpenRouter informa el costo de cada llamada).
- **En MLflow:** experimento `novadistribuidora-evaluacion`, con una corrida por
  alternativa y una traza por caso con sus calificaciones.
- **Sin `--mlflow`:** la evaluación corre igual y solo imprime y guarda el
  resultado.
- **Costo:** las tres corridas cuestan menos de US$ 0.10 en total.

Las solicitudes inválidas (422, 404 y 503) no tienen un pedido esperado, así que
se prueban en `tests/test_api.py`.

### Resultado

| Alternativa | Exactos (106) | Estado / Total / Motivos | Latencia p50 / p95 | US$ por pedido |
|---|---|---|---|---|
| Gemini 2.5 Flash Lite, instrucciones `base` | 102 (96.2 %) | 97 % / 97 % / 96 % | 1.28 / 1.66 s | 0.00012 |
| **Gemini 2.5 Flash Lite, instrucciones `ejemplos`** (entregada) | **104 (98.1 %)** | **100 % / 100 % / 100 %** | **1.24 / 1.59 s** | **0.00016** |
| Claude Haiku 5.5, instrucciones `ejemplos` | 106 (100 %) | 100 % / 100 % / 100 % | 1.71 / 3.10 s | 0.00058 |

Por qué se eligió esta configuración: [DECISIONES.md, sección 6](DECISIONES.md#6-elección-del-modelo).

---

## Evidencia

Carpeta [`evidencia/`](evidencia):

| Archivo | Contenido |
|---|---|
| `comparacion.md` | Tabla comparativa de la ronda final |
| `final_*.json` | Ronda final (106 casos): resumen y detalle por caso de cada alternativa |
| `eval_*.json` | Rondas anteriores (84 casos), incluidos los modelos descartados (gpt-5-nano) y el antes y después del ajuste del prompt |
| `capturas/01_comparacion_corridas.png` | Comparación de las corridas en MLflow |
| `capturas/02_evaluacion_casos.png` | Calificaciones por caso de la configuración entregada |
| `capturas/03_caso_manipulacion.png` | Un intento de manipulación: el precio no cambia |
| `capturas/04_traza_api.png` | Traza de una solicitud real a la API (modelo, tokens, costo) |

---

## Desarrollo

```bash
source .venv/bin/activate
pytest -q                       # 157 pruebas; no llaman al modelo ni necesitan MLflow
uvicorn novadistribuidora.api:app --port 8000   # API fuera de Docker (lee el .env)
python scripts/modelos_openrouter.py            # modelos con salida estructurada y precios
```

### Estructura

```
├── src/novadistribuidora/
│   ├── esquemas.py         # contrato (Pydantic) y salida estructurada del modelo
│   ├── catalogo.py         # lectura de los datos publicados
│   ├── extraccion.py       # LangChain: instrucciones y llamada al modelo
│   ├── reglas.py           # políticas del negocio (precios, descuentos, crédito, fechas)
│   ├── mensaje.py          # confirmación al cliente (plantilla)
│   ├── asistente.py        # extracción -> reglas -> mensaje
│   ├── api.py              # FastAPI
│   ├── observabilidad.py   # trazas de MLflow que no bloquean
│   └── evaluacion.py       # evaluación por código, con o sin MLflow
├── casos/casos_propios.jsonl
├── evidencia/
├── scripts/
├── tests/
├── docker/                 # Dockerfiles de la API y de MLflow
├── docker-compose.yaml
├── .env.example
├── CLAUDE.md
└── DECISIONES.md
```

## Uso de herramientas de IA

El desarrollo se apoyó en Claude como asistente de programación. Las reglas del
proyecto para el agente están en [CLAUDE.md](CLAUDE.md). El autor revisó, probó
y puede justificar todo el código y las decisiones.
