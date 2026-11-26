"""Evaluación reproducible: calificación con código de cada caso contra su respuesta esperada.

    python -m novadistribuidora.evaluacion --modelo google/gemini-2.5-flash-lite \\
        --casos publicados/ejemplos/mensajes_dev.jsonl casos/casos_propios.jsonl \\
        --mlflow http://localhost:5000

Con ``--mlflow`` la corrida se registra con ``mlflow.genai.evaluate`` en el
experimento ``novadistribuidora-evaluacion``: una traza por caso con sus
calificaciones, y métricas de calidad, latencia y costo para comparar corridas.

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


def evaluar_en_mlflow(asistente, casos: list[dict], uri: str, etiqueta: str, parametros: dict,
                      hilos: int = 4) -> tuple[list[dict], dict]:
    """Igual que ``evaluar``, pero con ``mlflow.genai.evaluate``: trazas y calificaciones por caso."""
    import os
    import threading

    import mlflow
    from mlflow.entities import Feedback
    from mlflow.genai.scorers import scorer

    from novadistribuidora.observabilidad import Observador

    obs = Observador(uri, experimento="novadistribuidora-evaluacion")
    if not obs.sondear():
        raise SystemExit(f"MLflow no responde en {uri}")
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", str(hilos))
    os.environ.setdefault("MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION", "true")  # evita llamar dos veces al 1.er caso

    por_id = {c["id"]: c for c in casos}
    registros: dict[str, dict] = {}
    candado = threading.Lock()

    @mlflow.trace(name="interpretar_pedido", span_type="CHAIN")  # raíz: agrupa LangChain y reglas
    def predecir(caso_id: str, cliente_id: str, fecha_hora: str, mensajes: list[str]) -> dict:
        r = evaluar_caso(asistente, por_id[caso_id])
        with candado:
            registros[caso_id] = r
        return {"respuesta": r.get("obtenido"), "error": r.get("error"), "latencia_s": r["latencia_s"],
                "costo_usd": r.get("costo_usd"), "tokens_entrada": r.get("tokens_entrada", 0),
                "tokens_salida": r.get("tokens_salida", 0)}

    def notas(outputs, expectations) -> dict:
        if not outputs or outputs.get("respuesta") is None:
            return {c: False for c in [*CRITERIOS, "exacto"]}
        return calificar(expectations["pedido_esperado"], outputs["respuesta"])

    def criterio(nombre: str):
        @scorer(name=nombre)
        def calificador(outputs, expectations) -> Feedback:
            n = notas(outputs, expectations)
            if nombre == "exacto":
                malos = [c for c in CRITERIOS if not n[c]]
                motivo = (outputs or {}).get("error") or ("falla: " + ", ".join(malos) if malos else "todo correcto")
                return Feedback(value=n["exacto"], rationale=motivo)
            return Feedback(value=n[nombre])
        return calificador

    @scorer(name="latencia_s", aggregations=["mean", "median", "p90", "max"])
    def latencia(outputs) -> float:
        return float((outputs or {}).get("latencia_s") or 0.0)

    @scorer(name="costo_usd", aggregations=["mean", "max"])
    def costo(outputs) -> float:
        return float((outputs or {}).get("costo_usd") or 0.0)

    # Una sola expectativa con el pedido completo: MLflow no admite expectativas con valor
    # null (fecha_entrega de un pedido fuera de alcance).
    datos = [{"inputs": {"caso_id": c["id"], **c["entrada"]}, "expectations": {"pedido_esperado": c["esperado"]},
              "tags": {"tipo": c.get("tipo", ""), "origen": c.get("origen", "")}} for c in casos]
    with mlflow.start_run(run_name=etiqueta) as run:
        mlflow.log_params(parametros)
        mlflow.genai.evaluate(data=datos, predict_fn=predecir,
                              scorers=[criterio(c) for c in ["exacto", *CRITERIOS]] + [latencia, costo])
        lista = [registros[c["id"]] for c in casos if c["id"] in registros]
        resumen = resumir(lista)
        mlflow.log_metrics({k: float(v) for k, v in resumen.items()
                            if isinstance(v, (int, float)) and not isinstance(v, bool)})
        for tipo, valor in resumen["exactitud_por_tipo"].items():
            a, b = valor.split("/")
            mlflow.log_metric(f"exactitud_tipo_{tipo}", int(a) / int(b))
        mlflow.log_dict({"resumen": resumen, "casos": lista}, "detalle_evaluacion.json")
        print(f"\nCorrida de MLflow: {uri}/#/experiments/{run.info.experiment_id}/runs/{run.info.run_id}")
    obs.detener()
    return lista, resumen


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
    p.add_argument("--mlflow", default=None, metavar="URI",
                   help="registrar la evaluación en MLflow (por ejemplo, http://localhost:5000)")
    a = p.parse_args(argv)

    if a.razonamiento is not None:
        import os

        os.environ["NOVA_RAZONAMIENTO"] = a.razonamiento
    catalogo = Catalogo.desde_carpeta()
    extractor = Extractor(catalogo, modelo=a.modelo, variante=a.variante)
    casos = cargar_casos(a.casos)[: a.limite]
    etiqueta = extractor.variante + (f", razonamiento {extractor.razonamiento}" if extractor.razonamiento else "")
    asistente = Asistente(catalogo, extractor)
    if a.mlflow:
        nombre = f"{extractor.modelo.split('/')[-1]} | {etiqueta} | {len(casos)} casos"
        parametros = {"modelo": extractor.modelo, "variante": extractor.variante,
                      "razonamiento": extractor.razonamiento or "por defecto",
                      "casos": " ".join(Path(c).name for c in a.casos), "n_casos": len(casos)}
        registros, resumen = evaluar_en_mlflow(asistente, casos, a.mlflow, nombre, parametros, a.hilos)
    else:
        registros, resumen = evaluar(asistente, casos, a.hilos)
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
