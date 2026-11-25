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
