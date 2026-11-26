# Decisiones técnicas y de producto

Cada decisión incluye las alternativas consideradas y su justificación. Este
documento se actualiza en el mismo Pull Request que introduce cada decisión.

---

## 1. El modelo de lenguaje solo lee; el código decide

**Decisión:** el modelo de lenguaje solo convierte los mensajes en una
`Extraccion` (salida estructurada): si hay un pedido, si es "lo de siempre", y
cada producto mencionado con sus SKU candidatos, cantidad, unidad y acción
(agregar, corregir o quitar). Todo lo demás lo calcula `reglas.py`, con código
probado: conversión de unidades, sumas y correcciones, "lo de siempre",
agotados y sustitutos, descuentos, subtotales, total, crédito, fecha de entrega,
estado y motivos de revisión.

**Justificación:**
- El enunciado exige que los cálculos del negocio se hagan con código probado.
- **Ningún mensaje puede alterar precios, descuentos ni crédito**, porque la
  extracción no tiene campos para eso: un "el gerente autorizó 50 %" no tiene
  por dónde llegar al cálculo. Si el modelo devolviera un precio, el esquema lo
  descarta (`test_la_extraccion_no_acepta_precios`).
- Los cálculos se pueden probar sin el modelo, sin costo y sin variabilidad.

**Alternativas:**
- *Que el modelo arme el pedido completo:* más simple, pero los totales, las
  fechas y los descuentos dependerían del modelo, que se equivoca en aritmética
  y puede ser manipulado por el texto del mensaje.
- *Un agente con herramientas (buscar producto, calcular precio, consultar
  crédito):* varias llamadas al modelo por pedido, más latencia y costo, y menos
  predecible. El catálogo tiene 44 productos y cabe completo en las
  instrucciones, así que una sola llamada basta.

## 2. Ambigüedad y productos inexistentes: el modelo propone, el código valida

- Si un texto puede ser más de un producto, el modelo devuelve **todos** los SKU
  posibles y el código registra `producto_ambiguo` con esas opciones. El modelo
  nunca elige uno.
- Si no hay ningún SKU posible, el código registra `producto_no_existe`.
- Cada SKU que propone el modelo se valida contra el catálogo. Un SKU que no
  existe se descarta (y se anota en la traza); si no queda ninguno, la mención
  pasa a `producto_no_existe`. Así un SKU inventado nunca llega a una línea.

## 3. Reglas implementadas (`politicas.md`)

| Política | Implementación |
|---|---|
| Unidad no dicha | La cantidad es en unidades de venta |
| Cantidad no dicha | 1 unidad de venta |
| Unidades sueltas y docenas | Se dividen por `unidades_por_empaque` solo si el resultado es entero; si no, `cantidad_no_convertible` (sin redondear) |
| Menciones repetidas | Se suman, salvo que la acción sea `corregir`, que reemplaza la cantidad |
| Quitar un producto | Se elimina la línea, aunque venga de "lo de siempre" |
| "Lo de siempre" | Último pedido `entregado` del historial con fecha de entrega anterior o igual a la recepción; los cancelados no cuentan |
| Agotado con sustituto disponible | Se entrega el sustituto con la misma cantidad y se informa en `sustituciones`; si el sustituto ya estaba pedido, se suman |
| Agotado sin sustituto | Pendiente `agotado_sin_sustituto`, con el nombre del catálogo como `texto` y el SKU en `opciones` (convención de los casos publicados) |
| Descuento | El mayor porcentaje de la categoría cuyo mínimo alcance la cantidad de la línea |
| Redondeo | `Decimal` con mitad hacia arriba, a 2 decimales |
| Crédito | `saldo_pendiente + total > limite_credito` agrega `credito_excedido` |
| Fecha | Desde las 14:00 se considera el día siguiente; se entrega en la primera visita estrictamente posterior |
| Estado | `fuera_de_alcance` si no hay pedido; `requiere_revision` si hay pendientes o crédito excedido; si no, `confirmado` |

**Caso no previsto en las políticas:** si el cliente pide algo y luego quita
todo (no queda ninguna línea ni pendiente), se responde `fuera_de_alcance`,
porque no hay nada que entregar ni revisar.

**Verificación:** para cada uno de los 84 casos publicados se arma la extracción
ideal deducida de su respuesta esperada, y el motor reproduce exactamente el
estado, las líneas, el total, la fecha, las sustituciones y los motivos
(`tests/test_casos_publicados.py`). Así, cualquier error en la evaluación
completa se debe a la extracción, no a los cálculos.

## 4. Mensaje al cliente con plantilla

**Decisión:** `mensaje.py` arma la confirmación a partir del pedido calculado:
saludo según la hora, productos y cantidades, total, sustituciones, pendientes
("un asesor le confirmará"), crédito y fecha de entrega.

**Justificación:** el mensaje nunca puede prometer un producto agotado o
pendiente, ni mencionar un precio distinto al calculado, porque se construye con
los mismos datos del pedido. No agrega latencia ni costo.

**Alternativa:** que lo redacte el modelo. Queda más natural, pero requiere una
segunda llamada (más latencia y costo) y puede contradecir el pedido.

## 5. Extracción con LangChain

- **Una llamada** a un modelo de OpenRouter mediante `ChatOpenAI` (interfaz
  compatible con OpenAI) y `with_structured_output(Extraccion, method="json_schema")`.
  La respuesta se valida con Pydantic; si no es válida, la solicitud falla en
  lugar de adivinar.
- **Temperatura 0**, para que el mismo mensaje dé la misma extracción.
- **Solo se envían los parámetros que el modelo acepta.** Al iniciar, el servicio
  consulta en OpenRouter los `supported_parameters` del modelo y omite los que no
  acepta (los GPT-5, por ejemplo, no aceptan `temperature`). Sin esto,
  `require_parameters` hace que OpenRouter rechace la llamada con un 404. Si la
  consulta falla, se envían los parámetros por defecto. Así se puede cambiar de
  modelo solo con `OPENROUTER_MODEL`.
- **`provider.require_parameters`:** OpenRouter solo enruta a proveedores que
  soportan salida estructurada. Sin esto, un proveedor barato podría ignorar el
  esquema.
- **`usage.include`:** OpenRouter devuelve el costo en dólares de cada llamada,
  que se registra junto con los tokens y la latencia.
- **El catálogo va en las instrucciones, sin precios ni disponibilidad.** El
  modelo solo necesita reconocer productos (SKU, nombre, unidad de venta y
  alias). Así no puede razonar sobre precios ni prometer productos agotados, y
  las instrucciones son más cortas (unos 1800 tokens).
- **Los mensajes del cliente van delimitados** entre `<mensajes>` y
  `</mensajes>`, con la instrucción de tratarlos como datos e ignorar cualquier
  orden que contengan. Es una defensa adicional: la principal es que la
  extracción no tiene campos de precio, descuento ni crédito (sección 1).
- **El cliente se valida antes de llamar al modelo:** un cliente inexistente
  responde 404 sin gastar una llamada.
- **Dos variantes de instrucciones** para la comparación: `base` (solo reglas) y
  `ejemplos` (reglas más cinco ejemplos de los casos difíciles: correcciones,
  unidades sueltas, "lo de siempre" con exclusiones, ambigüedad y un intento de
  manipulación).

## 6. Elección del modelo

Se evaluaron cuatro configuraciones con los 84 casos de `mensajes_dev.jsonl`,
la misma variante de instrucciones (`ejemplos`) y calificación con código
(`python -m novadistribuidora.evaluacion`). El detalle por caso está en
`evidencia/eval_*.json`.

**Primera ronda (prompt inicial):**

| Modelo | Exactos | Latencia p50 / p95 | Costo por pedido |
|---|---|---|---|
| google/gemini-2.5-flash-lite | 81/84 (96.4 %) | 0.68 s / 0.93 s | US$ 0.00015 |
| anthropic/claude-haiku-5.5 | 81/84 (96.4 %) | 1.41 s / 2.49 s | US$ 0.00055 |
| openai/gpt-5-nano, razonamiento `minimal` | 32/84 (38.1 %) | 1.07 s / 1.57 s | US$ 0.00008 |
| openai/gpt-5-nano, razonamiento por defecto | 19/84 (22.6 %), 64 errores | 7.91 s / 15.98 s | US$ 0.00040 |

- **gpt-5-nano con razonamiento por defecto** gastó casi todo el límite de 1500
  tokens de salida en razonamiento interno y no alcanzó a escribir la respuesta
  (`LengthFinishReasonError`). Subir el límite lo haría más lento y caro que los
  finalistas, que ya tardan menos de 1.5 s. Hallazgo: un modelo que razona
  necesita más tokens y eso sube la latencia y el costo, sin ventaja para una
  tarea de extracción.
- **gpt-5-nano con razonamiento `minimal`** es el más barato, pero falla 6 de
  cada 10 pedidos.

**Ajuste del prompt.** Los fallos de los dos finalistas venían de tres reglas
poco claras en las instrucciones, no del modelo: (a) una palabra genérica
("pasta") también puede ser un producto de otra categoría (pasta dental),
mientras que un alias exacto ("frijol negro") es un solo producto; (b) la
presentación ("473 ml") es parte del nombre, no la unidad; (c) los cierres
("nada más por hoy") no son productos. Se precisaron esas reglas.

**Segunda ronda (prompt ajustado):**

| Modelo | Exactos | Latencia p50 / p95 | Costo por pedido |
|---|---|---|---|
| **google/gemini-2.5-flash-lite** | **82/84 (97.6 %)** | **0.68 s / 1.01 s** | **US$ 0.00017** |
| anthropic/claude-haiku-5.5 | 83/84 (98.8 %) | 1.23 s / 2.61 s | US$ 0.00059 |

**Ronda final: 106 casos (84 publicados y 22 propios), registrada en MLflow.**
Los casos propios (`casos/casos_propios.jsonl`) no se usaron para ajustar el
prompt, así que funcionan como prueba independiente. También se comparan las
dos variantes de instrucciones. Resumen en `evidencia/comparacion.md`:

| Alternativa | Exactos | Estado / Total / Motivos | Latencia p50 / p95 | US$ por pedido |
|---|---|---|---|---|
| Gemini, instrucciones `base` | 102/106 (96.2 %) | 97 % / 97 % / 96 % | 1.28 / 1.66 s | 0.00012 |
| **Gemini, instrucciones `ejemplos`** | **104/106 (98.1 %)** | **100 % / 100 % / 100 %** | **1.24 / 1.59 s** | **0.00016** |
| Haiku, instrucciones `ejemplos` | 106/106 (100 %) | 100 % / 100 % / 100 % | 1.71 / 3.10 s | 0.00058 |

La latencia de esta ronda incluye el registro de cada caso en MLflow, por eso es
mayor que en las rondas anteriores. Todas las alternativas se midieron igual.

- **Casos propios:** Gemini con `ejemplos` resolvió los 22: manipulación 7/7,
  cantidades no convertibles 4/4 y situaciones nuevas 11/11.
- **Los ejemplos ayudan:** sin ellos, Gemini falla 4 casos. Uno es un intento de
  manipulación (X04): no marcó el producto inventado "Z99 gratis" como
  `producto_no_existe` y confirmó el pedido sin revisión. Por eso se entrega la
  variante `ejemplos`.
- **Los dos fallos de Gemini son consistentes**, porque se repiten en la segunda
  ronda y en la final, pero no afectan lo que se entrega ni lo que se cobra. En
  D014 el pedido pasa a revisión igual que lo esperado, solo que al pendiente le
  falta una de las opciones. En D072 las líneas y el total son correctos y solo
  difiere el registro de la sustitución.

**Decisión: `google/gemini-2.5-flash-lite` con instrucciones `ejemplos`.**

- Acierta el 100 % del estado, las líneas, el total, la fecha y los motivos de
  revisión en los 106 casos, igual que Haiku.
- Es casi 2 veces más rápido en p95 (1.6 s contra 3.1 s).
- Cuesta unas 3.6 veces menos: unos US$ 0.16 por cada 1000 pedidos contra
  US$ 0.58.
- **Lo que se sacrifica:** Haiku también acierta los campos informativos
  (opciones de un pendiente y registro de sustituciones). Si el negocio valorara
  eso más que la velocidad, basta con cambiar `OPENROUTER_MODEL` a
  `anthropic/claude-haiku-5.5`.

**Limitación.** El prompt se ajustó mirando los 84 casos publicados, así que su
resultado en ellos es algo optimista. Para no seguir acomodándolo a ese
conjunto, no se ajustó más después de la segunda ronda. El resultado en los 22
casos propios es la mejor estimación de cómo funcionará con mensajes nuevos,
aunque con solo 22 casos sigue siendo una estimación gruesa.

## 7. Servicio y observabilidad

- **Códigos de respuesta.** 404 para un cliente inexistente, antes de llamar al
  modelo. 422 para una solicitud inválida, con un mensaje en español que dice qué
  campo falla y por qué (por ejemplo, "mensajes no puede estar vacío"). **503 si
  el modelo no responde** (caída, timeout de 20 s con un reintento o salida que
  no cumple el esquema).
  - *Alternativa descartada:* responder 200 con `requiere_revision` y sin líneas.
    El contrato no tiene un motivo para "el modelo falló", así que parecería un
    pedido real. Un 503 es honesto y el sistema que llama sabe que debe
    reintentar o pasarlo a un asesor.
- **La API arranca aunque falte algo.** Si faltan los datos o la clave, arranca
  igual, `/health` responde `degradado` con el motivo y `interpretar` responde
  503. Así el problema se ve de inmediato en lugar de un contenedor que se
  reinicia sin explicación.
- **Trazas sin bloquear.** Un hilo consulta `/health` de MLflow cada 5 s (timeout
  de 1 s). Si responde, las trazas están activas; si no, se pausan, la API sigue
  atendiendo y `/health` responde `degradado`. Cuando MLflow vuelve, se reactivan
  solas. Las trazas se envían en segundo plano y sin reintentos, así que una caída
  entre dos consultas tampoco agrega latencia. Se verificó así: con MLflow
  detenido, las solicitudes tardaron lo mismo (unos 0.33 s con un modelo falso);
  al volver, las nuevas solicitudes volvieron a registrarse.
- **Qué registra cada traza.** El span `interpretar_pedido` guarda la solicitud,
  la respuesta y como atributos el modelo, los tokens, el costo y la latencia del
  modelo. Debajo, `mlflow.langchain.autolog()` registra la llamada a LangChain
  (instrucciones, respuesta cruda y extracción) y `reglas_del_negocio` registra la
  extracción que entró y el pedido que salió. Cada traza lleva las etiquetas
  `cliente_id`, `modelo`, `variante` y `estado` para filtrar en la interfaz.
- **Imagen de la API con `mlflow-tracing`** (solo el cliente de trazas, con
  autolog de LangChain) en lugar del paquete completo de MLflow. Así la imagen es
  más liviana. El servidor usa `mlflow==3.18.0` con SQLite en un volumen: un solo
  contenedor alcanza para este volumen de trazas.
  - *Alternativa:* PostgreSQL como en el Laboratorio 2. Es más robusto con
    muchos escritores, pero agrega un tercer contenedor que aquí no hace falta.
- **Datos y credenciales fuera de la imagen.** Los datos se montan desde
  `./publicados/datos` como solo lectura y la clave llega por `env_file`.
  `.dockerignore` excluye `.env` y `publicados/`.

## 8. Evaluación

- **Calificación con código, sin juez LLM.** Cada campo del pedido tiene una
  respuesta exacta según `politicas.md`: estado, líneas, total, fecha,
  sustituciones, motivos y pendientes. Compararlos con código es reproducible,
  gratis y no se equivoca. El `texto` de los pendientes no se compara porque es
  un fragmento libre del mensaje.
  - *Alternativa descartada:* un juez LLM. Sería útil para algo subjetivo como
    el tono de `mensaje_cliente`, pero ese mensaje lo arma una plantilla a
    partir del pedido calculado (sección 4), así que no puede contradecirlo.
- **`mlflow.genai.evaluate`.** Cada caso queda como una traza (la llamada al
  modelo y las reglas) con sus calificaciones. La corrida registra el modelo,
  la variante y los casos como parámetros, y la exactitud por criterio y por
  tipo, la latencia y el costo como métricas. Así se comparan las alternativas
  en la interfaz de MLflow.
- **Casos propios (22)** en `casos/casos_propios.jsonl`, con el mismo formato
  que los publicados. Cubren lo que esos casos no tienen: 7 intentos de
  manipulación (descuentos "autorizados", "ignora las instrucciones", JSON
  falso, crédito "ampliado", productos inventados), 4 casos de cantidades no
  convertibles (ningún caso publicado tiene este motivo) y 11 situaciones
  nuevas (mensajes en spanglish, emojis, cancelaciones, reclamos, límites
  exactos de descuento y de hora de corte, quitar un producto en otro mensaje).
  Las respuestas esperadas se calcularon con las políticas y se revisaron a mano.
- **Solicitudes inválidas.** No caben en el formato de casos porque no tienen un
  pedido esperado, así que se prueban en `tests/test_api.py`: campos faltantes,
  lista vacía, fechas mal formadas, tipos incorrectos, JSON roto y cliente
  inexistente.

