"""Private formalization revisions use the existing proof ontology and Horn rules.

step represents exact source; check represents revision-bound verification;
context represents library candidates/resolutions; obligation retains correspondence gaps.
"""
from .proof_state import ProofState, POLICY_DIGEST, PROFILE


class LeanDraftState(ProofState):
    KIND = "LeanDraftAttemptRevision"

    def _input_record(self):
        return super()._input_record().model_copy(update={"kind": "LeanDraftAttemptInput"})

    def _source(self, source_id):
        if source_id == "task":
            return self.task
        receipt = self.receipts.get(source_id, **self.scope)
        if (receipt is None or receipt.operation_id != self.attempt_id or receipt.status != "completed"
                or receipt.stage not in ("draft_lean_read_regions", "draft_lean_retrieve_context")):
            raise ValueError("evidence is not a successful same-attempt exact read")
        return receipt.metadata["output"]["source_text"]
