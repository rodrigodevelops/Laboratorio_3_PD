"""Lista los modelos de OpenRouter que soportan salida estructurada, ordenados por precio.

Excluye enrutadores (precio variable), variantes ":batch" (respuestas diferidas) y
":free" (límites estrictos). No necesita clave.

Uso: python scripts/modelos_openrouter.py [filtro]
Ejemplo: python scripts/modelos_openrouter.py gemini
"""

import json
import sys
import urllib.request

filtro = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
    modelos = json.load(r)["data"]

filas = []
for m in modelos:
    id_ = m["id"]
    params = set(m.get("supported_parameters") or [])
    if "structured_outputs" not in params or filtro not in id_.lower():
        continue
    if id_.endswith((":batch", ":free")) or id_.startswith("openrouter/"):
        continue
    precio = m.get("pricing", {})
    entrada = float(precio.get("prompt") or 0) * 1e6
    salida = float(precio.get("completion") or 0) * 1e6
    if entrada <= 0 or salida <= 0:
        continue
    # Costo aproximado de un pedido: ~2500 tokens de entrada y ~400 de salida.
    pedido = (2500 * entrada + 400 * salida) / 1e6
    razona = "sí" if "reasoning" in params or "include_reasoning" in params else "no"
    filas.append((pedido, id_, entrada, salida, razona))

print(f"{'modelo':<50}{'US$/M entr.':>12}{'US$/M sal.':>12}{'US$/pedido':>12}{'razona':>8}")
for pedido, id_, e, s, razona in sorted(filas)[:60]:
    print(f"{id_:<50}{e:>12.3f}{s:>12.3f}{pedido:>12.5f}{razona:>8}")
print(f"\n{len(filas)} modelos con salida estructurada" + (f" que contienen '{filtro}'" if filtro else ""))
