"""Inspectable ontology capabilities; native Langflow nodes own all generation."""

from __future__ import annotations


from nima_semantica.models import NimaError
from nima_semantica.component_contracts import (
    ComponentAuthority,
    ComponentManifest,
    ReceiptDeclaration,
    ReceiptMode,
    SideEffectClass,
)
from ..values import _diagnostics, _value


from .base import InspectableStage, body, result
from nima_semantica.component_contracts import manifest_for_component


class ValidateOKFSnapshot(InspectableStage):
    name = "ValidateOKFSnapshot"
    display_name = "Validate OKF Snapshot"
    description = "Validate an OKF snapshot against an explicit ontology profile without persistence."
    nima_manifest = ComponentManifest(
        component_id="validate_okf_snapshot",
        version="2",
        input_contract="okf_snapshot_validation_request",
        output_contract="okf_snapshot_validation_result",
        authority=ComponentAuthority.TRANSFORMATION,
        side_effect_class=SideEffectClass.PURE_TRANSFORM,
        receipt=ReceiptDeclaration(mode=ReceiptMode.NONE),
    )

    async def run(self):
        from nima_semantica.okf_contracts import OKFSnapshot
        from nima_semantica.ontology_services import validate_okf_snapshot
        from nima_semantica.ontology_profiles import OntologyProfile

        value = body(self.payload)
        try:
            profile = OntologyProfile.model_validate(value["profile"])
            snapshot = OKFSnapshot.model_validate(value["snapshot"])
            report = validate_okf_snapshot(snapshot, profile)
            diagnostics = [issue.model_dump(mode="json") for issue in report.issues]
            return result(
                "Validate OKF Snapshot",
                {"report": report.model_dump(mode="json")},
                status="complete" if report.valid else "failed",
                diagnostics=diagnostics,
            )
        except (ValueError, NimaError) as error:
            return result(
                "Validate OKF Snapshot",
                {"report": None},
                status="failed",
                diagnostics=_diagnostics(error),
            )
