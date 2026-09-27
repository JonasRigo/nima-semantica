"""Thin Langflow transport for typed GraphRAG context packets."""

from __future__ import annotations

from nima_semantica.models import NimaError
from nima_semantica.component_contracts import (
    ComponentAuthority,
    ComponentManifest,
    ReceiptDeclaration,
    ReceiptMode,
    SideEffectClass,
)
from nima_semantica.retrieval_contracts import RetrievalContextPacket

from .base import InspectableStage, body, result
from ..values import _diagnostics


class GraphContextPacket(InspectableStage):
    name = "GraphContextPacket"
    display_name = "GraphRAG Context Packet"
    description = "Validate and expose a revision-bound GraphRAG context packet as Langflow Data."
    nima_manifest = ComponentManifest(
        component_id="graph_context_packet",
        version="2",
        input_contract="retrieval_context_packet_input",
        output_contract="retrieval_context_packet",
        authority=ComponentAuthority.TRANSFORMATION,
        side_effect_class=SideEffectClass.PURE_TRANSFORM,
        receipt=ReceiptDeclaration(mode=ReceiptMode.NONE),
    )

    async def run(self):
        value = body(self.payload)
        try:
            packet = RetrievalContextPacket.model_validate(value["context_packet"])
            return result(
                "GraphRAG Context Packet",
                {"context_packet": packet.model_dump(mode="json"), "selected": value.get("selected", [])},
            )
        except (ValueError, NimaError) as error:
            return result(
                "GraphRAG Context Packet",
                {"context_packet": None},
                status="failed",
                diagnostics=_diagnostics(error),
            )
