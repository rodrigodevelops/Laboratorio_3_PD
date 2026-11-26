"""Evaluación reproducible: calificación con código de cada caso contra su respuesta esperada.

    python -m novadistribuidora.evaluacion --modelo openai/gpt-5-nano --variante ejemplos \\
        --casos publicados/ejemplos/mensajes_dev.jsonl casos/casos_propios.jsonl

Calificaciones (todas con código, sin juez LLM):

- ``estado``, ``total`` (±0.005), ``fecha_entrega``
- ``lineas``: mismos SKU y cantidades; ``lineas_exactas`` además precio, descuento y subtotal
- ``sustituciones``, ``motivos`` (como conjunto), ``pendientes`` (motivo y opciones; el
  ``texto`` no se compara porque es un fragmento libre del mensaje)
- ``exacto``: todo lo anterior correcto a la vez
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

CRITERIOS = ["estado", "lineas", "lineas_exactas", "total", "fecha_entrega", "sustituciones", "motivos", "pendientes"]


def _normalizar(valor):
    """Compara números como números: 0, 0.0 y 0.00 son lo mismo."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)
    if isinstance(valor, dict):
        return {k: _normalizar(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_normalizar(v) for v in valor]
    return valor


def _multiconjunto(items):
    return Counter(json.dumps(_normalizar(i), sort_keys=True) for i in items)


def calificar(esperado: dict, obtenido: dict) -> dict[str, bool]:
    lineas_e = [(l["sku"], l["cantidad"]) for l in esperado["lineas"]]
    lineas_o = [(l["sku"], l["cantidad"]) for l in obtenido["lineas"]]
    notas = {
        "estado": esperado["estado"] == obtenido["estado"],
        "lineas": Counter(lineas_e) == Counter(lineas_o),
        "lineas_exactas": _multiconjunto(esperado["lineas"]) == _multiconjunto(
            [{k: l[k] for k in ("sku", "cantidad", "precio_unitario", "descuento_pct", "subtotal")}
             for l in obtenido["lineas"]]),
        "total": abs(float(esperado["total"]) - float(obtenido["total"])) < 0.005,
        "fecha_entrega": esperado["fecha_entrega"] == obtenido["fecha_entrega"],
        "sustituciones": _multiconjunto(esperado["sustituciones"]) == _multiconjunto(obtenido["sustituciones"]),
        "motivos": set(esperado["motivos_revision"]) == set(obtenido["motivos_revision"]),
        "pendientes": Counter((p["motivo"], tuple(sorted(p["opciones"]))) for p in esperado["pendientes"])
                      == Counter((p["motivo"], tuple(sorted(p["opciones"]))) for p in obtenido["pendientes"]),
    }
    notas["exacto"] = all(notas.values())
    return notas


def cargar_casos(rutas: list[str | Path]) -> list[dict]:
    casos = []
    for ruta in rutas:
        for i, linea in enumerate(Path(ruta).read_text(encoding="utf-8").splitlines(), 1):
            if linea.strip():
                caso = json.loads(linea)
                caso.setdefault("origen", Path(ruta).name)
                casos.append(caso)
    return casos


def evaluar_caso(asistente, caso: dict) -> dict:
    e = caso["entrada"]
    registro = {"id": caso["id"], "tipo": caso.get("tipo", ""), "origen": caso.get("origen", "")}
    inicio = time.perf_counter()
    try:
        r = asistente.interpretar(e["cliente_id"], datetime.strptime(e["fecha_hora"], "%Y-%m-%dT%H:%M"), e["mensajes"])
        obtenido = r.respuesta.model_dump(mode="json")
        registro.update(
            latencia_s=time.perf_counter() - inicio, latencia_modelo_s=r.extraccion.latencia_s,
            tokens_entrada=r.extraccion.tokens_entrada, tokens_salida=r.extraccion.tokens_salida,
            costo_usd=r.extraccion.costo_usd, obtenido=obtenido,
            extraccion=r.extraccion.extraccion.model_dump(), notas=calificar(caso["esperado"], obtenido),
        )
    except Exception as ex:  # un error cuenta como caso fallido
        registro.update(latencia_s=time.perf_counter() - inicio, error=f"{type(ex).__name__}: {ex}",
                        notas={c: False for c in [*CRITERIOS, "exacto"]})
    return registro


def resumir(registros: list[dict]) -> dict:
    n = len(registros)
    lat = sorted(r["latencia_s"] for r in registros if "error" not in r) or [0.0]
    costos = [r["costo_usd"] for r in registros if r.get("costo_usd") is not None]
    resumen = {
        "casos": n,
        "exactos": sum(r["notas"]["exacto"] for r in registros),
        "exactitud": sum(r["notas"]["exacto"] for r in registros) / n if n else 0.0,
        **{f"acierto_{c}": sum(r["notas"][c] for r in registros) / n for c in CRITERIOS},
        "errores": sum("error" in r for r in registros),
        "latencia_p50_s": statistics.median(lat),
        "latencia_p95_s": lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))],
        "latencia_media_s": statistics.fmean(lat),
        "costo_total_usd": sum(costos) if costos else None,
        "costo_por_pedido_usd": statistics.fmean(costos) if costos else None,
        "tokens_entrada_media": statistics.fmean(r.get("tokens_entrada", 0) for r in registros),
        "tokens_salida_media": statistics.fmean(r.get("tokens_salida", 0) for r in registros),
    }
    por_tipo = {}
    for r in registros:
        t = por_tipo.setdefault(r["tipo"] or "sin_tipo", [0, 0])
        t[0] += r["notas"]["exacto"]
        t[1] += 1
    resumen["exactitud_por_tipo"] = {t: f"{a}/{b}" for t, (a, b) in sorted(por_tipo.items())}
    return resumen


def evaluar(asistente, casos: list[dict], hilos: int = 4) -> tuple[list[dict], dict]:
    with ThreadPoolExecutor(max_workers=hilos) as pool:
        registros = list(pool.map(lambda c: evaluar_caso(asistente, c), casos))
    return registros, resumir(registros)


def imprimir(resumen: dict, registros: list[dict], modelo: str, variante: str) -> None:
    print(f"\n== {modelo} ({variante}) ==")
    print(f"Exactos: {resumen['exactos']}/{resumen['casos']} ({resumen['exactitud']:.1%}), errores: {resumen['errores']}")
    print("Acierto por criterio: " + ", ".join(f"{c} {resumen['acierto_' + c]:.0%}" for c in CRITERIOS))
    costo = resumen["costo_por_pedido_usd"]
    print(f"Latencia p50 {resumen['latencia_p50_s']:.2f} s, p95 {resumen['latencia_p95_s']:.2f} s; "
          f"costo por pedido {'n/d' if costo is None else f'US$ {costo:.5f}'}")
    print("Por tipo: " + ", ".join(f"{t} {v}" for t, v in resumen["exactitud_por_tipo"].items()))
    fallidos = [r for r in registros if not r["notas"]["exacto"]]
    if fallidos:
        print("\nCasos con diferencias:")
        for r in fallidos[:40]:
            malos = [c for c in CRITERIOS if not r["notas"][c]]
            print(f"  {r['id']} ({r['tipo']}): {r.get('error') or ', '.join(malos)}")


def main(argv=None) -> None:
    from dotenv import load_dotenv

    from novadistribuidora.asistente import Asistente
    from novadistribuidora.catalogo import Catalogo
    from novadistribuidora.extraccion import Extractor

    load_dotenv()  # OPENROUTER_API_KEY y OPENROUTER_MODEL desde .env

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--casos", nargs="+", default=["publicados/ejemplos/mensajes_dev.jsonl"])
    p.add_argument("--modelo", default=None, help="por defecto, OPENROUTER_MODEL del .env")
    p.add_argument("--variante", default=None, choices=["base", "ejemplos"])
    p.add_argument("--razonamiento", default=None, help="minimal, low, medium, high o none (por defecto, .env)")
    p.add_argument("--hilos", type=int, default=4)
    p.add_argument("--limite", type=int, default=None, help="evaluar solo los primeros N casos")
    p.add_argument("--salida", default=None, help="archivo JSON con el detalle por caso")
    a = p.parse_args(argv)

    if a.razonamiento is not None:
        import os

        os.environ["NOVA_RAZONAMIENTO"] = a.razonamiento
    catalogo = Catalogo.desde_carpeta()
    extractor = Extractor(catalogo, modelo=a.modelo, variante=a.variante)
    casos = cargar_casos(a.casos)[: a.limite]
    registros, resumen = evaluar(Asistente(catalogo, extractor), casos, a.hilos)
    etiqueta = extractor.variante + (f", razonamiento {extractor.razonamiento}" if extractor.razonamiento else "")
    imprimir(resumen, registros, extractor.modelo, etiqueta)
    if a.salida:
        Path(a.salida).parent.mkdir(parents=True, exist_ok=True)
        Path(a.salida).write_text(json.dumps({"modelo": extractor.modelo, "variante": extractor.variante,
                                              "razonamiento": extractor.razonamiento,
                                              "resumen": resumen, "casos": registros},
                                             ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"\nDetalle en {a.salida}")


if __name__ == "__main__":
    sys.exit(main())
