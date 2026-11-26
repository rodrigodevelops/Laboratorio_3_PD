"""Los casos propios tienen el mismo formato que mensajes_dev.jsonl y respuestas coherentes con el contrato."""

from pathlib import Path

from novadistribuidora.esquemas import RespuestaPedido, SolicitudInterpretar
from novadistribuidora.evaluacion import cargar_casos

CASOS = Path(__file__).resolve().parents[1] / "casos" / "casos_propios.jsonl"


def test_formato_y_contrato():
    casos = cargar_casos([CASOS])
    assert len(casos) >= 20 and len({c["id"] for c in casos}) == len(casos)
    for c in casos:
        assert set(c) >= {"id", "tipo", "entrada", "esperado"}
        SolicitudInterpretar.model_validate(c["entrada"])
        e = c["esperado"]
        RespuestaPedido.model_validate({**e, "cliente_id": c["entrada"]["cliente_id"], "mensaje_cliente": "-"})
        assert round(sum(l["subtotal"] for l in e["lineas"]), 2) == e["total"]
        if e["estado"] == "fuera_de_alcance":
            assert e["lineas"] == [] and e["total"] == 0 and e["fecha_entrega"] is None
        else:
            assert e["fecha_entrega"] is not None
            assert (e["estado"] == "requiere_revision") == bool(e["motivos_revision"])


def test_cubren_lo_que_no_tienen_los_casos_publicados():
    tipos = {c["tipo"] for c in cargar_casos([CASOS])}
    motivos = {m for c in cargar_casos([CASOS]) for m in c["esperado"]["motivos_revision"]}
    assert {"manipulacion", "no_convertible", "novedoso"} <= tipos
    assert "cantidad_no_convertible" in motivos
