"""Tabla comparativa de las evaluaciones guardadas con --salida.

    python scripts/comparar.py evidencia/eval_*.json > evidencia/comparacion.md
"""

import json
import sys

filas = []
for ruta in sys.argv[1:]:
    d = json.load(open(ruta, encoding="utf-8"))
    r = d["resumen"]
    costo = r.get("costo_por_pedido_usd")
    filas.append((d["modelo"], d["variante"] + (f", razonamiento {d['razonamiento']}" if d.get("razonamiento") else ""),
                  f"{r['exactos']}/{r['casos']} ({r['exactitud']:.1%})", f"{r['acierto_estado']:.0%}",
                  f"{r['acierto_total']:.0%}", f"{r['acierto_motivos']:.0%}", str(r["errores"]),
                  f"{r['latencia_p50_s']:.2f} / {r['latencia_p95_s']:.2f}",
                  "n/d" if costo is None else f"{costo:.5f}", ruta))
print("| Modelo | Instrucciones | Exactos | Estado | Total | Motivos | Errores | Latencia p50 / p95 (s) | US$ por pedido | Detalle |")
print("|---|---|---|---|---|---|---|---|---|---|")
for f in filas:
    print("| " + " | ".join(f) + " |")
