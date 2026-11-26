"""Muestra los parámetros que acepta un modelo en cada proveedor de OpenRouter.

Uso: python scripts/parametros_modelo.py anthropic/claude-haiku-5.5
"""

import json
import sys
import urllib.request

modelo = sys.argv[1]
with urllib.request.urlopen(f"https://openrouter.ai/api/v1/models/{modelo}/endpoints", timeout=30) as r:
    datos = json.load(r)["data"]
for e in datos.get("endpoints", []):
    print(f"{e.get('provider_name')}: {sorted(e.get('supported_parameters') or [])}")
