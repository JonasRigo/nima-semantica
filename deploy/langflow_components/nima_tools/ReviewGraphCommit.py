"""Explicit exact-approval seam for committing a Deep Research review graph."""
import json

from lfx.io import BoolInput, DropdownInput, StrInput
from lfx.schema import Message

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.graph_commit import OKFCommitApproval
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.project_update import UpdateProjectContext, UpdateProjectRequest, update_project_graph


class ReviewGraphCommit(BaseComponent):
    name = "ReviewGraphCommit"
    display_name = "Review Graph · Approved Project Commit"
    description = "Prepare an exact review-graph delta or commit only with operator-owned matching approval. Default leaves the graph proposal pending. Committed scientific status remains proposed/attributed."
    nima_manifest = manifest_for_component("ReviewGraphCommit")
    inputs = [handle("payload", "Deep Research result"),
        DropdownInput(name="commit_mode", display_name="Project graph action (operator)", options=["pending", "prepare", "commit"], value="pending"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research"),
        StrInput(name="actor", display_name="Approving actor (operator)", value="harness"),
        StrInput(name="approval_json", display_name="Exact OKFCommitApproval JSON (operator)", value="null"),
        BoolInput(name="allow_graph_writes", display_name="Allow approved graph writes (operator)", value=False),
        BoolInput(name="allow_audit_writes", display_name="Allow commit receipts (operator)", value=False),
        BoolInput(name="allow_projection_writes", display_name="Allow projection rebuild (operator)", value=False)]

    def run_sync(self):
        prior = _value(self.payload)
        data = prior.get("data", {})
        proposal = data.get("project_graph_prepare_request")
        if self.commit_mode == "pending" or proposal is None:
            status = "pending_approval" if proposal else data.get("graph_commit", {}).get("status", "no_graph_candidate")
            return {"deep_research": prior, "graph_commit": {"status": status}}
        request = UpdateProjectRequest.model_validate({**proposal, "mode": self.commit_mode})
        raw = json.loads(self.approval_json)
        context = UpdateProjectContext(corpus_id=self.corpus_id, project_id=self.project_id, actor=self.actor,
            approval=OKFCommitApproval.model_validate(raw) if raw else None,
            allow_graph_writes=self.allow_graph_writes, allow_audit_writes=self.allow_audit_writes,
            allow_projection_writes=self.allow_projection_writes)
        with configured_store(required=False) as store:
            result = update_project_graph(store, request, context).model_dump(mode="json")
        return {"deep_research": prior, "graph_commit": result}

    async def preview_message(self) -> Message:
        packet = (await self.result_data()).data
        research = packet["deep_research"]
        return Message(text=json.dumps({"research_status": research.get("status"),
            "review": research.get("data", {}).get("review"),
            "coverage_status": research.get("data", {}).get("coverage_status"),
            "coverage_facets": research.get("data", {}).get("coverage_facets"),
            "coverage": research.get("data", {}).get("coverage"),
            "uncovered_questions": research.get("data", {}).get("uncovered_questions"),
            "source_inventory": research.get("data", {}).get("source_inventory"),
            "entry_mode": research.get("data", {}).get("entry_mode"),
            "focus_question": research.get("data", {}).get("focus_question"),
            "regional_passes": research.get("data", {}).get("regional_passes"),
            "assessments": research.get("data", {}).get("assessments"),
            "review_graph": research.get("data", {}).get("review_graph"), "graph_commit": packet["graph_commit"]}, ensure_ascii=False, indent=2))
