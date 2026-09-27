"""Inspectable lazy capability: exact-source checks and generated lookup probes."""
from lfx.custom import Component
from lfx.io import BoolInput, Output
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.lean_draft_backend import DraftLeanBackend
from nima_semantica.lean_transport import configured_lean_verifier
from nima_semantica.verify_lean_tool import verify_lean


class LeanDraftVerification(Component):
    name = "LeanDraftVerification"
    display_name = "Verify Lean · Exact Source and Local Resolution"
    description = "On-demand isolated Verify Lean for current source revisions and controller-generated declaration probes. Pinned environment, kernel replay, axiom inspection and durable receipts. Building this capability executes nothing."
    nima_manifest = manifest_for_component("LeanDraftVerification")
    inputs = [BoolInput(name="enabled",display_name="Allow isolated Lean execution (operator)",value=False)]
    outputs = [Output(name="backend",display_name="Pinned verification capability",method="build_backend",group_outputs=True)]

    async def build_backend(self) -> DraftLeanBackend:
        enabled = bool(self.enabled)
        def verify_exact(store, request, context, *, verifier):
            if not enabled:raise ValueError("Lean execution is not authorized")
            return verify_lean(store,request,context,verifier=verifier)
        return DraftLeanBackend(enabled,configured_lean_verifier,verify_exact)
