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

**Decisión: `google/gemini-2.5-flash-lite`.**

- En calidad empata en la práctica con Haiku: la diferencia es un caso de 84, y
  los casos que fallan cambian entre corridas (variación del modelo, no una
  regla mal entendida).
- Es unas 2 veces más rápido en la mediana y unas 2.6 veces en p95, lo que
  importa para responder por WhatsApp.
- Cuesta unas 3.5 veces menos: unos US$ 0.17 por cada 1000 pedidos contra
  US$ 0.59.
- **Respaldo:** `anthropic/claude-haiku-5.5`, de calidad equivalente. Para
  cambiar basta con `OPENROUTER_MODEL`.

**Limitación.** El prompt se ajustó mirando estos 84 casos, así que su resultado
es algo optimista. Para no seguir acomodándolo a este conjunto, no se ajustó más
después de la segunda ronda. Los casos propios de `casos/`, que el prompt nunca
vio, funcionan como prueba independiente.

